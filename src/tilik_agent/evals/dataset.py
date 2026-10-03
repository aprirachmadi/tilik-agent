"""Load the JSON question file that ``tilik benchmark`` upserts into Langfuse."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_DATASET_PATH = Path(__file__).parent / "datasets" / "demografi_sosial_questions.json"
_CONVERSATION_PATH = Path(__file__).parent / "datasets" / "demografi_sosial_conversations.json"


class BenchmarkItem(BaseModel):
    """One benchmark question. ``expected_response`` stays empty until a ground-truth answer exists."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_response: str | None = None


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


class ConversationItem(BaseModel):
    """Scripted user messages; assistant replies are generated during the run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    context: list[str] | None = None
    user_turns: list[str] = Field(min_length=1)


class ConversationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[ConversationItem] = Field(min_length=1)


def load_conversations(path: Path | None = None) -> ConversationDataset:
    """Read the scripted conversation file."""
    return ConversationDataset.model_validate(json.loads((path or _CONVERSATION_PATH).read_text(encoding="utf-8")))
