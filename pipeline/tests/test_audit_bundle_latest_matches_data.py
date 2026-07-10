"""Tests for gate_b.bundle_latest_matches_data -- catches a bundle (and its
plotted-array tails, and index.json's freshness row) anchored on a stale
period, by comparing directly against data/series/<id>.json's own newest
observation (never against the bundle's own claims, which is exactly what
let this class of bug through every other check).
"""
from __future__ import annotations

import json

from pipeline.audit.checks import bundle_latest_matches_data
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo, make_ctx


def test_passes_on_the_real_fixture_where_bundle_and_data_agree():
    ctx = make_ctx(CLEAN_REPO)
    report = bundle_latest_matches_data.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    assert any(f.status == "pass" for f in report.findings)


def test_catches_a_bundle_that_lags_data_series_by_one_period(tmp_path):
    """The exact bug reported: data/series/ advances to a new period (a real
    ingest landed) but site-data/ (bundle latest, plotted-array tails, and
    index.json's freshness row) was built from a stale catalog-anchored
    period and never picked up the new one."""
    repo_dir = copy_clean_repo(tmp_path)
    series_path = repo_dir / "data" / "series" / "test-retail-total.json"
    series = json.loads(series_path.read_text(encoding="utf-8"))
    series["observations"].append({"period": "2026-06", "m": 42000, "m_yoy": -0.2, "ytd": 248031, "ytd_yoy": 1.1, "src": "rel:20260716"})
    series_path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    # site-data/ deliberately NOT rebuilt -- bundle/index still claim 2026-05.

    report = bundle_latest_matches_data.run(make_ctx(repo_dir))
    assert report.has_block()

    fields = {f.field for f in report.findings if f.status == "block" and f.series == "test-retail-total"}
    assert "latest.period" in fields
    assert "yoy_series[-1].period" in fields
    assert "level_series[-1].period" in fields
    assert "index.json freshness.latest" in fields
    for f in report.findings:
        if f.series == "test-retail-total" and f.status == "block":
            assert f.expected == "2026-06"
            assert f.observed == "2026-05"


def test_a_series_with_no_observations_at_all_is_not_flagged(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    series_path = repo_dir / "data" / "series" / "test-retail-total.json"
    series = json.loads(series_path.read_text(encoding="utf-8"))
    series["observations"] = []
    series_path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")

    report = bundle_latest_matches_data.run(make_ctx(repo_dir))
    assert not any(f.series == "test-retail-total" for f in report.findings)


def test_missing_series_file_is_a_warn_not_a_crash(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    (repo_dir / "data" / "series" / "test-retail-total.json").unlink()

    report = bundle_latest_matches_data.run(make_ctx(repo_dir))
    assert not report.has_block()
    assert any(f.status == "warn" and f.series == "test-retail-total" for f in report.findings)
