"""pipeline/discover.py — release discovery.

Finds candidate (url, title, period) releases *without* fetching/parsing the full
article body. Two engines, per docs/ACQUISITION.md:

  discover_nbs(...)  Engine A — www.stats.gov.cn/sj/zxfb/ listing scrape. Each
                     listing page has ~45 titled <a> links spanning ~3 weeks;
                     released articles are matched by a per-release-type title
                     regex (the "configurable title regex" requirement), so the
                     same function serves CPI/retail/PMI/etc discovery.

  discover_pbc(...)  wzdig.pbc.gov.cn search, per ACQUISITION.md Group 6. This is
                     the *unverified* half of this module: ACQUISITION.md gives the
                     URL shape and query parameter but this repo has no captured
                     fixture of an actual wzdig response body, so the result
                     parsing below is a best-effort HTML-link scrape, not something
                     pinned by a contract test. Treat it as a documented starting
                     point for whoever wires up the real PBoC poller, not a proven
                     path (see the final report's "known limitations").

Both functions return [] rather than raising when nothing new is out yet -- a
release not being published on schedule is the expected steady state for a
scheduler poll, not an error. Only genuine fetch failures on an *existing* listing
page (a real network/HTTP problem, not "no matching link found") propagate as
FetchError, and even then discover_nbs stops paging gracefully and returns whatever
it already found rather than crashing the caller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlparse

from lxml import html

from pipeline.fetch import FetchError, fetch

NBS_LISTING_BASE = "https://www.stats.gov.cn/sj/zxfb/"
PBC_SEARCH_URL = "https://wzdig.pbc.gov.cn/search/pcRender"
PBC_DEFAULT_PAGE_ID = "c177a85bd02b4114bebebd210809f691"

# Interpretive/index pages that share a title stem with the real release but are
# not the release itself -- reject these regardless of which release type we're
# discovering (ACQUISITION.md Engine A).
REJECT_TITLE_SUBSTRINGS = ("解读", "走势图", "日程", "答记者问")

_PERIOD_PATTERN = re.compile(r"(\d{4})年(?:1[—\-－](\d{1,2})月|(\d{1,2})月)")


@dataclass(frozen=True)
class Candidate:
    """One discovered-but-not-yet-fetched release."""

    url: str
    title: str
    period_hint: str | None  # "YYYY-MM" best-effort, parsed from the title


def _period_hint(title: str) -> str | None:
    match = _PERIOD_PATTERN.search(title)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or match.group(3))
    return f"{year:04d}-{month:02d}"


def discover_nbs(
    title_pattern: str,
    *,
    max_pages: int = 4,
    session=None,
) -> list[Candidate]:
    """Scrape www.stats.gov.cn/sj/zxfb/ listing pages for <a title=...> links whose
    title matches `title_pattern` (a regex string the caller supplies per release
    type -- e.g. CPI's `r"^\\d{4}年\\d{1,2}月份居民消费价格"`).

    Returns [] if no page is reachable or nothing matches -- both are the normal
    "nothing new" case for a poller, not an error. `max_pages` bounds how many
    index_N.html pages get walked in one call (the listing tree only retains
    ~2-3 years; deep backfill uses the DG API instead, per ACQUISITION.md).
    """
    pattern = re.compile(title_pattern)
    candidates: dict[str, Candidate] = {}

    for index in range(max_pages):
        name = "index.html" if index == 0 else f"index_{index}.html"
        url = urljoin(NBS_LISTING_BASE, name)
        try:
            result = fetch(url, session=session)
        except FetchError:
            break  # listing page missing/unreachable -- stop paging, keep what we have

        try:
            doc = html.fromstring(result.text)
        except Exception:
            break

        for anchor in doc.xpath("//a[@title and @href]"):
            title = anchor.get("title", "")
            if not title or not pattern.search(title):
                continue
            if any(bad in title for bad in REJECT_TITLE_SUBSTRINGS):
                continue
            href = urljoin(url, anchor.get("href"))
            candidates[href] = Candidate(url=href, title=title, period_hint=_period_hint(title))

    return sorted(candidates.values(), key=lambda candidate: (candidate.period_hint or "", candidate.url))


def discover_pbc(
    query_title: str,
    *,
    page_id: str = PBC_DEFAULT_PAGE_ID,
    session=None,
) -> list[Candidate]:
    """wzdig.pbc.gov.cn search for an (near-)exact PBoC report title.

    Best-effort: see the module docstring for why this path is unverified. Returns
    [] on any fetch/parse failure or empty result set -- never raises for
    "nothing new yet", matching discover_nbs's contract.
    """
    search_url = f"{PBC_SEARCH_URL}?pageId={page_id}&q={quote(query_title)}"
    try:
        result = fetch(search_url, session=session)
    except FetchError:
        return []

    try:
        doc = html.fromstring(result.text)
    except Exception:
        return []

    candidates: dict[str, Candidate] = {}
    query_stem = query_title[:6]  # loose containment check; full titles carry a period suffix
    for anchor in doc.xpath("//a[@href]"):
        title = "".join(anchor.itertext()).strip()
        if not title or query_stem not in title:
            continue
        href = anchor.get("href") or ""
        # Drop province mirrors (厦门/深圳/广东…) per ACQUISITION.md Group 6 -- keep
        # only the national site. Matching on the exact hostname (not a "pbc.gov.cn"
        # substring) is what actually excludes e.g. xiamen.pbc.gov.cn.
        if urlparse(href).hostname != "www.pbc.gov.cn":
            continue
        if "/goutongjiaoliu/" not in href:
            continue
        candidates[href] = Candidate(url=href, title=title, period_hint=_period_hint(title))

    return sorted(candidates.values(), key=lambda candidate: (candidate.period_hint or "", candidate.url))
