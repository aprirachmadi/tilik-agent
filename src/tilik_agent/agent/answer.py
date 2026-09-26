"""Answer validation, chart construction, and the final answer writer."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.runtime import (
    compact_json,
    document_index,
    evidence_rows,
    evidence_source_details,
    model_json,
    observation_index,
)
from tilik_agent.agent.schemas import Analysis, AnswerDraft, Calculation, ChartSpec, State


def _answer_observation_rows(state: State) -> list[dict[str, Any]]:
    selected = set(state.get("answer_observation_ids") or [])
    return [
        row
        for row in state.get("observations", [])
        if row.get("observation_id") in selected and row.get("value") is not None
    ]


def _check_calculation(calc: Calculation, rows: dict[int, dict[str, Any]]) -> None:
    values = [Decimal(str(rows[i]["value"])) for i in calc.input_observation_ids]
    if any(not value.is_finite() for value in values):
        raise ValueError("Non-finite calculation input")
    if calc.operation == "difference" and len(values) == 2:
        expected = values[0] - values[1]
    elif calc.operation == "sum":
        expected = sum(values)
    elif calc.operation == "mean":
        expected = sum(values) / len(values)
    elif calc.operation == "ratio" and len(values) == 2 and values[1] != 0:
        expected = values[0] / values[1]
    elif calc.operation == "percent_change" and len(values) == 2 and values[1] != 0:
        expected = (values[0] - values[1]) / values[1] * 100
    else:
        raise ValueError("Invalid calculation inputs")
    if abs(expected - Decimal(str(calc.result))) > Decimal("0.00001"):
        raise ValueError("Calculation result disagrees with observation values")


def validate_analysis(
    proposed: Analysis,
    raw_rows: list[dict[str, Any]],
    raw_docs: list[dict[str, Any]],
) -> Analysis:
    rows = {int(row["observation_id"]): row for row in raw_rows}
    docs = {str(doc["id"]): doc for doc in raw_docs}
    for finding in proposed.findings:
        if not finding.observation_ids and not finding.document_ids:
            raise ValueError("Finding has no source IDs")
        if set(finding.observation_ids) - set(rows) or set(finding.document_ids) - set(docs):
            raise ValueError("Finding cites an unknown source ID")
        if finding.value is not None:
            values = {
                Decimal(str(rows[i]["value"]))
                for i in finding.observation_ids
                if rows[i].get("value") is not None
            }
            if Decimal(str(finding.value)) not in values:
                raise ValueError("Finding value disagrees with cited observations")
    for calc in proposed.calculations:
        if set(calc.input_observation_ids) - set(rows):
            raise ValueError("Calculation cites an unknown observation ID")
        if any(rows[i].get("value") is None for i in calc.input_observation_ids):
            raise ValueError("Calculation uses a missing value")
        _check_calculation(calc, rows)
    return proposed


def chart_needed(state: State) -> Literal["yes", "no"]:
    rows = _answer_observation_rows(state)
    if not rows:
        return "no"
    if state.get("requested_chart"):
        return "yes"
    question = state["question"].casefold()
    comparison = any(word in question for word in ("tertinggi", "perubahan", "banding"))
    return "yes" if len(rows) >= 2 and comparison else "no"


def build_chart(state: State) -> dict[str, Any]:
    rows = _answer_observation_rows(state)
    indexed = {int(row["observation_id"]): row for row in rows}
    prompt = f"""Build a compact declarative chart from the supported answer.
Return a ChartSpec, not plotting code. Every point's value must match its
observation_id. Prefer bars for category comparisons and lines for trends.
Keep distinct units in distinct series; if units cannot be compared on one
axis, chart only one metric. Question: {state['question']}
Answer: {state['answer']}
Rows: {compact_json(evidence_rows(rows))}"""
    try:
        chart = model_json(ChartSpec, prompt)
        for point in chart.data:
            row = indexed.get(point.observation_id)
            if row is None or Decimal(str(point.value)) != Decimal(str(row["value"])):
                raise ValueError("Chart point does not match a cited observation")
        spec = chart.model_dump()
        return {"chart": spec}
    except (ValidationError, ValueError, APIError) as exc:
        return {"chart": None, "chart_error": f"Chart unavailable: {exc}"}


def display_chart_spec(chart: dict[str, Any] | None) -> None:
    """Render a verified ChartSpec as an interactive figure in Jupyter."""
    if not chart:
        return
    import pandas as pd
    import plotly.express as px

    spec = ChartSpec.model_validate(chart)
    frame = pd.DataFrame(
        [{"label": point.label, "series": point.series, "value": point.value} for point in spec.data]
    )
    color = "series" if frame["series"].nunique() > 1 else None
    if spec.type == "bar":
        figure = px.bar(
            frame,
            x="label",
            y="value",
            color=color,
            title=spec.title,
            barmode="group",
        )
    else:
        figure = px.line(
            frame,
            x="label",
            y="value",
            color=color,
            title=spec.title,
            markers=True,
        )
    figure.update_layout(xaxis_title=spec.x_axis, yaxis_title=spec.y_axis)
    figure.show()


def _answer_prompt(state: State) -> str:
    return f"""You are Tilik. Answer in the user's language using only the
evidence below. Give the best supported answer, and clearly identify any
requested part that the evidence cannot establish. Never invent missing values.
For numeric claims, name the indicator, period, place, and source table.
For document claims, cite the publication and page or section when available.
Do not show database IDs or the literal "unspecified" in the answer text.
If a unit is missing, explain a likely unit only when the source context
supports it, and make clear that it is inferred.
Do not assert causality from side-by-side tables.
Return an AnswerDraft with evidence IDs in its ID fields, not its text field.
Question: {state['question']}
Plan: {compact_json(state.get('plan', {}))}
Step status: {compact_json(state.get('step_status', {}))}
Observations: {compact_json(evidence_rows(state.get('observations', [])))}
Source details: {compact_json(evidence_source_details(state.get('source_details', {})))}
Documents: {compact_json(state.get('documents', []))}
Intermediate calculations: {compact_json(state.get('intermediate_analysis', {}))}
Errors: {compact_json({k: state.get(k) for k in ('plan_error', 'retrieval_error', 'sql_error', 'sql_warnings')})}"""


def write_answer(state: State) -> dict[str, Any]:
    try:
        answer = model_json(AnswerDraft, _answer_prompt(state))
        known_rows = set(observation_index(state))
        known_docs = set(document_index(state))
        if set(answer.observation_ids) - known_rows or set(answer.document_ids) - known_docs:
            raise ValueError("Answer cites unknown evidence IDs")
        return {
            "answer": answer.text,
            "answer_observation_ids": answer.observation_ids,
            "answer_document_ids": answer.document_ids,
        }
    except (ValidationError, ValueError, APIError) as exc:
        return {
            "answer": f"Could not produce a verified answer: {exc}",
            "answer_observation_ids": [],
            "answer_document_ids": [],
        }
