"""Store ordered answer blocks for chart placement.

Revision ID: 20261003_0004
Revises: 20260927_0003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_0004"
down_revision: str | None = "20260927_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("message", sa.Column("blocks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "blocks")
