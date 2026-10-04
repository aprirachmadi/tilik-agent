"""Answer validation, chart construction, and the final answer writer."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.runtime import (
    compact_json,
    document_index,
    evidence_rows,
    evidence_source_details,
    history_block,
    model_json,
    observation_index,
)
from tilik_agent.agent.schemas import Analysis, AnswerDraft, Calculation, ChartSpec, State

_CHART_MARKER = re.compile(r"(?m)^[ \t]*\[\[chart\]\][ \t]*$")


def _clean_chart_markers(text: str) -> str:
    if "[[chart]]" not in text:
        return text
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("[[chart]]", "")).strip()


def answer_blocks(text: str, chart: dict[str, Any] | None) -> tuple[str, list[dict[str, str]] | None]:
    """Remove placement markers and place the validated chart among text blocks."""
    if "[[chart]]" in text:
        text = text.replace("\r\n", "\n")
    marker = _CHART_MARKER.search(text)
    answer = _clean_chart_markers(text)
    if chart is None:
        return answer, None

    if marker:
        before = _clean_chart_markers(text[: marker.start()]).strip()
        after = _clean_chart_markers(text[marker.end() :]).strip()
    else:
        paragraph_end = re.search(r"\r?\n\s*\r?\n", answer)
        before = answer[: paragraph_end.start()].strip() if paragraph_end else answer.strip()
        after = answer[paragraph_end.end() :].strip() if paragraph_end else ""

    blocks = []
    if before:
        blocks.append({"type": "text", "text": before})
    blocks.append({"type": "chart"})
    if after:
        blocks.append({"type": "text", "text": after})
    return answer, blocks


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
Answer: {_clean_chart_markers(state['answer'])}
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
    direct = state.get("source_route") == "direct"
    direct_rule = (
        "Answer from the conversation only. Restate numbers that already appear "
        "in prior assistant messages; do not invent new statistics. If the user "
        "asks for data not yet discussed, say you need a new lookup.\n"
        if direct
        else ""
    )
    return f"""You are Tilik. Answer in the user's language using only the
evidence below. Give the best supported answer, and clearly identify any
requested part that the evidence cannot establish. Never invent missing values.
For numeric claims, name the indicator, period, place, and source table.
For document claims, cite the publication and page or section when available.
Do not show database IDs or the literal "unspecified" in the answer text.
If a unit is missing, explain a likely unit only when the source context
supports it, and make clear that it is inferred.
Do not assert causality from side-by-side tables.
Before calling a value the largest, smallest, or peak, compare it with every
comparable observation for the same period.
Do not volunteer totals, sums, ratios, or shares that the question did not ask
for and that are not in the intermediate calculations.
Never draw charts, bar graphs, or text-art tables; the app renders charts.
For a chart request, write a brief introduction, put [[chart]] alone on a
line, then explain the key finding. If a chart would help an answer that did
not request one, put the marker where it belongs. The marker requests
placement; do not claim a chart exists until one is built.
Write only in the user's language, except for cited names and quotations.
Return an AnswerDraft with evidence IDs in its ID fields, not its text field.
{direct_rule}{history_block(state.get('history'), 'Conversation so far')}Question: {state['question']}
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
        truncated = any(row.get("truncated_batch") for row in state.get("observations", []))
        no_draft = isinstance(exc, ValueError) and str(exc) == "Model returned no AnswerDraft tool call"
        message = (
            "Could not produce a verified answer: at least one observation result was truncated."
            if truncated and no_draft
            else f"Could not produce a verified answer: {exc}"
        )
        return {
            "answer": message,
            "answer_observation_ids": [],
            "answer_document_ids": [],
        }
