"""Tests for idempotent observation loads and resume checkpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
from tilik_agent.ingestion.models import Dataset, Geography, Observation
from tilik_agent.ingestion.pipeline import PARSER_VERSION, RawStore, ingest_datasets
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
