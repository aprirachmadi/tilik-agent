"""The judge benchmark file stays valid and its agreement report counts only real verdicts."""

from __future__ import annotations

import json

from tilik_agent.evals.dataset import judge_traces_path, load_judge_benchmark
from tilik_agent.evals.judge_benchmark import _input, agreement_report


def test_judge_benchmark_file_is_valid_and_traces_match_items() -> None:
    dataset = load_judge_benchmark()
    ids = [item.id for item in dataset.items]
    assert len(ids) == len(set(ids))
    traces = json.loads(judge_traces_path().read_text(encoding="utf-8"))
    assert set(traces) <= set(ids)
    assert {"trajectory", "tools_called"} <= set(next(iter(traces.values())))


def test_follow_up_question_is_judged_with_its_earlier_turns() -> None:
    item = next(item for item in load_judge_benchmark().items if item.history)
    assert item.history[0]["content"] in _input(item) and _input(item).endswith(item.question)


def test_argument_correctness_without_tool_calls_is_not_counted_as_agreement() -> None:
    item = load_judge_benchmark().items[0].model_copy(deep=True)
    item.human_label.verdict = "fail"
    item.judge_labels = {
        "tool_calls": 0,
        "task_completion": {"score": 0.2, "verdict": "fail", "reason": ""},
        "argument_correctness": {"score": 1.0, "verdict": "pass", "reason": "No tool calls provided"},
    }
    report = agreement_report([item])
    assert "n/a (no tools)" in report
    assert "task_completion: agrees with human on 1/1 (100%)" in report
    assert "argument_correctness: agrees" not in report
