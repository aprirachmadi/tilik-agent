"""LangChain tool facade backed by the BPS MCP server."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from tilik_agent.connectors.mcp_client import call_bps


@tool("search_datasets")
def search_datasets(query: str) -> dict[str, Any]:
    """Find BPS datasets matching a topic, table title, or year; returns metadata, not values."""
    return call_bps("bps_search_datasets", {"query": query})


@tool("describe_dataset")
def describe_dataset(dataset_id: int) -> dict[str, Any]:
    """Inspect one BPS table's exact measures, periods, units, and dimension member IDs."""
    return call_bps("bps_describe_dataset", {"dataset_id": dataset_id})


@tool("get_observations")
def get_observations(
    dataset_id: int, measure_id: int, member_ids: list[int], period_labels: list[str]
) -> dict[str, Any]:
    """Retrieve bounded published BPS observations for exact dataset, measure, members, and periods."""
    return call_bps(
        "bps_get_observations",
        {"dataset_id": dataset_id, "measure_id": measure_id, "member_ids": member_ids, "period_labels": period_labels},
    )


@tool("select_series", response_format="content_and_artifact")
def select_series(
    dataset_id: int, measure_id: int, period_ids: list[int], member_ids: list[int]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a complete BPS province series, returning coverage and a selection ID, not values to the model."""
    response = call_bps(
        "bps_select_series",
        {"dataset_id": dataset_id, "measure_id": measure_id, "period_ids": period_ids, "member_ids": member_ids},
    )
    return response["summary"], response["artifact"]


SQL_TOOLS = [search_datasets, describe_dataset, get_observations, select_series]


def load_series(selection: Any) -> dict[str, dict[str, tuple[int, float, str]]]:
    """Return a validated panel without opening a BPS database connection in the agent."""
    raw = call_bps("bps_load_series", {"selection": selection.model_dump()})
    return {
        period: {code: (int(cell[0]), float(cell[1]), str(cell[2])) for code, cell in values.items()}
        for period, values in raw.items()
    }


def available_years(dataset_id: int) -> set[str]:
    return set(call_bps("bps_available_years", {"dataset_id": dataset_id}))
