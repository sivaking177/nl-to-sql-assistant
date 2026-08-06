"""DuckDB data layer.

Loads the NYC TLC yellow-taxi sample parquet into a ``trips`` table and the
TLC taxi-zone lookup CSV into a ``taxi_zones`` table, on an in-memory DuckDB
connection. Everything downstream (schema introspection, the query runner)
talks to the database through this class, so tests can build a throwaway
database from the bundled sample in milliseconds.

The loader expects genuine TLC trip-record files: monthly parquet files
published at https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
(also mirrored on the CloudFront CDN). ``scripts/download_data.py`` rebuilds
the bundled sample from a full monthly file.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..config import Settings

TRIPS_TABLE = "trips"
ZONES_TABLE = "taxi_zones"


class DataLoadError(RuntimeError):
    """Raised when the source data files are missing or unreadable."""


class TripDatabase:
    """An in-memory DuckDB database holding the taxi tables."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._con = connection

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    @classmethod
    def from_settings(cls, settings: Settings) -> TripDatabase:
        """Load the trips parquet and zones CSV described by *settings*."""
        trips_path = settings.data_dir / settings.trips_file
        zones_path = settings.data_dir / settings.zones_file
        for path in (trips_path, zones_path):
            if not path.exists():
                raise DataLoadError(
                    f"Data file not found: {path}. "
                    "Run scripts/download_data.py or check the data directory."
                )
        con = duckdb.connect(database=":memory:")
        # Parameter binding does not work for file paths in DDL, so the paths
        # are inlined after escaping single quotes (they come from our own
        # config, never from user input).
        con.execute(
            f"CREATE TABLE {TRIPS_TABLE} AS "
            f"SELECT * FROM read_parquet('{_escape(trips_path)}')"
        )
        con.execute(
            f"CREATE TABLE {ZONES_TABLE} AS "
            f"SELECT * FROM read_csv('{_escape(zones_path)}', header=true)"
        )
        return cls(con)

    # ------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------
    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._con

    def table_names(self) -> list[str]:
        rows = self._con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    def row_count(self, table: str = TRIPS_TABLE) -> int:
        row = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()
        if row is None:  # a count(*) query always returns one row
            raise DataLoadError(f"count query for {table} returned no row")
        return int(row[0])

    def execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Run *sql* and return ``(column_names, rows)``.

        This is a low-level escape hatch used by the schema module and the
        tests. Untrusted (LLM-generated) SQL must go through
        :mod:`nlsql.core.guardrails` and :class:`nlsql.engine.runner.QueryRunner`.
        """
        cursor = self._con.execute(sql)
        columns = [d[0] for d in cursor.description]
        return columns, cursor.fetchall()

    def close(self) -> None:
        self._con.close()


def _escape(path: Path) -> str:
    return str(path).replace("'", "''")
