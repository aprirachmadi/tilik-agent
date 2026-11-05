"""Tilik-owned MCP server for BMKG's official live feeds."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from tilik_agent.connectors.bmkg.client import fetch, fetched_at
from tilik_agent.connectors.bmkg.parsing import earthquakes, forecast, warning, warning_links

mcp = FastMCP(
    "tilik-bmkg", host=os.getenv("TILIK_MCP_HOST", "127.0.0.1"), port=int(os.getenv("TILIK_MCP_PORT", "8000"))
)
_PLACES = {"kemayoran": "31.71.03.1001"}


@mcp.tool()
def bmkg_forecast(place: str) -> dict[str, Any]:
    """Get a three-day village forecast; currently supports Kemayoran or an exact adm4 code."""
    adm4 = _PLACES.get(place.strip().casefold(), place.strip())
    if not __import__("re").fullmatch(r"\d{2}\.\d{2}\.\d{2}\.\d{4}", adm4):
        raise ValueError("Unknown village; provide a validated adm4 code (Kemayoran is supported)")
    url = f"https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={adm4}"
    payload = fetch(url, ttl=1800)
    result = forecast(payload, adm4)
    result["retrieved_at"] = fetched_at(url)
    result["_raw_payload"] = {"url": url, "body": payload.decode("utf-8")}
    return result


@mcp.tool()
def bmkg_earthquakes(feed: str = "latest") -> dict[str, Any]:
    """Get the latest, recent M5+, or recently felt BMKG earthquake reports."""
    if feed not in {"latest", "recent", "felt"}:
        raise ValueError("feed must be latest, recent, or felt")
    filename = {"latest": "autogempa.json", "recent": "gempaterkini.json", "felt": "gempadirasakan.json"}[feed]
    url = f"https://data.bmkg.go.id/DataMKG/TEWS/{filename}"
    payload = fetch(url, ttl=120)
    result = earthquakes(payload, feed)
    result["retrieved_at"] = fetched_at(url)
    result["_raw_payload"] = {"url": url, "body": payload.decode("utf-8")}
    return result


@mcp.tool()
def bmkg_active_warnings(area: str | None = None) -> dict[str, Any]:
    """Get active CAP weather warnings, optionally filtering by named affected area."""
    feed_url = "https://www.bmkg.go.id/alerts/nowcast/id"
    feed_payload = fetch(feed_url, ttl=120)
    links = warning_links(feed_payload)
    cap_payloads = [(link, fetch(link, ttl=120)) for link in links]
    active = [warning(payload, link) for link, payload in cap_payloads]
    warnings = [item for item in active if item is not None]
    if area:
        wanted = area.casefold()
        warnings = [
            item for item in warnings
            if wanted in " ".join([*item["areas"], item["headline"] or "", item["description"]]).casefold()
        ]
    cited_links = {item["source_url"] for item in warnings}
    return {
        "provider": "BMKG", "kind": "warnings", "source_url": feed_url,
        "retrieved_at": fetched_at(feed_url), "warnings": warnings,
        "_raw_payload": {
            "feed": feed_payload.decode("utf-8"),
            "cap": [
                {"url": link, "body": payload.decode("utf-8")}
                for link, payload in cap_payloads if link in cited_links
            ],
        },
    }


if __name__ == "__main__":
    mcp.run(transport=os.getenv("TILIK_MCP_TRANSPORT", "stdio"))
