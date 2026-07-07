"""Tests for pipeline/build.py against synthetic fixtures under
pipeline/tests/fixtures/build/ -- NEVER the real data/ tree, which concurrent
agents are writing in this same rebuild this wave.

Fixture roster (pipeline/tests/fixtures/build/):
    catalog.json                        4 entries across 4 sections + 1 panel
    series/test-cpi-break.json          a no_yoy_across rebase break
    series/test-retail-revised.json     a revision log (one recent, one old) + Jan-Feb
    series/test-fai-ytd.json            calibers:["ytd"] only (no "single" lane)
    panels/test-70city-panel.json       2 cities x 2 metrics x 2 periods, one null cell
    annotations.json                    series-level + period-specific notes

`as_of` is pinned to 2026-07-08 throughout so the 90-day revisions_recent /
12-month break_recent windows are test-stable regardless of when this suite
actually runs.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from pipeline.build import (
    BuildReport,
    _in_no_yoy_window,
    _is_break_first,
    _month_num,
    _period_label_zh,
    _prev_ytd_period,
    _recent_revisions,
    _resolve_file_path,
    _resolve_prev,
    build_site_data,
    main,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "build"
AS_OF = date(2026, 7, 8)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _series_by_id(section_bundle: dict, series_id: str) -> dict:
    return next(s for s in section_bundle["series"] if s["id"] == series_id)


@pytest.fixture()
def built(tmp_path: Path):
    out_dir = tmp_path / "site-data"
    report = build_site_data(FIXTURES_DIR, out_dir, as_of=AS_OF)
    return report, out_dir


# -- top-level shape ----------------------------------------------------------------


def test_build_report_counts(built):
    report, _out_dir = built
    assert isinstance(report, BuildReport)
    assert report.sections == 4  # prices, consumption, macro, property
    assert report.series == 3  # cpi-break, retail-revised, fai-ytd (panel excluded)
    assert report.panels == 1
    assert report.tiles == 2  # tier-1, non-panel: cpi-break + retail-revised (fai-ytd is tier 2)


def test_expected_files_are_written(built):
    _report, out_dir = built
    assert (out_dir / "index.json").exists()
    assert (out_dir / "sections" / "prices.json").exists()
    assert (out_dir / "sections" / "consumption.json").exists()
    assert (out_dir / "sections" / "macro.json").exists()
    assert (out_dir / "sections" / "property.json").exists()
    assert (out_dir / "panels" / "test-70city-panel.json").exists()
    # UTF-8, no BOM, trailing newline (DATA-CONTRACT §9's conventions, applied to site-data too)
    raw = (out_dir / "index.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n")


def test_generated_at_is_passed_through_from_catalog_not_wall_clock(built):
    """DATA-CONTRACT §9's idempotence principle, applied to the build stage:
    generated_at must be a deterministic function of the input, not
    datetime.now()."""
    _report, out_dir = built
    index = _load(out_dir / "index.json")
    assert index["generated_at"] == "2026-06-20T00:00:00Z"  # catalog.json's own generated_at
    section = _load(out_dir / "sections" / "prices.json")
    assert section["generated_at"] == "2026-06-20T00:00:00Z"


def test_section_bundle_shape_matches_contract(built):
    _report, out_dir = built
    section = _load(out_dir / "sections" / "consumption.json")
    assert section["section"] == "consumption"
    assert section["catalog_version"] == "test-1"
    assert isinstance(section["series"], list)


def test_headline_direction_is_none_not_flat_when_yoy_is_blocked_by_break():
    """Regression: a latest observation that exists (has a level value) but
    whose YoY is suppressed by a no_yoy_across window must report
    direction=None ("unknown"), never "flat" -- "flat" is a specific claim
    (0% change) that would be fabricated here, not a safe default."""
    from pipeline.build import _build_series_entry

    series = json.loads((FIXTURES_DIR / "series" / "test-cpi-break.json").read_text(encoding="utf-8"))
    catalog = json.loads((FIXTURES_DIR / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog["series"] if e["id"] == "test-cpi-break")
    # truncate to 2026-02: inside the blocked window (m present, m_yoy absent)
    truncated = dict(series, observations=[o for o in series["observations"] if o["period"] <= "2026-02"])

    bundle_entry = _build_series_entry(entry, truncated, {}, AS_OF)
    assert bundle_entry["latest"]["period"] == "2026-02"
    assert bundle_entry["headline"]["direction"] is None
    assert bundle_entry["headline"]["latest_yoy"] is None
    assert bundle_entry["takeaway"] is None


# -- CPI break: YoY nulls, is_break_first, normal takeaway afterwards --------------


def test_yoy_series_is_null_across_the_break_window(built):
    _report, out_dir = built
    prices = _load(out_dir / "sections" / "prices.json")
    cpi = _series_by_id(prices, "test-cpi-break")
    by_period = {pt["period"]: pt["yoy"] for pt in cpi["yoy_series"]}
    assert by_period["2025-11"] == 1.8
    assert by_period["2025-12"] == 1.9
    assert by_period["2026-01"] is None
    assert by_period["2026-02"] is None
    assert by_period["2026-03"] is None
    assert by_period["2026-04"] == 2.2  # yoy_valid_from -- first period back in bounds
    assert by_period["2026-05"] == 2.5
    assert by_period["2026-06"] == 2.0


def test_cpi_latest_takeaway_is_normal_deceleration_after_the_break(built):
    """Latest observation (2026-06) is two months past yoy_valid_from -- an
    ordinary comparison against the prior (also post-break) month, not the
    break-adjacent template."""
    _report, out_dir = built
    prices = _load(out_dir / "sections" / "prices.json")
    cpi = _series_by_id(prices, "test-cpi-break")
    assert cpi["latest"]["period"] == "2026-06"
    assert cpi["headline"]["direction"] == "up"
    assert cpi["takeaway"] == "2026 年 6 月测试_居民消费价格指数同比上涨 2.0%，涨幅较上月收窄 0.5 个百分点"


def test_cpi_break_first_takeaway_fires_at_yoy_valid_from(built):
    """White-box: the first period with a real post-break YoY (2026-04) must
    use the break-adjacent template, even though build_site_data's *latest*
    for the full fixture is 2026-06 (this checks the mechanism directly rather
    than needing a second, truncated fixture file)."""
    from pipeline.build import _build_series_entry

    series = json.loads((FIXTURES_DIR / "series" / "test-cpi-break.json").read_text(encoding="utf-8"))
    catalog = json.loads((FIXTURES_DIR / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog["series"] if e["id"] == "test-cpi-break")
    truncated = dict(series, observations=[o for o in series["observations"] if o["period"] <= "2026-04"])

    bundle_entry = _build_series_entry(entry, truncated, {}, AS_OF)
    assert bundle_entry["latest"]["period"] == "2026-04"
    assert bundle_entry["takeaway"] == "口径调整后首期数据：测试_居民消费价格指数上涨 2.2%（与旧口径不可比）"


def test_cpi_breaks_are_passed_through(built):
    _report, out_dir = built
    prices = _load(out_dir / "sections" / "prices.json")
    cpi = _series_by_id(prices, "test-cpi-break")
    assert cpi["breaks"][0]["effective"] == "2026-01"
    assert cpi["breaks"][0]["no_yoy_across"] is True


# -- retail: revisions_recent, annotations, Jan-Feb, sign flip ----------------------


def test_revisions_recent_includes_only_the_recent_one(built):
    _report, out_dir = built
    consumption = _load(out_dir / "sections" / "consumption.json")
    retail = _series_by_id(consumption, "test-retail-revised")
    assert retail["revisions_recent"] == [{"period": "2026-04", "measure": "ytd", "revised_on": "2026-06-16"}]
    # the 2025-12-01 revision to 2026-03/m_yoy is far outside the 90-day window
    assert all(r["period"] != "2026-03" for r in retail["revisions_recent"])


def test_annotations_merge_series_level_and_period_specific(built):
    _report, out_dir = built
    consumption = _load(out_dir / "sections" / "consumption.json")
    retail = _series_by_id(consumption, "test-retail-revised")
    periods = {a["period"] for a in retail["annotations"]}
    assert None in periods  # series-level note
    assert "2026-03" in periods
    series_note = next(a for a in retail["annotations"] if a["period"] is None)
    assert series_note["text_zh"] == "测试用序列级备注。"
    assert series_note["kind"] == "context"


def test_retail_latest_is_a_sign_flip_takeaway(built):
    _report, out_dir = built
    consumption = _load(out_dir / "sections" / "consumption.json")
    retail = _series_by_id(consumption, "test-retail-revised")
    assert retail["latest"]["period"] == "2026-05"
    assert retail["latest"]["m_yoy"] == -0.6
    assert retail["prev"]["period"] == "2026-04"
    assert retail["headline"]["direction"] == "down"
    assert retail["headline"]["delta_pp_vs_prev"] == pytest.approx(-5.7)
    assert retail["takeaway"] == "2026 年 5 月测试_社会消费品零售总额同比由升转降，下降 0.6%"


def test_retail_jan_feb_observation_is_in_yoy_series_with_correct_label():
    """White-box on _period_label_zh + the raw yoy_series build: the Jan-Feb
    row must carry the cumulative (em-dash) label form, not a bare month."""
    from pipeline.build import _build_yoy_series

    series = json.loads((FIXTURES_DIR / "series" / "test-retail-revised.json").read_text(encoding="utf-8"))
    yoy = _build_yoy_series(series["observations"], "m_yoy", [])
    by_period = {pt["period"]: pt["yoy"] for pt in yoy}
    assert by_period["2026-02"] == 4.0
    label = _period_label_zh("2026-02", freq="M", caliber="single", span=2)
    assert label == "2026 年 1—2 月"


def test_quarterly_ytd_only_series_without_real_yoy_does_not_crash():
    """Regression: the real catalog has ~20 series shaped exactly like this
    (nbs-income-median, nbs-consumption-expenditure-rural, ...) -- freq=='Q',
    calibers==['ytd'], no real_yoy. Before the freq=='M' guard on
    is_ytd_only, this raised ValueError (_month_num("2026-Q2") is None, and
    the YTD-only template requires a month number)."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-income-median-q",
        "name_zh": "测试_人均可支配收入中位数",
        "name_en": "TEST median disposable income",
        "unit_zh": "元",
        "unit_en": "CNY",
        "value_type": "level",
        "freq": "Q",
        "tier": 2,
        "calibers": ["ytd"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2026-Q1", "ytd": 9000, "ytd_yoy": 4.0},
            {"period": "2026-Q2", "ytd": 18500, "ytd_yoy": 5.0},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["headline"]["caliber"] == "ytd"
    assert bundle_entry["takeaway"] == "2026 年 2 季度测试_人均可支配收入中位数同比增长 5.0%，增速较上季度加快 1.0 个百分点"


# -- FAI: ytd-only headline caliber --------------------------------------------------


def test_fai_headline_uses_ytd_caliber(built):
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    fai = _series_by_id(macro, "test-fai-ytd")
    assert fai["headline"]["caliber"] == "ytd"
    assert fai["latest"]["period"] == "2026-05"
    assert "m" not in fai["latest"]  # single caliber never populated on this series
    assert fai["latest"]["ytd_yoy"] == 3.8


def test_fai_takeaway_uses_ytd_only_phrasing(built):
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    fai = _series_by_id(macro, "test-fai-ytd")
    assert fai["takeaway"] == "1-5 月测试_固定资产投资累计同比增长 3.8%，增速较 1-4 月加快 0.6 个百分点"


def test_fai_is_tier_2_and_excluded_from_tiles_but_present_in_freshness(built):
    _report, out_dir = built
    index = _load(out_dir / "index.json")
    assert all(t["id"] != "test-fai-ytd" for t in index["tiles"])
    assert any(f["id"] == "test-fai-ytd" for f in index["freshness"])


# -- panel bundle ---------------------------------------------------------------------


def test_panel_bundle_mirrors_and_computes_aggregates(built):
    _report, out_dir = built
    panel = _load(out_dir / "panels" / "test-70city-panel.json")
    assert panel["periods"] == ["2026-04", "2026-05"]
    assert panel["cells"]["北京"]["new_home"]["m"] == [-0.2, -0.3]

    agg = panel["national_aggregate"]
    assert agg["new_home"]["m"] == [pytest.approx(-0.05), pytest.approx(-0.05)]
    assert agg["new_home"]["m_yoy"] == [pytest.approx(-0.55), pytest.approx(-0.65)]
    assert agg["resale_home"]["m"] == [pytest.approx(-0.3), pytest.approx(-0.6)]  # 2nd period: 上海 cell is null, skipped

    assert panel["up_count"]["new_home"] == [1, 1]  # 上海 positive both periods
    assert panel["up_count"]["resale_home"] == [0, 0]

    latest = panel["latest_by_city"]
    assert latest["北京"]["new_home"] == {"m": -0.3, "m_yoy": -2.5}
    assert latest["上海"]["resale_home"] == {"m": None, "m_yoy": None}


def test_panel_excluded_from_index_tiles_and_freshness(built):
    _report, out_dir = built
    index = _load(out_dir / "index.json")
    assert all(t["id"] != "test-70city-panel" for t in index["tiles"])
    assert all(f["id"] != "test-70city-panel" for f in index["freshness"])


# -- determinism: byte-identical re-run -----------------------------------------------


def test_build_is_byte_stable_across_two_runs(tmp_path):
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"
    build_site_data(FIXTURES_DIR, out_a, as_of=AS_OF)
    build_site_data(FIXTURES_DIR, out_b, as_of=AS_OF)

    files_a = sorted(p.relative_to(out_a) for p in out_a.rglob("*.json"))
    files_b = sorted(p.relative_to(out_b) for p in out_b.rglob("*.json"))
    assert files_a == files_b
    assert len(files_a) > 0
    for rel in files_a:
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), f"non-deterministic output in {rel}"


# -- annotations.json is optional ------------------------------------------------------


def test_missing_annotations_file_is_treated_as_empty(tmp_path):
    data_copy = tmp_path / "data"
    shutil.copytree(FIXTURES_DIR, data_copy)
    (data_copy / "annotations.json").unlink()  # simulate "may not exist yet"

    report = build_site_data(data_copy, tmp_path / "out", as_of=AS_OF)
    assert report.series == 3

    consumption = _load(tmp_path / "out" / "sections" / "consumption.json")
    retail = _series_by_id(consumption, "test-retail-revised")
    assert retail["annotations"] == []


# -- CLI wiring (explicit --data/--out only -- never the real data/ dir) --------------


def test_cli_main_writes_expected_files(tmp_path, capsys):
    out_dir = tmp_path / "cli-out"
    exit_code = main(["--data", str(FIXTURES_DIR), "--out", str(out_dir)])
    assert exit_code == 0
    assert (out_dir / "index.json").exists()
    captured = capsys.readouterr()
    assert "wrote 4 section bundle(s)" in captured.out


# -- white-box: period labels, break windows, prev-resolution, path handling ----------


@pytest.mark.parametrize(
    "period,freq,caliber,span,expected",
    [
        ("2026-05", "M", "single", 1, "2026 年 5 月"),
        ("2026-02", "M", "single", 2, "2026 年 1—2 月"),  # Jan-Feb: em dash, per DATA-CONTRACT §12
        ("2026-05", "M", "ytd", 1, "2026 年 1—5 月"),  # cumulative caliber headlined, even without span>1
        ("2026-Q2", "Q", "single", 1, "2026 年 2 季度"),  # Arabic digit, lead's typography decision
        ("2026", "A", "single", 1, "2026 年"),
    ],
)
def test_period_label_zh(period, freq, caliber, span, expected):
    assert _period_label_zh(period, freq=freq, caliber=caliber, span=span) == expected


def test_prev_ytd_period_is_none_at_years_first_print():
    assert _prev_ytd_period("2026-02", "M") is None
    assert _prev_ytd_period("2026-03", "M") == "2026-02"
    assert _prev_ytd_period("2026-12", "M") == "2026-11"


def test_prev_ytd_period_quarterly():
    assert _prev_ytd_period("2026-Q1", "Q") is None
    assert _prev_ytd_period("2026-Q2", "Q") == "2026-Q1"
    assert _prev_ytd_period("2026-Q4", "Q") == "2026-Q3"


def test_month_num():
    assert _month_num("2026-05") == 5
    assert _month_num("2026-Q2") is None
    assert _month_num("2026") is None


def test_in_no_yoy_window():
    breaks = [{"effective": "2026-01", "no_yoy_across": True, "yoy_valid_from": "2027-01"}]
    assert _in_no_yoy_window("2025-12", breaks) is False
    assert _in_no_yoy_window("2026-01", breaks) is True
    assert _in_no_yoy_window("2026-12", breaks) is True
    assert _in_no_yoy_window("2027-01", breaks) is False


def test_resolve_prev_jan_feb_jumps_12_months_not_array_adjacent():
    observations = [
        {"period": "2025-02", "span": 2, "flags": ["jan_feb"], "m_yoy": 3.2},
        {"period": "2025-03", "m_yoy": 4.1},
        {"period": "2026-02", "span": 2, "flags": ["jan_feb"], "m_yoy": 4.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    latest = observations[-1]
    prev = _resolve_prev(observations, index_by_period, latest, caliber="single", freq="M", breaks=[])
    assert prev["period"] == "2025-02"  # NOT "2025-03" (which would be array-adjacent)


def test_resolve_prev_returns_none_across_a_break_wall():
    observations = [
        {"period": "2025-12", "m_yoy": 1.9},
        {"period": "2026-01", "m_yoy": 9.9},  # hypothetical stray value inside the blocked window
    ]
    index_by_period = {o["period"]: o for o in observations}
    breaks = [{"effective": "2026-01", "no_yoy_across": True}]
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="single", freq="M", breaks=breaks)
    assert prev is None


def test_resolve_prev_quarterly_ytd_is_none_at_q1():
    """Regression: the real catalog has ~20 freq=='Q'/calibers==['ytd']
    series (nbs-income-median, ...). Q1's cumulative print is the year's
    first -- there is no "previous quarter in this year" to compare against."""
    observations = [
        {"period": "2025-Q4", "ytd_yoy": 4.5},
        {"period": "2026-Q1", "ytd_yoy": 4.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", freq="Q", breaks=[])
    assert prev is None  # NOT 2025-Q4 (which would be array-adjacent, but a different year)


def test_resolve_prev_quarterly_ytd_uses_same_year_prior_quarter():
    observations = [
        {"period": "2026-Q1", "ytd_yoy": 4.0},
        {"period": "2026-Q2", "ytd_yoy": 5.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", freq="Q", breaks=[])
    assert prev["period"] == "2026-Q1"


def test_resolve_prev_annual_ytd_falls_back_to_array_adjacency():
    """Annual data has no intra-year cumulative-reset concept -- ytd caliber
    at freq=='A' must not attempt the monthly/quarterly calendar lookup (which
    would crash trying to parse "2026" as "YYYY-MM")."""
    observations = [
        {"period": "2025", "ytd_yoy": 4.5},
        {"period": "2026", "ytd_yoy": 5.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", freq="A", breaks=[])
    assert prev["period"] == "2025"


def test_is_break_first_true_when_prev_yoy_blocked_by_break():
    latest = {"period": "2026-04", "flags": []}
    prev = {"period": "2026-03"}
    breaks = [{"effective": "2026-01", "no_yoy_across": True, "yoy_valid_from": "2026-04"}]
    assert _is_break_first(latest, prev, None, breaks) is True


def test_is_break_first_true_when_flag_explicitly_set():
    latest = {"period": "2023-12", "flags": ["break_first"]}
    assert _is_break_first(latest, None, None, []) is True


def test_is_break_first_false_for_an_ordinary_period():
    latest = {"period": "2026-06", "flags": []}
    prev = {"period": "2026-05"}
    breaks = [{"effective": "2026-01", "no_yoy_across": True, "yoy_valid_from": "2026-04"}]
    assert _is_break_first(latest, prev, 2.5, breaks) is False


def test_recent_revisions_window():
    revisions = [
        {"period": "2026-04", "measure": "ytd", "old": 1, "new": 2, "revised_on": "2026-06-16"},
        {"period": "2026-03", "measure": "m_yoy", "old": 1, "new": 2, "revised_on": "2025-12-01"},
        {"period": "2026-02", "measure": "m", "old": 1, "new": 2, "revised_on": None},  # unknown date, excluded
    ]
    recent = _recent_revisions(revisions, AS_OF, window_days=90)
    assert [r["period"] for r in recent] == ["2026-04"]


def test_resolve_file_path_strips_redundant_data_prefix(tmp_path):
    assert _resolve_file_path(tmp_path, "data/series/x.json") == tmp_path / "series" / "x.json"
    assert _resolve_file_path(tmp_path, "series/x.json") == tmp_path / "series" / "x.json"
