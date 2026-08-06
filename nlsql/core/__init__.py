"""Core pipeline around the database: loading, schema, safety, reading.

- :mod:`nlsql.core.data`       -- DuckDB data layer.
- :mod:`nlsql.core.schema`     -- schema introspection and the prompt.
- :mod:`nlsql.core.guardrails` -- read-only SQL validation.
- :mod:`nlsql.core.explain`    -- plain-English query summaries.
"""

from .data import DataLoadError, TripDatabase
from .explain import QueryExplanation, explain_sql
from .guardrails import UnsafeSQLError, ValidatedSQL, validate_sql
from .schema import DatabaseSchema, build_system_prompt, introspect_schema

__all__ = [
    "DataLoadError",
    "DatabaseSchema",
    "QueryExplanation",
    "TripDatabase",
    "UnsafeSQLError",
    "ValidatedSQL",
    "build_system_prompt",
    "explain_sql",
    "introspect_schema",
    "validate_sql",
]
