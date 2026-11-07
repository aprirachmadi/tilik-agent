"""Tilik-owned MCP server for Bapanas consumer food prices."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.config import get_settings
from tilik_agent.connectors.bapanas import client
from tilik_agent.connectors.bapanas.parsing import today_items
from tilik_agent.connectors.bapanas.queries import match_commodity, query_prices
from tilik_agent.connectors.provinces import same_province

mcp = FastMCP(
    "tilik-bapanas", host=os.getenv("TILIK_MCP_HOST", "127.0.0.1"), port=int(os.getenv("TILIK_MCP_PORT", "8000"))
)


@mcp.tool()
def bapanas_prices_today(commodity: str | None = None, province: str | None = None) -> dict[str, Any]:
    """Get the latest consumer price per commodity, nationally or for one province."""
    province_id = None
    if province:
        found = [key for key, name in client.provinces().items() if same_province(name, province)]
        if len(found) != 1:
            raise ValueError(f"Unknown Bapanas province: {province}")
        province_id = found[0]
    body, fetched, raw = client.today(province_id)
    items = today_items(body)
    if commodity:
        names = match_commodity(commodity, [item["commodity"] for item in items])
        if not names:
            raise ValueError(f"Unknown commodity; available: {[item['commodity'] for item in items]}")
        items = [item for item in items if item["commodity"] in names]
    return {
        "provider": "Bapanas", "kind": "prices_today",
        "title": "Harga pangan harian tingkat konsumen", "source_url": client.PAGE_URL,
        "retrieved_at": fetched, "geography": province or "Indonesia", "price_level": "consumer",
        "items": items,
        "_raw_payload": raw,
    }


@mcp.tool()
def bapanas_price_series(
    commodity: str,
    start_month: str,
    end_month: str,
    geography_level: str = "national",
    province: str | None = None,
) -> dict[str, Any]:
    """Read monthly average consumer prices (YYYY-MM, from 2021-06) nationally or by province."""
    engine = create_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            return query_prices(
                session, commodity=commodity, geography_level=geography_level, province=province,
                start_month=start_month, end_month=end_month,
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    mcp.run(transport=os.getenv("TILIK_MCP_TRANSPORT", "stdio"))
