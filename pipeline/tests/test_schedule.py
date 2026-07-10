"""Tests for pipeline/schedule.py -- release-window due-ness, restricted
2026-07-08 to sources pipeline.runner actually implements (docs/OPERATIONS.md
§1), plus dg_refresh's own multi-window due-check (DG_REFRESH_CHECKPOINTS).
"""
from __future__ import annotations

import json
from datetime import date

from pipeline import schedule
from pipeline.runner import SOURCES as RUNNER_SOURCES


# -- CRITICAL 2 (2026-07-08 adversarial review): freshness must read
#    data/series/<id>.json directly, never data/catalog.json's own `latest`
#    field (which the runtime write path never advances) ----------------------


def _write_series(path, period: str) -> None:
    path.write_text(
        json.dumps({"observations": [{"period": "2020-01", "m": 0}, {"period": period, "m": 1}]}),
        encoding="utf-8",
    )


def test_series_latest_period_reads_the_series_file_not_the_catalog(tmp_path, monkeypatch):
    """The core regression: data/catalog.json is not consulted at all for
    freshness anymore -- a series file's own max observation period is the
    only source of truth. Proven by NOT creating a catalog.json at all."""
    series_dir = tmp_path / "series"
    series_dir.mkdir()
    _write_series(series_dir / "test-a.json", "2026-05")
    monkeypatch.setattr(schedule, "SERIES_DIR", series_dir)

    assert schedule._series_latest_period("test-a") == "2026-05"


def test_series_latest_period_takes_the_max_not_the_last_array_entry(tmp_path, monkeypatch):
    """DATA-CONTRACT §9 says observations[] should be ascending, but this
    must not assume it (mirrors build.py's own array-order defensiveness)."""
    series_dir = tmp_path / "series"
    series_dir.mkdir()
    (series_dir / "test-a.json").write_text(
        json.dumps({"observations": [{"period": "2026-05", "m": 1}, {"period": "2020-01", "m": 0}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(schedule, "SERIES_DIR", series_dir)

    assert schedule._series_latest_period("test-a") == "2026-05"


def test_series_latest_period_none_for_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "SERIES_DIR", tmp_path / "does-not-exist")
    assert schedule._series_latest_period("test-a") is None


def test_load_catalog_latest_reflects_a_fresh_write_the_run_immediately_before(tmp_path, monkeypatch):
    """The exact scenario CRITICAL-2 fixes: pipeline.runner/pipeline.dg_refresh
    just landed a new period into data/series/nbs-cpi-yoy.json this run --
    the VERY NEXT scheduler check must see it immediately (same day, same
    window), not stay stuck on a stale, never-advanced catalog.json value."""
    series_dir = tmp_path / "series"
    series_dir.mkdir()
    _write_series(series_dir / "nbs-cpi-yoy.json", "2026-06")  # freshly landed by a run moments ago
    monkeypatch.setattr(schedule, "SERIES_DIR", series_dir)

    latest = schedule._load_catalog_latest()
    assert latest["nbs-cpi-yoy"] == "2026-06"


def test_due_sources_uses_series_file_freshness_end_to_end(tmp_path, monkeypatch):
    """due_sources() itself, not just the helper: a source whose series file
    was JUST updated to the expected period must stop firing immediately,
    even though the window is still open and even with no catalog.json on
    disk at all."""
    series_dir = tmp_path / "series"
    series_dir.mkdir()
    _write_series(series_dir / "nbs-cpi-yoy.json", "2026-06")  # already current for a July 8 check (expects 2026-06)
    monkeypatch.setattr(schedule, "SERIES_DIR", series_dir)

    due = schedule.due_sources(date(2026, 7, 12))  # inside cpi_ppi's window (day 9-13)
    names = {spec.name for spec, _e, _s in due}
    assert "nbs_cpi" not in names


# -- --due only ever emits sources pipeline.runner implements --------------------


def test_every_active_source_is_implemented_by_runner():
    """The regression this whole fix targets: a scheduled run touching a
    SourceSpec runner.py doesn't implement used to file a FALSE "Gate A
    blocked" issue. Every entry actually in SOURCES today must be runnable."""
    for spec in schedule.SOURCES:
        assert spec.name in RUNNER_SOURCES, f"{spec.name!r} is active in schedule.SOURCES but not in runner.SOURCES"


def test_commented_out_sources_never_appear_in_due_or_explain(tmp_path, monkeypatch, capsys):
    """nbs_ppi/nbs_pmi/customs_trade/nbs_iva/nbs_fai/nbs_70city/nbs_gdp/
    nbs_income are commented out of SOURCES (not deleted -- see module
    docstring) precisely so they can never be emitted, regardless of window
    or freshness. A structural check (SOURCES/due_sources() can only ever
    surface a name from SOURCES + "dg_refresh"), so it can't actually depend
    on real data content -- isolated anyway (empty series dir) for hygiene
    and consistency with the sibling tests below."""
    monkeypatch.setattr(schedule, "SERIES_DIR", tmp_path / "empty-series")  # no files at all -- every lookup is None

    active_names = {spec.name for spec in schedule.SOURCES}
    not_implemented = {"nbs_ppi", "nbs_pmi", "customs_trade", "nbs_iva", "nbs_fai", "nbs_70city", "nbs_gdp", "nbs_income"}
    assert active_names.isdisjoint(not_implemented)

    # sweep a whole year of dates -- these names must never surface via --due
    # regardless of which window is open on any given day
    for month in range(1, 13):
        for day in (1, 9, 14, 25):
            due = {spec.name for spec, _expected, _stored in schedule.due_sources(date(2026, month, day))}
            assert due.isdisjoint(not_implemented)


def test_dg_refresh_is_a_recognized_runner_source():
    assert "dg_refresh" in RUNNER_SOURCES


# -- dg_refresh's own multi-window due-check -------------------------------------


def test_dg_refresh_due_when_trade_window_open_and_customs_stale(monkeypatch):
    """2026-07-08 falls inside the 'trade' window (day 7-14 +grace); the real
    catalog's customs-exports-usd is stale (latest 2026-05, expected 2026-06)
    -- confirmed live against the real repo state 2026-07-08."""
    due = schedule._dg_refresh_due(date(2026, 7, 8), {"customs-exports-usd": "2026-05"})
    assert due == ("2026-06", "customs-exports-usd")


def test_dg_refresh_not_due_when_every_checkpoint_is_current():
    latest = {
        "nbs-cpi-yoy": "9999-12",
        "cflp-pmi-mfg": "9999-12",
        "customs-exports-usd": "9999-12",
        "pbc-m2": "9999-12",
        "nbs-industrial-va": "9999-12",
    }
    assert schedule._dg_refresh_due(date(2026, 7, 8), latest) is None


def test_dg_refresh_not_due_outside_every_window():
    """The five DG_REFRESH_CHECKPOINTS windows collectively cover most of the
    month (by design -- see schedule.py's own comment on why this OR-based
    design was chosen over a narrow hand-picked day range), but day 23 is a
    genuine gap: past nbs_activity's day-18+grace close and before pmi's
    day-25 open, with every other window (cpi_ppi/trade/pboc_money, all
    month_offset=1 reporting the PRIOR month) also long closed."""
    assert schedule._dg_refresh_due(date(2026, 3, 23), {}) is None


def _isolate_series_dir(monkeypatch, tmp_path, **series_periods: str):
    """An isolated data/series/ (never the real repo's) containing exactly
    the synthetic series this test needs, each at the given latest period.
    Regression, 2026-07-08: several tests below used to call due_sources()/
    _explain()/main() against the REAL repo's data/series/ with no
    monkeypatch at all -- correct at the time, but a test must never depend
    on the live data state of the repo: a real scheduled cron run later
    landing fresh data (e.g. customs-exports-usd catching up to 2026-06)
    silently invalidated the "still stale, still due" assumption those
    tests were built on. Every test in this file now constructs its own
    series state explicitly instead."""
    series_dir = tmp_path / "series"
    series_dir.mkdir()
    for series_id, period in series_periods.items():
        _write_series(series_dir / f"{series_id}.json", period)
    monkeypatch.setattr(schedule, "SERIES_DIR", series_dir)
    return series_dir


def test_dg_refresh_appears_in_due_sources_with_the_expected_tuple_shape(tmp_path, monkeypatch):
    # 2026-07-08 falls inside the 'trade' window (day 7-14 +grace); a
    # synthetic customs-exports-usd stale at 2026-05 (expects 2026-06) makes
    # this deterministic regardless of what the real repo's data holds today.
    _isolate_series_dir(monkeypatch, tmp_path, **{"customs-exports-usd": "2026-05"})

    results = schedule.due_sources(date(2026, 7, 8))
    dg_hits = [r for r in results if r[0].name == "dg_refresh"]
    assert len(dg_hits) == 1
    spec, expected, _stored = dg_hits[0]
    assert spec.name == "dg_refresh"
    assert expected == "2026-06"


def test_dg_refresh_explain_output_names_a_checkpoint_series(tmp_path, monkeypatch, capsys):
    _isolate_series_dir(monkeypatch, tmp_path, **{"customs-exports-usd": "2026-05"})

    schedule._explain(date(2026, 7, 8))
    out = capsys.readouterr().out
    assert "dg_refresh" in out
    assert "customs-exports-usd" in out


def test_main_due_includes_dg_refresh_on_a_day_it_fires(tmp_path, monkeypatch, capsys):
    _isolate_series_dir(monkeypatch, tmp_path, **{"customs-exports-usd": "2026-05"})

    exit_code = schedule.main(["--due", "--date", "2026-07-08"])
    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert "dg_refresh" in lines


# -- pre-existing behavior, unaffected by this change ----------------------------


def test_implemented_sources_still_fire_normally_when_stale(tmp_path, monkeypatch):
    """nbs_cpi/nbs_retail/pboc_money's ordinary due-ness (one window group,
    one series) must be completely unaffected by the dg_refresh addition.
    Regression, 2026-07-08: this used to read the REAL repo's
    data/series/nbs-cpi-yoy.json with no isolation at all, assuming it was
    always stale relative to day 12 (expects 2026-06) -- a real scheduled
    cron run later landing June's data broke that assumption. A synthetic,
    explicitly-stale series makes this deterministic."""
    _isolate_series_dir(monkeypatch, tmp_path, **{"nbs-cpi-yoy": "2026-04"})  # stale: expects 2026-06

    due = schedule.due_sources(date(2026, 7, 12), grace=0)
    names = {spec.name for spec, _e, _s in due}
    assert "nbs_cpi" in names  # cpi_ppi window is day 9-13


def test_implemented_sources_do_not_fire_when_already_current(tmp_path, monkeypatch):
    """The other half of the same check: a synthetic series already AT the
    expected period must not be due, even with the window wide open."""
    _isolate_series_dir(monkeypatch, tmp_path, **{"nbs-cpi-yoy": "2026-06"})  # already current

    due = schedule.due_sources(date(2026, 7, 12), grace=0)
    names = {spec.name for spec, _e, _s in due}
    assert "nbs_cpi" not in names
