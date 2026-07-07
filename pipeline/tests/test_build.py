"""Tests for pipeline/build.py against synthetic fixtures under
pipeline/tests/fixtures/build/ -- NEVER the real data/ tree, which concurrent
agents are writing in this same rebuild this wave.

Fixture roster (pipeline/tests/fixtures/build/):
    catalog.json                             5 entries across 4 sections + 1 panel
    series/test-cpi-break.json               a no_yoy_across rebase break
    series/test-retail-revised.json          a revision log (one recent, one old) + Jan-Feb
    series/test-fai-ytd.json                 calibers:["ytd"] only (no "single" lane)
    series/test-income-mixed-annual.json     freq=="Q" + a 2015-2016 bare-"YYYY" annual-
                                              supplement layer before quarterly resumes --
                                              reproduces pipeline/migrate/REPORT.md item 6
    panels/test-70city-panel.json            2 cities x 2 metrics x 2 periods, one null cell
    annotations.json                         series-level + period-specific notes

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
    _load_json_with_retry,
    _month_num,
    _period_label_zh,
    _period_shape,
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
    # cpi-break, retail-revised, fai-ytd, income-mixed-annual, fai-yoy-pct,
    # iva-yoy-pct, gdp-contribution-ratio (panel excluded)
    assert report.series == 7
    assert report.panels == 1
    # tier-1, non-panel: cpi-break + retail-revised + iva-yoy-pct (fai-ytd/
    # income-mixed-annual/fai-yoy-pct/gdp-contribution-ratio are tier 2/3)
    assert report.tiles == 3


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
    row must carry the cumulative (hyphen) label form, not a bare month."""
    from pipeline.build import _build_yoy_series

    series = json.loads((FIXTURES_DIR / "series" / "test-retail-revised.json").read_text(encoding="utf-8"))
    yoy = _build_yoy_series(series["observations"], "m_yoy", [])
    by_period = {pt["period"]: pt["yoy"] for pt in yoy}
    assert by_period["2026-02"] == 4.0
    label = _period_label_zh("2026-02", caliber="single", span=2)
    assert label == "2026 年 1-2 月"


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
    assert bundle_entry["takeaway"] == "2026 年二季度测试_人均可支配收入中位数同比增长 5.0%，增速较上季度加快 1.0 个百分点"


# -- mixed quarterly + annual-supplement series (pipeline/migrate/REPORT.md item 6) --


def test_mixed_annual_quarterly_series_builds_without_crashing(built):
    """Regression, exact real shape: freq=='Q', calibers==['ytd'], a 2015-2016
    bare-"YYYY" annual-supplement layer before quarterly data resumes at
    2017-Q1. Before the _period_shape dispatch fix, _period_label_zh crashed
    on any bare-"YYYY" period passed through with freq=='Q'
    ("2016".split("-Q") has nothing to unpack)."""
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    mixed = _series_by_id(macro, "test-income-mixed-annual")

    # latest is the most recent quarterly observation, not touched by the
    # annual rows at all (ytd-caliber prev resolution is a calendar lookup
    # that never reaches array-adjacency here).
    assert mixed["latest"]["period"] == "2017-Q2"
    assert mixed["latest"]["period_label_zh"] == "2017 年二季度"
    assert mixed["prev"]["period"] == "2017-Q1"
    assert mixed["takeaway"] == "2017 年二季度测试_人均可支配收入中位数同比增长 6.2%，增速较上季度加快 0.2 个百分点"

    # the annual rows are still safely represented in the chart series (no
    # crash building them), each with the "全年" label baked into nothing here
    # (yoy_series only carries {period, yoy} -- label formatting is exercised
    # separately below) but present and correctly valued.
    by_period = {pt["period"]: pt["yoy"] for pt in mixed["yoy_series"]}
    assert by_period["2015"] == 7.0
    assert by_period["2016"] == 6.7
    assert by_period["2017-Q1"] == 6.0
    assert by_period["2017-Q2"] == 6.2


def test_annual_label_used_for_bare_year_period_inside_quarterly_series():
    assert _period_label_zh("2016", caliber="ytd") == "2016 年全年"


def test_prev_resolution_allows_annual_to_annual_same_shape_comparison():
    """White-box, truncated to just the annual-supplement rows: latest=="2016"
    is itself annual-shaped (not monthly/quarterly), so ytd-caliber's calendar
    lookup doesn't apply -- it falls to array-adjacency, which must still work
    correctly when the predecessor is ALSO annual-shaped (not reject a
    legitimate same-shape comparison just because the series is nominally
    quarterly)."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-income-mixed-annual-truncated",
        "name_zh": "测试_人均可支配收入中位数",
        "name_en": "TEST",
        "unit_zh": "元",
        "unit_en": "CNY",
        "value_type": "level",
        "freq": "Q",
        "tier": 3,
        "calibers": ["ytd"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2015", "ytd": 15000, "ytd_yoy": 7.0},
            {"period": "2016", "ytd": 16000, "ytd_yoy": 6.7},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["latest"]["period"] == "2016"
    assert bundle_entry["latest"]["period_label_zh"] == "2016 年全年"
    assert bundle_entry["prev"]["period"] == "2015"  # array-adjacent, same shape -- allowed
    # freq passed to takeaways.py is shape-derived ("A"), not the series'
    # nominal "Q" -- so the reference word is "上年", never "较上季度".
    assert bundle_entry["takeaway"] == "2016 年全年测试_人均可支配收入中位数同比增长 6.7%，增速较上年放缓 0.3 个百分点"


def test_prev_resolution_rejects_annual_to_quarterly_shape_mismatch():
    """White-box: a caliber=='single' quarterly series (so prev resolution
    always takes the array-adjacent branch, never the ytd calendar lookup)
    whose immediate predecessor is an annual-supplement row. Before the same-
    shape guard, this would have been treated as "last quarter" and later
    crashed _period_label_zh; now it must resolve to no comparable previous."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-single-caliber-mixed",
        "name_zh": "测试_单一口径混合序列",
        "name_en": "TEST",
        "unit_zh": "元",
        "unit_en": "CNY",
        "value_type": "level",
        "freq": "Q",
        "tier": 3,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2016", "m": 16000, "m_yoy": 6.7},
            {"period": "2017-Q1", "m": 4200, "m_yoy": 6.0},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["latest"]["period"] == "2017-Q1"
    assert bundle_entry["prev"] is None  # NOT "2016" -- different period shape, not comparable
    assert bundle_entry["takeaway"] == "2017 年一季度测试_单一口径混合序列同比增长 6.0%"


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


# -- value_type=="yoy_pct": FAI/industrial-va's REAL shape (no absolute level at
# all, not even the "test-fai-ytd" fixture above's synthetic ytd level) -------------
#
# test-fai-ytd (fixture above) has calibers==["ytd"] with a genuine "ytd" level
# alongside "ytd_yoy" -- a plausible shape in the abstract, but NOT the real
# nbs-fai.json's shape (DG publishes no absolute FAI level at all, ever -- see
# pipeline/backfill/backfill.py's build_fai coverage_note_zh). Before the
# value_type=="yoy_pct" fix, a series shaped like the real FAI (value_field
# "ytd" never populated, only "ytd_yoy") hit the top-of-function
# `if latest is None: return _empty_series_entry(...)` and got a permanently
# null latest/headline/takeaway -- "build.py currently emits an empty bundle
# entry for it" (the exact bug this fix addresses). industrial-va's real shape
# is the mirror image: its own "m"/"ytd" fields (not "m_yoy"/"ytd_yoy") ARE
# populated, but the fields are already YoY growth rates, not a true level --
# "latest" WAS found (so not empty), but yoy_series/headline/takeaway were
# stuck null since m_yoy never exists.


def test_fai_real_shape_no_longer_returns_an_empty_bundle(built):
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    fai = _series_by_id(macro, "test-fai-yoy-pct")
    assert fai["plot_kind"] == "yoy"
    assert fai["latest"]["period"] == "2026-05"
    assert fai["latest"]["ytd_yoy"] == -4.1
    assert fai["headline"]["direction"] == "down"
    assert fai["headline"]["latest_yoy"] == -4.1
    assert fai["yoy_series"] == [
        {"period": "2026-03", "yoy": 1.7},
        {"period": "2026-04", "yoy": -1.6},
        {"period": "2026-05", "yoy": -4.1},
    ]
    assert fai["takeaway"] == (
        "1-5 月测试_固定资产投资累计同比下降 4.1%，降幅较 1-4 月扩大 2.5 个百分点，连续 2 个月同比下降"
    )


def test_industrial_va_real_shape_gets_a_populated_yoy_series_and_takeaway(built):
    """industrial-va's real shape: value_field ("m"/"ytd") IS populated (so
    the old code never hit the empty-bundle path at all), but with a growth
    rate, not a level -- yoy_field ("m_yoy") never exists, so headline/
    takeaway/yoy_series were stuck null/empty before this fix."""
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    iva = _series_by_id(macro, "test-iva-yoy-pct")
    assert iva["plot_kind"] == "yoy"
    assert iva["latest"]["period"] == "2026-05"
    assert iva["latest"]["m"] == 4.5
    assert iva["headline"]["direction"] == "up"
    assert iva["headline"]["latest_yoy"] == 4.5
    assert iva["yoy_series"] == [
        {"period": "2026-03", "yoy": 5.7},
        {"period": "2026-04", "yoy": 4.1},
        {"period": "2026-05", "yoy": 4.5},
    ]
    assert iva["takeaway"] == "2026 年 5 月测试_工业增加值同比增长 4.5%，增速较上月加快 0.4 个百分点"


def test_iva_is_tier_1_and_included_in_tiles(built):
    """Unlike FAI (tier 2), industrial-va is tier 1 -- once its takeaway is no
    longer permanently null, it must actually show up as a landing tile."""
    _report, out_dir = built
    index = _load(out_dir / "index.json")
    tile = next((t for t in index["tiles"] if t["id"] == "test-iva-yoy-pct"), None)
    assert tile is not None
    assert tile["takeaway"] is not None


# -- value_type=="ratio": GDP-contribution's real shape (a point-in-time share,
# not a YoY rate -- widens the level-only path, gated off the boom-bust line) ------


def test_gdp_contribution_ratio_gets_a_level_only_takeaway_not_null(built):
    """GDP-contribution shares (value_type=='ratio') never publish a YoY --
    before widening _is_level_only_series, this stayed takeaway=None forever
    (excluded from the level-only path by the value_type=='index' gate,
    matching PMI's fix but not shares). Must also use the FREQ-correct
    previous-period word ("比上季度", not a hardcoded "比上月") since this
    series is quarterly -- a pre-existing gap in generate_level_takeaway that
    widening this path exposed and this change also fixes."""
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    contrib = _series_by_id(macro, "test-gdp-contribution-ratio")
    assert contrib["plot_kind"] == "level"  # a share is a genuine level, not a rate -- unaffected by the yoy_pct fix
    assert contrib["headline"]["direction"] is None  # no YoY concept at all for a contribution share
    assert contrib["takeaway"] == "2026 年一季度测试_消费贡献率为 46.7%，比上季度回落 6.2 个点"
    assert "荣枯线" not in contrib["takeaway"]  # boom-bust line is meaningless for a contribution share


def test_gdp_contribution_ratio_yoy_series_is_all_null_not_fabricated(built):
    """A share has no YoY concept -- yoy_series must stay all-null (a
    legitimately empty chart line), never fabricated from the share itself
    (that would misrepresent a level as if it were a rate of change)."""
    _report, out_dir = built
    macro = _load(out_dir / "sections" / "macro.json")
    contrib = _series_by_id(macro, "test-gdp-contribution-ratio")
    assert all(pt["yoy"] is None for pt in contrib["yoy_series"])
    assert contrib["level_series"] == [{"period": "2025-Q4", "m": 52.9}, {"period": "2026-Q1", "m": 46.7}]


def test_plot_kind_defaults_to_level_for_an_ordinary_series(built):
    _report, out_dir = built
    prices = _load(out_dir / "sections" / "prices.json")
    cpi = _series_by_id(prices, "test-cpi-break")
    assert cpi["plot_kind"] == "level"


def test_yoy_only_populated_field_direct():
    """Unit test on the helper itself: whichever of (value_field, yoy_field)
    actually has non-null data, scanning most-recent-first, wins -- covers
    both real-world directions (FAI: only the yoy slot; industrial-va: only
    the value slot) plus the "neither populated yet" edge case."""
    from pipeline.build import _yoy_only_populated_field

    assert _yoy_only_populated_field("ytd", "ytd_yoy", [{"period": "2026-05", "ytd_yoy": -4.1}]) == "ytd_yoy"
    assert _yoy_only_populated_field("m", "m_yoy", [{"period": "2026-05", "m": 4.5}]) == "m"
    assert _yoy_only_populated_field("m", "m_yoy", [{"period": "2026-05"}]) is None


# -- value_type=="rate_pct": the 城镇调查失业率 family (no YoY, no 荣枯线, streak n>=3) --

_UNEMP_ENTRY = {
    "id": "test-urban-unemp",
    "name_zh": "测试_全国城镇调查失业率",
    "name_en": "TEST surveyed urban unemployment rate",
    "unit_zh": "%",
    "unit_en": "%",
    "value_type": "rate_pct",
    "freq": "M",
    "tier": 1,
    "calibers": ["single"],
    "derived": None,
}


def test_rate_pct_takeaway_rising():
    from pipeline.build import _build_series_entry

    series = {
        "observations": [
            {"period": "2026-04", "m": 5.0},
            {"period": "2026-05", "m": 5.2},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(_UNEMP_ENTRY, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 5 月测试_全国城镇调查失业率为 5.2%，比上月上升 0.2 个百分点"
    assert "荣枯线" not in bundle_entry["takeaway"]
    assert bundle_entry["headline"]["direction"] is None  # no YoY concept -- "unknown", never fabricated


def test_rate_pct_takeaway_falling():
    from pipeline.build import _build_series_entry

    series = {
        "observations": [
            {"period": "2026-04", "m": 5.2},
            {"period": "2026-05", "m": 5.0},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(_UNEMP_ENTRY, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 5 月测试_全国城镇调查失业率为 5.0%，比上月下降 0.2 个百分点"


def test_rate_pct_takeaway_flat():
    from pipeline.build import _build_series_entry

    series = {
        "observations": [
            {"period": "2026-04", "m": 5.0},
            {"period": "2026-05", "m": 5.0},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(_UNEMP_ENTRY, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 5 月测试_全国城镇调查失业率为 5.0%，与上月持平"


def test_rate_pct_takeaway_streak_of_3_gets_the_clause():
    from pipeline.build import _build_series_entry

    series = {
        "observations": [
            {"period": "2026-02", "m": 4.8},
            {"period": "2026-03", "m": 4.9},
            {"period": "2026-04", "m": 5.0},
            {"period": "2026-05", "m": 5.1},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(_UNEMP_ENTRY, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 5 月测试_全国城镇调查失业率为 5.1%，比上月上升 0.1 个百分点，连续 3 个月上升"


def test_rate_pct_takeaway_streak_of_2_is_not_shown():
    """2-month streaks are noise for a rate that wiggles month to month
    (unlike the YoY streak's n>=2 floor) -- the clause only appears at n>=3."""
    from pipeline.build import _build_series_entry

    series = {
        "observations": [
            {"period": "2026-03", "m": 4.9},
            {"period": "2026-04", "m": 5.0},
            {"period": "2026-05", "m": 5.1},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(_UNEMP_ENTRY, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 5 月测试_全国城镇调查失业率为 5.1%，比上月上升 0.1 个百分点"
    assert "连续" not in bundle_entry["takeaway"]


def test_rate_pct_frozen_series_still_generates_a_takeaway():
    """The old-basis youth series (nbs-urban-unemp-youth-1624's real shape)
    stopped publishing in 2023-07 and is frozen (catalog `end`), but build.py
    doesn't special-case "is this series stale" -- it just runs the same
    level-only template against whatever the LATEST available observation
    is. The stale period_label_zh ("2023 年 6 月", not a recent month) is
    exactly what makes the staleness visible to the site; the takeaway itself
    must still generate, not silently stay null."""
    from pipeline.build import _build_series_entry

    frozen_entry = dict(_UNEMP_ENTRY, id="test-urban-unemp-youth-frozen")
    series = {
        "observations": [
            {"period": "2023-05", "m": 21.2},
            {"period": "2023-06", "m": 21.3},
        ],
        "revisions": [],
        "breaks": [
            {"effective": "2023-08", "kind": "suspended", "no_yoy_across": True}
        ],
    }
    bundle_entry = _build_series_entry(frozen_entry, series, {}, AS_OF)
    assert bundle_entry["latest"]["period"] == "2023-06"
    assert bundle_entry["takeaway"] == "2023 年 6 月测试_全国城镇调查失业率为 21.3%，比上月上升 0.1 个百分点"


def test_rate_pct_break_first_obs_does_not_compare_across():
    """The exstudent series' real shape: a brand-new id whose first
    observation is flagged break_first (no prior data in ITS OWN series at
    all -- the frozen old-basis series is a completely separate id/file, so
    there is structurally nothing to compare across even without this test).
    Truncated to just that first observation: prev must resolve to None, so
    the takeaway is a bare statement, never a fabricated comparison."""
    from pipeline.build import _build_series_entry

    entry = dict(_UNEMP_ENTRY, id="test-urban-unemp-youth-exstudent")
    series = {
        "observations": [
            {"period": "2023-12", "m": 14.9, "flags": ["break_first"]},
        ],
        "revisions": [],
        "breaks": [
            {"effective": "2023-12", "kind": "methodology", "no_yoy_across": True, "yoy_valid_from": "2024-12"}
        ],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["prev"] is None
    assert bundle_entry["takeaway"] == "2023 年 12 月测试_全国城镇调查失业率为 14.9%"


# -- name_short passthrough (catalog schema addition) ----------------------------------


def test_name_short_is_used_in_the_takeaway_and_passed_through_to_the_bundle():
    """catalog.schema.json's optional `name_short` -- pipeline/takeaways.py
    renders it instead of name_zh when present, and build.py also surfaces it
    as its own bundle field (for the site to use in tiles) rather than only
    consuming it internally."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-cpi-short",
        "name_zh": "居民消费价格指数",
        "name_en": "TEST CPI",
        "name_short": "CPI",
        "unit_zh": "%",
        "unit_en": "%",
        "value_type": "index",
        "freq": "M",
        "tier": 1,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2026-04", "m": 100.3, "m_yoy": 0.3},
            {"period": "2026-05", "m": 101.2, "m_yoy": 1.2},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["name_short"] == "CPI"
    # value_type=="index" -> choose_verb() heuristic picks 上涨 (price-type wording);
    # name_short "CPI" is Latin, so a pangu space is required on both sides of it.
    assert bundle_entry["takeaway"] == "2026 年 5 月 CPI 同比上涨 1.2%，涨幅较上月扩大 0.9 个百分点"


def test_name_short_absent_falls_back_to_name_zh_and_bundle_field_is_none():
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-no-short",
        "name_zh": "测试_无简称序列",
        "name_en": "TEST no-short series",
        "unit_zh": "%",
        "unit_en": "%",
        "value_type": "level",
        "freq": "M",
        "tier": 2,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [{"period": "2026-05", "m": 10.0, "m_yoy": 5.0}],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["name_short"] is None
    assert "测试_无简称序列" in bundle_entry["takeaway"]


# -- level-only takeaway wiring (PMI: no published YoY, ever) --------------------------


def test_level_only_series_gets_a_level_takeaway_instead_of_null():
    """A series that NEVER carries m_yoy anywhere (PMI's real shape: only
    "m" is ever published) must fall back to the level-only template rather
    than leaving takeaway permanently null."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-pmi",
        "name_zh": "测试_制造业采购经理指数",
        "name_en": "TEST manufacturing PMI",
        "name_short": "测试 PMI",
        "unit_zh": "%",
        "unit_en": "%",
        "value_type": "index",
        "freq": "M",
        "tier": 1,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2026-05", "m": 48.0},
            {"period": "2026-06", "m": 48.6},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["headline"]["direction"] is None  # no YoY concept at all -- still "unknown", not fabricated
    assert bundle_entry["takeaway"] == "2026 年 6 月测试 PMI 为 48.6%，比上月上升 0.6 个点"


def test_level_only_series_bare_unit_when_catalog_unit_is_not_percent():
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-diffusion-points",
        "name_zh": "测试_扩散指数",
        "name_en": "TEST diffusion index",
        "unit_zh": "点",
        "unit_en": "points",
        "value_type": "index",
        "freq": "M",
        "tier": 2,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [{"period": "2026-05", "m": 48.0}, {"period": "2026-06", "m": 49.0}],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["takeaway"] == "2026 年 6 月测试_扩散指数为 49.0，比上月上升 1.0 个点"


def test_level_only_gate_requires_value_type_index_not_just_absent_m_yoy():
    """Regression, found against the real catalog: a currency-level series
    (nbs-gdp's real shape -- value_type=="level", carries `real_yoy` but
    never `m_yoy` for its "single" caliber) must NOT fall into the level-only
    template just because its OWN caliber's yoy_field happens to be absent.
    A "为 334192.9，比上月回落 53718.4 个点，位于荣枯线上方" sentence is nonsense
    for a GDP level -- the level-only template is for diffusion indices
    (value_type=="index") specifically, not any series lacking a same-
    caliber YoY. Because `real_yoy` IS present here, this also is not a
    "no YoY published at all" series -- it correctly falls through to
    takeaway=None, exactly as it did before generate_level_takeaway existed."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-gdp-level",
        "name_zh": "测试_国内生产总值",
        "name_en": "TEST GDP",
        "unit_zh": "亿元",
        "unit_en": "100M CNY",
        "value_type": "level",
        "freq": "Q",
        "tier": 1,
        "calibers": ["single", "ytd"],
        "derived": None,
    }
    series = {
        "observations": [
            {"period": "2025-Q4", "m": 387911.3, "real_yoy": 5.4},
            {"period": "2026-Q1", "m": 334192.9, "real_yoy": 5.4},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["headline"]["direction"] is None
    assert bundle_entry["takeaway"] is None


def test_level_only_gate_excludes_a_count_type_series():
    """A city-count series (nbs-70city-*-up-count's real shape --
    value_type=="count", never publishes m_yoy) must also stay
    takeaway=None, not get a "位于荣枯线" note attached to a city count."""
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-city-count",
        "name_zh": "测试_上涨城市数",
        "name_en": "TEST up-count",
        "unit_zh": "个",
        "unit_en": "cities",
        "value_type": "count",
        "freq": "M",
        "tier": 2,
        "calibers": ["single"],
        "derived": None,
    }
    series = {
        "observations": [{"period": "2026-05", "m": 23}, {"period": "2026-06", "m": 25}],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["takeaway"] is None


def test_break_blocked_yoy_capable_series_still_gets_null_takeaway_not_level_fallback():
    """Regression guard on _is_level_only_series: a series that DOES carry
    m_yoy elsewhere in its history (CPI-shaped) but whose latest period sits
    inside a no_yoy_across window must keep takeaway=None -- it must NOT fall
    through to the level-only template just because `y` is None *this*
    period. Reuses the real test-cpi-break fixture shape, truncated to the
    blocked window, matching test_headline_direction_is_none_not_flat_...'s
    setup."""
    from pipeline.build import _build_series_entry

    series = json.loads((FIXTURES_DIR / "series" / "test-cpi-break.json").read_text(encoding="utf-8"))
    catalog = json.loads((FIXTURES_DIR / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog["series"] if e["id"] == "test-cpi-break")
    truncated = dict(series, observations=[o for o in series["observations"] if o["period"] <= "2026-02"])

    bundle_entry = _build_series_entry(entry, truncated, {}, AS_OF)
    assert bundle_entry["takeaway"] is None


# -- source / decimals bundle fields (DATA-CONTRACT §10.2) -----------------------------


def test_bundle_source_and_decimals_from_catalog(built):
    _report, out_dir = built
    prices = _load(out_dir / "sections" / "prices.json")
    cpi = _series_by_id(prices, "test-cpi-break")
    # fixture catalog entries have no agency_zh -- degrades to "" rather than KeyError
    assert cpi["source"] == {"agency_zh": "", "url": "https://example.invalid/test-cpi-break"}
    assert cpi["decimals"] == 1  # catalog declares decimals:1 explicitly


def test_bundle_source_uses_agency_zh_and_omits_url_when_absent():
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-source",
        "name_zh": "测试_来源字段",
        "name_en": "TEST source field",
        "unit_zh": "%",
        "unit_en": "%",
        "value_type": "level",
        "freq": "M",
        "tier": 2,
        "calibers": ["single"],
        "derived": None,
        "source": {"agency": "nbs", "agency_zh": "国家统计局"},
    }
    series = {"observations": [{"period": "2026-05", "m": 1.0}], "revisions": [], "breaks": []}
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["source"] == {"agency_zh": "国家统计局"}


def test_bundle_decimals_inferred_when_catalog_omits_it():
    from pipeline.build import _build_series_entry

    entry = {
        "id": "test-decimals-inferred",
        "name_zh": "测试_精度推断",
        "name_en": "TEST inferred decimals",
        "unit_zh": "亿元",
        "unit_en": "100M CNY",
        "value_type": "level",
        "freq": "M",
        "tier": 2,
        "calibers": ["single"],
        "derived": None,
        # no "decimals" key -- must be inferred from the observations themselves
    }
    series = {
        "observations": [
            {"period": "2026-04", "m": 40940},
            {"period": "2026-05", "m": 41090.5},
        ],
        "revisions": [],
        "breaks": [],
    }
    bundle_entry = _build_series_entry(entry, series, {}, AS_OF)
    assert bundle_entry["decimals"] == 1  # max precision actually present (41090.5) is 1 decimal


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
    assert report.series == 7

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


# -- white-box: period shapes, labels, break windows, prev-resolution, paths ---------


@pytest.mark.parametrize(
    "period,expected",
    [
        ("2026-05", "monthly"),
        ("2026-Q2", "quarterly"),
        ("2026", "annual"),
    ],
)
def test_period_shape(period, expected):
    assert _period_shape(period) == expected


@pytest.mark.parametrize(
    "period,caliber,span,expected",
    [
        ("2026-05", "single", 1, "2026 年 5 月"),
        ("2026-02", "single", 2, "2026 年 1-2 月"),  # Jan-Feb: hyphen, per DATA-CONTRACT §12 (2026-07-08 unification)
        ("2026-05", "ytd", 1, "2026 年 1-5 月"),  # cumulative caliber headlined, even without span>1
        ("2026-Q2", "single", 1, "2026 年二季度"),  # conventional ordinal, per DATA-CONTRACT §12's own worked example
        ("2026", "single", 1, "2026 年全年"),  # bare "YYYY" -- shape-dispatched regardless of declared freq
    ],
)
def test_period_label_zh(period, caliber, span, expected):
    assert _period_label_zh(period, caliber=caliber, span=span) == expected


def test_prev_ytd_period_is_none_at_years_first_print():
    assert _prev_ytd_period("2026-02", "monthly") is None
    assert _prev_ytd_period("2026-03", "monthly") == "2026-02"
    assert _prev_ytd_period("2026-12", "monthly") == "2026-11"


def test_prev_ytd_period_quarterly():
    assert _prev_ytd_period("2026-Q1", "quarterly") is None
    assert _prev_ytd_period("2026-Q2", "quarterly") == "2026-Q1"
    assert _prev_ytd_period("2026-Q4", "quarterly") == "2026-Q3"


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
    prev = _resolve_prev(observations, index_by_period, latest, caliber="single", breaks=[])
    assert prev["period"] == "2025-02"  # NOT "2025-03" (which would be array-adjacent)


def test_resolve_prev_returns_none_across_a_break_wall():
    observations = [
        {"period": "2025-12", "m_yoy": 1.9},
        {"period": "2026-01", "m_yoy": 9.9},  # hypothetical stray value inside the blocked window
    ]
    index_by_period = {o["period"]: o for o in observations}
    breaks = [{"effective": "2026-01", "no_yoy_across": True}]
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="single", breaks=breaks)
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
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", breaks=[])
    assert prev is None  # NOT 2025-Q4 (which would be array-adjacent, but a different year)


def test_resolve_prev_quarterly_ytd_uses_same_year_prior_quarter():
    observations = [
        {"period": "2026-Q1", "ytd_yoy": 4.0},
        {"period": "2026-Q2", "ytd_yoy": 5.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", breaks=[])
    assert prev["period"] == "2026-Q1"


def test_resolve_prev_annual_ytd_falls_back_to_array_adjacency():
    """Annual data has no intra-year cumulative-reset concept -- ytd caliber
    whose latest period is annual-shaped must not attempt the monthly/
    quarterly calendar lookup (which would crash trying to parse "2026" as
    "YYYY-MM"); it falls through to (same-shape-guarded) array-adjacency."""
    observations = [
        {"period": "2025", "ytd_yoy": 4.5},
        {"period": "2026", "ytd_yoy": 5.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="ytd", breaks=[])
    assert prev["period"] == "2025"


def test_resolve_prev_never_bridges_a_period_shape_seam():
    """Core regression for pipeline/migrate/REPORT.md item 6: array-adjacency
    must refuse to treat an annual-supplement row as "the previous quarter"
    just because it happens to sit immediately before in the array."""
    observations = [
        {"period": "2016", "m_yoy": 6.7},
        {"period": "2017-Q1", "m_yoy": 6.0},
    ]
    index_by_period = {o["period"]: o for o in observations}
    prev = _resolve_prev(observations, index_by_period, observations[-1], caliber="single", breaks=[])
    assert prev is None


def test_resolve_prev_is_robust_to_an_unsorted_observations_array():
    """Regression, found while verifying the shape-dispatch fix against real
    data: 10 real income/consumption series physically place their bare-
    "YYYY" annual-supplement row AFTER same-year quarterly rows, e.g.
    nbs-income-disposable's actual on-disk order is "...,2016-Q1,2016-Q2,
    2016-Q3,2016,2017-Q1,...". DATA-CONTRACT §9 says observations[] should be
    ascending by period; this data isn't (out of this module's scope to fix),
    so array-adjacent lookups must not trust raw position -- the previous
    same-shape period must be found by sorting period strings, not by
    `observations[pos - 1]`."""
    observations = [
        {"period": "2016-Q1", "m_yoy": 8.7},
        {"period": "2016-Q2", "m_yoy": 8.7},
        {"period": "2016-Q3", "m_yoy": 8.4},
        {"period": "2016", "m_yoy": 8.4},  # out of order on disk: annual row after the quarters it covers
        {"period": "2017-Q1", "m_yoy": 8.5},
    ]
    index_by_period = {o["period"]: o for o in observations}
    latest = observations[-1]  # "2017-Q1"
    prev = _resolve_prev(observations, index_by_period, latest, caliber="single", breaks=[])
    assert prev["period"] == "2016-Q3"  # the true chronological predecessor, NOT "2016" (array-adjacent but wrong shape)


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


# -- transient read failures: pipeline/backfill/ writes data/series/ concurrently ----


def test_load_json_with_retry_succeeds_on_a_valid_file(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    value, error = _load_json_with_retry(path, delay_seconds=0)
    assert value == {"a": 1}
    assert error is None


def test_load_json_with_retry_gives_up_after_one_retry_on_a_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text('{"a": 1,  "b":', encoding="utf-8")  # truncated mid-write
    value, error = _load_json_with_retry(path, retries=1, delay_seconds=0)
    assert value is None
    assert "corrupt.json" in error


def test_build_skips_an_unparseable_series_file_with_a_loud_warning(tmp_path, capsys):
    """Simulates pipeline/backfill/ catching a series file mid-write: build
    must skip just that series (with a warning naming it), not crash, and
    every other series must still build normally."""
    data_copy = tmp_path / "data"
    shutil.copytree(FIXTURES_DIR, data_copy)
    (data_copy / "series" / "test-fai-ytd.json").write_text('{"observations": [', encoding="utf-8")  # truncated

    report = build_site_data(data_copy, tmp_path / "out", as_of=AS_OF)

    assert report.skipped == ["test-fai-ytd"]
    assert report.series == 6  # everything except fai-ytd (skipped)

    stderr = capsys.readouterr().err
    assert "test-fai-ytd" in stderr
    assert "WARNING" in stderr

    macro = _load(tmp_path / "out" / "sections" / "macro.json")
    assert all(s["id"] != "test-fai-ytd" for s in macro["series"])
    assert any(s["id"] == "test-income-mixed-annual" for s in macro["series"])  # unaffected sibling still built

    # other sections are completely unaffected
    prices = _load(tmp_path / "out" / "sections" / "prices.json")
    assert any(s["id"] == "test-cpi-break" for s in prices["series"])
