"""Tavily MCP client. The match stage only sees WebSearch.search."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.config import Config
from app.infrastructure.web_search import RESULT_LIMIT, TavilySearch, build_web_search

SECRET = "tvly-test-secret"


def _search(tmp_path, mode="ok", timeout=30.0):
    calls = tmp_path / "tavily-calls.jsonl"
    server = Path(__file__).with_name("fake_tavily_mcp_server.py")
    search = TavilySearch(
        SECRET,
        timeout=timeout,
        launch=lambda: (sys.executable, [str(server), str(calls), mode]),
    )
    return search, calls


def _config(**overrides) -> Config:
    values = dict(database_url="sqlite://", jwt_secret="x" * 40, encryption_key="k")
    values.update(overrides)
    return Config(**values)


def test_search_calls_tavily_search_and_joins_snippets(tmp_path):
    search, calls = _search(tmp_path)

    text = search.search("חיפה", date(2026, 10, 6))

    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert recorded == [
        {
            "arguments": {"query": "חיפה transit disruption OR municipal emergency 2026-10-06"},
            "has_api_key_env": True,
        }
    ]
    assert SECRET not in calls.read_text(encoding="utf-8")
    assert text == "Buses stopped in the city centre.\nMunicipal emergency desk is open."


def test_search_truncates_the_tool_text_to_4000_characters(tmp_path):
    search, _calls = _search(tmp_path, mode="long")

    text = search.search("Haifa", date(2026, 10, 6))

    assert text == "x" * RESULT_LIMIT
    assert len(text) == 4000


def test_a_timed_out_server_raises_without_the_api_key(tmp_path):
    search, _calls = _search(tmp_path, mode="hang", timeout=0.5)

    with pytest.raises(RuntimeError) as caught:
        search.search("Haifa", date(2026, 10, 6))

    assert SECRET not in str(caught.value)


def test_build_web_search_does_not_construct_a_client_in_tests_or_without_a_key():
    assert build_web_search(_config(testing=True, tavily_api_key=SECRET)) is None
    assert build_web_search(_config(tavily_api_key="")) is None
    assert isinstance(build_web_search(_config(tavily_api_key=SECRET)), TavilySearch)


def test_config_repr_hides_the_tavily_key():
    assert SECRET not in repr(_config(tavily_api_key=SECRET))
