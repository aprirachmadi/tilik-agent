"""LLM client, SP2020 vector store, and evidence formatting from the end-to-end notebook."""

from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI
from langchain_postgres import PGVector
from pydantic import BaseModel

from tilik_agent.agent.schemas import State
from tilik_agent.config import LlmConfig, get_agent_config

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_chat: ChatOpenAI | None = None
_chat_config: LlmConfig | None = None


def get_llm() -> ChatOpenAI:
    """Return the chat client, rebuilding it when the yaml ``llm`` block changes."""
    global _chat, _chat_config
    config = get_agent_config().llm
    if _chat is not None and _chat_config == config:
        return _chat
    _chat = ChatOpenAI(
        model=config.model,
        temperature=config.temperature,
        base_url=config.base_url,
        api_key=os.environ[config.api_key_env],
        extra_body=config.extra_body,
    )
    _chat_config = config
    return _chat


def get_vector_store() -> PGVector:
    """Construct lazily so SQL-only questions do not load the embedding model."""
    config = get_agent_config()
    embedding = HuggingFaceEmbeddings(
        model_name=config.embedding.model,
        model_kwargs={"device": config.embedding.device},
        encode_kwargs={"normalize_embeddings": config.embedding.normalize_embeddings},
    )
    return PGVector(
        embeddings=embedding,
        collection_name=config.retrieval.collection,
        connection=os.getenv(
            "PGVECTOR_CONNECTION",
            "postgresql+psycopg://tilik:tilik@localhost:5432/tilik",
        ),
        use_jsonb=True,
    )


def model_json(schema: type[SchemaT], prompt: str) -> SchemaT:
    """Use tool-call structured output, as supported by the source SQL agent's LLM."""
    return get_llm().with_structured_output(schema, method="function_calling").invoke([HumanMessage(content=prompt)])


def llm_text(prompt: str) -> str:
    content = get_llm().invoke([HumanMessage(content=prompt)]).content
    return "".join(str(item) for item in content) if isinstance(content, list) else str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def compact_json(value: Any) -> str:
    # Keep complete JSON; slicing previously hid valid observations.
    return json.dumps(value, ensure_ascii=False, default=str)


def evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep all observations while removing repeated prompt-only fields."""
    fields = (
        "observation_id",
        "dataset_id",
        "measure_id",
        "period",
        "value",
        "source_value_text",
        "status_code",
        "unit",
        "truncated_batch",
    )
    return [
        {
            **{key: row.get(key) for key in fields},
            "members": [
                {key: member.get(key) for key in ("axis", "member_id", "member")} for member in row.get("members", [])
            ],
        }
        for row in rows
    ]


def evidence_source_details(details: dict[str, Any]) -> dict[str, Any]:
    datasets = {}
    for dataset_id, description in details.get("datasets", {}).items():
        dataset = description.get("dataset", {})
        datasets[dataset_id] = {
            "dataset": {key: dataset.get(key) for key in ("id", "bps_table_id", "title", "parser_status")},
            "measures": [
                {key: measure.get(key) for key in ("measure_id", "original_name", "unit", "source_note")}
                for measure in description.get("measures", [])
            ],
            "axes": [
                {
                    "label": axis.get("label"),
                    "members": [
                        {key: member.get(key) for key in ("member_id", "label", "canonical_code")}
                        for member in axis.get("members", [])
                    ],
                }
                for axis in description.get("axes", [])
            ],
        }
    return {"searches": details.get("searches", []), "datasets": datasets}


def observation_index(state: State) -> dict[int, dict[str, Any]]:
    return {int(row["observation_id"]): row for row in state.get("observations", [])}


def document_index(state: State) -> dict[str, dict[str, Any]]:
    return {str(doc["id"]): doc for doc in state.get("documents", [])}


