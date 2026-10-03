"""Build the DeepEval metrics that are switched on in ``evals.metrics``."""

from __future__ import annotations

from deepeval.metrics import (
    ArgumentCorrectnessMetric,
    ConversationCompletenessMetric,
    GoalAccuracyMetric,
    TaskCompletionMetric,
    ToolUseMetric,
    TurnRelevancyMetric,
)
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import ToolCall

from tilik_agent.agent.sql import SQL_TOOLS
from tilik_agent.config import ConversationMetricsConfig, EvalsMetricsConfig


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


def conversation_metrics(
    config: ConversationMetricsConfig, judge: DeepEvalBaseLLM, *, history_messages: int
) -> dict[str, GoalAccuracyMetric | ToolUseMetric | ConversationCompletenessMetric | TurnRelevancyMetric]:
    """Build only enabled referenceless conversational judges."""
    metrics: dict[str, GoalAccuracyMetric | ToolUseMetric | ConversationCompletenessMetric | TurnRelevancyMetric] = {}
    if config.goal_accuracy.enabled:
        metrics["goal_accuracy"] = GoalAccuracyMetric(
            threshold=config.goal_accuracy.threshold, model=judge, async_mode=False
        )
    if config.tool_use.enabled:
        metrics["tool_use"] = ToolUseMetric(
            available_tools=[ToolCall(name=tool.name, description=tool.description) for tool in SQL_TOOLS],
            threshold=config.tool_use.threshold,
            model=judge,
            async_mode=False,
        )
    if config.conversation_completeness.enabled:
        metrics["conversation_completeness"] = ConversationCompletenessMetric(
            threshold=config.conversation_completeness.threshold, model=judge, async_mode=False
        )
    if config.turn_relevancy.enabled:
        metrics["turn_relevancy"] = TurnRelevancyMetric(
            threshold=config.turn_relevancy.threshold,
            model=judge,
            window_size=history_messages // 2 + 1,
            async_mode=False,
        )
    return metrics
