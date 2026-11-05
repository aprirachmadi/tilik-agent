"""Validate and import BNPB's published yearly aggregate rows."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tilik_agent.connectors.bnpb.client import RESOURCES, fetch_tables
from tilik_agent.connectors.bnpb.models import BnpbFact, BnpbSnapshot

_YEAR = re.compile(r"^(20(?:1[0-9]|2[0-4]))(?:\.0)?$")


def _code(value: Any) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        raise ValueError(f"Invalid BNPB geography code: {value!r}")
    return text


def parse_rows(kind: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = {"province_events": 38, "district_events": 510, "type_events": 9, "type_affected": 9}
    if kind not in expected or len(rows) != expected[kind]:
        raise ValueError(f"Unexpected BNPB row count for {kind}: {len(rows)}")
    output = []
    seen = set()
    for row in rows:
        row_id = int(row["_id"])
        if kind == "province_events":
            level, code, name = "province", _code(row["Kode Wilayah Provinsi"]), str(row["Nama Provinsi"]).strip()
            disaster_type = None
        elif kind == "district_events":
            level = "district"
            code = _code(row["Kode Wilayah Kabupaten / Kota"])
            name = str(row["Nama Kabupaten/Kota"]).strip()
            disaster_type = None
        else:
            level, code, name = "national", None, "Indonesia"
            disaster_type = str(row["JENIS BENCANA"]).strip()
        measure = "affected" if kind == "type_affected" else "events"
        years = {int(match.group(1)): value for key, value in row.items() if (match := _YEAR.fullmatch(key))}
        if set(years) != set(range(2010, 2025)):
            raise ValueError(f"BNPB row {row_id} has incomplete year coverage")
        for year, value in years.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)
                                      or int(value) != value or value < 0):
                raise ValueError(f"Invalid BNPB value for row {row_id}, {year}: {value!r}")
            key = (row_id, year)
            if key in seen:
                raise ValueError(f"Duplicate BNPB fact: {key}")
            seen.add(key)
            output.append(
                dict(source_row_id=row_id, measure=measure, geography_level=level, geography_code=code,
                     geography_name=name, disaster_type=disaster_type, year=year,
                     value=int(value) if value is not None else None, source_row=row)
            )
    return output


def sync_bnpb(session: Session) -> dict[str, int]:
    """Import each changed resource as one complete, immutable snapshot."""
    counts = {}
    for kind, (metadata, rows) in fetch_tables().items():
        resource_id = RESOURCES[kind]
        rows = sorted(rows, key=lambda row: int(row["_id"]))
        checksum = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = session.scalar(
            select(BnpbSnapshot).where(BnpbSnapshot.resource_id == resource_id, BnpbSnapshot.content_sha256 == checksum)
        )
        if existing:
            counts[kind] = 0
            continue
        facts = parse_rows(kind, rows)
        snapshot = BnpbSnapshot(
            resource_id=resource_id, title=str(metadata.get("name") or kind), content_sha256=checksum,
            license_id=metadata.get("package_license"),
            source_modified_at=metadata.get("last_modified"),
        )
        session.add(snapshot)
        session.flush()
        session.add_all(BnpbFact(snapshot_id=snapshot.id, **fact) for fact in facts)
        session.commit()
        counts[kind] = len(facts)
    return counts
