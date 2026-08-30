"""Embedding provider construction."""

from __future__ import annotations

from packages.retrieval.embeddings import (
    BedrockTitanEmbeddingProvider,
    EmbeddingProvider,
    HashEmbeddingProvider,
)
from packages.settings import Settings


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Construct the configured embedding provider.

    Parameters
    ----------
    settings : Settings
        Process configuration.

    Returns
    -------
    EmbeddingProvider
        Deterministic or Bedrock embedding provider.
    """
    if settings.embedding_backend == "bedrock":
        return BedrockTitanEmbeddingProvider(
            region=settings.aws_region,
            model_id=settings.bedrock_embedding_model_id,
            dimension=settings.embedding_dimension,
        )
    return HashEmbeddingProvider(settings.embedding_dimension)
