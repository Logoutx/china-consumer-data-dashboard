"""Merge data/_backfill_catalog_fragment.json into data/catalog.json.

Idempotent, one-shot glue for the orchestrator to run ONCE the migration agent's
catalog.json exists (this backfill agent does not run it -- see pipeline/backfill/
REPORT.md for why: catalog.json is being built concurrently by another agent and this
script must not race it).

What it does:
  1. Load data/catalog.json (must already exist -- this script does not create one).
  2. Load data/_backfill_catalog_fragment.json (a flat list of catalog `entry` objects,
     written by pipeline/backfill/backfill.py).
  3. For each fragment entry: if an id already present in catalog["series"] is byte-
     identical, skip silently (safe to re-run). If the id is present but DIFFERENT,
     abort without writing anything and report the conflicting id (a real collision
     needs a human, not a silent overwrite). Otherwise append.
  4. Re-sort catalog["series"] by (section order per catalog["sections"], tier, id) --
     matching catalog.schema.json's documented sort order.
  5. Bump `version` (patch segment) and `generated_at`.
  6. Validate the merged result against data/schemas/catalog.schema.json. Only write
     data/catalog.json if it validates; otherwise abort and report the errors.

Usage:  python3 -m pipeline.backfill.merge_fragment
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.migrate.jsonio import load_json, write_json  # noqa: E402
from pipeline.migrate.schema_validator import validate as schema_validate  # noqa: E402

CATALOG_PATH = REPO_ROOT / "data" / "catalog.json"
FRAGMENT_PATH = REPO_ROOT / "data" / "_backfill_catalog_fragment.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "data" / "schemas" / "catalog.schema.json"


def _bump_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    return version  # non-semver version string -- leave it, not this script's job to invent a scheme


def merge() -> int:
    if not CATALOG_PATH.exists():
        print(f"ABORT: {CATALOG_PATH} does not exist yet -- run this after the migration agent's catalog lands.")
        return 1
    if not FRAGMENT_PATH.exists():
        print(f"ABORT: {FRAGMENT_PATH} does not exist -- nothing to merge.")
        return 1

    catalog = load_json(CATALOG_PATH)
    fragment = load_json(FRAGMENT_PATH)

    section_order = {s["id"]: s["order"] for s in catalog.get("sections", [])}
    by_id = {entry["id"]: entry for entry in catalog.get("series", [])}

    conflicts = []
    added = 0
    for entry in fragment:
        existing = by_id.get(entry["id"])
        if existing is None:
            by_id[entry["id"]] = entry
            added += 1
        elif existing != entry:
            conflicts.append(entry["id"])

    if conflicts:
        print("ABORT: the following ids exist in catalog.json with DIFFERENT content than the fragment -- "
              "resolve by hand, this script will not silently overwrite:")
        for cid in conflicts:
            print(f"  - {cid}")
        return 1

    merged_series = sorted(
        by_id.values(),
        key=lambda e: (section_order.get(e["section"], 999), e["tier"], e["id"]),
    )
    catalog["series"] = merged_series
    catalog["version"] = _bump_version(catalog.get("version", "0.0.0"))
    catalog["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    schema = json.loads(CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = schema_validate(catalog, schema)
    if errors:
        print("ABORT: merged catalog fails schema validation, NOT writing:")
        for e in errors[:20]:
            print(f"  - {e}")
        return 1

    write_json(CATALOG_PATH, catalog)
    print(f"Merged {added} new series into {CATALOG_PATH} (0 conflicts). "
          f"catalog now has {len(merged_series)} series, version {catalog['version']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(merge())
