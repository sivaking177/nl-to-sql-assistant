"""A plain-English summary of what a validated query actually does.

When the assistant answers a question it shows the SQL, but SQL is only
readable if you already read SQL. This module picks a validated query
apart -- which tables it reads, what it filters on, how it groups and
aggregates, how it orders and caps the result -- and renders that as data
(:class:`QueryExplanation`) plus a one-or-two-sentence summary. The CLI
prints the summary under the SQL; the HTTP service returns the parts as
JSON, so a UI could show "reads trips, grouped by borough" without
parsing anything itself.

Like the guardrails, this is a lexical pass over SQL with string literals
and comments blanked out, not a full parser. It describes the queries this
system produces (single SELECTs over known tables); it is a reading aid,
not a correctness check -- correctness is the guardrails' and the
database's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .guardrails import _blank_literals_and_comments

_AGGREGATE_FUNCTIONS = (
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "MEDIAN",
    "STDDEV",
    "VARIANCE",
    "STRING_AGG",
    "LIST",
    "APPROX_COUNT_DISTINCT",
)

_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)


@dataclass
class QueryExplanation:
    tables: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    aggregations: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: str | None = None
    limit: int | None = None

    @property
    def joins(self) -> int:
        # Every table after the first is reached through a join.
        return max(0, len(self.tables) - 1)

    def to_dict(self) -> dict:
        return {
            "tables": self.tables,
            "joins": self.joins,
            "filters": self.filters,
            "aggregations": self.aggregations,
            "group_by": self.group_by,
            "order_by": self.order_by,
            "limit": self.limit,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        """One or two plain sentences describing the query."""
        parts: list[str] = []
        if self.tables:
            parts.append(f"Reads from {', '.join(self.tables)}")
        if self.filters:
            parts.append(f"filters on {' and '.join(self.filters)}")
        if self.group_by:
            parts.append(f"groups by {', '.join(self.group_by)}")
        if self.aggregations:
            parts.append(f"aggregates with {', '.join(self.aggregations)}")
        if self.order_by:
            parts.append(f"orders by {self.order_by}")
        if self.limit is not None:
            parts.append(f"returns at most {self.limit} rows")
        if not parts:
            return "A plain SELECT with no filters, grouping, or limit."
        return "; ".join(parts) + "."


def _clause_text(
    code: str, start_keyword: str, end_keywords: tuple[str, ...]
) -> str | None:
    """Text of the clause starting at *start_keyword*, or None if absent.

    The clause ends at the first of *end_keywords* occurring at parenthesis
    depth zero after the start (so a subquery's GROUP BY can't end the
    outer query's WHERE clause early).
    """
    start_match = re.search(rf"\b{start_keyword}\b", code, re.IGNORECASE)
    if start_match is None:
        return None
    rest = code[start_match.end() :]
    depth = 0
    end = len(rest)
    token_re = re.compile(
        r"\(|\)|\b(?:" + "|".join(end_keywords) + r")\b", re.IGNORECASE
    )
    for match in token_re.finditer(rest):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            end = match.start()
            break
    return rest[:end].strip() or None


def _split_commas(text: str) -> list[str]:
    """Split on commas at parenthesis depth zero (so the comma inside a
    function call like ``date_trunc('day', x)`` doesn't split a group key)."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _split_top_level(text: str, separator_keyword: str) -> list[str]:
    """Split *text* on a keyword at parenthesis depth zero."""
    parts: list[str] = []
    depth = 0
    start = 0
    pattern = re.compile(rf"\(|\)|\b{separator_keyword}\b", re.IGNORECASE)
    for match in pattern.finditer(text):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            parts.append(text[start : match.start()])
            start = match.end()
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def explain_sql(sql: str) -> QueryExplanation:
    """Break *sql* down into a :class:`QueryExplanation`."""
    code = _blank_literals_and_comments(sql)

    tables: list[str] = []
    for name in _TABLE_RE.findall(code):
        if name not in tables:
            tables.append(name)

    where = _clause_text(code, "WHERE", ("GROUP BY", "ORDER BY", "LIMIT"))
    filters = _split_top_level(where, "AND") if where else []

    aggregations = sorted(
        {
            name.upper()
            for name in _AGGREGATE_FUNCTIONS
            if re.search(rf"\b{name}\s*\(", code, re.IGNORECASE)
        }
    )

    group_clause = _clause_text(code, "GROUP BY", ("ORDER BY", "LIMIT", "HAVING"))
    group_by = _split_commas(group_clause) if group_clause else []

    order_clause = _clause_text(code, "ORDER BY", ("LIMIT",))

    limit_match = _LIMIT_RE.search(code)
    limit = int(limit_match.group(1)) if limit_match else None

    return QueryExplanation(
        tables=tables,
        filters=filters,
        aggregations=aggregations,
        group_by=group_by,
        order_by=order_clause,
        limit=limit,
    )
