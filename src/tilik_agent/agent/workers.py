"""Interpret a question and run its fixed source routes."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.analysis import selection_time_error
from tilik_agent.agent.retrieval import _retrieve_step
from tilik_agent.agent.runtime import history_block, model_json
from tilik_agent.agent.schemas import PlanStep, RequestIntent, SeriesSelection, State
from tilik_agent.agent.sql import _sql_step
from tilik_agent.agent.tracing import bind_trace_context


def interpret_request(state: State) -> dict[str, Any]:
    original = state.get("original_question") or state["question"]
    allowed = state.get("allowed_connectors") or ["bps", "bnpb", "bmkg", "bapanas", "sipongi"]
    prompt = f"""Interpret the latest question for Tilik, an Indonesian data analyst.
Return RequestIntent, not a sequence of work steps. The graph owns execution.
Choose 1 or 2 connectors from {allowed}. BPS has statistical tables and SP2020
publication passages. BNPB has 2010-2024 disaster counts by province/district
for all disaster types or national series by disaster type. BMKG has live village
forecasts, earthquakes and active weather alerts. Bapanas has consumer food
prices: the latest daily price and monthly averages from 2021-06, national or by
province. SiPongi has satellite hotspot counts: the last 12/24/48 hours and
monthly counts from 2015-01, national, by province, or by district; a hotspot
is not a confirmed fire. For BPS use source_route=data
for tables, context for publication, both for both. For other sources use data.
Use direct only when no lookup is needed. Never interpret a historical BNPB
count as a current BMKG forecast or a causal relationship. Bapanas and SiPongi
never use analysis_operation.
Statistical analysis_operation applies to BPS province tables and to BNPB
national series by disaster type across years. A BPS+BNPB comparison may place
validated 2024 province values side by side, but does not run correlation or
regression.
For province dispersion over time choose analysis_operation=dispersion; for
relationship choose correlation, fitted models/outliers choose ols, initial
level versus change choose convergence, and paired survey rounds choose
paired_compare. For a BNPB yearly trend, its strength, or years that deviate
from it choose ols; for how two BNPB disaster types move together across years
choose correlation. Otherwise use null. BPS analysis requires SQL data.
Extract the indicator without inventing a dataset ID. Preserve exact years.
For 'since 2017' set start_year=2017 and through_latest=true; do not guess
the latest available year. Do not request a chart for a direct answer.
Resolve follow-ups into a standalone question using the recent conversation.
{history_block(state.get("history"), "Recent conversation")}Latest user message: {original}"""
    error = ""
    for attempt in range(2):
        try:
            intent = model_json(
                RequestIntent, prompt if not attempt else prompt + f"\nPrevious invalid intent: {error}"
            )
            intent = RequestIntent.model_validate(intent)
            if not intent.connectors or set(intent.connectors) - set(allowed):
                raise ValueError("Intent chose unavailable connectors")
            years = {int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", original)}
            standalone_years = {int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", intent.question)}
            if years - standalone_years:
                raise ValueError("Standalone question lost an explicit user year")
            # A follow-up such as "the same period" names no year; its standalone question carries them.
            years = years or standalone_years
            lowered = original.casefold()
            if "menyempit sejak" in lowered and intent.analysis_operation != "dispersion":
                raise ValueError("Province gap over time requires dispersion analysis")
            if intent.analysis_operation and "sejak" in lowered and len(years) == 1:
                intent.start_year = next(iter(years))
                intent.end_year = None
                intent.through_latest = True
            elif intent.analysis_operation and years:
                intent.start_year = min(years)
                intent.end_year = max(years)
            return {
                "original_question": original,
                "question": intent.question.strip(),
                "intent": intent.model_dump(),
                "source_route": intent.source_route,
                "requested_chart": intent.requested_chart,
                "search_query": intent.question.strip(),
            }
        except (ValidationError, ValueError, json.JSONDecodeError, APIError) as exc:
            error = str(exc)
    return {"plan_error": f"Could not validate request intent: {error}"}


def route_sources(state: State) -> Literal["context", "data", "both", "direct", "external", "plan_failed"]:
    if state.get("plan_error"):
        return "plan_failed"
    if state.get("source_route") == "direct":
        return "direct"
    if any(source != "bps" for source in (state.get("intent") or {}).get("connectors", ["bps"])):
        return "external"
    return state["source_route"]


def run_external(state: State) -> dict[str, Any]:
    """Run selected source servers and retain bounded evidence for one answer."""
    from tilik_agent.agent.external import compatible_province_pairs, fetch_external

    selected = state["intent"].get("connectors", [])
    merged: dict[str, Any] = {"used_connectors": selected}
    if "bps" in selected:
        route = state["source_route"]
        if route == "context":
            merged.update(run_retrieval(state))
        elif route == "both":
            merged.update(run_both(state))
        else:
            merged.update(run_sql(state))
    merged["external_evidence"] = fetch_external(state)
    if "bps" in selected and "bnpb" in selected:
        merged["cross_source_pairs"] = compatible_province_pairs(
            merged.get("observations", []), merged["external_evidence"]
        )
    return merged


def _sql_source(state: State) -> dict[str, Any]:
    intent = RequestIntent.model_validate(state["intent"])
    analysis = intent.analysis_operation is not None
    scope = f"start_year={intent.start_year}, end_year={intent.end_year}, through_latest={intent.through_latest}"
    request = state["question"]
    for attempt in range(2 if analysis else 1):
        step = PlanStep(id="s1", action="sql_lookup", request=request)
        result = _sql_step(step, state["question"], analysis=analysis, time_scope=scope)
        if not analysis:
            return result
        valid: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in result.get("series_selections", []):
            selection = SeriesSelection.model_validate(item)
            error = selection_time_error(intent, selection)
            if error:
                errors.append(error)
            else:
                valid.append(item)
        required = 1 if intent.analysis_operation in {"dispersion", "convergence"} else 2
        if len(valid) >= required:
            result["series_selections"] = valid
            return result
        reason = "; ".join(errors) or result.get("sql_error") or "SQL did not provide enough province series"
        if attempt == 0:
            request = f"{state['question']}\nRepair the previous selection: {reason}. Select every required year."
            continue
        result["series_selections"] = []
        result["sql_error"] = reason
    return result


def _retrieval_source(state: State, step_id: str = "s1") -> dict[str, Any]:
    return _retrieve_step(
        PlanStep(id=step_id, action="retrieve_document", request=state["question"]), state["question"]
    )


def _merge_sources(items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "documents": [],
        "observations": [],
        "series_selections": [],
        "source_details": {"searches": [], "datasets": {}},
        "step_status": {},
    }
    for action, result in items:
        step_id = "s1" if action == "sql_lookup" or len(items) == 1 else "s2"
        for key in ("documents", "observations", "series_selections"):
            merged[key].extend(result.get(key, []))
        details = result.get("source_details") or {}
        merged["source_details"]["searches"].extend(details.get("searches", []))
        merged["source_details"]["datasets"].update(details.get("datasets", {}))
        for key in ("retrieval_error", "sql_error", "sql_warnings"):
            if result.get(key):
                merged[key] = result[key]
        if result.get("search_query"):
            merged["search_query"] = result["search_query"]
        count = sum(len(result.get(key, [])) for key in ("documents", "observations", "series_selections"))
        error = result.get("retrieval_error") or (result.get("sql_error") if not count else "") or ""
        truncated = any(row.get("truncated_batch") for row in result.get("observations", []))
        status = "succeeded" if count and not error and not truncated else "partial" if count else "failed"
        merged["step_status"][step_id] = {
            "action": action,
            "status": status,
            "error": error,
            "warning": result.get("sql_warnings") or "",
            "evidence_count": count,
        }
    return merged


def run_sql(state: State) -> dict[str, Any]:
    return _merge_sources([("sql_lookup", _sql_source(state))])


def run_retrieval(state: State) -> dict[str, Any]:
    return _merge_sources([("retrieve_document", _retrieval_source(state))])


def run_both(state: State) -> dict[str, Any]:
    sql_job = bind_trace_context(_sql_source, state)
    retrieval_job = bind_trace_context(_retrieval_source, state, "s2")
    with ThreadPoolExecutor(max_workers=2) as pool:
        sql_future = pool.submit(sql_job)
        retrieval_future = pool.submit(retrieval_job)
        return _merge_sources(
            [
                ("sql_lookup", sql_future.result()),
                ("retrieve_document", retrieval_future.result()),
            ]
        )
