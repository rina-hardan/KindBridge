"""Ollama waits long enough for a short local reply and then stops generation."""

import json
from urllib.request import Request

from app.infrastructure.llm import LLM_TIMEOUT_SECONDS, OLLAMA_NUM_PREDICT, OllamaChat, refuses_request


def test_ollama_request_waits_ninety_seconds_and_caps_tokens(monkeypatch):
    captured: dict = {}

    class _Body:
        def read(self) -> bytes:
            return b'{"response": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    def _urlopen(request: Request, timeout: float):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode())
        return _Body()

    monkeypatch.setattr("app.infrastructure.llm.urllib.request.urlopen", _urlopen)
    assert OllamaChat("qwen2.5:3b").complete("hello") == "ok"
    assert captured["timeout"] == LLM_TIMEOUT_SECONDS == 300.0
    assert captured["payload"]["options"]["num_predict"] == OLLAMA_NUM_PREDICT == 256
    assert captured["payload"]["stream"] is False


def test_refuses_request_reads_json_and_clips_each_text():
    class FakeClient:
        def __init__(self, reply: str) -> None:
            self.reply = reply
            self.prompt = ""

        def complete(self, prompt: str, *, timeout: float = LLM_TIMEOUT_SECONDS) -> str:
            self.prompt = prompt
            return self.reply

    request = "קניות" * 800
    offer = "אני לא עושה קניות" * 200
    refusing = FakeClient('note {"refuses": true}')
    assert refuses_request(refusing, request, offer) is True
    assert '{"refuses": true}' in refusing.prompt
    assert '{"refuses": false}' in refusing.prompt
    assert request[:2000] in refusing.prompt
    assert request[:2001] not in refusing.prompt
    assert offer[:2000] in refusing.prompt
    assert offer[:2001] not in refusing.prompt
    assert refuses_request(FakeClient('{"refuses": false}'), "קניות", "אני יכול לעזור") is False
