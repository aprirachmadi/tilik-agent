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


class MonthlyRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    period_end: str
    complete_month: bool
    geography_name: str
    province_code: str | None = None
    value: int | None = Field(default=None, ge=0)


class YearRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int
    months: int = Field(ge=1, le=12)
    complete_year: bool
    geography_name: str
    province_code: str | None = None
    value: int = Field(ge=0)


class BapanasPriceRow(MonthlyRow):
    price_id: int
    reference_price: int | None = None
    reference_price_max: int | None = None
    status: str | None = None


class BapanasToday(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["Bapanas"]
    kind: Literal["prices_today"]
    title: str
    source_url: str
    retrieved_at: datetime
    geography: str
    price_level: Literal["consumer"]
    items: list[dict[str, Any]] = Field(min_length=1)


class BapanasSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["Bapanas"]
    kind: Literal["price_series"]
    title: str
    source_url: str
    retrieved_at: datetime
    commodity: str
    unit: str
    price_level: Literal["consumer"]
    geography_level: Literal["national", "province"]
    data_time: str
    missing_values: int = Field(ge=0)
    summaries: list[dict[str, Any]]
    rows: list[BapanasPriceRow]


class SipongiRecent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["SiPongi"]
    kind: Literal["recent_hotspots"]
    title: str
    source_url: str
    retrieved_at: datetime
    hours: Literal[12, 24, 48]
    geography: str
    satellite: Literal["all"]
    confidence: Literal["High, Medium, Low"]
    total_hotspots: int = Field(ge=0)
    by_satellite: dict[str, int]
    by_confidence: dict[str, int]
    by_province: list[dict[str, Any]]
    by_district: list[dict[str, Any]] | None = None


class SipongiSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["SiPongi"]
    kind: Literal["hotspot_series"]
    title: str
    source_url: str
    retrieved_at: datetime
    satellite: str
    confidence: str
    geography_level: Literal["national", "province", "district"]
    province: str | None = None
    unit: Literal["hotspots"]
    period: Literal["month", "year"]
    data_time: str | None = None
    missing_months: list[str]
    summaries: list[dict[str, Any]]
    rows: list[MonthlyRow] | list[YearRow]


RESULT_MODELS = {
    "bnpb_query_series": BnpbSeries,
    "bmkg_forecast": BmkgForecast,
    "bmkg_earthquakes": BmkgEarthquakes,
    "bmkg_active_warnings": BmkgWarnings,
    "bapanas_prices_today": BapanasToday,
    "bapanas_price_series": BapanasSeries,
    "sipongi_recent_hotspots": SipongiRecent,
    "sipongi_hotspot_series": SipongiSeries,
}
