"""Contract checks for source parsing and guarded cross-source evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.agent.answer import (
    _check_bmkg_numbers,
    _check_bnpb_numbers,
    _external_prompt_evidence,
    build_chart,
    chart_needed,
    write_answer,
)
from tilik_agent.agent.external import ExternalCall, ExternalPlan, compatible_province_pairs, fetch_external
from tilik_agent.agent.runner import QuestionResult
from tilik_agent.agent.schemas import AnswerDraft
from tilik_agent.agent.workers import route_sources
from tilik_agent.chat.models import Conversation, Message
from tilik_agent.connectors import mcp_client
from tilik_agent.connectors.bmkg.parsing import warning
from tilik_agent.connectors.bnpb.ingest import parse_rows
from tilik_agent.connectors.bnpb.models import BnpbFact, BnpbSnapshot
from tilik_agent.connectors.bnpb.queries import query_series
from tilik_agent.connectors.snapshots import ExternalSourceSnapshot


def test_bnpb_import_keeps_missing_values_distinct_from_zero() -> None:
    rows = [
        {
            "_id": index,
            "Kode Wilayah Provinsi": index,
            "Nama Provinsi": f"Province {index}",
            **{f"{year}.0": (None if index == 1 and year == 2016 else 0) for year in range(2010, 2025)},
        }
        for index in range(1, 39)
    ]
    facts = parse_rows("province_events", rows)
    assert len(facts) == 570
    assert next(fact for fact in facts if fact["source_row_id"] == 1 and fact["year"] == 2016)["value"] is None
    assert next(fact for fact in facts if fact["source_row_id"] == 1 and fact["year"] == 2017)["value"] == 0


def test_cap_expiry_changes_active_status() -> None:
    xml = b'''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
      <identifier>x</identifier><sent>2026-11-03T12:00:00+07:00</sent>
      <info><event>Heavy rain</event><expires>2026-11-03T14:00:00+07:00</expires>
      <area><areaDesc>Jakarta</areaDesc></area></info></alert>'''
    url = "https://www.bmkg.go.id/alerts/nowcast/id/x_alert.xml"
    assert warning(xml, url, now=datetime.fromisoformat("2026-11-03T13:00:00+07:00"))["source_id"] == "x"
    assert warning(xml, url, now=datetime.fromisoformat("2026-11-03T14:00:00+07:00")) is None


def test_cross_source_pair_requires_unique_2024_canonical_province() -> None:
    row = {
        "observation_id": 7, "period": "2024", "value": 3.5, "unit": "%",
        "members": [{"dimension_type": "geography", "canonical_code": "1100000"}],
    }
    evidence = [{"id": "ext1", "data": {
        "provider": "BNPB", "geography_level": "province",
        "rows": [{"year": 2024, "geography_code": "11", "geography_name": "ACEH", "value": 99}],
    }}]
    assert compatible_province_pairs([row], evidence)[0]["bnpb_events"] == 99
    assert compatible_province_pairs([row, {**row, "observation_id": 8}], evidence) == []
    assert compatible_province_pairs([{**row, "period": "2023"}], evidence) == []


def test_bnpb_parser_rejects_incomplete_years() -> None:
    rows = [{"_id": index, "JENIS BENCANA": f"Type {index}", "2010.0": 1} for index in range(1, 10)]
    with pytest.raises(ValueError, match="incomplete year coverage"):
        parse_rows("type_events", rows)


def test_bnpb_chart_uses_exact_cited_fact_values() -> None:
    state = {
        "question": "Tunjukkan grafik banjir",
        "requested_chart": True,
        "answer_external_ids": ["ext1"],
        "external_evidence": [{"id": "ext1", "data": {
            "provider": "BNPB", "title": "Flood events", "measure": "events", "unit": "events",
            "rows": [
                {"fact_id": 1, "year": 2023, "geography_name": "Indonesia", "disaster_type": "BANJIR", "value": 1255},
                {"fact_id": 2, "year": 2024, "geography_name": "Indonesia", "disaster_type": "BANJIR", "value": 1420},
            ],
        }}],
    }
    assert chart_needed(state) == "yes"
    chart = build_chart(state)["chart"]
    assert chart["type"] == "line"
    assert [point["value"] for point in chart["data"]] == [1255, 1420]


def test_bnpb_answer_numbers_must_match_values_or_calculations() -> None:
    state = {"external_evidence": [{"id": "ext1", "data": {
        "provider": "BNPB", "rows": [
            {"year": 2023, "value": 1255}, {"year": 2024, "value": 1420},
        ],
        "calculations": [{"difference": 165, "percent_change": 13.15}],
    }}]}
    _check_bnpb_numbers("Banjir naik dari 1.255 ke 1.420 pada 2024, bertambah 165 (13,15%).", state, ["ext1"])
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bnpb_numbers("Banjir mencapai 1.421 pada 2024.", state, ["ext1"])



def test_bmkg_answer_may_give_source_times_in_jakarta_time() -> None:
    state = {"external_evidence": [{"id": "ext1", "data": {
        "provider": "BMKG", "retrieved_at": "2026-11-07T13:28:01+00:00",
        "events": [{"datetime": "2026-11-07T10:05:52+00:00", "magnitude": 3.0, "depth": "10 km"}],
    }}]}
    _check_bmkg_numbers(
        "Diambil 7 November 2026 pukul 20.28 WIB; gempa M3,0 pukul 17.05.52 WIB, kedalaman 10 km.", state, ["ext1"]
    )
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bmkg_numbers("Gempa terjadi pukul 18.05 WIB.", state, ["ext1"])

@pytest.mark.parametrize(
    ("answer_text", "rows", "calculations"),
    [
        ("Pada 2024, 5.866.011 orang terdampak banjir.", [{"year": 2024, "value": 5866011}], []),
        (
            "Banjir naik dari 1.255 pada 2023 menjadi 1.420 pada 2024, selisih 165.",
            [{"year": 2023, "value": 1255}, {"year": 2024, "value": 1420}],
            [{"difference": 165, "percent_change": 13.15}],
        ),
    ],
)
def test_single_external_source_assigns_provenance_without_model_ids(
    monkeypatch: pytest.MonkeyPatch, answer_text: str,
    rows: list[dict[str, int]], calculations: list[dict[str, float]],
) -> None:
    calls = 0

    def draft(*_: object) -> AnswerDraft:
        nonlocal calls
        calls += 1
        return AnswerDraft(
            text=answer_text, observation_ids=[8235], document_ids=["missing"], external_ids=["wrong"]
        )

    monkeypatch.setattr("tilik_agent.agent.answer.model_json", draft)
    result = write_answer({
        "question": "BNPB?", "external_evidence": [
            {"id": "ext1", "data": {"provider": "BNPB", "rows": rows, "calculations": calculations}}
        ],
    })
    assert result["answer"] == answer_text
    assert result["answer_observation_ids"] == []
    assert result["answer_document_ids"] == []
    assert result["answer_external_ids"] == ["ext1"]
    assert calls == 1


def test_single_external_source_still_rejects_unsupported_number(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def draft(*_: object) -> AnswerDraft:
        nonlocal calls
        calls += 1
        return AnswerDraft(text="Pada 2024 ada 999 korban.", external_ids=["wrong"])

    monkeypatch.setattr("tilik_agent.agent.answer.model_json", draft)
    result = write_answer({
        "question": "BNPB?", "external_evidence": [
            {"id": "ext1", "data": {"provider": "BNPB", "rows": [{"year": 2024, "value": 5}]}}
        ],
    })
    assert "Unsupported numeric claim: 999" in result["answer"]
    assert calls == 1


def test_multiple_external_sources_require_valid_model_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.answer.model_json",
        lambda *_: AnswerDraft(text="Ada data BNPB dan BMKG.", external_ids=["wrong"]),
    )
    result = write_answer({
        "question": "Bandingkan sumber",
        "external_evidence": [
            {"id": "ext1", "data": {"provider": "BNPB", "rows": []}},
            {"id": "ext2", "data": {"provider": "BMKG", "events": []}},
        ],
    })
    assert "unknown evidence IDs" in result["answer"]


def test_bmkg_magnitude_must_match_cited_event() -> None:
    state = {"external_evidence": [{"id": "ext1", "data": {
        "provider": "BMKG", "events": [{"datetime": "2026-11-03T09:00:00+00:00", "magnitude": 5.2}],
    }}]}
    _check_bmkg_numbers("Gempa M 5,2 terjadi pada 2026.", state, ["ext1"])
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bmkg_numbers("Gempa M 6,2 terjadi pada 2026.", state, ["ext1"])
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bmkg_numbers("Gempa M6,2 terjadi pada 2026.", state, ["ext1"])


def test_bmkg_guard_accepts_hour_in_iso_event_timestamp() -> None:
    state = {"external_evidence": [{"id": "ext1", "data": {
        "provider": "BMKG", "events": [{"datetime": "2026-11-03T15:37:50+00:00", "magnitude": 5.2}],
    }}]}
    _check_bmkg_numbers("Gempa M5,2 terjadi pukul 15:37:50 UTC pada 2026.", state, ["ext1"])


def test_bmkg_numeric_guard_handles_dotted_source_dates() -> None:
    state = {"external_evidence": [{"id": "ext1", "data": {
        "provider": "BMKG", "events": [{
            "local_datetime": "03.11.2026 16:00 WIB", "magnitude": 5.2,
        }],
    }}]}
    _check_bmkg_numbers("Gempa M 5,2 pada 03.11.2026 pukul 16:00 WIB.", state, ["ext1"])
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bmkg_numbers("Gempa M 6,2 pada 03.11.2026.", state, ["ext1"])


def test_mixed_bnpb_bmkg_numbers_can_use_both_cited_sources() -> None:
    state = {"external_evidence": [
        {"id": "ext1", "data": {"provider": "BNPB", "rows": [{"year": 2024, "value": 1420}]}},
        {"id": "ext2", "data": {"provider": "BMKG", "events": [
            {"datetime": "2026-11-03T09:00:00+00:00", "magnitude": 5.2}
        ]}},
    ]}
    answer = "BNPB mencatat 1.420 pada 2024; BMKG melaporkan M 5,2 pada 2026."
    _check_bnpb_numbers(answer, state, ["ext1", "ext2"])
    _check_bmkg_numbers(answer, state, ["ext1", "ext2"])


def test_external_fetch_keeps_raw_snapshot_out_of_answer_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.external.model_json",
        lambda *_: ExternalPlan(calls=[ExternalCall(connector="bmkg", tool="bmkg_earthquakes", arguments={})]),
    )
    monkeypatch.setattr(
        "tilik_agent.agent.external.call_connector",
        lambda *_: {
            "provider": "BMKG", "kind": "earthquakes", "feed": "latest",
            "source_url": "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json",
            "retrieved_at": "2026-11-03T09:00:00+00:00", "events": [],
            "_raw_payload": {"body": "original"},
        },
    )
    evidence = fetch_external({"question": "Gempa terbaru?", "intent": {"connectors": ["bmkg"]}})
    assert evidence[0]["snapshot_payload"] == {"body": "original"}
    assert "_raw_payload" not in evidence[0]["data"]


@pytest.mark.parametrize(
    ("question", "expected_feed"),
    [
        ("Apa gempa bermagnitudo 5 atau lebih dalam daftar gempa terkini BMKG?", "recent"),
        ("Apa gempa terbaru yang dirasakan menurut BMKG?", "felt"),
    ],
)
def test_bmkg_earthquake_feed_matches_question_even_if_model_selects_latest(
    monkeypatch: pytest.MonkeyPatch, question: str, expected_feed: str,
) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.external.model_json",
        lambda *_: ExternalPlan(calls=[ExternalCall(
            connector="bmkg", tool="bmkg_earthquakes", arguments={"feed": "latest"}
        )]),
    )
    called: list[dict[str, object]] = []

    def connector(_server: str, _tool: str, arguments: dict[str, object]) -> dict[str, object]:
        called.append(arguments)
        return {
            "provider": "BMKG", "kind": "earthquakes", "feed": arguments["feed"],
            "source_url": "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json",
            "retrieved_at": "2026-11-04T00:00:00+00:00", "events": [],
        }

    monkeypatch.setattr("tilik_agent.agent.external.call_connector", connector)
    evidence = fetch_external({"question": question, "intent": {"connectors": ["bmkg"]}})
    assert called == [{"feed": expected_feed}]
    assert evidence[0]["data"]["feed"] == expected_feed


def test_bmkg_tomorrow_uses_jakarta_calendar_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.external.model_json",
        lambda *_: ExternalPlan(calls=[ExternalCall(
            connector="bmkg", tool="bmkg_forecast", arguments={"place": "Kemayoran"}
        )]),
    )
    monkeypatch.setattr(
        "tilik_agent.agent.external.call_connector",
        lambda *_: {
            "provider": "BMKG", "kind": "forecast", "source_id": "31.71.03.1001",
            "source_url": "https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=31.71.03.1001",
            "retrieved_at": "2026-11-03T17:35:00+00:00", "location": {},
            "slots": [
                {"local_datetime": "2026-11-04 09:00:00", "temperature_c": 32},
                {"local_datetime": "2026-11-05 09:00:00", "temperature_c": 30},
            ],
        },
    )
    evidence = fetch_external({"question": "Cuaca Kemayoran besok?", "intent": {"connectors": ["bmkg"]}})
    assert evidence[0]["data"]["slots"] == [
        {"local_datetime": "2026-11-05 09:00:00", "temperature_c": 30}
    ]


def test_bmkg_prompt_includes_jakarta_retrieval_time() -> None:
    evidence = _external_prompt_evidence({"external_evidence": [
        {"id": "ext1", "data": {"provider": "BMKG", "retrieved_at": "2026-11-03T17:35:00Z"}}
    ]})
    assert evidence[0]["retrieved_at_jakarta"] == "2026-11-04T00:35:00+07:00"


def test_direct_followup_does_not_fetch_a_selected_external_source() -> None:
    assert route_sources({"source_route": "direct", "intent": {"connectors": ["bmkg"]}}) == "direct"


def test_malformed_mcp_evidence_is_not_passed_to_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.external.model_json",
        lambda *_: ExternalPlan(calls=[ExternalCall(connector="bmkg", tool="bmkg_earthquakes", arguments={})]),
    )
    monkeypatch.setattr("tilik_agent.agent.external.call_connector", lambda *_: {"provider": "BMKG", "events": []})
    evidence = fetch_external({"question": "Gempa terbaru?", "intent": {"connectors": ["bmkg"]}})
    assert "data" not in evidence[0]
    assert evidence[0]["error"]


def test_bnpb_query_rejects_unavailable_type_by_province() -> None:
    with Session(create_engine("sqlite:///:memory:")) as session:
        with pytest.raises(ValueError, match="all disaster types only"):
            query_series(
                session, geography_level="province", geography_name="ACEH", disaster_type="BANJIR",
                start_year=2024, end_year=2024,
            )


def test_bnpb_query_blocks_unvalidated_papua_boundary_trend() -> None:
    engine = create_engine("sqlite:///:memory:")
    BnpbSnapshot.__table__.create(engine)
    BnpbFact.__table__.create(engine)
    with Session(engine) as session:
        snapshot = BnpbSnapshot(
            resource_id="5dfcae7e-acb3-4548-82b5-61d876ea478a", title="Province events", content_sha256="x"
        )
        session.add(snapshot)
        session.flush()
        session.add_all([
            BnpbFact(snapshot_id=snapshot.id, source_row_id=1, measure="events", geography_level="province",
                     geography_code="94", geography_name="PAPUA TENGAH", disaster_type=None, year=year,
                     value=value, source_row={})
            for year, value in [(2010, 3), (2024, 5)]
        ])
        session.commit()
        with pytest.raises(ValueError, match="boundaries"):
            query_series(
                session, geography_level="province", geography_name="PAPUA TENGAH",
                start_year=2010, end_year=2024,
            )


def test_saved_bmkg_answer_keeps_original_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from tilik_agent import api

    engine = create_engine("sqlite:///:memory:")
    for table in (Conversation.__table__, Message.__table__, ExternalSourceSnapshot.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add(Conversation(id="00000000-0000-0000-0000-000000000001", owner_id="visitor"))
        session.commit()
    monkeypatch.setattr(api, "_db_session", lambda: Session(engine))
    result = QuestionResult(
        answer="BMKG melaporkan gempa terbaru.",
        sources=[{"kind": "external", "provider": "BMKG", "title": "Gempabumi", "evidence_id": "ext1",
                  "url": "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json"}],
        external_evidence=[{"id": "ext1", "data": {"provider": "BMKG", "events": []},
                            "snapshot_payload": {"body": "original JSON"}}],
    )
    response = api._save_turn("00000000-0000-0000-0000-000000000001", "Gempa terbaru?", result)
    assert response.sources[0]["snapshot_id"] > 0
    with Session(engine) as session:
        snapshot = session.get(ExternalSourceSnapshot, response.sources[0]["snapshot_id"])
        assert snapshot.content["raw"]["body"] == "original JSON"


class _FakeMcpClient:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.closed = False

    def call(self, name: str, arguments: dict) -> object:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed = True


def test_dead_mcp_session_is_replaced_on_the_same_call(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = _FakeMcpClient(mcp_client.ConnectorUnavailable("server restarted"))
    fresh = _FakeMcpClient({"ok": True})
    clients = iter([fresh])
    monkeypatch.setattr(mcp_client, "_clients", {"bmkg": dead})
    monkeypatch.setattr(mcp_client, "LocalMcpClient", lambda _server: next(clients))

    assert mcp_client.call_connector("bmkg", "bmkg_earthquakes", {}) == {"ok": True}
    assert dead.closed
    assert mcp_client._clients == {"bmkg": fresh}


def test_unreachable_mcp_server_is_retried_on_the_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    down = mcp_client.ConnectorUnavailable("connection refused")
    clients = iter([_FakeMcpClient(down), _FakeMcpClient(down), _FakeMcpClient({"ok": True})])
    monkeypatch.setattr(mcp_client, "_clients", {})
    monkeypatch.setattr(mcp_client, "LocalMcpClient", lambda _server: next(clients))

    with pytest.raises(mcp_client.ConnectorUnavailable):
        mcp_client.call_connector("bmkg", "bmkg_earthquakes", {})
    assert mcp_client._clients == {}
    assert mcp_client.call_connector("bmkg", "bmkg_earthquakes", {}) == {"ok": True}


def test_mcp_tool_error_keeps_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeMcpClient(RuntimeError("bmkg MCP tool bmkg_forecast failed: Unknown village"))
    monkeypatch.setattr(mcp_client, "_clients", {"bmkg": client})

    with pytest.raises(RuntimeError, match="Unknown village"):
        mcp_client.call_connector("bmkg", "bmkg_forecast", {"place": "nowhere"})
    assert mcp_client._clients == {"bmkg": client}
    assert not client.closed


def _bnpb_national(item_id: str, disaster_type: str, values: dict[int, int], first_fact: int) -> dict:
    return {"id": item_id, "provider": "BNPB", "data": {
        "provider": "BNPB", "title": "Jumlah Kejadian Bencana Menurut Jenis Bencana", "measure": "events",
        "unit": "events", "geography_level": "national", "disaster_type": disaster_type,
        "rows": [
            {"fact_id": first_fact + index, "year": year, "geography_name": "Indonesia",
             "disaster_type": disaster_type, "value": value}
            for index, (year, value) in enumerate(values.items())
        ],
    }}


def test_bnpb_trend_and_correlation_pair_by_year() -> None:
    from tilik_agent.agent.statistics import year_series_result

    flood = _bnpb_national("ext1", "BANJIR", {2020: 100, 2021: 120, 2022: 140, 2023: 200}, 1)["data"]
    slide = _bnpb_national("ext2", "TANAH LONGSOR", {2021: 60, 2022: 70, 2023: 90, 2024: 95}, 10)["data"]
    trend = year_series_result("ols", [{"id": "ext1", "label": "Banjir", "unit": "events", "rows": flood["rows"]}])
    assert trend.statistics["slope"] == pytest.approx(32.0)
    assert trend.statistics["top_residuals"][0]["year"] == 2022
    pair = year_series_result("correlation", [
        {"id": "ext1", "label": "Banjir", "unit": "events", "rows": flood["rows"]},
        {"id": "ext2", "label": "Longsor", "unit": "events", "rows": slide["rows"]},
    ])
    assert (pair.statistics["n"], pair.statistics["first_year"], pair.statistics["last_year"]) == (3, 2021, 2023)
    assert pair.selection_ids == ["ext1", "ext2"]
    with pytest.raises(ValueError, match="province panels"):
        year_series_result("dispersion", [{"id": "ext1", "label": "Banjir", "unit": "events", "rows": flood["rows"]}])


def test_bnpb_analysis_runs_and_its_rounded_statistics_pass_the_number_check() -> None:
    from tilik_agent.agent.analysis import analyze_data

    state = {
        "question": "Seberapa kuat tren banjir?",
        "intent": {"question": "Seberapa kuat tren banjir?", "source_route": "data", "connectors": ["bnpb"],
                   "analysis_operation": "ols"},
        "external_evidence": [_bnpb_national("ext1", "BANJIR", {2020: 100, 2021: 120, 2022: 140, 2023: 200}, 1)],
    }
    result = analyze_data(state)
    assert result["analysis_error"] == ""
    state.update(result)
    _check_bnpb_numbers("Rata-rata naik 32 kejadian per tahun, R² 0,91.", state, ["ext1"])
    with pytest.raises(ValueError, match="Unsupported numeric claim"):
        _check_bnpb_numbers("Rata-rata naik 47 kejadian per tahun.", state, ["ext1"])


def test_analysis_over_two_bnpb_series_cites_both_when_the_model_omits_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tilik_agent.agent.answer.model_json", lambda *_: AnswerDraft(text="Hubungannya sedang.", external_ids=[])
    )
    result = write_answer({
        "question": "Apakah banjir dan longsor bergerak bersama?",
        "external_evidence": [
            _bnpb_national("ext1", "BANJIR", {2022: 140, 2023: 200, 2024: 180}, 1),
            _bnpb_national("ext2", "TANAH LONGSOR", {2022: 70, 2023: 90, 2024: 95}, 10),
        ],
        "analysis_results": [{"selection_ids": ["ext1", "ext2"], "statistics": {"pearson_r": 0.5}}],
    })
    assert result["answer"] == "Hubungannya sedang."
    assert result["answer_external_ids"] == ["ext1", "ext2"]


def test_bnpb_analysis_fetch_uses_the_requested_years(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr("tilik_agent.agent.external.model_json", lambda *_: ExternalPlan(calls=[
        ExternalCall(connector="bnpb", tool="bnpb_query_series",
                     arguments={"disaster_type": "BANJIR", "start_year": 2010, "end_year": 2024}),
    ]))
    monkeypatch.setattr(
        "tilik_agent.agent.external.call_connector",
        lambda connector, tool, arguments: calls.append(arguments) or {"provider": "BNPB", "rows": []},
    )
    fetch_external({"question": "Banjir dan longsor pada periode yang sama", "intent": {
        "connectors": ["bnpb"], "analysis_operation": "correlation", "start_year": 2015, "end_year": 2024,
    }})
    assert (calls[0]["start_year"], calls[0]["end_year"]) == (2015, 2024)


def test_decimal_with_leading_zero_is_not_read_as_thousands() -> None:
    from tilik_agent.agent.answer import _number_values

    assert _number_values("0,079") == [Decimal("0.079")]
    assert _number_values("1.794") == [Decimal("1794")]
    assert _number_values("1.307,7") == [Decimal("1307.7")]


def test_two_series_from_one_bnpb_table_are_cited_once() -> None:
    from tilik_agent.agent.runner import cited_sources

    sources = cited_sources({
        "answer_external_ids": ["ext1", "ext2"],
        "external_evidence": [
            _bnpb_national("ext1", "BANJIR", {2022: 140, 2023: 200}, 1),
            _bnpb_national("ext2", "TANAH LONGSOR", {2022: 70, 2023: 90}, 10),
        ],
    })
    assert len([source for source in sources if source["kind"] == "external"]) == 1
