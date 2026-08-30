"""Document parser contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from packages.schemas.documents import ParsedDocument


class DocumentParser(Protocol):
    """Convert an untrusted local document to canonical text."""

    async def parse(self, source: Path) -> ParsedDocument:
        """Parse a local document.

        Parameters
        ----------
        source : Path
            Existing local path controlled by the ingestion worker.

        Returns
        -------
        ParsedDocument
            Canonical Markdown plus safe parser metadata.
        """
        ...
