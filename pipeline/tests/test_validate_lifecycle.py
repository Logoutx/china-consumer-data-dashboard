"""Tests for pipeline/validate/checks/lifecycle.py: gate_a.calendar_expected,
gate_a.calendar_window, gate_a.partial_parse_completeness,
gate_a.archive_release_identity, gate_a.break_no_yoy, gate_a.break_link,
gate_a.revision_flood, gate_a.revision_integrity."""
from __future__ import annotations

import copy
import json
from datetime import date

from pipeline.normalize import NormalizeReport
from pipeline.tests._validate_helpers import load_fixture_series, make_batch, make_context, make_test_calendar, make_test_config, touch
from pipeline.validate.checks.lifecycle import (
    check_archive_release_identity,
    check_break_link,
    check_break_no_yoy,
    check_calendar_expected,
    check_calendar_window,
    check_partial_parse_completeness,
    check_revision_flood,
    check_revision_integrity,
)
from pipeline.validate.config import CalendarWindow, KnownDisagreement
from pipeline.validate.model import BLOCK, PASS, SKIP, WARN

TODAY = date(2026, 6, 20)


def _small_series(series_id: str, periods_m: list[tuple[str, float]]) -> dict:
    return {
        "schema": "series/v1", "id": series_id, "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": p, "m": v} for p, v in periods_m],
        "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
    }


# -- 14. gate_a.calendar_expected ----------------------------------------------


def _calendar_expected_case(tmp_path, new_period):
    real = _small_series("test-cal", [("2025-10", 100), ("2025-11", 101), ("2025-12", 102)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": new_period, "m": 999})
    batch = make_batch([touch("test-cal", new_period, m=999)])
    return make_context(tmp_path, real_overrides={"test-cal": real}, staged_overrides={"test-cal": staged}, batch=batch)


def test_calendar_expected_passes_for_exactly_one_step_ahead(tmp_path):
    ctx = _calendar_expected_case(tmp_path, "2026-01")
    result = check_calendar_expected(ctx)
    assert result.status == PASS


def test_calendar_expected_warns_on_a_one_period_gap(tmp_path):
    ctx = _calendar_expected_case(tmp_path, "2026-02")  # 2 steps past 2025-12 -- 2026-01 was skipped
    result = check_calendar_expected(ctx)
    assert result.status == WARN


def test_calendar_expected_blocks_stepping_backward(tmp_path):
    ctx = _calendar_expected_case(tmp_path, "2025-08")  # before the current latest, never seen before
    result = check_calendar_expected(ctx)
    assert result.status == BLOCK


def test_calendar_expected_blocks_an_implausible_jump(tmp_path):
    ctx = _calendar_expected_case(tmp_path, "2027-06")  # far more than 1 step ahead
    result = check_calendar_expected(ctx)
    assert result.status == BLOCK


def test_calendar_expected_demotes_an_acked_jump_to_warn(tmp_path):
    """Regression, 2026-07-08: a known_disagreements entry acknowledging
    (series, period, this check) -- e.g. a real one-time historical
    catch-up backfilling OUR OWN archive gap, per
    pipeline/config/validation.yaml's nbs-retail-cat-grain-food entry --
    must demote what would otherwise be a "wrong release" BLOCK to a WARN,
    mirroring gate_a.revision_flood's existing ack pattern."""
    real = _small_series("test-cal", [("2025-10", 100), ("2025-11", 101), ("2025-12", 102)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": "2027-06", "m": 999})
    batch = make_batch([touch("test-cal", "2027-06", m=999)])
    config = make_test_config(
        known_disagreements=[KnownDisagreement(series="test-cal", periods=["2027-06"], checks=["gate_a.calendar_expected"], note="test catch-up ack")]
    )
    ctx = make_context(tmp_path, real_overrides={"test-cal": real}, staged_overrides={"test-cal": staged}, batch=batch, config=config)
    result = check_calendar_expected(ctx)
    assert result.status == WARN
    assert any("acked: test catch-up ack" in (f.message or "") for f in result.findings)


def test_calendar_expected_ack_scoped_to_a_different_check_does_not_apply(tmp_path):
    """An ack recorded for a DIFFERENT check must not leak into this one."""
    real = _small_series("test-cal", [("2025-10", 100), ("2025-11", 101), ("2025-12", 102)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": "2027-06", "m": 999})
    batch = make_batch([touch("test-cal", "2027-06", m=999)])
    config = make_test_config(
        known_disagreements=[KnownDisagreement(series="test-cal", periods=["2027-06"], checks=["gate_a.sum_of_parts"], note="unrelated ack")]
    )
    ctx = make_context(tmp_path, real_overrides={"test-cal": real}, staged_overrides={"test-cal": staged}, batch=batch, config=config)
    result = check_calendar_expected(ctx)
    assert result.status == BLOCK


# -- 15. gate_a.calendar_window -------------------------------------------------


def test_calendar_window_passes_within_window(tmp_path):
    batch = make_batch(source="nbs-cpi", published_at="2026/01/10 10:00")
    calendar = make_test_calendar(cpi_ppi=CalendarWindow(freq="M", window_days=(9, 13), grace_days=1))
    ctx = make_context(tmp_path, batch=batch, calendar=calendar, archive_source="nbs-cpi")
    result = check_calendar_window(ctx)
    assert result.status == PASS


def test_calendar_window_warns_outside_window(tmp_path):
    batch = make_batch(source="nbs-cpi", published_at="2026/01/25 10:00")
    calendar = make_test_calendar(cpi_ppi=CalendarWindow(freq="M", window_days=(9, 13), grace_days=1))
    ctx = make_context(tmp_path, batch=batch, calendar=calendar, archive_source="nbs-cpi")
    result = check_calendar_window(ctx)
    assert result.status == WARN


def test_calendar_window_skips_without_published_at(tmp_path):
    batch = make_batch(source="nbs-cpi", published_at=None)
    ctx = make_context(tmp_path, batch=batch, archive_source="nbs-cpi")
    result = check_calendar_window(ctx)
    assert result.status == SKIP


# -- 16. gate_a.partial_parse_completeness --------------------------------------


def test_partial_parse_completeness_passes_when_all_anchors_present(tmp_path):
    batch = make_batch(source="nbs-cpi", raw_source_fields={"居民消费价格", "食品", "不包括食品和能源", "服务"})
    ctx = make_context(tmp_path, batch=batch)
    result = check_partial_parse_completeness(ctx)
    assert result.status == PASS


def test_partial_parse_completeness_blocks_a_missing_anchor_group(tmp_path):
    batch = make_batch(source="nbs-cpi", raw_source_fields={"居民消费价格", "食品", "不包括食品和能源"})  # missing 服务
    ctx = make_context(tmp_path, batch=batch)
    result = check_partial_parse_completeness(ctx)
    assert result.status == BLOCK


def test_partial_parse_completeness_skips_unknown_source(tmp_path):
    batch = make_batch(source="some-unregistered-source", raw_source_fields={"x"})
    ctx = make_context(tmp_path, batch=batch)
    result = check_partial_parse_completeness(ctx)
    assert result.status == SKIP


# -- 17. gate_a.archive_release_identity ----------------------------------------


def test_archive_release_identity_passes_when_capture_is_on_file(tmp_path):
    real = _small_series("test-cal", [("2025-11", 100), ("2025-12", 101)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": "2026-01", "m": 105})
    report = NormalizeReport(new_observations=[("test-cal", "2026-01")])
    batch = make_batch(release_id="2026-01-17_test-release", source="nbs-retail")
    ctx = make_context(
        tmp_path,
        real_overrides={"test-cal": real},
        staged_overrides={"test-cal": staged},
        batch=batch,
        normalize_report=report,
        archive_files={"nbs-retail": ["2026-01-17_test-release"]},
    )
    result = check_archive_release_identity(ctx)
    assert result.status == PASS


def test_archive_release_identity_blocks_when_no_capture_exists(tmp_path):
    real = _small_series("test-cal", [("2025-11", 100), ("2025-12", 101)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": "2026-01", "m": 105})
    report = NormalizeReport(new_observations=[("test-cal", "2026-01")])
    batch = make_batch(release_id="2026-01-17_test-release", source="nbs-retail")
    ctx = make_context(
        tmp_path,
        real_overrides={"test-cal": real},
        staged_overrides={"test-cal": staged},
        batch=batch,
        normalize_report=report,
        archive_files={},  # no archive capture at all
    )
    result = check_archive_release_identity(ctx)
    assert result.status == BLOCK


def test_archive_release_identity_blocks_a_stale_reserve(tmp_path):
    real = _small_series("test-cal", [("2025-11", 100), ("2025-12", 101)])
    staged = copy.deepcopy(real)
    # New period whose m AND m_yoy are byte-identical to the prior period -- a classic stale re-serve symptom
    staged["observations"][-1]["m_yoy"] = 2.0
    real["observations"][-1]["m_yoy"] = 2.0
    staged["observations"].append({"period": "2026-01", "m": 101, "m_yoy": 2.0})
    report = NormalizeReport(new_observations=[("test-cal", "2026-01")])
    batch = make_batch(release_id="2026-01-17_test-release", source="nbs-retail")
    ctx = make_context(
        tmp_path,
        real_overrides={"test-cal": real},
        staged_overrides={"test-cal": staged},
        batch=batch,
        normalize_report=report,
        archive_files={"nbs-retail": ["2026-01-17_test-release"]},
    )
    result = check_archive_release_identity(ctx)
    assert result.status == BLOCK


def _dg_refresh_case(tmp_path, *, write_manifest, captures_present=True):
    real = _small_series("nbs-industrial-va", [("2026-04", 4.9)])
    staged = copy.deepcopy(real)
    staged["observations"].append({"period": "2026-05", "m": 5.1})
    report = NormalizeReport(new_observations=[("nbs-industrial-va", "2026-05")])
    batch = make_batch(release_id="dg-refresh-2026-07-14", source="dg")
    ctx = make_context(
        tmp_path,
        real_overrides={"nbs-industrial-va": real},
        staged_overrides={"nbs-industrial-va": staged},
        batch=batch,
        normalize_report=report,
    )
    dg_dir = ctx.archive_dir / "dg"
    dg_dir.mkdir(parents=True, exist_ok=True)
    if write_manifest:
        captures = ["abc123.json"]
        if captures_present:
            (dg_dir / "abc123.json").write_text("{}", encoding="utf-8")
        manifest = {"release_id": "dg-refresh-2026-07-14", "generated_at": "2026-07-14T00:00:00Z", "captures": captures}
        (dg_dir / "manifest_dg-refresh-2026-07-14.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ctx


def test_archive_release_identity_passes_for_dg_refresh_with_a_valid_manifest(tmp_path):
    ctx = _dg_refresh_case(tmp_path, write_manifest=True, captures_present=True)
    result = check_archive_release_identity(ctx)
    assert result.status == PASS


def test_archive_release_identity_blocks_dg_refresh_with_no_manifest(tmp_path):
    ctx = _dg_refresh_case(tmp_path, write_manifest=False)
    result = check_archive_release_identity(ctx)
    assert result.status == BLOCK


def test_archive_release_identity_blocks_dg_refresh_when_a_listed_capture_is_missing(tmp_path):
    ctx = _dg_refresh_case(tmp_path, write_manifest=True, captures_present=False)
    result = check_archive_release_identity(ctx)
    assert result.status == BLOCK


# -- 18. gate_a.break_no_yoy -----------------------------------------------------


def test_break_no_yoy_passes_for_a_well_formed_break(tmp_path):
    ctx = make_context(tmp_path, touched=["test-cpi-break"])
    result = check_break_no_yoy(ctx)
    assert result.status == PASS


def test_break_no_yoy_blocks_yoy_inside_the_window(tmp_path):
    broken = load_fixture_series("test-cpi-break")
    obs = next(o for o in broken["observations"] if o["period"] == "2026-06")
    obs["m_yoy"] = 3.3  # inside [2026-01, 2027-01) -- must never be stored
    ctx = make_context(tmp_path, staged_overrides={"test-cpi-break": broken})
    result = check_break_no_yoy(ctx)
    assert result.status == BLOCK


def test_break_no_yoy_blocks_bridging_the_seam_without_enough_restated_history(tmp_path):
    broken = load_fixture_series("test-cpi-break")
    # Drop one restated period so only 11 (< the required 12) exist between effective and yoy_valid_from
    broken["observations"] = [o for o in broken["observations"] if o["period"] != "2026-06"]
    ctx = make_context(tmp_path, staged_overrides={"test-cpi-break": broken})
    result = check_break_no_yoy(ctx)
    assert result.status == BLOCK


def test_break_no_yoy_skips_series_without_a_no_yoy_break(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"])
    result = check_break_no_yoy(ctx)
    assert result.status == SKIP


def test_break_no_yoy_passes_when_yoy_valid_from_equals_effective(tmp_path):
    """Regression, 2026-07-08: yoy_valid_from == effective is a DIFFERENT,
    equally legitimate break shape from the genuine-gap one the other tests
    in this section exercise (DATA-CONTRACT.md §2.1: "a legitimate, common
    case -- not a no-op", used for real pbc-m1/nbs-cpi-yoy/nbs-ppi-yoy). The
    published m_yoy at the seam is valid immediately, with NO restated-history
    accumulation required -- the old code's unconditional `len(restated) <
    required` (restated is empty by construction whenever effective==
    valid_from) blocked this shape 100% of the time, which is exactly what
    broke every real run touching those three real series."""
    same_month = copy.deepcopy(load_fixture_series("test-cpi-break"))
    same_month["breaks"][0]["yoy_valid_from"] = "2026-01"  # == effective, unlike the fixture's default 2027-01
    seam_obs = next(o for o in same_month["observations"] if o["period"] == "2026-01")
    seam_obs["m_yoy"] = 0.2  # a real, already-published value -- must NOT be blocked
    ctx = make_context(tmp_path, staged_overrides={"test-cpi-break": same_month})
    result = check_break_no_yoy(ctx)
    assert result.status == PASS
    # Non-regression: the genuine-gap shape (yoy_valid_from > effective, e.g.
    # a brand-new id with no prior history) must still require `required`
    # restated periods -- unchanged, see
    # test_break_no_yoy_blocks_bridging_the_seam_without_enough_restated_history
    # above, which this fix does not touch.


# -- 19. gate_a.break_link --------------------------------------------------------


def test_break_link_passes_for_a_symmetric_pair(tmp_path):
    ctx = make_context(tmp_path, touched=["test-old-series", "test-new-series"])
    result = check_break_link(ctx)
    assert result.status == PASS


def test_break_link_blocks_when_old_series_is_not_frozen(tmp_path):
    broken = load_fixture_series("test-old-series")
    del broken["end"]
    ctx = make_context(tmp_path, staged_overrides={"test-old-series": broken}, touched=["test-old-series", "test-new-series"])
    result = check_break_link(ctx)
    assert result.status == BLOCK


def test_break_link_blocks_a_missing_backlink(tmp_path):
    broken = load_fixture_series("test-new-series")
    broken["breaks"][0]["supersedes"] = None  # no longer points back at test-old-series
    ctx = make_context(tmp_path, staged_overrides={"test-new-series": broken}, touched=["test-old-series", "test-new-series"])
    result = check_break_link(ctx)
    assert result.status == BLOCK


def test_break_link_skips_series_without_a_new_id_break(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"])
    result = check_break_link(ctx)
    assert result.status == SKIP


# -- 20. gate_a.revision_flood -----------------------------------------------------


def _flood_report(n: int) -> NormalizeReport:
    return NormalizeReport(
        revisions=[
            {"period": f"2024-{(i % 12) + 1:02d}", "measure": "m", "old": i, "new": i + 1, "revised_on": TODAY.isoformat(), "source": "rel:test", "series_id": "nbs-retail-total"}
            for i in range(n)
        ]
    )


def test_revision_flood_passes_under_threshold(tmp_path):
    report = _flood_report(2)
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], normalize_report=report, today=TODAY)
    result = check_revision_flood(ctx)
    assert result.status == PASS


def test_revision_flood_blocks_over_threshold(tmp_path):
    report = _flood_report(8)  # > max_per_release=6 and > 10% of ~33 observations
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], normalize_report=report, today=TODAY)
    result = check_revision_flood(ctx)
    assert result.status == BLOCK
    assert any(f.needs_ack for f in result.findings)


def test_revision_flood_demotes_to_warn_when_acknowledged(tmp_path):
    report = _flood_report(8)
    config = make_test_config(known_disagreements=[KnownDisagreement(series="nbs-retail-total", checks=["gate_a.revision_flood"], note="benchmark revision, acked")])
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], normalize_report=report, config=config, today=TODAY)
    result = check_revision_flood(ctx)
    assert result.status == WARN


def test_revision_flood_skips_with_no_revisions(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], normalize_report=NormalizeReport(), today=TODAY)
    result = check_revision_flood(ctx)
    assert result.status == SKIP


# -- 21. gate_a.revision_integrity --------------------------------------------------


def _revision_case(tmp_path, *, old=55920, new=56000, revised_on="2026-06-20", period="2025-12"):
    real = load_fixture_series("nbs-retail-total")
    staged = copy.deepcopy(real)
    staged_obs = next(o for o in staged["observations"] if o["period"] == period)
    staged_obs["ytd"] = new
    staged["revisions"].append({"period": period, "measure": "ytd", "old": old, "new": new, "revised_on": revised_on, "source": "rel:test-revision"})
    report = NormalizeReport(revisions=[{"period": period, "measure": "ytd", "old": old, "new": new, "revised_on": revised_on, "source": "rel:test-revision", "series_id": "nbs-retail-total"}])
    return real, staged, report


def test_revision_integrity_passes_for_a_correctly_formed_revision(tmp_path):
    real, staged, report = _revision_case(tmp_path)
    ctx = make_context(tmp_path, real_overrides={"nbs-retail-total": real}, staged_overrides={"nbs-retail-total": staged}, normalize_report=report, today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == PASS


def test_revision_integrity_blocks_when_old_does_not_match_pre_run_value(tmp_path):
    real, staged, report = _revision_case(tmp_path, old=99999)  # doesn't match the real file's actual 55920
    ctx = make_context(tmp_path, real_overrides={"nbs-retail-total": real}, staged_overrides={"nbs-retail-total": staged}, normalize_report=report, today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == BLOCK


def test_revision_integrity_blocks_when_new_does_not_match_staged_value(tmp_path):
    real, staged, report = _revision_case(tmp_path)
    report.revisions[0]["new"] = 12345  # staged file actually holds 56000
    ctx = make_context(tmp_path, real_overrides={"nbs-retail-total": real}, staged_overrides={"nbs-retail-total": staged}, normalize_report=report, today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == BLOCK


def test_revision_integrity_blocks_when_revised_on_is_not_today(tmp_path):
    real, staged, report = _revision_case(tmp_path, revised_on="2020-01-01")
    staged["revisions"][-1]["revised_on"] = "2020-01-01"
    ctx = make_context(tmp_path, real_overrides={"nbs-retail-total": real}, staged_overrides={"nbs-retail-total": staged}, normalize_report=report, today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == BLOCK


def test_revision_integrity_blocks_when_a_pre_existing_revision_is_dropped(tmp_path):
    real, staged, report = _revision_case(tmp_path)
    real["revisions"].append({"period": "2024-03", "measure": "m", "old": 1, "new": 2, "revised_on": "2025-01-01", "source": "legacy-migration"})
    # staged["revisions"] deliberately does NOT carry that pre-existing entry forward
    ctx = make_context(tmp_path, real_overrides={"nbs-retail-total": real}, staged_overrides={"nbs-retail-total": staged}, normalize_report=report, today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == BLOCK


def test_revision_integrity_skips_with_no_revisions(tmp_path):
    ctx = make_context(tmp_path, touched=["nbs-retail-total"], normalize_report=NormalizeReport(), today=TODAY)
    result = check_revision_integrity(ctx)
    assert result.status == SKIP
