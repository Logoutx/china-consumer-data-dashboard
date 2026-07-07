"""Parser for the NBS monthly CPI release (docs/ACQUISITION.md Group 1).

Fixture: pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html
Title shape: "YYYY年M月份居民消费价格同比上涨N%" (or 下降), published ~9th-10th.

The release's "YYYY年M月份居民消费价格主要数据" table carries three columns --
环比涨跌幅% (MoM), 同比涨跌幅% (YoY), 1—M月同比涨跌幅% (YTD-average YoY) -- for the
headline plus city/rural/food/non-food/consumer-goods/services/core rows, followed
by the 8 top-level category rows (一 through 八). This parser reads all of it
straight from that one table; it does NOT need the prose regexes ACQUISITION.md
sketched for "core CPI" / food / services, because in this fixture's table format
those rows are already present as labelled table rows, not prose-only figures (see
the project's final report for this as a documented deviation from the ACQUISITION
sketch).

Deliberately out of scope: the ~30 sub-item rows (猪肉, 牛肉, 家用器具, ...) nested
under each of the 8 categories -- the task calls for headline + 8 categories, not
the full 268-item breakdown, and NBS's own downloadable index-level data table
(the "点击下载：相关数据表" .xlsx link) is not fetched by an HTML parser, so no
absolute CPI index level (只有涨跌幅) is available here -- only mom/m_yoy/ytd_yoy.
"""
from __future__ import annotations

import re

from lxml import html

from pipeline import ParseError, ParsedRelease, ParsedRow
from pipeline.parsers._util import (
    first_matching_node,
    node_text,
    node_text_keep_spaces,
    strip_row_label,
    to_number,
)

SOURCE = "nbs-cpi"

_TITLE_PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月份居民消费价格")
_PUBDATE_XPATH = "//meta[@name='PubDate']/@content"
_BODY_XPATHS = [
    '//div[contains(@class,"detail-text-content")]',
    '//div[@id="zoom"]',
    '//div[contains(@class,"TRS_Editor")]',
]
_HEADLINE_YOY_RE = re.compile(r"全国居民消费价格同比(上涨|下降)([\d.]+)%")

# Summary rows carried directly in the main table (label after strip_row_label ->
# a bare field_map key).
_SUMMARY_LABELS = {"居民消费价格", "城市", "农村", "食品", "非食品", "消费品", "服务", "不包括食品和能源"}

# The 8 top-level categories, canonical spelling straight from this fixture's own
# 附注 (statistical scope) paragraph -- used both to match "一、食品烟酒及在外餐饮"
# style rows and as the completeness assertion below.
_CATEGORY_RE = re.compile(r"^[一二三四五六七八]、(.+)$")
_EXPECTED_CATEGORIES = {
    "食品烟酒及在外餐饮",
    "衣着",
    "居住",
    "生活用品及服务",
    "交通通信",
    "教育文化娱乐",
    "医疗保健",
    "其他用品及服务",
}


def _classify_header_cell(text: str) -> str | None:
    if re.search(r"\d.*月.*同比", text):
        return "ytd_yoy"
    if "环比" in text:
        return "mom"
    if "同比" in text:
        return "m_yoy"
    return None


def _locate_column_map(rows) -> tuple[dict[str, int], int]:
    """Find the header row (contains 环比 + two 同比-bearing cells) and return a
    {measure_name: column_index} map, so column order is detected, never assumed
    (guards against the "header-row count flips" failure mode in ACQUISITION.md's
    parser-sketch-A risk list)."""
    for row_index, row in enumerate(rows):
        cells = row.xpath("./td|./th")
        texts = [node_text(cell) for cell in cells]
        if not texts:
            continue
        classified = {index: _classify_header_cell(text) for index, text in enumerate(texts)}
        found = {measure: index for index, measure in classified.items() if measure}
        if {"mom", "m_yoy", "ytd_yoy"} <= found.keys():
            return found, row_index
    raise ParseError(
        "could not locate the CPI table header row",
        expected="a row with 环比涨跌幅(%) / 同比涨跌幅(%) / 1—N月同比涨跌幅(%) cells",
        found=f"{len(rows)} rows, none classified as a full mom/m_yoy/ytd_yoy header",
    )


def parse(html_text: str, *, url: str = "", release_id: str = "") -> ParsedRelease:
    doc = html.fromstring(html_text)

    title_nodes = doc.xpath("//title/text()")
    title = title_nodes[0] if title_nodes else ""
    period_match = _TITLE_PERIOD_RE.search(title)
    if not period_match:
        raise ParseError(
            "CPI release title did not match the expected period pattern",
            expected=r"YYYY年M月份居民消费价格...",
            found=title,
        )
    period_hint = f"{int(period_match.group(1)):04d}-{int(period_match.group(2)):02d}"

    pubdate_nodes = doc.xpath(_PUBDATE_XPATH)
    published_at = pubdate_nodes[0] if pubdate_nodes else None

    tables = doc.xpath("//table[.//*[contains(normalize-space(string(.)), '居民消费价格')]]")
    if not tables:
        raise ParseError(
            "CPI main data table not found",
            expected="a <table> containing '居民消费价格' text",
            found="no matching <table> in the document",
        )
    table = tables[0]
    rows = table.xpath(".//tr")
    column_map, header_row_index = _locate_column_map(rows)

    found_summary: set[str] = set()
    found_categories: set[str] = set()
    parsed_rows: list[ParsedRow] = []

    for row_index, row in enumerate(rows):
        if row_index <= header_row_index:
            continue
        cells = row.xpath("./td|./th")
        if not cells:
            continue
        texts = [node_text(cell) for cell in cells]
        if not texts or not texts[0]:
            continue

        raw_label = texts[0]
        label = strip_row_label(raw_label)
        category_match = _CATEGORY_RE.match(raw_label)

        if label in _SUMMARY_LABELS:
            source_field = label
            found_summary.add(label)
        elif category_match:
            source_field = category_match.group(1)
            found_categories.add(source_field)
        else:
            continue  # sub-item or section spacer row ("按类别分", 猪肉, ...) -- out of scope

        for measure, column_index in column_map.items():
            if column_index >= len(texts):
                continue
            value = to_number(texts[column_index])
            if value is None:
                continue
            parsed_rows.append(
                ParsedRow(
                    source_field=source_field,
                    raw_label=raw_label,
                    value=value,
                    unit_raw="%",
                    caliber_hint=measure,
                    period=period_hint,
                )
            )

    missing_summary = _SUMMARY_LABELS - found_summary
    missing_categories = _EXPECTED_CATEGORIES - found_categories
    if missing_summary or missing_categories:
        raise ParseError(
            "CPI table is missing expected summary/category rows -- format may have drifted",
            expected=f"summary={sorted(_SUMMARY_LABELS)} categories={sorted(_EXPECTED_CATEGORIES)}",
            found=f"missing_summary={sorted(missing_summary)} missing_categories={sorted(missing_categories)}",
        )

    # Cross-check: the headline YoY quoted in the article's opening prose sentence
    # must agree with the table's headline m_yoy (ACQUISITION.md parser-sketch-A
    # point 4: "headline YoY from <title>/first <p> as a cross-check").
    body = first_matching_node(doc, _BODY_XPATHS)
    if body is not None:
        body_text = node_text_keep_spaces(body)
        prose_match = _HEADLINE_YOY_RE.search(body_text)
        if prose_match:
            prose_yoy = to_number(prose_match.group(2))
            sign = 1 if prose_match.group(1) == "上涨" else -1
            prose_value = sign * prose_yoy if prose_yoy is not None else None
            table_value = next(
                (row.value for row in parsed_rows if row.source_field == "居民消费价格" and row.caliber_hint == "m_yoy"),
                None,
            )
            if prose_value is not None and table_value is not None and abs(prose_value - table_value) > 0.05:
                raise ParseError(
                    "CPI headline YoY in the prose does not match the table -- possible parse error",
                    expected=f"table m_yoy={table_value}",
                    found=f"prose headline yoy={prose_value}",
                )

    return ParsedRelease(
        source=SOURCE,
        release_id=release_id,
        url=url,
        published_at=published_at,
        period_hint=period_hint,
        rows=parsed_rows,
    )
