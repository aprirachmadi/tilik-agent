"""LLM client, publication vector store, and evidence formatting."""

from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain_postgres import PGVector
from openai import LengthFinishReasonError
from pydantic import BaseModel

from tilik_agent.agent.schemas import State
from tilik_agent.agent.tracing import current_callbacks
from tilik_agent.config import LlmConfig, get_agent_config, vector_connection

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
        max_tokens=config.max_tokens,
    )
    _chat_config = config
    return _chat


_vector_store: PGVector | None = None
_vector_store_key: tuple[Any, ...] | None = None


def get_vector_store() -> PGVector:
    """Return one vector store, rebuilding it when embedding or collection settings change."""
    from langchain_huggingface import HuggingFaceEmbeddings

    global _vector_store, _vector_store_key
    config = get_agent_config()
    connection = vector_connection()
    key = (
        config.embedding.model,
        config.embedding.device,
        config.embedding.normalize_embeddings,
        config.retrieval.collection,
        connection,
    )
    if _vector_store is not None and _vector_store_key == key:
        return _vector_store
    embedding = HuggingFaceEmbeddings(
        model_name=config.embedding.model,
        model_kwargs={"device": config.embedding.device},
        encode_kwargs={"normalize_embeddings": config.embedding.normalize_embeddings},
    )
    _vector_store = PGVector(
        embeddings=embedding,
        collection_name=config.retrieval.collection,
        connection=connection,
        use_jsonb=True,
    )
    _vector_store_key = key
    return _vector_store


def _invoke_kwargs() -> dict[str, Any]:
    callbacks = current_callbacks()
    if not callbacks:
        return {}
    return {"config": {"callbacks": callbacks}}


def model_json(schema: type[SchemaT], prompt: str) -> SchemaT:
    """Ask the configured model for ``schema`` through a tool call, retrying once when none comes back."""
    structured = get_llm().with_structured_output(schema, method="function_calling")
    for _ in range(2):
        try:
            result = structured.invoke([HumanMessage(content=prompt)], **_invoke_kwargs())
        except LengthFinishReasonError as exc:
            raise ValueError(f"Model exhausted output budget for {schema.__name__}") from exc
        if result is not None:
            return result
    raise ValueError(f"Model returned no {schema.__name__} tool call")


def llm_text(prompt: str) -> str:
    content = get_llm().invoke([HumanMessage(content=prompt)], **_invoke_kwargs()).content
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


def history_block(history: list[dict[str, str]] | None, header: str) -> str:
    """Render the most recent conversation messages for a prompt."""
    if not history:
        return ""
    recent = history[-get_agent_config().conversation.max_history_messages :]
    lines = [f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}" for turn in recent]
    return f"{header}:\n" + "\n".join(lines) + "\n\n"


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
                {key: member.get(key) for key in ("axis", "dimension_type", "member_id", "member", "canonical_code")}
                for member in row.get("members", [])
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


