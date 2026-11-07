"""Load the JSON question file that ``tilik benchmark`` upserts into Langfuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tilik_agent.agent.schemas import ChatTurn

_DATASET_PATH = Path(__file__).parent / "datasets" / "generation_benchmark.json"
_CONVERSATION_PATH = Path(__file__).parent / "datasets" / "demografi_sosial_conversations.json"


class BenchmarkItem(BaseModel):
    """One benchmark question. ``expected_response`` stays empty until a ground-truth answer exists."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard", "out_of_scope", "jailbreak"] | None = None
    sources: list[str] = Field(default_factory=list)
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


_JUDGE_PATH = Path(__file__).parent / "datasets" / "judge_benchmark.json"


class HumanLabel(BaseModel):
    """A person's verdict on one frozen answer."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail"]
    reason: str = Field(min_length=1)


class JudgeItem(BaseModel):
    """A saved answer, never regenerated, so its human label keeps describing the same text."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)
    generated_response: str = Field(min_length=1)
    # Tool name and arguments the agent used; full results stay in the traces file.
    generated_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    expected_response: str = Field(min_length=1)
    human_label: HumanLabel
    # Tool name and arguments the agent used; full results stay in the traces file.
    generated_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    judge_labels: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any]


class JudgeDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[JudgeItem] = Field(min_length=1)


def load_judge_benchmark(path: Path | None = None) -> JudgeDataset:
    """Read the judge benchmark; its recorded agent traces live beside it in ``*_traces.json``."""
    return JudgeDataset.model_validate(json.loads((path or _JUDGE_PATH).read_text(encoding="utf-8")))


def judge_traces_path(path: Path | None = None) -> Path:
    path = path or _JUDGE_PATH
    return path.with_name(f"{path.stem}_traces.json")
