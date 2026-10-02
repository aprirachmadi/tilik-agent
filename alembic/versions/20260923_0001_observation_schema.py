"""Create the statistical observation tables.

Publication embeddings stay in langchain_pg_collection and langchain_pg_embedding.
This migration only creates the observation schema.

Revision ID: 20260923_0001
Revises:
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from tilik_agent.ingestion.models import Base

revision: str = "20260923_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create observation, catalog, and provenance tables."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop only the observation schema."""
    Base.metadata.drop_all(bind=op.get_bind())
