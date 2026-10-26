"""The docs are checked against the code: healthcheck anchors resolve, every setting is documented, the OpenClaw
snippet parses, the homelab snippets exist, and nothing personal leaked into any text file of the repository (unit
docs). The personal-data scan here is the one pattern list; CI runs it on its own as the `personal-data` job."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from concert_mcp.config import env_table
from concert_mcp.flows.healthcheck import CHECK_NAMES
from concert_mcp.text.health import ANCHORS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
HOMELAB_DOC = ROOT / "docs" / "homelab-integration.md"
CONFIG_DOC = ROOT / "docs" / "configuration.md"
ARCH_DOC = ROOT / "docs" / "architecture.md"

TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".lock", ".cfg", ".ini", ""}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}


def text_files() -> list[Path]:
    """Every text file in the repository, tracked or not (the repository may have no commits yet)."""
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        if p.suffix in TEXT_SUFFIXES or p.name in ("LICENSE", ".gitignore", ".python-version"):
            out.append(p)
    return sorted(out)


PERSONAL_DATA = {
    "an email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "a Telegram supergroup chat id": re.compile(r"-100\d{9,}"),
    "a Telegram bot token": re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}"),
    "a Google calendar id": re.compile(r"@group\.calendar\.google\.com"),
    "a .local hostname": re.compile(r"\b[A-Za-z0-9-]+\.local\b"),
}
# The fictional values the tests and docs are allowed to carry: one chat id, and the reserved example domains
# (RFC 2606), which is also how the fixtures spell a calendar id (`abc@group.calendar.example`).
ALLOWED = {"-1001234567890"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", ".example")


def _allowed(hit: str) -> bool:
    return hit in ALLOWED or hit.lower().endswith(ALLOWED_EMAIL_DOMAINS)


def github_slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation removed, spaces to hyphens ('setlist.fm' -> 'setlistfm')."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def readme_headings() -> list[str]:
    out = []
    in_fence = False
    for line in README.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("#"):
            out.append(line.lstrip("#").strip())
    return out


def test_slug_rule_matches_github():
    assert github_slug("setlist.fm") == "setlistfm"
    assert github_slug("Getting started") == "getting-started"
    assert github_slug("OpenClaw configuration") == "openclaw-configuration"


@pytest.mark.parametrize("anchor", sorted(ANCHORS.values()))
def test_every_health_anchor_is_a_readme_heading(anchor: str):
    """The README is the quickstart: every FAIL row of the healthcheck must land on a heading there."""
    file, _, fragment = anchor.partition("#")
    assert file == "README.md"
    slugs = {github_slug(h) for h in readme_headings()}
    assert fragment in slugs, f"{anchor} points at a heading README.md does not have; headings: {sorted(slugs)}"


@pytest.mark.parametrize("name", [row[0] for row in env_table()])
def test_every_env_var_is_documented(name: str):
    assert name in CONFIG_DOC.read_text(encoding="utf-8"), f"{name} is missing from docs/configuration.md"


def _snippet_json(text: str) -> dict:
    m = re.search(r"openclaw mcp set concert '(\{.*?\})'", text, re.S)
    assert m, "no `openclaw mcp set concert '{...}'` command found"
    return json.loads(m.group(1))


def test_readme_openclaw_snippet_parses_and_waits_long_enough():
    cfg = _snippet_json(README.read_text(encoding="utf-8"))
    assert cfg["requestTimeoutMs"] >= 480000  # the documented worst case: every provider at its cap
    assert cfg["command"] == "uv"
    assert cfg["args"][-2:] == ["concert-mcp", "serve"]
    assert cfg.get("enabled", True) is True


def test_homelab_openclaw_snippet_parses_once_the_path_is_filled_in():
    text = HOMELAB_DOC.read_text(encoding="utf-8").replace("'\"$HOMELAB\"'", "/path/to/homelab")
    cfg = _snippet_json(text)
    assert cfg["requestTimeoutMs"] >= 480000  # the documented worst case: every provider at its cap
    assert cfg["cwd"] == "/path/to/homelab/apps/concert-mcp"
    assert "/path/to/homelab/apps/concert-mcp" in cfg["args"]


def test_homelab_doc_carries_the_doctor_pair():
    text = HOMELAB_DOC.read_text(encoding="utf-8")
    assert "check_concert_mcp" in text
    assert "fix_concert_mcp" in text
    assert "openclaw mcp doctor concert --probe" in text


@pytest.mark.parametrize("path", text_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_personal_data_in_any_text_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    for what, pattern in PERSONAL_DATA.items():
        hits = [m.group(0) for m in pattern.finditer(text) if not _allowed(m.group(0))]
        assert not hits, f"{path.relative_to(ROOT)} contains {what}: {hits[0]!r}"


def test_the_scan_covers_source_tests_docs_and_workflows():
    rel = {str(p.relative_to(ROOT)) for p in text_files()}
    for must in (
        "README.md",
        "src/concert_mcp/server.py",
        "tests/conftest.py",
        "docs/DESIGN.md",
        ".github/workflows/ci.yml",
        "uv.lock",
        "pyproject.toml",
    ):
        assert must in rel, must
    assert not any(r.startswith(".venv") for r in rel)


def test_the_scan_catches_the_shapes_it_names():
    # Built by concatenation so this file does not itself carry the shapes it forbids.
    bad = {
        "an email address": "someone@" + "gmail.com",
        "a Telegram supergroup chat id": "-100" + "9876543210",
        "a Telegram bot token": "123456789:" + "A" * 35,
        "a Google calendar id": "abc@group." + "calendar.google.com",
        "a .local hostname": "my-mac" + ".local",
    }
    for what, sample in bad.items():
        assert PERSONAL_DATA[what].search(sample) and not _allowed(PERSONAL_DATA[what].search(sample).group(0)), what
    assert _allowed("-1001234567890") and _allowed("name@example.com")
    assert PERSONAL_DATA["a .local hostname"].search("~/.local/state") is None  # the XDG dir is not a hostname


def test_architecture_doc_names_the_one_link_form():
    text = ARCH_DOC.read_text(encoding="utf-8")
    assert "https://calendar.google.com/calendar/u/0/r/eventedit/<eid>" in text
    assert "2026-10-25" in text


@pytest.mark.parametrize("name", CHECK_NAMES)
def test_every_health_check_appears_in_the_configuration_doc(name: str):
    """docs/configuration.md pastes a real healthcheck run; a renamed or added check must land there too."""
    assert name in CONFIG_DOC.read_text(encoding="utf-8"), (
        f"healthcheck row {name} is missing from docs/configuration.md"
    )


def test_architecture_doc_tool_table_lists_every_tool():
    pytest.importorskip("mcp")
    from concert_mcp.server import TOOLS

    text = ARCH_DOC.read_text(encoding="utf-8")
    for fn in TOOLS:
        assert f"| `{fn.__name__}` |" in text, f"tool {fn.__name__} has no row in the docs/architecture.md table"


def test_changelog_has_the_first_release():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.1.0] - 2026-10-25" in text
    for heading in ("### Added", "### Changed", "### Removed"):
        assert heading in text


PUBLIC_PYPI = ("https://pypi.org/", "https://files.pythonhosted.org/")


def test_lockfile_resolves_only_through_public_pypi():
    """A lock made behind a private package index names that index on every package.

    It leaks the host that made it and breaks `uv sync --locked` for everyone else,
    so every registry and download URL in uv.lock must be the public index.
    """
    urls = re.findall(r'"(https?://[^"]+)"', (ROOT / "uv.lock").read_text())
    assert urls, "uv.lock has no URLs; is it a uv lockfile?"
    bad = sorted({u for u in urls if not u.startswith(PUBLIC_PYPI)})
    assert not bad, f"uv.lock pins a non-public index: {bad[:3]}"
