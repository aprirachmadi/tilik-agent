from __future__ import annotations

import math

import pytest

from tilik_agent.agent.geography import province_code, province_names
from tilik_agent.agent.schemas import AnalysisRequest, SeriesSelection, WorkPlan
from tilik_agent.agent.statistics import _resolve_rows, compute
from tilik_agent.agent.workers import plan_question


def _selection(id_: str, periods: list[str]) -> SeriesSelection:
    return SeriesSelection(
        selection_id=id_,
        dataset_id=1,
        measure_id=2,
        period_ids=list(range(len(periods))),
        period_labels=periods,
        member_ids=[3],
        dataset_title="BPS table",
        measure_name="P0",
        coverage={period: {"count": 3} for period in periods},
        observation_ids=[],
    )


def test_province_aliases_and_unknowns() -> None:
    known = province_names([
        ("2100000", "Kepulauan Riau"), ("6500000", "Kalimantan Utara"),
        ("3200000", "Jawa Barat"), ("1400000", "Riau"),
    ])
    assert province_code("KEP. RIAU", known) == province_code("Kepulauan Riau", known)
    assert province_code("Kalimantan Utara 1)", known) == "6500000"
    assert province_code("Kalimantan Utara1)", known) == "6500000"
    assert province_code("32 Jawa Barat", known) == "3200000"
    assert province_code("R i a u", known) == "1400000"
    assert province_code("34 D I Y", known) == "3400000"
    assert province_code("Provinsi Imaginasi", known) is None
    assert province_code("Jumlah", known) is None


def test_unresolved_and_duplicate_provinces_block_selection() -> None:
    known = province_names([("2100000", "Kepulauan Riau")])
    with pytest.raises(ValueError, match="Unresolved"):
        _resolve_rows([(1, 1, "2025", 3.0, None, "Provinsi Imaginasi", None)], known)
    with pytest.raises(ValueError, match="Duplicate"):
        _resolve_rows(
            [
                (1, 1, "2025", 3.0, None, "Kepulauan Riau", None),
                (2, 1, "2025", 4.0, None, "KEP. RIAU", None),
            ],
            known,
        )


def test_dispersion_separates_available_and_common_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = {
        "2017": {"11": (1, 2.0, "A"), "12": (2, 4.0, "B"), "13": (3, 6.0, "C")},
        "2024": {"11": (4, 3.0, "A"), "12": (5, 4.0, "B"), "13": (6, 5.0, "C"), "14": (7, 20.0, "D")},
    }
    monkeypatch.setattr("tilik_agent.agent.statistics.load_series", lambda _: panel)
    result = compute(AnalysisRequest(operation="dispersion", selection_ids=["s"]), [_selection("s", ["2017", "2024"])])
    assert result.statistics["periods"]["2024"]["available"]["n"] == 4
    assert result.statistics["periods"]["2024"]["common"]["n"] == 3
    assert result.statistics["periods"]["2024"]["excluded_from_common"] == ["14"]
    assert math.isclose(result.statistics["periods"]["2017"]["available"]["cv"], 0.5)


def test_correlation_joins_by_code_and_reports_sample_size(monkeypatch: pytest.MonkeyPatch) -> None:
    panels = {
        "x": {"2025": {"11": (1, 1.0, "A"), "12": (2, 2.0, "B"), "13": (3, 3.0, "C")}},
        "y": {"2025": {"13": (4, 6.0, "C"), "11": (5, 2.0, "A"), "12": (6, 4.0, "B")}},
    }
    monkeypatch.setattr("tilik_agent.agent.statistics.load_series", lambda selection: panels[selection.selection_id])
    result = compute(
        AnalysisRequest(operation="correlation", selection_ids=["x", "y"]),
        [_selection("x", ["2025"]), _selection("y", ["2025"])],
    )
    assert result.statistics["n"] == 3
    assert math.isclose(result.statistics["pearson_r"], 1.0)


def test_planner_rejects_lost_explicit_year(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = WorkPlan.model_validate({
        "question": "Apakah kesenjangan menyempit pada 201?",
        "requested_analysis": True,
        "steps": [{"id": "s1", "action": "sql_lookup", "request": "Cari data provinsi"}],
    })
    monkeypatch.setattr("tilik_agent.agent.workers.model_json", lambda schema, prompt: bad)
    result = plan_question({"question": "Apakah kesenjangan menyempit sejak 2017?"})
    assert "plan_error" in result
    assert "lost an explicit user year" in result["plan_error"]
