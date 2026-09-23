"""Parse a dynamic BPS ``datacontent`` payload into observations."""

from __future__ import annotations

import itertools
from typing import Any

from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
from tilik_agent.ingestion.parsing import (
    canonical_member,
    classify_dimension_type,
    convert_value,
    duplicate_reason,
    format_excel_number,
    infer_unit,
    normalize_label,
)


def _items(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, list) and values:
        return [item for item in values if isinstance(item, dict)]
    return [{"val": 0, "label": ""}]


def _skip_member(label: str, source_code: str) -> bool:
    folded = normalize_label(label)
    return str(source_code) in {"0", ""} and folded in {"", "tahun", "tidak ada"}


def parse_dynamic(payload: dict[str, Any]) -> ParseResult:
    """Join ``datacontent`` keys through the dictionaries returned with them.

    A key that matches no combination is left unparsed and the dataset needs review.
    A missing key is an unpublished combination, not zero.
    """
    variables = _items(payload.get("var"))
    geographies = _items(payload.get("vervar"))
    characteristics = _items(payload.get("turvar"))
    periods = _items(payload.get("tahun"))
    subperiods = _items(payload.get("turtahun"))
    cells = payload.get("datacontent") or {}
    if not isinstance(cells, dict):
        return ParseResult(needs_review=True, review_reason="datacontent was not an object")
    if not cells:
        return ParseResult()

    dimension_label = str(payload.get("labelvervar") or "vervar")
    observations: list[ObservationDraft] = []
    matched: set[str] = set()
    for geography, variable, characteristic, period, subperiod in itertools.product(
        geographies, variables, characteristics, periods, subperiods
    ):
        key = "".join(str(item.get("val", 0)) for item in (geography, variable, characteristic, period, subperiod))
        if key not in cells:
            continue
        matched.add(key)
        raw_value = cells[key]
        status = None
        numeric: float | None
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool):
            numeric = float(raw_value)
            source_text = format_excel_number(numeric) if isinstance(raw_value, float) else str(raw_value)
        else:
            source_text = "" if raw_value is None else str(raw_value)
            numeric = None
            status = "missing" if source_text == "" else None
        unit = infer_unit(str(variable.get("label") or ""), str(variable.get("unit") or "") or None)
        canonical_numeric, canonical_unit = convert_value(numeric, unit, status)
        dimensions = _dimensions(dimension_label, geography, characteristic, subperiod)
        decimals = variable.get("decimal")
        observations.append(
            ObservationDraft(
                measure_source_code=str(variable.get("val")),
                measure_label=str(variable.get("label") or variable.get("val")),
                measure_unit=unit,
                measure_definition=_clean(variable.get("def")),
                measure_decimals=int(decimals) if isinstance(decimals, int) else None,
                measure_note=_clean(variable.get("note")),
                source_value=source_text,
                numeric_value=numeric,
                canonical_value=canonical_numeric,
                canonical_unit=canonical_unit,
                status_code=status,
                period_source_code=str(period.get("val")),
                period_label=str(period.get("label") or period.get("val")),
                dimensions=tuple(dimensions),
                source_cell_key=key,
            )
        )
    unmatched = set(map(str, cells)) - matched
    if unmatched:
        return ParseResult(needs_review=True, review_reason=f"Could not join {len(unmatched)} datacontent cells")
    duplicate = duplicate_reason(observations)
    if duplicate:
        return ParseResult(needs_review=True, review_reason=duplicate)
    return ParseResult(observations=observations)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dimensions(
    geography_label: str,
    geography: dict[str, Any],
    characteristic: dict[str, Any],
    subperiod: dict[str, Any],
) -> list[DimensionDraft]:
    drafts: list[DimensionDraft] = []
    if not _skip_member(str(geography.get("label") or ""), str(geography.get("val"))):
        drafts.append(_dimension("vervar", geography_label, geography))
    if not _skip_member(str(characteristic.get("label") or ""), str(characteristic.get("val"))):
        drafts.append(_dimension("turvar", "Karakteristik", characteristic))
    if not _skip_member(str(subperiod.get("label") or ""), str(subperiod.get("val"))):
        drafts.append(_dimension("turtahun", "Turunan Tahun", subperiod))
    return drafts


def _dimension(source_code: str, label: str, member: dict[str, Any]) -> DimensionDraft:
    member_label = str(member.get("label") or member.get("val"))
    return DimensionDraft(
        source_code=source_code,
        original_label=label,
        member_source_code=str(member.get("val")),
        member_label=member_label,
        dimension_type=classify_dimension_type(label),
        canonical_code=canonical_member(member_label),
    )

