"""Offline checks for the question file, metric switches, and Langfuse upsert."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from tilik_agent.agent.runner import QuestionResult
from tilik_agent.config import (
    EvalsMetricsConfig,
    JudgeMetricConfig,
    LlmConfig,
    StatusMetricConfig,
    get_agent_config,
)
from tilik_agent.evals.dataset import load_benchmark, parse_benchmark
from tilik_agent.evals.judge import YamlJudge
from tilik_agent.evals.langfuse_dataset import sync_benchmark
from tilik_agent.evals.metrics import argument_correctness_metric, task_completion_metric
from tilik_agent.evals.runner import _score_question
from tilik_agent.evals.scores import ItemScore, assemble_scores, pipeline_status_label


@tool
def search_datasets(query: str) -> str:
    """Search the dataset catalog."""
    return '{"datasets": [{"id": 7}]}'


class _CapturingMetric:
    def __init__(self, cases: list[LLMTestCase]) -> None:
        self.cases = cases
        self.score = 1.0
        self.reason = "ok"

    def measure(self, case: LLMTestCase) -> None:
        self.cases.append(case)


def _run_with_one_tool_call(question: str, *, callbacks: Sequence[Any] | None = None) -> QuestionResult:
    def agent(state: MessagesState) -> dict[str, Any]:
        call = {"name": "search_datasets", "args": {"query": "TPT Agustus 2025"}, "id": "c1"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode([search_datasets]))
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "tools")
    graph.add_edge("tools", END)
    graph.compile().invoke({"messages": [HumanMessage(content=question)]}, config={"callbacks": list(callbacks or [])})
    return QuestionResult(answer="Papua 6,96", step_status={"s1": {"status": "succeeded", "error": ""}})


class _DummyJudge(DeepEvalBaseLLM):
    def load_model(self) -> object:
        return object()

    def generate(self, prompt: str, schema: type | None = None) -> str:
        return "{}"

    async def a_generate(self, prompt: str, schema: type | None = None) -> str:
        return "{}"

    def get_model_name(self) -> str:
        return "dummy"


def _metrics(*, argument_correctness: bool = True, pipeline_status: bool = True) -> EvalsMetricsConfig:
    return EvalsMetricsConfig(
        task_completion=JudgeMetricConfig(enabled=True, threshold=0.5),
        argument_correctness=JudgeMetricConfig(enabled=argument_correctness, threshold=0.5),
        pipeline_status=StatusMetricConfig(enabled=pipeline_status),
    )


def test_benchmark_file_contains_the_notebook_question() -> None:
    """
    Condition:
    The packaged question file is loaded.

    Expected:
    It is the demography and social benchmark, item ids are unique, and it includes the notebook question.
    """
    dataset = load_benchmark()
    ids = [item.id for item in dataset.items]
    assert dataset.name == "demografi-sosial-questions"
    assert len(ids) == len(set(ids))
    assert any("tiga TPT tertinggi" in item.question for item in dataset.items)
    assert all(item.expected_response is None for item in dataset.items)


def test_benchmark_rejects_an_item_without_question() -> None:
    """
    Condition:
    A question file item has an id and no question.

    Expected:
    Validation fails.
    """
    payload = {"name": "demografi-sosial-questions", "version": "1", "items": [{"id": "1"}]}
    with pytest.raises(ValidationError):
        parse_benchmark(payload)


def test_expected_response_defaults_to_none() -> None:
    """
    Condition:
    A question item omits expected_response.

    Expected:
    The loaded item keeps that field empty.
    """
    payload = {
        "name": "demografi-sosial-questions",
        "version": "1",
        "items": [{"id": "1", "question": "Berapa jumlah penduduk?"}],
    }
    assert parse_benchmark(payload).items[0].expected_response is None


def test_disabled_argument_metric_is_omitted() -> None:
    """
    Condition:
    Argument correctness is disabled and a dummy judge is supplied.

    Expected:
    Only the task-completion metric is built.
    """
    judge = _DummyJudge()
    metrics = _metrics(argument_correctness=False)
    assert task_completion_metric(metrics, judge) is not None
    assert argument_correctness_metric(metrics, judge) is None


def test_disabled_metric_is_omitted_from_scores() -> None:
    """
    Condition:
    Argument correctness is disabled, and both a judge score and a step status are available.

    Expected:
    The score list keeps task completion and pipeline status only.
    """
    scores = assemble_scores(
        _metrics(argument_correctness=False),
        task_completion=ItemScore("task_completion", 0.8, "answered", "NUMERIC"),
        argument_correctness=ItemScore("argument_correctness", 0.2, "wrong year", "NUMERIC"),
        step_status={"s1": {"status": "succeeded", "error": ""}},
        plan_error=None,
    )
    assert [score.name for score in scores] == ["task_completion", "pipeline_status"]
    assert scores[1].value == "succeeded"


def test_pipeline_status_is_partial_when_steps_disagree() -> None:
    """
    Condition:
    One planned step succeeded and another failed.

    Expected:
    The raw status is partial and the comment includes the step error.
    """
    label, comment = pipeline_status_label(
        {"s1": {"status": "succeeded", "error": ""}, "s2": {"status": "failed", "error": "no rows"}}
    )
    assert label == "partial"
    assert comment == "no rows"


def test_yaml_judge_reads_the_evals_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A judge config names a model and an API key variable that is set.

    Expected:
    The DeepEval judge reports that model name.
    """
    monkeypatch.setenv("JUDGE_TEST_KEY", "test-key")
    judge = YamlJudge(
        LlmConfig(
            model="judge-model",
            base_url="https://example.test/v1",
            temperature=0,
            api_key_env="JUDGE_TEST_KEY",
            extra_body={"enable_thinking": False},
        )
    )
    assert judge.get_model_name() == "judge-model"


def test_sync_upserts_question_ids_and_archives_removals() -> None:
    """
    Condition:
    Langfuse has one old item, and the JSON file has a different question.

    Expected:
    The new id is upserted and the old id is archived.
    """
    dataset = load_benchmark()

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self._created = False

        def get_dataset(self, name: str) -> SimpleNamespace:
            if not self._created:
                raise RuntimeError("missing")
            return SimpleNamespace(items=[SimpleNamespace(id="old-question", input="gone", status="ACTIVE")])

        def create_dataset(self, *, name: str, description: str) -> None:
            self._created = True

        def create_dataset_item(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(id=kwargs["id"])

    client = _Client()
    created = sync_benchmark(client, dataset)
    assert created[0].id == "1"
    assert client.calls[0]["id"] == "1"
    archived = next(call for call in client.calls if call["id"] == "old-question")
    assert getattr(archived["status"], "value", archived["status"]) == "ARCHIVED"


def test_judges_receive_the_recorded_tool_calls_and_trajectory(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A question run makes one real tool call while the DeepEval handler records it, and both judges are enabled.

    Expected:
    Argument correctness receives that tool call with its arguments, and task completion receives the
    trajectory as a span dict that contains the tool span.
    """
    task_cases: list[LLMTestCase] = []
    argument_cases: list[LLMTestCase] = []
    monkeypatch.setattr("tilik_agent.evals.runner.run_question", _run_with_one_tool_call)
    monkeypatch.setattr("tilik_agent.evals.runner.task_completion_metric", lambda *_: _CapturingMetric(task_cases))
    monkeypatch.setattr(
        "tilik_agent.evals.runner.argument_correctness_metric", lambda *_: _CapturingMetric(argument_cases)
    )
    _score_question("berapa TPT", _DummyJudge())

    tools = argument_cases[0].tools_called
    assert [(call.name, call.input_parameters) for call in tools] == [
        ("search_datasets", {"query": "TPT Agustus 2025"})
    ]
    trajectory = task_cases[0]._trace_dict
    assert isinstance(trajectory, dict)

    def span_names(span: dict[str, Any]) -> list[str]:
        return [span.get("name", "")] + [name for child in span["children"] for name in span_names(child)]

    assert "search_datasets" in span_names(trajectory)


def test_loaded_config_enables_the_three_metrics() -> None:
    """
    Condition:
    The default yaml evals block is loaded.

    Expected:
    Task completion, argument correctness, and pipeline status start enabled.
    """
    metrics = get_agent_config().evals.metrics
    assert metrics.task_completion.enabled is True
    assert metrics.argument_correctness.threshold == 0.5
    assert metrics.pipeline_status.enabled is True
