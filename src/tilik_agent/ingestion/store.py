"""Persist parsed observations and replace a period idempotently.

A save resolves every distinct unit, measure, period, dimension, member, and geography once,
then inserts observations and their dimension links in batches. The number of queries grows
with the number of distinct labels, not with the number of cells.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TypeVar

from sqlalchemy import delete, insert, select, tuple_
from sqlalchemy.orm import Session

from tilik_agent.agent.geography import province_code, province_names
from tilik_agent.ingestion.contracts import ObservationDraft, ParseResult
from tilik_agent.ingestion.models import (
    Dataset,
    Dimension,
    DimensionMember,
    Geography,
    LabelMapping,
    Measure,
    Observation,
    ObservationDimension,
    Period,
    Unit,
)
from tilik_agent.ingestion.parsing import (
    CONVERSION_VERSION,
    MAPPING_VERSION,
    SEED_MAPPINGS,
    duplicate_reason,
    normalize_label,
    observation_fingerprint,
)

PROVINCE_CODE = re.compile(r"\d{7}")
LOOKUP_CHUNK = 500

T = TypeVar("T")


class DuplicateMappingError(RuntimeError):
    """The same source cell was mapped twice."""


def ensure_seed_mappings(session: Session) -> None:
    """Insert the explicit label registry when those rows are missing."""
    existing = set(
        session.execute(
            select(LabelMapping.normalized_label, LabelMapping.canonical_target).where(
                LabelMapping.mapping_version == MAPPING_VERSION
            )
        ).tuples()
    )
    for original, target in SEED_MAPPINGS:
        normalized = normalize_label(original)
        if (normalized, target) in existing:
            continue
        existing.add((normalized, target))
        session.add(
            LabelMapping(
                original_label=original,
                normalized_label=normalized,
                source_backend=None,
                canonical_target=target,
                confidence="high",
                mapping_version=MAPPING_VERSION,
                review_status="accepted",
            )
        )


def apply_parse_result(
    session: Session,
    dataset: Dataset,
    result: ParseResult,
    *,
    ingestion_id: int | None,
    replace_period_labels: set[str] | None,
) -> int:
    """Replace observations for the given periods and insert the new parse.

    ``replace_period_labels`` of None replaces every observation on the dataset.
    A review result does not delete stored observations.
    """
    if result.needs_review:
        if result.notes:
            dataset.notes = result.notes
        return 0
    if result.notes:
        dataset.notes = result.notes
    reason = duplicate_reason(result.observations)
    if reason:
        raise DuplicateMappingError(reason)
    _delete_observations(session, dataset.id, replace_period_labels)
    ensure_seed_mappings(session)
    drafts = result.observations
    if not drafts:
        session.flush()
        return 0
    units = _resolve_units(session, drafts)
    measures = _resolve_measures(session, dataset.id, drafts, units)
    periods = _resolve_periods(session, dataset.id, drafts)
    dimensions = _resolve_dimensions(session, dataset.id, drafts)
    members = _resolve_members(session, drafts, dimensions)
    _ensure_geographies(session, drafts)
    session.flush()
    observation_ids = _insert_observations(session, dataset.id, drafts, ingestion_id, units, measures, periods)
    _insert_links(session, drafts, observation_ids, dimensions, members)
    return len(drafts)


def _delete_observations(session: Session, dataset_id: int, period_labels: set[str] | None) -> None:
    period_ids = select(Period.id).where(Period.dataset_id == dataset_id)
    if period_labels is not None:
        period_ids = period_ids.where(Period.label.in_(period_labels))
    observation_ids = select(Observation.id).where(
        Observation.dataset_id == dataset_id,
        Observation.period_id.in_(period_ids),
    )
    session.execute(delete(ObservationDimension).where(ObservationDimension.observation_id.in_(observation_ids)))
    session.execute(delete(Observation).where(Observation.id.in_(observation_ids)))
    session.flush()


def _chunks(values: Sequence[T]) -> Iterable[Sequence[T]]:
    for start in range(0, len(values), LOOKUP_CHUNK):
        yield values[start : start + LOOKUP_CHUNK]


def _resolve_units(session: Session, drafts: Sequence[ObservationDraft]) -> dict[str, Unit]:
    """Map every source and canonical unit name to a row. The first canonical name seen wins."""
    wanted: dict[str, str | None] = {}
    for draft in drafts:
        if wanted.get(draft.measure_unit) is None:
            wanted[draft.measure_unit] = draft.canonical_unit
        if draft.canonical_unit:
            wanted.setdefault(draft.canonical_unit, None)
    units: dict[str, Unit] = {}
    for chunk in _chunks(list(wanted)):
        for unit in session.scalars(select(Unit).where(Unit.source_name.in_(chunk))):
            units[unit.source_name] = unit
    for name, canonical_name in wanted.items():
        unit = units.get(name)
        if unit is None:
            unit = Unit(
                source_name=name,
                canonical_name=canonical_name,
                scale=None,
                conversion_version=CONVERSION_VERSION if canonical_name else None,
            )
            session.add(unit)
            units[name] = unit
        elif canonical_name and unit.canonical_name is None:
            unit.canonical_name = canonical_name
            unit.conversion_version = CONVERSION_VERSION
    session.flush()
    return units


def _resolve_measures(
    session: Session,
    dataset_id: int,
    drafts: Sequence[ObservationDraft],
    units: dict[str, Unit],
) -> dict[str, Measure]:
    """Create or update one measure per source code. The last draft for a code wins."""
    latest = {draft.measure_source_code: draft for draft in drafts}
    measures = {
        measure.source_code: measure
        for measure in session.scalars(select(Measure).where(Measure.dataset_id == dataset_id))
    }
    for code, draft in latest.items():
        measure = measures.get(code)
        if measure is None:
            measure = Measure(dataset_id=dataset_id, source_code=code, canonical_name=None)
            session.add(measure)
            measures[code] = measure
        measure.original_name = draft.measure_label
        measure.source_unit_id = units[draft.measure_unit].id
        measure.definition = draft.measure_definition
        measure.decimal_places = draft.measure_decimals
        measure.source_note = draft.measure_note
    return measures


def _resolve_periods(
    session: Session,
    dataset_id: int,
    drafts: Sequence[ObservationDraft],
) -> dict[tuple[str, str], Period]:
    periods = {
        (period.source_code, period.label): period
        for period in session.scalars(select(Period).where(Period.dataset_id == dataset_id))
    }
    for draft in drafts:
        key = (draft.period_source_code, draft.period_label)
        if key not in periods:
            period = Period(dataset_id=dataset_id, source_code=key[0], label=key[1])
            session.add(period)
            periods[key] = period
    return periods


def _resolve_dimensions(
    session: Session,
    dataset_id: int,
    drafts: Sequence[ObservationDraft],
) -> dict[str, Dimension]:
    """Create or update one dimension per source code. The last draft for a code wins."""
    latest = {dimension.source_code: dimension for draft in drafts for dimension in draft.dimensions}
    dimensions = {
        dimension.source_code: dimension
        for dimension in session.scalars(select(Dimension).where(Dimension.dataset_id == dataset_id))
    }
    for code, draft in latest.items():
        dimension = dimensions.get(code)
        if dimension is None:
            dimension = Dimension(dataset_id=dataset_id, source_code=code)
            session.add(dimension)
            dimensions[code] = dimension
        dimension.original_label = draft.original_label
        dimension.dimension_type = draft.dimension_type
        dimension.canonical_role = draft.dimension_type
    session.flush()
    return dimensions


def _resolve_members(
    session: Session,
    drafts: Sequence[ObservationDraft],
    dimensions: dict[str, Dimension],
) -> dict[tuple[str, str], DimensionMember]:
    """Create or update one member per dimension and member code. The last draft for a pair wins."""
    latest = {
        (dimension.source_code, dimension.member_source_code): dimension
        for draft in drafts
        for dimension in draft.dimensions
    }
    code_by_dimension_id = {dimension.id: code for code, dimension in dimensions.items()}
    known_provinces = province_names(
        list(
            session.execute(
                select(Geography.bps_code, Geography.name).where(
                    Geography.administrative_level == "province", Geography.geography_version == "source"
                )
            ).tuples()
        )
    )

    def canonical(draft: object) -> str | None:
        if draft.dimension_type != "geography":
            return draft.canonical_code
        if draft.canonical_code == "national":
            return "national"
        if PROVINCE_CODE.fullmatch(draft.member_source_code) and draft.member_source_code.endswith("0000"):
            return draft.member_source_code
        return province_code(draft.member_label, known_provinces)

    def _load() -> dict[tuple[str, str], DimensionMember]:
        return {
            (code_by_dimension_id[member.dimension_id], member.source_code): member
            for member in session.scalars(
                select(DimensionMember).where(DimensionMember.dimension_id.in_(code_by_dimension_id))
            )
        }

    members = _load()
    new_rows = [
        {
            "dimension_id": dimensions[key[0]].id,
            "source_code": key[1],
            "canonical_code": canonical(draft),
            "original_label": draft.member_label,
            "normalized_label": normalize_label(draft.member_label),
        }
        for key, draft in latest.items()
        if key not in members
    ]
    for key, member in members.items():
        draft = latest.get(key)
        if draft is None:
            continue
        member.original_label = draft.member_label
        member.normalized_label = normalize_label(draft.member_label)
        member.canonical_code = canonical(draft)
    if new_rows:
        session.execute(insert(DimensionMember), new_rows)
        members = _load()
    return members


def _ensure_geographies(session: Session, drafts: Sequence[ObservationDraft]) -> None:
    wanted: set[tuple[str, str]] = {
        (dimension.member_source_code, dimension.member_label)
        for draft in drafts
        for dimension in draft.dimensions
        if dimension.dimension_type == "geography" and dimension.member_source_code.isdigit()
    }
    if not wanted:
        return
    keys = sorted(wanted)
    existing: set[tuple[str, str]] = set()
    for chunk in _chunks(keys):
        existing.update(
            session.execute(
                select(Geography.bps_code, Geography.name).where(
                    Geography.geography_version == "source",
                    tuple_(Geography.bps_code, Geography.name).in_(chunk),
                )
            ).tuples()
        )
    new_rows = [
        {
            "geography_version": "source",
            "bps_code": code,
            "name": name,
            "administrative_level": _administrative_level(code),
            "parent_code": None,
            "valid_from": None,
            "valid_to": None,
        }
        for code, name in keys
        if (code, name) not in existing
    ]
    if new_rows:
        session.execute(insert(Geography), new_rows)


def _administrative_level(bps_code: str) -> str | None:
    if PROVINCE_CODE.fullmatch(bps_code) and bps_code.endswith("0000"):
        return "province"
    return None


def _insert_observations(
    session: Session,
    dataset_id: int,
    drafts: Sequence[ObservationDraft],
    ingestion_id: int | None,
    units: dict[str, Unit],
    measures: dict[str, Measure],
    periods: dict[tuple[str, str], Period],
) -> list[int]:
    """Insert every draft in batched statements and return the new ids in draft order.

    Ids are read back by the unique cell key instead of ``RETURNING``, because not every
    backend can batch a ``RETURNING`` insert while keeping the row order.
    """
    rows = [
        {
            "dataset_id": dataset_id,
            "measure_id": measures[draft.measure_source_code].id,
            "period_id": periods[(draft.period_source_code, draft.period_label)].id,
            "source_value_text": draft.source_value,
            "value_numeric": draft.numeric_value,
            "canonical_value_numeric": draft.canonical_value,
            "source_unit_id": units[draft.measure_unit].id,
            "canonical_unit_id": units[draft.canonical_unit].id if draft.canonical_unit else None,
            "status_code": draft.status_code,
            "source_cell_key": draft.source_cell_key,
            "content_hash": observation_fingerprint(draft),
            "ingestion_id": ingestion_id,
        }
        for draft in drafts
    ]
    session.execute(insert(Observation), rows)
    stored = session.execute(
        select(Observation.measure_id, Observation.period_id, Observation.source_cell_key, Observation.id).where(
            Observation.dataset_id == dataset_id,
            Observation.period_id.in_({row["period_id"] for row in rows}),
        )
    ).tuples()
    id_by_cell = {(measure_id, period_id, cell): row_id for measure_id, period_id, cell, row_id in stored}
    return [id_by_cell[(row["measure_id"], row["period_id"], row["source_cell_key"])] for row in rows]


def _insert_links(
    session: Session,
    drafts: Sequence[ObservationDraft],
    observation_ids: Sequence[int],
    dimensions: dict[str, Dimension],
    members: dict[tuple[str, str], DimensionMember],
) -> None:
    rows = [
        {
            "observation_id": observation_id,
            "dimension_id": dimensions[dimension.source_code].id,
            "member_id": members[(dimension.source_code, dimension.member_source_code)].id,
        }
        for draft, observation_id in zip(drafts, observation_ids, strict=True)
        for dimension in draft.dimensions
    ]
    if rows:
        session.execute(insert(ObservationDimension), rows)
