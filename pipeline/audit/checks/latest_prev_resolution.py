"""gate_b.latest_prev_resolution — re-verifies the bundle's `latest`/`prev`
block resolution (DATA-CONTRACT §10.2) directly from the bundle's own fields,
independently of pipeline.build's `_resolve_prev` (forbidden import): given
only `latest`, `prev`, `flags_latest`, and `breaks` as already published in
site-data, three rules must hold for every series:

  1. If `latest` is a Jan-Feb combined print (`flags_latest` contains
     "jan_feb"), `prev` must be LAST YEAR'S Jan-Feb print
     ("{year-1}-02"), never array-adjacent December.
  2. `prev` must share `latest`'s period SHAPE (annual / quarterly / monthly)
     -- comparing a quarterly latest against an annual-supplement prev (or
     vice versa) is not a legitimate "previous period".
  3. `prev` must be null whenever it would otherwise sit on the other side of
     a `no_yoy_across` break from `latest` ("never compare across").

All BLOCK.
"""
from __future__ import annotations

import time

from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.periods import period_shape

CHECK_ID = "gate_b.latest_prev_resolution"


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    checked = 0

    for section_id, entry in ctx.bundle_entries():
        latest, prev = entry.get("latest"), entry.get("prev")
        if not latest:
            continue
        checked += 1
        series_id, tier = entry["id"], entry.get("tier")
        flags_latest = entry.get("flags_latest") or []

        if "jan_feb" in flags_latest:
            expected_prev_period = f"{int(latest['period'][:4]) - 1}-02"
            if prev is None:
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        period=latest["period"],
                        tier=tier,
                        expected=expected_prev_period,
                        observed=None,
                        note="jan_feb latest has no prev at all; expected last year's jan_feb print",
                    )
                )
            elif prev["period"] != expected_prev_period:
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        period=latest["period"],
                        tier=tier,
                        expected=expected_prev_period,
                        observed=prev["period"],
                        note="prev of a jan_feb observation must be last year's jan_feb print, never array-adjacent December",
                    )
                )
            continue  # jan_feb rule is exhaustive for this entry; other rules don't add information

        if prev is None:
            continue

        if not period_shape(prev["period"]) == period_shape(latest["period"]):
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    period=latest["period"],
                    tier=tier,
                    observed=prev["period"],
                    note=(
                        f"prev period shape ({period_shape(prev['period'])}) does not match "
                        f"latest's shape ({period_shape(latest['period'])})"
                    ),
                )
            )

        for brk in entry.get("breaks", []):
            if not brk.get("no_yoy_across"):
                continue
            effective = brk.get("effective")
            if effective and prev["period"] < effective <= latest["period"]:
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        period=latest["period"],
                        tier=tier,
                        observed=prev["period"],
                        note=f"prev sits across a no_yoy_across break (effective {effective}) from latest; should be null",
                    )
                )

    if not any(f.status == "block" for f in findings):
        findings.append(Finding(check=CHECK_ID, status="pass", note=f"{checked} series' latest/prev resolution checked clean"))

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
