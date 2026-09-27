"""Langfuse callbacks for one end-to-end question.

The question runner opens one trace. Nested model calls and the inner SQL graph
read callbacks from this context, including work started on a thread-pool worker.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from dataclasses import dataclass
from typing import Any

from langfuse.langchain import CallbackHandler

_trace: ContextVar[TraceBinding | None] = ContextVar("tilik_trace", default=None)
_extra_callbacks: ContextVar[tuple[Any, ...]] = ContextVar("tilik_extra_callbacks", default=())
_handler: ContextVar[CallbackHandler | None] = ContextVar("tilik_langfuse_handler", default=None)


@dataclass(frozen=True)
class TraceBinding:
    """Identifiers that let a worker attach its own handler to the question trace."""

    trace_id: str
    parent_span_id: str


def langfuse_configured() -> bool:
    """Return whether both Langfuse keys are present in the environment."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def current_trace_id() -> str | None:
    """Return the Langfuse trace id for the question in progress, if one is open."""
    binding = _trace.get()
    if binding is None:
        return None
    return binding.trace_id


def current_callbacks() -> list[Any]:
    """Return the Langfuse handler for this thread plus any callbacks the caller added."""
    callbacks: list[Any] = []
    binding = _trace.get()
    handler = _handler.get()
    if binding is not None and handler is None:
        handler = CallbackHandler(
            trace_context={"trace_id": binding.trace_id, "parent_span_id": binding.parent_span_id}
        )
        _handler.set(handler)
    if handler is not None:
        callbacks.append(handler)
    callbacks.extend(_extra_callbacks.get())
    return callbacks


def bind_trace_context(func: Any, /, *args: Any) -> Any:
    """Snapshot the caller context. The returned callable runs ``func`` inside it.

    Capture this on the thread that owns the question trace. A pool worker starts
    with an empty context, so copying inside the worker drops the eval callback.
    """
    context = copy_context()

    def invoke() -> Any:
        def run() -> Any:
            _handler.set(None)
            return func(*args)

        return context.run(run)

    return invoke


def call_with_trace_context(func: Any, /, *args: Any) -> Any:
    """Run ``func`` in the caller's context with a fresh Langfuse handler."""
    return bind_trace_context(func, *args)()


@contextmanager
def question_trace(
    question: str,
    extra_callbacks: Sequence[Any] | None = None,
    *,
    conversation_id: str | None = None,
) -> Iterator[Any]:
    """Open one Langfuse span for ``question`` when keys are set, and always yield."""
    extra_token = _extra_callbacks.set(tuple(extra_callbacks or ()))
    try:
        if not langfuse_configured():
            yield None
            return

        from langfuse import get_client, propagate_attributes

        from tilik_agent.config import get_agent_config

        client = get_client()
        model_name = get_agent_config().llm.model
        trace_attrs: dict[str, Any] = {
            "trace_name": "question",
            "tags": ["tilik"],
            "metadata": {"model": model_name},
        }
        if conversation_id:
            trace_attrs["session_id"] = conversation_id
        with client.start_as_current_observation(name="question", as_type="span", input=question) as span:
            with propagate_attributes(**trace_attrs):
                tokens = _bind_trace(client)
                try:
                    yield span
                finally:
                    _reset_trace(tokens)
        client.flush()
    finally:
        _extra_callbacks.reset(extra_token)


def _bind_trace(client: Any) -> tuple[Token[TraceBinding | None], Token[CallbackHandler | None]]:
    trace_id = client.get_current_trace_id()
    parent_span_id = client.get_current_observation_id()
    binding = TraceBinding(trace_id, parent_span_id) if trace_id and parent_span_id else None
    return _trace.set(binding), _handler.set(None)


def _reset_trace(tokens: tuple[Token[TraceBinding | None], Token[CallbackHandler | None]]) -> None:
    _handler.reset(tokens[1])
    _trace.reset(tokens[0])
