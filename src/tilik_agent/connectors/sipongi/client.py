"""Reads from SiPongi's hotspot data service, the backend of its public map page (no API key)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from tilik_agent.connectors.live_http import LiveFetcher

BASE = "https://opsroom.sipongidata.my.id/api"
PAGE_URL = "https://sipongi.gakkum.kehutanan.go.id/sebaran-titik-panas"
ALL_SATELLITES = "all-nasa-new"  # NASA-MODIS, NASA-NOAA20 and NASA-NOAA21 combined
_SPREAD = f"{BASE}/sebaran"
_PROVINCES = f"{BASE}/getProvinsi/all"
fetcher = LiveFetcher("SiPongi", (_SPREAD, _PROVINCES), max_bytes=5_000_000, timeout=120)


def provinces() -> dict[str, str]:
    """SiPongi province id (as the `provinsi` parameter expects) by province name."""
    payload, _ = fetcher.get(_PROVINCES, ttl=86_400)
    return {str(row["nama_provinsi"]).strip(): str(row["id"]) for row in json.loads(payload)}


def _spread(params: dict[str, Any], ttl: int) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    # An empty `provinsi` silently means Kalimantan Barat, so callers always pass "all" or an id.
    if not params.get("provinsi"):
        raise ValueError("SiPongi requests need provinsi=all or a SiPongi province id")
    params = {"satelit": ALL_SATELLITES, "confidence": "all", **params}
    payload, fetched = fetcher.get(_SPREAD, params, ttl=ttl)
    rows = json.loads(payload).get("data")
    if not isinstance(rows, list):
        raise ValueError("SiPongi returned no hotspot list")
    raw = {"url": str(httpx.URL(_SPREAD, params=params)), "body": payload.decode("utf-8")}
    return rows, fetched, raw


def recent(hours: int, province_id: str = "all") -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    """Hotspots detected in the last 12, 24 or 48 hours."""
    if hours not in {12, 24, 48}:
        raise ValueError("SiPongi recent windows are 12, 24 or 48 hours")
    return _spread({"late": str(hours), "provinsi": province_id}, ttl=600)


def between(start: date, end: date) -> tuple[list[dict[str, Any]], str]:
    """National hotspots from the start of `start` to the end of `end` (local dates)."""
    rows, fetched, _ = _spread(
        {"startdate": f"{start} 00:00:00", "enddate": f"{end} 23:59:59", "provinsi": "all"}, ttl=0
    )
    return rows, fetched
