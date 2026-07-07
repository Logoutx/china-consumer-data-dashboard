"""gate_b.takeaway_numbers — extracts every number out of each bundle's
`takeaway` (and, defensively, `headline.period_label_zh`-adjacent prose is
NOT in scope -- only the free-text `takeaway` string is regex-parsed) and
verifies each one is verbatim-traceable to a stored measure (latest YoY,
delta-vs-prev in percentage points, real YoY, or the streak count). Numeric
mismatch BLOCKs; this check does not look at prose/wording at all (a template
change that doesn't touch a number is not its concern, per task spec).

Design: bag-of-numbers, not a full parse. pipeline/takeaways.py (read for
research, never imported here) has six sentence shapes (break_first,
quarterly-income, ytd-only, jan_feb-suffixed, plain sign-matrix, and a
"level-only" template added 2026-07-08 for series with no published YoY at
all, e.g. PMI) and every one of them only ever prints a NON-negative magnitude
(sign is conveyed by a verb like 上涨/下降/由升转降/上升/回落, never a "-" glyph —
confirmed in real bundle output, e.g.
"2026 年 5 月社会消费品零售总额同比由升转降，下降 0.6%"). So: strip every
non-measure numeric substring we can positively identify (the series' own
name_zh AND name_short — build.py's `_headline_name` embeds whichever is
present, and a name_short like "M2" can itself contain a digit — the exact
period_label_zh, the YTD-only "1-{M} 月" anchor/ref forms, and a streak
clause), then require every digit-run left over to match SOME stored
measure's magnitude. The stored-measure set includes latest_yoy /
delta_pp_vs_prev / real_yoy (the YoY-shaped templates) AND the raw level
value(s) plus the level's own MoM point-delta (the level-only template,
which has no YoY at all) — unconditionally, not gated on detecting which
template fired, so a stored-measure set is always a superset of what any
known template could print. This is intentionally one-directional (text ⊆
stored, not stored ⊆ text): different templates legitimately surface
different subsets of the stored measures (e.g. a sign-flip sentence never
mentions delta_pp_vs_prev at all), so "a stored measure that never appears in
text" is correct, not a gap.
"""
from __future__ import annotations

import re
import time

from pipeline.audit.kernel import numeric
from pipeline.audit.models import AuditContext, CheckReport, Finding

CHECK_ID = "gate_b.takeaway_numbers"

_YTD_ANCHOR_RE = re.compile(r"1-\d{1,2}\s*月")
_YEAR_MONTH_RE = re.compile(r"\d{4}\s*年\s*(?:1[—-]\d{1,2}\s*月|\d{1,2}\s*月|全年|\d\s*季度)")
_STREAK_RE = re.compile(r"连续\s*(\d+)\s*个月(以上)?")  # takeaways.py's _join inserts a pangu space after "连续" (CJK-digit seam)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _extract_numbers(takeaway: str, entry: dict) -> tuple[list[float], int | None]:
    """Returns (leftover_measure_numbers, streak_number_or_None). Strips both
    name_zh and name_short (build.py's `_headline_name` picks whichever is
    present -- either could be embedded verbatim in the sentence, and a
    name_short like "M2"/"PMI" can itself contain digits) before the exact
    period_label_zh, a streak clause, and the YTD-only anchor forms."""
    text = takeaway
    for name_field in ("name_zh", "name_short"):
        name_value = entry.get(name_field)
        if name_value:
            text = text.replace(name_value, "")
    headline = entry.get("headline") or {}
    if headline.get("period_label_zh"):
        text = text.replace(headline["period_label_zh"], "")
    streak_match = _STREAK_RE.search(text)
    streak_number = int(streak_match.group(1)) if streak_match else None
    text = _STREAK_RE.sub(" ", text)
    text = _YEAR_MONTH_RE.sub(" ", text)
    text = _YTD_ANCHOR_RE.sub(" ", text)
    numbers = [float(m.group(0)) for m in _NUMBER_RE.finditer(text)]
    return numbers, streak_number


def _stored_magnitudes(entry: dict) -> set[float]:
    """Every magnitude ANY known takeaway template could print for this entry.
    Deliberately unconditional (not gated on first detecting which template
    fired from the bundle alone, which isn't always unambiguous) -- a superset
    costs nothing given the one-directional (text subseteq stored) check."""
    stored: set[float] = set()
    headline = entry.get("headline") or {}
    if headline.get("latest_yoy") is not None:
        stored.add(round(abs(headline["latest_yoy"]), 6))
    if headline.get("delta_pp_vs_prev") is not None:
        stored.add(round(abs(headline["delta_pp_vs_prev"]), 6))
    latest = entry.get("latest") or {}
    prev = entry.get("prev") or {}
    if latest.get("real_yoy") is not None:
        stored.add(round(abs(latest["real_yoy"]), 6))
    # Level-only template (generate_level_takeaway): the level itself ("为 X")
    # and its MoM point-delta ("比上月上升/回落 D 个点") -- covers PMI-shaped
    # series (no YoY published at all) without needing to detect that case
    # explicitly from bundle fields alone.
    for level_field in ("m", "ytd"):
        if latest.get(level_field) is not None:
            stored.add(round(abs(numeric(latest[level_field])), 6))
            if prev.get(level_field) is not None:
                stored.add(round(abs(numeric(latest[level_field]) - numeric(prev[level_field])), 6))
    return stored


def _matches_any(value: float, stored: set[float], tol: float = 0.06) -> bool:
    return any(abs(value - s) <= tol for s in stored)


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    checked = 0

    for section_id, entry in ctx.bundle_entries():
        takeaway = entry.get("takeaway")
        if not takeaway:
            continue
        checked += 1
        series_id, tier = entry["id"], entry.get("tier")
        headline = entry.get("headline") or {}
        numbers, streak_number = _extract_numbers(takeaway, entry)
        stored = _stored_magnitudes(entry)

        for value in numbers:
            if not _matches_any(value, stored):
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        period=headline.get("period_label_zh"),
                        tier=tier,
                        observed=value,
                        expected=sorted(stored),
                        evidence=takeaway,
                        note="number in takeaway text has no matching stored measure (latest_yoy / delta_pp_vs_prev / real_yoy)",
                    )
                )

        stored_streak = headline.get("streak") or 0
        if streak_number is not None:
            expected_display = 24 if stored_streak > 24 else stored_streak
            if streak_number != expected_display or stored_streak < 2:
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status="block",
                        series=series_id,
                        period=headline.get("period_label_zh"),
                        tier=tier,
                        field="streak",
                        observed=streak_number,
                        expected=expected_display,
                        evidence=takeaway,
                        note="streak count in takeaway text does not match headline.streak",
                    )
                )
        elif stored_streak >= 2:
            # A real streak is on record but the sentence doesn't mention one --
            # not necessarily wrong (e.g. is_break_first suppresses the streak
            # clause unconditionally), so this is a WARN, not a BLOCK: worth a
            # human glance, not proof of a numeric error.
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="warn",
                    series=series_id,
                    period=headline.get("period_label_zh"),
                    tier=tier,
                    field="streak",
                    observed=None,
                    expected=stored_streak,
                    evidence=takeaway,
                    note="headline.streak >= 2 but no streak clause found in takeaway text",
                )
            )

    if not any(f.status in ("block",) for f in findings):
        findings.append(Finding(check=CHECK_ID, status="pass", note=f"{checked} takeaway strings numerically verified"))

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
