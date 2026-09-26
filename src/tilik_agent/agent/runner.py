"""End-to-end question run shared by the API, the notebook, and the benchmark."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from tilik_agent.agent.graph import compile_graph
from tilik_agent.agent.schemas import State
from tilik_agent.agent.tracing import current_callbacks, question_trace
from tilik_agent.config import get_agent_config


@dataclass
class QuestionResult:
    """Merged graph state returned to the API."""

    answer: str | None = None
    chart: dict[str, Any] | None = None
    answer_observation_ids: list[int] = field(default_factory=list)
    answer_document_ids: list[str] = field(default_factory=list)
    step_status: dict[str, Any] = field(default_factory=dict)
    plan_error: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None


def stream_question(question: str, *, callbacks: Sequence[Any] | None = None) -> Iterator[dict[str, Any]]:
    """Stream graph updates for one question.

    ``callbacks`` is how the benchmark attaches its own observer. The API and the
    notebook leave it empty. This function does not score the answer.
    """
    graph = compile_graph()
    limit = get_agent_config().graph.recursion_limit
    with question_trace(question, callbacks) as span:
        config: dict[str, Any] = {"recursion_limit": limit}
        active = current_callbacks()
        if active:
            config["callbacks"] = active
        state: State = {"question": question}
        for update in graph.stream(state, config=config, stream_mode="updates"):
            for payload in update.values():
                if isinstance(payload, dict):
                    state.update(payload)
            yield update
        if span is not None:
            span.update(output=state.get("answer"))


def run_question(question: str, *, callbacks: Sequence[Any] | None = None) -> QuestionResult:
    """Stream one question and return the merged state."""
    state: State = {"question": question}
    for update in stream_question(question, callbacks=callbacks):
        for payload in update.values():
            if isinstance(payload, dict):
                state.update(payload)
    return QuestionResult(
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
