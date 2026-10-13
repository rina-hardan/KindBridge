"""Ollama waits long enough for a short local reply and then stops generation."""

import json
from urllib.request import Request

from app.infrastructure.llm import LLM_TIMEOUT_SECONDS, OLLAMA_NUM_PREDICT, OllamaChat


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
    assert OllamaChat("phi3:latest").complete("hello") == "ok"
    assert captured["timeout"] == LLM_TIMEOUT_SECONDS == 90.0
    assert captured["payload"]["options"]["num_predict"] == OLLAMA_NUM_PREDICT == 256
    assert captured["payload"]["stream"] is False
