"""Shared database fixture for ingestion tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tilik_agent.ingestion.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    """Yield a session bound to a fresh in-memory schema."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
