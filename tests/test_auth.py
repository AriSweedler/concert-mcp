"""flows/auth.py: the one-time auth commands on a fake 1Password, a fake terminal and fake HTTP, every refusal."""

from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.config import Settings
from concert_mcp.flows import auth
from concert_mcp.result import ProviderError
from fakes import FakeRunner, FakeUrlopen, MemoryState, http_error

TOKEN_URL = "https://accounts.spotify.com/api/token"


@pytest.fixture
def state() -> MemoryState:
    return MemoryState()


def op_table(settings: Settings, item: str, field: str, value: str) -> FakeRunner:
    return FakeRunner({("op", "read", f"op://{settings.op_vault}/{item}/{field}"): (0, value + "\n", "")})


def terminal(monkeypatch: pytest.MonkeyPatch, *typed: str, tty: bool = True) -> None:
    """A terminal that answers getpass with `typed`, in order."""
    answers = list(typed)
    monkeypatch.setattr(auth, "_stdin_is_tty", lambda: tty)
    monkeypatch.setattr(auth, "_getpass", lambda prompt: answers.pop(0))


# ---------- the three key copies: setlist.fm, Places, Gemini ----------


def test_key_auths_refuse_what_they_must(
    settings: Settings, state: MemoryState, monkeypatch: pytest.MonkeyPatch
) -> None:
    for fn, path, provider in (
        (auth.setlistfm_auth, settings.setlistfm_credentials, "setlistfm"),
        (auth.places_auth, settings.places_credentials, "places"),
        (auth.gemini_auth, settings.gemini_credentials, "gemini"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        with pytest.raises(ProviderError) as ei:
            fn(settings, state)
        assert ei.value.provider == provider and "pass --force" in ei.value.error
        path.unlink()
        terminal(monkeypatch, tty=False)
        with pytest.raises(ProviderError) as ei:
            fn(settings, state, manual=True)
        assert "needs a terminal" in ei.value.error
        monkeypatch.setattr(auth, "op_available", lambda: False)
        with pytest.raises(ProviderError) as ei:
            fn(settings, state)
        assert ei.value.provider == "op" and "--manual" in ei.value.error
        terminal(monkeypatch, "short")
        with pytest.raises(ProviderError) as ei:
            fn(settings, state, manual=True)
        assert ei.value.provider == provider and "length='5'" in ei.value.error


def test_key_auths_store_the_key_and_probe(
    settings: Settings, state: MemoryState, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Probe:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.key = kw.get("api_key")

        def search_setlists(self, artist: str, page: int = 1) -> dict[str, Any]:
            return {"total": 7}

        def find_venue(self, query: str) -> Any:
            return type("Hit", (), {"name": "Cafe du Nord"})()

        def probe(self) -> str:
            return "ok"

    monkeypatch.setattr(auth, "SetlistFmClient", Probe)
    monkeypatch.setattr(auth, "GooglePlacesClient", Probe)
    monkeypatch.setattr(auth, "GeminiClient", Probe)
    monkeypatch.setattr(auth, "op_available", lambda: True)
    key = "k" * 40
    # 1Password path, forced over an existing file
    settings.setlistfm_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.setlistfm_credentials.write_text("{}")
    rec = auth.setlistfm_auth(
        settings, state, force=True, runner=op_table(settings, settings.op_setlistfm_item, "credential", key)
    )
    assert rec == {"ok": True, "config": str(settings.setlistfm_credentials), "probe_total": 7}
    assert (
        state.files[settings.setlistfm_credentials] == {"api_key": key}
        and state.modes[settings.setlistfm_credentials] == 0o600
    )
    # the terminal path for the other two
    terminal(monkeypatch, key)
    assert auth.places_auth(settings, state, manual=True) == {
        "ok": True,
        "config": str(settings.places_credentials),
        "probe": "Cafe du Nord",
    }
    terminal(monkeypatch, key)
    assert auth.gemini_auth(settings, state, manual=True) == {
        "ok": True,
        "config": str(settings.gemini_credentials),
        "probe": "ok",
    }
    # Places through 1Password, with a probe that finds nothing
    monkeypatch.setattr(Probe, "find_venue", lambda self, q: None)
    rec = auth.places_auth(
        settings, state, force=True, runner=op_table(settings, settings.op_places_item, "credential", key)
    )
    assert rec["probe"] is None
    rec = auth.gemini_auth(
        settings, state, force=True, runner=op_table(settings, settings.op_gemini_item, "credential", key)
    )
    assert rec["probe"] == "ok"


# ---------- Spotify ----------


def spotify_urlopen(token: dict[str, Any]) -> FakeUrlopen:
    return FakeUrlopen(
        {("POST", TOKEN_URL): token, ("GET", "https://api.spotify.com/v1/me"): {"id": "u1", "display_name": "U"}}
    )


def test_spotify_auth_refusals(settings: Settings, state: MemoryState, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.spotify_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.spotify_credentials.write_text("{}")
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state)
    assert "pass --force" in ei.value.error
    settings.spotify_credentials.unlink()
    terminal(monkeypatch, tty=False)
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state, manual=True)
    assert "needs a terminal" in ei.value.error
    terminal(monkeypatch, "a" * 32, "short")
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state, manual=True)
    assert "client_secret_len='5'" in ei.value.error
    monkeypatch.setattr(auth, "op_available", lambda: False)
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state)
    assert ei.value.provider == "op"
    monkeypatch.setattr(auth, "op_available", lambda: True)
    item, vault = settings.op_spotify_item, settings.op_vault
    placeholder = FakeRunner(
        {
            ("op", "read", f"op://{vault}/{item}/username"): (0, "a" * 32 + "\n", ""),
            ("op", "read", f"op://{vault}/{item}/credential"): (0, "FILL_THIS_OUT\n", ""),
        }
    )
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state, runner=placeholder)
    assert "placeholder" in ei.value.error


def test_spotify_auth_manual_login_without_op(
    settings: Settings, state: MemoryState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal path, a token without a refresh token (refused), then a good one with op absent for the backup."""
    monkeypatch.setattr(auth, "_wait_for_code", lambda *a: "the-code")
    monkeypatch.setattr(auth, "op_available", lambda: False)
    terminal(monkeypatch, "a" * 32, "b" * 32)
    with pytest.raises(ProviderError) as ei:
        auth.spotify_auth(settings, state, manual=True, urlopen=spotify_urlopen({"access_token": "t"}))
    assert "no refresh token" in ei.value.error
    terminal(monkeypatch, "a" * 32, "b" * 32)
    rec = auth.spotify_auth(
        settings, state, manual=True, urlopen=spotify_urlopen({"access_token": "t", "refresh_token": "r"})
    )
    assert rec["ok"] is True and rec["user_id"] == "u1" and rec["scope"] == settings.spotify_scopes
    assert state.files[settings.spotify_credentials]["refresh_token"] == "r"


def test_store_refresh_token_is_best_effort(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "op_available", lambda: True)
    failing = FakeRunner({("op", "item", "edit"): (1, "", "no such item")})
    auth._store_refresh_token(settings, "r", failing)  # logs a warning, raises nothing
    assert failing.calls[0][0][:3] == ("op", "item", "edit")


def test_token_request_errors(settings: Settings) -> None:
    form = {"grant_type": "authorization_code"}
    refused = FakeUrlopen({("POST", TOKEN_URL): http_error(400, '{"error":"invalid_grant"}')})
    with pytest.raises(ProviderError) as ei:
        auth._token_request("a", "b", form, urlopen=refused, timeout_s=5, operator="Admin")
    assert "status='400'" in ei.value.error and "invalid_grant" in ei.value.error
    down = FakeUrlopen({("POST", TOKEN_URL): urllib.error.URLError("no route")})
    with pytest.raises(ProviderError) as ei:
        auth._token_request("a", "b", form, urlopen=down, timeout_s=5, operator="Admin")
    assert "reason='no route'" in ei.value.error


def test_wait_for_code_refuses_a_state_mismatch(tmp_path: Path) -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/callback"
    threads: list[threading.Thread] = []

    def browser(url: str) -> None:
        t = threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}?code=x&state=wrong", timeout=10).read())
        t.start()
        threads.append(t)

    with pytest.raises(ProviderError) as ei:
        auth._wait_for_code("https://accounts.example/authorize", redirect, "right", browser)
    for t in threads:
        t.join(timeout=10)
    assert "state mismatch" in ei.value.error
