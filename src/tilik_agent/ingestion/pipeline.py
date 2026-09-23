"""Catalog sync and resumable loads for the national BPS social statistics tables."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.ingestion.adapters.dynamic import parse_dynamic
from tilik_agent.ingestion.adapters.manual import EXTRACTORS, NO_DATA, parse_manual
from tilik_agent.ingestion.adapters.simdasi import parse_simdasi
from tilik_agent.ingestion.adapters.static import parse_workbook
from tilik_agent.ingestion.client import (
    BpsClient,
    BpsError,
    BpsYearUnavailable,
    dynamic_years,
    redact,
    simdasi_years,
)
from tilik_agent.ingestion.contracts import ParseResult
from tilik_agent.ingestion.models import Dataset, IngestionCheckpoint, IngestionRun, RawObject
from tilik_agent.ingestion.store import DuplicateMappingError, apply_parse_result

PARSER_VERSION = "1"
STATIC_PARSER_VERSION = "4"
LOG = structlog.get_logger(__name__)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._=-]+")


@dataclass
class IngestReport:
    """Counts from one ingest invocation."""

    parsed: int = 0
    needs_review: int = 0
    failed: int = 0
    skipped: int = 0
    unavailable: int = 0
    observations: int = 0
    errors: list[str] = field(default_factory=list)


class RawStore:
    """Write unmodified payloads under the raw directory and remember their checksum."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write_json(self, relative: str, payload: dict[str, Any], secret: str) -> tuple[Path, str, int]:
        """Store redacted JSON and return the path, sha256, and size."""
        body = (json.dumps(redact(payload, secret), ensure_ascii=False) + "\n").encode("utf-8")
        return self.write_bytes(relative, body)

    def write_bytes(self, relative: str, body: bytes) -> tuple[Path, str, int]:
        """Store bytes and return the path, sha256, and size."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path, hashlib.sha256(body).hexdigest(), len(body)


def sync_catalog(session: Session, client: BpsClient, *, subcat: int) -> int:
    """Upsert every national-domain table in the requested CSA category."""
    subjects = client.list_records("subjectcsa", [("subcat", subcat)])
    count = 0
    for subject in subjects:
        subject_id = str(subject.get("sub_id") or subject.get("val") or "")
        tables = client.list_records("tablestatistic", [("subject", subject_id)])
        for table in tables:
            _upsert_dataset(session, table, domain=client.domain, subject_id=subject_id)
            count += 1
        session.commit()
    return count


def ingest_datasets(
    session: Session,
    client: BpsClient,
    *,
    raw_store: RawStore,
    table_id: str | None = None,
) -> IngestReport:
    """Load every catalog dataset, or one table id, resuming finished periods."""
    report = IngestReport()
    run = IngestionRun(scope=table_id or "all", status="running")
    session.add(run)
    session.commit()
    query = select(Dataset).where(Dataset.domain_id == client.domain).order_by(Dataset.id)
    if table_id:
        query = query.where(Dataset.bps_table_id == table_id)
    datasets = list(session.scalars(query))
    if table_id and not datasets:
        raise BpsError(f"Dataset {table_id} is not in the catalog. Run `tilik ingest catalog` first.")
    for dataset in datasets:
        _ingest_dataset(session, client, raw_store, dataset, run, report)
    run.status = "completed" if report.failed == 0 else "completed_with_errors"
    session.commit()
    return report


def _ingest_dataset(
    session: Session,
    client: BpsClient,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
) -> None:
    try:
        if dataset.tablesource == "2":
            _ingest_dynamic(session, client, raw_store, dataset, run, report)
        elif dataset.tablesource == "3":
            _ingest_simdasi(session, client, raw_store, dataset, run, report)
        elif dataset.tablesource == "1":
            _ingest_static(session, client, raw_store, dataset, run, report)
        else:
            _mark(session, dataset, "workbook", "needs_review", None, f"Unknown tablesource {dataset.tablesource}")
            dataset.parser_status = "needs_review"
            report.needs_review += 1
            session.commit()
    except BpsError as exc:
        _fail_dataset(session, dataset, report, str(exc))
    except Exception as exc:
        _fail_dataset(session, dataset, report, f"Unexpected error: {exc}")
    LOG.info(
        "dataset_finished",
        bps_table_id=dataset.bps_table_id,
        tablesource=dataset.tablesource,
        parser_status=dataset.parser_status,
    )


def _ingest_dynamic(
    session: Session,
    client: BpsClient,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
) -> None:
    first_key = str(dataset.latest_period) if dataset.latest_period is not None else "undated"
    if _done(session, dataset, first_key):
        report.skipped += 1
        years = _dynamic_years_from_raw(raw_store, dataset, first_key, dataset.latest_period)
    else:
        payload = client.view_table(dataset.bps_table_id, dataset.latest_period)
        _store_year(session, client, raw_store, dataset, run, report, first_key, payload, parse_dynamic)
        years = dynamic_years(payload, dataset.latest_period)
    for year in years:
        key = str(year)
        if _done(session, dataset, key):
            report.skipped += 1
            continue
        payload = client.view_table(dataset.bps_table_id, year)
        _store_year(session, client, raw_store, dataset, run, report, key, payload, parse_dynamic)


def _ingest_simdasi(
    session: Session,
    client: BpsClient,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
) -> None:
    for year in simdasi_years(dataset.oldest_period, dataset.latest_period):
        key = "undated" if year is None else str(year)
        if _done(session, dataset, key):
            report.skipped += 1
            continue
        try:
            payload = client.view_table(dataset.bps_table_id, year)
        except BpsYearUnavailable as exc:
            _mark(session, dataset, key, "unavailable", None, str(exc))
            report.unavailable += 1
            session.commit()
            continue
        _store_year(session, client, raw_store, dataset, run, report, key, payload, parse_simdasi)


def _ingest_static(
    session: Session,
    client: BpsClient,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
) -> None:
    if _done(session, dataset, "workbook"):
        report.skipped += 1
        return
    if dataset.bps_table_id in NO_DATA:
        _mark(session, dataset, "workbook", "unavailable", None, NO_DATA[dataset.bps_table_id])
        dataset.parser_status = "unavailable"
        dataset.parser_version = parser_version(dataset)
        report.unavailable += 1
        session.commit()
        return
    payload = client.view_table(dataset.bps_table_id, None)
    _remember_raw(session, raw_store, dataset, run, "detail.json", "view", payload, client)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    excel_url = str(data.get("excel") or "") if isinstance(data, dict) else ""
    if not excel_url:
        result = ParseResult(needs_review=True, review_reason="Static detail did not include an Excel URL")
        _finish_parse(session, dataset, run, report, "workbook", None, result, replace_labels=None)
        return
    body, content_type = client.download(excel_url)
    relative = f"{_folder(dataset)}/workbook{_suffix(body)}"
    path, digest, size = raw_store.write_bytes(relative, body)
    _raw_row(
        session,
        dataset,
        run,
        "download",
        {"table": dataset.bps_table_id, "kind": "workbook"},
        path,
        digest,
        size,
        content_type,
        None,
    )
    if dataset.bps_table_id in EXTRACTORS:
        result = parse_manual(dataset.bps_table_id, path)
    else:
        result = parse_workbook(path, fallback_title=dataset.title)
    _finish_parse(session, dataset, run, report, "workbook", digest, result, replace_labels=None)


def _store_year(
    session: Session,
    client: BpsClient,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
    period_key: str,
    payload: dict[str, Any],
    parser: Any,
) -> None:
    _remember_raw(
        session,
        raw_store,
        dataset,
        run,
        f"{period_key}.json",
        "view",
        payload,
        client,
    )
    result = parser(payload)
    labels = {draft.period_label for draft in result.observations}
    if not labels and period_key != "undated":
        labels = {period_key}
    encoded = json.dumps(client.redact_payload(payload), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    _finish_parse(session, dataset, run, report, period_key, digest, result, replace_labels=labels)


def _finish_parse(
    session: Session,
    dataset: Dataset,
    run: IngestionRun,
    report: IngestReport,
    period_key: str,
    digest: str | None,
    result: ParseResult,
    *,
    replace_labels: set[str] | None,
) -> None:
    if result.needs_review:
        dataset.parser_status = "needs_review"
        dataset.parser_version = parser_version(dataset)
        _mark(session, dataset, period_key, "needs_review", digest, result.review_reason)
        report.needs_review += 1
        session.commit()
        return
    written = 0
    try:
        with session.begin_nested():
            written = apply_parse_result(
                session,
                dataset,
                result,
                ingestion_id=run.id,
                replace_period_labels=replace_labels,
            )
    except DuplicateMappingError as exc:
        dataset.parser_status = "needs_review"
        dataset.parser_version = parser_version(dataset)
        _mark(session, dataset, period_key, "needs_review", digest, str(exc))
        report.needs_review += 1
        session.commit()
        return
    dataset.parser_status = "parsed"
    dataset.parser_version = parser_version(dataset)
    _mark(session, dataset, period_key, "parsed", digest, None)
    report.parsed += 1
    report.observations += written
    session.commit()


def _fail_dataset(session: Session, dataset: Dataset, report: IngestReport, message: str) -> None:
    session.rollback()
    report.failed += 1
    report.errors.append(f"{dataset.bps_table_id}: {message}")
    LOG.error("dataset_failed", bps_table_id=dataset.bps_table_id, error=message)
    dataset.parser_status = "failed"
    _mark(session, dataset, "dataset", "failed", None, message[:2000])
    session.commit()


def _remember_raw(
    session: Session,
    raw_store: RawStore,
    dataset: Dataset,
    run: IngestionRun,
    filename: str,
    endpoint: str,
    payload: dict[str, Any],
    client: BpsClient,
) -> None:
    relative = f"{_folder(dataset)}/{filename}"
    path, digest, size = raw_store.write_json(relative, client.redact_payload(payload), "")
    _raw_row(
        session,
        dataset,
        run,
        endpoint,
        {"period": filename, "table": dataset.bps_table_id},
        path,
        digest,
        size,
        "application/json",
        str(payload.get("status") or ""),
    )


def _raw_row(
    session: Session,
    dataset: Dataset,
    run: IngestionRun,
    endpoint: str,
    params: dict[str, str],
    path: Path,
    digest: str,
    size: int,
    content_type: str,
    api_status: str | None,
) -> None:
    from tilik_agent.ingestion.client import params_fingerprint

    fingerprint = params_fingerprint(list(params.items()))
    existing = session.scalar(
        select(RawObject).where(RawObject.endpoint == endpoint, RawObject.params_sha256 == fingerprint)
    )
    if existing is None:
        session.add(
            RawObject(
                ingestion_run_id=run.id,
                dataset_id=dataset.id,
                endpoint=endpoint,
                request_params=params,
                params_sha256=fingerprint,
                content_type=content_type,
                api_status=api_status,
                sha256=digest,
                path=str(path),
                byte_size=size,
            )
        )
        return
    existing.sha256 = digest
    existing.path = str(path)
    existing.byte_size = size
    existing.api_status = api_status
    existing.ingestion_run_id = run.id


def parser_version(dataset: Dataset) -> str:
    """Return the parser version for a dataset's backend. Bumping one backend re-parses only its tables."""
    return STATIC_PARSER_VERSION if dataset.tablesource == "1" else PARSER_VERSION


def _done(session: Session, dataset: Dataset, period_key: str) -> bool:
    checkpoint = session.scalar(
        select(IngestionCheckpoint).where(
            IngestionCheckpoint.dataset_id == dataset.id,
            IngestionCheckpoint.period_key == period_key,
            IngestionCheckpoint.parser_version == parser_version(dataset),
        )
    )
    return checkpoint is not None and checkpoint.status in {"parsed", "needs_review", "unavailable"}


def _mark(
    session: Session,
    dataset: Dataset,
    period_key: str,
    status: str,
    digest: str | None,
    error: str | None,
) -> None:
    checkpoint = session.scalar(
        select(IngestionCheckpoint).where(
            IngestionCheckpoint.dataset_id == dataset.id,
            IngestionCheckpoint.period_key == period_key,
            IngestionCheckpoint.parser_version == parser_version(dataset),
        )
    )
    if checkpoint is None:
        session.add(
            IngestionCheckpoint(
                dataset_id=dataset.id,
                period_key=period_key,
                parser_version=parser_version(dataset),
                content_sha256=digest,
                status=status,
                error_message=error,
            )
        )
        return
    checkpoint.status = status
    checkpoint.content_sha256 = digest
    checkpoint.error_message = error


def _upsert_dataset(session: Session, table: dict[str, Any], *, domain: str, subject_id: str) -> None:
    table_id = str(table.get("id") or "")
    source = str(table.get("tablesource") or "")
    dataset = session.scalar(
        select(Dataset).where(
            Dataset.domain_id == domain,
            Dataset.tablesource == source,
            Dataset.bps_table_id == table_id,
        )
    )
    oldest = _optional_int(table.get("oldest_period"))
    latest = _optional_int(table.get("latest_period"))
    if dataset is None:
        session.add(
            Dataset(
                bps_table_id=table_id,
                tablesource=source,
                domain_id=domain,
                subject_id=str(table.get("id_subject") or subject_id),
                title=str(table.get("title") or table_id),
                oldest_period=oldest,
                latest_period=latest,
                last_update=None if table.get("last_update") is None else str(table.get("last_update")),
                parser_status="pending",
            )
        )
        return
    dataset.title = str(table.get("title") or dataset.title)
    dataset.subject_id = str(table.get("id_subject") or subject_id)
    dataset.oldest_period = oldest
    dataset.latest_period = latest
    dataset.last_update = None if table.get("last_update") is None else str(table.get("last_update"))


def _dynamic_years_from_raw(raw_store: RawStore, dataset: Dataset, period_key: str, requested: int | None) -> list[int]:
    path = raw_store.root / _folder(dataset) / f"{period_key}.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return dynamic_years(payload, requested)


def _folder(dataset: Dataset) -> str:
    return f"source-{dataset.tablesource}/{_SAFE_RE.sub('_', dataset.bps_table_id)[:120]}"


def _suffix(body: bytes) -> str:
    if body.startswith(b"PK\x03\x04"):
        return ".xlsx"
    return ".xls"


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
