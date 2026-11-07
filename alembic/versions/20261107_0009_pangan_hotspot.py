"""Add versioned Bapanas monthly prices and SiPongi monthly hotspot counts.

Revision ID: 20261107_0009
Revises: 20261106_0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261107_0009"
down_revision: str | None = "20261106_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bapanas_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("commodity", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("commodity_id", "month", "content_sha256", name="uq_bapanas_snapshot_content"),
    )
    op.create_index("ix_bapanas_snapshot_commodity_id", "bapanas_snapshot", ["commodity_id"])
    op.create_index("ix_bapanas_snapshot_month", "bapanas_snapshot", ["month"])
    op.create_table(
        "bapanas_price",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id", sa.Integer(), sa.ForeignKey("bapanas_snapshot.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("geography_level", sa.String(16), nullable=False),
        sa.Column("province_id", sa.Integer()),
        sa.Column("province_name", sa.String(128), nullable=False),
        sa.Column("province_code", sa.String(2)),
        sa.Column("value", sa.Integer()),
        sa.Column("reference_price", sa.Integer()),
        sa.Column("reference_price_max", sa.Integer()),
        sa.Column("status", sa.String(32)),
        sa.Column("source_row", sa.JSON(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "geography_level", "province_id", name="uq_bapanas_price"),
    )
    op.create_index("ix_bapanas_price_snapshot_id", "bapanas_price", ["snapshot_id"])
    op.create_table(
        "sipongi_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("month", "content_sha256", name="uq_sipongi_snapshot_content"),
    )
    op.create_index("ix_sipongi_snapshot_month", "sipongi_snapshot", ["month"])
    op.create_table(
        "sipongi_count",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id", sa.Integer(), sa.ForeignKey("sipongi_snapshot.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("province_name", sa.String(128), nullable=False),
        sa.Column("province_code", sa.String(2)),
        sa.Column("district", sa.String(128)),
        sa.Column("satellite", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_sipongi_count_snapshot_id", "sipongi_count", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("sipongi_count")
    op.drop_table("sipongi_snapshot")
    op.drop_table("bapanas_price")
    op.drop_table("bapanas_snapshot")
