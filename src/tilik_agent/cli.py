"""Command line entry points for the Tilik agent."""

from __future__ import annotations

import sys
from pathlib import Path

import structlog
import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.config import get_settings
from tilik_agent.ingestion.client import BpsClient, BpsError
from tilik_agent.ingestion.pipeline import IngestReport, RawStore, ingest_datasets, sync_catalog

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False, help="Tilik commands")
ingest_app = typer.Typer(no_args_is_help=True, help="Load BPS statistical tables into Postgres")
app.add_typer(ingest_app, name="ingest")

_PDF = typer.Option(..., "--pdf", help="PDF to chunk into a pgvector collection")
_COLLECTION = typer.Option(..., "--collection", help="Target pgvector collection to replace")

def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )


def _session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    return Session(engine)


@ingest_app.command("catalog")
def ingest_catalog() -> None:
    """Download the national BPS subject catalog into the dataset table."""
    _configure_logging()
    settings = get_settings()
    with BpsClient(settings.bps_api_key, domain=settings.bps_domain) as client, _session() as session:
        count = sync_catalog(session, client, subcat=settings.bps_subcat)
    typer.echo(f"Catalog datasets: {count}")


@ingest_app.command("run")
def ingest_run() -> None:
    """Load every catalogued national table. A stopped run resumes finished periods."""
    report = _load(table_id=None)
    _echo_report(report)
    if report.failed:
        raise typer.Exit(code=1)


@ingest_app.command("table")
def ingest_table(table_id: str = typer.Option(..., "--id", help="BPS table id from the catalog")) -> None:
    """Load one catalogued table, including every published year."""
    try:
        report = _load(table_id=table_id)
    except BpsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo_report(report)
    if report.failed:
        raise typer.Exit(code=1)


@ingest_app.command("pdf")
def ingest_pdf_command(
    pdf: Path = _PDF,
    collection: str = _COLLECTION,
) -> None:
    """Chunk a PDF into a pgvector collection."""
    if not pdf.is_file():
        typer.echo(f"PDF not found: {pdf}", err=True)
        raise typer.Exit(code=1)
    if not collection.strip():
        raise typer.BadParameter("Collection name cannot be empty", param_hint="--collection")
    from tilik_agent.ingestion.pdf_vectors import ingest_pdf

    try:
        count = ingest_pdf(pdf, collection=collection)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"PDF chunks in {collection}: {count}")


@ingest_app.command("bnpb")
def ingest_bnpb() -> None:
    """Synchronize four reviewed BNPB disaster tables through the public CKAN API."""
    from tilik_agent.connectors.bnpb.ingest import sync_bnpb

    with _session() as session:
        counts = sync_bnpb(session)
    for kind, count in counts.items():
        typer.echo(f"{kind}: {count} new facts")


def _load(*, table_id: str | None) -> IngestReport:
    _configure_logging()
    settings = get_settings()
    raw_store = RawStore(settings.bps_raw_dir)
    with BpsClient(settings.bps_api_key, domain=settings.bps_domain) as client, _session() as session:
        return ingest_datasets(session, client, raw_store=raw_store, table_id=table_id)


@app.command("benchmark")
def benchmark(
    conversations: bool = typer.Option(False, "--conversations", help="Score scripted multi-turn conversations"),
    n_samples: int | None = typer.Option(None, "--n-samples", min=1, help="Run this many dataset items"),
    seed: int = typer.Option(42, "--seed", help="Reproducible sample seed"),
) -> None:
    """Score the question file or scripted conversations with the enabled judges."""
    from tilik_agent.evals.runner import run_benchmark, run_conversation_benchmark

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    typer.echo(
        run_conversation_benchmark(n_samples=n_samples, seed=seed)
        if conversations else run_benchmark(n_samples=n_samples, seed=seed)
    )


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(4321, help="Bind port"),
) -> None:
    """Serve the question API."""
    import uvicorn

    uvicorn.run("tilik_agent.api:app", host=host, port=port)


def _echo_report(report: IngestReport) -> None:
    typer.echo(
        f"parsed={report.parsed} needs_review={report.needs_review} "
        f"failed={report.failed} skipped={report.skipped} unavailable={report.unavailable} "
        f"observations={report.observations}"
    )
    for error in report.errors:
        typer.echo(error, err=True)
