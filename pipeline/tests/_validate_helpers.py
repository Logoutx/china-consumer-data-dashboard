"""Shared test scaffolding for pipeline/tests/test_validate_*.py. Not itself a
test module (pytest only collects test_*.py). Builds a private, mutable copy
of pipeline/tests/fixtures/validate/data/ per test (never the committed
fixtures, never the real repo data/ tree) and assembles a GateContext/Batch
with minimal boilerplate.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from pipeline.validate import gate
from pipeline.validate.batch import Batch, BatchItem, empty_batch
from pipeline.validate.config import ReleaseCalendar, ValidationConfig

FIXTURES_DATA_DIR = Path(__file__).resolve().parent / "fixtures" / "validate" / "data"
FIXTURES_SERIES_DIR = FIXTURES_DATA_DIR / "series"


def load_fixture_series(series_id: str) -> dict:
    with (FIXTURES_SERIES_DIR / f"{series_id}.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def make_real_data_dir(tmp_path: Path) -> Path:
    """A private mutable copy of the committed fixture data/ tree."""
    dest = tmp_path / "data"
    shutil.copytree(FIXTURES_DATA_DIR, dest)
    return dest


def write_series(series_dir: Path, series_id: str, data: dict) -> None:
    """Writes to <series_dir>/<series_id>.json -- deliberately the CALLER-
    supplied id, not data['id'], so a test can stage a file whose own "id"
    field intentionally disagrees with its filename (gate_a.catalog_consistency)."""
    series_dir.mkdir(parents=True, exist_ok=True)
    with (series_dir / f"{series_id}.json").open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def make_test_config(**overrides) -> ValidationConfig:
    defaults = {
        "z_warn": 4.0,
        "z_block": 7.0,
        "yoy_base_tol_pp": 3.0,
        "unit_slip_factor": 100,
        "min_history": {"M": 24, "Q": 8},
        "revision_flood": {"max_per_release": 6, "max_fraction": 0.10, "panel_max_periods": 3},
        "yoy_band": [-40, 60],
        "sum_of_parts_rel_tol": 0.02,
        "shock_periods": [],
    }
    defaults.update(overrides.pop("defaults", {}) or {})
    return ValidationConfig(
        defaults=defaults,
        by_value_type=overrides.pop("by_value_type", {}) or {},
        by_series=overrides.pop("by_series", {}) or {},
        source_pairs=overrides.pop("source_pairs", []) or [],
        known_disagreements=overrides.pop("known_disagreements", []) or [],
        source_reliability=overrides.pop("source_reliability", {}) or {},
        orphan_ok=overrides.pop("orphan_ok", []) or [],
    )


def make_test_calendar(**sources) -> ReleaseCalendar:
    return ReleaseCalendar(sources=sources)


def touch(series_id: str, period: str, *, source_kind: str = "press", release_id: str = "rel:test", source_field: str | None = None, **measures) -> BatchItem:
    obs = {"period": period}
    for key in ("span", "flags"):
        if key in measures:
            obs[key] = measures.pop(key)
    obs.update(measures)
    return BatchItem(series_id=series_id, obs=obs, source_kind=source_kind, release_id=release_id, source_field=source_field)


def make_batch(items: list[BatchItem] | None = None, *, release_id: str = "rel:test", source: str = "nbs-retail", published_at: str | None = None, raw_source_fields=None, unmapped_source_fields=None) -> Batch:
    return Batch(
        release_id=release_id,
        items=items or [],
        panels=[],
        raw_source_fields=set(raw_source_fields or []),
        unmapped_source_fields=set(unmapped_source_fields or []),
        source=source,
        published_at=published_at,
    )


def make_context(
    tmp_path: Path,
    *,
    touched: list[str] | None = None,
    staged_overrides: dict[str, dict] | None = None,
    real_overrides: dict[str, dict] | None = None,
    batch: Batch | None = None,
    config: ValidationConfig | None = None,
    calendar: ReleaseCalendar | None = None,
    normalize_report=None,
    archive_source: str | None = None,
    today=None,
    missing_series: list[str] | None = None,
    archive_files: dict[str, list[str]] | None = None,
):
    """Build a GateContext against a private copy of the fixture data/ tree.

    `staged_overrides` maps series_id -> a (possibly mutated) series dict to
    write into the staged dir; any `touched` id not in staged_overrides is
    staged as an exact copy of the fixture's real file (the common "nothing
    about this series' history changed, we're just re-checking it" case).
    `real_overrides` does the same for the PRE-RUN "real" copy, independent of
    what gets staged -- needed for tests that must control both "what was on
    file before this run" and "what this run proposes" separately (revision
    integrity, calendar_expected). `archive_files` maps an archive source
    directory name -> a list of release-id stems to create empty .html files
    for under <real_data_dir>/archive/<source>/, for gate_a.archive_release_identity.
    """
    real_data_dir = make_real_data_dir(tmp_path)
    staged_dir = tmp_path / "staged"
    staged_series_dir = staged_dir / "series"
    staged_series_dir.mkdir(parents=True, exist_ok=True)

    for series_id, data in (real_overrides or {}).items():
        write_series(real_data_dir / "series", series_id, data)

    for source, release_ids in (archive_files or {}).items():
        archive_source_dir = real_data_dir / "archive" / source
        archive_source_dir.mkdir(parents=True, exist_ok=True)
        for release_id in release_ids:
            (archive_source_dir / f"{release_id}.html").write_text("<html></html>", encoding="utf-8")

    staged_overrides = staged_overrides or {}
    touched = list(touched) if touched is not None else list(staged_overrides.keys())

    for series_id in touched:
        if series_id in staged_overrides:
            write_series(staged_series_dir, series_id, staged_overrides[series_id])
            continue
        src = real_data_dir / "series" / f"{series_id}.json"
        if src.exists():
            shutil.copy2(src, staged_series_dir / f"{series_id}.json")

    return gate.build_context(
        staged_dir,
        batch=batch if batch is not None else empty_batch(),
        real_data_dir=real_data_dir,
        config=config if config is not None else make_test_config(),
        calendar=calendar if calendar is not None else make_test_calendar(),
        touched_series=touched,
        missing_series=missing_series or [],
        normalize_report=normalize_report,
        archive_source=archive_source,
        today=today,
    )
