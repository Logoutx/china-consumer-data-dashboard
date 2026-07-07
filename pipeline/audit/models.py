"""Shared record types for every gate_b.* check.

One flat `Finding` shape covers all 9 checks (structural checks like
bundle_source_consistency and numeric-sample checks like archive_independent_sample
report through the same type) so report.py / diary.py only need to understand one
schema. Fields that don't apply to a given finding are simply left None and
dropped by `to_dict`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from random import Random
from typing import Any, Callable

# -- status vocabulary -----------------------------------------------------------
#
# "block"  -> a real, independently-confirmed mismatch. Causes exit code 2.
# "warn"   -> known/expected gap (no archive to check against, coverage gap,
#             association-tier mismatch, freshness lag) or a documented,
#             whitelisted disagreement. Never blocks deploy.
# "pass"   -> independently re-verified and matched.
# "skip"   -> not applicable (e.g. a derived series has no raw source text to
#             fuzzy-match; a stratum had nothing to sample).
STATUSES = ("block", "warn", "pass", "skip")


@dataclass
class Finding:
    check: str
    status: str  # one of STATUSES
    series: str | None = None
    panel: str | None = None
    period: str | None = None
    field: str | None = None
    tier: int | None = None
    expected: Any = None
    observed: Any = None
    tolerance: float | None = None
    source: str | None = None
    evidence: str | None = None
    note: str | None = None
    rule: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown Finding status {self.status!r}; must be one of {STATUSES}")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class CheckReport:
    check: str
    findings: list[Finding] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None  # set if the check itself raised; treated as a block
    extra: dict = field(default_factory=dict)  # structured data a check wants to hand to diary.py
    # (e.g. freshness.py's per-series lag table) without report.py/diary.py
    # having to recompute it independently.

    def summary(self) -> dict:
        counts = {status: 0 for status in STATUSES}
        for finding in self.findings:
            counts[finding.status] += 1
        counts["total"] = len(self.findings)
        return counts

    def has_block(self) -> bool:
        return bool(self.error) or any(f.status == "block" for f in self.findings)


@dataclass
class AuditContext:
    """Everything a check needs, threaded through explicitly rather than each
    check module reaching for globals or re-deriving repo paths independently."""

    repo_root: Path
    data_dir: Path
    site_data_dir: Path
    catalog: dict
    section_bundles: dict[str, dict]  # section_id -> bundle dict
    panel_bundle_loader: Callable[[str], dict | None]  # panel_id -> bundle or None
    labels: dict[str, dict]
    rng: Random
    seed: str
    run_id: str
    offline: bool
    samples_per_section: int
    as_of: date
    previous_diary: dict | None
    live_dg_cap: int = 10

    def series_by_id(self) -> dict[str, dict]:
        return {entry["id"]: entry for entry in self.catalog["series"]}

    def bundle_entries(self):
        """Yield (section_id, series_entry_dict) across all loaded section bundles."""
        for section_id, bundle in self.section_bundles.items():
            for entry in bundle.get("series", []):
                yield section_id, entry

    def bundle_entry_for(self, series_id: str) -> tuple[str, dict] | tuple[None, None]:
        for section_id, entry in self.bundle_entries():
            if entry["id"] == series_id:
                return section_id, entry
        return None, None
