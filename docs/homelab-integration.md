# Homelab integration

**Prepare, do not apply.** These are patch-ready snippets for the homelab repository, where this package will live as the submodule `apps/concert-mcp`. Nothing here is applied by this repository; copy each snippet into the homelab once the submodule exists.

## 1. `bin/doctor.sh`: a runtime-tier check

The homelab doctor prints `PASS/FAIL/SKIP  name  evidence` per check (`%-16s` names), exits with the number of FAILs, and under `--fix` runs `fix_<name>` after a FAIL and checks again; `run_check` turns the check name into the function name with `${1//-/_}`, so the entry `concert-mcp` runs `check_concert_mcp` and `fix_concert_mcp`. This pair belongs in the runtime tier, after `check_vault`:

```bash
check_concert_mcp() { # the concert MCP submodule is checked out and its healthcheck passes
  [[ -f $ROOT/apps/concert-mcp/pyproject.toml ]] || { skip concert-mcp "apps/concert-mcp not checked out"; return; }
  command -v uv >/dev/null || { fail concert-mcp "uv missing: brew install uv"; return; }
  local out rc
  out=$(uv run --project "$ROOT/apps/concert-mcp" concert-mcp healthcheck 2>/dev/null); rc=$?
  if (( rc == 0 )); then pass concert-mcp "$(tail -n1 <<<"$out")"   # "all good"
  else fail concert-mcp "$(grep '^FAIL' <<<"$out" | sed -E 's/^FAIL +//; s/ +/ /g' | tr '\n' ';' | sed 's/;$//')"; fi
}
fix_concert_mcp() {
  note concert-mcp "git submodule update --init apps/concert-mcp && uv sync --project apps/concert-mcp"
  git -C "$ROOT" submodule update --init apps/concert-mcp && uv sync --project "$ROOT/apps/concert-mcp" || return 1
  note concert-mcp "credentials are per host and not scripted: uv run --project apps/concert-mcp concert-mcp healthcheck names each missing one and its README anchor"
}
```

The evidence on a FAIL is the FAIL rows joined by `;`, for example `gws-keyfile missing;spotify-credentials missing;setlistfm-key missing`. `--fix` can check out the submodule and sync its environment; it cannot log in to Google, Spotify or setlist.fm for you, so after a `--fix` the check still fails until the credentials steps in the package README are done on the gateway host. A `fix_concert_mcp` that returns non-zero (the sync failed) leaves the FAIL standing, as `run_check` expects.

The one-line edit that enables it, in the `RUNTIME` array near the top of `bin/doctor.sh`:

```diff
-RUNTIME=(ddns-record proxied-record hello cert routes containers authentik blueprints vikunja scotty torrent jellyfin vault)
+RUNTIME=(ddns-record proxied-record hello cert routes containers authentik blueprints vikunja scotty torrent jellyfin vault concert-mcp)
```

`bin/doctor.sh --list` then names it, and `bin/doctor.sh --only concert-mcp` runs it alone.

## 2. README paragraph for the homelab's service docs

For the homelab README, next to the `apps/torrent` note under "Adding a service" (a stack with no web UI):

> `apps/concert-mcp` is not a container and has no entry in `services.yaml`. It is the concert tools of the Telegram bot (Google Calendar event, Spotify teaser playlist, setlist.fm lookup, topic rename), packaged as one MCP server that OpenClaw launches itself as a stdio process on the gateway host with `uv run --project apps/concert-mcp concert-mcp serve`. It is registered with `openclaw mcp set concert ...` (the exact JSON is in `apps/concert-mcp/docs/homelab-integration.md`), health-checked by `bin/doctor.sh` (`concert-mcp`, runtime tier, SKIP when the submodule is not checked out), and keeps its credentials in the host's XDG directories (`~/.config/concert-mcp`, `~/.local/state/concert-mcp`, `~/.cache/concert-mcp`), never in this repository or in `.env`. Nothing listens on a port and nothing sits behind Caddy or authentik; the only network paths are outbound to Google, Spotify, setlist.fm and Telegram.

And one line for the directory table at the top of that README:

```
apps/concert-mcp/           the Telegram bot's concert tools as one MCP server; a stdio process OpenClaw launches, not a container; submodule
```

## 3. Registering the server with OpenClaw

On the gateway host, with `$HOMELAB` the absolute path of the homelab checkout:

```sh
HOMELAB=/path/to/homelab
openclaw mcp set concert '{"command":"uv","args":["run","--project","'"$HOMELAB"'/apps/concert-mcp","concert-mcp","serve"],"cwd":"'"$HOMELAB"'/apps/concert-mcp","env":{"CONCERT_MCP_OPERATOR_NAME":"${CONCERT_MCP_OPERATOR_NAME}","CONCERT_MCP_TZ":"${CONCERT_MCP_TZ}","CONCERT_MCP_GWS_BIN":"${CONCERT_MCP_GWS_BIN}","CONCERT_MCP_OPENCLAW_BIN":"${CONCERT_MCP_OPENCLAW_BIN}","CONCERT_MCP_LOG_LEVEL":"info"},"toolFilter":{"include":["concert_add","calendar_add","event_delete"]},"requestTimeoutMs":480000,"enabled":true}'
openclaw mcp doctor concert --probe
openclaw config set tools.alsoAllow '["exec","read","concert__concert_add","concert__calendar_add","concert__event_delete"]'
openclaw mcp reload
openclaw mcp probe concert
```

- `cwd` is the submodule so `uv run` finds the project's lockfile and virtual environment without a `--project` lookup from wherever the gateway was started.
- `toolFilter` exposes the three chat jobs (`concert_add`, `calendar_add`, `event_delete`); the four operator tools stay on the CLI. Drop it to expose all seven.
- `requestTimeoutMs` is 480000: a cold `concert_add` typically takes 60 to 150 s (Liked Songs fetch, teaser cap, gws, Telegram) and about 8 minutes when every provider sits at its cap. The default 60 s cuts the call off after the calendar insert and before the reply.
- `CONCERT_MCP_GWS_BIN` and `CONCERT_MCP_OPENCLAW_BIN` matter only when the gateway's PATH has neither binary and they live outside `/opt/homebrew/bin`, `/usr/local/bin` and `~/.local/bin`; unset, the package searches those three after PATH.
- The `${VAR}` placeholders expand from the gateway's environment at launch; an unset one expands to empty, which the package treats as "use the default" (`Admin`, `America/Los_Angeles`). Set them in the gateway's environment, or replace the placeholders with literal values, but keep the values out of this repository.
- `tools.alsoAllow` is needed when the agent runs the `minimal` tool profile, which hides every tool it does not name, MCP tools included; keep what the list already holds and add the three `concert__` names (`openclaw config get tools.alsoAllow` shows it).
- `openclaw mcp doctor concert --probe` starts the server once and lists its tools; `openclaw mcp reload` picks up a changed entry without restarting the gateway. Credentials added later (`concert-mcp spotify auth`, `setlist-auth`) need no reload: the server reads the files lazily on the next call.

The same command without `cwd`, for a standalone checkout, is in the package README under "OpenClaw configuration".
