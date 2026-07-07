"""Deterministic seed derivation + the strata builders for
gate_b.archive_independent_sample (task spec strata a-f).

Seed default: the repo's git HEAD short SHA (via `subprocess` only — never a git
Python library, so this package carries no git dependency) mixed with the
catalog version, so re-running the audit against an unchanged build reproduces
the exact same sample, but a new commit (or a new catalog version on the same
commit, e.g. a backfill merge) draws a fresh one.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from random import Random


def git_head_short_sha(repo_root: Path) -> str | None:
    """None on ANY failure (not a repo, git missing, detached weirdness,
    timeout) — callers fall back to the catalog version rather than crash or
    fabricate a placeholder SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def last_data_commit_touched_periods(repo_root: Path) -> dict[str, set[str]] | None:
    """Best-effort stratum (b): {series_id: {periods touched by the last commit
    that changed data/series or data/panels}}. Returns None (caller skips the
    stratum entirely, per task spec "if discoverable, else skip") when git is
    unavailable, there is no such commit, or the diff can't be parsed --
    this is explicitly a best-effort heuristic over a unified diff's added
    lines, not a real JSON-aware diff."""
    try:
        log = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", "data/series", "data/panels"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = log.stdout.strip()
    if log.returncode != 0 or not sha:
        return None
    try:
        diff = subprocess.run(
            ["git", "show", "--unified=0", sha, "--", "data/series", "data/panels"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode != 0 or not diff.stdout:
        return None

    touched: dict[str, set[str]] = {}
    current_id: str | None = None
    for line in diff.stdout.splitlines():
        file_match = re.match(r"^\+\+\+ b/(?:data/)?(?:series|panels)/([^/]+)\.json$", line)
        if file_match:
            current_id = file_match.group(1)
            continue
        if line.startswith("+++"):
            current_id = None
            continue
        if current_id and line.startswith("+"):
            for period_match in re.finditer(r'"period"\s*:\s*"([^"]+)"', line):
                touched.setdefault(current_id, set()).add(period_match.group(1))
    return touched or None


def derive_seed(explicit_seed: str | None, *, repo_root: Path, catalog_version: str) -> str:
    if explicit_seed:
        return explicit_seed
    sha = git_head_short_sha(repo_root)
    return f"{sha}:{catalog_version}" if sha else f"noseed:{catalog_version}"


@dataclass
class SampleItem:
    """One (series, period, caliber) unit selected for archive re-verification."""

    series_id: str
    period: str
    caliber: str  # "single" | "ytd"
    stratum: str
    section: str | None = None


@dataclass
class Strata:
    items: dict[str, list[SampleItem]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # stratum names skipped (not discoverable)

    def all_items(self) -> list[SampleItem]:
        out = []
        for items in self.items.values():
            out.extend(items)
        return out


def _calibers_for(entry: dict) -> list[str]:
    return [c for c in entry.get("calibers", []) if c in ("single", "ytd")] or ["single"]


def build_strata(
    *,
    catalog: dict,
    series_by_id: dict[str, dict],
    section_bundles: dict[str, dict],
    repo_root: Path,
    rng: Random,
    samples_per_section: int,
) -> Strata:
    """Build strata (a)-(f) from the task spec. Operates over the *bundles*
    (site-data/sections/*.json) for latest/observation-array content, since
    that is what's actually being deployed and audited -- not a re-read of
    data/series/ (which check 1's own value re-verification does separately
    once a sample is chosen).
    """
    strata = Strata()
    now = {}  # series_id -> (section_id, bundle_entry)
    for section_id, bundle in section_bundles.items():
        for entry in bundle.get("series", []):
            now[entry["id"]] = (section_id, entry)

    # (a) every series' latest observation per caliber [mandatory]
    stratum_a: list[SampleItem] = []
    for series_id, (section_id, entry) in now.items():
        catalog_entry = series_by_id.get(series_id, {})
        for caliber in _calibers_for(catalog_entry):
            latest = entry.get("latest")
            if latest and latest.get("period"):
                stratum_a.append(SampleItem(series_id, latest["period"], caliber, "a_latest", section_id))
    strata.items["a_latest_per_caliber"] = stratum_a

    # (b) every observation whose period was touched in the last data commit,
    # if discoverable, else skip stratum entirely.
    touched = last_data_commit_touched_periods(repo_root)
    if touched is None:
        strata.skipped.append("b_last_commit_touched")
    else:
        stratum_b = []
        for series_id, periods in touched.items():
            if series_id not in now:
                continue
            section_id, entry = now[series_id]
            catalog_entry = series_by_id.get(series_id, {})
            for period in sorted(periods):
                for caliber in _calibers_for(catalog_entry):
                    stratum_b.append(SampleItem(series_id, period, caliber, "b_last_commit", section_id))
        strata.items["b_last_commit_touched"] = stratum_b

    # (c) every break seam (the observation immediately on each side) [mandatory]
    stratum_c: list[SampleItem] = []
    for series_id, (section_id, entry) in now.items():
        breaks = entry.get("breaks") or []
        if not breaks:
            continue
        periods = sorted({pt["period"] for pt in entry.get("yoy_series", [])} | {pt["period"] for pt in entry.get("level_series", [])})
        catalog_entry = series_by_id.get(series_id, {})
        for brk in breaks:
            effective = brk.get("effective")
            if not effective:
                continue
            before = [p for p in periods if p < effective]
            after = [p for p in periods if p >= effective]
            seam_periods = ([before[-1]] if before else []) + ([after[0]] if after else [])
            for period in seam_periods:
                for caliber in _calibers_for(catalog_entry):
                    stratum_c.append(SampleItem(series_id, period, caliber, "c_break_seam", section_id))
    strata.items["c_break_seam"] = stratum_c

    # (d) every jan_feb observation of the last 2 years
    stratum_d: list[SampleItem] = []
    latest_years = sorted({int(e["latest"]["period"][:4]) for _, e in now.values() if e.get("latest")}, reverse=True)
    recent_years = set(latest_years[:1])
    if latest_years:
        recent_years = {latest_years[0], latest_years[0] - 1}
    for series_id, (section_id, entry) in now.items():
        catalog_entry = series_by_id.get(series_id, {})
        for pt in entry.get("level_series", []):
            period = pt["period"]
            if period.endswith("-02") and int(period[:4]) in recent_years:
                # jan_feb-ness is a flag on the raw observation, not exposed
                # per-point in level_series; a Feb point is a plausible
                # candidate regardless, and the per-value check below simply
                # finds no match if it isn't actually a combined print.
                for caliber in _calibers_for(catalog_entry):
                    stratum_d.append(SampleItem(series_id, period, caliber, "d_jan_feb", section_id))
    strata.items["d_jan_feb_recent"] = stratum_d

    # (e) latest of every derived series
    stratum_e: list[SampleItem] = []
    for series_id, catalog_entry in series_by_id.items():
        if not catalog_entry.get("derived"):
            continue
        if series_id not in now:
            continue
        section_id, entry = now[series_id]
        latest = entry.get("latest")
        if latest and latest.get("period"):
            for caliber in _calibers_for(catalog_entry):
                stratum_e.append(SampleItem(series_id, latest["period"], caliber, "e_derived_latest", section_id))
    strata.items["e_derived_latest"] = stratum_e

    # (f) random N per section (default cap 25)
    stratum_f: list[SampleItem] = []
    by_section: dict[str, list[SampleItem]] = {}
    for series_id, (section_id, entry) in now.items():
        catalog_entry = series_by_id.get(series_id, {})
        for pt in entry.get("level_series", []):
            for caliber in _calibers_for(catalog_entry):
                by_section.setdefault(section_id, []).append(
                    SampleItem(series_id, pt["period"], caliber, "f_random", section_id)
                )
    for section_id, pool in by_section.items():
        stratum_f.extend(rng.sample(pool, min(len(pool), samples_per_section)))
    strata.items["f_random_per_section"] = stratum_f

    return strata
