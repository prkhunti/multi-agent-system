"""Add tenant isolation and governed enterprise actions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_governed_actions"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenant ownership and the approval-gated action lifecycle."""
    op.add_column(
        "supplier_cases",
        sa.Column(
            "tenant_id",
            sa.String(length=255),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.alter_column("supplier_cases", "tenant_id", server_default=None)
    op.create_index("ix_supplier_cases_tenant_id", "supplier_cases", ["tenant_id"])

    op.create_table(
        "governed_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposer_id", sa.String(length=255), nullable=False),
        sa.Column("approver_id", sa.String(length=255), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("workflow_execution_arn", sa.String(length=2048), nullable=True),
        sa.Column("execution_receipt_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["supplier_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_governed_actions_tenant_idempotency",
        ),
    )
    op.create_index("ix_governed_actions_case_id", "governed_actions", ["case_id"])
    op.create_index("ix_governed_actions_review_id", "governed_actions", ["review_id"])
    op.create_index("ix_governed_actions_status", "governed_actions", ["status"])
    op.create_index("ix_governed_actions_tenant_id", "governed_actions", ["tenant_id"])


def downgrade() -> None:
    """Remove governed actions and tenant ownership."""
    op.drop_table("governed_actions")
    op.drop_index("ix_supplier_cases_tenant_id", table_name="supplier_cases")
    op.drop_column("supplier_cases", "tenant_id")
