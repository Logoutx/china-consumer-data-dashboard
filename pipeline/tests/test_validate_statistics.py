"""Tests for pipeline/validate/checks/statistics.py: gate_a.seasonal_z,
gate_a.ytd_arithmetic, gate_a.yoy_base_tolerance, gate_a.sum_of_parts,
gate_a.cpi_envelope, gate_a.online_share_bounds."""
from __future__ import annotations

from pipeline.tests._validate_helpers import load_fixture_series, make_batch, make_context, make_test_config, touch
from pipeline.validate.checks.statistics import (
    check_cpi_envelope,
    check_online_share_bounds,
    check_seasonal_z,
    check_sum_of_parts,
    check_ytd_arithmetic,
    check_yoy_base_tolerance,
)
from pipeline.validate.config import KnownDisagreement
from pipeline.validate.model import BLOCK, PASS, SKIP, WARN

# -- 6. gate_a.seasonal_z ------------------------------------------------------


def test_seasonal_z_passes_for_an_unremarkable_new_print(tmp_path):
    data = load_fixture_series("nbs-retail-total")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_seasonal_z(ctx)
    assert result.status == PASS


def test_seasonal_z_warns_at_moderate_z(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m_yoy"] = round(last["m_yoy"] + 1.3, 2)  # calibrated to land between z_warn and z_block
    batch = make_batch([touch("nbs-retail-total", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_seasonal_z(ctx)
    assert result.status == WARN


def test_seasonal_z_blocks_at_extreme_z(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m_yoy"] = round(last["m_yoy"] + 5.0, 2)  # comfortably past z_block
    batch = make_batch([touch("nbs-retail-total", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_seasonal_z(ctx)
    assert result.status == BLOCK


def test_seasonal_z_demotes_to_warn_when_cross_source_confirmed(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m_yoy"] = round(last["m_yoy"] + 5.0, 2)
    batch = make_batch([touch("nbs-retail-total", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    ctx.confirmed_cross_source_matches.add(("nbs-retail-total", last["period"]))
    result = check_seasonal_z(ctx)
    assert result.status == WARN
    assert any("demoted" in f.message for f in result.findings)


def test_seasonal_z_skips_short_history_series(tmp_path):
    series = {
        "schema": "series/v1", "id": "test-short-hist", "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": f"2025-{m:02d}", "m": 100 + m} for m in range(1, 6)],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    batch = make_batch([touch("test-short-hist", "2025-05", m=999)])
    ctx = make_context(tmp_path, staged_overrides={"test-short-hist": series}, batch=batch)
    result = check_seasonal_z(ctx)
    assert result.status == SKIP


# -- 9. gate_a.ytd_arithmetic ---------------------------------------------------


def test_ytd_arithmetic_passes_for_a_consistent_series(tmp_path):
    data = load_fixture_series("nbs-retail-total")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"], ytd=last["ytd"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_ytd_arithmetic(ctx)
    assert result.status == PASS


def test_ytd_arithmetic_warns_when_only_the_new_period_is_in_this_batch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["ytd"] = last["ytd"] + 500  # breaks ytd(t) == ytd(t-1) + m(t)
    batch = make_batch([touch("nbs-retail-total", last["period"], ytd=last["ytd"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_ytd_arithmetic(ctx)
    assert result.status == WARN


def test_ytd_arithmetic_blocks_when_both_periods_came_from_this_batch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    prior_period = broken["observations"][-2]["period"]
    last["ytd"] = last["ytd"] + 500
    batch = make_batch([touch("nbs-retail-total", last["period"], ytd=last["ytd"]), touch("nbs-retail-total", prior_period, ytd=broken["observations"][-2]["ytd"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_ytd_arithmetic(ctx)
    assert result.status == BLOCK


def test_ytd_arithmetic_warns_on_jan_feb_m_ytd_mismatch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    feb_obs = next(o for o in broken["observations"] if "jan_feb" in o.get("flags", []))
    feb_obs["ytd"] = feb_obs["ytd"] + 300  # m == ytd should hold on a jan_feb print
    batch = make_batch([touch("nbs-retail-total", feb_obs["period"], ytd=feb_obs["ytd"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_ytd_arithmetic(ctx)
    assert result.status == WARN


def test_ytd_arithmetic_skips_a_yoy_pct_series_even_with_non_additive_ytd(tmp_path):
    """nbs-industrial-va-style series store ONLY growth rates: 累计同比 is not
    additive from monthly rates, so a "mismatch" here is not a real error and
    must be skipped, not flagged."""
    series = {
        "schema": "series/v1", "id": "test-industrial-va", "name_zh": "x", "name_en": "x",
        "unit_zh": "%", "unit_en": "%", "value_type": "yoy_pct", "freq": "M",
        "calibers": ["single", "ytd"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [
            {"period": "2026-04", "m": 5.6, "ytd": 5.6},
            {"period": "2026-05", "m": 4.5, "ytd": 5.4},  # ytd(t) != ytd(t-1)+m(t) by construction, not by error
        ],
        "revisions": [], "breaks": [], "generated_at": "2026-06-01T00:00:00Z",
    }
    batch = make_batch([touch("test-industrial-va", "2026-05", m=4.5, ytd=5.4)])
    ctx = make_context(tmp_path, staged_overrides={"test-industrial-va": series}, batch=batch)
    result = check_ytd_arithmetic(ctx)
    assert result.status == SKIP


# -- 10. gate_a.yoy_base_tolerance -----------------------------------------------


def test_yoy_base_tolerance_passes_when_derived_yoy_matches(tmp_path):
    data = load_fixture_series("nbs-retail-total")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_yoy_base_tolerance(ctx)
    assert result.status == PASS


def test_yoy_base_tolerance_warns_on_a_moderate_gap(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m_yoy"] = round(last["m_yoy"] + 5.0, 2)  # > default tol of 3.0pp, but not impossible
    batch = make_batch([touch("nbs-retail-total", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_yoy_base_tolerance(ctx)
    assert result.status == WARN


def test_yoy_base_tolerance_blocks_impossible_math(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m_yoy"] = -250.0  # implies a negative prior-year level -- impossible
    batch = make_batch([touch("nbs-retail-total", last["period"], m_yoy=-250.0)])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_yoy_base_tolerance(ctx)
    assert result.status == BLOCK


def test_yoy_base_tolerance_skips_an_index_typed_series(tmp_path):
    """nbs-ppi-producer-yoy-style series: m is an index-typed value, not a
    chained level, so deriving 'the YoY' from a level ratio is meaningless --
    must be skipped regardless of how far apart published vs. derived look."""
    broken = load_fixture_series("nbs-cpi-yoy")
    last = broken["observations"][-1]
    last["m_yoy"] = last["m_yoy"] + 5.0  # would clearly WARN if this were value_type=="level"
    batch = make_batch([touch("nbs-cpi-yoy", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-cpi-yoy": broken}, batch=batch)
    result = check_yoy_base_tolerance(ctx)
    assert result.status == SKIP


# -- 11. gate_a.sum_of_parts ------------------------------------------------------


def test_sum_of_parts_passes_when_parts_add_up(tmp_path):
    data = load_fixture_series("nbs-retail-total")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-total", "nbs-retail-urban", "nbs-retail-rural", "nbs-retail-goods", "nbs-retail-catering"], batch=batch)
    result = check_sum_of_parts(ctx)
    assert result.status == PASS


def test_sum_of_parts_warns_when_total_disagrees_with_parts(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m"] = last["m"] + 200  # urban+rural / goods+catering no longer add up
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"])])
    ctx = make_context(
        tmp_path,
        staged_overrides={"nbs-retail-total": broken},
        touched=["nbs-retail-total", "nbs-retail-urban", "nbs-retail-rural", "nbs-retail-goods", "nbs-retail-catering"],
        batch=batch,
    )
    result = check_sum_of_parts(ctx)
    assert result.status == WARN


def test_sum_of_parts_skips_periods_acknowledged_by_known_disagreements(tmp_path):
    broken = load_fixture_series("mof-real-estate-tax-total")
    last = broken["observations"][-1]
    last["ytd"] = last["ytd"] + 50
    batch = make_batch([touch("mof-real-estate-tax-total", last["period"], ytd=last["ytd"])])
    config = make_test_config(
        known_disagreements=[KnownDisagreement(series="mof-real-estate-tax-total", periods=[last["period"]], checks=["gate_a.sum_of_parts"], note="test ack")]
    )
    ctx = make_context(
        tmp_path,
        staged_overrides={"mof-real-estate-tax-total": broken},
        touched=["mof-real-estate-tax-total", "mof-deed-tax", "mof-property-tax", "mof-urban-land-use-tax", "mof-land-vat", "mof-farmland-occupation-tax"],
        batch=batch,
        config=config,
    )
    result = check_sum_of_parts(ctx)
    assert result.status == PASS
    assert result.findings == []


# -- 12. gate_a.cpi_envelope ------------------------------------------------------


def test_cpi_envelope_passes_when_headline_is_inside_the_envelope(tmp_path):
    data = load_fixture_series("nbs-cpi-yoy")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-cpi-yoy", last["period"], m_yoy=last["m_yoy"])])
    ctx = make_context(tmp_path, touched=["nbs-cpi-yoy"], batch=batch)
    result = check_cpi_envelope(ctx)
    assert result.status == PASS


def test_cpi_envelope_warns_when_headline_is_outside_the_envelope(tmp_path):
    broken = load_fixture_series("nbs-cpi-yoy")
    last = broken["observations"][-1]
    last["m_yoy"] = 10.0  # well outside every sub-item's YoY this period
    batch = make_batch([touch("nbs-cpi-yoy", last["period"], m_yoy=10.0)])
    ctx = make_context(tmp_path, staged_overrides={"nbs-cpi-yoy": broken}, batch=batch)
    result = check_cpi_envelope(ctx)
    assert result.status == WARN


# -- 13. gate_a.online_share_bounds ------------------------------------------------


def test_online_share_bounds_passes_for_a_normal_share(tmp_path):
    data = load_fixture_series("nbs-retail-online-share")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-online-share", last["period"], m=last["m"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-online-share", "nbs-retail-online-goods", "nbs-retail-ex-auto"], batch=batch)
    result = check_online_share_bounds(ctx)
    assert result.status == PASS


def test_online_share_bounds_warns_outside_0_100(tmp_path):
    broken = load_fixture_series("nbs-retail-online-share")
    last = broken["observations"][-1]
    last["m"] = 150.0
    batch = make_batch([touch("nbs-retail-online-share", last["period"], m=150.0)])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-online-share": broken}, batch=batch)
    result = check_online_share_bounds(ctx)
    assert result.status == WARN


def test_online_share_bounds_warns_when_part_exceeds_whole(tmp_path):
    broken = load_fixture_series("nbs-retail-online-goods")
    last = broken["observations"][-1]
    last["m"] = 999999  # exceeds nbs-retail-ex-auto's m at the same period
    batch = make_batch([touch("nbs-retail-online-goods", last["period"], m=999999)])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-online-goods": broken}, touched=["nbs-retail-online-goods", "nbs-retail-ex-auto"], batch=batch)
    result = check_online_share_bounds(ctx)
    assert result.status == WARN
