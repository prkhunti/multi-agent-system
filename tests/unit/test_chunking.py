"""Tests for deterministic structural chunking."""

from __future__ import annotations

from uuid import uuid4

from packages.retrieval.chunking import StructuralChunker


def test_chunker_preserves_headings_and_stable_ids() -> None:
    case_id = uuid4()
    document_id = uuid4()
    markdown = "# Security\n\nShared credentials are prohibited.\n\n# Legal\n\nLiability is capped."
    chunker = StructuralChunker(max_chars=200, overlap_chars=20)

    first = chunker.chunk(case_id=case_id, document_id=document_id, markdown=markdown)
    second = chunker.chunk(case_id=case_id, document_id=document_id, markdown=markdown)

    assert [item.heading for item in first] == ["Security", "Legal"]
    assert [item.id for item in first] == [item.id for item in second]
    assert all(item.token_count > 0 for item in first)


def test_chunker_bounds_long_content() -> None:
    chunks = StructuralChunker(max_chars=200, overlap_chars=20).chunk(
        case_id=uuid4(),
        document_id=uuid4(),
        markdown="# Long\n\n" + "evidence " * 100,
    )

    assert len(chunks) > 1
    assert all(len(item.content) <= 200 for item in chunks)
