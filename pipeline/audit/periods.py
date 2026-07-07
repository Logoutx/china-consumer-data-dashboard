"""Period-string utilities, independently reimplemented from the same
DATA-CONTRACT-documented, publicly-observable format rules pipeline/build.py
also implements (period strings are part of the data model, §3.2 -- not an
implementation detail of the build). Deliberately duplicated rather than
imported: pipeline.build is on the forbidden-import list, and these are
5-10 line pure functions over a well-specified string format, not meaningful
shared logic whose duplication risks drifting out of sync silently.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

_ANNUAL_RE = re.compile(r"^\d{4}$")
_QUARTERLY_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTHLY_RE = re.compile(r"^(\d{4})-(\d{2})$")


def period_shape(period: str) -> str:
    if _ANNUAL_RE.fullmatch(period):
        return "annual"
    if _QUARTERLY_RE.fullmatch(period):
        return "quarterly"
    return "monthly"


def period_end_date(period: str) -> date | None:
    """The calendar date a period's OWN data ends -- used to measure freshness
    lag (days since the period could first have been published). None if the
    string doesn't parse as any known shape."""
    shape = period_shape(period)
    if shape == "annual":
        match = _ANNUAL_RE.fullmatch(period)
        if not match:
            return None
        return date(int(period), 12, 31)
    if shape == "quarterly":
        match = _QUARTERLY_RE.fullmatch(period)
        if not match:
            return None
        year, q = int(match.group(1)), int(match.group(2))
        end_month = q * 3
        return date(year, end_month, monthrange(year, end_month)[1])
    match = _MONTHLY_RE.fullmatch(period)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, monthrange(year, month)[1])


def same_shape(period_a: str, period_b: str) -> bool:
    return period_shape(period_a) == period_shape(period_b)
