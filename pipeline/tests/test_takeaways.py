"""Tests for pipeline/takeaways.py -- the conservative Chinese takeaway generator.

Thorough on the sign matrix and its edge cases per the task spec: every branch
gets an exact-string assertion (so the Chinese wording itself is pinned, not
just "a string came back"), plus a pangu-spacing regex check applied to every
generated example as an independent safety net on top of the hand-verified
exact strings.
"""
from __future__ import annotations

import re

import pytest

from pipeline.takeaways import (
    BANNED_SUBSTRINGS,
    LevelTakeawayInput,
    TakeawayInput,
    _assert_conservative,
    _join,
    choose_verb,
    compute_streak,
    generate_level_takeaway,
    generate_takeaway,
)

# -- pangu-spacing safety net (regex, independent of the hand-verified exact strings) --

_CJK = "一-鿿㐀-䶿豈-﫿"
# Widened from digits-only to Latin-or-digit (matching DATA-CONTRACT §12's own
# "CJK and adjacent Latin/digits" wording) once catalog `name_short` values
# could carry bare Latin letters ("CPI", "PPI", "M1", "M2", "GDP", "制造业
# PMI", ...) -- a digit-only safety net would have missed exactly the "PMI为"
# regression this widening was written to catch.
_CJK_TOUCHING_LATIN_OR_DIGIT_RE = re.compile(rf"[{_CJK}][A-Za-z0-9]|[A-Za-z0-9][{_CJK}]")
_SPACE_BEFORE_PERCENT_RE = re.compile(r"\d\s+%")


def assert_pangu_ok(text: str) -> None:
    assert not _CJK_TOUCHING_LATIN_OR_DIGIT_RE.search(text), f"missing pangu space (CJK<->Latin/digit) in: {text!r}"
    assert not _SPACE_BEFORE_PERCENT_RE.search(text), f"unwanted space before %% in: {text!r}"


def _base(**overrides) -> TakeawayInput:
    fields = dict(
        name_zh="测试系列",
        verb="增长",
        period_label_zh="2026 年 5 月",
        latest_yoy=5.9,
        prev_yoy=5.1,
    )
    fields.update(overrides)
    return TakeawayInput(**fields)


# -- sign matrix: positive both -------------------------------------------------


def test_positive_accelerating():
    text = generate_takeaway(_base(latest_yoy=5.9, prev_yoy=5.1))
    assert text == "2026 年 5 月测试系列同比增长 5.9%，增速较上月加快 0.8 个百分点"
    assert_pangu_ok(text)


def test_positive_decelerating():
    text = generate_takeaway(_base(latest_yoy=4.0, prev_yoy=5.9))
    assert text == "2026 年 5 月测试系列同比增长 4.0%，增速较上月放缓 1.9 个百分点"
    assert_pangu_ok(text)


def test_price_type_accelerating_uses_expand_wording():
    text = generate_takeaway(_base(verb="上涨", latest_yoy=2.5, prev_yoy=2.1))
    assert text == "2026 年 5 月测试系列同比上涨 2.5%，涨幅较上月扩大 0.4 个百分点"
    assert_pangu_ok(text)


def test_price_type_decelerating_uses_narrow_wording():
    text = generate_takeaway(_base(verb="上涨", latest_yoy=2.1, prev_yoy=2.5))
    assert text == "2026 年 5 月测试系列同比上涨 2.1%，涨幅较上月收窄 0.4 个百分点"
    assert_pangu_ok(text)


# -- sign matrix: negative both --------------------------------------------------


def test_negative_narrowing():
    text = generate_takeaway(_base(latest_yoy=-3.0, prev_yoy=-5.0))
    assert text == "2026 年 5 月测试系列同比下降 3.0%，降幅较上月收窄 2.0 个百分点"
    assert_pangu_ok(text)


def test_negative_widening():
    text = generate_takeaway(_base(latest_yoy=-5.0, prev_yoy=-3.0))
    assert text == "2026 年 5 月测试系列同比下降 5.0%，降幅较上月扩大 2.0 个百分点"
    assert_pangu_ok(text)


# -- sign flips -------------------------------------------------------------------


def test_flip_positive_to_negative():
    text = generate_takeaway(_base(latest_yoy=-0.6, prev_yoy=5.1))
    assert text == "2026 年 5 月测试系列同比由升转降，下降 0.6%"
    assert_pangu_ok(text)


def test_flip_negative_to_positive():
    text = generate_takeaway(_base(latest_yoy=0.6, prev_yoy=-5.1))
    assert text == "2026 年 5 月测试系列同比由降转升，增长 0.6%"
    assert_pangu_ok(text)


def test_flip_negative_to_positive_price_type():
    text = generate_takeaway(_base(verb="上涨", latest_yoy=0.6, prev_yoy=-5.1))
    assert text == "2026 年 5 月测试系列同比由降转升，上涨 0.6%"
    assert_pangu_ok(text)


# -- zero / equal -----------------------------------------------------------------


def test_latest_exactly_zero_is_terminal_regardless_of_prev():
    text = generate_takeaway(_base(latest_yoy=0.0, prev_yoy=5.0))
    assert text == "2026 年 5 月测试系列同比持平"
    assert_pangu_ok(text)

    text_no_prev = generate_takeaway(_base(latest_yoy=0.0, prev_yoy=None))
    assert text_no_prev == "2026 年 5 月测试系列同比持平"


def test_delta_zero_positive_both():
    text = generate_takeaway(_base(latest_yoy=5.9, prev_yoy=5.9))
    assert text == "2026 年 5 月测试系列同比增长 5.9%，与上月持平"
    assert_pangu_ok(text)


def test_delta_zero_negative_both():
    text = generate_takeaway(_base(latest_yoy=-5.9, prev_yoy=-5.9))
    assert text == "2026 年 5 月测试系列同比下降 5.9%，与上月持平"
    assert_pangu_ok(text)


def test_prev_exactly_zero_has_no_template_stays_conservative():
    """Not a flip (y stays positive), not a same-sign pair either -- the spec
    gives no template for "previous was exactly flat, then grew"; the
    conservative choice is to drop the trend clause rather than invent one."""
    text = generate_takeaway(_base(latest_yoy=3.0, prev_yoy=0.0))
    assert text == "2026 年 5 月测试系列同比增长 3.0%"
    assert_pangu_ok(text)


# -- missing previous / break-adjacent --------------------------------------------


def test_missing_previous_is_a_plain_statement():
    text = generate_takeaway(_base(latest_yoy=5.9, prev_yoy=None))
    assert text == "2026 年 5 月测试系列同比增长 5.9%"
    assert_pangu_ok(text)


def test_break_first_positive():
    text = generate_takeaway(_base(is_break_first=True, latest_yoy=5.9, prev_yoy=5.1))
    assert text == "口径调整后首期数据：测试系列增长 5.9%（与旧口径不可比）"
    assert_pangu_ok(text)


def test_break_first_negative():
    text = generate_takeaway(_base(is_break_first=True, latest_yoy=-2.3, verb="上涨"))
    assert text == "口径调整后首期数据：测试系列下降 2.3%（与旧口径不可比）"
    assert_pangu_ok(text)


def test_break_first_ignores_streak_and_jan_feb():
    """'Never compare across' -- break-first is fully self-contained even if
    the caller (incorrectly) also sets other flags."""
    text = generate_takeaway(
        _base(is_break_first=True, is_jan_feb=True, latest_yoy=5.9, streak=5, streak_kind="delta_accel")
    )
    assert text == "口径调整后首期数据：测试系列增长 5.9%（与旧口径不可比）"


# -- Jan-Feb ------------------------------------------------------------------


def test_jan_feb_suffix():
    """The period is stated exactly once (in period_label_zh); the Jan-Feb
    caveat is a trailing parenthetical, not a leading prefix that restates
    the span a second time (2026-07-08 typography fix)."""
    text = generate_takeaway(
        _base(is_jan_feb=True, period_label_zh="2026 年 1-2 月", latest_yoy=4.0, prev_yoy=None)
    )
    assert text == "2026 年 1-2 月测试系列同比增长 4.0%（1-2 月合并统计）"
    assert_pangu_ok(text)


def test_jan_feb_suffix_comes_after_the_streak_clause():
    text = generate_takeaway(
        _base(
            is_jan_feb=True, period_label_zh="2026 年 1-2 月", latest_yoy=-3.0, prev_yoy=-3.0,
            streak=2, streak_kind="sign_down",
        )
    )
    assert text == "2026 年 1-2 月测试系列同比下降 3.0%，与上月持平，连续 2 个月同比下降（1-2 月合并统计）"
    assert_pangu_ok(text)


# -- streaks --------------------------------------------------------------------


def test_streak_sign_down_shown_at_n_equal_2():
    text = generate_takeaway(_base(latest_yoy=-3.0, prev_yoy=-3.0, streak=2, streak_kind="sign_down"))
    assert text == "2026 年 5 月测试系列同比下降 3.0%，与上月持平，连续 2 个月同比下降"
    assert_pangu_ok(text)


def test_streak_below_2_is_not_shown():
    text = generate_takeaway(_base(latest_yoy=-3.0, prev_yoy=-5.0, streak=1, streak_kind="sign_down"))
    assert "连续" not in text


def test_streak_delta_decel():
    text = generate_takeaway(_base(latest_yoy=4.0, prev_yoy=5.9, streak=3, streak_kind="delta_decel"))
    assert text == "2026 年 5 月测试系列同比增长 4.0%，增速较上月放缓 1.9 个百分点，连续 3 个月放缓"
    assert_pangu_ok(text)


def test_streak_delta_accel():
    text = generate_takeaway(_base(latest_yoy=5.9, prev_yoy=5.1, streak=4, streak_kind="delta_accel"))
    assert text == "2026 年 5 月测试系列同比增长 5.9%，增速较上月加快 0.8 个百分点，连续 4 个月加快"
    assert_pangu_ok(text)


def test_streak_cap_at_24_not_yet_triggered():
    text = generate_takeaway(_base(latest_yoy=-3.0, prev_yoy=-3.0, streak=24, streak_kind="sign_down"))
    assert "连续 24 个月同比下降" in text
    assert "以上" not in text


def test_streak_cap_at_24_triggered_above_24():
    text = generate_takeaway(_base(latest_yoy=-3.0, prev_yoy=-3.0, streak=30, streak_kind="sign_down"))
    assert "连续 24 个月以上同比下降" in text
    assert_pangu_ok(text)


def test_streak_only_applies_to_monthly_freq():
    text = generate_takeaway(
        _base(freq="Q", latest_yoy=-3.0, prev_yoy=-3.0, streak=5, streak_kind="sign_down", period_label_zh="2026 年二季度")
    )
    assert "连续" not in text


# -- ytd-only caliber (e.g. FAI) --------------------------------------------------


def test_ytd_only_accelerating():
    text = generate_takeaway(
        _base(name_zh="固定资产投资", is_ytd_only=True, ytd_month=5, latest_yoy=4.0, prev_yoy=3.5)
    )
    assert text == "1-5 月固定资产投资累计同比增长 4.0%，增速较 1-4 月加快 0.5 个百分点"
    assert_pangu_ok(text)


def test_ytd_only_decelerating():
    text = generate_takeaway(
        _base(name_zh="固定资产投资", is_ytd_only=True, ytd_month=6, latest_yoy=3.5, prev_yoy=4.0)
    )
    assert text == "1-6 月固定资产投资累计同比增长 3.5%，增速较 1-5 月放缓 0.5 个百分点"
    assert_pangu_ok(text)


def test_ytd_only_first_print_of_year_has_no_comparison():
    """M==2 is the year's first cumulative print (no standalone January) --
    there is no "1-1 月" to compare against, so this must read as a plain
    statement even if a stray prev_yoy were supplied."""
    text = generate_takeaway(
        _base(name_zh="固定资产投资", is_ytd_only=True, ytd_month=2, latest_yoy=4.0, prev_yoy=99.0)
    )
    assert text == "1-2 月固定资产投资累计同比增长 4.0%"
    assert_pangu_ok(text)


def test_ytd_only_negative():
    text = generate_takeaway(
        _base(name_zh="固定资产投资", is_ytd_only=True, ytd_month=8, latest_yoy=-1.2, prev_yoy=None)
    )
    assert text == "1-8 月固定资产投资累计同比下降 1.2%"
    assert_pangu_ok(text)


# -- quarterly, ytd-caliber, but NOT the income pattern (no real_yoy) -------------


def test_quarterly_non_income_ytd_series_uses_last_quarter_wording():
    """~20 real catalog series are freq=='Q' with calibers==['ytd'] and no
    real_yoy (income/consumption sub-components, e.g. nbs-income-median).
    These must not crash (no month number exists to plug into the YTD-only
    "1-{M} 月" phrasing) and must not say "较上月" (wrong -- these are
    quarters, not months)."""
    text = generate_takeaway(
        _base(
            name_zh="测试_人均可支配收入中位数",
            period_label_zh="2026 年二季度",
            freq="Q",
            is_ytd_only=False,
            latest_yoy=5.0,
            prev_yoy=4.0,
        )
    )
    assert text == "2026 年二季度测试_人均可支配收入中位数同比增长 5.0%，增速较上季度加快 1.0 个百分点"
    assert_pangu_ok(text)


def test_quarterly_income_pattern_still_takes_priority_when_real_yoy_present():
    """Same freq=='Q' shape as above, but real_yoy present -- the income
    pattern must still win over the generic quarterly fallback."""
    text = generate_takeaway(
        _base(
            name_zh="全国居民人均可支配收入",
            period_label_zh="2026 年二季度",
            freq="Q",
            latest_yoy=5.2,
            real_yoy=3.8,
        )
    )
    assert "实际增长" in text
    assert "较上季度" not in text


# -- quarterly income pattern -----------------------------------------------------


def test_quarterly_income_pattern():
    text = generate_takeaway(
        _base(
            name_zh="全国居民人均可支配收入",
            period_label_zh="2026 年二季度",
            freq="Q",
            latest_yoy=5.2,
            real_yoy=3.8,
        )
    )
    assert text == "2026 年二季度全国居民人均可支配收入同比名义增长 5.2%，实际增长 3.8%"
    assert_pangu_ok(text)


def test_quarterly_income_pattern_negative_real_growth():
    text = generate_takeaway(
        _base(
            name_zh="全国居民人均可支配收入",
            period_label_zh="2026 年二季度",
            freq="Q",
            latest_yoy=1.0,
            real_yoy=-0.5,
        )
    )
    assert text == "2026 年二季度全国居民人均可支配收入同比名义增长 1.0%，实际下降 0.5%"
    assert_pangu_ok(text)


# -- precision: never round differently than published ---------------------------


def test_two_decimal_value_is_preserved():
    text = generate_takeaway(_base(latest_yoy=4.55, prev_yoy=4.0))
    assert "同比增长 4.55%" in text
    assert "0.55 个百分点" in text  # delta also carries 2 decimals here
    assert_pangu_ok(text)


def test_whole_number_still_shows_one_decimal():
    text = generate_takeaway(_base(latest_yoy=5.0, prev_yoy=None))
    assert "同比增长 5.0%" in text


# -- missing latest is a caller contract violation --------------------------------


def test_missing_latest_yoy_raises():
    with pytest.raises(ValueError):
        generate_takeaway(_base(latest_yoy=None))


# -- banned language guard ---------------------------------------------------------


def test_banned_substrings_are_rejected():
    for token in BANNED_SUBSTRINGS:
        with pytest.raises(ValueError):
            _assert_conservative(f"某某{token}某某")


def test_generated_examples_never_contain_banned_language():
    examples = [
        generate_takeaway(_base(latest_yoy=5.9, prev_yoy=5.1)),
        generate_takeaway(_base(latest_yoy=-5.9, prev_yoy=-3.0)),
        generate_takeaway(_base(is_break_first=True, latest_yoy=1.0)),
    ]
    for text in examples:
        for token in BANNED_SUBSTRINGS:
            assert token not in text


# -- choose_verb ------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"verb": "上涨", "value_type": "level"}, "上涨"),
        ({"verb": "增长", "value_type": "index"}, "增长"),
        ({"value_type": "index"}, "上涨"),
        ({"value_type": "mom_pct"}, "上涨"),
        ({"value_type": "level"}, "增长"),
        ({"value_type": "rate_pct"}, "增长"),
        ({"value_type": "count"}, "增长"),
        ({}, "增长"),
        ({"verb": "bogus", "value_type": "level"}, "增长"),  # invalid verb value ignored, falls to heuristic
    ],
)
def test_choose_verb(entry, expected):
    assert choose_verb(entry) == expected


# -- compute_streak ---------------------------------------------------------------


def test_compute_streak_sign_down_basic():
    assert compute_streak([-1.0, -2.0, -3.0]) == (3, "sign_down")


def test_compute_streak_sign_down_stops_at_positive():
    assert compute_streak([5.0, -1.0, -2.0]) == (2, "sign_down")


def test_compute_streak_sign_down_stops_at_none():
    assert compute_streak([-5.0, None, -1.0, -2.0]) == (2, "sign_down")


def test_compute_streak_sign_down_single_month_is_not_a_streak():
    assert compute_streak([5.0, -1.0]) == (0, None)


def test_compute_streak_delta_accel():
    assert compute_streak([1.0, 2.0, 4.0, 7.0]) == (3, "delta_accel")


def test_compute_streak_delta_decel():
    assert compute_streak([10.0, 8.0, 7.0, 6.5]) == (3, "delta_decel")


def test_compute_streak_delta_direction_reversal_stops_the_count():
    assert compute_streak([5.0, 3.0, 4.0]) == (0, None)


def test_compute_streak_delta_requires_positive_endpoints():
    assert compute_streak([-1.0, 2.0, 4.0]) == (0, None)  # only one qualifying delta (2.0->4.0)


def test_compute_streak_latest_zero_has_no_streak():
    assert compute_streak([5.0, 3.0, 0.0]) == (0, None)


def test_compute_streak_empty_or_single_history():
    assert compute_streak([]) == (0, None)
    assert compute_streak([5.0]) == (0, None)
    assert compute_streak([None]) == (0, None)


def test_compute_streak_returns_true_count_uncapped():
    """Capping to '24 个月以上' is a display concern (generate_takeaway), not
    compute_streak's -- it must return the real count."""
    history = [-1.0] * 30
    assert compute_streak(history) == (30, "sign_down")


# -- pangu join helper (direct) -----------------------------------------------------


def test_join_no_space_between_two_cjk_fragments():
    assert _join("较", "上月") == "较上月"


def test_join_inserts_space_at_cjk_digit_seam():
    assert _join("较", "1-4 月") == "较 1-4 月"


def test_join_inserts_space_between_word_and_number():
    assert _join("加快", "3") == "加快 3"


def test_join_no_space_around_full_width_punctuation():
    assert _join("统计，", "2026 年") == "统计，2026 年"
    assert _join("5.9%", "，", "增速") == "5.9%，增速"


# -- level-only takeaway (no published YoY, e.g. PMI) ------------------------------


def _level_base(**overrides) -> LevelTakeawayInput:
    fields = dict(
        name_zh="制造业 PMI",
        period_label_zh="2026 年 6 月",
        latest_level=48.6,
        prev_level=48.0,
    )
    fields.update(overrides)
    return LevelTakeawayInput(**fields)


def test_level_takeaway_rising():
    text = generate_level_takeaway(_level_base(latest_level=48.6, prev_level=48.0))
    assert text == "2026 年 6 月制造业 PMI 为 48.6%，比上月上升 0.6 个点"
    assert_pangu_ok(text)


def test_level_takeaway_falling():
    text = generate_level_takeaway(_level_base(latest_level=52.0, prev_level=53.0))
    assert text == "2026 年 6 月制造业 PMI 为 52.0%，比上月回落 1.0 个点"
    assert_pangu_ok(text)


def test_level_takeaway_flat():
    text = generate_level_takeaway(_level_base(latest_level=49.0, prev_level=49.0))
    assert text == "2026 年 6 月制造业 PMI 为 49.0%，与上月持平"
    assert_pangu_ok(text)


def test_level_takeaway_crossing_50_upward_appends_above_the_line():
    text = generate_level_takeaway(_level_base(latest_level=50.2, prev_level=49.5))
    assert text == "2026 年 6 月制造业 PMI 为 50.2%，比上月上升 0.7 个点，位于荣枯线上方"
    assert_pangu_ok(text)


def test_level_takeaway_crossing_50_downward_appends_below_the_line():
    text = generate_level_takeaway(_level_base(latest_level=49.6, prev_level=50.3))
    assert text == "2026 年 6 月制造业 PMI 为 49.6%，比上月回落 0.7 个点，位于荣枯线下方"
    assert_pangu_ok(text)


def test_level_takeaway_near_50_without_crossing_still_appends_the_line_note():
    """Same side both periods (no crossing) but the latest print sits within
    0.5 of 50 -- the 荣枯线 note fires on proximity alone."""
    text = generate_level_takeaway(_level_base(latest_level=50.3, prev_level=50.6))
    assert text == "2026 年 6 月制造业 PMI 为 50.3%，比上月回落 0.3 个点，位于荣枯线上方"
    assert_pangu_ok(text)


def test_level_takeaway_first_obs_has_no_comparison_or_line_note():
    """No previous print to compare against -- a plain statement, same
    "missing previous" conservatism as the sign-matrix template. Also away
    from 50, so no 荣枯线 clause either (there is nothing to be "near" or
    "cross" relative to without both sides having some data point)."""
    text = generate_level_takeaway(_level_base(latest_level=47.0, prev_level=None))
    assert text == "2026 年 6 月制造业 PMI 为 47.0%"
    assert_pangu_ok(text)


def test_level_takeaway_first_obs_near_50_still_gets_the_line_note():
    """A first observation CAN still be "near" 50 on its own -- only
    "crossing" requires a previous print to compare against."""
    text = generate_level_takeaway(_level_base(latest_level=50.1, prev_level=None))
    assert text == "2026 年 6 月制造业 PMI 为 50.1%，位于荣枯线上方"
    assert_pangu_ok(text)


def test_level_takeaway_bare_unit_omits_percent_sign():
    text = generate_level_takeaway(
        _level_base(latest_level=48.6, prev_level=48.0, is_percent_unit=False)
    )
    assert text == "2026 年 6 月制造业 PMI 为 48.6，比上月上升 0.6 个点"
    assert_pangu_ok(text)


def test_level_takeaway_boom_bust_line_can_be_disabled():
    text = generate_level_takeaway(
        _level_base(latest_level=50.2, prev_level=49.5, boom_bust_line=None)
    )
    assert text == "2026 年 6 月制造业 PMI 为 50.2%，比上月上升 0.7 个点"
    assert "荣枯线" not in text
