"""pipeline/validate/context.py -- GateContext: the one object every check
function receives. Split out from gate.py so checks/*.py can type-hint it
without importing the orchestrator (which imports the checks registry) --
avoids a circular import."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from pipeline.normalize import NormalizeReport
from pipeline.validate.batch import Batch
from pipeline.validate.config import ReleaseCalendar, ValidationConfig
from pipeline.validate.staging import SeriesStore


@dataclass
class GateContext:
    store: SeriesStore
    batch: Batch
    config: ValidationConfig
    calendar: ReleaseCalendar
    catalog_by_id: dict[str, dict]
    schemas: dict[str, dict]  # {"series": {...}, "panel": {...}}
    touched_series: list[str]
    requested_series: list[str]
    missing_series: list[str]
    archive_dir: Optional[Path] = None
    archive_source: Optional[str] = None
    normalize_report: Optional[NormalizeReport] = None
    today: date = field(default_factory=date.today)
    # Populated by gate.py after running checks 7/8, consumed by check 6:
    # (series_id, period) pairs where a cross-source comparison MATCHED this
    # run -- gate_a.seasonal_z demotes an otherwise-BLOCK z-score to WARN for
    # these, per the task spec ("unless a cross-source check confirmed the
    # same value this run").
    confirmed_cross_source_matches: set = field(default_factory=set)

    # -- series access --------------------------------------------------

    def load(self, series_id: str) -> dict | None:
        """Staged (this run's proposed) view, falling back to the real,
        pre-run file for context series this run didn't touch."""
        return self.store.load(series_id)

    def load_real(self, series_id: str) -> dict | None:
        """The pre-run file only, ignoring any staged copy -- 'what was on
        file before this run merged anything in'."""
        return self.store.load_real_only(series_id)

    def is_touched(self, series_id: str) -> bool:
        return series_id in self.touched_series

    def touched_series_dicts(self) -> list[tuple[str, dict]]:
        out = []
        for series_id in self.touched_series:
            data = self.store.load(series_id)
            if data is not None:
                out.append((series_id, data))
        return out

    def decimals_for(self, series_id: str) -> int:
        data = self.load(series_id)
        if data and data.get("decimals") is not None:
            return int(data["decimals"])
        cat = self.catalog_by_id.get(series_id)
        if cat and cat.get("decimals") is not None:
            return int(cat["decimals"])
        return 1

    def value_type_for(self, series_id: str) -> str | None:
        data = self.load(series_id)
        if data and data.get("value_type"):
            return data["value_type"]
        cat = self.catalog_by_id.get(series_id)
        return cat.get("value_type") if cat else None

    @property
    def effective_archive_source(self) -> str | None:
        """archive_source override if the caller set one (runner.py knows its
        own SOURCES[...]["archive_source"] mapping precisely), else fall back
        to the batch's own ParsedRelease.source -- identical for every source
        currently wired into runner.py (nbs-cpi/nbs-retail/pbc-money)."""
        return self.archive_source or self.batch.source

    def touched_periods(self, series_id: str, data: dict | None = None) -> set[str]:
        """Periods this run actually introduced/changed for series_id, per the
        batch. Falls back to 'just the latest observation on file' when the
        batch has nothing for this series (e.g. a check run standalone
        against a staged dir with no --batch) so per-check logic always has
        *something* to evaluate rather than silently checking nothing."""
        periods = set(self.batch.periods_for(series_id))
        if periods:
            return periods
        data = data if data is not None else self.load(series_id)
        observations = (data or {}).get("observations", [])
        return {observations[-1]["period"]} if observations else set()
