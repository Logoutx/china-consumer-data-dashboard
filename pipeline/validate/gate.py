"""pipeline/validate/gate.py -- Gate A orchestrator: build a GateContext from
a staged directory (+ optional batch/config/calendar) and run every
gate_a.* check, producing a GateReport. Writes gate_report.json (machine-
readable) and gate_report.md (human summary, what runner.py prints) into the
staged directory.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.normalize import NormalizeReport
from pipeline.validate.batch import Batch, empty_batch
from pipeline.validate.checks import run_all
from pipeline.validate.config import (
    ReleaseCalendar,
    ValidationConfig,
    load_release_calendar,
    load_validation_config,
)
from pipeline.validate.context import GateContext
from pipeline.validate.model import GateReport
from pipeline.validate.staging import SeriesStore

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / "data"


def _load_catalog_by_id(catalog_path: Path) -> dict[str, dict]:
    if not catalog_path.exists():
        return {}
    with catalog_path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    return {entry["id"]: entry for entry in catalog.get("series", [])}


def _load_schemas(schemas_dir: Path) -> dict[str, dict]:
    schemas = {}
    for name, key in (("series.schema.json", "series"), ("panel.schema.json", "panel")):
        path = schemas_dir / name
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                schemas[key] = json.load(handle)
    return schemas


def build_context(
    staged_dir: Path,
    *,
    batch: Batch | None = None,
    real_data_dir: Path | None = None,
    config: ValidationConfig | None = None,
    calendar: ReleaseCalendar | None = None,
    touched_series: list[str] | None = None,
    requested_series: list[str] | None = None,
    missing_series: list[str] | None = None,
    normalize_report: NormalizeReport | None = None,
    archive_source: str | None = None,
    today: date | None = None,
) -> GateContext:
    staged_dir = Path(staged_dir)
    real_data_dir = Path(real_data_dir) if real_data_dir is not None else DEFAULT_DATA_DIR
    batch = batch if batch is not None else empty_batch()
    config = config if config is not None else load_validation_config()
    calendar = calendar if calendar is not None else load_release_calendar()

    staged_series_dir = staged_dir / "series"
    real_series_dir = real_data_dir / "series"
    store = SeriesStore(staged_series_dir, real_series_dir)

    if touched_series is None:
        touched_series = sorted(p.stem for p in store.all_staged_files())
    requested_series = requested_series if requested_series is not None else list(touched_series)
    missing_series = missing_series if missing_series is not None else []

    return GateContext(
        store=store,
        batch=batch,
        config=config,
        calendar=calendar,
        catalog_by_id=_load_catalog_by_id(real_data_dir / "catalog.json"),
        schemas=_load_schemas(real_data_dir / "schemas"),
        touched_series=list(touched_series),
        requested_series=requested_series,
        missing_series=missing_series,
        archive_dir=real_data_dir / "archive",
        archive_source=archive_source,
        normalize_report=normalize_report,
        today=today if today is not None else date.today(),
    )


def run_gate(
    staged_dir: Path,
    *,
    batch: Batch | None = None,
    real_data_dir: Path | None = None,
    config: ValidationConfig | None = None,
    calendar: ReleaseCalendar | None = None,
    touched_series: list[str] | None = None,
    requested_series: list[str] | None = None,
    missing_series: list[str] | None = None,
    normalize_report: NormalizeReport | None = None,
    archive_source: str | None = None,
    today: date | None = None,
    write_report: bool = True,
) -> GateReport:
    """Run every gate_a.* check against a staged directory. Returns a
    GateReport whose .exit_code is 0 (pass, warnings allowed) or 2 (blocked).
    Never writes anywhere under real_data_dir -- staged_dir is the only
    directory this function (or anything it calls) opens in write mode."""
    staged_dir = Path(staged_dir)
    ctx = build_context(
        staged_dir,
        batch=batch,
        real_data_dir=real_data_dir,
        config=config,
        calendar=calendar,
        touched_series=touched_series,
        requested_series=requested_series,
        missing_series=missing_series,
        normalize_report=normalize_report,
        archive_source=archive_source,
        today=today,
    )
    results = run_all(ctx)
    report = GateReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        staged_dir=str(staged_dir),
        release_id=ctx.batch.release_id,
        touched_series=ctx.touched_series,
        results=results,
    )
    if write_report:
        staged_dir.mkdir(parents=True, exist_ok=True)
        (staged_dir / "gate_report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staged_dir / "gate_report.md").write_text(report.to_markdown(), encoding="utf-8")
    return report
