"""HTTP entry point for one question against the compiled graph."""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
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
from tilik_agent.chat.storage import (
    SHOWCASE_OWNER,
    ConversationNotFound,
    append_turn,
    conversation_title_from_question,
    ensure_conversation,
    list_conversations,
    load_full_thread,
    load_history,
)
from tilik_agent.config import get_settings
from tilik_agent.connectors.snapshots import ExternalSourceSnapshot
from tilik_agent.ingestion.models import Dataset

_FRONTEND_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
_SOURCE_NODES = ("run_sql", "run_retrieval", "run_both", "run_external")
VISITOR_COOKIE = "tilik_visitor"
_VISITOR_MAX_AGE = 60 * 60 * 24 * 365

app = FastAPI(title="Tilik")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_FRONTEND_ORIGINS),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _valid_visitor(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


@app.middleware("http")
async def assign_visitor(request: Request, call_next: Any) -> Response:
    """Give each browser a private random id; every conversation belongs to one."""
    visitor = request.cookies.get(VISITOR_COOKIE, "")
    fresh = not _valid_visitor(visitor)
    if fresh:
        visitor = str(uuid.uuid4())
    request.state.visitor_id = visitor
    response: Response = await call_next(request)
    if fresh:
        response.set_cookie(
            VISITOR_COOKIE, visitor, max_age=_VISITOR_MAX_AGE, httponly=True, secure=True, samesite="lax"
        )
    return response


def _visitor(request: Request) -> str:
    return request.state.visitor_id


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None
    sources: list[Literal["bps", "bnpb", "bmkg", "bapanas", "sipongi"]] | None = None


class QuestionResponse(BaseModel):
    conversation_id: str
    answer: str | None = None
    chart: dict[str, Any] | None = None
    blocks: list[dict[str, str]] | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    used_connectors: list[str] = Field(default_factory=list)
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
    pinned: bool = False


class ConversationMessage(BaseModel):
    id: int
    role: str
    content: str
    chart: dict[str, Any] | None = None
    blocks: list[dict[str, str]] | None = None
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
    intent = update.get("interpret_request")
    if isinstance(intent, dict):
        selected = (intent.get("intent") or {}).get("connectors") or ["bps"]
        if any(source != "bps" for source in selected):
            return ["Checking " + " and ".join(source.upper() for source in selected)]
        route = intent.get("source_route")
        if route == "data":
            return ["Looking up datasets"]
        if route == "context":
            return ["Searching SP2020"]
        if route == "both":
            return ["Looking up datasets", "Searching SP2020"]
        return ["Writing the answer"]
    if any(name in update for name in _SOURCE_NODES):
        if any((update.get(name) or {}).get("series_selections") for name in _SOURCE_NODES):
            return ["Analyzing province data"]
        return ["Writing the answer"]
    if "analyze_data" in update:
        return ["Writing the answer"]
    return []


def _open_turn(body: QuestionRequest, visitor: str) -> tuple[str, list[ChatTurn]]:
    """Open the visitor's conversation for a new turn; another visitor's id reads as missing."""
    with _db_session() as session:
        try:
            conversation_id = ensure_conversation(session, body.conversation_id, visitor)
        except ConversationNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
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
        blocks=result.blocks,
        sources=result.sources,
        used_connectors=result.used_connectors,
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
        external = {item["id"]: item for item in result.external_evidence if "data" in item}
        for source in result.sources:
            # Live feeds change; keep the payload a saved answer cited. Synced tables are already versioned.
            provider = source.get("provider")
            if source.get("kind") != "external" or provider not in {"BMKG", "Bapanas", "SiPongi"}:
                continue
            item = external.get(source.get("evidence_id"))
            if item is None or not item.get("snapshot_payload"):
                continue
            content = {"normalized": item["data"], "raw": item.get("snapshot_payload")}
            serialized = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
            if len(serialized) > 2_000_000:
                raise ValueError(f"Cited {provider} evidence exceeds snapshot size limit")
            digest = hashlib.sha256(serialized.encode()).hexdigest()
            snapshot = session.scalar(
                select(ExternalSourceSnapshot).where(
                    ExternalSourceSnapshot.provider == provider, ExternalSourceSnapshot.content_sha256 == digest
                )
            )
            if snapshot is None:
                snapshot = ExternalSourceSnapshot(
                    provider=provider, source_url=str(source.get("url") or ""),
                    content_sha256=digest, content=content,
                )
                session.add(snapshot)
                session.flush()
            source["snapshot_id"] = snapshot.id
        append_turn(
            session,
            conversation_id,
            user_content=question,
            assistant_content=result.answer,
            standalone_question=result.standalone_question,
            trace_id=result.trace_id,
            chart=result.chart,
            blocks=result.blocks,
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
        blocks=row.blocks,
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
        pinned=conversation.owner_id == SHOWCASE_OWNER,
    )


@app.get("/health")
def health() -> JSONResponse:
    """Report whether the API can run a query against Postgres."""
    try:
        with _engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/conversations")
def conversations(visitor: str = Depends(_visitor)) -> list[ConversationSummary]:
    """List this visitor's threads for the sidebar, newest activity first."""
    with _db_session() as session:
        return [_summary(row) for row in list_conversations(session, visitor)]


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, visitor: str = Depends(_visitor)) -> ConversationDetail:
    """Return one of this visitor's threads, including charts and source chips saved on assistant messages."""
    with _db_session() as session:
        loaded = load_full_thread(session, conversation_id, visitor)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        row, messages = loaded
        return ConversationDetail(
            **_summary(row).model_dump(),
            messages=[_message_response(session, message) for message in messages],
        )


@app.post("/questions")
def questions(body: QuestionRequest, visitor: str = Depends(_visitor)) -> QuestionResponse:
    """Answer one question and return the chart spec when the graph built one."""
    conversation_id, history = _open_turn(body, visitor)
    result = run_question(body.question, history=history, conversation_id=conversation_id, sources=body.sources)
    return _save_turn(conversation_id, body.question, result)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_chunks(body: QuestionRequest, conversation_id: str, history: list[ChatTurn]) -> Iterator[str]:
    state = initial_question_state(body.question, history, body.sources)
    trace_id: str | None = None
    sent: set[str] = set()
    for update in stream_question(
        body.question, history=history, conversation_id=conversation_id, sources=body.sources
    ):
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


def _event_stream(body: QuestionRequest, conversation_id: str, history: list[ChatTurn]) -> Iterator[str]:
    """Yield SSE chunks from one thread so the question trace stays in one context."""
    chunks: queue.Queue[str | Exception | None] = queue.Queue()

    def produce() -> None:
        try:
            for chunk in _event_chunks(body, conversation_id, history):
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
def questions_stream(body: QuestionRequest, visitor: str = Depends(_visitor)) -> StreamingResponse:
    """Stream a few status lines, then the same payload as POST /questions."""
    conversation_id, history = _open_turn(body, visitor)
    return StreamingResponse(
        _event_stream(body, conversation_id, history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
