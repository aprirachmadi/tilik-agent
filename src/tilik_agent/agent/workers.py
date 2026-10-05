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
    prompt = f"""Interpret the latest question for a BPS statistics assistant.
Return RequestIntent, not a sequence of work steps. The graph owns execution.
Use source_route=data for BPS observation tables, context for passages in the
configured publication collection, both only when both types of evidence are
needed, and direct only when the conversation already supplies the answer or
the user needs no lookup. Publication search cannot fill missing SQL values.
For province dispersion over time choose analysis_operation=dispersion; for
relationship choose correlation, fitted models/outliers choose ols, initial
level versus change choose convergence, and paired survey rounds choose
paired_compare. Otherwise use null. Statistical analysis requires SQL data.
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
            years = {int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", original)}
            standalone_years = {int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", intent.question)}
            if years - standalone_years:
                raise ValueError("Standalone question lost an explicit user year")
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


def route_sources(state: State) -> Literal["context", "data", "both", "direct", "plan_failed"]:
    if state.get("plan_error"):
        return "plan_failed"
    return state["source_route"]


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
