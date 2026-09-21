"""Retry-loop tests: the AssistingRunner must recover when the model's
first SQL is bad, feed the real error back for the correction, and give
up -- with the full attempt history attached -- when it never recovers."""

import pytest

from nlsql.engine.runner import AssistantError, AssistingRunner
from nlsql.llm import FakeLLMClient, LLMError, ScriptableFakeLLMClient

QUESTION = "How many trips are in the dataset?"
GOOD_SQL = "SELECT COUNT(*) AS total_trips FROM trips"


def test_first_answer_success_needs_no_retry(db):
    runner = AssistingRunner(db, max_attempts=3)
    client = ScriptableFakeLLMClient({QUESTION: [GOOD_SQL]})
    outcome = runner.ask(client, "prompt", QUESTION)
    assert outcome.result.rows[0][0] == db.row_count("trips")
    assert len(outcome.attempts) == 1
    assert outcome.retries_used == 0
    assert outcome.attempts[0].error is None


def test_execution_error_is_fed_back_and_corrected(db):
    # Attempt 1 references a column that doesn't exist; DuckDB's error is
    # fed back and the scripted correction fixes the column name.
    runner = AssistingRunner(db, max_attempts=3)
    client = ScriptableFakeLLMClient(
        {QUESTION: ["SELECT COUNT(*) FROM trips WHERE no_such_col = 1", GOOD_SQL]}
    )
    outcome = runner.ask(client, "prompt", QUESTION)
    assert outcome.result.rows[0][0] == db.row_count("trips")
    assert outcome.retries_used == 1
    assert "no_such_col" in outcome.attempts[0].error
    # The correction call saw the failed SQL and the error.
    assert client.corrections[0][1].startswith("SELECT COUNT(*)")
    assert "no_such_col" in client.corrections[0][2]


def test_guardrail_rejection_is_also_retried(db):
    runner = AssistingRunner(db, max_attempts=3)
    client = ScriptableFakeLLMClient({QUESTION: ["DELETE FROM trips", GOOD_SQL]})
    outcome = runner.ask(client, "prompt", QUESTION)
    assert outcome.result.rows[0][0] == db.row_count("trips")
    assert "UnsafeSQLError" in outcome.attempts[0].error


def test_gives_up_after_max_attempts_with_history(db):
    runner = AssistingRunner(db, max_attempts=2)
    client = ScriptableFakeLLMClient(
        {QUESTION: ["SELECT nope FROM trips", "SELECT alsono FROM trips"]}
    )
    with pytest.raises(AssistantError) as excinfo:
        runner.ask(client, "prompt", QUESTION)
    assert len(excinfo.value.attempts) == 2
    assert all(a.error for a in excinfo.value.attempts)


def test_plain_fake_client_is_reasked_without_correction_method(db):
    # FakeLLMClient has no generate_correction; the runner falls back to
    # asking again (and gets the same canned answer, which here is good).
    runner = AssistingRunner(db, max_attempts=3)
    outcome = runner.ask(FakeLLMClient({QUESTION: GOOD_SQL}), "prompt", QUESTION)
    assert outcome.result.row_count == 1


def test_client_failure_is_not_retried(db):
    runner = AssistingRunner(db, max_attempts=3)
    with pytest.raises(LLMError):
        runner.ask(FakeLLMClient({}), "prompt", QUESTION)


def test_max_attempts_must_be_positive(db):
    with pytest.raises(ValueError):
        AssistingRunner(db, max_attempts=0)


def test_retries_are_logged(db, caplog):
    import logging

    runner = AssistingRunner(db, max_attempts=3)
    client = ScriptableFakeLLMClient(
        {QUESTION: ["SELECT COUNT(*) FROM trips WHERE no_such_col = 1", GOOD_SQL]}
    )
    with caplog.at_level(logging.WARNING, logger="nlsql.engine.runner"):
        runner.ask(client, "prompt", QUESTION)
    assert any("attempt 1" in r.message for r in caplog.records)
    assert any("no_such_col" in r.message for r in caplog.records)
