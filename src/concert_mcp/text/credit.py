"""The line that says what made a thing (unit text-render): the last line of everything concert-mcp creates.

Two forms, the owner's (2026-11-16, 2026-11-17). Where the page shows plain text and turns an address into a link
itself (the Partiful description, the invite task's notes), the line spells the address. Everywhere else it is the
short form: the calendar event, where `to_html` makes it the text of a link to `PROJECT_URL`, and the Spotify
description, which never becomes a link, so an address there would add nothing.
"""

from __future__ import annotations

PROJECT_URL = "https://github.com/AriSweedler/concert-mcp"
CREATED_BY = "Created by github.com/AriSweedler/concert-mcp"
CREATED_BY_SHORT = "Created by AriSweedler/concert-mcp"
