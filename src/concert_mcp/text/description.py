"""The event description's two forms and the only conversions between them (unit text-description).

Pure. Every flow reads and writes a `PlainDescription`: lines joined by a newline, a fact per line, `Label: value`.
Google Calendar stores and renders an `HtmlDescription`: the same lines joined by `<br>`, where a line that is
`Label: <one URL>` is the anchor `<a href="URL">Label</a>`, so the calendar shows "Teaser playlist" as a link and
not the address. The two are distinct types on purpose: a description crosses from one to the other only through
`to_html` (on its way to Google) and `to_plain` (on its way back), and the calendar adapter is the one caller.

`to_plain` reads whatever Google holds: this module's HTML, the plain text of an event written before it existed,
and HTML a person typed into the calendar. `to_plain(to_html(d)) == d` for every description the flows write.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from html import escape
from html.parser import HTMLParser
from typing import Any, NewType

from concert_mcp.text.credit import CREATED_BY, PROJECT_URL

PlainDescription = NewType("PlainDescription", str)
HtmlDescription = NewType("HtmlDescription", str)

# `Label: https://one.url` and nothing else on the line: the label becomes the link text.
_LINK_LINE_RE = re.compile(r"^(?P<label>[^:\n]+(?::[^ \n][^:\n]*)*): (?P<url>https?://\S+)$")
# The credit is the stored form's alone: `to_html` adds it and `to_plain` drops it (linked, or as a person left it
# in plain text), so a flow never reads it as a fact, never orders it among the links and never writes it twice.
CREDIT_LINE = f"{CREATED_BY}: {PROJECT_URL}"
_CREDIT_FORMS = frozenset({CREDIT_LINE, CREATED_BY})
_BREAKS = frozenset({"br"})
_BLOCKS = frozenset({"p", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"})


def from_lines(lines: Iterable[str]) -> PlainDescription:
    """The plain description of these lines."""
    return PlainDescription("\n".join(lines))


def lines_of(plain: PlainDescription | None) -> list[str]:
    """The plain description's lines; none for an empty one."""
    return (plain or "").splitlines()


def description_of(event: Mapping[str, Any]) -> PlainDescription:
    """An event's description as the calendar adapter returned it: plain, never Google's HTML."""
    return PlainDescription(str(event.get("description") or ""))


def is_link_line(line: str) -> bool:
    """Whether the line is `Label: <one URL>`: the line the calendar shows as a link."""
    return _LINK_LINE_RE.match(line) is not None


def _line_html(line: str) -> str:
    m = _LINK_LINE_RE.match(line)
    if m is None:
        return escape(line, quote=False)
    return f'<a href="{escape(m.group("url"), quote=True)}">{escape(m.group("label"), quote=False)}</a>'


def to_html(plain: PlainDescription) -> HtmlDescription:
    """The form Google Calendar stores: link lines as anchors, the rest escaped, lines joined by <br>, and last,
    after a blank line, the credit as a link to the project."""
    lines = [*plain.split("\n"), "", CREDIT_LINE] if plain else [CREDIT_LINE]
    return HtmlDescription("<br>".join(_line_html(ln) for ln in lines))


class _Lines(HTMLParser):
    """Collects the text lines of an HTML (or plain) description; an anchor is kept as (text, href)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[list[str | tuple[str, str]]] = [[]]
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def _newline(self) -> None:
        self.lines.append([])

    def _block(self) -> None:
        if self.lines[-1]:
            self._newline()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAKS:
            self._newline()
        elif tag in _BLOCKS:
            self._block()
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self._block()
        elif tag == "a" and self._href is not None:
            self.lines[-1].append(("".join(self._anchor_text), self._href))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._anchor_text.append(data)
            return
        # A newline in the stored text is a line break, as Google renders it and as every plain event has it.
        first, *rest = data.replace("\xa0", " ").split("\n")
        if first:
            self.lines[-1].append(first)
        for part in rest:
            self._newline()
            if part:
                self.lines[-1].append(part)


def _line_plain(parts: list[str | tuple[str, str]]) -> str:
    solid = [p for p in parts if not (isinstance(p, str) and not p.strip())]
    if len(solid) == 1 and isinstance(solid[0], tuple):
        text, href = (x.strip() for x in solid[0])
        return href if text in ("", href) else f"{text}: {href}"
    out: list[str] = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        else:
            text, href = (x.strip() for x in p)
            out.append(href if text in ("", href) else f"{text} ({href})")
    return "".join(out).rstrip()


def to_plain(stored: HtmlDescription | None) -> PlainDescription:
    """The lines of what Google holds, whichever way it was written; an anchor alone on a line is `text: href`."""
    if not stored:
        return PlainDescription("")
    parser = _Lines()
    parser.feed(stored)
    parser.close()
    lines = [ln for ln in (_line_plain(parts) for parts in parser.lines) if ln not in _CREDIT_FORMS]
    while lines and not lines[-1]:
        lines.pop()
    return from_lines(lines)
