"""Contract test for pipeline/parsers/nbs_retail.py against the real committed
fixture. Expected numbers below were read directly off
pipeline/fixtures/raw/nbs_activity/2026-05_retail.html's "2026年5月份社会消费品
零售总额主要数据" table (5月 绝对量/同比增长, 1—5月 绝对量/同比增长) plus, for the
two online-only rows, the article's prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import ParseError
from pipeline.parsers import nbs_retail

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_activity" / "2026-05_retail.html"

# label -> (month_value, month_yoy, ytd_value, ytd_yoy); None where the fixture's
# table has no figure at all (a literal "-" cell, not a parse gap).
EXPECTED_TABLE = {
    "社会消费品零售总额": (41090, -0.6, 206031, 1.4),
    "除汽车以外的消费品零售额": (37781, 1.1, 190022, 2.7),
    "限额以上单位消费品零售额": (15609, -4.9, 77941, -0.5),
    "网上商品零售额": (None, None, 52718, 5.0),
    "城镇": (35741, -0.9, 178662, 1.2),
    "乡村": (5349, 1.5, 27369, 2.6),
    "餐饮收入": (4605, 0.6, 23488, 3.1),
    "限额以上单位餐饮收入": (1396, -1.7, 6875, 2.1),
    "商品零售额": (36485, -0.7, 182543, 1.2),
    "限额以上单位商品零售额": (14213, -5.2, 71066, -0.8),
    "粮油、食品类": (1853, 1.9, 10049, 7.4),
    "饮料类": (269, 6.1, 1315, 6.0),
    "烟酒类": (484, 4.8, 3016, 13.4),
    "服装、鞋帽、针纺织品类": (1251, 3.8, 6425, 7.2),
    "化妆品类": (449, 2.5, 1985, 4.9),
    "金银珠宝类": (267, -8.9, 1678, 2.8),
    "日用品类": (726, 1.6, 3543, 4.6),
    "体育、娱乐用品类": (157, -8.0, 697, -2.4),
    "家用电器和音像器材类": (975, -15.6, 4197, -6.9),
    "中西药品类": (596, 4.0, 2997, 3.1),
    "文化办公用品类": (390, -1.5, 1866, 3.8),
    "家具类": (150, -8.7, 696, -3.0),
    "通讯器材类": (889, 0.7, 4379, 13.8),
    "石油及制品类": (1793, -3.2, 8961, -5.8),
    "汽车类": (3309, -16.1, 16009, -11.8),
    "建筑及装潢材料类": (100, -13.6, 500, -8.4),
}

# Prose-only rows (not in the table at all): label -> (ytd_value, ytd_yoy).
EXPECTED_PROSE = {
    "网上商品和服务零售额": (83177, 5.9),
    "网上服务零售额": (30459, 7.6),
}


@pytest.fixture(scope="module")
def parsed():
    html_text = FIXTURE.read_text(encoding="utf-8")
    return nbs_retail.parse(html_text, url="https://example.invalid/retail", release_id="test-retail-202605")


def test_period_and_metadata(parsed):
    assert parsed.source == "nbs-retail"
    assert parsed.period_hint == "2026-05"
    assert parsed.published_at == "2026/06/16 10:00"


@pytest.mark.parametrize("label", sorted(EXPECTED_TABLE))
def test_exact_table_values(parsed, label):
    by_measure = {row.caliber_hint: row.value for row in parsed.rows if row.source_field == label}
    expected_m, expected_m_yoy, expected_ytd, expected_ytd_yoy = EXPECTED_TABLE[label]

    if expected_m is None:
        assert "m" not in by_measure, f"{label} should have no month-level value this release"
    else:
        assert by_measure.get("m") == pytest.approx(expected_m), f"{label} m"
    if expected_m_yoy is None:
        assert "m_yoy" not in by_measure, f"{label} should have no month YoY this release"
    else:
        assert by_measure.get("m_yoy") == pytest.approx(expected_m_yoy), f"{label} m_yoy"

    assert by_measure.get("ytd") == pytest.approx(expected_ytd), f"{label} ytd"
    assert by_measure.get("ytd_yoy") == pytest.approx(expected_ytd_yoy), f"{label} ytd_yoy"


@pytest.mark.parametrize("label", sorted(EXPECTED_PROSE))
def test_exact_prose_values(parsed, label):
    by_measure = {row.caliber_hint: row.value for row in parsed.rows if row.source_field == label}
    expected_ytd, expected_ytd_yoy = EXPECTED_PROSE[label]
    assert by_measure.get("ytd") == pytest.approx(expected_ytd), f"{label} ytd"
    assert by_measure.get("ytd_yoy") == pytest.approx(expected_ytd_yoy), f"{label} ytd_yoy"


def test_online_goods_2026_indicator_change_has_no_month_value(parsed):
    """DATA-CONTRACT §2.1 flags a 2026 online-indicator replacement as a break;
    this fixture's own footnote confirms it ("网上商品和服务零售额与网上零售额数据
    不可比"). Concretely that shows up here as: 网上商品零售额 publishes a YTD
    figure only, no month-level column at all (literal "-" cells, not a gap)."""
    online_goods_rows = [row for row in parsed.rows if row.source_field == "网上商品零售额"]
    assert {row.caliber_hint for row in online_goods_rows} == {"ytd", "ytd_yoy"}


def test_missing_table_raises_parse_error():
    broken_html = (
        "<html><head><title>2026年1—5月份社会消费品零售总额增长1.4%</title></head>"
        "<body>no table here</body></html>"
    )
    with pytest.raises(ParseError):
        nbs_retail.parse(broken_html)


def test_bad_title_raises_parse_error():
    broken_html = "<html><head><title>not a retail release</title></head><body></body></html>"
    with pytest.raises(ParseError):
        nbs_retail.parse(broken_html)


def test_missing_required_row_raises_parse_error():
    incomplete_html = """
    <html><head><title>2026年1—5月份社会消费品零售总额增长1.4%</title>
    <meta name="PubDate" content="2026/06/16 10:00"></head>
    <body><table>
      <tr><td>指标</td><td>5月</td><td></td><td>1—5月</td><td></td></tr>
      <tr><td></td><td>绝对量（亿元）</td><td>同比增长（%）</td><td>绝对量（亿元）</td><td>同比增长（%）</td></tr>
      <tr><td>社会消费品零售总额</td><td>41090</td><td>-0.6</td><td>206031</td><td>1.4</td></tr>
    </table></body></html>
    """
    with pytest.raises(ParseError):
        nbs_retail.parse(incomplete_html)
