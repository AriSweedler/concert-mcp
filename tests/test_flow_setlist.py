"""flows/setlist.py on FakeSetlistFm: exact date, venue tie-break, within a day, none, and the failures."""

from __future__ import annotations

from typing import Any

from concert_mcp.flows.services import Services
from concert_mcp.flows.setlist import find_setlist
from fakes import FakeSetlistFm

ARTIST = "Zed Fox"
DATE = "2026-11-14"


def with_setlistfm(svc: Services, fake: FakeSetlistFm | None) -> FakeSetlistFm | None:
    svc.setlistfm = fake
    return fake


# ---------- find_setlist ----------


def test_unexpected_exception_is_failed_with_its_type(svc: Services) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("wires crossed")

    svc.setlistfm.search_setlists = boom  # type: ignore[method-assign]
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.match, res.reason) == ("failed", "RuntimeError: wires crossed")


def test_unconfigured_is_unavailable(svc: Services) -> None:
    with_setlistfm(svc, None)
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.found, res.match, res.reason) == (False, "unavailable", "setlist.fm is not set up")


def test_provider_failure_is_failed_with_its_error(svc: Services) -> None:
    with_setlistfm(svc, FakeSetlistFm(fail=True))
    res = find_setlist(ARTIST, DATE, None, svc)
    assert (res.match, res.reason) == ("failed", "setlist.fm refused | path='/search/setlists' status='500'")
