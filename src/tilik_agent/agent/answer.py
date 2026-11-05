"""Answer validation, chart construction, and the final answer writer."""

from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

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
    """Legacy check for model-proposed findings and basic calculations.

    The current agent graph does not call this function. Statistical operations
    use ``statistics.compute`` instead; this does not verify final answer prose.
    Keep this only if a structured ordinary-arithmetic step is reintroduced.
    """
    rows = {int(row["observation_id"]): row for row in raw_rows}
    docs = {str(doc["id"]): doc for doc in raw_docs}
    for finding in proposed.findings:
        if not finding.observation_ids and not finding.document_ids:
            raise ValueError("Finding has no source IDs")
        if set(finding.observation_ids) - set(rows) or set(finding.document_ids) - set(docs):
            raise ValueError("Finding cites an unknown source ID")
        if finding.value is not None:
            values = {
                Decimal(str(rows[i]["value"])) for i in finding.observation_ids if rows[i].get("value") is not None
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
    if state.get("analysis_results") and rows:
        return "no"
    if not rows and (state.get("requested_chart") or state.get("analysis_results")):
        cited = set(state.get("answer_external_ids") or [])
        return "yes" if any(
            item["id"] in cited and item.get("data", {}).get("provider") == "BNPB"
            and len(item["data"].get("rows", [])) >= 2
            for item in state.get("external_evidence", [])
        ) else "no"
    if not rows:
        return "no"
    if state.get("requested_chart"):
        return "yes"
    question = state["question"].casefold()
    comparison = any(word in question for word in ("tertinggi", "perubahan", "banding"))
    return "yes" if len(rows) >= 2 and comparison else "no"


def build_chart(state: State) -> dict[str, Any]:
    rows = _answer_observation_rows(state)
    if not rows:
        return _build_bnpb_chart(state)
    indexed = {int(row["observation_id"]): row for row in rows}
    prompt = f"""Build a compact declarative chart from the supported answer.
Return a ChartSpec, not plotting code. Every point's value must match its
observation_id. Prefer bars for category comparisons and lines for trends.
Keep distinct units in distinct series; if units cannot be compared on one
axis, chart only one metric. Question: {state["question"]}
Answer: {_clean_chart_markers(state["answer"])}
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


_BNPB_UNITS = {"events": ("Jumlah kejadian", "kejadian"), "people": ("Jumlah orang terdampak", "jiwa")}


def _build_bnpb_chart(state: State) -> dict[str, Any]:
    cited = set(state.get("answer_external_ids") or [])
    sources = [
        item["data"] for item in state.get("external_evidence", [])
        if item["id"] in cited and (item.get("data") or {}).get("provider") == "BNPB"
    ]
    if not sources:
        return {"chart": None, "chart_error": "No BNPB values available for a chart"}
    if len({data.get("unit") for data in sources}) > 1:
        sources = sources[:1]
    rows = [row for data in sources for row in data.get("rows", []) if row.get("value") is not None]
    if len(rows) < 2 or len(rows) > 60:
        return {"chart": None, "chart_error": "BNPB chart needs 2–60 published values"}
    years = {row["year"] for row in rows}
    axis, unit = _BNPB_UNITS.get(sources[0].get("unit"), ("Nilai", sources[0].get("unit")))
    points = []
    for row in rows:
        name = (row.get("disaster_type") or row.get("geography_name") or "").title()
        points.append({
            "label": str(row["year"]) if len(years) > 1 else name,
            "series": name if len(years) > 1 else axis,
            "value": row["value"], "unit": unit, "observation_id": row["fact_id"],
        })
    series = sorted({point["series"] for point in points})
    title = sources[0].get("title") or "Data bencana BNPB"
    if len(years) > 1:
        title = f"{' dan '.join(series)} nasional, {min(years)}–{max(years)}"
    return {"chart": ChartSpec(
        type="line" if len(years) > 1 else "bar",
        title=title,
        x_axis="Tahun" if len(years) > 1 else "Wilayah atau jenis",
        y_axis=axis,
        data=points,
    ).model_dump()}


def display_chart_spec(chart: dict[str, Any] | None) -> None:
    """Render a verified ChartSpec as an interactive figure in Jupyter."""
    if not chart:
        return
    import pandas as pd
    import plotly.express as px

    spec = ChartSpec.model_validate(chart)
    frame = pd.DataFrame([{"label": point.label, "series": point.series, "value": point.value} for point in spec.data])
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


def _external_prompt_evidence(state: State) -> list[dict[str, Any]]:
    evidence = []
    for item in state.get("external_evidence", []):
        cleaned = {key: value for key, value in item.items() if key != "snapshot_payload"}
        data = cleaned.get("data")
        if data and data.get("provider") == "BMKG" and data.get("retrieved_at"):
            local = datetime.fromisoformat(data["retrieved_at"]).astimezone(ZoneInfo("Asia/Jakarta"))
            cleaned["retrieved_at_jakarta"] = local.isoformat()
        evidence.append(cleaned)
    return evidence


def _number(token: str) -> Decimal:
    # A thousands group never starts with 0, so "0,079" is a decimal, not 79.
    if re.fullmatch(r"[1-9]\d{0,2}(?:[.,]\d{3})+", token):
        return Decimal(token.replace(".", "").replace(",", ""))
    if re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+,\d+", token):
        return Decimal(token.replace(".", "").replace(",", "."))
    if re.fullmatch(r"[1-9]\d{0,2}(?:,\d{3})+\.\d+", token):
        return Decimal(token.replace(",", ""))
    return Decimal(token.replace(",", "."))


def _number_values(token: str) -> list[Decimal]:
    try:
        return [_number(token)]
    except InvalidOperation:
        # Source timestamps can contain several separators, e.g. 03.11.2026.
        return [Decimal(part) for part in re.split(r"[.,]", token) if part]


def _external_number_support(state: State, external_ids: list[str]) -> set[Decimal]:
    allowed = {Decimal(str(row["value"])) for row in state.get("observations", []) if row.get("value") is not None}
    for row in state.get("observations", []):
        if str(row.get("period", "")).isdigit():
            allowed.add(Decimal(str(row["period"])))
    number_fields = {"year", "value", "temperature_c", "humidity_pct", "wind_kmh", "magnitude"}
    text_fields = {
        "datetime", "local_datetime", "analysis_date", "depth", "location", "coordinates",
        "description", "headline", "expires", "effective", "sent", "retrieved_at", "felt", "tsunami_note",
    }

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, child_key)
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif key in number_fields and isinstance(value, int | float) and not isinstance(value, bool):
            allowed.add(Decimal(str(value)))
        elif key in text_fields and isinstance(value, str):
            for token in re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", value):
                allowed.update(_number_values(token))
            for hour, minute, second in re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?", value):
                allowed.update({Decimal(hour), Decimal(minute), Decimal(f"{hour}.{minute}")})
                if second:
                    allowed.add(Decimal(second))

    for item in state.get("external_evidence", []):
        if item["id"] not in external_ids or "data" not in item:
            continue
        data = item["data"]
        if data.get("provider") == "BMKG":
            collect(data)
        elif data.get("provider") == "BNPB":
            allowed.update({Decimal(2010), Decimal(2024)})
            for field in ("source_modified_at", "retrieved_at"):
                if data.get(field):
                    collect({"retrieved_at": data[field]})
            rows = data.get("rows", [])
            allowed.update(Decimal(str(row["year"])) for row in rows)
            allowed.add(Decimal(len(rows)))
            allowed.add(Decimal(len({row["year"] for row in rows})))
            allowed.update(Decimal(str(row["value"])) for row in rows if row.get("value") is not None)
            for calc in data.get("calculations", []):
                allowed.add(Decimal(str(abs(calc["difference"]))))
                if calc.get("percent_change") is not None:
                    allowed.add(Decimal(str(abs(calc["percent_change"]))))
    return allowed


def _analysis_number_support(state: State) -> set[Decimal]:
    """Computed statistics, at the precisions an answer may round them to."""
    allowed: set[Decimal] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
            for scaled in (value, value * 100) if abs(value) <= 1 else (value,):
                for digits in range(4):
                    allowed.add(abs(Decimal(str(round(scaled, digits)))))
                    allowed.add(abs(Decimal(str(math.trunc(scaled * 10**digits) / 10**digits))))

    for item in state.get("analysis_results", []):
        walk(item.get("statistics"))
    return allowed


def _check_bnpb_numbers(text: str, state: State, external_ids: list[str]) -> None:
    if not any(
        item["id"] in external_ids and item.get("data", {}).get("provider") == "BNPB"
        for item in state.get("external_evidence", [])
    ):
        return
    allowed = _external_number_support(state, external_ids) | _analysis_number_support(state)
    for token in re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text):
        if any((value >= 10 or "." in token or "," in token) and value not in allowed
               for value in _number_values(token)):
            raise ValueError(f"Unsupported numeric claim: {token}")


def _check_bmkg_numbers(text: str, state: State, external_ids: list[str]) -> None:
    if not any(
        item["id"] in external_ids and item.get("data", {}).get("provider") == "BMKG"
        for item in state.get("external_evidence", [])
    ):
        return
    allowed = _external_number_support(state, external_ids)
    tokens = re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text)
    tokens.extend(re.findall(r"\bM(?:w)?\s*(\d+(?:[.,]\d+)?)", text, flags=re.IGNORECASE))
    for token in tokens:
        if any((value >= 10 or "." in token or "," in token) and value not in allowed
               for value in _number_values(token)):
            raise ValueError(f"Unsupported numeric claim: {token}")


def _answer_prompt(state: State) -> str:
    direct = state.get("source_route") == "direct"
    direct_rule = (
        "Answer from the conversation only. Restate numbers that already appear "
        "in prior assistant messages; do not invent new statistics. If the user "
        "asks for data not yet discussed, say you need a new lookup.\n"
        if direct
        else ""
    )
    results = [
        {
            key: value
            for key, value in item.items()
            if key not in {"result_id", "selection_ids", "input_observation_ids"}
        }
        for item in state.get("analysis_results", [])
    ]
    selections = [
        {
            key: value
            for key, value in item.items()
            if key not in {"selection_id", "observation_ids", "dataset_id", "measure_id", "period_ids", "member_ids"}
        }
        for item in state.get("series_selections", [])
    ]
    error_keys = ("plan_error", "retrieval_error", "sql_error", "sql_warnings", "analysis_error")
    errors = {key: state.get(key) for key in error_keys}
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
{direct_rule}{history_block(state.get("history"), "Conversation so far")}Question: {state["question"]}
Request intent: {compact_json(state.get("intent", {}))}
Step status: {compact_json(state.get("step_status", {}))}
Observations: {compact_json(evidence_rows(state.get("observations", [])))}
Source details: {compact_json(evidence_source_details(state.get("source_details", {})))}
Documents: {compact_json(state.get("documents", []))}
External source evidence: {compact_json(_external_prompt_evidence(state))}
Validated cross-source province pairs: {compact_json(state.get("cross_source_pairs", []))}
For a BPS-BNPB province comparison, use only these validated pairs. If none
match, explain that the comparison is unavailable. These pairs only cover 2024;
do not infer a trend or causal link from them.
For external claims, cite the provider and relevant date in text, and include
the supporting ext ID in external_ids. A forecast is not observed weather;
a warning is active only until its expiry.
For BMKG retrieval dates, use retrieved_at_jakarta rather than the UTC date.
Intermediate calculations: {compact_json(state.get("intermediate_analysis", {}))}
Verified statistical results: {compact_json(results)}
Statistical selections: {compact_json(selections)}
For a statistical result, state its sample size, source table, survey round,
exclusions and comparability notes.
Use only the computed numbers; do not infer causality from correlation.
Errors: {compact_json(errors)}"""


def write_answer(state: State) -> dict[str, Any]:
    if state.get("analysis_error"):
        return {"answer": state["analysis_error"], "answer_observation_ids": [], "answer_document_ids": []}
    try:
        answer = model_json(AnswerDraft, _answer_prompt(state))
        known_rows = set(observation_index(state))
        known_docs = set(document_index(state))
        observation_ids = answer.observation_ids
        document_ids = answer.document_ids
        external_ids = list(getattr(answer, "external_ids", []))
        known_external = {item["id"] for item in state.get("external_evidence", []) if "data" in item}
        if known_external and not known_rows and not known_docs:
            # External-only answers cannot cite BPS rows or publication passages.
            observation_ids = []
            document_ids = []
            if len(known_external) == 1:
                # The sole fetched source has an unambiguous provenance ID.
                external_ids = list(known_external)
            elif not external_ids and state.get("analysis_results"):
                # A statistic over external series is backed by exactly the series it was computed from.
                external_ids = sorted(
                    {item_id for item in state["analysis_results"] for item_id in item["selection_ids"]}
                    & known_external
                )
        if (set(observation_ids) - known_rows or set(document_ids) - known_docs
                or set(external_ids) - known_external):
            raise ValueError("Answer cites unknown evidence IDs")
        if known_external and not external_ids:
            raise ValueError("Answer omitted external evidence IDs")
        _check_bnpb_numbers(answer.text, state, external_ids)
        _check_bmkg_numbers(answer.text, state, external_ids)
        output = {
            "answer": answer.text,
            "answer_observation_ids": observation_ids,
            "answer_document_ids": document_ids,
        }
        if external_ids:
            output["answer_external_ids"] = external_ids
        if state.get("analysis_results"):
            output["answer_analysis_selection_ids"] = sorted(
                {selection_id for item in state["analysis_results"] for selection_id in item["selection_ids"]}
            )
        return output
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
