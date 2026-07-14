"""Release-lifecycle checks: gate_a.calendar_expected, gate_a.calendar_window,
gate_a.partial_parse_completeness, gate_a.archive_release_identity,
gate_a.break_no_yoy, gate_a.break_link, gate_a.revision_flood,
gate_a.revision_integrity."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

from pipeline.validate.config import ARCHIVE_SOURCE_TO_CALENDAR_KEY
from pipeline.validate.context import GateContext
from pipeline.validate.model import BLOCK, WARN, Finding, make_result
from pipeline.validate.util import in_no_yoy_window, is_number, period_shape, steps_between

# ---------------------------------------------------------------------------
# 14. gate_a.calendar_expected
# ---------------------------------------------------------------------------


def _new_periods_for(ctx: GateContext, series_id: str) -> list[str]:
    if ctx.normalize_report is not None:
        return sorted(p for sid, p in ctx.normalize_report.new_observations if sid == series_id)
    real = ctx.load_real(series_id)
    existing = {o["period"] for o in (real.get("observations", []) if real else [])}
    return sorted(p for p in ctx.batch.periods_for(series_id) if p not in existing)


def check_calendar_expected(ctx: GateContext):
    """gate_a.calendar_expected -- staged release period should be exactly one
    step past whatever was on file before this run. Exactly +1 step: pass.
    Exactly +2 (one period never got ingested): WARN, a gap. <=0 steps (equal
    or backward) or >=3 steps: BLOCK, this looks like the wrong article -- UNLESS
    a known_disagreements config entry has already acknowledged this (series,
    period) for this check, which demotes it to a WARN instead (e.g. a genuine,
    understood one-time catch-up backfilling a hole in OUR OWN archive/history,
    not a sign the wrong release landed -- see pipeline/config/validation.yaml's
    nbs-retail-cat-grain-food entry)."""
    findings = []
    evaluated = False
    for series_id in ctx.touched_series:
        data = ctx.load(series_id)
        if data is None:
            continue
        freq = data.get("freq", "M")
        real = ctx.load_real(series_id)
        real_periods = [o["period"] for o in (real.get("observations", []) if real else []) if period_shape(o["period"]) == freq]
        if not real_periods:
            continue  # first-ever observation for this series -- no anchor to compare against
        latest_real = max(real_periods)

        for period in _new_periods_for(ctx, series_id):
            if period_shape(period) != freq:
                continue
            steps = steps_between(latest_real, period, freq)
            if steps is None:
                continue
            evaluated = True
            if steps == 1:
                continue
            if steps == 2:
                findings.append(
                    Finding("gate_a.calendar_expected", WARN, f"period {period} is 2 steps past current latest {latest_real} -- one period appears to have been skipped", series_id=series_id, period=period)
                )
            else:
                ack = ctx.config.is_known_disagreement(series_id=series_id, period=period, check_id="gate_a.calendar_expected")
                severity = WARN if ack else BLOCK
                note = f"period {period} is {steps} steps from current latest {latest_real} -- looks like the wrong release (expected exactly 1 step ahead)"
                if ack:
                    note += f"; acked: {ack.note}"
                findings.append(Finding("gate_a.calendar_expected", severity, note, series_id=series_id, period=period))
    if not evaluated:
        return make_result("gate_a.calendar_expected", skipped=True, note="no touched series had both prior history and a genuinely new period to compare")
    return make_result("gate_a.calendar_expected", findings)


# ---------------------------------------------------------------------------
# 15. gate_a.calendar_window
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d")


def _parse_published_at(value: str | None):
    if not value:
        return None
    text = value.strip().replace("Z", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[: len(fmt) + 2].split("+")[0], fmt)
        except ValueError:
            continue
    match = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return datetime(y, m, d)
    return None


def check_calendar_window(ctx: GateContext):
    """gate_a.calendar_window -- WARN. published_at's day-of-month (or lag
    from period-end, for a lag-shaped calendar entry) should fall within the
    configured window, extended by grace_days. Skips (not warns) when
    published_at can't be parsed or no calendar entry exists for this
    source -- this is a soft advisory check, not a format-drift alarm."""
    published_at = _parse_published_at(ctx.batch.published_at)
    if published_at is None:
        return make_result("gate_a.calendar_window", skipped=True, note="published_at missing or unparseable")
    calendar_key = ARCHIVE_SOURCE_TO_CALENDAR_KEY.get(ctx.effective_archive_source or "")
    window = ctx.calendar.get(calendar_key) if calendar_key else None
    if window is None:
        return make_result("gate_a.calendar_window", skipped=True, note=f"no release_calendar.yaml entry for source {ctx.batch.source!r}")

    findings = []
    if window.window_days is not None:
        lo, hi = window.window_days
        day = published_at.day
        if not (lo - window.grace_days <= day <= hi + window.grace_days):
            findings.append(
                Finding(
                    "gate_a.calendar_window", WARN,
                    f"published_at day-of-month {day} outside expected window [{lo}, {hi}] +/- {window.grace_days} grace days for {calendar_key!r}",
                )
            )
    elif window.lag_days is not None:
        # Advisory only: without the reference period's end-date on hand here,
        # this branch just records the lag concept is configured; the day-of-
        # month form above covers every source actually wired into runner.py
        # today (docs/DATA-CONTRACT.md's release_calendar.yaml worked example
        # notes consumer_confidence's ~40 day lag as the one exception).
        pass
    return make_result("gate_a.calendar_window", findings)


# ---------------------------------------------------------------------------
# 16. gate_a.partial_parse_completeness
# ---------------------------------------------------------------------------

# Anchor groups keyed by source_field vocabulary (pipeline/config/field_map.yaml's
# own Chinese labels), NOT by series id -- field_map.yaml's ids are explicitly
# documented placeholders that don't yet match data/catalog.json's real ids
# (see field_map.yaml's own module docstring), so keying completeness off an
# id would make this check permanently misfire on that unrelated, already-
# flagged reconciliation gap. The source_field vocabulary is exactly what a
# parser emits regardless of which id it eventually resolves to.
REQUIRED_ANCHORS = {
    "nbs-cpi": {
        "headline": {"居民消费价格"},
        "food": {"食品"},
        "core": {"不包括食品和能源"},
        "services": {"服务"},
    },
    "nbs-retail": {
        "headline": {"社会消费品零售总额"},
        "urban_rural": {"城镇", "乡村"},
        "goods_catering": {"商品零售额", "餐饮收入"},
        "online": {"网上商品零售额", "网上商品和服务零售额", "网上服务零售额"},
    },
    "pbc-money": {
        "m1": {"M1"},
        "m2": {"M2"},
        "tsf_stock": {"社会融资规模存量"},
        "tsf_flow": {"社会融资规模增量"},
    },
}


def check_partial_parse_completeness(ctx: GateContext):
    """gate_a.partial_parse_completeness -- BLOCK. Every required anchor group
    for this release type must have at least one of its candidate source
    fields present -- a parser returning a plausible-looking partial table
    (e.g. headline + food but no core/services) is the nastiest silent
    failure mode this gate exists to catch."""
    source = ctx.batch.source
    groups = REQUIRED_ANCHORS.get(source or "")
    if groups is None:
        return make_result("gate_a.partial_parse_completeness", skipped=True, note=f"no anchor-group table for source {source!r}")
    if not ctx.batch.raw_source_fields:
        return make_result("gate_a.partial_parse_completeness", skipped=True, note="batch carries no source_field vocabulary to check completeness against")

    findings = []
    for group_name, candidates in groups.items():
        if not candidates & ctx.batch.raw_source_fields:
            findings.append(
                Finding(
                    "gate_a.partial_parse_completeness", BLOCK,
                    f"anchor group {group_name!r} missing: none of {sorted(candidates)} present in this release",
                )
            )
    return make_result("gate_a.partial_parse_completeness", findings)


# ---------------------------------------------------------------------------
# 17. gate_a.archive_release_identity
# ---------------------------------------------------------------------------


def _new_observations(ctx: GateContext) -> list[tuple[str, str]]:
    if ctx.normalize_report is not None:
        return list(ctx.normalize_report.new_observations)
    out = []
    for series_id in ctx.touched_series:
        out.extend((series_id, p) for p in _new_periods_for(ctx, series_id))
    return out


def _looks_like_stale_reserve(data: dict, period: str) -> bool:
    observations = sorted(data.get("observations", []), key=lambda o: o["period"])
    periods = [o["period"] for o in observations]
    if period not in periods:
        return False
    idx = periods.index(period)
    if idx == 0:
        return False
    current, previous = observations[idx], observations[idx - 1]
    shared_measures = [m for m in ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy") if is_number(current.get(m)) and is_number(previous.get(m))]
    if len(shared_measures) < 2:
        return False
    return all(current[m] == previous[m] for m in shared_measures)


_DG_REFRESH_PREFIX = "dg-refresh"


def _dg_refresh_manifest_satisfied(ctx: GateContext) -> bool:
    """dg_refresh releases aren't a single HTML capture -- they're a DG bulk
    pull, and the capture files themselves are hash-named (no release_id
    match possible against them directly). The dg_refresh owner instead
    writes data/archive/dg/manifest_<release_id>.json =
    {"release_id", "generated_at", "captures": [filenames]}; identity is
    satisfied when that manifest exists AND every capture it lists is
    actually present on disk."""
    manifest_path = ctx.archive_dir / "dg" / f"manifest_{ctx.batch.release_id}.json"
    if not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return False
    captures = manifest.get("captures") or []
    if not captures:
        return False
    return all((ctx.archive_dir / "dg" / filename).exists() for filename in captures)


def check_archive_release_identity(ctx: GateContext):
    """gate_a.archive_release_identity -- BLOCK. Every genuinely new
    observation this run must trace to an archive capture actually fetched
    this run: for an HTML-sourced release, a matching data/archive/<source>/
    <release_id>.* file; for a dg_refresh release (release_id starting with
    'dg-refresh'), a data/archive/dg/manifest_<release_id>.json whose listed
    captures all exist on disk (see _dg_refresh_manifest_satisfied -- DG
    capture files themselves are hash-named, so no direct release_id match is
    possible there). A new "period" whose values are byte-identical to the
    immediately preceding period is flagged as a likely stale re-serve
    masquerading as fresh data."""
    new_obs = _new_observations(ctx)
    if not new_obs:
        return make_result("gate_a.archive_release_identity", skipped=True, note="no new observations this run")
    if ctx.archive_dir is None or not ctx.batch.release_id:
        return make_result("gate_a.archive_release_identity", skipped=True, note="no archive_dir/release_id wired in for this invocation")

    if ctx.batch.release_id.startswith(_DG_REFRESH_PREFIX):
        has_release_file = _dg_refresh_manifest_satisfied(ctx)
        missing_message = (
            f"new observation traces to dg_refresh release_id {ctx.batch.release_id!r} but no manifest "
            f"(data/archive/dg/manifest_{ctx.batch.release_id}.json) with all listed captures present was found"
        )
    else:
        archive_subdir = ctx.archive_dir / (ctx.effective_archive_source or "")
        has_release_file = archive_subdir.exists() and any(p.stem == ctx.batch.release_id for p in archive_subdir.glob("*"))
        missing_message = f"new observation traces to release_id {ctx.batch.release_id!r} but no matching archive capture found under {archive_subdir}"

    findings = []
    for series_id, period in new_obs:
        if not has_release_file:
            findings.append(Finding("gate_a.archive_release_identity", BLOCK, missing_message, series_id=series_id, period=period))
            continue
        data = ctx.load(series_id)
        if data is not None and _looks_like_stale_reserve(data, period):
            findings.append(
                Finding(
                    "gate_a.archive_release_identity", BLOCK,
                    f"new period {period} is byte-identical to the immediately preceding period on every shared measure -- likely a stale re-serve, not a real new print",
                    series_id=series_id, period=period,
                )
            )
    return make_result("gate_a.archive_release_identity", findings)


# ---------------------------------------------------------------------------
# 18. gate_a.break_no_yoy
# ---------------------------------------------------------------------------


def check_break_no_yoy(ctx: GateContext):
    """gate_a.break_no_yoy -- BLOCK. No YoY measure may sit inside a break's
    no_yoy_across window, and no YoY may bridge the seam right at
    yoy_valid_from before enough restated post-break history exists to make
    that comparison meaningful.

    Bug fixed 2026-07-08: the "enough restated history" requirement only
    applies when `yoy_valid_from` is genuinely AFTER `effective` (a real
    multi-period suppression window that needs time to accumulate restated
    history, e.g. a new id with no prior comparable history at all, or a
    methodology change with no valid splice). Per DATA-CONTRACT.md §2.1,
    `yoy_valid_from == effective` is a *different*, equally legitimate case
    -- "a legitimate, common case -- not a no-op" -- used for pbc-m1's
    redefinition and nbs-cpi/nbs-ppi's rebase, where NBS/PBoC's own published
    m_yoy (or "prior-year-month=100" index) remains valid and comparable
    immediately from the first post-break period, with no waiting period at
    all: no restated history ever needs to accumulate because none is
    required. The `restated` window `[effective, valid_from)` is empty BY
    CONSTRUCTION whenever they're equal, so the old code's unconditional
    `len(restated) < required` check could never NOT fire for this break
    shape -- it permanently BLOCKed every real run touching nbs-cpi-yoy,
    nbs-ppi-yoy, or pbc-m1 (any of their already-published, always-valid
    seam-period m_yoy), which is exactly backwards from what §2.1 documents
    as correct. Gated on `valid_from > effective` below so this requirement
    now only evaluates for the genuine-gap break shape it was designed for
    (see pipeline/tests/fixtures/validate/data/series/test-cpi-break.json,
    whose effective=2026-01/yoy_valid_from=2027-01 shape is exactly that
    case, and pipeline/tests/test_validate_lifecycle.py's new
    test_break_no_yoy_passes_when_yoy_valid_from_equals_effective)."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        breaks = data.get("breaks", [])
        no_yoy_breaks = [b for b in breaks if b.get("no_yoy_across")]
        if not no_yoy_breaks:
            continue
        evaluated = True
        observations = sorted(data.get("observations", []), key=lambda o: o["period"])
        freq = data.get("freq", "M")
        required = {"M": 12, "Q": 4, "A": 1}.get(freq, 12)

        for brk in no_yoy_breaks:
            effective, valid_from = brk.get("effective"), brk.get("yoy_valid_from")
            if effective and valid_from and valid_from < effective:
                findings.append(Finding("gate_a.break_no_yoy", BLOCK, f"break yoy_valid_from={valid_from} precedes its own effective={effective}", series_id=series_id))

            for obs in observations:
                period = obs["period"]
                if in_no_yoy_window(period, [brk]):
                    for measure in ("m_yoy", "ytd_yoy", "real_yoy"):
                        if is_number(obs.get(measure)):
                            findings.append(
                                Finding("gate_a.break_no_yoy", BLOCK, f"{measure} stored at {period}, inside no_yoy window [{effective}, {valid_from})", series_id=series_id, period=period, measure=measure)
                            )

            if effective and valid_from and valid_from > effective:
                restated = [o for o in observations if effective <= o["period"] < valid_from]
                if len(restated) < required:
                    seam_obs = next((o for o in observations if o["period"] == valid_from), None)
                    if seam_obs is not None:
                        for measure in ("m_yoy", "ytd_yoy", "real_yoy"):
                            if is_number(seam_obs.get(measure)):
                                findings.append(
                                    Finding(
                                        "gate_a.break_no_yoy", BLOCK,
                                        f"{measure} at yoy_valid_from={valid_from} bridges the break seam: only {len(restated)} restated periods exist (need {required})",
                                        series_id=series_id, period=valid_from, measure=measure,
                                    )
                                )
    if not evaluated:
        return make_result("gate_a.break_no_yoy", skipped=True, note="no touched series has a no_yoy_across break")
    return make_result("gate_a.break_no_yoy", findings)


# ---------------------------------------------------------------------------
# 19. gate_a.break_link
# ---------------------------------------------------------------------------


def check_break_link(ctx: GateContext):
    """gate_a.break_link -- BLOCK. A new-id break's superseded_by/supersedes
    pair must be symmetric and the old side of the pair must be frozen
    (`end` set). A missing counterpart file is only a WARN (the other agent
    in this same rebuild may simply not have created it yet)."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        for brk in data.get("breaks", []):
            superseded_by, supersedes = brk.get("superseded_by"), brk.get("supersedes")
            if not superseded_by and not supersedes:
                continue
            evaluated = True

            if superseded_by:
                if not data.get("end"):
                    findings.append(Finding("gate_a.break_link", BLOCK, f"break has superseded_by={superseded_by!r} but this series has no 'end' (not frozen)", series_id=series_id))
                counterpart = ctx.load(superseded_by)
                if counterpart is None:
                    findings.append(Finding("gate_a.break_link", WARN, f"superseded_by target {superseded_by!r} not found yet", series_id=series_id))
                elif not any(b.get("supersedes") == series_id for b in counterpart.get("breaks", [])):
                    findings.append(Finding("gate_a.break_link", BLOCK, f"{superseded_by} has no break.supersedes back to {series_id}", series_id=series_id))

            if supersedes:
                counterpart = ctx.load(supersedes)
                if counterpart is None:
                    findings.append(Finding("gate_a.break_link", WARN, f"supersedes target {supersedes!r} not found yet", series_id=series_id))
                else:
                    if not counterpart.get("end"):
                        findings.append(Finding("gate_a.break_link", BLOCK, f"{supersedes} is superseded by {series_id} but has no 'end' (not frozen)", series_id=series_id))
                    if not any(b.get("superseded_by") == series_id for b in counterpart.get("breaks", [])):
                        findings.append(Finding("gate_a.break_link", BLOCK, f"{supersedes} has no break.superseded_by forward to {series_id}", series_id=series_id))
    if not evaluated:
        return make_result("gate_a.break_link", skipped=True, note="no touched series has a new-id (supersedes/superseded_by) break")
    return make_result("gate_a.break_link", findings)


# ---------------------------------------------------------------------------
# Shared: "which revisions did THIS run add" (20 and 21 both need this)
# ---------------------------------------------------------------------------


def new_revisions_by_series(ctx: GateContext) -> dict[str, list[dict]]:
    if ctx.normalize_report is not None:
        out: dict[str, list[dict]] = defaultdict(list)
        for rev in ctx.normalize_report.revisions:
            out[rev["series_id"]].append(rev)
        return out
    out = defaultdict(list)
    for series_id, data in ctx.touched_series_dicts():
        for rev in data.get("revisions", []):
            if rev.get("source") == ctx.batch.release_id:
                out[series_id].append(rev)
    return out


# ---------------------------------------------------------------------------
# 20. gate_a.revision_flood
# ---------------------------------------------------------------------------


def check_revision_flood(ctx: GateContext):
    """gate_a.revision_flood -- BLOCK (needs-ack) when this run's revisions
    for a series exceed max_per_release or max_fraction of its observations
    (panels: more than panel_max_periods distinct periods revised). A
    known_disagreements config entry acknowledging the series demotes this to
    a WARN -- real benchmark revisions are expected to spike occasionally."""
    findings = []
    cfg = ctx.config.revision_flood()
    max_per_release = cfg.get("max_per_release", 6)
    max_fraction = cfg.get("max_fraction", 0.10)
    panel_max_periods = cfg.get("panel_max_periods", 3)
    evaluated = False

    for series_id, revisions in new_revisions_by_series(ctx).items():
        if not revisions:
            continue
        data = ctx.load(series_id)
        if data is None:
            continue
        evaluated = True
        ack = ctx.config.is_known_disagreement(series_id=series_id, period=None, check_id="gate_a.revision_flood")

        if data.get("schema") == "panel/v1":
            periods = {r["period"] for r in revisions}
            if len(periods) > panel_max_periods:
                severity = WARN if ack else BLOCK
                findings.append(
                    Finding(
                        "gate_a.revision_flood", severity,
                        f"panel revisions touch {len(periods)} periods this run (> panel_max_periods={panel_max_periods})" + (f"; acked: {ack.note}" if ack else ""),
                        series_id=series_id, needs_ack=not bool(ack),
                    )
                )
            continue

        total_obs = len(data.get("observations", []))
        fraction = (len(revisions) / total_obs) if total_obs else 1.0
        if len(revisions) > max_per_release or fraction > max_fraction:
            severity = WARN if ack else BLOCK
            findings.append(
                Finding(
                    "gate_a.revision_flood", severity,
                    f"{len(revisions)} revisions this run ({fraction:.1%} of {total_obs} observations) exceeds max_per_release={max_per_release}/max_fraction={max_fraction:.0%}"
                    + (f"; acked: {ack.note}" if ack else ""),
                    series_id=series_id, needs_ack=not bool(ack),
                )
            )
    if not evaluated:
        return make_result("gate_a.revision_flood", skipped=True, note="no revisions were added this run")
    return make_result("gate_a.revision_flood", findings)


# ---------------------------------------------------------------------------
# 21. gate_a.revision_integrity
# ---------------------------------------------------------------------------


def _count_equal(items: list[dict], target: dict) -> int:
    return sum(1 for item in items if item == target)


def check_revision_integrity(ctx: GateContext):
    """gate_a.revision_integrity -- BLOCK. Each revision this run added must
    satisfy: old == what was actually on file before this run; new == what
    the staged observation now holds; revised_on == today; and every
    pre-existing revision entry is still present in the staged log (appended,
    never rewritten)."""
    findings = []
    evaluated = False
    by_series = new_revisions_by_series(ctx)

    for series_id, revisions in by_series.items():
        if not revisions:
            continue
        evaluated = True
        staged = ctx.load(series_id)
        real = ctx.load_real(series_id)
        staged_by_period = {o["period"]: o for o in (staged.get("observations", []) if staged else [])}
        real_by_period = {o["period"]: o for o in (real.get("observations", []) if real else [])}

        for rev in revisions:
            period, measure = rev.get("period"), rev.get("measure")
            real_obs = real_by_period.get(period)
            real_value = real_obs.get(measure) if real_obs else None
            if real_obs is None or real_value != rev.get("old"):
                findings.append(
                    Finding("gate_a.revision_integrity", BLOCK, f"revision old={rev.get('old')!r} for {period}/{measure} does not match pre-run stored value {real_value!r}", series_id=series_id, period=period, measure=measure)
                )
            staged_obs = staged_by_period.get(period)
            staged_value = staged_obs.get(measure) if staged_obs else None
            if staged_obs is None or staged_value != rev.get("new"):
                findings.append(
                    Finding("gate_a.revision_integrity", BLOCK, f"revision new={rev.get('new')!r} for {period}/{measure} does not match the staged observation value {staged_value!r}", series_id=series_id, period=period, measure=measure)
                )
            if rev.get("revised_on") != ctx.today.isoformat():
                findings.append(
                    Finding("gate_a.revision_integrity", BLOCK, f"revision revised_on={rev.get('revised_on')!r} != today {ctx.today.isoformat()}", series_id=series_id, period=period, measure=measure)
                )

        real_revisions = real.get("revisions", []) if real else []
        staged_revisions = staged.get("revisions", []) if staged else []
        for old_rev in real_revisions:
            if _count_equal(staged_revisions, old_rev) < _count_equal(real_revisions, old_rev):
                findings.append(Finding("gate_a.revision_integrity", BLOCK, f"pre-existing revision entry {old_rev} is missing from the staged log -- rewritten, not appended", series_id=series_id))
                break
    if not evaluated:
        return make_result("gate_a.revision_integrity", skipped=True, note="no revisions were added this run")
    return make_result("gate_a.revision_integrity", findings)
