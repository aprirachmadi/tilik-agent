"""Tests for idempotent observation loads and resume checkpoints."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from tilik_agent.ingestion.client import BpsYearUnavailable
from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
from tilik_agent.ingestion.models import (
    Dataset,
    DimensionMember,
    Geography,
    IngestionCheckpoint,
    Observation,
    ObservationDimension,
)
from tilik_agent.ingestion.pipeline import (
    PARSER_VERSION,
    STATIC_PARSER_VERSION,
    RawStore,
    ingest_datasets,
    parser_version,
)
from tilik_agent.ingestion.store import apply_parse_result


def _draft(value: float) -> ObservationDraft:
    return ObservationDraft(
        measure_source_code="1",
        measure_label="Jumlah Penduduk",
        measure_unit="Orang",
        measure_definition=None,
        measure_decimals=0,
        measure_note=None,
        source_value=str(value),
        numeric_value=value,
        canonical_value=value,
        canonical_unit="person",
        status_code=None,
        period_source_code="125",
        period_label="2025",
        dimensions=(
            DimensionDraft(
                source_code="vervar",
                original_label="Provinsi",
                member_source_code="1100000",
                member_label="Aceh",
                dimension_type="geography",
                canonical_code=None,
            ),
        ),
        source_cell_key="cell-1",
    )


def _dataset(session: Session) -> Dataset:
    dataset = Dataset(
        bps_table_id="table-1",
        tablesource="2",
        domain_id="0000",
        subject_id="519",
        title="Penduduk",
        oldest_period=2025,
        latest_period=2025,
        parser_status="pending",
        parser_version=PARSER_VERSION,
    )
    session.add(dataset)
    session.commit()
    return dataset


def test_apply_parse_result_is_idempotent_and_updates_values(session: Session) -> None:
    """
    Condition:
    Given the same source cell loaded twice, then loaded again with a new number.

    Expected:
    The second load does not add a row, and the third load replaces the stored value.
    """
    dataset = _dataset(session)
    apply_parse_result(session, dataset, ParseResult([_draft(10)]), ingestion_id=None, replace_period_labels={"2025"})
    apply_parse_result(session, dataset, ParseResult([_draft(10)]), ingestion_id=None, replace_period_labels={"2025"})
    session.commit()
    assert session.scalar(select(func.count()).select_from(Observation)) == 1
    apply_parse_result(session, dataset, ParseResult([_draft(12)]), ingestion_id=None, replace_period_labels={"2025"})
    session.commit()
    stored = session.scalars(select(Observation)).one()
    assert stored.value_numeric == 12
    geography = session.scalars(select(Geography)).one()
    assert geography.bps_code == "1100000"
    assert geography.administrative_level == "province"


@pytest.fixture
def statement_counter(session: Session) -> Iterator[list[str]]:
    """Record every SQL statement sent through the session's engine."""
    statements: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    yield statements
    event.remove(engine, "before_cursor_execute", _record)


def _grid_drafts(regions: int, commodities: int, *, offset: float = 0) -> list[ObservationDraft]:
    drafts = []
    for region in range(regions):
        for commodity in range(commodities):
            value = region * commodities + commodity + offset
            drafts.append(
                replace(
                    _draft(value),
                    dimensions=(
                        DimensionDraft(
                            source_code="vervar",
                            original_label="Kabupaten/Kota",
                            member_source_code=f"{1101 + region}",
                            member_label=f"Kabupaten {region}",
                            dimension_type="geography",
                            canonical_code=None,
                        ),
                        DimensionDraft(
                            source_code="turvar",
                            original_label="Jenis Sayuran",
                            member_source_code=str(commodity),
                            member_label=f"Sayuran {commodity}",
                            dimension_type="commodity",
                            canonical_code=None,
                        ),
                    ),
                    source_cell_key=f"{region}-{commodity}",
                )
            )
    return drafts


def test_apply_parse_result_saves_a_large_table_in_few_statements(
    session: Session, statement_counter: list[str]
) -> None:
    """
    Condition:
    Given a kabupaten-by-commodity table of 5,400 cells saved twice with different numbers.

    Expected:
    Every cell and both of its dimension links are stored, the reload replaces the values,
    and the statement count stays small instead of growing with the number of cells.
    """
    dataset = _dataset(session)
    regions, commodities = 200, 27
    cells = regions * commodities
    for offset in (0, 0.5):
        statement_counter.clear()
        written = apply_parse_result(
            session,
            dataset,
            ParseResult(_grid_drafts(regions, commodities, offset=offset)),
            ingestion_id=None,
            replace_period_labels={"2025"},
        )
        session.commit()
        assert written == cells
        assert len(statement_counter) < 40
    assert session.scalar(select(func.count()).select_from(Observation)) == cells
    assert session.scalar(select(func.count()).select_from(ObservationDimension)) == cells * 2
    assert session.scalar(select(func.count()).select_from(DimensionMember)) == regions + commodities
    assert session.scalar(select(func.count()).select_from(Geography)) == regions
    stored = session.scalars(select(Observation).where(Observation.source_cell_key == "3-5")).one()
    assert stored.value_numeric == 3 * commodities + 5 + 0.5
    linked = set(
        session.scalars(
            select(DimensionMember.original_label)
            .join(ObservationDimension, ObservationDimension.member_id == DimensionMember.id)
            .where(ObservationDimension.observation_id == stored.id)
        )
    )
    assert linked == {"Kabupaten 3", "Sayuran 5"}


class _FakeClient:
    """In-memory BPS client that counts detail requests."""

    def __init__(self) -> None:
        self.domain = "0000"
        self.views = 0

    def view_table(self, table_id: str, year: int | None = None) -> dict[str, Any]:
        self.views += 1
        return {
            "status": "OK",
            "data-availability": "available",
            "labelvervar": "Provinsi",
            "var": [{"val": 1, "label": "Jumlah Penduduk", "unit": "Orang", "decimal": 0}],
            "vervar": [{"val": 11, "label": "Aceh"}],
            "turvar": [{"val": 0, "label": "Tidak ada"}],
            "tahun": [{"val": 125, "label": "2025"}],
            "turtahun": [{"val": 0, "label": "Tahun"}],
            "datacontent": {"11101250": 10},
            "available_years": ["2025"],
        }

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


def test_ingest_skips_a_period_that_already_succeeded(session: Session, tmp_path: Path) -> None:
    """
    Condition:
    Given a dynamic dataset whose latest year was already parsed.

    Expected:
    A second run does not call the API again and keeps a single observation.
    """
    _dataset(session)
    client = _FakeClient()
    raw_store = RawStore(tmp_path)
    first = ingest_datasets(session, client, raw_store=raw_store, table_id="table-1")  # type: ignore[arg-type]
    second = ingest_datasets(session, client, raw_store=raw_store, table_id="table-1")  # type: ignore[arg-type]
    assert first.observations == 1
    assert first.parsed == 1
    assert client.views == 1
    assert second.skipped == 1
    assert second.parsed == 0
    assert session.scalar(select(func.count()).select_from(Observation)) == 1


@pytest.mark.parametrize(
    "tablesource,expected",
    [("1", STATIC_PARSER_VERSION), ("2", PARSER_VERSION), ("3", PARSER_VERSION)],
)
def test_parser_version_is_per_backend(tablesource: str, expected: str) -> None:
    """
    Condition:
    Given datasets from each of the three BPS backends.

    Expected:
    Static workbooks carry their own parser version, so bumping it re-parses only static tables.
    """
    assert parser_version(Dataset(tablesource=tablesource)) == expected
    assert STATIC_PARSER_VERSION != PARSER_VERSION


def test_build_url_redacts_the_api_key() -> None:
    """
    Condition:
    Given a BPS path that includes the API key segment.

    Expected:
    Redaction removes the key before the URL could be stored.
    """
    from tilik_agent.ingestion.client import build_url, redact

    url = build_url("view", [("model", "tablestatistic"), ("id", "abc")], "test-key")
    assert "/key/test-key/" in url
    assert "test-key" not in redact(url, "test-key")


class _MixedClient:
    """Client that serves one static detail and one dynamic detail."""

    def __init__(self) -> None:
        self.domain = "0000"

    def view_table(self, table_id: str, year: int | None = None) -> dict[str, Any]:
        if table_id == "bad-xls":
            return {"status": "OK", "data": {"excel": "https://web-api.bps.go.id/download.php?f=x"}}
        return {
            "status": "OK",
            "data-availability": "available",
            "labelvervar": "Provinsi",
            "var": [{"val": 1, "label": "Jumlah Penduduk", "unit": "Orang", "decimal": 0}],
            "vervar": [{"val": 11, "label": "Aceh"}],
            "turvar": [{"val": 0, "label": "Tidak ada"}],
            "tahun": [{"val": 125, "label": "2025"}],
            "turtahun": [{"val": 0, "label": "Tahun"}],
            "datacontent": {"11101250": 10},
            "available_years": ["2025"],
        }

    def download(self, url: str) -> tuple[bytes, str]:
        return b"not-a-workbook", "application/vnd.ms-excel"

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


def test_one_broken_table_does_not_stop_the_run(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Condition:
    Given a static table whose workbook parser raises, followed by a valid dynamic table.

    Expected:
    The broken table is recorded as failed and the dynamic table is still loaded.
    """
    session.add_all(
        [
            Dataset(
                bps_table_id="bad-xls",
                tablesource="1",
                domain_id="0000",
                subject_id="519",
                title="Broken workbook",
                parser_status="pending",
                parser_version=PARSER_VERSION,
            ),
            Dataset(
                bps_table_id="good-dynamic",
                tablesource="2",
                domain_id="0000",
                subject_id="519",
                title="Penduduk",
                oldest_period=2025,
                latest_period=2025,
                parser_status="pending",
                parser_version=PARSER_VERSION,
            ),
        ]
    )
    session.commit()

    def _explode(path: Path, *, fallback_title: str) -> ParseResult:
        raise RuntimeError("workbook unreadable")

    monkeypatch.setattr("tilik_agent.ingestion.pipeline.parse_workbook", _explode)
    report = ingest_datasets(session, _MixedClient(), raw_store=RawStore(tmp_path))  # type: ignore[arg-type]
    assert report.failed == 1
    assert report.parsed == 1
    assert report.observations == 1
    assert any(error.startswith("bad-xls:") for error in report.errors)


class _GappySimdasiClient:
    """SIMDASI client whose catalog span includes a year BPS never published."""

    domain = "0000"

    def __init__(self, missing: int) -> None:
        self.missing = missing
        self.requested: list[int | None] = []

    def view_table(self, table_id: str, year: int | None = None) -> dict[str, Any]:
        self.requested.append(year)
        if year == self.missing:
            raise BpsYearUnavailable("BPS has no view table for this year (HTTP 500, judul_tabel undefined)")
        column = {"nama_variabel": "Jumlah Penduduk", "tipe": "Numerik", "angka_desimal_dibelakang_koma": 0}
        row = {"label": "Aceh", "kode_wilayah": 1100000, "variables": {"col": {"value": "10", "value_raw": 10}}}
        block = {"tahun_data": year, "lingkup_id": "Provinsi", "kolom": {"col": column}, "data": [row]}
        return {"data-availability": "available", "data": [{"page": 1}, block]}

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


@pytest.fixture
def gappy_simdasi(session: Session) -> Dataset:
    """A SIMDASI dataset spanning 2019-2021 in the catalog."""
    dataset = Dataset(
        bps_table_id="simdasi-gap",
        tablesource="3",
        domain_id="0000",
        subject_id="519",
        title="Penduduk",
        oldest_period=2019,
        latest_period=2021,
        parser_status="pending",
    )
    session.add(dataset)
    session.commit()
    return dataset


def test_unpublished_simdasi_year_is_skipped_not_failed(
    session: Session,
    tmp_path: Path,
    gappy_simdasi: Dataset,
) -> None:
    """
    Condition:
    Given a SIMDASI table whose catalog span 2019-2021 includes 2020, which BPS answers with its
    permanent ``judul_tabel`` HTTP 500.

    Expected:
    2019 and 2021 load, 2020 is checkpointed as unavailable, the table is not failed, and a second
    run requests nothing.
    """
    client = _GappySimdasiClient(missing=2020)
    report = ingest_datasets(session, client, raw_store=RawStore(tmp_path))  # type: ignore[arg-type]
    assert (report.parsed, report.unavailable, report.failed) == (2, 1, 0)
    assert gappy_simdasi.parser_status == "parsed"
    statuses = dict(
        session.execute(
            select(IngestionCheckpoint.period_key, IngestionCheckpoint.status).where(
                IngestionCheckpoint.dataset_id == gappy_simdasi.id
            )
        ).all()
    )
    assert statuses == {"2019": "parsed", "2020": "unavailable", "2021": "parsed"}
    client.requested.clear()
    rerun = ingest_datasets(session, client, raw_store=RawStore(tmp_path))  # type: ignore[arg-type]
    assert client.requested == []
    assert rerun.skipped == 3
