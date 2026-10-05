from __future__ import annotations

import math

import pytest

from tilik_agent.agent.geography import province_code, province_names
from tilik_agent.agent.schemas import AnalysisRequest, RequestIntent, SeriesSelection
from tilik_agent.agent.statistics import _resolve_rows, compute
from tilik_agent.agent.workers import interpret_request


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


def test_ols_matches_hand_calculated_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    panels = {
        "x": {"2025": {"11": (1, 1.0, "A"), "12": (2, 2.0, "B"), "13": (3, 3.0, "C")}},
        "y": {"2025": {"13": (4, 6.0, "C"), "11": (5, 2.0, "A"), "12": (6, 3.0, "B")}},
    }
    monkeypatch.setattr("tilik_agent.agent.statistics.load_series", lambda selection: panels[selection.selection_id])
    result = compute(
        AnalysisRequest(operation="ols", selection_ids=["x", "y"], top_k=1),
        [_selection("x", ["2025"]), _selection("y", ["2025"])],
    )
    assert result.statistics["n"] == 3
    assert result.statistics["slope"] == pytest.approx(2.0)
    assert result.statistics["intercept"] == pytest.approx(-1 / 3)
    assert result.statistics["r_squared"] == pytest.approx(12 / 13)
    assert result.statistics["pearson_r"] == pytest.approx(math.sqrt(12 / 13))
    assert result.statistics["spearman_rho"] == pytest.approx(1.0)
    assert result.statistics["top_residuals"] == [
        {"province_code": "12", "province": "B", "residual": pytest.approx(-2 / 3)}
    ]


def test_convergence_matches_hand_calculated_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = {
        "2017": {"11": (1, 10.0, "A"), "12": (2, 20.0, "B"), "13": (3, 30.0, "C")},
        "2025": {"13": (4, 23.0, "C"), "11": (5, 9.0, "A"), "12": (6, 16.0, "B")},
    }
    monkeypatch.setattr("tilik_agent.agent.statistics.load_series", lambda _: panel)
    result = compute(
        AnalysisRequest(operation="convergence", selection_ids=["s"]),
        [_selection("s", ["2017", "2025"])],
    )
    assert result.statistics["periods"] == ["2017", "2025"]
    assert result.statistics["n"] == 3
    assert result.statistics["slope"] == pytest.approx(-0.3)
    assert result.statistics["intercept"] == pytest.approx(2.0)
    assert result.statistics["r"] == pytest.approx(-1.0)
    assert result.statistics["r_squared"] == pytest.approx(1.0)
    assert result.statistics["change_unit"] == "percentage points"


def test_paired_compare_matches_hand_calculated_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    panels = {
        "first": {
            "2024": {"11": (1, 3.0, "A"), "12": (2, 4.0, "B"), "13": (3, 5.0, "C")},
            "2025": {"11": (4, 6.0, "A"), "12": (5, 7.0, "B"), "13": (6, 8.0, "C")},
        },
        "second": {
            "2024": {"13": (7, 5.0, "C"), "11": (8, 4.0, "A"), "12": (9, 2.0, "B")},
            "2025": {"13": (10, 10.0, "C"), "11": (11, 5.0, "A"), "12": (12, 9.0, "B")},
        },
    }
    monkeypatch.setattr("tilik_agent.agent.statistics.load_series", lambda selection: panels[selection.selection_id])
    result = compute(
        AnalysisRequest(operation="paired_compare", selection_ids=["first", "second"]),
        [_selection("first", ["2024", "2025"]), _selection("second", ["2024", "2025"])],
    )
    assert result.statistics["periods"]["2024"]["n"] == 3
    assert result.statistics["periods"]["2024"]["first_lower"] == 1
    assert result.statistics["periods"]["2024"]["mean_difference"] == pytest.approx(1 / 3)
    assert result.statistics["periods"]["2025"]["n"] == 3
    assert result.statistics["periods"]["2025"]["first_lower"] == 2
    assert result.statistics["periods"]["2025"]["mean_difference"] == pytest.approx(-1.0)


def test_interpreter_rejects_lost_explicit_year(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = RequestIntent.model_validate({
        "question": "Apakah kesenjangan menyempit pada 201?",
        "source_route": "data",
        "analysis_operation": "dispersion",
    })
    monkeypatch.setattr("tilik_agent.agent.workers.model_json", lambda schema, prompt: bad)
    result = interpret_request({"question": "Apakah kesenjangan menyempit sejak 2017?"})
    assert "plan_error" in result
    assert "lost an explicit user year" in result["plan_error"]
