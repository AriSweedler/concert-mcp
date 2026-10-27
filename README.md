# concert-mcp

Turns a concert ticket into a Google Calendar event, a Spotify teaser playlist and a setlist.fm link, from a Telegram bot or the command line. It is one Model Context Protocol (MCP) server built for a small local model. The model passes the artist, the start time and the venue; code does the rest.

## Getting started

concert-mcp needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/AriSweedler/concert-mcp.git && cd concert-mcp
uv sync
uv run concert-mcp healthcheck
```

The healthcheck names each missing credential and the section below that provides it. Only Google Calendar is required for the command line. This dry run prints the reply and writes nothing:

```sh
uv run concert-mcp add --concert --dry-run --no-send --title 'ZZZ Test Band' --start '2026-11-20 20:00' --location 'ZZZ Hall'
```

## Credentials

### Google Calendar

1. Install [gws](https://github.com/googleworkspace/cli), the Google Workspace command line tool, and complete its setup. It needs a Google Cloud OAuth client; its README shows how.
2. Set `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` before every gws command on this machine. A login without the variable deletes the stored token.

   ```sh
   echo 'export GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file' >> ~/.zprofile   # or your shell's profile
   export GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file
   gws auth login
   ```

3. Create a calendar named `Concerts` in Google Calendar.

Then run the `add` command above without `--dry-run --no-send`. Start times are read in `CONCERT_MCP_TZ`, default `America/Los_Angeles`. [Details](docs/credentials.md#google-calendar).

### Spotify

Spotify makes the teaser playlist from your liked songs by the artist.

1. Create an app at <https://developer.spotify.com/dashboard> with the redirect URI `http://127.0.0.1:8888/callback`.
2. Add your account under the app's User Management.
3. Run the login and paste the app's Client ID and Client Secret:

   ```sh
   uv run concert-mcp spotify auth --manual
   ```

[Details](docs/credentials.md#spotify).

### setlist.fm

setlist.fm provides the setlist link. Get a free key at <https://www.setlist.fm/settings/api>. Store it:

```sh
uv run concert-mcp spotify setlist-auth --manual
```

[Details](docs/credentials.md#setlistfm).

### OpenClaw

[OpenClaw](https://docs.openclaw.ai) is the Telegram gateway that calls this server from chat. On the OpenClaw host, register the server. Then allow its three tools:

```sh
openclaw mcp set concert '{"command":"uv","args":["run","--project","/path/to/concert-mcp","concert-mcp","serve"],"env":{"CONCERT_MCP_OPERATOR_NAME":"${CONCERT_MCP_OPERATOR_NAME}","CONCERT_MCP_GWS_BIN":"${CONCERT_MCP_GWS_BIN}","CONCERT_MCP_OPENCLAW_BIN":"${CONCERT_MCP_OPENCLAW_BIN}"},"toolFilter":{"include":["concert_add","calendar_add","event_delete"]},"requestTimeoutMs":480000,"enabled":true}'
openclaw config set tools.alsoAllow '["exec","read","concert__concert_add","concert__calendar_add","concert__event_delete"]'
openclaw mcp reload
openclaw mcp probe concert
```

An unset `${...}` variable falls back to its default. Without the `alsoAllow` line, OpenClaw's `minimal` tool profile hides the tools. Then send the bot a ticket screenshot: the reply lands in the chat that asked. [Details](docs/credentials.md#openclaw).

## Configuration

Settings are `CONCERT_MCP_*` environment variables, or keys in `~/.config/concert-mcp/config.toml`. `uv run concert-mcp config` prints them.

## Docs

| Document | What it holds |
|---|---|
| [Architecture and design](docs/architecture.md) | the nine tools, what the model sees, the small-model rule |
| [Credentials](docs/credentials.md) | each provider in full, and the OpenClaw entry explained |
| [Configuration](docs/configuration.md) | every setting, and what the healthcheck prints |
| [CLI](docs/cli.md) | every subcommand, flag and exit code |
| [Migration](docs/migration.md), [homelab](docs/homelab-integration.md), [design spec](docs/DESIGN.md), [contributing](CONTRIBUTING.md) | the author's earlier scripts and deployment, the build spec, how to contribute |

## License

MIT. See [LICENSE](LICENSE).
