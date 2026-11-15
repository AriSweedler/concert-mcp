# concert-commands

An OpenClaw plugin with two chat commands for a show's Telegram topic. No model turn is involved, and the plugin reads no other message: it runs only when a command is typed.

| Command | What it does |
|---|---|
| `/tickets <link>` | records the show's ticket page through `concert-mcp tickets`: the link goes on the calendar event and the Partiful invite is made |
| `/setlist <link>` | after the show, takes its setlist.fm page through `concert-mcp setlist-playlist`: the setlist playlist is made, and `setlist.fm` and `Setlist playlist` links go on the calendar event and on the Partiful invite |

Typed anywhere but a show's topic, a command answers with how to use it.

Without the plugin the same things happen through the model: a `tickets: <link>` message makes it call `tickets_add`, and a pasted setlist.fm link makes it call `setlist_playlist`.

## Install

On the OpenClaw host, from this directory:

```sh
openclaw plugins install -l "$PWD" --force     # --force: a local path is outside OpenClaw's reviewed store
openclaw config set plugins.entries.concert-commands.config.command '["uv","run","--project","/path/to/concert-mcp","concert-mcp"]'
openclaw plugins reload concert-commands
```

`command` is how the plugin runs the concert-mcp CLI; without it, `concert-mcp` must be on the gateway's PATH. `timeoutMs` (default 240000) caps one call.

## Test

```sh
node --test
```

The rules (`rules.js`) are pure and tested here; `index.js` is the thin shell that registers the commands.
