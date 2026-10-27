# Configuration

Precedence: a `CONCERT_MCP_<FIELD>` environment variable wins over the same key, lowercase, in `<config_dir>/config.toml`, which wins over the default. The CLI flags `--config-dir PATH` and `--log-level LEVEL` set `CONCERT_MCP_CONFIG_DIR` and `CONCERT_MCP_LOG_LEVEL` for that run. The three directories honour `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME`. No default carries personal data: the operator name, calendar names, timezone and 1Password item names are defaults you override. This table is `config.env_table()`; `tests/test_readme.py` checks that every variable is here.

| Variable | Default | Meaning |
|---|---|---|
| `CONCERT_MCP_CONFIG_DIR` | `~/.config/concert-mcp` | credentials files and config.toml |
| `CONCERT_MCP_STATE_DIR` | `~/.local/state/concert-mcp` | setlist-queue.json, setlist-poll.stamp, delete-candidates.json, last-sent.json, fix-log.json, topics.json |
| `CONCERT_MCP_CACHE_DIR` | `~/.cache/concert-mcp` | liked.json |
| `CONCERT_MCP_SPOTIFY_CREDENTIALS` | `<config_dir>/spotify.json` | legacy fallback ~/.config/ari-spotify/credentials.json |
| `CONCERT_MCP_SETLISTFM_CREDENTIALS` | `<config_dir>/setlistfm.json` | legacy fallback ~/.config/ari-spotify/setlistfm.json |
| `CONCERT_MCP_LIKED_CACHE` | `<cache_dir>/liked.json` | legacy fallback ~/.cache/ari-spotify/liked.json |
| `CONCERT_MCP_SETLIST_QUEUE` | `<state_dir>/setlist-queue.json` | legacy fallback ~/.local/state/ari-spotify/setlist-queue.json; the weekly poller reads this file |
| `CONCERT_MCP_CANDIDATES_FILE` | `<state_dir>/delete-candidates.json` | no legacy fallback; a list older than `CONCERT_MCP_CONTEXT_WINDOW_S` is discarded |
| `CONCERT_MCP_SENT_LOG` | `<state_dir>/last-sent.json` | reply dedupe memory |
| `CONCERT_MCP_FIX_LOG` | `<state_dir>/fix-log.json` | the retry-loop breaker's memory |
| `CONCERT_MCP_TOPICS_FILE` | `<state_dir>/topics.json` | which event each Telegram forum topic owns; a repeat `concert_add` in a topic updates that event |
| `CONCERT_MCP_GWS_BIN` | `which gws, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else gws` | the Google Workspace CLI |
| `CONCERT_MCP_GWS_KEY_FILE` | `~/.config/gws/.encryption_key` | exists -> GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file on every gws call |
| `CONCERT_MCP_GWS_TIMEOUT_S` | `60` | wall-clock cap per gws call |
| `CONCERT_MCP_OPENCLAW_BIN` | `which openclaw, else /opt/homebrew/bin, /usr/local/bin, ~/.local/bin, else openclaw` | the OpenClaw CLI |
| `CONCERT_MCP_OPENCLAW_HOME` | `~/.openclaw` | base for openclaw_config, session_db and ocr_last |
| `CONCERT_MCP_OPENCLAW_CONFIG` | `<openclaw_home>/openclaw.json` | read for channels.telegram.botToken only |
| `CONCERT_MCP_SESSION_DB` | `<openclaw_home>/agents/main/agent/openclaw-agent.sqlite` | opened read-only |
| `CONCERT_MCP_OCR_LAST` | `<openclaw_home>/media/ocr-last.txt` | the last OCR output |
| `CONCERT_MCP_TZ` | `America/Los_Angeles` | IANA zone for every start/end |
| `CONCERT_MCP_CONCERTS_CALENDAR` | `Concerts` | calendar name, resolved through gws calendar calendarList list |
| `CONCERT_MCP_CONCERTS_CHAT` | (empty) | Telegram chat id the weekly setlist poll announces a new playlist to when the show's topic is not in `topics.json` |
| `CONCERT_MCP_DEFAULT_CALENDAR` | `primary` | id used for plain events and the second delete search |
| `CONCERT_MCP_DEFAULT_CALENDAR_NAME` | `Personal` | how replies name it |
| `CONCERT_MCP_CONTEXT_WINDOW_S` | `900` | live chat / OCR / transcript recency |
| `CONCERT_MCP_DEDUPE_WINDOW_S` | `600` | an identical reply is not sent twice within this window |
| `CONCERT_MCP_TEASER_TIMEOUT_S` | `150` | cap on the Spotify teaser step |
| `CONCERT_MCP_HTTP_TIMEOUT_S` | `30` | per HTTP request |
| `CONCERT_MCP_LIKED_CACHE_TTL_S` | `21600` | Liked Songs cache lifetime |
| `CONCERT_MCP_SETLIST_QUEUE_DAYS` | `61` | how long after the show the weekly setlist check keeps looking |
| `CONCERT_MCP_DEFAULT_DURATION_H` | `2` | end = start + this when no end is given |
| `CONCERT_MCP_PAST_GRACE_H` | `12` | a start this far in the past is still accepted |
| `CONCERT_MCP_MAX_FUTURE_DAYS` | `730` | a start further out is refused |
| `CONCERT_MCP_SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | the Spotify app's redirect URI |
| `CONCERT_MCP_SPOTIFY_SCOPES` | `user-library-read playlist-read-private playlist-modify-private playlist-modify-public` | OAuth scopes |
| `CONCERT_MCP_OP_VAULT` | `Private` | 1Password vault, auth commands only |
| `CONCERT_MCP_OP_SPOTIFY_ITEM` | `Spotify - concert-mcp` | fields username (client id), credential (secret), refresh token |
| `CONCERT_MCP_OP_SETLISTFM_ITEM` | `setlist.fm - API key` | field credential |
| `CONCERT_MCP_VENUES_FILE` | `<config_dir>/venues.json` | the venue registry: address lines, website, dates visited |
| `CONCERT_MCP_PLACES_CREDENTIALS` | `<config_dir>/places.json` | optional Google Places key; without it Nominatim answers |
| `CONCERT_MCP_VENUE_LOOKUP` | `1` | 0 skips every network venue lookup; the ticket's address block is still used |
| `CONCERT_MCP_OP_PLACES_ITEM` | `Google Places - API key` | field credential, `venues auth` only |
| `CONCERT_MCP_GEMINI_CREDENTIALS` | `<config_dir>/gemini.json` | optional Gemini key for the lineup step (openers, set times); without it the step is skipped |
| `CONCERT_MCP_GEMINI_TIMEOUT_S` | `60` | cap on the one Gemini call per concert |
| `CONCERT_MCP_GEMINI_MODEL` | `gemini-3.5-flash-lite` | the Gemini model behind the lineup lookups; the free tier answers it |
| `CONCERT_MCP_GEMINI_FALLBACKS` | `gemini-3.5-flash,gemini-3.8-flash` | tried in turn when the model answers 429 or 503 |
| `CONCERT_MCP_GEMINI_GROUNDING` | `url` | `url`: the model reads the venue's pages (`url_context`, free); `search`: adds Google Search grounding, which needs a billed key |
| `CONCERT_MCP_OP_GEMINI_ITEM` | `Gemini - API key` | field credential, `gemini auth` only |
| `CONCERT_MCP_OPERATOR_NAME` | `Admin` | the name in 'Admin: check `gws auth` on the host.' |
| `CONCERT_MCP_SEND` | `1` | 0 disables reply delivery and topic renames everywhere |
| `CONCERT_MCP_RENAME_TOPICS` | `1` | 0 keeps Telegram topic names as they are |
| `CONCERT_MCP_DRY_RUN` | `0` | 1 makes every tool change nothing and return the reply it would have sent, marked `NOT SENT: dry run`; for rehearsing the bot end to end |
| `CONCERT_MCP_LOG_LEVEL` | `info` | debug logs every subprocess argv (never tokens); stderr only |

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`. A `config.toml` sets the same fields without the prefix:

```toml
# ~/.config/concert-mcp/config.toml
operator_name = "your-name"
tz = "America/New_York"
concerts_calendar = "Shows"
default_calendar_name = "Home"
```

`concert-mcp config` prints the effective table, redacted: one row per field as `field  status  value`, where a path's status is `EXISTS`, `MISSING` or `LEGACY` (the ari-spotify file is in use because the new one is absent) and every other field shows `-`. No file's contents are ever read or printed:

```
config_dir               EXISTS   /Users/you/.config/concert-mcp
spotify_credentials      MISSING  /Users/you/.config/concert-mcp/spotify.json
gws_key_file             MISSING  /Users/you/.config/gws/.encryption_key
tz                       -        America/Los_Angeles
operator_name            -        Admin
send                     -        True
```

## Healthcheck

The healthcheck prints one row per check, a `->` line per failure naming the section of this README that fixes it, and exits with the number of FAIL rows. It is read-only: it creates no directory and writes no file. This is what it printed in an empty home directory on a Mac that has `gws` installed and nothing else configured (paths shortened):

```
PASS  python           3.12.12
PASS  mcp-sdk          mcp 2.3.0
PASS  config-dir       /Users/you/.config/concert-mcp absent (created on first write)
PASS  state-dir        /Users/you/.local/state/concert-mcp absent (created on first write)
PASS  gws-binary       /opt/homebrew/bin/gws
FAIL  gws-keyfile      missing
SKIP  gws-auth         pass --probe
SKIP  concerts-calendar pass --probe
FAIL  spotify-credentials missing
SKIP  spotify-auth     pass --probe
SKIP  liked-cache      absent: the first teaser will be slow (~40 s)
FAIL  setlistfm-key    missing
SKIP  setlistfm-auth   pass --probe
SKIP  places-key       optional: concert-mcp venues auth stores a Google Places key
SKIP  gemini-key       optional: concert-mcp gemini auth stores a Gemini key (openers, set times)
FAIL  openclaw-binary  openclaw not found
SKIP  openclaw-config  absent
SKIP  openclaw-session-db absent or unreadable: replies are not delivered
PASS  setlist-queue    0 shows queued
  ->  gws-keyfile      create the file keyring key: GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file gws auth login (README.md#google-calendar)
  ->  spotify-credentials concert-mcp spotify auth (README.md#spotify)
  ->  setlistfm-key    concert-mcp spotify setlist-auth (README.md#setlistfm)
  ->  openclaw-binary  install openclaw, or set CONCERT_MCP_OPENCLAW_BIN to its path (README.md#openclaw)
4 failing
```

`--probe` makes one real call per provider (list calendars, resolve the Concerts calendar, Spotify `/me`, one setlist.fm search) in place of the `pass --probe` rows. With `CONCERT_MCP_SEND=0` the three `openclaw-*` rows read `SKIP ... delivery off (CONCERT_MCP_SEND=0)`, which is how a development machine without OpenClaw gets to `3 failing`. When everything passes the last line is `all good`. `--json` prints the same report as one JSON object.
