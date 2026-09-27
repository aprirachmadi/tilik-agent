"""Plan, evidence, chart, and graph state models from the end-to-end notebook."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

Action = Literal["sql_lookup", "retrieve_sp2020", "calculate"]


class PlanStep(BaseModel):
    id: str = Field(pattern=r"^s[1-9][0-9]*$")
    action: Action
    request: str = Field(min_length=3)
    depends_on: list[str] = Field(default_factory=list)


class WorkPlan(BaseModel):
    question: str = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list, max_length=8)
    requested_chart: bool = False

    @model_validator(mode="after")
    def validate_steps(self) -> WorkPlan:
        if not self.steps:
            return self
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique")
        seen: set[str] = set()
        for step in self.steps:
            if len(step.depends_on) != len(set(step.depends_on)):
                raise ValueError("Duplicate dependencies")
            if any(dep not in seen for dep in step.depends_on):
                raise ValueError("Dependencies must refer to earlier steps")
            if step.action == "calculate" and not step.depends_on:
                raise ValueError("A calculation must depend on earlier evidence")
            seen.add(step.id)
        if not any(step.action != "calculate" for step in self.steps):
            raise ValueError("A plan needs at least one source lookup")
        return self


class Finding(BaseModel):
    statement: str
    value: float | None = None
    unit: str | None = None
    observation_ids: list[int] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)


class Calculation(BaseModel):
    operation: Literal["difference", "sum", "mean", "ratio", "percent_change"]
    input_observation_ids: list[int] = Field(min_length=1)
    result: float
    unit: str | None = None


class Analysis(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ChartPoint(BaseModel):
    label: str
    series: str
    value: float
    unit: str | None = None
    observation_id: int


class ChartSpec(BaseModel):
    type: Literal["bar", "line"]
    title: str
    x_axis: str
    y_axis: str
    data: list[ChartPoint] = Field(min_length=1)


class AnswerDraft(BaseModel):
    text: str
    observation_ids: list[int] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)


ChatRole = Literal["user", "assistant"]


class ChatTurn(TypedDict):
    role: ChatRole
    content: str


class State(TypedDict, total=False):
    question: str
    original_question: str
    history: list[ChatTurn]
    search_query: str
    plan: dict[str, Any]
    source_route: Literal["context", "data", "both", "direct"]
    requested_chart: bool
    plan_error: str
    documents: list[dict[str, Any]]
    retrieval_error: str
    observations: list[dict[str, Any]]
    source_details: dict[str, Any]
    sql_error: str
    sql_warnings: str
    step_status: dict[str, dict[str, Any]]
    intermediate_analysis: dict[str, Any]
    chart: dict[str, Any] | None
    chart_error: str
    answer: str
    answer_observation_ids: list[int]
    answer_document_ids: list[str]
