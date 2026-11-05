"""Read-only dataset tools and the inner SQL agent from the end-to-end notebook."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from openai import APIError

from tilik_agent.agent.runtime import get_llm
from tilik_agent.agent.schemas import PlanStep
from tilik_agent.agent.tracing import current_callbacks
from tilik_agent.config import LlmConfig, get_agent_config
from tilik_agent.connectors.bps_tools import SQL_TOOLS


def get_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "tilik"),
        user="sql_agent",
        password=os.environ["SQL_AGENT_PASSWORD"],
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )


@tool
def search_datasets(query: str) -> dict[str, Any]:
    """Find BPS datasets relevant to a topic or table identifier.

    Each word is matched separately against dataset titles and measure
    names, and datasets matching more words rank first. A four-digit year
    in the query ranks datasets with that period higher, so include the
    year you need. Return a short list of candidates with dataset.id, BPS
    table code, title, source/domain, and parser status. Use this when the
    relevant dataset ID is unknown. Search results are metadata and contain
    no published values.

    Args:
        query: Topic words, a title fragment, or a measure name, optionally with a year.
    """
    if not query.strip():
        return {"error": "Search query is empty"}
    limit = get_agent_config().dataset_lookup.max_search_results
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH tokens AS (
                SELECT DISTINCT token
                FROM regexp_split_to_table(lower(%(query)s), '[^[:alnum:]]+') AS token
                WHERE token <> ''
            ),
            words AS (SELECT token AS word FROM tokens WHERE token !~ '^\\d{4}$'),
            years AS (SELECT token AS year FROM tokens WHERE token ~ '^\\d{4}$'),
            measure_text AS (
                SELECT dataset_id, lower(string_agg(concat_ws(' ', original_name, canonical_name), ' ')) AS text
                FROM measure
                GROUP BY dataset_id
            ),
            scored AS (
                SELECT d.id, d.bps_table_id, d.tablesource, d.domain_id, d.title, d.parser_status,
                       sum(CASE WHEN lower(d.title) LIKE '%%' || w.word || '%%' THEN 2
                                WHEN coalesce(mt.text, '') LIKE '%%' || w.word || '%%' THEN 1
                                ELSE 0 END) AS score
                FROM dataset d
                LEFT JOIN measure_text mt ON mt.dataset_id = d.id
                CROSS JOIN words w
                GROUP BY d.id, d.bps_table_id, d.tablesource, d.domain_id, d.title, d.parser_status
            )
            SELECT s.id, s.bps_table_id, s.tablesource, s.domain_id, s.title, s.parser_status
            FROM scored s
            WHERE s.score > 0
            ORDER BY s.parser_status = 'parsed' DESC,
                     s.score DESC,
                     EXISTS (
                         SELECT 1 FROM observation o JOIN period p ON p.id = o.period_id
                         WHERE o.dataset_id = s.id AND p.label IN (SELECT year FROM years)
                     ) DESC,
                     length(s.title),
                     s.id
            LIMIT %(limit)s
            """,
            {"query": query, "limit": limit},
        ).fetchall()

    return {
        "datasets": [
            dict(
                zip(
                    ["dataset_id", "bps_table_id", "tablesource", "domain_id", "title", "parser_status"],
                    row,
                    strict=True,
                )
            )
            for row in rows
        ],
        "truncated": len(rows) == limit,
    }


@tool
def describe_dataset(dataset_id: int) -> dict[str, Any]:
    """Inspect the structure of one BPS dataset before retrieving values.

    Return its title and parser status; measures and units; available
    periods; and axes with exact member IDs, codes, and labels. Use this
    after search_datasets, or directly when the dataset ID is already
    known. Choose measure, period, and member filters from this result.
    This tool returns metadata, not numeric observations.

    Args:
        dataset_id: Integer dataset.id, not the BPS code or table title.
    """
    limits = get_agent_config().dataset_lookup
    with get_connection() as conn:
        dataset = conn.execute(
            """
            SELECT id, bps_table_id, tablesource, domain_id,
                   title, parser_status, notes
            FROM dataset WHERE id = %s
            """,
            (dataset_id,),
        ).fetchone()
        if dataset is None:
            return {"error": "Dataset not found"}

        measures = conn.execute(
            """
            SELECT m.id, m.original_name, m.definition,
                   m.source_note, u.source_name
            FROM measure m
            LEFT JOIN unit u ON u.id = m.source_unit_id
            WHERE m.dataset_id = %s
            ORDER BY m.id LIMIT %s
            """,
            (dataset_id, limits.max_measures),
        ).fetchall()
        members = conn.execute(
            """
            SELECT d.id, d.source_code, d.original_label,
                   dm.id, dm.source_code, dm.original_label
            FROM dimension d
            JOIN dimension_member dm ON dm.dimension_id = d.id
            WHERE d.dataset_id = %s
            ORDER BY d.id, dm.id LIMIT %s
            """,
            (dataset_id, limits.max_dimension_members),
        ).fetchall()
        periods = conn.execute(
            """
            SELECT id, label FROM period
            WHERE dataset_id = %s ORDER BY id DESC LIMIT %s
            """,
            (dataset_id, limits.max_periods),
        ).fetchall()

    axes: dict[int, dict[str, Any]] = {}
    for axis_id, axis_code, axis_label, member_id, member_code, member_label in members:
        axis = axes.setdefault(
            axis_id,
            {
                "axis_id": axis_id,
                "source_code": axis_code,
                "label": axis_label,
                "members": [],
            },
        )
        axis["members"].append(
            {
                "member_id": member_id,
                "source_code": member_code,
                "label": member_label,
            }
        )

    return {
        "dataset": dict(
            zip(
                ["id", "bps_table_id", "tablesource", "domain_id", "title", "parser_status", "notes"],
                dataset,
                strict=True,
            )
        ),
        "measures": [
            dict(
                zip(
                    ["measure_id", "original_name", "definition", "source_note", "unit"],
                    row,
                    strict=True,
                )
            )
            for row in measures
        ],
        "axes": list(axes.values()),
        "periods": [{"period_id": period_id, "label": label} for period_id, label in periods],
        "truncated": {
            "measures": len(measures) == limits.max_measures,
            "axes_and_members": len(members) == limits.max_dimension_members,
            "periods": len(periods) == limits.max_periods,
        },
    }


@tool
def get_observations(
    dataset_id: int,
    measure_id: int,
    member_ids: list[int],
    period_labels: list[str],
) -> dict[str, Any]:
    """Retrieve published values for one dataset and measure.

    Return values with their periods, units, source text, status codes,
    and complete axis/member labels. Every ID in member_ids must match
    the same observation (AND semantics); an empty list selects all
    members. Period labels must exactly match those returned by
    describe_dataset; an empty list selects all periods. Report whether
    the response was truncated by its row limit. Do not calculate new
    totals or replace missing values with zero.

    Args:
        dataset_id: Integer dataset.id from search_datasets.
        measure_id: Measure ID belonging to that dataset.
        member_ids: Exact member IDs from describe_dataset.
        period_labels: Exact published period labels, such as ["2015", "2016"].
    """
    if len(member_ids) > 8 or len(period_labels) > 20:
        return {"error": "Too many filters"}

    limit = get_agent_config().dataset_lookup.max_observations
    with get_connection() as conn:
        measure_ok = conn.execute(
            "SELECT 1 FROM measure WHERE id = %s AND dataset_id = %s",
            (measure_id, dataset_id),
        ).fetchone()
        if not measure_ok:
            return {"error": "Measure is not in dataset"}

        selected_members = conn.execute(
            """
            SELECT d.id, dm.id
            FROM dimension_member dm
            JOIN dimension d ON d.id = dm.dimension_id
            WHERE d.dataset_id = %s AND dm.id = ANY(%s::int[])
            """,
            (dataset_id, member_ids),
        ).fetchall()
        if len(selected_members) != len(set(member_ids)) or len({axis_id for axis_id, _ in selected_members}) != len(
            selected_members
        ):
            return {"error": "Invalid members or two members from one axis"}

        rows = conn.execute(
            """
            SELECT o.id, p.label, o.value_numeric, o.source_value_text,
                   o.status_code, u.source_name,
                   array_agg(
                       json_build_object(
                           'axis', d.original_label,
                           'dimension_type', d.dimension_type,
                           'member_id', dm.id,
                           'member', dm.original_label,
                           'canonical_code', dm.canonical_code
                       ) ORDER BY d.id
                   ) AS members
            FROM observation o
            JOIN period p ON p.id = o.period_id
            LEFT JOIN unit u ON u.id = o.source_unit_id
            LEFT JOIN observation_dimension od ON od.observation_id = o.id
            LEFT JOIN dimension d ON d.id = od.dimension_id
            LEFT JOIN dimension_member dm ON dm.id = od.member_id
            WHERE o.dataset_id = %s AND o.measure_id = %s
              AND (%s::text[] IS NULL OR p.label = ANY(%s::text[]))
              AND NOT EXISTS (
                  SELECT 1 FROM unnest(%s::int[]) AS wanted(member_id)
                  WHERE NOT EXISTS (
                      SELECT 1 FROM observation_dimension match_od
                      WHERE match_od.observation_id = o.id
                        AND match_od.member_id = wanted.member_id
                  )
              )
            GROUP BY o.id, p.label, u.source_name
            ORDER BY p.label DESC, o.id
            LIMIT %s
            """,
            (
                dataset_id,
                measure_id,
                period_labels or None,
                period_labels or None,
                member_ids,
                limit + 1,
            ),
        ).fetchall()

    return {
        "dataset_id": dataset_id,
        "measure_id": measure_id,
        "truncated": len(rows) > limit,
        "rows": [
            dict(
                zip(
                    ["observation_id", "period", "value", "source_value_text", "status_code", "unit", "members"],
                    row,
                    strict=True,
                )
            )
            for row in rows[:limit]
        ],
    }


@tool(response_format="content_and_artifact")
def select_series(
    dataset_id: int, measure_id: int, period_ids: list[int], member_ids: list[int]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a complete province series for statistical analysis.

    Use exact IDs from describe_dataset. Include every requested year (through
    the latest available year for 'since' questions). Pin exactly one member
    of every non-geography axis, such as Jumlah and March; leave the province
    axis unpinned. Returns coverage and a selection ID, not numeric rows.
    """
    from tilik_agent.agent.statistics import create_selection

    full = create_selection(dataset_id, measure_id, period_ids, member_ids)
    if "error" in full:
        return full, full
    selection = full["selection"]
    summary = {
        "selection_id": selection["selection_id"],
        "dataset_id": dataset_id,
        "measure_id": measure_id,
        "periods": selection["period_labels"],
        "province_counts": {period: info["count"] for period, info in full["coverage"].items()},
        "missing_province_codes": {
            period: info["missing_from_selected_union"]
            for period, info in full["coverage"].items()
            if info["missing_from_selected_union"]
        },
    }
    return summary, full


SQL_SYSTEM_PROMPT = SystemMessage("""You gather published BPS observations.
Find the relevant parsed datasets, inspect their measures, periods, and members,
then call select_series for statistical analysis or get_observations for ordinary
lookups. Never use get_observations for a full province panel. Use the
metadata and exact member IDs returned by the tools. Do not average published
percentages or combine totals with their component categories. If a tool
returns incomplete or truncated data, do not pretend it is complete.
After the necessary tool calls, give only a short completion status. Do not
write a user-facing report or repeat the observation values.""")


def build_agent(chat_model: ChatOpenAI):
    llm_with_tools = chat_model.bind_tools(SQL_TOOLS)

    def agent_node(state: MessagesState) -> dict[str, Any]:
        response = llm_with_tools.invoke([SQL_SYSTEM_PROMPT, *state["messages"]])
        return {"messages": [response]}

    sql_graph = StateGraph(MessagesState)
    sql_graph.add_node("agent", agent_node)
    sql_graph.add_node("tools", ToolNode(SQL_TOOLS))
    sql_graph.add_edge(START, "agent")
    sql_graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    sql_graph.add_edge("tools", "agent")
    return sql_graph.compile()


_sql_agent: Any = None
_sql_agent_config: LlmConfig | None = None


def get_sql_agent() -> Any:
    """Return the inner SQL graph, rebuilding it when the yaml ``llm`` block changes."""
    global _sql_agent, _sql_agent_config
    config = get_agent_config().llm
    if _sql_agent is not None and _sql_agent_config == config:
        return _sql_agent
    _sql_agent = build_agent(get_llm())
    _sql_agent_config = config
    return _sql_agent


def _tool_payload(message: Any) -> dict[str, Any]:
    content = message.content
    if isinstance(content, dict):
        return content
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {"data": data}
    except (TypeError, ValueError):
        return {"error": str(content)[:500]}


def _harvest_search_datasets(data: dict[str, Any], ctx: dict[str, Any]) -> None:
    ctx["details"]["searches"].append(data)


def _harvest_describe_dataset(data: dict[str, Any], ctx: dict[str, Any]) -> None:
    dataset = data.get("dataset") or {}
    if dataset.get("id") is not None:
        ctx["details"]["datasets"][str(dataset["id"])] = data
    if any(data.get("truncated", {}).values()):
        ctx["errors"].append("describe_dataset returned truncated metadata")


def _harvest_get_observations(data: dict[str, Any], ctx: dict[str, Any]) -> None:
    if data.get("truncated"):
        ctx["errors"].append("get_observations returned a truncated result")
    for row in data.get("rows", []):
        ctx["observations"].append(
            {
                **row,
                "dataset_id": data.get("dataset_id"),
                "measure_id": data.get("measure_id"),
                "step_id": ctx["step_id"],
                "truncated_batch": bool(data.get("truncated")),
            }
        )


def _harvest_select_series(data: dict[str, Any], ctx: dict[str, Any]) -> None:
    selection = data.get("selection")
    if selection:
        ctx.setdefault("selections", []).append({**selection, "step_id": ctx["step_id"]})


TOOL_HANDLERS: dict[str, Any] = {
    "search_datasets": _harvest_search_datasets,
    "describe_dataset": _harvest_describe_dataset,
    "get_observations": _harvest_get_observations,
    "select_series": _harvest_select_series,
}


def _harvest_tool_message(message: Any, ctx: dict[str, Any]) -> None:
    data = _tool_payload(message)
    if data.get("error"):
        ctx["errors"].append(f"{message.name}: {data['error']}")
        return
    handler = TOOL_HANDLERS.get(message.name)
    if handler:
        artifact = getattr(message, "artifact", None)
        handler(artifact if message.name == "select_series" and isinstance(artifact, dict) else data, ctx)


def _sql_step_once(
    step: PlanStep, question: str, dependency: str = "", *, analysis: bool = False, time_scope: str = ""
) -> dict[str, Any]:
    dependency_line = ""
    if dependency:
        dependency_line = (
            "Evidence from dependencies (use it to select the next lookup, but verify values with tools): " + dependency
        )
    prompt = (
        f"Original question: {question}\n"
        f"Current planned lookup: {step.request}\n"
        f"{dependency_line}\n"
        f"Statistical analysis required: {analysis}. Required time scope: {time_scope}.\n"
        + (
            "Use search_datasets, describe_dataset, and select_series. "
            "Select all needed province series with every required period; "
            "pin every category and survey round. Do not fetch the panel "
            "with get_observations.\n"
            if analysis
            else "Use search_datasets, describe_dataset, and get_observations. "
            "Obtain all observations needed for the answer.\n"
        )
        + "Keep all tool results available for the final "
        "answer writer. Finish with only a brief status, not a report."
    )
    ctx: dict[str, Any] = {
        "step_id": step.id,
        "observations": [],
        "selections": [],
        "details": {"searches": [], "datasets": {}},
        "errors": [],
    }
    tool_rounds = 0
    config = get_agent_config()
    max_tool_rounds = config.dataset_lookup.max_tool_rounds
    recursion_limit = config.graph.recursion_limit
    publish = _parent_stream_writer()
    # A graph inheriting the parent's runnable config runs as a subgraph, and LangGraph
    # then forces "values" mode, which hides the per-node updates harvested below.
    parent_config = var_child_runnable_config.set(None)
    try:
        stream_config: dict[str, Any] = {"recursion_limit": recursion_limit}
        callbacks = current_callbacks()
        if callbacks:
            stream_config["callbacks"] = callbacks
        for update in get_sql_agent().stream(
            {"messages": [HumanMessage(content=prompt)]},
            config=stream_config,
            stream_mode="updates",
        ):
            for node_name, payload in update.items():
                publish({node_name: {**payload, "step_id": step.id}})
                for message in payload.get("messages", []):
                    if node_name == "tools":
                        _harvest_tool_message(message, ctx)
                if node_name == "tools":
                    tool_rounds += 1
            if tool_rounds >= max_tool_rounds:
                ctx["errors"].append(f"SQL tool-round limit ({max_tool_rounds}) reached")
                break
    except GraphRecursionError:
        ctx["errors"].append(f"SQL graph recursion limit ({recursion_limit}) reached")
    except (psycopg.Error, ConnectionError, TimeoutError, OSError, APIError, RuntimeError) as exc:
        ctx["errors"].append(f"SQL source error: {exc}")
    finally:
        var_child_runnable_config.reset(parent_config)
    observations: list[dict[str, Any]] = ctx["observations"]
    selections: list[dict[str, Any]] = ctx["selections"]
    errors: list[str] = ctx["errors"]
    if (observations or selections) and errors:
        return {
            "observations": observations,
            "series_selections": selections,
            "source_details": ctx["details"],
            "sql_error": "",
            "sql_warnings": "; ".join(errors),
        }
    return {
        "observations": observations,
        "series_selections": selections,
        "source_details": ctx["details"],
        "sql_error": "; ".join(errors),
        "sql_warnings": "",
    }


def _parent_stream_writer() -> Any:
    """Return the question graph's stream writer, or a no-op outside a running graph."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _: None


def _sql_step(
    step: PlanStep, question: str, dependency: str = "", *, analysis: bool = False, time_scope: str = ""
) -> dict[str, Any]:
    # Single pass: inner sql_agent already retries tool calls; no outer duplicate run.
    return _sql_step_once(step, question, dependency, analysis=analysis, time_scope=time_scope)
