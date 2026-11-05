"""Read-only MCP surface for Tilik's existing BPS data."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from tilik_agent.agent.schemas import SeriesSelection
from tilik_agent.agent.sql import describe_dataset, get_observations, search_datasets, select_series
from tilik_agent.agent.statistics import load_series
from tilik_agent.config import load_project_env

mcp = FastMCP("tilik-bps", host=os.getenv("TILIK_MCP_HOST", "127.0.0.1"), port=int(os.getenv("TILIK_MCP_PORT", "8000")))


@mcp.tool()
def bps_search_datasets(query: str) -> dict[str, Any]:
    """Find published BPS tables by topic, title, or year."""
    return search_datasets.invoke({"query": query})


@mcp.tool()
def bps_describe_dataset(dataset_id: int) -> dict[str, Any]:
    """List exact measures, periods, and dimension members for one BPS table."""
    return describe_dataset.invoke({"dataset_id": dataset_id})


@mcp.tool()
def bps_get_observations(
    dataset_id: int, measure_id: int, member_ids: list[int], period_labels: list[str]
) -> dict[str, Any]:
    """Get bounded published observations using exact IDs and period labels."""
    return get_observations.invoke(
        {"dataset_id": dataset_id, "measure_id": measure_id, "member_ids": member_ids, "period_labels": period_labels}
    )


@mcp.tool()
def bps_select_series(dataset_id: int, measure_id: int, period_ids: list[int], member_ids: list[int]) -> dict[str, Any]:
    """Validate a complete province series; return model summary and full selection metadata."""
    summary, artifact = select_series.func(dataset_id, measure_id, period_ids, member_ids)
    return {"summary": summary, "artifact": artifact}


@mcp.tool()
def bps_load_series(selection: dict[str, Any]) -> dict[str, Any]:
    """Load a previously validated province series for deterministic analysis."""
    validated = SeriesSelection.model_validate(selection)
    return load_series(validated)


@mcp.tool()
def bps_available_years(dataset_id: int) -> list[str]:
    """List available four-digit periods for validation of an analysis selection."""
    from tilik_agent.agent.sql import get_connection

    with get_connection() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT label FROM period WHERE dataset_id=%s AND label ~ '^[0-9]{4}$'", (dataset_id,)
            ).fetchall()
        ]


@mcp.tool()
def bps_search_publication(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search bounded passages in the existing BPS SP2020 publication collection."""
    from tilik_agent.agent.runtime import get_vector_store

    if not query.strip():
        return []
    hits = get_vector_store().similarity_search(query, k=max(1, min(limit, 10)))
    return [{"content": hit.page_content[:4000], "metadata": dict(hit.metadata or {})} for hit in hits]


def main() -> None:
    load_project_env()
    os.environ["TILIK_BPS_MCP_SERVER"] = "1"
    mcp.run(transport=os.getenv("TILIK_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
