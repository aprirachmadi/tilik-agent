"""Backfill confirmed province codes on geography members.

Revision ID: 20261004_0005
Revises: 20261003_0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tilik_agent.agent.geography import province_code, province_names

revision: str = "20261004_0005"
down_revision: str | None = "20261003_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    known = province_names(
        list(
            bind.execute(
                sa.text(
                    "SELECT bps_code, name FROM geography "
                    "WHERE administrative_level='province' AND geography_version='source'"
                )
            )
        )
    )
    members = bind.execute(
        sa.text(
            """SELECT dm.id, dm.original_label FROM dimension_member dm
           JOIN dimension d ON d.id=dm.dimension_id
           WHERE d.dimension_type='geography' AND dm.canonical_code IS NULL"""
        )
    ).fetchall()
    updates = [{"id": id_, "code": code} for id_, label in members if (code := province_code(label, known))]
    if updates:
        bind.execute(sa.text("UPDATE dimension_member SET canonical_code=:code WHERE id=:id"), updates)


def downgrade() -> None:
    # Source-backed identity is preserved when rolling back unrelated schema changes.
    pass
