"""Run the question file through the agent and score the enabled metrics.

This module is the only place that calls the judges. ``run_question`` stays a
normal question call.
"""

from __future__ import annotations

from typing import Any

from deepeval.integrations.langchain import CallbackHandler
from deepeval.test_case import LLMTestCase
from deepeval.tracing import trace_manager

from tilik_agent.agent.runner import QuestionResult, run_question
from tilik_agent.agent.tracing import langfuse_configured
from tilik_agent.config import get_agent_config
from tilik_agent.evals.dataset import BenchmarkDataset, load_benchmark
from tilik_agent.evals.judge import YamlJudge
from tilik_agent.evals.langfuse_dataset import sync_benchmark
from tilik_agent.evals.metrics import argument_correctness_metric, task_completion_metric
from tilik_agent.evals.scores import ItemScore, assemble_scores, format_report


def run_benchmark() -> str:
    """Score every question in the JSON file and upload the run when Langfuse is configured."""
    dataset = load_benchmark()
    config = get_agent_config()
    metrics = config.evals.metrics
    judge = None
    if metrics.task_completion.enabled or metrics.argument_correctness.enabled:
        judge = YamlJudge(config.evals.judge)
    if not langfuse_configured():
        _progress(f"Scoring {len(dataset.items)} questions")
        rows = []
        for item in dataset.items:
            _, scores = _score_item(item.id, item.question, judge)
            rows.append((item.id, scores))
        return format_report(rows) + "\n\nLangfuse upload skipped"
    return _run_published(dataset, judge)


def _score_item(
    item_id: str, question: str, judge: YamlJudge | None
) -> tuple[QuestionResult, list[ItemScore]]:
    _progress(f"[{item_id}] {question}")
    result, scores = _score_question(question, judge)
    _progress(format_report([(item_id, scores)]))
    return result, scores


def _score_question(question: str, judge: YamlJudge | None) -> tuple[QuestionResult, list[ItemScore]]:
    metrics = get_agent_config().evals.metrics
    handler = None
    callbacks = None
    if judge is not None:
        handler = CallbackHandler(name="tilik-eval")
        callbacks = [handler]
    _progress("  answering")
    result = run_question(question, callbacks=callbacks)
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
    question: str, result: QuestionResult, handler: CallbackHandler | None, judge: YamlJudge | None
) -> ItemScore:
    if judge is None:
        raise RuntimeError("task completion was scored while disabled")
    metric = task_completion_metric(get_agent_config().evals.metrics, judge)
    if metric is None:
        raise RuntimeError("task completion was scored while disabled")
    case = LLMTestCase(input=question, actual_output=result.answer or "")
    trace = _trace(handler)
    if trace is not None and trace.root_spans:
        case._trace_dict = _trajectory(trace)
    metric.measure(case)
    return ItemScore("task_completion", float(metric.score), metric.reason or "", "NUMERIC")


def _argument_score(question: str, handler: CallbackHandler | None, judge: YamlJudge | None) -> ItemScore:
    if judge is None:
        raise RuntimeError("argument correctness was scored while disabled")
    metric = argument_correctness_metric(get_agent_config().evals.metrics, judge)
    if metric is None:
        raise RuntimeError("argument correctness was scored while disabled")
    trace = _trace(handler)
    tools = list(trace.tools_called or []) if trace is not None else []
    case = LLMTestCase(input=question, actual_output="", tools_called=tools)
    metric.measure(case)
    return ItemScore("argument_correctness", float(metric.score), metric.reason or "", "NUMERIC")


def _progress(message: str) -> None:
    print(message, flush=True)


def _trace(handler: CallbackHandler | None) -> Any:
    # trace_manager forgets a trace once its last span ends, so read the handler's own reference.
    return handler._trace if handler is not None else None


def _trajectory(trace: Any) -> dict[str, Any]:
    spans = [trace_manager.create_nested_spans_dict(span) for span in trace.root_spans]
    return spans[0] if len(spans) == 1 else {"name": "question", "children": spans}


def _run_published(dataset: BenchmarkDataset, judge: YamlJudge | None) -> str:
    from langfuse import get_client

    _progress("Connecting to Langfuse")
    client = get_client()
    _progress("Syncing the question file")
    remote_items = sync_benchmark(client, dataset)
    _progress(f"Scoring {len(dataset.items)} questions")
    captured: dict[str, list[ItemScore]] = {}

    def task(*, item: Any, **kwargs: Any) -> str:
        question = item.input if isinstance(item.input, str) else str(item.input)
        result, scores = _score_item(item.id, question, judge)
        captured[item.id] = scores
        captured[question] = scores
        return result.answer or ""

    evaluators = [_replay(score_name, captured) for score_name in _enabled_names()]
    experiment = client.run_experiment(
        name=dataset.name,
        run_name=f"{dataset.name} {dataset.version}",
        description="Referenceless benchmark of the BPS question agent",
        data=remote_items,
        task=task,
        evaluators=evaluators,
        max_concurrency=1,
        metadata={"version": dataset.version},
    )
    rows = [(item.id, captured[item.id]) for item in dataset.items]
    report = format_report(rows)
    url = experiment.dataset_run_url or ""
    return f"{report}\n\n{url}" if url else report


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
