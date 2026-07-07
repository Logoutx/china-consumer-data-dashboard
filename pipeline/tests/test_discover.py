"""Tests for pipeline/discover.py. No real network calls -- `pipeline.discover.fetch`
is monkeypatched to a canned in-memory responder, so listing-page filtering logic
is exercised deterministically. (The one live, polite end-to-end discovery check
required by the task is a manual, out-of-band run against the real
www.stats.gov.cn listing -- see the final report -- not part of this test suite.)
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pipeline import discover as discover_module
from pipeline.fetch import FetchError

NBS_LISTING_PAGE = """
<html><body>
<a title="2026年5月份居民消费价格同比上涨1.2%" href="/sj/zxfb/202606/t20260610_1963923.html">CPI</a>
<a title="2026年5月份居民消费价格数据解读" href="/sj/zxfb/202606/t20260610_1963924.html">interp</a>
<a title="2026年4月份居民消费价格同比上涨0.5%" href="/sj/zxfb/202605/t20260509_1963000.html">CPI old</a>
<a title="不相关标题" href="/sj/zxfb/202606/other.html">other</a>
<a title="2026年5月份居民消费价格走势图" href="/sj/zxfb/202606/chart.html">chart</a>
</body></html>
"""

PBC_SEARCH_PAGE = """
<html><body>
<a href="https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026061214273613328/index.html">2026年5月金融统计数据报告</a>
<a href="https://xiamen.pbc.gov.cn/goutongjiaoliu/000/2026061200000000/index.html">2026年5月金融统计数据报告（厦门中心支行）</a>
<a href="https://www.pbc.gov.cn/somewhereelse/index.html">不相关链接</a>
</body></html>
"""


@dataclass
class _FakeResult:
    text: str


def test_discover_nbs_filters_and_matches_title_regex(monkeypatch):
    def fake_fetch(url, session=None):
        if url.endswith("index.html") and "index_" not in url:
            return _FakeResult(text=NBS_LISTING_PAGE)
        raise FetchError("no more pages")

    monkeypatch.setattr(discover_module, "fetch", fake_fetch)
    candidates = discover_module.discover_nbs(r"^\d{4}年\d{1,2}月份居民消费价格", max_pages=2)

    titles = {candidate.title for candidate in candidates}
    assert "2026年5月份居民消费价格同比上涨1.2%" in titles
    assert "2026年4月份居民消费价格同比上涨0.5%" in titles
    assert not any("解读" in title for title in titles)
    assert not any("走势图" in title for title in titles)
    assert not any(title == "不相关标题" for title in titles)

    latest = candidates[-1]
    assert latest.period_hint == "2026-05"


def test_discover_nbs_returns_empty_when_nothing_out_yet(monkeypatch):
    def fake_fetch(url, session=None):
        raise FetchError("listing unreachable")

    monkeypatch.setattr(discover_module, "fetch", fake_fetch)
    candidates = discover_module.discover_nbs(r"^\d{4}年\d{1,2}月份居民消费价格")
    assert candidates == []  # graceful, not a raised exception


def test_discover_pbc_filters_to_national_tree(monkeypatch):
    def fake_fetch(url, session=None):
        return _FakeResult(text=PBC_SEARCH_PAGE)

    monkeypatch.setattr(discover_module, "fetch", fake_fetch)
    candidates = discover_module.discover_pbc("2026年5月金融统计数据报告")

    urls = {candidate.url for candidate in candidates}
    assert any("www.pbc.gov.cn/goutongjiaoliu" in url for url in urls)
    assert not any("xiamen.pbc.gov.cn" in url for url in urls)  # province mirror dropped
    assert not any("somewhereelse" in url for url in urls)  # title didn't match


def test_discover_pbc_returns_empty_on_fetch_failure(monkeypatch):
    def fake_fetch(url, session=None):
        raise FetchError("search endpoint down")

    monkeypatch.setattr(discover_module, "fetch", fake_fetch)
    candidates = discover_module.discover_pbc("2026年5月金融统计数据报告")
    assert candidates == []
