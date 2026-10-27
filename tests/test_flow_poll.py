"""flows/poll.py: the weekly setlist poll over fakes, three runs apart, with a page that grows 0 -> 5 -> 12 songs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.config import Settings, load_settings
from concert_mcp.flows.poll import poll_outcome, poll_setlists
from concert_mcp.flows.queue import QueueEntry, make_entry, queue_add, queue_list, read_poll_stamp
from concert_mcp.flows.services import Services
from concert_mcp.flows.topics import make_topic_entry, topic_remember
from concert_mcp.result import Kind
from concert_mcp.text.setlists import SetlistResult, SyncResult
from concert_mcp.text.topics import topic_key
from conftest import make_services, settings_env

LA = ZoneInfo("America/Los_Angeles")
SHOW = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
RUN1 = datetime(2026, 11, 15, 9, 0, tzinfo=LA)  # the morning after the show: the page exists, no songs yet
RUN2 = RUN1 + timedelta(days=7)
RUN3 = RUN2 + timedelta(days=7)
PAGE_URL = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-san-francisco-ca-abc.html"
PLAYLIST_URL = "https://open.spotify.com/playlist/pl-setlist-1"


def page(songs: int) -> dict[str, Any]:
    """One setlist.fm search response for the show, with `songs` songs on it."""
    return {
        "setlist": [
            {
                "id": "abc",
                "url": PAGE_URL,
                "eventDate": "14-11-2026",
                "artist": {"name": "Zed Fox"},
                "venue": {"name": "The Quarry", "city": {"name": "San Francisco"}},
                "sets": {"set": [{"song": [{"name": f"Song {i}"} for i in range(songs)]}]},
            }
        ]
    }


class FakeSync:
    """Stands in for sync_setlist_playlist: creates once, then reports the songs added since."""

    def __init__(self, url: str = PLAYLIST_URL) -> None:
        self.url = url
        self.have = 0
        self.calls: list[tuple[str, str, int]] = []

    def __call__(self, entry: QueueEntry, setlist: SetlistResult, svc: Services) -> SyncResult:
        self.calls.append((entry.artist, entry.date, setlist.song_count))
        created = self.have == 0
        added = setlist.song_count - self.have
        self.have = setlist.song_count
        name = f"{entry.date} | {entry.artist}"
        return SyncResult("created" if created else "updated", name, setlist.song_count, url=self.url, added=added)


def failing_sync(entry: QueueEntry, setlist: SetlistResult, svc: Services) -> SyncResult:
    return SyncResult("failed", "2026-11-14 | Zed Fox", setlist.song_count, reason="Spotify refused")


def catalogue(songs: int) -> list[dict[str, Any]]:
    """The Spotify tracks the fake's search finds: every song of the page, by Zed Fox."""
    return [{"name": f"Song {i}", "artists": ["Zed Fox"], "uri": f"spotify:track:s{i}"} for i in range(songs)]


@pytest.fixture
def poll_svc(tmp_path, ticket_text: str) -> Services:
    """Services at RUN1 with no live chat (launchd), the Concerts event on the calendar, its row in the queue."""
    settings = load_settings(settings_env(tmp_path), home=tmp_path)
    svc = make_services(settings, RUN1, route=None, text=ticket_text)
    seed(svc, settings)
    return svc


def seed(svc: Services, settings: Settings, *, topic: bool = True) -> dict[str, Any]:
    event = svc.calendar.seed_event(
        "cal-concerts",
        summary="Zed Fox",
        start=SHOW,
        location="The Quarry, San Francisco",
        description="Doors: 7:30 PM\nTeaser playlist: https://open.spotify.com/playlist/teaser",
    )
    queue_add(
        make_entry(
            event_id=event["id"],
            calendar_id="cal-concerts",
            artist="Zed Fox",
            start=SHOW,
            venue="The Quarry",
            setlist=None,
            now=SHOW - timedelta(days=20),
            days=settings.setlist_queue_days,
        ),
        svc,
    )
    if topic:
        key = topic_key("-1001234567890", "7")
        topic_remember(
            key,
            make_topic_entry(event_id=event["id"], calendar_id="cal-concerts", summary="Zed Fox", start=SHOW, now=RUN1),
            svc,
        )
    return event


def set_page(svc: Services, songs: int | None) -> None:
    svc.setlistfm.responses = {("Zed Fox", "14-11-2026", None): page(songs)} if songs is not None else {}


def one_row(svc: Services) -> QueueEntry:
    (row,) = queue_list(svc)
    return row


class TestThreeWeeks:
    def test_the_page_fills_in_and_the_playlist_follows(self, poll_svc: Services) -> None:
        svc, sync = poll_svc, FakeSync()
        event_id = one_row(svc).event_id

        # Run 1: the page is up, no songs yet. Nothing is made, nothing is said; the url is remembered.
        set_page(svc, 0)
        r1 = poll_setlists(svc, RUN1, sync=sync)
        assert r1.ran and [r.action for r in r1.rows] == ["page found, no songs yet"]
        assert sync.calls == [] and svc.chat.sent == [] and svc.calendar.patched == []
        assert one_row(svc).setlistfm_url == PAGE_URL and one_row(svc).song_count == 0
        assert read_poll_stamp(svc) == RUN1

        # Run 2: five songs. The playlist is created, the event gets its link, the topic gets one message.
        set_page(svc, 5)
        svc.clock.advance(7 * 86400)
        r2 = poll_setlists(svc, RUN2, sync=sync)
        assert [r.action for r in r2.rows] == ["5 songs; playlist created; announced"]
        assert sync.calls == [("Zed Fox", "2026-11-14", 5)]
        assert len(svc.calendar.patched) == 1
        event = svc.calendar.get_event("cal-concerts", event_id)
        assert event["description"] == (
            "Doors: 7:30 PM\nTeaser playlist: https://open.spotify.com/playlist/teaser\nSetlist playlist: "
            + PLAYLIST_URL
        )
        assert len(svc.chat.sent) == 1
        target, text = svc.chat.sent[0]
        assert (target.channel, target.chat, target.thread) == ("telegram", "-1001234567890", "7")
        assert text == (
            "**Setlist playlist:** Zed Fox @ The Quarry, 2026-11-14\n"
            f"• **Setlist:** [setlist.fm]({PAGE_URL}) 5 songs\n"
            f"• **Playlist:** [2026-11-14 | Zed Fox]({PLAYLIST_URL})\n"
            "• **Calendar:** [Open in Google Calendar](https://calendar.google.com/calendar/u/0/r/eventedit/"
            "ZXZ0MSBjYWwtY29uY2VydHM)"
        )
        assert one_row(svc).playlist_url == PLAYLIST_URL and one_row(svc).song_count == 5

        # Run 3: twelve songs. Seven are added, silently: no second patch, no second message.
        set_page(svc, 12)
        svc.clock.advance(7 * 86400)
        r3 = poll_setlists(svc, RUN3, sync=sync)
        assert [r.action for r in r3.rows] == ["12 songs; 7 added to the playlist"]
        assert sync.calls[-1] == ("Zed Fox", "2026-11-14", 12)
        assert len(svc.calendar.patched) == 1 and len(svc.chat.sent) == 1
        assert one_row(svc).song_count == 12

    def test_a_page_that_did_not_grow_is_left_alone(self, poll_svc: Services) -> None:
        svc, sync = poll_svc, FakeSync()
        set_page(svc, 5)
        poll_setlists(svc, RUN1, sync=sync)
        r = poll_setlists(svc, RUN2, sync=sync)
        assert [x.action for x in r.rows] == ["5 songs; playlist unchanged"]
        assert len(sync.calls) == 1


class TestRealSync:
    """No `sync=`: the poll runs flows/setlist_playlist.sync_setlist_playlist on the page's songs."""

    def test_creates_the_playlist_from_the_page_and_tops_it_up(self, poll_svc: Services) -> None:
        svc = poll_svc
        svc.spotify.tracks = catalogue(12)
        set_page(svc, 5)
        svc.setlistfm.setlists = {"abc": page(5)}
        r = poll_setlists(svc, RUN1)
        assert [x.action for x in r.rows] == ["5 songs; playlist created; announced"]
        assert svc.setlistfm.fetched == ["abc"]
        (created,) = svc.spotify.created
        assert created["name"] == "2026-11-14 | Zed Fox" and created["description"].startswith("Setlist:")
        assert [uris for _pl, uris in svc.spotify.added] == [[f"spotify:track:s{i}" for i in range(5)]]
        assert one_row(svc).playlist_url == created["external_urls"]["spotify"]
        assert len(svc.chat.sent) == 1 and "[2026-11-14 | Zed Fox](" in svc.chat.sent[0][1]

        set_page(svc, 12)
        svc.setlistfm.setlists = {"abc": page(12)}
        r = poll_setlists(svc, RUN2)
        assert [x.action for x in r.rows] == ["12 songs; 7 added to the playlist"]
        assert len(svc.spotify.created) == 1 and len(svc.chat.sent) == 1
        assert svc.spotify.added[-1][1] == [f"spotify:track:s{i}" for i in range(5, 12)]

    def test_falls_back_to_the_search_rows_names_when_the_page_fetch_is_empty(self, poll_svc: Services) -> None:
        svc = poll_svc
        svc.spotify.tracks = catalogue(5)
        set_page(svc, 5)  # no `setlists` table: get_setlist answers None
        r = poll_setlists(svc, RUN1)
        assert [x.action for x in r.rows] == ["5 songs; playlist created; announced"]
        assert len(svc.spotify.added[0][1]) == 5

    def test_without_spotify_the_row_keeps_the_count_and_tries_again(self, poll_svc: Services) -> None:
        svc = poll_svc
        svc.spotify = None
        set_page(svc, 5)
        r = poll_setlists(svc, RUN1)
        assert [x.action for x in r.rows] == ["5 songs; playlist not made (Spotify is not set up)"]
        assert one_row(svc).playlist_url is None and one_row(svc).song_count == 5


class TestWeeklyGate:
    def test_a_run_within_the_week_is_a_no_op(self, poll_svc: Services) -> None:
        svc, sync = poll_svc, FakeSync()
        set_page(svc, 5)
        poll_setlists(svc, RUN1, sync=sync)
        again = poll_setlists(svc, RUN1 + timedelta(days=6, hours=12), sync=sync)
        assert again.ran is False and again.rows == () and again.last_poll == RUN1.isoformat(timespec="seconds")
        assert len(svc.setlistfm.calls) == 1 and read_poll_stamp(svc) == RUN1

    def test_force_runs_it_anyway(self, poll_svc: Services) -> None:
        svc = poll_svc
        set_page(svc, 0)
        poll_setlists(svc, RUN1, sync=FakeSync())
        forced = poll_setlists(svc, RUN1 + timedelta(hours=1), force=True, sync=FakeSync())
        assert forced.ran is True and read_poll_stamp(svc) == RUN1 + timedelta(hours=1)

    def test_the_seventh_day_runs(self, poll_svc: Services) -> None:
        svc = poll_svc
        set_page(svc, 0)
        poll_setlists(svc, RUN1, sync=FakeSync())
        assert poll_setlists(svc, RUN1 + timedelta(days=7), sync=FakeSync()).ran is True


class TestQueueHousekeeping:
    def test_an_entry_past_until_leaves_the_queue(self, poll_svc: Services) -> None:
        svc = poll_svc
        set_page(svc, 12)
        late = SHOW + timedelta(days=svc.settings.setlist_queue_days + 1)
        r = poll_setlists(svc, late, sync=FakeSync())
        assert [x.action for x in r.rows] == ["expired; dropped from the queue"]
        assert queue_list(svc) == [] and svc.setlistfm.calls == []

    def test_a_show_not_on_setlistfm_yet(self, poll_svc: Services) -> None:
        set_page(poll_svc, None)
        r = poll_setlists(poll_svc, RUN1, sync=FakeSync())
        assert [x.action for x in r.rows] == ["not on setlist.fm yet"]

    def test_a_failed_lookup_keeps_the_row(self, poll_svc: Services) -> None:
        poll_svc.setlistfm.fail = True
        r = poll_setlists(poll_svc, RUN1, sync=FakeSync())
        assert r.rows[0].action.startswith("lookup failed: setlist.fm refused")
        assert len(queue_list(poll_svc)) == 1

    def test_a_failed_playlist_is_retried_next_week(self, poll_svc: Services) -> None:
        svc = poll_svc
        set_page(svc, 5)
        r = poll_setlists(svc, RUN1, sync=failing_sync)
        assert [x.action for x in r.rows] == ["5 songs; playlist not made (Spotify refused)"]
        assert one_row(svc).playlist_url is None and one_row(svc).song_count == 5
        assert svc.chat.sent == [] and svc.calendar.patched == []
        sync = FakeSync()
        r = poll_setlists(svc, RUN2, sync=sync)
        assert [x.action for x in r.rows] == ["5 songs; playlist created; announced"]

    def test_an_empty_queue_still_stamps_the_run(self, poll_svc: Services) -> None:
        svc = poll_svc
        svc.state.remove(svc.settings.setlist_queue)
        r = poll_setlists(svc, RUN1, sync=FakeSync())
        assert r.ran and r.rows == () and read_poll_stamp(svc) == RUN1


class TestAnnounceTarget:
    def test_without_a_topic_the_configured_concerts_chat_gets_it(self, tmp_path, ticket_text: str) -> None:
        env = {**settings_env(tmp_path), "CONCERT_MCP_CONCERTS_CHAT": "-1001234567890"}
        settings = load_settings(env, home=tmp_path)
        svc = make_services(settings, RUN1, route=None, text=ticket_text)
        seed(svc, settings, topic=False)
        set_page(svc, 5)
        r = poll_setlists(svc, RUN1, sync=FakeSync())
        assert [x.action for x in r.rows] == ["5 songs; playlist created; announced"]
        (target, _text) = svc.chat.sent[0]
        assert (target.chat, target.thread) == ("-1001234567890", None)

    def test_without_any_chat_the_playlist_is_made_but_not_announced(self, tmp_path, ticket_text: str) -> None:
        settings = load_settings(settings_env(tmp_path), home=tmp_path)
        svc = make_services(settings, RUN1, route=None, text=ticket_text)
        seed(svc, settings, topic=False)
        set_page(svc, 5)
        r = poll_setlists(svc, RUN1, sync=FakeSync())
        assert [x.action for x in r.rows] == [
            "5 songs; playlist created; not announced (no chat: set CONCERT_MCP_CONCERTS_CHAT)"
        ]
        assert svc.chat.sent == [] and len(svc.calendar.patched) == 1

    def test_send_off_patches_but_does_not_send(self, poll_svc: Services) -> None:
        set_page(poll_svc, 5)
        r = poll_setlists(poll_svc, RUN1, send=False, sync=FakeSync())
        assert [x.action for x in r.rows] == ["5 songs; playlist created; not announced (NOT SENT: delivery disabled)"]
        assert len(poll_svc.calendar.patched) == 1


class TestDryRun:
    def test_changes_nothing(self, poll_svc: Services) -> None:
        svc, sync = poll_svc, FakeSync()
        set_page(svc, 5)
        before = one_row(svc)
        r = poll_setlists(svc, RUN1, dry_run=True, sync=sync)
        assert r.dry_run and [x.action for x in r.rows] == ["5 songs; would make the playlist and announce it"]
        assert sync.calls == [] and svc.chat.sent == [] and svc.calendar.patched == []
        assert one_row(svc) == before and read_poll_stamp(svc) is None


class TestPollOutcome:
    def test_report_text_and_data(self, poll_svc: Services) -> None:
        set_page(poll_svc, 5)
        out = poll_outcome(poll_svc, sync=FakeSync())
        assert out.kind is Kind.OK
        assert out.reply == (
            "**Setlist poll** 2026-11-15T17:00:00+00:00 (last: never)\n"
            "• 2026-11-14 Zed Fox — 5 songs; playlist created; announced"
        )
        assert out.data["ran"] is True and out.data["rows"][0]["playlist_url"] == PLAYLIST_URL

    def test_skipped_report(self, poll_svc: Services) -> None:
        set_page(poll_svc, 0)
        poll_outcome(poll_svc, sync=FakeSync())
        poll_svc.clock.advance(3600)
        out = poll_outcome(poll_svc, sync=FakeSync())
        assert out.reply == (
            "**Setlist poll** skipped: last run 2026-11-15T17:00:00+00:00, less than 7 days ago "
            "(`concert-mcp poll --force` runs it now)"
        )
        assert out.data["ran"] is False

    def test_empty_queue_report(self, poll_svc: Services) -> None:
        poll_svc.state.remove(poll_svc.settings.setlist_queue)
        out = poll_outcome(poll_svc, dry_run=True, sync=FakeSync())
        assert out.kind is Kind.DRY_RUN
        assert (
            out.reply
            == "**Setlist poll (dry run)** 2026-11-15T17:00:00+00:00 (last: never)\nThe setlist queue is empty."
        )
