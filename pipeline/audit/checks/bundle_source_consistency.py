"""gate_b.bundle_source_consistency — structural cross-check between
data/catalog.json and site-data/*, with no sampling: every non-panel catalog
series appears in exactly one section bundle (the one matching its own catalog
`section`); panel ids appear ONLY under site-data/panels/, never inside a
section bundle's `series` list; nothing appears in a bundle that isn't in the
catalog; site-data/index.json's `freshness` list covers every non-panel series.
All violations BLOCK (this is a structural invariant, not a numeric tolerance
call -- any violation means the deployed site is inconsistent with its own
manifest).
"""
from __future__ import annotations

import time

from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.site_data import load_index, non_panel_catalog_entries, panel_catalog_entries

CHECK_ID = "gate_b.bundle_source_consistency"


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []

    non_panel = {entry["id"]: entry for entry in non_panel_catalog_entries(ctx.catalog)}
    panels = {entry["id"]: entry for entry in panel_catalog_entries(ctx.catalog)}

    # Every id appearing in any section bundle, with a count of how many
    # bundles/times it appears and which section(s).
    appearances: dict[str, list[str]] = {}
    for section_id, bundle in ctx.section_bundles.items():
        for entry in bundle.get("series", []):
            appearances.setdefault(entry["id"], []).append(section_id)

    # 1) every non-panel series appears in exactly one bundle, matching its
    #    own catalog section.
    for series_id, catalog_entry in non_panel.items():
        sections = appearances.get(series_id, [])
        expected_section = catalog_entry["section"]
        if not sections:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=catalog_entry.get("tier"),
                    note=f"catalog series missing from every section bundle (expected in {expected_section!r})",
                )
            )
            continue
        if len(sections) > 1:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=catalog_entry.get("tier"),
                    observed=sections,
                    note=f"series appears in {len(sections)} section bundles, expected exactly 1: {sections}",
                )
            )
            continue
        if sections[0] != expected_section:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=catalog_entry.get("tier"),
                    expected=expected_section,
                    observed=sections[0],
                    note="series bundled under a section that does not match its own catalog `section`",
                )
            )

    # 2) panel ids never appear inside a section bundle's series list, and DO
    #    have a corresponding site-data/panels/<id>.json.
    for panel_id, catalog_entry in panels.items():
        if panel_id in appearances:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    panel=panel_id,
                    tier=catalog_entry.get("tier"),
                    note=f"panel id appears inside section bundle(s) {appearances[panel_id]}; panels must live only under panels/",
                )
            )
        bundle = ctx.panel_bundle_loader(panel_id)
        if bundle is None:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    panel=panel_id,
                    tier=catalog_entry.get("tier"),
                    note="catalog panel entry has no corresponding site-data/panels/<id>.json",
                )
            )

    # 3) nothing in any bundle that isn't in the catalog at all.
    known_ids = set(non_panel) | set(panels)
    for series_id, sections in appearances.items():
        if series_id not in known_ids:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    observed=sections,
                    note="id appears in a section bundle but does not exist in data/catalog.json",
                )
            )

    # 4) index.json freshness entries cover all (non-panel) series.
    index = load_index(ctx.site_data_dir)
    if index is None:
        findings.append(Finding(check=CHECK_ID, status="block", note="site-data/index.json is missing"))
    else:
        freshness_ids = {row["id"] for row in index.get("freshness", [])}
        missing = sorted(set(non_panel) - freshness_ids)
        for series_id in missing:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    tier=non_panel[series_id].get("tier"),
                    note="series missing from site-data/index.json's freshness list",
                )
            )
        extra = sorted(freshness_ids - set(non_panel))
        for series_id in extra:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    series=series_id,
                    note="index.json freshness entry references an id absent from data/catalog.json",
                )
            )

    if not findings:
        findings.append(
            Finding(
                check=CHECK_ID,
                status="pass",
                note=f"{len(non_panel)} series + {len(panels)} panel(s) consistent across catalog/bundles/index",
            )
        )

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
