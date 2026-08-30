"""Model provider contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class ModelTask(StrEnum):
    """Logical tasks routed independently of physical model IDs."""

    SECURITY_REVIEW = "security_review"
    LEGAL_REVIEW = "legal_review"
    FINANCIAL_REVIEW = "financial_review"
    SYNTHESIZE = "synthesize"


class ModelRequest(BaseModel):
    """Provider-neutral structured generation request."""

    model_config = ConfigDict(extra="forbid")

    task: ModelTask
    system_prompt: str
    user_prompt: str
    context: dict[str, Any]
    schema_name: str
    response_schema: dict[str, Any]


class ModelBackend(Protocol):
    """Interface implemented by all model providers."""

    @property
    def name(self) -> str:
        """Return a safe backend identifier."""
        ...

    async def generate(self, request: ModelRequest) -> str:
        """Generate a JSON response conforming to the requested schema."""
        ...
