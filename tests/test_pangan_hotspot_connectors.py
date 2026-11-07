"""Contract checks for the Bapanas price and SiPongi hotspot connectors (no network)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tilik_agent.connectors.bapanas.models import BapanasPrice, BapanasSnapshot
from tilik_agent.connectors.bapanas.parsing import _price, province_rows
from tilik_agent.connectors.bapanas.queries import query_prices
from tilik_agent.connectors.provinces import bps_province_code
from tilik_agent.connectors.sipongi import client as sipongi_client
from tilik_agent.connectors.sipongi import ingest as sipongi_ingest
from tilik_agent.connectors.sipongi.models import SipongiCount, SipongiSnapshot
from tilik_agent.connectors.sipongi.parsing import counts
from tilik_agent.connectors.sipongi.queries import query_counts


def _engine(*tables):
    engine = create_engine("sqlite:///:memory:")
    for table in tables:
        table.__table__.create(engine)
    return engine


def test_bapanas_prices_parse_indonesian_thousands_and_treat_zero_as_missing() -> None:
    assert _price("15.400") == 15400
    assert _price(16206) == 16206
    assert _price(0) is None
    with pytest.raises(ValueError):
        _price("15,4")


def test_papua_provinces_map_to_bps_codes_not_kemendagri_codes() -> None:
    # Both sources call Papua 91; BPS stores it as 9400000.
    assert bps_province_code("Papua") == "94"
    assert bps_province_code("Papua Barat") == "91"
    assert bps_province_code("D.I Yogyakarta") == bps_province_code("Daerah Istimewa Yogyakarta") == "34"
    assert bps_province_code("IKN") is None
    rows = province_rows({"detail": {"hargaratarata": 15451}, "data": [
        {"province_id": 34, "province_name": "Papua", "rata_rata_geometrik": 17307, "hpp_hap": "15.800",
         "status_map": "Waspada"},
    ]})
    assert rows[0] == {**rows[0], "geography_level": "national", "value": 15451}
    assert rows[1]["province_code"] == "94" and rows[1]["reference_price"] == 15800
    ranged = province_rows({"detail": {}, "data": [
        {"province_id": 20, "province_name": "Kalimantan Barat", "rata_rata_geometrik": 40000,
         "hpp_hap": "36.500 - 41.500", "status_map": "Aman"},
    ]})
    assert (ranged[1]["reference_price"], ranged[1]["reference_price_max"]) == (36500, 41500)


def test_sipongi_never_sends_an_empty_province_filter() -> None:
    # An empty `provinsi` silently returns Kalimantan Barat only.
    with pytest.raises(ValueError, match="provinsi=all"):
        sipongi_client._spread({"provinsi": ""}, ttl=0)


def test_sipongi_rejects_unknown_satellite() -> None:
    with pytest.raises(ValueError, match="satellite"):
        counts([{"provinsi": "Riau", "kabupaten": "SIAK", "sumber": "LAPAN", "confidence": "High", "counter": 1}])


def _sipongi_month(session: Session, month: date, rows: list[tuple[str, str, str, int]]) -> None:
    snapshot = SipongiSnapshot(month=month, period_end=month, complete=True, content_sha256=str(month))
    session.add(snapshot)
    session.flush()
    session.add_all(
        SipongiCount(snapshot_id=snapshot.id, province_name=province, province_code=bps_province_code(province),
                     district=district, satellite=satellite, confidence="High", count=value)
        for province, district, satellite, value in rows
    )
    session.commit()


def test_sipongi_series_fills_zero_months_and_blocks_cross_year_combined_satellites() -> None:
    engine = _engine(SipongiSnapshot, SipongiCount)
    with Session(engine) as session:
        _sipongi_month(session, date(2024, 1, 1), [
            ("Riau", "SIAK", "NASA-MODIS", 4), ("Riau", "SIAK", "NASA-NOAA20", 9),
        ])
        _sipongi_month(session, date(2024, 2, 1), [])
        series = query_counts(session, start_month="2024-01", end_month="2024-03")
        assert [(row["month"], row["value"]) for row in series["rows"]] == [("2024-01", 4), ("2024-02", 0)]
        assert series["missing_months"] == ["2024-03"]
        assert series["summaries"][0]["total"] == 4
        combined = query_counts(session, start_month="2024-01", end_month="2024-02", satellite="all")
        assert combined["rows"][0]["value"] == 13
        with pytest.raises(ValueError, match="NASA-MODIS"):
            query_counts(session, start_month="2023-12", end_month="2024-02", satellite="all")
        province = query_counts(session, start_month="2024-01", end_month="2024-02",
                                geography_level="province", province="riau")
        assert province["rows"][0]["province_code"] == "14"


def test_sipongi_sync_skips_complete_months_and_refetches_the_current_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sipongi_ingest, "HISTORY_START", date(2026, 10, 1))
    calls = []

    def between(start, end):
        calls.append((start, end))
        return [{"provinsi": "Riau", "kabupaten": "SIAK", "sumber": "NASA-MODIS", "confidence": "High",
                 "counter": len(calls)}], "2026-11-07T00:00:00+00:00"

    monkeypatch.setattr(sipongi_client, "between", between)
    engine = _engine(SipongiSnapshot, SipongiCount)
    with Session(engine) as session:
        sipongi_ingest.sync_sipongi(session, today=date(2026, 11, 7), pause=0)
        assert calls == [(date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 6))]
        sipongi_ingest.sync_sipongi(session, today=date(2026, 11, 8), pause=0)
        assert calls[2:] == [(date(2026, 11, 1), date(2026, 11, 7))]
        assert [snapshot.complete for snapshot in session.scalars(select(SipongiSnapshot))] == [True, False, False]


def test_bapanas_series_needs_one_commodity_and_summarizes_change() -> None:
    engine = _engine(BapanasSnapshot, BapanasPrice)
    with Session(engine) as session:
        for commodity_id, commodity in [(32, "Cabai Merah Keriting"), (33, "Cabai Rawit Merah")]:
            for month, value in [(date(2025, 1, 1), 50000), (date(2025, 2, 1), 45000)]:
                snapshot = BapanasSnapshot(commodity_id=commodity_id, commodity=commodity, unit="Rp/kg", month=month,
                                           period_end=month, complete=True, content_sha256=f"{commodity_id}{month}")
                session.add(snapshot)
                session.flush()
                session.add(BapanasPrice(snapshot_id=snapshot.id, geography_level="national", province_id=None,
                                         province_name="Indonesia", value=value, source_row={}))
        session.commit()
        with pytest.raises(ValueError, match="Choose one"):
            query_prices(session, commodity="cabai", start_month="2025-01", end_month="2025-02")
        series = query_prices(session, commodity="cabai rawit", start_month="2025-01", end_month="2025-02")
        assert series["summaries"][0] == {**series["summaries"][0], "difference": -5000, "percent_change": -10.0}
        assert series["data_time"] == "2025-01–2025-02"





def test_sipongi_sync_splits_a_month_the_server_cannot_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def between(start, end):
        if (end - start).days > 15:
            request = httpx.Request("GET", "https://opsroom.sipongidata.my.id/api/sebaran")
            raise httpx.HTTPStatusError("busy", request=request, response=httpx.Response(500, request=request))
        return [{"provinsi": "Riau", "kabupaten": "SIAK", "sumber": "NASA-MODIS", "confidence": "High",
                 "counter": 1}], ""

    monkeypatch.setattr(sipongi_client, "between", between)
    assert len(sipongi_ingest._rows(date(2026, 9, 1), date(2026, 9, 30))) == 2


def test_long_sipongi_series_returns_exact_yearly_totals() -> None:
    engine = _engine(SipongiSnapshot, SipongiCount)
    with Session(engine) as session:
        for year in (2019, 2020, 2021):
            for month in range(1, 13):
                _sipongi_month(session, date(year, month, 1), [("Riau", "SIAK", "NASA-MODIS", year - 2018)])
        series = query_counts(session, start_month="2019-01", end_month="2021-12")
        assert series["period"] == "year" and series["data_time"] == "2019–2021"
        assert [(row["year"], row["value"], row["complete_year"]) for row in series["rows"]] == [
            (2019, 12, True), (2020, 24, True), (2021, 36, True),
        ]
        assert series["summaries"][0]["first_year"] == 2019 and series["summaries"][0]["difference"] == 24


def test_connector_call_is_traced_as_a_named_tool_with_its_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.callbacks import BaseCallbackHandler

    from tilik_agent.agent.external import ExternalCall, ExternalPlan, fetch_external

    class Recorder(BaseCallbackHandler):
        calls: list[tuple[str, dict]] = []

        def on_tool_start(self, serialized, input_str, **kwargs):  # noqa: ANN001, ANN201
            self.calls.append((serialized["name"], kwargs["inputs"]))

    recorder = Recorder()
    monkeypatch.setattr("tilik_agent.agent.external.current_callbacks", lambda: [recorder])
    monkeypatch.setattr("tilik_agent.agent.external.model_json", lambda *_: ExternalPlan(calls=[ExternalCall(
        connector="sipongi", tool="sipongi_recent_hotspots", arguments={"hours": 24})]))
    monkeypatch.setattr("tilik_agent.agent.external.call_connector", lambda *_: {
        "provider": "SiPongi", "kind": "recent_hotspots", "title": "t", "source_url": "https://x",
        "retrieved_at": "2026-11-07T00:00:00+00:00", "hours": 24, "geography": "Indonesia", "satellite": "all",
        "confidence": "High, Medium, Low", "total_hotspots": 0, "by_satellite": {}, "by_confidence": {},
        "by_province": [],
    })
    evidence = fetch_external({"question": "Titik panas 24 jam?", "intent": {"connectors": ["sipongi"]}})
    assert "data" in evidence[0]
    assert recorder.calls == [("sipongi_recent_hotspots", {"hours": 24})]
