"""Development and OpenID Connect authentication implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol, cast

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from packages.schemas.identity import Principal, Role


class AuthenticationError(ValueError):
    """Raised when request credentials cannot establish a trusted principal."""


class Authenticator(Protocol):
    """Authentication operation required by HTTP and MCP boundaries."""

    async def authenticate(self, headers: Mapping[str, str]) -> Principal:
        """Validate request headers and return a trusted principal."""
        ...


class DevelopmentAuthenticator:
    """Explicit header authentication for local development and tests only."""

    async def authenticate(self, headers: Mapping[str, str]) -> Principal:
        """Build a principal from required local-only identity headers."""
        subject = headers.get("x-actor-id", "").strip()
        tenant_id = headers.get("x-tenant-id", "").strip()
        raw_roles = headers.get("x-roles", "")
        if not subject or not tenant_id:
            raise AuthenticationError("X-Actor-ID and X-Tenant-ID headers are required")
        try:
            roles = frozenset(
                Role(value.strip()) for value in raw_roles.split(",") if value.strip()
            )
        except ValueError as exc:
            raise AuthenticationError("X-Roles contains an unknown role") from exc
        scopes = frozenset(
            value.strip() for value in headers.get("x-scopes", "").split() if value.strip()
        )
        return Principal(subject=subject, tenant_id=tenant_id, roles=roles, scopes=scopes)


class _SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any:
        """Return the JWK selected by the token's key identifier."""
        ...


class OIDCAuthenticator:
    """Validate asymmetric OIDC access tokens against a configured JWKS endpoint."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithm: str,
        signing_keys: _SigningKeyProvider | None = None,
    ) -> None:
        if not issuer or not audience or not jwks_url:
            raise ValueError("OIDC issuer, audience, and JWKS URL are required")
        self._issuer = issuer
        self._audience = audience
        self._algorithm = algorithm
        self._signing_keys = signing_keys or PyJWKClient(jwks_url)

    async def authenticate(self, headers: Mapping[str, str]) -> Principal:
        """Verify an OIDC bearer token and map its claims to a principal."""
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("A bearer access token is required")
        try:
            signing_key = await asyncio.to_thread(
                self._signing_keys.get_signing_key_from_jwt,
                token,
            )
            claims = cast(
                dict[str, Any],
                jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=[self._algorithm],
                    audience=self._audience,
                    issuer=self._issuer,
                    options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                ),
            )
        except (PyJWTError, AttributeError, TypeError, ValueError) as exc:
            raise AuthenticationError("The bearer access token is invalid") from exc
        return self._principal_from_claims(claims)

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        subject = claims.get("sub")
        tenant_id = claims.get("tenant_id")
        if not isinstance(subject, str) or not isinstance(tenant_id, str):
            raise AuthenticationError("OIDC claims must include string sub and tenant_id values")

        role_values: set[str] = set()
        for claim_name in ("roles", "cognito:groups"):
            values = claims.get(claim_name, [])
            if isinstance(values, list):
                role_values.update(value for value in values if isinstance(value, str))
        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict):
            values = realm_access.get("roles", [])
            if isinstance(values, list):
                role_values.update(value for value in values if isinstance(value, str))

        roles = frozenset(Role(value) for value in role_values if value in Role._value2member_map_)
        raw_scope = claims.get("scope", "")
        scopes = frozenset(raw_scope.split()) if isinstance(raw_scope, str) else frozenset()
        return Principal(subject=subject, tenant_id=tenant_id, roles=roles, scopes=scopes)
