"""Pure title handling: squash, opener split, matching, small-model parameter cleaning (unit text-parse)."""

from __future__ import annotations

import re
from dataclasses import dataclass

OPENER_SPLIT_RE = re.compile(
    r"^(.+?)\s+(?:with|w/|\+|featuring|feat\.?)\s+(?:special guests?\s+|support from\s+)?(.+)$", re.I
)
_OPENER_LIST_RE = re.compile(r",|\s+and\s+|\s*&\s*|\s*/\s*")
_PICK_RE = re.compile(r"\s*\d{1,2}\.?\s*")
_CONCERT_PREFIX_RE = re.compile(r"^\[concert\]\s*", re.I)
# What a small model sends when it means "nothing".
_NOTHING = frozenset({"null", "none", "n/a", "undefined"})


@dataclass(frozen=True)
class SplitTitle:
    title: str
    openers: tuple[str, ...]


def squash(s: object) -> str:
    """Collapse whitespace and strip; None -> ''."""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def clean_param(v: object) -> str:
    """squash, strip one pair of matching quotes, and fold None/null/none/n/a/undefined to ''."""
    if v is None:
        return ""
    s = squash(v)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return "" if s.lower() in _NOTHING else s


def is_yes(v: object) -> bool:
    """A flag the model typed: yes, y, true, 1, on (any case); everything else is no."""
    return clean_param(v).lower() in {"yes", "y", "true", "1", "on"}


def looks_like_pick(s: str) -> bool:
    """True for a bare one- or two-digit number, optionally followed by a dot."""
    return _PICK_RE.fullmatch(s) is not None


def normalize_name(s: object) -> str:
    """lower, '&' -> 'and', whitespace collapsed."""
    return " ".join(str(s or "").lower().replace("&", "and").split())


def split_opener_list(s: str) -> list[str]:
    """Split 'A, B and C & D / E' into names."""
    return [o.strip() for o in _OPENER_LIST_RE.split(s) if o.strip()]


def split_openers(title: str) -> SplitTitle:
    """'Artist with special guest X' -> (Artist, (X,)); the headliner must be at least 2 chars."""
    m = OPENER_SPLIT_RE.match(title)
    if m and len(m.group(1)) >= 2:
        return SplitTitle(m.group(1).strip(), tuple(split_opener_list(m.group(2))))
    return SplitTitle(title, ())


def strip_concert_prefix(title: str) -> str:
    """Remove a leading '[Concert]'."""
    return _CONCERT_PREFIX_RE.sub("", title)


def title_matches(wanted: str, have: str) -> bool:
    """Normalized: wanted in have, or every word of wanted longer than one char in have; empty wanted matches."""
    w, h = normalize_name(wanted), normalize_name(have)
    if not w:
        return True
    return w in h or all(word in h for word in w.split() if len(word) > 1)
