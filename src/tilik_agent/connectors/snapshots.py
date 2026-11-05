"""Immutable copies of mutable external evidence cited in saved answers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from tilik_agent.ingestion.models import Base


class ExternalSourceSnapshot(Base):
    __tablename__ = "external_source_snapshot"
    __table_args__ = (UniqueConstraint("provider", "content_sha256", name="uq_external_source_content"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(String(512))
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
