"""text/artists.py: artist picking, liked-song selection, uri checks and the legacy teaser record."""

from __future__ import annotations

import pytest

from concert_mcp.result import FixInput
from concert_mcp.text.artists import (
    TEASER_FOLDER,
    Artist,
    NoArtistMatch,
    TeaserResult,
    find_playlist_named,
    item_track,
    liked_hits,
    new_uris,
    pick_artist,
    playlist_row,
    sort_oldest_first,
    track_row,
    validate_track_uris,
)


def spotify_artist(name: str, id: str, followers: int) -> dict:
    return {
        "name": name,
        "id": id,
        "external_urls": {"spotify": f"https://open.spotify.com/artist/{id}"},
        "followers": {"total": followers},
    }


ZED_FOX = Artist("Zed Fox", "artist-zedfox", "https://open.spotify.com/artist/artist-zedfox")


# ---------- pick_artist ----------


def test_pick_artist_exact_beats_higher_follower_non_exact() -> None:
    items = [spotify_artist("Zed Fox & Friends", "a2", 9_000_000), spotify_artist("Zed Fox", "a1", 500_000)]
    artist, corrected = pick_artist("Zed Fox", items)
    assert artist == ZED_FOX.__class__("Zed Fox", "a1", "https://open.spotify.com/artist/a1")
    assert corrected is False


def test_pick_artist_two_exact_most_followers_wins() -> None:
    items = [spotify_artist("Zed Fox", "small", 10), spotify_artist("ZED FOX", "big", 500_000)]
    artist, corrected = pick_artist("zed fox", items)
    assert artist.id == "big"
    assert corrected is False


def test_pick_artist_exact_match_normalizes_ampersand() -> None:
    items = [spotify_artist("Simon & Garfunkel", "sg", 1)]
    artist, corrected = pick_artist("Simon and Garfunkel", items)
    assert (artist.id, corrected) == ("sg", False)


def test_pick_artist_word_subset_is_corrected() -> None:
    items = [spotify_artist("Zed Fox & Friends", "a2", 100)]
    artist, corrected = pick_artist("zed fox", items)
    assert artist.name == "Zed Fox & Friends"
    assert artist.url == "https://open.spotify.com/artist/a2"
    assert corrected is True


def test_pick_artist_word_superset_is_corrected_most_followers() -> None:
    items = [spotify_artist("Fox", "w1", 10), spotify_artist("Zed", "j1", 50)]
    artist, corrected = pick_artist("Zed Fox", items)
    assert (artist.id, corrected) == ("j1", True)


def test_pick_artist_single_shared_word_is_not_close() -> None:
    items = [spotify_artist("Band of Horses", "boh", 3_000_000), spotify_artist("Test Icicles", "ti", 9)]
    with pytest.raises(NoArtistMatch) as info:
        pick_artist("ZZZ Test Band", items)
    msg = str(info.value)
    assert "no close Spotify artist match" in msg
    assert "artist='ZZZ Test Band'" in msg
    assert "top_results='Band of Horses, Test Icicles'" in msg
    assert info.value.top_names == ("Band of Horses", "Test Icicles")


def test_pick_artist_top_results_capped_at_five() -> None:
    items = [spotify_artist(f"Other {i}", f"o{i}", i) for i in range(8)]
    with pytest.raises(NoArtistMatch) as info:
        pick_artist("ZZZ Test Band", items)
    assert str(info.value).endswith("top_results='Other 0, Other 1, Other 2, Other 3, Other 4'")


def test_pick_artist_no_items() -> None:
    with pytest.raises(NoArtistMatch) as info:
        pick_artist("ZZZ Test Band", [])
    assert str(info.value) == "no Spotify artist matches | artist='ZZZ Test Band'"
    assert info.value.top_names == ()


def test_pick_artist_missing_followers_counts_as_zero() -> None:
    items = [{"name": "Zed Fox", "id": "nofollow"}, spotify_artist("Zed Fox", "big", 1)]
    artist, _ = pick_artist("Zed Fox", items)
    assert artist.id == "big"
    assert pick_artist("Zed Fox", [{"name": "Zed Fox", "id": "nofollow"}])[0].url is None


# ---------- liked rows ----------


def liked(name: str, uri: str, added_at: str, *, artists: list[str], artist_ids: list[str] | None) -> dict:
    row = {"name": name, "artists": artists, "uri": uri, "added_at": added_at}
    if artist_ids is not None:
        row["artist_ids"] = artist_ids
    return row


def test_liked_hits_by_id_keeps_order() -> None:
    rows = [
        liked(
            "Second Wind", "spotify:track:t2", "2021-06-15T10:00:00Z", artists=["Zed Fox"], artist_ids=["artist-zedfox"]
        ),
        liked("Other", "spotify:track:x", "2020-01-01T00:00:00Z", artists=["Someone"], artist_ids=["someone"]),
        liked(
            "First Light",
            "spotify:track:t1",
            "2019-03-01T10:00:00Z",
            artists=["ZED FOX"],
            artist_ids=["artist-zedfox"],
        ),
    ]
    hits = liked_hits(rows, ZED_FOX)
    assert [h["uri"] for h in hits] == ["spotify:track:t2", "spotify:track:t1"]


def test_liked_hits_by_name_on_old_cache_rows_without_ids() -> None:
    rows = [
        liked(
            "Second Wind", "spotify:track:t2", "2021-06-15T10:00:00Z", artists=["zed  fox", "Zork."], artist_ids=None
        ),
        liked("Other", "spotify:track:x", "2020-01-01T00:00:00Z", artists=["Someone"], artist_ids=None),
        liked("Feature", "spotify:track:f", "2020-01-01T00:00:00Z", artists=["Zork."], artist_ids=[]),
    ]
    assert [h["name"] for h in liked_hits(rows, ZED_FOX)] == ["Second Wind"]


def test_liked_hits_empty() -> None:
    assert liked_hits([], ZED_FOX) == []


def test_sort_oldest_first() -> None:
    rows = [
        {"uri": "b", "added_at": "2021-06-15T10:00:00Z"},
        {"uri": "c"},
        {"uri": "a", "added_at": "2019-03-01T10:00:00Z"},
    ]
    assert [r["uri"] for r in sort_oldest_first(rows)] == ["c", "a", "b"]


def test_new_uris_keeps_order_and_drops_have() -> None:
    wanted = ["spotify:track:t1", "spotify:track:t2", "spotify:track:t3"]
    assert new_uris(wanted, {"spotify:track:t2"}) == ["spotify:track:t1", "spotify:track:t3"]
    assert new_uris(wanted, iter(wanted)) == []
    assert new_uris([], set()) == []


def test_validate_track_uris() -> None:
    assert validate_track_uris([" spotify:track:t1 ", "", "spotify:track:t2"]) == [
        "spotify:track:t1",
        "spotify:track:t2",
    ]
    with pytest.raises(FixInput) as info:
        validate_track_uris(["spotify:track:t1", "spotify:album:a1", "nope"])
    assert info.value.error == "not track uris: spotify:album:a1, nope"
    assert info.value.fix == "use spotify:track:<id>"


# ---------- rows ----------


def test_track_row() -> None:
    track = {
        "name": "Second Wind",
        "artists": [{"name": "Zed Fox", "id": "artist-zedfox"}, {"name": "Mr Example", "id": "mrg"}],
        "album": {"name": "Test Album"},
        "uri": "spotify:track:t2",
        "external_urls": {"spotify": "https://open.spotify.com/track/t2"},
        "duration_ms": 200000,
    }
    assert track_row(track) == {
        "name": "Second Wind",
        "artists": ["Zed Fox", "Mr Example"],
        "artist_ids": ["artist-zedfox", "mrg"],
        "album": "Test Album",
        "uri": "spotify:track:t2",
        "url": "https://open.spotify.com/track/t2",
        "duration_ms": 200000,
    }
    assert track_row(track, "2021-06-15T10:00:00Z")["added_at"] == "2021-06-15T10:00:00Z"
    assert track_row({})["artists"] == [] and track_row({})["album"] is None


def test_playlist_row_items_total_and_tracks_total() -> None:
    base = {
        "name": "2026-11-14 | Zed Fox",
        "id": "pl1",
        "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"},
        "public": False,
        "owner": {"display_name": "Listener", "id": "user1"},
        "description": "Teaser",
    }
    new_api = playlist_row({**base, "items": {"total": 12}})
    old_api = playlist_row({**base, "tracks": {"total": 7}})
    assert new_api["tracks"] == 12
    assert old_api["tracks"] == 7
    assert new_api == {
        "name": "2026-11-14 | Zed Fox",
        "id": "pl1",
        "url": "https://open.spotify.com/playlist/pl1",
        "public": False,
        "tracks": 12,
        "owner": "Listener",
        "owner_id": "user1",
        "description": "Teaser",
    }
    assert playlist_row({**base, "owner": {"id": "user1"}})["owner"] == "user1"
    assert playlist_row({"name": "x"})["tracks"] is None


def test_item_track_item_track_and_episode() -> None:
    track = {"type": "track", "uri": "spotify:track:t1"}
    assert item_track({"item": track}) is track
    assert item_track({"track": track}) is track
    assert item_track({"item": {"uri": "spotify:track:untyped"}}) == {"uri": "spotify:track:untyped"}
    assert item_track({"item": {"type": "episode", "uri": "spotify:episode:e1"}}) is None
    assert item_track({"item": None}) is None
    assert item_track({}) is None


def test_find_playlist_named() -> None:
    pls = [{"name": "Road trip", "id": "a"}, {"name": "2026-11-14  |  ZED FOX", "id": "b"}]
    assert find_playlist_named(pls, "2026-11-14 | Zed Fox")["id"] == "b"
    assert find_playlist_named(pls, "2026-11-15 | Zed Fox") is None
    assert find_playlist_named([], "x") is None


# ---------- TeaserResult.to_json ----------


def test_teaser_created_record_has_folder_and_reply() -> None:
    res = TeaserResult(
        status="created",
        artist=ZED_FOX,
        date="2026-11-14",
        name="2026-11-14 | Zed Fox",
        liked_count=3,
        url="https://open.spotify.com/playlist/pl1",
        id="pl1",
        added=3,
        already_there=0,
        tracks=("First Light", "Second Wind", "Third Rail"),
    )
    rec = res.to_json()
    assert rec == {
        "artist": {"name": "Zed Fox", "id": "artist-zedfox", "url": "https://open.spotify.com/artist/artist-zedfox"},
        "date": "2026-11-14",
        "name": "2026-11-14 | Zed Fox",
        "liked_count": 3,
        "tracks": ["First Light", "Second Wind", "Third Rail"],
        "created": True,
        "id": "pl1",
        "url": "https://open.spotify.com/playlist/pl1",
        "added": 3,
        "already_there": 0,
        "folder": "Concerts / Teasers (move it there in the Spotify app; the API cannot)",
        "reply": "Teaser playlist created: 2026-11-14 | Zed Fox (3 liked songs) https://open.spotify.com/playlist/pl1",
    }
    assert rec["folder"] == TEASER_FOLDER
    assert "ok" not in rec and "dry_run" not in rec


def test_teaser_updated_record() -> None:
    res = TeaserResult(
        status="updated",
        artist=ZED_FOX,
        date="2026-11-14",
        name="2026-11-14 | Zed Fox",
        liked_count=3,
        url="https://open.spotify.com/playlist/pl1",
        id="pl1",
        added=1,
        already_there=2,
        tracks=("a", "b", "c"),
        exists=True,
    )
    rec = res.to_json()
    assert rec["created"] is False
    assert (rec["added"], rec["already_there"]) == (1, 2)
    assert (
        rec["reply"]
        == "Teaser playlist updated: 2026-11-14 | Zed Fox (3 liked songs) https://open.spotify.com/playlist/pl1"
    )
    assert "exists" not in rec


def test_teaser_none_record_reply() -> None:
    res = TeaserResult(status="none", artist=ZED_FOX, date="2026-11-14", name="2026-11-14 | Zed Fox", liked_count=0)
    rec = res.to_json()
    assert rec["created"] is False
    assert rec["tracks"] == []
    assert rec["reply"] == "No teaser: you have not liked any songs by Zed Fox yet."
    assert "url" not in rec and "folder" not in rec


def test_teaser_dry_run_record() -> None:
    res = TeaserResult(
        status="dry_run",
        artist=ZED_FOX,
        date="2026-11-14",
        name="2026-11-14 | Zed Fox",
        liked_count=3,
        tracks=("a", "b", "c"),
        exists=True,
    )
    rec = res.to_json()
    assert rec["dry_run"] is True
    assert rec["exists"] is True
    assert rec["created"] is False
    assert "reply" not in rec


def test_teaser_failed_unavailable_timeout_records() -> None:
    for status in ("failed", "unavailable", "timeout"):
        res = TeaserResult(
            status=status, artist=None, date="2026-11-14", name=None, liked_count=0, reason="Spotify API refused"
        )
        rec = res.to_json()
        assert rec["ok"] is False
        assert rec["error"] == "Spotify API refused"
        assert rec["artist"] is None
        assert rec["created"] is False
        assert "reply" not in rec
