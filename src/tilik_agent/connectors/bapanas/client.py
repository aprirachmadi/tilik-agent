"""Reads from the routes behind Bapanas' public Panel Harga Pangan page (no API key)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from tilik_agent.connectors.live_http import LiveFetcher

BASE = "https://api-panelhargav2.badanpangan.go.id/api"
PAGE_URL = "https://panelharga.badanpangan.go.id/"
CONSUMER = 3  # level_harga_id: 1 producer, 2 wholesale, 3 consumer
_TODAY = f"{BASE}/front/harga-pangan-informasi"
_PROVINCE_MAP = f"{BASE}/front/harga-peta-provinsi"
_PROVINCES = f"{BASE}/provinces"
fetcher = LiveFetcher("Bapanas", (_TODAY, _PROVINCE_MAP, _PROVINCES))


def _data(payload: bytes) -> Any:
    body = json.loads(payload)
    if body.get("status") != "success":
        raise ValueError(f"Bapanas request failed: {str(body.get('message'))[:200]}")
    return body


def provinces() -> dict[int, str]:
    payload, _ = fetcher.get(_PROVINCES, ttl=86_400)
    return {int(row["id"]): str(row["nama"]).strip() for row in _data(payload)["data"]}


def today(province_id: int | None = None) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Latest consumer price per commodity, nationally or for one Bapanas province id."""
    params = {"province_id": province_id or "", "city_id": "", "level_harga_id": CONSUMER}
    payload, fetched = fetcher.get(_TODAY, params, ttl=900)
    raw = {"url": str(httpx.URL(_TODAY, params=params)), "body": payload.decode("utf-8")}
    return _data(payload), fetched, raw


def province_prices(commodity_id: int, start: date, end: date, *, ttl: int = 3600) -> tuple[dict[str, Any], str]:
    """Each province's average consumer price over [start, end], plus the national average."""
    params = {
        "level_harga_id": CONSUMER, "komoditas_id": commodity_id,
        "period_date": f"{start:%d/%m/%Y} - {end:%d/%m/%Y}",
    }
    payload, fetched = fetcher.get(_PROVINCE_MAP, params, ttl=ttl)
    return _data(payload), fetched
