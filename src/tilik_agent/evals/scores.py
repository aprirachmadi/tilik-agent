"""Turn a finished question into the scores ``tilik eval`` prints and uploads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from tilik_agent.config import EvalsMetricsConfig

ScoreType = Literal["NUMERIC", "CATEGORICAL"]


@dataclass(frozen=True)
class ItemScore:
    """One metric result for one question."""

    name: str
    value: float | str
    comment: str
    data_type: ScoreType


def pipeline_status_label(step_status: dict[str, Any], plan_error: str | None = None) -> tuple[str, str]:
    """Summarize planned steps as succeeded, partial, or failed."""
    if not step_status:
        return "failed", plan_error or "No planned steps finished"
    labels = [str(item.get("status", "failed")) for item in step_status.values()]
    errors = [str(item.get("error")) for item in step_status.values() if item.get("error")]
    if all(label == "succeeded" for label in labels):
        label = "succeeded"
    elif any(label in {"succeeded", "partial"} for label in labels):
        label = "partial"
    else:
        label = "failed"
    return label, "; ".join(errors)


def assemble_scores(
    metrics: EvalsMetricsConfig,
    *,
    task_completion: ItemScore | None,
    argument_correctness: ItemScore | None,
    step_status: dict[str, Any],
    plan_error: str | None,
) -> list[ItemScore]:
    """Keep only the metrics switched on in ``evals.metrics``."""
    scores: list[ItemScore] = []
    if metrics.task_completion.enabled and task_completion is not None:
        scores.append(task_completion)
    if metrics.argument_correctness.enabled and argument_correctness is not None:
        scores.append(argument_correctness)
    if metrics.pipeline_status.enabled:
        label, comment = pipeline_status_label(step_status, plan_error)
        scores.append(ItemScore("pipeline_status", label, comment, "CATEGORICAL"))
    return scores


def format_report(rows: list[tuple[str, list[ItemScore]]]) -> str:
    """Render one block per question for the terminal."""
    lines: list[str] = []
    for item_id, scores in rows:
        lines.append(item_id)
        for score in scores:
            value = f"{score.value:.2f}" if isinstance(score.value, float) else score.value
            suffix = f"  {score.comment}" if score.comment else ""
            lines.append(f"  {score.name}: {value}{suffix}")
    return "\n".join(lines)
