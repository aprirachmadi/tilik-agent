"""Add versioned BNPB aggregate snapshots.

Revision ID: 20261103_0006
Revises: 20261004_0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261103_0006"
down_revision: str | None = "20261004_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bnpb_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("license_id", sa.String(64)),
        sa.Column("source_modified_at", sa.String(64)),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("resource_id", "content_sha256", name="uq_bnpb_snapshot_content"),
    )
    op.create_index("ix_bnpb_snapshot_resource_id", "bnpb_snapshot", ["resource_id"])
    op.create_table(
        "bnpb_fact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("bnpb_snapshot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_row_id", sa.Integer(), nullable=False),
        sa.Column("measure", sa.String(32), nullable=False),
        sa.Column("geography_level", sa.String(16), nullable=False),
        sa.Column("geography_code", sa.String(16)),
        sa.Column("geography_name", sa.String(128)),
        sa.Column("disaster_type", sa.String(64)),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=True),
        sa.Column("source_row", sa.JSON(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "source_row_id", "year", name="uq_bnpb_fact_source"),
    )
    op.create_index("ix_bnpb_fact_snapshot_id", "bnpb_fact", ["snapshot_id"])
    op.create_index("ix_bnpb_fact_year", "bnpb_fact", ["year"])
    op.create_table(
        "external_source_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.UniqueConstraint("provider", "content_sha256", name="uq_external_source_content"),
    )


def downgrade() -> None:
    op.drop_table("external_source_snapshot")
    op.drop_table("bnpb_fact")
    op.drop_table("bnpb_snapshot")
