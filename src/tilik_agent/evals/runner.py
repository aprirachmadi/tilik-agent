"""Run the question file through the agent and score the enabled metrics.

This module is the only place that calls the judges. ``run_question`` stays a
normal question call. ``score_question`` scores a single question.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from deepeval.integrations.langchain import CallbackHandler
from deepeval.test_case import ConversationalTestCase, LLMTestCase, Turn
from deepeval.tracing import trace_manager

from tilik_agent.agent.graph import compile_graph
from tilik_agent.agent.runner import QuestionResult, run_question
from tilik_agent.agent.schemas import ChatTurn
from tilik_agent.agent.sql import SQL_TOOLS, get_sql_agent
from tilik_agent.agent.tracing import langfuse_configured
from tilik_agent.config import get_agent_config
from tilik_agent.evals.dataset import (
    BenchmarkDataset,
    BenchmarkItem,
    ConversationDataset,
    ConversationItem,
    load_benchmark,
    load_conversations,
)
from tilik_agent.evals.judge import AgentJudgeLlm
from tilik_agent.evals.langfuse_dataset import sync_benchmark, sync_conversations
from tilik_agent.evals.metrics import argument_correctness_metric, conversation_metrics, task_completion_metric
from tilik_agent.evals.scores import ItemScore, assemble_scores, format_report, pipeline_status_label

_TOOL_DESCRIPTIONS = {tool.name: tool.description for tool in SQL_TOOLS}
_Item = TypeVar("_Item")


def _sample_items(items: list[_Item], n_samples: int | None, seed: int) -> list[_Item]:
    """Choose a stable subset while preserving the dataset's display order."""
    if n_samples is not None and n_samples < 1:
        raise ValueError("n_samples must be at least 1")
    if n_samples is None or n_samples >= len(items):
        return items
    positions = set(random.Random(seed).sample(range(len(items)), n_samples))
    return [item for index, item in enumerate(items) if index in positions]


def run_benchmark(*, n_samples: int | None = None, seed: int = 42) -> str:
    """Score every question in the JSON file and upload the run when Langfuse is configured."""
    dataset = load_benchmark()
    selected = _sample_items(dataset.items, n_samples, seed)
    judge = build_judge()
    if not langfuse_configured():
        _progress(f"Scoring {len(selected)}/{len(dataset.items)} questions")
        rows = []
        for item in selected:
            _, scores = _score_item(item.id, item.question, judge)
            rows.append((item.id, scores))
        return format_report(rows) + "\n\nLangfuse upload skipped"
    return _run_published(dataset, judge, selected=selected, seed=seed)


def run_conversation_benchmark(*, n_samples: int | None = None, seed: int = 42) -> str:
    """Replay scripted conversations and score each complete transcript once."""
    dataset = load_conversations()
    selected = _sample_items(dataset.items, n_samples, seed)
    judge = build_conversation_judge()
    if not langfuse_configured():
        _progress(f"Scoring {len(selected)}/{len(dataset.items)} conversations")
        rows = []
        for item in selected:
            _, scores = _score_conversation_item(item, judge)
            rows.append((item.id, scores))
        return format_report(rows) + "\n\nLangfuse upload skipped"
    return _run_conversations_published(dataset, judge, selected=selected, seed=seed)


def _score_item(
    item_id: str, question: str, judge: AgentJudgeLlm | None
) -> tuple[QuestionResult, list[ItemScore]]:
    _progress(f"[{item_id}] {question}")
    result, scores = score_question(question, judge)
    if item_id in _ANALYSIS_FACTS:
        scores.append(_analysis_facts_score(item_id, result))
    _progress(format_report([(item_id, scores)]))
    return result, scores


_ANALYSIS_FACTS = {
    "12": ("38", "0,565", "positif"),
    "13": ("38", "0,572", "negatif"),
    "14": ("2017", "2026", "0,531", "0,618", "34", "38", "papua", "batas"),
    "15": ("34", "0,902", "poin persentase"),
    "16": ("2017", "2020", "2021", "2025", "20/34", "14/38"),
    "17": ("bali", "3,38", "residual"),
    "18": ("38", "0,801", "sebab"),
}


def _analysis_facts_score(item_id: str, result: QuestionResult) -> ItemScore:
    answer = (result.answer or "").casefold().replace(".", ",").replace("−", "-")
    missing = [fact for fact in _ANALYSIS_FACTS[item_id] if fact not in answer]
    if not result.answer_analysis_result_ids:
        missing.append("verified analysis result")
    return ItemScore(
        "analysis_facts",
        "passed" if not missing else "failed",
        "" if not missing else "Missing: " + ", ".join(missing),
        "CATEGORICAL",
    )


def build_judge() -> AgentJudgeLlm | None:
    """Return the judge from ``evals.judge``, or none when both judge metrics are disabled."""
    config = get_agent_config()
    metrics = config.evals.metrics
    if metrics.task_completion.enabled or metrics.argument_correctness.enabled:
        return AgentJudgeLlm(config.evals.judge)
    return None


def build_conversation_judge() -> AgentJudgeLlm | None:
    """Build a judge only when a conversation judge metric is enabled."""
    config = get_agent_config().evals
    metrics = config.conversation_metrics
    if any((metrics.goal_accuracy.enabled, metrics.tool_use.enabled,
            metrics.conversation_completeness.enabled, metrics.turn_relevancy.enabled)):
        return AgentJudgeLlm(config.judge)
    return None


def score_conversation(
    item: ConversationItem, judge: AgentJudgeLlm | None, *, session_id: str | None = None
) -> tuple[ConversationalTestCase, list[ItemScore]]:
    """Generate all assistant turns before judging the full conversation."""
    config = get_agent_config()
    history: list[ChatTurn] = []
    turns: list[Turn] = []
    statuses: list[tuple[str, str]] = []
    session_id = session_id or f"benchmark-{uuid4()}"
    for number, question in enumerate(item.user_turns, start=1):
        handler = CallbackHandler(name="tilik-benchmark-conversation")
        _progress(f"  turn {number}: {question}")
        result = run_question(question, history=history, conversation_id=session_id, callbacks=[handler])
        answer = result.answer or ""
        _progress(f"  answer:\n{answer}")
        turns.extend((Turn(role="user", content=question), Turn(
            role="assistant", content=answer,
            tools_called=_tools_called(handler),
        )))
        history.extend(({"role": "user", "content": question}, {"role": "assistant", "content": answer}))
        statuses.append(pipeline_status_label(result.step_status, result.plan_error))
    case = ConversationalTestCase(
        scenario=item.scenario, expected_outcome=item.expected_outcome,
        context=item.context, turns=turns,
    )
    scores: list[ItemScore] = []
    if judge is not None:
        _progress("  judging")
        for name, metric in conversation_metrics(
            config.evals.conversation_metrics, judge,
            history_messages=config.conversation.max_history_messages,
        ).items():
            _progress(f"  judging {name}")
            metric.measure(case)
            scores.append(ItemScore(name, float(metric.score), metric.reason or "", "NUMERIC"))
    if config.evals.conversation_metrics.pipeline_status.enabled:
        label = min((status for status, _ in statuses), key={"failed": 0, "partial": 1, "succeeded": 2}.get)
        comments = [f"turn {number}: {comment}" for number, (_, comment) in enumerate(statuses, start=1) if comment]
        scores.append(ItemScore("pipeline_status", label, "; ".join(comments), "CATEGORICAL"))
    return case, scores


def _score_conversation_item(
    item: ConversationItem, judge: AgentJudgeLlm | None
) -> tuple[ConversationalTestCase, list[ItemScore]]:
    _progress(f"[{item.id}] {item.scenario}")
    case, scores = score_conversation(item, judge)
    _progress(format_report([(item.id, scores)]))
    return case, scores


def score_question(question: str, judge: AgentJudgeLlm | None) -> tuple[QuestionResult, list[ItemScore]]:
    """Answer one question and score it with the metrics enabled in ``evals.metrics``."""
    metrics = get_agent_config().evals.metrics
    handler = None
    callbacks = None
    if judge is not None:
        handler = CallbackHandler(name="tilik-eval")
        callbacks = [handler]
    _progress("  answering")
    result = run_question(question, callbacks=callbacks)
    _progress(f"  answer:\n{result.answer}")
    if judge is not None:
        _progress("  judging")
    task_score = _task_score(question, result, handler, judge) if metrics.task_completion.enabled else None
    argument_score = (
        _argument_score(question, handler, judge) if metrics.argument_correctness.enabled else None
    )
    scores = assemble_scores(
        metrics,
        task_completion=task_score,
        argument_correctness=argument_score,
        step_status=result.step_status,
        plan_error=result.plan_error,
    )
    return result, scores


def _task_score(
    question: str, result: QuestionResult, handler: CallbackHandler | None, judge: AgentJudgeLlm | None
) -> ItemScore:
    if judge is None:
        raise RuntimeError("task completion was scored while disabled")
    metric = task_completion_metric(get_agent_config().evals.metrics, judge)
    if metric is None:
        raise RuntimeError("task completion was scored while disabled")
    case = LLMTestCase(input=question, actual_output=result.answer or "")
    trace = _trace(handler)
    if trace is not None and trace.root_spans:
        case._trace_dict = _trajectory(question, result.answer, trace)
    metric.measure(case)
    return ItemScore("task_completion", float(metric.score), metric.reason or "", "NUMERIC")


def _argument_score(question: str, handler: CallbackHandler | None, judge: AgentJudgeLlm | None) -> ItemScore:
    if judge is None:
        raise RuntimeError("argument correctness was scored while disabled")
    metric = argument_correctness_metric(get_agent_config().evals.metrics, judge)
    if metric is None:
        raise RuntimeError("argument correctness was scored while disabled")
    case = LLMTestCase(input=question, actual_output="", tools_called=_tools_called(handler))
    metric.measure(case)
    return ItemScore("argument_correctness", float(metric.score), metric.reason or "", "NUMERIC")


def _tools_called(handler: CallbackHandler | None) -> list[Any]:
    trace = _trace(handler)
    calls = (trace.tools_called or []) if trace is not None else []
    return [call.model_copy(update={"description": _TOOL_DESCRIPTIONS.get(call.name)}) for call in calls]


def _progress(message: str) -> None:
    print(message, flush=True)


def _trace(handler: CallbackHandler | None) -> Any:
    # trace_manager forgets a trace once its last span ends, so read the handler's own reference.
    return handler._trace if handler is not None else None


def _trajectory(question: str, answer: str | None, trace: Any) -> dict[str, Any]:
    """Return the trace as DeepEval's span tree without LangGraph's repeated state.

    Graph node spans carry the whole accumulated state as input, and plumbing spans
    repeat it again, so only the new data on each span is kept. Nothing is clipped.
    """
    nodes = (set(compile_graph().nodes) | set(get_sql_agent().nodes)) - {"__start__"}
    children = [
        node
        for span in trace.root_spans
        for child in trace_manager.create_nested_spans_dict(span).get("children", [])
        for node in _prune(child, nodes)
    ]
    return {"name": "question", "type": "agent", "input": question, "output": answer or "", "children": children}


def _prune(span: dict[str, Any], nodes: set[str]) -> list[dict[str, Any]]:
    """Return the span with only its new data, or its pruned children when it is plumbing."""
    children = [node for child in span.get("children") or [] for node in _prune(child, nodes)]
    span_type, name = span.get("type"), span.get("name")
    if span_type == "tool":
        fields = {"input": span.get("input"), "output": span.get("output"), "description": _TOOL_DESCRIPTIONS.get(name)}
    elif span_type == "retriever":
        fields = {"input": span.get("input"), "output": span.get("output")}
    elif span_type == "llm":
        fields = {"model": span.get("model"), "output": span.get("output")}
    elif name in nodes:
        fields = {"output": span.get("output")}
    else:
        return children
    node = {"name": name, "type": span_type, **{key: value for key, value in fields.items() if value is not None}}
    if children:
        node["children"] = children
    return [node]


def _run_published(
    dataset: BenchmarkDataset, judge: AgentJudgeLlm | None,
    *, selected: list[BenchmarkItem] | None = None, seed: int = 42,
) -> str:
    from langfuse import get_client

    _progress("Connecting to Langfuse")
    client = get_client()
    _progress("Syncing the question file")
    remote_items = sync_benchmark(client, dataset)
    selected = selected if selected is not None else dataset.items
    selected_ids = {item.id for item in selected}
    _progress(f"Scoring {len(selected)}/{len(dataset.items)} questions")
    captured: dict[str, list[ItemScore]] = {}
    errors: dict[str, str] = {}

    def task(*, item: Any, **kwargs: Any) -> str:
        question = item.input if isinstance(item.input, str) else str(item.input)
        try:
            result, scores = _score_item(item.id, question, judge)
        except Exception as exc:
            errors[item.id] = str(exc)
            raise
        captured[item.id] = scores
        captured[question] = scores
        return result.answer or ""

    evaluators = [_replay(score_name, captured) for score_name in _enabled_names()]
    experiment = client.run_experiment(
        name=dataset.name,
        run_name=_run_name(dataset.name, dataset.version),
        description="Referenceless benchmark of the BPS question agent",
        data=[item for item in remote_items if item.id in selected_ids],
        task=task,
        evaluators=evaluators,
        max_concurrency=1,
        metadata={"version": dataset.version, "n_samples": str(len(selected)), "seed": str(seed)},
    )
    _check_scored(selected_ids, captured, errors)
    rows = [(item.id, captured[item.id]) for item in selected]
    report = format_report(rows)
    url = experiment.dataset_run_url or ""
    return f"{report}\n\n{url}" if url else report


def _run_conversations_published(
    dataset: ConversationDataset, judge: AgentJudgeLlm | None,
    *, selected: list[ConversationItem] | None = None, seed: int = 42,
) -> str:
    from langfuse import get_client

    _progress("Connecting to Langfuse")
    client = get_client()
    _progress("Syncing the conversation file")
    remote_items = sync_conversations(client, dataset)
    selected = selected if selected is not None else dataset.items
    selected_ids = {item.id for item in selected}
    _progress(f"Scoring {len(selected)}/{len(dataset.items)} conversations")
    captured: dict[str, list[ItemScore]] = {}
    errors: dict[str, str] = {}
    by_id = {item.id: item for item in dataset.items}

    def task(*, item: Any, **kwargs: Any) -> dict[str, Any]:
        local = by_id[item.id]
        try:
            case, scores = _score_conversation_item(local, judge)
        except Exception as exc:
            errors[item.id] = str(exc)
            raise
        captured[item.id] = scores
        captured[str(item.input)] = scores
        return {"turns": [{"role": turn.role, "content": turn.content} for turn in case.turns]}

    evaluators = [_replay(name, captured) for name in _enabled_conversation_names()]
    experiment = client.run_experiment(
        name=dataset.name,
        run_name=_run_name(dataset.name, dataset.version),
        description="Referenceless multi-turn benchmark of the BPS question agent",
        data=[item for item in remote_items if item.id in selected_ids],
        task=task,
        evaluators=evaluators,
        max_concurrency=1,
        metadata={"version": dataset.version, "n_samples": str(len(selected)), "seed": str(seed)},
    )
    _check_scored(selected_ids, captured, errors)
    report = format_report([(item.id, captured[item.id]) for item in selected])
    url = experiment.dataset_run_url or ""
    return f"{report}\n\n{url}" if url else report


def _run_name(name: str, version: str) -> str:
    """Give every execution its own Langfuse run; a run holds one result per item."""
    return f"{name} {version} {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"


def _check_scored(ids: set[str], captured: dict[str, list[ItemScore]], errors: dict[str, str]) -> None:
    missing = ids - captured.keys()
    if missing:
        details = "; ".join(f"{item_id}: {errors.get(item_id, 'no scores recorded')}" for item_id in sorted(missing))
        raise RuntimeError(f"Benchmark items failed before scoring: {details}")


def _enabled_names() -> list[str]:
    metrics = get_agent_config().evals.metrics
    names: list[str] = []
    if metrics.task_completion.enabled:
        names.append("task_completion")
    if metrics.argument_correctness.enabled:
        names.append("argument_correctness")
    if metrics.pipeline_status.enabled:
        names.append("pipeline_status")
    return names


def _enabled_conversation_names() -> list[str]:
    metrics = get_agent_config().evals.conversation_metrics
    return [name for name in (
        "goal_accuracy", "tool_use", "conversation_completeness", "turn_relevancy", "pipeline_status"
    ) if getattr(metrics, name).enabled]


def _replay(name: str, captured: dict[str, list[ItemScore]]) -> Any:
    from langfuse import Evaluation

    def evaluate(*, input: Any, output: Any, metadata: dict[str, Any] | None = None, **kwargs: Any) -> Evaluation:
        item_id = metadata.get("benchmark_id") if isinstance(metadata, dict) else None
        scores = captured.get(str(item_id)) if item_id else None
        if scores is None:
            scores = captured[str(input)]
        score = next(item for item in scores if item.name == name)
        return Evaluation(name=score.name, value=score.value, comment=score.comment or None, data_type=score.data_type)

    evaluate.__name__ = name
    return evaluate
