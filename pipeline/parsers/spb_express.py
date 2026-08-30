"""Parser for 国家邮政局 monthly 邮政行业运行情况 bulletins, fetched from the
交通运输部 mirror (https://www.mot.gov.cn/shuju/tongjishuju/youzheng/ -- the
SPB's own site 403s non-mainland clients, the MOT mirror carries the same
bulletins and serves plain HTTP; see docs/SOURCE-CANDIDATES.md).

Unlike the NBS releases, these bulletins carry no data table -- the numbers
live in prose, in a stable sentence grammar (verified against 2023-2026
bulletins):

    1-7月，……其中，快递业务收入累计完成9017.7亿元，同比增长7.4%。
    1-7月，……其中,快递业务量累计完成1174.7亿件，同比增长4.8%。
    7月份，……其中，快递业务收入完成1303.7亿元，同比增长8.1%。
    7月份，……其中,快递业务量完成170.8亿件，同比增长4.1%。

so this parser regex-scans the whitespace-compacted article text rather than
walking table rows. Only the two headline express series are extracted (快递
业务量 / 快递业务收入); the 同城/异地/国际 splits and the 邮政行业 totals in
the same prose have no catalog series and are deliberately not emitted (so
they never even reach the unmapped-fields report -- the anchored 其中 pattern
below can't match them).

Title period grammar (the part after 国家邮政局公布, mirror suffix stripped
by matching, not by string surgery):

    2026年1-7月   -> 2026-07 (cumulative phrasing; 1-2月 -> jan_feb span-2)
    2026年上半年   -> 2026-06
    2025年一季度   -> 2025-03
    2018年前三季度 -> 2018-09
    2025年8月[份]  -> 2025-08 (single-month phrasing; body still carries both
                     the 1-8月 cumulative and the 8月份 monthly sentences)
    2025年        -> 2025-12 (the full-year bulletin)
"""
from __future__ import annotations

import re

from lxml import html

from pipeline import ParseError, ParsedRelease, ParsedRow
from pipeline.parsers._util import first_matching_node, node_text, sign_from_word

SOURCE = "spb-express"

_TITLE_PERIOD_RE = re.compile(
    r"(\d{4})年(?:1[—\-－~至](\d{1,2})月|(上半年)|(一季度)|(前三季度)|(\d{1,2})月(?:份)?)?邮政行业(?:经济)?运行情况"
)

_PUBDATE_XPATH = "//meta[@name='PubDate']/@content"

_BODY_XPATHS = [
    "//div[contains(@class,'article-content')]",
    "//div[contains(@class,'TRS_UEDITOR')]",
    "//div[@id='zoom']",
    "//body",
]

# The 其中 anchor is what keeps the 同城/异地/国际快递业务量 sentences (and
# the 邮政行业 totals) from matching. Whether a match is the cumulative or the
# single-month figure is NOT decided by a 累计 verb -- the combined Jan-Feb
# bulletins write the cumulative WITHOUT it ("1-2月，……其中,快递业务量完成
# 232.6亿件……2月份，……其中,快递业务量完成85.6亿件", real text of the 2024-02
# bulletin) -- but by the period marker that opens each sentence group
# ("1-N月，"/"上半年，"/… = cumulative; "N月份，" = single month), resolved per
# match by _caliber_at() below. 累计 still counts as an unambiguous cumulative
# signal on its own (the 2026 bulletins pair it with the 1-N月 marker).
_NUM = r"([\d,.]+)"
# Parentheticals appear on either side of the number in some years -- e.g.
# 2024-01's "快递业务量完成147.0亿件（注1），同比增长84.8%（按可比口径计算，
# 下同）" -- hence the optional （…） groups around both joints.
_PAREN = r"(?:（[^）]*）)?"


def _label_re(label: str, unit: str) -> re.Pattern:
    return re.compile(
        rf"其中[，,、]?{label}{_PAREN}(累计)?完成{_NUM}{unit}{_PAREN}，同比(增长|下降){_NUM}%"
    )


_SPECS = [
    ("快递业务量", "亿件", _label_re("快递业务量", "亿件")),
    ("快递业务收入", "亿元", _label_re("快递业务收入", "亿元")),
]

# Nearest one of these BEFORE a match decides its caliber. 年， catches the
# full-year bulletins' "2023年，……" cumulative paragraphs.
_MARKER_RE = re.compile(r"(\d{1,2}月份，)|(1[—\-－~至]\d{1,2}月，|上半年，|一季度，|前三季度，|\d{4}年，)")


def _caliber_at(text: str, pos: int, has_leiji: bool) -> str | None:
    """'ytd' | 'm' | None for a match starting at `pos`. An explicit 累计 verb
    is an unambiguous cumulative signal on its own; otherwise the nearest
    preceding period marker decides; no signal at all -> None (skip the match
    rather than guess)."""
    if has_leiji:
        return "ytd"
    last = None
    for marker in _MARKER_RE.finditer(text, 0, pos):
        last = marker
    if last is not None:
        return "m" if last.group(1) else "ytd"
    return None


def period_from_title(title: str) -> tuple[str, bool]:
    """(period, is_jan_feb). Raises ParseError on an unrecognized title."""
    match = _TITLE_PERIOD_RE.search(title)
    if not match:
        raise ParseError(
            "SPB bulletin title did not match the expected period pattern",
            expected="YYYY年[1-N月|上半年|一季度|前三季度|N月份]邮政行业运行情况",
            found=title,
        )
    year = int(match.group(1))
    if match.group(2):  # 1-N月 cumulative phrasing
        month = int(match.group(2))
    elif match.group(3):  # 上半年
        month = 6
    elif match.group(4):  # 一季度
        month = 3
    elif match.group(5):  # 前三季度
        month = 9
    elif match.group(6):  # N月[份]
        month = int(match.group(6))
    else:  # bare "YYYY年邮政行业运行情况" -- the full-year bulletin
        month = 12
    return f"{year:04d}-{month:02d}", bool(match.group(2)) and month == 2


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse(html_text: str, *, url: str = "", release_id: str = "") -> ParsedRelease:
    doc = html.fromstring(html_text)

    title_nodes = doc.xpath("//title/text()")
    title = title_nodes[0] if title_nodes else ""
    period_hint, is_jan_feb = period_from_title(title)

    pubdate_nodes = doc.xpath(_PUBDATE_XPATH)
    published_at = pubdate_nodes[0] if pubdate_nodes else None

    body = first_matching_node(doc, _BODY_XPATHS)
    text = node_text(body if body is not None else doc)

    span = 2 if is_jan_feb else 1
    flags = ["jan_feb"] if is_jan_feb else []

    rows: list[ParsedRow] = []
    for source_field, unit, pattern in _SPECS:
        seen: set[str] = set()
        for match in pattern.finditer(text):
            caliber = _caliber_at(text, match.start(), bool(match.group(1)))
            if caliber is None or caliber in seen:
                continue  # unclassifiable, or a repeat (first occurrence wins)
            seen.add(caliber)
            level = _to_float(match.group(2))
            yoy = sign_from_word(match.group(3)) * _to_float(match.group(4))
            yoy_measure = "m_yoy" if caliber == "m" else "ytd_yoy"
            rows.append(
                ParsedRow(source_field=source_field, raw_label=match.group(0), value=level,
                          unit_raw=unit, caliber_hint=caliber, period=period_hint,
                          span=span, flags=list(flags))
            )
            rows.append(
                ParsedRow(source_field=source_field, raw_label=match.group(0), value=yoy,
                          unit_raw="%", caliber_hint=yoy_measure, period=period_hint,
                          span=span, flags=list(flags))
            )

    # A jan_feb bulletin prints only the cumulative sentences; per
    # DATA-CONTRACT §3.2 the merged Jan-Feb print IS that period's 当月 too
    # (m == ytd, span=2). Mirror the ytd rows into m rows so the single
    # caliber doesn't open a hole every February.
    if is_jan_feb:
        have_m = {row.source_field for row in rows if row.caliber_hint == "m"}
        for row in [r for r in rows if r.caliber_hint in ("ytd", "ytd_yoy")]:
            if row.source_field in have_m:
                continue
            mirrored = "m" if row.caliber_hint == "ytd" else "m_yoy"
            rows.append(
                ParsedRow(source_field=row.source_field, raw_label=row.raw_label,
                          value=row.value, unit_raw=row.unit_raw, caliber_hint=mirrored,
                          period=row.period, span=row.span, flags=list(row.flags))
            )

    if not any(row.source_field == "快递业务量" for row in rows):
        raise ParseError(
            "express volume (快递业务量) not found in bulletin prose",
            expected="其中，快递业务量[累计]完成N亿件，同比增长/下降N%",
            found=f"no match in {len(text)}-char compacted body text",
        )

    return ParsedRelease(
        source=SOURCE,
        release_id=release_id,
        url=url,
        published_at=published_at,
        period_hint=period_hint,
        rows=rows,
    )
