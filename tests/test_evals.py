"""Offline checks for the question file, metric switches, and Langfuse upsert."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import ConversationalTestCase, LLMTestCase
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ValidationError

from tilik_agent.agent.runner import QuestionResult
from tilik_agent.config import (
    EvalsMetricsConfig,
    JudgeMetricConfig,
    LlmConfig,
    StatusMetricConfig,
    get_agent_config,
)
from tilik_agent.evals.dataset import ConversationItem, load_benchmark, load_conversations, parse_benchmark
from tilik_agent.evals.judge import AgentJudgeLlm
from tilik_agent.evals.langfuse_dataset import sync_benchmark, sync_conversations
from tilik_agent.evals.metrics import argument_correctness_metric, conversation_metrics, task_completion_metric
from tilik_agent.evals.runner import (
    _check_scored,
    _enabled_conversation_names,
    _prune,
    _run_conversations_published,
    _run_published,
    _sample_items,
    score_conversation,
    score_question,
)
from tilik_agent.evals.scores import ItemScore, assemble_scores, pipeline_status_label


@tool
def search_datasets(query: str) -> str:
    """Search the dataset catalog."""
    return '{"datasets": [{"id": 7, "title": "Kepadatan Penduduk menurut Provinsi"}]}'


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
    It is the demography and social benchmark, item ids are unique, it includes the notebook question,
    and the age-group regression item carries its ground truth.
    """
    dataset = load_benchmark()
    ids = [item.id for item in dataset.items]
    assert dataset.name == "demografi-sosial-questions"
    assert len(ids) == len(set(ids))
    assert any("tiga TPT tertinggi" in item.question for item in dataset.items)
    age_item = next(item for item in dataset.items if item.id == "11")
    assert "22.752,6" in (age_item.expected_response or "")


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


def test_pipeline_status_succeeds_when_no_lookup_was_needed() -> None:
    """
    Condition:
    A direct answer finished without planned steps or a plan error.

    Expected:
    Pipeline status is succeeded with a no-lookup comment.
    """
    label, comment = pipeline_status_label({}, plan_error=None)
    assert label == "succeeded"
    assert comment == "No lookup was needed"


def test_pipeline_status_fails_when_plan_error_and_no_steps() -> None:
    """
    Condition:
    Planning failed before any step could run.

    Expected:
    Pipeline status stays failed and surfaces the plan error.
    """
    label, comment = pipeline_status_label({}, plan_error="Could not validate the work plan")
    assert label == "failed"
    assert comment == "Could not validate the work plan"


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


def test_agent_judge_llm_reads_the_evals_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A judge config names a model and an API key variable that is set.

    Expected:
    The DeepEval judge reports that model name.
    """
    monkeypatch.setenv("JUDGE_TEST_KEY", "test-key")
    judge = AgentJudgeLlm(
        LlmConfig(
            model="judge-model",
            base_url="https://example.test/v1",
            temperature=0,
            api_key_env="JUDGE_TEST_KEY",
            extra_body={"enable_thinking": False},
        )
    )
    assert judge.get_model_name() == "judge-model"


def test_agent_judge_uses_strict_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE_TEST_KEY", "test-key")
    judge = AgentJudgeLlm(LlmConfig(
        model="judge-model", base_url="https://example.test/v1", temperature=0,
        api_key_env="JUDGE_TEST_KEY", extra_body={},
    ))
    options: dict[str, Any] = {}

    class _Reason(BaseModel):
        reason: str

    class _Chat:
        def with_structured_output(self, schema: type[BaseModel], *, strict: bool) -> SimpleNamespace:
            options.update({"schema": schema, "strict": strict})
            return SimpleNamespace(invoke=lambda prompt: _Reason(reason=prompt))

    judge.model = _Chat()
    assert judge.generate("ok", _Reason).reason == "ok"
    assert options == {"schema": _Reason, "strict": True}


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
    score_question("berapa TPT", _DummyJudge())

    tools = argument_cases[0].tools_called
    assert [(call.name, call.input_parameters) for call in tools] == [
        ("search_datasets", {"query": "TPT Agustus 2025"})
    ]
    trajectory = task_cases[0]._trace_dict
    assert isinstance(trajectory, dict)

    assert tools[0].description and tools[0].output
    assert trajectory["input"] == "berapa TPT"

    def spans(span: dict[str, Any]) -> list[dict[str, Any]]:
        return [span] + [node for child in span.get("children", []) for node in spans(child)]

    names = [span["name"] for span in spans(trajectory)]
    assert not any(name.startswith(("ChannelWrite", "RunnableSequence", "__start__")) for name in names)
    tool_span = next(span for span in spans(trajectory) if span["name"] == "search_datasets")
    assert tool_span["description"] and tool_span["output"]


def test_prune_hoists_plumbing_and_drops_repeated_inputs() -> None:
    """
    Condition:
    A span tree has a plumbing span wrapping a graph node, which holds an LLM span and a tool span.

    Expected:
    The plumbing span is replaced by its children, the node and LLM keep only their output,
    and the tool keeps its input, output, and description.
    """
    tree = {
        "name": "RunnableSequence",
        "type": "base",
        "input": "state",
        "output": "state",
        "children": [
            {
                "name": "agent",
                "type": "base",
                "input": "state",
                "output": "delta",
                "children": [
                    {"name": "ChatOpenAI", "type": "llm", "input": "history", "output": "reply", "model": "m"},
                    {"name": "search_datasets", "type": "tool", "input": {"query": "x"}, "output": "rows"},
                ],
            }
        ],
    }
    [node] = _prune(tree, {"agent"})
    assert node["name"] == "agent" and "input" not in node and node["output"] == "delta"
    llm, tool_span = node["children"]
    assert llm == {"name": "ChatOpenAI", "type": "llm", "model": "m", "output": "reply"}
    assert tool_span["input"] == {"query": "x"} and tool_span["output"] == "rows" and tool_span["description"]


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


def test_conversation_dataset_and_default_metric_switches() -> None:
    dataset = load_conversations()
    assert dataset.name == "demografi-sosial-conversations"
    assert [len(item.user_turns) for item in dataset.items] == [3, 2, 2, 2]
    assert len({item.id for item in dataset.items}) == len(dataset.items)
    assert _enabled_conversation_names() == [
        "goal_accuracy", "tool_use", "conversation_completeness", "pipeline_status"
    ]
    with pytest.raises(ValidationError):
        ConversationItem(id="x", scenario="test", expected_outcome="test", user_turns=[])


def test_conversation_replays_generated_history_and_scores_once(monkeypatch: pytest.MonkeyPatch) -> None:
    histories: list[list[dict[str, str]]] = []
    sessions: list[str | None] = []
    measured: dict[str, list[ConversationalTestCase]] = {}

    def agent(question: str, *, history: list[dict[str, str]], conversation_id: str,
              callbacks: Sequence[Any]) -> QuestionResult:
        histories.append([dict(turn) for turn in history])
        sessions.append(conversation_id)
        result = _run_with_one_tool_call(question, callbacks=callbacks)
        result.answer = f"generated {len(histories)}"
        result.step_status = {"s1": {"status": "failed" if len(histories) == 2 else "succeeded",
                                     "error": "lookup failed" if len(histories) == 2 else ""}}
        return result

    def fake_metrics(*args: Any, **kwargs: Any) -> dict[str, _CapturingMetric]:
        names = ["goal_accuracy", "tool_use", "conversation_completeness"]
        return {name: _CapturingMetric(measured.setdefault(name, [])) for name in names}

    monkeypatch.setattr("tilik_agent.evals.runner.run_question", agent)
    monkeypatch.setattr("tilik_agent.evals.runner.conversation_metrics", fake_metrics)
    item = ConversationItem(
        id="c", scenario="follow up", expected_outcome="human-only description",
        user_turns=["August?", "February?", "Difference?"],
    )
    case, scores = score_conversation(item, _DummyJudge(), session_id="one-session")
    assert histories == [
        [],
        [{"role": "user", "content": "August?"}, {"role": "assistant", "content": "generated 1"}],
        [{"role": "user", "content": "August?"}, {"role": "assistant", "content": "generated 1"},
         {"role": "user", "content": "February?"}, {"role": "assistant", "content": "generated 2"}],
    ]
    assert sessions == ["one-session"] * 3
    assert [(turn.role, turn.content) for turn in case.turns] == [
        ("user", "August?"), ("assistant", "generated 1"),
        ("user", "February?"), ("assistant", "generated 2"),
        ("user", "Difference?"), ("assistant", "generated 3"),
    ]
    assert all(turn.tools_called[0].name == "search_datasets" for turn in case.turns[1::2])
    assert all(turn.tools_called[0].description for turn in case.turns[1::2])
    assert all(
        "Kepadatan Penduduk menurut Provinsi" in str(turn.tools_called[0].output)
        for turn in case.turns[1::2]
    )
    assert {name: len(cases) for name, cases in measured.items()} == {
        "goal_accuracy": 1, "tool_use": 1, "conversation_completeness": 1
    }
    assert [score.name for score in scores] == [
        "goal_accuracy", "tool_use", "conversation_completeness", "pipeline_status"
    ]
    assert scores[-1].value == "failed"
    assert "turn 2: lookup failed" in scores[-1].comment


def test_conversation_metric_builder_uses_all_agent_tools_and_optional_relevancy() -> None:
    config = get_agent_config().evals.conversation_metrics.model_copy(deep=True)
    judge = _DummyJudge()
    built = conversation_metrics(config, judge, history_messages=6)
    assert list(built) == ["goal_accuracy", "tool_use", "conversation_completeness"]
    names = {tool.name for tool in built["tool_use"].available_tools}
    assert {"search_datasets", "describe_dataset", "get_observations", "select_series"} <= names
    assert {"bnpb_query_series", "bmkg_earthquakes", "bapanas_prices_today", "sipongi_recent_hotspots"} <= names
    assert not any(name.startswith("bps_") for name in names)
    assert all(tool.description for tool in built["tool_use"].available_tools)
    config.turn_relevancy.enabled = True
    built = conversation_metrics(config, judge, history_messages=6)
    assert built["turn_relevancy"].window_size == 4


def test_sync_conversation_dataset_stores_script_and_human_context() -> None:
    dataset = load_conversations()

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def get_dataset(self, name: str) -> SimpleNamespace:
            return SimpleNamespace(items=[])

        def create_dataset_item(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(id=kwargs["id"])

    client = _Client()
    items = sync_conversations(client, dataset)
    assert len(items) == len(dataset.items)
    first = client.calls[0]
    assert first["input"] == dataset.items[0].user_turns
    assert first["expected_output"] == dataset.items[0].expected_outcome
    assert first["metadata"]["scenario"] == dataset.items[0].scenario


def test_published_conversation_run_outputs_generated_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = load_conversations()
    selected = dataset.items[1:2]  # The two-turn case.
    seen_outputs: list[dict[str, Any]] = []
    sessions: list[str | None] = []
    synced_ids: list[str] = []

    def agent(question: str, *, history: list[dict[str, str]], conversation_id: str,
              callbacks: Sequence[Any]) -> QuestionResult:
        sessions.append(conversation_id)
        return QuestionResult(answer=f"answer {len(history) // 2 + 1}")

    class _Client:
        def get_dataset(self, name: str) -> SimpleNamespace:
            return SimpleNamespace(items=[])

        def create_dataset_item(self, **kwargs: Any) -> SimpleNamespace:
            synced_ids.append(kwargs["id"])
            return SimpleNamespace(id=kwargs["id"], input=kwargs["input"], metadata=kwargs["metadata"])

        def run_experiment(self, **kwargs: Any) -> SimpleNamespace:
            assert len(kwargs["data"]) == 1
            assert kwargs["metadata"]["n_samples"] == "1"
            for item in kwargs["data"]:
                output = kwargs["task"](item=item)
                seen_outputs.append(output)
                evaluations = [evaluate(input=item.input, output=output, metadata=item.metadata)
                               for evaluate in kwargs["evaluators"]]
                assert [evaluation.name for evaluation in evaluations] == ["pipeline_status"]
            return SimpleNamespace(dataset_run_url="https://langfuse.test/run")

    monkeypatch.setattr("tilik_agent.evals.runner.run_question", agent)
    monkeypatch.setattr("tilik_agent.evals.runner._enabled_conversation_names", lambda: ["pipeline_status"])
    monkeypatch.setattr("langfuse.get_client", lambda: _Client())
    report = _run_conversations_published(dataset, None, selected=selected)
    assert synced_ids == [item.id for item in dataset.items]
    assert report.count("pipeline_status:") == 1
    assert "https://langfuse.test/run" in report
    assert sessions[0] == sessions[1]
    assert [turn["content"] for turn in seen_outputs[0]["turns"]] == [
        selected[0].user_turns[0], "answer 1", selected[0].user_turns[1], "answer 2"
    ]


def test_sample_count_is_reproducible_and_full_dataset_is_default() -> None:
    items = list(range(12))
    assert _sample_items(items, None, 42) == items
    assert _sample_items(items, 20, 42) == items
    assert _sample_items(items, 3, 42) == _sample_items(items, 3, 42)
    assert len(_sample_items(items, 3, 42)) == 3
    assert _sample_items(items, 3, 42) != _sample_items(items, 3, 7)
    with pytest.raises(ValueError, match="at least 1"):
        _sample_items(items, 0, 42)


def test_failed_experiment_item_reports_original_error() -> None:
    with pytest.raises(RuntimeError, match="c1: Connection error"):
        _check_scored({"c1"}, {}, {"c1": "Connection error"})


def test_published_question_sample_keeps_full_langfuse_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = load_benchmark()
    selected = dataset.items[:1]
    synced_ids: list[str] = []

    class _Client:
        def get_dataset(self, name: str) -> SimpleNamespace:
            return SimpleNamespace(items=[])

        def create_dataset_item(self, **kwargs: Any) -> SimpleNamespace:
            synced_ids.append(kwargs["id"])
            return SimpleNamespace(id=kwargs["id"], input=kwargs["input"], metadata=kwargs["metadata"])

        def run_experiment(self, **kwargs: Any) -> SimpleNamespace:
            assert len(kwargs["data"]) == 1
            item = kwargs["data"][0]
            output = kwargs["task"](item=item)
            score = kwargs["evaluators"][0](input=item.input, output=output, metadata=item.metadata)
            assert score.name == "pipeline_status"
            return SimpleNamespace(dataset_run_url="")

    monkeypatch.setattr("langfuse.get_client", lambda: _Client())
    monkeypatch.setattr("tilik_agent.evals.runner._enabled_names", lambda: ["pipeline_status"])
    monkeypatch.setattr("tilik_agent.evals.runner._score_item", lambda *args: (
        QuestionResult(answer="stub answer"), [ItemScore("pipeline_status", "succeeded", "", "CATEGORICAL")]
    ))
    report = _run_published(dataset, None, selected=selected)
    assert synced_ids == [item.id for item in dataset.items]
    assert report.count("pipeline_status:") == 1
