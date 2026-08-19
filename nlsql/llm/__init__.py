"""LLM clients: the live Claude client and the offline fakes.

The public names live in :mod:`nlsql.llm.clients` and are re-exported
here, so callers write ``from nlsql.llm import FakeLLMClient`` regardless
of which file the class sits in.
"""

from .clients import (
    AnthropicClient,
    FakeLLMClient,
    LLMClient,
    LLMError,
    ScriptableFakeLLMClient,
    extract_sql,
)

__all__ = [
    "AnthropicClient",
    "FakeLLMClient",
    "LLMClient",
    "LLMError",
    "ScriptableFakeLLMClient",
    "extract_sql",
]
