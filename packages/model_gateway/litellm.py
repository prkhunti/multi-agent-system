"""LiteLLM proxy backend using its OpenAI-compatible Responses endpoint."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from packages.model_gateway.base import ModelRequest


class LiteLLMBackend:
    """Route logical model aliases through a LiteLLM proxy."""

    def __init__(self, *, base_url: str, api_key: str, model_alias: str) -> None:
        self._client: Any = AsyncOpenAI(base_url=base_url, api_key=api_key or "local-dev")
        self._model_alias = model_alias

    @property
    def name(self) -> str:
        """Return the backend identifier."""
        return "litellm"

    async def generate(self, request: ModelRequest) -> str:
        """Generate through a LiteLLM logical alias."""
        response = await self._client.responses.create(
            model=self._model_alias,
            instructions=request.system_prompt,
            input=request.user_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.response_schema,
                }
            },
        )
        return str(response.output_text)
