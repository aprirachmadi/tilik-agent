"""Load and persist conversation history for the question API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.agent.schemas import ChatTurn
from tilik_agent.chat.models import Conversation, Message
from tilik_agent.config import get_agent_config


def ensure_conversation(session: Session, conversation_id: str | None) -> str:
    """Return an existing conversation id or create a new row."""
    if conversation_id:
        existing = session.get(Conversation, conversation_id)
        if existing is None:
            session.add(Conversation(id=conversation_id))
            session.flush()
        return conversation_id
    new_id = str(uuid.uuid4())
    session.add(Conversation(id=new_id))
    session.flush()
    return new_id


def load_history(session: Session, conversation_id: str) -> list[ChatTurn]:
    """Return the most recent user and assistant messages for planning."""
    rows = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(get_agent_config().conversation.max_history_messages)
    ).all()
    turns: list[ChatTurn] = [{"role": row.role, "content": row.content} for row in reversed(rows)]
    return turns


def append_turn(
    session: Session,
    conversation_id: str,
    *,
    user_content: str,
    assistant_content: str | None,
    standalone_question: str | None = None,
    trace_id: str | None = None,
    chart: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    source_route: str | None = None,
    chart_error: str | None = None,
) -> None:
    """Persist one user message and optional assistant reply."""
    session.add(Message(conversation_id=conversation_id, role="user", content=user_content))
    if assistant_content is not None:
        session.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                standalone_question=standalone_question,
                trace_id=trace_id,
                chart=chart,
                sources=sources or [],
                source_route=source_route,
                chart_error=chart_error,
            )
        )
    conversation = session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = datetime.now(UTC)
    session.commit()


def list_conversations(session: Session) -> list[Conversation]:
    """Return every thread, newest activity first."""
    return list(
        session.scalars(select(Conversation).order_by(Conversation.updated_at.desc(), Conversation.created_at.desc()))
    )


def load_full_thread(session: Session, conversation_id: str) -> tuple[Conversation, list[Message]] | None:
    """Return one conversation and every message, or none when the id is unknown."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    messages = list(
        session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc()))
    )
    return conversation, messages


def conversation_title_from_question(question: str, *, max_len: int = 240) -> str:
    """Build a title from the first user message, breaking on a word when it must be shortened."""
    collapsed = " ".join(question.split())
    if len(collapsed) <= max_len:
        return collapsed
    clipped = collapsed[:max_len].rsplit(" ", 1)[0].rstrip()
    if not clipped:
        clipped = collapsed[:max_len].rstrip()
    return clipped + "…"
