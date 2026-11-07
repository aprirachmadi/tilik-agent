"""Tilik-owned MCP server for SiPongi satellite hotspots."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.config import get_settings
from tilik_agent.connectors.provinces import same_province
from tilik_agent.connectors.sipongi import client
from tilik_agent.connectors.sipongi.parsing import ALL_CONFIDENCES, counts, totals
from tilik_agent.connectors.sipongi.queries import query_counts

mcp = FastMCP(
    "tilik-sipongi", host=os.getenv("TILIK_MCP_HOST", "127.0.0.1"), port=int(os.getenv("TILIK_MCP_PORT", "8000"))
)


@mcp.tool()
def sipongi_recent_hotspots(hours: int = 24, province: str | None = None) -> dict[str, Any]:
    """Get satellite hotspots detected in the last 12, 24, or 48 hours, nationally or for one province."""
    province_id = "all"
    if province:
        found = [key for name, key in client.provinces().items() if same_province(name, province)]
        if len(found) != 1:
            raise ValueError(f"Unknown SiPongi province: {province}")
        province_id = found[0]
    rows, fetched, raw = client.recent(hours, province_id)
    return {
        "provider": "SiPongi", "kind": "recent_hotspots", "title": "Sebaran titik panas terkini",
        "source_url": client.PAGE_URL, "retrieved_at": fetched, "hours": hours,
        "geography": province or "Indonesia", "satellite": "all", "confidence": ALL_CONFIDENCES,
        **totals(counts(rows), districts=province is not None),
        "_raw_payload": raw,
    }


@mcp.tool()
def sipongi_hotspot_series(
    start_month: str,
    end_month: str,
    geography_level: str = "national",
    province: str | None = None,
    satellite: str = "NASA-MODIS",
    confidence: str = "all",
) -> dict[str, Any]:
    """Read monthly hotspot counts (YYYY-MM, from 2015-01) nationally, by province, or by district."""
    engine = create_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            return query_counts(
                session, start_month=start_month, end_month=end_month, geography_level=geography_level,
                province=province, satellite=satellite, confidence=confidence,
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    mcp.run(transport=os.getenv("TILIK_MCP_TRANSPORT", "stdio"))
