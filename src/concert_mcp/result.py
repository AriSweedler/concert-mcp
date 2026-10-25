"""The outcome of a flow and the three error kinds. Flows return Outcome; only flows/deliver.py turns it into text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    OK = "ok"
    DUPLICATE = "duplicate"
    DRY_RUN = "dry_run"
    NEED_USER = "need_user"
    FIX = "fix"
    ERROR = "error"


@dataclass(frozen=True)
class Outcome:
    kind: Kind
    reply: str | None = None
    error: str | None = None
    fix: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.kind in (Kind.OK, Kind.DUPLICATE, Kind.DRY_RUN)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_json(self) -> dict[str, Any]:
        """The legacy record: ok, [duplicate], [dryRun], [retry, fix], [error], [reply], then data."""
        rec: dict[str, Any] = {"ok": self.ok}
        if self.kind is Kind.DUPLICATE:
            rec["duplicate"] = True
        if self.kind is Kind.DRY_RUN:
            rec["dryRun"] = True
        if self.kind is Kind.FIX:
            rec["retry"] = True
            rec["fix"] = self.fix
        if self.error is not None:
            rec["error"] = self.error
        if self.reply is not None:
            rec["reply"] = self.reply
        rec.update(self.data)
        return rec


@dataclass(frozen=True)
class Delivery:
    sent: bool
    status: str  # "SENT to telegram <chat> topic <n>" | "NOT SENT: <why>"


class FixInput(Exception):
    """The caller's input is wrong; the fix text tells it what to change. Never delivered to the chat."""

    def __init__(self, error: str, fix: str) -> None:
        super().__init__(error)
        self.error = error
        self.fix = fix


class NeedUser(Exception):
    """A human must supply something; the reply is delivered to the chat."""

    def __init__(self, error: str, reply: str) -> None:
        super().__init__(error)
        self.error = error
        self.reply = reply


class ProviderError(Exception):
    """gws, Spotify, setlist.fm or openclaw failed; the reply carries an operator hint and is delivered."""

    def __init__(self, provider: str, error: str, reply: str) -> None:
        super().__init__(error)
        self.provider = provider
        self.error = error
        self.reply = reply


NEED_USER = "I could not read the {what} from that. Reply with it and I will add the event."
NO_REPLY_TEXT = (
    "NO_REPLY\n"
    "The chat already received the full answer. Do not describe it or add anything. "
    "Your entire reply must be exactly: NO_REPLY"
)


def need(what: str) -> NeedUser:
    """The legacy need(): a required field is missing and the user is asked for it."""
    return NeedUser(f"{what} is required", NEED_USER.format(what=what))


def outcome_from(exc: FixInput | NeedUser | ProviderError) -> Outcome:
    """Map one of the three error kinds to its Outcome."""
    if isinstance(exc, FixInput):
        return Outcome(Kind.FIX, error=exc.error, fix=exc.fix)
    if isinstance(exc, NeedUser):
        return Outcome(Kind.NEED_USER, reply=exc.reply, error=exc.error)
    if isinstance(exc, ProviderError):
        return Outcome(Kind.ERROR, reply=exc.reply, error=exc.error, data={"provider": exc.provider})
    raise TypeError(f"outcome_from() takes FixInput, NeedUser or ProviderError, not {type(exc).__name__}")


def crash_outcome(exc: BaseException, operator: str) -> Outcome:
    """Any other exception: a short reply the user can relay, the repr for the log."""
    return Outcome(
        Kind.ERROR,
        reply=f"Something broke inside the concert tool ({type(exc).__name__}). {operator}: check the gateway log.",
        error=repr(exc)[:300],
    )
