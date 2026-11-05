"""End-to-end Tilik question agent."""


def compile_graph():
    from tilik_agent.agent.graph import compile_graph as build

    return build()

__all__ = ["compile_graph"]
