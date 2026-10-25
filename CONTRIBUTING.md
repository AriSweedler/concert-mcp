# Contributing

```sh
uv sync --dev
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

Run all three before opening a pull request; CI runs the same on Python 3.11, 3.12 and 3.13, plus a healthcheck on a bare runner and the personal-data scan from `tests/test_readme.py` on its own.

## The layering rule

- `src/concert_mcp/text/` is pure: no I/O, no clock, no environment, no imports from `adapters/` or `flows/`. Everything here is tested against literal strings.
- `src/concert_mcp/adapters/` holds the side effects, one class per provider, each implementing a `Protocol` from `adapters/protocols.py`. Adapters take a `runner=` or `urlopen=` keyword so their own tests need no network or subprocess.
- `src/concert_mcp/flows/` holds the policy: it composes `text/` and the Protocols and returns an `Outcome`. A flow never prints, never exits, never raises past its boundary (`FixInput`, `NeedUser` and `ProviderError` become outcomes; anything else becomes the crash reply).
- `server.py` is the only module that imports the MCP SDK; `cli.py` imports it lazily, so every other subcommand works without it.
- No `print()` anywhere in `src/` except `cli.py` and `flows/auth.py`; stdout is the MCP transport. `tests/test_no_stdout_in_library.py` greps for it.

## Every user-visible string has a golden test

A reply, a FIX line, a `NOT SENT` status, a health row: each is asserted byte for byte in `tests/`. When you change wording, change the golden in the same commit and add a line under `Changed` in `CHANGELOG.md`.

## No personal data in the repo

Nothing that identifies a person or a machine: no email addresses, no Google calendar ids (the long `group.calendar` addresses), no Telegram chat or thread ids, no bot tokens or API keys, no hostnames of anyone's machines, no 1Password item names that are not the documented defaults, no operator name in a reply string (use `{operator}`). Use placeholders (`/path/to/concert-mcp`, `<chat>`, `your-name`, `<owner>`). Test fixtures use fictional values (a fictional artist, venue and chat id, never a real plan). `tests/test_readme.py` scans every text file in the repository for the common shapes; CI runs it as its own job.

## Commits

Conventional commits: `feat(flows): ...`, `fix(text): ...`, `docs(readme): ...`, `test(adapters): ...`, `chore: ...`. One change per commit; the body says why.

## Adding a tool

1. Write the flow in `flows/`: a function that takes plain strings and `Services` and returns an `Outcome`. Reply text comes from a `render_*` function in `text/reply.py`, with a golden test.
2. Route it through `flows/deliver.py` (`run_tool`), so delivery, dedupe, `fix_guard` and the NO_REPLY / NOT SENT / FIX rendering are the same as for every other tool. Add a `FIX_GUARD_REPLIES` entry when the generic "artist and date" wording does not fit.
3. Add one function to `server.py` and to its `TOOLS` tuple, with an entry in `DESCRIPTIONS` (under 300 characters, literal, with an example) and `ANNOTATIONS`. Every parameter is `Annotated[Param, Field(description=...)]` with default `""` (`Param` cleans a number, a null or a quoted value into a plain string before validation, so the model never sees a schema error); required-ness is enforced in the flow, which returns the `need()` reply. Add the same subcommand to `cli.py`.
4. Add the tool's row to the table in `README.md` under "Architecture".
5. Add a routing rule to `INSTRUCTIONS` in `server.py` (keep it under 2000 characters) and extend `tests/test_server.py` and `tests/test_smoke_stdio.py` with the new name.

Keep tools coarse. The model behind the bot is a 9B model: it does well with one tool per job and a plan inside the tool, and badly with many small tools it must chain.
