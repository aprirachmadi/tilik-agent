"""Rate-aware reads from BMKG's official public endpoints."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import httpx

_LOCK = threading.Lock()
_TIMES: list[float] = []
_CACHE: dict[str, tuple[float, bytes, str]] = {}


def fetch(url: str, *, ttl: int = 180) -> bytes:
    if not url.startswith(("https://api.bmkg.go.id/publik/prakiraan-cuaca?", "https://data.bmkg.go.id/DataMKG/TEWS/", "https://www.bmkg.go.id/alerts/nowcast/id")):
        raise ValueError("Unapproved BMKG endpoint")
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and now - cached[0] < ttl:
            return cached[1]
        _TIMES[:] = [value for value in _TIMES if now - value < 60]
        if len(_TIMES) >= 50:
            raise RuntimeError("BMKG request budget exhausted; retry later")
        _TIMES.append(now)
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.get(url, headers={"User-Agent": "Tilik/0.1 (+https://data.bmkg.go.id/)"})
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("BMKG response exceeds size limit")
        payload = response.content
    with _LOCK:
        _CACHE[url] = (time.monotonic(), payload, datetime.now(UTC).isoformat())
    return payload


def fetched_at(url: str) -> str:
    with _LOCK:
        return _CACHE[url][2]
