"""Shape-and-bounds checks: gate_a.schema_series, gate_a.caliber_declared,
gate_a.value_type_bounds, gate_a.period_monotonic, gate_a.unit_magnitude,
gate_a.catalog_consistency."""
from __future__ import annotations

from statistics import median

from pipeline.migrate import schema_validator
from pipeline.validate.context import GateContext
from pipeline.validate.model import BLOCK, WARN, Finding, make_result
from pipeline.validate.util import is_number, period_shape, period_sort_key

# DATA-CONTRACT.md section 3.3: which measures a (value_type, caliber) combo
# legitimately carries. `mom` is the one documented real-data exception
# (CPI-family index series print a headline 涨跌幅 alongside the 定基 level) --
# see the module docstring in checks/__init__.py for the rest of the spec
# adaptations.
_SINGLE_MEASURES = {"m", "m_yoy"}
_YTD_MEASURES = {"ytd", "ytd_yoy"}


def check_schema_series(ctx: GateContext):
    """gate_a.schema_series -- BLOCK. Every staged file validates against
    data/schemas/series.schema.json or panel.schema.json (by its own "schema"
    tag), via the migrate-stage's stdlib-only draft-07 subset validator (no
    jsonschema dependency, per this milestone's constraint)."""
    findings = []
    files = ctx.store.all_staged_files()
    if not files:
        return make_result("gate_a.schema_series", skipped=True, note="no staged files")
    for path in files:
        series_id = path.stem
        data = ctx.load(series_id)
        if data is None:
            continue
        schema_tag = data.get("schema")
        schema = ctx.schemas.get("panel") if schema_tag == "panel/v1" else ctx.schemas.get("series")
        if schema is None:
            findings.append(Finding("gate_a.schema_series", BLOCK, f"no schema loaded to validate schema tag {schema_tag!r}", series_id=series_id))
            continue
        errors = schema_validator.validate(data, schema)
        for error in errors:
            findings.append(Finding("gate_a.schema_series", BLOCK, error, series_id=series_id))
    return make_result("gate_a.schema_series", findings)


def _allowed_measures(value_type: str | None, calibers: list[str]) -> set[str]:
    allowed: set[str] = set()
    if "single" in calibers:
        allowed |= _SINGLE_MEASURES
    if "ytd" in calibers:
        allowed |= _YTD_MEASURES
    if value_type == "index":
        # CPI-family real-data allowances (DATA-CONTRACT section 3.2/3.3): `mom`
        # is the documented one. `ytd_yoy` is a second one this task's own spec
        # didn't name but pipeline/parsers/nbs_cpi.py's already-accepted parser
        # requires -- NBS's CPI table carries a third column, "1-M月同比涨跌幅%"
        # (a cumulative-average-of-monthly YoY), for every row including the
        # headline, even though nbs-cpi-yoy's catalog entry declares
        # calibers:["single"] only (no "ytd" caliber). Without this, Gate A
        # would BLOCK every single real CPI release on measure alone.
        allowed.add("mom")
        allowed.add("ytd_yoy")
    allowed.add("real_yoy")  # income-style verbatim real YoY; not caliber-gated in the contract
    allowed.add("src")
    return allowed


def check_caliber_declared(ctx: GateContext):
    """gate_a.caliber_declared -- BLOCK. No measure illegal for the series'
    calibers x value_type (e.g. m/m_yoy on a ytd-only series, or vice versa).
    CPI-family index series legitimately carry `mom` and `ytd_yoy` as extra
    measures (see _allowed_measures for why both are real-data allowances,
    not spec violations)."""
    findings = []
    for series_id, data in ctx.touched_series_dicts():
        calibers = data.get("calibers", [])
        value_type = data.get("value_type")
        allowed = _allowed_measures(value_type, calibers)
        for obs in data.get("observations", []):
            present = {k for k in obs.keys() if k not in ("period", "freq", "span", "flags")}
            illegal = present - allowed
            for measure in sorted(illegal):
                findings.append(
                    Finding(
                        "gate_a.caliber_declared",
                        BLOCK,
                        f"measure {measure!r} illegal for value_type={value_type!r} calibers={calibers!r}",
                        series_id=series_id,
                        period=obs.get("period"),
                        measure=measure,
                    )
                )
    return make_result("gate_a.caliber_declared", findings)


def check_value_type_bounds(ctx: GateContext):
    """gate_a.value_type_bounds -- BLOCK on absurd. count>=0 integer;
    rate_pct m in [0,100]; mom_pct m in [-25,25]; index/level/ratio YoY within
    the configured yoy_band. Scoped to the observations this run actually
    touched (an ingest gate judges the new print, not the whole history)."""
    findings = []
    for series_id, data in ctx.touched_series_dicts():
        value_type = data.get("value_type")
        touched_periods = {p for _sid, p in _touched_periods(ctx, series_id)}
        for obs in data.get("observations", []):
            if touched_periods and obs.get("period") not in touched_periods:
                continue
            findings.extend(_bounds_for_obs(ctx, series_id, value_type, obs))
    return make_result("gate_a.value_type_bounds", findings)


def _touched_periods(ctx: GateContext, series_id: str):
    return [(item.series_id, item.period) for item in ctx.batch.items_for(series_id)]


def _bounds_for_obs(ctx: GateContext, series_id: str, value_type: str | None, obs: dict) -> list[Finding]:
    findings: list[Finding] = []
    period = obs.get("period")

    if value_type == "count":
        value = obs.get("m")
        if is_number(value):
            if value < 0 or abs(value - round(value)) > 1e-6:
                findings.append(Finding("gate_a.value_type_bounds", BLOCK, f"count value {value!r} not a non-negative integer", series_id=series_id, period=period, measure="m"))

    if value_type == "rate_pct":
        value = obs.get("m")
        if is_number(value) and not (0 <= value <= 100):
            findings.append(Finding("gate_a.value_type_bounds", BLOCK, f"rate_pct m={value} outside [0,100]", series_id=series_id, period=period, measure="m"))

    if value_type == "mom_pct":
        value = obs.get("m")
        if is_number(value) and not (-25 <= value <= 25):
            findings.append(Finding("gate_a.value_type_bounds", BLOCK, f"mom_pct m={value} outside [-25,25]", series_id=series_id, period=period, measure="m"))

    if value_type == "ratio":
        value = obs.get("m") if obs.get("m") is not None else obs.get("ytd")
        if is_number(value) and not (0 <= value <= 100):
            findings.append(Finding("gate_a.value_type_bounds", BLOCK, f"ratio value {value} outside [0,100]", series_id=series_id, period=period))

    if value_type in ("index", "level", "ratio"):
        lo, hi = ctx.config.yoy_band(series_id, value_type)
        for measure in ("m_yoy", "ytd_yoy"):
            value = obs.get(measure)
            if is_number(value) and not (lo <= value <= hi):
                findings.append(
                    Finding(
                        "gate_a.value_type_bounds",
                        BLOCK,
                        f"{measure}={value} outside configured yoy_band [{lo}, {hi}]",
                        series_id=series_id,
                        period=period,
                        measure=measure,
                    )
                )
    return findings


def check_unit_magnitude(ctx: GateContext):
    """gate_a.unit_magnitude -- BLOCK. The new level vs median(|last 12
    levels|): if the ratio (or its inverse) is >= unit_slip_factor, this
    looks like a 亿/万亿 unit slip, not a real 100x move. Scoped to
    value_type=="level" series (an index/rate/count/ratio print isn't
    denominated in a unit that can "slip" by 100x the same way). Both the new
    observation and the history pool are restricted to span==1 (ordinary
    single-period) prints -- a Jan-Feb combined print is legitimately ~2x a
    normal month (DATA-CONTRACT section 3.4's own worked example: 83726 vs a
    normal ~40940) and would otherwise skew the baseline or look like a false
    slip on a completely unremarkable release."""
    findings = []
    evaluated = False
    for series_id, data in ctx.touched_series_dicts():
        value_type = data.get("value_type")
        if value_type != "level":
            continue
        calibers = data.get("calibers", [])
        level_key = "m" if "single" in calibers else "ytd" if "ytd" in calibers else None
        if level_key is None:
            continue
        observations = sorted(data.get("observations", []), key=lambda o: period_sort_key(o["period"]))
        slip_factor = ctx.config.unit_slip_factor(series_id, value_type)

        for period in ctx.touched_periods(series_id, data):
            idx = next((i for i, o in enumerate(observations) if o["period"] == period), None)
            if idx is None:
                continue
            new_obs = observations[idx]
            if new_obs.get("span", 1) != 1:
                continue  # a merged (e.g. jan_feb) print isn't comparable to single-period history
            new_value = new_obs.get(level_key)
            if not is_number(new_value):
                continue
            prior_levels = [abs(o[level_key]) for o in observations[:idx] if is_number(o.get(level_key)) and o.get("span", 1) == 1][-12:]
            if len(prior_levels) < 3:
                continue  # not enough history yet to judge a unit slip
            evaluated = True
            baseline = median(prior_levels)
            if baseline == 0 or new_value == 0:
                continue
            ratio = abs(new_value) / baseline
            if ratio >= slip_factor or (1 / ratio) >= slip_factor:
                findings.append(
                    Finding(
                        "gate_a.unit_magnitude", BLOCK,
                        f"{level_key}={new_value} vs median(|last {len(prior_levels)}|)={baseline:.4g}: ratio {ratio:.4g} suggests a 亿/万亿 unit slip (factor >= {slip_factor})",
                        series_id=series_id, period=period, measure=level_key,
                    )
                )
    if not evaluated:
        return make_result("gate_a.unit_magnitude", skipped=True, note="no value_type=='level' series had enough prior single-period history to judge a unit slip")
    return make_result("gate_a.unit_magnitude", findings)


def check_period_monotonic(ctx: GateContext):
    """gate_a.period_monotonic -- BLOCK. Ascending unique periods; each
    period string's shape matches whatever freq applies to that observation
    (its own `freq` override if present, else the series default); jan_feb
    observations anchor at YYYY-02 with span:2 (and nothing else claims
    span:2 without the flag) -- normalize.py already enforces the jan_feb
    rule at merge time, this is a defensive re-check for any staged file that
    reached Gate A by another path (e.g. the standalone CLI)."""
    findings = []
    for series_id, data in ctx.touched_series_dicts():
        default_freq = data.get("freq")
        observations = data.get("observations", [])
        keys = [period_sort_key(obs["period"]) for obs in observations]
        if keys != sorted(keys):
            findings.append(Finding("gate_a.period_monotonic", BLOCK, "observations are not in ascending period order", series_id=series_id))
        periods = [obs["period"] for obs in observations]
        if len(set(periods)) != len(periods):
            findings.append(Finding("gate_a.period_monotonic", BLOCK, "duplicate period in observations", series_id=series_id))

        for obs in observations:
            period = obs["period"]
            freq = obs.get("freq", default_freq)
            shape = period_shape(period)
            if shape is None:
                findings.append(Finding("gate_a.period_monotonic", BLOCK, f"period {period!r} matches no known shape", series_id=series_id, period=period))
                continue
            if freq and shape != freq:
                findings.append(
                    Finding(
                        "gate_a.period_monotonic",
                        BLOCK,
                        f"period {period!r} has shape {shape!r} but applicable freq is {freq!r}",
                        series_id=series_id,
                        period=period,
                    )
                )
            flags = obs.get("flags", [])
            span = obs.get("span", 1)
            if "jan_feb" in flags and not (period.endswith("-02") and span == 2):
                findings.append(Finding("gate_a.period_monotonic", BLOCK, "jan_feb flag requires period YYYY-02 and span:2", series_id=series_id, period=period))
            if span == 2 and "jan_feb" not in flags:
                findings.append(Finding("gate_a.period_monotonic", BLOCK, "span:2 without jan_feb flag", series_id=series_id, period=period))
    return make_result("gate_a.period_monotonic", findings)


def check_catalog_consistency(ctx: GateContext):
    """gate_a.catalog_consistency -- BLOCK on mismatch, WARN on unmapped-new.
    Staged series must exist in the catalog with matching value_type /
    calibers / section; id-in-file == filename == catalog id; every raw
    source_field this batch carried must map to *something* (unmapped is a
    WARN listing, not a block -- a brand new commodity category is a normal,
    recoverable state for a poller, matching normalize.py's own framing of
    unmapped_fields)."""
    findings = []
    for series_id, data in ctx.touched_series_dicts():
        if data.get("id") != series_id:
            findings.append(Finding("gate_a.catalog_consistency", BLOCK, f"series id {data.get('id')!r} != filename stem {series_id!r}", series_id=series_id))
        cat = ctx.catalog_by_id.get(series_id)
        if cat is None:
            if series_id in ctx.config.orphan_ok:
                continue
            findings.append(Finding("gate_a.catalog_consistency", BLOCK, "series id not present in data/catalog.json", series_id=series_id))
            continue
        for field_name in ("value_type", "calibers"):
            if cat.get(field_name) != data.get(field_name):
                findings.append(
                    Finding(
                        "gate_a.catalog_consistency",
                        BLOCK,
                        f"catalog {field_name}={cat.get(field_name)!r} != series file {field_name}={data.get(field_name)!r}",
                        series_id=series_id,
                    )
                )
        if cat.get("file") and not cat["file"].endswith(f"/{series_id}.json"):
            findings.append(Finding("gate_a.catalog_consistency", BLOCK, f"catalog file path {cat.get('file')!r} does not match id {series_id!r}", series_id=series_id))

    for field_name in sorted(ctx.batch.unmapped_source_fields):
        findings.append(Finding("gate_a.catalog_consistency", WARN, f"source_field {field_name!r} did not map to any series id", detail={"source_field": field_name}))

    return make_result("gate_a.catalog_consistency", findings)
