"""Tests for the deterministic embedding provider."""

from __future__ import annotations

from packages.retrieval.embeddings import HashEmbeddingProvider


async def test_hash_embeddings_are_stable_and_normalized() -> None:
    provider = HashEmbeddingProvider(dimension=64)

    first, second = await provider.embed(["shared credentials", "shared credentials"])

    assert first == second
    assert round(sum(value * value for value in first), 6) == 1.0
    assert len(first) == 64
