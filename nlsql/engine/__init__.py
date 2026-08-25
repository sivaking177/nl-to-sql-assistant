"""The engine: executing questions and grading them.

- :mod:`nlsql.engine.runner`   -- validated execution + the retry loop.
- :mod:`nlsql.engine.evaluate` -- the fixed-question evaluation harness.
"""

from .evaluate import (
    CATEGORIES,
    CaseResult,
    EvalCase,
    category_breakdown,
    load_cases,
    pass_rate,
    run_evaluation,
    summarise,
)
from .runner import (
    AskOutcome,
    AssistantError,
    AssistingRunner,
    AttemptRecord,
    QueryResult,
    QueryRunner,
    format_result,
)

__all__ = [
    "CATEGORIES",
    "AskOutcome",
    "AssistantError",
    "AssistingRunner",
    "AttemptRecord",
    "CaseResult",
    "EvalCase",
    "QueryResult",
    "QueryRunner",
    "category_breakdown",
    "format_result",
    "load_cases",
    "pass_rate",
    "run_evaluation",
    "summarise",
]
