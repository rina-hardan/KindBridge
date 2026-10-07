"""Tavily city-context lookup. A failure is skipped; it does not reject volunteers."""

import json
import urllib.request
from datetime import date


class TavilySearch:
    def __init__(self, api_key: str, timeout: float = 8.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def search(self, city: str, today: date) -> str:
        query = f"{city} transit disruption OR municipal emergency {today.isoformat()}"
        payload = json.dumps({"api_key": self._api_key, "query": query, "max_results": 5}).encode()
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            body = json.loads(response.read().decode())
        snippets = []
        for item in body.get("results") or []:
            text = item.get("content") or item.get("title") or ""
            if text:
                snippets.append(text)
        return "\n".join(snippets)[:4000]
