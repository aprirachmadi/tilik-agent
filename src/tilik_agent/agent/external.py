"""Plan and execute narrow BNPB, BMKG, Bapanas, and SiPongi MCP queries."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from tilik_agent.agent.runtime import model_json
from tilik_agent.agent.schemas import State
from tilik_agent.connectors.contracts import RESULT_MODELS
from tilik_agent.connectors.mcp_client import call_connector


class ExternalCall(BaseModel):
    connector: Literal["bnpb", "bmkg", "bapanas", "sipongi"]
    tool: Literal[
        "bnpb_query_series", "bmkg_forecast", "bmkg_earthquakes", "bmkg_active_warnings",
        "bapanas_prices_today", "bapanas_price_series", "sipongi_recent_hotspots", "sipongi_hotspot_series",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExternalPlan(BaseModel):
    calls: list[ExternalCall] = Field(min_length=1, max_length=2)


_TOOLS = {
    "bnpb": {"bnpb_query_series"},
    "bmkg": {"bmkg_forecast", "bmkg_earthquakes", "bmkg_active_warnings"},
    "bapanas": {"bapanas_prices_today", "bapanas_price_series"},
    "sipongi": {"sipongi_recent_hotspots", "sipongi_hotspot_series"},
}
_SERIES_TOOLS = {"bnpb_query_series", "bapanas_price_series", "sipongi_hotspot_series"}


def _earthquake_feed_for(question: str) -> str | None:
    text = question.casefold()
    if "gempa" not in text:
        return None
    if "dirasakan" in text or "terasa" in text:
        return "felt"
    if "gempa terkini" in text or re.search(r"(?:magnitudo\s*5|\bm\s*5\s*\+)", text):
        return "recent"
    if "terbaru" in text:
        return "latest"
    return None


def _select_forecast_day(question: str, data: dict[str, Any]) -> None:
    if "besok" not in question.casefold():
        return
    retrieved = datetime.fromisoformat(data["retrieved_at"])
    target = (retrieved.astimezone(ZoneInfo("Asia/Jakarta")).date() + timedelta(days=1)).isoformat()
    data["slots"] = [slot for slot in data["slots"] if slot["local_datetime"][:10] == target]


def fetch_external(state: State) -> list[dict[str, Any]]:
    selected = set(state["intent"].get("connectors", [])) - {"bps"}
    prompt = f"""Select the minimum source calls for this question. Return ExternalPlan.
Only these sources are selected: {sorted(selected)}.
BNPB tool bnpb_query_series arguments: measure=events|affected,
geography_level=national|province|district, geography_name exact name if
province/district, disaster_type exact Indonesian type if national,
start_year/end_year between 2010 and 2024. Province and district tables count
all disaster types. National tables are by disaster type. Never ask for a
province-by-type combination. To compare two national disaster types, make one
call per type with the same years.
BMKG tools: bmkg_forecast(place) accepts Kemayoran or a verified adm4 code;
bmkg_earthquakes(feed=latest|recent|felt); bmkg_active_warnings(area optional).
Do not invent an adm4 code. If an unsupported location is requested, call
bmkg_forecast with its name so the connector reports the limitation.
Bapanas tools (consumer food prices): bapanas_prices_today(commodity optional,
province optional) for the latest daily price; bapanas_price_series(commodity,
start_month=YYYY-MM, end_month=YYYY-MM, geography_level=national|province,
province optional) for monthly average prices from 2021-06, at most 72 months.
Commodities include Beras Premium, Beras Medium, Cabai Rawit Merah, Cabai Merah
Keriting, Cabai Merah Besar, Bawang Merah, Bawang Putih Bonggol, Daging Sapi
Murni, Daging Ayam Ras, Telur Ayam Ras, Gula Konsumsi, Minyakita, Minyak Goreng
Kemasan, Tepung Terigu Kemasan, Ikan Kembung, Garam Konsumsi.
SiPongi tools (satellite hotspots, not confirmed fires):
sipongi_recent_hotspots(hours=12|24|48, province optional);
sipongi_hotspot_series(start_month=YYYY-MM, end_month=YYYY-MM,
geography_level=national|province|district, province required for district,
satellite=NASA-MODIS (default, the only series comparable across years) or all
(only within one calendar year), confidence=all|high|medium|low), from 2015-01.
Today in Jakarta is {datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()}.
Question: {state['question']}"""
    try:
        plan = model_json(ExternalPlan, prompt)
    except Exception as exc:
        return [{"id": "ext1", "provider": "Tilik", "error": f"Could not plan connector query: {exc}"}]
    evidence = []
    for index, call in enumerate(plan.calls, start=1):
        item: dict[str, Any] = {"id": f"ext{index}", "provider": call.connector.upper()}
        if call.connector not in selected or call.tool not in _TOOLS[call.connector]:
            item["error"] = "Connector call was not allowed by the selected source"
        else:
            try:
                arguments = dict(call.arguments)
                intent = state["intent"]
                if call.connector == "bnpb" and intent.get("analysis_operation"):
                    # Statistics must cover exactly the requested years, whatever range the planner chose.
                    for key in ("start_year", "end_year"):
                        if intent.get(key):
                            arguments[key] = intent[key]
                if call.connector == "bmkg" and call.tool == "bmkg_earthquakes":
                    requested_feed = _earthquake_feed_for(state["question"])
                    if requested_feed:
                        arguments["feed"] = requested_feed
                data = call_connector(call.connector, call.tool, arguments)
                if isinstance(data, dict) and "_raw_payload" in data:
                    item["snapshot_payload"] = data.pop("_raw_payload")
                data = RESULT_MODELS[call.tool].model_validate(data).model_dump(mode="json")
                if call.connector == "bmkg" and call.tool == "bmkg_forecast":
                    _select_forecast_day(state["question"], data)
                if call.tool in _SERIES_TOOLS and len(data.get("rows", [])) > 150:
                    item["error"] = "Query returned too many rows for a reliable answer; narrow the place or years"
                else:
                    item["data"] = data
            except (ValueError, RuntimeError, TimeoutError) as exc:
                item["error"] = str(exc)[:500]
        evidence.append(item)
    return evidence


def compatible_province_pairs(
    observations: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join same-year 2024 province cells only when BPS has a canonical code."""
    bps = {}
    for row in observations:
        if row.get("period") != "2024" or row.get("value") is None or row.get("status_code"):
            continue
        geography = [
            member for member in row.get("members", [])
            if member.get("dimension_type") == "geography"
        ]
        if len(geography) != 1:
            continue
        code = geography[0].get("canonical_code")
        if not isinstance(code, str) or len(code) != 7 or not code.endswith("00000"):
            continue
        bps.setdefault((code[:2], "2024"), []).append(row)
    pairs = []
    for item in evidence:
        data = item.get("data") or {}
        if data.get("provider") != "BNPB" or data.get("geography_level") != "province":
            continue
        for row in data.get("rows", []):
            if row.get("year") != 2024 or row.get("value") is None:
                continue
            candidates = bps.get((row.get("geography_code"), "2024"), [])
            if len(candidates) != 1:
                continue
            bps_row = candidates[0]
            pairs.append({
                "year": 2024, "province": row["geography_name"], "province_code": row["geography_code"],
                "bps_observation_id": bps_row["observation_id"], "bps_value": bps_row["value"],
                "bps_unit": bps_row.get("unit"), "bnpb_evidence_id": item["id"],
                "bnpb_events": row["value"],
            })
    return pairs
