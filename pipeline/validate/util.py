"""pipeline/validate/util.py -- small stateless helpers shared by every check.

Kept dependency-free (stdlib only, like pipeline/migrate/schema_validator.py)
since this whole package sits on the ingest hot path and must never fail to
import because of a missing third-party package.
"""
from __future__ import annotations

import math
import re
from statistics import median

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_ANNUAL_RE = re.compile(r"^(\d{4})$")

MEASURE_NAMES = ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")
YOY_MEASURES = ("m_yoy", "ytd_yoy", "real_yoy")
LEVEL_MEASURES = ("m", "ytd")


def period_shape(period: str) -> str | None:
    """'M' / 'Q' / 'A' from the period string's own literal shape -- never from
    a series' declared `freq` (build.py learned this the hard way; see its
    module docstring). None if the string matches none of the three shapes."""
    if _MONTH_RE.match(period):
        return "M"
    if _QUARTER_RE.match(period):
        return "Q"
    if _ANNUAL_RE.match(period):
        return "A"
    return None


def period_sort_key(period: str) -> tuple[int, int, int]:
    """Chronological sort key. A bare annual period sorts AFTER all quarters of
    the same year (it is the full-year total, published last) -- mirrors
    pipeline/migrate/migrate.py's period_sort_key exactly."""
    shape = period_shape(period)
    if shape == "A":
        return (int(period), 13, 0)
    if shape == "Q":
        y, q = period.split("-Q")
        return (int(y), int(q) * 3, 0)
    if shape == "M":
        y, m = period.split("-")
        return (int(y), int(m), 0)
    return (0, 0, 0)


def next_period(period: str, freq: str) -> str | None:
    """The next period one step ahead, for the given freq ('M'/'Q'/'A'). Steps
    strictly by the requested freq regardless of the input string's own shape
    (caller is expected to pass a period whose shape already matches freq)."""
    shape = period_shape(period)
    if shape == "M" and freq == "M":
        y, m = (int(part) for part in period.split("-"))
        m += 1
        if m > 12:
            m = 1
            y += 1
        return f"{y:04d}-{m:02d}"
    if shape == "Q" and freq == "Q":
        y, q = period.split("-Q")
        y, q = int(y), int(q)
        q += 1
        if q > 4:
            q = 1
            y += 1
        return f"{y:04d}-Q{q}"
    if shape == "A" and freq == "A":
        return str(int(period) + 1)
    return None


def steps_between(a: str, b: str, freq: str) -> int | None:
    """How many `freq`-steps from a to b (positive if b is after a). None if
    either period's shape doesn't match freq."""
    if period_shape(a) != freq or period_shape(b) != freq:
        return None
    if freq == "M":
        ay, am = (int(p) for p in a.split("-"))
        by, bm = (int(p) for p in b.split("-"))
        return (by - ay) * 12 + (bm - am)
    if freq == "Q":
        ay, aq = a.split("-Q")
        by, bq = b.split("-Q")
        return (int(by) - int(ay)) * 4 + (int(bq) - int(aq))
    if freq == "A":
        return int(b) - int(a)
    return None


def month_of(period: str) -> int | None:
    if period_shape(period) == "M":
        return int(period.split("-")[1])
    return None


def quarter_of(period: str) -> int | None:
    if period_shape(period) == "Q":
        return int(period.split("-Q")[1])
    return None


def is_jan_feb_period(period: str) -> bool:
    return period_shape(period) == "M" and period.endswith("-02")


def in_no_yoy_window(period: str, breaks: list[dict]) -> bool:
    """True if `period` falls inside [break.effective, break.yoy_valid_from)
    for any break with no_yoy_across:true -- mirrors pipeline/normalize.py's
    private _yoy_blocked_by_break exactly (that function is the pre-write
    filter; this is Gate A's independent post-hoc re-check of the same
    invariant, used by both gate_a.seasonal_z and gate_a.break_no_yoy)."""
    for brk in breaks:
        if not brk.get("no_yoy_across"):
            continue
        effective = brk.get("effective")
        if not effective or period < effective:
            continue
        valid_from = brk.get("yoy_valid_from")
        if valid_from is not None and period >= valid_from:
            continue
        return True
    return False


def cohort_key(period: str, *, jan_feb: bool = False) -> tuple | None:
    """Group key for 'same calendar slot across years': month-of-year for
    monthly (Jan-Feb merged prints form their own cohort, never mixed with a
    plain Feb), quarter-of-year for quarterly. None for annual (no cohort
    finer than the series itself)."""
    shape = period_shape(period)
    if shape == "M":
        if jan_feb and is_jan_feb_period(period):
            return ("jan_feb",)
        return ("month", month_of(period))
    if shape == "Q":
        return ("quarter", quarter_of(period))
    return None


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not (
        isinstance(value, float) and math.isnan(value)
    )


def safe_div(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return a / b


def robust_z(new_value: float, history: list[float]) -> float | None:
    """(x - median) / (1.4826 * MAD). None if history is empty. If MAD is 0
    (a degenerate, perfectly flat history) the new value's z is 0 when it
    equals that flat value and +/-inf otherwise -- any departure from a
    dead-flat history is maximally suspicious, so it must always clear
    z_block rather than silently divide-by-zero into a false pass."""
    if not history:
        return None
    med = median(history)
    mad = median([abs(x - med) for x in history])
    if mad == 0:
        return 0.0 if new_value == med else math.inf * (1 if new_value > med else -1)
    return (new_value - med) / (1.4826 * mad)


def infer_source_kind(tag: str | None) -> str:
    """'dg' | 'press' | 'other', from a `src`/`release_id`-shaped provenance
    string. 'dg:<hash>' (pipeline/backfill's DG bulk-query path) vs
    'rel:<date>' / 'natdata:...' / 'legacy...' (the fetch->parse->normalize
    press-release path this milestone's runner.py drives) -- see
    pipeline/validate/batch.py for where this is used."""
    if not tag:
        return "other"
    if tag.startswith("dg:"):
        return "dg"
    if tag.startswith(("rel:", "natdata:", "legacy")):
        return "press"
    return "other"


def display_tolerance(decimals: int | None) -> float:
    """Half a ULP of the coarser display precision, e.g. decimals=1 -> 0.05."""
    if decimals is None:
        decimals = 1
    return 0.5 * (10 ** -decimals)


def rel_diff(a: float, b: float) -> float | None:
    """|a-b| relative to the larger magnitude; None if both are 0 (equal)."""
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 0.0
    return abs(a - b) / denom
