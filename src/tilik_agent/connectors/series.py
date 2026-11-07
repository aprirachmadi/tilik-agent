"""Month ranges and series summaries shared by the monthly synced connectors."""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any


def parse_month(value: str) -> date:
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ValueError(f"Month must be YYYY-MM, got {value!r}") from exc


def months(start: date, end: date) -> Iterator[date]:
    month = start.replace(day=1)
    while month <= end:
        yield month
        month = (month + timedelta(days=32)).replace(day=1)


def month_bounds(month: date, today: date) -> tuple[date, bool]:
    """The month's last fetched day and whether the month has ended (data through yesterday)."""
    last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    complete = last_day < today
    return (last_day if complete else today - timedelta(days=1)), complete


def month_range(start_month: str, end_month: str, *, limit: int) -> tuple[date, date]:
    start, end = parse_month(start_month), parse_month(end_month)
    if not start <= end or (end.year - start.year) * 12 + end.month - start.month >= limit:
        raise ValueError(f"Month range must be ordered and at most {limit} months")
    return start, end


def summarize(rows: list[dict[str, Any]], *, total: bool = False, period: str = "month") -> list[dict[str, Any]]:
    """First-to-last change and extremes per geography, over periods that have a value."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["value"] is not None:
            groups.setdefault(row["geography_name"], []).append(row)
    summaries = []
    for name, group in groups.items():
        first, last = group[0], group[-1]
        low = min(group, key=lambda row: row["value"])
        high = max(group, key=lambda row: row["value"])
        difference = last["value"] - first["value"]
        summaries.append({
            "geography_name": name, f"{period}s": len(group),
            f"first_{period}": first[period], "first_value": first["value"],
            f"last_{period}": last[period], "last_value": last["value"], "difference": difference,
            "percent_change": round(difference / first["value"] * 100, 2) if first["value"] else None,
            f"min_{period}": low[period], "min_value": low["value"],
            f"max_{period}": high[period], "max_value": high["value"],
            **({"total": sum(row["value"] for row in group)} if total else {}),
        })
    return summaries


def data_time(rows: list[dict[str, Any]], period: str = "month") -> str:
    first, last = str(rows[0][period]), str(rows[-1][period])
    return first if first == last else f"{first}–{last}"
