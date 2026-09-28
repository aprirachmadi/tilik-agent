"""HTTP entry point for one question against the compiled graph."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tilik_agent.agent.runner import (
    QuestionResult,
    initial_question_state,
    merge_question_update,
    question_result,
    run_question,
    stream_question,
)
from tilik_agent.agent.schemas import ChatTurn
from tilik_agent.agent.tracing import current_trace_id
from tilik_agent.chat.models import Conversation, Message
from tilik_agent.ingestion.models import Dataset
from tilik_agent.chat.storage import (
    append_turn,
    conversation_title_from_question,
    ensure_conversation,
    list_conversations,
    load_full_thread,
    load_history,
)
from tilik_agent.config import get_settings

_FRONTEND_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
_SOURCE_NODES = ("run_sql", "run_retrieval", "run_both")

app = FastAPI(title="Tilik")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_FRONTEND_ORIGINS),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None


class QuestionResponse(BaseModel):
    conversation_id: str
    answer: str | None = None
    chart: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    source_route: str | None = None
    chart_error: str | None = None
    answer_observation_ids: list[int] = Field(default_factory=list)
    answer_document_ids: list[str] = Field(default_factory=list)
    step_status: dict[str, Any] = Field(default_factory=dict)
    plan_error: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationMessage(BaseModel):
    id: int
    role: str
    content: str
    chart: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    source_route: str | None = None
    chart_error: str | None = None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessage]


@lru_cache(maxsize=1)
def _engine() -> Any:
    return create_engine(get_settings().database_url)


def _db_session() -> Session:
    return Session(_engine())


def status_lines(update: dict[str, Any]) -> list[str]:
    """Map one graph update to the short progress lines the workspace shows."""
    plan = update.get("plan_question")
    if isinstance(plan, dict):
        route = plan.get("source_route")
        if route == "data":
            return ["Looking up datasets"]
        if route == "context":
            return ["Searching SP2020"]
        if route == "both":
            return ["Looking up datasets", "Searching SP2020"]
        return ["Writing the answer"]
    if any(name in update for name in _SOURCE_NODES):
        return ["Writing the answer"]
    return []


def _open_turn(body: QuestionRequest) -> tuple[str, list[ChatTurn]]:
    with _db_session() as session:
        conversation_id = ensure_conversation(session, body.conversation_id)
        history = load_history(session, conversation_id) if body.conversation_id else []
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None and conversation.title is None and not history:
            conversation.title = conversation_title_from_question(body.question)
        session.commit()
        return conversation_id, history


def _response(conversation_id: str, result: QuestionResult) -> QuestionResponse:
    return QuestionResponse(
        conversation_id=conversation_id,
        answer=result.answer,
        chart=result.chart,
        sources=result.sources,
        source_route=result.source_route,
        chart_error=result.chart_error,
        answer_observation_ids=result.answer_observation_ids,
        answer_document_ids=result.answer_document_ids,
        step_status=result.step_status,
        plan_error=result.plan_error,
        sql_error=result.sql_error,
        sql_warnings=result.sql_warnings,
        retrieval_error=result.retrieval_error,
    )


def _save_turn(conversation_id: str, question: str, result: QuestionResult) -> QuestionResponse:
    with _db_session() as session:
        result.sources = _with_tablesource(session, result.sources)
        append_turn(
            session,
            conversation_id,
            user_content=question,
            assistant_content=result.answer,
            standalone_question=result.standalone_question,
            trace_id=result.trace_id,
            chart=result.chart,
            sources=result.sources,
            source_route=result.source_route,
            chart_error=result.chart_error,
        )
    return _response(conversation_id, result)


def _with_tablesource(session: Session, sources: list[Any]) -> list[dict[str, Any]]:
    """Copy source chips and fill tablesource from dataset when an older chip omitted it."""
    copied = [dict(source) for source in sources if isinstance(source, dict)]
    missing = {
        int(source["dataset_id"])
        for source in copied
        if source.get("kind") == "dataset"
        and not str(source.get("tablesource") or "").strip()
        and source.get("dataset_id") is not None
    }
    if not missing:
        return copied
    rows = session.execute(select(Dataset.id, Dataset.tablesource).where(Dataset.id.in_(missing))).all()
    found = {int(row.id): str(row.tablesource) for row in rows}
    for source in copied:
        if source.get("kind") != "dataset" or str(source.get("tablesource") or "").strip():
            continue
        raw_id = source.get("dataset_id")
        if raw_id is None:
            continue
        tablesource = found.get(int(raw_id))
        if tablesource:
            source["tablesource"] = tablesource
    return copied


def _message_response(session: Session, row: Message) -> ConversationMessage:
    return ConversationMessage(
        id=row.id,
        role=row.role,
        content=row.content,
        chart=row.chart,
        sources=_with_tablesource(session, list(row.sources or [])),
        source_route=row.source_route,
        chart_error=row.chart_error,
        created_at=row.created_at,
    )


def _summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@app.get("/conversations")
def conversations() -> list[ConversationSummary]:
    """List threads for the sidebar, newest activity first."""
    with _db_session() as session:
        return [_summary(row) for row in list_conversations(session)]


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: str) -> ConversationDetail:
    """Return one thread, including charts and source chips saved on assistant messages."""
    with _db_session() as session:
        loaded = load_full_thread(session, conversation_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        row, messages = loaded
        return ConversationDetail(
            **_summary(row).model_dump(),
            messages=[_message_response(session, message) for message in messages],
        )


@app.post("/questions")
def questions(body: QuestionRequest) -> QuestionResponse:
    """Answer one question and return the chart spec when the graph built one."""
    conversation_id, history = _open_turn(body)
    result = run_question(body.question, history=history, conversation_id=conversation_id)
    return _save_turn(conversation_id, body.question, result)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_chunks(body: QuestionRequest) -> Iterator[str]:
    conversation_id, history = _open_turn(body)
    state = initial_question_state(body.question, history)
    trace_id: str | None = None
    sent: set[str] = set()
    for update in stream_question(body.question, history=history, conversation_id=conversation_id):
        for line in status_lines(update):
            if line in sent:
                continue
            sent.add(line)
            yield _sse("status", {"status": line})
        merge_question_update(state, update)
        trace_id = current_trace_id() or trace_id
    result = question_result(state, trace_id=trace_id)
    saved = _save_turn(conversation_id, body.question, result)
    yield _sse("result", saved.model_dump(mode="json"))


def _event_stream(body: QuestionRequest) -> Iterator[str]:
    """Yield SSE chunks from one thread so the question trace stays in one context."""
    chunks: queue.Queue[str | Exception | None] = queue.Queue()

    def produce() -> None:
        try:
            for chunk in _event_chunks(body):
                chunks.put(chunk)
        except Exception as exc:
            chunks.put(exc)
        else:
            chunks.put(None)

    threading.Thread(target=produce, daemon=True).start()
    while True:
        item = chunks.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


@app.post("/questions/stream")
def questions_stream(body: QuestionRequest) -> StreamingResponse:
    """Stream a few status lines, then the same payload as POST /questions."""
    return StreamingResponse(
        _event_stream(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
