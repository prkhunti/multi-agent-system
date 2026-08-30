"""Native OpenAI Responses API backend."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from packages.model_gateway.base import ModelRequest


class OpenAIResponsesBackend:
    """Generate structured results through the Responses API."""

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL_BACKEND=openai")
        self._client: Any = AsyncOpenAI(api_key=api_key)
        self._model = model

    @property
    def name(self) -> str:
        """Return the backend identifier without exposing configuration."""
        return "openai-responses"

    async def generate(self, request: ModelRequest) -> str:
        """Generate a strict JSON-schema response."""
        response = await self._client.responses.create(
            model=self._model,
            instructions=request.system_prompt,
            input=request.user_prompt,
            store=False,
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
