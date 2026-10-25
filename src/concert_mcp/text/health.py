"""The health report and its rendering (unit flows-deliver-health). Pure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    evidence: str
    anchor: str | None = None
    fix: str | None = None


@dataclass(frozen=True)
class HealthReport:
    checks: tuple[Check, ...]

    @property
    def failures(self) -> int:
        """Number of FAIL rows."""
        return sum(1 for c in self.checks if c.status == "FAIL")

    @property
    def ok(self) -> bool:
        """True when nothing fails."""
        return self.failures == 0

    def to_json(self) -> dict[str, Any]:
        """{"ok", "failures", "checks": [...]}."""
        return {"ok": self.ok, "failures": self.failures, "checks": [asdict(c) for c in self.checks]}


ANCHORS = {
    "getting-started": "README.md#getting-started",
    "configuration": "README.md#configuration",
    "google-calendar": "README.md#google-calendar",
    "spotify": "README.md#spotify",
    "setlistfm": "README.md#setlistfm",
    "openclaw": "README.md#openclaw",
}

# The homelab doctor's row: 'PASS  %-16s %s'.
_ROW = "{status}  {name:<16} {evidence}"
_FIX = "  ->  {name:<16} {fix} ({anchor})"


def render_health(report: HealthReport) -> str:
    """One 'STATUS  name  evidence' row per check, a fix line per FAIL, then 'N failing' or 'all good'."""
    lines = [_ROW.format(status=c.status, name=c.name, evidence=c.evidence) for c in report.checks]
    for c in report.checks:
        if c.status == "FAIL":
            lines.append(_FIX.format(name=c.name, fix=c.fix or "", anchor=ANCHORS.get(c.anchor or "", "")).rstrip())
    n = report.failures
    lines.append(f"{n} failing" if n else "all good")
    return "\n".join(lines)
