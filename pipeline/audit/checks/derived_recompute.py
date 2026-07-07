"""gate_b.derived_recompute — independently recomputes every derived series
from its documented rule (DATA-CONTRACT §6) applied to its OWN inputs' raw
`data/series/*.json` / `data/panels/*.json` files, and compares against the
derived series' own stored value. Mismatch BLOCKs, except at whitelisted
"known disagreement" periods (WARN instead — see `_known_disagreements`).

Scope, stated explicitly because it's a real boundary, not an oversight:
DATA-CONTRACT §6 only ever specifies the formula for a derived series' PRIMARY
measure (the `m` or `ytd` its `derived.caliber` names) -- it never specifies a
formula for that series' OWN m_yoy/ytd_yoy. This check therefore only
recomputes the primary measure. (`nbs-retail-online-share`'s m_yoy, for
example, is not independently re-derived here.)

Rules implemented, one function each, all reading ONLY data/series/ +
data/panels/ (never the bundle, never pipeline.normalize/build):

  single_from_ytd   m(t) = ytd(t) - ytd(t-1); at a jan_feb print, m(t) = ytd(t)
                    (no prior-year YTD to subtract). Self-referential: the
                    single series' own ytd feeds its own m.
  ratio             100 * inputs[0].<caliber> / inputs[1].<caliber> (inferred
                    convention from data: nbs-retail-online-share = 100 *
                    online-goods / ex-auto at both single and ytd calibers --
                    verified empirically against real data/series/ values,
                    e.g. 2026-05: 100*11533/37781 = 30.53 = stored m).
  simple_mean_of_cities / count_cities_gt_zero
                    mean / count>0 across cities of panel cells[city][metric]["m"]
                    at each period; metric inferred from the series id
                    ("newhome" -> "new_home", "resale" -> "resale_home").
  sum               sum of every input's `ytd` at a period; a period is
                    skipped (not a mismatch) if any input lacks that period --
                    matches pipeline/migrate/REPORT.md's own documented
                    behavior for mof-real-estate-tax-total (5 periods excluded
                    there for exactly this reason).
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from pipeline.audit.kernel import close_enough, numeric
from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.site_data import load_series_file

CHECK_ID = "gate_b.derived_recompute"

# From pipeline/migrate/REPORT.md's "Flagged ambiguities / deviations" #5:
# property_release_archive.json vs property_city_history.json disagree
# materially at 29 of 184 overlapping periods; the panel's cells[] prefer
# property_city_history.json pre-2026-05 and property_release_archive.json at
# 2026-05, so the *-up-count aggregate recomputed from the panel can
# legitimately disagree with the separately-migrated named series at those
# periods. migrate.py's own "Derived series validation" section lists this
# 5-period sample per aggregate (not the full 29 -- the report does not
# enumerate all 29 verbatim). Hardcoded here per the task spec ("hardcode the
# migrate REPORT §5 periods").
#
# This is MERGED with (not replaced by) pipeline/config/validation.yaml's own
# known_disagreements when that file exists (see _load_known_disagreements):
# the real file that landed during this rebuild is scoped to gate_a checks
# (`checks: ["gate_a.sum_of_parts"]`) and only covers mof-real-estate-tax-
# total's missing-component periods -- a case this check already handles
# structurally (see _check_sum's skip-on-missing-input logic below), not the
# 70-city up-count vintage disagreement above, which no other document
# records. Replacing this list outright the moment ANY validation.yaml
# appears would silently regress real coverage this research already
# established; the task spec's "if that file exists" is read here as "also
# respect what it says", not "and forget everything else."
_HARDCODED_KNOWN_DISAGREEMENTS: set[tuple[str, str]] = {
    ("nbs-70city-newhome-up-count", "2020-02"),
    ("nbs-70city-newhome-up-count", "2020-10"),
    ("nbs-70city-newhome-up-count", "2023-10"),
    ("nbs-70city-newhome-up-count", "2023-11"),
    ("nbs-70city-newhome-up-count", "2024-02"),
    ("nbs-70city-resale-up-count", "2020-02"),
    ("nbs-70city-resale-up-count", "2020-10"),
    ("nbs-70city-resale-up-count", "2023-10"),
    ("nbs-70city-resale-up-count", "2024-11"),
    ("nbs-70city-resale-up-count", "2025-04"),
}


def _load_known_disagreements(repo_root: Path) -> set[tuple[str, str]]:
    """Hardcoded 70-city entries, UNIONED with pipeline/config/validation.yaml's
    own known_disagreements when that file exists (see the module-level
    comment above _HARDCODED_KNOWN_DISAGREEMENTS for why this is a union, not
    a replacement). Real schema (confirmed against the file that landed
    during this rebuild): each row is `{series, periods: [...], checks?,
    note?}` -- `periods` PLURAL (a list), not the singular `period` an
    earlier draft of this function assumed."""
    merged = set(_HARDCODED_KNOWN_DISAGREEMENTS)
    path = repo_root / "pipeline" / "config" / "validation.yaml"
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("known_disagreements"), list):
            for row in data["known_disagreements"]:
                series = row.get("series")
                periods = row.get("periods") or ([row["period"]] if row.get("period") else [])
                if series and periods:
                    merged.update((series, period) for period in periods)
    return merged


def _metric_for(series_id: str) -> str | None:
    if "newhome" in series_id:
        return "new_home"
    if "resale" in series_id:
        return "resale_home"
    return None


def _check_single_from_ytd(ctx: AuditContext, entry: dict, series: dict, known: set) -> list[Finding]:
    findings = []
    observations = series.get("observations", [])
    index_by_period = {obs["period"]: obs for obs in observations}
    for obs in observations:
        stored_m = obs.get("m")
        ytd = obs.get("ytd")
        if stored_m is None or ytd is None:
            continue
        period = obs["period"]
        if "jan_feb" in obs.get("flags", []) or obs.get("span", 1) > 1:
            expected = ytd
        else:
            month = int(period.split("-")[1]) if len(period) == 7 else None
            if month is None or month <= 1:
                continue  # no prior-year YTD to subtract; shouldn't occur per migrate report, but defensive
            prev_period = f"{period[:4]}-{month - 1:02d}"
            prev_obs = index_by_period.get(prev_period)
            if prev_obs is None or prev_obs.get("ytd") is None:
                continue
            expected = ytd - prev_obs["ytd"]
        tol = max(1.0, abs(expected) * 0.002)
        status = "pass" if close_enough(expected, stored_m, tol) else "block"
        if (entry["id"], period) in known:
            status = "warn" if status == "block" else status
        if status != "pass":
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status=status,
                    series=entry["id"],
                    period=period,
                    field="m",
                    tier=entry.get("tier"),
                    expected=round(expected, 4),
                    observed=stored_m,
                    tolerance=tol,
                    rule="single_from_ytd",
                )
            )
    return findings


def _check_ratio(ctx: AuditContext, entry: dict, series: dict, inputs: list[str], known: set) -> list[Finding]:
    findings = []
    numerator = load_series_file(ctx.data_dir, ctx.series_by_id()[inputs[0]]) if inputs[0] in ctx.series_by_id() else None
    denominator = load_series_file(ctx.data_dir, ctx.series_by_id()[inputs[1]]) if inputs[1] in ctx.series_by_id() else None
    if numerator is None or denominator is None:
        findings.append(
            Finding(check=CHECK_ID, status="warn", series=entry["id"], rule="ratio", note=f"could not load input series {inputs}")
        )
        return findings
    num_by_period = {o["period"]: o for o in numerator.get("observations", [])}
    den_by_period = {o["period"]: o for o in denominator.get("observations", [])}
    for obs in series.get("observations", []):
        period = obs["period"]
        for caliber, field_name in (("single", "m"), ("ytd", "ytd")):
            stored = obs.get(field_name)
            num_obs, den_obs = num_by_period.get(period), den_by_period.get(period)
            if stored is None or num_obs is None or den_obs is None:
                continue
            num_val, den_val = num_obs.get(field_name), den_obs.get(field_name)
            if num_val is None or den_val is None or den_val == 0:
                continue
            expected = 100.0 * num_val / den_val
            tol = max(0.05, abs(expected) * 0.01)
            status = "pass" if close_enough(expected, stored, tol) else "block"
            if (entry["id"], period) in known:
                status = "warn" if status == "block" else status
            if status != "pass":
                findings.append(
                    Finding(
                        check=CHECK_ID,
                        status=status,
                        series=entry["id"],
                        period=period,
                        field=field_name,
                        tier=entry.get("tier"),
                        expected=round(expected, 4),
                        observed=stored,
                        tolerance=tol,
                        rule="ratio",
                    )
                )
    return findings


def _check_panel_aggregate(ctx: AuditContext, entry: dict, series: dict, rule: str, panel_id: str, known: set) -> list[Finding]:
    findings = []
    panel_catalog_entry = ctx.series_by_id().get(panel_id)
    panel = load_series_file(ctx.data_dir, panel_catalog_entry) if panel_catalog_entry else None
    if panel is None:
        findings.append(Finding(check=CHECK_ID, status="warn", series=entry["id"], rule=rule, note=f"could not load panel {panel_id}"))
        return findings
    metric = _metric_for(entry["id"])
    if metric is None:
        findings.append(
            Finding(check=CHECK_ID, status="warn", series=entry["id"], rule=rule, note="could not infer panel metric from series id")
        )
        return findings
    cities = panel["dimensions"][list(panel["dimensions"].keys())[0]]
    periods = panel["periods"]
    cells = panel["cells"]
    by_period_expected: dict[str, float] = {}
    for i, period in enumerate(periods):
        values = []
        for city in cities:
            arr = (cells.get(city, {}).get(metric, {}) or {}).get("m", [])
            if i < len(arr) and arr[i] is not None:
                values.append(arr[i])
        if not values:
            continue
        if rule == "simple_mean_of_cities":
            by_period_expected[period] = round(sum(values) / len(values), 4)
        else:
            by_period_expected[period] = float(sum(1 for v in values if v > 0))

    for obs in series.get("observations", []):
        period = obs["period"]
        stored = obs.get("m")
        expected = by_period_expected.get(period)
        if stored is None or expected is None:
            continue
        tol = 0.01 if rule == "simple_mean_of_cities" else 0.0
        status = "pass" if close_enough(expected, stored, tol) else "block"
        if (entry["id"], period) in known:
            status = "warn" if status == "block" else status
        if status != "pass":
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status=status,
                    series=entry["id"],
                    period=period,
                    field="m",
                    tier=entry.get("tier"),
                    expected=expected,
                    observed=stored,
                    tolerance=tol,
                    rule=rule,
                    note="known disagreement (property_release_archive vs property_city_history vintage) per migrate/REPORT.md #5"
                    if status == "warn"
                    else None,
                )
            )
    return findings


def _check_sum(ctx: AuditContext, entry: dict, series: dict, inputs: list[str], known: set) -> list[Finding]:
    findings = []
    input_series = {}
    for input_id in inputs:
        catalog_entry = ctx.series_by_id().get(input_id)
        loaded = load_series_file(ctx.data_dir, catalog_entry) if catalog_entry else None
        if loaded is None:
            findings.append(Finding(check=CHECK_ID, status="warn", series=entry["id"], rule="sum", note=f"could not load input {input_id}"))
            return findings
        input_series[input_id] = {o["period"]: o for o in loaded.get("observations", [])}

    for obs in series.get("observations", []):
        period = obs["period"]
        stored = obs.get("ytd")
        if stored is None:
            continue
        values = []
        skip = False
        for input_id in inputs:
            input_obs = input_series[input_id].get(period)
            if input_obs is None or input_obs.get("ytd") is None:
                skip = True
                break
            values.append(input_obs["ytd"])
        if skip:
            continue  # not computable this period (matches migrate.py's own documented exclusion behavior)
        expected = sum(values)
        tol = max(1.0, abs(expected) * 0.002)
        status = "pass" if close_enough(expected, stored, tol) else "block"
        if (entry["id"], period) in known:
            status = "warn" if status == "block" else status
        if status != "pass":
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status=status,
                    series=entry["id"],
                    period=period,
                    field="ytd",
                    tier=entry.get("tier"),
                    expected=round(expected, 4),
                    observed=stored,
                    tolerance=tol,
                    rule="sum",
                )
            )
    return findings


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    known = _load_known_disagreements(ctx.repo_root)
    checked = 0

    for series_id, catalog_entry in ctx.series_by_id().items():
        derived = catalog_entry.get("derived")
        if not derived:
            continue
        series = load_series_file(ctx.data_dir, catalog_entry)
        if series is None:
            findings.append(Finding(check=CHECK_ID, status="warn", series=series_id, note="derived series file missing/unreadable"))
            continue
        checked += 1
        rule = derived.get("rule")
        inputs = derived.get("inputs", [])
        if rule == "single_from_ytd":
            findings.extend(_check_single_from_ytd(ctx, catalog_entry, series, known))
        elif rule == "ratio":
            findings.extend(_check_ratio(ctx, catalog_entry, series, inputs, known))
        elif rule in ("simple_mean_of_cities", "count_cities_gt_zero"):
            panel_id = inputs[0] if inputs else None
            if panel_id:
                findings.extend(_check_panel_aggregate(ctx, catalog_entry, series, rule, panel_id, known))
        elif rule == "sum":
            findings.extend(_check_sum(ctx, catalog_entry, series, inputs, known))
        else:
            findings.append(Finding(check=CHECK_ID, status="warn", series=series_id, note=f"unrecognized derived rule {rule!r}; not recomputed"))

    if not any(f.status in ("block", "warn") for f in findings):
        findings.append(Finding(check=CHECK_ID, status="pass", note=f"{checked} derived series recomputed clean from inputs"))

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
