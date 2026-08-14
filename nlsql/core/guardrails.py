"""Read-only SQL guardrails.

Every LLM-generated query passes through :func:`validate_sql` before it is
allowed anywhere near DuckDB. The validator is deliberately conservative --
it is the security boundary of the whole system, because a language model
will eventually produce something it shouldn't, whether through a bad day or
a prompt-injection attempt in the question text.

Policy:
  * exactly one statement;
  * the statement must be a ``SELECT`` (a ``WITH ... SELECT`` CTE is fine);
  * no data-modifying or administrative keywords anywhere in the statement
    (INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH/COPY/...), checked on a copy of
    the SQL with string literals and comments blanked out, so a fare code of
    'DELETE' inside a string can't trigger a false positive and a keyword
    hidden in a comment can't slip past;
  * no file- or network-reading table functions (``read_parquet``,
    ``read_csv``, ``st_read``, ...), which DuckDB would otherwise happily
    execute from a SELECT;
  * the result is capped: an existing oversized ``LIMIT`` is clamped and a
    missing one is appended.

Known limitation: this is a lexical validator, not a full SQL parser. It is
paired with defence in depth -- the runner executes against an in-memory
database built from the bundled sample, so even a hypothetical bypass has
nothing to destroy and no credentials to reach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class UnsafeSQLError(ValueError):
    """Raised when a query violates the read-only policy."""


FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "REPLACE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "COPY",
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "SET",
        "CALL",
        "EXEC",
        "EXECUTE",
        "EXPORT",
        "IMPORT",
        "GRANT",
        "REVOKE",
        "VACUUM",
        "INSTALL",
        "LOAD",
        "USE",
        "CHECKPOINT",
        "INTO",
    }
)

FORBIDDEN_FUNCTIONS = frozenset(
    {
        "read_parquet",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "read_file",
        "parquet_scan",
        "csv_scan",
        "json_scan",
        "st_read",
        "sqlite_scan",
        "postgres_scan",
        "mysql_scan",
        "glob",
    }
)

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    limit_applied: bool
    limit_clamped: bool


def _blank_literals_and_comments(sql: str) -> str:
    """Return *sql* with string literals, quoted identifiers and comments
    replaced by spaces (same length, so offsets still line up with the
    original). Keyword scanning runs on this, never on the raw text."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":  # line comment
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif ch == "/" and nxt == "*":  # block comment
            j = sql.find("*/", i + 2)
            j = n - 2 if j == -1 else j
            out.append(" " * (j + 2 - i))
            i = j + 2
        elif ch in ("'", '"', "`"):  # string literal / quoted identifier
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:  # escaped quote
                        j += 2
                        continue
                    break
                j += 1
            end = min(j + 1, n)
            out.append(" " * (end - i))
            i = end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_statements(code_only: str) -> list[str]:
    return [part for part in code_only.split(";") if part.strip()]


def validate_sql(sql: str, max_rows: int = 1_000) -> ValidatedSQL:
    """Validate *sql* against the read-only policy.

    Returns the (possibly LIMIT-adjusted) SQL on success; raises
    :class:`UnsafeSQLError` with a specific reason otherwise.
    """
    if not sql or not sql.strip():
        raise UnsafeSQLError("empty query")

    code_only = _blank_literals_and_comments(sql)

    statements = _split_statements(code_only)
    if len(statements) > 1:
        raise UnsafeSQLError(f"expected a single statement, found {len(statements)}")

    words = _WORD_RE.findall(code_only)
    if not words:
        raise UnsafeSQLError("no SQL statement found")
    first = words[0].upper()
    if first not in ("SELECT", "WITH"):
        raise UnsafeSQLError(f"only SELECT statements are allowed, got {first!r}")

    upper_words = [w.upper() for w in words]
    for word in upper_words:
        if word in FORBIDDEN_KEYWORDS:
            raise UnsafeSQLError(f"forbidden keyword: {word}")

    lowered = code_only.lower()
    for func in FORBIDDEN_FUNCTIONS:
        if re.search(rf"\b{re.escape(func)}\s*\(", lowered):
            raise UnsafeSQLError(f"forbidden function: {func}")

    # A WITH clause must terminate in a SELECT; data-modifying CTEs are
    # already excluded by the keyword scan above (INSERT/UPDATE/DELETE).
    if first == "WITH" and "SELECT" not in upper_words:
        raise UnsafeSQLError("WITH clause without a SELECT")

    return _enforce_limit(sql, code_only, max_rows)


def _enforce_limit(sql: str, code_only: str, max_rows: int) -> ValidatedSQL:
    match = _LIMIT_RE.search(code_only)
    if match is None:
        trimmed = sql.rstrip().rstrip(";").rstrip()
        return ValidatedSQL(
            sql=f"{trimmed}\nLIMIT {max_rows}",
            limit_applied=True,
            limit_clamped=False,
        )
    existing = int(match.group(1))
    if existing <= max_rows:
        return ValidatedSQL(sql=sql, limit_applied=False, limit_clamped=False)
    # Clamp in place: offsets in code_only match the original string.
    start, end = match.span(1)
    clamped = sql[:start] + str(max_rows) + sql[end:]
    return ValidatedSQL(sql=clamped, limit_applied=False, limit_clamped=True)
