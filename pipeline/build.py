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
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pipeline.takeaways import TakeawayInput, choose_verb, compute_streak, generate_takeaway

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


def _month_num(period: str) -> int | None:
    if "-Q" in period or "-" not in period:
        return None
    year, month = period.split("-")
    return int(month)


def _period_label_zh(period: str, *, freq: str, caliber: str, span: int = 1) -> str:
    """Human period label per DATA-CONTRACT §12 (Arabic numerals throughout).

    Lead decision, called out explicitly per the task spec: quarters render
    with an Arabic digit ("2026 年 2 季度"), not the conventional NBS prose
    "二季度" -- the house typography rule (Arabic numerals, always) wins here
    even though it reads slightly unusually to a Chinese business-press eye.

    Note this is a *different* convention from pipeline/takeaways.py's own
    "1-{M} 月" YTD-only sentence prefix (plain hyphen, no year) -- that is
    deliberate, not an inconsistency; see takeaways.py's module docstring.
    """
    if freq == "A":
        return f"{period} 年"
    if freq == "Q":
        year, q = period.split("-Q")
        return f"{year} 年 {q} 季度"
    year, month_str = period.split("-")
    month = int(month_str)
    if span > 1 or caliber == "ytd":
        return f"{year} 年 1—{month} 月"
    return f"{year} 年 {month} 月"


def _prev_ytd_period(period: str, freq: str) -> str | None:
    """The same-year, one-period-earlier YTD anchor -- None at the year's
    first cumulative print (monthly: month<=2, since a YTD-only series never
    publishes a standalone January; quarterly: Q1). YTD resets every January,
    so this is a calendar-anchored lookup, not array-adjacency. Only ever
    called for freq in ("M", "Q") -- annual data has no intra-year cumulative-
    reset concept, so annual ytd-caliber series fall through to plain array-
    adjacency in _resolve_prev instead of reaching this function at all."""
    if freq == "Q":
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
    observations: list[dict], index_by_period: dict, latest: dict, *, caliber: str, freq: str, breaks: list[dict]
) -> dict | None:
    """The "correct comparable prior period" per DATA-CONTRACT §10.2:

      - Jan-Feb print: prev is last year's Jan-Feb print (12 months back), not
        array-adjacent December -- spans don't match (2 vs 1), so a naive
        array lookup would compare incompatible aggregates.
      - YTD caliber, monthly or quarterly: prev is the same-year, one-period-
        earlier cumulative print, found by exact calendar lookup (not array-
        adjacency) -- a genuine data gap must produce None here rather than
        silently comparing against a cumulative window of the wrong width.
        Annual ytd-caliber series have no intra-year reset concept, so they
        skip this and fall through to plain array-adjacency below.
      - Otherwise: array-adjacent previous observation.

    Then, regardless of which branch resolved a candidate: if that candidate
    sits on the *other side* of a no_yoy_across break from `latest`, the
    comparison is walled off entirely (return None) -- "never compare across".
    """
    period = latest["period"]
    flags = latest.get("flags", [])

    if "jan_feb" in flags:
        year = int(period[:4])
        candidate = index_by_period.get(f"{year - 1}-02")
        prev = candidate if candidate and "jan_feb" in candidate.get("flags", []) else None
    elif caliber == "ytd" and freq in ("M", "Q"):
        target = _prev_ytd_period(period, freq)
        prev = index_by_period.get(target) if target else None
    else:
        pos = next(i for i, obs in enumerate(observations) if obs["period"] == period)
        prev = observations[pos - 1] if pos > 0 else None

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


# -- per-series bundle entry -----------------------------------------------------


def _empty_series_entry(entry: dict, observations: list[dict], breaks: list[dict], yoy_field: str, value_field: str, annotations: dict) -> dict:
    return {
        "id": entry["id"],
        "name_zh": entry["name_zh"],
        "name_en": entry["name_en"],
        "unit_zh": entry["unit_zh"],
        "value_type": entry["value_type"],
        "freq": entry["freq"],
        "tier": entry.get("tier", 3),
        "calibers": entry["calibers"],
        "latest": None,
        "prev": None,
        "headline": None,
        "takeaway": None,
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
    # The "1-{M} 月...累计" phrasing is inherently monthly (M is a month
    # number). Real catalog data includes ~20 quarterly series with
    # calibers==["ytd"] and no real_yoy (income/consumption sub-components) --
    # those must NOT take this branch (there is no month number to plug in);
    # they fall through to the plain sign-matrix template instead, which
    # takeaways.py picks a freq-correct "previous period" word for.
    is_ytd_only = entry["freq"] == "M" and "single" not in calibers and "ytd" in calibers
    value_field, yoy_field = _MEASURE_FIELDS[caliber]

    observations = series.get("observations", [])
    breaks = series.get("breaks", [])
    index_by_period = {obs["period"]: obs for obs in observations}

    # Scan backward for the last observation actually carrying the headline
    # caliber's value -- not simply observations[-1], in case the newest
    # array entry only landed a different measure first.
    latest = next((obs for obs in reversed(observations) if obs.get(value_field) is not None), None)

    if latest is None:
        return _empty_series_entry(entry, observations, breaks, yoy_field, value_field, annotations)

    prev = _resolve_prev(observations, index_by_period, latest, caliber=caliber, freq=entry["freq"], breaks=breaks)
    y = _safe_yoy(latest, yoy_field, breaks)
    yp = _safe_yoy(prev, yoy_field, breaks)
    is_break_first = _is_break_first(latest, prev, yp, breaks)

    latest_label = _period_label_zh(latest["period"], freq=entry["freq"], caliber=caliber, span=latest.get("span", 1))

    streak_n, streak_kind = 0, None
    if entry["freq"] == "M" and y is not None and not is_break_first:
        history = [pt["yoy"] for pt in _build_yoy_series(observations, yoy_field, breaks)]
        streak_n, streak_kind = compute_streak(history)

    takeaway = None
    if y is not None:
        takeaway_input = TakeawayInput(
            name_zh=entry["name_zh"],
            verb=choose_verb(entry),
            period_label_zh=latest_label,
            latest_yoy=y,
            prev_yoy=yp,
            real_yoy=_safe_yoy(latest, "real_yoy", breaks),
            freq=entry["freq"],
            is_jan_feb="jan_feb" in latest.get("flags", []),
            is_break_first=is_break_first,
            is_ytd_only=is_ytd_only,
            ytd_month=_month_num(latest["period"]) if is_ytd_only else None,
            streak=streak_n,
            streak_kind=streak_kind,
        )
        takeaway = generate_takeaway(takeaway_input)

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
        prev_label = _period_label_zh(prev["period"], freq=entry["freq"], caliber=caliber, span=prev.get("span", 1))
        prev_block = _measure_block(prev, prev_label, breaks)

    return {
        "id": entry["id"],
        "name_zh": entry["name_zh"],
        "name_en": entry["name_en"],
        "unit_zh": entry["unit_zh"],
        "value_type": entry["value_type"],
        "freq": entry["freq"],
        "tier": entry.get("tier", 3),
        "calibers": calibers,
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


def build_site_data(data_dir: Path, out_dir: Path, *, as_of: date | None = None) -> BuildReport:
    as_of = as_of or date.today()
    catalog = _load_json(data_dir / "catalog.json")
    annotations_path = data_dir / "annotations.json"
    annotations = _load_json(annotations_path) if annotations_path.exists() else {}
    generated_at = catalog["generated_at"]  # deterministic passthrough -- see module docstring

    series_by_id: dict[str, dict] = {}
    panel_entries: list[dict] = []
    for entry in catalog["series"]:
        if entry.get("panel"):
            panel_entries.append(entry)
        else:
            series_by_id[entry["id"]] = _load_json(_resolve_file_path(data_dir, entry["file"]))

    section_bundles: dict[str, dict] = {}
    for section in catalog["sections"]:
        entries = [e for e in catalog["series"] if e["section"] == section["id"] and not e.get("panel")]
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
            panel = _load_json(_resolve_file_path(data_dir, entry["file"]))
            _write_json(panels_dir / f"{entry['id']}.json", _build_panel_bundle(panel))
            panels_written += 1

    total_series = sum(len(bundle["series"]) for bundle in section_bundles.values())
    return BuildReport(sections=len(section_bundles), series=total_series, panels=panels_written, tiles=len(index["tiles"]))


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
