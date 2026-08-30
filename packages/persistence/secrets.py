"""Resolve managed database credentials without logging secret material."""

from __future__ import annotations

import json
from typing import Any, Protocol

import boto3

from packages.settings import Settings


class _SecretsManagerClient(Protocol):
    def get_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        """Return a Secrets Manager value."""
        ...


def resolve_database_settings(
    settings: Settings,
    client: _SecretsManagerClient | None = None,
) -> Settings:
    """Return settings with database URLs resolved from AWS Secrets Manager.

    Parameters
    ----------
    settings : Settings
        Validated process settings.
    client : _SecretsManagerClient | None
        Optional client override used by unit tests.

    Returns
    -------
    Settings
        Original settings when no secret ARN is configured, otherwise a copy
        containing assembled asyncpg and psycopg URLs.
    """
    if not settings.database_secret_arn:
        return settings
    secrets = client or boto3.client("secretsmanager", region_name=settings.aws_region)
    response = secrets.get_secret_value(SecretId=settings.database_secret_arn)
    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise RuntimeError("The database secret must contain a SecretString")
    payload = json.loads(secret_string)
    if not isinstance(payload, dict):
        raise RuntimeError("The database secret must contain a JSON object")
    required = ("host", "username", "password")
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise RuntimeError(f"The database secret is missing required fields: {', '.join(missing)}")
    return Settings.model_validate(
        settings.model_dump()
        | {
            "database_host": str(payload["host"]),
            "database_port": int(payload.get("port", 5432)),
            "database_name": str(payload.get("dbname", settings.database_name)),
            "database_user": str(payload["username"]),
            "database_password": str(payload["password"]),
        }
    )
