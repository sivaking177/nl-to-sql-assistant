"""HTTP service tests: the real FastAPI app, exercised through its
TestClient, backed by the offline fake LLM client and the bundled sample.
No API key, no network, no live server process needed."""

import pytest
from fastapi.testclient import TestClient

from nlsql.api.service import create_app
from nlsql.config import DEFAULT_EVAL_FILE
from nlsql.engine.evaluate import load_cases
from nlsql.llm import FakeLLMClient, ScriptableFakeLLMClient

CASES = load_cases(DEFAULT_EVAL_FILE)
QUESTION = "How many trips were paid for by credit card?"


@pytest.fixture(scope="module")
def http() -> TestClient:
    fake = FakeLLMClient({c.question: c.fake_sql for c in CASES})
    return TestClient(create_app(client=fake))


def test_health(http):
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_schema_endpoint_shows_the_prompt(http):
    payload = http.get("/schema").json()
    assert set(payload["tables"]) == {"trips", "taxi_zones"}
    assert "TABLE trips" in payload["system_prompt"]
    assert "1 = credit card" in payload["system_prompt"]


def test_ask_returns_sql_explanation_and_rows(http):
    response = http.post("/ask", json={"question": QUESTION})
    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == QUESTION
    assert "payment_type = 1" in payload["sql"]
    assert payload["explanation"]["tables"] == ["trips"]
    assert payload["explanation"]["aggregations"] == ["COUNT"]
    assert payload["attempts"] == 1
    # Same number the CLI smoke check produces for this question.
    assert payload["rows"][0][0] == 38065


def test_ask_empty_question_is_a_422(http):
    response = http.post("/ask", json={"question": ""})
    assert response.status_code == 422


def test_ask_unknown_question_is_a_502(http):
    # The fake client has no answer: an upstream failure, not a bad request.
    response = http.post("/ask", json={"question": "What is the moon made of?"})
    assert response.status_code == 502


def test_ask_recovers_through_the_retry_loop():
    target = next(c for c in CASES if c.id == "credit-card-trips")
    scripted = ScriptableFakeLLMClient(
        {
            target.question: [
                "SELECT COUNT(*) FROM trips WHERE nope = 1",
                target.fake_sql,
            ]
        }
    )
    http = TestClient(create_app(client=scripted))
    payload = http.post("/ask", json={"question": target.question}).json()
    assert payload["attempts"] == 2
    assert payload["rows"][0][0] == 38065


def test_ask_unfixable_sql_is_a_422_with_attempts():
    scripted = ScriptableFakeLLMClient({QUESTION: ["DELETE FROM trips"]})
    http = TestClient(create_app(client=scripted))
    response = http.post("/ask", json={"question": QUESTION})
    assert response.status_code == 422
    attempts = response.json()["detail"]["attempts"]
    assert len(attempts) == 3  # default max_attempts
    assert "UnsafeSQLError" in attempts[0]["error"]
