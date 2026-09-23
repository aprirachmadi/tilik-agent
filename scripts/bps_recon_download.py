#!/usr/bin/env python3
"""Download a sanitized BPS CSA catalog using BPS_API_KEY from .env.

The key is loaded only in memory. It is never printed or written to disk.
"""

from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bps" / f"recon-{date.today().isoformat()}"
BASE = "https://webapi.bps.go.id/v1/api"


def load_dotenv_key() -> str:
    existing = os.environ.get("BPS_API_KEY")
    if existing:
        return existing
    env_path = ROOT / ".env"
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "BPS_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
    raise RuntimeError("BPS_API_KEY is missing from the environment and .env")


API_KEY = load_dotenv_key()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return value.replace(API_KEY, "[REDACTED]")
    return value


def api_request(endpoint: str, params: list[tuple[str, Any]]) -> dict[str, Any]:
    segments = [BASE, endpoint]
    for name, value in params + [("key", API_KEY)]:
        segments.extend(
            [
                urllib.parse.quote(str(name), safe=""),
                urllib.parse.quote(str(value), safe=";:"),
            ]
        )
    url = "/".join(segment.strip("/") for segment in segments) + "/"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "tilik-bps-recon/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"BPS HTTP error {exc.code} for {endpoint}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"BPS request failed for {endpoint}: {exc.reason}") from None
    if "json" not in content_type.lower() and not body.lstrip().startswith((b"{", b"[")):
        raise RuntimeError(f"BPS returned non-JSON content for {endpoint}")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected top-level response for {endpoint}")
    if payload.get("status") != "OK":
        message = payload.get("message") or payload.get("error") or "unknown API error"
        raise RuntimeError(f"BPS API error for {endpoint}: {message}")
    return payload


def page_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = payload.get("data")
    if payload.get("data-availability") != "available" or data in (None, "", []):
        return {"page": 1, "pages": 1, "per_page": 0, "count": 0, "total": 0}, []
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError("Expected paginated BPS response")
    meta = data[0] if isinstance(data[0], dict) else {}
    records = data[1] if isinstance(data[1], list) else []
    return meta, records


def paginated_list(model: str, extra: list[tuple[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page = 1
    records: list[dict[str, Any]] = []
    first_meta: dict[str, Any] = {}
    while True:
        payload = api_request(
            "list",
            [("model", model), ("domain", "0000"), *extra, ("page", page), ("perpage", 100)],
        )
        meta, batch = page_parts(payload)
        if page == 1:
            first_meta = meta
        records.extend(record for record in batch if isinstance(record, dict))
        pages = int(meta.get("pages") or 1)
        if page >= pages:
            break
        page += 1
        time.sleep(0.15)
    return records, first_meta


def write_json(name: str, payload: Any) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def download_bps_file(url: str, stem: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.hostname.endswith("bps.go.id"):
        raise RuntimeError("Refusing a non-BPS download URL")
    request = urllib.request.Request(url, headers={"User-Agent": "tilik-bps-recon/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        body = response.read(50 * 1024 * 1024 + 1)
    if len(body) > 50 * 1024 * 1024:
        raise RuntimeError("Sample download exceeds 50 MiB safety limit")
    extension = ".xlsx" if body.startswith(b"PK\x03\x04") else ".xls"
    filename = stem + extension
    (OUT / filename).write_bytes(body)
    return {
        "filename": filename,
        "bytes": len(body),
        "content_type": content_type,
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: schema(child) for key, child in value.items()}
    if isinstance(value, list):
        sample = next((item for item in value if item is not None), None)
        return {"type": "array", "length": len(value), "item": schema(sample)}
    return type(value).__name__


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    categories, category_meta = paginated_list("subcatcsa", [])
    subjects, subject_meta = paginated_list("subjectcsa", [("subcat", 514)])

    subject_catalogs: dict[str, list[dict[str, Any]]] = {}
    subject_counts: list[dict[str, Any]] = []
    all_tables: list[dict[str, Any]] = []
    for subject in subjects:
        subject_id = str(subject.get("sub_id"))
        tables, meta = paginated_list("tablestatistic", [("subject", subject_id)])
        subject_catalogs[subject_id] = tables
        all_tables.extend(tables)
        by_source = Counter(str(table.get("tablesource")) for table in tables)
        subject_counts.append(
            {
                "subject_id": subject_id,
                "subject": subject.get("title"),
                "api_total": meta.get("total"),
                "downloaded_records": len(tables),
                "source_counts": dict(sorted(by_source.items())),
            }
        )
        time.sleep(0.15)

    samples: dict[str, Any] = {}
    sample_schemas: dict[str, Any] = {}
    for source in ("1", "2", "3"):
        table = next((row for row in all_tables if str(row.get("tablesource")) == source), None)
        if table is None:
            continue
        detail_params: list[tuple[str, Any]] = [
            ("model", "tablestatistic"),
            ("domain", "0000"),
            ("id", table.get("id")),
        ]
        if table.get("latest_period") is not None:
            detail_params.append(("year", table["latest_period"]))
        detail_params.append(("lang", "ind"))
        detail = api_request("view", detail_params)
        samples[f"tablesource_{source}"] = {
            "catalog_record": table,
            "detail_response": detail,
        }
        sample_schemas[f"tablesource_{source}"] = schema(detail)
        time.sleep(0.15)

    total_by_source = Counter(str(table.get("tablesource")) for table in all_tables)
    static_excel = samples.get("tablesource_1", {}).get("detail_response", {}).get("data", {}).get("excel")
    sample_file = download_bps_file(static_excel, "sample-static-table") if static_excel else None
    summary = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "domain": "0000",
        "csa_category": 514,
        "category_pagination": category_meta,
        "subject_pagination": subject_meta,
        "subject_count": len(subjects),
        "table_count": len(all_tables),
        "table_count_by_source": dict(sorted(total_by_source.items())),
        "subjects": subject_counts,
        "sample_detail_schemas": sample_schemas,
        "sample_static_download": sample_file,
    }

    write_json("summary.json", summary)
    write_json("categories.json", categories)
    write_json("subjects.json", subjects)
    write_json("tables.json", all_tables)
    write_json("tables-by-subject.json", subject_catalogs)
    write_json("sample-details.json", samples)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved sanitized responses under {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
