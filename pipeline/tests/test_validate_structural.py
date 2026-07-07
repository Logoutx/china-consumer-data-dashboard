"""Tests for pipeline/validate/checks/structural.py: gate_a.schema_series,
gate_a.caliber_declared, gate_a.value_type_bounds, gate_a.period_monotonic,
gate_a.unit_magnitude, gate_a.catalog_consistency. One failing + one passing
case per check, per the task spec."""
from __future__ import annotations

import copy

from pipeline.tests._validate_helpers import load_fixture_series, make_batch, make_context, touch
from pipeline.validate.checks.structural import (
    check_caliber_declared,
    check_catalog_consistency,
    check_period_monotonic,
    check_schema_series,
    check_unit_magnitude,
    check_value_type_bounds,
)
from pipeline.validate.model import BLOCK, PASS, SKIP, WARN

# -- 1. gate_a.schema_series --------------------------------------------------


def test_schema_series_passes_on_a_valid_staged_file(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"])
    result = check_schema_series(ctx)
    assert result.status == PASS


def test_schema_series_blocks_on_a_missing_required_field(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    del broken["value_type"]  # required by series.schema.json
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_schema_series(ctx)
    assert result.status == BLOCK
    assert any("value_type" in f.message for f in result.findings)


# -- 2. gate_a.caliber_declared -----------------------------------------------


def test_caliber_declared_passes_for_matching_measures(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total", "nbs-cpi-yoy"])
    result = check_caliber_declared(ctx)
    assert result.status == PASS  # includes the CPI mom real-data allowance


def test_caliber_declared_blocks_bare_m_on_ytd_only_series(tmp_path):
    broken = load_fixture_series("test-fai")  # calibers: ["ytd"] only
    broken["observations"][-1]["m"] = 12345  # illegal: no 'single' caliber
    ctx = make_context(tmp_path, staged_overrides={"test-fai": broken})
    result = check_caliber_declared(ctx)
    assert result.status == BLOCK
    assert any(f.measure == "m" and f.series_id == "test-fai" for f in result.findings)


def test_caliber_declared_blocks_mom_on_a_non_index_series(tmp_path):
    broken = load_fixture_series("nbs-retail-total")  # value_type: level
    broken["observations"][-1]["mom"] = 1.0
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_caliber_declared(ctx)
    assert result.status == BLOCK
    assert any(f.measure == "mom" for f in result.findings)


# -- 3. gate_a.value_type_bounds ----------------------------------------------


def test_value_type_bounds_passes_for_a_normal_rate(tmp_path):
    batch = make_batch([touch("test-unemp", "2025-12", m=5.1)])
    ctx = make_context(tmp_path, touched=["test-unemp"], batch=batch)
    result = check_value_type_bounds(ctx)
    assert result.status == PASS


def test_value_type_bounds_blocks_rate_pct_outside_0_100(tmp_path):
    broken = load_fixture_series("test-unemp")
    broken["observations"][-1]["m"] = 150.0
    batch = make_batch([touch("test-unemp", broken["observations"][-1]["period"], m=150.0)])
    ctx = make_context(tmp_path, staged_overrides={"test-unemp": broken}, batch=batch)
    result = check_value_type_bounds(ctx)
    assert result.status == BLOCK
    assert any("rate_pct" in f.message for f in result.findings)


def test_value_type_bounds_blocks_count_negative_or_non_integer(tmp_path):
    series = {
        "schema": "series/v1", "id": "test-count", "name_zh": "x", "name_en": "x",
        "unit_zh": "个", "unit_en": "cities", "value_type": "count", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2025-12", "m": -3.5}],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    batch = make_batch([touch("test-count", "2025-12", m=-3.5)])
    ctx = make_context(tmp_path, staged_overrides={"test-count": series}, batch=batch)
    result = check_value_type_bounds(ctx)
    assert result.status == BLOCK


def test_value_type_bounds_blocks_mom_pct_outside_range(tmp_path):
    series = {
        "schema": "series/v1", "id": "test-mompct", "name_zh": "x", "name_en": "x",
        "unit_zh": "%", "unit_en": "%", "value_type": "mom_pct", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2025-12", "m": 40.0}],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    batch = make_batch([touch("test-mompct", "2025-12", m=40.0)])
    ctx = make_context(tmp_path, staged_overrides={"test-mompct": series}, batch=batch)
    result = check_value_type_bounds(ctx)
    assert result.status == BLOCK


def test_value_type_bounds_blocks_yoy_outside_configured_band(tmp_path):
    broken = load_fixture_series("nbs-cpi-yoy")
    last = broken["observations"][-1]
    last["m_yoy"] = 500.0
    batch = make_batch([touch("nbs-cpi-yoy", last["period"], m_yoy=500.0)])
    ctx = make_context(tmp_path, staged_overrides={"nbs-cpi-yoy": broken}, batch=batch)
    result = check_value_type_bounds(ctx)
    assert result.status == BLOCK
    assert any(f.measure == "m_yoy" for f in result.findings)


# -- 4. gate_a.period_monotonic -----------------------------------------------


def test_period_monotonic_passes_for_a_well_formed_series(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"])
    result = check_period_monotonic(ctx)
    assert result.status == PASS


def test_period_monotonic_blocks_out_of_order_periods(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    broken["observations"].insert(0, copy.deepcopy(broken["observations"][-1]))  # latest period, out of order at index 0
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_period_monotonic(ctx)
    assert result.status == BLOCK


def test_period_monotonic_blocks_span2_without_jan_feb_flag(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    broken["observations"][-1]["span"] = 2  # no jan_feb flag on this (non-Feb) observation
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_period_monotonic(ctx)
    assert result.status == BLOCK


def test_period_monotonic_blocks_freq_shape_mismatch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    broken["observations"][-1]["freq"] = "Q"  # period is "YYYY-MM" shaped, not "YYYY-Qn"
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_period_monotonic(ctx)
    assert result.status == BLOCK


# -- 5. gate_a.unit_magnitude --------------------------------------------------


def test_unit_magnitude_passes_for_a_normal_new_level(tmp_path):
    data = load_fixture_series("nbs-retail-total")
    last = data["observations"][-1]
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"])])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_unit_magnitude(ctx)
    assert result.status == PASS


def test_unit_magnitude_blocks_a_100x_slip_up(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m"] = last["m"] * 100  # 亿 -> 万亿-scale slip
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_unit_magnitude(ctx)
    assert result.status == BLOCK


def test_unit_magnitude_blocks_a_100x_slip_down(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    last = broken["observations"][-1]
    last["m"] = last["m"] / 500  # comfortably past the ratio=100 threshold even against a trending baseline
    batch = make_batch([touch("nbs-retail-total", last["period"], m=last["m"])])
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken}, batch=batch)
    result = check_unit_magnitude(ctx)
    assert result.status == BLOCK


def test_unit_magnitude_skips_when_not_enough_history(tmp_path):
    series = {
        "schema": "series/v1", "id": "test-short", "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2025-11", "m": 100}, {"period": "2025-12", "m": 9999999}],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    batch = make_batch([touch("test-short", "2025-12", m=9999999)])
    ctx = make_context(tmp_path, staged_overrides={"test-short": series}, batch=batch)
    result = check_unit_magnitude(ctx)
    assert result.status == SKIP


# -- 22. gate_a.catalog_consistency -------------------------------------------


def test_catalog_consistency_passes_for_a_registered_series(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"])
    result = check_catalog_consistency(ctx)
    assert result.status == PASS


def test_catalog_consistency_blocks_on_id_filename_mismatch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    broken["id"] = "nbs-retail-total-typo"
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_catalog_consistency(ctx)
    assert result.status == BLOCK


def test_catalog_consistency_blocks_on_series_not_in_catalog(tmp_path):
    series = {
        "schema": "series/v1", "id": "test-orphan", "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2025-12", "m": 100}],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    ctx = make_context(tmp_path, staged_overrides={"test-orphan": series})
    result = check_catalog_consistency(ctx)
    assert result.status == BLOCK


def test_catalog_consistency_allows_orphan_ok_series(tmp_path):
    from pipeline.tests._validate_helpers import make_test_config

    series = {
        "schema": "series/v1", "id": "test-orphan", "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2025-12", "m": 100}],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }
    config = make_test_config(orphan_ok=["test-orphan"])
    ctx = make_context(tmp_path, staged_overrides={"test-orphan": series}, config=config)
    result = check_catalog_consistency(ctx)
    assert result.status == PASS


def test_catalog_consistency_blocks_value_type_mismatch(tmp_path):
    broken = load_fixture_series("nbs-retail-total")
    broken["value_type"] = "count"  # catalog fixture says "level"
    ctx = make_context(tmp_path, staged_overrides={"nbs-retail-total": broken})
    result = check_catalog_consistency(ctx)
    assert result.status == BLOCK


def test_catalog_consistency_warns_on_unmapped_source_field(tmp_path):
    batch = make_batch(unmapped_source_fields=["从未见过的字段"])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_catalog_consistency(ctx)
    assert result.status == WARN
    assert any("从未见过的字段" in f.message for f in result.findings)
