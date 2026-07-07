"""The 9 gate_b.* checks. Each module exposes a module-level `CHECK_ID: str`
and `run(ctx: AuditContext) -> CheckReport`. `CHECK_MODULES` fixes the order
they run in (and are reported in) -- structural/cheap checks first, the
subprocess rebuild last, matching roughly how expensive/likely-to-need-context
each one is."""
from __future__ import annotations

from pipeline.audit.checks import (
    archive_independent_sample,
    bundle_source_consistency,
    build_determinism,
    derived_recompute,
    dg_archive_sample,
    freshness,
    latest_prev_resolution,
    takeaway_numbers,
    yoy_break_nulls,
)

CHECK_MODULES = [
    bundle_source_consistency,
    yoy_break_nulls,
    latest_prev_resolution,
    takeaway_numbers,
    derived_recompute,
    dg_archive_sample,
    archive_independent_sample,
    freshness,
    build_determinism,
]
