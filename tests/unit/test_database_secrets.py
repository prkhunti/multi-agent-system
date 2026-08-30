"""Tests for managed database credential resolution."""

from __future__ import annotations

import json
from typing import Any

import pytest

from packages.persistence.secrets import resolve_database_settings
from packages.settings import Settings


class FakeSecretsClient:
    """Return a fixed database secret without AWS access."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def get_secret_value(self, **_: Any) -> dict[str, str]:
        """Return the configured payload as a JSON SecretString."""
        return {"SecretString": json.dumps(self._payload)}


def test_separate_database_credentials_are_url_encoded() -> None:
    settings = Settings(
        database_host="db.internal",
        database_user="supplier user",
        database_password="p@ss/word",
    )

    assert settings.database_url == (
        "postgresql+asyncpg://supplier+user:p%40ss%2Fword@db.internal:5432/"
        "supplier_assurance"
    )
    assert settings.langgraph_database_url.startswith("postgresql://supplier+user:")


def test_database_secret_resolves_driver_specific_urls() -> None:
    settings = Settings(database_secret_arn="arn:aws:secretsmanager:us-east-1:123:secret:db")
    client = FakeSecretsClient(
        {
            "host": "prod.cluster.internal",
            "port": 5432,
            "dbname": "supplier_assurance",
            "username": "supplier_admin",
            "password": "secret-value",
        }
    )

    resolved = resolve_database_settings(settings, client)

    assert resolved.database_url.startswith("postgresql+asyncpg://supplier_admin:")
    assert resolved.langgraph_database_url.startswith("postgresql://supplier_admin:")
    assert "prod.cluster.internal:5432" in resolved.database_url


def test_database_secret_requires_credentials() -> None:
    settings = Settings(database_secret_arn="arn:aws:secretsmanager:us-east-1:123:secret:db")

    with pytest.raises(RuntimeError, match="missing required fields"):
        resolve_database_settings(settings, FakeSecretsClient({"host": "db.internal"}))
