"""Store chart, sources, and last activity for the chat workspace.

Revision ID: 20260927_0003
Revises: 20260927_0002
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260927_0003"
down_revision: str | None = "20260927_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the assistant payload and conversation.updated_at."""
    op.add_column(
        "conversation",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute(
        """
        UPDATE conversation AS c
        SET updated_at = COALESCE(
            (SELECT MAX(m.created_at) FROM message AS m WHERE m.conversation_id = c.id),
            c.created_at
        )
        """
    )
    op.add_column("message", sa.Column("chart", sa.JSON(), nullable=True))
    op.add_column("message", sa.Column("sources", sa.JSON(), nullable=True))
    op.add_column("message", sa.Column("source_route", sa.String(length=16), nullable=True))
    op.add_column("message", sa.Column("chart_error", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove the assistant payload and conversation.updated_at."""
    op.drop_column("message", "chart_error")
    op.drop_column("message", "source_route")
    op.drop_column("message", "sources")
    op.drop_column("message", "chart")
    op.drop_column("conversation", "updated_at")
