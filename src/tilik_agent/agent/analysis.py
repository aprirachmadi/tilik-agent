"""Route validated selections into deterministic statistical operations."""

from __future__ import annotations

import json
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.runtime import model_json
from tilik_agent.agent.schemas import AnalysisRequest, RequestIntent, SeriesSelection, State
from tilik_agent.agent.statistics import compute


def selection_time_error(intent: RequestIntent, selection: SeriesSelection) -> str:
    """Check one SQL selection before it can enter the statistical branch."""
    if intent.start_year is None and intent.end_year is None:
        return ""
    from tilik_agent.agent.sql import get_connection

    with get_connection() as conn:
        selected = set(selection.period_labels)
        all_years = {
            label
            for (label,) in conn.execute(
                "SELECT DISTINCT label FROM period WHERE dataset_id=%s AND label ~ '^[0-9]{4}$'",
                (selection.dataset_id,),
            ).fetchall()
        }
        if intent.start_year and str(intent.start_year) not in selected:
            return f"Selection {selection.selection_id} omitted requested start year {intent.start_year}"
        if intent.end_year and str(intent.end_year) not in selected:
            return f"Selection {selection.selection_id} omitted requested end year {intent.end_year}"
        if intent.start_year == intent.end_year and not intent.through_latest and selected != {str(intent.start_year)}:
            return f"Selection {selection.selection_id} includes periods outside requested year {intent.start_year}"
        if intent.through_latest:
            expected = {year for year in all_years if int(year) >= (intent.start_year or 0)}
            if expected - selected:
                return f"Selection {selection.selection_id} omitted requested years: {sorted(expected - selected)}"
    return ""


def route_after_sources(state: State) -> Literal["analyze_data", "write_answer"]:
    intent = RequestIntent.model_validate(state["intent"])
    return "analyze_data" if intent.analysis_operation else "write_answer"


def analyze_data(state: State) -> dict[str, Any]:
    try:
        intent = RequestIntent.model_validate(state["intent"])
        if not intent.analysis_operation:
            raise ValueError("No statistical operation requested")
        selections = [SeriesSelection.model_validate(item) for item in state.get("series_selections", [])]
        required = 1 if intent.analysis_operation in {"dispersion", "convergence"} else 2
        if len(selections) < required:
            raise ValueError(state.get("sql_error") or "SQL did not provide enough validated province series")
        # The model receives labels, meaning, and coverage, never raw panel rows.
        summaries = [
            {
                "selection_id": item.selection_id,
                "dataset_title": item.dataset_title,
                "measure_name": item.measure_name,
                "unit": item.unit,
                "periods": item.period_labels,
                "coverage": {period: info["count"] for period, info in item.coverage.items()},
            }
            for item in selections
        ]
        if len(selections) == required:
            request = AnalysisRequest(
                operation=intent.analysis_operation,
                selection_ids=[item.selection_id for item in selections],
            )
        else:
            prompt = f"""Choose {required} selection IDs for the requested {intent.analysis_operation}
analysis. Return AnalysisRequest with exactly that operation. Use only the
supplied selection IDs. Do not calculate statistics yourself.
Question: {state["question"]}
Selections: {json.dumps(summaries, ensure_ascii=False)}"""
            request = model_json(AnalysisRequest, prompt)
            if request.operation != intent.analysis_operation:
                raise ValueError("Selection choice changed the requested operation")
        result = compute(request, selections)
        return {
            "analysis_results": [result.model_dump()],
            "analysis_error": "",
            "step_status": {
                **state.get("step_status", {}),
                "analysis": {"action": "analyze_data", "status": "succeeded", "error": "", "evidence_count": 1},
            },
        }
    except (ValueError, ValidationError, APIError) as exc:
        error = f"Statistical analysis unavailable: {exc}"
        return {
            "analysis_results": [],
            "analysis_error": error,
            "step_status": {
                **state.get("step_status", {}),
                "analysis": {"action": "analyze_data", "status": "failed", "error": error, "evidence_count": 0},
            },
        }
