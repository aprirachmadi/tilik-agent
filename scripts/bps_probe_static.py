#!/usr/bin/env python3
"""Download a few BPS static tables and record how their Excel sheets are shaped.

This script is a format probe. It is not the production loader.
The API key is loaded only in memory and redacted before anything is written.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from bps_recon_download import OUT as RECON_OUT
from bps_recon_download import ROOT, api_request, download_bps_file, redact

PROBE_OUT = ROOT / "data" / "bps" / "probes" / "static"
CATALOG = ROOT / "data" / "bps" / "recon-2026-09-11" / "tables.json"
# One table each from politics, population, housing, and education.
TABLE_IDS = ("MTU3MyMx", "MjIxNSMx", "MTUzNyMx", "MTYwOSMx")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
PREVIEW_ROWS = 18
PREVIEW_COLS = 14


class _TableTextParser(HTMLParser):
    """Collect visible text from each HTML table row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
        elif tag == "tr":
            self._row = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._row.append(text)
            self._in_cell = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def catalog_by_id() -> dict[str, dict[str, Any]]:
    tables = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {str(row["id"]): row for row in tables if isinstance(row, dict)}


def html_preview(escaped_html: str) -> dict[str, Any]:
    from html import unescape

    parser = _TableTextParser()
    parser.feed(unescape(escaped_html))
    preview = [row[:PREVIEW_COLS] for row in parser.rows[:PREVIEW_ROWS]]
    return {"row_count": len(parser.rows), "preview": preview}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_xls(path: Path) -> list[dict[str, Any]]:
    import xlrd

    book = xlrd.open_workbook(str(path), formatting_info=False)
    sheets: list[dict[str, Any]] = []
    for sheet in book.sheets():
        preview: list[list[str]] = []
        for row_index in range(min(sheet.nrows, PREVIEW_ROWS)):
            preview.append(
                [_cell_text(sheet.cell_value(row_index, col)) for col in range(min(sheet.ncols, PREVIEW_COLS))]
            )
        sheets.append(
            {
                "name": sheet.name,
                "nrows": sheet.nrows,
                "ncols": sheet.ncols,
                "merged_cells": [],
                "merged_cell_note": "BIFF .xls merged ranges are taken from the HTML preview, not from xlrd.",
                "preview": preview,
            }
        )
    return sheets


def _read_xlsx(path: Path) -> list[dict[str, Any]]:
    import openpyxl

    book = openpyxl.load_workbook(path, data_only=True)
    sheets: list[dict[str, Any]] = []
    for sheet in book.worksheets:
        preview: list[list[str]] = []
        max_row = min(sheet.max_row or 1, PREVIEW_ROWS)
        max_col = min(sheet.max_column or 1, PREVIEW_COLS)
        for row in sheet.iter_rows(min_row=1, max_row=max_row, max_col=max_col, values_only=True):
            preview.append([_cell_text(value) for value in row])
        merged = [str(item) for item in list(sheet.merged_cells.ranges)[:40]]
        sheets.append(
            {
                "name": sheet.title,
                "nrows": sheet.max_row,
                "ncols": sheet.max_column,
                "merged_cells": merged,
                "preview": preview,
            }
        )
    book.close()
    return sheets


def read_workbook(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".xlsx" or path.read_bytes()[:2] == b"PK":
        return _read_xlsx(path)
    return _read_xls(path)


def draft_mapping(sheet: dict[str, Any]) -> list[dict[str, str]]:
    """Draft observation roles from the preview. Ambiguous headers stay unresolved."""
    preview: list[list[str]] = sheet["preview"]
    notes: list[dict[str, str]] = [
        {
            "field": "title and note rows above the grid",
            "role": "dataset note, not an observation",
            "reason": "Static sheets start with a title, unit, or source line before the rectangular grid.",
        }
    ]
    year_cells = [text.replace(".0", "") for row in preview for text in row if YEAR_RE.match(text.replace(".0", ""))]
    if year_cells:
        notes.append(
            {
                "field": "header cells matching 19xx/20xx",
                "role": "period",
                "reason": f"Preview contains year labels such as {', '.join(year_cells[:8])}.",
            }
        )
    else:
        notes.append(
            {
                "field": "column headers",
                "role": "needs_review",
                "reason": "No 4-digit year header was in the preview, so columns are not treated as periods.",
            }
        )
    if any(row and row[0] for row in preview):
        notes.append(
            {
                "field": "first populated column",
                "role": "dimension_member",
                "reason": (
                    "The stub column is the row dimension. Its role stays generic_category "
                    "unless the header names sex, geography, or another known axis."
                ),
            }
        )
    if sheet.get("merged_cells"):
        notes.append(
            {
                "field": "merged header cells",
                "role": "measure when one label spans year columns; otherwise needs_review",
                "reason": "A merged label may be a measure or a dimension. Keep the raw file when both are stacked.",
            }
        )
    return notes


def move_download(meta: dict[str, Any]) -> Path:
    source = RECON_OUT / str(meta["filename"])
    target = PROBE_OUT / source.name
    target.write_bytes(source.read_bytes())
    source.unlink(missing_ok=True)
    return target


def main() -> None:
    PROBE_OUT.mkdir(parents=True, exist_ok=True)
    RECON_OUT.mkdir(parents=True, exist_ok=True)
    catalog = catalog_by_id()
    missing = [table_id for table_id in TABLE_IDS if table_id not in catalog]
    if missing:
        raise SystemExit(f"Catalog is missing static table ids: {', '.join(missing)}")

    summaries: list[dict[str, Any]] = []
    for table_id in TABLE_IDS:
        record = catalog[table_id]
        detail = api_request(
            "view",
            [("model", "tablestatistic"), ("domain", "0000"), ("id", table_id), ("lang", "ind")],
        )
        data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
        excel_url = str(data.get("excel") or "")
        sheets: list[dict[str, Any]] = []
        workbook_name: str | None = None
        excel_bytes = None
        excel_sha256 = None
        if excel_url:
            meta = download_bps_file(excel_url, f"static-{table_id}")
            workbook = move_download(meta)
            workbook_name = workbook.name
            excel_bytes = meta.get("bytes")
            excel_sha256 = meta.get("sha256")
            sheets = read_workbook(workbook)
        html = html_preview(str(data.get("table") or "")) if data else {"row_count": 0, "preview": []}
        mapping = draft_mapping(sheets[0]) if sheets else [
            {"field": "detail.excel", "role": "raw", "reason": "The detail response did not include an Excel URL."}
        ]
        summary = {
            "bps_table_id": table_id,
            "title": record.get("title"),
            "subject": record.get("subject"),
            "detail_keys": sorted(data.keys()) if isinstance(data, dict) else [],
            "excel_bytes": excel_bytes,
            "excel_sha256": excel_sha256,
            "workbook_file": workbook_name,
            "html_row_count": html["row_count"],
            "html_preview": html["preview"],
            "sheets": sheets,
            "mapping_draft": mapping,
        }
        summaries.append(summary)
        payload = {"catalog_record": record, "detail_response": detail, "summary": summary}
        (PROBE_OUT / f"{table_id}.json").write_text(
            json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{table_id}: html_rows={html['row_count']} sheets={len(sheets)} file={workbook_name}")

    note = {
        "probed_at_utc": datetime.now(UTC).isoformat(),
        "tablesource": 1,
        "tables": [
            {
                "bps_table_id": item["bps_table_id"],
                "title": item["title"],
                "workbook_file": item["workbook_file"],
                "sheet_shapes": [
                    {
                        "name": sheet["name"],
                        "nrows": sheet["nrows"],
                        "ncols": sheet["ncols"],
                    }
                    for sheet in item["sheets"]
                ],
                "mapping_draft": item["mapping_draft"],
            }
            for item in summaries
        ],
        "production_rule": (
            "Parse the official Excel, not the HTML string. A column whose header is a 4-digit year is a period. "
            "The first stub column is a dimension. A single label spanning those year columns is the measure. "
            "Stacked non-year headers stay needs_review, and the raw workbook is kept."
        ),
    }
    (PROBE_OUT / "schema-notes.json").write_text(
        json.dumps(redact(note), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {(PROBE_OUT / 'schema-notes.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
