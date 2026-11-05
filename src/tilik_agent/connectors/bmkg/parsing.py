"""Normalize bounded BMKG JSON and CAP feeds with explicit timestamps."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

_CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"


def forecast(payload: bytes, adm4: str) -> dict[str, Any]:
    data = json.loads(payload)
    location = data["lokasi"]
    if location["adm4"] != adm4:
        raise ValueError("BMKG returned a different adm4 location")
    slots = [slot for group in data["data"] for day in group["cuaca"] for slot in day]
    return {
        "provider": "BMKG", "kind": "forecast", "source_id": adm4,
        "source_url": f"https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={adm4}",
        "location": {key: location.get(key) for key in ("provinsi", "kotkab", "kecamatan", "desa", "adm4")},
        "slots": [
            {"datetime": slot["datetime"], "local_datetime": slot["local_datetime"],
             "temperature_c": slot.get("t"), "humidity_pct": slot.get("hu"),
             "weather": slot.get("weather_desc"), "wind_kmh": slot.get("ws")}
            for slot in slots[:24]
        ],
        "analysis_date": slots[0].get("analysis_date") if slots else None,
    }


def earthquakes(payload: bytes, kind: str) -> dict[str, Any]:
    root = json.loads(payload)["Infogempa"]["gempa"]
    events = root if isinstance(root, list) else [root]
    if kind not in {"latest", "recent", "felt"}:
        raise ValueError("Unknown earthquake feed")
    return {
        "provider": "BMKG", "kind": "earthquakes", "feed": kind,
        "source_url": "https://data.bmkg.go.id/DataMKG/TEWS/" +
        {"latest": "autogempa.json", "recent": "gempaterkini.json", "felt": "gempadirasakan.json"}[kind],
        "events": [
            {"datetime": item["DateTime"], "magnitude": float(item["Magnitude"]),
             "depth": item.get("Kedalaman"), "location": item.get("Wilayah"),
             "coordinates": item.get("Coordinates"), "tsunami_note": item.get("Potensi"),
             "felt": item.get("Dirasakan")}
            for item in events[:15]
        ],
    }


def warning_links(payload: bytes) -> list[str]:
    root = ET.fromstring(payload)
    links = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        if re.fullmatch(r"https://www\.bmkg\.go\.id/alerts/nowcast/id/[A-Za-z0-9_.-]+_alert\.xml", link):
            links.append(link)
    if len(links) > 40:
        raise ValueError("BMKG warning feed exceeds the 40-detail request budget")
    return links


def warning(payload: bytes, source_url: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    root = ET.fromstring(payload)
    info = root.find(f"{_CAP}info")
    if info is None:
        raise ValueError("CAP warning has no info block")
    expires_raw = info.findtext(f"{_CAP}expires")
    if not expires_raw:
        raise ValueError("CAP warning has no expiry")
    expires = datetime.fromisoformat(expires_raw)
    if expires.tzinfo is None:
        raise ValueError("CAP warning expiry has no time zone")
    if expires <= (now or datetime.now(UTC)):
        return None
    return {
        "provider": "BMKG", "kind": "warning", "source_url": source_url,
        "source_id": root.findtext(f"{_CAP}identifier"),
        "sent": root.findtext(f"{_CAP}sent"), "event": info.findtext(f"{_CAP}event"),
        "effective": info.findtext(f"{_CAP}effective"), "expires": expires_raw,
        "severity": info.findtext(f"{_CAP}severity"), "certainty": info.findtext(f"{_CAP}certainty"),
        "headline": info.findtext(f"{_CAP}headline"),
        "description": (info.findtext(f"{_CAP}description") or "")[:1500],
        "areas": [area.findtext(f"{_CAP}areaDesc") or "" for area in info.findall(f"{_CAP}area")],
    }
