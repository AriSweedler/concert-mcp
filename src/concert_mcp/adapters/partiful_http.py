"""Partiful over urllib (unit adapter-partiful): the calls partiful.com's own pages make, signed as the account.

Partiful publishes no API. Its site talks to Firebase callables: `POST https://api.partiful.com/<function>` with
`{"data": {"params": {...}}}`, answered with `{"result": {"data": ...}}`, and the account's Firebase ID token as
`Authorization: Bearer`. The shapes were read from the site's scripts and checked against the live service on
2026-11-14; a change on Partiful's side shows as a ProviderError('partiful').

The session. A login is two calls, as the site's login form makes them for a number that needs no captcha (US, Canada,
UK, Australia): `sendAuthCodeTrusted` texts a code, `getLoginToken` trades the code for a Firebase custom token, and
Google's `signInWithCustomToken` trades that for a refresh token. The refresh token is kept in the credentials file
(mode 600) and traded for a one-hour ID token only when a call needs one. Nothing stays logged in between calls, and
no token is ever logged. A session Partiful or Google no longer accepts is ProviderError('partiful-signed-out'): the
fix is a new login, whatever ended the old one.
"""

from __future__ import annotations

import json
import secrets
import string
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from concert_mcp.adapters.protocols import Clock, StateStore
from concert_mcp.config import Settings
from concert_mcp.log import kv, log_debug, log_info
from concert_mcp.result import ProviderError
from concert_mcp.text.partiful import STORAGE
from concert_mcp.text.reply import provider_reply

WEB = "https://partiful.com"
API = "https://api.partiful.com"
# The public web key of Partiful's Firebase project, as its pages carry it; Google refuses it without the referer.
FIREBASE_KEY = "AIzaSyCky6PJ7cHRdBKk5X7gjuWERWaKWBHr4_k"
SECURETOKEN = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_KEY}"
CUSTOM_TOKEN = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={FIREBASE_KEY}"
HEADERS = {"origin": WEB, "referer": f"{WEB}/", "user-agent": "Mozilla/5.0"}
# An event is edited the way the site's editor does it: a Firestore commit on events/<id>, as the host.
DATABASE = "projects/getpartiful/databases/(default)"
COMMIT = f"https://firestore.googleapis.com/v1/{DATABASE}/documents:commit"

# An ID token this close to its end is renewed first.
TOKEN_SLACK_S = 300
BODY_LIMIT = 200
_ID_ALPHABET = string.ascii_letters + string.digits + "_-"


def new_object_id() -> str:
    """A 21-character object name, the length the site gives an upload."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(21))


class PartifulClient:
    """PartifulApi over urllib. Always built: without a stored session every call but the login is signed out."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        state: StateStore,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        object_id: Callable[[], str] = new_object_id,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.state = state
        self.urlopen = urlopen
        self.object_id = object_id

    # ---------- the session ----------

    @property
    def path(self) -> Path:
        return Path(self.settings.partiful_credentials)

    def _creds(self) -> dict[str, Any]:
        raw = self.state.read_json(self.path, {})
        return dict(raw) if isinstance(raw, Mapping) else {}

    def _fail(self, what: str, status: int, body: Any) -> ProviderError:
        message = (body.get("error") or {}).get("message") if isinstance(body, Mapping) else None
        detail = str(message or body or "")[:BODY_LIMIT]
        return ProviderError(
            "partiful",
            f"Partiful refused | call='{what}' status='{status}' message='{detail}'",
            provider_reply("partiful", self.settings.operator_name),
        )

    def _signed_out(self, why: str) -> ProviderError:
        return ProviderError(
            "partiful-signed-out",
            f"Partiful is signed out | {why}",
            provider_reply("partiful-signed-out", self.settings.operator_name),
        )

    def _send(
        self, method: str, url: str, *, data: bytes | None, headers: Mapping[str, str], what: str
    ) -> tuple[int, bytes]:
        """One request: (status, body) for any HTTP answer; ProviderError('partiful') when nothing answers."""
        req = urllib.request.Request(url, data=data, headers={**HEADERS, **headers}, method=method)
        log_debug("partiful " + kv(method=method, call=what))
        try:
            with self.urlopen(req, timeout=self.settings.http_timeout_s) as resp:
                return int(resp.getcode()), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            raise ProviderError(
                "partiful",
                f"Partiful unreachable | call='{what}' reason='{e.reason}'",
                provider_reply("partiful", self.settings.operator_name),
            ) from e

    def _json(
        self, url: str, *, body: Any = None, form: Mapping[str, str] | None = None, token: str | None = None, what: str
    ) -> tuple[int, Any]:
        """POST JSON (or a form) and parse the JSON answer; an unparseable answer is its text."""
        if form is not None:
            data = urllib.parse.urlencode(dict(form)).encode("utf-8")
            headers = {"content-type": "application/x-www-form-urlencoded"}
        else:
            data = json.dumps(body).encode("utf-8")
            headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        status, raw = self._send("POST", url, data=data, headers=headers, what=what)
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def signed_in(self) -> bool:
        """Whether a session is stored. It may still be one Partiful no longer accepts; only a call tells."""
        return bool(self._creds().get("refresh_token"))

    def send_code(self, phone: str) -> None:
        """Have Partiful text a login code to `phone` (E.164)."""
        status, answer = self._json(
            f"{API}/sendAuthCodeTrusted",
            body={"data": {"params": {"phoneNumber": phone, "silent": False}}},
            what="sendAuthCodeTrusted",
        )
        if status != 200:
            raise self._fail("sendAuthCodeTrusted", status, answer)

    def login(self, phone: str, code: str) -> None:
        """Trade the texted code for a session and store it. ProviderError('partiful-code') when the code is refused."""
        params = {"phoneNumber": phone, "authCode": code, "utms": {}, "deviceType": "desktopWeb"}
        status, answer = self._json(f"{API}/getLoginToken", body={"data": {"params": params}}, what="getLoginToken")
        custom = ((answer.get("result") or {}).get("data") or {}).get("token") if isinstance(answer, Mapping) else None
        if status != 200 or not custom:
            failure = self._fail("getLoginToken", status, answer)
            raise ProviderError("partiful-code", failure.error, provider_reply("partiful-code", ""))
        status, answer = self._json(
            CUSTOM_TOKEN, body={"token": custom, "returnSecureToken": True}, what="signInWithCustomToken"
        )
        if status != 200 or not isinstance(answer, Mapping) or not answer.get("refreshToken"):
            raise self._fail("signInWithCustomToken", status, answer)
        # No ID token is kept from the login: the first call renews one, which also yields the account's uid.
        self.state.write_json(self.path, {"refresh_token": answer["refreshToken"]}, mode=0o600)
        log_info("partiful session stored | " + kv(path=str(self.path)))

    def id_token(self) -> tuple[str, str]:
        """(uid, ID token), renewed from the refresh token when stale; ProviderError('partiful-signed-out')."""
        creds = self._creds()
        if not creds.get("refresh_token"):
            raise self._signed_out("no session stored")
        if creds.get("id_token") and float(creds.get("expires_at") or 0) > self.clock.time() + TOKEN_SLACK_S:
            return str(creds.get("uid") or ""), str(creds["id_token"])
        status, answer = self._json(
            SECURETOKEN,
            form={"grant_type": "refresh_token", "refresh_token": str(creds["refresh_token"])},
            what="securetoken",
        )
        if status == 400:
            # INVALID_REFRESH_TOKEN, TOKEN_EXPIRED, USER_DISABLED, USER_NOT_FOUND: the session is over.
            message = (answer.get("error") or {}).get("message") if isinstance(answer, Mapping) else None
            raise self._signed_out(f"renewal refused: {message or 'no reason given'}")
        if status != 200 or not isinstance(answer, Mapping) or not answer.get("id_token"):
            raise self._fail("securetoken", status, answer)
        creds.update(
            id_token=answer["id_token"],
            uid=str(answer.get("user_id") or ""),
            expires_at=self.clock.time() + int(answer.get("expires_in") or 3600),
            refresh_token=answer.get("refresh_token") or creds["refresh_token"],
        )
        self.state.write_json(self.path, creds, mode=0o600)
        return creds["uid"], creds["id_token"]

    def _forget_id_token(self) -> None:
        creds = self._creds()
        creds.pop("id_token", None)
        creds.pop("expires_at", None)
        self.state.write_json(self.path, creds, mode=0o600)

    # ---------- the callables ----------

    def call(self, function: str, params: Mapping[str, Any]) -> Any:
        """One callable's `result.data`. A 401 means Partiful no longer takes the session."""
        _uid, token = self.id_token()
        status, answer = self._json(
            f"{API}/{function}", body={"data": {"params": dict(params)}}, token=token, what=function
        )
        if status == 401:
            self._forget_id_token()
            raise self._signed_out(f"{function} answered 401")
        if status != 200 or not isinstance(answer, Mapping) or "result" not in answer:
            raise self._fail(function, status, answer)
        return (answer["result"] or {}).get("data")

    def me(self) -> dict[str, Any]:
        """The signed-in account, as Partiful names it."""
        uid, _token = self.id_token()
        data = self.call("getCurrentUser", {"targetUserId": uid})
        return dict((data or {}).get("user") or {})

    def create_event(self, event: Mapping[str, Any]) -> str:
        """Create and publish an event page hosted by the account; its id. Nobody is invited or texted."""
        event_id = self.call("createEvent", {"event": dict(event), "cohostIds": []})
        if not isinstance(event_id, str) or not event_id:
            raise self._fail("createEvent", 200, "no event id in the answer")
        return event_id

    def find_place(self, query: str) -> dict[str, Any] | None:
        """The first place Partiful's own venue search finds for `query`, with its address; None when it finds none."""
        found = self.call("searchLocationAutocomplete", {"query": query}) or {}
        results = found.get("results") or []
        if not results:
            return None
        place = self.call("getLocationDetails", {"searchInput": results[0], "augmentLocationDetails": True})
        return dict(place) if isinstance(place, Mapping) and place.get("formattedAddressLines") else None

    def set_location(self, event_id: str, location_info: Mapping[str, Any]) -> None:
        """Set an event's location (a `structured` Maps place or `freeform` text)."""
        self.call("setEventLocation", {"eventId": event_id, "locationInfo": dict(location_info)})

    def event_links(self, event_id: str) -> list[dict[str, Any]]:
        """The link fields an event's page shows now, in page order."""
        data = self.call("getEventInfo", {"eventId": event_id}) or {}
        fields = (data.get("event") or {}).get("customFields") or []
        return [dict(f) for f in fields if isinstance(f, Mapping)]

    def set_links(self, event_id: str, fields: Sequence[Mapping[str, Any]]) -> None:
        """Replace an event's link fields. Nothing else on the page changes (checked live, 2026-11-14)."""
        uid, token = self.id_token()
        values = [{"mapValue": {"fields": {k: {"stringValue": str(v)} for k, v in f.items()}}} for f in fields]
        write = {
            "update": {
                "name": f"{DATABASE}/documents/events/{event_id}",
                "fields": {
                    "customFields": {"arrayValue": {"values": values}},
                    "updatedBy": {"referenceValue": f"{DATABASE}/documents/users/{uid}"},
                },
            },
            "updateMask": {"fieldPaths": ["customFields", "updatedBy"]},
            "currentDocument": {"exists": True},
        }
        status, answer = self._json(COMMIT, body={"writes": [write]}, token=token, what="firestore commit")
        if status != 200:
            raise self._fail("firestore commit", status, answer)

    def rsvp_going(self, event_id: str, name: str, timezone: str) -> None:
        """Mark the account as going to an event, alone, under `name`. Partiful refuses an RSVP without a name
        (400 "Bad request"; checked live, 2026-11-14)."""
        rsvp = {"status": "GOING", "count": 1, "plusOneCount": 0, "name": name, "timezone": timezone}
        self.call("addGuest", {"eventId": event_id, "rsvp": rsvp})

    def upload_image(self, data: bytes, content_type: str) -> str:
        """Store an image in the account's folder of Partiful's bucket; its object path. It cannot be deleted
        afterwards (the bucket refuses the account's DELETE), so an upload is only made for an invite about to be
        created."""
        uid, token = self.id_token()
        path = f"external/user/{uid}/{self.object_id()}"
        status, raw = self._send(
            "POST",
            f"{STORAGE}?name={urllib.parse.quote(path, safe='')}",
            data=data,
            headers={"authorization": f"Firebase {token}", "content-type": content_type},
            what="storage upload",
        )
        if status != 200:
            raise self._fail("storage upload", status, raw.decode("utf-8", "replace"))
        return path

    def download(self, url: str) -> bytes:
        """The bytes at a public URL (an album cover)."""
        status, raw = self._send("GET", url, data=None, headers={}, what="download")
        if status != 200 or not raw:
            raise self._fail("download", status, "")
        return raw
