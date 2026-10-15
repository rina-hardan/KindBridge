"""The match graph is a Deep Agent with two compiled sub-agents.

Delegation is optional. A missing or silent model still runs search_filter
and then match_analysis, and a stage that already finished is not run again.
Delegation is also one sub-agent at a time: the two share one MatchWork, and
LangGraph runs the tool calls of a single reply in parallel.
"""

from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.graph import (
    MATCH_ANALYSIS,
    SEARCH_FILTER,
    _OneDelegationPerReply,
    compile_match_graph,
    invoke_match_graph,
)
from app.commands.match_commands import PIPELINE, MatchWork, ProposeMatchCommand


class SilentModel(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    def bind_tools(self, tools, **kwargs):
        return self

    def _get_ls_params(self, **kwargs):
        return {"ls_provider": "kindbridge", "ls_model_name": "supervisor"}

    @property
    def _llm_type(self) -> str:
        return "silent"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._compiled = None
        self._safety = None

    def _bind(self) -> "Recorder":
        for name in PIPELINE:
            setattr(self, name, self._mark(name))
        return self

    def _mark(self, name: str):
        def run(_work) -> None:
            self.calls.append(name)

        return run


def _work() -> MatchWork:
    return MatchWork(command=ProposeMatchCommand(request_id=uuid4(), match_attempt=0))


def test_pipeline_is_split_into_the_two_subagents():
    assert SEARCH_FILTER + MATCH_ANALYSIS == PIPELINE
    assert SEARCH_FILTER == ("load", "retrieve", "hard_filter")
    assert MATCH_ANALYSIS == ("fit", "capacity", "travel", "web", "score_and_write")


def test_supervisor_exposes_only_the_two_match_subagents():
    compiled = compile_match_graph(Recorder()._bind(), model=SilentModel())
    # The task tool lists sub-agents in its description.
    description = compiled.agent.nodes["tools"].bound.tools_by_name["task"].description
    assert "search_filter" in description
    assert "match_analysis" in description
    assert "- general-purpose:" not in description


def test_silent_supervisor_still_runs_both_subagents_in_order():
    pipeline = Recorder()._bind()
    invoke_match_graph(pipeline, _work(), model=SilentModel())
    assert pipeline.calls == list(PIPELINE)


def test_finished_search_is_not_repeated():
    pipeline = Recorder()._bind()
    work = _work()
    work.view = object()
    invoke_match_graph(pipeline, work, model=SilentModel())
    assert pipeline.calls == list(MATCH_ANALYSIS)


def test_stopped_request_skips_analysis():
    pipeline = Recorder()._bind()
    work = _work()
    work.stop = True
    work.outcome = "noop"
    invoke_match_graph(pipeline, work, model=SilentModel())
    assert pipeline.calls == []


def test_no_model_runs_the_same_order():
    pipeline = Recorder()._bind()
    invoke_match_graph(pipeline, _work())
    assert pipeline.calls == list(PIPELINE)


def _delegation(*subagents: str) -> AIMessage:
    return AIMessage(
        content="",
        id="reply",
        tool_calls=[
            {"name": "task", "args": {"subagent_type": name}, "id": f"call-{name}"}
            for name in subagents
        ],
    )


def test_a_reply_delegating_both_subagents_is_trimmed_to_the_first():
    state = {"messages": [_delegation("search_filter", "match_analysis")]}
    update = _OneDelegationPerReply().after_model(state, None)
    trimmed = update["messages"][0]
    assert trimmed.id == "reply"
    assert [call["args"]["subagent_type"] for call in trimmed.tool_calls] == ["search_filter"]


def test_a_single_delegation_is_left_alone():
    state = {"messages": [_delegation("search_filter")]}
    assert _OneDelegationPerReply().after_model(state, None) is None
