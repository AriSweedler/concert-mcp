"""The 1Password CLI wrappers on FakeRunner: exact argv, placeholder and failure handling."""

from __future__ import annotations

import pytest

from concert_mcp.adapters.onepassword import OP_PLACEHOLDER, op_available, op_read, op_store_field
from concert_mcp.proc import RunResult, run
from concert_mcp.result import ProviderError
from fakes import FakeRunner, plain


def test_op_available_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("concert_mcp.adapters.onepassword.shutil.which", lambda _name: "/usr/local/bin/op")
    assert op_available() is True
    monkeypatch.setattr("concert_mcp.adapters.onepassword.shutil.which", lambda _name: None)
    assert op_available() is False


def test_op_read_argv_and_value() -> None:
    runner = FakeRunner({("op", "read"): (0, "  s3cret\n", "")})
    assert op_read("Private", "Item", "credential", runner=runner) == "s3cret"
    argv, _env, _cwd = runner.calls[0]
    assert argv == ("op", "read", "op://Private/Item/credential")


def test_op_read_passes_timeout() -> None:
    seen: dict[str, object] = {}

    def entry(argv: list[str], kw: dict[str, object]) -> RunResult:
        seen.update(kw)
        return RunResult(0, "v", "")

    op_read("Private", "Item", "credential", runner=FakeRunner({("op", "read"): entry}), timeout_s=7)
    assert seen["timeout_s"] == 7


def test_op_read_placeholder_refused() -> None:
    runner = FakeRunner({("op", "read"): (0, OP_PLACEHOLDER + "\n", "")})
    with pytest.raises(ProviderError) as ei:
        op_read("Private", "Item", "credential", runner=runner)
    assert ei.value.provider == "op"
    assert ei.value.error == "the 1Password item still holds the placeholder | item='Item' field='credential'"


def test_op_read_empty_refused() -> None:
    runner = FakeRunner({("op", "read"): (0, "   \n", "")})
    with pytest.raises(ProviderError) as ei:
        op_read("Private", "Item", "credential", runner=runner)
    assert "placeholder" in ei.value.error


def test_op_read_rc_1_refused() -> None:
    runner = FakeRunner({("op", "read"): (1, "", "[ERROR] not signed in\n")})
    with pytest.raises(ProviderError) as ei:
        op_read("Private", "Item", "credential", runner=runner)
    assert ei.value.provider == "op"
    assert ei.value.error == (
        "op read failed | ref='op://Private/Item/credential' rc='1' stderr='[ERROR] not signed in'"
    )


def test_op_read_missing_binary_refused() -> None:
    runner = FakeRunner({("op", "read"): lambda _argv, _kw: RunResult(-1, "", "", "binary not found: op")})
    with pytest.raises(ProviderError) as ei:
        op_read("Private", "Item", "credential", runner=runner)
    assert "rc='binary not found: op'" in ei.value.error


def test_op_store_field_argv_and_true() -> None:
    runner = FakeRunner({("op", "item", "edit"): (0, "", "")})
    assert op_store_field("Private", "Spotify - concert-mcp", "refresh token", "rt-1", runner=runner) is True
    argv, _env, _cwd = runner.calls[0]
    assert argv == (
        "op",
        "item",
        "edit",
        "Spotify - concert-mcp",
        "--vault",
        "Private",
        "refresh token[password]=rt-1",
    )


def test_op_store_field_false_on_failure_and_warns(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner({("op", "item", "edit"): (1, "", "denied")})
    assert op_store_field("Private", "Item", "credential", "rt-1", runner=runner) is False
    err = plain(capsys.readouterr().err)
    assert "[WARN]" in err
    assert "could not store the field in 1Password" in err
    assert "rt-1" not in err


def test_op_store_field_false_when_runner_reports_error() -> None:
    runner = FakeRunner({("op", "item", "edit"): lambda _a, _k: RunResult(-1, "", "", "timed out after 90 s")})
    assert op_store_field("Private", "Item", "credential", "v", runner=runner) is False


def test_op_store_field_never_raises() -> None:
    def boom(_argv: list[str], _kw: dict[str, object]) -> RunResult:
        raise RuntimeError("runner exploded")

    runner = FakeRunner({("op", "item", "edit"): boom})
    assert op_store_field("Private", "Item", "credential", "v", runner=runner) is False


def test_op_store_field_never_logs_the_value_at_debug_level(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The real runner at debug level: the argv line shows the command but `<redacted>` in place of the secret."""
    import sys

    from concert_mcp import log

    monkeypatch.setattr("concert_mcp.adapters.onepassword.shutil.which", lambda _name: "/usr/local/bin/op")
    log.set_level("debug")
    try:
        seen: list[tuple[list[str], dict[str, object]]] = []

        def runner(cmd, timeout_s, **kw):
            seen.append((list(cmd), kw))
            return run([sys.executable, "-c", "pass", *cmd[1:]], timeout_s, **kw)

        assert op_store_field("Private", "Item", "refresh token", "S3CRET-VALUE", runner=runner) is True
    finally:
        log.set_level("info")
    err = capsys.readouterr().err
    assert "S3CRET-VALUE" not in err
    assert "<redacted>" in err
    assert "'refresh token[password]=S3CRET-VALUE'" in str(seen[0][0])
    assert seen[0][1] == {"redact_from": 6}
