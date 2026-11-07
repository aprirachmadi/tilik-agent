"""Score saved answers with the configured judges and compare each verdict with the human label.

The agent never runs here: every answer and its recorded trace are frozen, so a
human label always describes the exact text the judge sees.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deepeval.test_case import LLMTestCase, ToolCall

from tilik_agent.config import get_agent_config
from tilik_agent.evals.dataset import JudgeItem, judge_traces_path, load_judge_benchmark
from tilik_agent.evals.judge import AgentJudgeLlm
from tilik_agent.evals.metrics import argument_correctness_metric, task_completion_metric


def _input(item: JudgeItem) -> str:
    """A follow-up question only makes sense with the turns before it."""
    if not item.history:
        return item.question
    turns = "\n".join(f"{turn['role']}: {turn['content']}" for turn in item.history)
    return f"Earlier conversation:\n{turns}\n\nLatest question: {item.question}"


def _verdict(score: float, threshold: float) -> str:
    return "pass" if score >= threshold else "fail"


def judge_item(item: JudgeItem, trace: dict[str, Any] | None, judge: AgentJudgeLlm) -> dict[str, Any]:
    """Run each enabled answer judge once; a judge error is recorded, not raised."""
    config = get_agent_config().evals
    tools = [ToolCall(**call) for call in (trace or {}).get("tools_called", [])]
    labels: dict[str, Any] = {"judge_model": config.judge.model, "judged_at": datetime.now(UTC).isoformat(),
                              "had_trace": trace is not None, "tool_calls": len(tools)}
    builders = {
        "task_completion": (task_completion_metric, config.metrics.task_completion.threshold),
        "argument_correctness": (argument_correctness_metric, config.metrics.argument_correctness.threshold),
    }
    for name, (build, threshold) in builders.items():
        metric = build(config.metrics, judge)
        if metric is None:
            continue
        if name == "task_completion":
            case = LLMTestCase(input=_input(item), actual_output=item.generated_response, tools_called=tools)
            if trace is not None:
                case._trace_dict = trace["trajectory"]
        else:
            case = LLMTestCase(input=_input(item), actual_output="", tools_called=tools)
        try:
            metric.measure(case)
        except Exception as exc:
            labels[name] = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            continue
        score = float(metric.score)
        labels[name] = {"score": score, "verdict": _verdict(score, threshold), "reason": metric.reason or ""}
    return labels


def run_judge_benchmark(path: Path | None = None, *, item_ids: list[str] | None = None) -> str:
    """Judge every item (or ``item_ids``), save the judge labels into the file, and report agreement."""
    dataset = load_judge_benchmark(path)
    traces = json.loads(judge_traces_path(path).read_text(encoding="utf-8"))
    judge = AgentJudgeLlm(get_agent_config().evals.judge)
    selected = [item for item in dataset.items if not item_ids or item.id in item_ids]
    for number, item in enumerate(selected, start=1):
        print(f"[{number}/{len(selected)}] {item.id} {item.question[:70]}", flush=True)
        item.judge_labels = judge_item(item, traces.get(item.id), judge)
        target = path or Path(__file__).parent / "datasets" / "judge_benchmark.json"
        # Save after each item so a failure late in the run keeps the earlier labels.
        target.write_text(json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return agreement_report(selected)


def agreement_report(items: list[JudgeItem]) -> str:
    """Per-item verdicts and how often each judge agrees with the human label."""
    lines = ["id   human  task_completion       argument_correctness"]
    totals: dict[str, list[int]] = {}
    for item in items:
        cells = []
        for name in ("task_completion", "argument_correctness"):
            label = item.judge_labels.get(name) or {}
            if name == "argument_correctness" and not item.judge_labels.get("tool_calls"):
                # With no tool calls the metric passes by default; that is not agreement.
                cells.append("n/a (no tools)")
            elif "verdict" in label:
                agree = label["verdict"] == item.human_label.verdict
                totals.setdefault(name, [0, 0])[0] += agree
                totals[name][1] += 1
                cells.append(f"{label['verdict']:<4} {label['score']:.2f} {'✓' if agree else '✗'}")
            else:
                cells.append("error" if "error" in label else "-")
        lines.append(f"{item.id:<4} {item.human_label.verdict:<6} {cells[0]:<21} {cells[1]}")
    for name, (agree, scored) in totals.items():
        lines.append(f"{name}: agrees with human on {agree}/{scored} ({agree / scored:.0%})")
    return "\n".join(lines)
