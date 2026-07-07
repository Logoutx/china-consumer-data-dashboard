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

Two typography decisions the lead resolved on 2026-07-08 (both used to read as
open questions in this docstring; recorded here now as settled conventions,
not flagged ambiguities):

  - **Period ranges always use the half-width hyphen**, never an em dash. An
    earlier draft had build.py's period_label_zh emit an em dash ("2026 年
    1—5 月", following DATA-CONTRACT §12's then-current worked example)
    while this module's own YTD-only anchor used a plain hyphen ("1-{M} 月")
    -- two conventions live in the same sentence depending on which half
    built it. Both DATA-CONTRACT §12 and build.py's _period_label_zh now use
    the hyphen uniformly ("2026 年 1-5 月", "2026 年 1-2 月"), matching this
    module's own YTD-only phrasing, per the owner's global typography rule
    that ranges render with a hyphen.
  - **Quarters render with the conventional Chinese ordinal** ("二季度"), not
    an Arabic digit ("2 季度"). This was DATA-CONTRACT §12's own worked
    example all along ("2026 年一季度"); build.py's _period_label_zh had
    drifted to an Arabic digit under an earlier "Arabic numerals always win"
    reading of §12 point 2. Quarter ordinals are treated as a closed,
    conventional set -- like the colloquial small numbers (两个, 三五个) §12
    point 2 already carves out -- so this is a deliberate *exception* to the
    Arabic-numerals rule, not a violation of it; see §12's own text.
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


def _is_latin_or_digit(ch: str) -> bool:
    """DATA-CONTRACT §12's pangu rule is "CJK and adjacent Latin/digits", not
    just digits -- this module only ever needed the digit half until catalog
    `name_short` values could contain bare Latin letters (`CPI`, `PPI`, `M1`,
    `M2`, `GDP`, `制造业 PMI`, ...). `str.isalnum()` alone would also match a
    CJK character (Python's Unicode alnum includes ideographs), so this
    additionally requires `isascii()` to scope it to the Latin-letter/digit
    half of the rule specifically -- `_is_cjk` already owns the CJK half."""
    return ch.isascii() and ch.isalnum()


def _needs_pangu_space(left: str, right: str) -> bool:
    return (_is_cjk(left) and _is_latin_or_digit(right)) or (_is_latin_or_digit(left) and _is_cjk(right))


def _join(*parts: str) -> str:
    """Concatenate fragments, inserting one half-width space at any CJK<->ASCII
    letter/digit seam between two consecutive fragments. Each fragment is
    assumed to already be correctly spaced *internally* (e.g. "1-4 月",
    "2026 年 5 月", "制造业 PMI") -- this only fixes the boundary where two
    fragments meet, which is the seam that's actually error-prone to get
    right by hand (a fixed Chinese word might be followed by either "上月"
    (CJK, no space wanted), "1-4 月" (digit-first, space wanted), or a
    catalog `name_short` like "PMI"/"CPI" (Latin-first, space wanted),
    depending on branch)."""
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


def _points(value: float) -> str:
    """Point-difference unit for a diffusion index (PMI and friends): "个点",
    never "个百分点" -- a diffusion index's own level is already expressed on
    a points/percent scale, so a change in it is a change of N points, not an
    N-percentage-point change in some other rate. Always used for the delta
    in generate_level_takeaway(), regardless of whether the level itself is
    rendered with a "%" suffix (see _level_value)."""
    return f"{_fmt(value)} 个点"


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

    name_zh: str  # build.py resolves this to the catalog's name_short when present, else name_zh (see build.py)
    verb: str  # "上涨" | "增长" -- see choose_verb()
    period_label_zh: str  # e.g. "2026 年 5 月" / "2026 年二季度" (conventional ordinal, not "2 季度"); unused when is_ytd_only
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


JAN_FEB_SUFFIX = "（1-2 月合并统计）"
# Was a PREFIX ("1-2 月合并统计，", read before the sentence) through 2026-07-07.
# The prefix duplicated the period: "1-2 月合并统计，2026 年 1-2 月{name}…" states
# the Jan-Feb span twice (once in the prefix, once in period_label_zh) and, before
# the hyphen-unification above, mixed the prefix's hyphen with period_label_zh's
# then-em-dash in the same sentence. Moved to a trailing parenthetical caveat
# 2026-07-08 (mirrors how _break_first_sentence appends its own "（与旧口径不可比）"
# caveat at the end): the period is now stated exactly once, in period_label_zh.

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
    the module docstring for the two resolved typography conventions (hyphen
    period ranges, conventional-ordinal quarters)."""
    if inp.latest_yoy is None:
        raise ValueError("generate_takeaway requires a non-null latest_yoy -- callers should not invoke this for a period with nothing published yet")

    if inp.is_break_first:
        # "never compare across" -- break-first is fully self-contained: no
        # Jan-Feb suffix, no streak, no trend clause, regardless of what
        # other flags happen to also be set.
        return _assert_conservative(_break_first_sentence(inp))

    jan_feb_suffix = JAN_FEB_SUFFIX if inp.is_jan_feb else ""

    # The task spec conditions this pattern only on "when real_yoy present" --
    # no freq check. An earlier draft added "freq=='Q'" as extra caution, but
    # that turned out actively wrong: a real income series' annual-supplement
    # observations (bare "YYYY" periods, see build.py's _period_shape) still
    # legitimately carry real_yoy and should still get this template -- the
    # sentence reads fine regardless of period shape, since it only ever
    # interpolates the already-formatted period_label_zh, never parses it.
    if inp.real_yoy is not None:
        return _assert_conservative(_join(_quarterly_income_sentence(inp), jan_feb_suffix))

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
    streak_suffix = _streak_suffix(inp.streak, inp.streak_kind) if inp.freq == "M" else ""
    text = _join(body, streak_suffix, jan_feb_suffix)
    return _assert_conservative(text)


# -- level-only takeaway (no published YoY at all, e.g. PMI) ---------------------

# NBS never publishes a same-month YoY for a diffusion index (PMI and
# friends): the level itself IS the headline, and the only legitimate
# comparison is against the immediately preceding print. generate_takeaway's
# whole sign-matrix machinery is YoY-shaped and doesn't apply here, so this is
# a separate, deliberately narrower template -- conservative and arithmetic
# only, exactly like the rest of this module. build.py opts a series into
# this path when it *never* carries a YoY-shaped measure anywhere in its
# history (a structural absence, not a transient break-blocked gap -- see
# build.py's `_is_level_only_series`), so a genuinely YoY-capable series that
# is merely mid-break (CPI, PPI, M1, ...) keeps getting `takeaway: null`
# instead of a fabricated level-only sentence.

_BOOM_BUST_LINE = 50.0  # the PMI 荣枯线 -- >=50 signals expansion, <50 contraction


@dataclass(frozen=True)
class LevelTakeawayInput:
    """Already-resolved scalars for a level-only series' headline (no
    published YoY at all). Mirrors TakeawayInput's shape (build.py resolves
    which caliber/period and the name_short-vs-name_zh choice the same way
    for both), but the sign-matrix fields (`latest_yoy`/`prev_yoy`/...) don't
    apply here -- there is no YoY to compare."""

    name_zh: str  # build.py resolves this to the catalog's name_short when present, else name_zh
    period_label_zh: str  # e.g. "2026 年 6 月"
    latest_level: float  # the level itself (e.g. PMI's own index value)
    prev_level: float | None = None  # level at the immediately preceding print; None => "missing previous"
    is_percent_unit: bool = True  # catalog unit_zh == "%" -> render the level with a "%" suffix; else bare
    boom_bust_line: float | None = _BOOM_BUST_LINE  # None disables the 荣枯线 clause (non-PMI-shaped diffusion index)
    freq: str = "M"  # "M" | "Q" | "A" -- the CONFIRMED shape of latest_level/prev_level's period(s) (mirrors
    # TakeawayInput.freq: build.py derives this from the latest observation's own period shape, not the
    # series' nominal declared freq). Every level-only series in the catalog until 2026-07-08 was PMI-shaped
    # (freq=="M" always), so this defaults to "M" -- every existing caller/test is unaffected. Added when
    # widening the level-only path to GDP-contribution shares (freq=="Q"): the previous-period word must say
    # "比上季度", never a hardcoded "比上月", once a quarterly series can reach this template.
    fall_word: str = "回落"  # "回落" (diffusion index, e.g. PMI: a value receding from a peak) | "下降" (a
    # rate declining, e.g. the unemployment-rate family -- 2026-07-08 rate_pct widening). Default preserves
    # every existing caller's wording unchanged; build.py passes "下降" only for value_type=="rate_pct".
    delta_in_pp: bool = False  # False -> delta rendered in _points() ("个点", diffusion-index convention) |
    # True -> _pp() ("个百分点", the conventional unit for a RATE's month-over-month change, e.g. unemployment).
    # Default preserves every existing caller (PMI, GDP-contribution) unchanged.
    streak: int = 0  # consecutive same-direction month-over-month LEVEL changes, from compute_level_streak() --
    # NOT the same concept as TakeawayInput.streak (which counts YoY deltas); ignored if streak_kind is None.
    streak_kind: str | None = None  # "up" | "down" | None. Unlike the YoY streak (§task spec "n>=2"), this
    # clause only appears at n>=3 -- a noisy month-to-month rate (unemployment) makes a 2-month streak
    # unremarkable, per the lead's 2026-07-08 rate_pct widening decision.


def _level_value(value: float, is_percent: bool) -> str:
    return _pct(value) if is_percent else _fmt(value)


def compute_level_streak(history: list[float | None]) -> tuple[int, str | None]:
    """Count the trailing streak of same-direction month-over-month CHANGES
    in a plain LEVEL series (a rate_pct reading, e.g. the unemployment-rate
    family) -- a different concept from compute_streak(), which scans YoY
    values. There is no "sign_down"/"accel"/"decel" distinction here (a rate
    doesn't have the positive-both gating a YoY growth rate does): just
    whether the level itself has risen, or fallen, for N consecutive prints.

    Returns (0, None) below n==3 -- deliberately a *higher* floor than
    compute_streak's n>=2: an unemployment rate wiggles month to month, so a
    2-month run isn't worth a streak clause (lead decision, 2026-07-08,
    scoped to the rate_pct widening). The returned n is the TRUE count,
    uncapped; generate_level_takeaway applies the same ">24 -> 24 个月以上"
    display substitution used elsewhere in this module.
    """
    if not history or len(history) < 2:
        return 0, None

    deltas: list[float] = []
    for i in range(len(history) - 1, 0, -1):
        a, b = history[i], history[i - 1]
        if a is None or b is None:
            break
        deltas.append(round(a - b, 6))

    if len(deltas) < 3:
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

    if n < 3:
        return 0, None
    return n, ("up" if direction > 0 else "down")


def _level_streak_suffix(streak: int, streak_kind: str | None) -> str:
    if streak_kind is None or streak < 3:
        return ""
    if streak_kind not in ("up", "down"):
        raise ValueError(f"unknown level streak_kind: {streak_kind!r}")
    count_phrase = "24 个月以上" if streak > 24 else f"{streak} 个月"
    direction_word = "上升" if streak_kind == "up" else "下降"
    return _join("，连续", count_phrase, direction_word)


def generate_level_takeaway(inp: LevelTakeawayInput) -> str:
    """"{period}{name}为 {x}，比上月{上升/回落或下降} {d} 个点或百分点", plus
    either a 荣枯线 (50) proximity/crossing note (diffusion index) or a
    same-direction streak clause at n>=3 (rate_pct) -- never both; the
    caller (build.py) only ever populates the one relevant to the series'
    value_type. Conservative, arithmetic only -- states the level and, when
    there is a comparable previous print, its arithmetic change; no
    interpretation of why it moved.

    The 荣枯线 clause fires when the latest value crosses the line relative to
    the previous print (regardless of distance) OR sits within 0.5 of it
    (regardless of crossing) -- either condition alone is worth surfacing;
    neither is required when there is no previous print (a first observation
    can still be "near" 50 on its own, but can't have "crossed" anything).
    """
    body = _join(inp.period_label_zh, inp.name_zh, "为", _level_value(inp.latest_level, inp.is_percent_unit))
    prev_word = _PREV_PERIOD_WORD.get(inp.freq, "上月")

    if inp.prev_level is not None:
        d = round(inp.latest_level - inp.prev_level, 6)
        if d == 0:
            body = _join(body, f"，与{prev_word}持平")
        else:
            word = "上升" if d > 0 else inp.fall_word
            delta_str = _pp(abs(d)) if inp.delta_in_pp else _points(abs(d))
            body = _join(body, f"，比{prev_word}", word, delta_str)

    if inp.boom_bust_line is not None:
        line = inp.boom_bust_line
        crossed = inp.prev_level is not None and (inp.prev_level >= line) != (inp.latest_level >= line)
        near = abs(inp.latest_level - line) <= 0.5
        if crossed or near:
            side = "上方" if inp.latest_level >= line else "下方"
            body = _join(body, "，位于荣枯线", side)

    body = _join(body, _level_streak_suffix(inp.streak, inp.streak_kind))

    return _assert_conservative(body)
