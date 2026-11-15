"""text/partiful.py and the Partiful strings of text/reply.py: every shape and every reply, against literals."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from concert_mcp.text.partiful import (
    CREATED_BY,
    InviteResult,
    event_id_of,
    event_payload,
    freeform_location,
    image_record,
    invite_description,
    invite_title,
    latest_album_cover,
    line_value,
    link_fields,
    location_parts,
    merged_links,
    phone_tail,
    place_query,
    same_venue,
    short_clock,
    show_links,
    storage_url,
    structured_location,
    ticket_link,
    ticket_link_in,
    with_line,
)
from concert_mcp.text.reply import (
    PARTIFUL_NO_PHONE,
    partiful_note,
    partiful_problem,
    provider_reply,
    render_partiful,
    render_partiful_code_sent,
    render_partiful_signed_in,
)

LA = ZoneInfo("America/Los_Angeles")
SHOW = datetime(2026, 11, 14, 20, 30, tzinfo=LA)
TICKETS = "https://www.ticketweb.com/event/zed-fox/123"
PLAYLIST = "https://open.spotify.com/playlist/pl1"
PAGE = "https://partiful.com/e/pf1"
TAIL = "\n\nCreated by github.com/AriSweedler/concert-mcp"
PLACE = {
    "id": "I1",
    "name": "The Quarry",
    "formattedAddressLines": ["1 Main St", "San Francisco, CA 94100", "United States"],
    "structuredAddress": {"postCode": "94100"},
    "country": "United States",
}


def test_the_ticket_link_is_found_by_rule() -> None:
    assert ticket_link(TICKETS) == TICKETS
    assert ticket_link(f"  tickets: {TICKETS}.  ") == TICKETS
    assert ticket_link("the Ticketweb page") is None
    text = f"ZED FOX\nTicketweb order https://www.ticketweb.com/order/abc123\n  Tickets : {TICKETS}\n"
    assert ticket_link_in(text) == TICKETS
    assert ticket_link_in(f"ticket: {TICKETS}") == TICKETS
    # Another address in the ticket's own text is not where a friend buys a ticket.
    assert ticket_link_in("Ticketweb order https://www.ticketweb.com/order/abc123") is None
    assert ticket_link_in(f"get tickets: {TICKETS}") is None


def test_lines_times_and_the_title() -> None:
    assert TAIL == "\n\n" + CREATED_BY
    lines = ["Doors: 7:30 PM", "Teaser playlist: " + PLAYLIST, "Partiful: "]
    assert line_value(lines, "Teaser playlist: ") == PLAYLIST
    assert line_value(lines, "Partiful: ") is None
    assert line_value(lines, "Tickets: ") is None
    # A fact gained goes last; a fact corrected takes the place of the first line of its kind.
    assert with_line(lines, "Tickets: ", "https://t.example") == [*lines, "Tickets: https://t.example"]
    assert with_line(lines, "Doors: ", "7:00 PM") == ["Doors: 7:00 PM", *lines[1:]]
    assert [short_clock(SHOW.replace(hour=h)) for h in (0, 12, 20)] == ["12:30", "12:30", "8:30"]
    assert invite_title("Zed Fox", "The Quarry", SHOW) == "Concert: Zed Fox at The Quarry 11/14"
    assert invite_title("Zed Fox", "", SHOW.replace(month=3, day=7)) == "Concert: Zed Fox 3/7"
    assert invite_description(lines, SHOW) == "Doors 7:30, show 8:30" + TAIL
    assert invite_description(["Doors: 7 PM"], SHOW) == "Doors 7:00, show 8:30" + TAIL
    assert invite_description(["Purchased: 2 tickets"], SHOW) == "Show 8:30" + TAIL
    # No doors time on the ticket: the announced one the lineup step wrote, when it has one.
    expected = ["Expected: doors 6:00 PM, Zed Fox 7:00 PM"]
    assert invite_description(expected, SHOW.replace(hour=19, minute=0)) == "Doors 6:00, show 7:00" + TAIL
    assert invite_description(["Doors: 7:30 PM", *expected], SHOW) == "Doors 7:30, show 8:30" + TAIL
    assert invite_description(["Expected: Zork 8:00 PM, Zed Fox 9:00 PM"], SHOW) == "Show 8:30" + TAIL


def test_link_fields_and_the_event_payload() -> None:
    both = link_fields("Zed Fox", tickets=TICKETS, playlist=PLAYLIST)
    assert both == [
        {"icon": "link", "value": "Tickets", "url": TICKETS},
        {"icon": "music-note", "value": "Zed Fox songs I like", "url": PLAYLIST},
    ]
    assert link_fields("Zed Fox", tickets=TICKETS) == both[:1] and link_fields("Zed Fox") == []
    page = "https://www.setlist.fm/setlist/zed-fox/2026/the-quarry-7b9c1a2e.html"
    played = "https://open.spotify.com/playlist/pl2"
    after_the_show = [
        *both,
        {"icon": "link", "value": "setlist.fm", "url": page},
        {"icon": "music-note", "value": "Setlist playlist", "url": played},
    ]
    assert link_fields("Zed Fox", tickets=TICKETS, playlist=PLAYLIST, setlist=page, setlist_playlist=played) == (
        after_the_show
    )
    lines = [
        f"Tickets: {TICKETS}",
        f"Teaser playlist: {PLAYLIST}",
        f"setlist.fm: {page}",
        f"Setlist playlist: {played}",
    ]
    assert show_links("Zed Fox", lines) == after_the_show
    # An event made before 2026-11-14 names the page `Setlist:`; it is still the setlist.fm link.
    assert show_links("Zed Fox", [f"Setlist: {page}"]) == [{"icon": "link", "value": "setlist.fm", "url": page}]

    # A page's fields with the wanted ones in: a label already there takes the new address, a hand-made field stays.
    by_hand = {"icon": "sparkles", "value": "Pregame at mine", "url": "https://example.com/pregame"}
    current = [{"icon": "link", "value": "Tickets", "url": "https://old.example/t"}, by_hand]
    assert merged_links(current, after_the_show) == [
        {"icon": "link", "value": "Tickets", "url": TICKETS},
        by_hand,
        *after_the_show[1:],
    ]
    assert merged_links(after_the_show, after_the_show) == after_the_show
    assert event_id_of("https://partiful.com/e/pf1 ") == "pf1" and event_id_of("https://go.partiful.com/abc") is None

    event = event_payload(title="T", start=SHOW, tz="America/Los_Angeles", description="D", fields=both, image=None)
    assert event["startDate"] == "2026-11-15T04:30:00.000Z"
    assert (event["status"], event["visibility"], event["timezone"]) == ("UNSAVED", "public", "America/Los_Angeles")
    assert event["customFields"] == both and event["rsvpsEnabled"] is True
    assert "image" not in event and "endDate" not in event
    with_image = event_payload(title="T", start=SHOW, tz="UTC", description="D", fields=[], image={"source": "upload"})
    assert with_image["image"] == {"source": "upload"}


def test_locations() -> None:
    maps_form = "The Quarry\n1 Main St\nSan Francisco, CA 94100"
    assert location_parts(maps_form) == ["The Quarry", "1 Main St", "San Francisco", "CA 94100"]
    assert place_query(maps_form) == "The Quarry, San Francisco, CA 94100"
    assert place_query("The Quarry, 1 Main St, San Francisco") == "The Quarry, San Francisco"
    assert place_query("The Quarry, San Francisco") == "The Quarry, San Francisco"
    assert place_query("") == ""
    assert same_venue("The Quarry", "Quarry") and same_venue("Bimbo's 365 Club", "Bimbos 365 Club SF")
    assert not same_venue("The Quarry", "1 Main St") and not same_venue("The Quarry", "") and not same_venue("", "")
    assert structured_location(PLACE, "The Quarry") == {
        "type": "structured",
        "mapsInfo": {"addressLines": ["1 Main St", "San Francisco, CA 94100"], "name": "The Quarry"},
        "placeResult": PLACE,
        "hasPostCode": True,
        "displayName": "The Quarry",
    }
    bare = structured_location({"formattedAddressLines": ["Somewhere"], "country": "United States"})
    assert bare["mapsInfo"] == {"addressLines": ["Somewhere"]} and bare["hasPostCode"] is False
    assert "displayName" not in bare
    assert freeform_location("The Quarry, San Francisco") == {"type": "freeform", "value": "The Quarry, San Francisco"}


def test_the_poster_record() -> None:
    url = "https://firebasestorage.googleapis.com/v0/b/getpartiful.appspot.com/o/external%2Fuser%2Fu1%2Fobj1?alt=media"
    assert storage_url("external/user/u1/obj1") == url
    rec = image_record(
        path="external/user/u1/obj1",
        size=10,
        width=640,
        height=640,
        content_type="image/jpeg",
        name="Album.jpg",
        now=datetime(2026, 10, 25, 19, 0, 0, tzinfo=UTC),
    )
    assert rec["source"] == "upload" and rec["url"] == url and (rec["width"], rec["height"]) == (640, 640)
    assert rec["upload"] == {
        "type": "image",
        "path": "external/user/u1/obj1",
        "fileCreatedAt": "2026-10-25T19:00:00.000Z",
        "uploadedAt": "2026-10-25T19:00:00.000Z",
        "name": "Album.jpg",
        "size": 10,
        "width": 640,
        "height": 640,
        "contentType": "image/jpeg",
        "storageUri": "gs://getpartiful.appspot.com/external/user/u1/obj1",
        "url": url,
    }


def test_the_newest_album_cover() -> None:
    def album(name: str, kind: str, date: str, *sizes: int) -> dict:
        images = [{"url": f"https://i.example/{name}-{w}", "width": w, "height": w} for w in sizes]
        return {"name": name, "album_type": kind, "release_date": date, "images": images}

    albums = [
        album("Old", "album", "2019", 300, 640),
        album("New", "album", "2024-05-01", 64, 640, 300),
        album("Newer single", "single", "2026-01-01", 640),
        album("No cover", "album", "2027", *()),
    ]
    assert latest_album_cover(albums) == {
        "url": "https://i.example/New-640",
        "width": 640,
        "height": 640,
        "name": "New",
    }
    assert latest_album_cover(albums[2:3])["name"] == "Newer single"
    assert latest_album_cover(albums[3:]) is None and latest_album_cover([]) is None
    assert latest_album_cover([{"images": [{"width": 640}]}]) is None
    assert latest_album_cover([{"images": [{"url": "https://i.example/x"}]}]) == {
        "url": "https://i.example/x",
        "width": 640,
        "height": 640,
        "name": "cover",
    }


def test_phone_tail_and_the_result_record() -> None:
    assert phone_tail("+1 (555) 555-0100") == "0100"
    res = InviteResult("created", url=PAGE, title="T", links=(("Tickets", TICKETS),), poster=True, going=True)
    assert res.to_json() == {
        "status": "created",
        "url": PAGE,
        "title": "T",
        "links": [["Tickets", TICKETS]],
        "poster": True,
        "going": True,
        "reason": None,
    }


def test_every_partiful_reply() -> None:
    links = (("Tickets", TICKETS), ("Zed Fox songs I like", PLAYLIST))
    title = "Concert: Zed Fox at The Quarry 11/14"
    created = InviteResult("created", url=PAGE, title=title, links=links, poster=True, going=True)
    assert render_partiful(created, "Admin") == (
        f"**Partiful made:** [{title}]({PAGE})\n• [Tickets]({TICKETS})\n• [Zed Fox songs I like]({PLAYLIST})"
    )
    plain = InviteResult("created", url=PAGE, title=title, links=links[:1])
    assert render_partiful(plain, "Admin", event_note="Calendar event not updated: x") == (
        f"**Partiful made:** [{title}]({PAGE})\n• [Tickets]({TICKETS})\nCalendar event not updated: x"
    )
    exists = InviteResult("exists", url=PAGE)
    assert render_partiful(exists, "Admin") == f"This show already has a Partiful: [Partiful]({PAGE})"
    dry = InviteResult("dry_run", title=title, links=links[:1])
    assert render_partiful(dry, "Admin") == f"**Would make the Partiful** {title}\n• [Tickets]({TICKETS})"

    need = InviteResult("need_tickets")
    texted = InviteResult("signed_out", phone_tail="0100")
    silent = InviteResult("signed_out")
    failed = InviteResult("failed", reason="Partiful refused | call='createEvent'")
    assert partiful_problem(need, "Admin") == (
        "I need the ticket link to make the Partiful. Reply with it and I will make it."
    )
    assert partiful_problem(texted, "Admin") == (
        "Partiful is signed out. I texted a code to the number ending 0100. "
        "Send me the code, then ask for the Partiful again."
    )
    assert partiful_problem(silent, "Admin") == (
        "Partiful is signed out. Admin: set CONCERT_MCP_PARTIFUL_PHONE, then ask me to log in to Partiful."
    )
    assert partiful_problem(failed, "Admin") == "Partiful failed: Partiful refused | call='createEvent'"
    assert partiful_problem(InviteResult("failed"), "Admin") == "Partiful failed: unknown error"
    assert render_partiful(texted, "Admin") == partiful_problem(texted, "Admin")
    assert partiful_note(created, "Admin") is None and partiful_note(exists, "Admin") is None
    assert partiful_note(need, "Admin") == "Partiful not made: " + partiful_problem(need, "Admin")

    assert render_partiful_code_sent("0100") == "I texted a Partiful code to the number ending 0100. Send me the code."
    assert render_partiful_signed_in("Pat Example") == "Partiful is signed in as Pat Example."
    assert render_partiful_signed_in(None) == "Partiful is signed in."
    assert PARTIFUL_NO_PHONE.format(operator="Admin") == (
        "Partiful has no phone number to text a code to. Admin: set CONCERT_MCP_PARTIFUL_PHONE (README.md#partiful)."
    )
    assert provider_reply("partiful", "Admin") == "Partiful refused the request. Admin: check the host log."
    assert provider_reply("partiful-signed-out", "Admin") == "Partiful is signed out. Ask me to log in to Partiful."
    assert provider_reply("partiful-code", "Admin") == (
        "Partiful did not accept that code. Ask me to log in to Partiful for a new one."
    )
