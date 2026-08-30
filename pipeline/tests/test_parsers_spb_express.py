"""Tests for pipeline/parsers/spb_express.py against two committed real
bulletins (pipeline/fixtures/raw/spb_express/):

  - 2026-07: the modern shape -- title "2026年1-7月", explicit 累计完成
    cumulative sentences plus 7月份 single-month sentences.
  - 2024-02: the tricky shape that broke the first regex draft -- a combined
    Jan-Feb bulletin titled "2024年2月" whose CUMULATIVE sentence has NO 累计
    verb ("1-2月，……其中,快递业务量完成232.6亿件（注1）……"), parentheticals on
    both sides of the numbers, and a separate true 2月份 single-month section.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import ParseError
from pipeline.parsers import spb_express

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "raw" / "spb_express"


def _rows_by(parsed, source_field):
    return {row.caliber_hint: row for row in parsed.rows if row.source_field == source_field}


def test_modern_bulletin_extracts_all_eight_measures():
    parsed = spb_express.parse((FIXTURES / "2026-07_邮政行业运行情况.html").read_text(), url="u", release_id="r")
    assert parsed.period_hint == "2026-07"
    assert parsed.source == "spb-express"
    vol = _rows_by(parsed, "快递业务量")
    rev = _rows_by(parsed, "快递业务收入")
    assert {k: v.value for k, v in vol.items()} == {"ytd": 1174.7, "ytd_yoy": 4.8, "m": 170.8, "m_yoy": 4.1}
    assert {k: v.value for k, v in rev.items()} == {"ytd": 9017.7, "ytd_yoy": 7.4, "m": 1303.7, "m_yoy": 8.1}
    assert vol["ytd"].unit_raw == "亿件" and rev["ytd"].unit_raw == "亿元"


def test_combined_feb_bulletin_splits_cumulative_from_monthly_by_marker():
    """The 1-2月 cumulative sentence (no 累计 verb) must land as ytd, the
    2月份 sentence as m -- the first regex draft read 232.6 as the monthly
    value and Gate A caught the resulting impossible YoY base."""
    parsed = spb_express.parse((FIXTURES / "2024-02_邮政行业运行情况.html").read_text(), url="u", release_id="r")
    assert parsed.period_hint == "2024-02"
    vol = _rows_by(parsed, "快递业务量")
    assert vol["ytd"].value == 232.6 and vol["ytd_yoy"].value == 28.5
    assert vol["m"].value == 85.6 and vol["m_yoy"].value == -15.6
    # Title says 2月 (a real single-month print exists) -- NOT a jan_feb merge.
    assert vol["m"].span == 1 and vol["m"].flags == []


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("国家邮政局公布2026年1-7月邮政行业运行情况", "2026-07"),
        ("国家邮政局公布2026年上半年邮政行业运行情况", "2026-06"),
        ("国家邮政局公布2025年一季度邮政行业运行情况", "2025-03"),
        ("国家邮政局关于2018年前三季度邮政行业经济运行情况的通报", "2018-09"),
        ("国家邮政局公布2025年8月份邮政行业运行情况", "2025-08"),
        ("国家邮政局公布2025年5月邮政行业运行情况", "2025-05"),
        ("国家邮政局公布2025年邮政行业运行情况", "2025-12"),
    ],
)
def test_title_period_grammar(title, expected):
    assert spb_express.period_from_title(title)[0] == expected


def test_jan_feb_title_mirrors_ytd_into_m_with_flags():
    html_text = """
    <html><head><title>国家邮政局公布2026年1-2月邮政行业运行情况</title>
    <meta name="PubDate" content="2026/03/20 10:00"></head>
    <body><div class="article-content">
    1-2月，邮政行业寄递业务量累计完成300.0亿件，同比增长10.0%。其中,快递业务量累计完成280.0亿件，同比增长12.0%。
    1-2月，邮政行业业务收入累计完成2000.0亿元，同比增长8.0%。其中，快递业务收入累计完成1600.0亿元，同比增长9.0%。
    </div></body></html>
    """
    parsed = spb_express.parse(html_text)
    assert parsed.period_hint == "2026-02"
    vol = _rows_by(parsed, "快递业务量")
    assert vol["ytd"].value == 280.0 and vol["m"].value == 280.0
    assert vol["m"].span == 2 and vol["m"].flags == ["jan_feb"]
    assert vol["m_yoy"].value == 12.0


def test_city_and_regional_splits_are_never_emitted():
    parsed = spb_express.parse((FIXTURES / "2024-02_邮政行业运行情况.html").read_text(), url="u", release_id="r")
    assert {row.source_field for row in parsed.rows} == {"快递业务量", "快递业务收入"}


def test_bad_title_raises_parse_error():
    with pytest.raises(ParseError):
        spb_express.parse("<html><head><title>not a bulletin</title></head><body></body></html>")


def test_missing_volume_raises_parse_error():
    html_text = (
        "<html><head><title>国家邮政局公布2026年1-7月邮政行业运行情况</title></head>"
        "<body><div class='article-content'>没有任何数字。</div></body></html>"
    )
    with pytest.raises(ParseError):
        spb_express.parse(html_text)
