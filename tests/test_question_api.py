"""Question API returns the merged graph state."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tilik_agent.agent.answer import answer_blocks
from tilik_agent.agent.runner import cited_sources, question_result, run_question, stream_question
from tilik_agent.agent.schemas import State
from tilik_agent.api import app, status_lines
from tilik_agent.chat.models import Conversation, Message
from tilik_agent.chat.storage import list_conversations, load_full_thread
from tilik_agent.config import get_agent_config
from tilik_agent.ingestion.models import Dataset


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


def test_questions_returns_answer_and_chart(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
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


_CHART = {
    "type": "bar",
    "title": "TPT",
    "x_axis": "Provinsi",
    "y_axis": "Persen",
    "data": [{"label": "Papua", "series": "TPT", "value": 6.96, "unit": "persen", "observation_id": 1}],
}


@pytest.mark.parametrize(
    ("text", "chart", "expected_answer", "expected_blocks"),
    [
        (
            "Intro.\n\n[[chart]]\n\nInterpretation.",
            _CHART,
            "Intro.\n\nInterpretation.",
            [{"type": "text", "text": "Intro."}, {"type": "chart"}, {"type": "text", "text": "Interpretation."}],
        ),
        (
            "Intro.\r\n\r\n[[chart]]\r\n\r\nInterpretation.",
            _CHART,
            "Intro.\n\nInterpretation.",
            [{"type": "text", "text": "Intro."}, {"type": "chart"}, {"type": "text", "text": "Interpretation."}],
        ),
        (
            "Intro.\n\nInterpretation.",
            _CHART,
            "Intro.\n\nInterpretation.",
            [{"type": "text", "text": "Intro."}, {"type": "chart"}, {"type": "text", "text": "Interpretation."}],
        ),
        (
            "Intro.\n\n[[chart]]\n\nInterpretation.\n\n[[chart]]",
            _CHART,
            "Intro.\n\nInterpretation.",
            [{"type": "text", "text": "Intro."}, {"type": "chart"}, {"type": "text", "text": "Interpretation."}],
        ),
        ("Intro.\n\n[[chart]]\n\nInterpretation.", None, "Intro.\n\nInterpretation.", None),
    ],
)
def test_answer_blocks(
    text: str,
    chart: dict[str, Any] | None,
    expected_answer: str,
    expected_blocks: list[dict[str, str]] | None,
) -> None:
    assert answer_blocks(text, chart) == (expected_answer, expected_blocks)


class _PlacedChartGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Iterator[dict[str, Any]]:
        yield {"write_answer": {"answer": "Intro.\n\n[[chart]]\n\nInterpretation."}}
        yield {"build_chart": {"chart": _CHART}}


class _WorkspaceGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Iterator[dict[str, Any]]:
        yield {"interpret_request": {"question": "berapa TPT Papua", "source_route": "both"}}
        yield {"agent": {"messages": ["lookup"]}}
        yield {
            "run_both": {
                "observations": [{"observation_id": 1, "dataset_id": 9, "value": 6.96}],
                "source_details": {
                    "datasets": {
                        "9": {
                            "dataset": {
                                "id": 9,
                                "bps_table_id": "MTU3MyMx",
                                "tablesource": "1",
                                "title": "Tingkat Pengangguran Terbuka",
                            }
                        }
                    }
                },
            }
        }
        yield {
            "write_answer": {
                "answer": "Papua 6,96",
                "answer_observation_ids": [1],
                "answer_document_ids": ["s1:doc1"],
            }
        }
        yield {"build_chart": {"chart": _CHART}}


class _ChartFailedGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Iterator[Any]:
        yield {"write_answer": {"answer": "Papua 6,96", "answer_observation_ids": [], "answer_document_ids": []}}
        yield {"build_chart": {"chart": None, "chart_error": "Chart unavailable: mismatch"}}


class _EmptyAnswerGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Iterator[Any]:
        yield {"write_answer": {"answer": None}}


class _RaisingGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> None:
        raise RuntimeError("graph down")


@pytest.fixture
def chat_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """In-memory chat tables so the HTTP tests do not need Postgres."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Conversation.__table__.create(engine)
    Message.__table__.create(engine)

    def open_session() -> Session:
        return Session(engine)

    monkeypatch.setattr("tilik_agent.api._db_session", open_session)
    monkeypatch.setattr("tilik_agent.agent.tracing.langfuse_configured", lambda: False)
    session = Session(engine)
    yield session
    session.close()


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        parsed.append((event, json.loads(data)))
    return parsed


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"interpret_request": {"source_route": "data"}}, ["Looking up datasets"]),
        ({"interpret_request": {"source_route": "context"}}, ["Searching SP2020"]),
        ({"interpret_request": {"source_route": "both"}}, ["Looking up datasets", "Searching SP2020"]),
        ({"interpret_request": {"source_route": "direct"}}, ["Writing the answer"]),
        ({"interpret_request": {"plan_error": "invalid"}}, ["Writing the answer"]),
        ({"run_sql": {}}, ["Writing the answer"]),
        ({"run_retrieval": {}}, ["Writing the answer"]),
        ({"run_both": {}}, ["Writing the answer"]),
        ({"agent": {"messages": ["lookup"]}}, []),
        ({"write_answer": {"answer": "ok"}}, []),
        ({"build_chart": {"chart": None}}, []),
    ],
)
def test_status_lines(update: dict[str, Any], expected: list[str]) -> None:
    """
    Condition:
    A graph update is a plan, a source node, an inner SQL step, or an answer node.

    Expected:
    Only the plan and the finished source lookup become a short status line.
    """
    assert status_lines(update) == expected


def test_cited_sources_use_titles_for_cited_ids() -> None:
    """
    Condition:
    The answer cites one observation and one document, and source details include the dataset title.

    Expected:
    The compact source list has that dataset id, BPS table id, tablesource, and title, then the document id.
    """
    state: State = {
        "answer_observation_ids": [1, 1],
        "answer_document_ids": ["s1:doc1", "s1:doc1"],
        "observations": [
            {"observation_id": 1, "dataset_id": 9},
            {"observation_id": 2, "dataset_id": 8},
        ],
        "source_details": {
            "datasets": {
                "9": {
                    "dataset": {
                        "bps_table_id": "MTU3MyMx",
                        "tablesource": "1",
                        "title": "Tingkat Pengangguran Terbuka",
                    }
                }
            }
        },
    }
    assert cited_sources(state) == [
        {
            "kind": "dataset",
            "dataset_id": 9,
            "bps_table_id": "MTU3MyMx",
            "tablesource": "1",
            "title": "Tingkat Pengangguran Terbuka",
        },
        {"kind": "document", "document_id": "s1:doc1"},
    ]
    assert question_result({}).sources == []
    assert question_result({}).chart_error is None
    assert question_result({}).trace_id is None


def test_questions_persists_chart_sources_and_lists_the_thread(
    monkeypatch: pytest.MonkeyPatch, chat_db: Session
) -> None:
    """
    Condition:
    A question run writes an answer, a chart, and cited dataset and document ids.

    Expected:
    The JSON response and the reloaded thread both contain the chart and compact sources.
    The Langfuse trace id is stored on the assistant row and left out of the response.
    A later turn keeps the original title and refreshes updated_at.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _WorkspaceGraph())
    monkeypatch.setattr("tilik_agent.agent.runner.current_trace_id", lambda: "trace-abc")
    client = TestClient(app)
    created = client.post("/questions", json={"question": "berapa TPT Papua"})
    assert created.status_code == 200
    body = created.json()
    assert body["chart"] == _CHART
    assert body["blocks"] == [{"type": "text", "text": "Papua 6,96"}, {"type": "chart"}]
    assert body["source_route"] == "both"
    assert body["chart_error"] is None
    assert body["sources"] == [
        {
            "kind": "dataset",
            "dataset_id": 9,
            "bps_table_id": "MTU3MyMx",
            "tablesource": "1",
            "title": "Tingkat Pengangguran Terbuka",
        },
        {"kind": "document", "document_id": "s1:doc1"},
    ]
    assert "trace_id" not in body
    assert "trace-abc" not in created.text

    listed = client.get("/conversations")
    assert listed.status_code == 200
    summaries = listed.json()
    assert summaries[0]["id"] == body["conversation_id"]
    assert summaries[0]["title"] == "berapa TPT Papua"
    assert summaries[0]["updated_at"]

    detail = client.get(f"/conversations/{body['conversation_id']}")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["chart"] == _CHART
    assert messages[1]["blocks"] == body["blocks"]
    assert messages[1]["sources"] == body["sources"]
    assert messages[1]["source_route"] == "both"
    assert "trace_id" not in messages[1]
    assistant = chat_db.get(Message, messages[1]["id"])
    assert assistant is not None
    assert assistant.trace_id == "trace-abc"

    first_updated = summaries[0]["updated_at"]
    chat_db.get(Conversation, body["conversation_id"]).updated_at = datetime.now(UTC) - timedelta(days=1)
    chat_db.commit()
    follow = client.post(
        "/questions",
        json={"question": "dan tahun 2024", "conversation_id": body["conversation_id"]},
    )
    assert follow.status_code == 200
    reloaded = client.get(f"/conversations/{body['conversation_id']}").json()
    assert reloaded["title"] == "berapa TPT Papua"
    assert len(reloaded["messages"]) == 4
    assert reloaded["updated_at"] >= first_updated
    missing = client.get("/conversations/missing")
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/questions", "/questions/stream"])
def test_placed_chart_round_trips(
    path: str, monkeypatch: pytest.MonkeyPatch, chat_db: Session
) -> None:
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _PlacedChartGraph())
    response = TestClient(app).post(path, json={"question": "grafik"})
    assert response.status_code == 200
    body = response.json() if path == "/questions" else next(
        payload for event, payload in _events(response.text) if event == "result"
    )
    assert body["answer"] == "Intro.\n\nInterpretation."
    assert body["blocks"] == [
        {"type": "text", "text": "Intro."},
        {"type": "chart"},
        {"type": "text", "text": "Interpretation."},
    ]
    detail = TestClient(app).get(f"/conversations/{body['conversation_id']}").json()
    saved = detail["messages"][1]
    assert saved["content"] == body["answer"]
    assert saved["blocks"] == body["blocks"]
    assert saved["chart"] == body["chart"] == _CHART


def test_old_message_has_no_blocks(chat_db: Session) -> None:
    chat_db.add(Conversation(id="old-thread"))
    chat_db.add(Message(conversation_id="old-thread", role="assistant", content="Old answer", chart=_CHART))
    chat_db.commit()

    detail = TestClient(app).get("/conversations/old-thread")
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["blocks"] is None


class _SourceWithoutTablesourceGraph:
    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: list[str]) -> Iterator[dict[str, Any]]:
        yield {
            "run_sql": {
                "observations": [{"observation_id": 1, "dataset_id": 129, "value": 1}],
                "source_details": {
                    "datasets": {
                        "129": {
                            "dataset": {
                                "id": 129,
                                "bps_table_id": "MTk3NSMy",
                                "title": "Jumlah Penduduk Pertengahan Tahun (Ribu Jiwa)",
                            }
                        }
                    }
                },
            }
        }
        yield {
            "write_answer": {
                "answer": "Naik.",
                "answer_observation_ids": [1],
                "answer_document_ids": [],
            }
        }


def test_saved_answer_fills_tablesource(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
    """
    Condition:
    The answer cites a dataset whose source details omit tablesource, and that dataset row stores one.

    Expected:
    The question response and the stored assistant message include tablesource from the dataset row.
    """
    Dataset.__table__.create(chat_db.get_bind())
    chat_db.add(
        Dataset(
            id=129,
            bps_table_id="MTk3NSMy",
            tablesource="2",
            domain_id="0000",
            title="Jumlah Penduduk Pertengahan Tahun (Ribu Jiwa)",
        )
    )
    chat_db.commit()
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _SourceWithoutTablesourceGraph())

    created = TestClient(app).post("/questions", json={"question": "berapa penduduk"})
    assert created.status_code == 200
    source = created.json()["sources"][0]
    assert source["tablesource"] == "2"
    assert source["bps_table_id"] == "MTk3NSMy"

    stored = chat_db.scalars(select(Message).where(Message.role == "assistant")).one()
    chat_db.refresh(stored)
    assert stored.sources[0]["tablesource"] == "2"


def test_reloaded_source_gains_tablesource(chat_db: Session) -> None:
    """
    Condition:
    An assistant message was saved with a dataset id and BPS table id, and no tablesource.

    Expected:
    Reloading the thread fills tablesource from the dataset row so the chip can link.
    """
    Dataset.__table__.create(chat_db.get_bind())
    chat_db.add(
        Dataset(
            id=129,
            bps_table_id="MTk3NSMy",
            tablesource="2",
            domain_id="0000",
            title="Jumlah Penduduk Pertengahan Tahun (Ribu Jiwa)",
        )
    )
    chat_db.add(Conversation(id="saved-thread", title="penduduk"))
    chat_db.add(
        Message(
            conversation_id="saved-thread",
            role="assistant",
            content="Jumlahnya naik.",
            sources=[
                {
                    "kind": "dataset",
                    "dataset_id": 129,
                    "bps_table_id": "MTk3NSMy",
                    "title": "Jumlah Penduduk Pertengahan Tahun (Ribu Jiwa)",
                }
            ],
        )
    )
    chat_db.commit()

    detail = TestClient(app).get("/conversations/saved-thread")
    assert detail.status_code == 200
    source = detail.json()["messages"][0]["sources"][0]
    assert source["tablesource"] == "2"
    assert source["bps_table_id"] == "MTk3NSMy"


def test_chart_error_round_trips(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
    """
    Condition:
    The graph returns an answer and a chart error instead of a chart spec.

    Expected:
    The response and the stored assistant message keep chart null and the error text.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _ChartFailedGraph())
    response = TestClient(app).post("/questions", json={"question": "berapa"})
    assert response.status_code == 200
    body = response.json()
    assert body["chart"] is None
    assert body["chart_error"] == "Chart unavailable: mismatch"
    detail = TestClient(app).get(f"/conversations/{body['conversation_id']}").json()
    assert detail["messages"][1]["chart"] is None
    assert detail["messages"][1]["chart_error"] == "Chart unavailable: mismatch"


def test_null_answer_keeps_the_user_message(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
    """
    Condition:
    The graph finishes without an answer.

    Expected:
    The conversation id is returned, the user message is stored, and there is no assistant row.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _EmptyAnswerGraph())
    response = TestClient(app).post("/questions", json={"question": "halo"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    loaded = load_full_thread(chat_db, body["conversation_id"])
    assert loaded is not None
    _, messages = loaded
    assert [message.role for message in messages] == ["user"]


def test_failed_run_keeps_the_conversation(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
    """
    Condition:
    The graph raises after the conversation title is saved.

    Expected:
    The request fails and the conversation row remains, with no messages, so the client can retry.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _RaisingGraph())
    response = TestClient(app, raise_server_exceptions=False).post("/questions", json={"question": "berapa"})
    assert response.status_code == 500
    rows = list_conversations(chat_db)
    assert len(rows) == 1
    assert rows[0].title == "berapa"
    loaded = load_full_thread(chat_db, rows[0].id)
    assert loaded is not None
    assert loaded[1] == []


def test_questions_stream_ends_with_the_json_payload(monkeypatch: pytest.MonkeyPatch, chat_db: Session) -> None:
    """
    Condition:
    The stream endpoint runs a graph that plans a combined lookup and then writes an answer.

    Expected:
    Status events name the lookup and the answer, skip the inner SQL step, and the last event
    matches the saved question payload.
    """
    monkeypatch.setattr("tilik_agent.agent.runner.compile_graph", lambda: _WorkspaceGraph())
    monkeypatch.setattr("tilik_agent.api.current_trace_id", lambda: "trace-sse")
    response = TestClient(app).post("/questions/stream", json={"question": "berapa TPT Papua"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [payload["status"] for event, payload in events if event == "status"] == [
        "Looking up datasets",
        "Searching SP2020",
        "Writing the answer",
    ]
    result_events = [payload for event, payload in events if event == "result"]
    assert len(result_events) == 1
    assert result_events[0]["answer"] == "Papua 6,96"
    assert result_events[0]["chart"] == _CHART
    assert "trace_id" not in result_events[0]
    detail = TestClient(app).get(f"/conversations/{result_events[0]['conversation_id']}").json()
    assert detail["messages"][1]["content"] == "Papua 6,96"
    stored = chat_db.get(Message, detail["messages"][1]["id"])
    assert stored is not None
    assert stored.trace_id == "trace-sse"


def test_cors_allows_the_local_frontend() -> None:
    """
    Condition:
    A browser on the local frontend origin asks to call the API.

    Expected:
    The response allows that origin.
    """
    response = TestClient(app).options(
        "/questions",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_db_session_reuses_one_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    Two API sessions are opened in one process.

    Expected:
    Both sessions use the engine created on the first call.
    """
    from tilik_agent import api

    calls: list[str] = []
    engine = create_engine("sqlite://")

    def fake_create(url: str) -> Any:
        calls.append(url)
        return engine

    api._engine.cache_clear()
    monkeypatch.setattr(api, "create_engine", fake_create)
    first = api._db_session()
    second = api._db_session()
    try:
        assert first.get_bind() is engine
        assert second.get_bind() is engine
        assert len(calls) == 1
    finally:
        first.close()
        second.close()
        api._engine.cache_clear()
