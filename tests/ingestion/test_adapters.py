"""Tests for the three BPS table adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilik_agent.ingestion.adapters.dynamic import parse_dynamic
from tilik_agent.ingestion.adapters.simdasi import parse_simdasi
from tilik_agent.ingestion.adapters.static import parse_workbook

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DETAILS = ROOT / "data" / "bps" / "recon-2026-09-11" / "sample-details.json"
STATIC_DIR = ROOT / "data" / "bps" / "probes" / "static"


@pytest.fixture(scope="module")
def sample_details() -> dict[str, object]:
    """Load the sanitized recon detail payloads once."""
    loaded = json.loads(SAMPLE_DETAILS.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_dynamic_adapter_joins_sparse_cells(sample_details: dict[str, object]) -> None:
    """
    Condition:
    Given the dynamic DPR sample whose datacontent keys are concatenations of dictionary ids.

    Expected:
    Each matched cell becomes an observation, sex is a dimension, and Orang converts to persons.
    """
    detail = sample_details["tablesource_2"]
    assert isinstance(detail, dict)
    result = parse_dynamic(detail["detail_response"])
    assert result.needs_review is False
    assert len(result.observations) == 3
    by_member = {draft.dimensions[0].member_label: draft for draft in result.observations}
    assert by_member["Laki-laki"].numeric_value == 451
    assert by_member["Laki-laki"].dimensions[0].canonical_code == "male"
    assert by_member["Laki-laki"].canonical_value == 451
    assert by_member["Laki-laki"].canonical_unit == "person"
    assert by_member["Laki-laki + Perempuan"].dimensions[0].canonical_code == "total"


def test_simdasi_adapter_parses_thousands_and_missing_rows(sample_details: dict[str, object]) -> None:
    """
    Condition:
    Given the SIMDASI hajj table, including rows whose variables are an empty list.

    Expected:
    4.400 becomes 4400, geography keeps the MFD code, and an empty row is unavailable rather than zero.
    """
    detail = sample_details["tablesource_3"]
    assert isinstance(detail, dict)
    result = parse_simdasi(detail["detail_response"])
    assert result.needs_review is False
    aceh = next(draft for draft in result.observations if draft.dimensions[0].member_label == "Aceh")
    assert aceh.numeric_value == 4400
    assert aceh.dimensions[0].member_source_code == "1100000"
    assert aceh.dimensions[0].dimension_type == "geography"
    missing = [draft for draft in result.observations if draft.status_code == "unavailable"]
    assert missing
    assert all(draft.numeric_value is None for draft in missing)


def test_simdasi_en_dash_is_status() -> None:
    """
    Condition:
    Given a numeric column whose value_raw is null and whose value is an en dash.

    Expected:
    The cell is stored as a status flag with no numeric value.
    """
    payload = {
        "data-availability": "available",
        "data": [
            {"page": 1},
            {
                "tahun_data": 2010,
                "lingkup_id": "Provinsi",
                "kolom": {
                    "col": {
                        "nama_variabel": "Jumlah Penduduk",
                        "tipe": "Numerik",
                        "angka_desimal_dibelakang_koma": 1,
                    }
                },
                "data": [
                    {
                        "label": "Kalimantan Utara",
                        "kode_wilayah": 6500000,
                        "variables": {"col": {"value": "\u2013", "value_raw": None}},
                    }
                ],
            },
        ],
    }
    result = parse_simdasi(payload)
    assert result.observations[0].status_code == "\u2013"
    assert result.observations[0].numeric_value is None


def test_static_housing_year_columns() -> None:
    """
    Condition:
    Given the housing-indicator workbook whose columns are years and whose rows are indicators.

    Expected:
    1993 contains 90.33, n.a is a status, and the English sheet is ignored.
    """
    result = parse_workbook(STATIC_DIR / "static-MTUzNyMx.xls", fallback_title="housing")
    assert result.needs_review is False
    assert any(
        draft.period_label == "1993" and draft.numeric_value is not None and abs(draft.numeric_value - 90.33) < 0.001
        for draft in result.observations
    )
    assert any(draft.status_code == "n.a" and draft.numeric_value is None for draft in result.observations)
    assert all(not draft.measure_label.casefold().startswith("sumber") for draft in result.observations)


def test_static_literacy_stacked_headers() -> None:
    """
    Condition:
    Given the literacy workbook with area, sex, and year header rows.

    Expected:
    Aceh in 2009 for urban males is 99.34 and carries those dimension roles.
    """
    result = parse_workbook(STATIC_DIR / "static-MTYwOSMx.xls", fallback_title="literacy")
    assert result.needs_review is False
    match = [
        draft
        for draft in result.observations
        if draft.period_label == "2009"
        and any(item.member_label == "Aceh" for item in draft.dimensions)
        and any(item.canonical_code == "male" for item in draft.dimensions)
        and any(item.canonical_code == "urban" for item in draft.dimensions)
        and draft.numeric_value is not None
        and abs(draft.numeric_value - 99.34) < 0.02
    ]
    assert match


def test_static_politics_sections() -> None:
    """
    Condition:
    Given the legislative-vote workbook split into one block per election year.

    Expected:
    The 2024 PDIP vote total is kept, and percentage columns convert to a ratio.
    """
    result = parse_workbook(STATIC_DIR / "static-MTU3MyMx.xls", fallback_title="votes")
    assert result.needs_review is False
    votes = [
        draft
        for draft in result.observations
        if draft.period_label == "2024"
        and draft.numeric_value is not None
        and abs(draft.numeric_value - 25387279) < 1
        and any("Demokrasi Indonesia Perjuangan" in item.member_label for item in draft.dimensions)
    ]
    assert votes
    percent = next(
        draft
        for draft in result.observations
        if draft.canonical_unit == "ratio" and draft.period_label == "2024"
    )
    assert percent.canonical_value is not None


def test_static_asfr_without_a_year_axis_needs_review() -> None:
    """
    Condition:
    Given the age-specific fertility workbook whose columns are age bands.

    Expected:
    The workbook is kept for review and no observations are guessed from SP2020.
    """
    result = parse_workbook(STATIC_DIR / "static-MjIxNSMx.xls", fallback_title="asfr")
    assert result.needs_review is True
    assert result.observations == []


def test_static_xls_skips_defined_names_xlrd_cannot_parse() -> None:
    """
    Condition:
    Given a BPS .xls whose defined-name formulas contain tokens xlrd rejects.

    Expected:
    The Indonesian sheet is still parsed, including the 2025 unmarried 10–14 count.
    """
    fixture = Path(__file__).parent / "fixtures" / "workbook-defined-name.xls"
    result = parse_workbook(fixture, fallback_title="marital status")
    assert result.needs_review is False
    match = [
        draft
        for draft in result.observations
        if draft.period_label == "2025"
        and draft.numeric_value == 22011958
        and "Belum Kawin" in draft.measure_label
        and any(item.member_label == "10\u201314" for item in draft.dimensions)
    ]
    assert match
