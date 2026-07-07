"""Read-only JSON loaders for `data/` and `site-data/`.

Deliberately dumb: every function here is a plain JSON read (or a trivial path
join). No semantics — deriving a value, resolving "the correct previous
period", generating a takeaway sentence — lives here; that belongs to a check
module, and it must be *re-derived* independently rather than borrowed from
pipeline.build (forbidden import; see pipeline/audit/__init__.py).
"""
from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_data_file(data_dir: Path, file_field: str) -> Path:
    """Catalog `file` fields are repo-relative (e.g. "data/series/x.json") but
    --data points at data/ itself. Mirrors build.py's own _resolve_file_path
    rule -- reimplemented independently (it is two lines) rather than imported,
    since pipeline.build is on the forbidden-import list."""
    if file_field.startswith("data/"):
        file_field = file_field[len("data/") :]
    return data_dir / file_field


def load_catalog(data_dir: Path) -> dict:
    return load_json(data_dir / "catalog.json")


def load_annotations(data_dir: Path) -> dict:
    path = data_dir / "annotations.json"
    return load_json(path) if path.exists() else {}


def load_series_file(data_dir: Path, entry: dict) -> dict | None:
    path = resolve_data_file(data_dir, entry["file"])
    if not path.exists():
        return None
    return load_json(path)


def load_index(site_data_dir: Path) -> dict | None:
    path = site_data_dir / "index.json"
    return load_json(path) if path.exists() else None


def load_section_bundle(site_data_dir: Path, section_id: str) -> dict | None:
    path = site_data_dir / "sections" / f"{section_id}.json"
    return load_json(path) if path.exists() else None


def load_all_section_bundles(site_data_dir: Path, catalog: dict) -> dict[str, dict]:
    out = {}
    for section in catalog["sections"]:
        bundle = load_section_bundle(site_data_dir, section["id"])
        if bundle is not None:
            out[section["id"]] = bundle
    return out


def load_panel_bundle(site_data_dir: Path, panel_id: str) -> dict | None:
    path = site_data_dir / "panels" / f"{panel_id}.json"
    return load_json(path) if path.exists() else None


def make_panel_bundle_loader(site_data_dir: Path):
    cache: dict[str, dict | None] = {}

    def _load(panel_id: str) -> dict | None:
        if panel_id not in cache:
            cache[panel_id] = load_panel_bundle(site_data_dir, panel_id)
        return cache[panel_id]

    return _load


def panel_catalog_entries(catalog: dict) -> list[dict]:
    return [entry for entry in catalog["series"] if entry.get("panel")]


def non_panel_catalog_entries(catalog: dict) -> list[dict]:
    return [entry for entry in catalog["series"] if not entry.get("panel")]
