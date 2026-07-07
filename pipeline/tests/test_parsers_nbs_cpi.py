"""Contract test for pipeline/parsers/nbs_cpi.py against the real committed
fixture. Expected numbers below were read directly off
pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html's "2026年5月份居民消费价格主要数据"
table (环比涨跌幅% / 同比涨跌幅% / 1—5月同比涨跌幅%) -- see that file for the
underlying HTML if these ever need re-verifying against the source.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import ParseError
from pipeline.parsers import nbs_cpi

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html"

# label -> (mom%, yoy%, ytd_yoy% i.e. "1—5月同比"), read off the fixture's table.
EXPECTED = {
    "居民消费价格": (-0.1, 1.2, 1.0),
    "城市": (-0.1, 1.3, 1.0),
    "农村": (-0.1, 1.1, 0.8),
    "食品": (-0.4, -1.7, -0.4),
    "非食品": (-0.1, 1.9, 1.3),
    "消费品": (-0.2, 1.6, 1.1),
    "服务": (-0.1, 0.8, 0.8),
    "不包括食品和能源": (-0.1, 1.1, 1.2),
    "食品烟酒及在外餐饮": (-0.2, -0.9, 0.0),
    "衣着": (0.6, 1.4, 1.7),
    "居住": (-0.1, -0.2, -0.2),
    "生活用品及服务": (-0.4, 1.8, 2.0),
    "交通通信": (-0.3, 5.4, 1.3),
    "教育文化娱乐": (0.0, 1.3, 1.1),
    "医疗保健": (0.0, 2.1, 2.0),
    "其他用品及服务": (-0.4, 9.9, 12.6),
}


@pytest.fixture(scope="module")
def parsed():
    html_text = FIXTURE.read_text(encoding="utf-8")
    return nbs_cpi.parse(html_text, url="https://example.invalid/cpi", release_id="test-cpi-202605")


def test_period_and_metadata(parsed):
    assert parsed.source == "nbs-cpi"
    assert parsed.period_hint == "2026-05"
    assert parsed.published_at == "2026/06/10 09:30"
    assert parsed.release_id == "test-cpi-202605"


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_exact_values(parsed, label):
    by_measure = {row.caliber_hint: row.value for row in parsed.rows if row.source_field == label}
    expected_mom, expected_yoy, expected_ytd_yoy = EXPECTED[label]
    assert by_measure.get("mom") == pytest.approx(expected_mom), f"{label} mom"
    assert by_measure.get("m_yoy") == pytest.approx(expected_yoy), f"{label} m_yoy"
    assert by_measure.get("ytd_yoy") == pytest.approx(expected_ytd_yoy), f"{label} ytd_yoy"


def test_no_index_level_is_fabricated(parsed):
    """This fixture's HTML table carries only percent changes, never an absolute
    CPI index level -- the parser must not invent an "m" measure from nothing."""
    assert all(row.caliber_hint != "m" for row in parsed.rows)


def test_sub_items_are_out_of_scope(parsed):
    """Sub-items like 猪肉/牛肉/家用器具 are deliberately not extracted (task asks
    for headline + the 8 top-level categories, not the ~30-row sub-item detail)."""
    labels = {row.source_field for row in parsed.rows}
    assert "猪肉" not in labels
    assert "家用器具" not in labels


def test_missing_table_raises_parse_error():
    broken_html = "<html><head><title>2026年5月份居民消费价格同比上涨1.2%</title></head><body>no table here</body></html>"
    with pytest.raises(ParseError):
        nbs_cpi.parse(broken_html)


def test_bad_title_raises_parse_error():
    broken_html = "<html><head><title>not a CPI release</title></head><body></body></html>"
    with pytest.raises(ParseError):
        nbs_cpi.parse(broken_html)


def test_missing_category_row_raises_parse_error():
    """A table present but missing one of the required category rows must fail
    loudly, not silently return a thinner ParsedRelease."""
    incomplete_html = """
    <html><head><title>2026年5月份居民消费价格同比上涨1.2%</title>
    <meta name="PubDate" content="2026/06/10 09:30"></head>
    <body><table>
      <tr><td></td><td>环比涨跌幅（%）</td><td>同比涨跌幅（%）</td><td>1—5月同比涨跌幅（%）</td></tr>
      <tr><td>居民消费价格</td><td>-0.1</td><td>1.2</td><td>1.0</td></tr>
    </table></body></html>
    """
    with pytest.raises(ParseError):
        nbs_cpi.parse(incomplete_html)
