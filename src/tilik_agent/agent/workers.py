"""Planning, calculation, and plan execution from the end-to-end notebook."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from openai import APIError
from pydantic import ValidationError

from tilik_agent.agent.answer import validate_analysis
from tilik_agent.agent.retrieval import _retrieve_step
from tilik_agent.agent.runtime import compact_json, evidence_rows, history_block, model_json
from tilik_agent.agent.schemas import Analysis, PlanStep, State, WorkPlan
from tilik_agent.agent.sql import _sql_step
from tilik_agent.agent.tracing import bind_trace_context

def plan_question(state: State) -> dict[str, Any]:
    original_question = state.get("original_question") or state["question"]
    history = state.get("history") or []
    prompt = f"""Plan source work for a BPS statistics assistant. Return a WorkPlan.
Allowed actions: sql_lookup, retrieve_sp2020, calculate. No web lookup exists.
SQL has BPS observation tables; retrieve_sp2020 has only the SP2020 demography
publication and cannot answer 2024-2025 questions.
Keep dependencies in depends_on. The SQL agent can perform several dependent
dataset lookups in ONE sql_lookup step; prefer that for a SQL-only question.
Do not create calculate steps when the SQL agent can select a ranking as part
of its lookup. Use calculate for arithmetic needed between independent lookups.
Never assume that a document search can provide a missing SQL value.
Set WorkPlan.question to a standalone version of the latest user message. When
recent conversation is present, resolve follow-ups such as "what about 2024?"
into a full question with place, indicator, and period.
Return steps: [] when the latest message needs no new BPS SQL or SP2020 lookup
(greetings, who are you, or confirming numbers already stated in the conversation).
When steps is empty, requested_chart must be false.
{history_block(history, "Recent conversation")}Latest user message: {original_question}"""
    error = ""
    for attempt in range(2):
        try:
            plan = model_json(WorkPlan, prompt if attempt == 0 else prompt + f"\nPrevious invalid plan: {error}")
            plan = WorkPlan.model_validate(plan)
            standalone = plan.question.strip() or original_question
            if not plan.steps:
                return {
                    "original_question": original_question,
                    "question": standalone,
                    "plan": plan.model_dump(),
                    "source_route": "direct",
                    "requested_chart": False,
                    "search_query": standalone,
                }
            actions = {step.action for step in plan.steps}
            route: Literal["context", "data", "both"] = (
                "both"
                if {"sql_lookup", "retrieve_sp2020"} <= actions
                else "context"
                if "retrieve_sp2020" in actions
                else "data"
            )
            return {
                "original_question": original_question,
                "question": standalone,
                "plan": plan.model_dump(),
                "source_route": route,
                "requested_chart": plan.requested_chart,
                "search_query": standalone,
            }
        except (ValidationError, ValueError, json.JSONDecodeError, APIError) as exc:
            error = str(exc)
    return {"plan_error": f"Could not validate the work plan: {error}"}


def route_sources(state: State) -> Literal["context", "data", "both", "direct", "plan_failed"]:
    if state.get("plan_error"):
        return "plan_failed"
    return state["source_route"]


def _calculate_step(step: PlanStep, prior: dict[str, Any]) -> dict[str, Any]:
    """Calculation steps are selected by the LLM and verified from row IDs."""
    rows = prior.get("observations", [])
    if not rows:
        return {"error": "No observations available for calculation"}
    prompt = f"""Select the calculation requested below using only these rows.
Return an Analysis with calculations and findings. Every numeric input must
cite observation IDs. Request: {step.request}\nRows: {compact_json(evidence_rows(rows))}"""
    try:
        proposed = model_json(Analysis, prompt)
        validated = validate_analysis(proposed, rows, prior.get("documents", []))
        return {"analysis": validated.model_dump()}
    except (ValidationError, ValueError, APIError) as exc:
        return {"error": f"Calculation validation failed: {exc}"}


def _merge_result(target: dict[str, Any], result: dict[str, Any]) -> None:
    target["documents"].extend(result.get("documents", []))
    target["observations"].extend(result.get("observations", []))
    source = result.get("source_details", {})
    target["source_details"]["searches"].extend(source.get("searches", []))
    target["source_details"]["datasets"].update(source.get("datasets", {}))
    for key in ("retrieval_error", "sql_error", "sql_warnings"):
        if result.get(key):
            target[key] = "; ".join(filter(None, [target.get(key), result[key]]))
    if result.get("search_query"):
        target["search_query"] = result["search_query"]
    if result.get("analysis"):
        previous = target.setdefault(
            "intermediate_analysis",
            {"findings": [], "calculations": [], "limitations": []},
        )
        for key in ("findings", "calculations", "limitations"):
            previous[key].extend(result["analysis"].get(key, []))


def _execute_plan(state: State) -> dict[str, Any]:
    plan = WorkPlan.model_validate(state["plan"])
    aggregate: dict[str, Any] = {
        "documents": [],
        "observations": [],
        "source_details": {"searches": [], "datasets": {}},
        "step_status": {},
    }
    pending = {step.id: step for step in plan.steps}
    while pending:
        ready = [
            step
            for step in plan.steps
            if step.id in pending and all(dep in aggregate["step_status"] for dep in step.depends_on)
        ]
        if not ready:
            raise ValueError("Validated plan has unresolved dependencies")

        def execute(step: PlanStep) -> tuple[str, dict[str, Any]]:
            failures = [dep for dep in step.depends_on if aggregate["step_status"][dep]["status"] != "succeeded"]
            if failures:
                return step.id, {"error": f"Blocked by failed dependencies: {failures}"}
            dependent_rows = [row for row in aggregate["observations"] if row.get("step_id") in step.depends_on]
            dependent_docs = [doc for doc in aggregate["documents"] if doc.get("step_id") in step.depends_on]
            context = compact_json(
                {
                    "observations": evidence_rows(dependent_rows),
                    "documents": dependent_docs,
                    "calculations": aggregate.get("intermediate_analysis", {}),
                }
            )
            if step.action == "sql_lookup":
                return step.id, _sql_step(step, state["question"], context if step.depends_on else "")
            if step.action == "retrieve_sp2020":
                return step.id, _retrieve_step(step, state["question"], context if step.depends_on else "")
            return step.id, _calculate_step(step, aggregate)

        # Snapshot the question trace here. The pool worker does not inherit it.
        jobs = [bind_trace_context(execute, step) for step in ready]

        def run_job(job: Any) -> tuple[str, dict[str, Any]]:
            return job()

        with ThreadPoolExecutor(max_workers=min(2, len(ready))) as pool:
            results = list(pool.map(run_job, jobs))
        for step_id, result in results:
            _merge_result(aggregate, result)
            step = pending.pop(step_id)
            evidence_count = (
                len(result.get("observations", []))
                + len(result.get("documents", []))
                + len(result.get("analysis", {}).get("findings", []))
            )
            blocking = result.get("error") or result.get("retrieval_error") or (
                result.get("sql_error") if not evidence_count else ""
            )
            warning = result.get("sql_warnings") or ""
            if evidence_count and not blocking:
                status = "succeeded"
            elif evidence_count:
                status = "partial"
            else:
                status = "failed"
            aggregate["step_status"][step_id] = {
                "action": step.action,
                "status": status,
                "error": blocking or "",
                "warning": warning,
                "evidence_count": evidence_count,
            }
    # De-duplicate observations fetched by more than one tool call.
    aggregate["observations"] = list({row["observation_id"]: row for row in aggregate["observations"]}.values())
    return aggregate


def run_retrieval(state: State) -> dict[str, Any]:
    return _execute_plan(state)


def run_sql(state: State) -> dict[str, Any]:
    return _execute_plan(state)


def run_both(state: State) -> dict[str, Any]:
    return _execute_plan(state)
