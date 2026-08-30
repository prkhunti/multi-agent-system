"""Embedding provider contracts and implementations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from typing import Any, Protocol

import boto3


class EmbeddingProvider(Protocol):
    """Create equal-dimension vector embeddings for retrieval."""

    @property
    def name(self) -> str:
        """Return a safe backend identifier."""
        ...

    @property
    def dimension(self) -> int:
        """Return the vector dimension."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text values in input order."""
        ...


class HashEmbeddingProvider:
    """Deterministic local embedding test double."""

    def __init__(self, dimension: int = 1024) -> None:
        self._dimension = dimension

    @property
    def name(self) -> str:
        """Return the backend identifier."""
        return "deterministic-hash"

    @property
    def dimension(self) -> int:
        """Return the vector dimension."""
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Create stable normalized token-hash vectors."""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class BedrockTitanEmbeddingProvider:
    """Generate embeddings with Amazon Titan Text Embeddings v2."""

    def __init__(self, *, region: str, model_id: str, dimension: int = 1024) -> None:
        self._client: Any = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id
        self._dimension = dimension

    @property
    def name(self) -> str:
        """Return the backend identifier."""
        return "bedrock-titan-embeddings"

    @property
    def dimension(self) -> int:
        """Return the configured embedding dimension."""
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text sequentially through Bedrock Runtime."""
        return [await self._embed_one(text) for text in texts]

    async def _embed_one(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": self._dimension,
                "normalize": True,
            }
        )
        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self._model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        return [float(value) for value in payload["embedding"]]
