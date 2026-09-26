"""Build the DeepEval metrics that are switched on in ``evals.metrics``."""

from __future__ import annotations

from deepeval.metrics import ArgumentCorrectnessMetric, TaskCompletionMetric
from deepeval.models import DeepEvalBaseLLM

from tilik_agent.config import EvalsMetricsConfig


def task_completion_metric(
    metrics: EvalsMetricsConfig, judge: DeepEvalBaseLLM
) -> TaskCompletionMetric | None:
    """Return the task-completion judge, or none when that metric is disabled."""
    block = metrics.task_completion
    if not block.enabled:
        return None
    return TaskCompletionMetric(
        threshold=block.threshold,
        model=judge,
        include_reason=True,
        async_mode=False,
    )


def argument_correctness_metric(
    metrics: EvalsMetricsConfig, judge: DeepEvalBaseLLM
) -> ArgumentCorrectnessMetric | None:
    """Return the argument-correctness judge, or none when that metric is disabled."""
    block = metrics.argument_correctness
    if not block.enabled:
        return None
    return ArgumentCorrectnessMetric(
        threshold=block.threshold,
        model=judge,
        include_reason=True,
        async_mode=False,
    )
