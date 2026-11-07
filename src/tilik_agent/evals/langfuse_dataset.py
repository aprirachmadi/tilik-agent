"""Upsert the JSON question file into a Langfuse dataset."""

from __future__ import annotations

from typing import Any

from langfuse.api.commons.types.dataset_status import DatasetStatus

from tilik_agent.evals.dataset import BenchmarkDataset, ConversationDataset, JudgeDataset


def sync_benchmark(client: Any, dataset: BenchmarkDataset) -> list[Any]:
    """Create or update items, archive removals, and return the items to score."""
    try:
        client.get_dataset(dataset.name)
    except Exception:
        client.create_dataset(name=dataset.name, description="Agent generation benchmark across all data sources")
    created: list[Any] = []
    for item in dataset.items:
        created.append(
            client.create_dataset_item(
                dataset_name=dataset.name,
                id=item.id,
                input=item.question,
                expected_output=item.expected_response,
                metadata={
                    "benchmark_id": item.id, "version": dataset.version,
                    "difficulty": item.difficulty, "sources": item.sources,
                },
            )
        )
    _archive_removed(client, dataset)
    return created


def sync_conversations(client: Any, dataset: ConversationDataset) -> list[Any]:
    """Upsert scripted conversations into a separate Langfuse dataset."""
    try:
        client.get_dataset(dataset.name)
    except Exception:
        client.create_dataset(name=dataset.name, description="Multi-turn demography and social statistics benchmark")
    created = [
        client.create_dataset_item(
            dataset_name=dataset.name,
            id=item.id,
            input=item.user_turns,
            expected_output=item.expected_outcome,
            metadata={"benchmark_id": item.id, "version": dataset.version, "scenario": item.scenario},
        )
        for item in dataset.items
    ]
    _archive_removed(client, dataset)
    return created


def sync_judge_benchmark(client: Any, dataset: JudgeDataset, traces: dict[str, Any]) -> list[Any]:
    """Upsert the frozen answers, tool calls, judged trajectory, and human labels; link the original trace."""
    try:
        client.get_dataset(dataset.name)
    except Exception:
        client.create_dataset(name=dataset.name, description="Saved Tilik answers with human labels")
    created = [
        client.create_dataset_item(
            dataset_name=dataset.name,
            id=item.id,
            # Everything the judges receive: the question and the frozen answer and tool calls.
            input={
                "question": item.question, "history": item.history,
                "generated_response": item.generated_response, "generated_tool_calls": item.generated_tool_calls,
                # The trimmed trace task_completion reads; the raw one is behind source_trace_id.
                "trajectory": (traces.get(item.id) or {}).get("trajectory"),
            },
            expected_output=item.expected_response,
            metadata={
                "benchmark_id": item.id, "version": dataset.version,
                "human_verdict": item.human_label.verdict, "human_reason": item.human_label.reason,
            },
            source_trace_id=item.source.get("trace_id"),
        )
        for item in dataset.items
    ]
    _archive_removed(client, dataset)
    return created


def _archive_removed(client: Any, dataset: BenchmarkDataset | ConversationDataset | JudgeDataset) -> None:
    local_ids = {item.id for item in dataset.items}
    remote = client.get_dataset(dataset.name)
    for item in remote.items:
        status = getattr(item, "status", None)
        status_value = getattr(status, "value", status)
        if item.id in local_ids or status_value == "ARCHIVED":
            continue
        client.create_dataset_item(
            dataset_name=dataset.name,
            id=item.id,
            input=item.input,
            status=DatasetStatus.ARCHIVED,
        )
