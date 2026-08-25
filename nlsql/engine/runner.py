"""Query execution: the only path by which generated SQL reaches DuckDB.

Two runners live here, on purpose:

- :class:`QueryRunner` does one thing: validate a SQL string (see
  :mod:`nlsql.core.guardrails`), execute it, return the result. The evaluation
  harness uses it because the eval grades the model's *first* answer.
- :class:`AssistingRunner` wraps a client plus a QueryRunner and adds the
  retry loop the interactive paths (CLI ``ask``, the HTTP service) want:
  if the generated SQL is rejected by the guardrails or fails in DuckDB,
  the error is fed back to the model and it gets another attempt, up to
  ``max_attempts`` tries in total. Models fix most of their own mistakes
  when shown the actual error message; without the loop, one typo'd column
  name means the user gets nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.data import TripDatabase
from ..core.guardrails import ValidatedSQL, validate_sql
from ..llm import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    sql: str  # the SQL actually executed (after guardrail LIMIT handling)
    columns: list[str]
    rows: list[tuple[Any, ...]]
    limit_applied: bool
    limit_clamped: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


class QueryRunner:
    def __init__(self, db: TripDatabase, max_rows: int = 1_000) -> None:
        self._db = db
        self._max_rows = max_rows

    def run(self, raw_sql: str) -> QueryResult:
        """Validate *raw_sql*, execute it, and return the result."""
        validated: ValidatedSQL = validate_sql(raw_sql, max_rows=self._max_rows)
        columns, rows = self._db.execute(validated.sql)
        return QueryResult(
            sql=validated.sql,
            columns=columns,
            rows=rows,
            limit_applied=validated.limit_applied,
            limit_clamped=validated.limit_clamped,
        )


# ----------------------------------------------------------------------
# Retry loop
# ----------------------------------------------------------------------


@dataclass
class AttemptRecord:
    """One try inside the retry loop: the SQL offered and, if it failed,
    the error that killed it (``None`` on the successful attempt)."""

    sql: str
    error: str | None = None


@dataclass
class AskOutcome:
    """A successful :meth:`AssistingRunner.ask`: the result, plus the full
    attempt history so callers can show "needed 2 attempts" honestly."""

    result: QueryResult
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def retries_used(self) -> int:
        return len(self.attempts) - 1


class AssistantError(RuntimeError):
    """Raised when every attempt failed. Carries the attempt history."""

    def __init__(self, question: str, attempts: list[AttemptRecord]) -> None:
        last = attempts[-1].error if attempts else "no attempts made"
        super().__init__(
            f"could not answer {question!r} after {len(attempts)} "
            f"attempt(s); last error: {last}"
        )
        self.question = question
        self.attempts = attempts


@runtime_checkable
class _CorrectionCapable(Protocol):
    """Structural type for clients that can repair a failed query."""

    def generate_correction(
        self,
        system_prompt: str,
        question: str,
        failed_sql: str,
        error: str,
    ) -> str: ...


class AssistingRunner:
    """Ask-a-question runner with an error-feedback retry loop."""

    def __init__(
        self,
        db: TripDatabase,
        max_rows: int = 1_000,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._runner = QueryRunner(db, max_rows=max_rows)
        self._max_attempts = max_attempts

    def ask(
        self,
        client: LLMClient,
        system_prompt: str,
        question: str,
    ) -> AskOutcome:
        """Generate SQL for *question*, retrying with error feedback.

        The loop catches any exception raised while validating or
        executing the SQL. In practice those are guardrail rejections
        (:class:`UnsafeSQLError`) and DuckDB execution errors, and both
        mean "the SQL was wrong", which a correction can fix; a bug in
        our own code would be caught too -- accepted, because the attempt
        history keeps it visible instead of hiding it. Errors from the
        client itself are a different story: the first generation happens
        before the loop and correction calls are not wrapped either, so a
        client that cannot produce an answer at all
        (:class:`~nlsql.llm.LLMError`) propagates immediately -- retrying
        a dead client just adds latency.
        """
        attempts: list[AttemptRecord] = []
        raw_sql = client.generate_sql(system_prompt, question)
        for attempt_number in range(1, self._max_attempts + 1):
            try:
                result = self._runner.run(raw_sql)
            except Exception as exc:
                # Two failure kinds land here, and both mean "the SQL was
                # wrong", which a correction can fix: guardrail rejection
                # (UnsafeSQLError) and DuckDB execution errors (whose
                # exception types vary by failure). A bug in our own code
                # would land here too -- accepted, because the attempt
                # history keeps it visible instead of hiding it.
                error = f"{type(exc).__name__}: {exc}"
                attempts.append(AttemptRecord(sql=raw_sql, error=error))
                if attempt_number == self._max_attempts:
                    logger.error(
                        "giving up on %r after %d attempts: %s",
                        question,
                        attempt_number,
                        error,
                    )
                    raise AssistantError(question, attempts) from exc
                logger.warning(
                    "attempt %d for %r failed (%s); asking for a correction",
                    attempt_number,
                    question,
                    error,
                )
                raw_sql = self._request_correction(
                    client, system_prompt, question, raw_sql, error
                )
            else:
                attempts.append(AttemptRecord(sql=raw_sql))
                return AskOutcome(result=result, attempts=attempts)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _request_correction(
        client: LLMClient,
        system_prompt: str,
        question: str,
        failed_sql: str,
        error: str,
    ) -> str:
        # Use the client's dedicated correction call when it has one (the
        # live client sends the error verbatim; the scriptable fake plays
        # its next scripted answer). Plain clients just get asked again.
        if isinstance(client, _CorrectionCapable):
            return client.generate_correction(
                system_prompt, question, failed_sql, error
            )
        return client.generate_sql(system_prompt, question)


def format_result(result: QueryResult, max_width: int = 28) -> str:
    """Render *result* as an aligned plain-text table."""
    if not result.columns:
        return "(no result columns)"

    def cell(value: Any) -> str:
        if value is None:
            text = "NULL"
        elif isinstance(value, float):
            text = f"{value:,.4f}".rstrip("0").rstrip(".")
        else:
            text = str(value)
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    rendered = [[cell(v) for v in row] for row in result.rows]
    widths = [
        (
            max(len(result.columns[i]), *(len(r[i]) for r in rendered))
            if rendered
            else len(result.columns[i])
        )
        for i in range(len(result.columns))
    ]
    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(result.columns))
    divider = "-+-".join("-" * w for w in widths)
    body = [" | ".join(r[i].ljust(widths[i]) for i in range(len(r))) for r in rendered]
    noun = "row" if result.row_count == 1 else "rows"
    return "\n".join([header, divider, *body, f"({result.row_count} {noun})"])
