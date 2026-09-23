"""Persist parsed observations and replace a period idempotently."""

from __future__ import annotations

import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tilik_agent.ingestion.contracts import DimensionDraft, ObservationDraft, ParseResult
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


class DuplicateMappingError(RuntimeError):
    """The same source cell was mapped twice."""


def ensure_seed_mappings(session: Session) -> None:
    """Insert the explicit label registry when those rows are missing."""
    for original, target in SEED_MAPPINGS:
        normalized = normalize_label(original)
        exists = session.scalar(
            select(LabelMapping.id).where(
                LabelMapping.normalized_label == normalized,
                LabelMapping.canonical_target == target,
                LabelMapping.mapping_version == MAPPING_VERSION,
            )
        )
        if exists is not None:
            continue
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
    count = 0
    for draft in result.observations:
        _insert_observation(session, dataset, draft, ingestion_id)
        count += 1
    return count


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


def _insert_observation(
    session: Session,
    dataset: Dataset,
    draft: ObservationDraft,
    ingestion_id: int | None,
) -> None:
    unit = _unit(session, draft.measure_unit, draft.canonical_unit)
    canonical_unit = _unit(session, draft.canonical_unit, None) if draft.canonical_unit else None
    measure = _measure(session, dataset.id, draft, unit.id)
    period = _period(session, dataset.id, draft.period_source_code, draft.period_label)
    observation = Observation(
        dataset_id=dataset.id,
        measure_id=measure.id,
        period_id=period.id,
        source_value_text=draft.source_value,
        value_numeric=draft.numeric_value,
        canonical_value_numeric=draft.canonical_value,
        source_unit_id=unit.id,
        canonical_unit_id=None if canonical_unit is None else canonical_unit.id,
        status_code=draft.status_code,
        source_cell_key=draft.source_cell_key,
        content_hash=observation_fingerprint(draft),
        ingestion_id=ingestion_id,
    )
    session.add(observation)
    session.flush()
    for dimension_draft in draft.dimensions:
        dimension = _dimension(session, dataset.id, dimension_draft)
        member = _member(session, dimension, dimension_draft)
        _geography(session, dimension_draft)
        session.add(
            ObservationDimension(
                observation_id=observation.id,
                dimension_id=dimension.id,
                member_id=member.id,
            )
        )


def _unit(session: Session, source_name: str, canonical_name: str | None) -> Unit:
    unit = session.scalar(select(Unit).where(Unit.source_name == source_name))
    if unit is None:
        unit = Unit(
            source_name=source_name,
            canonical_name=canonical_name,
            scale=None,
            conversion_version=CONVERSION_VERSION if canonical_name else None,
        )
        session.add(unit)
        session.flush()
        return unit
    if canonical_name and unit.canonical_name is None:
        unit.canonical_name = canonical_name
        unit.conversion_version = CONVERSION_VERSION
    return unit


def _measure(session: Session, dataset_id: int, draft: ObservationDraft, unit_id: int) -> Measure:
    measure = session.scalar(
        select(Measure).where(Measure.dataset_id == dataset_id, Measure.source_code == draft.measure_source_code)
    )
    if measure is None:
        measure = Measure(
            dataset_id=dataset_id,
            source_code=draft.measure_source_code,
            canonical_name=None,
            original_name=draft.measure_label,
            source_unit_id=unit_id,
            definition=draft.measure_definition,
            decimal_places=draft.measure_decimals,
            source_note=draft.measure_note,
        )
        session.add(measure)
        session.flush()
        return measure
    measure.original_name = draft.measure_label
    measure.source_unit_id = unit_id
    measure.definition = draft.measure_definition
    measure.decimal_places = draft.measure_decimals
    measure.source_note = draft.measure_note
    return measure


def _dimension(session: Session, dataset_id: int, draft: DimensionDraft) -> Dimension:
    dimension = session.scalar(
        select(Dimension).where(Dimension.dataset_id == dataset_id, Dimension.source_code == draft.source_code)
    )
    if dimension is None:
        dimension = Dimension(
            dataset_id=dataset_id,
            source_code=draft.source_code,
            canonical_role=draft.dimension_type,
            original_label=draft.original_label,
            dimension_type=draft.dimension_type,
        )
        session.add(dimension)
        session.flush()
        return dimension
    dimension.original_label = draft.original_label
    dimension.dimension_type = draft.dimension_type
    dimension.canonical_role = draft.dimension_type
    return dimension


def _member(session: Session, dimension: Dimension, draft: DimensionDraft) -> DimensionMember:
    member = session.scalar(
        select(DimensionMember).where(
            DimensionMember.dimension_id == dimension.id,
            DimensionMember.source_code == draft.member_source_code,
        )
    )
    if member is None:
        member = DimensionMember(
            dimension_id=dimension.id,
            source_code=draft.member_source_code,
            canonical_code=draft.canonical_code,
            original_label=draft.member_label,
            normalized_label=normalize_label(draft.member_label),
        )
        session.add(member)
        session.flush()
        return member
    member.original_label = draft.member_label
    member.normalized_label = normalize_label(draft.member_label)
    member.canonical_code = draft.canonical_code
    return member


def _period(session: Session, dataset_id: int, source_code: str, label: str) -> Period:
    period = session.scalar(
        select(Period).where(
            Period.dataset_id == dataset_id,
            Period.source_code == source_code,
            Period.label == label,
        )
    )
    if period is None:
        period = Period(dataset_id=dataset_id, source_code=source_code, label=label)
        session.add(period)
        session.flush()
    return period


def _geography(session: Session, draft: DimensionDraft) -> None:
    if draft.dimension_type != "geography" or not draft.member_source_code.isdigit():
        return
    level = None
    if re.fullmatch(r"\d{7}", draft.member_source_code) and draft.member_source_code.endswith("0000"):
        level = "province"
    existing = session.scalar(
        select(Geography).where(
            Geography.geography_version == "source",
            Geography.bps_code == draft.member_source_code,
            Geography.name == draft.member_label,
        )
    )
    if existing is not None:
        return
    session.add(
        Geography(
            geography_version="source",
            bps_code=draft.member_source_code,
            name=draft.member_label,
            administrative_level=level,
            parent_code=None,
            valid_from=None,
            valid_to=None,
        )
    )
