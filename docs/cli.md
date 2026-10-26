# CLI

The same flows without an MCP client, for developers and for the dotfiles wrappers:

```
concert-mcp [--json] [--dry-run] [--no-send] [--config-dir PATH] [--log-level LEVEL] <subcommand>
```

| Subcommand | Example |
|---|---|
| `serve` | `concert-mcp serve` (the MCP server over stdio; also the default with no subcommand) |
| `add` | `concert-mcp add --concert --title 'Zed Fox with special guest Zork.' --start '2026-11-14 20:30' --location 'The Quarry, San Francisco' --source "$(cat ticket.txt)"` |
| `add` (plain) | `concert-mcp add --title '[Dinner] Team dinner' --start '2026-11-03 19:30' --location 'ZZZ Bistro, San Francisco'` |
| `del` (alias `delete`) | `concert-mcp del --title 'Zed Fox' --date 2026-11-14`, then `concert-mcp del --pick 2` after a numbered list (the list is good for 15 minutes, `CONCERT_MCP_CONTEXT_WINDOW_S`) |
| `teaser` | `concert-mcp teaser --artist 'Zed Fox' --date 2026-11-14 [--description TEXT]` |
| `setlist-find` | `concert-mcp setlist-find --artist 'Zed Fox' --date 2026-11-14 --venue 'The Quarry' --songs` (`--songs` adds the song list to the `--json` record) |
| `queue` | `concert-mcp queue` |
| `healthcheck` | `concert-mcp healthcheck --probe`, `concert-mcp healthcheck --only gws-keyfile --only spotify-credentials` |
| `config` | `concert-mcp config` |
| `migrate` | `concert-mcp migrate --dry-run`, then `concert-mcp migrate` |
| `spotify auth` | `concert-mcp spotify auth [--force] [--manual]` |
| `spotify me` | `concert-mcp spotify me` |
| `spotify liked` | `concert-mcp spotify liked --artist 'Zed Fox' [--refresh]` |
| `spotify playlists` | `concert-mcp spotify playlists` |
| `spotify playlist-tracks` | `concert-mcp spotify playlist-tracks --playlist <id>` |
| `spotify playlist-create` | `concert-mcp spotify playlist-create --name '2026-11-14 \| Zed Fox' [--description TEXT] [--public]` |
| `spotify playlist-add` | `concert-mcp spotify playlist-add --playlist <id> --uri spotify:track:<id> --uri spotify:track:<id>` |
| `spotify rename` | `concert-mcp spotify rename --playlist <id> --name '2026-11-14 \| Zed Fox'` |
| `spotify playlist-unfollow` | `concert-mcp spotify playlist-unfollow --playlist <id>` |
| `spotify search-artist` | `concert-mcp spotify search-artist --name 'Zed Fox'` |
| `spotify setlist-auth` | `concert-mcp spotify setlist-auth [--force] [--manual]` |

`add` and `del` also take one JSON object as the positional argument or on stdin, with the legacy aliases folded (`artist` and `summary` for `title`, `venue` for `location`, `notes` for `description`, `datetime` for `start`, `text` for `source`; `del` also takes `event`, `name`, `when`, `day`, `cal`, `eventId`, `number`, `choice`); an unknown key or flag is a `FIX:` line, never a traceback. `add` takes `--concert` (the Concerts calendar, teaser, setlist lookup, queue, rename) and `--no-teaser`. The `spotify` subcommands print one JSON record in the legacy shape.

Flags:

- `--json`: print the legacy record (`ok`, `duplicate`, `dryRun`, `retry` + `fix`, `error`, `reply`, the event fields and `sent`) instead of the reply text.
- `--dry-run`: parse, derive, resolve and render, but insert nothing, create no playlist, queue nothing and send nothing. The reply starts `**Would add to ...**`, and the status is `NOT SENT: dry run`.
- `--no-send`: do everything except deliver the reply and rename the topic; prints the reply plus `NOT SENT: delivery disabled`.
- `--config-dir PATH`, `--log-level LEVEL`: the two settings most often changed per run (`debug` logs every subprocess argv with any secret argument shown as `<redacted>`, never a token).

Exit codes: `0` for ok, including a duplicate and a dry run; `1` for a `FIX:` line, a reply that asks the user for something, or a provider failure (`130` on Ctrl-C); `healthcheck` exits with the number of FAIL rows. Logging goes to stderr only; stdout is exactly one block.

The dotfiles skills that used to carry `cal-add.py`, `cal-del.py` and `spotify.zsh` become one-line wrappers:

```sh
exec concert-mcp add --concert "$@"    # cal-add.py
exec concert-mcp del "$@"              # cal-del.py
exec concert-mcp spotify "$@"          # spotify.zsh (teaser and setlist-find are top-level: concert-mcp teaser, concert-mcp setlist-find)
```
