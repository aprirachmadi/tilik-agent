"""Versioned monthly province prices synchronized from Bapanas."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from tilik_agent.ingestion.models import Base


class BapanasSnapshot(Base):
    """One commodity-month as returned on one fetch; a changed payload is a new snapshot."""

    __tablename__ = "bapanas_snapshot"
    __table_args__ = (
        UniqueConstraint("commodity_id", "month", "content_sha256", name="uq_bapanas_snapshot_content"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity_id: Mapped[int] = mapped_column(Integer, index=True)
    commodity: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32))
    month: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)
    complete: Mapped[bool] = mapped_column(Boolean)
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BapanasPrice(Base):
    __tablename__ = "bapanas_price"
    __table_args__ = (UniqueConstraint("snapshot_id", "geography_level", "province_id", name="uq_bapanas_price"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("bapanas_snapshot.id", ondelete="CASCADE"), index=True)
    geography_level: Mapped[str] = mapped_column(String(16))
    province_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    province_name: Mapped[str] = mapped_column(String(128))
    province_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Some reference prices (HET/HAP) are a range such as "36.500 - 41.500"; a single price leaves this null.
    reference_price_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_row: Mapped[dict] = mapped_column(JSON)
