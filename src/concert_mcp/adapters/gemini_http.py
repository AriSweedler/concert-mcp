"""Gemini over urllib: one `generateContent` call per lookup (unit adapter-music).

The model reads the venue's pages for us through the `url_context` tool, so no venue page is ever scraped
here; a free AI Studio key allows that, while Google Search grounding needs a billed key and is added only
when `CONCERT_MCP_GEMINI_GROUNDING=search`. The default model is `gemini-3.5-flash-lite` (the free tier
answers it; the larger Flash models shed free traffic with 503), with `CONCERT_MCP_GEMINI_FALLBACKS` tried in
turn on a 429 or 503. The key comes from `<config_dir>/gemini.json` (`concert-mcp gemini auth` writes it); a
concert costs one call, capped at `CONCERT_MCP_GEMINI_TIMEOUT_S` (60 s, a page read takes twenty or so).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from concert_mcp.adapters.protocols import StateStore
from concert_mcp.adapters.setlistfm_http import load_api_key, save_api_key, user_agent
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug
from concert_mcp.result import ProviderError
from concert_mcp.text.lineup import GroundedAnswer
from concert_mcp.text.reply import provider_reply

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
# Models are settings, so the next retirement or quota change is an .env line, not a release.
GEMINI_MODEL = os.environ.get("CONCERT_MCP_GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite"
GEMINI_FALLBACKS = tuple(
    m.strip()
    for m in (os.environ.get("CONCERT_MCP_GEMINI_FALLBACKS") or "gemini-3.5-flash,gemini-3.8-flash").split(",")
    if m.strip()
)
# "url" reads the pages the prompt names (free tier); "search" adds Google Search grounding (a billed key).
GEMINI_GROUNDING = os.environ.get("CONCERT_MCP_GEMINI_GROUNDING", "").strip().lower() or "url"
RETRY_STATUSES = {429, 503}
PROBE_PROMPT = "Answer with the single word: ok"


def load_gemini_key(path: Path) -> str | None:
    """The api_key in the Gemini key file; None when absent."""
    return load_api_key(path)


def save_gemini_key(path: Path, key: str, state: StateStore) -> None:
    """Write {"api_key": key} with mode 600."""
    save_api_key(path, key, state)


def grounding_tools(mode: str = GEMINI_GROUNDING) -> list[dict[str, Any]]:
    """The tools a grounded call carries: url_context always, Google Search only in "search" mode (pure)."""
    tools: list[dict[str, Any]] = [{"url_context": {}}]
    if mode == "search":
        tools.append({"google_search": {}})
    return tools


def request_body(prompt: str, *, grounded: bool, mode: str = GEMINI_GROUNDING) -> dict[str, Any]:
    """The generateContent body: the prompt, the grounding tools when grounded, a cool temperature (pure)."""
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1},
    }
    if grounded:
        body["tools"] = grounding_tools(mode)
    return body


def parse_answer(data: Any) -> GroundedAnswer:
    """The text parts of the first candidate joined, and the grounding chunks' URIs as the sources (pure)."""
    candidates = data.get("candidates") if isinstance(data, dict) else None
    first = candidates[0] if isinstance(candidates, list) and candidates else {}
    parts = ((first.get("content") or {}).get("parts") or []) if isinstance(first, dict) else []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    chunks = ((first.get("groundingMetadata") or {}).get("groundingChunks") or []) if isinstance(first, dict) else []
    sources = [
        str((c.get("web") or {}).get("uri") or "")
        for c in chunks
        if isinstance(c, dict) and (c.get("web") or {}).get("uri")
    ]
    # url_context names the pages it read; only the ones it actually fetched count as sources
    pages = ((first.get("urlContextMetadata") or {}).get("urlMetadata") or []) if isinstance(first, dict) else []
    sources += [
        str(u.get("retrievedUrl") or "")
        for u in pages
        if isinstance(u, dict) and u.get("retrievedUrl") and "SUCCESS" in str(u.get("urlRetrievalStatus") or "")
    ]
    return GroundedAnswer(text=text, sources=tuple(dict.fromkeys(sources)))


class GeminiClient:
    """GroundedModel over the Gemini API."""

    def __init__(
        self, settings: Settings, *, urlopen: Callable[..., Any] = urllib.request.urlopen, api_key: str | None = None
    ) -> None:
        self.settings = settings
        self.urlopen = urlopen
        key = api_key or load_gemini_key(settings.gemini_credentials)
        if not key:
            raise ProviderError(
                "gemini-unconfigured",
                f"no Gemini key yet | path='{settings.gemini_credentials}' hint='run: concert-mcp gemini auth'",
                provider_reply("gemini-unconfigured", settings.operator_name),
            )
        self._api_key = key

    @classmethod
    def if_configured(cls, settings: Settings, **kw: Any) -> GeminiClient | None:
        """A client when the key file exists; None otherwise."""
        if not Path(settings.gemini_credentials).exists():
            return None
        return cls(settings, **kw)

    def ask(self, prompt: str, *, grounded: bool = True) -> GroundedAnswer:
        """One generateContent call, the fallback models tried on a 429 or 503; ProviderError('gemini') otherwise."""
        last: ProviderError | None = None
        for model in (GEMINI_MODEL, *GEMINI_FALLBACKS):
            try:
                return self._ask(model, prompt, grounded=grounded)
            except ProviderError as e:
                last = e
                if not any(f"status='{code}'" in e.error for code in RETRY_STATUSES):
                    raise
                log_debug("gemini fallback " + kv(model=model, error=e.error[:80]))
        assert last is not None
        raise last

    def _ask(self, model: str, prompt: str, *, grounded: bool) -> GroundedAnswer:
        url = f"{GEMINI_API}/models/{model}:generateContent"
        log_debug("gemini ask " + kv(model=model, grounded=grounded, mode=GEMINI_GROUNDING, chars=len(prompt)))
        req = urllib.request.Request(
            url, data=json.dumps(request_body(prompt, grounded=grounded)).encode("utf-8"), method="POST"
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("x-goog-api-key", self._api_key)
        req.add_header("User-Agent", user_agent())
        operator = self.settings.operator_name
        try:
            with self.urlopen(req, timeout=self.settings.gemini_timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 - a body that cannot be read is not worth a second error
                body = ""
            raise ProviderError(
                "gemini",
                f"gemini refused | status='{e.code}' model='{model}' body='{body}'",
                provider_reply("gemini", operator),
            ) from e
        except urllib.error.URLError as e:
            raise ProviderError(
                "gemini", f"gemini unreachable | reason='{e.reason}'", provider_reply("gemini", operator)
            ) from e
        except (TimeoutError, OSError) as e:
            raise ProviderError(
                "gemini",
                f"gemini timed out | after_s='{self.settings.gemini_timeout_s}' error='{e}'",
                provider_reply("gemini", operator),
            ) from e
        except ValueError as e:
            raise ProviderError(
                "gemini", f"gemini gave an unreadable answer | error='{e}'", provider_reply("gemini", operator)
            ) from e
        return parse_answer(data)

    def probe(self) -> str:
        """One ungrounded call for `gemini auth` and healthcheck --probe: the model's one-word answer."""
        return self.ask(PROBE_PROMPT, grounded=False).text.strip()
