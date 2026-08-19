"""Runtime configuration, resolved from environment variables.

Nothing in this module is required for the offline test-suite: the defaults
point at the sample dataset bundled in ``data/`` and the fake LLM client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_EVAL_FILE = PROJECT_ROOT / "eval" / "questions.json"


@dataclass(frozen=True)
class Settings:
    """Settings for one assistant session."""

    data_dir: Path = DEFAULT_DATA_DIR
    trips_file: str = "yellow_tripdata_sample.parquet"
    zones_file: str = "taxi_zone_lookup.csv"
    # Hard cap on rows any single query may return to the caller.
    max_rows: int = 1_000
    # How many SQL attempts the ask path makes before giving up: the first
    # try plus error-feedback retries (see nlsql.engine.runner.AssistingRunner).
    max_attempts: int = 3
    # Claude model used by the live client.
    model: str = os.environ.get("NLSQL_MODEL", "claude-sonnet-4-5")
    # Anthropic API key; only needed for the live client.
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(api_key=os.environ.get("ANTHROPIC_API_KEY"))
