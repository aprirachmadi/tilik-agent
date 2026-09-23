"""BPS WebAPI client.

The API key is sent on the request path and is never logged or written to disk.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tilik_agent.ingestion.parsing import published_years

BASE = "https://webapi.bps.go.id/v1/api"
USER_AGENT = "tilik-bps-ingest/1.0"
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


class BpsError(RuntimeError):
    """The BPS API returned an error payload or an unusable response."""


class BpsTransientError(BpsError):
    """A transport or server failure that is worth retrying."""


def redact(value: Any, secret: str) -> Any:
    """Remove the API key from nested response data before it is stored."""
    if not secret:
        return value
    if isinstance(value, dict):
        return {key: redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def build_url(endpoint: str, params: list[tuple[str, Any]], api_key: str) -> str:
    """Build the slash-separated BPS URL. ``api_key`` is appended as the ``key`` segment."""
    segments = [BASE, endpoint]
    for name, value in [*params, ("key", api_key)]:
        segments.extend([quote(str(name), safe=""), quote(str(value), safe=";:")])
    return "/".join(segment.strip("/") for segment in segments) + "/"


def params_fingerprint(params: list[tuple[str, Any]]) -> str:
    """Hash request parameters. The API key is not part of this fingerprint."""
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BpsClient:
    """Throttled JSON client for the national BPS WebAPI."""

    def __init__(self, api_key: str, *, domain: str = "0000", timeout: float = 60.0) -> None:
        if not api_key:
            raise BpsError("BPS_API_KEY is missing from the environment and .env")
        self._api_key = api_key
        self.domain = domain
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``payload`` with the API key removed."""
        redacted = redact(payload, self._api_key)
        if not isinstance(redacted, dict):
            raise BpsError("Expected a JSON object")
        return redacted

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> BpsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(BpsTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def request_json(self, endpoint: str, params: list[tuple[str, Any]]) -> dict[str, Any]:
        """GET a JSON endpoint. Logical API errors are not retried."""
        url = build_url(endpoint, params, self._api_key)
        try:
            response = self._client.get(url)
        except httpx.TransportError as exc:
            raise BpsTransientError(f"BPS request failed for {endpoint}") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise BpsTransientError(f"BPS HTTP {response.status_code} for {endpoint}")
        content_type = response.headers.get("Content-Type", "")
        body = response.content
        if "json" not in content_type.lower() and not body.lstrip().startswith((b"{", b"[")):
            raise BpsError(f"BPS returned non-JSON content for {endpoint}")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise BpsError(f"Unexpected top-level response for {endpoint}")
        status = str(payload.get("status") or "")
        if status and status.upper() != "OK":
            message = payload.get("message") or payload.get("error") or "unknown API error"
            raise BpsError(redact(f"BPS API error for {endpoint}: {message}", self._api_key))
        time.sleep(0.15)
        return payload

    def list_records(self, model: str, extra: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        """Follow pagination for a list model and return every record."""
        page = 1
        records: list[dict[str, Any]] = []
        while True:
            payload = self.request_json(
                "list",
                [("model", model), ("domain", self.domain), *extra, ("page", page), ("perpage", 100)],
            )
            meta, batch = _page_parts(payload)
            records.extend(batch)
            pages = int(meta.get("pages") or 1)
            if page >= pages:
                return records
            page += 1

    def view_table(self, table_id: str, year: int | None = None) -> dict[str, Any]:
        """Fetch one statistical table detail, optionally for a single year."""
        params: list[tuple[str, Any]] = [
            ("model", "tablestatistic"),
            ("domain", self.domain),
            ("id", table_id),
            ("lang", "ind"),
        ]
        if year is not None:
            params.append(("year", year))
        return self.request_json("view", params)

    def download(self, url: str) -> tuple[bytes, str]:
        """Download an official BPS workbook. Non-BPS hosts are refused."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host.endswith("bps.go.id"):
            raise BpsError("Refusing a non-BPS download URL")
        try:
            response = self._client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        except httpx.TransportError as exc:
            raise BpsTransientError("BPS workbook download failed") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise BpsTransientError(f"BPS workbook HTTP {response.status_code}")
        body = response.content
        if len(body) > _MAX_DOWNLOAD_BYTES:
            raise BpsError("Workbook download exceeds 50 MiB")
        time.sleep(0.15)
        return body, response.headers.get("Content-Type", "application/octet-stream")


def dynamic_years(payload: dict[str, Any], requested_year: int | None) -> list[int]:
    """Years still to fetch after the first dynamic response.

    ``available_years`` is the authority. The year already returned is removed.
    """
    returned = _returned_years(payload)
    years: list[int] = []
    raw_years = payload.get("available_years")
    if isinstance(raw_years, list):
        for item in raw_years:
            text = str(item).strip()
            if not text.isdigit():
                continue
            year = int(text)
            if year == requested_year or (requested_year is None and year in returned):
                continue
            years.append(year)
    return years


def _returned_years(payload: dict[str, Any]) -> set[int]:
    years: set[int] = set()
    tahun = payload.get("tahun")
    if not isinstance(tahun, list):
        return years
    for item in tahun:
        if isinstance(item, dict) and str(item.get("label", "")).strip().isdigit():
            years.add(int(str(item["label"]).strip()))
    return years


def simdasi_years(oldest: int | None, latest: int | None) -> list[int | None]:
    """Catalog year span for a SIMDASI table. ``list_tahun_data`` is not complete."""
    return published_years(oldest, latest)


def _page_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = payload.get("data")
    if payload.get("data-availability") not in (None, "available") or data in (None, "", []):
        return {"page": 1, "pages": 1, "per_page": 0, "count": 0, "total": 0}, []
    if not isinstance(data, list) or len(data) < 2:
        raise BpsError("Expected a paginated BPS response")
    meta = data[0] if isinstance(data[0], dict) else {}
    records = data[1] if isinstance(data[1], list) else []
    return meta, [record for record in records if isinstance(record, dict)]
