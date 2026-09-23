#!/usr/bin/env python3
"""Download a few BPS SIMDASI tables and record how their JSON rows are shaped.

This script is a format probe. It is not the production loader.
The API key is loaded only in memory and redacted before anything is written.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from bps_recon_download import ROOT, api_request, redact

PROBE_OUT = ROOT / "data" / "bps" / "probes" / "simdasi"
CATALOG = ROOT / "data" / "bps" / "recon-2026-09-11" / "tables.json"
# One table each from culture, crime, population, and education.
TABLE_IDS = (
    "ZVZwMGQySkRRbWx6WnpSVmMyRkVTa052WlRoUGR6MDkjMyMwMDAw",
    "VlVneFlXOVJOSFpNUzNKcU4zVlFla2syVEZjd1VUMDkjMyMwMDAw",
    "V1ZSbFRUY3lTbFpEYTNsVWNGcDZjek53YkhsNFFUMDkjMyMwMDAw",
    "TlRWVWFVSTRiRE5CUjFOcFpERkxaVEJJU1N0cGR6MDkjMyMwMDAw",
)
THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
STATUS_TOKENS = {"-", "–", "NA", "...", "e", "r", "*", "**", "***", "~0", "a", "b", "c"}


def catalog_by_id() -> dict[str, dict[str, Any]]:
    tables = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {str(row["id"]): row for row in tables if isinstance(row, dict)}


def record_body(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
        return data[1]
    if isinstance(data, dict):
        return data
    return {}


def earlier_year(record: dict[str, Any], latest: int | None) -> int | None:
    oldest = record.get("oldest_period")
    if isinstance(oldest, int) and isinstance(latest, int) and oldest < latest:
        return oldest
    if isinstance(latest, int) and latest > 1900:
        return latest - 1
    return None


def describe_rows(body: dict[str, Any]) -> dict[str, Any]:
    rows = body.get("data") if isinstance(body.get("data"), list) else []
    variable_types: Counter[str] = Counter()
    status_examples: list[dict[str, Any]] = []
    thousands_examples: list[str] = []
    column_ids: set[str] = set()
    geography_codes = 0
    for row in rows:
        if not isinstance(row, dict):
            variable_types["non_dict_row"] += 1
            continue
        if row.get("kode_wilayah") not in (None, ""):
            geography_codes += 1
        variables = row.get("variables")
        variable_types[type(variables).__name__] += 1
        cells: list[tuple[str, Any]] = []
        if isinstance(variables, dict):
            cells = list(variables.items())
        elif isinstance(variables, list):
            for item in variables:
                if isinstance(item, dict):
                    cells.append((str(item.get("id") or item.get("kode") or ""), item))
        for column_id, cell in cells:
            column_ids.add(column_id)
            if not isinstance(cell, dict):
                continue
            raw = str(cell.get("value_raw") if cell.get("value_raw") is not None else cell.get("value") or "")
            token = raw.strip()
            if token in STATUS_TOKENS and len(status_examples) < 8:
                status_examples.append({"label": row.get("label"), "column": column_id, "value_raw": token})
            if THOUSANDS_RE.match(token) and len(thousands_examples) < 8:
                thousands_examples.append(token)
    columns = body.get("kolom") if isinstance(body.get("kolom"), dict) else {}
    column_summaries = []
    for column_id, column in columns.items():
        if not isinstance(column, dict):
            continue
        column_summaries.append(
            {
                "column_id": column_id,
                "nama_variabel": column.get("nama_variabel"),
                "tipe": column.get("tipe"),
                "angka_desimal_dibelakang_koma": column.get("angka_desimal_dibelakang_koma"),
                "satuan_in_column": column.get("satuan") or column.get("metadata_satuan"),
            }
        )
    return {
        "row_count": len(rows),
        "variable_container_types": dict(variable_types),
        "rows_with_kode_wilayah": geography_codes,
        "column_ids_seen_in_rows": sorted(column_ids),
        "columns": column_summaries,
        "status_examples": status_examples,
        "thousands_separator_examples": thousands_examples,
        "keterangan_data": body.get("keterangan_data"),
        "tahun_data": body.get("tahun_data"),
        "list_tahun_data": body.get("list_tahun_data"),
        "lingkup_id": body.get("lingkup_id"),
        "show_satuan": body.get("show_satuan"),
    }


def mapping_draft(description: dict[str, Any]) -> list[dict[str, str]]:
    notes = [
        {
            "field": "kolom.*.nama_variabel",
            "role": "measure",
            "reason": "Each entry in kolom is one published measure, including its decimal setting.",
        },
        {
            "field": "tahun_data",
            "role": "period",
            "reason": "The response year is the period. Request it explicitly so an older year is not returned.",
        },
        {
            "field": "data[].label and data[].kode_wilayah",
            "role": "dimension_member",
            "reason": (
                "Row label is the member. kode_wilayah is the geography code when present. "
                "Otherwise the member stays generic_category."
            ),
        },
        {
            "field": "variables as an empty list",
            "role": "status_flag",
            "reason": "An empty list means the source published no cell. Store status unavailable, not numeric zero.",
        },
        {
            "field": "value_raw",
            "role": "source value",
            "reason": "Keep the original text. Parse a number only after applying the column decimal setting.",
        },
        {
            "field": "keterangan_data keys",
            "role": "status_flag",
            "reason": "Tokens such as -, NA, e, r, and * qualify the cell. They are not zero.",
        },
        {
            "field": "list_tahun_data",
            "role": "raw hint, not the full year list",
            "reason": (
                "The list on one response can omit the catalog latest year. "
                "Walk oldest_period through latest_period as well."
            ),
        },
    ]
    if description.get("thousands_separator_examples"):
        notes.append(
            {
                "field": "value_raw with dot groups",
                "role": "source value",
                "reason": (
                    "Dot groups are thousands separators when the column allows 0 decimal places: "
                    + ", ".join(description["thousands_separator_examples"][:5])
                ),
            }
        )
    if len(description.get("columns") or []) > 1:
        notes.append(
            {
                "field": "multiple kolom entries",
                "role": "measure",
                "reason": "One row expands to one observation per column. Columns are not dimensions.",
            }
        )
    return notes


def main() -> None:
    PROBE_OUT.mkdir(parents=True, exist_ok=True)
    catalog = catalog_by_id()
    missing = [table_id for table_id in TABLE_IDS if table_id not in catalog]
    if missing:
        raise SystemExit(f"Catalog is missing SIMDASI table ids: {', '.join(missing)}")

    summaries: list[dict[str, Any]] = []
    for table_id in TABLE_IDS:
        record = catalog[table_id]
        latest = record.get("latest_period")
        years = [latest]
        previous = earlier_year(record, latest if isinstance(latest, int) else None)
        if previous is not None:
            years.append(previous)
        year_notes: list[dict[str, Any]] = []
        for year in years:
            params: list[tuple[str, Any]] = [
                ("model", "tablestatistic"),
                ("domain", "0000"),
                ("id", table_id),
                ("lang", "ind"),
            ]
            if year is not None:
                params.append(("year", year))
            detail = api_request("view", params)
            body = record_body(detail)
            description = describe_rows(body)
            slug = f"{table_id[:12]}-{year}"
            (PROBE_OUT / f"{slug}.json").write_text(
                json.dumps(
                    redact({"catalog_record": record, "requested_year": year, "detail_response": detail}),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            year_notes.append({"requested_year": year, "description": description})
            print(
                f"{table_id[:12]} year={year}: rows={description['row_count']} "
                f"columns={len(description['columns'])} returned={description['tahun_data']}"
            )
        summaries.append(
            {
                "bps_table_id": table_id,
                "title": record.get("title"),
                "subject": record.get("subject"),
                "catalog_oldest_period": record.get("oldest_period"),
                "catalog_latest_period": record.get("latest_period"),
                "years": year_notes,
                "mapping_draft": mapping_draft(year_notes[0]["description"]),
            }
        )

    note = {
        "probed_at_utc": datetime.now(UTC).isoformat(),
        "tablesource": 3,
        "tables": summaries,
        "production_rule": (
            "Each kolom entry is a measure. Each data row is a dimension member, "
            "and geography when kode_wilayah is present. "
            "tahun_data is the period. value_raw is the source text. "
            "Empty variables and keterangan_data tokens are status, not zero. "
            "Dot-grouped integers with 0 decimal places are thousands separators."
        ),
    }
    (PROBE_OUT / "schema-notes.json").write_text(
        json.dumps(redact(note), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {(PROBE_OUT / 'schema-notes.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
