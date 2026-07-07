"""Tests for gate_b.dg_archive_sample and gate_b.archive_independent_sample --
the two archive-based re-verification checks. Both read the same
data/archive/dg/*.json + data/archive/<source>/*.html fixtures under
clean_repo/data/archive/ (see test_audit_helpers.py's module docstring and the
gen_audit_fixtures.py generation script for how they were built): two DG
indicators (one plain `nbs`-agency level, one `cflp`-agency PMI-shaped one,
covering the tier/association severity split) and one archived HTML page
matching test-retail-total's real numbers.
"""
from __future__ import annotations

import copy
import json

from pipeline.audit.checks import archive_independent_sample, dg_archive_sample
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo, make_ctx


# =====================================================================================
# gate_b.dg_archive_sample
# =====================================================================================


def test_dg_archive_sample_passes_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=10)
    report = dg_archive_sample.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    assert any(f.status == "pass" for f in report.findings)


def test_dg_archive_sample_uses_src_provenance_fast_path():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=10)
    report = dg_archive_sample.run(ctx)
    src_matches = [f for f in report.findings if f.status == "pass" and f.series == "test-dg-level" and f.source]
    assert any(f.source.startswith("dg:aaaa1111") for f in src_matches)


def test_dg_archive_sample_label_fallback_covers_observation_without_src():
    # test-dg-level's 2026-04 observation deliberately has no `src` -- it must
    # still be verified, via the labels.yaml-driven candidate search.
    ctx = make_ctx(CLEAN_REPO, samples_per_section=10)
    report = dg_archive_sample.run(ctx)
    hit = [f for f in report.findings if f.series == "test-dg-level" and f.period == "2026-04"]
    assert hit, "2026-04 point was not sampled/verified at all"
    assert hit[0].status == "pass"


def test_dg_archive_sample_catches_an_official_agency_mismatch_as_block(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-dg-level.json"
    series = json.loads(path.read_text(encoding="utf-8"))
    for obs in series["observations"]:
        if obs["period"] == "2026-05":
            obs["m"] = -42.0  # archived DG value is 1300.0
    path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")

    report = dg_archive_sample.run(make_ctx(repo_dir, samples_per_section=10))
    assert report.has_block()
    hit = [f for f in report.findings if f.status == "block" and f.series == "test-dg-level" and f.period == "2026-05"]
    assert hit


def test_dg_archive_sample_catches_an_association_agency_mismatch_as_warn_not_block(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-pmi.json"
    series = json.loads(path.read_text(encoding="utf-8"))
    for obs in series["observations"]:
        if obs["period"] == "2026-05":
            obs["m"] = -1.0  # archived DG value is 50.3
    path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")

    report = dg_archive_sample.run(make_ctx(repo_dir, samples_per_section=10))
    hit = [f for f in report.findings if f.series == "test-pmi" and f.period == "2026-05"]
    assert hit and hit[0].status == "warn"
    assert not report.has_block()


def test_dg_archive_sample_no_dg_series_is_a_skip():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=10)
    ctx.catalog = copy.deepcopy(ctx.catalog)
    ctx.catalog["series"] = [e for e in ctx.catalog["series"] if "dg/website" not in e.get("source", {}).get("url", "")]
    report = dg_archive_sample.run(ctx)
    assert any(f.status == "skip" for f in report.findings)
    assert not report.has_block()


# =====================================================================================
# gate_b.archive_independent_sample
# =====================================================================================


def test_archive_independent_sample_passes_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=15)
    report = archive_independent_sample.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    assert "coverage" in report.extra
    assert report.extra["coverage_pct"] is not None


def test_archive_independent_sample_matches_the_archived_html_page():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=15)
    report = archive_independent_sample.run(ctx)
    html_matches = [f for f in report.findings if f.status == "pass" and f.series == "test-retail-total" and f.source]
    assert any("test-retail.html" in (f.source or "") for f in html_matches)


def test_archive_independent_sample_skips_derived_series():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=15)
    report = archive_independent_sample.run(ctx)
    derived_skips = [f for f in report.findings if f.status == "skip" and f.series == "test-tax-total"]
    assert derived_skips
    assert "derived_recompute" in derived_skips[0].note


def test_archive_independent_sample_reports_unverifiable_when_no_label():
    ctx = make_ctx(CLEAN_REPO, samples_per_section=15, labels={})  # strip every label
    report = archive_independent_sample.run(ctx)
    unverifiable = [f for f in report.findings if f.status == "warn" and "unverifiable" in (f.note or "")]
    assert unverifiable
    assert not report.has_block()  # missing labels is a coverage gap, not a mismatch


def test_archive_independent_sample_catches_a_genuine_html_mismatch_as_block(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-retail-total.json"
    series = json.loads(path.read_text(encoding="utf-8"))
    for obs in series["observations"]:
        if obs["period"] == "2026-05":
            obs["m"] = -777777.0  # archived page says 41090
    path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")

    report = archive_independent_sample.run(make_ctx(repo_dir, samples_per_section=15))
    assert report.has_block()
    hit = [f for f in report.findings if f.status == "block" and f.series == "test-retail-total" and f.period == "2026-05"]
    assert hit


def test_archive_independent_sample_honest_coverage_gap_for_series_with_no_archive():
    # test-tax-a has a label but no archived page anywhere under
    # clean_repo/data/archive/ -- must be an honest WARN coverage gap.
    ctx = make_ctx(CLEAN_REPO, samples_per_section=15)
    report = archive_independent_sample.run(ctx)
    gaps = [f for f in report.findings if f.series == "test-tax-a" and f.status == "warn"]
    assert gaps
    assert any("coverage gap" in (f.note or "") for f in gaps)
