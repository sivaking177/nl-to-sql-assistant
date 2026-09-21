"""End-to-end evaluation tests: the full pipeline (prompt -> fake client ->
guardrails -> DuckDB) must answer every fixed question correctly, and the
harness must *fail* when an answer is wrong -- a harness that always passes
is worth nothing."""

import pytest
from pydantic import ValidationError

from nlsql.engine.evaluate import (
    CATEGORIES,
    EvalCase,
    category_breakdown,
    pass_rate,
    run_checks,
    run_evaluation,
)
from nlsql.engine.runner import QueryResult
from nlsql.llm import FakeLLMClient


def test_evaluation_has_fifteen_cases(cases):
    assert len(cases) == 15
    assert len({c.id for c in cases}) == 15


def test_every_case_has_a_known_category(cases):
    assert all(c.category in CATEGORIES for c in cases)
    # All four categories are exercised by the fixed set.
    assert {c.category for c in cases} == set(CATEGORIES)


def test_results_carry_category_latency_and_breakdown(db, cases, fake_client):
    results = run_evaluation(db, fake_client, cases)
    assert all(r.latency_ms >= 0 for r in results)
    assert {r.case_id: r.category for r in results} == {c.id: c.category for c in cases}
    assert pass_rate(results) == 1.0
    breakdown = category_breakdown(results)
    assert set(breakdown) == set(CATEGORIES)
    assert all(passed == total for passed, total in breakdown.values())
    assert sum(total for _, total in breakdown.values()) == 15


def test_full_evaluation_passes_with_fake_client(db, cases, fake_client):
    results = run_evaluation(db, fake_client, cases)
    failures = {r.case_id: r.failures for r in results if not r.passed}
    assert failures == {}


def test_harness_catches_a_wrong_answer(db, cases):
    answers = {c.question: c.fake_sql for c in cases}
    target = next(c for c in cases if c.id == "credit-card-trips")
    # Sabotage: answer the credit-card question with the cash count.
    answers[target.question] = "SELECT COUNT(*) FROM trips WHERE payment_type = 2"
    results = run_evaluation(db, FakeLLMClient(answers), cases)
    by_id = {r.case_id: r for r in results}
    assert not by_id["credit-card-trips"].passed
    # ...while the untouched cases still pass.
    assert by_id["total-trips"].passed


def test_eval_case_rejects_a_typoed_category():
    # The eval file is hand-edited; validation at load time is what keeps
    # a typo from silently skewing the category report.
    with pytest.raises(ValidationError):
        EvalCase(
            id="x",
            question="q",
            fake_sql="SELECT 1",
            reference_sql="SELECT 1",
            category="aggreagtion",
        )


def test_value_check_on_an_empty_result_fails_cleanly():
    # Regression test: a value check against a zero-row result used to
    # surface as a bare IndexError. It must be a readable failure.
    case = EvalCase(
        id="x",
        question="q",
        fake_sql="SELECT 1 WHERE false",
        reference_sql="SELECT 1",
        checks=[{"type": "value_at_least", "value": 5}],
    )
    empty = QueryResult(
        sql="SELECT 1 WHERE false",
        columns=["x"],
        rows=[],
        limit_applied=False,
        limit_clamped=False,
    )
    failures = run_checks(empty, case)
    assert len(failures) == 1
    assert "no value" in failures[0]


def test_harness_catches_an_unsafe_answer(db, cases):
    answers = {c.question: c.fake_sql for c in cases}
    target = next(c for c in cases if c.id == "total-trips")
    answers[target.question] = "DROP TABLE trips"
    results = run_evaluation(db, FakeLLMClient(answers), cases)
    by_id = {r.case_id: r for r in results}
    assert not by_id["total-trips"].passed
    assert "UnsafeSQLError" in by_id["total-trips"].failures[0]
