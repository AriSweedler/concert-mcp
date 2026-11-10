"""The defensive branches, one each: unreadable state files, a provider that fails mid-flow, a tool whose body
crashes, a bad config value. Grouped by module; every test names the rule it holds."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sqlite3
import sys
import urllib.error
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from concert_mcp.adapters import places_http, setlistfm_http, spotify_http
from concert_mcp.adapters.gemini_http import GEMINI_API, GEMINI_MODEL, GeminiClient
from concert_mcp.adapters.openclaw import OpenClawContext
from concert_mcp.adapters.places_http import GooglePlacesClient
from concert_mcp.adapters.setlistfm_http import SetlistFmClient
from concert_mcp.adapters.spotify_http import API, SpotifyClient, SpotifyCredentials
from concert_mcp.config import Settings, load_settings, migrate, read_config_file
from concert_mcp.flows import concert_add as concert_add_mod
from concert_mcp.flows import healthcheck as hc
from concert_mcp.flows import lineup as lineup_mod
from concert_mcp.flows import poll as poll_mod
from concert_mcp.flows import setlist as setlist_mod
from concert_mcp.flows import setlist_playlist as sp_mod
from concert_mcp.flows import teaser as teaser_mod
from concert_mcp.flows.deliver import _read_fix_log
from concert_mcp.flows.event_delete import _stored_candidates
from concert_mcp.flows.lineup import find_lineup, lineup_outcome, venue_website
from concert_mcp.flows.movie_add import add_movie
from concert_mcp.flows.poll import _patch_event, _songs_for, poll_outcome, poll_setlists, sync_playlist
from concert_mcp.flows.queue import QueueEntry, queue_add, queue_list, read_poll_stamp
from concert_mcp.flows.services import Services, build_services
from concert_mcp.flows.setlist import setlist_outcome
from concert_mcp.flows.setlist_playlist import link_event, setlist_playlist_outcome, sync_setlist_playlist
from concert_mcp.flows.teaser import teaser_outcome
from concert_mcp.flows.topics import make_topic_entry, topic_for_event, topic_forget, topic_remember
from concert_mcp.flows.venues import load_registry, refresh_venue, resolve_venue
from concert_mcp.result import Kind, ProviderError, outcome_from
from concert_mcp.text.failures import Failure, find_failures
from concert_mcp.text.lineup import Lineup, Opener, _json_object, clock_time, lineup_lines
from concert_mcp.text.setlists import Setlist, SetlistResult, Song
from concert_mcp.text.venues import Venue, VenueHit
from conftest import make_services, run, settings_env
from fakes import FakeCalendar, FakeClock, FakeGemini, FakeSetlistFm, FakeUrlopen, FakeVenueLookup, MemoryState
from test_failures import T0, assistant, shot
from test_flow_poll import RUN1, FakeSync, page, seed


class BrokenState(MemoryState):
    """A state store whose reads or writes of the named paths fail."""

    def __init__(self, *, read_fails: tuple[Path, ...] = (), write_fails: tuple[Path, ...] = ()) -> None:
        super().__init__()
        self.read_fails, self.write_fails = read_fails, write_fails

    def read_json(self, path: Path, default: Any) -> Any:
        if Path(path) in self.read_fails:
            raise OSError("disk gone")
        return super().read_json(path, default)

    def write_json(self, path: Path, obj: Any, *, mode: int | None = None) -> None:
        if Path(path) in self.write_fails:
            raise OSError("disk full")
        super().write_json(path, obj, mode=mode)


class _Unreadable(urllib.error.HTTPError):
    def read(self, *a: Any) -> bytes:
        raise OSError("connection reset while reading the body")


def unreadable_http_error(code: int) -> urllib.error.HTTPError:
    """An HTTPError whose body cannot be read."""
    return _Unreadable("https://x", code, "err", None, None)  # type: ignore[arg-type]


# ---------- state files that are not what they should be ----------


def test_unreadable_or_malformed_state_files_read_as_empty(svc_cli: Services) -> None:
    s = svc_cli.settings
    svc_cli.state.write_json(s.fix_log, ["not", "a", "dict"])
    assert _read_fix_log(svc_cli) == {}
    svc_cli.state = BrokenState(read_fails=(s.fix_log,))
    assert _read_fix_log(svc_cli) == {}
    svc_cli.state = MemoryState()
    svc_cli.state.write_json(s.candidates_file, {"not": "a list"})
    assert _stored_candidates(svc_cli) == []
    svc_cli.state.write_json(s.candidates_file, [{"id": 1}, "not a row"])
    assert _stored_candidates(svc_cli) == []
    svc_cli.state.write_json(s.state_dir / "setlist-poll.stamp", {"last_run": "not a time"})
    assert read_poll_stamp(svc_cli) is None
    svc_cli.state.write_json(s.setlist_queue, [{"event_id": "e1", "until": 5, "song_count": "x"}, 3])
    assert [(e.event_id, e.until, e.song_count) for e in queue_list(svc_cli)] == [("e1", "5", 0)]
    svc_cli.state.write_json(s.venues_file, ["not", "a dict"])
    assert load_registry(svc_cli) == {}
    svc_cli.state.write_json(s.venues_file, {"k": "not a venue"})
    assert load_registry(svc_cli) == {}


def test_topic_registry_forget_and_lookup(svc_cli: Services, now: datetime) -> None:
    assert topic_forget("chat:1", svc_cli) is False
    topic_remember("chat:1", make_topic_entry(event_id="e1", calendar_id="c", summary="S", start=now, now=now), svc_cli)
    assert topic_for_event("e1", svc_cli) == "chat:1" and topic_for_event("e2", svc_cli) is None
    assert topic_forget("chat:1", svc_cli) is True


def test_venue_registry_survives_a_failed_write_and_refreshes_in_place(svc_cli: Services) -> None:
    hit = VenueHit(
        name="Cafe du Nord", address_lines=("2174 Market St",), website="https://cafedunord.com", source="places"
    )
    svc_cli.places = FakeVenueLookup({"cafe du nord": hit})
    svc_cli.state = BrokenState(write_fails=(svc_cli.settings.venues_file,))
    res = resolve_venue("Cafe du Nord, San Francisco", "", svc_cli, date="2026-11-14")
    assert res is not None and res.new and res.venue.name == "Cafe du Nord"
    svc_cli.state = MemoryState()
    first = resolve_venue("Cafe du Nord", "", svc_cli, date="2026-11-14")
    assert first is not None
    svc_cli.places = FakeVenueLookup({})
    assert refresh_venue("Cafe du Nord", svc_cli) == first.venue  # no answer: the stored one
    newer = VenueHit(name="Cafe du Nord", address_lines=("2174 Market St",), website="", source="places")
    svc_cli.places = FakeVenueLookup({"cafe du nord": newer})
    refreshed = refresh_venue("Cafe du Nord", svc_cli)
    assert (
        refreshed is not None and refreshed.visits == ["2026-11-14"] and refreshed.website == "https://cafedunord.com"
    )
    assert venue_website("Cafe du Nord", svc_cli) == "https://cafedunord.com"
    assert venue_website("", svc_cli) is None and venue_website("Nowhere Hall", svc_cli) is None


def test_concert_add_still_replies_when_the_queue_or_the_topic_cannot_be_written(
    svc: Services, ticket_text: str
) -> None:
    s = svc.settings
    svc.state = BrokenState(write_fails=(s.setlist_queue, s.topics_file))
    out = concert_add_mod.add_concert(
        svc=svc, artist="Zed Fox", start="2026-11-14 20:30", venue="The Quarry", source_text=ticket_text
    )
    assert out.kind is Kind.OK
    assert "Not queued for the setlist check: OSError" in out.reply and "Topic not remembered: OSError" in out.reply


# ---------- flows: a provider failing mid-way, a body that crashes ----------


def test_tool_outcomes_turn_a_crash_into_the_crash_reply(svc_cli: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(teaser_mod, "make_teaser", boom)
    monkeypatch.setattr(setlist_mod, "find_setlist", boom)
    monkeypatch.setattr(sp_mod, "setlist_id_of", boom)
    monkeypatch.setattr(lineup_mod, "find_lineup", boom)
    monkeypatch.setattr(poll_mod, "queue_list", boom)
    svc_cli.gemini = FakeGemini("{}")
    for out in (
        teaser_outcome("Zed Fox", "2026-11-14", svc_cli),
        setlist_outcome("Zed Fox", "2026-11-14", "", svc_cli),
        setlist_playlist_outcome("https://www.setlist.fm/setlist/x/2026/y-1a2b3c4d.html", "", "", "", svc_cli),
        lineup_outcome("Zed Fox", "2026-11-14", "The Quarry", svc_cli),
        poll_outcome(svc_cli),
    ):
        assert out.kind is Kind.ERROR and "RuntimeError" in (out.error or ""), out


def test_outcome_from_refuses_other_exceptions() -> None:
    with pytest.raises(TypeError):
        outcome_from(RuntimeError("x"))  # type: ignore[arg-type]


def test_lineup_failures_and_the_topic_show(svc: Services, settings: Settings, now: datetime, ticket_text: str) -> None:
    class Model:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def ask(self, prompt: str, *, grounded: bool = True) -> Any:
            raise self.exc

    svc.gemini = Model(ProviderError("gemini", "refused", ""))
    assert find_lineup("Zed Fox", "2026-11-14", "The Quarry", "https://v", svc).reason == "refused"
    svc.gemini = Model(ValueError("odd"))
    assert find_lineup("Zed Fox", "2026-11-14", "The Quarry", "https://v", svc).reason == "ValueError: odd"
    assert lineup_outcome("Zed Fox", "2026-11-14", "The Quarry", svc).kind is Kind.ERROR
    # the topic owns an event that is gone from the calendar: no topic show
    topic_remember(
        "-1001234567890:7",
        make_topic_entry(event_id="gone", calendar_id="cal-concerts", summary="S", start=now, now=now),
        svc,
    )
    assert lineup_mod._topic_show(svc) is None
    assert lineup_mod._topic_show(make_services(settings, now, route=None, text=ticket_text)) is None


def test_lineup_text_edges() -> None:
    assert _json_object('```json\n{"openers": []}\n```') == {"openers": []}
    assert _json_object("{not json}") is None and _json_object("[1, 2]") is None and _json_object("") is None
    assert clock_time("19:30") == "7:30 PM" and clock_time("00:15") == "12:15 AM"
    assert clock_time("25:00") is None and clock_time("7:75 pm") is None and clock_time("13 pm") is None
    full = Lineup("found", openers=(Opener("Zork", None),), doors="7:00 PM", headliner_time="9:00 PM")
    assert lineup_lines(full, "Zed Fox", openers_known=False) == [
        "Headliner: Zed Fox",
        "Opener: Zork",
        "Expected: doors 7:00 PM, Zed Fox 9:00 PM",
    ]
    assert lineup_lines(Lineup("none"), "Zed Fox", openers_known=False) == []


def test_setlist_playlist_failures(svc_cli: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    songs = [Song(name="Hum")]
    svc_cli.spotify.fail = "create_playlist"  # type: ignore[union-attr]
    res = sync_setlist_playlist(
        artist="Zed Fox", date="2026-11-14", songs=songs, setlistfm_url="https://s", svc=svc_cli
    )
    assert res.status == "failed" and "refused" in (res.reason or "")
    svc_cli.spotify.fail = None  # type: ignore[union-attr]
    monkeypatch.setattr(sp_mod, "track_query", lambda *a, **kw: (_ for _ in ()).throw(ValueError("odd")))
    res = sync_setlist_playlist(
        artist="Zed Fox", date="2026-11-14", songs=songs, setlistfm_url="https://s", svc=svc_cli
    )
    assert res.status == "failed" and res.reason == "ValueError: odd"
    monkeypatch.undo()
    cal: FakeCalendar = svc_cli.calendar  # type: ignore[assignment]
    cal.seed_event(
        "cal-concerts", summary="Zed Fox", start=datetime(2026, 11, 14, 20, 30, tzinfo=svc_cli.tz), location="Q"
    )
    cal.fail = "patch_event"
    note = link_event("Zed Fox", "2026-11-14", "https://open.spotify.com/playlist/p", svc_cli)
    assert note is not None and note.startswith("Calendar event not updated:")
    cal.fail = None
    svc_cli.setlistfm = FakeSetlistFm({}, fail=True)
    out = setlist_playlist_outcome("", "Zed Fox", "2026-11-14", "", svc_cli)
    assert out.kind is Kind.ERROR and out.reply.startswith("Setlist lookup failed:")
    svc_cli.setlistfm = None
    out = setlist_playlist_outcome("", "Zed Fox", "2026-11-14", "", svc_cli)
    assert out.kind is Kind.ERROR and "setlist.fm is not set up" in out.reply


def test_setlist_playlist_needs_a_name_and_a_date_the_page_lacks(svc_cli: Services) -> None:
    bare = {
        "id": "1a2b3c4d",
        "url": "https://www.setlist.fm/setlist/x/2026/y-1a2b3c4d.html",
        "sets": {"set": [{"song": [{"name": "Hum"}]}]},
    }
    svc_cli.setlistfm = FakeSetlistFm({}, setlists={"1a2b3c4d": bare})
    out = setlist_playlist_outcome(bare["url"], "", "", "", svc_cli)
    assert out.kind is Kind.NEED_USER and "artist is required" in (out.error or "")
    out = setlist_playlist_outcome(bare["url"], "Zed Fox", "", "", svc_cli)
    assert out.kind is Kind.NEED_USER and "date is required" in (out.error or "")


# ---------- the poll's rarer rows ----------


def test_poll_rows_for_a_dropped_lookup_a_gone_event_and_an_unreadable_page(tmp_path: Path, ticket_text: str) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    svc = make_services(settings, RUN1, route=None, text=ticket_text)
    event = seed(svc, settings)
    fm: FakeSetlistFm = svc.setlistfm  # type: ignore[assignment]
    fm.fail = True
    assert [r.action for r in poll_setlists(svc, RUN1, sync=FakeSync()).rows] == [
        "lookup failed: setlist.fm refused | path='/search/setlists' status='500'"
    ]
    fm.fail = False
    svc.setlistfm = None
    assert [r.action for r in poll_setlists(svc, RUN1, sync=FakeSync()).rows] == [
        "lookup skipped (setlist.fm is not set up)"
    ]
    svc.setlistfm = fm
    entry = queue_list(svc)[0]
    bad = QueueEntry(**{**entry.__dict__, "event_id": "e-bad", "date": "not-a-date"})
    queue_add(bad, svc)
    actions = [r.action for r in poll_setlists(svc, RUN1, sync=FakeSync()).rows]
    assert any(a.startswith("skipped:") for a in actions)
    # the event is gone when the playlist is made, and the patch is refused
    cal: FakeCalendar = svc.calendar  # type: ignore[assignment]
    assert _patch_event(QueueEntry(**{**entry.__dict__, "event_id": "nope"}), "https://p", svc) == (
        "event not patched (gws calendar events exited 1: no such event nope)",
        None,
    )
    event["status"] = "cancelled"  # the seeded dict is the one the fake serves
    assert _patch_event(entry, "https://p", svc) == ("event gone from the calendar", None)
    event["status"] = "confirmed"
    cal.fail = "patch_event"
    note, link = _patch_event(entry, "https://p", svc)
    assert note is not None and note.startswith("event not patched") and link
    # an expired row is dropped, or only reported on a dry run
    far = RUN1.replace(year=2028)
    assert [r.action for r in poll_setlists(svc, far, dry_run=True, sync=FakeSync()).rows][0] == "would drop (expired)"
    assert [r.action for r in poll_setlists(svc, far, sync=FakeSync()).rows][0] == "expired; dropped from the queue"


def test_songs_for_prefers_the_fetched_page_and_falls_back_to_the_row(tmp_path: Path, ticket_text: str) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    svc = make_services(settings, RUN1, route=None, text=ticket_text)
    row = Setlist(
        id="abc",
        url="https://s",
        artist="Zed Fox",
        date="2026-11-14",
        venue="Q",
        city="SF",
        tour=None,
        song_count=2,
        songs=("A", "B"),
    )
    res = SetlistResult(found=True, match="exact date", artist="Zed Fox", date="2026-11-14", venue="Q", setlist=row)
    assert (
        _songs_for(SetlistResult(found=False, match="none", artist="Zed Fox", date="2026-11-14", venue=None), svc) == []
    )
    fm: FakeSetlistFm = svc.setlistfm  # type: ignore[assignment]
    fm.setlists["abc"] = page(2)["setlist"][0]
    assert [s.name for s in _songs_for(res, svc)] == ["Song 0", "Song 1"]
    fm.fail = True
    assert [s.name for s in _songs_for(res, svc)] == ["A", "B"]
    fm.fail = False
    fm.setlists["abc"] = {"sets": {"set": []}}
    assert [s.name for s in _songs_for(res, svc)] == ["A", "B"]
    entry = QueueEntry(
        event_id="e",
        calendar_id="c",
        artist="Zed Fox",
        date="2026-11-14",
        venue="Q",
        setlistfm_url=None,
        song_count=0,
        until="2027-01-01",
        added="",
    )
    svc.spotify.fail = "create_playlist"  # type: ignore[union-attr]
    assert sync_playlist(entry, res, svc).status == "failed"


# ---------- adapters: bodies that cannot be read, answers that are not JSON ----------


def test_spotify_adapter_edges(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    path.write_text("[1, 2]")
    with pytest.raises(ProviderError):
        SpotifyCredentials.load(path)
    assert spotify_http._retry_after({"Retry-After": "soon"}) == 2
    assert spotify_http._body(unreadable_http_error(500), 10) == ""
    fake = FakeUrlopen(
        {
            ("GET", f"{API}/search"): {"tracks": {"items": [{"name": "Hum"}]}},
            ("PUT", f"{API}/playlists/p1"): {},
        }
    )
    creds = SpotifyCredentials(client_id="c", client_secret="s", refresh_token="r", access_token="a", expires_at=4e9)
    client = SpotifyClient(settings, FakeClock(RUN1), MemoryState(), urlopen=fake, creds=creds)
    assert client.search_tracks("track:Hum") == [{"name": "Hum"}]
    client.update_playlist_details("p1", description="d")
    assert json.loads(fake.requests[-1][3] or b"{}") == {"description": "d"}


def test_places_and_setlistfm_and_gemini_adapter_edges(settings: Settings) -> None:
    assert places_http._text({"a": 1}, "a", "b") == "" and places_http._text({"a": {"b": None}}, "a", "b") == ""
    for exc in (unreadable_http_error(500), urllib.error.URLError("down"), None):
        fake = FakeUrlopen({("GET", "https://x"): exc if exc is not None else b"not json"})
        with pytest.raises(ProviderError) as ei:
            places_http._send(fake, urllib.request.Request("https://x"), 5, "places", "Admin")
        assert ei.value.provider == "places"
    assert GooglePlacesClient.if_configured(settings) is None
    fm = SetlistFmClient(
        settings,
        FakeClock(RUN1),
        urlopen=FakeUrlopen({("GET", setlistfm_http.SETLISTFM_API): unreadable_http_error(500)}),
        api_key="k",
    )
    with pytest.raises(ProviderError) as ei:
        fm.get_setlist("abc")
    assert "status='500' body=''" in ei.value.error
    fm_ok = SetlistFmClient(
        settings,
        FakeClock(RUN1),
        urlopen=FakeUrlopen({("GET", f"{setlistfm_http.SETLISTFM_API}/setlist/abc"): {"id": "abc"}}),
        api_key="k",
    )
    assert fm_ok.get_setlist("abc") == {"id": "abc"}
    gem = GeminiClient(
        settings,
        urlopen=FakeUrlopen(
            {("POST", f"{GEMINI_API}/models/{GEMINI_MODEL}:generateContent"): unreadable_http_error(400)}
        ),
        api_key="k",
    )
    with pytest.raises(ProviderError) as ei:
        gem.probe()
    assert "status='400'" in ei.value.error and "body=''" in ei.value.error


def test_openclaw_transcript_since_reads_message_events_only(settings: Settings) -> None:
    ctx = OpenClawContext(settings)
    assert ctx.transcript_since(0) == []  # no database
    db = settings.session_db
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table transcript_events(session_id TEXT, created_at INTEGER, seq INTEGER, event_json TEXT)")
    con.executemany(
        "insert into transcript_events values (?, ?, ?, ?)",
        [
            ("s1", T0, 1, json.dumps(shot("e1", "/i.jpg"))),
            ("s1", T0 + 1, 2, "{not json"),
            ("s1", T0 + 2, 3, json.dumps({"type": "other"})),
            ("s1", T0 + 3, 4, json.dumps(assistant("stop"))),
        ],
    )
    con.commit()
    con.close()
    rows = ctx.transcript_since(T0)
    assert [(s, at, ev["type"]) for s, at, ev in rows] == [("s1", T0, "message"), ("s1", T0 + 3, "message")]
    assert find_failures(rows, now_ms=T0 + 10**6, grace_ms=1) == []


def test_failure_text_edges() -> None:
    f = Failure("event-1234567", "s", T0, "r", "c", "o", "/i.jpg", "chat", "no reply")
    assert f.slug() == "20261107T193845Z-event-12"
    no_media = shot("e1", "/i.jpg")
    no_media["message"]["__openclaw"]["media"] = []
    assert find_failures([("s1", T0, no_media)], now_ms=T0 + 10**9, grace_ms=1) == []
    rows = [("s1", T0, shot("e1", "/a.jpg")), ("s1", T0 + 1, shot("e2", "/b.jpg")), ("s1", T0 + 2, assistant("error"))]
    found = find_failures(rows, now_ms=T0 + 3, grace_ms=10**9)
    assert [(x.event_id, x.error) for x in found] == [
        ("e1", "no reply before the next screenshot"),
        ("e2", "model error"),
    ]


# ---------- config and healthcheck ----------


def test_config_edges(tmp_path: Path) -> None:
    env = settings_env(tmp_path) | {"CONCERT_MCP_CONFIG_DIR": "~"}
    assert load_settings(env, home=tmp_path).config_dir == tmp_path
    bad = tmp_path / "config.toml"
    bad.write_text("x = [1, 2]\n")
    with pytest.raises(ValueError, match="must be a string, number or boolean"):
        read_config_file(bad)
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    rows = migrate(settings, tmp_path, dry=True)  # env None: the process environment
    assert len(rows) == 4 and all(status.startswith(("skipped", "would copy")) for _, _, status in rows)
    legacy = tmp_path / ".local" / "state" / "ari-spotify" / "setlist-queue.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[]")
    rows = migrate(settings, tmp_path, dry=True, env=settings_env(tmp_path))
    assert ("would copy", legacy) in [(status, src) for src, _, status in rows]
    # a binary found on PATH (the runner has no gws; one is planted)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "gws").write_text("#!/bin/sh\n")
    (fake_bin / "gws").chmod(0o700)
    on_path = load_settings(settings_env(tmp_path) | {"PATH": str(fake_bin)}, home=tmp_path)
    assert on_path.gws_bin == str(fake_bin / "gws")


def test_healthcheck_directory_binary_and_sdk_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        assert hc._writable_dir("state-dir", locked).status == "FAIL"
        assert hc._writable_dir("state-dir", locked / "child" / "deeper").status == "FAIL"
    finally:
        locked.chmod(0o700)
    a_file = tmp_path / "file"
    a_file.write_text("")
    assert hc._writable_dir("state-dir", a_file).fix == "move the file away"
    assert hc._writable_dir("state-dir", tmp_path / "absent" / "yet").status == "PASS"
    assert hc._binary("gws-binary", str(a_file), "x", "install", "VAR").status == "PASS"
    planted = tmp_path / "bin"
    planted.mkdir()
    (planted / "planted-gws").write_text("#!/bin/sh\n")
    (planted / "planted-gws").chmod(0o700)
    with monkeypatch.context() as m:
        m.setenv("PATH", str(planted))
        assert hc._binary("gws-binary", "planted-gws", "x", "install", "VAR").evidence == str(planted / "planted-gws")
    real_import = importlib.import_module

    def no_mcp(name: str) -> Any:
        if name == "mcp":
            raise ImportError("no mcp")
        return real_import(name)

    real_version = importlib.metadata.version

    def no_version(name: str) -> str:
        if name == "mcp":
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    with monkeypatch.context() as m:
        m.setattr(importlib, "import_module", no_mcp)
        assert hc._check_mcp_sdk(None).status == "FAIL"  # type: ignore[arg-type]
    with monkeypatch.context() as m:
        m.setattr(importlib.metadata, "version", no_version)
        assert hc._check_mcp_sdk(None).evidence == "mcp unknown"  # type: ignore[arg-type]
    with monkeypatch.context() as m:
        m.setattr(sys, "version_info", namedtuple("V", "major minor micro")(3, 10, 2))
        assert hc._check_python(None).status == "FAIL"  # type: ignore[arg-type]
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    settings.gemini_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.gemini_credentials.write_text(json.dumps({"api_key": "k" * 40}))
    settings.gemini_credentials.chmod(0o600)
    assert hc._check_gemini_key(hc._Ctx(settings=settings, probe=False, svc=None)).status == "PASS"


def test_services_build_lazily_from_settings(tmp_path: Path) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    svc = build_services(settings)
    assert svc.spotify is None and svc.setlistfm is None and svc.places is None and svc.gemini is None
    svc.places = FakeVenueLookup()
    svc.gemini = "model"  # type: ignore[assignment]
    assert svc.places is not None and svc.gemini == "model"
    assert hc._Ctx(settings=settings, probe=False, svc=None).services().settings is settings


# ---------- the second batch: what the first left ----------


def test_places_hit_skips_an_unknown_component_and_if_configured_reads_the_key(settings: Settings) -> None:
    hit = places_http.places_hit(
        {
            "addressComponents": [{"longText": "x", "types": ["sublocality"]}],
            "formattedAddress": "1 Main St, Town, CA 90000, USA",
            "displayName": {"text": "Hall"},
        }
    )
    assert hit.address_lines
    settings.places_credentials.parent.mkdir(parents=True, exist_ok=True)
    settings.places_credentials.write_text(json.dumps({"api_key": "k" * 40}))
    assert isinstance(GooglePlacesClient.if_configured(settings), GooglePlacesClient)


def test_cli_without_an_openclaw_config_and_migrate_json(
    cli_env: Services, tmp_path: Path, monkeypatch, capsys
) -> None:
    from concert_mcp.adapters import chat_model_http, openclaw
    from test_cli_commands import StubModel

    monkeypatch.setattr(chat_model_http, "ChatModelClient", StubModel)
    text = tmp_path / "ocr.txt"
    text.write_text("x")
    code, out, _ = run(
        monkeypatch, capsys, ["try-image", "--text", str(text), "--model-url", "http://m", "--model", "q"]
    )
    assert code == 0 and "== Model called healthcheck" in out
    monkeypatch.setattr(openclaw.OpenClawContext, "transcript_since", lambda self, since: [])
    assert run(monkeypatch, capsys, ["failures", "collect"]) == (0, "", "")
    code, out, _ = run(monkeypatch, capsys, ["migrate", "--dry-run", "--json"])
    assert code == 0 and len(json.loads(out)) == 4


def test_migrate_never_copies_a_legacy_file_onto_itself(tmp_path: Path) -> None:
    legacy = tmp_path / ".config" / "ari-spotify" / "credentials.json"
    env = settings_env(tmp_path) | {"CONCERT_MCP_SPOTIFY_CREDENTIALS": str(legacy)}
    settings = load_settings(env, home=tmp_path)
    (src, dst, status) = next(r for r in migrate(settings, tmp_path, dry=True, env=env) if r[0] == legacy)
    assert dst != src and status == "skipped: no legacy"


def test_auth_terminal_probe_and_a_busy_callback_port() -> None:
    import socket

    from concert_mcp.flows import auth

    assert auth._stdin_is_tty() in (True, False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        with pytest.raises(ProviderError) as ei:
            auth._wait_for_code("https://a", f"http://127.0.0.1:{port}/callback", "t", lambda url: None)
    assert "cannot listen for the callback" in ei.value.error


def test_failures_folder_rules(settings: Settings, tmp_path: Path) -> None:
    from concert_mcp.flows.failures import MAX_KEPT, collect, saved_failures

    assert saved_failures(settings) == []
    root = settings.state_dir / "failures"
    for i in range(MAX_KEPT + 1):
        (root / str(1_700_000_000 + i)).mkdir(parents=True)
    rows = [("s1", T0, shot("e1", "/gone.jpg")), ("s1", T0 + 63_000, assistant("error", "Connection error."))]

    def down(system: str, user: str) -> str:
        raise ProviderError("model", "EHOSTUNREACH", "unreachable")

    (folder,) = collect(settings, lambda since: rows, T0 + 70_000, ask=down)
    assert len(saved_failures(settings)) == MAX_KEPT and folder in saved_failures(settings)
    assert "unreachable" in (folder / "orin-explanation.txt").read_text()


def test_discover_website_asks_once_and_remembers_it(svc: Services) -> None:
    from concert_mcp.flows.lineup import discover_website
    from concert_mcp.flows.venues import save_registry

    assert discover_website("The Quarry", svc) is None  # no model
    svc.gemini = FakeGemini('{"website": "https://thequarry.example", "openers": []}')
    assert discover_website("", svc) is None
    svc.state.write_json(
        svc.settings.venues_file,
        {"quarry": Venue(name="The Quarry", address_lines=(), website="", source="caller").to_json()},
    )
    assert discover_website("The Quarry, San Francisco", svc) == "https://thequarry.example/"
    assert load_registry(svc)["quarry"].website == "https://thequarry.example/"
    svc.gemini = FakeGemini('{"website": "https://www.songkick.com/venues/1"}')
    assert discover_website("Other Hall", svc) is None
    svc.gemini = FakeGemini("", fail=True)
    assert discover_website("Other Hall", svc) is None
    svc.gemini = FakeGemini('{"website": "https://venue.example/", "openers": []}')
    lu = find_lineup("Zed Fox", "2026-11-14", "Other Hall", None, svc)
    assert lu.status in ("found", "none") and "official website" in svc.gemini.prompts[0][0]  # type: ignore[union-attr]
    assert discover_website(" , ", svc) == "https://venue.example/"  # a location with no name: nothing to remember
    save_registry({}, svc)


def test_movie_lookup_edges(svc_cli: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    from concert_mcp.flows import movie_add as mod
    from concert_mcp.result import need

    assert mod._letterboxd(None, "", svc_cli) is None

    class Boom:
        def film_url(self, film: str, year: int | None) -> str | None:
            raise RuntimeError("down")

    svc_cli.films = Boom()
    assert mod._letterboxd(None, "Godzilla", svc_cli) is None
    monkeypatch.setattr(mod, "movie_request", lambda **kw: (_ for _ in ()).throw(need("title")))
    assert add_movie(title="[Movie]", start="", location="", svc=svc_cli).kind is Kind.NEED_USER


def test_poll_sync_and_patch_edges(tmp_path: Path, ticket_text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    svc = make_services(settings, RUN1, route=None, text=ticket_text)
    event = seed(svc, settings)
    entry = queue_list(svc)[0]
    row = Setlist(
        id="abc",
        url="https://s",
        artist="Zed Fox",
        date="2026-11-14",
        venue="Q",
        city="SF",
        tour=None,
        song_count=1,
        songs=("A",),
    )
    res = SetlistResult(found=True, match="exact date", artist="Zed Fox", date="2026-11-14", venue="Q", setlist=row)
    svc.setlistfm = None
    assert [s.name for s in _songs_for(res, svc)] == ["A"]
    monkeypatch.setattr(poll_mod, "sync_setlist_playlist", lambda **kw: (_ for _ in ()).throw(RuntimeError("odd")))
    assert sync_playlist(entry, res, svc).reason == "RuntimeError: odd"
    event["description"] = "Setlist playlist: https://p"
    assert _patch_event(entry, "https://p", svc) == (None, event["htmlLink"])


def test_text_module_corners() -> None:
    from concert_mcp.flows.setlist_playlist import SyncResult
    from concert_mcp.text.lineup import Lineup, Opener, lineup_lines
    from concert_mcp.text.movie import letterboxd_matches
    from concert_mcp.text.reply import render_setlist_playlist, render_setlist_playlist_announce
    from concert_mcp.text.topics import changed_fields
    from concert_mcp.text.venues import Venue, address_from_text

    rows = [("s1", T0, shot("e1", "/a.jpg")), ("s1", T0 + 1, assistant("length"))]
    assert find_failures(rows, now_ms=T0 + 2, grace_ms=10**9) == []  # not answered, not an error: still open
    paged = Lineup("found", openers=(Opener("Zork", "https://www.setlist.fm/setlist/zork/2026/x-1.html"),))
    assert lineup_lines(paged, "Zed Fox", openers_known=True) == [
        "Opener setlist (recent): https://www.setlist.fm/setlist/zork/2026/x-1.html"
    ]
    assert letterboxd_matches("<html>no title</html>", "Godzilla", None) is False
    assert render_setlist_playlist(SyncResult("unavailable", "n", 0), setlistfm_url="https://s", event=None).endswith(
        "run `concert-mcp spotify auth`)"
    )
    assert "Calendar" not in render_setlist_playlist_announce(
        "Z", "2026-11-14", "", setlist_url="s", song_count=1, playlist_name="p", playlist_url="u", html_link=None
    )
    assert changed_fields(
        {"summary": "Z", "start": "garbage", "location": "Q"},
        summary="Z",
        start=datetime(2026, 11, 14, tzinfo=UTC),
        location="Q",
    ) == ["when"]
    assert changed_fields(
        {"summary": "Z", "start": {"dateTime": "not a time"}, "location": "Q"},
        summary="Z",
        start=datetime(2026, 11, 14, tzinfo=UTC),
        location="Q",
    ) == ["when"]
    assert Venue(name="The Quarry", address_lines=(), source="caller").key == "quarry"
    assert address_from_text("The Quarry\n2174 Market St.\nno city here\nnor here\n", "The Quarry") is None


def test_setlist_playlist_duplicate_songs_and_a_stale_topic(svc: Services, now: datetime) -> None:
    from concert_mcp.flows.setlist_playlist import _event_for_show

    svc.spotify.tracks = [{"name": "First Light", "artists": ["Zed Fox"], "uri": "spotify:track:t1"}]  # type: ignore[union-attr]
    songs = [Song(name="First Light"), Song(name="First Light")]
    res = sync_setlist_playlist(artist="Zed Fox", date="2026-11-14", songs=songs, setlistfm_url="https://s", svc=svc)
    assert res.status == "created" and res.added == 1 and res.missing == ()
    topic_remember(
        "-1001234567890:7",
        make_topic_entry(event_id="gone", calendar_id="cal-concerts", summary="S", start=now, now=now),
        svc,
    )
    assert _event_for_show("Zed Fox", "2026-11-14", svc) is None
