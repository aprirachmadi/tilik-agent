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
    for item in dataset.items:
        calls = traces.get(item.id, {}).get("tools_called", [])
        expected = [{"name": call["name"], "arguments": call["input_parameters"]} for call in calls]
        assert item.generated_tool_calls == expected


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


def test_publish_uploads_frozen_answers_and_scores_agreement(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    import langfuse

    from tilik_agent.evals.judge_benchmark import publish_judge_benchmark

    dataset = load_judge_benchmark()
    item = dataset.items[2].model_copy(deep=True)  # human "fail", no tool calls
    item.judge_labels = {
        "judge_model": "m", "tool_calls": 0,
        "task_completion": {"score": 1.0, "verdict": "pass", "reason": "r"},
        "argument_correctness": {"score": 1.0, "verdict": "pass", "reason": "No tool calls provided"},
    }
    path = tmp_path / "judge_benchmark.json"
    judge_traces_path(path).write_text(judge_traces_path().read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps({**dataset.model_dump(mode="json"), "items": [item.model_dump(mode="json")]}))

    class Client:
        items: list[dict] = []
        run: dict = {}

        def get_dataset(self, name):  # noqa: ANN001, ANN201
            return type("D", (), {"items": []})()

        def create_dataset_item(self, **kwargs):  # noqa: ANN003, ANN201
            self.items.append(kwargs)
            return type("I", (), {"id": kwargs["id"]})()

        def run_experiment(self, **kwargs):  # noqa: ANN003, ANN201
            remote = kwargs["data"][0]
            self.run = {"output": kwargs["task"](item=remote),
                        "scores": kwargs["evaluators"][0](metadata=self.items[0]["metadata"])}
            return type("E", (), {"dataset_run_url": "url", "run_name": "run"})()

        def flush(self) -> None:
            pass

    client = Client()
    monkeypatch.setattr(langfuse, "get_client", lambda: client)
    assert publish_judge_benchmark(path) == "Langfuse run: url"
    assert client.items[0]["metadata"]["human_verdict"] == "fail"
    assert client.items[0]["input"]["generated_response"] == item.generated_response
    assert client.items[0]["input"]["generated_tool_calls"] == item.generated_tool_calls
    assert client.items[0]["source_trace_id"] == item.source["trace_id"]
    traces = json.loads(judge_traces_path().read_text(encoding="utf-8"))
    assert client.items[0]["input"]["trajectory"] == traces[item.id]["trajectory"]
    assert client.run["output"] == item.generated_response
    scores = {score.name: score.value for score in client.run["scores"]}
    assert scores == {"human_label": "fail", "task_completion": 1.0, "task_completion_agrees": 0.0}
