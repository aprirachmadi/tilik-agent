"""HTTP entry point for one question against the compiled graph."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.agent.runner import run_question
from tilik_agent.chat.models import Conversation
from tilik_agent.chat.storage import append_turn, conversation_title_from_question, ensure_conversation, load_history
from tilik_agent.config import get_settings

app = FastAPI(title="Tilik")


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None


class QuestionResponse(BaseModel):
    conversation_id: str
    answer: str | None = None
    chart: dict[str, Any] | None = None
    answer_observation_ids: list[int] = Field(default_factory=list)
    answer_document_ids: list[str] = Field(default_factory=list)
    step_status: dict[str, Any] = Field(default_factory=dict)
    plan_error: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None


def _db_session() -> Session:
    engine = create_engine(get_settings().database_url)
    return Session(engine)


@app.post("/questions")
def questions(body: QuestionRequest) -> QuestionResponse:
    """Answer one question and return the chart spec when the graph built one."""
    with _db_session() as session:
        conversation_id = ensure_conversation(session, body.conversation_id)
        history = load_history(session, conversation_id) if body.conversation_id else []
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None and conversation.title is None and not history:
            conversation.title = conversation_title_from_question(body.question)
        session.commit()

    result = run_question(body.question, history=history, conversation_id=conversation_id)

    with _db_session() as session:
        append_turn(
            session,
            conversation_id,
            user_content=body.question,
            assistant_content=result.answer,
            standalone_question=result.standalone_question,
        )

    return QuestionResponse(
        conversation_id=conversation_id,
        answer=result.answer,
        chart=result.chart,
        answer_observation_ids=result.answer_observation_ids,
        answer_document_ids=result.answer_document_ids,
        step_status=result.step_status,
        plan_error=result.plan_error,
        sql_error=result.sql_error,
        sql_warnings=result.sql_warnings,
        retrieval_error=result.retrieval_error,
    )
