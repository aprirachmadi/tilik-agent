"""Tilik-owned MCP server for synchronized BNPB disaster history."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.config import get_settings
from tilik_agent.connectors.bnpb.queries import query_series

mcp = FastMCP(
    "tilik-bnpb", host=os.getenv("TILIK_MCP_HOST", "127.0.0.1"), port=int(os.getenv("TILIK_MCP_PORT", "8000"))
)


@mcp.tool()
def bnpb_query_series(
    measure: str = "events",
    geography_level: str = "national",
    geography_name: str | None = None,
    disaster_type: str | None = None,
    start_year: int = 2010,
    end_year: int = 2024,
) -> dict[str, Any]:
    """Read 2010–2024 disaster events or affected people from approved BNPB aggregates."""
    engine = create_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            return query_series(
                session, measure=measure, geography_level=geography_level,
                geography_name=geography_name, disaster_type=disaster_type,
                start_year=start_year, end_year=end_year,
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    mcp.run(transport=os.getenv("TILIK_MCP_TRANSPORT", "stdio"))
