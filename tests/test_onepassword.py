"""The 1Password CLI wrappers on FakeRunner: exact argv, placeholder and failure handling."""

from __future__ import annotations

import pytest

from concert_mcp.adapters.onepassword import op_available, op_read, op_store_field
from concert_mcp.proc import RunResult
from concert_mcp.result import ProviderError
from fakes import FakeRunner


def test_op_available_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("concert_mcp.adapters.onepassword.shutil.which", lambda _name: "/usr/local/bin/op")
    assert op_available() is True
    monkeypatch.setattr("concert_mcp.adapters.onepassword.shutil.which", lambda _name: None)
    assert op_available() is False


def test_op_read_missing_binary_refused() -> None:
    runner = FakeRunner({("op", "read"): lambda _argv, _kw: RunResult(-1, "", "", "binary not found: op")})
    with pytest.raises(ProviderError) as ei:
        op_read("Private", "Item", "credential", runner=runner)
    assert "rc='binary not found: op'" in ei.value.error


def test_op_store_field_never_raises() -> None:
    def boom(_argv: list[str], _kw: dict[str, object]) -> RunResult:
        raise RuntimeError("runner exploded")

    runner = FakeRunner({("op", "item", "edit"): boom})
    assert op_store_field("Private", "Item", "credential", "v", runner=runner) is False
