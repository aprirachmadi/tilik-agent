"""Typed reads over synchronized Bapanas monthly prices."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tilik_agent.connectors.bapanas.client import PAGE_URL
from tilik_agent.connectors.bapanas.models import BapanasPrice, BapanasSnapshot
from tilik_agent.connectors.provinces import same_province
from tilik_agent.connectors.series import data_time, month_range, summarize


def match_commodity(wanted: str, names: list[str]) -> list[str]:
    """Exact name first; otherwise every name containing the words (e.g. "cabai")."""
    key = wanted.strip().casefold()
    exact = [name for name in names if name.casefold() == key]
    return exact or [name for name in names if key in name.casefold()]


def query_prices(
    session: Session,
    *,
    commodity: str,
    geography_level: str = "national",
    province: str | None = None,
    start_month: str,
    end_month: str,
) -> dict[str, Any]:
    if geography_level not in {"national", "province"}:
        raise ValueError("geography_level must be national or province")
    if geography_level == "national" and province:
        raise ValueError("A national series cannot filter a province")
    start, end = month_range(start_month, end_month, limit=72)
    names = list(session.scalars(select(BapanasSnapshot.commodity).distinct()))
    if not names:
        raise ValueError("Bapanas prices are not synchronized yet; run `tilik ingest bapanas`")
    matches = match_commodity(commodity, names)
    if len(matches) != 1:
        raise ValueError(f"Choose one Bapanas commodity; matches: {matches or sorted(names)}")
    latest = (
        select(func.max(BapanasSnapshot.id))
        .where(BapanasSnapshot.commodity == matches[0], BapanasSnapshot.month >= start, BapanasSnapshot.month <= end)
        .group_by(BapanasSnapshot.month)
    )
    statement = (
        select(BapanasPrice, BapanasSnapshot)
        .join(BapanasSnapshot, BapanasPrice.snapshot_id == BapanasSnapshot.id)
        .where(BapanasSnapshot.id.in_(latest), BapanasPrice.geography_level == geography_level)
        .order_by(BapanasSnapshot.month, BapanasPrice.province_name)
    )
    pairs = [
        (price, snapshot) for price, snapshot in session.execute(statement).all()
        if not province or same_province(price.province_name, province)
    ]
    if not pairs:
        raise ValueError("No Bapanas prices match that commodity, place, and month range")
    if len(pairs) > 600:
        raise ValueError("Bapanas query exceeds 600 rows; name one province or shorten the range")
    rows = [
        {
            "price_id": price.id, "month": f"{snapshot.month:%Y-%m}", "period_end": snapshot.period_end.isoformat(),
            "complete_month": snapshot.complete, "geography_name": price.province_name,
            "province_code": price.province_code, "value": price.value,
            "reference_price": price.reference_price, "reference_price_max": price.reference_price_max,
            "status": price.status,
        }
        for price, snapshot in pairs
    ]
    snapshot = pairs[0][1]
    return {
        "provider": "Bapanas",
        "kind": "price_series",
        "title": f"Harga {snapshot.commodity} tingkat konsumen (rata-rata bulanan)",
        "source_url": PAGE_URL,
        "retrieved_at": max(snapshot.retrieved_at for _, snapshot in pairs).isoformat(),
        "commodity": snapshot.commodity,
        "unit": snapshot.unit,
        "price_level": "consumer",
        "geography_level": geography_level,
        "data_time": data_time(rows),
        "missing_values": sum(row["value"] is None for row in rows),
        "summaries": summarize(rows) if len(rows) <= 60 else [],
        "rows": rows,
    }
