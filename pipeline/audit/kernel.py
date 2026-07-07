"""Ported fuzzy-matching + numeric-tolerance kernel.

Ported near-verbatim from `tools/audit_official_data.py` (the legacy, pre-rebuild
auditor) — `numeric`, `tolerance_for`, `close_enough`, `strip_html`,
`compact_text`, and the shape of `format_number_candidates` /
`source_contains_value` all carry over unchanged in *semantics*: strip an HTML
page down to compact text, generate plausible on-page renderings of a number
(a `:g` form, 1- and 2-decimal forms, an integer form, and — for negative
values — `下降`/`负`-prefixed forms, since Chinese official releases almost
never print a bare minus sign), and search a window near a Chinese label first,
then the whole page.

Three adaptations made while porting, all verified against real fixture pages
in test_audit_kernel.py rather than assumed:

1. **万亿 (10,000-亿) scale awareness — genuinely new, not in the legacy code.**
   The legacy auditor only ever audited retail/property/income series, all
   already denominated in 亿元 or a raw index/percent, so a unit-scale mismatch
   never came up. The rebuilt catalog adds PBOC money-supply series
   (`pbc-m1/m2/m0`) stored in 亿元, but PBOC's own release prose reports them in
   万亿元 (e.g. "广义货币(M2)余额353.67万亿元" —
   pipeline/fixtures/raw/pboc_money/2026-05_finstats.html). Searching for the
   literal stored magnitude (353.67 万) would never find the "353.67" printed
   next to 万亿元. `format_number_candidates` now generates candidates at two
   scales — 1x (unchanged) and value/10000 (万亿) — and `source_contains_value`
   reports which scale actually matched, so callers/report output can show which
   unit convention the source page used.
2. **Returns the matched scale, not just a bool.** The legacy function returned
   `(matched, evidence)`; this one returns `(matched, evidence, scale_name)` so
   a caller can flag "matched, but only at the 万亿 scale" as evidence worth
   surfacing rather than silently equivalent to a same-unit match.
3. **Direction-aware matching — a real gap the mutation harness found, not in
   the legacy code either.** `format_number_candidates` for a NEGATIVE value
   always embeds its own sign (either a literal "-", or a 下降/负 prefix) --
   Python's own `:g`/`:.1f` formatting never drops the sign. But a POSITIVE
   value's candidates are bare magnitudes ("0.6"), and a bare substring search
   does not care what word comes before it: "0.6" is trivially a substring of
   "下降0.6%" too. A sign-flipped stored value (+0.6 where the release actually
   says 下降0.6%, i.e. -0.6) therefore used to verify as a false PASS --
   confirmed by pipeline/tests/test_mutation_gates.py's
   test_mut_sign_flip_gate_b_archive_gap. `_direction_is_consistent` now scans
   the text immediately before a bare-candidate match for a directional signal
   word (下降/回落/减少/... vs 增长/上涨/上升/...) within the current clause
   (stops at ，。；) and rejects a match whose nearest signal contradicts the
   stored value's sign. Deliberately NOT a general sentiment scan: 收窄/扩大
   (which describe a DELTA's own trend -- "涨幅扩大"/"降幅收窄" -- not the sign
   of the level being matched) are excluded from both signal lists, so they
   never manufacture a false signal near an unrelated number.

Everything else — the graduated tolerance curve, the negative-number phrasing,
the near-label-then-whole-page window fallback — is unchanged from the legacy
module's behavior.
"""
from __future__ import annotations

import html
import re

_WHITESPACE_RE = re.compile(r"\s+")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<script.*?</script>|<style.*?</style>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")

# (scale_name, factor): the stored value divided by `factor` gives the magnitude
# to search for. 1x is the overwhelming common case (source page uses the same
# unit as the series). wan_yi handles a 万亿元-denominated source page against a
# 亿元-stored series (see module docstring point 1).
DEFAULT_SCALES: tuple[tuple[str, float], ...] = (("1x", 1.0), ("wan_yi_1e4", 10000.0))

# Directional signal words scanned immediately before a matched number (see
# module docstring point 3). Deliberately narrow, curated lists of the
# multi-character VERBS these release templates actually use directly
# adjacent to a level/YoY number -- not a general sentiment lexicon, and
# deliberately NOT single characters:
#   负增长 is listed even though it contains 增长, since as a whole word it
#   means "negative growth"; checked before the bare 增长/负 fragment below
#   via longest-first matching so it can't be shadowed by a fragment match.
#   Bare single characters (降/升/跌) are deliberately EXCLUDED even though
#   they read as "decline"/"rise" in isolation: real release prose combines
#   them into MARGIN nouns ("涨幅"/"降幅", literally "gain margin"/"decline
#   margin") that say nothing about the CURRENT number's own sign -- confirmed
#   empirically ("食品价格下降0.9%，降幅比上月收窄0.04个百分点": a bare "降" in
#   the signal list matched inside "降幅" and wrongly rejected the genuinely
#   positive-phrased "0.04" delta that follows 收窄). The multi-character verb
#   forms below are the only shapes actually observed immediately before a
#   number in real fixture pages (下降X%/回落X个点/增长X%/上涨X%/...).
_NEGATIVE_SIGNAL_WORDS = ("负增长", "下降", "下跌", "下滑", "降至", "回落", "减少", "亏损", "负", "-", "－", "−")
_POSITIVE_SIGNAL_WORDS = ("增长", "增加", "上涨", "上升", "提高", "攀升", "回升")
# Explicitly EXCLUDED from both lists (documented, not just omitted): 涨幅 /
# 降幅 / 收窄 / 扩大 describe a DELTA's own trend ("涨幅扩大" = the gain is
# growing, "降幅收窄" = the decline is narrowing) -- neither the margin nouns
# nor 收窄/扩大 themselves say anything about the sign of the number being
# matched, so treating any of them as directional signals would manufacture
# false rejections/acceptances near a 个百分点 delta phrase sitting next to the
# label. 持平 (unchanged) is similarly excluded -- not a direction.
_CLAUSE_BOUNDARY_CHARS = "，。；！？、\n"
_SIGNAL_LOOKBACK_CHARS = 12


def _directional_signal_before(window: str, index: int) -> str | None:
    """Scan up to `_SIGNAL_LOOKBACK_CHARS` immediately before `index`, clipped
    to the current clause (stops at the nearest ，/。/；/... so a signal word
    from a PRECEDING, unrelated clause can never leak in), for a directional
    signal word. Returns "negative", "positive", or None (no signal, or only
    a neutral word like 收窄/扩大/持平) -- checked longest-word-first so e.g.
    "负增长" (whole word) isn't shadowed by a bare "负" or "增长" fragment
    match landing first."""
    segment = window[max(0, index - _SIGNAL_LOOKBACK_CHARS) : index]
    for boundary in _CLAUSE_BOUNDARY_CHARS:
        pos = segment.rfind(boundary)
        if pos >= 0:
            segment = segment[pos + 1 :]
    for word in sorted(_NEGATIVE_SIGNAL_WORDS, key=len, reverse=True):
        if word in segment:
            return "negative"
    for word in sorted(_POSITIVE_SIGNAL_WORDS, key=len, reverse=True):
        if word in segment:
            return "positive"
    return None


def _direction_is_consistent(value: float, candidate: str, window: str, index: int) -> bool:
    """True iff a substring match of `candidate` at `window[index:]` is
    directionally consistent with `value`'s sign -- the fix for the
    sign-flip false-PASS gap (see module docstring point 3).

    A candidate that already self-encodes a sign (starts with a minus glyph,
    or a 下降/负 prefix -- the only shapes `format_number_candidates` ever
    produces for a negative value; see _candidate_strings) is self-
    confirming: it cannot possibly match unless the text itself said exactly
    that. Otherwise (the bare-magnitude candidates every value gets, which is
    the ONLY form a non-negative value's candidates ever take) this scans the
    immediately-preceding text for a signal word and rejects a match whose
    nearest signal contradicts the stored value's sign. No signal at all (a
    bare level mention, e.g. "CPI为103.6") is accepted for either sign --
    most legitimate index/level statements carry no directional word at all.
    """
    self_signed = candidate.startswith(("-", "－", "−")) or candidate.startswith(("下降", "负"))
    if self_signed:
        return value < 0  # by construction only ever generated for value < 0; defensive
    signal = _directional_signal_before(window, index)
    if value < 0:
        return signal != "positive"
    if value > 0:
        return signal != "negative"
    return True  # value == 0: 持平, no sign to contradict


def numeric(value) -> float | None:
    """Best-effort float coercion. None for anything that isn't a real number
    (bool is deliberately excluded — True/False are not audit values even though
    isinstance(True, int) is True in Python)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if cleaned in {"", "-", "--", "—"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def tolerance_for(value: float | int | None) -> float:
    """Graduated absolute tolerance by magnitude — small series (rates, index
    points) get a tight band; large levels (亿元 aggregates) get a proportional
    one. Unchanged from the legacy auditor."""
    if value is None:
        return 0.0
    value = abs(float(value))
    if value < 10:
        return 0.03
    if value < 100:
        return 0.08
    if value < 1000:
        return 0.2
    return max(1.0, value * 0.0008)


def close_enough(expected, observed, tolerance: float | None = None) -> bool:
    expected_number = numeric(expected)
    observed_number = numeric(observed)
    if expected_number is None or observed_number is None:
        return expected == observed
    tolerance = tolerance_for(expected_number) if tolerance is None else tolerance
    return abs(expected_number - observed_number) <= tolerance


def strip_html(raw: bytes | str) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def compact_text(text: str) -> str:
    return _WHITESPACE_RE.sub("", text.replace("　", "").replace("\xa0", ""))


def _rstrip_decimal(formatted: str) -> str | None:
    """Trim trailing zeros off a fixed-precision format (e.g. "0.60" -> "0.6"),
    but reject the result if stripping ate the number's only significant digit
    (e.g. a magnitude of 0.04 formatted at 1 decimal is "0.0" -> naive
    stripping gives the degenerate "0", which is not a rendering of 0.04 at
    all -- it is every bare zero on the page. Confirmed as a real false-match
    source empirically: "下降0" -- the same degenerate stripping applied to a
    负/下降-prefixed candidate -- matched inside an unrelated "下降0.9%" for a
    stored value of -0.04). None means "this precision has nothing useful to
    contribute"; callers must skip it rather than add a wildcard-like digit."""
    stripped = formatted.rstrip("0").rstrip(".")
    if stripped in ("", "0", "-0"):
        return None
    return stripped


def _candidate_strings(value: float) -> set[str]:
    candidates = {f"{value:g}"}
    for precision in (1, 2):
        stripped = _rstrip_decimal(f"{value:.{precision}f}")
        if stripped is not None:
            candidates.add(stripped)
    if abs(value - round(value)) < 0.000001:
        candidates.add(str(int(round(value))))
    if abs(value) >= 1000:
        candidates.add(str(int(round(value))))
    if value < 0:
        abs_value = abs(value)
        candidates.add(f"下降{abs_value:g}")
        candidates.add(f"负{abs_value:g}")
        stripped = _rstrip_decimal(f"{abs_value:.1f}")
        if stripped is not None:
            candidates.add(f"下降{stripped}")
    return candidates


def format_number_candidates(value: float, *, scales=DEFAULT_SCALES) -> list[tuple[str, str]]:
    """[(scale_name, candidate_string), ...], longer candidates first within
    each scale (longer strings are less likely to false-positive on a
    substring search)."""
    out: list[tuple[str, str]] = []
    for scale_name, factor in scales:
        scaled = value / factor
        for candidate in sorted(_candidate_strings(scaled), key=len, reverse=True):
            if candidate:
                out.append((scale_name, candidate))
    return out


def source_contains_value(
    text: str, label: str, value: float, *, scales=DEFAULT_SCALES
) -> tuple[bool, str | None, str | None]:
    """(matched, evidence_snippet, scale_name).

    Searches a +-window around the first occurrence of `label` first (a label
    can legitimately appear more than once — e.g. a release states both a
    monthly and a cumulative figure under the same series name — so proximity
    is a hint, not a requirement), then falls back to the whole page. Ported
    from the legacy `source_contains_value`, generalized over multiple unit
    scales (see module docstring point 1) and, critically, direction-aware
    (module docstring point 3): a raw substring hit is only accepted if
    `_direction_is_consistent` confirms the text's own directional wording
    (下降/增长/... or none) doesn't contradict `value`'s sign -- a match that
    fails this is skipped, NOT returned as a false positive, and the search
    continues (a genuine same-magnitude, correct-direction mention may still
    exist elsewhere in the page).
    """
    compact = compact_text(text).replace(",", "")
    compact_label = compact_text(label)
    name_index = compact.find(compact_label) if compact_label else -1
    windows = []
    if name_index >= 0:
        windows.append(compact[max(0, name_index - 220) : name_index + len(compact_label) + 320])
    windows.append(compact)
    for scale_name, candidate in format_number_candidates(value, scales=scales):
        for window in windows:
            search_from = 0
            while True:
                index = window.find(candidate, search_from)
                if index < 0:
                    break
                if _direction_is_consistent(value, candidate, window, index):
                    start, end = max(0, index - 50), min(len(window), index + len(candidate) + 50)
                    return True, window[start:end], scale_name
                search_from = index + 1  # direction-inconsistent here; a later occurrence may still be valid
    return False, None, None


def sample(items: list, rng, size: int) -> list:
    """Deterministic-given-rng subsample, ported unchanged from the legacy
    `sample` helper."""
    if len(items) <= size:
        return list(items)
    return rng.sample(items, size)


def severity_for_mismatch(catalog_entry: dict) -> str:
    """Shared BLOCK-vs-WARN policy for a confirmed numeric mismatch (used by
    every archive-sampling check): Tier-1/2 official series BLOCK; Tier-3
    series, or a series published by an industry association rather than a
    government body, WARN instead. `cflp` (中国物流与采购联合会, publisher of the
    PMI) is the one non-government agency in this catalog -- it can be Tier-1
    (PMI is) yet still get the softer "association" treatment per task spec's
    literal "Tier-3/association" framing, which lists them as two independent
    reasons for the softer bucket, not "association implies tier-3"."""
    if catalog_entry.get("source", {}).get("agency") == "cflp":
        return "warn"
    return "warn" if catalog_entry.get("tier") == 3 else "block"
