"""Give each conversation an owning visitor.

Conversations created before ownership cannot be attributed to a browser,
so their rows in this app's two chat tables are removed. Nothing else in
the database is touched.

Revision ID: 20261106_0007
Revises: 20261103_0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261106_0007"
down_revision: str | None = "20261103_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("owner_id", sa.String(36), nullable=True))
    op.execute(
        "DELETE FROM message WHERE conversation_id IN (SELECT id FROM conversation WHERE owner_id IS NULL)"
    )
    op.execute("DELETE FROM conversation WHERE owner_id IS NULL")
    op.alter_column("conversation", "owner_id", nullable=False)
    op.create_index("ix_conversation_owner_id", "conversation", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_owner_id", table_name="conversation")
    op.drop_column("conversation", "owner_id")
