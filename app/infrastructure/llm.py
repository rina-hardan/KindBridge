"""LLM calls used only for travel safety and the rationale text.

The numeric score is computed in ``app.domain.matching`` and is not read back
from the model. A failure here falls back to the template rationale.
"""

import json
import urllib.request
from typing import Protocol


class LlmClient(Protocol):
    def complete(self, prompt: str, *, timeout: float = 10.0) -> str: ...


class OpenAIChat:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, prompt: str, *, timeout: float = 10.0) -> str:
        from openai import OpenAI

        response = OpenAI(api_key=self._api_key, timeout=timeout).chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


class OllamaChat:
    def __init__(self, model: str, base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, prompt: str, *, timeout: float = 10.0) -> str:
        payload = json.dumps({"model": self._model, "prompt": prompt, "stream": False}).encode()
        request = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
        return str(body.get("response") or "")


def chat_from_settings(provider: str, model: str, api_key: str, ollama_base_url: str) -> LlmClient | None:
    if not model:
        return None
    if provider == "ollama":
        return OllamaChat(model, ollama_base_url)
    if provider == "openai" and api_key:
        return OpenAIChat(api_key, model)
    return None


def unsafe_travel(client: LlmClient, evidence: str) -> bool:
    raw = client.complete(
        'Reply with JSON {"unsafe_travel": true} or {"unsafe_travel": false}. '
        "Use true only when the text reports a transit shutdown, storm, or municipal "
        "emergency that makes travel unsafe today.\n"
        + evidence[:4000],
        timeout=10,
    )
    data = json.loads(_json_object(raw))
    return bool(data.get("unsafe_travel"))


def _json_object(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    return raw[start : end + 1]
