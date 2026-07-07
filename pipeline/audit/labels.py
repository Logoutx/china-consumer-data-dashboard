"""Loader for pipeline/audit/labels.yaml -- see that file's own header for the
schema and curation notes. Uses PyYAML (a project dependency already -- see
requirements.txt / pipeline/config/field_map.yaml) directly; this is a
third-party library import, not a read of pipeline/config/field_map.yaml or an
import of a forbidden pipeline module.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LABELS_PATH = Path(__file__).resolve().parent / "labels.yaml"


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data.get("series", {})


def label_candidates(label_entry: dict | None) -> list[str]:
    """[label_zh, *alt_labels] in try-order, or [] if there is no entry at all
    (caller reports coverage=unverifiable) or the entry has no source text to
    match at all (no_source_text: true)."""
    if not label_entry or label_entry.get("no_source_text"):
        return []
    out = []
    if label_entry.get("label_zh"):
        out.append(label_entry["label_zh"])
    out.extend(label_entry.get("alt_labels") or [])
    return out
