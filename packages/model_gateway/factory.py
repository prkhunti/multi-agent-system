"""Model backend construction."""

from __future__ import annotations

from packages.model_gateway.base import ModelBackend
from packages.model_gateway.bedrock_converse import BedrockConverseBackend
from packages.model_gateway.deterministic import DeterministicBackend
from packages.model_gateway.litellm import LiteLLMBackend
from packages.model_gateway.openai_responses import OpenAIResponsesBackend
from packages.settings import Settings


def create_model_backend(settings: Settings) -> ModelBackend:
    """Construct the configured model backend."""
    if settings.model_backend == "openai":
        return OpenAIResponsesBackend(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    if settings.model_backend == "bedrock":
        return BedrockConverseBackend(
            region=settings.aws_region,
            model_id=settings.bedrock_model_id,
        )
    if settings.model_backend == "litellm":
        return LiteLLMBackend(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            model_alias=settings.model_alias,
        )
    return DeterministicBackend()
