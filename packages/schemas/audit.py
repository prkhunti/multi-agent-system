"""Audit event schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEventCreate(BaseModel):
    """Payload for appending an audit event."""

    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    event_type: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=255)
    payload: dict[str, object] = Field(default_factory=dict)


class AuditEvent(AuditEventCreate):
    """Persisted append-only audit event."""

    id: UUID
    created_at: datetime
