"""Question API returns the merged graph state."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tilik_agent.agent.runner import run_question, stream_question
from tilik_agent.api import app
from tilik_agent.config import get_agent_config


class _Graph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: str) -> Any:
        assert state["question"] == "berapa"
        assert config["recursion_limit"] == get_agent_config().graph.recursion_limit
        assert "callbacks" not in config
        assert stream_mode == ["updates", "custom"]
        yield {
            "write_answer": {
                "answer": "Papua 6,96",
                "answer_observation_ids": [1],
                "answer_document_ids": [],
            }
        }
        yield {
            "build_chart": {
                "chart": {
                    "type": "bar",
                    "title": "TPT",
                    "x_axis": "Provinsi",
                    "y_axis": "Persen",
                    "data": [{"label": "Papua", "series": "TPT", "value": 6.96, "unit": "persen", "observation_id": 1}],
                }
            }
        }


def test_stream_question_yields_inner_sql_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The graph emits inner SQL updates and then an answer.

    Expected:
    The stream yields the SQL turns, and the returned answer stays the written answer.
    """

    class _Graph:
        def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Any:
            yield ("custom", {"agent": {"messages": ["lookup"]}})
            yield ("custom", {"tools": {"messages": ["rows"]}})
            yield ("updates", {"write_answer": {"answer": "Papua 6,96"}})

    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _Graph())
    monkeypatch.setattr("tilik_agent.agent.tracing.langfuse_configured", lambda: False)
    assert list(stream_question("berapa")) == [
        {"agent": {"messages": ["lookup"]}},
        {"tools": {"messages": ["rows"]}},
        {"write_answer": {"answer": "Papua 6,96"}},
    ]
    assert run_question("berapa").answer == "Papua 6,96"


def test_questions_returns_answer_and_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The question endpoint streams a graph that writes an answer and a chart spec.

    Expected:
    The response body contains that answer, chart, and evidence ids.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _Graph())
    monkeypatch.setattr("tilik_agent.agent.tracing.langfuse_configured", lambda: False)
    response = TestClient(app).post("/questions", json={"question": "berapa"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Papua 6,96"
    assert body["chart"]["data"][0]["value"] == 6.96
    assert body["answer_observation_ids"] == [1]
    assert body["answer_document_ids"] == []
