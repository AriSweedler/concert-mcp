"""flows/queue.py: the setlist queue file the poll reads when asked."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from concert_mcp.flows.queue import (
    QueueEntry,
    queue_add,
    queue_list,
    queue_replace,
    queue_status,
)

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 11, 14, 20, 30, tzinfo=LA)


def entry(event_id: str = "evt1", *, date: str = "2026-11-14", until: str = "2027-01-14") -> QueueEntry:
    return QueueEntry(
        event_id=event_id,
        calendar_id="cal-concerts",
        artist="Zed Fox",
        date=date,
        venue="The Quarry, San Francisco",
        setlistfm_url=None,
        song_count=0,
        until=until,
        added="2026-10-25T19:00:00+00:00",
    )


class TestQueueStatus:
    def test_an_unreadable_until_counts_as_expired(self, svc):
        queue_add(entry("odd", until="someday"), svc)
        st = queue_status(svc)
        assert st.active == ()
        assert [e.event_id for e in st.expired] == ["odd"]


class TestQueueReplace:
    def test_appends_when_the_row_is_absent(self, svc):
        queue_replace(entry("evt1"), svc)
        assert queue_list(svc) == [entry("evt1")]
