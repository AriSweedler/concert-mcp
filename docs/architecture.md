# Architecture and design

One MCP server and CLI for the concert stack behind a small-model Telegram bot. A 9B local model behind an OpenClaw Telegram bot turns a ticket screenshot into a Google Calendar event, a Spotify teaser playlist, a setlist.fm lookup and a weekly setlist check, and renames the Telegram topic to `YYYY-MM-DD | Artist | Venue`. The same flows run from the command line, so a developer can test every step without an MCP client and the old dotfiles scripts become one-line wrappers. Credentials stay on the host in XDG directories; nothing in this repository names a person, a machine, a calendar or a chat. The rule that shapes everything: **the model supplies a few plain strings (artist, date, venue); code owns every policy, default, validation, idempotency guard, side effect and the exact reply text.**

```
phone --photo--> Telegram --> OpenClaw gateway (laptop) --stdio--> `concert-mcp serve`
                                  |  the 9B model picks one tool           |
                                  |  and passes artist, start, venue       +-- gws CLI -------------> Google Calendar
                                  |                                        +-- urllib ---------------> Spotify Web API
                                  |                                        +-- urllib ---------------> setlist.fm API
                                  <---- `openclaw message send` -----------+   the reply, delivered by code
                                  <---- Bot API editForumTopic ------------+   the topic rename
```

Three layers, each importable without the one above it:

- `text/`: pure functions. No I/O, no clock, no environment. Date parsing, title and opener split, the derived Doors/Purchased lines, the hallucination filter, playlist naming, every reply string, candidate matching, the health table.
- `adapters/`: one class per side effect, each behind a `Protocol` so tests use fakes: `GwsCalendar` (subprocess), `SpotifyClient`, `SetlistFmClient`, `GooglePlacesClient` and `NominatimClient` (urllib), `OpenClawChat` and `OpenClawContext` (subprocess, sqlite, Bot API), `JsonState`, `SystemClock`.
- `flows/`: the policy. `concert_add`, `event_delete`, `queue`, `topics`, `venues`, `teaser`, `setlist`, `playlists`, `auth`, `deliver`, `healthcheck`. A flow returns an `Outcome`; it never prints and never exits. `server.py` (MCP over stdio) and `cli.py` are thin shells over the same flows.

The output contract, which every tool and every CLI subcommand ends in:

1. A flow returns `Outcome(kind, reply, error, fix, data)`.
2. `deliver()` sends the reply itself through `openclaw message send` when a chat spoke in the last 15 minutes and the same text was not sent in the last 10; otherwise the status is `NOT SENT: <why>`.
3. Sent: the tool result is the `NO_REPLY` block, and the model's whole answer is `NO_REPLY`.
4. Not sent: the tool result is the reply text plus the `NOT SENT` line, and the model relays the text unchanged.
5. The caller's input was wrong: `FIX: <what to change>`, the only retry signal. On the MCP path it ends with `Call <tool> once more with only that change.`; the third FIX for one tool within 15 minutes becomes a question to the user instead.

The eight tools. Every parameter is a string with default `""`; a missing required value is answered in code (`I could not read the artist from that. Reply with it and I will add the event.`), never by a schema error:

| Tool | Parameters | What it does |
|---|---|---|
| `concert_add` | `artist`, `start`, `venue`, `source_text`, `notes`, `another` | the whole concert flow: venue resolved through the registry (Maps-form address, website, visit), event on the Concerts calendar (updated in place when the Telegram topic already owns one, unless `another` is `yes`), teaser playlist, setlist.fm lookup, weekly queue, topic rename, reply delivery |
| `calendar_add` | `title`, `start`, `location`, `notes`, `calendar` | a plain event on the personal calendar, title kept as the user wrote it (`[Dinner] ...`) |
| `event_delete` | `title`, `date`, `pick` | delete one event by title; several matches become a numbered list, and `pick` (or a bare number as the title) chooses from it |
| `teaser_playlist` | `artist`, `date` | the Spotify teaser alone: every liked song by the artist, oldest first, named `YYYY-MM-DD \| Artist` |
| `setlist_find` | `artist`, `date`, `venue` | the setlist.fm page for one show: exact date first, then within a day at a venue sharing a word |
| `setlist_playlist` | `url`, `artist`, `date`, `venue` | the Spotify playlist of one show's setlist, in setlist.fm order, named `YYYY-MM-DD \| Artist`: built from a pasted setlist.fm page (or the show found by artist and date), topped up on a repeat, cross-linked with the teaser playlist, linked from the calendar event |
| `queue_status` | none | the concerts waiting for the weekly setlist check |
| `healthcheck` | none | which credentials and tools are present and what fixes each missing one |

`event_delete` carries `destructiveHint`; `setlist_find`, `queue_status` and `healthcheck` carry `readOnlyHint`.

**Two playlists, one name.** The teaser and the setlist playlist of a show are both named `YYYY-MM-DD | Artist`, so the Concerts folders in Spotify read the same. The description's first word tells them apart: a setlist playlist's starts with `Setlist:` (`Setlist: Zed Fox on 2026-11-14, in setlist order, from <setlist.fm url> Teaser playlist: <url>`), a teaser's with `Teaser for`. Both flows look a playlist up by name and marker, so neither tops the other up. `setlist_playlist` searches each setlist.fm song on Spotify as `track:<name> artist:<artist>` (the original artist for a cover; tape intros are skipped), adds only the URIs the playlist lacks (setlist order kept for the new tail), writes `Setlist playlist: <url>` into the teaser's description and the event's description once, and reports the songs Spotify does not have. The owner may paste the page of an equivalent show of the same tour: in a topic the playlist takes the topic event's date, not the page's. The weekly poll reuses `sync_setlist_playlist` to top the playlist up as the page fills in.

**One event per topic.** A Telegram forum topic is the conversation about one show, so it owns one event. `<state_dir>/topics.json` maps `<chat>:<thread>` to the event last added from that topic. A `concert_add` whose live route is that topic patches the event (title, start, end, location, description) instead of inserting, keeps a `Teaser playlist:`, `Setlist:` or `Venue:` line the new text could not supply, rewrites the queue row, and replies `**Updated on Concerts**` with a `Changed:` note. `another=yes` inserts a second event and makes it the topic's current one; `event_delete` frees the topic; a DM or the General topic has no key and falls back to the title-and-minute duplicate guard.

## Design principles

**The small-model rule.** The model behind the bot is a 9B model on a small GPU. Models that size fail with many tools, drop chained calls and print tool JSON as text, so: few coarse tools (one per job, with the plan inside the tool); every parameter a string with an example in its description, never a required field in the schema (a missing one yields a validation blob the model loops on; code returns the `I could not read the ... from that` reply instead); code owns policy, defaults, validation, idempotency, side effects and the reply text. `clean_param` squashes whitespace, strips one pair of matching quotes and turns `null`, `None`, `n/a` and `undefined` into empty.

**Three result shapes.** The tool result tells the model exactly what to say: the `NO_REPLY` block when code delivered the reply itself, or the reply text followed by `NOT SENT: <why>` for the model to relay, or `FIX: <one change>`. `FIX` is the only retry signal, and `fix_guard` turns the third FIX for one tool within 15 minutes into a plain question to the user (`I could not get the details right from that after a few tries. Reply with the artist, date, show time and venue in one line and I will add it.`), so a confused model cannot loop. A crash inside a tool is a reply too: `Something broke inside the concert tool (<Type>). Admin: check the gateway log.`

**The hallucination filter.** Doors, Purchased and Opener lines are derived from the ticket text by code, never taken from the model. A caller-supplied description line is kept only when every URL, dollar amount and clock time in it appears in the source text (the ticket text passed in, the last OCR output and the user's recent messages); otherwise it is dropped and the reply says so: `Left out (not in the text I was given): ...`. A start equal to the doors time is refused with a literal FIX (`set start to "2026-11-14 8:30 PM" (the show time in the text; 7:30 PM is doors)`); a date-only start takes the show time from the text. Only a concert runs these parsers: a plain `calendar_add` event keeps its title and start as given, and the context text (which belongs to the last ticket) is used only to verify the caller's own lines.

**The duplicate guard.** Before inserting, the flow lists events at the exact start minute with the same title; a match means nothing is created, missing playlist and setlist links are patched into the existing description, and the reply reads `**Already on Concerts** (nothing new created)`. A model retry after a timeout is therefore harmless. The setlist queue, the delete candidates list and the sent log are all idempotent by key and written atomically.

**One link form.** The Google Calendar link in every reply is `https://calendar.google.com/calendar/u/0/r/eventedit/<eid>`, built from the `eid` in the API's `htmlLink`. Four link forms were tested on a phone on 2026-10-25 (the API's `htmlLink`, the `calendar.google.com/calendar/event?eid=` form, the day view and the share template): only the `eventedit` form opened the event in the Google Calendar app. There is no fallback form.

**Stdout discipline.** Stdout is the MCP transport. Nothing in `src/` calls `print()` except `cli.py` and the interactive `flows/auth.py`; a test greps for it. All logging goes to stderr.

**Configuration carries no personal data.** The operator's name, calendar names, timezone and 1Password item names are settings with neutral defaults; replies say `Admin: ...` until you set `CONCERT_MCP_OPERATOR_NAME`. The healthcheck prints a calendar's name, never its id, and reports a bot token's presence, never its value.
