"""pipeline/schedule.py -- the polling brain for the GitHub Actions poller
(DATA-CONTRACT.md §11.2).

Usage:
    python -m pipeline.schedule --due       # newline-separated due source names (machine)
    python -m pipeline.schedule --explain   # same decision, human-readable, for every source
    python -m pipeline.schedule --due --date 2026-07-10   # override "today" (testing)

A source is "due" when BOTH hold, evaluated against an Asia/Shanghai calendar date:

  (a) today falls inside that source's release window (± a grace period), and
  (b) data/series/<id>.json's own observations for that source's representative
      series (the max period actually on file, scanned directly -- see
      _load_catalog_latest's docstring for why NOT data/catalog.json's own
      cached `latest` field) is older than the period the window implies
      should now be live.

Exit code is always 0 (this is a read-only planning step, not a gate -- gate
failures belong to pipeline.runner/pipeline.validate/pipeline.audit, which
this module knows nothing about). Output is intentionally allowed to be
empty: "nothing is due right now" is the expected steady state between
release windows, matching pipeline.runner's own "no new release" philosophy
(see runner.py's module docstring).

## Where the window numbers come from

The window table below is transcribed directly from the rebuild brief (day-
of-month ranges, Asia/Shanghai) and cross-checked against docs/ACQUISITION.md's
per-group release-day findings. `pipeline/config/release_calendar.yaml` is
DATA-CONTRACT.md's designated long-term home for these windows (§1's directory
layout, §11.2) but does not exist on disk yet -- a concurrent agent owns it in
this same rebuild wave. Until it lands, WINDOWS/SOURCES below are the literal
source of truth.

    TODO(release_calendar.yaml): once that file exists, replace the WINDOWS /
    SOURCES literals with a loader that reads it, keeping due_sources() and
    main()'s CLI surface unchanged so callers (this file's own tests, the
    update-data.yml workflow) don't need to change.

## Why "expected period" needs real date arithmetic, not day-of-month ints

A day-of-month upper bound of 31 does not mean "the 31st" -- PMI's release is
literally "the last calendar day of the month" (ACQUISITION.md Group 4), which
is the 28th in most Februaries. `_rule_window` below clamps any day_end >= 28
to that month's *actual* last day via `calendar.monthrange`, then adds the
grace period as a real `timedelta` on a real `date` object so a grace window
correctly spills into the next calendar month (e.g. Feb 28 + 3 days = Mar 3)
instead of silently comparing invalid or truncated day numbers.

## Why sources map to ONE representative catalog series, not many

Each source here mirrors one *release* (one discoverable article / API poll),
matching pipeline/runner.py's SOURCES dict, which is keyed the same way (one
entry per release type, not per series the release happens to populate). A
release that populates 20 series is still one due/not-due decision, so one
"headline" series per source is enough to answer "is this release's data
current" -- the max period actually on file in that series' own
data/series/<id>.json (all series populated by the same release share the
same release cadence and therefore the same latest period, barring a
genuinely partial parse, which is validate's job to catch, not schedule's).

## Why some source names below are commented out

`--due` must only ever emit sources `pipeline.runner` actually implements
(fixed 2026-07-08 -- see docs/OPERATIONS.md §6's former "known assumption"
about this). `pipeline.runner.SOURCES` implements exactly `nbs_cpi`,
`nbs_retail`, `pboc_money`, and `dg_refresh` as of this writing. The other
release-window entries this module's SOURCES list originally carried
(`nbs_ppi`, `nbs_pmi`, `customs_trade`, `nbs_iva`, `nbs_fai`, `nbs_70city`,
`nbs_gdp`, `nbs_income`) are commented out below rather than deleted -- the
window/cadence/series_id data the brief specified for each is still real and
still worth keeping on file, ready to uncomment the moment a real parser
lands for that source; a scheduled run touching one of them today would
otherwise file a **false** "Gate A blocked" issue (it's actually just "not
implemented yet"), which is exactly the false positive this fix removes.
`_ASSERT_SOURCES_MATCH_RUNNER` below is a belt-and-suspenders regression
guard: it fails loudly at import time if an ACTIVE entry here and
`pipeline.runner.SOURCES` ever drift apart again, in either direction.

Most of that DG-eligible list (nbs_iva/nbs_fai/nbs_gdp, plus nbs_ppi/nbs_pmi/
customs_trade) doesn't actually need its own future parser at all, in fact --
`pipeline.dg_refresh` (wired in as the `dg_refresh` source, see
DG_REFRESH_CHECKPOINTS below) already keeps every DG-sourced series current
without one. What's left genuinely waiting on a not-yet-written HTML parser
is just `nbs_70city` (the press-release-only 70-city panel) and `nbs_income`
(income/consumption, per docs/MIGRATION-MAP.md owned by the migration agent,
not a DG-refresh target).
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline.runner import SOURCES as _RUNNER_SOURCES

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover -- stdlib on Python 3.9+; defensive only
    ZoneInfo = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"
SHANGHAI_TZ_NAME = "Asia/Shanghai"
GRACE_DAYS = 3

# --- Release windows (day-of-month, Asia/Shanghai) -------------------------
# Each rule is (month_offset, day_start, day_end):
#   month_offset -- how many months after the REFERENCE period's own month
#                   the window falls (0 = same month as the data it reports,
#                   e.g. PMI; 1 = the month after, e.g. everything reporting
#                   "last month's" data).
#   day_start/day_end -- inclusive day-of-month bounds; day_end >= 28 means
#                   "through the end of the month" (see module docstring).
# Grace (GRACE_DAYS) extends day_end, applied uniformly per the brief ("grace
# +3 days each"), not the start.
WINDOWS: dict[str, list[tuple[int, int, int]]] = {
    "cpi_ppi": [(1, 9, 13)],
    "pmi": [(0, 25, 31), (1, 1, 1)],
    "trade": [(1, 7, 14)],
    "pboc_money": [(1, 10, 15)],
    "nbs_activity": [(1, 14, 18)],
    "spb_post": [(1, 10, 28)],
}

# Release month -> which quarter it reports, for the quarterly GDP/income
# sources that ride the nbs_activity day-of-month window but only in the
# four months following a quarter's end.
_QUARTERLY_GATING_MONTHS: tuple[int, ...] = (1, 4, 7, 10)
_QUARTERLY_REPORT_MONTH_TO_QUARTER: dict[int, int] = {1: 4, 4: 1, 7: 2, 10: 3}


@dataclass(frozen=True)
class SourceSpec:
    """One pollable release. `name` matches (or, for not-yet-implemented
    sources, will match) `python -m pipeline.runner --source <name>`."""

    name: str
    window_group: str  # key into WINDOWS
    cadence: str  # "monthly" or "quarterly"
    series_id: str  # data/catalog.json series id used as the freshness signal


SOURCES: list[SourceSpec] = [
    # -- implemented in pipeline/runner.py today --
    SourceSpec("nbs_cpi", "cpi_ppi", "monthly", "nbs-cpi-yoy"),
    SourceSpec("pboc_money", "pboc_money", "monthly", "pbc-m2"),
    SourceSpec("nbs_retail", "nbs_activity", "monthly", "nbs-retail-total"),
    SourceSpec("spb_express", "spb_post", "monthly", "spb-express-volume"),
    # dg_refresh isn't a SourceSpec -- it doesn't ride ONE window group (see
    # DG_REFRESH_CHECKPOINTS + _dg_refresh_due below, wired into due_sources()
    # separately). Still asserted against pipeline.runner.SOURCES below.
    #
    # -- NOT commented for a future parser, but for pipeline.dg_refresh instead:
    #    these three concepts are already kept current by dg_refresh (see the
    #    module docstring) and need no HTML parser of their own. Left here,
    #    inert, only as a historical record of the windows the rebuild brief
    #    specified; due_sources() never sees them (they're not in SOURCES).
    # SourceSpec("nbs_ppi", "cpi_ppi", "monthly", "nbs-ppi-yoy"),              # dg_refresh
    # SourceSpec("nbs_iva", "nbs_activity", "monthly", "nbs-industrial-va"),  # dg_refresh
    # SourceSpec("nbs_gdp", "nbs_activity", "quarterly", "nbs-gdp"),          # dg_refresh
    #
    # -- genuinely waiting on a not-yet-written parser --
    # SourceSpec("nbs_pmi", "pmi", "monthly", "cflp-pmi-mfg"),                # dg_refresh covers this too, in fact
    # SourceSpec("customs_trade", "trade", "monthly", "customs-exports-usd"), # dg_refresh covers this too, in fact
    # SourceSpec("nbs_fai", "nbs_activity", "monthly", "nbs-fai"),            # dg_refresh covers this too, in fact
    # SourceSpec("nbs_70city", "nbs_activity", "monthly", "nbs-70city-price"),
    # SourceSpec("nbs_income", "nbs_activity", "quarterly", "nbs-income-disposable"),
]

# Belt-and-suspenders regression guard (see module docstring): every ACTIVE
# entry above must name a source pipeline.runner.SOURCES actually implements,
# checked at import time so a future drift (either direction) fails loudly
# instead of silently reintroducing the false "Gate A blocked" bug this fix
# removed. dg_refresh is checked too even though it isn't a SourceSpec here.
for _spec in SOURCES:
    assert _spec.name in _RUNNER_SOURCES, (
        f"pipeline/schedule.py's SOURCES lists {_spec.name!r} as active, but pipeline.runner.SOURCES "
        f"doesn't implement it -- comment the entry out (see the module docstring) until a real "
        f"source lands, or add it to runner.py's SOURCES."
    )
assert "dg_refresh" in _RUNNER_SOURCES, "dg_refresh must stay in pipeline.runner.SOURCES for this module's own due-check to be meaningful"
del _spec


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return (year, month) `delta` months from (year, month); `month` is
    1-indexed. Handles negative delta and year rollover in both directions."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _rule_window(rule: tuple[int, int, int], ref_year: int, ref_month: int, grace: int) -> tuple[date, date]:
    """The real [start, end-with-grace] date range a monthly rule implies,
    for a candidate reference period (ref_year, ref_month)."""
    month_offset, day_start, day_end = rule
    win_year, win_month = _add_months(ref_year, ref_month, month_offset)
    last_day = _days_in_month(win_year, win_month)
    start = date(win_year, win_month, min(day_start, last_day))
    end_day = last_day if day_end >= 28 else min(day_end, last_day)
    end = date(win_year, win_month, end_day) + timedelta(days=grace)
    return start, end


def _fires_monthly(window_group: str, today: date, grace: int) -> tuple[int, int] | None:
    """The (year, month) reference period if `today` falls inside any rule
    of `window_group`, else None.

    Checks the current month and the two previous months as candidate
    reference periods for every rule. That's provably enough here: the
    largest month_offset in WINDOWS is 1 and grace is a few days, so no
    rule's [start, end+grace] window can reach more than one calendar month
    away from its own reference month in either direction. If more than one
    candidate fires (possible right at a month boundary), the most recent
    reference period wins -- "what should now be live," not "what used to
    be checked."
    """
    best: tuple[int, int] | None = None
    for rule in WINDOWS[window_group]:
        for delta in (0, -1, -2):
            ref_year, ref_month = _add_months(today.year, today.month, delta)
            start, end = _rule_window(rule, ref_year, ref_month, grace)
            if start <= today <= end:
                if best is None or (ref_year, ref_month) > best:
                    best = (ref_year, ref_month)
    return best


def _fires_quarterly(today: date, grace: int) -> str | None:
    """'YYYY-Qn' if `today` falls inside the nbs_activity day-of-month window
    AND today's month is one of the four quarterly-reporting months, else
    None. Quarterly windows never spill across a month boundary (max day +
    grace here is 18 + 3 = 21, comfortably inside any month), so no
    _add_months bookkeeping is needed the way _fires_monthly requires."""
    if today.month not in _QUARTERLY_GATING_MONTHS:
        return None
    _, day_start, day_end = WINDOWS["nbs_activity"][0]
    last_day = _days_in_month(today.year, today.month)
    start = date(today.year, today.month, min(day_start, last_day))
    end_day = last_day if day_end >= 28 else min(day_end, last_day)
    end = date(today.year, today.month, end_day) + timedelta(days=grace)
    if not (start <= today <= end):
        return None
    quarter = _QUARTERLY_REPORT_MONTH_TO_QUARTER[today.month]
    year = today.year - 1 if today.month == 1 else today.year
    return f"{year:04d}-Q{quarter}"


def _expected_period_if_due(spec: SourceSpec, today: date, grace: int = GRACE_DAYS) -> str | None:
    """The period (e.g. "2026-06" or "2026-Q2") that should now be
    `latest` if `spec` is inside its window today, else None if it's outside
    the window (in which case freshness isn't even checked -- see
    due_sources)."""
    if spec.cadence == "quarterly":
        return _fires_quarterly(today, grace)
    ref = _fires_monthly(spec.window_group, today, grace)
    if ref is None:
        return None
    return f"{ref[0]:04d}-{ref[1]:02d}"


DG_REFRESH_SOURCE_NAME = "dg_refresh"

# pipeline/dg_refresh.py isn't one release riding one window group -- it's
# nine DG-mirrored concepts riding FIVE different NBS/PBoC/CFLP/GACC release
# schedules (CPI/PPI, PMI, trade, PBoC money, the NBS activity batch). Rather
# than inventing a new hand-rolled day-of-month range for it (which would
# either miss a real window or be right only by coincidence -- CPI's 9-13,
# trade's 7-14, and PBoC money's 10-15 aren't shaped like "[14,18] union
# [1,3]"), dg_refresh is due whenever ANY window group that has at least one
# DG-sourced series is both in-window and stale -- reusing the EXISTING
# WINDOWS table and the exact same "in window AND stale" test every other
# source already uses, just OR'd across every group instead of checked
# against a single one. One representative (DG-sourced) series per group:
DG_REFRESH_CHECKPOINTS: list[tuple[str, str]] = [
    ("cpi_ppi", "nbs-cpi-yoy"),
    ("pmi", "cflp-pmi-mfg"),
    ("trade", "customs-exports-usd"),
    ("pboc_money", "pbc-m2"),
    ("nbs_activity", "nbs-industrial-va"),
]

# A SourceSpec purely so dg_refresh's due-ness can ride due_sources()'s
# existing (SourceSpec, expected, stored) tuple shape without changing it or
# main()'s CLI surface -- window_group/series_id here are for display only
# (_dg_refresh_due below, not _expected_period_if_due, does the real check).
_DG_REFRESH_SPEC = SourceSpec(DG_REFRESH_SOURCE_NAME, "dg_refresh*", "monthly", "")


def _dg_refresh_due(today: date, latest_by_series: dict[str, str], *, grace: int = GRACE_DAYS) -> tuple[str, str] | None:
    """(expected_period, checkpoint_series_id) for the first DG_REFRESH_CHECKPOINTS
    entry that's both in-window and stale on `today`, else None if none of
    them are. Order doesn't matter for --due (only None-or-not is used); the
    first hit is shown for --explain."""
    for window_group, series_id in DG_REFRESH_CHECKPOINTS:
        ref = _fires_monthly(window_group, today, grace)
        if ref is None:
            continue  # this concept's window isn't open today
        expected = f"{ref[0]:04d}-{ref[1]:02d}"
        stored = latest_by_series.get(series_id)
        if stored is not None and stored >= expected:
            continue  # this concept is already current
        return expected, series_id
    return None


def _series_latest_period(series_id: str) -> str | None:
    """The max period actually ON FILE in data/series/<series_id>.json's own
    observations[] -- scanned directly, not read from data/catalog.json's
    cached `latest` field (see _load_catalog_latest's docstring for why).
    None if the file is missing/unreadable/has no observations at all."""
    path = SERIES_DIR / f"{series_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    periods = [o["period"] for o in data.get("observations", []) if o.get("period")]
    return max(periods) if periods else None


def _load_catalog_latest() -> dict[str, str]:
    """series_id -> latest period actually on file, for exactly the series
    ids this module's SOURCES / DG_REFRESH_CHECKPOINTS reference (a handful
    of small files, cheap to scan fresh on every invocation -- not a full
    catalog walk). Kept under this name (not renamed) so due_sources()'s and
    _explain()'s call sites didn't need to change.

    CRITICAL bug fixed 2026-07-08 (adversarial review): this used to read
    data/catalog.json's own `latest` field per series -- but the RUNTIME
    write path (pipeline.normalize, called from both pipeline.runner and
    pipeline.dg_refresh) never advances that cached field; only
    pipeline.migrate's one-time historical-seed step does, and that never
    runs again after the initial rebuild. Practical consequence: the moment
    a source's release window opened, due_sources() would see it as due,
    fire it, and pipeline.runner would genuinely land fresh data into
    data/series/<id>.json -- but data/catalog.json's `latest` field for that
    id stayed exactly as stale as it always was, so the VERY NEXT scheduled
    run (two hours later, same day, same window) saw the exact same stale
    `latest` and fired the SAME source again -- every remaining cron tick of
    that source's entire release window, not just once. For dg_refresh
    specifically (whose own window is effectively open most days -- see
    DG_REFRESH_CHECKPOINTS) this meant up to ~56 redundant DG API requests
    every ~2 hours, hammering exactly the upstream API this project most
    needs to stay polite with. Reading data/series/ directly instead means
    due-ness always reflects what actually landed, the run immediately
    before included.
    """
    ids = {spec.series_id for spec in SOURCES if spec.series_id} | {series_id for _window_group, series_id in DG_REFRESH_CHECKPOINTS}
    latest: dict[str, str] = {}
    for series_id in ids:
        period = _series_latest_period(series_id)
        if period is not None:
            latest[series_id] = period
    return latest


def _shanghai_today() -> date:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(SHANGHAI_TZ_NAME)).date()
        except Exception:
            pass  # e.g. no tzdata installed -- fall through to the fixed-offset fallback
    # China does not observe DST, so a fixed UTC+8 offset is exact, not an
    # approximation. This path only matters on a runtime with no tzdata at
    # all; GitHub Actions' ubuntu-latest images always have it, so `--due`
    # in CI takes the ZoneInfo branch above.
    return (datetime.utcnow() + timedelta(hours=8)).date()


def due_sources(today: date | None = None, *, grace: int = GRACE_DAYS) -> list[tuple[SourceSpec, str, str | None]]:
    """(spec, expected_period, stored_latest) for every source that is both
    inside its release window on `today` and not yet current in data/.
    dg_refresh (not a plain SourceSpec -- see _dg_refresh_due) is appended
    using the same tuple shape whenever ANY of its own window groups fires."""
    if today is None:
        today = _shanghai_today()
    latest_by_series = _load_catalog_latest()
    results: list[tuple[SourceSpec, str, str | None]] = []
    for spec in SOURCES:
        expected = _expected_period_if_due(spec, today, grace)
        if expected is None:
            continue  # outside the window entirely -- not due regardless of freshness
        stored = latest_by_series.get(spec.series_id)
        if stored is not None and stored >= expected:
            continue  # already current
        results.append((spec, expected, stored))

    dg_due = _dg_refresh_due(today, latest_by_series, grace=grace)
    if dg_due is not None:
        expected, checkpoint_series_id = dg_due
        results.append((_DG_REFRESH_SPEC, expected, latest_by_series.get(checkpoint_series_id)))
    return results


def _explain(today: date, *, grace: int = GRACE_DAYS) -> None:
    latest_by_series = _load_catalog_latest()
    print(f"schedule check for {today.isoformat()} (Asia/Shanghai), grace={grace}d")
    for spec in SOURCES:
        expected = _expected_period_if_due(spec, today, grace)
        if expected is None:
            print(f"  {spec.name:16s} [{spec.window_group:12s}] outside window today -- not due")
            continue
        stored = latest_by_series.get(spec.series_id)
        due = stored is None or stored < expected
        verdict = "DUE" if due else "in window, already current"
        print(
            f"  {spec.name:16s} [{spec.window_group:12s}] in window -- expected {expected}, "
            f"stored {stored!r} ({spec.series_id}) -> {verdict}"
        )

    dg_due = _dg_refresh_due(today, latest_by_series, grace=grace)
    if dg_due is None:
        print(f"  {DG_REFRESH_SOURCE_NAME:16s} [{'(multi)':12s}] outside every DG-window today -- not due")
    else:
        expected, checkpoint_series_id = dg_due
        stored = latest_by_series.get(checkpoint_series_id)
        print(
            f"  {DG_REFRESH_SOURCE_NAME:16s} [{'(multi)':12s}] in window -- expected {expected}, "
            f"stored {stored!r} ({checkpoint_series_id}) -> DUE"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="China consumer dashboard release-window poller (which sources are due right now)"
    )
    parser.add_argument(
        "--due", action="store_true", help="print due source names, one per line (default action)"
    )
    parser.add_argument(
        "--explain", action="store_true", help="human-readable reasoning for every source, not just due ones"
    )
    parser.add_argument(
        "--date", help="override 'today' as YYYY-MM-DD, Asia/Shanghai (default: real current date)"
    )
    parser.add_argument(
        "--grace", type=int, default=GRACE_DAYS, help=f"grace days appended to each window's close (default: {GRACE_DAYS})"
    )
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.date) if args.date else _shanghai_today()

    if args.explain:
        _explain(today, grace=args.grace)
        return 0

    # --due is the documented/expected invocation; bare `python -m
    # pipeline.schedule` degrades to the same machine-readable behavior
    # rather than printing nothing, so a workflow that forgets the flag
    # still gets a usable list instead of silence.
    for spec, _expected, _stored in due_sources(today, grace=args.grace):
        print(spec.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
