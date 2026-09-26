"""Outer question graph from the end-to-end notebook."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tilik_agent.agent.answer import build_chart, chart_needed, write_answer
from tilik_agent.agent.schemas import State
from tilik_agent.agent.workers import plan_question, route_sources, run_both, run_retrieval, run_sql


def compile_graph() -> CompiledStateGraph:
    """Compile the question graph. The caller streams it and supplies run config."""
    graph = StateGraph(State)
    for name, node in (
        ("plan_question", plan_question),
        ("run_retrieval", run_retrieval),
        ("run_sql", run_sql),
        ("run_both", run_both),
        ("write_answer", write_answer),
        ("build_chart", build_chart),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "plan_question")
    graph.add_conditional_edges(
        "plan_question",
        route_sources,
        {"context": "run_retrieval", "data": "run_sql", "both": "run_both", "limited": "write_answer"},
    )
    for source_node in ("run_retrieval", "run_sql", "run_both"):
        graph.add_edge(source_node, "write_answer")
    graph.add_conditional_edges(
        "write_answer",
        chart_needed,
        {"yes": "build_chart", "no": END},
    )
    graph.add_edge("build_chart", END)
    return graph.compile()
