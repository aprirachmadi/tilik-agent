"""Tests for the extractors of static workbooks the generic parser cannot read."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.ingestion.adapters.manual import (
    _SP2020_PROVINCES,
    EXTRACTORS,
    NO_DATA,
    _dot_thousands,
    _household_blocks,
    parse_manual,
)
from tilik_agent.ingestion.adapters.static import Cell
from tilik_agent.ingestion.contracts import ObservationDraft
from tilik_agent.ingestion.models import Dataset, IngestionCheckpoint
from tilik_agent.ingestion.pipeline import RawStore, ingest_datasets

WorkbookWriter = Callable[[list[list[Any]], str], Path]


@pytest.fixture
def write_workbook(tmp_path: Path) -> WorkbookWriter:
    """Write rows to a one-sheet ``.xlsx`` and return its path."""

    def write(rows: list[list[Any]], sheet: str) -> Path:
        book = openpyxl.Workbook()
        worksheet = book.active
        worksheet.title = sheet
        for row in rows:
            worksheet.append(row)
        path = tmp_path / f"{sheet}.xlsx"
        book.save(path)
        return path

    return write


def _members(draft: ObservationDraft) -> dict[str, str]:
    return {dimension.original_label: dimension.member_label for dimension in draft.dimensions}


@pytest.fixture
def sp2020_rows() -> list[list[Any]]:
    """A CMR sheet with one regency per province, the Bengkulu typo, and the Gorontalo regency twin."""
    rows: list[list[Any]] = [
        [None, "Angka Kematian Anak (CMR) Hasil Long Form SP2020 Menurut Provinsi/Kabupaten/Kota, 2020"],
        [],
        [None, "Provinsi/Kabupaten/Kota", "CMR"],
        [None, None, "LF SP2020"],
    ]
    for index, province in enumerate(_SP2020_PROVINCES):
        rows.append([None, "BENKULU" if province == "BENGKULU" else province, 3.0 + index])
        rows.append([None, "GORONTALO" if province == "GORONTALO" else f"KAB {index}", 4.0 + index])
    rows.append([None, "INDONESIA", 2.98])
    return rows


def test_sp2020_rows_are_split_into_provinces_and_regencies(
    write_workbook: WorkbookWriter, sp2020_rows: list[list[Any]]
) -> None:
    """
    Condition:
    Given an SP2020 sheet listing each province followed by its regencies, where the regency GORONTALO
    follows the province GORONTALO and the Bengkulu province row is spelled BENKULU.

    Expected:
    Province rows carry only a province, regency rows carry their province too, the Gorontalo regency is
    not mistaken for a province, BENKULU is stored as BENGKULU, and INDONESIA is the national row.
    """
    result = parse_manual("MjIyMSMx", write_workbook(sp2020_rows, "CMR_Kabkot_id"))
    assert not result.needs_review
    members = [_members(draft) for draft in result.observations]
    assert len(members) == 2 * len(_SP2020_PROVINCES) + 1
    assert {"Provinsi": "GORONTALO", "Kabupaten/Kota": "GORONTALO"} in members
    assert {"Provinsi": "BENGKULU"} in members
    assert members[-1] == {"Provinsi": "INDONESIA"}
    assert {draft.period_label for draft in result.observations} == {"LF SP2020"}


def test_sp2020_missing_province_goes_to_review(write_workbook: WorkbookWriter, sp2020_rows: list[list[Any]]) -> None:
    """
    Condition:
    Given an SP2020 sheet whose PAPUA province row is missing.

    Expected:
    The extractor reports the layout change and stores no values.
    """
    rows = [row for row in sp2020_rows if row[1:2] != ["PAPUA"]]
    result = parse_manual("MjIyMSMx", write_workbook(rows, "CMR_Kabkot_id"))
    assert result.needs_review
    assert "PAPUA" in (result.review_reason or "")
    assert result.observations == []


@pytest.mark.parametrize(
    "cell,scale_integers,expected",
    [
        (Cell("1.333", 1.333), False, 1333),
        (Cell("454.82", 454.82), True, 454820),
        (Cell("473", 473.0), True, 473000),
        (Cell("908", 908.0), False, 908),
        (Cell("908", None), False, 908),
        (Cell("1.121.190", None), True, 1121190),
        (Cell("19370.000046296296", None), True, None),
    ],
)
def test_dot_thousands(cell: Cell, scale_integers: bool, expected: float | None) -> None:
    """
    Condition:
    Given counts typed with dots between thousands, some of which Excel stored as decimals.

    Expected:
    Each cell reads as the count the typist meant, and text that is not such a count reads as None.
    """
    assert _dot_thousands(cell, scale_integers=scale_integers) == expected


def test_household_blocks_ignore_dragged_year_labels(write_workbook: WorkbookWriter) -> None:
    """
    Condition:
    Given year blocks ending in ``Jumlah`` whose later year cells repeat an earlier year, and footnote
    numbers after a province and a category.

    Expected:
    Each block takes the year in its first cell, and the footnote numbers are dropped from the labels.
    """
    rows = [
        ["Persentase Rumah Tangga"],
        [],
        ["Provinsi", 2000, None, None, 2001, 2000, 2000],
        [None, "Leding 1", "Lainnya", "Jumlah", "Leding 1", "Lainnya", "Jumlah"],
        ["DI Yogyakarta 2", "10,5", 89.5, 100, 11, 89, 100],
        ["Catatan:"],
    ]
    extract = _household_blocks("Sumber Air Minum", 2000, 2001)
    drafts, _notes = extract(write_workbook(rows, "Sheet1"))
    assert [(draft.period_label, _members(draft)["Sumber Air Minum"]) for draft in drafts[:4]] == [
        ("2000", "Leding"),
        ("2000", "Lainnya"),
        ("2000", "Jumlah"),
        ("2001", "Leding"),
    ]
    assert {_members(draft)["Provinsi"] for draft in drafts} == {"DI Yogyakarta"}
    assert drafts[0].numeric_value == 10.5


def test_extractor_and_no_data_tables_do_not_overlap() -> None:
    """
    Condition:
    Given the registry of hand-written extractors and the list of link-only tables.

    Expected:
    No table is in both, so each table has exactly one way to load.
    """
    assert not set(EXTRACTORS) & set(NO_DATA)


class _NoCallClient:
    """Client that fails the test if the pipeline asks BPS for anything."""

    domain = "0000"

    def view_table(self, table_id: str, year: int | None = None) -> dict[str, Any]:
        raise AssertionError(f"unexpected request for {table_id}")

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


def test_link_only_table_is_marked_unavailable_without_a_download(session: Session, tmp_path: Path) -> None:
    """
    Condition:
    Given a static table whose workbook only links to an outside publication.

    Expected:
    The run records it as unavailable with the reason, calls BPS for nothing, and skips it next time.
    """
    table_id = next(iter(NO_DATA))
    dataset = Dataset(
        bps_table_id=table_id,
        tablesource="1",
        domain_id="0000",
        subject_id="519",
        title="Laporan",
        parser_status="pending",
    )
    session.add(dataset)
    session.commit()
    report = ingest_datasets(session, _NoCallClient(), raw_store=RawStore(tmp_path))  # type: ignore[arg-type]
    assert (report.unavailable, report.failed) == (1, 0)
    assert dataset.parser_status == "unavailable"
    checkpoint = session.scalars(select(IngestionCheckpoint)).one()
    assert checkpoint.error_message == NO_DATA[table_id]
    rerun = ingest_datasets(session, _NoCallClient(), raw_store=RawStore(tmp_path))  # type: ignore[arg-type]
    assert rerun.skipped == 1
