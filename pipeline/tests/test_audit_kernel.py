"""Tests for pipeline/audit/kernel.py -- the ported fuzzy-matching +
tolerance kernel -- including the 3-real-page verification the task spec
asked for (pipeline/fixtures/raw/{nbs_activity,nbs_cpi,pboc_money}/*.html),
covering: a plain 亿元-denominated match, a negative-value (下降-phrased)
match, and the 万亿-scale addition made while porting (see kernel.py's module
docstring).
"""
from __future__ import annotations

from pathlib import Path

from pipeline.audit import kernel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_RAW = REPO_ROOT / "pipeline" / "fixtures" / "raw"


# -- numeric / tolerance -----------------------------------------------------------


def test_numeric_coerces_strings_and_rejects_bool():
    assert kernel.numeric("41,090") == 41090.0
    assert kernel.numeric("--") is None
    assert kernel.numeric(None) is None
    assert kernel.numeric(True) is None
    assert kernel.numeric(3.5) == 3.5


def test_tolerance_grows_with_magnitude():
    assert kernel.tolerance_for(5) == 0.03
    assert kernel.tolerance_for(50) == 0.08
    assert kernel.tolerance_for(500) == 0.2
    assert kernel.tolerance_for(200000) == max(1.0, 200000 * 0.0008)


def test_close_enough_respects_tolerance():
    assert kernel.close_enough(100.0, 100.02)
    assert not kernel.close_enough(100.0, 100.5, tolerance=0.03)


# -- html stripping -----------------------------------------------------------------


def test_strip_html_removes_script_and_style_and_unescapes_entities():
    text = kernel.strip_html(b"<html><style>.a{}</style><script>x=1</script><body>A&amp;B<b>C</b></body></html>")
    assert "script" not in text.lower() or "x=1" not in text
    assert "A&B" in text.replace(" ", "")


def test_compact_text_strips_all_whitespace_and_ideographic_space():
    assert kernel.compact_text("a 　 b\n c") == "abc"


# -- number candidates + 万亿 scale --------------------------------------------------


def test_format_number_candidates_includes_negative_phrasing():
    # format_number_candidates returns a LIST of (scale, candidate) pairs --
    # several candidates share the "1x" scale key, so this must not be
    # collapsed into a dict (which would keep only the last "1x" entry).
    candidate_strings = [value for _, value in kernel.format_number_candidates(-0.6)]
    assert "下降0.6" in candidate_strings
    assert "负0.6" in candidate_strings


def test_direction_aware_negative_stored_matches_a_下降_phrased_page():
    text = "5月份，全国居民消费价格同比下降0.6%。"
    matched, evidence, scale = kernel.source_contains_value(text, "居民消费价格", -0.6)
    assert matched and scale == "1x"
    assert "下降" in evidence


def test_direction_aware_rejects_sign_flipped_positive_against_a_下降_page():
    """The mutation-harness gap this was written for: a stored value of +0.6
    must NOT verify against a page that says 下降0.6% (i.e. -0.6) just
    because the bare substring "0.6" happens to sit right after "下降"."""
    text = "5月份，全国居民消费价格同比下降0.6%。"
    matched, evidence, scale = kernel.source_contains_value(text, "居民消费价格", 0.6)
    assert not matched
    assert evidence is None and scale is None


def test_direction_aware_positive_stored_matches_a_增长_phrased_page():
    text = "1—5月份，测试_社会消费品零售总额206031亿元，同比增长1.4%。"
    matched, evidence, scale = kernel.source_contains_value(text, "测试_社会消费品零售总额", 1.4)
    assert matched and scale == "1x"
    assert "增长" in evidence


def test_direction_aware_rejects_sign_flipped_negative_against_a_增长_page():
    text = "1—5月份，测试_社会消费品零售总额206031亿元，同比增长1.4%。"
    matched, _, _ = kernel.source_contains_value(text, "测试_社会消费品零售总额", -1.4)
    assert not matched


def test_direction_aware_accepts_a_bare_level_with_no_directional_word():
    # An index level statement ("CPI为103.6") carries no verb at all --
    # must not be rejected for "missing" a direction it was never claiming.
    text = "居民消费价格指数(上年同月=100)为103.6。"
    matched, _, _ = kernel.source_contains_value(text, "居民消费价格指数", 103.6)
    assert matched


def test_direction_aware_ignores_收窄_扩大_delta_phrases_as_a_false_signal():
    """涨幅/降幅 收窄/扩大 describe a DELTA's own trend, not the sign of the
    number being matched -- they must never be treated as a directional
    signal (neither positive nor negative), or a positive-value match sitting
    near one would be wrongly rejected."""
    text = "5月份，测试_居民消费价格同比上涨1.2%，涨幅比上月扩大0.3个百分点。"
    matched_level, _, _ = kernel.source_contains_value(text, "测试_居民消费价格", 1.2)
    assert matched_level
    # The delta figure itself (0.3) sits right after 扩大 -- a neutral word,
    # not a signal -- so a POSITIVE stored value for it must still verify.
    matched_delta, _, _ = kernel.source_contains_value(text, "测试_居民消费价格", 0.3)
    assert matched_delta


def test_direction_aware_收窄_does_not_let_a_negative_delta_masquerade_as_positive_evidence():
    # 降幅收窄0.04个百分点: the MARGIN (幅) narrowed, which is itself always
    # phrased as a positive magnitude -- confirms 收窄 truly adds no sign
    # information either way (a negative stored value must NOT spuriously
    # match here; the sentence never claims a decline of 0.04 anywhere).
    text = "食品价格下降0.9%，降幅比上月收窄0.04个百分点。"
    matched_negative, _, _ = kernel.source_contains_value(text, "食品价格", -0.04)
    assert not matched_negative
    matched_positive, _, _ = kernel.source_contains_value(text, "食品价格", 0.04)
    assert matched_positive


def test_direction_aware_clause_boundary_stops_a_signal_word_leaking_across_commas():
    # "下降" belongs to the PRECEDING clause; the number in the current
    # clause has no signal word of its own and must be accepted for a
    # positive stored value.
    text = "出口下降2.1%，进口增长1.0亿美元不相关，其他指标为0.6。"
    matched, _, _ = kernel.source_contains_value(text, "其他指标", 0.6)
    assert matched


def test_format_number_candidates_includes_wan_yi_scale():
    # 353.67万亿元 stored as 3536700 亿元 -- the 万亿-scaled candidate must render "353.67".
    candidates = kernel.format_number_candidates(3536700.0)
    scale_names = {name for name, _ in candidates}
    assert "wan_yi_1e4" in scale_names
    wan_yi_values = {val for name, val in candidates if name == "wan_yi_1e4"}
    assert "353.67" in wan_yi_values


def test_severity_for_mismatch_tier3_and_association_warn_official_blocks():
    assert kernel.severity_for_mismatch({"tier": 1, "source": {"agency": "nbs"}}) == "block"
    assert kernel.severity_for_mismatch({"tier": 2, "source": {"agency": "pbc"}}) == "block"
    assert kernel.severity_for_mismatch({"tier": 3, "source": {"agency": "nbs"}}) == "warn"
    assert kernel.severity_for_mismatch({"tier": 1, "source": {"agency": "cflp"}}) == "warn"


# -- real archived pages (task spec: verify against >=3 real archived pages) --------


def _load(fixture_rel_path: str) -> str:
    raw = (FIXTURES_RAW / fixture_rel_path).read_bytes()
    return kernel.strip_html(raw)


def test_real_page_retail_total_matches_ytd_and_month_values():
    text = _load("nbs_activity/2026-05_retail.html")
    matched_ytd, evidence_ytd, scale_ytd = kernel.source_contains_value(text, "社会消费品零售总额", 206031)
    assert matched_ytd and scale_ytd == "1x"
    matched_month, _, _ = kernel.source_contains_value(text, "社会消费品零售总额", 41090)
    assert matched_month


def test_real_page_cpi_matches_positive_yoy_and_negative_mom():
    text = _load("nbs_cpi/2026-05_cpi.html")
    matched_yoy, _, _ = kernel.source_contains_value(text, "居民消费价格", 1.2)
    assert matched_yoy
    # "环比下降0.1%" -- negative MoM, exercises the 下降-phrasing candidate.
    matched_mom, evidence, _ = kernel.source_contains_value(text, "居民消费价格", -0.1)
    assert matched_mom
    assert "下降" in evidence


def test_real_page_pboc_money_matches_m2_at_wan_yi_scale():
    text = _load("pboc_money/2026-05_finstats.html")
    # catalog stores pbc-m2 in 亿元; the page reports "353.67万亿元" = 3,536,700 亿元.
    matched, evidence, scale = kernel.source_contains_value(text, "广义货币(M2)", 3536700.0)
    assert matched
    assert scale == "wan_yi_1e4"
    assert "353.67" in evidence


def test_real_page_pboc_money_growth_rate_matches_at_1x_scale():
    text = _load("pboc_money/2026-05_finstats.html")
    matched, _, scale = kernel.source_contains_value(text, "广义货币(M2)", 8.6)
    assert matched and scale == "1x"


def test_source_contains_value_false_when_absent():
    text = _load("nbs_cpi/2026-05_cpi.html")
    matched, evidence, scale = kernel.source_contains_value(text, "居民消费价格", 999999.25)
    assert not matched and evidence is None and scale is None


# -- sampling helper ----------------------------------------------------------------


def test_sample_returns_all_when_smaller_than_size():
    import random

    rng = random.Random(1)
    assert kernel.sample([1, 2, 3], rng, 10) == [1, 2, 3]


def test_sample_is_deterministic_given_same_rng_seed():
    import random

    items = list(range(50))
    a = kernel.sample(items, random.Random("seed-x"), 5)
    b = kernel.sample(items, random.Random("seed-x"), 5)
    assert a == b
