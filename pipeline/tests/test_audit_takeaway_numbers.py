"""Tests for gate_b.takeaway_numbers -- bag-of-numbers verification of every
bundle `takeaway` string against its stored measures. Covers the plain
sign-matrix template (real fixture), the "level-only" template added to
takeaways.py on 2026-07-08 (real fixture: test-retail-ex-auto /
test-retail-online-goods, neither of which ever carries a published YoY),
and hand-constructed synthetic entries for streak-count verification and the
name_short stripping fix.
"""
from __future__ import annotations

import copy
import json
import random
from datetime import date

from pipeline.audit.checks import takeaway_numbers
from pipeline.audit.models import AuditContext
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo, make_ctx


def _bare_ctx(section_bundles: dict) -> AuditContext:
    return AuditContext(
        repo_root=CLEAN_REPO,
        data_dir=CLEAN_REPO / "data",
        site_data_dir=CLEAN_REPO / "site-data",
        catalog={"sections": []},
        section_bundles=section_bundles,
        panel_bundle_loader=lambda _pid: None,
        labels={},
        rng=random.Random(0),
        seed="unit-seed",
        run_id="unit-seed",
        offline=True,
        samples_per_section=5,
        as_of=date(2026, 7, 8),
        previous_diary=None,
    )


def test_passes_on_the_real_fixture_including_the_level_only_template():
    # build.py currently scopes the level-only template to value_type=="index"
    # series with no published YoY at all (PMI's own shape) -- test-pmi is
    # this fixture's one such series (also exercising the 荣枯线 clause).
    ctx = make_ctx(CLEAN_REPO)
    entry = next(e for e in ctx.section_bundles["macro"]["series"] if e["id"] == "test-pmi")
    assert entry["headline"]["latest_yoy"] is None
    assert entry["takeaway"] and "个点" in entry["takeaway"], entry["takeaway"]

    report = takeaway_numbers.run(ctx)
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]


def test_catches_a_corrupted_number_in_a_sign_matrix_takeaway():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    entry = next(e for e in ctx.section_bundles["consumption"]["series"] if e["id"] == "test-retail-total")
    assert "0.6%" in entry["takeaway"]
    entry["takeaway"] = entry["takeaway"].replace("0.6%", "9.9%")

    report = takeaway_numbers.run(ctx)
    assert report.has_block()
    hit = [f for f in report.findings if f.status == "block" and f.series == "test-retail-total"]
    assert hit and hit[0].observed == 9.9


def test_catches_a_corrupted_number_in_a_level_only_takeaway():
    ctx = make_ctx(CLEAN_REPO)
    ctx.section_bundles = copy.deepcopy(ctx.section_bundles)
    entry = next(e for e in ctx.section_bundles["macro"]["series"] if e["id"] == "test-pmi")
    original = entry["takeaway"]
    assert "49.8" in original
    entry["takeaway"] = original.replace("49.8", "12.3")

    report = takeaway_numbers.run(ctx)
    assert report.has_block()
    assert any(f.status == "block" and f.series == "test-pmi" for f in report.findings)


def test_streak_count_mismatch_is_caught():
    entry = {
        "id": "streak-series", "tier": 1, "name_zh": "测试序列",
        "headline": {"latest_yoy": -1.2, "delta_pp_vs_prev": None, "streak": 3, "period_label_zh": "2026 年 5 月"},
        "latest": {},
        "takeaway": "2026 年 5 月测试序列同比下降 1.2%，连续 5 个月同比下降",  # says 5, headline says 3
    }
    report = takeaway_numbers.run(_bare_ctx({"x": {"series": [entry]}}))
    assert report.has_block()
    hit = [f for f in report.findings if f.field == "streak"]
    assert hit and hit[0].observed == 5 and hit[0].expected == 3


def test_streak_count_capped_display_of_24_is_accepted_for_a_longer_true_streak():
    entry = {
        "id": "streak-series-2", "tier": 1, "name_zh": "测试序列2",
        "headline": {"latest_yoy": -1.2, "delta_pp_vs_prev": None, "streak": 30, "period_label_zh": "2026 年 5 月"},
        "latest": {},
        "takeaway": "2026 年 5 月测试序列2同比下降 1.2%，连续 24 个月以上同比下降",
    }
    report = takeaway_numbers.run(_bare_ctx({"x": {"series": [entry]}}))
    assert not report.has_block()


def test_missing_streak_clause_for_a_real_streak_is_a_warn_not_a_block():
    entry = {
        "id": "streak-series-3", "tier": 1, "name_zh": "测试序列3",
        "headline": {"latest_yoy": -1.2, "delta_pp_vs_prev": None, "streak": 4, "period_label_zh": "2026 年 5 月"},
        "latest": {},
        "takeaway": "2026 年 5 月测试序列3同比下降 1.2%",  # no streak clause at all
    }
    report = takeaway_numbers.run(_bare_ctx({"x": {"series": [entry]}}))
    assert not report.has_block()
    assert any(f.status == "warn" and f.field == "streak" for f in report.findings)


def test_name_short_containing_a_digit_is_stripped_before_number_matching():
    """Regression test for build.py's _headline_name (added 2026-07-08):
    the takeaway embeds catalog `name_short` ("M2") when present, INSTEAD OF
    name_zh -- if this check only stripped name_zh, the "2" in "M2" would
    leak through as a spurious unmatched number."""
    entry = {
        "id": "m2-series", "tier": 1, "name_zh": "货币和准货币供应量", "name_short": "M2",
        "headline": {"latest_yoy": None, "delta_pp_vs_prev": None, "streak": 0, "period_label_zh": "2026 年 5 月"},
        "latest": {"m": 3536700.0}, "prev": {"m": 3500000.0},
        "takeaway": "2026 年 5 月M2为 3536700.0，比上月上升 36700.0 个点",
    }
    report = takeaway_numbers.run(_bare_ctx({"x": {"series": [entry]}}))
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
