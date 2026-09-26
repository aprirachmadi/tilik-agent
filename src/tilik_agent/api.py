"""HTTP entry point for one question against the compiled graph."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from tilik_agent.agent.runner import run_question

app = FastAPI(title="Tilik")


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)


class QuestionResponse(BaseModel):
    answer: str | None = None
    chart: dict[str, Any] | None = None
    answer_observation_ids: list[int] = Field(default_factory=list)
    answer_document_ids: list[str] = Field(default_factory=list)
    step_status: dict[str, Any] = Field(default_factory=dict)
    plan_error: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None


@app.post("/questions")
def questions(body: QuestionRequest) -> QuestionResponse:
    """Answer one question and return the chart spec when the graph built one."""
    result = run_question(body.question)
    return QuestionResponse(
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
