"""Separate, versioned storage for BNPB's published aggregate tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from tilik_agent.ingestion.models import Base


class BnpbSnapshot(Base):
    __tablename__ = "bnpb_snapshot"
    __table_args__ = (UniqueConstraint("resource_id", "content_sha256", name="uq_bnpb_snapshot_content"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    license_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_modified_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BnpbFact(Base):
    __tablename__ = "bnpb_fact"
    __table_args__ = (UniqueConstraint("snapshot_id", "source_row_id", "year", name="uq_bnpb_fact_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("bnpb_snapshot.id", ondelete="CASCADE"), index=True)
    source_row_id: Mapped[int] = mapped_column(Integer)
    measure: Mapped[str] = mapped_column(String(32))
    geography_level: Mapped[str] = mapped_column(String(16))
    geography_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    geography_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    disaster_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_row: Mapped[dict] = mapped_column(JSON)
