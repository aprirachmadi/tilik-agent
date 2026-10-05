"""Deterministic question-graph decisions, with the model and database stubbed out."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import ValidationError

from tilik_agent.agent.answer import _check_calculation, build_chart, chart_needed, validate_analysis, write_answer
from tilik_agent.agent.graph import compile_graph
from tilik_agent.agent.schemas import Analysis, Calculation, ChartPoint, ChartSpec, Finding, WorkPlan
from tilik_agent.agent.sql import (
    _harvest_describe_dataset,
    _harvest_get_observations,
    _harvest_search_datasets,
    _harvest_tool_message,
    _sql_step_once,
    _tool_payload,
)
from tilik_agent.agent.workers import _execute_plan, plan_question, route_sources
from tilik_agent.config import get_agent_config


class _ParentState(TypedDict, total=False):
    question: str
    result: dict[str, Any]


def _observation(observation_id: int, value: float | None) -> dict[str, Any]:
    return {"observation_id": observation_id, "value": value}


def _plan(*actions: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        step: dict[str, Any] = {"id": f"s{index}", "action": action, "request": f"do {action}"}
        if action == "calculate":
            step["depends_on"] = [f"s{index - 1}"]
        steps.append(step)
    return {"question": "berapa TPT", "steps": steps, "requested_chart": True}


@pytest.fixture
def calculation_rows() -> dict[int, dict[str, Any]]:
    """Two observation rows used as calculation inputs."""
    return {1: _observation(1, 10), 2: _observation(2, 4)}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"plan_error": "invalid", "source_route": "data"}, "plan_failed"),
        ({"source_route": "context"}, "context"),
        ({"source_route": "data"}, "data"),
        ({"source_route": "both"}, "both"),
        ({"source_route": "direct"}, "direct"),
    ],
)
def test_route_sources(state: dict[str, Any], expected: str) -> None:
    """
    Condition:
    A planned state, including one whose plan failed validation.

    Expected:
    A plan error routes to plan_failed. Any other state keeps its source route.
    """
    assert route_sources(state) == expected


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (("sql_lookup", "retrieve_document"), "both"),
        (("retrieve_document",), "context"),
        (("sql_lookup",), "data"),
        (("sql_lookup", "calculate"), "data"),
    ],
)
def test_plan_question_sets_the_source_route(
    monkeypatch: pytest.MonkeyPatch,
    actions: tuple[str, ...],
    expected: str,
) -> None:
    """
    Condition:
    The planner returns a valid work plan and no model is called.

    Expected:
    SQL plus publication search routes to both, publication search routes to context,
    and a SQL lookup routes to data.
    """
    monkeypatch.setattr("tilik_agent.agent.workers.model_json", lambda schema, prompt: _plan(*actions))
    result = plan_question({"question": "berapa TPT"})
    assert result["source_route"] == expected
    assert result["requested_chart"] is True
    assert result["search_query"] == "berapa TPT"


def test_plan_question_routes_direct_when_there_are_no_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The planner returns a valid work plan with no lookup steps.

    Expected:
    The source route is direct and charting is disabled.
    """
    monkeypatch.setattr(
        "tilik_agent.agent.workers.model_json",
        lambda schema, prompt: {"question": "Hi", "steps": [], "requested_chart": False},
    )
    result = plan_question({"question": "Hi"})
    assert result["source_route"] == "direct"
    assert result["requested_chart"] is False
    assert result["plan"]["steps"] == []


def test_plan_question_retries_once_then_keeps_the_valid_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The first planner response is not a valid work plan and the second response is.

    Expected:
    The retry prompt includes the validation error, and the valid plan is returned.
    """
    prompts: list[str] = []

    def fake_model(schema: type[WorkPlan], prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"question": "berapa TPT", "steps": [{"id": "s1", "action": "calculate", "request": "add them"}]}
        return _plan("sql_lookup")

    monkeypatch.setattr("tilik_agent.agent.workers.model_json", fake_model)
    result = plan_question({"question": "berapa TPT"})
    assert "Previous invalid plan" in prompts[1]
    assert result["source_route"] == "data"


def test_plan_question_reports_a_plan_that_never_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    Both planner attempts raise a validation error.

    Expected:
    The node returns a plan error and no source route.
    """

    def fail(schema: type[WorkPlan], prompt: str) -> dict[str, Any]:
        raise ValueError("not a plan")

    monkeypatch.setattr("tilik_agent.agent.workers.model_json", fail)
    result = plan_question({"question": "berapa TPT"})
    assert result["plan_error"].startswith("Could not validate the work plan:")
    assert "source_route" not in result


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "question": "lookup",
                "steps": [
                    {"id": "s1", "action": "sql_lookup", "request": "lookup tpt"},
                    {"id": "s1", "action": "sql_lookup", "request": "lookup aps"},
                ],
            },
            "Step IDs must be unique",
        ),
        (
            {
                "question": "lookup",
                "steps": [
                    {"id": "s1", "action": "sql_lookup", "request": "lookup tpt", "depends_on": ["s1"]},
                ],
            },
            "Dependencies must refer to earlier steps",
        ),
        (
            {
                "question": "lookup",
                "steps": [
                    {"id": "s1", "action": "sql_lookup", "request": "lookup tpt"},
                    {"id": "s2", "action": "calculate", "request": "add the values", "depends_on": ["s1", "s1"]},
                ],
            },
            "Duplicate dependencies",
        ),
        (
            {"question": "calc", "steps": [{"id": "s1", "action": "calculate", "request": "add them"}]},
            "A calculation must depend on earlier evidence",
        ),
    ],
)
def test_work_plan_rejects_invalid_steps(payload: dict[str, Any], message: str) -> None:
    """
    Condition:
    A work plan with duplicate ids, a backward dependency, a repeated dependency, or a calculation with no source.

    Expected:
    Validation fails with the matching reason.
    """
    with pytest.raises(ValidationError, match=message):
        WorkPlan.model_validate(payload)


def test_work_plan_accepts_an_empty_step_list() -> None:
    """
    Condition:
    A work plan declares no lookup steps for a direct answer.

    Expected:
    Validation succeeds.
    """
    plan = WorkPlan.model_validate({"question": "Hi", "steps": [], "requested_chart": False})
    assert plan.steps == []


@pytest.mark.parametrize(
    ("operation", "result"),
    [
        ("difference", 6),
        ("sum", 14),
        ("mean", 7),
        ("ratio", 2.5),
        ("percent_change", 150),
    ],
)
def test_check_calculation_accepts_each_operation(
    calculation_rows: dict[int, dict[str, Any]],
    operation: str,
    result: float,
) -> None:
    """
    Condition:
    A calculation whose result matches the two stored observation values.

    Expected:
    The check accepts difference, sum, mean, ratio, and percent change.
    """
    calc = Calculation(operation=operation, input_observation_ids=[1, 2], result=result)
    _check_calculation(calc, calculation_rows)


@pytest.mark.parametrize(
    ("operation", "ids", "result", "message"),
    [
        ("difference", [1, 2], 0, "disagrees"),
        ("ratio", [1, 2], 1, "disagrees"),
        ("difference", [1], 10, "Invalid calculation inputs"),
        ("ratio", [1, 3], 1, "Invalid calculation inputs"),
    ],
)
def test_check_calculation_rejects_a_bad_result(
    operation: str,
    ids: list[int],
    result: float,
    message: str,
) -> None:
    """
    Condition:
    A calculation whose result or inputs do not match the stored rows.

    Expected:
    The check raises because the result disagrees or the inputs are invalid.
    """
    rows = {1: _observation(1, 10), 2: _observation(2, 4), 3: _observation(3, 0)}
    calc = Calculation(operation=operation, input_observation_ids=ids, result=result)
    with pytest.raises(ValueError, match=message):
        _check_calculation(calc, rows)


def test_check_calculation_rejects_a_non_finite_input() -> None:
    """
    Condition:
    A sum whose cited observation value is not finite.

    Expected:
    The check raises before comparing the result.
    """
    calc = Calculation(operation="sum", input_observation_ids=[1], result=1)
    with pytest.raises(ValueError, match="Non-finite"):
        _check_calculation(calc, {1: _observation(1, float("inf"))})


def test_validate_analysis_keeps_a_cited_finding(calculation_rows: dict[int, dict[str, Any]]) -> None:
    """
    Condition:
    A finding and a difference both cite stored observation values.

    Expected:
    The analysis is returned unchanged.
    """
    proposed = Analysis(
        findings=[Finding(statement="nilai pertama", value=10, observation_ids=[1])],
        calculations=[Calculation(operation="difference", input_observation_ids=[1, 2], result=6)],
    )
    assert validate_analysis(proposed, list(calculation_rows.values()), []) is proposed


@pytest.mark.parametrize(
    ("finding", "docs", "message"),
    [
        (Finding(statement="kosong"), [], "no source IDs"),
        (Finding(statement="asing", observation_ids=[9]), [], "unknown source ID"),
        (Finding(statement="dokumen", document_ids=["missing"]), [], "unknown source ID"),
        (Finding(statement="salah", value=1, observation_ids=[1]), [], "disagrees"),
    ],
)
def test_validate_analysis_rejects_a_bad_finding(
    finding: Finding,
    docs: list[dict[str, Any]],
    message: str,
) -> None:
    """
    Condition:
    A finding with no source, an unknown id, or a value that is not on the cited row.

    Expected:
    Validation raises the matching reason.
    """
    proposed = Analysis(findings=[finding])
    with pytest.raises(ValueError, match=message):
        validate_analysis(proposed, [_observation(1, 10)], docs)


def test_validate_analysis_rejects_a_missing_calculation_value() -> None:
    """
    Condition:
    A sum cites an observation whose value is missing.

    Expected:
    Validation raises before the arithmetic check.
    """
    proposed = Analysis(
        calculations=[Calculation(operation="sum", input_observation_ids=[1], result=0)],
    )
    with pytest.raises(ValueError, match="missing value"):
        validate_analysis(proposed, [_observation(1, None)], [])


def test_execute_plan_marks_a_lookup_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    One SQL step returns an observation and a warning.

    Expected:
    The step succeeds, and the warning is kept beside the observation.
    """

    def sql_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        return {
            "observations": [_observation(1, 6.96) | {"step_id": step.id}],
            "sql_warnings": "truncated",
        }

    monkeypatch.setattr("tilik_agent.agent.workers._sql_step", sql_step)
    result = _execute_plan({"question": "berapa TPT", "plan": _plan("sql_lookup")})
    assert result["step_status"]["s1"]["status"] == "succeeded"
    assert result["step_status"]["s1"]["warning"] == "truncated"
    assert result["observations"] == [_observation(1, 6.96) | {"step_id": "s1"}]
    assert result["sql_warnings"] == "truncated"


def test_execute_plan_marks_truncated_rows_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    def sql_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        return {
            "observations": [_observation(1, 6.96) | {"step_id": step.id, "truncated_batch": True}],
            "sql_warnings": "get_observations returned a truncated result",
        }

    monkeypatch.setattr("tilik_agent.agent.workers._sql_step", sql_step)
    result = _execute_plan({"question": "berapa TPT", "plan": _plan("sql_lookup")})
    assert result["step_status"]["s1"]["status"] == "partial"


def test_execute_plan_marks_an_empty_lookup_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    One SQL step returns an error and no rows.

    Expected:
    The step is failed and the SQL error is stored on the aggregate.
    """

    def sql_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        return {"sql_error": "no table"}

    monkeypatch.setattr("tilik_agent.agent.workers._sql_step", sql_step)
    result = _execute_plan({"question": "berapa TPT", "plan": _plan("sql_lookup")})
    assert result["step_status"]["s1"]["status"] == "failed"
    assert result["step_status"]["s1"]["error"] == "no table"
    assert result["sql_error"] == "no table"


def test_execute_plan_marks_partial_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A publication search returns a document and a retrieval error.

    Expected:
    The step is partial because it has evidence and a blocking error.
    """

    def retrieve_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        return {"documents": [{"id": "d1", "step_id": step.id}], "retrieval_error": "none graded"}

    monkeypatch.setattr("tilik_agent.agent.workers._retrieve_step", retrieve_step)
    result = _execute_plan({"question": "berapa", "plan": _plan("retrieve_document")})
    assert result["step_status"]["s1"]["status"] == "partial"
    assert result["documents"] == [{"id": "d1", "step_id": "s1"}]


def test_execute_plan_blocks_a_step_after_a_failed_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A SQL lookup fails, and a calculation depends on it.

    Expected:
    The calculation is blocked and does not run.
    """

    def sql_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        return {"sql_error": "no table"}

    def calculate_step(step: Any, prior: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("calculation ran after a failed lookup")

    monkeypatch.setattr("tilik_agent.agent.workers._sql_step", sql_step)
    monkeypatch.setattr("tilik_agent.agent.workers._calculate_step", calculate_step)
    result = _execute_plan({"question": "berapa", "plan": _plan("sql_lookup", "calculate")})
    assert result["step_status"]["s2"]["status"] == "failed"
    assert "s1" in result["step_status"]["s2"]["error"]


def test_execute_plan_keeps_the_latest_copy_of_an_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    Two SQL steps return the same observation id with different values.

    Expected:
    The aggregate keeps one row, and that row is the later step's copy.
    """

    def sql_step(step: Any, question: str, dependency: str = "") -> dict[str, Any]:
        value = 1 if step.id == "s1" else 2
        return {"observations": [_observation(7, value) | {"step_id": step.id}]}

    monkeypatch.setattr("tilik_agent.agent.workers._sql_step", sql_step)
    result = _execute_plan({"question": "berapa", "plan": _plan("sql_lookup", "sql_lookup")})
    assert result["observations"] == [_observation(7, 2) | {"step_id": "s2"}]


@pytest.mark.parametrize(
    ("question", "ids", "requested", "expected"),
    [
        ("berapa", [1, 2], False, "no"),
        ("provinsi tertinggi", [1], False, "no"),
        ("provinsi tertinggi", [1, 2], False, "yes"),
        ("ada perubahan", [1, 2], False, "yes"),
        ("perbandingan provinsi", [1, 2], False, "yes"),
        ("berapa", [1], True, "yes"),
        ("berapa", [], True, "no"),
    ],
)
def test_chart_needed(question: str, ids: list[int], requested: bool, expected: str) -> None:
    """
    Condition:
    An answer cites zero, one, or two observation values, and may request a chart.

    Expected:
    A chart is needed when it was requested, or when a comparison has at least two cited values.
    """
    state = {
        "question": question,
        "requested_chart": requested,
        "answer_observation_ids": ids,
        "observations": [_observation(1, 6.96), _observation(2, 4.85), _observation(3, None)],
    }
    assert chart_needed(state) == expected


def test_build_chart_returns_a_matching_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The chart model returns a point whose value matches the cited observation.

    Expected:
    The node returns that chart spec.
    """
    spec = ChartSpec(
        type="bar",
        title="TPT",
        x_axis="Provinsi",
        y_axis="Persen",
        data=[ChartPoint(label="Papua", series="TPT", value=6.96, unit="persen", observation_id=1)],
    )
    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: spec)
    result = build_chart(
        {
            "question": "tertinggi",
            "answer": "Papua 6,96",
            "answer_observation_ids": [1],
            "observations": [_observation(1, 6.96)],
        }
    )
    assert result["chart"]["data"][0]["value"] == 6.96


def test_build_chart_rejects_a_point_that_changes_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The chart model returns a point whose value differs from the cited observation.

    Expected:
    The node returns no chart and records why.
    """
    spec = ChartSpec(
        type="bar",
        title="TPT",
        x_axis="Provinsi",
        y_axis="Persen",
        data=[ChartPoint(label="Papua", series="TPT", value=7, unit="persen", observation_id=1)],
    )
    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: spec)
    result = build_chart(
        {
            "question": "tertinggi",
            "answer": "Papua 6,96",
            "answer_observation_ids": [1],
            "observations": [_observation(1, 6.96)],
        }
    )
    assert result["chart"] is None
    assert "Chart unavailable" in result["chart_error"]


def test_write_answer_keeps_known_evidence_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The answer draft cites an observation id and a document id that are both in state.

    Expected:
    The node returns the draft text and those ids.
    """

    class _Draft:
        text = "Papua 6,96"
        observation_ids = [1]
        document_ids = ["d1"]

    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: _Draft())
    result = write_answer(
        {
            "question": "berapa",
            "observations": [_observation(1, 6.96)],
            "documents": [{"id": "d1"}],
        }
    )
    assert result == {
        "answer": "Papua 6,96",
        "answer_observation_ids": [1],
        "answer_document_ids": ["d1"],
    }


def test_write_answer_rejects_an_unknown_evidence_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The answer draft cites an observation id that was not retrieved.

    Expected:
    The node returns an unverified answer and no evidence ids.
    """

    class _Draft:
        text = "Papua 6,96"
        observation_ids = [99]
        document_ids: list[str] = []

    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: _Draft())
    result = write_answer({"question": "berapa", "observations": [_observation(1, 6.96)], "documents": []})
    assert result["answer_observation_ids"] == []
    assert result["answer_document_ids"] == []
    assert "unknown evidence" in result["answer"]


def test_statistical_answer_accepts_derived_text_and_list_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = SimpleNamespace(
        text="1. CV turun.\n2. Selisih simpangan baku sekitar 1,83 poin.",
        observation_ids=[],
        document_ids=[],
        analysis_result_ids=["res_1"],
    )
    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: draft)
    result = write_answer({"question": "Apakah kesenjangan menyempit?", "analysis_results": [{"result_id": "res_1"}]})
    assert result["answer"] == draft.text
    assert result["answer_analysis_result_ids"] == ["res_1"]


def test_statistical_answer_still_requires_result_citation(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = SimpleNamespace(text="CV turun.", observation_ids=[], document_ids=[], analysis_result_ids=[])
    monkeypatch.setattr("tilik_agent.agent.answer.model_json", lambda schema, prompt: draft)
    result = write_answer({"question": "Apakah kesenjangan menyempit?", "analysis_results": [{"result_id": "res_1"}]})
    assert "missing or unknown analysis result IDs" in result["answer"]


def test_write_answer_explains_truncated_evidence_after_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(schema: Any, prompt: str) -> Any:
        raise ValueError("Model returned no AnswerDraft tool call")

    monkeypatch.setattr("tilik_agent.agent.answer.model_json", fail)
    result = write_answer(
        {
            "question": "Apakah kesenjangan menyempit?",
            "observations": [_observation(1, 6.96) | {"truncated_batch": True}],
        }
    )
    assert "truncated" in result["answer"]
    assert result["answer_observation_ids"] == []


def test_tool_payload_reads_json_and_reports_unreadable_text() -> None:
    """
    Condition:
    A tool message contains a dict, a JSON object, or text that is not JSON.

    Expected:
    A dict is kept, JSON is parsed, and unreadable text becomes an error payload.
    """
    assert _tool_payload(SimpleNamespace(content={"ok": True})) == {"ok": True}
    assert _tool_payload(SimpleNamespace(content='{"rows": []}')) == {"rows": []}
    assert _tool_payload(SimpleNamespace(content="not json")) == {"error": "not json"}


def test_harvest_stores_searches_datasets_and_observation_rows() -> None:
    """
    Condition:
    Search, describe, and observation payloads from the SQL tools.

    Expected:
    Searches are appended, a dataset is stored by id, and each row keeps its step and truncation flag.
    """
    ctx: dict[str, Any] = {
        "step_id": "s1",
        "observations": [],
        "details": {"searches": [], "datasets": {}},
        "errors": [],
    }
    _harvest_search_datasets({"query": "tpt"}, ctx)
    _harvest_describe_dataset({"dataset": {"id": 3}, "truncated": {"measures": True}}, ctx)
    _harvest_get_observations(
        {"dataset_id": 3, "measure_id": 9, "truncated": True, "rows": [{"observation_id": 1, "value": 6.96}]},
        ctx,
    )
    assert ctx["details"]["searches"] == [{"query": "tpt"}]
    assert "3" in ctx["details"]["datasets"]
    assert ctx["observations"][0]["step_id"] == "s1"
    assert ctx["observations"][0]["dataset_id"] == 3
    assert ctx["observations"][0]["truncated_batch"] is True
    assert "truncated metadata" in ctx["errors"][0]
    assert "truncated result" in ctx["errors"][1]


def test_harvest_tool_message_records_a_tool_error_and_ignores_an_unknown_tool() -> None:
    """
    Condition:
    One tool message carries an error, and another names a tool that has no harvester.

    Expected:
    The error is recorded and the unknown tool adds nothing.
    """
    ctx: dict[str, Any] = {"observations": [], "details": {"searches": [], "datasets": {}}, "errors": []}
    _harvest_tool_message(SimpleNamespace(name="get_observations", content='{"error": "timeout"}'), ctx)
    _harvest_tool_message(SimpleNamespace(name="other", content='{"rows": []}'), ctx)
    assert ctx["errors"] == ["get_observations: timeout"]
    assert ctx["observations"] == []


def test_sql_step_publishes_each_inner_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The inner SQL agent emits an agent turn and a tool turn, and a stream writer is installed.

    Expected:
    Both updates are published in order, each tagged with its plan step.
    """
    published: list[dict[str, Any]] = []

    class _Agent:
        def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: str) -> Any:
            yield {"agent": {"messages": ["lookup"]}}
            yield {"tools": {"messages": [SimpleNamespace(name="search_datasets", content="[]")]}}

    monkeypatch.setattr("tilik_agent.agent.sql.get_sql_agent", lambda: _Agent())
    monkeypatch.setattr("tilik_agent.agent.sql.get_stream_writer", lambda: published.append)
    _sql_step_once(WorkPlan.model_validate(_plan("sql_lookup")).steps[0], "berapa TPT")
    assert [(list(update), next(iter(update.values()))["step_id"]) for update in published] == [
        (["agent"], "s1"),
        (["tools"], "s1"),
    ]


def test_sql_step_inside_a_graph_node_streams_inner_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    A real SQL agent graph with agent and tools nodes runs inside a node of a parent graph that streams
    updates and custom events.

    Expected:
    The parent receives node-keyed agent and tool events tagged with the step, and the tool rows are harvested.
    """
    rows = json.dumps({"dataset_id": 7, "measure_id": 1, "rows": [{"observation_id": 1, "value": 6.96}]})

    def agent(state: MessagesState) -> dict[str, Any]:
        return {"messages": [AIMessage(content="lookup")]}

    def tools(state: MessagesState) -> dict[str, Any]:
        return {"messages": [ToolMessage(content=rows, name="get_observations", tool_call_id="c1")]}

    inner = StateGraph(MessagesState)
    inner.add_node("agent", agent)
    inner.add_node("tools", tools)
    inner.add_edge(START, "agent")
    inner.add_edge("agent", "tools")
    inner.add_edge("tools", END)
    monkeypatch.setattr("tilik_agent.agent.sql.get_sql_agent", lambda: inner.compile())
    step = WorkPlan.model_validate(_plan("sql_lookup")).steps[0]

    def run_sql(state: _ParentState) -> _ParentState:
        return {"result": _sql_step_once(step, state["question"])}

    outer = StateGraph(_ParentState)
    outer.add_node("run_sql", run_sql)
    outer.add_edge(START, "run_sql")
    outer.add_edge("run_sql", END)
    items = list(outer.compile().stream({"question": "berapa TPT"}, stream_mode=["updates", "custom"]))
    custom = [(list(chunk), next(iter(chunk.values()))["step_id"]) for mode, chunk in items if mode == "custom"]
    assert custom == [(["agent"], "s1"), (["tools"], "s1")]
    result = next(chunk["run_sql"]["result"] for mode, chunk in items if mode == "updates")
    assert [row["observation_id"] for row in result["observations"]] == [1]


def test_sql_step_puts_partial_tool_errors_on_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The inner SQL agent returns one observation and then hits the tool-round cap.

    Expected:
    The observation is kept and the cap is a warning, not a SQL error.
    """
    config = get_agent_config()
    limited = config.model_copy(
        update={"dataset_lookup": config.dataset_lookup.model_copy(update={"max_tool_rounds": 1})}
    )

    class _Agent:
        def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: str) -> Any:
            payload = {
                "dataset_id": 3,
                "measure_id": 9,
                "truncated": False,
                "rows": [{"observation_id": 1, "value": 6.96}],
            }
            message = SimpleNamespace(name="get_observations", content=payload)
            yield {"tools": {"messages": [message]}}
            yield {"tools": {"messages": []}}

    monkeypatch.setattr("tilik_agent.agent.sql.get_agent_config", lambda: limited)
    monkeypatch.setattr("tilik_agent.agent.sql.get_sql_agent", lambda: _Agent())
    result = _sql_step_once(WorkPlan.model_validate(_plan("sql_lookup")).steps[0], "berapa TPT")
    assert result["observations"][0]["value"] == 6.96
    assert result["sql_error"] == ""
    assert "tool-round limit (1)" in result["sql_warnings"]


def test_sql_step_reports_a_recursion_limit_without_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Condition:
    The inner SQL agent raises a graph recursion error before any observation is harvested.

    Expected:
    The SQL error names the recursion limit and the warning field stays empty.
    """

    class _Agent:
        def stream(self, state: dict[str, Any], config: dict[str, Any], stream_mode: str) -> Any:
            raise GraphRecursionError("limit")

    monkeypatch.setattr("tilik_agent.agent.sql.get_sql_agent", lambda: _Agent())
    result = _sql_step_once(WorkPlan.model_validate(_plan("sql_lookup")).steps[0], "berapa TPT")
    limit = get_agent_config().graph.recursion_limit
    assert result["observations"] == []
    assert result["sql_error"] == f"SQL graph recursion limit ({limit}) reached"
    assert result["sql_warnings"] == ""


def test_compiled_graph_connects_plan_routes_and_chart() -> None:
    """
    Condition:
    The question graph is compiled without invoking a node.

    Expected:
    Planning fans out to retrieval, SQL, both, or the answer, and the chart is optional.
    """
    graph = compile_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert (START, "plan_question") in edges
    assert {("plan_question", target) for target in ("run_retrieval", "run_sql", "run_both", "write_answer")} <= edges
    assert {("run_retrieval", "write_answer"), ("run_sql", "write_answer"), ("run_both", "write_answer")} <= edges
    assert ("write_answer", "build_chart") in edges
    assert ("write_answer", END) in edges
    assert ("build_chart", END) in edges
