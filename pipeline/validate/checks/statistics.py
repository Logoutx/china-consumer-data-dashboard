"""Statistical / arithmetic-consistency checks: gate_a.seasonal_z,
gate_a.ytd_arithmetic, gate_a.yoy_base_tolerance, gate_a.sum_of_parts,
gate_a.cpi_envelope, gate_a.online_share_bounds."""
from __future__ import annotations

import math

from pipeline.validate.context import GateContext
from pipeline.validate.model import BLOCK, WARN, Finding, make_result
from pipeline.validate.util import in_no_yoy_window, is_number, period_shape, period_sort_key, rel_diff, robust_z

MIN_COHORT_POINTS = 3  # same-month/quarter history points needed for the seasonal_z fallback transform


# ---------------------------------------------------------------------------
# 6. gate_a.seasonal_z
# ---------------------------------------------------------------------------

def _cohort_key(period: str, jan_feb: bool) -> tuple | None:
    shape = period_shape(period)
    if shape == "M":
        if jan_feb:
            return ("jan_feb",)
        return ("month", int(period.split("-")[1]))
    if shape == "Q":
        return ("quarter", int(period.split("-Q")[1]))
    return None


def _level_measure(calibers: list[str]) -> str | None:
    if "single" in calibers:
        return "m"
    if "ytd" in calibers:
        return "ytd"
    return None


def _yoy_measure_present(obs: dict) -> str | None:
    if is_number(obs.get("m_yoy")):
        return "m_yoy"
    if is_number(obs.get("ytd_yoy")):
        return "ytd_yoy"
    return None


def _seasonal_z_for_period(data: dict, period: str, *, shock_periods: set[str]) -> tuple[float | None, str | None]:
    """Returns (z, basis) or (None, reason) if there isn't enough history to
    judge. Primary transform: the series' own published YoY history (already
    seasonality-free by construction). Fallback: same-calendar-cohort
    log-change (positive levels) / pp-change (%-types)."""
    observations = sorted(data.get("observations", []), key=lambda o: period_sort_key(o["period"]))
    by_period = {o["period"]: o for o in observations}
    obs = by_period.get(period)
    if obs is None:
        return None, "period not on file"
    breaks = data.get("breaks", [])
    value_type = data.get("value_type")
    calibers = data.get("calibers", [])
    prior_periods = [o["period"] for o in observations if period_sort_key(o["period"]) < period_sort_key(period)]

    # -- primary: published YoY history --------------------------------
    yoy_measure = _yoy_measure_present(obs)
    if yoy_measure is not None:
        history = [
            by_period[p][yoy_measure]
            for p in prior_periods
            if is_number(by_period[p].get(yoy_measure)) and p not in shock_periods and not in_no_yoy_window(p, breaks)
        ]
        if history:
            return robust_z(obs[yoy_measure], history), "yoy_history"

    # -- fallback: same-calendar-cohort change --------------------------
    level_measure = _level_measure(calibers)
    if level_measure is None or not is_number(obs.get(level_measure)):
        return None, "no level measure to fall back on"
    jan_feb = "jan_feb" in obs.get("flags", [])
    cohort = _cohort_key(period, jan_feb)
    if cohort is None:
        return None, "annual period has no finer cohort"

    cohort_periods = [
        p
        for p in prior_periods
        if _cohort_key(p, "jan_feb" in by_period[p].get("flags", [])) == cohort
        and is_number(by_period[p].get(level_measure))
        and p not in shock_periods
    ]
    if len(cohort_periods) < MIN_COHORT_POINTS + 1:  # need >=1 prior point to diff against, plus MIN_COHORT_POINTS changes
        return None, "not enough same-cohort history"

    use_log = value_type in ("level", "index", "count") and obs[level_measure] > 0 and all(
        by_period[p][level_measure] > 0 for p in cohort_periods
    )

    def _transform(x: float) -> float:
        return math.log(x) if use_log else x

    changes = []
    for prev_p, cur_p in zip(cohort_periods, cohort_periods[1:]):
        changes.append(_transform(by_period[cur_p][level_measure]) - _transform(by_period[prev_p][level_measure]))
    new_change = _transform(obs[level_measure]) - _transform(by_period[cohort_periods[-1]][level_measure])
    if len(changes) < MIN_COHORT_POINTS:
        return None, "not enough same-cohort changes"
    basis = "cohort_log_change" if use_log else "cohort_pp_change"
    return robust_z(new_change, changes), basis


def check_seasonal_z(ctx: GateContext):
    """gate_a.seasonal_z -- WARN at |z|>=z_warn, BLOCK at |z|>=z_block unless
    a cross-source check (7/8) confirmed the same value this run, in which
    case it is demoted to WARN. Skipped entirely when the series has fewer
    than config.min_history periods of history -- not enough of a baseline to
    call anything an outlier yet."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        freq = data.get("freq", "M")
        observations = data.get("observations", [])
        if len(observations) < ctx.config.min_history(freq):
            continue
        z_warn, z_block = ctx.config.z_thresholds(series_id, data.get("value_type"))
        shocks = ctx.config.shock_periods(series_id)
        for period in ctx.touched_periods(series_id, data):
            evaluated = True
            z, basis = _seasonal_z_for_period(data, period, shock_periods=shocks)
            if z is None or math.isnan(z):
                continue
            az = abs(z)
            if az >= z_block:
                if (series_id, period) in ctx.confirmed_cross_source_matches:
                    findings.append(
                        Finding(
                            "gate_a.seasonal_z", WARN,
                            f"|z|={az:.2f} >= z_block={z_block} ({basis}), demoted from BLOCK: a cross-source check confirmed this value this run",
                            series_id=series_id, period=period,
                        )
                    )
                else:
                    findings.append(Finding("gate_a.seasonal_z", BLOCK, f"|z|={az:.2f} >= z_block={z_block} ({basis})", series_id=series_id, period=period))
            elif az >= z_warn:
                findings.append(Finding("gate_a.seasonal_z", WARN, f"|z|={az:.2f} >= z_warn={z_warn} ({basis})", series_id=series_id, period=period))
    if not evaluated:
        return make_result("gate_a.seasonal_z", skipped=True, note="no touched series had enough history to evaluate")
    return make_result("gate_a.seasonal_z", findings)


# ---------------------------------------------------------------------------
# 9. gate_a.ytd_arithmetic
# ---------------------------------------------------------------------------

def _prior_month_period(period: str) -> str | None:
    if period_shape(period) != "M":
        return None
    y, m = period.split("-")
    y, m = int(y), int(m) - 1
    if m < 1:
        return None
    return f"{y:04d}-{m:02d}"


_ADDITIVE_VALUE_TYPES = {"level", "count"}


def check_ytd_arithmetic(ctx: GateContext):
    """gate_a.ytd_arithmetic -- WARN normally; BLOCK when ytd(t), ytd(t-1) AND
    m(t) all came from this SAME staged release (no later-revision excuse for
    the mismatch). Feb (jan_feb): m==ytd. March onward: ytd(t) ~= ytd(t-1)+m(t).

    Scoped to value_type in {level, count} -- genuine additive levels. A
    series like nbs-industrial-va (value_type yoy_pct) stores ONLY growth
    rates: 累计同比 is a ratio of cumulative sums, not itself additive from
    monthly rates (ytd(t) != ytd(t-1)+m(t) for rates by construction, not by
    error), so rate/index-typed series are skipped rather than flagged."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        if data.get("value_type") not in _ADDITIVE_VALUE_TYPES:
            continue
        calibers = set(data.get("calibers", []))
        if not {"single", "ytd"} <= calibers:
            continue
        decimals = data.get("decimals", 1) or 1
        ulp = 10 ** -decimals
        observations = data.get("observations", [])
        by_period = {o["period"]: o for o in observations}
        batch_periods = {item.period for item in ctx.batch.items_for(series_id)}

        for period in ctx.touched_periods(series_id, data):
            obs = by_period.get(period)
            if obs is None or period_shape(period) != "M":
                continue
            ytd, m = obs.get("ytd"), obs.get("m")
            if not is_number(ytd) or not is_number(m):
                continue
            evaluated = True
            tol = max(2 * ulp, 0.005 * abs(ytd))

            if "jan_feb" in obs.get("flags", []):
                if abs(m - ytd) > tol:
                    findings.append(Finding("gate_a.ytd_arithmetic", WARN, f"jan_feb print: m={m} != ytd={ytd} (tol={tol:.4g})", series_id=series_id, period=period))
                continue

            prior_period = _prior_month_period(period)
            prior_obs = by_period.get(prior_period) if prior_period else None
            if prior_obs is None or not is_number(prior_obs.get("ytd")):
                continue
            prior_ytd = prior_obs["ytd"]
            expected = prior_ytd + m
            if abs(ytd - expected) > tol:
                same_batch = period in batch_periods and prior_period in batch_periods
                severity = BLOCK if same_batch else WARN
                findings.append(
                    Finding(
                        "gate_a.ytd_arithmetic", severity,
                        f"ytd({period})={ytd} != ytd({prior_period})={prior_ytd} + m({period})={m} = {expected:.4g} (tol={tol:.4g})",
                        series_id=series_id, period=period,
                    )
                )
    if not evaluated:
        return make_result("gate_a.ytd_arithmetic", skipped=True, note="no single+ytd series with both m and ytd touched this run")
    return make_result("gate_a.ytd_arithmetic", findings)


# ---------------------------------------------------------------------------
# 10. gate_a.yoy_base_tolerance
# ---------------------------------------------------------------------------

def _year_ago_period(period: str) -> str | None:
    shape = period_shape(period)
    if shape == "M":
        y, m = period.split("-")
        return f"{int(y) - 1:04d}-{m}"
    if shape == "Q":
        y, q = period.split("-Q")
        return f"{int(y) - 1:04d}-Q{q}"
    if shape == "A":
        return str(int(period) - 1)
    return None


def _seam_between(prior_period: str, period: str, breaks: list[dict]) -> bool:
    for brk in breaks:
        if not brk.get("no_yoy_across"):
            continue
        eff = brk.get("effective")
        if eff and prior_period < eff <= period:
            return True
    return False


def check_yoy_base_tolerance(ctx: GateContext):
    """gate_a.yoy_base_tolerance -- WARN when |published_yoy - level_derived_yoy|
    exceeds the configured tolerance (comparable-caliber base differences make
    small gaps legitimate, hence a tolerance rather than equality). BLOCK only
    for impossible math: non-positive implied denominator, or an implied
    prior-year level more than 50% off the level actually on file.

    Scoped to value_type=="level" -- deriving a YoY from a level ratio only
    makes sense when `m`/`ytd` is a genuine chained level. An index-typed
    series (e.g. nbs-ppi-producer-yoy, base=100-style) has no such chain --
    dividing two index prints and calling it "the YoY" is a different,
    meaningless number, not a base-comparability nuance the tolerance exists
    to absorb."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        if data.get("value_type") != "level":
            continue
        calibers = set(data.get("calibers", []))
        observations = data.get("observations", [])
        by_period = {o["period"]: o for o in observations}
        breaks = data.get("breaks", [])
        tol = ctx.config.yoy_base_tol_pp(series_id, data.get("value_type"))

        for caliber, level_key, yoy_key in (("single", "m", "m_yoy"), ("ytd", "ytd", "ytd_yoy")):
            if caliber not in calibers:
                continue
            for period in ctx.touched_periods(series_id, data):
                obs = by_period.get(period)
                if obs is None:
                    continue
                level, pub_yoy = obs.get(level_key), obs.get(yoy_key)
                if not is_number(level) or not is_number(pub_yoy):
                    continue
                prior_period = _year_ago_period(period)
                prior_obs = by_period.get(prior_period) if prior_period else None
                if prior_obs is None:
                    continue
                prior_level = prior_obs.get(level_key)
                if not is_number(prior_level):
                    continue
                if obs.get("span", 1) != prior_obs.get("span", 1):
                    continue  # span mismatch (e.g. jan_feb vs a plain month) -- not comparable
                if _seam_between(prior_period, period, breaks):
                    continue
                evaluated = True

                denom = 1 + pub_yoy / 100
                if prior_level == 0 or denom == 0:
                    findings.append(
                        Finding("gate_a.yoy_base_tolerance", BLOCK, "impossible math: prior-year level is 0 or published yoy implies a 0 denominator", series_id=series_id, period=period, measure=yoy_key)
                    )
                    continue
                implied_prior = level / denom
                off = abs(implied_prior - prior_level) / abs(prior_level)
                if off > 0.5:
                    findings.append(
                        Finding(
                            "gate_a.yoy_base_tolerance", BLOCK,
                            f"published {yoy_key}={pub_yoy} implies prior-year {level_key}={implied_prior:.4g}, but {prior_period} on file is {prior_level} ({off:.0%} off)",
                            series_id=series_id, period=period, measure=yoy_key,
                        )
                    )
                    continue
                derived_yoy = (level / prior_level - 1) * 100
                gap = abs(pub_yoy - derived_yoy)
                if gap > tol:
                    findings.append(
                        Finding(
                            "gate_a.yoy_base_tolerance", WARN,
                            f"published {yoy_key}={pub_yoy} vs level-derived {derived_yoy:.2f} from {level_key} (gap {gap:.2f}pp > tol {tol}pp)",
                            series_id=series_id, period=period, measure=yoy_key,
                        )
                    )
    if not evaluated:
        return make_result("gate_a.yoy_base_tolerance", skipped=True, note="no comparable level+published-yoy pair with a same-caliber prior year on file")
    return make_result("gate_a.yoy_base_tolerance", findings)


# ---------------------------------------------------------------------------
# 11. gate_a.sum_of_parts
# ---------------------------------------------------------------------------

SUM_RULES = [
    {
        "total": "mof-real-estate-tax-total",
        "parts": ["mof-deed-tax", "mof-property-tax", "mof-urban-land-use-tax", "mof-land-vat", "mof-farmland-occupation-tax"],
        "measure": "ytd",
    },
    {"total": "nbs-retail-total", "parts": ["nbs-retail-urban", "nbs-retail-rural"], "measure": "m"},
    {"total": "nbs-retail-total", "parts": ["nbs-retail-goods", "nbs-retail-catering"], "measure": "m"},
]


def check_sum_of_parts(ctx: GateContext):
    """gate_a.sum_of_parts -- WARN. Loose relative tolerance; skips periods a
    config known_disagreements entry has already acknowledged (see
    pipeline/config/validation.yaml), and periods where any part is simply
    absent (a part missing is not a sum mismatch -- there is nothing to sum)."""
    findings = []
    applicable = False
    rel_tol = ctx.config.sum_of_parts_rel_tol()

    for rule in SUM_RULES:
        total_id, parts, measure = rule["total"], rule["parts"], rule["measure"]
        if not (ctx.is_touched(total_id) or any(ctx.is_touched(p) for p in parts)):
            continue
        total_data = ctx.load(total_id)
        parts_data = [ctx.load(p) for p in parts]
        if total_data is None or any(p is None for p in parts_data):
            continue
        applicable = True

        total_by_period = {o["period"]: o.get(measure) for o in total_data.get("observations", [])}
        parts_by_period = [{o["period"]: o.get(measure) for o in pd.get("observations", [])} for pd in parts_data]

        touched_periods = set(ctx.batch.periods_for(total_id))
        for p in parts:
            touched_periods |= set(ctx.batch.periods_for(p))
        if not touched_periods:
            touched_periods = set(total_by_period)

        for period in sorted(touched_periods, key=period_sort_key):
            if ctx.config.is_known_disagreement(series_id=total_id, period=period, check_id="gate_a.sum_of_parts"):
                continue
            total_v = total_by_period.get(period)
            part_vals = [pbp.get(period) for pbp in parts_by_period]
            if not is_number(total_v) or any(not is_number(v) for v in part_vals):
                continue  # a part missing this period -- nothing to sum, not a mismatch
            part_sum = sum(part_vals)
            gap = rel_diff(total_v, part_sum)
            if gap is not None and gap > rel_tol:
                findings.append(
                    Finding(
                        "gate_a.sum_of_parts", WARN,
                        f"{total_id}={total_v} vs sum({'+'.join(parts)})={part_sum:.4g} (rel diff {gap:.1%} > tol {rel_tol:.0%})",
                        series_id=total_id, period=period,
                    )
                )
    if not applicable:
        return make_result("gate_a.sum_of_parts", skipped=True, note="no sum-of-parts rule's total+parts all present/touched this run")
    return make_result("gate_a.sum_of_parts", findings)


# ---------------------------------------------------------------------------
# 12. gate_a.cpi_envelope
# ---------------------------------------------------------------------------

CPI_HEADLINE = "nbs-cpi-yoy"
CPI_SUBITEMS = ["nbs-cpi-food-yoy", "nbs-cpi-nonfood-yoy", "nbs-cpi-core-yoy", "nbs-cpi-services-yoy", "nbs-cpi-goods-yoy"]


def check_cpi_envelope(ctx: GateContext):
    """gate_a.cpi_envelope -- WARN. Headline YoY must sit within [min, max] of
    whichever sub-item YoYs are present this period -- true for ANY weighted
    mean regardless of the (unpublished) weights, so the envelope needs none."""
    if not (ctx.is_touched(CPI_HEADLINE) or any(ctx.is_touched(s) for s in CPI_SUBITEMS)):
        return make_result("gate_a.cpi_envelope", skipped=True, note="no CPI series touched this run")

    headline = ctx.load(CPI_HEADLINE)
    if headline is None:
        return make_result("gate_a.cpi_envelope", skipped=True, note="headline CPI series not found")

    sub_data = {sid: ctx.load(sid) for sid in CPI_SUBITEMS}
    sub_data = {sid: d for sid, d in sub_data.items() if d is not None}
    if not sub_data:
        return make_result("gate_a.cpi_envelope", skipped=True, note="no CPI sub-item series found")

    headline_by_period = {o["period"]: o.get("m_yoy") for o in headline.get("observations", [])}
    touched_periods = set(ctx.batch.periods_for(CPI_HEADLINE))
    for sid in sub_data:
        touched_periods |= set(ctx.batch.periods_for(sid))
    if not touched_periods:
        touched_periods = set(headline_by_period)

    epsilon = 0.05
    findings = []
    for period in sorted(touched_periods, key=period_sort_key):
        headline_yoy = headline_by_period.get(period)
        if not is_number(headline_yoy):
            continue
        sub_vals = []
        for data in sub_data.values():
            obs = next((o for o in data.get("observations", []) if o["period"] == period), None)
            if obs is not None and is_number(obs.get("m_yoy")):
                sub_vals.append(obs["m_yoy"])
        if len(sub_vals) < 2:
            continue
        lo, hi = min(sub_vals), max(sub_vals)
        if not (lo - epsilon <= headline_yoy <= hi + epsilon):
            findings.append(
                Finding(
                    "gate_a.cpi_envelope", WARN,
                    f"headline m_yoy={headline_yoy} outside present sub-item envelope [{lo}, {hi}]",
                    series_id=CPI_HEADLINE, period=period,
                )
            )
    return make_result("gate_a.cpi_envelope", findings)


# ---------------------------------------------------------------------------
# 13. gate_a.online_share_bounds
# ---------------------------------------------------------------------------

SHARE_RULES = [{"share": "nbs-retail-online-share", "part": "nbs-retail-online-goods", "whole": "nbs-retail-ex-auto"}]


def check_online_share_bounds(ctx: GateContext):
    """gate_a.online_share_bounds -- WARN. Derived share ratios must sit in
    [0,100]; the online-goods numerator must not exceed its own denominator."""
    findings = []
    applicable = False
    for rule in SHARE_RULES:
        share_id, part_id, whole_id = rule["share"], rule["part"], rule["whole"]
        if not any(ctx.is_touched(x) for x in (share_id, part_id, whole_id)):
            continue
        applicable = True

        share_data = ctx.load(share_id)
        if share_data is not None:
            for period in ctx.touched_periods(share_id, share_data):
                obs = next((o for o in share_data.get("observations", []) if o["period"] == period), None)
                if obs is None:
                    continue
                value = obs.get("m") if obs.get("m") is not None else obs.get("ytd")
                if is_number(value) and not (0 <= value <= 100):
                    findings.append(Finding("gate_a.online_share_bounds", WARN, f"{share_id} share={value} outside [0,100]", series_id=share_id, period=period))

        part_data, whole_data = ctx.load(part_id), ctx.load(whole_id)
        if part_data is not None and whole_data is not None:
            whole_by_period = {o["period"]: o.get("m") for o in whole_data.get("observations", [])}
            for period in ctx.touched_periods(part_id, part_data) | ctx.touched_periods(whole_id, whole_data):
                part_obs = next((o for o in part_data.get("observations", []) if o["period"] == period), None)
                part_v = part_obs.get("m") if part_obs else None
                whole_v = whole_by_period.get(period)
                if is_number(part_v) and is_number(whole_v) and part_v > whole_v * 1.001:
                    findings.append(
                        Finding("gate_a.online_share_bounds", WARN, f"{part_id} m={part_v} exceeds {whole_id} m={whole_v}", series_id=part_id, period=period)
                    )
    if not applicable:
        return make_result("gate_a.online_share_bounds", skipped=True, note="no online-share rule touched this run")
    return make_result("gate_a.online_share_bounds", findings)
