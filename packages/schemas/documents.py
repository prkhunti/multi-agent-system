"""Document ingestion and retrieval schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ParsedDocument(BaseModel):
    """Canonical representation emitted by a document parser."""

    model_config = ConfigDict(extra="forbid")

    title: str
    markdown: str
    source_uri: str
    parser: str
    metadata: dict[str, object] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """A structural retrieval unit derived from one document."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    document_id: UUID
    case_id: UUID
    position: int = Field(ge=0)
    heading: str | None
    content: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class RetrievedChunk(DocumentChunk):
    """Retrieved chunk with document provenance and distance."""

    document_title: str
    source_uri: str | None = None
    distance: float = Field(ge=0.0)


class IndexResult(BaseModel):
    """Summary of a document indexing operation."""

    case_id: UUID
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    embedding_backend: str
