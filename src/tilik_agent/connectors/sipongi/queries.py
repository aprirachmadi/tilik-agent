"""Typed reads over synchronized SiPongi monthly hotspot counts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tilik_agent.connectors.provinces import bps_province_code, same_province
from tilik_agent.connectors.series import data_time, month_range, months, summarize
from tilik_agent.connectors.sipongi.client import PAGE_URL
from tilik_agent.connectors.sipongi.models import SipongiCount, SipongiSnapshot
from tilik_agent.connectors.sipongi.parsing import ALL_CONFIDENCES, CONFIDENCES, SATELLITES


def query_counts(
    session: Session,
    *,
    start_month: str,
    end_month: str,
    geography_level: str = "national",
    province: str | None = None,
    satellite: str = "NASA-MODIS",
    confidence: str = "all",
) -> dict[str, Any]:
    if geography_level not in {"national", "province", "district"}:
        raise ValueError("geography_level must be national, province, or district")
    if geography_level == "national" and province:
        raise ValueError("A national series cannot filter a province")
    if geography_level == "district" and not province:
        raise ValueError("A district series needs one province")
    if satellite != "all" and satellite not in SATELLITES:
        raise ValueError(f"satellite must be all or one of {sorted(SATELLITES)}")
    confidence = confidence.capitalize() if confidence != "all" else "all"
    if confidence != "all" and confidence not in CONFIDENCES:
        raise ValueError("confidence must be all, high, medium, or low")
    start, end = month_range(start_month, end_month, limit=144)
    if satellite == "all" and start.year != end.year:
        raise ValueError(
            "Combined satellites change over the years; use satellite=NASA-MODIS for a multi-year series"
        )
    latest = select(func.max(SipongiSnapshot.id)).where(
        SipongiSnapshot.month >= start, SipongiSnapshot.month <= end
    ).group_by(SipongiSnapshot.month)
    snapshots = {snapshot.id: snapshot for snapshot in session.scalars(select(SipongiSnapshot).where(
        SipongiSnapshot.id.in_(latest)))}
    if not snapshots:
        raise ValueError("SiPongi hotspots are not synchronized for that range; run `tilik ingest sipongi`")
    key = {
        "national": None, "province": SipongiCount.province_name, "district": SipongiCount.district,
    }[geography_level]
    statement = select(SipongiCount.snapshot_id, *([key] if key is not None else []), func.sum(SipongiCount.count))
    statement = statement.where(SipongiCount.snapshot_id.in_(snapshots))
    if satellite != "all":
        statement = statement.where(SipongiCount.satellite == satellite)
    if confidence != "all":
        statement = statement.where(SipongiCount.confidence == confidence)
    if province:
        names = {name for name in session.scalars(select(SipongiCount.province_name).distinct())
                 if same_province(name, province)}
        if not names:
            raise ValueError(f"Unknown SiPongi province: {province}")
        statement = statement.where(SipongiCount.province_name.in_(names))
    statement = statement.group_by(SipongiCount.snapshot_id, *([key] if key is not None else []))
    sums = {}
    for row in session.execute(statement).all():
        name = "Indonesia" if key is None else (row[1] or "Tidak diketahui")
        sums[(row[0], name)] = int(row[-1])
    if geography_level == "national":
        names = ["Indonesia"]
    elif geography_level == "province" and province:
        names = sorted({name for _, name in sums}) or [province]
    else:
        names = sorted({name for _, name in sums})
    # A synchronized month with no detection is a real zero, not a gap.
    by_month = {snapshot.month: snapshot for snapshot in snapshots.values()}
    rows = [
        {
            "month": f"{snapshot.month:%Y-%m}", "period_end": snapshot.period_end.isoformat(),
            "complete_month": snapshot.complete, "geography_name": name,
            "province_code": bps_province_code(name) if geography_level == "province" else None,
            "value": sums.get((snapshot.id, name), 0),
        }
        for month in sorted(by_month) for snapshot in [by_month[month]] for name in names
    ]
    if len(rows) > 600:
        raise ValueError("SiPongi query exceeds 600 rows; name one province or shorten the range")
    missing = [f"{month:%Y-%m}" for month in months(start, end) if month not in by_month]
    period = "year" if (end.year - start.year) * 12 + end.month - start.month >= 24 else "month"
    if period == "year":
        # Over more than two years a trend reads from yearly totals; sums of monthly counts are exact.
        rows = yearly(rows)
    return {
        "provider": "SiPongi",
        "kind": "hotspot_series",
        "title": "Jumlah titik panas bulanan",
        "source_url": PAGE_URL,
        "retrieved_at": max(snapshot.retrieved_at for snapshot in snapshots.values()).isoformat(),
        "satellite": satellite,
        "confidence": ALL_CONFIDENCES if confidence == "all" else confidence,
        "geography_level": geography_level,
        "province": province,
        "unit": "hotspots",
        "period": period,
        "data_time": data_time(rows, period) if rows else None,
        "missing_months": missing,
        "summaries": summarize(rows, total=True, period=period) if len(rows) <= 150 else [],
        "rows": rows,
    }


def yearly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sum monthly rows per year and geography; a year is complete only when all 12 months are."""
    years: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["month"][:4]), row["geography_name"])
        total = years.setdefault(key, {
            "year": key[0], "months": 0, "complete_year": True, "geography_name": key[1],
            "province_code": row["province_code"], "value": 0,
        })
        total["months"] += 1
        total["complete_year"] = total["complete_year"] and row["complete_month"]
        total["value"] += row["value"]
    for total in years.values():
        total["complete_year"] = total["complete_year"] and total["months"] == 12
    return list(years.values())
