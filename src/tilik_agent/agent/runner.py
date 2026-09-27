"""End-to-end question run shared by the API, the notebook, and the benchmark."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from tilik_agent.agent.graph import compile_graph
from tilik_agent.agent.schemas import ChatTurn, State
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
    standalone_question: str | None = None
    source_route: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None


_TRACE_NODES = frozenset({"agent", "tools"})


def stream_question(
    question: str,
    *,
    history: Sequence[ChatTurn] | None = None,
    conversation_id: str | None = None,
    callbacks: Sequence[Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream graph updates for one question, including each inner SQL step.

    ``callbacks`` is how the benchmark attaches its own observer. The API and the
    notebook leave it empty. This function does not score the answer.
    """
    graph = compile_graph()
    limit = get_agent_config().graph.recursion_limit
    with question_trace(question, callbacks, conversation_id=conversation_id) as span:
        config: dict[str, Any] = {"recursion_limit": limit}
        active = current_callbacks()
        if active:
            config["callbacks"] = active
        state: State = {
            "question": question,
            "original_question": question,
            "history": list(history or []),
        }
        for item in graph.stream(state, config=config, stream_mode=["updates", "custom"]):
            mode, update = _stream_item(item)
            if mode == "updates":
                _merge_state(state, update)
            yield update
        if span is not None:
            span.update(output=state.get("answer"))


def _stream_item(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, tuple) and len(item) == 2 and item[0] in {"updates", "custom"}:
        return item
    return "updates", item


def _merge_state(state: State, update: dict[str, Any]) -> None:
    for node_name, payload in update.items():
        if node_name in _TRACE_NODES or not isinstance(payload, dict):
            continue
        state.update(payload)


def run_question(
    question: str,
    *,
    history: Sequence[ChatTurn] | None = None,
    conversation_id: str | None = None,
    callbacks: Sequence[Any] | None = None,
) -> QuestionResult:
    """Stream one question and return the merged state."""
    state: State = {
        "question": question,
        "original_question": question,
        "history": list(history or []),
    }
    for update in stream_question(
        question,
        history=history,
        conversation_id=conversation_id,
        callbacks=callbacks,
    ):
        _merge_state(state, update)
    return QuestionResult(
        answer=state.get("answer"),
        chart=state.get("chart"),
        answer_observation_ids=state.get("answer_observation_ids") or [],
        answer_document_ids=state.get("answer_document_ids") or [],
        step_status=state.get("step_status") or {},
        plan_error=state.get("plan_error"),
        standalone_question=state.get("question"),
        source_route=state.get("source_route"),
        sql_error=state.get("sql_error"),
        sql_warnings=state.get("sql_warnings"),
        retrieval_error=state.get("retrieval_error"),
    )
