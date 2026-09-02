"""Evaluation harness.

A fixed set of natural-language questions (``eval/questions.json``) with two
SQL queries each:

- ``fake_sql`` -- what the offline :class:`~nlsql.llm.FakeLLMClient` answers.
  Written in a deliberately different style from the reference (different
  aliases, functions, and formulations) so a pass means the *pipeline*
  works, not that two identical strings were compared.
- ``reference_sql`` -- a hand-written ground-truth query. The harness runs
  it through the same guardrailed runner and compares result sets.

Each case also carries property ``checks`` (row counts, columns, value
bounds) that must hold regardless of the reference, so a wrong-but-
self-consistent pair of queries can't silently pass.

Each case is tagged with a ``category`` (aggregation / filter / join /
ranking) describing the main skill the question exercises, and the harness
records per-case latency (generation + execution). The report breaks the
pass rate down by category, because "13 of 15" hides exactly the thing I
want to know: *which kind* of question broke.

Two deliberate choices: the harness grades the client's first answer only
(no retry loop -- the eval measures single-shot quality; the loop is an
interactive-path feature), and with ``--live`` the same harness runs
against the Claude API, the canned ``fake_sql`` ignored and the model's
own SQL judged against the references. Live results vary; the offline run
is the regression gate.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.data import TripDatabase
from ..core.schema import build_system_prompt, introspect_schema
from ..llm import LLMClient
from .runner import QueryResult, QueryRunner

DEFAULT_TOLERANCE = 1e-6


#: The categories an eval case can be tagged with, in report order.
CATEGORIES = ("aggregation", "filter", "join", "ranking")


class EvalCase(BaseModel):
    """One fixed evaluation question, loaded from the eval JSON file.

    A pydantic model rather than a plain dataclass because this file is
    hand-edited: a typo'd category or compare mode should fail loudly at
    load time, not produce a quietly wrong report later.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    fake_sql: str
    reference_sql: str
    compare: Literal["scalar", "rows_unordered", "rows_ordered"] = "rows_unordered"
    tolerance: float = DEFAULT_TOLERANCE
    checks: list[dict[str, Any]] = Field(default_factory=list)
    category: Literal["aggregation", "filter", "join", "ranking"] = "aggregation"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    category: str = "aggregation"
    latency_ms: float = 0.0  # generation + execution time for this case
    failures: list[str] = field(default_factory=list)
    sql_used: str = ""


def load_cases(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase.model_validate(entry) for entry in raw["cases"]]


def _cells_equal(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    return a == b or str(a) == str(b)


def _rows_equal(a: tuple, b: tuple, tol: float) -> bool:
    return len(a) == len(b) and all(_cells_equal(x, y, tol) for x, y in zip(a, b))


def results_match(
    actual: QueryResult, expected: QueryResult, case: EvalCase
) -> list[str]:
    """Return a list of mismatch descriptions (empty = match)."""
    failures: list[str] = []
    tol = case.tolerance
    if case.compare == "scalar":
        if actual.row_count != 1 or len(actual.columns) != 1:
            failures.append(
                f"expected a single value, got {actual.row_count} rows x "
                f"{len(actual.columns)} columns"
            )
            return failures
        if expected.row_count != 1:
            failures.append("reference query did not return a single row")
            return failures
        if not _cells_equal(actual.rows[0][0], expected.rows[0][0], tol):
            failures.append(
                f"scalar mismatch: got {actual.rows[0][0]!r}, "
                f"expected {expected.rows[0][0]!r}"
            )
        return failures

    if len(actual.columns) != len(expected.columns):
        failures.append(
            f"column count mismatch: got {actual.columns}, "
            f"expected {expected.columns}"
        )
    if case.compare == "rows_ordered":
        if actual.row_count != expected.row_count:
            failures.append(
                f"row count mismatch: got {actual.row_count}, "
                f"expected {expected.row_count}"
            )
            return failures
        for i, (a_row, e_row) in enumerate(zip(actual.rows, expected.rows)):
            if not _rows_equal(a_row, e_row, tol):
                failures.append(f"row {i} mismatch: got {a_row}, expected {e_row}")
        return failures

    # rows_unordered: multiset comparison with tolerant float matching.
    remaining = list(expected.rows)
    for a_row in actual.rows:
        for j, e_row in enumerate(remaining):
            if _rows_equal(a_row, e_row, tol):
                remaining.pop(j)
                break
        else:
            failures.append(f"unexpected row: {a_row}")
    for e_row in remaining:
        failures.append(f"missing row: {e_row}")
    return failures


def run_checks(result: QueryResult, case: EvalCase) -> list[str]:
    failures: list[str] = []
    for check in case.checks:
        kind = check["type"]
        if kind == "row_count":
            if result.row_count != check["value"]:
                failures.append(
                    f"check row_count: got {result.row_count}, "
                    f"expected {check['value']}"
                )
        elif kind == "min_rows":
            if result.row_count < check["value"]:
                failures.append(
                    f"check min_rows: got {result.row_count}, "
                    f"expected >= {check['value']}"
                )
        elif kind == "column_present":
            if check["name"].lower() not in [c.lower() for c in result.columns]:
                failures.append(
                    f"check column_present: {check['name']!r} not in "
                    f"{result.columns}"
                )
        elif kind in ("value_at_least", "value_at_most"):
            column_index = check.get("column_index", 0)
            if not result.rows or column_index >= len(result.rows[0]):
                # Used to be an IndexError that the harness caught and
                # reported as a bare "IndexError: list index out of
                # range" -- true but useless. Say what actually happened.
                failures.append(
                    f"check {kind}: result has no value at "
                    f"row 0, column {column_index}"
                )
                continue
            value = result.rows[0][column_index]
            bound = check["value"]
            if kind == "value_at_least" and not float(value) >= bound:
                failures.append(
                    f"check value_at_least: got {value}, expected >= {bound}"
                )
            if kind == "value_at_most" and not float(value) <= bound:
                failures.append(
                    f"check value_at_most: got {value}, expected <= {bound}"
                )
        else:
            failures.append(f"unknown check type: {kind}")
    return failures


def run_evaluation(
    db: TripDatabase,
    client: LLMClient,
    cases: list[EvalCase],
    max_rows: int = 1_000,
) -> list[CaseResult]:
    """Run every case end-to-end: prompt -> client -> guardrails -> DuckDB."""
    runner = QueryRunner(db, max_rows=max_rows)
    system_prompt = build_system_prompt(introspect_schema(db.connection))
    results: list[CaseResult] = []
    for case in cases:
        failures: list[str] = []
        sql_used = ""
        started = time.perf_counter()
        try:
            generated = client.generate_sql(system_prompt, case.question)
            sql_used = generated
            actual = runner.run(generated)
            expected = runner.run(case.reference_sql)
            failures.extend(results_match(actual, expected, case))
            failures.extend(run_checks(actual, case))
        except Exception as exc:  # noqa: BLE001 -- any failure fails the case
            failures.append(f"{type(exc).__name__}: {exc}")
        results.append(
            CaseResult(
                case_id=case.id,
                passed=not failures,
                category=case.category,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                failures=failures,
                sql_used=sql_used,
            )
        )
    return results


def summarise(results: list[CaseResult]) -> tuple[int, int]:
    passed = sum(1 for r in results if r.passed)
    return passed, len(results)


def pass_rate(results: list[CaseResult]) -> float:
    """Fraction of cases passed, 0.0-1.0 (0.0 for an empty run)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


def category_breakdown(results: list[CaseResult]) -> dict[str, tuple[int, int]]:
    """``{category: (passed, total)}`` in :data:`CATEGORIES` order.

    Categories are validated when the eval file loads (EvalCase is a
    pydantic model with a Literal category), so there is no unknown-
    category case left to defend against here.
    """
    breakdown: dict[str, tuple[int, int]] = {}
    for category in CATEGORIES:
        in_category = [r for r in results if r.category == category]
        if in_category:
            breakdown[category] = (
                sum(1 for r in in_category if r.passed),
                len(in_category),
            )
    return breakdown
