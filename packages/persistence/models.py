"""SQLAlchemy records for the durable persistence implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative database metadata base."""


class SupplierCaseRecord(Base):
    """Durable supplier onboarding case."""

    __tablename__ = "supplier_cases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    supplier_name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list[DocumentRecord]] = relationship(
        back_populates="supplier_case",
        cascade="all, delete-orphan",
    )


class DocumentRecord(Base):
    """Metadata and canonical text for an ingested document."""

    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_cases.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    supplier_case: Mapped[SupplierCaseRecord] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunkRecord]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentChunkRecord(Base):
    """A retrievable structural chunk and its vector embedding."""

    __tablename__ = "document_chunks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_cases.id", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(1024))

    document: Mapped[DocumentRecord] = relationship(back_populates="chunks")


class ReviewRunRecord(Base):
    """A versioned execution of the supplier review graph."""

    __tablename__ = "review_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_cases.id", ondelete="CASCADE"),
        index=True,
    )
    model_backend: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_actions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FindingRecord(Base):
    """A structured finding emitted during a review run."""

    __tablename__ = "findings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    review_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"),
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    remediation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)


class GovernedActionRecord(Base):
    """Approval-gated proposal for one enterprise-system mutation."""

    __tablename__ = "governed_actions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_governed_actions_tenant_idempotency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_cases.id", ondelete="CASCADE"),
        index=True,
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"),
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    proposer_id: Mapped[str] = mapped_column(String(255))
    approver_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_comment: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[UUID] = mapped_column(Uuid)
    workflow_execution_arn: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    execution_receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventRecord(Base):
    """Append-only business and agent audit event."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_cases.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OutboxMessageRecord(Base):
    """External side effect committed with its originating business transaction."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "event_type",
            name="uq_outbox_messages_aggregate_event",
        ),
        Index(
            "ix_outbox_messages_dispatch",
            "published_at",
            "dead_lettered_at",
            "available_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_result_json: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
