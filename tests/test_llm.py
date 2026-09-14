import pytest

from nlsql.llm import (
    FakeLLMClient,
    LLMError,
    ScriptableFakeLLMClient,
    extract_sql,
)


def test_fake_client_returns_canned_sql():
    client = FakeLLMClient({"How many trips?": "SELECT count(*) FROM trips"})
    assert client.generate_sql("prompt", "How many trips?") == (
        "SELECT count(*) FROM trips"
    )
    assert client.calls == ["How many trips?"]


def test_fake_client_question_matching_is_lenient():
    client = FakeLLMClient({"how   many trips?": "SELECT 1"})
    assert client.generate_sql("p", "How many trips?") == "SELECT 1"


def test_fake_client_unknown_question_raises():
    client = FakeLLMClient({})
    with pytest.raises(LLMError):
        client.generate_sql("p", "anything at all")


def test_extract_sql_strips_markdown_fences():
    assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("```\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("SELECT 1") == "SELECT 1"


def test_scriptable_client_plays_its_script_in_order():
    client = ScriptableFakeLLMClient({"Q?": ["SELECT bad", "SELECT good"]})
    assert client.generate_sql("p", "Q?") == "SELECT bad"
    # Corrections draw from the same script: fail-then-succeed staging.
    assert client.generate_correction("p", "Q?", "SELECT bad", "boom") == (
        "SELECT good"
    )
    assert client.corrections == [("Q?", "SELECT bad", "boom")]


def test_scriptable_client_repeats_last_answer_when_script_runs_out():
    client = ScriptableFakeLLMClient({"Q?": ["SELECT 1"]})
    assert client.generate_sql("p", "Q?") == "SELECT 1"
    assert client.generate_sql("p", "Q?") == "SELECT 1"


def test_scriptable_client_unknown_question_raises():
    client = ScriptableFakeLLMClient({})
    with pytest.raises(LLMError):
        client.generate_sql("p", "unscripted")
