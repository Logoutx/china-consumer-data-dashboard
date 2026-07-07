"""gate_b.freshness — per pipeline/config/release_calendar.yaml (or the
embedded fallback table if that file doesn't exist yet), flags Tier-1 official
series whose latest observation is older than the agency's expected release
lag + grace period. This NEVER blocks deploy: a freshness lag means the real-
world release is late (or the poller hasn't run), not that the built data is
wrong -- task spec is explicit ("freshness failure means the world moved on,
not that our data is wrong"). It is always a WARN, surfaced prominently in the
diary.

Also WARNs if site-data/index.json's `generated_at` did not advance versus the
previous audited run's diary (site-data/diary/latest.json, read BEFORE this
run overwrites it) -- a same-or-earlier generated_at across two audit runs
suggests the build didn't actually pick up new data (a stuck pipeline), worth
flagging even though it isn't itself an accuracy defect.
"""
from __future__ import annotations

import time

from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.periods import period_end_date
from pipeline.audit.release_calendar import lag_budget_for, load_release_calendar
from pipeline.audit.site_data import load_index

CHECK_ID = "gate_b.freshness"


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []

    calendar, used_fallback = load_release_calendar(ctx.repo_root / "pipeline" / "config" / "release_calendar.yaml")
    if used_fallback:
        findings.append(
            Finding(
                check=CHECK_ID,
                status="warn",
                note="pipeline/config/release_calendar.yaml not found; using pipeline/audit's embedded fallback table",
            )
        )

    freshness_rows = []
    tier1_checked = 0
    for section_id, entry in ctx.bundle_entries():
        if entry.get("tier") != 1:
            continue
        latest = entry.get("latest")
        if not latest or not latest.get("period"):
            continue
        tier1_checked += 1
        catalog_entry = ctx.series_by_id().get(entry["id"], {})
        agency = catalog_entry.get("source", {}).get("agency", "")
        end_date = period_end_date(latest["period"])
        if end_date is None:
            continue
        lag_days = (ctx.as_of - end_date).days
        # freq comes from the bundle entry itself (build.py's own "freq"
        # metadata field, §10.2) -- not re-derived from the period string,
        # since a budget is inherently about the SERIES' nominal cadence, the
        # one case where entry["freq"] (rather than shape-of-this-period) is
        # exactly the right thing to read (see build.py's own module
        # docstring on that distinction).
        budget = lag_budget_for(calendar, agency, entry.get("freq", "M"))
        freshness_rows.append(
            {"id": entry["id"], "name_zh": entry.get("name_zh"), "latest": latest["period"], "lag_days": lag_days, "budget_days": budget}
        )
        if lag_days > budget:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="warn",
                    series=entry["id"],
                    period=latest["period"],
                    tier=1,
                    observed=lag_days,
                    expected=budget,
                    note=(
                        f"{entry.get('name_zh')} latest observation ({latest['period']}) is {lag_days} days old, "
                        f"beyond the {agency or 'default'} agency's {budget}-day expected-lag+grace budget"
                    ),
                )
            )

    if ctx.previous_diary:
        prev_generated_at = ctx.previous_diary.get("generated_at")
        index = load_index(ctx.site_data_dir)
        current_generated_at = index.get("generated_at") if index else None
        if prev_generated_at and current_generated_at and current_generated_at <= prev_generated_at:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="warn",
                    expected=f"> {prev_generated_at}",
                    observed=current_generated_at,
                    note="site-data/index.json generated_at did not advance since the previous audited run -- build may be stuck",
                )
            )

    if not any(f.status == "warn" for f in findings):
        findings.append(Finding(check=CHECK_ID, status="pass", note=f"{tier1_checked} tier-1 series within freshness budget"))

    return CheckReport(
        check=CHECK_ID,
        findings=findings,
        duration_seconds=time.monotonic() - start,
        extra={"freshness_rows": freshness_rows},
    )
