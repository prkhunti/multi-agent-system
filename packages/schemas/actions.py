"""Governed enterprise action schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.schemas.reviews import Decision

ToolName = Literal["supplier.set_onboarding_decision"]


class ActionStatus(StrEnum):
    """Lifecycle state of a governed enterprise action."""

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalDecision(StrEnum):
    """Human decision applied to a pending action."""

    APPROVE = "approve"
    REJECT = "reject"


class SupplierDecisionArguments(BaseModel):
    """Immutable arguments for the enterprise supplier-system write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_case_id: UUID
    review_id: UUID
    decision: Decision
    rationale: str = Field(min_length=1, max_length=4_000)
    required_actions: list[str] = Field(default_factory=list, max_length=100)


class ActionProposalRequest(BaseModel):
    """Idempotent request to derive an action from the latest review."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID


class ApprovalRequest(BaseModel):
    """Human approval or rejection of a pending action."""

    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    comment: str = Field(default="", max_length=2_000)


class ApprovalStatusRequest(BaseModel):
    """Step Functions request for the persisted human decision state."""

    model_config = ConfigDict(extra="forbid")

    action_id: UUID


class ApprovalStatusResponse(BaseModel):
    """Persisted approval state returned to the durable workflow."""

    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    status: ActionStatus


class ExecutionReceipt(BaseModel):
    """Idempotent receipt returned by the enterprise system adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    external_reference: str = Field(min_length=1, max_length=255)
    applied_decision: Decision
    executed_at: datetime


class GovernedAction(BaseModel):
    """Persisted proposal whose arguments cannot change after creation."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    case_id: UUID
    review_id: UUID
    tenant_id: str = Field(min_length=1, max_length=255)
    tool_name: ToolName = "supplier.set_onboarding_decision"
    arguments: SupplierDecisionArguments
    status: ActionStatus
    proposer_id: str = Field(min_length=1, max_length=255)
    approver_id: str | None = Field(default=None, max_length=255)
    approval_comment: str = Field(default="", max_length=2_000)
    idempotency_key: UUID
    workflow_execution_arn: str | None = Field(default=None, max_length=2_048)
    execution_receipt: ExecutionReceipt | None = None
    created_at: datetime
    decided_at: datetime | None = None
    executed_at: datetime | None = None
