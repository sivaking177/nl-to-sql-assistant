"""LLM client abstraction.

The assistant talks to a language model through one narrow method --
``generate_sql(system_prompt, question) -> str`` -- with these
implementations:

- :class:`AnthropicClient` calls the Claude API (key from the
  ``ANTHROPIC_API_KEY`` environment variable). This is the production path.
- :class:`FakeLLMClient` returns one canned SQL string per known question.
  It exists so the evaluation harness and the whole test-suite run offline
  and deterministically; it is also genuinely useful for demos without a key.
- :class:`ScriptableFakeLLMClient` returns a *sequence* of answers per
  question. I added it to test the runner's retry loop: a script can say
  "first answer is broken SQL, second answer is the fix", which is exactly
  the situation the retry loop exists for.

Clients may also implement ``generate_correction(...)``: given a failed
query and the error it produced, return a corrected query. The runner uses
it when present and falls back to plain ``generate_sql`` when it isn't.

All clients return raw model text; :func:`extract_sql` normalises it
(models like to wrap SQL in markdown fences even when told not to).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    # Only for annotations -- the SDK itself is imported lazily in
    # AnthropicClient so the package works offline without it.
    from anthropic.types import MessageParam


class LLMError(RuntimeError):
    """Raised when the LLM cannot produce a response."""


class LLMClient(Protocol):
    def generate_sql(self, system_prompt: str, question: str) -> str:
        """Return the model's SQL for *question* (raw, unvalidated)."""
        ...


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_sql(text: str) -> str:
    """Pull the SQL out of a model response (strip fences/explanations)."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


class AnthropicClient:
    """Live client backed by the Anthropic Messages API."""

    def __init__(self, api_key: str | None, model: str) -> None:
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set; the live client needs a key. "
                "Use the fake client (CLI: `eval` without --live) offline."
            )
        # Imported lazily so the package (and tests) work without the SDK's
        # network machinery ever being touched offline.
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _complete(self, system_prompt: str, messages: list[MessageParam]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError("model returned an empty response")
        return extract_sql(text)

    def generate_sql(self, system_prompt: str, question: str) -> str:
        return self._complete(system_prompt, [{"role": "user", "content": question}])

    def generate_correction(
        self,
        system_prompt: str,
        question: str,
        failed_sql: str,
        error: str,
    ) -> str:
        """Ask the model to fix *failed_sql* given the error it produced.

        The failed query and the database/guardrail error go back verbatim:
        in practice the error message ("column fare_amnt does not exist")
        is the single most useful thing the model can be shown.
        """
        follow_up = (
            f"{question}\n\n"
            f"Your previous SQL was:\n{failed_sql}\n\n"
            f"It failed with this error:\n{error}\n\n"
            "Return one corrected SQL query and nothing else."
        )
        return self._complete(system_prompt, [{"role": "user", "content": follow_up}])


class FakeLLMClient:
    """Deterministic offline client: canned SQL per question.

    Questions are matched case-insensitively with whitespace normalised, the
    same way the eval file keys them. An unknown question raises
    :class:`LLMError`, mirroring a real client failing rather than silently
    inventing an answer.
    """

    def __init__(self, answers: dict[str, str]) -> None:
        self._answers = {_normalise(q): sql for q, sql in answers.items()}
        self.calls: list[str] = []

    def generate_sql(self, system_prompt: str, question: str) -> str:
        self.calls.append(question)
        try:
            return self._answers[_normalise(question)]
        except KeyError:
            raise LLMError(
                f"FakeLLMClient has no canned answer for: {question!r}"
            ) from None


class ScriptableFakeLLMClient:
    """Fake client that plays a script of answers per question.

    ``scripts`` maps a question to the list of SQL strings to return, in
    order, across ``generate_sql`` and ``generate_correction`` calls. When
    the script runs out, the last answer repeats (so a one-entry script
    behaves like :class:`FakeLLMClient`). This is how the retry-loop tests
    stage a model that gets it wrong first and right second -- without the
    script mechanism there would be no deterministic way to test recovery.
    """

    def __init__(self, scripts: dict[str, list[str]]) -> None:
        self._scripts = {_normalise(q): list(answers) for q, answers in scripts.items()}
        self._positions: dict[str, int] = {}
        self.calls: list[str] = []
        self.corrections: list[tuple[str, str, str]] = []

    def _next_answer(self, question: str) -> str:
        key = _normalise(question)
        if key not in self._scripts or not self._scripts[key]:
            raise LLMError(f"ScriptableFakeLLMClient has no script for: {question!r}")
        position = self._positions.get(key, 0)
        self._positions[key] = position + 1
        script = self._scripts[key]
        return script[min(position, len(script) - 1)]

    def generate_sql(self, system_prompt: str, question: str) -> str:
        self.calls.append(question)
        return self._next_answer(question)

    def generate_correction(
        self,
        system_prompt: str,
        question: str,
        failed_sql: str,
        error: str,
    ) -> str:
        self.corrections.append((question, failed_sql, error))
        return self._next_answer(question)


def _normalise(question: str) -> str:
    return " ".join(question.strip().lower().split())
