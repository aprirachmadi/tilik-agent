"""Load the JSON question file that ``tilik eval`` upserts into Langfuse."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_DATASET_PATH = Path(__file__).parent / "datasets" / "bps_questions.json"


class BenchmarkItem(BaseModel):
    """One question. Expected answers stay absent until ground truth exists."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    input: str = Field(min_length=1)


class BenchmarkDataset(BaseModel):
    """The question list and the version string stored on a Langfuse dataset run."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[BenchmarkItem] = Field(min_length=1)


def parse_benchmark(payload: object) -> BenchmarkDataset:
    """Validate a decoded question file."""
    return BenchmarkDataset.model_validate(payload)


def load_benchmark(path: Path | None = None) -> BenchmarkDataset:
    """Read the packaged question file, or ``path`` when a test supplies one."""
    dataset_path = path or _DATASET_PATH
    return parse_benchmark(json.loads(dataset_path.read_text(encoding="utf-8")))
