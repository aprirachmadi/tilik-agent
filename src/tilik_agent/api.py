"""HTTP entry point for one question against the compiled graph."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from tilik_agent.agent import compile_graph
from tilik_agent.agent.schemas import State
from tilik_agent.config import get_agent_config

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


def run_question(question: str) -> QuestionResponse:
    """Stream the graph the same way the notebook does and return the merged state."""
    graph = compile_graph()
    limit = get_agent_config().graph.recursion_limit
    state: State = {"question": question}
    for update in graph.stream(state, config={"recursion_limit": limit}, stream_mode="updates"):
        for payload in update.values():
            state.update(payload)
    return QuestionResponse(
        answer=state.get("answer"),
        chart=state.get("chart"),
        answer_observation_ids=state.get("answer_observation_ids") or [],
        answer_document_ids=state.get("answer_document_ids") or [],
        step_status=state.get("step_status") or {},
        plan_error=state.get("plan_error"),
        sql_error=state.get("sql_error"),
        sql_warnings=state.get("sql_warnings"),
        retrieval_error=state.get("retrieval_error"),
    )


@app.post("/questions")
def questions(body: QuestionRequest) -> QuestionResponse:
    """Answer one question and return the chart spec when the graph built one."""
    return run_question(body.question)
