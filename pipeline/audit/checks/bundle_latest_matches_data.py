"""gate_b.bundle_latest_matches_data — catches the class of bug takeaway_
numbers/latest_prev_resolution structurally cannot: build.py anchoring a
bundle's `latest` (and the tail of its plotted arrays, and index.json's
freshness row) on catalog.json's own static `latest` field instead of
data/series/<id>.json's actual newest observation. Every OTHER check verifies
the bundle against ITSELF (is the takeaway consistent with the bundle's own
`latest` block?) -- self-consistently wrong if the anchor itself is stale.
This check is the one place Gate B reads data/series/<id>.json's period
column directly and compares it to what shipped, independent of whatever
catalog.json claims.

For every bundled (non-panel) series:
  1. `latest.period` (site-data/sections/<section>.json) must equal the max
     period, among ALL of that series' own observations that carry at least
     one non-null measure, read directly from data/series/<id>.json.
  2. The final entry of `yoy_series` and of `level_series` must carry that
     same period (a stale bundle lags on the plotted arrays' tail too, not
     just the `latest` block).
  3. site-data/index.json's `freshness` row for this id must report the same
     `latest` period.

All BLOCK -- this is "the deployed site shows data that doesn't exist yet
was cut off before the newest print," not a tolerance judgment call.
"""
from __future__ import annotations

import time

from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.periods import period_end_date
from pipeline.audit.site_data import load_index, load_series_file

CHECK_ID = "gate_b.bundle_latest_matches_data"

_MEASURE_KEYS = ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")


def _true_latest_period(series: dict) -> str | None:
    """The max period (by actual calendar end date, not string sort -- a
    mixed annual/quarterly/monthly observations[] sorts wrong lexically)
    among observations carrying at least one non-null measure. Mirrors
    DATA-CONTRACT §3.2's "absent vs null" distinction: a period with every
    measure null/absent is not a real print yet."""
    best_period, best_end = None, None
    for obs in series.get("observations", []):
        if not any(obs.get(key) is not None for key in _MEASURE_KEYS):
            continue
        end = period_end_date(obs["period"])
        if end is None:
            continue
        if best_end is None or end > best_end:
            best_end, best_period = end, obs["period"]
    return best_period


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    checked = 0

    index = load_index(ctx.site_data_dir)
    freshness_by_id = {row["id"]: row for row in (index.get("freshness", []) if index else [])}

    for section_id, entry in ctx.bundle_entries():
        catalog_entry = ctx.series_by_id().get(entry["id"])
        if catalog_entry is None or catalog_entry.get("panel"):
            continue
        series = load_series_file(ctx.data_dir, catalog_entry)
        if series is None:
            findings.append(
                Finding(check=CHECK_ID, status="warn", series=entry["id"], note="data/series/<id>.json missing/unreadable; cannot cross-check")
            )
            continue
        true_latest = _true_latest_period(series)
        if true_latest is None:
            continue  # series genuinely has no observation yet -- nothing to be "behind"
        checked += 1
        series_id, tier = entry["id"], entry.get("tier")

        bundle_latest = (entry.get("latest") or {}).get("period")
        if bundle_latest != true_latest:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=tier,
                    field="latest.period",
                    expected=true_latest,
                    observed=bundle_latest,
                    note="bundle latest.period lags data/series/<id>.json's actual newest observation",
                )
            )

        for array_field in ("yoy_series", "level_series"):
            points = entry.get(array_field) or []
            tail_period = points[-1]["period"] if points else None
            if tail_period != true_latest:
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        tier=tier,
                        field=f"{array_field}[-1].period",
                        expected=true_latest,
                        observed=tail_period,
                        note=f"tail of {array_field} lags data/series/<id>.json's actual newest observation",
                    )
                )

        freshness_row = freshness_by_id.get(series_id)
        if freshness_row is None:
            continue  # gate_b.bundle_source_consistency already BLOCKs a missing freshness row
        if freshness_row.get("latest") != true_latest:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=tier,
                    field="index.json freshness.latest",
                    expected=true_latest,
                    observed=freshness_row.get("latest"),
                    note="index.json freshness entry lags data/series/<id>.json's actual newest observation",
                )
            )

    if not any(f.status == "block" for f in findings):
        findings.append(Finding(check=CHECK_ID, status="pass", note=f"{checked} series' bundle latest anchor matches data/series/ directly"))

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
