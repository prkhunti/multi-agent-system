"""Deterministic structural Markdown chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from packages.schemas.documents import DocumentChunk

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class _Section:
    heading: str | None
    text: str


class StructuralChunker:
    """Split Markdown on headings and bounded paragraph groups."""

    def __init__(self, *, max_chars: int = 1_600, overlap_chars: int = 200) -> None:
        if max_chars < 200:
            raise ValueError("max_chars must be at least 200")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be between zero and max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def chunk(
        self,
        *,
        case_id: UUID,
        document_id: UUID,
        markdown: str,
        metadata: dict[str, object] | None = None,
    ) -> list[DocumentChunk]:
        """Create stable structural chunks.

        Parameters
        ----------
        case_id : UUID
            Owning supplier case.
        document_id : UUID
            Owning document.
        markdown : str
            Canonical Markdown produced by the parser.
        metadata : dict[str, object] | None
            Safe metadata copied onto each chunk.

        Returns
        -------
        list[DocumentChunk]
            Ordered non-empty chunks with deterministic identifiers.
        """
        outputs: list[DocumentChunk] = []
        for section in self._sections(markdown):
            for text in self._bounded_parts(section.text):
                content = text.strip()
                if not content:
                    continue
                position = len(outputs)
                identifier = uuid5(
                    NAMESPACE_URL,
                    f"{document_id}:{position}:{content}",
                )
                outputs.append(
                    DocumentChunk(
                        id=identifier,
                        document_id=document_id,
                        case_id=case_id,
                        position=position,
                        heading=section.heading,
                        content=content,
                        token_count=max(1, len(content.split())),
                        metadata=metadata or {},
                    )
                )
        return outputs

    def _sections(self, markdown: str) -> list[_Section]:
        sections: list[_Section] = []
        heading: str | None = None
        lines: list[str] = []
        for line in markdown.splitlines():
            match = _HEADING.match(line)
            if match:
                if any(item.strip() for item in lines):
                    sections.append(_Section(heading=heading, text="\n".join(lines)))
                heading = match.group(2).strip()
                lines = []
            else:
                lines.append(line)
        if any(item.strip() for item in lines):
            sections.append(_Section(heading=heading, text="\n".join(lines)))
        return sections or [_Section(heading=None, text=markdown)]

    def _bounded_parts(self, text: str) -> list[str]:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        parts: list[str] = []
        current = ""
        for paragraph in paragraphs:
            remaining = paragraph
            while remaining:
                available = self._max_chars - len(current) - (2 if current else 0)
                if available <= 0:
                    parts.append(current)
                    current = current[-self._overlap_chars :] if self._overlap_chars else ""
                    continue
                piece = remaining[:available]
                current = f"{current}\n\n{piece}" if current else piece
                remaining = remaining[len(piece) :]
                if len(current) >= self._max_chars:
                    parts.append(current)
                    current = current[-self._overlap_chars :] if self._overlap_chars else ""
        if current:
            parts.append(current)
        return parts
