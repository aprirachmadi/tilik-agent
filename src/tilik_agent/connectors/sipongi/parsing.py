"""Validate SiPongi hotspot rows and total them."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tilik_agent.connectors.provinces import bps_province_code

SATELLITES = {"NASA-MODIS", "NASA-NOAA20", "NASA-NOAA21"}
CONFIDENCES = {"High", "Medium", "Low"}
ALL_CONFIDENCES = "High, Medium, Low"


def counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        satellite, confidence, value = row.get("sumber"), row.get("confidence"), row.get("counter")
        if satellite not in SATELLITES or confidence not in CONFIDENCES:
            raise ValueError(f"Unexpected SiPongi satellite or confidence: {satellite!r}, {confidence!r}")
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError(f"Invalid SiPongi hotspot count: {value!r}")
        province = str(row["provinsi"]).strip()
        output.append({
            "province_name": province, "province_code": bps_province_code(province),
            "district": str(row.get("kabupaten") or "").strip() or None,
            "satellite": satellite, "confidence": confidence, "count": int(value),
        })
    return output


def _sum_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    sums: Counter[str] = Counter()
    for row in rows:
        sums[row[key]] += row["count"]
    return dict(sorted(sums.items()))


def totals(rows: list[dict[str, Any]], *, districts: bool) -> dict[str, Any]:
    """Hotspot totals overall and by province, satellite, and confidence (all from the given rows)."""
    by_province: Counter[str] = Counter()
    by_district: Counter[tuple[str, str]] = Counter()
    for row in rows:
        by_province[row["province_name"]] += row["count"]
        by_district[(row["province_name"], row["district"] or "")] += row["count"]
    result = {
        "total_hotspots": sum(row["count"] for row in rows),
        "by_satellite": _sum_by(rows, "satellite"),
        "by_confidence": _sum_by(rows, "confidence"),
        "by_province": [
            {"province_name": name, "province_code": bps_province_code(name), "hotspots": value}
            for name, value in by_province.most_common()
        ],
    }
    if districts:
        result["by_district"] = [
            {"province_name": province, "district": district, "hotspots": value}
            for (province, district), value in by_district.most_common(40)
        ]
    return result
