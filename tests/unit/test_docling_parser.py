"""Tests for the optional Docling parser adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from packages.ingestion.docling_parser import DoclingDocumentParser


class _FakeDocument:
    def export_to_markdown(self) -> str:
        return "# Parsed\n\nStructured content"


class _FakeConverter:
    def convert(self, source: Path) -> SimpleNamespace:
        assert source.exists()
        return SimpleNamespace(document=_FakeDocument())


async def test_docling_parser_exports_canonical_markdown(tmp_path: Path) -> None:
    source = tmp_path / "supplier.pdf"
    source.write_bytes(b"synthetic test document")
    parser = DoclingDocumentParser(converter=_FakeConverter())

    parsed = await parser.parse(source)

    assert parsed.title == "supplier.pdf"
    assert parsed.markdown.startswith("# Parsed")
    assert parsed.parser == "docling-v2"
