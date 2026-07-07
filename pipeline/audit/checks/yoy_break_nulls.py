"""gate_b.yoy_break_nulls — DATA-CONTRACT §4.2's build-time invariant, re-
verified independently: no YoY-shaped value may be stored or rendered across a
`no_yoy_across` break seam. For every bundled series with such a break:

  1. `yoy_series` must be null (or the key simply absent from the point, which
     the actual bundle shape never does -- every yoy_series point always has a
     `yoy` key, so we check it is None) for every period in the blocked window
     `[effective, yoy_valid_from)`.
  2. `latest` / `prev` measure blocks must not expose a YoY-shaped key
     (m_yoy / ytd_yoy / real_yoy) at all for a period inside the blocked
     window -- the bundle's own `_measure_block` OMITS the key rather than
     nulling it, so presence-of-key is itself the violation, not its value.
  3. No takeaway/latest block may expose a cross-break comparison: if `latest`
     and `prev` are both present, no break's `effective` date may fall
     strictly between `prev.period` and `latest.period` (inclusive of
     `latest.period`) -- `prev` should have resolved to null in that case.

All BLOCK — this is a hard "never compare across" correctness invariant, not a
numeric-tolerance judgment call.
"""
from __future__ import annotations

import time

from pipeline.audit.models import AuditContext, CheckReport, Finding

CHECK_ID = "gate_b.yoy_break_nulls"

_YOY_LIKE_KEYS = ("m_yoy", "ytd_yoy", "real_yoy")


def _in_blocked_window(period: str, brk: dict) -> bool:
    if not brk.get("no_yoy_across"):
        return False
    effective = brk.get("effective")
    if not effective or period < effective:
        return False
    valid_from = brk.get("yoy_valid_from")
    if valid_from is not None and period >= valid_from:
        return False
    return True


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    checked_series = 0

    for section_id, entry in ctx.bundle_entries():
        breaks = [b for b in entry.get("breaks", []) if b.get("no_yoy_across")]
        if not breaks:
            continue
        checked_series += 1
        series_id = entry["id"]
        tier = entry.get("tier")

        # 1) yoy_series nulled across the blocked window.
        for point in entry.get("yoy_series", []):
            for brk in breaks:
                if _in_blocked_window(point["period"], brk) and point.get("yoy") is not None:
                    findings.append(
                        Finding(
                            check=CHECK_ID,
                            status="block",
                            series=series_id,
                            period=point["period"],
                            field="yoy_series.yoy",
                            tier=tier,
                            observed=point.get("yoy"),
                            expected=None,
                            note=f"yoy value present inside no_yoy_across window (break effective {brk.get('effective')})",
                        )
                    )

        # 2) latest/prev blocks never expose a yoy-like KEY inside the window.
        for block_name in ("latest", "prev"):
            block = entry.get(block_name)
            if not block:
                continue
            for brk in breaks:
                if not _in_blocked_window(block["period"], brk):
                    continue
                for key in _YOY_LIKE_KEYS:
                    if key in block:
                        findings.append(
                            Finding(
                                check=CHECK_ID,
                                status="block",
                                series=series_id,
                                period=block["period"],
                                field=f"{block_name}.{key}",
                                tier=tier,
                                observed=block[key],
                                note=f"{block_name} block exposes {key!r} inside no_yoy_across window (break effective {brk.get('effective')})",
                            )
                        )

        # 3) no cross-break latest/prev comparison.
        latest, prev = entry.get("latest"), entry.get("prev")
        if latest and prev:
            for brk in breaks:
                effective = brk.get("effective")
                if effective and prev["period"] < effective <= latest["period"]:
                    findings.append(
                        Finding(
                            check=CHECK_ID,
                            status="block",
                            series=series_id,
                            period=latest["period"],
                            field="prev",
                            tier=tier,
                            observed=prev["period"],
                            expected=None,
                            note=(
                                f"prev ({prev['period']}) sits on the other side of a no_yoy_across break "
                                f"(effective {effective}) from latest ({latest['period']}); prev should be null"
                            ),
                        )
                    )

    if not any(f.status == "block" for f in findings):
        findings.append(
            Finding(check=CHECK_ID, status="pass", note=f"{checked_series} series with a no_yoy_across break checked clean")
        )

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
