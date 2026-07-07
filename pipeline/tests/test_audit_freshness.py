"""Tests for gate_b.freshness -- per-agency release-calendar lag budget
(WARN only, never BLOCK, per task spec: staleness means the world moved on,
not that our data is wrong) + the "generated_at didn't advance since the
previous diary" check.
"""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from pipeline.audit.checks import freshness
from pipeline.audit.models import AuditContext
from pipeline.audit.release_calendar import FALLBACK_RELEASE_CALENDAR, lag_budget_for, load_release_calendar
from pipeline.tests.test_audit_helpers import CLEAN_REPO, make_ctx


def _bare_ctx(catalog, section_bundles, *, as_of: date, previous_diary=None, repo_root=CLEAN_REPO, site_data_dir=None) -> AuditContext:
    return AuditContext(
        repo_root=repo_root,
        data_dir=repo_root / "data",
        site_data_dir=site_data_dir or (repo_root / "site-data"),
        catalog=catalog,
        section_bundles=section_bundles,
        panel_bundle_loader=lambda _pid: None,
        labels={},
        rng=random.Random(0),
        seed="unit-seed",
        run_id="unit-seed",
        offline=True,
        samples_per_section=5,
        as_of=as_of,
        previous_diary=previous_diary,
    )


def _catalog_with(series_id: str, agency: str) -> dict:
    return {"series": [{"id": series_id, "tier": 1, "section": "x", "source": {"agency": agency}}]}


def _bundle_with_latest(series_id: str, period: str, name_zh: str = "测试序列", freq: str = "M") -> dict:
    return {"x": {"series": [{"id": series_id, "tier": 1, "name_zh": name_zh, "freq": freq, "latest": {"period": period}}]}}


def test_release_calendar_fallback_used_when_config_absent(tmp_path):
    calendar, used_fallback = load_release_calendar(tmp_path / "does-not-exist.yaml")
    assert used_fallback
    assert calendar == FALLBACK_RELEASE_CALENDAR


def test_release_calendar_reads_a_real_file(tmp_path):
    path = tmp_path / "release_calendar.yaml"
    path.write_text("nbs:\n  expected_lag_days: 5\n  grace_days: 5\n", encoding="utf-8")
    calendar, used_fallback = load_release_calendar(path)
    assert not used_fallback
    assert calendar["nbs"]["expected_lag_days"] == 5


def test_release_calendar_reads_the_concept_keyed_schema_natively(tmp_path):
    """Regression test (flipped 2026-07-08): the real pipeline/config/
    release_calendar.yaml that landed during this rebuild is keyed by concept
    ("cpi_ppi", "pbc_money", ...) with a window_days/lag_days shape for
    pipeline/validate's ingest-time gate, not this module's OWN originally-
    assumed agency -> {expected_lag_days, grace_days} shape. That assumed
    schema never actually existed in the wild -- this module used to (wrongly)
    treat the real, concept-keyed file as "incompatible" and silently fall
    back to DEFAULT_ENTRY for every single agency. It must instead be read
    natively: trusted as the real calendar (used_fallback=False), with
    lag_budget_for bridging concept keys to agencies (see the next test)."""
    path = tmp_path / "release_calendar.yaml"
    path.write_text(
        "cpi_ppi:\n  window_days: [9, 13]\n  freq: M\n  grace_days: 1\n"
        "pbc_money:\n  window_days: [10, 15]\n  freq: M\n  grace_days: 2\n",
        encoding="utf-8",
    )
    calendar, used_fallback = load_release_calendar(path)
    assert not used_fallback
    assert calendar["cpi_ppi"]["window_days"] == [9, 13]
    assert calendar["pbc_money"]["window_days"] == [10, 15]


def test_lag_budget_for_bridges_a_concept_keyed_calendar_via_agency(tmp_path):
    """lag_budget_for resolves an AGENCY (the only thing gate_b.freshness has
    per-series) through AGENCY_TO_CALENDAR_KEYS against the concept-keyed real
    schema, picking the most lenient (max) of every concept key that agency
    maps to. nbs maps to cpi_ppi (window_days=[9,13], grace=1 -> 14) and
    nbs_activity (window_days=[14,18], grace=2 -> 20) -- nbs_activity wins."""
    path = tmp_path / "release_calendar.yaml"
    path.write_text(
        "cpi_ppi:\n  window_days: [9, 13]\n  freq: M\n  grace_days: 1\n"
        "nbs_activity:\n  window_days: [14, 18]\n  freq: M\n  grace_days: 2\n"
        "consumer_confidence:\n  lag_days: 40\n  freq: M\n  grace_days: 10\n",
        encoding="utf-8",
    )
    calendar, used_fallback = load_release_calendar(path)
    assert not used_fallback
    assert lag_budget_for(calendar, "nbs", "M") == 30 + 18 + 2  # nbs_activity wins over cpi_ppi
    # an agency absent from AGENCY_TO_CALENDAR_KEYS (and from this tiny
    # calendar) falls back to its own FALLBACK_RELEASE_CALENDAR default,
    # independent of the real file having loaded successfully overall.
    assert lag_budget_for(calendar, "mof", "M") == 30 + 25 + 20


def test_lag_budget_for_reads_the_real_repo_release_calendar_natively():
    """The single most important regression here: pipeline/config/
    release_calendar.yaml genuinely exists on disk in this repo (a concurrent
    workstream landed it) -- confirm THAT file, not just a synthetic one, is
    read natively (used_fallback=False), so gate_b.freshness's "not found;
    using fallback" WARN no longer fires for a real run against this repo."""
    real_path = Path(__file__).resolve().parents[2] / "pipeline" / "config" / "release_calendar.yaml"
    assert real_path.exists()
    calendar, used_fallback = load_release_calendar(real_path)
    assert not used_fallback
    assert lag_budget_for(calendar, "nbs", "M") > 0
    assert lag_budget_for(calendar, "pbc", "M") > 0
    assert lag_budget_for(calendar, "cflp", "M") > 0
    assert lag_budget_for(calendar, "customs", "M") > 0


def test_quarterly_series_gets_a_longer_freshness_budget_than_monthly():
    """Regression test: a quarterly print (GDP, income) is legitimately still
    the latest available data for ~90 days before the next quarter's print is
    even due -- it must not be held to a flat monthly-cadence budget."""
    catalog = _catalog_with("s1", "nbs")
    # Same underlying period-end date (2026-03-31), expressed as a monthly vs
    # a quarterly period -- isolates the freq effect from the date itself.
    monthly_bundle = _bundle_with_latest("s1", "2026-03", freq="M")
    quarterly_bundle = _bundle_with_latest("s1", "2026-Q1", freq="Q")

    as_of = date(2026, 7, 8)  # 99 days after 2026-03-31: > monthly budget (70), < quarterly budget (131)
    monthly_report = freshness.run(_bare_ctx(catalog, monthly_bundle, as_of=as_of))
    quarterly_report = freshness.run(_bare_ctx(catalog, quarterly_bundle, as_of=as_of))

    assert any(f.status == "warn" and f.series == "s1" for f in monthly_report.findings)
    assert not any(f.status == "warn" and f.series == "s1" for f in quarterly_report.findings)


def test_fresh_tier1_series_passes():
    catalog = _catalog_with("s1", "nbs")
    bundles = _bundle_with_latest("s1", "2026-06")  # ends 2026-06-30
    ctx = _bare_ctx(catalog, bundles, as_of=date(2026, 7, 8))  # 8 days after period end -- well within budget
    report = freshness.run(ctx)
    assert not any(f.status == "warn" and f.series == "s1" for f in report.findings)


def test_stale_tier1_series_warns_never_blocks():
    catalog = _catalog_with("s1", "nbs")
    bundles = _bundle_with_latest("s1", "2025-01")  # over a year stale
    ctx = _bare_ctx(catalog, bundles, as_of=date(2026, 7, 8))
    report = freshness.run(ctx)
    assert not report.has_block()  # freshness NEVER blocks, by design
    hits = [f for f in report.findings if f.series == "s1"]
    assert hits and hits[0].status == "warn"


def test_tier2_series_are_not_checked_for_freshness():
    catalog = {"series": [{"id": "s2", "tier": 2, "section": "x", "source": {"agency": "nbs"}}]}
    bundles = {"x": {"series": [{"id": "s2", "tier": 2, "name_zh": "y", "latest": {"period": "2020-01"}}]}}
    ctx = _bare_ctx(catalog, bundles, as_of=date(2026, 7, 8))
    report = freshness.run(ctx)
    assert not any(f.series == "s2" for f in report.findings)


def test_generated_at_not_advancing_warns(tmp_path):
    site_data_dir = tmp_path / "site-data"
    site_data_dir.mkdir()
    (site_data_dir / "index.json").write_text('{"generated_at": "2026-07-01T00:00:00Z"}', encoding="utf-8")
    previous_diary = {"generated_at": "2026-07-01T00:00:00Z"}  # same as current -- no advance
    ctx = _bare_ctx({"series": []}, {}, as_of=date(2026, 7, 8), previous_diary=previous_diary, site_data_dir=site_data_dir)
    report = freshness.run(ctx)
    assert any("did not advance" in (f.note or "") for f in report.findings)
    assert not report.has_block()


def test_generated_at_advancing_is_clean(tmp_path):
    site_data_dir = tmp_path / "site-data"
    site_data_dir.mkdir()
    (site_data_dir / "index.json").write_text('{"generated_at": "2026-07-08T00:00:00Z"}', encoding="utf-8")
    previous_diary = {"generated_at": "2026-07-01T00:00:00Z"}
    ctx = _bare_ctx({"series": []}, {}, as_of=date(2026, 7, 8), previous_diary=previous_diary, site_data_dir=site_data_dir)
    report = freshness.run(ctx)
    assert not any("did not advance" in (f.note or "") for f in report.findings)


def test_first_run_ever_has_no_previous_diary_to_compare():
    ctx = _bare_ctx({"series": []}, {}, as_of=date(2026, 7, 8), previous_diary=None)
    report = freshness.run(ctx)
    assert not any("did not advance" in (f.note or "") for f in report.findings)


def test_freshness_rows_extra_is_populated_for_diary_reuse():
    catalog = _catalog_with("s1", "nbs")
    bundles = _bundle_with_latest("s1", "2026-06")
    ctx = _bare_ctx(catalog, bundles, as_of=date(2026, 7, 8))
    report = freshness.run(ctx)
    assert report.extra["freshness_rows"]
    assert report.extra["freshness_rows"][0]["id"] == "s1"


def test_real_fixture_runs_clean_through_freshness():
    ctx = make_ctx(CLEAN_REPO, as_of=date(2026, 7, 8))
    report = freshness.run(ctx)
    assert not report.has_block()
