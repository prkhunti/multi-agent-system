"""Supplier case schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CaseStatus(StrEnum):
    """Lifecycle state of a supplier onboarding case."""

    NEW = "new"
    IN_REVIEW = "in_review"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVIEW_FAILED = "review_failed"


class DocumentInput(BaseModel):
    """A document supplied for review in the initial vertical slice."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    source_uri: str | None = None


class SupplierCaseCreate(BaseModel):
    """Request payload for creating a supplier case."""

    model_config = ConfigDict(extra="forbid")

    supplier_name: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=2_000)
    documents: list[DocumentInput] = Field(default_factory=list, max_length=50)


class SupplierCase(BaseModel):
    """Persisted supplier case."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: str = Field(min_length=1, max_length=255)
    supplier_name: str
    description: str
    status: CaseStatus
    documents: list[DocumentInput]
    created_at: datetime
    updated_at: datetime
