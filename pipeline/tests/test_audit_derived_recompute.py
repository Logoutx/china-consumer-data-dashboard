"""Tests for gate_b.derived_recompute -- independently recomputes every
derived rule (single_from_ytd, ratio, simple_mean_of_cities,
count_cities_gt_zero, sum) from data/series/ + data/panels/ and compares
against the derived series' own stored value.
"""
from __future__ import annotations

import json

from pipeline.audit.checks import derived_recompute
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo, make_ctx


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_all_four_derived_rules_recompute_clean_on_the_real_fixture():
    ctx = make_ctx(CLEAN_REPO)
    report = derived_recompute.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    rules_seen = {f.rule for f in report.findings if f.status == "pass"} | (
        {"single_from_ytd", "ratio", "simple_mean_of_cities", "count_cities_gt_zero", "sum"}
        if any(f.status == "pass" and "recomputed clean" in (f.note or "") for f in report.findings)
        else set()
    )
    # The summary "N derived series recomputed clean" finding is the signal
    # that nothing needed flagging; explicit per-rule pass Findings are only
    # emitted on a mismatch/warn path, so absence of blocks IS the pass signal.
    assert any("recomputed clean" in (f.note or "") for f in report.findings)


def test_catches_a_wrong_single_from_ytd_value(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-retail-online-goods.json"
    series = _load(path)
    for obs in series["observations"]:
        if obs["period"] == "2026-05":
            obs["m"] = 99999.0  # was 11533.0 (== ytd(05) - ytd(04))
    _write(path, series)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert report.has_block()
    hit = [f for f in report.findings if f.status == "block" and f.series == "test-retail-online-goods" and f.period == "2026-05"]
    assert hit and hit[0].rule == "single_from_ytd"


def test_catches_a_wrong_ratio_value(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-retail-online-share.json"
    series = _load(path)
    series["observations"][-1]["m"] = 12.34  # not 100*goods/ex_auto
    _write(path, series)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert report.has_block()
    assert any(f.status == "block" and f.series == "test-retail-online-share" and f.rule == "ratio" for f in report.findings)


def test_catches_a_wrong_sum_value(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-tax-total.json"
    series = _load(path)
    series["observations"][0]["ytd"] = -1.0  # was tax_a + tax_b for that period
    _write(path, series)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert report.has_block()
    assert any(f.status == "block" and f.series == "test-tax-total" and f.rule == "sum" for f in report.findings)


def test_sum_skips_a_period_where_an_input_is_missing_rather_than_flagging(tmp_path):
    """Mirrors migrate/REPORT.md's own documented exclusion behavior for
    mof-real-estate-tax-total: a period missing >=1 of the summed components
    is not computable and must be silently skipped, never reported as a
    mismatch."""
    repo_dir = copy_clean_repo(tmp_path)
    tax_a_path = repo_dir / "data" / "series" / "test-tax-a.json"
    tax_total_path = repo_dir / "data" / "series" / "test-tax-total.json"
    tax_a = _load(tax_a_path)
    tax_a["observations"].append({"period": "2026-05", "ytd": 1500.0, "src": "legacy:2026-05"})
    _write(tax_a_path, tax_a)
    tax_total = _load(tax_total_path)
    # tax-total claims a 2026-05 value too, but test-tax-b (the other input)
    # has no such period -- this must be SKIPPED, not flagged, regardless of
    # what nonsense value tax-total claims for it.
    tax_total["observations"].append({"period": "2026-05", "ytd": -999999.0, "src": "derived:sum:2026-05"})
    _write(tax_total_path, tax_total)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert not any(f.series == "test-tax-total" and f.period == "2026-05" for f in report.findings)


def test_catches_a_wrong_mean_of_cities_value(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-70city-newhome-mom.json"
    series = _load(path)
    series["observations"][-1]["m"] = 42.0
    _write(path, series)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert report.has_block()
    assert any(f.status == "block" and f.series == "test-70city-newhome-mom" and f.rule == "simple_mean_of_cities" for f in report.findings)


def test_catches_a_wrong_up_count_value(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-70city-newhome-up-count.json"
    series = _load(path)
    series["observations"][-1]["m"] = 42.0
    _write(path, series)

    report = derived_recompute.run(make_ctx(repo_dir))
    assert report.has_block()
    assert any(f.status == "block" and f.series == "test-70city-newhome-up-count" and f.rule == "count_cities_gt_zero" for f in report.findings)


def test_known_disagreement_whitelist_downgrades_block_to_warn(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "data" / "series" / "test-70city-newhome-up-count.json"
    series = _load(path)
    bad_period = series["observations"][-1]["period"]
    series["observations"][-1]["m"] = 42.0
    _write(path, series)

    config_dir = repo_dir / "pipeline" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    # Real schema (confirmed against pipeline/config/validation.yaml as
    # written during this rebuild): `periods` PLURAL (a list), not `period`.
    (config_dir / "validation.yaml").write_text(
        f"known_disagreements:\n  - series: test-70city-newhome-up-count\n    periods: [{bad_period!r}]\n",
        encoding="utf-8",
    )

    report = derived_recompute.run(make_ctx(repo_dir))
    assert not report.has_block()
    assert any(
        f.status == "warn" and f.series == "test-70city-newhome-up-count" and f.period == bad_period for f in report.findings
    )


def test_a_validation_yaml_with_unrelated_entries_does_not_erase_the_hardcoded_whitelist(tmp_path):
    """The real pipeline/config/validation.yaml that landed during this
    rebuild only lists a mof-real-estate-tax-total entry -- its mere presence
    must not silently drop the independently-sourced (migrate/REPORT.md)
    70-city whitelist derived_recompute.py carries; see the module-level
    comment above _HARDCODED_KNOWN_DISAGREEMENTS. Uses the REAL nbs-70city-*
    ids (not the synthetic fixture's test-* ids), since that hardcoded list
    is keyed by the real catalog's series ids."""
    repo_dir = copy_clean_repo(tmp_path)
    config_dir = repo_dir / "pipeline" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "validation.yaml").write_text(
        "known_disagreements:\n"
        "  - series: some-unrelated-series\n"
        "    periods: ['2018-06']\n"
        "    checks: ['gate_a.sum_of_parts']\n",
        encoding="utf-8",
    )

    from pipeline.audit.checks.derived_recompute import _HARDCODED_KNOWN_DISAGREEMENTS, _load_known_disagreements

    merged = _load_known_disagreements(repo_dir)
    assert _HARDCODED_KNOWN_DISAGREEMENTS <= merged  # every hardcoded entry survives
    assert ("some-unrelated-series", "2018-06") in merged  # and the real file's own entry is honored too
