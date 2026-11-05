"""Validated evidence returned by Tilik-owned source MCP servers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BnpbRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: int
    year: int = Field(ge=2010, le=2024)
    geography_code: str | None = None
    geography_name: str | None = None
    disaster_type: str | None = None
    value: int | None = Field(default=None, ge=0)


class BnpbSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["BNPB"]
    title: str
    resource_id: str
    source_url: str
    retrieved_at: datetime
    license: str | None = None
    source_modified_at: str | None = None
    measure: Literal["events", "affected"]
    unit: str
    geography_level: Literal["national", "province", "district"]
    disaster_type: str | None = None
    missing_values: int = Field(ge=0)
    calculations: list[dict[str, Any]]
    rows: list[BnpbRow]


class BmkgForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["BMKG"]
    kind: Literal["forecast"]
    source_id: str
    source_url: str
    retrieved_at: datetime
    location: dict[str, Any]
    analysis_date: str | None = None
    slots: list[dict[str, Any]]


class BmkgEarthquakes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["BMKG"]
    kind: Literal["earthquakes"]
    feed: Literal["latest", "recent", "felt"]
    source_url: str
    retrieved_at: datetime
    events: list[dict[str, Any]]


class BmkgWarnings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["BMKG"]
    kind: Literal["warnings"]
    source_url: str
    retrieved_at: datetime
    warnings: list[dict[str, Any]]


RESULT_MODELS = {
    "bnpb_query_series": BnpbSeries,
    "bmkg_forecast": BmkgForecast,
    "bmkg_earthquakes": BmkgEarthquakes,
    "bmkg_active_warnings": BmkgWarnings,
}
