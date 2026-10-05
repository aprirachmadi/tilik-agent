"""Route validated selections into deterministic statistical operations."""

from __future__ import annotations

import json
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.runtime import model_json
from tilik_agent.agent.schemas import AnalysisRequest, SeriesSelection, State, WorkPlan
from tilik_agent.agent.statistics import compute


def _time_error(state: State, selections: list[SeriesSelection]) -> str:
    plan = WorkPlan.model_validate(state["plan"])
    if not selections:
        return "SQL did not provide validated province series selections"
    if plan.start_year is None and plan.end_year is None:
        return ""
    from tilik_agent.agent.sql import get_connection

    with get_connection() as conn:
        for selection in selections:
            selected = set(selection.period_labels)
            all_years = {
                label
                for (label,) in conn.execute(
                    "SELECT DISTINCT label FROM period WHERE dataset_id=%s AND label ~ '^[0-9]{4}$'",
                    (selection.dataset_id,),
                ).fetchall()
            }
            if plan.start_year and str(plan.start_year) not in selected:
                return f"Selection {selection.selection_id} omitted requested start year {plan.start_year}"
            if plan.end_year and str(plan.end_year) not in selected:
                return f"Selection {selection.selection_id} omitted requested end year {plan.end_year}"
            if plan.start_year == plan.end_year and not plan.through_latest and selected != {str(plan.start_year)}:
                return f"Selection {selection.selection_id} includes periods outside requested year {plan.start_year}"
            if plan.through_latest:
                expected = {year for year in all_years if int(year) >= (plan.start_year or 0)}
                if expected - selected:
                    return f"Selection {selection.selection_id} omitted requested years: {sorted(expected - selected)}"
    return ""


def route_after_sources(state: State) -> Literal["analyze_data", "write_answer"]:
    plan = WorkPlan.model_validate(state["plan"])
    return "analyze_data" if plan.requested_analysis else "write_answer"


def analyze_data(state: State) -> dict[str, Any]:
    try:
        selections = [SeriesSelection.model_validate(item) for item in state.get("series_selections", [])]
        error = _time_error(state, selections)
        if error:
            raise ValueError(error)
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
        prompt = f"""Choose one typed statistical operation for this question.
Return AnalysisRequest. Use only the supplied selection IDs. For dispersion
over time choose dispersion (CV of provinces in each year); for relationship
choose correlation, for a fitted model or outlier choose ols, for whether
initially high provinces changed more choose convergence, and for two survey
rounds choose paired_compare. Do not calculate statistics yourself.
Question: {state["question"]}
Selections: {json.dumps(summaries, ensure_ascii=False)}"""
        request = model_json(AnalysisRequest, prompt)
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
