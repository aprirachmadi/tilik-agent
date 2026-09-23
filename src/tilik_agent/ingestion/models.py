"""SQLAlchemy models for BPS observations.

These tables live in the same Postgres database as the pgvector embedding tables.
They are a different set of tables, not a second connection.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the statistical observation schema."""


class IngestionRun(Base):
    """One execution of a catalog sync or table load."""

    __tablename__ = "ingestion_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    scope: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Dataset(Base):
    """One BPS table identity inside a domain and backend."""

    __tablename__ = "dataset"
    __table_args__ = (UniqueConstraint("domain_id", "tablesource", "bps_table_id", name="uq_dataset_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bps_table_id: Mapped[str] = mapped_column(String(255))
    tablesource: Mapped[str] = mapped_column(String(8))
    domain_id: Mapped[str] = mapped_column(String(8))
    subject_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    oldest_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_update: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parser_status: Mapped[str] = mapped_column(String(32), default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionCheckpoint(Base):
    """Resume marker for one dataset period and parser version."""

    __tablename__ = "ingestion_checkpoint"
    __table_args__ = (
        UniqueConstraint("dataset_id", "period_key", "parser_version", name="uq_checkpoint_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    period_key: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RawObject(Base):
    """Pointer to an unmodified API payload or workbook on disk."""

    __tablename__ = "raw_object"
    __table_args__ = (UniqueConstraint("endpoint", "params_sha256", name="uq_raw_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_run.id"), nullable=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("dataset.id"), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(64))
    request_params: Mapped[dict[str, str]] = mapped_column(JSON)
    params_sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(128))
    api_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(512))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    byte_size: Mapped[int] = mapped_column(Integer)


class Unit(Base):
    """Source unit and, when the conversion is certain, its canonical unit."""

    __tablename__ = "unit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String(128), unique=True)
    canonical_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    conversion_version: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Measure(Base):
    """A quantity published inside one dataset."""

    __tablename__ = "measure"
    __table_args__ = (UniqueConstraint("dataset_id", "source_code", name="uq_measure_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(255))
    canonical_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_name: Mapped[str] = mapped_column(Text)
    source_unit_id: Mapped[int | None] = mapped_column(ForeignKey("unit.id"), nullable=True)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    decimal_places: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Dimension(Base):
    """An axis that qualifies observations in one dataset."""

    __tablename__ = "dimension"
    __table_args__ = (UniqueConstraint("dataset_id", "source_code", name="uq_dimension_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(255))
    canonical_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_label: Mapped[str] = mapped_column(Text)
    dimension_type: Mapped[str] = mapped_column(String(64))


class DimensionMember(Base):
    """One source-defined value on a dimension."""

    __tablename__ = "dimension_member"
    __table_args__ = (UniqueConstraint("dimension_id", "source_code", name="uq_member_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(255))
    canonical_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_label: Mapped[str] = mapped_column(Text)
    normalized_label: Mapped[str] = mapped_column(Text)


class Period(Base):
    """A period label as published for one dataset."""

    __tablename__ = "period"
    __table_args__ = (UniqueConstraint("dataset_id", "source_code", "label", name="uq_period_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(64))


class Observation(Base):
    """One published value for a measure, period, and source cell."""

    __tablename__ = "observation"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "measure_id",
            "period_id",
            "source_cell_key",
            name="uq_observation_cell",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    measure_id: Mapped[int] = mapped_column(ForeignKey("measure.id", ondelete="CASCADE"))
    period_id: Mapped[int] = mapped_column(ForeignKey("period.id", ondelete="CASCADE"))
    source_value_text: Mapped[str] = mapped_column(Text)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_unit_id: Mapped[int | None] = mapped_column(ForeignKey("unit.id"), nullable=True)
    canonical_unit_id: Mapped[int | None] = mapped_column(ForeignKey("unit.id"), nullable=True)
    status_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_cell_key: Mapped[str] = mapped_column(String(400))
    content_hash: Mapped[str] = mapped_column(String(64))
    ingestion_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_run.id"), nullable=True)


class ObservationDimension(Base):
    """Links an observation to one dimension member."""

    __tablename__ = "observation_dimension"

    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observation.id", ondelete="CASCADE"), primary_key=True
    )
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("dimension_member.id", ondelete="CASCADE"))


class LabelMapping(Base):
    """Explicit registry from a source label to a canonical interpretation."""

    __tablename__ = "label_mapping"
    __table_args__ = (
        UniqueConstraint(
            "normalized_label",
            "canonical_target",
            "mapping_version",
            name="uq_label_mapping",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_label: Mapped[str] = mapped_column(Text)
    normalized_label: Mapped[str] = mapped_column(Text)
    source_backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canonical_target: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[str] = mapped_column(String(16))
    mapping_version: Mapped[str] = mapped_column(String(16))
    review_status: Mapped[str] = mapped_column(String(16))


class Geography(Base):
    """A BPS area code and label as published. Boundaries are not rewritten."""

    __tablename__ = "geography"
    __table_args__ = (UniqueConstraint("geography_version", "bps_code", "name", name="uq_geography_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geography_version: Mapped[str] = mapped_column(String(32))
    bps_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(Text)
    administrative_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
