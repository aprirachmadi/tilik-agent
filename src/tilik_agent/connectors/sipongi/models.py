"""Versioned monthly hotspot counts synchronized from SiPongi."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from tilik_agent.ingestion.models import Base


class SipongiSnapshot(Base):
    """One national month as returned on one fetch; a changed payload is a new snapshot."""

    __tablename__ = "sipongi_snapshot"
    __table_args__ = (UniqueConstraint("month", "content_sha256", name="uq_sipongi_snapshot_content"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)
    complete: Mapped[bool] = mapped_column(Boolean)
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SipongiCount(Base):
    __tablename__ = "sipongi_count"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("sipongi_snapshot.id", ondelete="CASCADE"), index=True)
    province_name: Mapped[str] = mapped_column(String(128))
    province_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    district: Mapped[str | None] = mapped_column(String(128), nullable=True)
    satellite: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(8))
    count: Mapped[int] = mapped_column(Integer)
