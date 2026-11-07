"""Allowlisted, cached, rate-budgeted GET requests for live source servers."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx


class LiveFetcher:
    """One per source: only approved URL prefixes, a per-minute budget, and a short cache."""

    def __init__(self, provider: str, prefixes: tuple[str, ...], *, per_minute: int = 50,
                 max_bytes: int = 2_000_000, timeout: float = 30) -> None:
        self._provider = provider
        self._prefixes = prefixes
        self._per_minute = per_minute
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._lock = threading.Lock()
        self._times: list[float] = []
        self._cache: dict[str, tuple[float, bytes, str]] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, *, ttl: int = 300) -> tuple[bytes, str]:
        """Return the body and its UTC fetch time; a cache hit keeps the original fetch time."""
        if not url.startswith(self._prefixes):
            raise ValueError(f"Unapproved {self._provider} endpoint")
        key = str(httpx.URL(url, params=params or {}))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl:
                return cached[1], cached[2]
            self._times[:] = [value for value in self._times if now - value < 60]
            if len(self._times) >= self._per_minute:
                raise RuntimeError(f"{self._provider} request budget exhausted; retry later")
            self._times.append(now)
        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
            response = client.get(url, params=params, headers={"User-Agent": "Tilik/0.1", "Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > self._max_bytes:
                raise ValueError(f"{self._provider} response exceeds size limit")
            payload = response.content
        fetched = datetime.now(UTC).isoformat()
        with self._lock:
            self._cache[key] = (time.monotonic(), payload, fetched)
        return payload, fetched
