"""Publication search, grade, and rewrite loop."""

from __future__ import annotations

from typing import Any

import psycopg
from langchain_core.documents import Document
from openai import APIError
from sqlalchemy.exc import SQLAlchemyError

from tilik_agent.agent.runtime import _parse_json_object, get_vector_store, llm_text
from tilik_agent.agent.schemas import PlanStep
from tilik_agent.config import get_agent_config


def _document_record(doc: Document, step_id: str, rank: int) -> dict[str, Any]:
    metadata = dict(doc.metadata or {})
    return {
        "id": f"{step_id}:doc{rank}",
        "step_id": step_id,
        "content": doc.page_content,
        "metadata": metadata,
    }


def _retrieve_step(step: PlanStep, question: str, dependency: str = "") -> dict[str, Any]:
    query = step.request
    if dependency:
        query += "\nRelevant preceding evidence: " + dependency[:4000]
    retrieval = get_agent_config().retrieval
    try:
        vector_store = get_vector_store()
        for rewrite_count in range(retrieval.max_rewrites + 1):
            hits = vector_store.similarity_search(query, k=retrieval.top_k)
            kept: list[dict[str, Any]] = []
            for rank, doc in enumerate(hits, start=1):
                prompt = f"""Does this excerpt help answer the question?
Return JSON only: {{"binary_score": "yes"}} or {{"binary_score": "no"}}

Question: {query}
Excerpt: {doc.page_content[:800]}"""
                score = _parse_json_object(llm_text(prompt)).get("binary_score", "no")
                if score == "yes":
                    kept.append(_document_record(doc, step.id, rank))
            if kept:
                return {"documents": kept, "search_query": query}
            if rewrite_count < retrieval.max_rewrites:
                query = (
                    llm_text(
                        "Rewrite only this search query for the configured publication. "
                        f"Return only the query. Original request: {question}. Current query: {query}"
                    )
                    .strip()
                    .strip('"')
                    or query
                )
        return {
            "documents": [],
            "search_query": query,
            "retrieval_error": f"No relevant publication excerpts found after {retrieval.max_rewrites} rewrites",
        }
    except (psycopg.Error, SQLAlchemyError, ConnectionError, TimeoutError, OSError, APIError) as exc:
        return {"documents": [], "search_query": query, "retrieval_error": str(exc)}
