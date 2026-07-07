"""China Consumer / Economy Dashboard — acquisition pipeline.

Stages (docs/DATA-CONTRACT.md §11): fetch -> discover -> parse -> normalize ->
validate -> build -> audit. This package owns fetch / discover / parse / normalize;
validate/build/audit belong to a later milestone.

Exchange-format types below implement the contracts frozen in DATA-CONTRACT §11.1:

    RawCapture    fetch -> parse    (also what lands verbatim in data/archive/)
    ParsedRelease parse -> normalize (source vocabulary, no series ids yet)

Design note on `caliber_hint` (a deliberate, documented simplification):
DATA-CONTRACT's illustrative ParsedRelease example uses `caliber_hint: "single"`, a
coarse tag, leaving "is this the level or the published YoY" to be re-derived from
`raw_label` text downstream. This package instead sets `caliber_hint` directly to
the target *observation measure name* -- one of "m", "m_yoy", "ytd", "ytd_yoy",
"mom", "real_yoy" (the exact vocabulary of data/schemas/series.schema.json's
`measure_name` enum). Rationale: ParsedRelease has no independent JSON Schema to
satisfy (only series/catalog/panel files are schema-enforced), so nothing external
depends on the coarser shape; going straight to the measure name removes a whole
ambiguous text-sniffing translation step per source, and every parser test in
pipeline/tests/ asserts against these exact measure names. `pipeline/config/
field_map.yaml` therefore maps (source, source_field) -> series_id only; the
measure comes from the row itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ParseError(Exception):
    """Raised when a parser's anchor pattern misses an expected release shape.

    Parsers must fail loudly here rather than silently returning a partial result --
    a format drift on stats.gov.cn/pbc.gov.cn must surface as a hard error, never a
    quietly-thinner ParsedRelease.
    """

    def __init__(self, message: str, *, expected: str | None = None, found: str | None = None):
        self.expected = expected
        self.found = found
        detail = message
        if expected is not None or found is not None:
            detail = f"{message} (expected: {expected!r}, found: {found!r})"
        super().__init__(detail)


@dataclass
class RawCapture:
    """fetch -> parse handoff; also the shape archived verbatim under data/archive/."""

    source: str
    release_id: str
    url: str
    title: str
    published_at: str | None
    fetched_at: str
    content_hash: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedRow:
    """One (series, measure) data point extracted from a release, still in source
    vocabulary -- `source_field` is the raw Chinese label, not yet a series id."""

    source_field: str
    raw_label: str
    value: float | None
    unit_raw: str | None
    caliber_hint: str | None  # one of the six measure names -- see module docstring
    period: str
    city: str | None = None
    span: int = 1
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedRelease:
    """parse -> normalize handoff (DATA-CONTRACT §11.1). `source` matches a
    top-level key in pipeline/config/field_map.yaml (e.g. "nbs-cpi", "nbs-retail",
    "pbc-money")."""

    source: str
    release_id: str
    url: str
    published_at: str | None
    period_hint: str
    rows: list[ParsedRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "release_id": self.release_id,
            "url": self.url,
            "published_at": self.published_at,
            "period_hint": self.period_hint,
            "rows": [row.to_dict() for row in self.rows],
        }
