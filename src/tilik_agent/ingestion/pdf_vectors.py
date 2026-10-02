"""Load text from a PDF into a pgvector collection."""

from __future__ import annotations

from pathlib import Path

from docling.chunking import HybridChunker
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector

from tilik_agent.config import get_agent_config, vector_connection


def ingest_pdf(pdf: Path, *, collection: str) -> int:
    """Replace a vector collection with chunks from the supplied PDF."""
    if not collection.strip():
        raise ValueError("Collection name is required")
    config = get_agent_config()
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(do_ocr=False, do_table_structure=True)
            )
        }
    )
    document = converter.convert(str(pdf)).document
    chunker = HybridChunker()
    documents: list[Document] = []
    for chunk in chunker.chunk(dl_doc=document):
        content = chunker.contextualize(chunk)
        if content.strip():
            headings = getattr(chunk.meta, "headings", None)
            documents.append(
                Document(
                    page_content=content,
                    metadata={"source": str(pdf), "headings": list(headings) if headings else []},
                )
            )
    if not documents:
        raise RuntimeError(f"No text chunks found in {pdf}; the existing collection was not changed")

    embeddings = HuggingFaceEmbeddings(
        model_name=config.embedding.model,
        model_kwargs={"device": config.embedding.device},
        encode_kwargs={"normalize_embeddings": config.embedding.normalize_embeddings},
    )
    PGVector.from_documents(
        documents=documents,
        embedding=embeddings,
        connection=vector_connection(),
        collection_name=collection,
        use_jsonb=True,
        pre_delete_collection=True,
    )
    return len(documents)
