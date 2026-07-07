"""Indexes and looks up `data/archive/dg/*.json` -- the raw NBS DG-portal
captures written by `pipeline/backfill/dg_client.py` (indicators_*.json listing
files + values_*.json per-indicator value files; see that module's docstring
for the wire protocol). This module only ever reads these two file families as
plain JSON -- it does not import pipeline.backfill (or anything else outside
the standard library / this package).

Two ways a sampled (series, period) value gets matched against this archive:

1. **Provenance fast path** — DATA-CONTRACT §3.2's `src` observation field is a
   first-class, documented part of the data model (not an implementation
   detail of pipeline.normalize): a DG-sourced observation carries
   `"src": "dg:<indicator_id>"`. Reading it is reading data, not importing
   normalize's code. `resolve_src_indicator_id` extracts the id;
   `lookup_value` then reads the archived value for that exact indicator+period
   directly.
2. **Label fallback** — for observations without a usable `src`, or to
   independently corroborate the fast path, `find_candidate_indicator_ids`
   fuzzy-searches the indicator LISTING files (indicators_*.json) by Chinese
   name (from labels.yaml) and returns every indicator id whose name matches,
   so a caller can check the archived value of *any* plausibly-matching
   indicator at that period.

Either way, the raw archived value ("v", a string like "103.6" or "-0.6") is
compared against a series' stored measures under BOTH a same-unit hypothesis
and an index-base-100 hypothesis (`candidate_transforms`) -- empirically, some
DG indicators are raw levels/percentages (GDP levels, industrial-VA YoY%) and
some are "same-period-last-year=100" index numbers whose YoY% is index-100
(confirmed against real data: data/series/nbs-cpi-yoy.json's m=101.2 for
2026-05 equals the archived "上年同月=100" indicator's raw v=101.2 directly,
while its m_yoy=1.2 equals v-100). Trying both hypotheses generically, rather
than hand-classifying which of the 26 DG-sourced series uses which, is more
robust than a per-series lookup table that could itself be wrong.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.audit.kernel import close_enough, compact_text, numeric, severity_for_mismatch
from pipeline.audit.labels import label_candidates
from pipeline.audit.models import Finding

_SRC_RE = re.compile(r"^dg:([0-9a-f]{32})$")


@dataclass
class DGArchiveIndex:
    # indicator_id -> {period: raw "v" string}, most-recently-fetched snapshot wins
    values_by_indicator: dict[str, dict[str, str]] = field(default_factory=dict)
    # indicator_id -> {_name, i_showname, dp_name, du_name, ek_name, ...}
    indicator_rows: dict[str, dict] = field(default_factory=dict)
    files_loaded: int = 0


def load_dg_archive(archive_dg_dir: Path) -> DGArchiveIndex:
    index = DGArchiveIndex()
    if not archive_dg_dir.is_dir():
        return index

    for path in sorted(archive_dg_dir.glob("indicators_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        rows = (((payload.get("response") or {}).get("data") or {}).get("list")) or []
        for row in rows:
            indicator_id = row.get("_id")
            if indicator_id:
                index.indicator_rows[indicator_id] = row
        index.files_loaded += 1

    for path in sorted(archive_dg_dir.glob("values_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        indicator_id = (payload.get("request") or {}).get("id")
        if not indicator_id:
            continue
        items = (payload.get("response") or {}).get("data") or []
        bucket = index.values_by_indicator.setdefault(indicator_id, {})
        for item in items:
            period = period_from_dt(item.get("dt", ""))
            if period is not None and item.get("v") is not None:
                bucket[period] = item["v"]
        index.files_loaded += 1

    return index


def period_from_dt(dt: str) -> str | None:
    """DG 'dt' code -> contract period string. '202605MM'->'2026-05',
    '202602SS'->'2026-Q2'. Independently reimplemented (the same 6-line rule
    documented in pipeline/backfill/dg_client.py's own docstring -- that
    module is not imported here, by design)."""
    match = re.match(r"^(\d{4})(\d{2})(MM|SS|AA)$", dt)
    if not match:
        return None
    year, unit, kind = match.groups()
    if kind == "MM":
        return f"{year}-{unit}"
    if kind == "SS":
        return f"{year}-Q{int(unit)}"
    return year


def resolve_src_indicator_id(src: str | None) -> str | None:
    if not src:
        return None
    match = _SRC_RE.match(src.strip())
    return match.group(1) if match else None


def find_candidate_indicator_ids(index: DGArchiveIndex, labels: list[str]) -> list[str]:
    """Every indicator id whose _name/i_showname/ek_name contains ANY of
    `labels` (compact-text substring match, same normalization kernel.py uses
    for HTML). Order is not meaningful; caller checks all of them."""
    if not labels:
        return []
    compact_labels = [compact_text(label) for label in labels if label]
    matches = []
    for indicator_id, row in index.indicator_rows.items():
        haystacks = [row.get("_name"), row.get("i_showname"), row.get("ek_name")]
        compact_haystacks = [compact_text(h) for h in haystacks if h]
        if any(label in haystack for label in compact_labels for haystack in compact_haystacks):
            matches.append(indicator_id)
    return matches


def lookup_value(index: DGArchiveIndex, indicator_id: str, period: str) -> float | None:
    bucket = index.values_by_indicator.get(indicator_id)
    if not bucket:
        return None
    return numeric(bucket.get(period))


# transform_name -> fn(raw_value) -> candidate stored-measure value
CANDIDATE_TRANSFORMS: dict[str, "callable[[float], float]"] = {
    "raw": lambda v: v,
    "index100_to_pct": lambda v: v - 100.0,
}


def match_against_observation(
    raw_value: float, obs: dict, *, measure_keys=("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")
) -> tuple[bool, str | None, str | None]:
    """(matched, measure_field, transform_name). Tries every declared measure
    on the observation against every candidate transform of raw_value, so
    neither "which field did this indicator feed" nor "is this a same-period-
    last-year=100 index" needs to be known in advance (see module docstring)."""
    for transform_name, transform in CANDIDATE_TRANSFORMS.items():
        candidate = transform(raw_value)
        for measure_key in measure_keys:
            if measure_key not in obs or obs[measure_key] is None:
                continue
            stored = numeric(obs[measure_key])
            if stored is not None and close_enough(stored, candidate):
                return True, measure_key, transform_name
    return False, None, None


_OBSERVATION_MEASURE_KEYS = ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")


def verify_observation(dg_index: DGArchiveIndex, catalog_entry: dict, label_entry: dict | None, obs: dict, *, check_id: str) -> Finding:
    """One sampled (series, period) observation, verified against this DG
    archive via the provenance fast path (`src`) and/or the label-name
    fallback (see module docstring). Shared by gate_b.dg_archive_sample and
    the DG branch of gate_b.archive_independent_sample so both checks apply
    IDENTICAL matching semantics -- they differ only in which observations
    they choose to sample, not in how a sampled point gets verified."""
    series_id, period = catalog_entry["id"], obs["period"]
    src_indicator_id = resolve_src_indicator_id(obs.get("src"))
    tried_ids: list[str] = [src_indicator_id] if src_indicator_id else []
    tried_ids += [c for c in find_candidate_indicator_ids(dg_index, label_candidates(label_entry)) if c not in tried_ids]

    if not tried_ids:
        return Finding(
            check=check_id,
            status="warn",
            series=series_id,
            period=period,
            tier=catalog_entry.get("tier"),
            note="no candidate DG indicator id (no `src` provenance and no labels.yaml name match) -- coverage gap",
        )

    any_archived = False
    for indicator_id in tried_ids:
        raw_value = lookup_value(dg_index, indicator_id, period)
        if raw_value is None:
            continue
        any_archived = True
        matched, measure_key, transform = match_against_observation(raw_value, obs, measure_keys=_OBSERVATION_MEASURE_KEYS)
        if matched:
            return Finding(
                check=check_id,
                status="pass",
                series=series_id,
                period=period,
                tier=catalog_entry.get("tier"),
                field=measure_key,
                source=f"dg:{indicator_id}",
                rule=transform,
            )

    if not any_archived:
        return Finding(
            check=check_id,
            status="warn",
            series=series_id,
            period=period,
            tier=catalog_entry.get("tier"),
            note=f"candidate indicator id(s) {tried_ids} have no archived value for period {period} -- coverage gap",
        )

    return Finding(
        check=check_id,
        status=severity_for_mismatch(catalog_entry),
        series=series_id,
        period=period,
        tier=catalog_entry.get("tier"),
        observed={key: obs.get(key) for key in _OBSERVATION_MEASURE_KEYS if obs.get(key) is not None},
        note=f"none of the archived candidate indicator(s) {tried_ids} matched (tried raw + index100 transforms)",
    )
