# Migration from ari-spotify

The `ari-spotify` dotfiles skill and the `cal-add.py` / `cal-del.py` scripts kept their files under `ari-spotify` and `~/.openclaw/media`. This package keeps its own under `concert-mcp`, XDG-aware. Nothing breaks on the day you switch: the four files below are read from their legacy path whenever the new path is absent.

## The four files

| File | Legacy path | New path (default) | Setting |
|---|---|---|---|
| Spotify credentials | `~/.config/ari-spotify/credentials.json` | `~/.config/concert-mcp/spotify.json` | `CONCERT_MCP_SPOTIFY_CREDENTIALS` |
| setlist.fm key | `~/.config/ari-spotify/setlistfm.json` | `~/.config/concert-mcp/setlistfm.json` | `CONCERT_MCP_SETLISTFM_CREDENTIALS` |
| Liked Songs cache | `~/.cache/ari-spotify/liked.json` | `~/.cache/concert-mcp/liked.json` | `CONCERT_MCP_LIKED_CACHE` |
| Setlist queue | `~/.local/state/ari-spotify/setlist-queue.json` | `~/.local/state/concert-mcp/setlist-queue.json` | `CONCERT_MCP_SETLIST_QUEUE` |

`~/.config`, `~/.cache` and `~/.local/state` follow `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` on both sides.

Two short-lived state files moved without a fallback and need no migration: the delete candidates list (`~/.openclaw/media/cal-del-candidates.json` → `~/.local/state/concert-mcp/delete-candidates.json`) and the reply dedupe log (`~/.openclaw/media/cal-add-last-sent.json` → `~/.local/state/concert-mcp/last-sent.json`). Both expire within minutes.

## The fallback rule

For each of the four files, when the setting is not set explicitly (neither the environment variable nor the `config.toml` key):

1. If the new path exists, it is used.
2. Else, if the legacy path exists, the legacy path is used; `concert-mcp config` marks the field `LEGACY`, and the healthcheck's `spotify-credentials` or `setlistfm-key` row passes with the evidence `present, mode 600, LEGACY path: run concert-mcp migrate`.
3. Else the new path is used, and the first write (a Spotify token refresh, a liked-songs fetch, a queued concert) creates it there.

Rule 3 is the catch: once the new file exists, the legacy one is never read again, so a process that still reads the legacy path (see the queue caveat) silently falls behind.

## `concert-mcp migrate`

```sh
uv run concert-mcp migrate --dry-run   # what would be copied
uv run concert-mcp migrate
```

For each of the four files: copy the legacy file to the new path when the new one is absent; `chmod 600` the two credential files; print one line per file as `<status>  <legacy path> -> <new path>` with the status `copied`, `skipped: exists`, `skipped: no legacy`, or `would copy` on a dry run (`--json` prints the same rows as JSON). It never deletes or moves the legacy file, so the old scripts keep working until you remove them yourself. Run it before the first real command, so the first write lands in a file that already holds the legacy contents.

## The setlist queue caveat

The weekly setlist poller reads the queue file. After migration, `concert-mcp` appends new concerts to the new path, and a poller still reading `~/.local/state/ari-spotify/setlist-queue.json` never sees them. Do one of these at the same time as the migration:

- repoint the poller at the new path (the value of `CONCERT_MCP_SETLIST_QUEUE`, which `concert-mcp config` prints), or
- set `CONCERT_MCP_SETLIST_QUEUE=~/.local/state/ari-spotify/setlist-queue.json` on the host, so both sides keep using the legacy file.

The file's shape is unchanged: a JSON list of `{event_id, calendar_id, artist, date, venue, setlistfm_url, song_count, until, added}`, indented, with a trailing newline. `concert-mcp queue` lists it; `concert-mcp del` removes a deleted event's row.

## What the dotfiles wrappers become

The three scripts in the dotfiles skills become one-line wrappers that keep their flag forms and JSON-on-stdin forms, because `concert-mcp add` and `concert-mcp del` parse the same `--key value` flags, the same aliases and the same one-JSON-object input:

| Legacy entry point | Wrapper |
|---|---|
| `bin/cal-add.py [--concert] ...` | `exec concert-mcp add --concert "$@"` (drop `--concert` for the plain-event wrapper) |
| `bin/cal-del.py ...` | `exec concert-mcp del "$@"` |
| `bin/spotify.zsh <command> ...` | `exec concert-mcp spotify "$@"` |

Two of `spotify.zsh`'s commands are top-level subcommands in the new CLI rather than under `spotify`: `spotify.zsh teaser` → `concert-mcp teaser`, `spotify.zsh setlist-find` → `concert-mcp setlist-find`. The `--json` record of `add` and `del` keeps the legacy keys (`ok`, `duplicate`, `dryRun`, `retry`, `fix`, `error`, `reply`, `id`, `htmlLink`, `calendar`, `summary`, `start`, `end`, `location`, `description`, `dropped`, `teaser`, `setlist`, `topic`, `topic_status`, `candidates`, `deleted`) plus `sent`.

Set `CONCERT_MCP_OPERATOR_NAME` on the host before switching the wrappers; until then every operator-facing reply says `Admin:` where the scripts said a name.
