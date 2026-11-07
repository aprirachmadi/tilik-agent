"""Validate Bapanas price payloads into bounded rows."""

from __future__ import annotations

import re
from typing import Any

from tilik_agent.connectors.provinces import bps_province_code


def unit(value: Any) -> str:
    return re.sub(r"^Rp\.?\s*/", "Rp/", str(value or "").strip()).replace("/Kg", "/kg").replace("/Liter", "/liter")


def _price(value: Any) -> int | None:
    """Prices arrive as ints or as Indonesian-formatted strings such as "15.400"."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid Bapanas price: {value!r}")
    if isinstance(value, int | float):
        number = value
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d+", str(value).strip()):
        number = int(str(value).strip().replace(".", ""))
    else:
        raise ValueError(f"Invalid Bapanas price: {value!r}")
    if number < 0 or int(number) != number:
        raise ValueError(f"Invalid Bapanas price: {value!r}")
    return int(number) or None  # a zero price is not a price; keep it missing


def _reference(value: Any) -> tuple[int | None, int | None]:
    """A reference price (HET/HAP) is one price or a "low - high" range."""
    if isinstance(value, str) and "-" in value:
        low, high = (_price(part) for part in value.split("-", 1))
        if low is None or high is None or low > high:
            raise ValueError(f"Invalid Bapanas reference range: {value!r}")
        return low, high
    return _price(value), None


def today_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in body["data"]:
        price, previous = _price(row.get("today")), _price(row.get("yesterday"))
        items.append({
            "commodity_id": int(row["id"]), "commodity": str(row["name"]).strip(), "unit": unit(row.get("satuan")),
            "price": price, "previous_price": previous, "previous_date": row.get("yesterday_date"),
            "change": price - previous if price is not None and previous is not None else None,
            "change_pct": row.get("gap_percentage") if price is not None and previous is not None else None,
        })
    if not items or len(items) > 60:
        raise ValueError(f"Unexpected Bapanas commodity count: {len(items)}")
    return items


def province_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """One national row (source's own average) plus one row per reporting province."""
    rows = [{
        "geography_level": "national", "province_id": None, "province_name": "Indonesia", "province_code": None,
        "value": _price((body.get("detail") or {}).get("hargaratarata")), "reference_price": None,
        "reference_price_max": None, "status": None, "source_row": body.get("detail") or {},
    }]
    seen = set()
    for row in body.get("data") or []:
        province_id = int(row["province_id"])
        if province_id in seen:
            raise ValueError(f"Duplicate Bapanas province: {province_id}")
        seen.add(province_id)
        name = str(row["province_name"]).strip()
        reference, reference_max = _reference(row.get("hpp_hap"))
        rows.append({
            "geography_level": "province", "province_id": province_id, "province_name": name,
            "province_code": bps_province_code(name), "value": _price(row.get("rata_rata_geometrik")),
            "reference_price": reference, "reference_price_max": reference_max,
            "status": row.get("status_map"), "source_row": row,
        })
    if len(rows) > 40:
        raise ValueError(f"Unexpected Bapanas province count: {len(rows) - 1}")
    return rows
