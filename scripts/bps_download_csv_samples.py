#!/usr/bin/env python3
"""Download selected BPS dynamic tables and normalize their cells to CSV."""

from __future__ import annotations

import csv
import itertools
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bps_recon_download import ROOT, api_request


OUT = ROOT / "data" / "bps" / "csv-samples-2026-09-11"
TABLES = [
    ("population_by_province_and_sex_2025.csv", "Mjc4MSMy", 2025),
    ("unemployment_rate_by_province_2026.csv", "NTQzIzI=", 2026),
    ("school_participation_by_province_and_age_2025.csv", "MjIxMSMy", 2025),
    ("life_expectancy_by_province_and_sex_2025.csv", "MjI3MyMy", 2025),
    ("sanitation_access_by_province_and_area_2025.csv", "ODM0IzI=", 2025),
]


def dimension(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, list) and values:
        return [item for item in values if isinstance(item, dict)]
    return [{"val": 0, "label": ""}]


def write_table(filename: str, table_id: str, year: int) -> dict[str, Any]:
    payload = api_request(
        "view",
        [
            ("model", "tablestatistic"),
            ("domain", "0000"),
            ("id", table_id),
            ("year", year),
            ("lang", "ind"),
        ],
    )
    variables = dimension(payload.get("var"))
    geographies = dimension(payload.get("vervar"))
    characteristics = dimension(payload.get("turvar"))
    periods = dimension(payload.get("tahun"))
    subperiods = dimension(payload.get("turtahun"))
    cells = payload.get("datacontent") or {}
    if not isinstance(cells, dict):
        raise RuntimeError(f"Unexpected datacontent for {filename}")

    downloaded_at = datetime.now(timezone.utc).isoformat()
    public_url = f"https://www.bps.go.id/id/statistics-table/2/{quote(table_id, safe='')}"
    rows: list[dict[str, Any]] = []
    matched_keys: set[str] = set()
    for geography, variable, characteristic, period, subperiod in itertools.product(
        geographies, variables, characteristics, periods, subperiods
    ):
        key = "".join(
            str(item.get("val", 0))
            for item in (geography, variable, characteristic, period, subperiod)
        )
        if key not in cells:
            continue
        matched_keys.add(key)
        rows.append(
            {
                "geography_id": geography.get("val"),
                "geography": geography.get("label"),
                "characteristic_id": characteristic.get("val"),
                "characteristic": characteristic.get("label"),
                "period_id": period.get("val"),
                "period": period.get("label"),
                "subperiod_id": subperiod.get("val"),
                "subperiod": subperiod.get("label"),
                "value": cells[key],
                "unit": variable.get("unit"),
                "indicator_id": variable.get("val"),
                "indicator": variable.get("label"),
                "subject": variable.get("subj"),
                "definition": variable.get("def"),
                "note": variable.get("note"),
                "last_update": payload.get("last_update"),
                "bps_table_url": public_url,
                "downloaded_at_utc": downloaded_at,
            }
        )

    unmatched = set(map(str, cells)) - matched_keys
    if unmatched:
        raise RuntimeError(f"Could not decode {len(unmatched)} cells for {filename}")
    if not rows:
        raise RuntimeError(f"No cells returned for {filename}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / filename
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {
        "file": filename,
        "rows": len(rows),
        "columns": len(rows[0]),
        "indicator": variables[0].get("label"),
        "periods": sorted({str(row["period"]) for row in rows}),
        "subperiods": sorted({str(row["subperiod"]) for row in rows}),
    }


def main() -> None:
    results = [write_table(*spec) for spec in TABLES]
    for result in results:
        print(
            f"{result['file']}: {result['rows']} rows, {result['columns']} columns, "
            f"period={','.join(result['periods'])}, subperiod={','.join(result['subperiods'])}"
        )


if __name__ == "__main__":
    main()
