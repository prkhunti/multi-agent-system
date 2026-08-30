"""Structured supplier review schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RiskCategory(StrEnum):
    """Risk domains reviewed by specialist agents."""

    SECURITY = "security"
    LEGAL = "legal"
    FINANCIAL = "financial"


class Severity(StrEnum):
    """Finding severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Evidence(BaseModel):
    """Source evidence supporting a finding."""

    model_config = ConfigDict(extra="forbid")

    document_title: str
    quote: str = Field(min_length=1, max_length=1_000)
    source_uri: str | None = None


class Finding(BaseModel):
    """A structured risk finding produced by a specialist."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    category: RiskCategory
    severity: Severity
    title: str
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    remediation: str
    confidence: float = Field(ge=0.0, le=1.0)


class FindingBatch(BaseModel):
    """Validated specialist output envelope."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]


class Decision(StrEnum):
    """Recommended business decision prior to human approval."""

    APPROVE = "approve"
    REMEDIATE = "remediate"
    ESCALATE = "escalate"
    REJECT = "reject"


class Recommendation(BaseModel):
    """Synthesized recommendation from all specialist findings."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    rationale: str
    required_actions: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewResult(BaseModel):
    """Completed review returned to the API caller."""

    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    case_id: UUID
    findings: list[Finding]
    recommendation: Recommendation
    model_backend: str
    started_at: datetime
    completed_at: datetime


class EvidenceConfirmationDecision(StrEnum):
    """Analyst decision at the pre-synthesis evidence gate."""

    CONFIRM = "confirm"
    REJECT = "reject"


class EvidenceConfirmation(BaseModel):
    """Validated analyst response used to resume an interrupted review."""

    model_config = ConfigDict(extra="forbid")

    decision: EvidenceConfirmationDecision
    comment: str = Field(min_length=1, max_length=2_000)


class ReviewExecutionStart(BaseModel):
    """Idempotent request to start a durable review execution."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    require_evidence_confirmation: bool = False


class ReviewExecutionStatus(StrEnum):
    """Externally visible lifecycle of a checkpointed review execution."""

    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReviewInterrupt(BaseModel):
    """JSON-safe human-input request emitted by the review graph."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["evidence_confirmation"] = "evidence_confirmation"
    question: str
    finding_count: int = Field(ge=0)
    categories: list[RiskCategory]


class ReviewExecution(BaseModel):
    """Current state of one durable, tenant-owned review execution."""

    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    case_id: UUID
    status: ReviewExecutionStatus
    interrupt: ReviewInterrupt | None = None
    result: ReviewResult | None = None
