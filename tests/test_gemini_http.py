"""GeminiClient on FakeUrlopen: the key file, the request body, the parse of an answer, the failures."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from concert_mcp.adapters.gemini_http import (
    GEMINI_API,
    GEMINI_FALLBACKS,
    GEMINI_MODEL,
    PROBE_PROMPT,
    GeminiClient,
    parse_answer,
    request_body,
    save_gemini_key,
)
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from concert_mcp.text.lineup import GroundedAnswer
from fakes import FakeUrlopen, MemoryState, http_error

GENERATE = f"{GEMINI_API}/models/{GEMINI_MODEL}:generateContent"
ANSWER = {
    "candidates": [
        {
            "content": {"parts": [{"text": '{"openers": ["Zork"], '}, {"text": '"doors": "7:00 PM"}'}]},
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": "https://venue.example/events/zed-fox", "title": "Zed Fox"}},
                    {"retrievedContext": {"uri": "ignored"}},
                    {"web": {"title": "no uri"}},
                ]
            },
        }
    ]
}


@pytest.fixture
def fake() -> FakeUrlopen:
    return FakeUrlopen()


@pytest.fixture
def client(settings: Settings, fake: FakeUrlopen) -> GeminiClient:
    return GeminiClient(settings, urlopen=fake, api_key="k" * 39)


def test_constructor_without_key_is_unconfigured(settings: Settings, fake: FakeUrlopen) -> None:
    with pytest.raises(ProviderError) as ei:
        GeminiClient(settings, urlopen=fake)
    assert ei.value.provider == "gemini-unconfigured"
    assert "concert-mcp gemini auth" in ei.value.error
    assert ei.value.reply == "Gemini is not set up on this machine. Admin: run `concert-mcp gemini auth`."


def test_if_configured_and_key_file(settings: Settings, fake: FakeUrlopen) -> None:
    assert GeminiClient.if_configured(settings, urlopen=fake) is None
    state = MemoryState()
    save_gemini_key(settings.gemini_credentials, "k" * 39, state)
    assert state.modes[Path(settings.gemini_credentials)] == 0o600
    settings.gemini_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.gemini_credentials.write_text(json.dumps({"api_key": "from-file"}))
    c = GeminiClient.if_configured(settings, urlopen=fake)
    assert isinstance(c, GeminiClient)
    fake.add("POST", GENERATE, ANSWER)
    c.ask("q")
    assert any(k.lower() == "x-goog-api-key" and v == "from-file" for k, v in fake.requests[-1][2].items())


def test_request_body_grounded_and_not() -> None:
    assert request_body("q", grounded=True, mode="search") == {
        "contents": [{"role": "user", "parts": [{"text": "q"}]}],
        "generationConfig": {"temperature": 0.1},
        "tools": [{"url_context": {}}, {"google_search": {}}],
    }
    assert request_body("q", grounded=True, mode="url")["tools"] == [{"url_context": {}}]
    assert "tools" not in request_body("q", grounded=False)


def test_ask_posts_one_grounded_call_with_the_timeout(
    client: GeminiClient, fake: FakeUrlopen, settings: Settings
) -> None:
    seen: dict[str, object] = {}

    fake.add("POST", GENERATE, ANSWER)
    original = fake.__call__

    def spy(req, data=None, timeout=None):  # noqa: ANN001
        seen["timeout"] = timeout
        return original(req, data, timeout)

    client.urlopen = spy
    answer = client.ask("who opens?")
    method, url, headers, body = fake.requests[-1]
    assert (method, url) == ("POST", GENERATE)
    assert headers["Content-type"] == "application/json"
    assert any(k.lower() == "x-goog-api-key" and v == "k" * 39 for k, v in headers.items())
    assert json.loads(body or b"{}") == request_body("who opens?", grounded=True)
    assert seen["timeout"] == settings.gemini_timeout_s == 60
    assert answer == GroundedAnswer(
        '{"openers": ["Zork"], "doors": "7:00 PM"}', ("https://venue.example/events/zed-fox",)
    )


def test_parse_answer_tolerates_any_shape() -> None:
    assert parse_answer({}) == GroundedAnswer("", ())
    assert parse_answer({"candidates": []}) == GroundedAnswer("", ())
    assert parse_answer([1, 2]) == GroundedAnswer("", ())
    assert parse_answer({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}) == GroundedAnswer("x", ())


def test_http_error_is_provider_error_with_the_status(client: GeminiClient, fake: FakeUrlopen) -> None:
    # a 429 or 503 moves on to the next model; when every model refuses, the last refusal is the error
    for model in (GEMINI_MODEL, *GEMINI_FALLBACKS):
        fake.add("POST", f"{GEMINI_API}/models/{model}:generateContent", http_error(429, '{"error": "quota"}'))
    with pytest.raises(ProviderError) as ei:
        client.ask("q")
    assert ei.value.provider == "gemini"
    assert "status='429'" in ei.value.error and "quota" in ei.value.error
    assert f"model='{GEMINI_FALLBACKS[-1]}'" in ei.value.error


def test_a_busy_model_falls_back_to_the_next(client: GeminiClient, fake: FakeUrlopen) -> None:
    fake.add("POST", GENERATE, http_error(503, '{"error": "high demand"}'))
    fake.add("POST", f"{GEMINI_API}/models/{GEMINI_FALLBACKS[0]}:generateContent", ANSWER)
    assert client.ask("q").text == parse_answer(ANSWER).text


def test_a_real_refusal_does_not_fall_back(client: GeminiClient, fake: FakeUrlopen) -> None:
    fake.add("POST", GENERATE, http_error(400, '{"error": "bad"}'))
    with pytest.raises(ProviderError) as ei:
        client.ask("q")
    assert "status='400'" in ei.value.error and f"model='{GEMINI_MODEL}'" in ei.value.error
    assert ei.value.reply == (
        "The lineup lookup failed; the event keeps what the ticket said. Admin: check the host log."
    )


def test_unreachable_and_timeout_are_provider_errors(client: GeminiClient, fake: FakeUrlopen) -> None:
    fake.add("POST", GENERATE, urllib.error.URLError("dns"))
    with pytest.raises(ProviderError) as ei:
        client.ask("q")
    assert "unreachable" in ei.value.error
    fake.add("POST", GENERATE, TimeoutError("timed out"))
    with pytest.raises(ProviderError) as ei:
        client.ask("q")
    assert "timed out" in ei.value.error and "after_s='60'" in ei.value.error


def test_unreadable_answer_is_a_provider_error(client: GeminiClient, fake: FakeUrlopen) -> None:
    fake.add("POST", GENERATE, b"not json")
    with pytest.raises(ProviderError) as ei:
        client.ask("q")
    assert "unreadable" in ei.value.error


def test_probe_is_ungrounded(client: GeminiClient, fake: FakeUrlopen) -> None:
    fake.add("POST", GENERATE, {"candidates": [{"content": {"parts": [{"text": " ok\n"}]}}]})
    assert client.probe() == "ok"
    body = json.loads(fake.requests[-1][3] or b"{}")
    assert body["contents"][0]["parts"][0]["text"] == PROBE_PROMPT
    assert "tools" not in body
