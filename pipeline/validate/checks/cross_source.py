"""Cross-source triangulation checks: gate_a.triangulate_dg_press,
gate_a.triangulate_pbc_nbs.

Both checks compare two independent views of "the same official number" and
therefore both default to `status=skipped` (not a pass) when only one view is
available this run -- a normal single-parser runner.py invocation only ever
produces ONE ingestion path per batch (see batch.py's module docstring), so
skipped is the expected everyday outcome, not a degraded one.
"""
from __future__ import annotations

from collections import defaultdict

from pipeline.validate.context import GateContext
from pipeline.validate.model import BLOCK, WARN, Finding, make_result
from pipeline.validate.util import MEASURE_NAMES, display_tolerance, is_number


def check_triangulate_dg_press(ctx: GateContext):
    """gate_a.triangulate_dg_press -- BLOCK. When THIS batch carries both a
    dg-sourced and a press-sourced value for the same (series, period,
    measure), they must agree to display precision (half a ULP of the
    series' own declared `decimals`) -- both paths claim to report the same
    official print. Populates ctx.confirmed_cross_source_matches for any pair
    that DID match, which gate_a.seasonal_z consults to demote a would-be
    BLOCK z-score."""
    groups: dict[tuple[str, str, str], list[tuple[str, float]]] = defaultdict(list)
    for item in ctx.batch.items:
        for measure in MEASURE_NAMES:
            value = item.obs.get(measure)
            if is_number(value):
                groups[(item.series_id, item.period, measure)].append((item.source_kind, value))

    findings: list[Finding] = []
    any_pair = False
    for (series_id, period, measure), values in groups.items():
        kinds_present = {kind for kind, _ in values}
        if not {"dg", "press"} <= kinds_present:
            continue
        any_pair = True
        dg_values = [v for kind, v in values if kind == "dg"]
        press_values = [v for kind, v in values if kind == "press"]
        tol = display_tolerance(ctx.decimals_for(series_id))
        matched = True
        for dv in dg_values:
            for pv in press_values:
                if abs(dv - pv) > tol:
                    matched = False
                    findings.append(
                        Finding(
                            "gate_a.triangulate_dg_press", BLOCK,
                            f"dg={dv} vs press={pv} for {measure} differ by more than {tol:.4g}",
                            series_id=series_id, period=period, measure=measure,
                        )
                    )
        if matched:
            ctx.confirmed_cross_source_matches.add((series_id, period))

    if not any_pair:
        return make_result("gate_a.triangulate_dg_press", skipped=True, note="no (series, period, measure) had both a dg- and press-sourced value this run")
    return make_result("gate_a.triangulate_dg_press", findings)


def check_triangulate_pbc_nbs(ctx: GateContext):
    """gate_a.triangulate_pbc_nbs -- WARN. For each configured source_pairs
    entry, compares overlapping periods between a primary and secondary
    series id (e.g. a PBoC-published series vs its NBS republication) against
    that pair's tolerance. Skips a pair entirely when either series doesn't
    exist yet (nothing to compare) rather than failing."""
    findings = []
    any_pair = False
    for pair in ctx.config.source_pairs:
        for id_pair in pair.series:
            if len(id_pair) != 2:
                continue
            primary_id, secondary_id = id_pair
            if not (ctx.is_touched(primary_id) or ctx.is_touched(secondary_id)):
                continue
            primary = ctx.load(primary_id)
            secondary = ctx.load(secondary_id)
            if primary is None or secondary is None:
                continue
            any_pair = True
            secondary_by_period = {o["period"]: o.get("m") for o in secondary.get("observations", [])}
            touched = ctx.touched_periods(primary_id, primary) | ctx.touched_periods(secondary_id, secondary)
            for obs in primary.get("observations", []):
                period = obs["period"]
                if period not in touched:
                    continue
                primary_v = obs.get("m")
                secondary_v = secondary_by_period.get(period)
                if not is_number(primary_v) or not is_number(secondary_v):
                    continue
                if abs(primary_v - secondary_v) > pair.tol:
                    findings.append(
                        Finding(
                            "gate_a.triangulate_pbc_nbs", WARN,
                            f"{primary_id}={primary_v} ({pair.primary}) vs {secondary_id}={secondary_v} ({pair.secondary}) differ by more than tol={pair.tol}",
                            series_id=primary_id, period=period,
                        )
                    )
    if not any_pair:
        return make_result("gate_a.triangulate_pbc_nbs", skipped=True, note="no configured source_pairs entry had both series present/touched this run")
    return make_result("gate_a.triangulate_pbc_nbs", findings)
