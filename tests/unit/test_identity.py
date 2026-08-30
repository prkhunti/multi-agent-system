"""Tests for development and OIDC identity verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from packages.identity.auth import (
    AuthenticationError,
    DevelopmentAuthenticator,
    OIDCAuthenticator,
)
from packages.schemas.identity import Role


class _StaticSigningKeys:
    def __init__(self, public_key: object) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> object:
        assert token
        return SimpleNamespace(key=self._public_key)


async def test_oidc_authenticator_verifies_signature_claims_roles_and_scopes() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://identity.example.com/",
            "aud": "supplier-assurance-api",
            "sub": "analyst@example.com",
            "tenant_id": "tenant-northstar",
            "roles": ["analyst"],
            "scope": "cases:read cases:write",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    authenticator = OIDCAuthenticator(
        issuer="https://identity.example.com/",
        audience="supplier-assurance-api",
        jwks_url="https://identity.example.com/.well-known/jwks.json",
        algorithm="RS256",
        signing_keys=_StaticSigningKeys(private_key.public_key()),
    )

    principal = await authenticator.authenticate({"authorization": f"Bearer {token}"})

    assert principal.subject == "analyst@example.com"
    assert principal.tenant_id == "tenant-northstar"
    assert principal.roles == frozenset({Role.ANALYST})
    assert principal.scopes == frozenset({"cases:read", "cases:write"})


async def test_development_authenticator_rejects_unknown_roles() -> None:
    with pytest.raises(AuthenticationError, match="unknown role"):
        await DevelopmentAuthenticator().authenticate(
            {
                "x-actor-id": "analyst@example.com",
                "x-tenant-id": "tenant-northstar",
                "x-roles": "superuser",
            }
        )
