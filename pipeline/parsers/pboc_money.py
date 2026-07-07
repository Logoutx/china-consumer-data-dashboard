"""Parser for the PBoC monthly 金融统计数据报告 (docs/ACQUISITION.md Group 6,
"parser sketch B" -- everything is in running prose, no tables at all).

Fixture: pipeline/fixtures/raw/pboc_money/2026-05_finstats.html

Deviation from ACQUISITION.md worth flagging: the sketch assumed three *separate*
PBoC articles (金融统计数据报告 for M1/M2/loans/deposits, a 社会融资规模增量 report
for the TSF flow, and a 社会融资规模存量 report for the TSF stock). This fixture's
single 金融统计数据报告 page in fact contains TSF stock ("一、社会融资规模存量同比
增长7.7%") *and* TSF flow ("二、前五个月社会融资规模增量累计为17.48万亿元") *and*
M2/M1/M0 *and* deposits *and* loans, all in one article -- so one parser call
covers everything the task asks for from a single fetch, which is simpler than the
three-article sketch (kept in case a future month reverts to separate reports; this
parser only depends on the phrases actually being present in whatever page it's
given, not on it being exactly one specific article).

Two traps this parser guards explicitly (both actually present in this fixture, not
hypothetical):

  1. Half-width vs full-width punctuation *in the same document*: "广义货币(M2)余额
     353.67万亿元,同比增长8.6%" uses ASCII "(" ")" "," while two paragraphs earlier
     "社会融资规模存量为458.81万亿元，同比增长7.7%" uses full-width "，". Every
     regex below accepts both.
  2. Stock vs flow phrases sharing a substring: "对实体经济发放的人民币贷款余额
     277.4万亿元" (part of the TSF breakdown) and "月末人民币贷款余额281.02万亿元"
     (the all-loans aggregate) are DIFFERENT numbers this same month, and a loose
     `人民币贷款余额` regex would grab whichever comes first in document order --
     silently picking the wrong one about half the time. Every stock/flow pattern
     below is bound to its full distinguishing prefix (对实体经济发放的... vs
     月末...; 前N个月... vs 对实体经济发放的...), never a bare `人民币贷款(余额|增加)`.
"""
from __future__ import annotations

import re

from lxml import html

from pipeline import ParseError, ParsedRelease, ParsedRow
from pipeline.parsers._util import first_matching_node, node_text_keep_spaces, sign_from_word, to_number, yi_yuan

SOURCE = "pbc-money"

_TITLE_PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月.*金融统计数据报告")
_PUBDATE_XPATH = "//meta[@name='PubDate']/@content"
_BODY_XPATHS = [
    '//div[contains(@class,"detail-text-content")]',
    '//div[@id="zoom"]',
    '//div[contains(@class,"TRS_Editor")]',
]

_COMMA = r"[，,]"
_LPAREN = r"[（(]"
_RPAREN = r"[）)]"
_MONTH_COUNT = r"(?:[一二三四五六七八九十百]{1,3}|\d{1,2})"


def _stock_re(label: str, *, unit: str = r"万亿") -> re.Pattern:
    return re.compile(rf"{label}([\d.]+){unit}元{_COMMA}\s*同比(增长|下降)([\d.]+)%")


_M2_RE = _stock_re(rf"广义货币{_LPAREN}M2{_RPAREN}余额")
_M1_RE = _stock_re(rf"狭义货币{_LPAREN}M1{_RPAREN}余额")
_M0_RE = _stock_re(rf"流通中货币{_LPAREN}M0{_RPAREN}余额")
_TSF_STOCK_RE = _stock_re(r"社会融资规模存量为")
_REAL_ECON_LOAN_STOCK_RE = _stock_re(r"对实体经济发放的人民币贷款余额")
_LOAN_STOCK_RE = _stock_re(r"月末人民币贷款余额")
_DEPOSIT_STOCK_RE = _stock_re(r"月末人民币存款余额")
_TOTAL_DEPOSIT_STOCK_RE = _stock_re(r"本外币存款余额")
_TOTAL_LOAN_STOCK_RE = _stock_re(r"本外币贷款余额")

_TSF_FLOW_RE = re.compile(rf"社会融资规模增量累计为([\d.]+)万亿元")
_REAL_ECON_LOAN_FLOW_RE = re.compile(r"对实体经济发放的人民币贷款增加([\d.]+)万亿元")
_LOAN_FLOW_RE = re.compile(rf"前{_MONTH_COUNT}个月人民币贷款增加([\d.]+)万亿元")
_DEPOSIT_FLOW_RE = re.compile(rf"前{_MONTH_COUNT}个月人民币存款增加([\d.]+)万亿元")

_HOUSEHOLD_DEPOSITS_RE = re.compile(r"住户存款(增加|减少)([\d.]+)(万?)亿元")
_HOUSEHOLD_LOANS_RE = re.compile(r"住户贷款(增加|减少)([\d.]+)(万?)亿元")

# (source_field, regex, required) for the simple {level, yoy%} stock patterns.
_STOCK_SPECS = [
    ("M2", _M2_RE, True),
    ("M1", _M1_RE, True),
    ("M0", _M0_RE, False),
    ("社会融资规模存量", _TSF_STOCK_RE, True),
    ("对实体经济发放的人民币贷款余额", _REAL_ECON_LOAN_STOCK_RE, False),
    ("人民币贷款余额", _LOAN_STOCK_RE, True),
    ("人民币存款余额", _DEPOSIT_STOCK_RE, True),
    ("本外币存款余额", _TOTAL_DEPOSIT_STOCK_RE, False),
    ("本外币贷款余额", _TOTAL_LOAN_STOCK_RE, False),
]

# (source_field, regex) for {level only, no %} cumulative-flow patterns.
_FLOW_SPECS = [
    ("社会融资规模增量", _TSF_FLOW_RE),
    ("对实体经济发放的人民币贷款增加", _REAL_ECON_LOAN_FLOW_RE),
    ("人民币贷款增加", _LOAN_FLOW_RE),
    ("人民币存款增加", _DEPOSIT_FLOW_RE),
]


def parse(html_text: str, *, url: str = "", release_id: str = "") -> ParsedRelease:
    doc = html.fromstring(html_text)

    title_nodes = doc.xpath("//title/text()")
    title = title_nodes[0] if title_nodes else ""
    period_match = _TITLE_PERIOD_RE.search(title)
    if not period_match:
        raise ParseError(
            "PBoC release title did not match the expected period pattern",
            expected=r"YYYY年M月...金融统计数据报告",
            found=title,
        )
    period_hint = f"{int(period_match.group(1)):04d}-{int(period_match.group(2)):02d}"

    pubdate_nodes = doc.xpath(_PUBDATE_XPATH)
    published_at = pubdate_nodes[0] if pubdate_nodes else None

    body = first_matching_node(doc, _BODY_XPATHS)
    if body is None:
        raise ParseError(
            "PBoC article body not found",
            expected="one of detail-text-content / #zoom / TRS_Editor wrapper",
            found="no matching body container",
        )
    text = node_text_keep_spaces(body)

    parsed_rows: list[ParsedRow] = []
    found: set[str] = set()
    missing_required: list[str] = []

    for source_field, pattern, required in _STOCK_SPECS:
        match = pattern.search(text)
        if not match:
            if required:
                missing_required.append(source_field)
            continue
        amount, sign_word, yoy = match.groups()
        level = yi_yuan(to_number(amount), "万亿")
        yoy_signed = sign_from_word(sign_word) * to_number(yoy)
        found.add(source_field)
        parsed_rows.append(
            ParsedRow(source_field=source_field, raw_label=match.group(0), value=level, unit_raw="亿元", caliber_hint="m", period=period_hint)
        )
        parsed_rows.append(
            ParsedRow(source_field=source_field, raw_label=match.group(0), value=yoy_signed, unit_raw="%", caliber_hint="m_yoy", period=period_hint)
        )

    for source_field, pattern in _FLOW_SPECS:
        match = pattern.search(text)
        if not match:
            continue
        found.add(source_field)
        parsed_rows.append(
            ParsedRow(
                source_field=source_field,
                raw_label=match.group(0),
                value=yi_yuan(to_number(match.group(1)), "万亿"),
                unit_raw="亿元",
                caliber_hint="ytd",
                period=period_hint,
            )
        )

    household_deposits = _HOUSEHOLD_DEPOSITS_RE.search(text)
    if household_deposits:
        sign_word, amount, wan = household_deposits.groups()
        value = sign_from_word(sign_word) * yi_yuan(to_number(amount), "万亿" if wan else "亿")
        found.add("住户存款增加")
        parsed_rows.append(
            ParsedRow(source_field="住户存款增加", raw_label=household_deposits.group(0), value=value, unit_raw="亿元", caliber_hint="ytd", period=period_hint)
        )

    household_loans = _HOUSEHOLD_LOANS_RE.search(text)
    if household_loans:
        sign_word, amount, wan = household_loans.groups()
        value = sign_from_word(sign_word) * yi_yuan(to_number(amount), "万亿" if wan else "亿")
        found.add("住户贷款增加")
        parsed_rows.append(
            ParsedRow(source_field="住户贷款增加", raw_label=household_loans.group(0), value=value, unit_raw="亿元", caliber_hint="ytd", period=period_hint)
        )

    if missing_required:
        raise ParseError(
            "PBoC report is missing required stock figures -- format may have drifted",
            expected=[spec[0] for spec in _STOCK_SPECS if spec[2]],
            found=f"missing={missing_required}, present={sorted(found)}",
        )

    return ParsedRelease(
        source=SOURCE,
        release_id=release_id,
        url=url,
        published_at=published_at,
        period_hint=period_hint,
        rows=parsed_rows,
    )
