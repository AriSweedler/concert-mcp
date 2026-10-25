---
name: Bug report
about: Something concert-mcp did wrong
labels: bug
---

**What happened**

**What you expected**

**The command or chat message that triggered it**

For a CLI run, the exact command, with `--dry-run --no-send` added if it reproduces that way (redact the ticket text if you like). For the bot, what you sent to the chat and what the model answered, including any `FIX:` or `NOT SENT:` line.

```
```

**Output of `concert-mcp healthcheck`** (it prints no secrets; `--probe` adds one real call per provider; replace your home directory in paths with `~` if you prefer)

```
```

**Output of `concert-mcp config`** (redacted by design: paths and EXISTS / MISSING / LEGACY, never a file's contents; shorten paths the same way)

```
```

**OS and Python**

- OS:
- `python3 --version`:
- `uv --version`:
- concert-mcp version (`uv run python -c "import concert_mcp; print(concert_mcp.__version__)"`):
- OpenClaw version, if the bot was involved (`openclaw --version`):
