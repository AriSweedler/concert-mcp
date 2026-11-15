"""PartifulClient on FakeUrlopen, FakeClock and MemoryState: the login, the session, the callables, the upload."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
from typing import Any

import pytest

from concert_mcp.adapters.partiful_http import API, CUSTOM_TOKEN, SECURETOKEN, PartifulClient, new_object_id
from concert_mcp.config import Settings
from concert_mcp.result import ProviderError
from concert_mcp.text.partiful import STORAGE
from fakes import FakeClock, FakeUrlopen, MemoryState, http_error

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
PHONE = "+15555550100"
RENEWED = {"id_token": "idt-new", "user_id": "u1", "expires_in": "3600", "refresh_token": "rt-1"}


def result(data: Any) -> dict[str, Any]:
    return {"result": {"data": data}}


def body_of(request: tuple[str, str, dict[str, str], bytes | None]) -> Any:
    return json.loads((request[3] or b"{}").decode("utf-8"))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def state(clock: FakeClock) -> MemoryState:
    return MemoryState(clock=clock)


@pytest.fixture
def fake() -> FakeUrlopen:
    return FakeUrlopen({("POST", SECURETOKEN): RENEWED})


@pytest.fixture
def client(settings: Settings, clock: FakeClock, state: MemoryState, fake: FakeUrlopen) -> PartifulClient:
    return PartifulClient(settings, clock, state, urlopen=fake, object_id=lambda: "obj1")


def sign_in(client: PartifulClient, **creds: Any) -> None:
    client.state.write_json(client.path, {"refresh_token": "rt-1", **creds}, mode=0o600)


def test_object_ids_are_21_characters() -> None:
    assert len(new_object_id()) == 21 and new_object_id() != new_object_id()


def test_login_texts_a_code_then_stores_only_the_refresh_token(client: PartifulClient, fake: FakeUrlopen) -> None:
    assert client.signed_in() is False
    fake.add("POST", f"{API}/sendAuthCodeTrusted", result({}))
    client.send_code(PHONE)
    assert body_of(fake.requests[-1]) == {"data": {"params": {"phoneNumber": PHONE, "silent": False}}}
    assert fake.requests[-1][2]["Referer"] == "https://partiful.com/"

    fake.add("POST", f"{API}/getLoginToken", result({"token": "custom-1", "isNewUser": False}))
    fake.add("POST", CUSTOM_TOKEN, {"idToken": "idt-0", "refreshToken": "rt-1", "expiresIn": "3600"})
    client.login(PHONE, "123456")
    assert body_of(fake.requests[-2])["data"]["params"] == {
        "phoneNumber": PHONE,
        "authCode": "123456",
        "utms": {},
        "deviceType": "desktopWeb",
    }
    assert body_of(fake.requests[-1]) == {"token": "custom-1", "returnSecureToken": True}
    assert client.state.read_json(client.path, {}) == {"refresh_token": "rt-1"}
    assert client.signed_in() is True


def test_login_refusals(client: PartifulClient, fake: FakeUrlopen) -> None:
    fake.add("POST", f"{API}/sendAuthCodeTrusted", http_error(429, json.dumps({"error": {"message": "slow down"}})))
    with pytest.raises(ProviderError) as e:
        client.send_code(PHONE)
    assert e.value.provider == "partiful"
    assert e.value.error == "Partiful refused | call='sendAuthCodeTrusted' status='429' message='slow down'"
    assert e.value.reply == "Partiful refused the request. Admin: check the host log."

    fake.add("POST", f"{API}/getLoginToken", http_error(400, json.dumps({"error": {"message": "Invalid code"}})))
    with pytest.raises(ProviderError) as e:
        client.login(PHONE, "000000")
    assert e.value.provider == "partiful-code" and "Invalid code" in e.value.error
    assert e.value.reply == "Partiful did not accept that code. Ask me to log in to Partiful for a new one."
    # A 200 with no token in it is a refusal too, and so is an answer that is not JSON.
    fake.add("POST", f"{API}/getLoginToken", result({}))
    with pytest.raises(ProviderError) as e:
        client.login(PHONE, "000000")
    assert e.value.provider == "partiful-code"
    fake.add("POST", f"{API}/getLoginToken", "<html>down</html>")
    with pytest.raises(ProviderError) as e:
        client.login(PHONE, "000000")
    assert "message='<html>down</html>'" in e.value.error

    fake.add("POST", f"{API}/getLoginToken", result({"token": "custom-1"}))
    fake.add("POST", CUSTOM_TOKEN, http_error(400, json.dumps({"error": {"message": "INVALID_CUSTOM_TOKEN"}})))
    with pytest.raises(ProviderError) as e:
        client.login(PHONE, "123456")
    assert e.value.provider == "partiful" and "INVALID_CUSTOM_TOKEN" in e.value.error
    assert client.signed_in() is False


def test_id_token_is_renewed_kept_and_reused(client: PartifulClient, fake: FakeUrlopen, clock: FakeClock) -> None:
    with pytest.raises(ProviderError) as e:
        client.id_token()
    assert e.value.provider == "partiful-signed-out" and e.value.error == "Partiful is signed out | no session stored"

    sign_in(client)
    assert client.id_token() == ("u1", "idt-new")
    assert fake.requests[-1][3] == b"grant_type=refresh_token&refresh_token=rt-1"
    stored = client.state.read_json(client.path, {})
    assert stored == {"refresh_token": "rt-1", "id_token": "idt-new", "uid": "u1", "expires_at": NOW.timestamp() + 3600}
    assert client.id_token() == ("u1", "idt-new") and len(fake.requests) == 1  # still fresh: no second renewal

    clock.advance(3400)  # inside the slack: renewed again; Google may leave the refresh token out of its answer
    fake.add("POST", SECURETOKEN, {"id_token": "idt-2"})
    assert client.id_token() == ("", "idt-2")
    assert client.state.read_json(client.path, {})["refresh_token"] == "rt-1"


def test_a_refused_renewal_is_signed_out_whatever_the_reason(client: PartifulClient, fake: FakeUrlopen) -> None:
    sign_in(client)
    fake.add("POST", SECURETOKEN, http_error(400, json.dumps({"error": {"message": "TOKEN_EXPIRED"}})))
    with pytest.raises(ProviderError) as e:
        client.id_token()
    assert e.value.provider == "partiful-signed-out"
    assert e.value.error == "Partiful is signed out | renewal refused: TOKEN_EXPIRED"
    assert e.value.reply == "Partiful is signed out. Ask me to log in to Partiful."
    fake.add("POST", SECURETOKEN, http_error(400, "nope"))
    with pytest.raises(ProviderError) as e:
        client.id_token()
    assert e.value.error == "Partiful is signed out | renewal refused: no reason given"
    # Any other failure is Partiful's (or Google's) trouble, not a dead session.
    fake.add("POST", SECURETOKEN, http_error(503, ""))
    with pytest.raises(ProviderError) as e:
        client.id_token()
    assert e.value.provider == "partiful" and "status='503'" in e.value.error
    fake.add("POST", SECURETOKEN, urllib.error.URLError("no route"))
    with pytest.raises(ProviderError) as e:
        client.id_token()
    assert e.value.error == "Partiful unreachable | call='securetoken' reason='no route'"


def test_callables(client: PartifulClient, fake: FakeUrlopen, state: MemoryState) -> None:
    state.write_json(client.path, ["not", "a", "session"])
    assert client.signed_in() is False
    sign_in(client)
    fake.add("POST", f"{API}/getCurrentUser", result({"user": {"name": "Pat Example"}}))
    assert client.me() == {"name": "Pat Example"}
    method, url, headers, _body = fake.requests[-1]
    assert (method, url) == ("POST", f"{API}/getCurrentUser") and headers["Authorization"] == "<redacted>"
    assert body_of(fake.requests[-1]) == {"data": {"params": {"targetUserId": "u1"}}}
    fake.add("POST", f"{API}/getCurrentUser", {"result": None})
    assert client.me() == {}

    fake.add("POST", f"{API}/createEvent", result("ev1"))
    assert client.create_event({"title": "T"}) == "ev1"
    assert body_of(fake.requests[-1]) == {"data": {"params": {"event": {"title": "T"}, "cohostIds": []}}}
    fake.add("POST", f"{API}/createEvent", result({}))
    with pytest.raises(ProviderError) as e:
        client.create_event({"title": "T"})
    assert "no event id in the answer" in e.value.error

    fake.add("POST", f"{API}/setEventLocation", result({}))
    client.set_location("ev1", {"type": "freeform", "value": "The Quarry"})
    assert body_of(fake.requests[-1])["data"]["params"] == {
        "eventId": "ev1",
        "locationInfo": {"type": "freeform", "value": "The Quarry"},
    }

    fake.add("POST", f"{API}/addGuest", result({"id": "g1", "status": "GOING"}))
    client.rsvp_going("ev1", "Pat Example", "America/Los_Angeles")
    assert body_of(fake.requests[-1])["data"]["params"] == {
        "eventId": "ev1",
        "rsvp": {
            "status": "GOING",
            "count": 1,
            "plusOneCount": 0,
            "name": "Pat Example",
            "timezone": "America/Los_Angeles",
        },
    }

    fake.add("POST", f"{API}/setEventLocation", http_error(500, json.dumps({"error": {"message": "boom"}})))
    with pytest.raises(ProviderError) as e:
        client.set_location("ev1", {})
    assert e.value.error == "Partiful refused | call='setEventLocation' status='500' message='boom'"
    fake.add("POST", f"{API}/setEventLocation", {"unexpected": True})
    with pytest.raises(ProviderError):
        client.set_location("ev1", {})


def test_a_401_from_partiful_drops_the_id_token_and_is_signed_out(client: PartifulClient, fake: FakeUrlopen) -> None:
    sign_in(client, id_token="idt-old", uid="u1", expires_at=NOW.timestamp() + 3600)
    fake.add("POST", f"{API}/createEvent", http_error(401, json.dumps({"error": {"message": "Not authenticated"}})))
    with pytest.raises(ProviderError) as e:
        client.create_event({"title": "T"})
    assert e.value.provider == "partiful-signed-out" and e.value.error.endswith("createEvent answered 401")
    assert client.state.read_json(client.path, {}) == {"refresh_token": "rt-1", "uid": "u1"}


def test_find_place(client: PartifulClient, fake: FakeUrlopen) -> None:
    sign_in(client)
    fake.add("POST", f"{API}/searchLocationAutocomplete", result({"results": []}))
    assert client.find_place("Nowhere") is None
    fake.add("POST", f"{API}/searchLocationAutocomplete", result(None))
    assert client.find_place("Nowhere") is None
    hit = {"id": "I1", "completionUrl": "/v1/search?q=x"}
    place = {"name": "The Quarry", "formattedAddressLines": ["1 Main St", "San Francisco, CA 94100"]}
    fake.add("POST", f"{API}/searchLocationAutocomplete", result({"results": [hit, {"id": "I2"}]}))
    fake.add("POST", f"{API}/getLocationDetails", result(place))
    assert client.find_place("The Quarry, San Francisco") == place
    assert body_of(fake.requests[-2])["data"]["params"] == {"query": "The Quarry, San Francisco"}
    assert body_of(fake.requests[-1])["data"]["params"] == {"searchInput": hit, "augmentLocationDetails": True}
    fake.add("POST", f"{API}/getLocationDetails", result({"name": "A region"}))
    assert client.find_place("The Quarry") is None


def test_upload_and_download(client: PartifulClient, fake: FakeUrlopen) -> None:
    sign_in(client)
    fake.add("POST", STORAGE, {"name": "external/user/u1/obj1"})
    assert client.upload_image(b"JPEG", "image/jpeg") == "external/user/u1/obj1"
    method, url, headers, body = fake.requests[-1]
    assert url == f"{STORAGE}?name=external%2Fuser%2Fu1%2Fobj1" and body == b"JPEG"
    assert headers["Content-type"] == "image/jpeg" and headers["Authorization"] == "<redacted>"
    fake.add("POST", STORAGE, http_error(403, "Permission denied."))
    with pytest.raises(ProviderError) as e:
        client.upload_image(b"JPEG", "image/jpeg")
    assert e.value.error == "Partiful refused | call='storage upload' status='403' message='Permission denied.'"

    fake.add("GET", "https://i.example/cover", b"COVER")
    assert client.download("https://i.example/cover") == b"COVER"
    fake.add("GET", "https://i.example/cover", b"")
    with pytest.raises(ProviderError):
        client.download("https://i.example/cover")
    fake.add("GET", "https://i.example/cover", http_error(404, "gone"))
    with pytest.raises(ProviderError) as e:
        client.download("https://i.example/cover")
    assert "call='download' status='404'" in e.value.error
