"""Tests for pipeline/validate/checks/cross_source.py: gate_a.triangulate_dg_press,
gate_a.triangulate_pbc_nbs. Both default to SKIP when only one view of "the
same number" is available -- that is the everyday outcome for a single-parser
runner.py invocation (see the module docstring), so it gets its own test too."""
from __future__ import annotations

from pipeline.tests._validate_helpers import load_fixture_series, make_batch, make_context, make_test_config, touch
from pipeline.validate.checks.cross_source import check_triangulate_dg_press, check_triangulate_pbc_nbs
from pipeline.validate.config import SourcePair
from pipeline.validate.model import BLOCK, PASS, SKIP, WARN

# -- 7. gate_a.triangulate_dg_press --------------------------------------------


def test_triangulate_dg_press_skips_when_only_one_path_present(tmp_path):
    batch = make_batch([touch("nbs-retail-total", "2026-01", source_kind="press", m=100.0)])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_triangulate_dg_press(ctx)
    assert result.status == SKIP


def test_triangulate_dg_press_passes_and_confirms_when_both_paths_agree(tmp_path):
    batch = make_batch(
        [
            touch("nbs-retail-total", "2026-01", source_kind="dg", m=100.0),
            touch("nbs-retail-total", "2026-01", source_kind="press", m=100.0),
        ]
    )
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_triangulate_dg_press(ctx)
    assert result.status == PASS
    assert ("nbs-retail-total", "2026-01") in ctx.confirmed_cross_source_matches


def test_triangulate_dg_press_blocks_when_both_paths_disagree(tmp_path):
    batch = make_batch(
        [
            touch("nbs-retail-total", "2026-01", source_kind="dg", m=100.0),
            touch("nbs-retail-total", "2026-01", source_kind="press", m=105.0),
        ]
    )
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], batch=batch)
    result = check_triangulate_dg_press(ctx)
    assert result.status == BLOCK
    assert ("nbs-retail-total", "2026-01") not in ctx.confirmed_cross_source_matches


# -- 8. gate_a.triangulate_pbc_nbs ---------------------------------------------


def test_triangulate_pbc_nbs_skips_with_no_configured_pairs(tmp_path):
    batch = make_batch([touch("test-pbc-m1", "2025-12", m=1.0)])
    ctx = make_context(tmp_path, touched=["test-pbc-m1", "test-nbs-m1-republished"], batch=batch)
    result = check_triangulate_pbc_nbs(ctx)
    assert result.status == SKIP


def test_triangulate_pbc_nbs_passes_when_values_agree(tmp_path):
    config = make_test_config(source_pairs=[SourcePair(primary="pbc", secondary="nbs", tol=0.5, series=[["test-pbc-m1", "test-nbs-m1-republished"]])])
    batch = make_batch([touch("test-pbc-m1", "2025-12", m=1.0)])
    ctx = make_context(tmp_path, touched=["test-pbc-m1", "test-nbs-m1-republished"], batch=batch, config=config)
    result = check_triangulate_pbc_nbs(ctx)
    assert result.status == PASS


def test_triangulate_pbc_nbs_warns_when_values_disagree_beyond_tolerance(tmp_path):
    broken = load_fixture_series("test-pbc-m1")
    broken["observations"][-1]["m"] = broken["observations"][-1]["m"] + 1000
    config = make_test_config(source_pairs=[SourcePair(primary="pbc", secondary="nbs", tol=0.5, series=[["test-pbc-m1", "test-nbs-m1-republished"]])])
    batch = make_batch([touch("test-pbc-m1", broken["observations"][-1]["period"], m=broken["observations"][-1]["m"])])
    ctx = make_context(tmp_path, staged_overrides={"test-pbc-m1": broken}, touched=["test-pbc-m1", "test-nbs-m1-republished"], batch=batch, config=config)
    result = check_triangulate_pbc_nbs(ctx)
    assert result.status == WARN
