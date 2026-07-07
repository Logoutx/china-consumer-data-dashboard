"""Loads pipeline/config/release_calendar.yaml for gate_b.freshness -- natively,
as of 2026-07-08. Falls back to a small embedded per-agency table only when the
file is genuinely absent, unreadable, or empty; every other real-world state of
the file is now understood directly (see "schema" below).

gate_b.freshness only ever needs a coarse "has this gone suspiciously stale"
signal per Tier-1 series (see lag_budget_for's docstring): agency -> (expected
lag days, grace days). The real pipeline/config/release_calendar.yaml that
landed during this rebuild, however, is keyed by release CONCEPT ("cpi_ppi",
"pbc_money", "nbs_activity", ...), each entry `{window_days: [a,b], freq,
grace_days}` OR `{lag_days, freq, grace_days}` -- a day-of-month/lag-window
shape built for pipeline/validate's ingest-time `gate_a.calendar_window` check
(a different question, "did this fetch land inside its expected window", not
"has the currently-deployed observation gone stale"). It was never going to be
agency-keyed; that was this module's own earlier guess at a schema that turned
out not to be how the concurrent workstream actually shaped the file.

`load_release_calendar` no longer tries to detect "is this the schema I
expected" and fall back when it isn't -- there is exactly one real schema now,
and it's the concept-keyed one. It trusts any non-empty parseable mapping
verbatim (`used_fallback=False`), whatever shape its *entries* take; only a
missing/unparseable/empty file falls back. `lag_budget_for` is what actually
bridges the two shapes: `AGENCY_TO_CALENDAR_KEYS` maps an agency to the
concept key(s) it publishes under in the real file, and picks the MOST LENIENT
(max) budget among them -- consistent with this module's pre-existing
"over-estimate lag" bias (a false 'stale' WARN is cheap; a missed one isn't).
An agency with no entry in that map (or whose mapped keys aren't present in
whatever was loaded) falls back to the per-agency FALLBACK_RELEASE_CALENDAR
default, independently of whether the real file loaded overall -- so a gap in
the bridge table degrades gracefully rather than crashing or mis-reporting.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# Fallback/default numbers, used (a) whenever pipeline/config/release_calendar.yaml
# is absent, unparseable, or empty, and (b) as the per-agency default inside
# lag_budget_for whenever an agency isn't covered by AGENCY_TO_CALENDAR_KEYS (or
# none of its mapped concept keys are present in whatever calendar WAS loaded).
# Numbers are deliberately generous (over-estimate lag, under-estimate urgency)
# since a false "stale" WARN is cheap but a missed one hides a real pipeline
# outage less than a genuinely broken build would -- and this check never
# blocks deploy regardless (task spec: freshness lag is a WARN, not a BLOCK,
# because it means "the world moved on", not "our data is wrong").
#
# `expected_lag_days` is the agency's typical PUBLICATION lag after a period's
# own end (e.g. NBS retail for month M publishes ~15 days into M+1); it is
# NOT itself the freshness budget -- lag_budget_for() adds the period's own
# length (a monthly print is superseded in ~30 days, a quarterly one in ~91,
# an annual one in ~365) before adding `grace_days`, so a quarterly series
# isn't held to a monthly-cadence budget just because both publish "~20 days
# after period end" (caught empirically: nbs-income-disposable/nbs-gdp, both
# freq=="Q", were flagged "stale" at 99 days under a flat 40-60 day budget
# even though a quarterly print is legitimately still the latest for ~90 of
# those days before the NEXT quarter's print is even due).
FALLBACK_RELEASE_CALENDAR: dict[str, dict[str, int]] = {
    "nbs": {"expected_lag_days": 20, "grace_days": 20},
    "pbc": {"expected_lag_days": 20, "grace_days": 20},
    "mof": {"expected_lag_days": 25, "grace_days": 20},
    "mohurd": {"expected_lag_days": 25, "grace_days": 20},
    "customs": {"expected_lag_days": 20, "grace_days": 20},
    "caam": {"expected_lag_days": 20, "grace_days": 20},
    "safe": {"expected_lag_days": 25, "grace_days": 20},
    "cflp": {"expected_lag_days": 10, "grace_days": 15},
}
DEFAULT_ENTRY = {"expected_lag_days": 30, "grace_days": 30}

_PERIOD_LENGTH_DAYS = {"M": 30, "Q": 91, "A": 365}

# agency (data/catalog.json series[].source.agency) -> the real release_calendar.yaml
# concept key(s) that agency publishes under. Deliberately agency-grained, not
# series-grained (see module docstring) -- an agency with more than one key here
# gets the MOST LENIENT of them (see lag_budget_for), which is the same "honest
# minimum, don't hand-tune ~90 individual dates" philosophy this module already
# used for FALLBACK_RELEASE_CALENDAR. Agencies absent here (mof, mohurd, caam,
# safe) have no matching concept in the real file today -- they simply fall
# through to FALLBACK_RELEASE_CALENDAR's per-agency default inside
# lag_budget_for, same as if the file were missing.
AGENCY_TO_CALENDAR_KEYS: dict[str, list[str]] = {
    "nbs": ["cpi_ppi", "nbs_activity"],
    "pbc": ["pbc_money", "lpr"],
    "cflp": ["pmi"],
    "customs": ["trade"],
}


def load_release_calendar(config_path: Path) -> tuple[dict, bool]:
    """(calendar, used_fallback). Reads and trusts the real file whenever it
    exists, parses as YAML, and yields a non-empty mapping -- regardless of
    what shape its entries take (see module docstring: there is only one real
    schema now, so there is nothing left to "detect"). Falls back
    (used_fallback=True, calendar==FALLBACK_RELEASE_CALENDAR) only if the file
    is absent, malformed, or empty -- a freshness check must never itself
    crash the audit."""
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict) and data:
            return data, False
    return dict(FALLBACK_RELEASE_CALENDAR), True


def _agency_shaped_entry(calendar: dict, agency: str) -> tuple[int, int] | None:
    """If `calendar[agency]` itself looks like this module's own
    {expected_lag_days, grace_days} shape, use it directly -- covers both the
    FALLBACK_RELEASE_CALENDAR dict and a hand-authored agency-keyed override
    file, if anyone ever writes one. Returns None (not this shape) for the
    real concept-keyed release_calendar.yaml, since no agency name is ever a
    top-level key there."""
    entry = calendar.get(agency)
    if not isinstance(entry, dict):
        return None
    if "expected_lag_days" not in entry and "grace_days" not in entry:
        return None
    return (
        int(entry.get("expected_lag_days", DEFAULT_ENTRY["expected_lag_days"])),
        int(entry.get("grace_days", DEFAULT_ENTRY["grace_days"])),
    )


def _concept_keyed_budget(calendar: dict, agency: str) -> tuple[int, int] | None:
    """Resolve (expected_lag_days, grace_days) via AGENCY_TO_CALENDAR_KEYS
    against a concept-keyed calendar (the real release_calendar.yaml shape):
    `lag_days` is already exactly "days after period end", usable as-is;
    `window_days`'s upper bound is itself close enough to that same quantity
    (the window is expressed as days INTO the month after the reference
    period, i.e. days-after-period-end by construction) to reuse directly
    without a separate unit. Picks the single MOST LENIENT (largest total)
    entry among every key the agency maps to, per this module's "over-estimate
    lag" bias. Returns None if the agency isn't in the map, or maps to no key
    actually present in `calendar`."""
    best: tuple[int, int] | None = None
    for key in AGENCY_TO_CALENDAR_KEYS.get(agency, []):
        entry = calendar.get(key)
        if not isinstance(entry, dict):
            continue
        if entry.get("lag_days") is not None:
            expected_lag = int(entry["lag_days"])
        elif entry.get("window_days"):
            expected_lag = int(entry["window_days"][1])
        else:
            continue
        grace = int(entry.get("grace_days", 0))
        if best is None or (expected_lag + grace) > (best[0] + best[1]):
            best = (expected_lag, grace)
    return best


def lag_budget_for(calendar: dict[str, dict[str, int]], agency: str, freq: str = "M") -> int:
    resolved = (
        _agency_shaped_entry(calendar, agency)
        or _concept_keyed_budget(calendar, agency)
        or _agency_shaped_entry(FALLBACK_RELEASE_CALENDAR, agency)
        or (DEFAULT_ENTRY["expected_lag_days"], DEFAULT_ENTRY["grace_days"])
    )
    expected_lag, grace = resolved
    period_length = _PERIOD_LENGTH_DAYS.get(freq, _PERIOD_LENGTH_DAYS["M"])
    return period_length + expected_lag + grace
