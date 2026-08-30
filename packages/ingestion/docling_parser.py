"""Docling-backed document parser."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from packages.schemas.documents import ParsedDocument


class DoclingDocumentParser:
    """Convert local enterprise documents with Docling v2."""

    def __init__(self, converter: Any | None = None) -> None:
        self._converter = converter

    async def parse(self, source: Path) -> ParsedDocument:
        """Parse one local file into canonical Markdown.

        Parameters
        ----------
        source : Path
            Existing local file. Remote URLs are intentionally not accepted.

        Returns
        -------
        ParsedDocument
            Canonical Markdown representation.

        Raises
        ------
        FileNotFoundError
            If the source does not exist or is not a regular file.
        RuntimeError
            If Docling is not installed in the worker environment.
        """
        resolved = source.resolve(strict=True)
        if not resolved.is_file():
            raise FileNotFoundError(f"Document source is not a file: {source}")
        converter = self._converter or self._create_converter()
        conversion = await asyncio.to_thread(converter.convert, resolved)
        markdown = conversion.document.export_to_markdown()
        return ParsedDocument(
            title=resolved.name,
            markdown=markdown,
            source_uri=resolved.as_uri(),
            parser="docling-v2",
            metadata={"suffix": resolved.suffix.lower()},
        )

    def _create_converter(self) -> Any:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise RuntimeError(
                "Docling is not installed; install the worker dependency extra"
            ) from exc
        self._converter = DocumentConverter()
        return self._converter
