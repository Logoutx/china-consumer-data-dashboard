"""Shared text/number helpers for pipeline/parsers/*.

Every parser is a pure function `html_text -> ParsedRelease`. These helpers
implement the two fixture gotchas documented in
pipeline/fixtures/raw/README.md:

  1. Wrapper drift: NBS pages have used both `id="zoom"` (PBoC still does) and
     `class="detail-text-content"` (2026 NBS pages). `first_matching_node` walks a
     list of XPath candidates and returns the first that matches, so callers don't
     hardcode one wrapper shape.
  2. Inline-tag splitting: labels like `综合PMI产出指数` are split across
     `<span>` tags in the raw HTML. `node_text` joins `.itertext()` and normalises
     whitespace, so a raw substring test never sees a false negative.
"""
from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
_BLANK_VALUES = {"", "-", "--", "—", "－"}


def node_text(node) -> str:
    """Join every text fragment under `node` (defeats inline-tag splitting) and
    collapse whitespace, including full-width space/nbsp."""
    text = "".join(node.itertext())
    text = text.replace("　", " ").replace("\xa0", " ")
    return _WHITESPACE_RE.sub("", text)


def node_text_keep_spaces(node) -> str:
    """Like node_text, but collapses runs of whitespace to a single space instead
    of deleting it -- for prose blocks where word/number boundaries matter."""
    text = "".join(node.itertext())
    text = text.replace("　", " ").replace("\xa0", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def first_matching_node(doc, xpaths: list[str]):
    """Return the first node matched by any XPath in `xpaths`, trying them in
    order. Returns None if none match (caller decides whether that's fatal)."""
    for xpath in xpaths:
        matches = doc.xpath(xpath)
        if matches:
            return matches[0]
    return None


def to_number(raw: str) -> float | None:
    """Parse a table cell into a float, or None for a blank/placeholder cell
    ("-", "--", "—", empty). Does not interpret 增长/下降 sign words -- table cells
    in these fixtures already carry a literal sign; prose sign words are handled by
    `signed_value` below."""
    text = raw.strip()
    if text in _BLANK_VALUES:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def sign_from_word(word: str) -> int:
    """+1 for an increase word (增长/增加/上涨/多增/多), -1 for a decrease word
    (下降/减少/少增/少/负). Raises ValueError on an unrecognised word so a prose
    format drift is never silently treated as +1."""
    positive = {"增长", "增加", "上涨", "多增", "多", "上升"}
    negative = {"下降", "减少", "少增", "少", "负", "下跌"}
    if word in positive:
        return 1
    if word in negative:
        return -1
    raise ValueError(f"unrecognised sign word: {word!r}")


def yi_yuan(amount: float, unit: str) -> float:
    """Normalise a CNY (or USD, structurally identical) amount to 亿-scale given the
    unit token that preceded it: 万亿 -> x10000, 亿 -> unchanged."""
    if unit == "万亿":
        return round(amount * 10000, 4)
    if unit == "亿":
        return amount
    raise ValueError(f"unrecognised money unit: {unit!r}")


def strip_row_label(label: str) -> str:
    """Strip the leading indentation / “其中：” prefix NBS uses for sub-rows, so
    the remaining text matches a bare field_map key (e.g. "　　其中：食品" -> "食品")."""
    text = label.strip()
    text = re.sub(r"^其中[：:]", "", text)
    return text.strip()
