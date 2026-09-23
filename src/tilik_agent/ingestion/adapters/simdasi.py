"""Parse a SIMDASI detail response into observations."""

from __future__ import annotations

import re
from typing import Any

from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
from tilik_agent.ingestion.parsing import (
    UNSPECIFIED_UNIT,
    canonical_member,
    classify_dimension_type,
    convert_value,
    duplicate_reason,
    infer_unit,
    is_status_token,
    normalize_label,
    parse_indonesian_number,
)

_AREA_SUFFIXES = {
    "perkotaan": "urban",
    "perdesaan": "rural",
    "perkotaan+perdesaan": "urban_rural_combined",
}
_TAG_RE = re.compile(r"<[^>]+>")


def parse_simdasi(payload: dict[str, Any]) -> ParseResult:
    """Read bilingual SIMDASI detail JSON.

    Each ``kolom`` entry is a measure. Each data row is one dimension member.
    """
    body = _body(payload)
    if body is None:
        if _unavailable(payload):
            return ParseResult()
        return ParseResult(needs_review=True, review_reason="SIMDASI detail had no record body")
    columns = body.get("kolom") if isinstance(body.get("kolom"), dict) else {}
    rows = body.get("data") if isinstance(body.get("data"), list) else []
    if not columns:
        return ParseResult(needs_review=True, review_reason="SIMDASI detail had no kolom measures")
    period_label = str(body.get("tahun_data") or "")
    if not period_label:
        return ParseResult(needs_review=True, review_reason="SIMDASI detail had no tahun_data")
    scope = str(body.get("lingkup_id") or "baris")
    dimension_type = classify_dimension_type(scope)
    observations: list[ObservationDraft] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        observations.extend(
            _row_observations(
                row=row,
                row_index=row_index,
                columns=columns,
                scope=scope,
                dimension_type=dimension_type,
                period_label=period_label,
            )
        )
    notes = _notes(body)
    duplicate = duplicate_reason(observations)
    if duplicate:
        return ParseResult(needs_review=True, review_reason=duplicate, notes=notes)
    return ParseResult(observations=observations, notes=notes)


def _body(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
        return data[1]
    if isinstance(data, dict) and "kolom" in data:
        return data
    return None


def _unavailable(payload: dict[str, Any]) -> bool:
    availability = str(payload.get("data-availability") or "")
    return availability not in {"", "available"}


def _row_observations(
    *,
    row: dict[str, Any],
    row_index: int,
    columns: dict[str, Any],
    scope: str,
    dimension_type: str,
    period_label: str,
) -> list[ObservationDraft]:
    label = str(row.get("label_raw") or row.get("label") or "")
    code = row.get("kode_wilayah")
    member_code = str(code) if code not in (None, "") else normalize_label(label) or f"row-{row_index}"
    member = DimensionDraft(
        source_code="row",
        original_label=scope,
        member_source_code=member_code,
        member_label=label or member_code,
        dimension_type=dimension_type,
        canonical_code=canonical_member(label),
    )
    variables = row.get("variables")
    cells: dict[str, Any]
    if isinstance(variables, dict):
        cells = variables
    else:
        cells = {}
    drafts: list[ObservationDraft] = []
    for column_id, column in columns.items():
        if not isinstance(column, dict):
            continue
        cell = cells.get(column_id) if isinstance(cells.get(column_id), dict) else None
        drafts.append(
            _cell_observation(
                column_id=str(column_id),
                column=column,
                cell=cell,
                member=member,
                period_label=period_label,
                row_index=row_index,
            )
        )
    return drafts


def _cell_observation(
    *,
    column_id: str,
    column: dict[str, Any],
    cell: dict[str, Any] | None,
    member: DimensionDraft,
    period_label: str,
    row_index: int,
) -> ObservationDraft:
    measure_label, area_code, area_label = _split_measure(str(column.get("nama_variabel") or column_id))
    decimals = column.get("angka_desimal_dibelakang_koma")
    decimal_places = int(decimals) if isinstance(decimals, int) else None
    unit = infer_unit(measure_label, _text(column.get("satuan")))
    if unit == UNSPECIFIED_UNIT:
        unit = infer_unit(measure_label, _text(column.get("metadata_satuan")))
    source_text, numeric, status = _interpret_cell(
        cell,
        column_type=str(column.get("tipe") or ""),
        decimals=decimal_places,
    )
    canonical_numeric, canonical_unit = convert_value(numeric, unit, status)
    dimensions = [member]
    if area_code and area_label:
        dimensions.append(
            DimensionDraft(
                source_code="area",
                original_label="Daerah Tempat Tinggal",
                member_source_code=area_code,
                member_label=area_label,
                dimension_type="urban_rural",
                canonical_code=area_code,
            )
        )
    return ObservationDraft(
        measure_source_code=column_id,
        measure_label=measure_label,
        measure_unit=unit,
        measure_definition=_text(column.get("metadata_konsep_definisi")),
        measure_decimals=decimal_places,
        measure_note=_text(column.get("metadata_keterangan_tambahan")),
        source_value=source_text,
        numeric_value=numeric,
        canonical_value=canonical_numeric,
        canonical_unit=canonical_unit,
        status_code=status,
        period_source_code=period_label,
        period_label=period_label,
        dimensions=tuple(dimensions),
        source_cell_key=f"{period_label}:{member.member_source_code}:{column_id}:{row_index}",
    )


def _interpret_cell(
    cell: dict[str, Any] | None,
    *,
    column_type: str,
    decimals: int | None,
) -> tuple[str, float | None, str | None]:
    if cell is None:
        return "", None, "unavailable"
    raw = cell.get("value_raw")
    value = cell.get("value")
    source = raw if raw not in (None, "") else value
    source_text = "" if source is None else str(source).strip()
    if source_text == "" or is_status_token(source_text, letter_flags=True):
        return source_text, None, source_text or "unavailable"
    if column_type.casefold() == "teks":
        return source_text, None, None
    numeric = parse_indonesian_number(source_text, decimal_places=decimals)
    if numeric is None:
        return source_text, None, None
    return source_text, numeric, None


def _split_measure(name: str) -> tuple[str, str | None, str | None]:
    if " - " not in name:
        return name, None, None
    head, tail = name.rsplit(" - ", 1)
    compact = normalize_label(tail).replace(" ", "")
    canonical = _AREA_SUFFIXES.get(compact)
    if canonical is None:
        return name, None, None
    return head.strip(), canonical, tail.strip()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = _TAG_RE.sub("", str(value)).strip()
    return text or None


def _notes(body: dict[str, Any]) -> str | None:
    parts = [str(body.get("catatan") or "").strip(), str(body.get("sumber") or "").strip()]
    text = "\n".join(part for part in parts if part)
    return text or None
