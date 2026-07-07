"""JSON + Markdown report writers, adapted from the legacy auditor's
`write_report` (tools/audit_official_data.py) to the 9-check, Finding-based
shape used here. HTML output was dropped -- the task spec only asks for
JSON + MD (plus the separate public diary payload, see diary.py); the legacy
HTML writer wasn't requested and would be pure scope creep to carry over.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.audit.models import CheckReport


def build_report_payload(
    *,
    run_id: str,
    seed: str,
    generated_at: str,
    catalog_version: str,
    check_reports: list[CheckReport],
    exit_code: int,
) -> dict:
    return {
        "schema": "gate_b_audit/v1",
        "run_id": run_id,
        "seed": seed,
        "generated_at": generated_at,
        "catalog_version": catalog_version,
        "exit_code": exit_code,
        "summary": {r.check: r.summary() for r in check_reports},
        "checks": [
            {
                "check": r.check,
                "summary": r.summary(),
                "duration_seconds": round(r.duration_seconds, 3),
                "error": r.error,
                "findings": [f.to_dict() for f in r.findings],
            }
            for r in check_reports
        ],
    }


def write_json_report(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_md_report(payload: dict, path: Path) -> None:
    lines = [
        "# Gate B — Post-Build Independent Audit",
        "",
        f"- Run id: `{payload['run_id']}`",
        f"- Seed: `{payload['seed']}`",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Catalog version: `{payload['catalog_version']}`",
        f"- Exit code: **{payload['exit_code']}** ({'BLOCK' if payload['exit_code'] else 'deployable'})",
        "",
        "## Checks",
        "",
    ]
    for check in payload["checks"]:
        summary = check["summary"]
        lines.append(
            f"### `{check['check']}` — "
            f"{summary.get('pass', 0)} pass / {summary.get('warn', 0)} warn / "
            f"{summary.get('block', 0)} block / {summary.get('skip', 0)} skip "
            f"({check['duration_seconds']}s)"
        )
        if check["error"]:
            lines.append(f"\n**Check raised an error:** `{check['error']}`\n")
        blocks = [f for f in check["findings"] if f.get("status") == "block"]
        warns = [f for f in check["findings"] if f.get("status") == "warn"]
        if blocks:
            lines.append("\n**Blocking findings:**\n")
            for f in blocks[:50]:
                lines.append(f"- {_format_finding(f)}")
            if len(blocks) > 50:
                lines.append(f"- … and {len(blocks) - 50} more (see JSON report)")
        if warns:
            lines.append("\n**Warnings:**\n")
            for f in warns[:30]:
                lines.append(f"- {_format_finding(f)}")
            if len(warns) > 30:
                lines.append(f"- … and {len(warns) - 30} more (see JSON report)")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_finding(f: dict) -> str:
    where = f.get("series") or f.get("panel") or ""
    period = f.get("period", "")
    field = f.get("field", "")
    bits = [b for b in (where, period, field) if b]
    location = " ".join(str(b) for b in bits)
    tail = f.get("note") or ""
    if f.get("expected") is not None or f.get("observed") is not None:
        tail = f"expected `{f.get('expected')}`, observed `{f.get('observed')}`" + (f" — {tail}" if tail else "")
    return f"{location}: {tail}".strip(": ")
