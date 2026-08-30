"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -- Application -----------------------------------------------------------
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    # -- Identity --------------------------------------------------------------
    auth_mode: Literal["dev", "oidc"] = "dev"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_algorithm: Literal["RS256", "ES256"] = "RS256"

    # -- Model routing ---------------------------------------------------------
    model_backend: Literal["deterministic", "openai", "bedrock", "litellm"] = "deterministic"
    model_alias: str = "supplier-review"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-terra"
    aws_region: str = "us-east-1"
    bedrock_model_id: str = ""
    litellm_base_url: str = "http://localhost:4000/v1"
    litellm_api_key: str = ""

    # -- Data ------------------------------------------------------------------
    repository_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = Field(
        default="postgresql+asyncpg://supplier:supplier@localhost:55432/supplier_assurance"
    )
    checkpoint_backend: Literal["memory", "postgres"] = "memory"
    langgraph_database_url: str = Field(
        default="postgresql://supplier:supplier@localhost:55432/supplier_assurance"
    )
    database_secret_arn: str = ""
    database_host: str = ""
    database_port: int = Field(default=5432, ge=1, le=65_535)
    database_name: str = "supplier_assurance"
    database_user: str = ""
    database_password: str = ""
    redis_url: str = "redis://localhost:6379/0"
    opensearch_url: str = "http://localhost:9200"
    embedding_backend: Literal["deterministic", "bedrock"] = "deterministic"
    embedding_dimension: int = Field(default=1024, ge=32, le=2_000)
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    retrieval_limit: int = Field(default=8, ge=1, le=50)

    # -- Governed workflows ---------------------------------------------------
    approval_workflow_backend: Literal["local", "step_functions"] = "local"
    step_functions_state_machine_arn: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8001, ge=1, le=65_535)

    # -- Transactional outbox -------------------------------------------------
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    outbox_batch_size: int = Field(default=20, ge=1, le=500)
    outbox_lock_timeout_seconds: int = Field(default=120, ge=10, le=3_600)
    outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    outbox_retry_base_seconds: int = Field(default=2, ge=1, le=3_600)
    outbox_retry_max_seconds: int = Field(default=300, ge=1, le=86_400)

    @model_validator(mode="after")
    def assemble_database_urls(self) -> Settings:
        """Assemble driver-specific URLs from separately injected credentials."""
        if not self.database_host:
            return self
        if not self.database_user or not self.database_password:
            raise ValueError(
                "DATABASE_USER and DATABASE_PASSWORD are required when DATABASE_HOST is set"
            )
        username = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        authority = f"{username}:{password}@{self.database_host}:{self.database_port}"
        self.database_url = f"postgresql+asyncpg://{authority}/{self.database_name}"
        self.langgraph_database_url = f"postgresql://{authority}/{self.database_name}"
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached process settings."""
    return Settings()
