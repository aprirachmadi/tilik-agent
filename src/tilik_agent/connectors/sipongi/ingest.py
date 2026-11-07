"""Synchronize monthly national hotspot counts from SiPongi."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.connectors.series import month_bounds, months
from tilik_agent.connectors.sipongi import client
from tilik_agent.connectors.sipongi.models import SipongiCount, SipongiSnapshot
from tilik_agent.connectors.sipongi.parsing import counts

HISTORY_START = date(2015, 1, 1)  # MODIS-only before VIIRS joins; verified to return data


def _rows(start: date, end: date) -> list[dict[str, Any]]:
    """SiPongi answers 500 for a very busy month (e.g. 2026-09); split the range until it answers."""
    try:
        return client.between(start, end)[0]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 500 or start == end:
            raise
    middle = start + (end - start) // 2
    return _rows(start, middle) + _rows(middle + timedelta(days=1), end)


def sync_sipongi(session: Session, *, today: date | None = None, pause: float = 1.3) -> dict[str, int]:
    """Fetch every month not yet stored as complete; a month is complete once it has ended."""
    today = today or datetime.now(ZoneInfo("Asia/Jakarta")).date()
    added = {}
    for month in months(HISTORY_START, today - timedelta(days=1)):
        period_end, complete = month_bounds(month, today)
        latest = session.scalar(
            select(SipongiSnapshot).where(SipongiSnapshot.month == month).order_by(SipongiSnapshot.id.desc()).limit(1)
        )
        if latest and latest.complete:
            continue
        rows = _rows(month, period_end)
        parsed = counts(rows)
        content = {"period_end": period_end.isoformat(), "rows": sorted(rows, key=lambda row: json.dumps(row))}
        checksum = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if latest is None or latest.content_sha256 != checksum:
            snapshot = SipongiSnapshot(month=month, period_end=period_end, complete=complete, content_sha256=checksum)
            session.add(snapshot)
            session.flush()
            session.add_all(SipongiCount(snapshot_id=snapshot.id, **row) for row in parsed)
            session.commit()
            added[f"{month:%Y-%m}"] = sum(row["count"] for row in parsed)
        time.sleep(pause)
    return added
