"""pipeline/normalize.py — merge a ParsedRelease into data/series/<id>.json files.

Implements DATA-CONTRACT.md §4 (revisions/breaks) and the normalize stage of §11:
maps ParsedRelease rows to series ids via pipeline/config/field_map.yaml, then
merges each row's value into the target series file's `observations[]` in place.

    - New period for a series      -> append a new observation.
    - Existing period, value changed -> update the observation AND append a
      revisions[] entry (append-only log; the observation itself is never
      silently overwritten with no trace).
    - Existing period, value unchanged -> no-op. Re-running the same
      ParsedRelease twice therefore produces zero additional revisions
      (idempotent), matching DATA-CONTRACT §9's build-idempotence requirement.
    - First print of a measure (key was absent, not merely null) is never logged
      as a revision -- "the observation *is* the first print until something
      changes it" (DATA-CONTRACT §4.1).
    - `no_yoy_across` breaks: an m_yoy/ytd_yoy/real_yoy value that falls in
      [break.effective, break.yoy_valid_from) for a break with `no_yoy_across:
      true` is never persisted, regardless of what the source row says --
      DATA-CONTRACT's own expectation is that NBS/PBoC simply won't publish a
      YoY there, so in practice there is usually no row to suppress; this is a
      defensive backstop, not the primary mechanism.
    - Jan-Feb: a `flags:["jan_feb"]` observation must anchor to the *end* month
      (period ending "-02") with `span:2` (DATA-CONTRACT §3.2) -- violations
      raise rather than silently writing a malformed observation -- and a
      standalone "-01" observation is rejected if that year's "-02" observation
      already carries the jan_feb merge (that Jan value is already inside it).

IMPORTANT: this module is tested exclusively against synthetic fixtures under
pipeline/tests/fixtures/series/ -- the real data/series/ tree is being written by
a concurrent agent in this same rebuild and is deliberately never touched here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from pipeline import ParsedRelease, ParsedRow

DEFAULT_FIELD_MAP_PATH = Path(__file__).resolve().parent / "config" / "field_map.yaml"

_YOY_MEASURES = {"m_yoy", "ytd_yoy", "real_yoy"}
_COMPACT_ARRAY_KEYS = {"observations", "revisions", "breaks"}


@dataclass
class NormalizeReport:
    """What one apply_parsed_release() call did (or would do, under dry_run)."""

    new_observations: list[tuple[str, str]] = field(default_factory=list)  # (series_id, period)
    revisions: list[dict] = field(default_factory=list)  # revision dict + "series_id"
    unmapped_fields: list[str] = field(default_factory=list)  # source_fields absent from field_map
    missing_series: list[str] = field(default_factory=list)  # mapped series_id with no file on disk
    series_written: list[str] = field(default_factory=list)  # series_id actually touched


def load_field_map(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = path or DEFAULT_FIELD_MAP_PATH
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def _load_series(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _values_equal(current, incoming) -> bool:
    if current is None or incoming is None:
        return current is None and incoming is None
    try:
        return abs(float(current) - float(incoming)) < 1e-9
    except (TypeError, ValueError):
        return current == incoming


def _yoy_blocked_by_break(period: str, measure: str, breaks: list[dict]) -> bool:
    if measure not in _YOY_MEASURES:
        return False
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


def _validate_jan_feb_row(row: ParsedRow) -> None:
    if "jan_feb" not in row.flags:
        return
    month = row.period.split("-")[-1] if "-" in row.period else None
    if month != "02":
        raise ValueError(
            f"jan_feb flag must anchor to the YYYY-02 (end) period per DATA-CONTRACT §3.2, "
            f"got period={row.period!r}"
        )
    if row.span != 2:
        raise ValueError(f"a jan_feb observation must have span=2, got span={row.span!r} for period={row.period!r}")


def _would_double_count_jan_feb(observations_by_period: dict, period: str) -> bool:
    if not period.endswith("-01"):
        return False
    year = period.split("-")[0]
    feb_obs = observations_by_period.get(f"{year}-02")
    return bool(feb_obs and "jan_feb" in feb_obs.get("flags", []))


def _merge_rows_into_series(
    series: dict,
    rows: list[ParsedRow],
    release_id: str,
    revised_on: str,
) -> tuple[list[str], list[dict], bool]:
    """Mutates series["observations"]/series["revisions"] in place. Returns
    (new_observation_periods, new_revision_entries, changed).

    `changed` is True if ANY row actually mutated the series -- a new
    observation, a revision, OR a first print of a measure that was absent on an
    *existing* observation (that last case touches neither new_periods nor
    new_revisions, so callers must check `changed` rather than inferring "did
    anything happen" from whether those two lists are non-empty)."""
    observations = series.setdefault("observations", [])
    revisions = series.setdefault("revisions", [])
    breaks = series.get("breaks", [])

    by_period = {obs["period"]: obs for obs in observations}
    new_periods: list[str] = []
    new_revisions: list[dict] = []
    changed = False

    for row in rows:
        measure = row.caliber_hint
        if measure is None:
            continue
        _validate_jan_feb_row(row)
        if _yoy_blocked_by_break(row.period, measure, breaks):
            continue  # no_yoy_across: never persist a YoY value spanning the seam

        obs = by_period.get(row.period)
        if obs is None:
            if _would_double_count_jan_feb(by_period, row.period):
                raise ValueError(
                    f"refusing to create a standalone {row.period!r} observation: "
                    f"{row.period.split('-')[0]}-02 already carries the jan_feb merge"
                )
            obs = {"period": row.period}
            if row.span and row.span != 1:
                obs["span"] = row.span
            if row.flags:
                obs["flags"] = sorted(row.flags)
            observations.append(obs)
            by_period[row.period] = obs
            new_periods.append(row.period)
            changed = True
        else:
            if row.span and row.span != 1 and obs.get("span") != row.span:
                obs["span"] = row.span
                changed = True
            if row.flags:
                merged_flags = sorted(set(obs.get("flags", [])) | set(row.flags))
                if merged_flags != obs.get("flags", []):
                    obs["flags"] = merged_flags
                    changed = True

        if measure not in obs:
            obs[measure] = row.value  # first print for this measure -- not a revision
            changed = True
            continue
        current = obs[measure]
        if _values_equal(current, row.value):
            continue  # idempotent no-op: identical re-run changes nothing

        revision = {
            "period": row.period,
            "measure": measure,
            "old": current,
            "new": row.value,
            "revised_on": revised_on,
            "source": release_id or "unknown",
        }
        revisions.append(revision)
        new_revisions.append(revision)
        obs[measure] = row.value
        changed = True

    observations.sort(key=lambda item: item["period"])
    revisions.sort(key=lambda item: (item["period"], item["measure"]))
    return new_periods, new_revisions, changed


def apply_parsed_release(
    parsed: ParsedRelease,
    series_dir: Path,
    field_map: dict[str, dict[str, str]],
    *,
    revised_on: str | None = None,
    dry_run: bool = False,
) -> NormalizeReport:
    """Merge one ParsedRelease into the series files under series_dir.

    Unmapped source_fields and series ids with no existing file are collected in
    the report rather than raised -- a new commodity category or a series id not
    yet scaffolded by the catalog is an expected, recoverable state for a poller,
    not a format-drift emergency (that distinction is what ParseError in the
    parsers is for).
    """
    report = NormalizeReport()
    revised_on = revised_on or date.today().isoformat()
    source_map = field_map.get(parsed.source, {})

    rows_by_series: dict[str, list[ParsedRow]] = {}
    for row in parsed.rows:
        series_id = source_map.get(row.source_field)
        if series_id is None:
            report.unmapped_fields.append(row.source_field)
            continue
        rows_by_series.setdefault(series_id, []).append(row)

    for series_id, rows in rows_by_series.items():
        series_path = series_dir / f"{series_id}.json"
        if not series_path.exists():
            report.missing_series.append(series_id)
            continue
        series = _load_series(series_path)
        new_periods, revisions, changed = _merge_rows_into_series(series, rows, parsed.release_id, revised_on)
        for period in new_periods:
            report.new_observations.append((series_id, period))
        for revision in revisions:
            report.revisions.append({**revision, "series_id": series_id})
        if changed:
            series["generated_at"] = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                series_path.write_text(dump_series(series), encoding="utf-8")
            report.series_written.append(series_id)

    return report


def _compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def dump_series(series: dict) -> str:
    """Render a series dict per DATA-CONTRACT §9: one observation/revision/break
    per line, UTF-8, \\n line endings, trailing newline, no BOM. Key order follows
    whatever order the dict already carries (json.load preserves insertion order,
    and this project's synthetic/real series files are written in schema field
    order), so this function does not re-sort top-level keys itself."""
    lines = ["{"]
    keys = list(series.keys())
    for index, key in enumerate(keys):
        value = series[key]
        comma = "," if index < len(keys) - 1 else ""
        if key in _COMPACT_ARRAY_KEYS and isinstance(value, list):
            if not value:
                lines.append(f'  "{key}": []{comma}')
            else:
                lines.append(f'  "{key}": [')
                for item_index, item in enumerate(value):
                    item_comma = "," if item_index < len(value) - 1 else ""
                    lines.append(f"    {_compact_json(item)}{item_comma}")
                lines.append(f"  ]{comma}")
        else:
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            rendered = "\n".join(
                (("  " + line) if line_index > 0 else line) for line_index, line in enumerate(rendered.split("\n"))
            )
            lines.append(f'  "{key}": {rendered}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"
