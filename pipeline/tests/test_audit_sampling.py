"""Tests for pipeline/audit/sampling.py: seed derivation (subprocess-based git
HEAD short SHA, never a git library), and reproducibility + basic shape of the
strata (a)-(f) builder used by gate_b.archive_independent_sample.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from pipeline.audit import sampling
from pipeline.tests.test_audit_helpers import CLEAN_REPO, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # the real china-consumer-data-dashboard checkout


def test_git_head_short_sha_matches_real_git_against_the_real_repo():
    sha = sampling.git_head_short_sha(REPO_ROOT)
    expected = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    assert sha == (expected or None)


def test_git_head_short_sha_returns_none_for_a_non_repo(tmp_path):
    assert sampling.git_head_short_sha(tmp_path) is None


def test_derive_seed_falls_back_to_catalog_version_outside_a_repo(tmp_path):
    seed = sampling.derive_seed(None, repo_root=tmp_path, catalog_version="9.9.9")
    assert seed == "noseed:9.9.9"


def test_derive_seed_prefers_explicit_seed():
    seed = sampling.derive_seed("pinned-seed", repo_root=REPO_ROOT, catalog_version="1.0.1")
    assert seed == "pinned-seed"


def test_derive_seed_is_deterministic_for_the_real_repo():
    a = sampling.derive_seed(None, repo_root=REPO_ROOT, catalog_version="1.0.1")
    b = sampling.derive_seed(None, repo_root=REPO_ROOT, catalog_version="1.0.1")
    assert a == b
    # Changing the catalog version alone must change the derived seed (a
    # backfill merge that only bumps `version` should still draw a fresh
    # sample even on the same commit).
    c = sampling.derive_seed(None, repo_root=REPO_ROOT, catalog_version="1.0.2")
    assert c != a


# -- strata -------------------------------------------------------------------------


def _build(seed="strata-seed", samples_per_section=25):
    ctx = make_ctx(CLEAN_REPO, seed=seed, samples_per_section=samples_per_section)
    strata = sampling.build_strata(
        catalog=ctx.catalog,
        series_by_id=ctx.series_by_id(),
        section_bundles=ctx.section_bundles,
        repo_root=ctx.repo_root,
        rng=ctx.rng,
        samples_per_section=samples_per_section,
    )
    return ctx, strata


def test_build_strata_produces_every_mandatory_stratum():
    _, strata = _build()
    # (a) and (c) are mandatory per task spec; (b) is best-effort (the
    # synthetic fixture tree is not a git repo with a "last data commit" to
    # discover, so it is legitimately skipped, not silently empty).
    assert strata.items["a_latest_per_caliber"], "stratum (a) must be non-empty for a non-trivial catalog"
    assert strata.items["c_break_seam"], "test-cpi-break's break should populate stratum (c)"
    assert "b_last_commit_touched" in strata.skipped or strata.items.get("b_last_commit_touched") is not None


def test_break_seam_stratum_includes_periods_on_both_sides_of_the_break():
    _, strata = _build()
    periods = {item.period for item in strata.items["c_break_seam"] if item.series_id == "test-cpi-break"}
    # test-cpi-break's break is effective 2026-01 -- the seam is the last
    # obs before it (2025-12) and the first at/after it (2026-01).
    assert "2025-12" in periods
    assert "2026-01" in periods


def test_derived_latest_stratum_only_contains_derived_series():
    ctx, strata = _build()
    series_by_id = ctx.series_by_id()
    for item in strata.items["e_derived_latest"]:
        assert series_by_id[item.series_id].get("derived"), f"{item.series_id} is in stratum (e) but isn't derived"
    # And every derived series in the catalog IS represented (has a latest obs).
    derived_ids = {sid for sid, e in series_by_id.items() if e.get("derived")}
    covered_ids = {item.series_id for item in strata.items["e_derived_latest"]}
    assert derived_ids == covered_ids


def test_random_per_section_stratum_respects_the_cap():
    _, strata = _build(samples_per_section=2)
    by_section: dict[str, int] = {}
    for item in strata.items["f_random_per_section"]:
        by_section[item.section] = by_section.get(item.section, 0) + 1
    for section_id, count in by_section.items():
        assert count <= 2, f"section {section_id} exceeded the samples-per-section cap: {count}"


def test_strata_are_reproducible_given_the_same_seed():
    _, strata_a = _build(seed="same-seed", samples_per_section=3)
    _, strata_b = _build(seed="same-seed", samples_per_section=3)
    key_a = sorted((i.series_id, i.period, i.caliber) for i in strata_a.items["f_random_per_section"])
    key_b = sorted((i.series_id, i.period, i.caliber) for i in strata_b.items["f_random_per_section"])
    assert key_a == key_b


def test_strata_differ_for_different_seeds_when_pool_is_large_enough():
    _, strata_a = _build(seed="seed-one", samples_per_section=1)
    _, strata_b = _build(seed="seed-two", samples_per_section=1)
    key_a = sorted((i.series_id, i.period) for i in strata_a.items["f_random_per_section"])
    key_b = sorted((i.series_id, i.period) for i in strata_b.items["f_random_per_section"])
    # Not a hard guarantee for every possible pool, but true for this fixture
    # (several sections have >1 period to choose from) -- if this ever
    # flakes, it means the two seeds coincidentally drew the same sample.
    assert key_a != key_b


def test_jan_feb_stratum_only_picks_recent_years_of_february_points():
    _, strata = _build()
    for item in strata.items["d_jan_feb_recent"]:
        assert item.period.endswith("-02")
