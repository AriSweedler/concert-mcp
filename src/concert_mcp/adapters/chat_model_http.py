"""One chat-completions call to the bot's own model over urllib (unit adapter-trial).

`concert-mcp try-image` asks the model OpenClaw uses (an OpenAI-compatible server such as llama.cpp's) which tool it
would call for a screenshot. One request, no streaming, no retries: a trial that cannot reach the model says so.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from concert_mcp.result import ProviderError

TIMEOUT_S = 300  # a 9B model on a small GPU takes a minute or two with the tools in the prompt


class ChatModelClient:
    def __init__(self, base_url: str, model: str, *, urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._urlopen = urlopen

    def complete(self, system: str, user: str, *, max_tokens: int = 400) -> str:
        """The model's text answer to one turn, with no tools; capped, so a small GPU is never busy for minutes."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        answer = self._post({"model": self.model, "messages": messages, "max_tokens": max_tokens})
        return answer["choices"][0]["message"].get("content") or ""

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ProviderError(
                "model", f"{self.base_url}: {type(e).__name__}: {e}", f"Could not reach the model at {self.base_url}."
            ) from e

    def first_tool_call(
        self, *, system: str, user: str, tools: Sequence[dict[str, Any]]
    ) -> tuple[str | None, dict[str, Any], str]:
        """(tool name, arguments, text) of the model's first answer; the name is None when it called no tool."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": list(tools),
            "tool_choice": "auto",
        }
        answer = self._post(body)
        msg = answer["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return None, {}, msg.get("content") or ""
        fn = calls[0]["function"]
        args = fn.get("arguments") or "{}"
        return fn["name"], json.loads(args) if isinstance(args, str) else dict(args), msg.get("content") or ""
