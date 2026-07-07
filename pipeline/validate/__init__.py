"""pipeline/validate -- Gate A: the ingest validation gate (docs/DATA-
CONTRACT.md section 11, "validate/ -- ingest gate (accuracy gate #1)").

Runner flow (this milestone's binding architecture):

    discover -> fetch(+archive) -> parse -> stage -> Gate A -> write (on pass)

`stage` (pipeline/validate/staging.py) dry-run-merges a ParsedRelease into a
private temp copy of only the series files it touches, reusing
pipeline/normalize.py's own apply_parsed_release() rather than duplicating
merge logic. Gate A (pipeline/validate/gate.py) then runs all 22 gate_a.*
checks (pipeline/validate/checks/) against that staged copy plus the
NormalizedBatch (pipeline/validate/batch.py) this run produced, and either
returns exit 0 (pass; warnings allowed) or exit 2 (blocked) -- real data/ is
never written to until a caller (runner.py) sees exit 0 and calls
staging.promote_to_real() itself.

Standalone use: `python -m pipeline.validate --staged <dir> --batch
<parsed_release.json>` (see __main__.py).

Config: pipeline/config/validation.yaml (thresholds, per-series/value_type
overrides, known disagreements, source pairs) and
pipeline/config/release_calendar.yaml (expected release windows), loaded via
pipeline/validate/config.py.
"""
from __future__ import annotations

from pipeline.validate.batch import Batch, BatchItem, batch_from_parsed_release, dump_batch, empty_batch, load_batch
from pipeline.validate.config import ReleaseCalendar, ValidationConfig, load_release_calendar, load_validation_config
from pipeline.validate.context import GateContext
from pipeline.validate.gate import build_context, run_gate
from pipeline.validate.model import BLOCK, PASS, SKIP, WARN, CheckResult, Finding, GateReport
from pipeline.validate.staging import SeriesStore, StageResult, promote_to_real, stage_release

__all__ = [
    "Batch",
    "BatchItem",
    "batch_from_parsed_release",
    "dump_batch",
    "empty_batch",
    "load_batch",
    "ReleaseCalendar",
    "ValidationConfig",
    "load_release_calendar",
    "load_validation_config",
    "GateContext",
    "build_context",
    "run_gate",
    "BLOCK",
    "PASS",
    "SKIP",
    "WARN",
    "CheckResult",
    "Finding",
    "GateReport",
    "SeriesStore",
    "StageResult",
    "promote_to_real",
    "stage_release",
]
