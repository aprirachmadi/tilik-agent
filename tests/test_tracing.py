"""Offline checks that question runs carry callbacks only when a trace is open."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tilik_agent.agent.runtime import llm_text, model_json
from tilik_agent.agent.schemas import Analysis, PlanStep
from tilik_agent.agent.sql import _sql_step_once
from tilik_agent.agent.tracing import (
    TraceBinding,
    _extra_callbacks,
    _handler,
    _trace,
    bind_trace_context,
    call_with_trace_context,
    current_callbacks,
)


class _Sentinel:
    """Stand-in callback recorded by the graph and model calls."""


class _Message:
    content = "ok"


class _InvokeRecorder:
    def __init__(self) -> None:
        self.config: dict[str, Any] | None = None

    def invoke(self, messages: list[Any], config: dict[str, Any] | None = None) -> _Message:
        self.config = config
        return _Message()

    def with_structured_output(self, schema: type[Any], method: str) -> _InvokeRecorder:
        return self


class _SqlRecorder:
    def __init__(self) -> None:
        self.config: dict[str, Any] | None = None

    def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: str) -> Any:
        self.config = config
        yield {"agent": {"messages": []}}


def test_model_json_retries_missing_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = Analysis()

    class _Structured:
        calls = 0

        def invoke(self, messages: list[Any], **kwargs: Any) -> Analysis | None:
            self.calls += 1
            return None if self.calls == 1 else expected

    structured = _Structured()

    class _Chat:
        def with_structured_output(self, schema: type[Any], method: str) -> _Structured:
            assert schema is Analysis
            assert method == "function_calling"
            return structured

    monkeypatch.setattr("tilik_agent.agent.runtime.get_llm", lambda: _Chat())
    assert model_json(Analysis, "test") is expected
    assert structured.calls == 2


def test_model_json_reports_repeated_missing_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Structured:
        def invoke(self, messages: list[Any], **kwargs: Any) -> None:
            return None

    class _Chat:
        def with_structured_output(self, schema: type[Any], method: str) -> _Structured:
            return _Structured()

    monkeypatch.setattr("tilik_agent.agent.runtime.get_llm", lambda: _Chat())
    with pytest.raises(ValueError, match="Model returned no Analysis tool call"):
        model_json(Analysis, "test")






def test_worker_gets_its_own_langfuse_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A question trace is bound on the parent thread, and a worker runs through the copied context.

    Expected:
    The worker receives the same trace id and a different handler instance.
    """

    class _Handler:
        def __init__(self, *, trace_context: dict[str, str]) -> None:
            self.trace_context = trace_context

    monkeypatch.setattr("tilik_agent.agent.tracing.CallbackHandler", _Handler)
    binding = TraceBinding(trace_id="a" * 32, parent_span_id="b" * 16)
    parent_handler = _Sentinel()
    trace_token = _trace.set(binding)
    handler_token = _handler.set(parent_handler)
    seen: dict[str, Any] = {}

    def read_callbacks() -> None:
        seen["callbacks"] = current_callbacks()

    try:
        call_with_trace_context(read_callbacks)
        assert seen["callbacks"][0] is not parent_handler
        assert seen["callbacks"][0].trace_context["trace_id"] == binding.trace_id
        assert current_callbacks()[0] is parent_handler
    finally:
        _handler.reset(handler_token)
        _trace.reset(trace_token)


def test_bound_trace_context_reaches_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A question trace and an extra callback are bound on the parent thread, then the snapshot runs in a pool worker.

    Expected:
    The worker sees that trace and the extra callback.
    """

    class _Handler:
        def __init__(self, *, trace_context: dict[str, str]) -> None:
            self.trace_context = trace_context

    monkeypatch.setattr("tilik_agent.agent.tracing.CallbackHandler", _Handler)
    binding = TraceBinding(trace_id="c" * 32, parent_span_id="d" * 16)
    extra = _Sentinel()
    trace_token = _trace.set(binding)
    handler_token = _handler.set(_Sentinel())
    extra_token = _extra_callbacks.set((extra,))
    seen: dict[str, Any] = {}

    def read_callbacks() -> None:
        seen["callbacks"] = current_callbacks()

    job = bind_trace_context(read_callbacks)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            list(pool.map(lambda bound: bound(), [job]))
        assert seen["callbacks"][0].trace_context["trace_id"] == binding.trace_id
        assert extra in seen["callbacks"]
    finally:
        _extra_callbacks.reset(extra_token)
        _handler.reset(handler_token)
        _trace.reset(trace_token)


def test_model_json_passes_active_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A callback is bound for the current question, and the chat client is replaced with a recorder.

    Expected:
    The structured model call receives that callback.
    """
    recorder = _InvokeRecorder()
    monkeypatch.setattr("tilik_agent.agent.runtime.get_llm", lambda: recorder)
    monkeypatch.setattr("tilik_agent.agent.runtime.current_callbacks", lambda: [_Sentinel()])
    model_json(PlanStep, "plan a lookup for unemployment")
    assert recorder.config is not None
    assert len(recorder.config["callbacks"]) == 1


def test_llm_text_skips_config_without_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    No trace callbacks are bound, and the chat client is replaced with a recorder.

    Expected:
    The text call is invoked without a callback config.
    """
    recorder = _InvokeRecorder()
    monkeypatch.setattr("tilik_agent.agent.runtime.get_llm", lambda: recorder)
    monkeypatch.setattr("tilik_agent.agent.runtime.current_callbacks", lambda: [])
    assert llm_text("grade this excerpt") == "ok"
    assert recorder.config is None


def test_sql_stream_receives_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A callback is bound, and the inner SQL agent is replaced with a recorder.

    Expected:
    The SQL stream config includes that callback and the recursion limit.
    """
    recorder = _SqlRecorder()
    monkeypatch.setattr("tilik_agent.agent.sql.get_sql_agent", lambda: recorder)
    monkeypatch.setattr("tilik_agent.agent.sql.current_callbacks", lambda: [_Sentinel()])
    step = PlanStep(id="s1", action="sql_lookup", request="find the unemployment table")
    _sql_step_once(step, "berapa TPT")
    assert recorder.config is not None
    assert recorder.config["callbacks"]
    assert "recursion_limit" in recorder.config
