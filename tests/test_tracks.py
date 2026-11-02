"""text/tracks.py: the loose name forms, the plain query, and which search candidate is the setlist song."""

from __future__ import annotations

from concert_mcp.text.setlists import Song
from concert_mcp.text.tracks import (
    artist_match,
    candidate_summary,
    core_name,
    loose_name,
    loose_words,
    names_match,
    pick_track,
    plain_query,
)


def track(name: str, *artists: str, ids: tuple[str | None, ...] = (), uri: str = "spotify:track:x") -> dict:
    return {
        "name": name,
        "uri": uri,
        "artists": [{"name": a, "id": ids[i] if i < len(ids) else None} for i, a in enumerate(artists)],
    }


def test_loose_forms_ignore_case_punctuation_and_accents() -> None:
    assert loose_name("Z.Fox") == loose_name("Z Fox") == loose_name("zfox") == loose_name("Z. FOX") == "zfox"
    assert loose_words("Foxes Don’t Cry") == loose_words("Foxes Don't Cry") == ["foxes", "dont", "cry"]
    assert loose_name("Zoë Fox") == "zoefox" and loose_words("Mötley Zèd") == ["motley", "zed"]
    assert loose_words("Song - Acoustic") == loose_words("Song (acoustic)") == ["song", "acoustic"]
    assert loose_words("") == [] and loose_name(None) == ""
    assert loose_words("Zed Fox & Friends") == ["zed", "fox", "friends"]
    assert loose_words("Rock 'n' Roll") == ["rock", "n", "roll"] and loose_name("O’Brien") == "obrien"


def test_core_name_drops_trailing_parentheticals_and_a_dash_version() -> None:
    assert core_name("Third Rail (acoustic)") == "Third Rail"
    assert core_name("Third Rail [live]") == "Third Rail"
    assert core_name("Third Rail - 2011 Remaster") == "Third Rail"
    assert core_name("Third Rail (feat. Other Band) - Live") == "Third Rail"
    assert core_name("Song (with Other Band) (acoustic)") == "Song"
    assert core_name("Third Rail") == "Third Rail"
    assert core_name("Twenty-One") == "Twenty-One"  # a hyphen inside a word is not a version dash
    assert core_name("(Intro)") == "(Intro)"


def test_plain_query_uses_the_core_name_and_the_cover_artist() -> None:
    assert plain_query(Song("Third Rail (acoustic)"), "Zed Fox") == "Third Rail Zed Fox"
    assert plain_query(Song("Borrowed Song", "Other Band"), "Zed Fox") == "Borrowed Song Other Band"


def test_names_match_on_the_whole_name_or_the_core() -> None:
    assert names_match("Foxes Don’t Cry", "Foxes Don't Cry")
    assert names_match("Third Rail (acoustic)", "Third Rail")
    assert names_match("Third Rail", "Third Rail - Acoustic")
    assert names_match("Third Rail", "Third Rail (feat. Other Band)")
    assert names_match("Hell", "HELL")
    assert not names_match("Hell", "Hello")
    assert not names_match("Vision", "Visions")
    assert not names_match("Alive", "Alive Again")  # a longer title with no version marker is another song
    assert not names_match("", "Hell") and not names_match("Hell", "")


def test_artist_match_by_id_then_by_loose_name() -> None:
    t = track("Hell", "Z.Fox", ids=("artist-zfox",))
    assert artist_match(t, "Z Fox")
    assert artist_match(t, "zfox")
    assert artist_match(t, "Someone Else", "artist-zfox")
    assert not artist_match(t, "Someone Else", "artist-other")
    assert not artist_match(t, "Someone Else")
    assert artist_match(track("Duet", "Other Band", "Zed Fox"), "Zed Fox")
    assert not artist_match(track("Empty"), "Zed Fox")
    assert not artist_match(track("Blank", ""), "")


def test_pick_track_takes_the_first_song_by_the_artist() -> None:
    wrong_artist = track("Hell", "Other Band", uri="spotify:track:wrong")
    other_song = track("Heaven", "Zed Fox", uri="spotify:track:other")
    right = track("Hell", "Zed Fox", uri="spotify:track:right")
    assert pick_track([wrong_artist, other_song, right], "Hell", "Zed Fox") is right
    assert pick_track([wrong_artist, other_song], "Hell", "Zed Fox") is None
    assert pick_track([], "Hell", "Zed Fox") is None
    by_id = track("Hell", "Z. Fox", ids=("artist-zedfox",), uri="spotify:track:id")
    assert pick_track([by_id], "Hell", "Zed Fox") is None
    assert pick_track([by_id], "Hell", "Zed Fox", "artist-zedfox") is by_id


def test_candidate_summary_reads_as_a_log_field() -> None:
    assert candidate_summary([]) == "none"
    assert candidate_summary([track("Hell", "Zed Fox"), track("Duet", "A", "B")]) == "Hell by Zed Fox; Duet by A, B"
