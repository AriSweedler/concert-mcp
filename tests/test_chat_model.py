"""adapters/chat_model_http.py on FakeUrlopen: one chat-completions call, the tool call it carries, the failures."""

from __future__ import annotations

import json
import urllib.error

import pytest

from concert_mcp.adapters.chat_model_http import ChatModelClient
from concert_mcp.result import ProviderError
from fakes import FakeUrlopen

URL = "http://orin:8080/v1/chat/completions"


def answer(message: dict) -> dict:
    return {"choices": [{"message": message}]}


def test_complete_and_first_tool_call() -> None:
    fake = FakeUrlopen({("POST", URL): answer({"content": "a movie"})})
    client = ChatModelClient("http://orin:8080/v1/", "qwen", urlopen=fake)
    assert client.complete("sys", "user", max_tokens=5) == "a movie"
    body = json.loads(fake.requests[-1][3] or b"{}")
    assert (
        body["model"] == "qwen"
        and body["max_tokens"] == 5
        and [m["role"] for m in body["messages"]] == ["system", "user"]
    )
    assert (
        ChatModelClient("http://m", "q", urlopen=FakeUrlopen({("POST", "http://m"): answer({})})).complete("", "u")
        == ""
    )

    called = answer({"content": "", "tool_calls": [{"function": {"name": "movie_add", "arguments": '{"film": "G"}'}}]})
    client = ChatModelClient("http://orin:8080/v1", "qwen", urlopen=FakeUrlopen({("POST", URL): called}))
    assert client.first_tool_call(system="s", user="u", tools=[{"type": "function"}]) == (
        "movie_add",
        {"film": "G"},
        "",
    )
    as_dict = answer({"content": "hm", "tool_calls": [{"function": {"name": "lineup", "arguments": {"artist": "Z"}}}]})
    client = ChatModelClient("http://orin:8080/v1", "qwen", urlopen=FakeUrlopen({("POST", URL): as_dict}))
    assert client.first_tool_call(system="s", user="u", tools=[]) == ("lineup", {"artist": "Z"}, "hm")
    client = ChatModelClient(
        "http://orin:8080/v1", "qwen", urlopen=FakeUrlopen({("POST", URL): answer({"content": "no"})})
    )
    assert client.first_tool_call(system="s", user="u", tools=[]) == (None, {}, "no")


def test_an_unreachable_or_unreadable_model_is_a_provider_error() -> None:
    for entry in (urllib.error.URLError("down"), b"not json"):
        client = ChatModelClient("http://orin:8080/v1", "qwen", urlopen=FakeUrlopen({("POST", URL): entry}))
        with pytest.raises(ProviderError) as ei:
            client.complete("s", "u")
        assert ei.value.provider == "model" and ei.value.reply == "Could not reach the model at http://orin:8080/v1."
