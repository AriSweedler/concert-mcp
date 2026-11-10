"""text/tracks.py: the loose name forms, the plain query, and which search candidate is the setlist song."""

from __future__ import annotations

from concert_mcp.text.setlists import Song
from concert_mcp.text.tracks import (
    album_rank,
    artist_match,
    candidate_summary,
    core_name,
    loose_name,
    loose_words,
    names_match,
    pick_track,
    plain_query,
    rank_key,
    release_key,
    stale_rows,
    track_label,
    version_rank,
)


def track(
    name: str,
    *artists: str,
    ids: tuple[str | None, ...] = (),
    uri: str = "spotify:track:x",
    album: tuple[str, str, str] | None = None,
) -> dict:
    t = {
        "name": name,
        "uri": uri,
        "artists": [{"name": a, "id": ids[i] if i < len(ids) else None} for i, a in enumerate(artists)],
    }
    if album:
        t["album"] = {"name": album[0], "album_type": album[1], "release_date": album[2]}
    return t


# The K.Flay case (beads-3ly): Spotify's `track:Black Wave artist:K.Flay` answered four candidates, in this order, and
# the first one was taken; the one wanted is the album track, the version she released first.
BLACK_WAVE_DELUXE = track(
    "Black Wave",
    "K.Flay",
    uri="spotify:track:bw-deluxe",
    album=("Every Where Is Some Where (Deluxe)", "album", "2017-10-27"),
)
BLACK_WAVE_SINGLE = track(
    "Black Wave", "K.Flay", uri="spotify:track:bw-single", album=("Black Wave", "single", "2017-02-03")
)
BLACK_WAVE_SEATTLE = track(
    "Black Wave - Seattle Sessions",
    "K.Flay",
    uri="spotify:track:bw-seattle",
    album=("Seattle Sessions", "single", "2018-11-16"),
)
BLACK_WAVE_ALBUM = track(
    "Black Wave", "K.Flay", uri="spotify:track:bw-album", album=("Every Where Is Some Where", "album", "2017-04-07")
)
BLACK_WAVE_HITS = [BLACK_WAVE_DELUXE, BLACK_WAVE_SINGLE, BLACK_WAVE_SEATTLE, BLACK_WAVE_ALBUM]


def test_tracks() -> None:
    # loose_forms_ignore_case_punctuation_and_accents:
    assert loose_name("Z.Fox") == loose_name("Z Fox") == loose_name("zfox") == loose_name("Z. FOX") == "zfox"
    assert loose_words("Foxes Don’t Cry") == loose_words("Foxes Don't Cry") == ["foxes", "dont", "cry"]
    assert loose_name("Zoë Fox") == "zoefox" and loose_words("Mötley Zèd") == ["motley", "zed"]
    assert loose_words("Song - Acoustic") == loose_words("Song (acoustic)") == ["song", "acoustic"]
    assert loose_words("") == [] and loose_name(None) == ""
    assert loose_words("Zed Fox & Friends") == ["zed", "fox", "friends"]
    assert loose_words("Rock 'n' Roll") == ["rock", "n", "roll"] and loose_name("O’Brien") == "obrien"

    # core_name_drops_trailing_parentheticals_and_a_dash_version:
    assert core_name("Third Rail (acoustic)") == "Third Rail"
    assert core_name("Third Rail [live]") == "Third Rail"
    assert core_name("Third Rail - 2011 Remaster") == "Third Rail"
    assert core_name("Third Rail (feat. Other Band) - Live") == "Third Rail"
    assert core_name("Song (with Other Band) (acoustic)") == "Song"
    assert core_name("Third Rail") == "Third Rail"
    assert core_name("Twenty-One") == "Twenty-One"  # a hyphen inside a word is not a version dash
    assert core_name("(Intro)") == "(Intro)"

    # plain_query_uses_the_core_name_and_the_cover_artist:
    assert plain_query(Song("Third Rail (acoustic)"), "Zed Fox") == "Third Rail Zed Fox"
    assert plain_query(Song("Borrowed Song", "Other Band"), "Zed Fox") == "Borrowed Song Other Band"

    # names_match_on_the_whole_name_or_the_core:
    assert names_match("Foxes Don’t Cry", "Foxes Don't Cry")
    assert names_match("Third Rail (acoustic)", "Third Rail")
    assert names_match("Third Rail", "Third Rail - Acoustic")
    assert names_match("Third Rail", "Third Rail (feat. Other Band)")
    assert names_match("Hell", "HELL")
    assert not names_match("Hell", "Hello")
    assert not names_match("Vision", "Visions")
    assert not names_match("Alive", "Alive Again")  # a longer title with no version marker is another song
    assert not names_match("", "Hell") and not names_match("Hell", "")

    # artist_match_by_id_then_by_loose_name:
    t = track("Hell", "Z.Fox", ids=("artist-zfox",))
    assert artist_match(t, "Z Fox")
    assert artist_match(t, "zfox")
    assert artist_match(t, "Someone Else", "artist-zfox")
    assert not artist_match(t, "Someone Else", "artist-other")
    assert not artist_match(t, "Someone Else")
    assert artist_match(track("Duet", "Other Band", "Zed Fox"), "Zed Fox")
    assert not artist_match(track("Empty"), "Zed Fox")
    assert not artist_match(track("Blank", ""), "")

    # pick_track_takes_the_first_song_by_the_artist:
    wrong_artist = track("Hell", "Other Band", uri="spotify:track:wrong")
    other_song = track("Heaven", "Zed Fox", uri="spotify:track:other")
    right = track("Hell", "Zed Fox", uri="spotify:track:right")
    assert pick_track([wrong_artist, other_song, right], "Hell", "Zed Fox") is right
    assert pick_track([wrong_artist, other_song], "Hell", "Zed Fox") is None
    assert pick_track([], "Hell", "Zed Fox") is None
    by_id = track("Hell", "Z. Fox", ids=("artist-zedfox",), uri="spotify:track:id")
    assert pick_track([by_id], "Hell", "Zed Fox") is None
    assert pick_track([by_id], "Hell", "Zed Fox", "artist-zedfox") is by_id

    # version_rank_plain_then_the_version_the_setlist_names_then_any_other:
    assert version_rank("Black Wave", "Black Wave") == 0
    assert version_rank("Black Wave", "Black Wave - Seattle Sessions") == 2
    assert version_rank("Giver", "Giver (softer version)") == 2
    assert version_rank("Giver", "Giver - Sir Sly Remix") == 2
    assert version_rank("Third Rail", "Third Rail - 2011 Remaster") == 2
    assert version_rank("Third Rail (acoustic)", "Third Rail") == 0
    assert version_rank("Third Rail (acoustic)", "Third Rail - Acoustic") == 1
    assert version_rank("Third Rail (acoustic)", "Third Rail - Live") == 2
    assert version_rank("(Intro)", "(Intro)") == 0  # a title that is only a parenthetical is plain

    # album_rank_album_single_compilation_unknown:
    assert album_rank(track("x", album=("a", "album", "2017"))) == 0
    assert album_rank(track("x", album=("a", "single", "2017"))) == 1
    assert album_rank(track("x", album=("a", "compilation", "2017"))) == 2
    assert album_rank(track("x", album=("a", "", "2017"))) == 3
    assert album_rank(track("x")) == 3
    assert album_rank(track("x", album=("a", "ALBUM", "2017"))) == 0

    # release_key_pads_a_year_or_a_month_to_a_day_and_sorts_no_date_last:
    assert release_key(track("x", album=("a", "album", "2017"))) == "2017-01-01"
    assert release_key(track("x", album=("a", "album", "2017-04"))) == "2017-04-01"
    assert release_key(track("x", album=("a", "album", "2017-04-07"))) == "2017-04-07"
    assert release_key(track("x", album=("a", "album", "2017-04-07T00:00:00"))) == "2017-04-07"
    assert release_key(track("x", album=("a", "album", ""))) == "9999-12-31"
    assert release_key(track("x")) == "9999-12-31"
    assert release_key(track("x", album=("a", "album", "2017"))) < release_key(
        track("x", album=("a", "album", "2017-04"))
    )

    # rank_key_orders_the_black_wave_candidates_album_first:
    assert rank_key(BLACK_WAVE_ALBUM, "Black Wave") == (0, 0, "2017-04-07")
    assert rank_key(BLACK_WAVE_DELUXE, "Black Wave") == (0, 0, "2017-10-27")
    assert rank_key(BLACK_WAVE_SINGLE, "Black Wave") == (0, 1, "2017-02-03")
    assert rank_key(BLACK_WAVE_SEATTLE, "Black Wave") == (2, 1, "2018-11-16")
    ranked = sorted(BLACK_WAVE_HITS, key=lambda t: rank_key(t, "Black Wave"))
    assert ranked == [BLACK_WAVE_ALBUM, BLACK_WAVE_DELUXE, BLACK_WAVE_SINGLE, BLACK_WAVE_SEATTLE]

    # pick_track_takes_the_version_the_artist_originally_released:
    pick = pick_track(BLACK_WAVE_HITS, "Black Wave", "K.Flay")
    assert pick is BLACK_WAVE_ALBUM
    assert pick["album"] == {"name": "Every Where Is Some Where", "album_type": "album", "release_date": "2017-04-07"}
    assert pick_track(list(reversed(BLACK_WAVE_HITS)), "Black Wave", "K.Flay") is BLACK_WAVE_ALBUM
    # Without the album track, the deluxe (an album) beats the earlier single; without both, the single beats the take.
    assert (
        pick_track([BLACK_WAVE_SINGLE, BLACK_WAVE_SEATTLE, BLACK_WAVE_DELUXE], "Black Wave", "K.Flay")
        is BLACK_WAVE_DELUXE
    )
    assert pick_track([BLACK_WAVE_SEATTLE, BLACK_WAVE_SINGLE], "Black Wave", "K.Flay") is BLACK_WAVE_SINGLE
    assert pick_track([BLACK_WAVE_SEATTLE], "Black Wave", "K.Flay") is BLACK_WAVE_SEATTLE
    # Giver: the plain title over the softer version and the remix, even when both come first.
    softer = track(
        "Giver (softer version)", "K.Flay", uri="spotify:track:g-soft", album=("Giver", "single", "2017-01-13")
    )
    remix = track(
        "Giver - Sir Sly Remix",
        "K.Flay",
        uri="spotify:track:g-remix",
        album=("Giver (Remixes)", "single", "2017-03-10"),
    )
    giver = track("Giver", "K.Flay", uri="spotify:track:g", album=("Every Where Is Some Where", "album", "2017-04-07"))
    assert pick_track([softer, remix, giver], "Giver", "K.Flay") is giver
    # A wrong-artist plain title never outranks the artist's own version; the name and artist filters still gate.
    cover = track("Black Wave", "Other Band", uri="spotify:track:cover", album=("Covers", "album", "2010"))
    assert pick_track([cover, BLACK_WAVE_SEATTLE], "Black Wave", "K.Flay") is BLACK_WAVE_SEATTLE

    # pick_track_ties_fall_back_to_spotify_order:
    first = track("Hell", "Zed Fox", uri="spotify:track:first")
    second = track("Hell", "Zed Fox", uri="spotify:track:second")
    assert pick_track([first, second], "Hell", "Zed Fox") is first
    assert pick_track([second, first], "Hell", "Zed Fox") is second

    # track_label_names_the_album_and_the_year:
    assert track_label(BLACK_WAVE_ALBUM) == "Black Wave (Every Where Is Some Where, 2017)"
    assert track_label(BLACK_WAVE_SEATTLE) == "Black Wave - Seattle Sessions (Seattle Sessions, 2018)"
    assert track_label(track("Hell", "Zed Fox")) == "Hell"
    assert track_label(track("Hell", "Zed Fox", album=("Album", "album", ""))) == "Hell (Album)"
    assert track_label({"name": "Hell", "album": {"release_date": "2011-05"}}) == "Hell (2011)"

    # stale_rows_finds_the_rows_holding_another_version_of_a_song:
    def row(name: str, uri: str) -> dict:
        return {"name": name, "uri": uri, "artists": ["K.Flay"]}

    rows = [
        row("Giver", "spotify:track:g"),
        row("Black Wave - Seattle Sessions", "spotify:track:bw-seattle"),
        row("High Enough", "spotify:track:he"),
    ]
    picks = [
        ("Giver", "spotify:track:g"),
        ("Black Wave", "spotify:track:bw-album"),
        ("High Enough", "spotify:track:he"),
    ]
    assert stale_rows(rows, picks) == [(1, "spotify:track:bw-seattle", "spotify:track:bw-album", "Black Wave")]
    # Nothing to swap: every row is a pick, or the pick is already in the playlist, or no song has that name.
    assert stale_rows(rows, [("Giver", "spotify:track:g")]) == []
    assert stale_rows(rows + [row("Black Wave", "spotify:track:bw-album")], picks) == []
    assert stale_rows([row("Unrelated", "spotify:track:u")], picks) == []
    assert stale_rows([], picks) == [] and stale_rows(rows, []) == []
    # Two rows of the same song: one swap, the first row; a row without a uri is skipped.
    twice = [
        row("Black Wave - Seattle Sessions", "spotify:track:bw-seattle"),
        row("Black Wave (Live)", "spotify:track:bw-live"),
    ]
    assert stale_rows(twice, picks) == [(0, "spotify:track:bw-seattle", "spotify:track:bw-album", "Black Wave")]
    assert stale_rows([{"name": "Black Wave", "uri": None}], picks) == []

    # candidate_summary_reads_as_a_log_field:
    assert candidate_summary([]) == "none"
    assert candidate_summary([track("Hell", "Zed Fox"), track("Duet", "A", "B")]) == "Hell by Zed Fox; Duet by A, B"
