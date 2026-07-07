"""gate_b.dg_archive_sample — for every DG-sourced series (catalog
`source.url` pointing at data.stats.gov.cn/dg), samples observations and
matches them exactly against the raw `data/archive/dg/*.json` payloads
(indicators_*.json listing files + values_*.json per-indicator value files).
BLOCK on a genuine mismatch; WARN when the sample point simply isn't covered
by any archived response yet (a coverage gap, not a data error).

The actual matching (provenance `src` fast path, label-name fallback, raw vs
index-base-100 transform trial) lives in `dg_archive.verify_observation`,
shared verbatim with the DG branch of gate_b.archive_independent_sample so
both checks apply identical matching semantics — they differ only in which
observations they choose to sample (this check samples densely per DG series,
since the DG archive happens to be the one genuinely well-populated archive in
this repo today; archive_independent_sample's strata are a shallower
cross-cutting sample spanning ALL sources, DG and HTML alike).
"""
from __future__ import annotations

import time

from pipeline.audit.dg_archive import load_dg_archive, resolve_src_indicator_id, verify_observation
from pipeline.audit.kernel import close_enough, sample
from pipeline.audit.live_dg import fetch_live_value
from pipeline.audit.models import AuditContext, CheckReport, Finding
from pipeline.audit.site_data import load_series_file

CHECK_ID = "gate_b.dg_archive_sample"


def _is_dg_sourced(catalog_entry: dict) -> bool:
    return "data.stats.gov.cn/dg" in (catalog_entry.get("source", {}).get("url") or "")


def _live_repull(ctx: AuditContext, dg_index, dg_series: list[dict]) -> list[Finding]:
    """--live only: re-pull up to ctx.live_dg_cap already-sampled DG points
    straight from the live endpoint and compare against the archived value
    used to verify them, catching "archive itself is stale/wrong" rather than
    "site disagrees with archive" (which the offline path already covers)."""
    candidates = []
    for catalog_entry in dg_series:
        series = load_series_file(ctx.data_dir, catalog_entry)
        if not series:
            continue
        for obs in series.get("observations", []):
            indicator_id = resolve_src_indicator_id(obs.get("src"))
            row = dg_index.indicator_rows.get(indicator_id) if indicator_id else None
            cid = row.get("catalogid") if row else None
            if indicator_id and cid:
                candidates.append((catalog_entry, obs, indicator_id, cid))
    chosen = sample(candidates, ctx.rng, ctx.live_dg_cap)

    findings = []
    for catalog_entry, obs, indicator_id, cid in chosen:
        live_value = fetch_live_value(cid, indicator_id, obs["period"])
        if live_value is None:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="warn",
                    series=catalog_entry["id"],
                    period=obs["period"],
                    note="--live re-pull did not return a value (network issue or endpoint shape changed); not counted as a mismatch",
                )
            )
            continue
        archived_value = obs.get("m") if obs.get("m") is not None else obs.get("ytd")
        matched = close_enough(archived_value, live_value) or close_enough((archived_value or 0) + 100, live_value)
        findings.append(
            Finding(
                check=CHECK_ID,
                status="pass" if matched else "warn",
                series=catalog_entry["id"],
                period=obs["period"],
                field="live_repull",
                expected=archived_value,
                observed=live_value,
                note=None if matched else "live DG endpoint disagrees with the archived capture used to verify this point",
            )
        )
    return findings


def run(ctx: AuditContext) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []
    dg_index = load_dg_archive(ctx.data_dir / "archive" / "dg")

    dg_series = [entry for entry in ctx.catalog["series"] if not entry.get("panel") and _is_dg_sourced(entry)]
    checked_observations = 0

    for catalog_entry in dg_series:
        series = load_series_file(ctx.data_dir, catalog_entry)
        if series is None:
            findings.append(Finding(check=CHECK_ID, status="warn", series=catalog_entry["id"], note="series file missing/unreadable"))
            continue
        label_entry = ctx.labels.get(catalog_entry["id"])
        observations = series.get("observations", [])
        chosen = sample(observations, ctx.rng, ctx.samples_per_section)
        for obs in chosen:
            checked_observations += 1
            findings.append(verify_observation(dg_index, catalog_entry, label_entry, obs, check_id=CHECK_ID))

    if not ctx.offline and dg_series:
        findings.extend(_live_repull(ctx, dg_index, dg_series))

    passes = sum(1 for f in findings if f.status == "pass")
    if not dg_series:
        findings.append(Finding(check=CHECK_ID, status="skip", note="no DG-sourced series found in catalog"))
    elif not any(f.status == "block" for f in findings):
        findings.append(
            Finding(check=CHECK_ID, status="pass", note=f"{passes}/{checked_observations} sampled DG observations independently confirmed")
        )

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
