"""pipeline/validate/batch.py -- the NormalizedBatch view Gate A checks read.

DATA-CONTRACT.md section 11.1 freezes NormalizedBatch as the normalize->build
handoff:

    { "release_id": "...",
      "series": [ { "series_id": "...", "obs": {...}, "provenance": {...} } ],
      "panels": [...] }

This module loads/builds exactly that shape (plus one addition -- an inferred
`source_kind` per item, see util.infer_source_kind) so gate_a.triangulate_dg_press
can tell a DG-sourced row from a press-release-sourced row without a schema
change. `batch_from_parsed_release` lets runner.py hand Gate A an in-memory
Batch straight from the ParsedRelease it already has, with no JSON round trip;
`load_batch`/`dump_batch` back the standalone `python -m pipeline.validate
--batch <file>` CLI path and this package's own tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.validate.util import MEASURE_NAMES, infer_source_kind


@dataclass
class BatchItem:
    """One (series_id, period) touched by this run, in canonical measure
    vocabulary, tagged with which ingestion path it came from."""

    series_id: str
    obs: dict[str, Any]  # period + span?/flags? + measure fields
    source_kind: str = "other"  # "dg" | "press" | "other"
    release_id: str | None = None
    source_field: str | None = None

    @property
    def period(self) -> str | None:
        return self.obs.get("period")

    def measure_value(self, measure: str):
        return self.obs.get(measure)


@dataclass
class Batch:
    release_id: str | None
    items: list[BatchItem] = field(default_factory=list)
    panels: list[dict] = field(default_factory=list)
    raw_source_fields: set[str] = field(default_factory=set)
    unmapped_source_fields: set[str] = field(default_factory=set)
    source: str | None = None  # ParsedRelease.source, e.g. "nbs-cpi" / "nbs-retail" / "pbc-money"
    published_at: str | None = None  # ParsedRelease.published_at, passed through verbatim

    def series_ids(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item.series_id not in seen:
                seen.append(item.series_id)
        return seen

    def items_for(self, series_id: str) -> list[BatchItem]:
        return [item for item in self.items if item.series_id == series_id]

    def periods_for(self, series_id: str) -> list[str]:
        periods = []
        for item in self.items_for(series_id):
            if item.period and item.period not in periods:
                periods.append(item.period)
        return periods


def batch_from_parsed_release(parsed, field_map: dict[str, dict[str, str]]) -> Batch:
    """Build a Batch straight from a ParsedRelease + the field_map used to
    normalize it -- mirrors normalize.py's own source_map.get(row.source_field)
    lookup exactly, so "what did this run touch" always agrees with what
    normalize.py actually wrote. Rows with no field_map mapping are dropped
    from `items` (nothing to validate a series/period against) but their raw
    Chinese label still lands in `raw_source_fields`, which is what
    gate_a.partial_parse_completeness and gate_a.catalog_consistency's
    unmapped-field listing key off -- deliberately independent of whether the
    mapping happens to resolve to a real catalog id today (see the module
    docstring in checks/completeness.py for why that independence matters)."""
    source_map = field_map.get(parsed.source, {})
    grouped: dict[tuple[str, str], dict] = {}
    field_of_item: dict[tuple[str, str], str] = {}
    raw_fields: set[str] = set()
    unmapped_fields: set[str] = set()
    kind = infer_source_kind(parsed.release_id) if parsed.release_id else "press"
    if kind == "other":
        kind = "press"  # every parser wired into runner.py today is a press-release path

    for row in parsed.rows:
        raw_fields.add(row.source_field)
        series_id = source_map.get(row.source_field)
        if series_id is None:
            unmapped_fields.add(row.source_field)
            continue
        key = (series_id, row.period)
        obs = grouped.setdefault(key, {"period": row.period})
        field_of_item[key] = row.source_field
        if row.span and row.span != 1:
            obs["span"] = row.span
        if row.flags:
            obs["flags"] = sorted(set(obs.get("flags", [])) | set(row.flags))
        if row.caliber_hint:
            obs[row.caliber_hint] = row.value

    items = [
        BatchItem(series_id=series_id, obs=obs, source_kind=kind, release_id=parsed.release_id, source_field=field_of_item.get((series_id, obs["period"])))
        for (series_id, _period), obs in grouped.items()
    ]
    return Batch(
        release_id=parsed.release_id,
        items=items,
        panels=[],
        raw_source_fields=raw_fields,
        unmapped_source_fields=unmapped_fields,
        source=parsed.source,
        published_at=parsed.published_at,
    )


def dump_batch(batch: Batch) -> dict:
    return {
        "release_id": batch.release_id,
        "source": batch.source,
        "published_at": batch.published_at,
        "series": [
            {
                "series_id": item.series_id,
                "obs": item.obs,
                "provenance": {"release_id": item.release_id, "source_field": item.source_field, "source_kind": item.source_kind},
            }
            for item in batch.items
        ],
        "panels": batch.panels,
        "source_fields": sorted(batch.raw_source_fields),
        "unmapped_source_fields": sorted(batch.unmapped_source_fields),
    }


def load_batch(path: Path) -> Batch:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    items = []
    for entry in raw.get("series", []):
        provenance = entry.get("provenance", {}) or {}
        kind = provenance.get("source_kind") or infer_source_kind(provenance.get("release_id") or raw.get("release_id"))
        items.append(
            BatchItem(
                series_id=entry["series_id"],
                obs={k: v for k, v in entry.get("obs", {}).items() if k == "period" or k in MEASURE_NAMES or k in ("span", "flags")},
                source_kind=kind,
                release_id=provenance.get("release_id"),
                source_field=provenance.get("source_field"),
            )
        )
    raw_fields = set(raw.get("source_fields", []) or [])
    unmapped_fields = set(raw.get("unmapped_source_fields", []) or [])
    return Batch(
        release_id=raw.get("release_id"),
        source=raw.get("source"),
        published_at=raw.get("published_at"),
        items=items,
        panels=raw.get("panels", []) or [],
        raw_source_fields=raw_fields,
        unmapped_source_fields=unmapped_fields,
    )


def empty_batch() -> Batch:
    return Batch(release_id=None, items=[], panels=[], raw_source_fields=set())
