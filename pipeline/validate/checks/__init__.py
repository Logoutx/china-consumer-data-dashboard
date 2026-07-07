"""pipeline/validate/checks -- the 22 gate_a.* check functions, registered
here so gate.py never has to know which module a check id lives in.

Checks are split by theme across four modules (structural / statistics /
cross_source / lifecycle) purely for file-size sanity -- there is no
behavioral coupling to the split itself. The one real ordering dependency is
that gate_a.triangulate_dg_press and gate_a.triangulate_pbc_nbs (7, 8) must
run before gate_a.seasonal_z (6), since 6 consults
GateContext.confirmed_cross_source_matches, which 7 populates. EXECUTION_ORDER
encodes that; CHECK_ORDER is the spec's own 1-22 numbering, used only to sort
the final report so it reads in the order the task described the checks.
"""
from __future__ import annotations

from pipeline.validate.checks.cross_source import check_triangulate_dg_press, check_triangulate_pbc_nbs
from pipeline.validate.checks.lifecycle import (
    check_archive_release_identity,
    check_break_link,
    check_break_no_yoy,
    check_calendar_expected,
    check_calendar_window,
    check_partial_parse_completeness,
    check_revision_flood,
    check_revision_integrity,
)
from pipeline.validate.checks.statistics import (
    check_cpi_envelope,
    check_online_share_bounds,
    check_seasonal_z,
    check_sum_of_parts,
    check_ytd_arithmetic,
    check_yoy_base_tolerance,
)
from pipeline.validate.checks.structural import (
    check_caliber_declared,
    check_catalog_consistency,
    check_period_monotonic,
    check_schema_series,
    check_unit_magnitude,
    check_value_type_bounds,
)

CHECK_ORDER = [
    "gate_a.schema_series",
    "gate_a.caliber_declared",
    "gate_a.value_type_bounds",
    "gate_a.period_monotonic",
    "gate_a.unit_magnitude",
    "gate_a.seasonal_z",
    "gate_a.triangulate_dg_press",
    "gate_a.triangulate_pbc_nbs",
    "gate_a.ytd_arithmetic",
    "gate_a.yoy_base_tolerance",
    "gate_a.sum_of_parts",
    "gate_a.cpi_envelope",
    "gate_a.online_share_bounds",
    "gate_a.calendar_expected",
    "gate_a.calendar_window",
    "gate_a.partial_parse_completeness",
    "gate_a.archive_release_identity",
    "gate_a.break_no_yoy",
    "gate_a.break_link",
    "gate_a.revision_flood",
    "gate_a.revision_integrity",
    "gate_a.catalog_consistency",
]

_FUNCTIONS = {
    "gate_a.schema_series": check_schema_series,
    "gate_a.caliber_declared": check_caliber_declared,
    "gate_a.value_type_bounds": check_value_type_bounds,
    "gate_a.period_monotonic": check_period_monotonic,
    "gate_a.unit_magnitude": check_unit_magnitude,
    "gate_a.seasonal_z": check_seasonal_z,
    "gate_a.triangulate_dg_press": check_triangulate_dg_press,
    "gate_a.triangulate_pbc_nbs": check_triangulate_pbc_nbs,
    "gate_a.ytd_arithmetic": check_ytd_arithmetic,
    "gate_a.yoy_base_tolerance": check_yoy_base_tolerance,
    "gate_a.sum_of_parts": check_sum_of_parts,
    "gate_a.cpi_envelope": check_cpi_envelope,
    "gate_a.online_share_bounds": check_online_share_bounds,
    "gate_a.calendar_expected": check_calendar_expected,
    "gate_a.calendar_window": check_calendar_window,
    "gate_a.partial_parse_completeness": check_partial_parse_completeness,
    "gate_a.archive_release_identity": check_archive_release_identity,
    "gate_a.break_no_yoy": check_break_no_yoy,
    "gate_a.break_link": check_break_link,
    "gate_a.revision_flood": check_revision_flood,
    "gate_a.revision_integrity": check_revision_integrity,
    "gate_a.catalog_consistency": check_catalog_consistency,
}

# Dependency order: triangulation (7, 8) before seasonal_z (6) so the demotion
# signal exists by the time 6 runs; everything else follows CHECK_ORDER.
EXECUTION_ORDER = [
    "gate_a.schema_series",
    "gate_a.caliber_declared",
    "gate_a.value_type_bounds",
    "gate_a.period_monotonic",
    "gate_a.unit_magnitude",
    "gate_a.triangulate_dg_press",
    "gate_a.triangulate_pbc_nbs",
    "gate_a.seasonal_z",
    "gate_a.ytd_arithmetic",
    "gate_a.yoy_base_tolerance",
    "gate_a.sum_of_parts",
    "gate_a.cpi_envelope",
    "gate_a.online_share_bounds",
    "gate_a.calendar_expected",
    "gate_a.calendar_window",
    "gate_a.partial_parse_completeness",
    "gate_a.archive_release_identity",
    "gate_a.break_no_yoy",
    "gate_a.break_link",
    "gate_a.revision_flood",
    "gate_a.revision_integrity",
    "gate_a.catalog_consistency",
]

assert set(CHECK_ORDER) == set(EXECUTION_ORDER) == set(_FUNCTIONS), "CHECK_ORDER/EXECUTION_ORDER/registry must all list the same 22 check ids"


def get_check(check_id: str):
    return _FUNCTIONS[check_id]


def run_all(ctx) -> list:
    """Run every check in EXECUTION_ORDER, return CheckResults sorted back
    into CHECK_ORDER (the task spec's own 1-22 numbering) for reporting."""
    results = {check_id: _FUNCTIONS[check_id](ctx) for check_id in EXECUTION_ORDER}
    return [results[check_id] for check_id in CHECK_ORDER]
