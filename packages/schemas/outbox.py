"""Transactional outbox message contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OutboxEventType(StrEnum):
    """External side effects emitted from committed business transactions."""

    APPROVAL_WORKFLOW_START = "approval.workflow.start"


class OutboxStatus(StrEnum):
    """Delivery state of a transactional outbox message."""

    PENDING = "pending"
    PUBLISHED = "published"
    DEAD_LETTERED = "dead_lettered"


class ApprovalWorkflowStartPayload(BaseModel):
    """Schema for starting the approval workflow for one governed action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    case_id: UUID
    tenant_id: str = Field(min_length=1, max_length=255)


class OutboxMessageCreate(BaseModel):
    """Message written atomically with its governed action aggregate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aggregate_type: Literal["governed_action"] = "governed_action"
    aggregate_id: UUID
    event_type: Literal[OutboxEventType.APPROVAL_WORKFLOW_START]
    payload: ApprovalWorkflowStartPayload


class OutboxMessage(OutboxMessageCreate):
    """Persisted outbox message with lease and delivery metadata."""

    id: UUID
    status: OutboxStatus
    attempt_count: int = Field(ge=0)
    available_at: datetime
    locked_at: datetime | None = None
    locked_by: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=255)
    delivery_result: dict[str, str] | None = None
    created_at: datetime


class OutboxDispatchResult(BaseModel):
    """Summary returned by one bounded dispatcher poll."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claimed: int = Field(ge=0)
    published: int = Field(ge=0)
    retried: int = Field(ge=0)
    dead_lettered: int = Field(ge=0)
