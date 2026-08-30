"""Native Amazon Bedrock Converse backend."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3

from packages.model_gateway.base import ModelRequest


class BedrockConverseBackend:
    """Generate structured results through Bedrock Converse."""

    def __init__(self, *, region: str, model_id: str) -> None:
        if not model_id:
            raise ValueError("BEDROCK_MODEL_ID is required when MODEL_BACKEND=bedrock")
        self._client: Any = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id

    @property
    def name(self) -> str:
        """Return the backend identifier."""
        return "bedrock-converse"

    async def generate(self, request: ModelRequest) -> str:
        """Call Converse and return its text content."""
        schema = json.dumps(request.response_schema, separators=(",", ":"))
        prompt = f"{request.user_prompt}\n\nReturn only JSON matching this schema:\n{schema}"
        response = await asyncio.to_thread(
            self._client.converse,
            modelId=self._model_id,
            system=[{"text": request.system_prompt}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.0, "maxTokens": 4_096},
        )
        blocks = response["output"]["message"]["content"]
        return "".join(str(block.get("text", "")) for block in blocks)
