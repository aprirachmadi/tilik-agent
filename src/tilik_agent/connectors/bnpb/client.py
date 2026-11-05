"""Bounded reads from BNPB's public CKAN DataStore API."""

from __future__ import annotations

from typing import Any

import httpx

BASE = "https://data.bnpb.go.id/api/3/action"
PACKAGE = "data-bencana-indonesia"
RESOURCES = {
    "province_events": "5dfcae7e-acb3-4548-82b5-61d876ea478a",
    "district_events": "21044ffd-c397-4b3c-acbd-5adaa03d79e3",
    "type_events": "9b41007e-c998-456b-8cbc-385b17986e46",
    "type_affected": "7f9e5218-bbba-4916-a2b8-13cf1764dc96",
}


def fetch_tables() -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Fetch only the four reviewed resources; reject a changed catalog or row count."""
    output = {}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        package = client.get(f"{BASE}/package_show", params={"id": PACKAGE})
        package.raise_for_status()
        metadata = package.json()["result"]
        listed = {item["id"]: item for item in metadata["resources"]}
        for kind, resource_id in RESOURCES.items():
            if resource_id not in listed:
                raise ValueError(f"BNPB resource missing: {resource_id}")
            records = []
            total = None
            for offset in range(0, 10_000, 100):
                response = client.get(
                    f"{BASE}/datastore_search", params={"resource_id": resource_id, "limit": 100, "offset": offset}
                )
                response.raise_for_status()
                result = response.json()["result"]
                total = int(result["total"])
                records.extend(result["records"])
                if len(records) >= total:
                    break
            if total is None or len(records) != total or total > 10_000:
                raise ValueError(f"Incomplete BNPB resource {resource_id}: {len(records)}/{total}")
            output[kind] = ({**listed[resource_id], "package_license": metadata.get("license_id")}, records)
    return output
