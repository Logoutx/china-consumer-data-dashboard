"""pipeline/build.py — build stage: data/ -> site-data/ (DATA-CONTRACT §10).

Usage:
    python -m pipeline.build [--out site-data/] [--data data/]

Reads data/catalog.json + data/series/<id>.json + data/panels/<id>.json +
data/annotations.json (optional -- treated as {} if the file doesn't exist yet)
and emits:

    site-data/index.json                  landing tiles + a lightweight
                                           freshness index (see _build_index)
    site-data/sections/<section-id>.json  one bundle per catalog section
    site-data/panels/<panel-id>.json      lazy-loaded panel bundle(s)

All heavy math (YoY extraction, latest/prev resolution, streaks, takeaway
prose) happens here, at build time, so the client renders without recomputing
anything caliber-sensitive (§10.2). The actual takeaway sentences come from
pipeline/takeaways.py; this module's job is entirely about assembling the
right *facts* to hand that module (which caliber to headline, what counts as
"previous" across a Jan-Feb print / a YTD year-reset / a break seam, and the
streak history) -- see takeaways.py's module docstring for that split.

IMPORTANT: this module is tested exclusively against synthetic fixtures under
pipeline/tests/fixtures/build/ -- data/series/, data/panels/, and data/
catalog.json are being written by concurrent agents in this same rebuild this
wave and are deliberately never read here except through an explicit --data
override in a test's tmp_path.

Determinism (DATA-CONTRACT §9's idempotence requirement, restated for the
build stage): `generated_at` in every emitted file is a *passthrough* of the
input catalog's own `generated_at`, never `datetime.now()` -- using wall-clock
time here would make two back-to-back builds of the same unchanged input
non-byte-identical for no reason. The one deliberately time-relative field is
`revisions_recent` / the `break_recent` flag, which are genuinely defined
relative to "today" (an `as_of` date, defaulting to date.today() but
injectable for tests) -- that is a property of *what the field means*, not an
accident of using the wrong clock.

Period FORMAT is not the same thing as a series' declared `freq` (hard lesson
from the first real-build run): several freq=="Q" income series carry a
2013-2016 annual-supplement layer whose observations are bare "YYYY" periods,
not "YYYY-Qn" (pipeline/migrate/REPORT.md item 6). Every function that parses
a period string (_period_label_zh, _resolve_prev, _prev_ytd_period, the
streak history) dispatches on `_period_shape(period)` -- the string's own
literal shape -- never on `entry["freq"]`. Even the `freq` passed into
takeaways.py's TakeawayInput is shape-derived (_SHAPE_TO_FREQ_LETTER of
`latest`'s own shape), not `entry["freq"]` verbatim, so the "较上月/上季度/
上年" word choice stays correct for a same-shape comparison regardless of what
the series is nominally declared as. `entry["freq"]` itself is still read, but
only for the bundle's own `"freq"` metadata field exposed to the client (a
description of the series, not of any single period string).

`observations[]` is not guaranteed to be ascending by period, despite DATA-
CONTRACT §9's invariant -- `_resolve_prev`'s array-adjacent fallback therefore
never trusts raw array position -- it sorts same-shape period STRINGS and
finds the neighbor that way, which is correct regardless of how the array
happens to be ordered on disk. This defensive stance is deliberately kept even
though its original trigger is now fixed: 10 real income/consumption series
used to physically place their bare-"YYYY" annual-supplement row AFTER the
quarterly rows for the same year (e.g. nbs-income-disposable had "...,2016-
Q1,2016-Q2,2016-Q3,2016,2017-Q1,..."), traced to a sort-key bug in
pipeline/migrate/migrate.py's `period_sort_key` (fixed 2026-07-08 -- see
pipeline/migrate/REPORT.md's addendum and DATA-CONTRACT §9). Sorting by
period string rather than array position costs nothing and removes any
future dependency on migrate.py (or a future agent's re-migration) getting
that ordering right on disk.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from pipeline.takeaways import (
    LevelTakeawayInput,
    TakeawayInput,
    choose_verb,
    compute_level_streak,
    compute_streak,
    generate_level_takeaway,
    generate_takeaway,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_OUT_DIR = ROOT / "site-data"

_MEASURE_KEYS = ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")
_YOY_LIKE_KEYS = {"m_yoy", "ytd_yoy", "real_yoy"}
_MEASURE_FIELDS = {"single": ("m", "m_yoy"), "ytd": ("ytd", "ytd_yoy")}


# -- I/O --------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_with_retry(path: Path, *, retries: int = 1, delay_seconds: float = 0.2) -> tuple[dict | None, str | None]:
    """Read+parse a JSON file, retrying once on failure before giving up.

    pipeline/backfill/ writes data/series/ (and potentially data/panels/)
    concurrently with this build running -- a file caught mid-write is a
    transient state (truncated/invalid JSON bytes), not a real error. One
    retry after a short pause resolves the common case; a file that's STILL
    unparseable after that is genuinely broken (or backfill is stuck on it)
    and must not be allowed to take down the whole build -- the caller skips
    it and surfaces a loud warning instead. Returns (parsed_dict, None) on
    success or (None, error_message) after exhausting retries. Scoped to
    read/parse failures only (json.JSONDecodeError, OSError) -- a downstream
    processing bug (KeyError, etc.) is a real defect and must still raise.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _load_json(path), None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(delay_seconds)
    return None, f"{path}: {last_error}"


def _resolve_file_path(data_dir: Path, file_field: str) -> Path:
    """catalog entries' `file` is documented (catalog.schema.json) as "repo-
    relative", e.g. "data/series/nbs-retail-total.json" -- but --data points
    at the data/ directory itself, matching every other pipeline stage's
    ROOT/"data" convention (runner.py's SERIES_DIR, etc.). Strip a redundant
    leading "data/" segment if present so a literal repo-relative path
    resolves correctly against --data, while this module's own test fixtures
    can just write data_dir-relative paths ("series/x.json") directly without
    needing to nest an extra data/ folder inside the fixture tree."""
    if file_field.startswith("data/"):
        file_field = file_field[len("data/") :]
    return data_dir / file_field


# -- period helpers -----------------------------------------------------------------


def _resolve_caliber(calibers: list[str]) -> str:
    return "single" if "single" in calibers else "ytd"


_ANNUAL_PERIOD_RE = re.compile(r"^\d{4}$")


def _period_shape(period: str) -> str:
    """Classify a period STRING by its own literal format -- "annual"
    ("YYYY"), "quarterly" ("YYYY-Qn"), or "monthly" ("YYYY-MM"). Every period-
    format-sensitive function below (labels, prev-resolution, streaks) MUST
    dispatch on this, never on a series' declared `freq`. Real migrated data
    proved the two can disagree: several freq=="Q" income series carry a
    2013-2016 annual-supplement layer whose observations have bare "YYYY"
    periods (pipeline/migrate/REPORT.md item 6) -- a freq-driven dispatch
    crashes on those (`"2016".split("-Q")` has nothing to unpack), and even
    where it wouldn't crash outright it would silently mislabel or mis-compare
    them. Shape-based dispatch is correct regardless of which agent's data
    model changes underneath."""
    if _ANNUAL_PERIOD_RE.fullmatch(period):
        return "annual"
    if "-Q" in period:
        return "quarterly"
    return "monthly"


_SHAPE_TO_FREQ_LETTER = {"annual": "A", "quarterly": "Q", "monthly": "M"}


def _month_num(period: str) -> int | None:
    if _period_shape(period) != "monthly":
        return None
    return int(period.split("-")[1])


_QUARTER_ORDINAL_ZH = {1: "一", 2: "二", 3: "三", 4: "四"}


def _period_label_zh(period: str, *, caliber: str, span: int = 1) -> str:
    """Human period label per DATA-CONTRACT §12, dispatched on the period
    STRING's own shape (see _period_shape) -- NOT on a series' declared freq,
    which real data has shown can disagree within a single series (annual-
    supplement rows inside an otherwise quarterly series). An annual-shaped
    period always renders "{YYYY} 年全年" ("full year"), whether it comes from
    a genuinely annual series or is a stray annual row mixed into quarterly/
    monthly data -- the label needs to read unambiguously either way, and
    "全年" is accurate and no worse for a "pure" annual series either.

    Quarters render with the conventional Chinese ordinal ("2026 年二季度"),
    matching §12's own worked example -- a deliberate *exception* to the
    "numerals are Arabic" rule (§12 point 2), treated the same way as that
    rule's own carve-out for closed conventional sets (colloquial small
    numbers, idioms): a quarter is always one of exactly four values, so
    "二季度" is unambiguous the way spelling out an arbitrary count would not
    be. An earlier draft rendered this as "2026 年 2 季度" (Arabic digit)
    under a stricter reading of point 2; reverted 2026-07-08.

    Cumulative/Jan-Feb spans use the half-width hyphen ("2026 年 1-5 月"),
    per the owner's global range-typesetting rule (also §12) -- this now
    matches pipeline/takeaways.py's own "1-{M} 月" YTD-only sentence anchor,
    which always used the hyphen; the two had briefly disagreed (this
    function used an em dash) before the 2026-07-08 unification.
    """
    shape = _period_shape(period)
    if shape == "annual":
        return f"{period} 年全年"
    if shape == "quarterly":
        year, q = period.split("-Q")
        return f"{year} 年{_QUARTER_ORDINAL_ZH[int(q)]}季度"
    year, month_str = period.split("-")
    month = int(month_str)
    if span > 1 or caliber == "ytd":
        return f"{year} 年 1-{month} 月"
    return f"{year} 年 {month} 月"


def _prev_ytd_period(period: str, shape: str) -> str | None:
    """The same-year, one-period-earlier YTD anchor -- None at the year's
    first cumulative print (monthly: month<=2, since a YTD-only series never
    publishes a standalone January; quarterly: Q1). YTD resets every January,
    so this is a calendar-anchored lookup, not array-adjacency. `shape` is the
    CONFIRMED shape of `period` (from _period_shape, checked by the caller) --
    "quarterly" or "monthly" only. Annual data has no intra-year cumulative-
    reset concept, so annual ytd-caliber series never reach this function;
    they fall through to plain (same-shape-guarded) array-adjacency in
    _resolve_prev instead."""
    if shape == "quarterly":
        year, q_str = period.split("-Q")
        q = int(q_str)
        return None if q <= 1 else f"{year}-Q{q - 1}"
    year, month_str = period.split("-")
    month = int(month_str)
    return None if month <= 2 else f"{year}-{month - 1:02d}"


# -- breaks --------------------------------------------------------------------


def _in_no_yoy_window(period: str, breaks: list[dict]) -> bool:
    """DATA-CONTRACT §4.2's build-time invariant: no YoY value may be stored or
    rendered across a no_yoy_across break seam. Re-checked independently here
    (mirrors normalize.py's own ingest-time check) rather than trusted from
    upstream -- each pipeline stage re-verifying the same invariant is this
    repo's existing validate/audit "gate #1 / gate #2" philosophy, applied
    once more at the build stage."""
    for brk in breaks:
        if not brk.get("no_yoy_across"):
            continue
        effective = brk.get("effective")
        if not effective or period < effective:
            continue
        valid_from = brk.get("yoy_valid_from")
        if valid_from is not None and period >= valid_from:
            continue
        return True
    return False


def _safe_yoy(obs: dict | None, yoy_field: str, breaks: list[dict]) -> float | None:
    if obs is None:
        return None
    value = obs.get(yoy_field)
    if value is not None and _in_no_yoy_window(obs["period"], breaks):
        return None
    return value


def _resolve_prev(
    observations: list[dict], index_by_period: dict, latest: dict, *, caliber: str, breaks: list[dict]
) -> dict | None:
    """The "correct comparable prior period" per DATA-CONTRACT §10.2, dispatched
    on `latest`'s own period SHAPE (_period_shape), never on a series' declared
    freq -- see _period_shape's docstring for why (real freq=="Q" income series
    carry bare-"YYYY" annual-supplement rows).

      - Jan-Feb print: prev is last year's Jan-Feb print (12 months back), not
        array-adjacent December -- spans don't match (2 vs 1), so a naive
        array lookup would compare incompatible aggregates.
      - YTD caliber, monthly- or quarterly-shaped `latest`: prev is the same-
        year, one-period-earlier cumulative print, found by exact calendar
        lookup (not array-adjacency) -- a genuine data gap must produce None
        here rather than silently comparing against a cumulative window of
        the wrong width. Annual-shaped `latest` has no intra-year reset
        concept, so it skips this and falls through to array-adjacency below.
      - Otherwise: the chronologically-previous SAME-SHAPE observation, found
        by sorting period STRINGS -- not by trusting observations[]'s array
        position. Real migrated data proved this matters: several of the same
        freq=="Q" income series with a bare-"YYYY" annual-supplement layer
        also have that layer physically OUT of chronological order in the
        array (the "2016" row sits *after* "2016-Q1/Q2/Q3", not before --
        DATA-CONTRACT §9 says observations should be "ascending by period",
        but the migrated data doesn't yet honor that for these splice points).
        A quarterly latest whose chronologically-nearest same-shape
        predecessor is one of the annual-supplement rows must never be
        treated as "last quarter" either way (comparisons only between same-
        format periods); it's simply not comparable, so prev is None there,
        same as a genuine missing-history case.

    Then, regardless of which branch resolved a candidate: if that candidate
    sits on the *other side* of a no_yoy_across break from `latest`, the
    comparison is walled off entirely (return None) -- "never compare across".
    """
    period = latest["period"]
    shape = _period_shape(period)
    flags = latest.get("flags", [])

    if "jan_feb" in flags:
        year = int(period[:4])
        candidate = index_by_period.get(f"{year - 1}-02")
        prev = candidate if candidate and "jan_feb" in candidate.get("flags", []) else None
    elif caliber == "ytd" and shape in ("monthly", "quarterly"):
        target = _prev_ytd_period(period, shape)
        prev = index_by_period.get(target) if target else None
    else:
        same_shape_periods = sorted(obs["period"] for obs in observations if _period_shape(obs["period"]) == shape)
        idx = same_shape_periods.index(period)
        prev_period = same_shape_periods[idx - 1] if idx > 0 else None
        prev = index_by_period.get(prev_period) if prev_period is not None else None

    if prev is not None:
        for brk in breaks:
            if brk.get("no_yoy_across") and prev["period"] < brk.get("effective", "") <= period:
                return None
    return prev


def _is_break_first(latest: dict, prev: dict | None, yp: float | None, breaks: list[dict]) -> bool:
    """True for the takeaway's "break-adjacent" case: either upstream
    explicitly tagged this observation as the first of a new id (schema flag
    `break_first`, used for the new-id break case -- e.g. the ex-student youth
    unemployment series' very first print, which has no earlier data at all to
    derive this from), OR -- the same-id rebase case (e.g. CPI) -- this period
    has a real YoY value but its natural predecessor observation exists and
    its YoY was suppressed specifically because it fell inside a
    no_yoy_across window. The second condition deliberately fires on the
    *first period where a post-break YoY becomes available again*
    (yoy_valid_from), not on the break's effective month itself -- that month
    typically has no YoY at all yet (blocked), so there would be nothing for
    the "{name}{verb} {y}%" template to render anyway.
    """
    if "break_first" in latest.get("flags", []):
        return True
    return yp is None and prev is not None and _in_no_yoy_window(prev["period"], breaks)


# -- revisions / recency flags --------------------------------------------------


def _recent_revisions(revisions: list[dict], as_of: date, window_days: int = 90) -> list[dict]:
    cutoff = as_of - timedelta(days=window_days)
    recent = []
    for rev in revisions:
        revised_on = rev.get("revised_on")
        if not revised_on:
            continue
        try:
            revised_date = date.fromisoformat(revised_on)
        except ValueError:
            continue
        if revised_date >= cutoff:
            recent.append({"period": rev["period"], "measure": rev["measure"], "revised_on": revised_on})
    return recent


def _to_year_month(period: str) -> tuple[int, int]:
    if "-Q" in period:
        year, q = period.split("-Q")
        return int(year), (int(q) - 1) * 3 + 1
    parts = period.split("-")
    return int(parts[0]), (int(parts[1]) if len(parts) > 1 else 1)


def _months_between(earlier: str, later: str) -> int:
    y1, m1 = _to_year_month(earlier)
    y2, m2 = _to_year_month(later)
    return (y2 - y1) * 12 + (m2 - m1)


def _break_recent(period: str, breaks: list[dict], as_of: date, window_months: int = 12) -> bool:
    """Unlike revisions_recent's explicit 90-day window (task spec), DATA-
    CONTRACT does not give a number for how long a break stays "recent" enough
    to badge (§10.2 just lists `break_recent` as an example flag). 12 months
    is a judgment call made here -- "still worth a tooltip a year on" -- and
    flagged back to the lead rather than silently invented."""
    as_of_period = as_of.strftime("%Y-%m")
    for brk in breaks:
        effective = brk.get("effective")
        if not effective:
            continue
        months_since = _months_between(effective, as_of_period)
        if 0 <= months_since <= window_months:
            return True
    return False


def _flags_latest(latest: dict, entry: dict, breaks: list[dict], as_of: date) -> list[str]:
    flags = []
    if "jan_feb" in latest.get("flags", []):
        flags.append("jan_feb")
    if entry.get("derived"):
        flags.append("derived")
    if _break_recent(latest["period"], breaks, as_of):
        flags.append("break_recent")
    return flags


# -- series-level array builders ------------------------------------------------


def _measure_block(obs: dict, period_label_zh: str, breaks: list[dict]) -> dict:
    block = {"period": obs["period"], "period_label_zh": period_label_zh}
    blocked = _in_no_yoy_window(obs["period"], breaks)
    for key in _MEASURE_KEYS:
        if key not in obs:
            continue
        if key in _YOY_LIKE_KEYS and blocked:
            continue  # never render a YoY-shaped value across a no_yoy_across seam
        block[key] = obs[key]
    return block


def _build_yoy_series(observations: list[dict], yoy_field: str, breaks: list[dict]) -> list[dict]:
    """One entry per existing observation (no synthesis of periods that have
    no observation object at all -- see build.py's design notes in the final
    report). `yoy` is None both where the source never published the measure
    and where a no_yoy_across break blocks it -- either way, the chart line
    gets exactly the gap DATA-CONTRACT §10.2 asks for."""
    out = []
    for obs in observations:
        value = obs.get(yoy_field)
        if value is not None and _in_no_yoy_window(obs["period"], breaks):
            value = None
        out.append({"period": obs["period"], "yoy": value})
    return out


def _build_level_series(observations: list[dict], value_field: str) -> list[dict]:
    return [{"period": obs["period"], "m": obs.get(value_field)} for obs in observations]


def _build_spark(level_series: list[dict], n: int = 24) -> list:
    """Downsampled last-N points for the tile sparkline. N=24 (2 years of
    monthly data) is a reasonable default for a small inline chart -- not
    specified numerically by the contract, so this is a judgment call."""
    return [pt["m"] for pt in level_series[-n:]]


def _annotations_for(series_id: str, annotations: dict) -> list[dict]:
    entry_notes = annotations.get(series_id)
    if not entry_notes:
        return []
    out = [{"period": None, **note} for note in entry_notes.get("_series", [])]
    for period in sorted(k for k in entry_notes if k != "_series"):
        out.extend({"period": period, **note} for note in entry_notes[period])
    return out


# -- name_short / source / decimals (DATA-CONTRACT §10.2) -----------------------


def _headline_name(entry: dict) -> str:
    """The name pipeline/takeaways.py renders into a headline sentence: the
    catalog's optional compact `name_short` ("CPI", "M2", "制造业 PMI", ...)
    when present, else the full `name_zh`. Centralized here so every takeaway
    path (sign-matrix, quarterly-income, YTD-only, break-first, level-only)
    resolves the name identically -- see catalog.schema.json's `name_short`."""
    return entry.get("name_short") or entry["name_zh"]


def _bundle_source(entry: dict) -> dict:
    """Per-series `source` object for the bundle (§10.2): the publishing
    agency's Chinese name (catalog `source.agency_zh`) plus the release URL
    when the catalog has one. The site consumes both fields defensively
    already (optional), so a catalog entry that somehow still lacks
    `agency_zh` degrades to an empty string rather than a KeyError."""
    src = entry.get("source") or {}
    out = {"agency_zh": src.get("agency_zh") or ""}
    if src.get("url"):
        out["url"] = src["url"]
    return out


def _decimal_places(value: float) -> int:
    """The number of decimal places `value` actually carries, capped at 4 --
    mirrors takeaways.py's own _fmt() rounding-noise guard (round-trip through
    round() rather than string-parsing, so 5.10 and 5.1 agree)."""
    value = round(value, 6)
    for dp in range(0, 4):
        if round(value, dp) == value:
            return dp
    return 4


def _infer_decimals(observations: list[dict], value_field: str) -> int:
    """Fallback display precision when the catalog doesn't declare `decimals`
    (§10.2): the maximum precision actually used across this series' own
    values for the headline measure. A pure inference from the data actually
    present -- never a guess, never overriding an explicit catalog value.
    Defaults to 1 (the NBS-headline norm; matches takeaways.py's own default)
    when the series has no values to infer from at all."""
    seen = [obs[value_field] for obs in observations if obs.get(value_field) is not None]
    if not seen:
        return 1
    return max(_decimal_places(v) for v in seen)


def _bundle_decimals(entry: dict, observations: list[dict], value_field: str) -> int:
    if "decimals" in entry:
        return entry["decimals"]
    return _infer_decimals(observations, value_field)


_ALL_YOY_FIELDS = ("m_yoy", "ytd_yoy", "real_yoy")
_LEVEL_ONLY_VALUE_TYPES = {"index", "ratio", "rate_pct"}

# value_type -> (fall_word, delta_in_pp) for generate_level_takeaway. Every
# entry here PRESERVES the original PMI/GDP-contribution wording ("回落",
# "个点") except rate_pct (2026-07-08 widening, the surveyed-unemployment
# family): a RATE declining reads as "下降", and its month-over-month change
# is conventionally a "个百分点" (percentage point), not a diffusion index's
# "个点". Absent entries (e.g. a future value_type reusing this template)
# fall back to the PMI-era default via .get(...)'s second argument below.
_LEVEL_WORDING_BY_VALUE_TYPE = {
    "rate_pct": ("下降", True),
}


def _is_level_only_series(entry: dict, observations: list[dict]) -> bool:
    """True for a series whose headline caliber has a real "level"-shaped
    reading but never publishes a same-caliber YoY at all -- a genuine
    diffusion index (PMI and similar), a share/contribution-rate reading
    (GDP's three 贡献率 components), or a surveyed rate with no YoY concept at
    all (the 城镇调查失业率 family: nbs-urban-unemp, -31city, -youth-1624(-
    exstudent) -- widened 2026-07-08). Two conditions, both required:

      1. `value_type` is `"index"` (CPI/PPI/PMI's own catalog shape, a same-
         month-comparison index), `"ratio"` (GDP-contribution's shape, a
         point-in-time share of GDP growth), or `"rate_pct"` (a surveyed
         rate) -- the "opt in via catalog value_type" the task asks for.
         `_build_series_entry` disables the level-only template's 荣枯线
         (boom-bust line) clause for everything except `"index"` -- that
         clause is meaningful only for a genuine diffusion index, never for a
         GDP-contribution share or an unemployment rate.
      2. The series never carries ANY YoY-shaped measure (`m_yoy`, `ytd_yoy`,
         `real_yoy`) anywhere in its history -- a structural absence, not a
         transient/break-blocked gap. A genuinely YoY-capable series that is
         merely mid-break (CPI, PPI) still has `m_yoy` present on *other*
         observations, so this correctly returns False for it and it keeps
         getting `takeaway: null` during the blocked window instead of a
         fabricated level-only sentence. (No real rate_pct series in the
         current catalog publishes a YoY/pp-change field at all, but a future
         one that did would correctly keep getting `takeaway: null` here too,
         same as CPI mid-break.)

    Both conditions matter: condition 1 alone would also catch every CPI/PPI
    sub-index (all `value_type=="index"`, but they DO carry `m_yoy`, so
    condition 2 excludes them); condition 2 alone -- checking only "no YoY
    measure anywhere" without the value_type gate -- would also catch several
    series that structurally never populate a YoY field for unrelated reasons
    and must NOT get a "为 X，比上月...个点，位于荣枯线..." sentence: `nbs-gdp`
    (a currency level with `real_yoy` on close inspection, but no `m_yoy` --
    caught while testing this function, see pipeline/tests/test_build.py) and
    the two `nbs-70city-*-up-count` city counts. `nbs-industrial-va` and
    `nbs-fai` (whose only-ever-populated field already IS a yoy_pct value) are
    excluded a different way -- see `_yoy_only_populated_field` -- they never
    reach this function with `y is None` in the first place once that fix
    applies, because their `y` resolves to a real value instead."""
    if entry.get("value_type") not in _LEVEL_ONLY_VALUE_TYPES:
        return False
    return not any(any(f in obs for f in _ALL_YOY_FIELDS) for obs in observations)


_YOY_ONLY_VALUE_TYPES = {"yoy_pct"}


def _yoy_only_populated_field(value_field: str, yoy_field: str, observations: list[dict]) -> str | None:
    """For a `value_type=="yoy_pct"` series (FAI, industrial value added): NBS
    publishes no absolute level for these concepts at all, ever -- only a
    growth rate (see pipeline/backfill/backfill.py's build_fai/build_iva
    `coverage_note_zh`). Which JSON key that rate happens to land under still
    follows the ordinary single/ytd caliber convention, but which HALF of the
    pair (`value_field` or `yoy_field`) actually holds it differs by series:
    industrial-va's rate is under "m"/"ytd" -- the caliber's own VALUE slot,
    because NBS does publish a distinct monthly print for it; FAI's is under
    "ytd_yoy" -- the caliber's YOY slot, because FAI structurally only ever
    gets a cumulative YTD print with no separate monthly reading to tell
    "value" apart from "yoy" in the first place. Detects empirically which
    slot actually has data (scanning real observations, most recent first)
    rather than hardcoding per-id, so a third series shaped like this needs
    no build.py change at all -- only the right catalog `value_type`. Returns
    None if genuinely nothing is populated in either slot (no data at all
    yet)."""
    for obs in reversed(observations):
        if obs.get(value_field) is not None:
            return value_field
        if obs.get(yoy_field) is not None:
            return yoy_field
    return None


# -- per-series bundle entry -----------------------------------------------------


def _empty_series_entry(
    entry: dict, observations: list[dict], breaks: list[dict], yoy_field: str, value_field: str, annotations: dict, *, plot_kind: str = "level"
) -> dict:
    return {
        "id": entry["id"],
        "name_zh": entry["name_zh"],
        "name_en": entry["name_en"],
        "name_short": entry.get("name_short"),
        "unit_zh": entry["unit_zh"],
        "value_type": entry["value_type"],
        "freq": entry["freq"],
        "tier": entry.get("tier", 3),
        "calibers": entry["calibers"],
        "source": _bundle_source(entry),
        "decimals": _bundle_decimals(entry, observations, value_field),
        "latest": None,
        "prev": None,
        "headline": None,
        "takeaway": None,
        "plot_kind": plot_kind,
        "yoy_series": _build_yoy_series(observations, yoy_field, breaks),
        "level_series": _build_level_series(observations, value_field),
        "spark": [],
        "breaks": breaks,
        "annotations": _annotations_for(entry["id"], annotations),
        "flags_latest": [],
        "revisions_recent": [],
    }


def _build_series_entry(entry: dict, series: dict, annotations: dict, as_of: date) -> dict:
    calibers = entry["calibers"]
    caliber = _resolve_caliber(calibers)
    value_field, yoy_field = _MEASURE_FIELDS[caliber]

    observations = series.get("observations", [])
    breaks = series.get("breaks", [])
    index_by_period = {obs["period"]: obs for obs in observations}

    # A value_type=="yoy_pct" series (FAI, industrial value added) never
    # publishes an absolute level at all -- see _yoy_only_populated_field's
    # docstring. Point BOTH value_field/yoy_field at whichever slot actually
    # has the data so every downstream step (latest-detection, y/yp, decimals,
    # yoy_series/level_series) just works unmodified; `plot_kind` records that
    # this bundle's "level" IS a growth rate, for the client to render it like
    # one (a "yoy" line, %-formatted) instead of expecting a separate,
    # structurally-always-empty yoy_series.
    plot_kind = "level"
    if entry.get("value_type") in _YOY_ONLY_VALUE_TYPES:
        populated = _yoy_only_populated_field(value_field, yoy_field, observations)
        if populated is not None:
            value_field = yoy_field = populated
            plot_kind = "yoy"

    # Scan backward for the last observation actually carrying the headline
    # caliber's value -- not simply observations[-1], in case the newest
    # array entry only landed a different measure first.
    latest = next((obs for obs in reversed(observations) if obs.get(value_field) is not None), None)

    if latest is None:
        return _empty_series_entry(entry, observations, breaks, yoy_field, value_field, annotations, plot_kind=plot_kind)

    latest_shape = _period_shape(latest["period"])
    # The "1-{M} 月...累计" phrasing is inherently monthly (M is a month
    # number). Real catalog data includes ~20 quarterly series with
    # calibers==["ytd"] and no real_yoy (income/consumption sub-components),
    # PLUS an annual-supplement layer wedged into some of those same series
    # (pipeline/migrate/REPORT.md item 6) -- so this is gated on the LATEST
    # observation's own period shape, not the series' declared freq: neither
    # a quarterly nor an annual latest period has a month number to plug in.
    # They fall through to the plain sign-matrix template instead, which
    # takeaways.py picks a shape/freq-correct "previous period" word for.
    is_ytd_only = latest_shape == "monthly" and "single" not in calibers and "ytd" in calibers

    prev = _resolve_prev(observations, index_by_period, latest, caliber=caliber, breaks=breaks)
    y = _safe_yoy(latest, yoy_field, breaks)
    yp = _safe_yoy(prev, yoy_field, breaks)
    is_break_first = _is_break_first(latest, prev, yp, breaks)

    latest_label = _period_label_zh(latest["period"], caliber=caliber, span=latest.get("span", 1))

    # takeaways.py's `freq` is the CONFIRMED comparison cadence, not the
    # series' nominal declared freq -- _resolve_prev already guarantees `prev`
    # (when not None) shares `latest`'s shape, so this mapping is always a
    # legitimate description of what's actually being compared (e.g. an
    # annual-supplement observation inside a nominally quarterly series
    # correctly gets "A", so takeaways.py says "较上年", never "较上季度").
    takeaway_freq = _SHAPE_TO_FREQ_LETTER[latest_shape]

    # Streaks are an explicitly monthly narrative (compute_streak's "sign_down"
    # / "delta_*" months). Gate on the LATEST period's own shape and
    # additionally null out any history entry whose period isn't monthly-
    # shaped -- belt-and-suspenders against the same annual-supplement-layer
    # issue contaminating a delta computed across a shape seam (an annual
    # YoY% and a monthly YoY% are not adjacent, comparable data points even
    # though both are plain floats and would subtract fine without error).
    streak_n, streak_kind = 0, None
    if latest_shape == "monthly" and y is not None and not is_break_first:
        history = [
            pt["yoy"] if _period_shape(pt["period"]) == "monthly" else None
            for pt in _build_yoy_series(observations, yoy_field, breaks)
        ]
        streak_n, streak_kind = compute_streak(history)

    name_for_takeaway = _headline_name(entry)

    takeaway = None
    if y is not None:
        takeaway_input = TakeawayInput(
            name_zh=name_for_takeaway,
            verb=choose_verb(entry),
            period_label_zh=latest_label,
            latest_yoy=y,
            prev_yoy=yp,
            real_yoy=_safe_yoy(latest, "real_yoy", breaks),
            freq=takeaway_freq,
            is_jan_feb="jan_feb" in latest.get("flags", []),
            is_break_first=is_break_first,
            is_ytd_only=is_ytd_only,
            ytd_month=_month_num(latest["period"]) if is_ytd_only else None,
            streak=streak_n,
            streak_kind=streak_kind,
        )
        takeaway = generate_takeaway(takeaway_input)
    elif caliber == "single" and _is_level_only_series(entry, observations):
        # No published YoY anywhere in this series' history (e.g. PMI/any
        # diffusion index, a GDP-contribution share, or a surveyed rate like
        # unemployment) -- fall back to the level-only "为 X，比上月上升/回落
        # D 个点[，位于荣枯线...]" template (takeaways.py) instead of leaving
        # the takeaway blank forever. Scoped to caliber=="single"
        # (value_field=="m"): that is the real shape of every no-YoY series
        # in the current catalog; a future ytd-only, no-YoY series would need
        # this extended. The 荣枯线 (boom-bust line) clause only makes sense
        # for a genuine diffusion index -- disabled for everything else
        # (a contribution share or an unemployment rate crossing 50 means
        # nothing), per _is_level_only_series's docstring.
        #
        # The level streak ("，连续 N 个月上升/下降") is a DIFFERENT concept
        # from streak_n/streak_kind above (which scans YoY values and stayed
        # 0/None here since `y is None` skipped that loop) -- it scans the
        # LEVEL itself, and is currently only requested for rate_pct
        # (unemployment wiggles too much month to month for index/ratio to
        # want this; PMI keeps its 荣枯线 clause instead). Same monthly-shape
        # + None-padding discipline as the YoY streak history above.
        level_streak_n, level_streak_kind = 0, None
        if entry.get("value_type") == "rate_pct" and latest_shape == "monthly":
            level_history = [
                pt["m"] if _period_shape(pt["period"]) == "monthly" else None
                for pt in _build_level_series(observations, value_field)
            ]
            level_streak_n, level_streak_kind = compute_level_streak(level_history)
        fall_word, delta_in_pp = _LEVEL_WORDING_BY_VALUE_TYPE.get(entry.get("value_type"), ("回落", False))
        level_input = LevelTakeawayInput(
            name_zh=name_for_takeaway,
            period_label_zh=latest_label,
            latest_level=latest[value_field],
            prev_level=prev.get(value_field) if prev is not None else None,
            is_percent_unit=entry.get("unit_zh") == "%",
            freq=takeaway_freq,
            fall_word=fall_word,
            delta_in_pp=delta_in_pp,
            streak=level_streak_n,
            streak_kind=level_streak_kind,
            **({"boom_bust_line": None} if entry.get("value_type") != "index" else {}),
        )
        takeaway = generate_level_takeaway(level_input)

    if y is None:
        direction = None  # e.g. latest observation exists but its YoY is blocked by a break -- "unknown", not "flat"
    elif y > 0:
        direction = "up"
    elif y < 0:
        direction = "down"
    else:
        direction = "flat"
    delta_pp = round(y - yp, 6) if (y is not None and yp is not None) else None

    prev_block = None
    if prev is not None:
        prev_label = _period_label_zh(prev["period"], caliber=caliber, span=prev.get("span", 1))
        prev_block = _measure_block(prev, prev_label, breaks)

    return {
        "id": entry["id"],
        "name_zh": entry["name_zh"],
        "name_en": entry["name_en"],
        "name_short": entry.get("name_short"),
        "unit_zh": entry["unit_zh"],
        "value_type": entry["value_type"],
        "freq": entry["freq"],
        "tier": entry.get("tier", 3),
        "calibers": calibers,
        "source": _bundle_source(entry),
        "decimals": _bundle_decimals(entry, observations, value_field),
        "latest": _measure_block(latest, latest_label, breaks),
        "prev": prev_block,
        "headline": {
            "caliber": caliber,
            "direction": direction,
            "latest_yoy": y,
            "delta_pp_vs_prev": delta_pp,
            "streak": streak_n,
            "period_label_zh": latest_label,
        },
        "takeaway": takeaway,
        "plot_kind": plot_kind,
        "yoy_series": _build_yoy_series(observations, yoy_field, breaks),
        "level_series": _build_level_series(observations, value_field),
        "spark": _build_spark(_build_level_series(observations, value_field)),
        "breaks": breaks,
        "annotations": _annotations_for(entry["id"], annotations),
        "flags_latest": _flags_latest(latest, entry, breaks, as_of),
        "revisions_recent": _recent_revisions(series.get("revisions", []), as_of),
    }


# -- section / index / panel bundles --------------------------------------------


def _build_section_bundle(
    section_id: str, entries: list[dict], series_by_id: dict, annotations: dict, catalog_version: str, generated_at: str, as_of: date
) -> dict:
    series_out = [_build_series_entry(entry, series_by_id[entry["id"]], annotations, as_of) for entry in entries]
    return {
        "section": section_id,
        "generated_at": generated_at,
        "catalog_version": catalog_version,
        "series": series_out,
    }


def _build_index(catalog: dict, section_bundles: dict, generated_at: str, as_of: date) -> dict:
    """Superset of DATA-CONTRACT §10.1's "tier-1 landing tiles": the task's own
    brief additionally asked for "catalog summary + latest-value blocks per
    series + freshness metadata", which is broader than the contract's
    tier-1-only framing. Resolved as: `tiles` is exactly the contract's
    tier-1, full-block landing data; `sections` + `freshness` is a genuinely
    lightweight (no yoy_series/level_series) per-series index covering every
    series, satisfying the task brief without bloating the "loaded once on
    first paint" file the contract cares about keeping small.
    """
    sections = [{"id": s["id"], "name_zh": s["name_zh"], "name_en": s["name_en"], "order": s["order"]} for s in catalog["sections"]]

    tiles = []
    freshness = []
    for entry in catalog["series"]:
        if entry.get("panel"):
            continue
        bundle = section_bundles.get(entry["section"])
        if bundle is None:
            continue
        bundle_entry = next((s for s in bundle["series"] if s["id"] == entry["id"]), None)
        if bundle_entry is None:
            continue
        freshness.append(
            {
                "id": entry["id"],
                "latest": bundle_entry["latest"]["period"] if bundle_entry["latest"] else None,
                "revisions_recent": bundle_entry["revisions_recent"],
            }
        )
        if entry.get("tier") == 1:
            tiles.append({k: v for k, v in bundle_entry.items() if k not in ("yoy_series", "level_series")})

    return {
        "generated_at": generated_at,
        "catalog_version": catalog["version"],
        "sections": sections,
        "tiles": tiles,
        "freshness": freshness,
    }


def _build_panel_bundle(panel: dict) -> dict:
    """Mirror the panel file (§5) into a render-ready bundle: same fields,
    plus pre-computed national aggregates (mean across the outer dimension,
    e.g. city) and per-city latest cells for the grid (§10.2's closing
    paragraph). Scoped to the one documented panel shape -- exactly two
    dimensions, an outer entity dimension and an inner metric dimension,
    matching every worked example in DATA-CONTRACT §5. A third dimension would
    need a genuinely different aggregation design, not a speculative
    generalization here."""
    dim_names = list(panel["dimensions"].keys())
    if len(dim_names) != 2:
        raise ValueError(f"_build_panel_bundle only supports 2-dimensional panels (outer, metric); got dims={dim_names!r}")
    outer_dim, metric_dim = dim_names
    outer_values = panel["dimensions"][outer_dim]
    metrics = panel["dimensions"][metric_dim]
    periods = panel["periods"]
    measures = panel["measures"]
    cells = panel["cells"]
    n = len(periods)

    national_aggregate: dict = {}
    up_count: dict = {}
    for metric in metrics:
        national_aggregate[metric] = {}
        for measure in measures:
            series = [cells.get(o, {}).get(metric, {}).get(measure, [None] * n) for o in outer_values]
            means = []
            for i in range(n):
                vals = [s[i] for s in series if i < len(s) and s[i] is not None]
                means.append(round(sum(vals) / len(vals), 4) if vals else None)
            national_aggregate[metric][measure] = means
        if "m" in measures:
            m_series = [cells.get(o, {}).get(metric, {}).get("m", [None] * n) for o in outer_values]
            counts = []
            for i in range(n):
                vals = [s[i] for s in m_series if i < len(s) and s[i] is not None]
                counts.append(sum(1 for v in vals if v > 0))
            up_count[metric] = counts

    latest_by_outer: dict = {}
    for o in outer_values:
        latest_by_outer[o] = {}
        for metric in metrics:
            latest_by_outer[o][metric] = {
                measure: (cells.get(o, {}).get(metric, {}).get(measure, [None] * n) or [None] * n)[-1] if n else None
                for measure in measures
            }

    return {
        "schema": panel["schema"],
        "id": panel["id"],
        "name_zh": panel["name_zh"],
        "name_en": panel["name_en"],
        "value_type": panel["value_type"],
        "freq": panel["freq"],
        "dimensions": panel["dimensions"],
        "measures": measures,
        "periods": periods,
        "cells": cells,
        "national_aggregate": national_aggregate,
        "up_count": up_count,
        f"latest_by_{outer_dim}": latest_by_outer,
        "breaks": panel.get("breaks", []),
        "generated_at": panel["generated_at"],
    }


# -- top-level orchestration -----------------------------------------------------


@dataclass
class BuildReport:
    sections: int
    series: int
    panels: int
    tiles: int
    skipped: list[str] = field(default_factory=list)  # series/panel ids skipped after a transient read failure


def build_site_data(data_dir: Path, out_dir: Path, *, as_of: date | None = None) -> BuildReport:
    as_of = as_of or date.today()
    catalog = _load_json(data_dir / "catalog.json")  # not retried: a different agent's manifest, not backfill's concern
    annotations_path = data_dir / "annotations.json"
    annotations = _load_json(annotations_path) if annotations_path.exists() else {}
    generated_at = catalog["generated_at"]  # deterministic passthrough -- see module docstring

    series_by_id: dict[str, dict] = {}
    panel_entries: list[dict] = []
    skipped: list[str] = []
    for entry in catalog["series"]:
        if entry.get("panel"):
            panel_entries.append(entry)
            continue
        # pipeline/backfill/ writes data/series/ concurrently with this build
        # (this wave) -- a file caught mid-write is transient, not a defect.
        # Retry once, then skip (with a loud warning) rather than let one
        # in-flight write take down the whole build.
        series, error = _load_json_with_retry(_resolve_file_path(data_dir, entry["file"]))
        if series is None:
            print(f"[build] WARNING: skipping series {entry['id']!r}, failed to read after retry: {error}", file=sys.stderr)
            skipped.append(entry["id"])
            continue
        series_by_id[entry["id"]] = series

    section_bundles: dict[str, dict] = {}
    for section in catalog["sections"]:
        entries = [
            e for e in catalog["series"] if e["section"] == section["id"] and not e.get("panel") and e["id"] in series_by_id
        ]
        section_bundles[section["id"]] = _build_section_bundle(
            section["id"], entries, series_by_id, annotations, catalog["version"], generated_at, as_of
        )

    index = _build_index(catalog, section_bundles, generated_at, as_of)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "index.json", index)

    sections_dir = out_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    for section_id, bundle in section_bundles.items():
        _write_json(sections_dir / f"{section_id}.json", bundle)

    panels_written = 0
    if panel_entries:
        panels_dir = out_dir / "panels"
        panels_dir.mkdir(parents=True, exist_ok=True)
        for entry in panel_entries:
            panel, error = _load_json_with_retry(_resolve_file_path(data_dir, entry["file"]))
            if panel is None:
                print(f"[build] WARNING: skipping panel {entry['id']!r}, failed to read after retry: {error}", file=sys.stderr)
                skipped.append(entry["id"])
                continue
            _write_json(panels_dir / f"{entry['id']}.json", _build_panel_bundle(panel))
            panels_written += 1

    if skipped:
        print(f"[build] WARNING: {len(skipped)} id(s) skipped due to unreadable source file(s): {sorted(skipped)}", file=sys.stderr)

    total_series = sum(len(bundle["series"]) for bundle in section_bundles.values())
    return BuildReport(
        sections=len(section_bundles), series=total_series, panels=panels_written, tiles=len(index["tiles"]), skipped=sorted(skipped)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build site-data/ bundles from data/ (DATA-CONTRACT §10)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output directory (default: site-data/)")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR, help="input data/ directory (default: data/)")
    args = parser.parse_args(argv)

    report = build_site_data(args.data, args.out)
    print(
        f"[build] wrote {report.sections} section bundle(s), {report.series} series, "
        f"{report.panels} panel bundle(s), {report.tiles} tier-1 tile(s) -> {args.out}"
    )
    if report.skipped:
        print(f"[build] {len(report.skipped)} id(s) skipped (unreadable after retry): {report.skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
