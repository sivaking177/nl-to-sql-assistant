"""Command-line interface.

Usage:
    python -m nlsql.cli info                  # dataset summary
    python -m nlsql.cli schema                # print the system prompt
    python -m nlsql.cli ask "How many trips were paid by credit card?"
    python -m nlsql.cli eval                  # offline eval (fake client)
    python -m nlsql.cli eval --live           # eval against the Claude API

``ask`` uses the live Claude client when ANTHROPIC_API_KEY is set, and
otherwise falls back to the fake client, which can only answer the fixed
evaluation questions (by design -- it exists for offline verification).
``ask`` goes through the retry loop: if the first SQL fails, the error is
fed back to the model for another attempt (see
:class:`nlsql.engine.runner.AssistingRunner`).
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import DEFAULT_EVAL_FILE, Settings
from .core.data import TripDatabase
from .core.explain import explain_sql
from .core.schema import build_system_prompt, introspect_schema
from .engine.evaluate import (
    category_breakdown,
    load_cases,
    pass_rate,
    run_evaluation,
    summarise,
)
from .engine.runner import AssistantError, AssistingRunner, format_result
from .llm import AnthropicClient, FakeLLMClient, LLMClient


def _open_db(settings: Settings) -> TripDatabase:
    return TripDatabase.from_settings(settings)


def _fake_client(settings: Settings) -> FakeLLMClient:
    cases = load_cases(DEFAULT_EVAL_FILE)
    return FakeLLMClient({c.question: c.fake_sql for c in cases})


def _client(settings: Settings, live: bool) -> LLMClient:
    if live:
        return AnthropicClient(api_key=settings.api_key, model=settings.model)
    if settings.api_key:
        return AnthropicClient(api_key=settings.api_key, model=settings.model)
    return _fake_client(settings)


def cmd_info(settings: Settings) -> int:
    db = _open_db(settings)
    try:
        for table in db.table_names():
            print(f"{table}: {db.row_count(table):,} rows")
    finally:
        db.close()
    return 0


def cmd_schema(settings: Settings) -> int:
    db = _open_db(settings)
    try:
        print(build_system_prompt(introspect_schema(db.connection)))
    finally:
        db.close()
    return 0


def cmd_ask(settings: Settings, question: str, live: bool) -> int:
    db = _open_db(settings)
    try:
        client = _client(settings, live)
        prompt = build_system_prompt(introspect_schema(db.connection))
        runner = AssistingRunner(
            db,
            max_rows=settings.max_rows,
            max_attempts=settings.max_attempts,
        )
        try:
            outcome = runner.ask(client, prompt, question)
        except AssistantError as exc:
            # Report the attempts here and return a failure code; an
            # earlier version re-raised, so the user got this report AND
            # main()'s generic "error:" line for the same failure.
            for attempt in exc.attempts:
                print(
                    f"attempt failed: {attempt.sql}\n  -> {attempt.error}",
                    file=sys.stderr,
                )
            return 1
        result = outcome.result
        print(f"SQL: {result.sql}")
        print(f"Plan: {explain_sql(result.sql).summary()}")
        if outcome.retries_used:
            print(f"(needed {len(outcome.attempts)} attempts)")
        print()
        print(format_result(result))
    finally:
        db.close()
    return 0


def cmd_eval(settings: Settings, live: bool) -> int:
    db = _open_db(settings)
    try:
        cases = load_cases(DEFAULT_EVAL_FILE)
        client = _client(settings, live)
        results = run_evaluation(db, client, cases, max_rows=settings.max_rows)
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"[{status}] {result.case_id} ({result.category}, "
                f"{result.latency_ms:.1f} ms)"
            )
            for failure in result.failures:
                print(f"       - {failure}")
        passed, total = summarise(results)
        print(f"\n{passed}/{total} cases passed ({pass_rate(results):.1%})")
        for category, (cat_passed, cat_total) in category_breakdown(results).items():
            print(f"  {category}: {cat_passed}/{cat_total}")
        if results:
            average_ms = sum(r.latency_ms for r in results) / len(results)
            print(
                f"average latency: {average_ms:.1f} ms per case "
                "(generation + execution)"
            )
        return 0 if passed == total else 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nlsql", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="dataset summary")
    sub.add_parser("schema", help="print the schema-aware system prompt")

    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--live", action="store_true", help="force the Claude API")

    ev = sub.add_parser("eval", help="run the fixed evaluation set")
    ev.add_argument(
        "--live",
        action="store_true",
        help="run against the Claude API instead of the fake client",
    )

    args = parser.parse_args(argv)
    # The library modules log through the stdlib logging module (retries,
    # corrections); the CLI is where a handler gets configured. The API
    # service leaves configuration to uvicorn, as it should.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    try:
        if args.command == "info":
            return cmd_info(settings)
        if args.command == "schema":
            return cmd_schema(settings)
        if args.command == "ask":
            return cmd_ask(settings, args.question, args.live)
        if args.command == "eval":
            return cmd_eval(settings, args.live)
    except Exception as exc:  # noqa: BLE001 -- top-level CLI error surface
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
