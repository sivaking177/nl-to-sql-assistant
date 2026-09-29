"""HTTP service: the assistant behind a small FastAPI API.

The CLI is fine for one person at a terminal. Anything else -- a web UI, a
notebook, another program -- wants HTTP. This module wraps the same
pipeline the CLI uses (same database, same prompt, same guardrails, same
retry loop) in three endpoints:

- ``GET  /health``  -- liveness probe, returns ``{"status": "ok"}``.
- ``GET  /schema``  -- the exact system prompt the model is given, so a
  caller can see what the assistant knows about the data.
- ``POST /ask``     -- ``{"question": "..."}`` in; the SQL, a plain-English
  explanation of it, the result rows, and the attempt count out.

Run it with ``uvicorn nlsql.api.service:app --port 8000`` (the Dockerfile and
docker-compose.yml in the repo root do exactly this). With no
``ANTHROPIC_API_KEY`` set it serves the offline fake client, same as the
CLI -- enough to click through the API shape, not to answer new questions.
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..config import DEFAULT_EVAL_FILE, Settings
from ..core.data import TripDatabase
from ..core.explain import explain_sql
from ..core.schema import build_system_prompt, introspect_schema
from ..engine.evaluate import load_cases
from ..engine.runner import AssistantError, AssistingRunner
from ..llm import AnthropicClient, FakeLLMClient, LLMClient, LLMError


class AskRequest(BaseModel):
    question: str = Field(min_length=1)  # an empty question is a 422


class AskResponse(BaseModel):
    question: str
    sql: str  # the SQL that actually ran (after guardrail LIMIT handling)
    explanation: dict[str, Any]  # QueryExplanation.to_dict()
    columns: list[str]
    rows: list[list[Any]]
    attempts: int  # 1 = the model's first answer worked


def _jsonable(value: Any) -> Any:
    """Make a DuckDB cell JSON-safe: dates and decimals become strings /
    floats, everything else JSON already understands passes through."""
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _default_client(settings: Settings) -> LLMClient:
    """Live client when a key is configured, the offline fake otherwise --
    the same choice the CLI makes."""
    if settings.api_key:
        return AnthropicClient(api_key=settings.api_key, model=settings.model)
    cases = load_cases(DEFAULT_EVAL_FILE)
    return FakeLLMClient({c.question: c.fake_sql for c in cases})


def create_app(
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> FastAPI:
    """Build the FastAPI app. *client* is injectable so tests can pass the
    fake client and exercise the real HTTP layer offline."""
    settings = settings or Settings.from_env()
    db = TripDatabase.from_settings(settings)
    client = client or _default_client(settings)
    system_prompt = build_system_prompt(introspect_schema(db.connection))
    runner = AssistingRunner(
        db, max_rows=settings.max_rows, max_attempts=settings.max_attempts
    )

    app = FastAPI(title="NL-to-SQL Assistant", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/schema")
    def schema() -> dict[str, Any]:
        return {
            "tables": db.table_names(),
            "system_prompt": system_prompt,
        }

    @app.post("/ask", response_model=AskResponse)
    def ask(request: AskRequest) -> AskResponse:
        try:
            outcome = runner.ask(client, system_prompt, request.question)
        except LLMError as exc:
            # The client couldn't produce SQL at all (no canned answer, or
            # the API is down): that's upstream's fault, not the caller's.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except AssistantError as exc:
            # SQL was produced but never passed validation/execution. 422:
            # the request was understood, the answer couldn't be built.
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "attempts": [
                        {"sql": a.sql, "error": a.error} for a in exc.attempts
                    ],
                },
            ) from exc
        result = outcome.result
        return AskResponse(
            question=request.question,
            sql=result.sql,
            explanation=explain_sql(result.sql).to_dict(),
            columns=result.columns,
            rows=[[_jsonable(v) for v in row] for row in result.rows],
            attempts=len(outcome.attempts),
        )

    return app


# uvicorn entry point: `uvicorn nlsql.api.service:app`
app = create_app()
