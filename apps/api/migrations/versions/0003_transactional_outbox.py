"""Add the transactional outbox for durable workflow delivery."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_transactional_outbox"
down_revision: str | Sequence[str] | None = "0002_governed_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create leased, retryable outbox messages."""
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("delivery_result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "event_type",
            name="uq_outbox_messages_aggregate_event",
        ),
    )
    op.create_index(
        "ix_outbox_messages_aggregate_id",
        "outbox_messages",
        ["aggregate_id"],
    )
    op.create_index(
        "ix_outbox_messages_created_at",
        "outbox_messages",
        ["created_at"],
    )
    op.create_index(
        "ix_outbox_messages_dispatch",
        "outbox_messages",
        ["published_at", "dead_lettered_at", "available_at"],
    )
    op.create_index(
        "ix_outbox_messages_event_type",
        "outbox_messages",
        ["event_type"],
    )


def downgrade() -> None:
    """Remove the transactional outbox."""
    op.drop_table("outbox_messages")
