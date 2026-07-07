"""Contract test for pipeline/parsers/pboc_money.py against the real committed
fixture. Expected numbers below were read directly off the prose of
pipeline/fixtures/raw/pboc_money/2026-05_finstats.html ("2026年5月金融统计数据
报告"), converting 万亿元 -> 亿元 (x10000) to match the series-file unit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import ParseError
from pipeline.parsers import pboc_money

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "pboc_money" / "2026-05_finstats.html"

# label -> (level in 亿元, published YoY %)
EXPECTED_STOCK = {
    "M2": (3536700.0, 8.6),
    "M1": (1148900.0, 5.5),
    "M0": (146900.0, 11.9),
    "社会融资规模存量": (4588100.0, 7.7),
    "对实体经济发放的人民币贷款余额": (2774000.0, 5.5),
    "人民币贷款余额": (2810200.0, 5.5),
    "人民币存款余额": (3444500.0, 8.7),
    "本外币存款余额": (3523800.0, 8.7),
    "本外币贷款余额": (2847900.0, 5.4),
}

# label -> signed level in 亿元 (no published YoY% for any of these -- flows are
# quoted as a raw YTD delta only, never as a percentage, in this fixture).
EXPECTED_FLOW = {
    "社会融资规模增量": 174800.0,
    "对实体经济发放的人民币贷款增加": 90000.0,
    "人民币贷款增加": 91100.0,
    "人民币存款增加": 157700.0,
    "住户存款增加": 56300.0,
    "住户贷款增加": -6314.0,  # "住户贷款减少6314亿元" -- decrease, and unit is 亿, not 万亿
}


@pytest.fixture(scope="module")
def parsed():
    html_text = FIXTURE.read_text(encoding="utf-8")
    return pboc_money.parse(html_text, url="https://example.invalid/pboc", release_id="test-pboc-202605")


def test_period_and_metadata(parsed):
    assert parsed.source == "pbc-money"
    assert parsed.period_hint == "2026-05"
    assert parsed.published_at == "2026-06-12"


@pytest.mark.parametrize("label", sorted(EXPECTED_STOCK))
def test_exact_stock_values(parsed, label):
    by_measure = {row.caliber_hint: row.value for row in parsed.rows if row.source_field == label}
    expected_level, expected_yoy = EXPECTED_STOCK[label]
    assert by_measure.get("m") == pytest.approx(expected_level), f"{label} level"
    assert by_measure.get("m_yoy") == pytest.approx(expected_yoy), f"{label} yoy"


@pytest.mark.parametrize("label", sorted(EXPECTED_FLOW))
def test_exact_flow_values(parsed, label):
    by_measure = {row.caliber_hint: row.value for row in parsed.rows if row.source_field == label}
    assert by_measure.get("ytd") == pytest.approx(EXPECTED_FLOW[label]), f"{label} ytd flow"
    assert "ytd_yoy" not in by_measure, f"{label} has no published YoY% in this fixture"


def test_real_economy_loans_are_distinct_from_aggregate_loans(parsed):
    """Regression guard for ACQUISITION.md's stock/flow overlapping-phrase trap:
    '对实体经济发放的人民币贷款余额277.4万亿元' (part of the TSF breakdown) and
    '月末人民币贷款余额281.02万亿元' (the all-loans aggregate) are DIFFERENT
    numbers this month that a loose regex could easily conflate."""
    by_field = {}
    for row in parsed.rows:
        by_field.setdefault(row.source_field, {})[row.caliber_hint] = row.value
    assert by_field["对实体经济发放的人民币贷款余额"]["m"] == pytest.approx(2774000.0)
    assert by_field["人民币贷款余额"]["m"] == pytest.approx(2810200.0)
    assert by_field["对实体经济发放的人民币贷款余额"]["m"] != by_field["人民币贷款余额"]["m"]

    assert by_field["对实体经济发放的人民币贷款增加"]["ytd"] == pytest.approx(90000.0)
    assert by_field["人民币贷款增加"]["ytd"] == pytest.approx(91100.0)
    assert by_field["对实体经济发放的人民币贷款增加"]["ytd"] != by_field["人民币贷款增加"]["ytd"]


def test_half_and_full_width_punctuation_both_parse(parsed):
    """This fixture genuinely mixes half-width '(M2),' and full-width '，' in the
    same document -- both M2 (half-width) and TSF stock (full-width) must parse."""
    by_field = {}
    for row in parsed.rows:
        by_field.setdefault(row.source_field, {})[row.caliber_hint] = row.value
    assert by_field["M2"]["m_yoy"] == pytest.approx(8.6)
    assert by_field["社会融资规模存量"]["m_yoy"] == pytest.approx(7.7)


def test_missing_required_figures_raises_parse_error():
    incomplete_html = """
    <html><head><title>2026年5月金融统计数据报告</title>
    <meta name="PubDate" content="2026-06-12"></head>
    <body><div id="zoom"><p>本月没有可解析的金融数据。</p></div></body></html>
    """
    with pytest.raises(ParseError):
        pboc_money.parse(incomplete_html)


def test_bad_title_raises_parse_error():
    broken_html = "<html><head><title>not a PBoC release</title></head><body></body></html>"
    with pytest.raises(ParseError):
        pboc_money.parse(broken_html)
