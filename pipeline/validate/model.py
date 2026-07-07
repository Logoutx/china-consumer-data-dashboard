"""pipeline/validate/model.py -- the report shapes every check returns and the
gate assembles: Finding -> CheckResult -> GateReport, plus JSON/Markdown
rendering. Kept separate from gate.py so checks/*.py can import these types
without importing the orchestrator itself (avoids a circular import)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PASS = "pass"
WARN = "warn"
BLOCK = "block"
SKIP = "skip"

_STATUS_RANK = {PASS: 0, SKIP: 0, WARN: 1, BLOCK: 2}


@dataclass
class Finding:
    """One concrete thing a check noticed about one (series, period)."""

    check_id: str
    severity: str  # WARN | BLOCK
    message: str
    series_id: str | None = None
    period: str | None = None
    measure: str | None = None
    needs_ack: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "check_id": self.check_id,
            "severity": self.severity,
            "message": self.message,
            "series_id": self.series_id,
            "period": self.period,
            "measure": self.measure,
        }
        if self.needs_ack:
            out["needs_ack"] = True
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class CheckResult:
    """The outcome of one gate_a.* check across the whole staged batch."""

    check_id: str
    status: str  # PASS | WARN | BLOCK | SKIP
    findings: list[Finding] = field(default_factory=list)
    note: str | None = None  # e.g. why this check was skipped

    def to_dict(self) -> dict[str, Any]:
        out = {"check_id": self.check_id, "status": self.status, "findings": [f.to_dict() for f in self.findings]}
        if self.note:
            out["note"] = self.note
        return out


def _status_from_findings(findings: list[Finding]) -> str:
    worst = PASS
    for finding in findings:
        if _STATUS_RANK[finding.severity] > _STATUS_RANK[worst]:
            worst = finding.severity
    return worst


def make_result(check_id: str, findings: list[Finding] | None = None, *, note: str | None = None, skipped: bool = False) -> CheckResult:
    """Convenience constructor: derive status from the worst finding severity,
    or SKIP if the check declares itself skipped (no applicable data)."""
    findings = findings or []
    status = SKIP if skipped else _status_from_findings(findings)
    return CheckResult(check_id=check_id, status=status, findings=findings, note=note)


@dataclass
class GateReport:
    """Everything Gate A decided about one staged batch."""

    generated_at: str
    staged_dir: str
    release_id: str | None
    touched_series: list[str]
    results: list[CheckResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(r.status == BLOCK for r in self.results)

    @property
    def exit_code(self) -> int:
        return 2 if self.blocked else 0

    @property
    def all_findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings]

    @property
    def needs_ack(self) -> list[str]:
        seen = []
        for f in self.all_findings:
            if f.needs_ack and f.check_id not in seen:
                seen.append(f.check_id)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "staged_dir": self.staged_dir,
            "release_id": self.release_id,
            "touched_series": self.touched_series,
            "exit_code": self.exit_code,
            "blocked": self.blocked,
            "needs_ack": self.needs_ack,
            "results": [r.to_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        lines = ["# Gate A report", ""]
        lines.append(f"- generated_at: {self.generated_at}")
        lines.append(f"- release_id: {self.release_id}")
        lines.append(f"- touched series: {len(self.touched_series)}")
        verdict = "BLOCKED" if self.blocked else "PASS"
        lines.append(f"- verdict: **{verdict}** (exit {self.exit_code})")
        if self.needs_ack:
            lines.append(f"- needs ack: {', '.join(self.needs_ack)}")
        lines.append("")
        lines.append("| check | status | findings |")
        lines.append("|---|---|---|")
        for r in self.results:
            lines.append(f"| {r.check_id} | {r.status} | {len(r.findings)} |")
        lines.append("")
        findings = self.all_findings
        if findings:
            lines.append("## Findings")
            lines.append("")
            for f in findings:
                loc = f.series_id or ""
                if f.period:
                    loc += f"@{f.period}"
                if f.measure:
                    loc += f" ({f.measure})"
                ack = " [needs-ack]" if f.needs_ack else ""
                lines.append(f"- **{f.severity.upper()}** `{f.check_id}` {loc}: {f.message}{ack}")
        else:
            lines.append("No findings.")
        lines.append("")
        return "\n".join(lines)
