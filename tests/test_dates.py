"""text/dates.py: parsing, show time vs doors, the time window, rendering."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.result import NEED_USER, FixInput, NeedUser
from concert_mcp.text.dates import (
    START_FORMATS,
    apply_show_time,
    check_duration,
    check_window,
    clock,
    concert_date,
    default_end,
    doors_time,
    far_future_reply,
    fmt_range,
    is_date_only,
    parse_time,
    past_reply,
    show_time,
    start_from_date,
    validate_tz,
    when_of,
)

TZ = "America/Los_Angeles"
LA = ZoneInfo(TZ)
START = datetime(2026, 11, 14, 20, 30, tzinfo=LA)

# ---- parse_time ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "2026-11-14 20:30",
        "2026-11-14 20:30:00",
        "2026-11-14 8:30 PM",
        "2026-11-14 8:30 pm",
        "2026-11-14 8:30PM",
        "2026-11-14T20:30",
        "2026-11-14T20:30:00",
        "  2026-11-14   8:30   PM ",
    ],
)
def test_parse_time_formats(raw: str) -> None:
    assert parse_time(raw, TZ) == START


def test_parse_time_hour_only_forms() -> None:
    eight = datetime(2026, 11, 14, 20, 0, tzinfo=LA)
    assert parse_time("2026-11-14 8 PM", TZ) == eight
    assert parse_time("2026-11-14 8PM", TZ) == eight
    assert parse_time("2026-11-14 8pm", TZ) == eight


def test_every_start_format_is_exercised() -> None:
    samples = {
        "%Y-%m-%d %H:%M": "2026-11-14 20:30",
        "%Y-%m-%d %H:%M:%S": "2026-11-14 20:30:00",
        "%Y-%m-%d %I:%M %p": "2026-11-14 8:30 PM",
        "%Y-%m-%d %I %p": "2026-11-14 8 PM",
        "%Y-%m-%d %I:%M%p": "2026-11-14 8:30PM",
        "%Y-%m-%d %I%p": "2026-11-14 8PM",
        "%Y-%m-%dT%H:%M": "2026-11-14T20:30",
        "%Y-%m-%dT%H:%M:%S": "2026-11-14T20:30:00",
    }
    assert set(samples) == set(START_FORMATS)
    for fmt, raw in samples.items():
        assert datetime.strptime(raw, fmt).date() == START.date()
        assert parse_time(raw, TZ).tzinfo == LA


def test_parse_time_z_keeps_utc() -> None:
    dt = parse_time("2026-11-14T20:30:00Z", TZ)
    assert dt.utcoffset() == timedelta(0)
    assert (dt.hour, dt.minute) == (20, 30)


def test_parse_time_rfc3339_offset_wins_over_tz() -> None:
    dt = parse_time("2026-11-14T20:30:00-05:00", TZ)
    assert dt.utcoffset() == timedelta(hours=-5)
    assert dt == datetime(2026, 11, 14, 20, 30, tzinfo=timezone(timedelta(hours=-5)))


def test_parse_time_naive_iso_gets_tz() -> None:
    dt = parse_time("2026-11-14 20:30:00.500", TZ)
    assert dt.tzinfo == LA
    assert dt.microsecond == 500000


def test_parse_time_garbage_is_fix_input() -> None:
    with pytest.raises(FixInput) as ei:
        parse_time("Nov 14", TZ)
    assert ei.value.error == "start 'Nov 14' is not a date"
    assert ei.value.fix == 'start must look like "2026-11-03 20:00" (24h) or "2026-11-03 8:00 PM"'
    with pytest.raises(FixInput) as ei:
        parse_time("tomorrow", TZ, "end")
    assert ei.value.error == "end 'tomorrow' is not a date"
    assert ei.value.fix.startswith("end must look like")


def test_is_date_only() -> None:
    assert is_date_only("2026-11-14")
    assert is_date_only(" 2026-11-14 ")
    assert not is_date_only("2026-11-14 20:30")
    assert not is_date_only("2026/11/14")
    assert not is_date_only("")


def test_validate_tz() -> None:
    assert validate_tz(TZ) == TZ
    assert validate_tz("UTC") == "UTC"
    with pytest.raises(FixInput) as ei:
        validate_tz("Mars/Olympus")
    assert ei.value.error == "unknown tz 'Mars/Olympus'"
    assert ei.value.fix == "use an IANA name like America/Los_Angeles, or omit it"


# ---- clock / doors / show --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7:30 PM", time(19, 30)),
        ("7pm", time(19, 0)),
        ("7.30 p.m.", time(19, 30)),
        ("12:00 AM", time(0, 0)),
        ("12 pm", time(12, 0)),
        ("10:05P.M.", time(22, 5)),
    ],
)
def test_clock(raw: str, expected: time) -> None:
    assert clock(raw) == expected


def test_doors_time(ticket_text: str) -> None:
    assert doors_time(ticket_text) == time(19, 30)
    assert doors_time("Doors open 7pm") == time(19, 0)
    assert doors_time("Door: 6.30 p.m.") == time(18, 30)
    assert doors_time("Show 8 PM") is None


def test_show_time_certain(ticket_text: str) -> None:
    assert show_time(ticket_text) == (time(20, 30), True)
    assert show_time("Doors 7:30 PM Show 8:30 PM") == (time(20, 30), True)
    assert show_time("Showtime: 9pm") == (time(21, 0), True)
    assert show_time("Music starts at 8 PM") == (time(20, 0), True)
    assert show_time("Performance begins 7.30 p.m.") == (time(19, 30), True)


def test_show_time_inferred_from_doors_and_one_other_time() -> None:
    assert show_time("Doors 7pm ... 8pm") == (time(20, 0), False)


def test_show_time_none_when_doors_only_or_ambiguous() -> None:
    assert show_time("Doors 7pm") == (None, False)
    assert show_time("Doors 7pm, 8pm, curfew 11pm") == (None, False)
    assert show_time("The Quarry") == (None, False)
    assert show_time("") == (None, False)


# ---- default_end / check_duration ------------------------------------------------------------------------------------


def test_default_end() -> None:
    assert default_end(START) == START + timedelta(hours=2)
    assert default_end(START, 3) == START + timedelta(hours=3)


def test_check_duration() -> None:
    check_duration(START, START + timedelta(hours=2))
    check_duration(START, START + timedelta(hours=12))
    for end in (START, START - timedelta(minutes=1), START + timedelta(hours=12, minutes=1)):
        with pytest.raises(FixInput) as ei:
            check_duration(START, end)
        assert ei.value.error == "end must be after start and within 12 hours"
        assert ei.value.fix == "fix end, or omit it for start + 2 hours"


# ---- check_window ----------------------------------------------------------------------------------------------------


def test_past_and_far_future_replies() -> None:
    assert past_reply(START) == (
        "That reads as Sat Nov 14 2026, 8:30 PM, which is in the past. "
        "Reply with the right date (include the year) and I will add it."
    )
    assert far_future_reply(START) == (
        "That reads as Sat Nov 14 2026, over two years from now. Reply with the right date and I will add it."
    )


def test_check_window_past(now: datetime) -> None:
    start = now - timedelta(hours=13)
    with pytest.raises(NeedUser) as ei:
        check_window(start, now, allow_past=False)
    assert ei.value.error == f"start {start.isoformat()} is in the past"
    assert ei.value.reply == past_reply(start)


def test_check_window_allow_past(now: datetime) -> None:
    check_window(now - timedelta(days=400), now, allow_past=True)


def test_check_window_within_grace(now: datetime) -> None:
    check_window(now - timedelta(hours=11), now, allow_past=False)
    check_window(now - timedelta(hours=11), now, allow_past=False, past_grace_h=12)
    with pytest.raises(NeedUser):
        check_window(now - timedelta(hours=11), now, allow_past=False, past_grace_h=10)


def test_check_window_far_future(now: datetime) -> None:
    start = now + timedelta(days=731)
    with pytest.raises(NeedUser) as ei:
        check_window(start, now, allow_past=False)
    assert ei.value.error == f"start {start.isoformat()} is over two years out"
    assert ei.value.reply == far_future_reply(start)
    check_window(now + timedelta(days=729), now, allow_past=False)
    check_window(start, now, allow_past=False, max_future_days=1000)


# ---- apply_show_time -------------------------------------------------------------------------------------------------


def test_apply_show_time_corrects_start_and_moves_an_assumed_end(ticket_text: str) -> None:
    start = datetime(2026, 11, 14, 19, 30, tzinfo=LA)  # the doors time
    end = start + timedelta(hours=2)
    fixed, new_end, notes = apply_show_time(start, end, True, ticket_text)
    assert fixed == START
    assert new_end == START + timedelta(hours=2)
    assert notes == ["Start corrected to 8:30 PM, the show time in the text."]


def test_apply_show_time_leaves_an_explicit_end(ticket_text: str) -> None:
    start = datetime(2026, 11, 14, 19, 30, tzinfo=LA)
    end = datetime(2026, 11, 14, 23, 0, tzinfo=LA)
    fixed, new_end, notes = apply_show_time(start, end, False, ticket_text)
    assert fixed == START
    assert new_end == end
    assert notes == ["Start corrected to 8:30 PM, the show time in the text."]


def test_apply_show_time_inferred_mismatch_is_fix_input() -> None:
    start = datetime(2026, 11, 14, 19, 0, tzinfo=LA)
    with pytest.raises(FixInput) as ei:
        apply_show_time(start, start + timedelta(hours=2), True, "Doors 7pm ... 8pm")
    assert ei.value.error == "start 7:00 PM is not the show time"
    assert ei.value.fix == 'set start to "2026-11-14 8:00 PM" (the show time in the text; 7:00 PM is doors)'


def test_apply_show_time_no_change_when_equal_or_no_show_time(ticket_text: str) -> None:
    end = START + timedelta(hours=2)
    assert apply_show_time(START, end, True, ticket_text) == (START, end, [])
    assert apply_show_time(START, end, True, "no times here") == (START, end, [])
    # An inferred show time that agrees with start is not a mismatch.
    eight = datetime(2026, 11, 14, 20, 0, tzinfo=LA)
    assert apply_show_time(eight, end, True, "Doors 7pm ... 8pm") == (eight, end, [])


# ---- start_from_date -------------------------------------------------------------------------------------------------


def test_start_from_date_with_show_time(ticket_text: str) -> None:
    assert start_from_date("2026-11-14", ticket_text, TZ) == (START, "Start taken from the text (8:30 PM).")
    inferred, note = start_from_date("2026-11-14", "Doors 7pm ... 8pm", TZ)
    assert inferred == datetime(2026, 11, 14, 20, 0, tzinfo=LA)
    assert note == "Start taken from the text (8:00 PM)."


def test_start_from_date_without_show_time() -> None:
    with pytest.raises(NeedUser) as ei:
        start_from_date("2026-11-14", "Doors 7pm", TZ)
    assert ei.value.error == "show time is required"
    assert ei.value.reply == NEED_USER.format(what="show time")
    assert ei.value.reply == "I could not read the show time from that. Reply with it and I will add the event."


def test_start_from_date_rejects_a_non_date() -> None:
    with pytest.raises(FixInput) as ei:
        start_from_date("Nov 14", "Show 8 PM", TZ)
    assert ei.value.error == "start 'Nov 14' is not a date"


# ---- fmt_range / concert_date / when_of ------------------------------------------------------------------------------


def test_fmt_range_same_day() -> None:
    assert fmt_range(START, START + timedelta(hours=2)) == "Sat Nov 14, 8:30 PM – 10:30 PM PST"


def test_fmt_range_cross_day() -> None:
    assert fmt_range(START, START + timedelta(hours=5)) == "Sat Nov 14, 8:30 PM – Sun Nov 15, 1:30 AM PST"


def test_fmt_range_uses_the_start_zone_name() -> None:
    july = datetime(2026, 7, 4, 20, 0, tzinfo=LA)
    assert fmt_range(july, july + timedelta(hours=1)).endswith(" PDT")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-14", "2026-11-14"),
        ("2026/11/14", "2026-11-14"),
        ("2026-11-14T20:30:00-08:00", "2026-11-14"),
        (" 2026-11-14 20:30 ", "2026-11-14"),
    ],
)
def test_concert_date(raw: str, expected: str) -> None:
    assert concert_date(raw) == expected


def test_concert_date_garbage_is_fix_input() -> None:
    with pytest.raises(FixInput) as ei:
        concert_date("Nov 14")
    assert ei.value.error == "unreadable concert date 'Nov 14'"
    assert ei.value.fix == 'date must look like "2026-11-03"'


def test_when_of_timed() -> None:
    event = {"start": {"dateTime": "2026-11-14T20:30:00-08:00"}, "end": {"dateTime": "2026-11-14T22:30:00-08:00"}}
    assert when_of(event, LA) == ("Sat Nov 14, 8:30 PM – 10:30 PM PST", START)


def test_when_of_timed_converts_utc_and_defaults_the_end() -> None:
    event = {"start": {"dateTime": "2026-11-15T04:30:00Z"}}
    when, start = when_of(event, LA)
    assert start == START
    assert when == "Sat Nov 14, 8:30 PM – 8:30 PM PST"


def test_when_of_all_day() -> None:
    when, day = when_of({"start": {"date": "2026-11-14"}, "end": {"date": "2026-11-15"}}, LA)
    assert when == "Sat Nov 14 (all day)"
    assert day == datetime(2026, 11, 14, tzinfo=LA)
