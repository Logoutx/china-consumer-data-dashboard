"""Indexes and searches archived HTML release pages for the flat-text fuzzy
scan (kernel.source_contains_value).

Real-archive layout, confirmed against pipeline/fetch.py's own
`archive_path_for` (the code that will eventually populate this directory --
not imported here, just read to settle a genuine documentation conflict: data/
archive/README.md and DATA-CONTRACT §8 disagree on whether an HTML capture is a
raw `.html` file or a JSON-wrapped envelope; fetch.py's actual implementation
writes a raw `.html` file, so that is the primary assumption below):

    data/archive/<source>/<YYYY-MM-DD>_<slug>.html   (raw response body)

data/archive/dg/ is excluded here (it is JSON, handled entirely by
dg_archive.py). A defensive fallback also accepts a `.json` file under a non-dg
source directory shaped like DATA-CONTRACT §8's envelope
(`{... "payload": {...}}`) in case that format is what eventually lands --
untestable against real data today (the directory is empty for every non-dg
source as of this writing; data/archive/README.md says so explicitly), so both
paths are exercised only by synthetic fixtures in test_audit_kernel.py /
test_audit_dg_and_archive_sample.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.audit.kernel import compact_text, source_contains_value, strip_html

_REL_SRC_RE = re.compile(r"^rel:(\d{4})(\d{2})(\d{2})$")


@dataclass
class ArchivedPage:
    path: Path
    source: str  # the directory name under data/archive/ (or "fixture" for test pages)
    text: str  # already run through strip_html (NOT yet compact_text -- source_contains_value does that)


def _extract_text(path: Path) -> str | None:
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        candidate = payload.get("payload") if isinstance(payload, dict) else None
        if isinstance(candidate, str):
            return strip_html(candidate)
        if isinstance(candidate, dict):
            return strip_html(json.dumps(candidate, ensure_ascii=False))
        return strip_html(json.dumps(payload, ensure_ascii=False))
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return strip_html(raw)


def load_archived_pages(archive_dir: Path, *, exclude_dirs: tuple[str, ...] = ("dg",)) -> list[ArchivedPage]:
    """Every page under data/archive/<source>/ for every source except
    `exclude_dirs`. Safe to call with a directory that doesn't exist yet
    (returns [])."""
    pages: list[ArchivedPage] = []
    if not archive_dir.is_dir():
        return pages
    for source_dir in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
        if source_dir.name in exclude_dirs:
            continue
        for path in sorted(source_dir.glob("**/*")):
            if not path.is_file() or path.suffix not in (".html", ".htm", ".json"):
                continue
            text = _extract_text(path)
            if text:
                pages.append(ArchivedPage(path=path, source=source_dir.name, text=text))
    return pages


def load_fixture_pages(fixtures_dir: Path) -> list[ArchivedPage]:
    """Load pipeline/fixtures/raw/**/*.html as ArchivedPage -- used by tests to
    verify the fuzzy matcher against real committed pages, and reusable by a
    check as a supplementary pool when the real archive is thin (the fixtures
    are real captured NBS/PBOC pages, just not wired into data/archive/ yet)."""
    return load_archived_pages(fixtures_dir, exclude_dirs=())


# Normal publication/archival lag is weeks-to-months, never years, so a page
# more than 1 calendar year from the observation's period can't plausibly be
# its source. The previous value (3) re-opened the exact "right label, wrong
# time" trap this filter exists to close, at a shorter range: the first
# full-history sweep (2026-08-30, deploy run 33318642705) checked legacy:2023-*
# retail observations against the only archived retail pages -- all 2026
# captures, within 3 years, whose repeating headline row labels (社会消费品
# 零售总额/餐饮收入/...) pass any_page_mentions_label -- and reported 55
# honest coverage gaps as BLOCK-severity mismatches.
_FALLBACK_YEAR_TOLERANCE = 1
# YYYY-MM-DD (fetch.py's archive_path_for convention for a real live fetch) OR
# bare YYYY-MM (pipeline.runner's --fixture archive registration, whose
# filename is the fixture's own stem -- a committed fixture like
# "2026-05_cpi.html" carries only a period, never a real fetch day).
_LEADING_DATE_RE = re.compile(r"^(\d{4})-\d{2}(?:-\d{2})?(?:[_.-]|$)")
_LEADING_YEAR_RE = re.compile(r"^(\d{4})")


def _page_year(page: ArchivedPage) -> int | None:
    """The page's own fetch/archive year, from its filename's leading date
    (YYYY-MM-DD for a real live fetch, or bare YYYY-MM for a --fixture
    archive registration -- see _LEADING_DATE_RE). None if the filename
    doesn't start with a recognizable date -- an unusual/hand-placed file,
    conservatively treated as "no date info to reason about", never as "any
    year matches"."""
    match = _LEADING_DATE_RE.match(page.path.name)
    return int(match.group(1)) if match else None


def _period_year(period: str) -> int | None:
    match = _LEADING_YEAR_RE.match(period)
    return int(match.group(1)) if match else None


def _drop_implausible_years(pages: list[ArchivedPage], period: str | None) -> list[ArchivedPage]:
    """Fallback-only temporal plausibility filter (see pages_for_src's
    docstring): drops a page whose own filename year is more than
    _FALLBACK_YEAR_TOLERANCE years from `period`'s year. Never excludes a
    page when `period` is absent, or when either side's year can't be parsed
    -- conservative on both ends, exactly like the rest of this module."""
    if period is None:
        return pages
    period_year = _period_year(period)
    if period_year is None:
        return pages
    return [p for p in pages if (page_year := _page_year(p)) is None or abs(page_year - period_year) <= _FALLBACK_YEAR_TOLERANCE]


def pages_for_src(pages: list[ArchivedPage], src: str | None, period: str | None = None) -> list[ArchivedPage]:
    """Narrow the full page pool to the ones plausibly archiving a given
    observation's `src`, mirroring the legacy auditor's own targeted
    filename-based lookup (`find_cached_page`) rather than blindly scanning
    every archived page for every sample. `src` values of the form
    "rel:YYYYMMDD" (a release id) are expected to correspond to
    data/archive/<source>/YYYY-MM-DD_<slug>.html (fetch.py's own
    `archive_path_for` convention) -- narrow to filenames starting with that
    date.

    A "rel:YYYYMMDD" src is a POSITIVE claim about which release this value
    came from: if no file starts with that date, that is itself the honest
    answer ("no archive for this specific release yet") -- returning the full
    pool here instead would let an unrelated page's coincidental text decide
    the verdict, turning an archive-coverage GAP into a false mismatch (caught
    empirically: test-retail-ex-auto's 2026-04 obs, src "rel:20260518", was
    being checked against clean_repo's one 2026-06-16 retail page and
    "failing" to find 2026-04's numbers on a page that was never about
    2026-04 in the first place). Any OTHER `src` shape (legacy:*, natdata:*,
    derived:*, or no `src` at all) carries no date hint of its own, so this
    still returns a broad, best-effort pool -- but (added 2026-07-08, `period`
    now optional) narrowed by _drop_implausible_years when the caller supplies
    the observation's own `period`: the SAME "right label, wrong time" trap
    the rel:-narrowing above closes for rel:-shaped src also applies here,
    just without a src-embedded date to narrow by -- caught empirically when
    a single fixture-mode archive capture dated 2026-05, sitting alone in what
    used to be an empty data/archive/nbs-retail/, was treated as a
    "candidate" for natdata:-sourced samples from 2005 and 2009 (17-21 years
    away, but the SAME headline row labels repeat in every monthly release,
    so any_page_mentions_label's gate didn't catch it either) -- turning an
    honest coverage gap into a false BLOCK-severity mismatch. Callers also
    still combine this with `find_value_in_pages`'s label-presence gate (see
    its docstring) for whatever residual imprecision remains in this
    fallback pool.
    """
    if not src:
        return _drop_implausible_years(pages, period)
    match = _REL_SRC_RE.match(src.strip())
    if not match:
        return _drop_implausible_years(pages, period)
    date_prefix = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return [p for p in pages if p.path.name.startswith(date_prefix)]


def any_page_mentions_label(pages: list[ArchivedPage], label_candidates: list[str]) -> bool:
    """True iff ANY candidate page's text contains ANY label at all
    (regardless of whether the specific value is found near it). Used to
    distinguish "this concept was never archived here at all" (a coverage
    gap) from "the concept IS archived here, but this specific number isn't"
    (a genuine mismatch) -- without this gate, a broad (unnarrowed) page pool
    would misclassify plenty of honest gaps as mismatches just because SOME
    unrelated archived page happened to be in the search pool."""
    compact_labels = [compact_text(label) for label in label_candidates if label]
    for page in pages:
        compact_page = compact_text(page.text)
        if any(label in compact_page for label in compact_labels):
            return True
    return False


def find_value_in_pages(
    pages: list[ArchivedPage], label_candidates: list[str], value: float
) -> tuple[bool, ArchivedPage | None, str | None, str | None]:
    """(matched, page, evidence, scale_name) -- tries every label candidate
    against every page's text, first match wins. O(pages * labels); fine at
    the scale this repo operates at (dozens of pages, a handful of labels)."""
    for page in pages:
        for label in label_candidates:
            matched, evidence, scale_name = source_contains_value(page.text, label, value)
            if matched:
                return True, page, evidence, scale_name
    return False, None, None, None
