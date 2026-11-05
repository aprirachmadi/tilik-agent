"""Validated series loading and deterministic province statistics."""

from __future__ import annotations

import hashlib
import math
import re
import statistics as st
from collections import defaultdict
from html import unescape
from typing import Any

from tilik_agent.agent.geography import is_nonprovince, province_code, province_names
from tilik_agent.agent.schemas import AnalysisRequest, AnalysisResult, SeriesSelection


def _connection():
    from tilik_agent.agent.sql import get_connection

    return get_connection()


def _rows(conn: Any, dataset_id: int, measure_id: int, period_ids: list[int], member_ids: list[int]) -> list[tuple]:
    return conn.execute(
        """
        SELECT o.id, p.id, p.label, o.value_numeric, o.status_code,
               geo.original_label, geo.canonical_code
        FROM observation o
        JOIN period p ON p.id = o.period_id
        JOIN observation_dimension god ON god.observation_id = o.id
        JOIN dimension gd ON gd.id = god.dimension_id AND gd.dimension_type = 'geography'
        JOIN dimension_member geo ON geo.id = god.member_id
        WHERE o.dataset_id = %(dataset)s AND o.measure_id = %(measure)s
          AND o.period_id = ANY(%(periods)s::int[])
          AND NOT EXISTS (
            SELECT 1 FROM unnest(%(members)s::int[]) wanted(member_id)
            WHERE NOT EXISTS (
              SELECT 1 FROM observation_dimension od
              WHERE od.observation_id = o.id AND od.member_id = wanted.member_id
            )
          )
        ORDER BY p.label, o.id
        """,
        {"dataset": dataset_id, "measure": measure_id, "periods": period_ids, "members": member_ids},
    ).fetchall()


def _known_provinces(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        "SELECT bps_code, name FROM geography WHERE administrative_level = 'province' AND geography_version = 'source'"
    ).fetchall()
    return province_names(rows)


def _resolve_rows(rows: list[tuple], names: dict[str, str]) -> dict[str, dict[str, tuple[int, float, str]]]:
    panel: dict[str, dict[str, tuple[int, float, str]]] = defaultdict(dict)
    unresolved: set[str] = set()
    for observation_id, _, period, value, status, label, canonical in rows:
        code = canonical if canonical and canonical.isdigit() and len(canonical) == 7 else province_code(label, names)
        if not code:
            if not is_nonprovince(label):
                unresolved.add(label)
            continue
        if value is None or status is not None or not math.isfinite(float(value)):
            raise ValueError(f"Non-numeric or status observation for {label}, {period}")
        if code in panel[period]:
            raise ValueError(f"Duplicate province-period cell: {label}, {period}")
        panel[period][code] = (int(observation_id), float(value), label)
    if unresolved:
        raise ValueError(f"Unresolved province labels: {sorted(unresolved)}")
    return dict(panel)


def _pin_error(axes: list[tuple], pinned_axes: list[int]) -> str:
    """Name the axes a selection must fix, so the caller can correct its member IDs."""
    problems = []
    for axis_id, kind, label in axes:
        count = pinned_axes.count(axis_id)
        if kind == "geography" and count:
            problems.append(f"unpin geography axis '{label}' (dimension {axis_id})")
        elif kind != "geography" and count == 0:
            problems.append(f"pin one member of '{label}' (dimension {axis_id})")
        elif kind != "geography" and count > 1:
            problems.append(f"pin only one member of '{label}' (dimension {axis_id}), not {count}")
    detail = "; ".join(problems) or "the dataset needs exactly one geography axis"
    return f"Pin exactly one member on every non-geography axis; leave geography unpinned. Fix: {detail}"


def create_selection(dataset_id: int, measure_id: int, period_ids: list[int], member_ids: list[int]) -> dict[str, Any]:
    """Validate a complete query specification without exposing its rows to the model."""
    if not period_ids or len(period_ids) != len(set(period_ids)) or len(period_ids) > 40:
        return {"error": "Supply 1–40 distinct exact period IDs"}
    if len(member_ids) != len(set(member_ids)):
        return {"error": "Duplicate member ID"}
    try:
        with _connection() as conn:
            dataset = conn.execute(
                "SELECT title, parser_status, notes FROM dataset WHERE id=%s", (dataset_id,)
            ).fetchone()
            measure = conn.execute(
                """SELECT m.original_name, m.source_note, u.source_name FROM measure m
                   LEFT JOIN unit u ON u.id=m.source_unit_id WHERE m.id=%s AND m.dataset_id=%s""",
                (measure_id, dataset_id),
            ).fetchone()
            periods = conn.execute(
                "SELECT id,label FROM period WHERE dataset_id=%s AND id=ANY(%s::int[]) ORDER BY label",
                (dataset_id, period_ids),
            ).fetchall()
            axes = conn.execute(
                "SELECT id, dimension_type, original_label FROM dimension WHERE dataset_id=%s", (dataset_id,)
            ).fetchall()
            members = conn.execute(
                """SELECT dm.id, d.id FROM dimension_member dm JOIN dimension d ON d.id=dm.dimension_id
                   WHERE d.dataset_id=%s AND dm.id=ANY(%s::int[])""",
                (dataset_id, member_ids),
            ).fetchall()
            if not dataset or dataset[1] != "parsed" or not measure:
                return {"error": "Dataset or measure is unavailable or not parsed"}
            if len(periods) != len(period_ids):
                return {"error": "Period ID does not belong to dataset"}
            if len(members) != len(member_ids):
                return {"error": "Member ID does not belong to dataset"}
            pinned_axes = [axis_id for _, axis_id in members]
            non_geo = {axis_id for axis_id, kind, _ in axes if kind != "geography"}
            geo_axes = {axis_id for axis_id, kind, _ in axes if kind == "geography"}
            if len(geo_axes) != 1 or set(pinned_axes) != non_geo or len(pinned_axes) != len(non_geo):
                return {"error": _pin_error(axes, pinned_axes)}
            rows = _rows(conn, dataset_id, measure_id, period_ids, member_ids)
            names = _known_provinces(conn)
        panel = _resolve_rows(rows, names)
        labels = [label for _, label in periods]
        if any(not panel.get(label) for label in labels):
            return {"error": "No numeric province observations for every requested period"}
        all_codes = set().union(*(set(cells) for cells in panel.values()))
        coverage = {
            label: {
                "count": len(panel[label]),
                "province_codes": sorted(panel[label]),
                "missing_from_selected_union": sorted(all_codes - set(panel[label])),
            }
            for label in labels
        }
        key = f"{dataset_id}:{measure_id}:{sorted(period_ids)}:{sorted(member_ids)}"
        selection = SeriesSelection(
            selection_id="sel_" + hashlib.sha256(key.encode()).hexdigest()[:12],
            dataset_id=dataset_id,
            measure_id=measure_id,
            period_ids=[id_ for id_, _ in periods],
            period_labels=labels,
            member_ids=member_ids,
            dataset_title=dataset[0],
            measure_name=measure[0],
            unit=measure[2],
            source_note=re.sub(
                r"\s+", " ", re.sub(r"<[^>]*>", " ", unescape("; ".join(filter(None, [measure[1], dataset[2]]))))
            ).strip(),
            coverage=coverage,
            observation_ids=sorted(row[0] for row in rows),
        )
        return {"selection": selection.model_dump(), "coverage": coverage}
    except ValueError as exc:
        return {"error": str(exc)}


def load_series(selection: SeriesSelection) -> dict[str, dict[str, tuple[int, float, str]]]:
    with _connection() as conn:
        rows = _rows(conn, selection.dataset_id, selection.measure_id, selection.period_ids, selection.member_ids)
        panel = _resolve_rows(rows, _known_provinces(conn))
    if sorted(row[0] for row in rows) != selection.observation_ids:
        raise ValueError("Selected observations changed since validation")
    for period in selection.period_labels:
        if len(panel.get(period, {})) != selection.coverage[period]["count"]:
            raise ValueError("Province coverage changed since validation")
    return panel


def _paired(a: dict[str, tuple[int, float, str]], b: dict[str, tuple[int, float, str]]):
    codes = sorted(set(a) & set(b))
    if len(codes) < 3:
        raise ValueError("At least three matched provinces are required")
    return codes, [a[c][1] for c in codes], [b[c][1] for c in codes]


def _pearson(x: list[float], y: list[float]) -> float:
    mx, my = st.mean(x), st.mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    if denominator == 0:
        raise ValueError("Correlation is undefined for a constant series")
    return numerator / denominator


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    for index in ordered:
        same = [i for i in ordered if values[i] == values[index]]
        result[index] = st.mean([ordered.index(i) + 1 for i in same])
    return result


def _ols(x: list[float], y: list[float]) -> tuple[float, float, float]:
    mx, my = st.mean(x), st.mean(y)
    denominator = sum((value - mx) ** 2 for value in x)
    if denominator == 0:
        raise ValueError("Regression is undefined for a constant predictor")
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / denominator
    intercept = my - slope * mx
    return slope, intercept, _pearson(x, y) ** 2


def compute(
    request: AnalysisRequest,
    selections: list[SeriesSelection],
    *,
    loader: Any = None,
) -> AnalysisResult:
    selected = {item.selection_id: item for item in selections}
    if len(set(request.selection_ids)) != len(request.selection_ids) or set(request.selection_ids) - set(selected):
        raise ValueError("Unknown or duplicate selection ID")
    required = 1 if request.operation in {"dispersion", "convergence"} else 2
    if len(request.selection_ids) != required:
        raise ValueError(f"{request.operation} requires {required} selection(s)")
    chosen = [selected[id_] for id_ in request.selection_ids]
    panels = [(loader or load_series)(item) for item in chosen]
    notes = [item.source_note for item in chosen if item.source_note]
    stats: dict[str, Any]
    if request.operation == "dispersion":
        panel = panels[0]
        periods = chosen[0].period_labels
        if len(periods) < 2:
            raise ValueError("Dispersion needs at least two periods")
        common = set.intersection(*(set(panel[period]) for period in periods))
        if len(common) < 3:
            raise ValueError("Fewer than three provinces occur in every period")

        def summary(values: list[float]) -> dict[str, float | int]:
            mean = st.mean(values)
            sd = st.stdev(values)
            return {"n": len(values), "mean": mean, "sd": sd, "cv": sd / mean if mean else math.nan}

        stats = {
            "periods": {
                period: {
                    "available": summary([cell[1] for cell in panel[period].values()]),
                    "common": summary([panel[period][code][1] for code in sorted(common)]),
                    "excluded_from_common": sorted(set(panel[period]) - common),
                }
                for period in periods
            },
            "common_province_codes": sorted(common),
        }
        if any(code in set().union(*(set(panel[p]) for p in periods)) for code in ("9100000", "9400000")):
            notes.append(
                "Papua and Papua Barat may have changed boundaries; same codes do not establish comparable territories."
            )
    elif request.operation == "convergence":
        periods = chosen[0].period_labels
        if len(periods) != 2:
            raise ValueError("Convergence needs exactly two periods")
        codes, initial, final = _paired(panels[0][periods[0]], panels[0][periods[1]])
        change = [later - earlier for earlier, later in zip(initial, final, strict=True)]
        slope, intercept, r2 = _ols(initial, change)
        stats = {
            "periods": periods,
            "n": len(codes),
            "slope": slope,
            "intercept": intercept,
            "r": _pearson(initial, change),
            "r_squared": r2,
            "change_unit": "percentage points",
        }
    else:
        shared_periods = sorted(set(chosen[0].period_labels) & set(chosen[1].period_labels))
        if not shared_periods:
            raise ValueError("Selections have no matching periods")
        if request.operation == "paired_compare":
            stats = {"periods": {}}
            for period in shared_periods:
                codes, x, y = _paired(panels[0][period], panels[1][period])
                stats["periods"][period] = {
                    "n": len(codes),
                    "first_lower": sum(a < b for a, b in zip(x, y, strict=True)),
                    "mean_difference": st.mean(a - b for a, b in zip(x, y, strict=True)),
                }
        else:
            if len(shared_periods) != 1:
                raise ValueError("Correlation and OLS need one matching period")
            period = shared_periods[0]
            codes, x, y = _paired(panels[0][period], panels[1][period])
            stats = {
                "period": period,
                "n": len(codes),
                "pearson_r": _pearson(x, y),
                "spearman_rho": _pearson(_ranks(x), _ranks(y)),
            }
            if request.operation == "ols":
                slope, intercept, r2 = _ols(x, y)
                stats.update({"slope": slope, "intercept": intercept, "r_squared": r2})
                residuals = sorted(
                    (
                        (codes[i], panels[1][period][codes[i]][2], y[i] - (intercept + slope * x[i]))
                        for i in range(len(codes))
                    ),
                    key=lambda row: abs(row[2]),
                    reverse=True,
                )
                stats["top_residuals"] = [
                    {"province_code": code, "province": name, "residual": value}
                    for code, name, value in residuals[: request.top_k]
                ]
    ids = sorted(
        {observation_id for panel in panels for cells in panel.values() for observation_id, _, _ in cells.values()}
    )
    result_key = f"{request.model_dump_json()}:{','.join(map(str, ids))}"
    return AnalysisResult(
        result_id="res_" + hashlib.sha256(result_key.encode()).hexdigest()[:12],
        operation=request.operation,
        selection_ids=request.selection_ids,
        statistics=stats,
        input_observation_ids=ids,
        notes=notes,
    )


def year_series_result(operation: str, series: list[dict[str, Any]], *, top_k: int = 3) -> AnalysisResult:
    """Trend and relationship statistics over national BNPB series, paired by year.

    Each series is {"id", "label", "unit", "rows": [{"fact_id", "year", "value"}]}. With one
    series, ols fits the yearly trend; with two, correlation and ols compare them year by year.
    """
    if operation not in {"correlation", "ols"}:
        raise ValueError(f"{operation} needs province panels; BNPB year series support correlation and ols")
    panels = [
        {
            int(row["year"]): (int(row["fact_id"]), float(row["value"]))
            for row in item["rows"]
            if row.get("value") is not None
        }
        for item in series
    ]
    notes = ["BNPB counts recorded events; changes in reporting coverage can affect trends."]
    if operation == "ols" and len(series) == 1:
        years = sorted(panels[0])
        x, y = [float(year) for year in years], [panels[0][year][1] for year in years]
        labels = ["year", series[0]["label"]]
    else:
        if len(series) != 2:
            raise ValueError(f"{operation} needs two BNPB series over the same years")
        years = sorted(set(panels[0]) & set(panels[1]))
        x, y = [panels[0][year][1] for year in years], [panels[1][year][1] for year in years]
        labels = [series[0]["label"], series[1]["label"]]
        notes.append("Moving together across years does not show that one disaster type causes the other.")
    if len(years) < 3:
        raise ValueError("At least three matched years are required")
    stats: dict[str, Any] = {
        "paired_by": "year",
        "x": labels[0],
        "y": labels[1],
        "first_year": years[0],
        "last_year": years[-1],
        "n": len(years),
        "pearson_r": _pearson(x, y),
        "spearman_rho": _pearson(_ranks(x), _ranks(y)),
    }
    if operation == "ols":
        slope, intercept, r2 = _ols(x, y)
        fitted = [intercept + slope * value for value in x]
        stats.update({"slope": slope, "intercept": intercept, "r_squared": r2})
        stats["top_residuals"] = [
            {"year": year, "actual": actual, "fitted": round(estimate, 1), "residual": round(actual - estimate, 1)}
            for year, actual, estimate in sorted(
                zip(years, y, fitted, strict=True), key=lambda row: abs(row[1] - row[2]), reverse=True
            )[:top_k]
        ]
    ids = sorted(panel[year][0] for panel in panels for year in years if year in panel)
    key = f"{operation}:{[item['id'] for item in series]}:{','.join(map(str, ids))}"
    return AnalysisResult(
        result_id="res_" + hashlib.sha256(key.encode()).hexdigest()[:12],
        operation=operation,
        selection_ids=[item["id"] for item in series],
        statistics=stats,
        input_observation_ids=ids,
        notes=notes,
    )
