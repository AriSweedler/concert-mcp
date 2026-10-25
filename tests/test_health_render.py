"""text/health.py: the report shape and the doctor-style rendering."""

from __future__ import annotations

import re

from concert_mcp.text.health import ANCHORS, Check, HealthReport, render_health

ROW_RE = re.compile(r"^(PASS|FAIL|SKIP)  \S+\s+.+$")


def _report() -> HealthReport:
    return HealthReport(
        (
            Check("python", "PASS", "3.12.1"),
            Check("gws-keyfile", "FAIL", "missing", "google-calendar", "create the file keyring key: see README.md"),
            Check("gws-auth", "SKIP", "pass --probe"),
            Check("spotify-credentials", "FAIL", "mode 0o644, want 600", "spotify", "chmod 600 /x/spotify.json"),
            Check("openclaw-session-db", "SKIP", "absent or unreadable: replies are not delivered"),
        )
    )


def test_failures_and_ok() -> None:
    report = _report()
    assert report.failures == 2
    assert report.ok is False
    assert HealthReport((Check("python", "PASS", "3.12.1"),)).ok is True
    assert HealthReport(()).ok is True


def test_every_check_row_matches_the_doctor_shape() -> None:
    lines = render_health(_report()).split("\n")
    rows = [ln for ln in lines if not ln.startswith("  ->") and ln not in ("all good",) and not ln.endswith("failing")]
    assert len(rows) == 5
    for ln in rows:
        assert ROW_RE.match(ln), ln


def test_row_is_status_two_spaces_name_padded_to_16() -> None:
    text = render_health(HealthReport((Check("python", "PASS", "3.12.1"),)))
    assert text.split("\n")[0] == "PASS  python           3.12.1"


def test_fix_line_carries_the_readme_anchor() -> None:
    lines = render_health(_report()).split("\n")
    fixes = [ln for ln in lines if ln.startswith("  ->")]
    assert fixes == [
        "  ->  gws-keyfile      create the file keyring key: see README.md (README.md#google-calendar)",
        "  ->  spotify-credentials chmod 600 /x/spotify.json (README.md#spotify)",
    ]
    for ln in fixes:
        assert any(anchor in ln for anchor in ANCHORS.values())


def test_fix_lines_follow_all_rows_and_precede_the_summary() -> None:
    lines = render_health(_report()).split("\n")
    first_fix = next(i for i, ln in enumerate(lines) if ln.startswith("  ->"))
    assert all(ROW_RE.match(ln) for ln in lines[:first_fix])
    assert lines[-1] == "2 failing"


def test_summary_all_good_vs_n_failing() -> None:
    good = HealthReport((Check("python", "PASS", "3.12.1"), Check("gws-auth", "SKIP", "pass --probe")))
    assert render_health(good).split("\n")[-1] == "all good"
    assert "  ->" not in render_health(good)
    assert render_health(_report()).split("\n")[-1] == "2 failing"


def test_fail_without_fix_or_anchor_still_renders() -> None:
    text = render_health(HealthReport((Check("x", "FAIL", "boom"),)))
    assert text.split("\n") == ["FAIL  x                boom", "  ->  x                 ()", "1 failing"]


def test_to_json_round_trip() -> None:
    report = _report()
    data = report.to_json()
    assert data["ok"] is False
    assert data["failures"] == 2
    assert [c["name"] for c in data["checks"]] == [c.name for c in report.checks]
    rebuilt = HealthReport(tuple(Check(**c) for c in data["checks"]))
    assert rebuilt == report


def test_anchors_point_into_the_readme() -> None:
    assert set(ANCHORS) == {"getting-started", "configuration", "google-calendar", "spotify", "setlistfm", "openclaw"}
    for value in ANCHORS.values():
        assert value.startswith("README.md#")
