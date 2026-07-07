"""Builds the public "data diary" payload — site-data/diary/<run_id>.json and
site-data/diary/latest.json. This is the human-facing "what changed and what
should I trust" summary; the JSON/MD audit report (report.py) is the technical
counterpart for whoever owns the pipeline.

Content (task spec): what_changed (new_observations + revisions), warnings,
blocked_checks, a freshness table, and a plain-Chinese changelog line per
changed series. "Changed" is relative to the PREVIOUS run's diary
(`series_snapshot`, carried in every diary payload precisely so the next run
can diff against it without needing to re-read old site-data/ trees) -- on the
very first run ever (no site-data/diary/latest.json yet), every series with a
`latest` observation counts as "new" once, which is the correct behavior (nothing
to compare against) rather than an empty diary.
"""
from __future__ import annotations

import datetime as dt

from pipeline.audit.models import AuditContext, CheckReport

_SNAPSHOT_FIELDS = ("period", "m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")


def _snapshot_of(entry: dict) -> dict | None:
    latest = entry.get("latest")
    if not latest:
        return None
    return {field_name: latest.get(field_name) for field_name in _SNAPSHOT_FIELDS if field_name in latest}


def _changelog_line(entry: dict) -> str | None:
    """One Chinese sentence per newly-observed series, following the repo's
    typesetting rules (盘古之白 / Arabic numerals / curly quotes -- no curly
    quotes needed here, there's no quoted material). Deliberately its own
    format, not a re-emission of the bundle's own `takeaway` string: the task
    spec's own worked example ("社会消费品零售总额 新增 2026 年 5 月，当月同比
    -0.6%") uses an explicit minus sign and "新增" framing, which differs from
    takeaways.py's conservative no-minus-glyph convention (that module's
    output is written for public headline prose; this is an internal factual
    diary line where a plain "-0.6%" is the clearer, more literal statement)."""
    headline = entry.get("headline")
    if not headline or headline.get("latest_yoy") is None:
        return None
    caliber_label = "当月" if headline.get("caliber") == "single" else "累计"
    period_label = headline.get("period_label_zh", "")
    yoy = headline["latest_yoy"]
    sign = "-" if yoy < 0 else ""
    value = abs(round(yoy, 1))
    return f"{entry['name_zh']} 新增 {period_label}，{caliber_label}同比 {sign}{value}%"


def build_diary_payload(ctx: AuditContext, check_reports: list[CheckReport], *, exit_code: int) -> dict:
    previous_snapshot = (ctx.previous_diary or {}).get("series_snapshot", {})
    previous_generated_at = (ctx.previous_diary or {}).get("generated_at")

    new_observations = []
    revisions = []
    changelog_zh = []
    series_snapshot: dict[str, dict] = {}

    for section_id, entry in ctx.bundle_entries():
        snapshot = _snapshot_of(entry)
        if snapshot is not None:
            series_snapshot[entry["id"]] = snapshot
            previous = previous_snapshot.get(entry["id"])
            is_new = previous is None or previous.get("period") != snapshot.get("period")
            if is_new:
                new_observations.append(
                    {
                        "id": entry["id"],
                        "name_zh": entry.get("name_zh"),
                        "section": section_id,
                        **snapshot,
                    }
                )
                line = _changelog_line(entry)
                if line:
                    changelog_zh.append(line)

        for revision in entry.get("revisions_recent", []):
            revised_on = revision.get("revised_on")
            # Only surface a revision once: skip it if it was already dated at
            # or before the previous diary's own generated_at (already
            # reported in a prior run's diary). First run (no previous diary)
            # surfaces every revisions_recent entry the bundle already carries
            # (build.py's own 90-day window is the only filter then).
            if previous_generated_at and revised_on and revised_on <= previous_generated_at[:10]:
                continue
            revisions.append(
                {
                    "id": entry["id"],
                    "name_zh": entry.get("name_zh"),
                    "period": revision.get("period"),
                    "measure": revision.get("measure"),
                    "revised_on": revised_on,
                }
            )

    warnings = []
    blocked_checks = []
    for report in check_reports:
        if report.has_block():
            blocked_checks.append(report.check)
        for finding in report.findings:
            if finding.status == "warn":
                warnings.append(
                    {
                        "check": report.check,
                        "series": finding.series,
                        "panel": finding.panel,
                        "period": finding.period,
                        "note": finding.note,
                    }
                )

    freshness_report = next((r for r in check_reports if r.check == "gate_b.freshness"), None)
    freshness_rows = freshness_report.extra.get("freshness_rows", []) if freshness_report else []

    # site-data/index.json, every section bundle, and every panel bundle all
    # passthrough the same catalog `generated_at` verbatim (build.py's
    # deliberate determinism rule -- see that module's docstring); reading it
    # off the catalog already loaded onto ctx is equivalent and avoids a
    # redundant site_data.load_index() call here.
    generated_at = ctx.catalog.get("generated_at")

    return {
        "schema": "gate_b_diary/v1",
        "run_id": ctx.run_id,
        "seed": ctx.seed,
        "generated_at": generated_at,
        "audited_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "exit_code": exit_code,
        "what_changed": {
            "new_observations": new_observations,
            "revisions": revisions,
        },
        "changelog_zh": changelog_zh,
        "warnings": warnings,
        "blocked_checks": sorted(set(blocked_checks)),
        "freshness": freshness_rows,
        "series_snapshot": series_snapshot,
    }
