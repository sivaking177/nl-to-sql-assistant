"""Schema introspection and schema-aware prompt construction.

The single most effective trick in NL-to-SQL is showing the model the actual
schema: real table/column names, types, row counts, and -- for low-cardinality
columns -- the set of values it will actually encounter (e.g. ``payment_type``
is an opaque integer code until the prompt says 1 = credit card). This module
extracts that information from DuckDB's ``information_schema`` and renders it
into the system prompt the LLM client sends with every question.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

# Columns with at most this many distinct values get their values enumerated
# in the prompt.
MAX_ENUMERATED_DISTINCT = 25
# Cap on enumerated values actually printed, to keep the prompt bounded.
MAX_ENUMERATED_VALUES = 25


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    sample_values: list[str] = field(default_factory=list)


@dataclass
class TableInfo:
    name: str
    row_count: int
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass
class DatabaseSchema:
    tables: list[TableInfo]

    def table(self, name: str) -> TableInfo:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)


def introspect_schema(con: duckdb.DuckDBPyConnection) -> DatabaseSchema:
    """Read the live schema of every user table in *con*."""
    table_names = [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
    ]
    tables: list[TableInfo] = []
    for table_name in table_names:
        count_row = con.execute(f"SELECT count(*) FROM {table_name}").fetchone()
        row_count = int(count_row[0]) if count_row else 0
        columns: list[ColumnInfo] = []
        for col_name, data_type in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
            [table_name],
        ).fetchall():
            columns.append(
                ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    sample_values=_sample_values(con, table_name, col_name),
                )
            )
        tables.append(TableInfo(name=table_name, row_count=row_count, columns=columns))
    return DatabaseSchema(tables=tables)


def _sample_values(
    con: duckdb.DuckDBPyConnection, table: str, column: str
) -> list[str]:
    """Distinct values for low-cardinality columns, else an empty list."""
    distinct_row = con.execute(
        f"SELECT count(DISTINCT {column}) FROM {table}"
    ).fetchone()
    distinct = int(distinct_row[0]) if distinct_row else 0
    if distinct == 0 or distinct > MAX_ENUMERATED_DISTINCT:
        return []
    rows = con.execute(
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} IS NOT NULL ORDER BY 1 LIMIT {MAX_ENUMERATED_VALUES}"
    ).fetchall()
    return [str(r[0]) for r in rows]


# Domain notes a human engineer would give the model. Kept deliberately
# factual: these describe the TLC data dictionary, not the sample's contents.
_DOMAIN_NOTES = """\
Domain notes (NYC TLC yellow taxi data):
- payment_type codes: 1 = credit card, 2 = cash, 3 = no charge, 4 = dispute,
  5 = unknown, 6 = voided trip. Tips are only recorded for credit-card trips.
- RatecodeID: 1 = standard rate, 2 = JFK, 3 = Newark, 4 = Nassau/Westchester,
  5 = negotiated fare, 6 = group ride, 99 = null.
- PULocationID / DOLocationID reference taxi_zones.LocationID.
- Trip duration in minutes: epoch(tpep_dropoff_datetime - tpep_pickup_datetime) / 60.
- The raw TLC feed contains some out-of-range rows (negative fares, zero
  distances); aggregate questions do not need to filter them unless asked."""


def build_system_prompt(schema: DatabaseSchema) -> str:
    """Render *schema* into the system prompt sent to the LLM."""
    lines: list[str] = [
        (
            "You are a data analyst assistant that answers questions about "
            "NYC taxi trips by writing DuckDB SQL."
        ),
        "",
        "Rules:",
        (
            "- Reply with exactly one SQL query and nothing else: no "
            "explanation, no markdown fences."
        ),
        "- The query must be a single read-only SELECT statement.",
        "- Use only the tables and columns listed below.",
        "- Prefer explicit column names over SELECT * for aggregate questions.",
        "",
        "Database schema:",
    ]
    for table in schema.tables:
        lines.append(f"TABLE {table.name} ({table.row_count:,} rows)")
        for col in table.columns:
            suffix = ""
            if col.sample_values:
                suffix = f" -- values: {', '.join(col.sample_values)}"
            lines.append(f"  - {col.name} {col.data_type}{suffix}")
    lines.append("")
    lines.append(_DOMAIN_NOTES)
    return "\n".join(lines)
