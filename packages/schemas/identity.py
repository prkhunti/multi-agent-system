"""Identity and authorization schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Application roles derived from trusted identity-provider claims."""

    ANALYST = "analyst"
    APPROVER = "approver"
    EXECUTOR = "executor"
    ADMIN = "admin"


class Principal(BaseModel):
    """Authenticated enterprise principal and its tenant boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=255)
    roles: frozenset[Role] = Field(default_factory=frozenset)
    scopes: frozenset[str] = Field(default_factory=frozenset)

    def has_role(self, role: Role) -> bool:
        """Return whether the principal has a role or administrator access.

        Parameters
        ----------
        role : Role
            Role required by an application operation.

        Returns
        -------
        bool
            True when the principal is authorized by role membership.
        """
        return role in self.roles or Role.ADMIN in self.roles
