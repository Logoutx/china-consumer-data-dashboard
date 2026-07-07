"""Tests for pipeline/audit/html_archive.py's pages_for_src -- the archived-
page candidate-pool narrowing gate_b.archive_independent_sample relies on to
tell an honest "never archived" coverage gap from a genuine mismatch.
"""
from __future__ import annotations

from pipeline.audit.html_archive import ArchivedPage, pages_for_src


def _page(name: str, text: str = "") -> ArchivedPage:
    from pathlib import Path

    return ArchivedPage(path=Path(f"/fake/archive/nbs-retail/{name}"), source="nbs-retail", text=text)


# -- rel:YYYYMMDD src: unchanged, date-exact narrowing ---------------------------


def test_rel_src_narrows_to_the_exact_date_prefix():
    pages = [_page("2026-05-18_retail.html"), _page("2026-06-16_retail.html")]
    result = pages_for_src(pages, "rel:20260518")
    assert [p.path.name for p in result] == ["2026-05-18_retail.html"]


def test_rel_src_with_no_matching_file_returns_empty_not_the_full_pool():
    pages = [_page("2026-06-16_retail.html")]
    result = pages_for_src(pages, "rel:20260518")
    assert result == []


# -- non-rel: src (natdata:/legacy:/no src): fallback pool, now year-plausibility
#    filtered when `period` is supplied -----------------------------------------


def test_fallback_without_period_returns_the_full_pool_unchanged():
    """Backward compatible: omitting `period` (the old call shape) must not
    change behavior at all."""
    pages = [_page("2005-09-01_retail.html"), _page("2026-05-18_retail.html")]
    assert pages_for_src(pages, "natdata:monthly") == pages
    assert pages_for_src(pages, None) == pages


def test_fallback_with_period_drops_a_page_from_a_wildly_different_year():
    """Regression, 2026-07-08: a single fixture-mode archive capture dated
    2026-05, alone in what used to be an empty data/archive/nbs-retail/, was
    being treated as a "candidate" for natdata:-sourced samples from 2005 and
    2009 (17-21 years away) -- any_page_mentions_label's label-presence gate
    doesn't catch this because the SAME headline row labels repeat in every
    monthly release. This is exactly the trap that turned an honest coverage
    gap into a false BLOCK-severity mismatch."""
    pages = [_page("2026-05-18_retail.html")]
    result = pages_for_src(pages, "natdata:monthly", period="2005-08")
    assert result == []


def test_fallback_with_period_keeps_a_page_within_the_tolerance_window():
    pages = [_page("2026-05-18_retail.html")]
    result = pages_for_src(pages, "natdata:monthly", period="2027-01")  # within 3 years
    assert result == pages


def test_fallback_with_period_keeps_pages_with_no_parseable_date_conservatively():
    """A page whose filename doesn't start with a recognizable YYYY-MM-DD
    (unusual/hand-placed) is never excluded -- conservative on both ends,
    matching the rest of this module's philosophy: never silently drop a
    page we can't actually reason about."""
    pages = [_page("some-hand-placed-file.html")]
    result = pages_for_src(pages, "natdata:monthly", period="2005-08")
    assert result == pages


def test_fallback_with_unparseable_period_returns_the_full_pool():
    pages = [_page("2026-05-18_retail.html")]
    result = pages_for_src(pages, "natdata:monthly", period="not-a-period")
    assert result == pages


def test_fallback_mixed_pool_keeps_only_the_plausible_ones():
    pages = [_page("2007-09-01_retail.html"), _page("2009-04-10_retail.html"), _page("2026-05-18_retail.html")]
    result = pages_for_src(pages, "legacy:2009-03", period="2009-03")  # 2007: diff=2 (kept), 2009: diff=0 (kept), 2026: diff=17 (dropped)
    assert [p.path.name for p in result] == ["2007-09-01_retail.html", "2009-04-10_retail.html"]
