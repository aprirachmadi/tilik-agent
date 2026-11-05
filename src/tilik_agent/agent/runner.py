"""End-to-end question run shared by the API, the notebook, and the benchmark."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from tilik_agent.agent.answer import answer_blocks
from tilik_agent.agent.graph import compile_graph
from tilik_agent.agent.schemas import ChatTurn, State
from tilik_agent.agent.tracing import current_callbacks, current_trace_id, question_trace
from tilik_agent.config import get_agent_config


@dataclass
class QuestionResult:
    """Merged graph state returned to the API."""

    answer: str | None = None
    chart: dict[str, Any] | None = None
    blocks: list[dict[str, str]] | None = None
    answer_observation_ids: list[int] = field(default_factory=list)
    answer_document_ids: list[str] = field(default_factory=list)
    analysis_results: list[dict[str, Any]] = field(default_factory=list)
    series_selections: list[dict[str, Any]] = field(default_factory=list)
    analysis_error: str | None = None
    step_status: dict[str, Any] = field(default_factory=dict)
    plan_error: str | None = None
    standalone_question: str | None = None
    source_route: str | None = None
    sql_error: str | None = None
    sql_warnings: str | None = None
    retrieval_error: str | None = None
    chart_error: str | None = None
    trace_id: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    used_connectors: list[str] = field(default_factory=list)
    external_evidence: list[dict[str, Any]] = field(default_factory=list)


_TRACE_NODES = frozenset({"agent", "tools"})


def _trusted_source_url(provider: str, url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    allowed = {
        "BNPB": {"data.bnpb.go.id"},
        "BMKG": {"api.bmkg.go.id", "data.bmkg.go.id", "www.bmkg.go.id"},
    }
    return url if parsed.scheme == "https" and parsed.hostname in allowed.get(provider, set()) else None


def stream_question(
    question: str,
    *,
    history: Sequence[ChatTurn] | None = None,
    conversation_id: str | None = None,
    callbacks: Sequence[Any] | None = None,
    sources: list[str] | None = None,
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
            "allowed_connectors": sources,
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


def initial_question_state(
    question: str, history: Sequence[ChatTurn] | None = None, sources: list[str] | None = None
) -> State:
    """Return the graph state for one user message."""
    return {
        "question": question,
        "original_question": question,
        "history": list(history or []),
        "allowed_connectors": sources,
    }


def merge_question_update(state: State, update: dict[str, Any]) -> None:
    """Fold one streamed graph update into ``state``."""
    _merge_state(state, update)


def cited_sources(state: State) -> list[dict[str, Any]]:
    """Build the source chip from cited observation and document ids."""
    observations = {
        int(row["observation_id"]): row
        for row in state.get("observations") or []
        if row.get("observation_id") is not None
    }
    datasets = (state.get("source_details") or {}).get("datasets") or {}
    sources: list[dict[str, Any]] = []
    seen_datasets: set[int] = set()
    for observation_id in state.get("answer_observation_ids") or []:
        row = observations.get(int(observation_id))
        if row is None:
            continue
        raw_dataset_id = row.get("dataset_id")
        if raw_dataset_id is None:
            continue
        dataset_id = int(raw_dataset_id)
        if dataset_id in seen_datasets:
            continue
        seen_datasets.add(dataset_id)
        dataset = (datasets.get(str(dataset_id)) or {}).get("dataset") or {}
        sources.append(
            {
                "kind": "dataset",
                "dataset_id": dataset_id,
                "bps_table_id": dataset.get("bps_table_id"),
                "tablesource": dataset.get("tablesource"),
                "title": dataset.get("title"),
            }
        )
    cited_selections = set(state.get("answer_analysis_selection_ids") or [])
    for selection in state.get("series_selections") or []:
        if selection["selection_id"] not in cited_selections:
            continue
        dataset_id = int(selection["dataset_id"])
        if dataset_id in seen_datasets:
            continue
        seen_datasets.add(dataset_id)
        dataset = (datasets.get(str(dataset_id)) or {}).get("dataset") or {}
        sources.append(
            {
                "kind": "dataset",
                "dataset_id": dataset_id,
                "bps_table_id": dataset.get("bps_table_id"),
                "tablesource": dataset.get("tablesource"),
                "title": selection.get("dataset_title"),
            }
        )
    seen_documents: set[str] = set()
    for document_id in state.get("answer_document_ids") or []:
        key = str(document_id)
        if key in seen_documents:
            continue
        seen_documents.add(key)
        sources.append({"kind": "document", "document_id": key})
    cited_external = set(state.get("answer_external_ids") or [])
    for item in state.get("external_evidence") or []:
        if item["id"] not in cited_external or "data" not in item:
            continue
        data = item["data"]
        rows = data.get("rows") or []
        years = [row["year"] for row in rows if isinstance(row.get("year"), int)]
        warnings = data.get("warnings") or []
        data_time = data.get("analysis_date")
        if years:
            data_time = str(min(years)) if min(years) == max(years) else f"{min(years)}–{max(years)}"
        elif data.get("events"):
            data_time = data["events"][0].get("datetime")
        elif len(warnings) == 1:
            data_time = warnings[0].get("sent")
        source_url = warnings[0].get("source_url") if len(warnings) == 1 else data.get("source_url")
        title = data.get("title") or {
            "forecast": "Prakiraan cuaca", "earthquakes": "Gempabumi", "warnings": "Peringatan dini cuaca"
        }.get(data.get("kind"), "Source data")
        sources.append({
            "kind": "external", "provider": data.get("provider", item["provider"]),
            "evidence_id": item["id"],
            "title": title,
            "url": _trusted_source_url(data.get("provider", item["provider"]), source_url),
            "source_id": data.get("resource_id") or data.get("source_id"),
            "retrieved_at": data.get("retrieved_at"), "data_time": data_time,
            "expires_at": warnings[0].get("expires") if len(warnings) == 1 else None,
        })
    return sources


def question_result(state: State, *, trace_id: str | None = None) -> QuestionResult:
    """Copy the merged graph state into the object the API stores and returns."""
    raw_answer = state.get("answer")
    chart = state.get("chart")
    answer, blocks = answer_blocks(raw_answer, chart) if raw_answer is not None else (None, None)
    return QuestionResult(
        answer=answer,
        chart=chart,
        blocks=blocks,
        answer_observation_ids=state.get("answer_observation_ids") or [],
        answer_document_ids=state.get("answer_document_ids") or [],
        analysis_results=state.get("analysis_results") or [],
        series_selections=state.get("series_selections") or [],
        analysis_error=state.get("analysis_error"),
        step_status=state.get("step_status") or {},
        plan_error=state.get("plan_error"),
        standalone_question=state.get("question"),
        source_route=state.get("source_route"),
        sql_error=state.get("sql_error"),
        sql_warnings=state.get("sql_warnings"),
        retrieval_error=state.get("retrieval_error"),
        chart_error=state.get("chart_error"),
        trace_id=trace_id,
        sources=cited_sources(state),
        used_connectors=state.get("used_connectors") or state.get("intent", {}).get("connectors", []),
        external_evidence=state.get("external_evidence") or [],
    )


def run_question(
    question: str,
    *,
    history: Sequence[ChatTurn] | None = None,
    conversation_id: str | None = None,
    callbacks: Sequence[Any] | None = None,
    sources: list[str] | None = None,
) -> QuestionResult:
    """Stream one question and return the merged state."""
    state = initial_question_state(question, history, sources)
    trace_id: str | None = None
    for update in stream_question(
        question,
        history=history,
        conversation_id=conversation_id,
        callbacks=callbacks,
        sources=sources,
    ):
        merge_question_update(state, update)
        trace_id = current_trace_id() or trace_id
    return question_result(state, trace_id=trace_id)
