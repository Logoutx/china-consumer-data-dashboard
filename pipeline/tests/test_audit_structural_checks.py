"""Tests for the three purely-structural gate_b checks -- bundle_source_
consistency, yoy_break_nulls, latest_prev_resolution -- which are pure
functions of already-loaded bundle dicts. Two testing styles are used:

  1. Against the real clean_repo fixture (built by actually running
     pipeline.build) -- a realistic smoke test: "run against consistent,
     build.py-produced data, expect zero block findings."
  2. Hand-constructed minimal bundle entries -- precise, deterministic
     coverage of each individual rule (including edge cases the fixture's
     own latest/prev pairs don't happen to exercise, e.g. a Jan-Feb LATEST
     observation, since clean_repo's series all currently have a later,
     non-Jan-Feb latest print).

AuditContext is plain data (no hidden state), so style 2 just constructs one
directly with a single synthetic section bundle -- no need to run the real
build or touch disk.
"""
from __future__ import annotations

import copy
import random
from datetime import date

import pytest

from pipeline.audit.checks import bundle_source_consistency, latest_prev_resolution, yoy_break_nulls
from pipeline.audit.models import AuditContext
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo, make_ctx


def _bare_ctx(catalog: dict, section_bundles: dict, panels: dict | None = None) -> AuditContext:
    panels = panels or {}
    return AuditContext(
        repo_root=CLEAN_REPO,
        data_dir=CLEAN_REPO / "data",
        site_data_dir=CLEAN_REPO / "site-data",
        catalog=catalog,
        section_bundles=section_bundles,
        panel_bundle_loader=lambda panel_id: panels.get(panel_id),
        labels={},
        rng=random.Random(0),
        seed="unit-seed",
        run_id="unit-seed",
        offline=True,
        samples_per_section=5,
        as_of=date(2026, 7, 8),
        previous_diary=None,
    )


# =====================================================================================
# bundle_source_consistency
# =====================================================================================


def test_bundle_source_consistency_passes_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO)
    report = bundle_source_consistency.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    assert any(f.status == "pass" for f in report.findings)


def test_catches_series_missing_from_every_bundle():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    bundle = ctx.section_bundles["consumption"]
    bundle["series"] = [s for s in bundle["series"] if s["id"] != "test-retail-total"]
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    assert any(f.series == "test-retail-total" and f.status == "block" for f in report.findings)


def test_catches_series_duplicated_across_two_bundles():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    victim = next(s for s in ctx.section_bundles["consumption"]["series"] if s["id"] == "test-retail-total")
    ctx.section_bundles["macro"]["series"].append(copy.deepcopy(victim))
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    hit = [f for f in report.findings if f.series == "test-retail-total" and f.status == "block"]
    assert hit and "2 section bundles" in hit[0].note


def test_catches_series_bundled_under_the_wrong_section():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    victim = next(s for s in ctx.section_bundles["consumption"]["series"] if s["id"] == "test-retail-total")
    ctx.section_bundles["consumption"]["series"].remove(victim)
    ctx.section_bundles["macro"]["series"].append(victim)
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    hit = [f for f in report.findings if f.series == "test-retail-total" and f.status == "block"]
    assert hit and hit[0].expected == "consumption" and hit[0].observed == "macro"


def test_catches_panel_id_leaking_into_a_section_bundle():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    ctx.section_bundles["property"]["series"].append({"id": "test-70city-panel"})
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    assert any(f.panel == "test-70city-panel" and f.status == "block" for f in report.findings)


def test_catches_unknown_id_in_a_bundle():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    ctx.section_bundles["macro"]["series"].append({"id": "test-does-not-exist-in-catalog"})
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    assert any(f.series == "test-does-not-exist-in-catalog" for f in report.findings)


def test_catches_index_freshness_missing_a_series(tmp_path):
    import json

    repo_dir = copy_clean_repo(tmp_path)  # mutate a throwaway copy, never the committed fixture
    ctx = make_ctx(repo_dir)
    index_path = ctx.site_data_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["freshness"] = [row for row in index["freshness"] if row["id"] != "test-retail-total"]
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    report = bundle_source_consistency.run(ctx)
    assert report.has_block()
    assert any(f.series == "test-retail-total" for f in report.findings)


# =====================================================================================
# yoy_break_nulls
# =====================================================================================


def _synthetic_break_entry(**overrides) -> dict:
    entry = {
        "id": "synthetic-break-series",
        "tier": 1,
        "breaks": [{"effective": "2026-01", "kind": "rebase", "no_yoy_across": True, "yoy_valid_from": "2026-04"}],
        "yoy_series": [
            {"period": "2025-12", "yoy": 1.9},
            {"period": "2026-01", "yoy": None},
            {"period": "2026-02", "yoy": None},
            {"period": "2026-03", "yoy": None},
            {"period": "2026-04", "yoy": 2.2},
        ],
        # prev is None by default: the only earlier same-shape point in this
        # small example (2025-12) sits on the OTHER side of the break from
        # latest (2026-04), so a correct build would never expose it as prev
        # -- this is what "clean" actually looks like here, not a real
        # array-adjacent prev. test_catches_cross_break_prev_still_present
        # below overrides this to demonstrate the violation.
        "latest": {"period": "2026-04", "m": 100.7, "m_yoy": 2.2},
        "prev": None,
    }
    entry.update(overrides)
    return entry


def test_yoy_break_nulls_passes_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO)
    report = yoy_break_nulls.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    # test-cpi-break must actually have been exercised (not a vacuous pass).
    assert any(f.status == "pass" and "1" in (f.note or "") for f in report.findings)


def test_yoy_break_nulls_passes_for_a_clean_synthetic_entry():
    ctx = _bare_ctx({"sections": []}, {"prices": {"series": [_synthetic_break_entry()]}})
    report = yoy_break_nulls.run(ctx)
    assert not report.has_block()


def test_catches_a_nonnull_yoy_inside_the_blocked_window():
    entry = _synthetic_break_entry()
    entry["yoy_series"][2]["yoy"] = 1.5  # 2026-02, inside [2026-01, 2026-04)
    ctx = _bare_ctx({"sections": []}, {"prices": {"series": [entry]}})
    report = yoy_break_nulls.run(ctx)
    assert report.has_block()
    hit = [f for f in report.findings if f.status == "block" and f.field == "yoy_series.yoy"]
    assert hit and hit[0].period == "2026-02"


def test_catches_latest_block_exposing_yoy_key_inside_window():
    entry = _synthetic_break_entry(latest={"period": "2026-02", "m": 100.3, "m_yoy": 1.1}, prev=None)
    ctx = _bare_ctx({"sections": []}, {"prices": {"series": [entry]}})
    report = yoy_break_nulls.run(ctx)
    assert report.has_block()
    assert any(f.field == "latest.m_yoy" for f in report.findings)


def test_catches_cross_break_prev_still_present():
    entry = _synthetic_break_entry(latest={"period": "2026-04", "m": 100.7, "m_yoy": 2.2}, prev={"period": "2025-12", "m": 101.9})
    ctx = _bare_ctx({"sections": []}, {"prices": {"series": [entry]}})
    report = yoy_break_nulls.run(ctx)
    assert report.has_block()
    assert any(f.field == "prev" and f.status == "block" for f in report.findings)


# =====================================================================================
# latest_prev_resolution
# =====================================================================================


def test_latest_prev_resolution_passes_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO)
    report = latest_prev_resolution.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]


def test_jan_feb_prev_must_be_last_years_jan_feb():
    good = {
        "id": "s1", "tier": 1, "breaks": [], "flags_latest": ["jan_feb"],
        "latest": {"period": "2026-02"}, "prev": {"period": "2025-02"},
    }
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [good]}})
    assert not latest_prev_resolution.run(ctx).has_block()

    bad = {
        "id": "s2", "tier": 1, "breaks": [], "flags_latest": ["jan_feb"],
        "latest": {"period": "2026-02"}, "prev": {"period": "2025-12"},  # array-adjacent December -- wrong
    }
    ctx2 = _bare_ctx({"sections": []}, {"x": {"series": [bad]}})
    report = latest_prev_resolution.run(ctx2)
    assert report.has_block()
    assert any(f.series == "s2" and f.expected == "2025-02" for f in report.findings)


def test_jan_feb_missing_prev_entirely_is_a_block():
    entry = {"id": "s3", "tier": 1, "breaks": [], "flags_latest": ["jan_feb"], "latest": {"period": "2026-02"}, "prev": None}
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [entry]}})
    report = latest_prev_resolution.run(ctx)
    assert report.has_block()


def test_prev_must_share_latest_period_shape():
    entry = {
        "id": "s4", "tier": 2, "breaks": [], "flags_latest": [],
        "latest": {"period": "2026-Q2"}, "prev": {"period": "2025"},  # quarterly vs annual
    }
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [entry]}})
    report = latest_prev_resolution.run(ctx)
    assert report.has_block()
    assert any("shape" in (f.note or "") for f in report.findings)


def test_prev_across_a_no_yoy_break_must_be_null():
    entry = {
        "id": "s5", "tier": 1, "flags_latest": [],
        "breaks": [{"effective": "2026-01", "no_yoy_across": True}],
        "latest": {"period": "2026-02"}, "prev": {"period": "2025-12"},
    }
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [entry]}})
    report = latest_prev_resolution.run(ctx)
    assert report.has_block()


def test_prev_correctly_null_across_a_break_passes():
    entry = {
        "id": "s6", "tier": 1, "flags_latest": [],
        "breaks": [{"effective": "2026-01", "no_yoy_across": True}],
        "latest": {"period": "2026-01"}, "prev": None,
    }
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [entry]}})
    assert not latest_prev_resolution.run(ctx).has_block()


@pytest.mark.parametrize("shape_a,shape_b", [("2026-Q1", "2026-Q4"), ("2026-05", "2025-05")])
def test_same_shape_different_period_still_passes_when_no_break_involved(shape_a, shape_b):
    entry = {"id": "s7", "tier": 3, "breaks": [], "flags_latest": [], "latest": {"period": shape_a}, "prev": {"period": shape_b}}
    ctx = _bare_ctx({"sections": []}, {"x": {"series": [entry]}})
    assert not latest_prev_resolution.run(ctx).has_block()
