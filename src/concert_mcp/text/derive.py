"""Description lines derived from the ticket text and the hallucination filter (unit text-parse)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from concert_mcp.result import FixInput
from concert_mcp.text.titles import split_opener_list, squash

PLATFORMS = (
    "Ticketweb",
    "Ticketmaster",
    "AXS",
    "Eventbrite",
    "DICE",
    "Dothebay",
    "See Tickets",
    "Etix",
    "Tixr",
    "Shotgun",
    "RA",
)
URL_RE = re.compile(r"https?://\S+", re.I)
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
CLOCK_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.I)
PLAIN_RE = re.compile(r"\b\d{1,2}:\d{2}\b")

# A clock literal as a ticket prints it: "7:30 PM", "7pm", "7.30 p.m.". The dot between the digits is OCR's colon.
TIME_LITERAL = r"(\d{1,2}(?:[:.]\d{2})?\s*[ap]\.?m\.?)"
# Defined here, not in dates.py, because derived_lines() needs it and dates.py imports from this module.
DOORS_RE = re.compile(r"doors?\s*(?:open|at|:|@)?\s*" + TIME_LITERAL, re.I)
# "2 tickets", "1 General Admission", "Qty: 2", "2 x $20.00": the count as a receipt prints it.
_COUNT_RES = (
    re.compile(r"\b(\d{1,2})\s*(?:x\s*)?tickets?\b", re.I),
    re.compile(r"\b(\d{1,2})\s*x?\s*general admission\b", re.I),
    re.compile(r"\bq(?:uantity|ty)\.?:?\s*(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*x\s*\$", re.I),
)
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{2})?)")
# The amount after a bare TOTAL label (not SUBTOTAL), on the same line or the next: what the card was charged.
_TOTAL_RE = re.compile(r"(?<![a-z])total\b[^\n$]*\n?\s*\$\s?(\d[\d,]*(?:\.\d{2})?)", re.I)
_OPENERS_LINE_RE = re.compile(r"^\s*(?:with|w/)\s+(?:special guests?\s+|support from\s+)(.+?)\s*$", re.I | re.M)
_AMPM_RE = re.compile(r"\s*([ap])\.?m\.?", re.I)
_DOTTED_RE = re.compile(r"(\d)\.(\d)")


def derived_lines(text: str) -> list[str]:
    """['Doors: 7:30 PM', 'Purchased: 2 tickets, $42.00 on Ticketweb'] built from the text itself, so the doors time
    and the purchase record never pass through a model (or a shell that eats "$42")."""
    lines: list[str] = []
    d = DOORS_RE.search(text)
    if d:
        doors = _AMPM_RE.sub(lambda m: " " + m.group(1).upper() + "M", d.group(1)).strip()
        lines.append("Doors: " + _DOTTED_RE.sub(r"\1:\2", doors))
    n = ticket_count(text)
    amt = charged_amount(text)
    plat = next((pl for pl in PLATFORMS if re.search(r"\b" + re.escape(pl) + r"\b", text, re.I)), None)
    if amt or n:
        parts: list[str] = []
        if n:
            parts.append(f"{n} ticket" + ("" if n == "1" else "s"))
        if amt:
            parts.append("$" + amt)
        lines.append("Purchased: " + ", ".join(parts) + (f" on {plat}" if plat else ""))
    return lines


def ticket_count(text: str) -> str | None:
    """The first count any receipt form states, as printed."""
    return next((m.group(1) for rx in _COUNT_RES if (m := rx.search(text))), None)


def charged_amount(text: str) -> str | None:
    """The TOTAL amount when the receipt labels one (SUBTOTAL is not it); else the largest amount, which on a receipt
    with fees is the charge and on a ticket with one price is that price."""
    m = _TOTAL_RE.search(text)
    if m:
        return m.group(1)
    amounts = _AMOUNT_RE.findall(text)
    if not amounts:
        return None
    return max(amounts, key=lambda a: float(a.replace(",", "")))


def openers_from_text(text: str) -> list[str]:
    """Openers named on their own 'with special guest X' line."""
    m = _OPENERS_LINE_RE.search(text)
    return split_opener_list(m.group(1)) if m else []


def norm_claims(s: object) -> str:
    """Whitespace removed, lowercased, trailing /.,) stripped: the comparison form for claims."""
    return re.sub(r"\s+", "", str(s)).lower().rstrip("/.,)")


def build_source(caller_source: str | None, context_text: str) -> tuple[str, str | None]:
    """(raw joined source, normalized source or None when there is no text at all)."""
    raw = "\n".join(x for x in (str(caller_source or ""), str(context_text or "")) if x)
    return raw, (norm_claims(raw) if raw.strip() else None)


def verify_line(line: str, source_norm: str | None) -> bool:
    """Keep a line only when every URL, dollar amount and clock time in it appears in the source."""
    claims = URL_RE.findall(line) + MONEY_RE.findall(line) + CLOCK_RE.findall(line) + PLAIN_RE.findall(line)
    if not claims:
        return True
    if source_norm is None:
        return False
    return all(norm_claims(c) in source_norm for c in claims)


def _key(line: str) -> str:
    return line.split(":")[0].lower()


def merge_lines(
    *,
    derived: Sequence[str],
    openers: Sequence[str],
    caller: Sequence[str],
    source_norm: str | None,
    title: str,
) -> tuple[list[str], list[str]]:
    """(kept, dropped): derived lines, Opener lines, then verified caller lines whose key is new."""
    kept = list(derived) + [f"Opener: {o}" for o in openers if o.lower() != title.lower()]
    dropped: list[str] = []
    for raw in caller:
        ln = squash(raw)
        # The derived Doors/Purchased lines win over the caller's version of the same key.
        if not ln or any(_key(ln) == _key(k) for k in kept):
            continue
        (kept if verify_line(ln, source_norm) else dropped).append(ln)
    return kept, dropped


def coerce_description(desc: object) -> list[str]:
    """None -> []; str -> lines (literal \\n honoured); list -> strings; FixInput otherwise."""
    if not desc:
        return []
    if isinstance(desc, str):
        return desc.replace("\\n", "\n").split("\n")
    if isinstance(desc, list):
        return [str(x) for x in desc]
    raise FixInput("description must be a list of lines", 'use ["Doors: 7:00 PM", "Purchased: 1 ticket, $45"]')


def check_attendees(att: object) -> list[str]:
    """None -> []; a list of email addresses passes; FixInput otherwise."""
    if not att:
        return []
    if isinstance(att, list) and all("@" in str(a) for a in att):
        return [str(a) for a in att]
    raise FixInput("attendees must be a list of email addresses", 'use ["name@example.com"]')
