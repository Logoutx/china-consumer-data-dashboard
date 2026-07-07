"""Parser for the NBS monthly retail/activity release (docs/ACQUISITION.md Group 2,
"parser sketch A" -- the representative worst case: numbers split across a table
plus prose supplements).

Fixture: pipeline/fixtures/raw/nbs_activity/2026-05_retail.html
Title shape: "YYYY年1—M月份社会消费品零售总额增长N%" (retail's headline is always
the YTD figure; a standalone January release does not exist -- January is folded
into the "1—2月" combined print, per the Jan-Feb rule in DATA-CONTRACT §3.2/§6).

The "YYYY年M月份社会消费品零售总额主要数据" table carries a two-row header
(coarse "M月" / "1—M月" column groups, then a granular 绝对量/同比增长 pair under
each) followed by: the headline total, 除汽车以外, 限额以上单位, 网上商品零售额
(YTD-only -- see the 2026-indicator-change note below), urban/rural, catering vs
goods, and the 限额以上 commodity-category breakdown.

2026 online-indicator change (DATA-CONTRACT §2.1): this fixture's own footnote
confirms the break the contract anticipated -- NBS replaced "网上零售额" with
"网上商品和服务零售额" (broader platform scope), split "实物商品网上零售额" into
"网上商品零售额" (goods) + a new "网上服务零售额" (services), and states outright
"网上商品和服务零售额与网上零售额数据不可比" (not comparable to the old series).
This parser reads the *new* labels only; do not splice this id across the seam.
Also note this specific month's table has no month-level column for 网上商品零售额
at all (only YTD) -- table cells are literally "-", not just visually blank.

The commodity-category table in this fixture has 16 rows (ACQUISITION.md's sketch
said "18"); there is no 书报杂志类 row this month. This parser does not hardcode an
expected count for the category table -- only the headline anchors below are
required -- so a category appearing/disappearing release-to-release does not by
itself trip ParseError.
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

SOURCE = "nbs-retail"

_TITLE_PERIOD_RE = re.compile(r"(\d{4})年(?:1[—\-－](\d{1,2})月份|(\d{1,2})月份)社会消费品零售总额")
_PUBDATE_XPATH = "//meta[@name='PubDate']/@content"
_BODY_XPATHS = [
    '//div[contains(@class,"detail-text-content")]',
    '//div[@id="zoom"]',
    '//div[contains(@class,"TRS_Editor")]',
]

_HEADLINE_TITLE_RE = re.compile(r"社会消费品零售总额(增长|下降)([\d.]+)%")
_ONLINE_TOTAL_RE = re.compile(r"全国网上商品和服务零售额([\d.]+)亿元[，,]\s*(?:同比)?(增长|下降)([\d.]+)%")
_ONLINE_SERVICES_RE = re.compile(r"网上服务零售额([\d.]+)亿元[，,]\s*(?:同比)?(增长|下降)([\d.]+)%")

# Headline anchors that must be present with at least one non-null measure, or the
# release table has drifted out from under this parser (fail loud, not partial).
_REQUIRED_LABELS = {"社会消费品零售总额", "城镇", "乡村", "商品零售额", "餐饮收入", "网上商品零售额"}


def _classify_header_cell(text: str) -> str | None:
    if "绝对量" in text:
        return "value"
    if "同比增长" in text or "同比" in text:
        return "yoy"
    return None


def _find_header_row_index(rows) -> int:
    """Find the granular value/yoy header row, checking its LAST 4 cells for the
    exact [value, yoy, value, yoy] pattern -- not a fixed cell count. This
    fixture's header spans two <tr>s: the first has "指标"/"5月"/"1—5月" with
    rowspan/colspan, so the SECOND header <tr> (绝对量/同比增长 x2) has only 4
    literal <td> children, not 5 -- the label cell is consumed by the first row's
    rowspan and never appears here. Data rows below it *do* carry a literal label
    cell (5 children each), which is why extraction below always takes a data
    row's *last* 4 cells rather than reusing a column offset computed from this
    header row (ACQUISITION.md parser-sketch-A risk #1: guard the header shape
    explicitly, don't assume a fixed index -- including across a rowspan quirk)."""
    for row_index, row in enumerate(rows):
        cells = row.xpath("./td|./th")
        texts = [node_text(cell) for cell in cells]
        if len(texts) < 4:
            continue
        classified = [_classify_header_cell(text) for text in texts[-4:]]
        if classified == ["value", "yoy", "value", "yoy"]:
            return row_index
    raise ParseError(
        "could not locate the retail table's granular value/yoy header row",
        expected="a row whose last 4 cells are 绝对量(亿元)/同比增长(%) repeated twice (month, then YTD)",
        found=f"{len(rows)} rows, none classified as [value, yoy, value, yoy]",
    )


def parse(html_text: str, *, url: str = "", release_id: str = "") -> ParsedRelease:
    doc = html.fromstring(html_text)

    title_nodes = doc.xpath("//title/text()")
    title = title_nodes[0] if title_nodes else ""
    period_match = _TITLE_PERIOD_RE.search(title)
    if not period_match:
        raise ParseError(
            "retail release title did not match the expected period pattern",
            expected=r"YYYY年1—M月份社会消费品零售总额... (or YYYY年M月份...)",
            found=title,
        )
    year = int(period_match.group(1))
    month = int(period_match.group(2) or period_match.group(3))
    period_hint = f"{year:04d}-{month:02d}"

    pubdate_nodes = doc.xpath(_PUBDATE_XPATH)
    published_at = pubdate_nodes[0] if pubdate_nodes else None

    tables = doc.xpath("//table[.//*[contains(normalize-space(string(.)), '社会消费品零售总额')]]")
    if not tables:
        raise ParseError(
            "retail main data table not found",
            expected="a <table> containing '社会消费品零售总额' text",
            found="no matching <table> in the document",
        )
    table = tables[0]
    rows = table.xpath(".//tr")
    header_row_index = _find_header_row_index(rows)

    found_labels: set[str] = set()
    parsed_rows: list[ParsedRow] = []

    for row_index, row in enumerate(rows):
        if row_index <= header_row_index:
            continue
        cells = row.xpath("./td|./th")
        if len(cells) < 5:
            continue
        texts = [node_text(cell) for cell in cells]
        raw_label = texts[0]
        if not raw_label:
            continue
        label = strip_row_label(raw_label)

        measure_texts = texts[-4:]  # [m, m_yoy, ytd, ytd_yoy] -- always the last 4 cells
        values = {
            "m": to_number(measure_texts[0]),
            "m_yoy": to_number(measure_texts[1]),
            "ytd": to_number(measure_texts[2]),
            "ytd_yoy": to_number(measure_texts[3]),
        }
        if all(value is None for value in values.values()):
            continue  # section spacer row ("按经营地分", "按消费类型分") -- no numbers at all

        found_labels.add(label)
        for measure, value in values.items():
            if value is None:
                continue
            parsed_rows.append(
                ParsedRow(
                    source_field=label,
                    raw_label=raw_label,
                    value=value,
                    unit_raw="亿元",
                    caliber_hint=measure,
                    period=period_hint,
                )
            )

    missing = _REQUIRED_LABELS - found_labels
    if missing:
        raise ParseError(
            "retail table is missing required rows -- format may have drifted",
            expected=sorted(_REQUIRED_LABELS),
            found=f"missing={sorted(missing)}, present={sorted(found_labels)}",
        )

    # Prose supplements for what the table omits entirely (ACQUISITION.md
    # parser-sketch-A point 4): the combined online total and the services-only
    # split are prose-only in this fixture, not table rows.
    body = first_matching_node(doc, _BODY_XPATHS)
    if body is None:
        raise ParseError(
            "retail article body not found",
            expected="one of detail-text-content / #zoom / TRS_Editor wrapper",
            found="no matching body container",
        )
    body_text = node_text_keep_spaces(body)

    online_total_match = _ONLINE_TOTAL_RE.search(body_text)
    if online_total_match:
        value, sign_word, yoy = online_total_match.groups()
        sign = 1 if sign_word == "增长" else -1
        parsed_rows.append(
            ParsedRow(
                source_field="网上商品和服务零售额",
                raw_label="全国网上商品和服务零售额",
                value=to_number(value),
                unit_raw="亿元",
                caliber_hint="ytd",
                period=period_hint,
            )
        )
        parsed_rows.append(
            ParsedRow(
                source_field="网上商品和服务零售额",
                raw_label="全国网上商品和服务零售额同比",
                value=sign * to_number(yoy),
                unit_raw="%",
                caliber_hint="ytd_yoy",
                period=period_hint,
            )
        )

    online_services_match = _ONLINE_SERVICES_RE.search(body_text)
    if online_services_match:
        value, sign_word, yoy = online_services_match.groups()
        sign = 1 if sign_word == "增长" else -1
        parsed_rows.append(
            ParsedRow(
                source_field="网上服务零售额",
                raw_label="网上服务零售额",
                value=to_number(value),
                unit_raw="亿元",
                caliber_hint="ytd",
                period=period_hint,
            )
        )
        parsed_rows.append(
            ParsedRow(
                source_field="网上服务零售额",
                raw_label="网上服务零售额同比",
                value=sign * to_number(yoy),
                unit_raw="%",
                caliber_hint="ytd_yoy",
                period=period_hint,
            )
        )

    # Cross-check: the title's headline growth figure must agree with the table's
    # headline ytd_yoy.
    title_match = _HEADLINE_TITLE_RE.search(title)
    if title_match:
        sign = 1 if title_match.group(1) == "增长" else -1
        title_value = sign * to_number(title_match.group(2))
        table_value = next(
            (row.value for row in parsed_rows if row.source_field == "社会消费品零售总额" and row.caliber_hint == "ytd_yoy"),
            None,
        )
        if title_value is not None and table_value is not None and abs(title_value - table_value) > 0.05:
            raise ParseError(
                "retail headline YoY in the title does not match the table -- possible parse error",
                expected=f"table ytd_yoy={table_value}",
                found=f"title headline yoy={title_value}",
            )

    return ParsedRelease(
        source=SOURCE,
        release_id=release_id,
        url=url,
        published_at=published_at,
        period_hint=period_hint,
        rows=parsed_rows,
    )
