"""gate_b.archive_independent_sample — the main sampling gate. Builds the
task-spec strata (a)-(f) (pipeline/audit/sampling.py), then re-verifies each
sampled (series, period, caliber) point against `data/archive/` through
whichever paradigm applies:

  - **derived series** — skipped here entirely (status="skip"); re-verified by
    gate_b.derived_recompute instead, which recomputes from inputs rather than
    fuzzy-matching a raw source that, for several of these, was never printed
    verbatim in the first place (see labels.yaml's `no_source_text` entries).
  - **DG-sourced series** — `dg_archive.verify_observation` (shared with
    gate_b.dg_archive_sample; see that check's docstring).
  - **HTML-release-sourced series** — `kernel.source_contains_value` against
    archived pages narrowed by `html_archive.pages_for_src`.
  - **70-city panel samples** — a dedicated per-city path: the label is the
    CITY NAME itself (not a labels.yaml entry, which only covers the panel as
    a whole), since that's what would actually anchor a value in a future
    per-city release table.
  - **no labels.yaml entry at all** — reported as coverage="unverifiable",
    WARN, never silent (task spec).

Severity: a confirmed mismatch is BLOCK for Tier-1/2 official series, WARN for
Tier-3 or association-published series (`kernel.severity_for_mismatch`).
Sampled-but-nothing-archived is always a WARN "coverage gap" (expected for
most series today — the migrated history predates any real archive fetch;
see data/archive/README.md) and is counted honestly in the reported coverage
percentage rather than hidden.
"""
from __future__ import annotations

import time

from pipeline.audit.dg_archive import load_dg_archive, verify_observation
from pipeline.audit.html_archive import any_page_mentions_label, find_value_in_pages, load_archived_pages, pages_for_src
from pipeline.audit.kernel import severity_for_mismatch
from pipeline.audit.labels import label_candidates
from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.sampling import build_strata
from pipeline.audit.site_data import load_series_file

CHECK_ID = "gate_b.archive_independent_sample"

_CALIBER_FIELDS = {"single": ("m", "m_yoy"), "ytd": ("ytd", "ytd_yoy")}


def _is_dg_sourced(catalog_entry: dict) -> bool:
    return "data.stats.gov.cn/dg" in (catalog_entry.get("source", {}).get("url") or "")


def _verify_html(html_pages, catalog_entry: dict, label_entry: dict | None, obs: dict, caliber: str, check_id: str) -> list[Finding]:
    """One Finding PER present field of this caliber (m/m_yoy or ytd/ytd_yoy),
    each independently checked against the archived pages -- deliberately NOT
    "pass if ANY field matches somewhere", which would let a genuinely wrong
    `m` hide behind a still-correct `m_yoy` on the same observation (caught
    empirically while testing: corrupting only nbs-retail-total's `m` at
    2026-05 was silently absolved because `m_yoy` was untouched and still
    findable in the archived page)."""
    series_id, period = catalog_entry["id"], obs["period"]
    labels = label_candidates(label_entry)
    tier = catalog_entry.get("tier")
    fields_present = [f for f in _CALIBER_FIELDS[caliber] if obs.get(f) is not None]
    if not fields_present:
        return []

    if not labels:
        return [
            Finding(
                check=check_id,
                status="warn",
                series=series_id,
                period=period,
                field=field_name,
                tier=tier,
                note="coverage=unverifiable: no usable label (missing from labels.yaml, or no_source_text)",
            )
            for field_name in fields_present
        ]

    candidate_pages = pages_for_src(html_pages, obs.get("src"), period=period)
    if not candidate_pages or not any_page_mentions_label(candidate_pages, labels):
        # Either no page was even plausibly archived for this observation's
        # release (pages_for_src's date-narrowed empty result), or none of the
        # candidate pages ever mention this series' label at all -- either
        # way that is an honest "never archived here", not "archived but
        # wrong": see html_archive.any_page_mentions_label's docstring for why
        # this gate exists (without it, an unrelated page in a broad fallback
        # pool could turn a coverage gap into a false mismatch).
        return [
            Finding(
                check=check_id,
                status="warn",
                series=series_id,
                period=period,
                field=field_name,
                tier=tier,
                note="coverage gap: no archived HTML page mentions this series for this observation",
            )
            for field_name in fields_present
        ]

    findings = []
    for field_name in fields_present:
        value = obs[field_name]
        matched, page, evidence, scale = find_value_in_pages(candidate_pages, labels, value)
        if matched:
            findings.append(
                Finding(
                    check=check_id,
                    status="pass",
                    series=series_id,
                    period=period,
                    tier=tier,
                    field=field_name,
                    source=str(page.path) if page else None,
                    evidence=evidence,
                    rule=scale,
                )
            )
        else:
            findings.append(
                Finding(
                    check=check_id,
                    status=severity_for_mismatch(catalog_entry),
                    series=series_id,
                    period=period,
                    tier=tier,
                    field=field_name,
                    observed=value,
                    note=f"value not found in {len(candidate_pages)} candidate archived page(s) near label {labels[0]!r}",
                )
            )
    return findings


def _verify_panel_sample(html_pages, catalog_entry: dict, panel: dict, period: str, check_id: str) -> list[Finding]:
    """70-city panel: sample one (city, metric) cell at `period` per metric,
    fuzzy-matched against archived pages using the CITY NAME as the label (see
    module docstring). No real archive exists for property releases yet
    (confirmed: data/archive/ has no non-dg source directories), so in
    practice this reports coverage gaps honestly today; the path is exercised
    against a synthetic fixture in test_audit_dg_and_archive_sample.py."""
    findings = []
    if period not in panel.get("periods", []):
        return findings
    idx = panel["periods"].index(period)
    dims = panel["dimensions"]
    outer_dim = list(dims.keys())[0]
    cities = dims[outer_dim]
    metrics = dims[list(dims.keys())[1]]
    if not cities:
        return findings
    city = cities[idx % len(cities)]
    for metric in metrics:
        cell = (panel["cells"].get(city, {}).get(metric, {}) or {}).get("m", [])
        value = cell[idx] if idx < len(cell) else None
        if value is None:
            continue
        metric_label = "新建商品住宅销售价格" if metric == "new_home" else "二手住宅销售价格"
        matched, page, evidence, scale = find_value_in_pages(html_pages, [city], value)
        if matched:
            findings.append(
                Finding(
                    check=check_id,
                    status="pass",
                    panel=catalog_entry["id"],
                    period=period,
                    field=f"{city}.{metric}.m",
                    source=str(page.path) if page else None,
                    evidence=evidence,
                    rule=scale,
                )
            )
        else:
            findings.append(
                Finding(
                    check=check_id,
                    status="warn",
                    panel=catalog_entry["id"],
                    period=period,
                    field=f"{city}.{metric}.m ({metric_label})",
                    tier=catalog_entry.get("tier"),
                    observed=value,
                    note="coverage gap: no archived per-city page found for this period",
                )
            )
    return findings


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    series_by_id = ctx.series_by_id()

    strata = build_strata(
        catalog=ctx.catalog,
        series_by_id=series_by_id,
        section_bundles=ctx.section_bundles,
        repo_root=ctx.repo_root,
        rng=ctx.rng,
        samples_per_section=ctx.samples_per_section,
    )
    for skipped in strata.skipped:
        findings.append(Finding(check=CHECK_ID, status="skip", note=f"stratum {skipped!r} not discoverable this run; skipped"))

    dg_index = load_dg_archive(ctx.data_dir / "archive" / "dg")
    html_pages = load_archived_pages(ctx.data_dir / "archive")
    series_cache: dict[str, dict | None] = {}

    def _series(series_id: str) -> dict | None:
        if series_id not in series_cache:
            catalog_entry = series_by_id.get(series_id)
            series_cache[series_id] = load_series_file(ctx.data_dir, catalog_entry) if catalog_entry else None
        return series_cache[series_id]

    seen: set[tuple[str, str, str]] = set()
    coverage = {"verified": 0, "mismatch": 0, "gap": 0, "unverifiable": 0, "derived_skip": 0, "no_observation": 0}

    for item in strata.all_items():
        key = (item.series_id, item.period, item.caliber)
        if key in seen:
            continue
        seen.add(key)

        catalog_entry = series_by_id.get(item.series_id)
        if catalog_entry is None:
            continue

        if catalog_entry.get("panel"):
            panel = _series(item.series_id)
            if panel is not None:
                panel_findings = _verify_panel_sample(html_pages, catalog_entry, panel, item.period, CHECK_ID)
                findings.extend(panel_findings)
                for f in panel_findings:
                    coverage["verified" if f.status == "pass" else "gap"] += 1
            continue

        if catalog_entry.get("derived"):
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="skip",
                    series=item.series_id,
                    period=item.period,
                    tier=catalog_entry.get("tier"),
                    note="derived series; independently re-verified by gate_b.derived_recompute, not archive fuzzy-match",
                )
            )
            coverage["derived_skip"] += 1
            continue

        series = _series(item.series_id)
        if series is None:
            coverage["no_observation"] += 1
            continue
        index_by_period = {o["period"]: o for o in series.get("observations", [])}
        obs = index_by_period.get(item.period)
        if obs is None:
            coverage["no_observation"] += 1
            continue

        label_entry = ctx.labels.get(item.series_id)
        if label_entry is None:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="warn",
                    series=item.series_id,
                    period=item.period,
                    tier=catalog_entry.get("tier"),
                    note="coverage=unverifiable: series has no pipeline/audit/labels.yaml entry",
                )
            )
            coverage["unverifiable"] += 1
            continue
        if label_entry.get("no_source_text"):
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="skip",
                    series=item.series_id,
                    period=item.period,
                    tier=catalog_entry.get("tier"),
                    note="no raw source ever prints this value verbatim; see gate_b.derived_recompute",
                )
            )
            coverage["derived_skip"] += 1
            continue

        if _is_dg_sourced(catalog_entry):
            item_findings = [verify_observation(dg_index, catalog_entry, label_entry, obs, check_id=CHECK_ID)]
        else:
            # One Finding per present measure field (m/m_yoy or ytd/ytd_yoy),
            # independently verified -- see _verify_html's docstring for why
            # this must not collapse to "pass if any field matches".
            item_findings = _verify_html(html_pages, catalog_entry, label_entry, obs, item.caliber, CHECK_ID)
        findings.extend(item_findings)
        for finding in item_findings:
            if finding.status == "pass":
                coverage["verified"] += 1
            elif finding.status == "block" or (finding.status == "warn" and finding.observed is not None):
                coverage["mismatch"] += 1
            else:
                coverage["gap"] += 1

    total_sampled = sum(coverage.values())
    denom = coverage["verified"] + coverage["mismatch"] + coverage["gap"]
    coverage_pct = round(100.0 * coverage["verified"] / denom, 1) if denom else None
    # Purely informational roll-up -- deliberately never "block" itself (that
    # would double-count against the real per-item block findings already in
    # this list; CheckReport.has_block() already scans all of them).
    findings.append(
        Finding(
            check=CHECK_ID,
            status="warn" if coverage["mismatch"] else "pass",
            note=(
                f"sampled {total_sampled} points across {len(strata.items)} strata: "
                f"{coverage['verified']} verified, {coverage['mismatch']} mismatched, {coverage['gap']} archive-coverage gaps, "
                f"{coverage['unverifiable']} unverifiable (no label), {coverage['derived_skip']} derived/no-source-text (skipped), "
                f"{coverage['no_observation']} sample points with no matching observation. "
                f"coverage% (verified / attempted-with-archive) = {coverage_pct}"
            ),
        )
    )

    report = CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
    report.extra["coverage"] = coverage
    report.extra["coverage_pct"] = coverage_pct
    return report
