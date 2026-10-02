"""PDF vector ingestion safeguards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tilik_agent.ingestion import pdf_vectors


def test_empty_conversion_keeps_existing_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty conversion must not trigger the destructive collection replacement."""
    converter = SimpleNamespace(convert=lambda path: SimpleNamespace(document=object()))
    monkeypatch.setattr(pdf_vectors, "DocumentConverter", lambda **kwargs: converter)
    monkeypatch.setattr(pdf_vectors, "HybridChunker", lambda: SimpleNamespace(chunk=lambda **kwargs: []))
    monkeypatch.setattr(
        pdf_vectors.PGVector,
        "from_documents",
        lambda **kwargs: pytest.fail("the existing collection must not be deleted"),
    )

    with pytest.raises(RuntimeError, match="No text chunks found"):
        pdf_vectors.ingest_pdf(Path("empty.pdf"), collection="publication")


def test_pdf_targets_requested_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline can load a publication into a collection chosen by the caller."""
    converter = SimpleNamespace(convert=lambda path: SimpleNamespace(document=object()))
    chunk = SimpleNamespace(meta=SimpleNamespace(headings=["Chapter 1"]))
    chunker = SimpleNamespace(chunk=lambda **kwargs: [chunk], contextualize=lambda value: "Some text")
    written: dict[str, object] = {}
    monkeypatch.setattr(pdf_vectors, "DocumentConverter", lambda **kwargs: converter)
    monkeypatch.setattr(pdf_vectors, "HybridChunker", lambda: chunker)
    monkeypatch.setattr(pdf_vectors, "HuggingFaceEmbeddings", lambda **kwargs: object())
    monkeypatch.setattr(pdf_vectors, "vector_connection", lambda: "postgresql+psycopg://test")
    monkeypatch.setattr(pdf_vectors.PGVector, "from_documents", lambda **kwargs: written.update(kwargs))

    assert pdf_vectors.ingest_pdf(Path("other.pdf"), collection="other-publication") == 1
    assert written["collection_name"] == "other-publication"
    assert written["pre_delete_collection"] is True
    assert written["documents"][0].metadata == {"source": "other.pdf", "headings": ["Chapter 1"]}
