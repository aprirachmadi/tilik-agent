"""Synchronize monthly province consumer prices from Bapanas."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.connectors.bapanas import client
from tilik_agent.connectors.bapanas.models import BapanasPrice, BapanasSnapshot
from tilik_agent.connectors.bapanas.parsing import province_rows, today_items
from tilik_agent.connectors.series import month_bounds, months

HISTORY_START = date(2021, 6, 1)  # earliest month verified to return prices


def sync_bapanas(session: Session, *, today: date | None = None, pause: float = 1.3) -> dict[str, int]:
    """Fetch every month not yet stored as complete; a month is complete once it has ended."""
    today = today or datetime.now(ZoneInfo("Asia/Jakarta")).date()
    counts = {}
    for item in today_items(client.today()[0]):
        added = 0
        for month in months(HISTORY_START, today - timedelta(days=1)):
            period_end, complete = month_bounds(month, today)
            latest = session.scalar(
                select(BapanasSnapshot)
                .where(BapanasSnapshot.commodity_id == item["commodity_id"], BapanasSnapshot.month == month)
                .order_by(BapanasSnapshot.id.desc())
                .limit(1)
            )
            if latest and latest.complete:
                continue
            body, _ = client.province_prices(item["commodity_id"], month, period_end, ttl=0)
            rows = province_rows(body)
            content = {"period_end": period_end.isoformat(), "detail": body.get("detail"), "data": body.get("data")}
            checksum = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if latest is None or latest.content_sha256 != checksum:
                snapshot = BapanasSnapshot(
                    commodity_id=item["commodity_id"], commodity=item["commodity"], unit=item["unit"],
                    month=month, period_end=period_end, complete=complete, content_sha256=checksum,
                )
                session.add(snapshot)
                session.flush()
                session.add_all(BapanasPrice(snapshot_id=snapshot.id, **row) for row in rows)
                session.commit()
                added += 1
            time.sleep(pause)
        counts[item["commodity"]] = added
    return counts
