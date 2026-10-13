"""Deep Agent supervisor for one ProposeMatchCommand.

``search_filter`` runs load, retrieve, and hard filter. ``match_analysis``
runs capacity, travel, web, and score and write. The numeric score stays in
the score stage. The supervisor never approves or assigns.

When no chat model is configured, or delegation fails or skips a sub-agent,
that sub-agent graph runs directly. A failed delegation does not block the proposal.
"""

import logging
from typing import NotRequired, TypedDict

from deepagents import (
    CompiledSubAgent,
    DeepAgentState,
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated

from app.commands.match_commands import PIPELINE, MatchCommandHandlers, MatchWork
from app.infrastructure.llm import LLM_TIMEOUT_SECONDS, OLLAMA_NUM_PREDICT, OllamaChat, OpenAIChat

logger = logging.getLogger(__name__)

SEARCH_FILTER = PIPELINE[:3]
MATCH_ANALYSIS = PIPELINE[3:]
_SUBAGENTS = ("search_filter", "match_analysis")
_FILESYSTEM_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)
_DELEGATION = (
    "Delegate this one match and then stop. "
    "Call search_filter once, then match_analysis once. "
    "Do not score, approve, assign, or use files."
)
_registered_profiles: set[str] = set()
_HARNESS = HarnessProfile(
    excluded_tools=_FILESYSTEM_TOOLS,
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)
_NO_FILES = [
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
]


class MatchGraphState(TypedDict):
    work: MatchWork
    messages: Annotated[list, add_messages]


class MatchAgentState(DeepAgentState):
    work: NotRequired[MatchWork]


class _MatchGraphs:
    def __init__(self, search, analysis, agent) -> None:
        self.search = search
        self.analysis = analysis
        self.agent = agent


def compile_match_graph(pipeline: MatchCommandHandlers, model=None) -> _MatchGraphs:
    search = _compile_stages(pipeline, SEARCH_FILTER, "search_filter")
    analysis = _compile_stages(pipeline, MATCH_ANALYSIS, "match_analysis")
    agent = None if model is None else _supervisor(search, analysis, model)
    return _MatchGraphs(search, analysis, agent)


def invoke_match_graph(pipeline: MatchCommandHandlers, work: MatchWork, *, model=None) -> MatchWork:
    chosen = _model_from_pipeline(pipeline) if model is None else model
    compiled = pipeline._compiled
    if compiled is None or (chosen is not None and getattr(compiled, "agent", None) is None):
        compiled = compile_match_graph(pipeline, chosen)
        pipeline._compiled = compiled
    if compiled.agent is not None:
        _delegate(compiled.agent, work)
    _complete(compiled, work)
    return work


def _compile_stages(pipeline: MatchCommandHandlers, names: tuple[str, ...], label: str):
    graph = StateGraph(MatchGraphState)
    previous = START
    for name in names:
        graph.add_node(name, _stage(pipeline, name))
        graph.add_edge(previous, name)
        previous = name
    graph.add_node("report", _report(label))
    graph.add_edge(previous, "report")
    graph.add_edge("report", END)
    return graph.compile()


def _stage(pipeline: MatchCommandHandlers, name: str):
    method = getattr(pipeline, name)

    def run(state: MatchGraphState) -> dict:
        method(state["work"])
        return {"work": state["work"]}

    run.__name__ = name
    return run


def _report(label: str):
    def report(state: MatchGraphState) -> dict:
        work = state["work"]
        return {
            "messages": [
                AIMessage(content=f"{label} finished stop={work.stop} outcome={work.outcome or 'pending'}")
            ]
        }

    return report


def _supervisor(search, analysis, model):
    _install_profile(model)
    subagents = [
        CompiledSubAgent(
            name="search_filter",
            description="Load the request, retrieve 15 resumes, and hard-filter that set.",
            runnable=search,
        ),
        CompiledSubAgent(
            name="match_analysis",
            description="Check capacity and travel, read web context, then score and write the proposal.",
            runnable=analysis,
        ),
    ]
    return create_deep_agent(
        model=model,
        system_prompt=_DELEGATION,
        subagents=subagents,
        permissions=_NO_FILES,
        state_schema=MatchAgentState,
        name="match_supervisor",
    )


def _install_profile(model) -> None:
    from deepagents._models import get_model_identifier, get_model_provider

    provider = get_model_provider(model) or ""
    identifier = get_model_identifier(model) or ""
    keys = []
    if provider and identifier:
        keys.append(f"{provider}:{identifier}")
    if provider:
        keys.append(provider)
    for key in keys:
        if key in _registered_profiles:
            continue
        register_harness_profile(key, _HARNESS)
        _registered_profiles.add(key)


def _delegate(agent, work: MatchWork) -> None:
    try:
        agent.invoke(
            {"work": work, "messages": [HumanMessage(content=_DELEGATION)]},
            config={"recursion_limit": 12},
        )
    except Exception:
        logger.exception("match_supervisor_failed")


def _complete(compiled: _MatchGraphs, work: MatchWork) -> None:
    if not _search_done(work):
        compiled.search.invoke({"work": work, "messages": []})
    if not _analysis_done(work):
        compiled.analysis.invoke({"work": work, "messages": []})


def _search_done(work: MatchWork) -> bool:
    return work.stop or work.view is not None or bool(work.outcome)


def _analysis_done(work: MatchWork) -> bool:
    return work.stop or bool(work.outcome)


def _model_from_pipeline(pipeline: MatchCommandHandlers):
    client = getattr(getattr(pipeline, "_safety", None), "_client", None)
    if isinstance(client, OpenAIChat):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=client._model,
            api_key=client._api_key,
            temperature=0,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
    if isinstance(client, OllamaChat):
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=client._model,
            base_url=client._base_url,
            temperature=0,
            num_predict=OLLAMA_NUM_PREDICT,
            client_kwargs={"timeout": LLM_TIMEOUT_SECONDS},
        )
    return None
