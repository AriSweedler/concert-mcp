# concert-commands

An OpenClaw plugin with one chat command, `/tickets <link>`. Typed in a show's Telegram topic (anywhere else it answers with how to use it), it records the show's ticket link through `concert-mcp tickets`, which writes the link to the calendar event and makes the Partiful invite. No model turn is involved, and the plugin reads no other message: it runs only when the command is typed.

Without it, the same thing happens through the model: a `tickets: <link>` message makes the model call `tickets_add`.

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

The rules (`rules.js`) are pure and tested here; `index.js` is the thin shell that registers the command.
