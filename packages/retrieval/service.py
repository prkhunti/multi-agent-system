"""In-memory and pgvector retrieval services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.persistence.models import DocumentChunkRecord, DocumentRecord
from packages.retrieval.chunking import StructuralChunker
from packages.retrieval.embeddings import EmbeddingProvider
from packages.schemas.cases import SupplierCase
from packages.schemas.documents import DocumentChunk, IndexResult, RetrievedChunk


class RetrievalService(Protocol):
    """Index and retrieve evidence scoped to a supplier case."""

    async def index_case(self, supplier_case: SupplierCase) -> IndexResult:
        """Index every document attached to a supplier case."""
        ...

    async def search(
        self,
        *,
        case_id: UUID,
        query: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        """Retrieve the closest evidence chunks for one case."""
        ...


@dataclass(frozen=True, slots=True)
class _IndexedChunk:
    chunk: DocumentChunk
    embedding: list[float]
    document_title: str
    source_uri: str | None


class InMemoryRetrievalService:
    """Offline retrieval service used by tests and local development."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        chunker: StructuralChunker | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._chunker = chunker or StructuralChunker()
        self._chunks: dict[UUID, list[_IndexedChunk]] = {}

    async def index_case(self, supplier_case: SupplierCase) -> IndexResult:
        """Chunk, embed, and replace one case's in-memory index."""
        chunks: list[tuple[DocumentChunk, str, str | None]] = []
        for position, document in enumerate(supplier_case.documents):
            document_id = uuid5(
                NAMESPACE_URL,
                f"{supplier_case.id}:{position}:{document.title}",
            )
            document_chunks = self._chunker.chunk(
                case_id=supplier_case.id,
                document_id=document_id,
                markdown=document.content,
                metadata={"document_position": position},
            )
            chunks.extend((chunk, document.title, document.source_uri) for chunk in document_chunks)
        vectors = await self._embeddings.embed([item[0].content for item in chunks])
        self._chunks[supplier_case.id] = [
            _IndexedChunk(
                chunk=chunk,
                embedding=vector,
                document_title=title,
                source_uri=source_uri,
            )
            for (chunk, title, source_uri), vector in zip(chunks, vectors, strict=True)
        ]
        return IndexResult(
            case_id=supplier_case.id,
            document_count=len(supplier_case.documents),
            chunk_count=len(chunks),
            embedding_backend=self._embeddings.name,
        )

    async def search(
        self,
        *,
        case_id: UUID,
        query: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        """Return chunks ordered by cosine distance."""
        query_vector = (await self._embeddings.embed([query]))[0]
        scored = [
            (
                max(
                    0.0,
                    1.0
                    - sum(
                        left * right
                        for left, right in zip(item.embedding, query_vector, strict=True)
                    ),
                ),
                item,
            )
            for item in self._chunks.get(case_id, [])
        ]
        scored.sort(key=lambda item: item[0])
        return [
            RetrievedChunk(
                **item.chunk.model_dump(),
                document_title=item.document_title,
                source_uri=item.source_uri,
                distance=distance,
            )
            for distance, item in scored[:limit]
        ]


class PgVectorRetrievalService:
    """Persist and query case evidence with PostgreSQL and pgvector."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingProvider,
        chunker: StructuralChunker | None = None,
    ) -> None:
        if embeddings.dimension != 1024:
            raise ValueError("The current pgvector schema requires 1024-dimensional embeddings")
        self._sessions = sessions
        self._embeddings = embeddings
        self._chunker = chunker or StructuralChunker()

    async def index_case(self, supplier_case: SupplierCase) -> IndexResult:
        """Replace all persisted chunks for one supplier case."""
        statement = (
            select(DocumentRecord)
            .where(DocumentRecord.case_id == supplier_case.id)
            .order_by(DocumentRecord.position)
        )
        async with self._sessions() as session:
            documents = list((await session.scalars(statement)).all())
        chunks: list[DocumentChunk] = []
        for document in documents:
            chunks.extend(
                self._chunker.chunk(
                    case_id=supplier_case.id,
                    document_id=document.id,
                    markdown=document.content,
                    metadata={"document_position": document.position},
                )
            )
        vectors = await self._embeddings.embed([chunk.content for chunk in chunks])
        records = [
            DocumentChunkRecord(
                id=chunk.id,
                document_id=chunk.document_id,
                case_id=chunk.case_id,
                position=chunk.position,
                heading=chunk.heading,
                content=chunk.content,
                token_count=chunk.token_count,
                metadata_json=chunk.metadata,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with self._sessions() as session:
            await session.execute(
                delete(DocumentChunkRecord).where(DocumentChunkRecord.case_id == supplier_case.id)
            )
            session.add_all(records)
            await session.commit()
        return IndexResult(
            case_id=supplier_case.id,
            document_count=len(documents),
            chunk_count=len(chunks),
            embedding_backend=self._embeddings.name,
        )

    async def search(
        self,
        *,
        case_id: UUID,
        query: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        """Run a tenant-scoped cosine-distance query."""
        query_vector = (await self._embeddings.embed([query]))[0]
        embedding_column: Any = DocumentChunkRecord.embedding
        distance = embedding_column.cosine_distance(query_vector)
        statement = (
            select(
                DocumentChunkRecord,
                DocumentRecord.title,
                DocumentRecord.source_uri,
                distance.label("distance"),
            )
            .join(DocumentRecord, DocumentRecord.id == DocumentChunkRecord.document_id)
            .where(DocumentChunkRecord.case_id == case_id)
            .order_by(distance)
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            RetrievedChunk(
                id=chunk.id,
                document_id=chunk.document_id,
                case_id=chunk.case_id,
                position=chunk.position,
                heading=chunk.heading,
                content=chunk.content,
                token_count=chunk.token_count,
                metadata=chunk.metadata_json,
                document_title=title,
                source_uri=source_uri,
                distance=float(row_distance),
            )
            for chunk, title, source_uri, row_distance in rows
        ]
