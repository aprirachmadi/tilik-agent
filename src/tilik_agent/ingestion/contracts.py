"""Shared observation contract emitted by every BPS adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DimensionDraft:
    """One dimension member attached to an observation."""

    source_code: str
    original_label: str
    member_source_code: str
    member_label: str
    dimension_type: str
    canonical_code: str | None


@dataclass(frozen=True)
class ObservationDraft:
    """One source cell normalized to the observation contract."""

    measure_source_code: str
    measure_label: str
    measure_unit: str
    measure_definition: str | None
    measure_decimals: int | None
    measure_note: str | None
    source_value: str
    numeric_value: float | None
    canonical_value: float | None
    canonical_unit: str | None
    status_code: str | None
    period_source_code: str
    period_label: str
    dimensions: tuple[DimensionDraft, ...]
    source_cell_key: str


@dataclass
class ParseResult:
    """Adapter output. A review result stores no guessed observations."""

    observations: list[ObservationDraft] = field(default_factory=list)
    needs_review: bool = False
    review_reason: str | None = None
    notes: str | None = None
