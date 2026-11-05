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

SHOWCASE_OWNER = "showcase"
"""Owner of the pinned example conversations every visitor can read but nobody can continue."""


class ConversationNotFound(LookupError):
    """The conversation does not exist, or belongs to another visitor."""


def owned_conversation(session: Session, conversation_id: str, owner_id: str) -> Conversation | None:
    """Return the conversation only when this visitor owns it."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id != owner_id:
        return None
    return conversation


def ensure_conversation(session: Session, conversation_id: str | None, owner_id: str) -> str:
    """Return the visitor's existing conversation id, or create a new row when none is given."""
    if conversation_id:
        if owned_conversation(session, conversation_id, owner_id) is None:
            raise ConversationNotFound(conversation_id)
        return conversation_id
    new_id = str(uuid.uuid4())
    session.add(Conversation(id=new_id, owner_id=owner_id))
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
    blocks: list[dict[str, str]] | None = None,
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
                blocks=blocks,
                sources=sources or [],
                source_route=source_route,
                chart_error=chart_error,
            )
        )
    conversation = session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = datetime.now(UTC)
    session.commit()


def list_conversations(session: Session, owner_id: str) -> list[Conversation]:
    """Return the pinned examples, then the visitor's threads, each newest activity first."""
    rows = session.scalars(
        select(Conversation)
        .where(Conversation.owner_id.in_([SHOWCASE_OWNER, owner_id]))
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    )
    return sorted(rows, key=lambda row: row.owner_id != SHOWCASE_OWNER)


def load_full_thread(
    session: Session, conversation_id: str, owner_id: str
) -> tuple[Conversation, list[Message]] | None:
    """Return one of the visitor's conversations or a pinned example, or none when it is neither."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id not in (owner_id, SHOWCASE_OWNER):
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
