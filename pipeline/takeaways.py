"""pipeline/takeaways.py — the conservative Chinese takeaway-headline generator.

States ONLY arithmetic facts about a series' published YoY, never causes. Every
template below is fixed by the task spec (wording is binding, including
typography) -- this module's job is to pick the right template for a given
(latest, previous) pair of published values and render it with correct pangu
spacing (DATA-CONTRACT §12), never to interpret *why* a number moved.

Design shape
------------
`generate_takeaway(TakeawayInput) -> str` is a pure function of already-resolved
scalars (the latest/previous published YoY, a period label, a couple of flags).
It knows nothing about observations arrays, breaks, or catalog files -- that
orchestration (which caliber to headline, what counts as "previous" across a
Jan-Feb print or a break seam, streak history) is pipeline/build.py's job, by
design: build.py owns "what are the facts", this module owns "how do we say
them". `compute_streak` is the one exception that takes a plain list rather
than scalars, because a streak is inherently a scan over history; it is still a
pure function of numbers, with no notion of breaks (the caller must slice/null
the history at any no_yoy_across seam before calling it -- see its docstring).

Typography decisions worth flagging back to the lead (both applied literally
per the task spec's own template strings, but the two disagree with each other
and it seemed important to make that explicit rather than silently pick one):

  - DATA-CONTRACT §12's own worked examples use an EM DASH for a cumulative
    period span ("2026 年 1—5 月"). The task spec's literal YTD-only template
    ("1-{M} 月{name}累计…") and the Jan-Feb prefix ("1-2 月合并统计，") use a
    plain ASCII hyphen instead, and omit the year entirely. Both conventions
    are honored literally here, in their own places: build.py's period_label_zh
    (used for the bundle's `latest`/`prev` blocks) uses the DATA-CONTRACT em
    dash + year; the YTD-only takeaway sentence built *inside this module* uses
    the task spec's bare "1-{M} 月" / "1-{M-1} 月" (hyphen, no year). If the
    lead actually wants one convention everywhere, this is the line to change.
  - Quarters render with an Arabic digit ("2 季度"), not the conventional NBS
    prose "二季度" -- the task spec calls this out explicitly as a deliberate
    typography choice (Arabic numerals win per DATA-CONTRACT §12) even though
    it reads slightly unusually to a Chinese business-press eye.
"""
from __future__ import annotations

from dataclasses import dataclass

# -- verb selection -------------------------------------------------------------

_PRICE_VALUE_TYPES = {"index", "mom_pct"}


def choose_verb(catalog_entry: dict) -> str:
    """Pick the sign-matrix's positive-direction verb for a series: price-type
    series (CPI/PPI, 70-city price) use 上涨; activity/money series (retail,
    M2, FAI, ...) use 增长, which is also the spec's documented default.

    Contract gap flagged to the lead: the task spec says "a per-series verb
    field in catalog metadata chooses" the verb, but data/schemas/
    catalog.schema.json has no `verb` property and sets
    additionalProperties:false on a catalog entry -- so a real catalog
    conforming to today's schema can never actually carry one. This function
    is forward-compatible (it honors `verb` if a caller injects one, at zero
    cost when absent) but falls back to a value_type heuristic so it produces
    the right answer against the *actual* schema as it exists today:
    `index` / `mom_pct` series are priced/indexed quantities (CPI, PPI,
    70-city price) -> 上涨; everything else -> 增长, matching the spec's
    default. Recommend the lead add an optional `verb` enum to
    catalog.schema.json in a future wave so this stops being a heuristic.
    """
    verb = catalog_entry.get("verb")
    if verb in ("上涨", "增长"):
        return verb
    if catalog_entry.get("value_type") in _PRICE_VALUE_TYPES:
        return "上涨"
    return "增长"


# -- pangu spacing (DATA-CONTRACT §12) -------------------------------------------

# Main CJK ideograph blocks actually in play here (common + a couple of rarer
# extension blocks); deliberately excludes CJK *punctuation* (，。“”「」 etc.),
# which must never trigger a forced space either side (§12: "no space next to
# full-width punctuation") -- those code points simply aren't in these ranges.
_CJK_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _needs_pangu_space(left: str, right: str) -> bool:
    return (_is_cjk(left) and right.isdigit()) or (left.isdigit() and _is_cjk(right))


def _join(*parts: str) -> str:
    """Concatenate fragments, inserting one half-width space at any CJK<->ASCII
    -digit seam between two consecutive fragments. Each fragment is assumed to
    already be correctly spaced *internally* (e.g. "1-4 月", "2026 年 5 月") --
    this only fixes the boundary where two fragments meet, which is the seam
    that's actually error-prone to get right by hand (a fixed Chinese word
    might be followed by either "上月" (CJK, no space wanted) or "1-4 月"
    (digit-first, space wanted), depending on branch)."""
    result = ""
    for part in parts:
        if not part:
            continue
        if result and _needs_pangu_space(result[-1], part[0]):
            result += " "
        result += part
    return result


# -- number formatting ------------------------------------------------------------


def _fmt(value: float) -> str:
    """Render a magnitude with the precision it actually carries: 1 decimal by
    default (the NBS-headline norm), 2 if the stored value needs 2 -- "never
    round differently than published" (task spec). Always non-negative: sign
    is conveyed by the surrounding verb (上涨/下降/加快/放缓/...), never by a
    '-' glyph, matching every template in the spec and DATA-CONTRACT's own
    worked examples.
    """
    value = abs(round(value, 6))  # kill binary-float subtraction noise (e.g. 5.9-5.1)
    decimals = 1
    if round(value, 1) != value:
        decimals = 2
    return f"{value:.{decimals}f}"


def _pct(value: float) -> str:
    return f"{_fmt(value)}%"  # no space before % (§12)


def _pp(value: float) -> str:
    return f"{_fmt(value)} 个百分点"  # point-difference unit, not %


# -- conservative-language guard --------------------------------------------------

# Task spec's banned list. Single characters like 受/由 are broad, but this
# module fully controls every template fragment below (none of them contain
# these tokens), so the check can never fire on the generator's own copy -- it
# exists as a regression guard against a future template edit accidentally
# reintroducing causal language, not as a claim that arbitrary catalog names
# could never coincidentally contain 受. If that ever happens in practice, the
# fix is in the series' name_zh, not here.
BANNED_SUBSTRINGS = ("受", "因为", "主要是", "由于", "大幅", "显著", "明显")


def _assert_conservative(text: str) -> str:
    for token in BANNED_SUBSTRINGS:
        if token in text:
            raise ValueError(f"generated takeaway contains banned language {token!r}: {text!r}")
    return text


# -- streaks ------------------------------------------------------------------


def compute_streak(history: list[float | None]) -> tuple[int, str | None]:
    """Count the trailing streak ending at history[-1] ("latest"), ascending
    chronological order. Two kinds, mutually exclusive per period (§ task spec
    "prefer sign-streak when y<0, else delta-streak"):

      - sign_down: latest < 0 -- count consecutive trailing negative values.
      - delta_accel / delta_decel: latest > 0 -- count consecutive trailing
        month-over-month deltas (each requires both endpoints > 0) that all
        share the same sign as the most recent delta.

    A `None` in `history` is a hard stop for both scans -- it stands for a
    missing observation *or* the far side of a no_yoy_across break. Callers
    (build.py) are responsible for slicing/nulling the history at any break
    seam before calling this; this module has no notion of breaks itself, by
    design (see module docstring).

    Returns (0, None) when there is nothing to report (n < 2, per spec: the
    streak clause only appears at n>=2). The returned n is the TRUE count,
    uncapped -- generate_takeaway is responsible for the ">24 -> 24 个月以上"
    display substitution, so this function keeps the real number available to
    any other caller that might want it.
    """
    if not history or history[-1] is None:
        return 0, None
    latest = history[-1]

    if latest < 0:
        n = 0
        for v in reversed(history):
            if v is None or v >= 0:
                break
            n += 1
        return (n, "sign_down") if n >= 2 else (0, None)

    if latest == 0:
        return 0, None  # 持平 has no streak concept in the spec

    # delta-streak: walk backward building each month's delta, but only while
    # both endpoints of that delta are strictly positive ("positive both").
    deltas: list[float] = []
    for i in range(len(history) - 1, 0, -1):
        a, b = history[i], history[i - 1]
        if a is None or b is None or a <= 0 or b <= 0:
            break
        deltas.append(round(a - b, 6))

    if len(deltas) < 2:
        return 0, None

    direction = None
    n = 0
    for d in deltas:
        if d == 0:
            break
        sign = 1 if d > 0 else -1
        if direction is None:
            direction = sign
        elif sign != direction:
            break
        n += 1

    if n < 2:
        return 0, None
    return n, ("delta_accel" if direction > 0 else "delta_decel")


_STREAK_TAIL = {"sign_down": "同比下降", "delta_decel": "放缓", "delta_accel": "加快"}


def _streak_suffix(streak: int, streak_kind: str | None) -> str:
    if streak_kind is None or streak < 2:
        return ""
    if streak_kind not in _STREAK_TAIL:
        raise ValueError(f"unknown streak_kind: {streak_kind!r}")
    count_phrase = "24 个月以上" if streak > 24 else f"{streak} 个月"
    return _join("，连续", count_phrase, _STREAK_TAIL[streak_kind])


# -- the input contract ---------------------------------------------------------


@dataclass(frozen=True)
class TakeawayInput:
    """Already-resolved scalars for one series' headline. build.py is
    responsible for picking which caliber (single vs ytd) these numbers come
    from, resolving `prev_yoy` to the correct comparable prior period
    (respecting Jan-Feb / YTD-year-reset / break seams), and computing the
    streak. See module docstring for the division of labor.
    """

    name_zh: str
    verb: str  # "上涨" | "增长" -- see choose_verb()
    period_label_zh: str  # e.g. "2026 年 5 月" / "2026 年 2 季度"; unused when is_ytd_only
    latest_yoy: float | None  # the sign-matrix value y (m_yoy or ytd_yoy, whichever caliber build.py chose)
    prev_yoy: float | None = None  # y at the comparable prior period; None => "missing previous"
    real_yoy: float | None = None  # triggers the quarterly-income pattern whenever this is not None
    freq: str = "M"  # "M" | "Q" | "A" -- the CONFIRMED shape of latest_yoy/prev_yoy's period(s) (build.py
    # derives this from _period_shape, not a series' nominal declared freq -- prev_yoy, when present, is
    # guaranteed same-shape as latest_yoy, so this is always a legitimate comparison cadence, never a guess)
    is_jan_feb: bool = False  # latest observation carries flags:["jan_feb"]
    is_break_first: bool = False  # previous is across a no_yoy_across break, or this is a new id's first obs
    is_ytd_only: bool = False  # series' only caliber is "ytd" AND freq=="M" (e.g. FAI) -- switches to "1-{M} 月...累计" phrasing
    ytd_month: int | None = None  # the M in "1-{M} 月"; required when is_ytd_only
    streak: int = 0  # from compute_streak(); 0/ignored if streak_kind is None
    streak_kind: str | None = None  # "sign_down" | "delta_accel" | "delta_decel" | None


JAN_FEB_PREFIX = "1-2 月合并统计，"

# The spec's own templates only ever write out "较上月" (monthly). Real catalog
# data isn't all monthly, though: ~20 real series (nbs-income-median,
# nbs-consumption-expenditure-*, nbs-income-wage, ...) are freq=="Q" with
# calibers==["ytd"] and no real_yoy, so they hit neither the quarterly-income
# pattern nor (after the shape-based guard on is_ytd_only, see build.py) the
# YTD-only pattern -- they fall through to this plain sign-matrix branch. Since
# "较上月" would be factually wrong there (comparing consecutive quarters, not
# months), the reference word is chosen by `inp.freq` -- which build.py
# derives from the CONFIRMED shape of the compared periods (_period_shape),
# not the series' nominal freq, so this stays correct even for the annual-
# supplement-vs-annual-supplement comparisons within an otherwise quarterly
# series (uses "上年", not "上季度", there). This isn't a new template shape,
# just the obvious freq-correct word in the existing "较{...}" slot the spec
# already established for the monthly case.
_PREV_PERIOD_WORD = {"M": "上月", "Q": "上季度", "A": "上年"}


def _break_first_sentence(inp: TakeawayInput) -> str:
    y = inp.latest_yoy
    if y > 0:
        word, val = inp.verb, y
    elif y < 0:
        word, val = "下降", -y
    else:
        word, val = "持平", None
    body = _join(inp.name_zh, word) if val is None else _join(inp.name_zh, word, _pct(val))
    return _join("口径调整后首期数据：", body, "（与旧口径不可比）")


def _quarterly_income_sentence(inp: TakeawayInput) -> str:
    y, r = inp.latest_yoy, inp.real_yoy
    nominal_word = "增长" if y >= 0 else "下降"
    real_word = "增长" if r >= 0 else "下降"
    nominal = _join(inp.period_label_zh, inp.name_zh, "同比名义" + nominal_word, _pct(y if y >= 0 else -y))
    real = _join("实际" + real_word, _pct(r if r >= 0 else -r))
    return _join(nominal, "，", real)


def _sign_matrix_sentence(inp: TakeawayInput, anchor: str, ref: str | None) -> str:
    y = inp.latest_yoy
    yp = inp.prev_yoy if ref is not None else None  # no comparable "previous" label => no comparison at all

    if y > 0:
        base = _join(anchor, "同比", inp.verb, _pct(y))
    elif y < 0:
        base = _join(anchor, "同比下降", _pct(-y))
    else:
        return _join(anchor, "同比持平")  # y==0: terminal, no template given for a further trend clause

    if yp is None:
        return base  # "missing previous: plain statement, no comparison clause"

    if yp > 0 and y < 0:
        return _join(anchor, "同比由升转降，下降", _pct(-y))
    if yp < 0 and y > 0:
        return _join(anchor, "同比由降转升，", inp.verb, _pct(y))
    if yp == 0:
        return base  # prev exactly flat: not a same-sign pair, not a flip either -- spec gives no template, stay conservative

    d = round(y - yp, 6)
    if d == 0:
        return _join(base, "，", _join("与", ref, "持平"))

    is_price = inp.verb == "上涨"
    if y > 0:  # positive both (yp>0 too, since the flip/zero cases above already returned)
        noun = "涨幅" if is_price else "增速"
        dirword = ("扩大" if d > 0 else "收窄") if is_price else ("加快" if d > 0 else "放缓")
    else:  # negative both
        noun = "降幅"
        dirword = "收窄" if d > 0 else "扩大"  # d>0 => |y| shrank => narrowing
    trend = _join(f"{noun}较", ref, dirword, _pp(abs(d)))
    return _join(base, "，", trend)


def generate_takeaway(inp: TakeawayInput) -> str:
    """Render inp into exactly one of the task spec's template patterns. See
    the module docstring for the division of labor with build.py and the two
    typography decisions (dash convention, quarter numerals) flagged there."""
    if inp.latest_yoy is None:
        raise ValueError("generate_takeaway requires a non-null latest_yoy -- callers should not invoke this for a period with nothing published yet")

    if inp.is_break_first:
        # "never compare across" -- break-first is fully self-contained: no
        # Jan-Feb prefix, no streak, no trend clause, regardless of what
        # other flags happen to also be set.
        return _assert_conservative(_break_first_sentence(inp))

    prefix = JAN_FEB_PREFIX if inp.is_jan_feb else ""

    # The task spec conditions this pattern only on "when real_yoy present" --
    # no freq check. An earlier draft added "freq=='Q'" as extra caution, but
    # that turned out actively wrong: a real income series' annual-supplement
    # observations (bare "YYYY" periods, see build.py's _period_shape) still
    # legitimately carry real_yoy and should still get this template -- the
    # sentence reads fine regardless of period shape, since it only ever
    # interpolates the already-formatted period_label_zh, never parses it.
    if inp.real_yoy is not None:
        return _assert_conservative(_join(prefix, _quarterly_income_sentence(inp)))

    if inp.is_ytd_only:
        if not inp.ytd_month:
            raise ValueError("is_ytd_only requires ytd_month")
        anchor = _join(f"1-{inp.ytd_month} 月", inp.name_zh, "累计")
        # YTD resets every January; the year's first cumulative print (M==2,
        # since standalone January is never published for a ytd-only series)
        # has no "1-1 月" to reference -- ref=None collapses to the
        # "missing previous" branch inside _sign_matrix_sentence regardless of
        # whatever prev_yoy the caller passed in (defensive: this invariant
        # should already hold by construction, since there is no such data).
        ref = f"1-{inp.ytd_month - 1} 月" if inp.ytd_month > 2 else None
    else:
        anchor = _join(inp.period_label_zh, inp.name_zh)
        ref = _PREV_PERIOD_WORD.get(inp.freq, "上月")

    body = _sign_matrix_sentence(inp, anchor, ref)
    # Streaks are an explicitly monthly narrative ("连续 N 个月...") -- build.py
    # is expected to only ever pass a nonzero streak for freq=="M" series, but
    # guard here too since this module owns the wording.
    suffix = _streak_suffix(inp.streak, inp.streak_kind) if inp.freq == "M" else ""
    text = _join(prefix, body, suffix)
    return _assert_conservative(text)
