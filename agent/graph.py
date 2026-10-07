"""LangGraph pipeline for one ProposeMatchCommand.

Node order is the product order: load, embed and retrieve 15, hard-filter
that set, capacity, travel, web context, then score and write. The graph
never approves or assigns.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.commands.match_commands import PIPELINE, MatchCommandHandlers, MatchWork


class MatchGraphState(TypedDict):
    work: MatchWork


def compile_match_graph(pipeline: MatchCommandHandlers):
    graph = StateGraph(MatchGraphState)
    previous = START
    for name in PIPELINE:
        graph.add_node(name, _stage(pipeline, name))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


def invoke_match_graph(pipeline: MatchCommandHandlers, work: MatchWork) -> MatchWork:
    compiled = pipeline._compiled
    if compiled is None:
        compiled = compile_match_graph(pipeline)
        pipeline._compiled = compiled
    result = compiled.invoke({"work": work})
    return result["work"]


def _stage(pipeline: MatchCommandHandlers, name: str):
    method = getattr(pipeline, name)

    def run(state: MatchGraphState) -> MatchGraphState:
        method(state["work"])
        return state

    run.__name__ = name
    return run
