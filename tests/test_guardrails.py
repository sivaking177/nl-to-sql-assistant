"""Guardrail tests: this module is the security boundary, so every
forbidden category gets its own rejection test, and the tricky *allowed*
cases (keywords inside strings/comments) are pinned down too."""

import pytest

from nlsql.core.guardrails import UnsafeSQLError, validate_sql

VALID = [
    "SELECT count(*) FROM trips",
    "SELECT * FROM trips WHERE payment_type = 1 LIMIT 10",
    "WITH t AS (SELECT * FROM trips) SELECT count(*) FROM t",
    "SELECT Zone FROM taxi_zones WHERE Borough = 'Queens'",
    # A forbidden word inside a string literal is data, not code.
    "SELECT * FROM trips WHERE store_and_fwd_flag = 'DELETE'",
    # A forbidden word inside a comment is inert.
    "SELECT /* DELETE everything */ count(*) FROM trips",
    # Semicolon inside a string literal is not a statement separator.
    "SELECT 'a;b' AS label",
]

FORBIDDEN = [
    ("INSERT INTO trips VALUES (1)", "INSERT"),
    ("UPDATE trips SET fare_amount = 0", "UPDATE"),
    ("DELETE FROM trips", "DELETE"),
    ("DROP TABLE trips", "DROP"),
    ("CREATE TABLE x AS SELECT * FROM trips", "CREATE"),
    ("ALTER TABLE trips ADD COLUMN x INT", "ALTER"),
    ("SELECT * FROM trips; DROP TABLE trips", "single statement"),
    ("SELECT * FROM trips; SELECT 1", "single statement"),
    ("PRAGMA database_list", "PRAGMA"),
    ("ATTACH 'other.db' AS other", "ATTACH"),
    ("COPY trips TO 'out.csv'", "COPY"),
    ("SELECT * INTO new_table FROM trips", "INTO"),
    ("WITH t AS (DELETE FROM trips RETURNING *) SELECT * FROM t", "DELETE"),
    ("SELECT * FROM read_parquet('secret.parquet')", "read_parquet"),
    ("SELECT * FROM read_csv('https://example.com/x.csv')", "read_csv"),
    ("VACUUM", "VACUUM"),
    ("SET memory_limit = '1GB'", "SET"),
    ("", "empty"),
    ("   ", "empty"),
]


@pytest.mark.parametrize("sql", VALID)
def test_valid_queries_pass(sql):
    result = validate_sql(sql, max_rows=1000)
    assert result.sql.strip()


@pytest.mark.parametrize("sql,reason", FORBIDDEN)
def test_forbidden_queries_rejected(sql, reason):
    with pytest.raises(UnsafeSQLError) as excinfo:
        validate_sql(sql, max_rows=1000)
    assert reason.lower() in str(excinfo.value).lower()


def test_limit_is_appended_when_missing():
    result = validate_sql("SELECT * FROM trips", max_rows=500)
    assert result.limit_applied is True
    assert result.sql.rstrip().endswith("LIMIT 500")


def test_existing_small_limit_is_kept():
    result = validate_sql("SELECT * FROM trips LIMIT 10", max_rows=500)
    assert result.limit_applied is False
    assert result.limit_clamped is False
    assert "LIMIT 10" in result.sql


def test_oversized_limit_is_clamped():
    result = validate_sql("SELECT * FROM trips LIMIT 999999", max_rows=500)
    assert result.limit_clamped is True
    assert "LIMIT 500" in result.sql
    assert "999999" not in result.sql


def test_trailing_semicolon_is_tolerated():
    result = validate_sql("SELECT count(*) FROM trips;", max_rows=100)
    assert "LIMIT 100" in result.sql
