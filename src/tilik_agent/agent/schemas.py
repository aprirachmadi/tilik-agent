"""Plan, evidence, chart, and graph state models from the end-to-end notebook."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanStep(BaseModel):
    id: str = Field(pattern=r"^s[1-9][0-9]*$")
    action: Literal["sql_lookup", "retrieve_document"]
    request: str = Field(min_length=3)


class RequestIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    source_route: Literal["context", "data", "both", "direct"]
    connectors: list[Literal["bps", "bnpb", "bmkg"]] = Field(default_factory=lambda: ["bps"])
    indicator: str | None = None
    analysis_operation: Literal["correlation", "ols", "dispersion", "convergence", "paired_compare"] | None = None
    requested_chart: bool = False
    start_year: int | None = None
    end_year: int | None = None
    through_latest: bool = False

    @model_validator(mode="after")
    def validate_route(self) -> RequestIntent:
        if len(self.connectors) != len(set(self.connectors)) or len(self.connectors) > 2:
            raise ValueError("Choose one or two distinct connectors")
        if self.analysis_operation and self.connectors != ["bps"]:
            raise ValueError("Statistical operations currently use BPS series only")
        if self.analysis_operation and self.source_route not in {"data", "both"}:
            raise ValueError("Statistical analysis needs SQL data")
        if self.source_route == "direct" and self.requested_chart:
            raise ValueError("A direct answer cannot request a chart")
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("Start year follows end year")
        return self


class SeriesSelection(BaseModel):
    selection_id: str
    dataset_id: int
    measure_id: int
    period_ids: list[int]
    period_labels: list[str]
    member_ids: list[int]
    dataset_title: str
    measure_name: str
    unit: str | None = None
    geography_version: str = "source"
    source_note: str | None = None
    coverage: dict[str, Any]
    observation_ids: list[int]


class AnalysisRequest(BaseModel):
    operation: Literal["correlation", "ols", "dispersion", "convergence", "paired_compare"]
    selection_ids: list[str] = Field(min_length=1, max_length=2)
    top_k: int = Field(default=5, ge=1, le=10)


class AnalysisResult(BaseModel):
    result_id: str
    operation: str
    selection_ids: list[str]
    statistics: dict[str, Any]
    input_observation_ids: list[int]
    notes: list[str] = Field(default_factory=list)


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
    external_ids: list[str] = Field(default_factory=list)


ChatRole = Literal["user", "assistant"]


class ChatTurn(TypedDict):
    role: ChatRole
    content: str


class State(TypedDict, total=False):
    question: str
    original_question: str
    history: list[ChatTurn]
    allowed_connectors: list[str] | None
    external_evidence: list[dict[str, Any]]
    cross_source_pairs: list[dict[str, Any]]
    answer_external_ids: list[str]
    used_connectors: list[str]
    search_query: str
    intent: dict[str, Any]
    source_route: Literal["context", "data", "both", "direct"]
    requested_chart: bool
    plan_error: str
    documents: list[dict[str, Any]]
    retrieval_error: str
    observations: list[dict[str, Any]]
    source_details: dict[str, Any]
    sql_error: str
    sql_warnings: str
    series_selections: list[dict[str, Any]]
    analysis_results: list[dict[str, Any]]
    analysis_error: str
    step_status: dict[str, dict[str, Any]]
    intermediate_analysis: dict[str, Any]
    chart: dict[str, Any] | None
    chart_error: str
    answer: str
    answer_observation_ids: list[int]
    answer_document_ids: list[str]
    answer_analysis_selection_ids: list[str]
