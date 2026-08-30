"""pipeline/validate/config.py -- loaders + typed accessors for
pipeline/config/validation.yaml and pipeline/config/release_calendar.yaml.

Both files are plain YAML (pyyaml is already a project dependency, used by
pipeline/normalize.py); this module is the only place their shape is assumed,
so a future config-shape change touches one file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_VALIDATION_CONFIG_PATH = ROOT / "pipeline" / "config" / "validation.yaml"
DEFAULT_RELEASE_CALENDAR_PATH = ROOT / "pipeline" / "config" / "release_calendar.yaml"


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass
class KnownDisagreement:
    """One config-acknowledged exception: 'don't flag this (series, period)
    combination for these checks -- we already know why it disagrees.'"""

    series: str | None = None
    periods: list[str] | None = None  # None => applies to every period
    checks: list[str] | None = None  # None => applies to every check
    note: str = ""

    def applies(self, *, series_id: str, period: str | None, check_id: str) -> bool:
        if self.series is not None and self.series != series_id:
            return False
        if self.periods is not None and (period is None or period not in self.periods):
            return False
        if self.checks is not None and check_id not in self.checks:
            return False
        return True


@dataclass
class SourcePair:
    primary: str
    secondary: str
    tol: float
    series: list[list[str]] = field(default_factory=list)  # [[primary_id, secondary_id], ...]


@dataclass
class ValidationConfig:
    defaults: dict[str, Any] = field(default_factory=dict)
    by_value_type: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_series: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_pairs: list[SourcePair] = field(default_factory=list)
    known_disagreements: list[KnownDisagreement] = field(default_factory=list)
    source_reliability: dict[str, str] = field(default_factory=dict)
    orphan_ok: list[str] = field(default_factory=list)

    # -- accessors -----------------------------------------------------------

    def _series_override(self, series_id: str, key: str):
        return self.by_series.get(series_id, {}).get(key)

    def _value_type_override(self, value_type: str | None, key: str):
        if value_type is None:
            return None
        return self.by_value_type.get(value_type, {}).get(key)

    def get(self, series_id: str, value_type: str | None, key: str, fallback=None):
        """series override -> value_type override -> defaults -> fallback."""
        for value in (
            self._series_override(series_id, key),
            self._value_type_override(value_type, key),
            self.defaults.get(key),
        ):
            if value is not None:
                return value
        return fallback

    def z_thresholds(self, series_id: str, value_type: str | None) -> tuple[float, float]:
        warn = self.get(series_id, value_type, "z_warn", 4.0)
        block = self.get(series_id, value_type, "z_block", 7.0)
        return float(warn), float(block)

    def yoy_band(self, series_id: str, value_type: str | None) -> tuple[float, float]:
        band = self.get(series_id, value_type, "yoy_band", [-40, 60])
        return float(band[0]), float(band[1])

    def yoy_base_tol_pp(self, series_id: str, value_type: str | None) -> float:
        return float(self.get(series_id, value_type, "yoy_base_tol_pp", 3.0))

    def unit_slip_factor(self, series_id: str, value_type: str | None) -> float:
        return float(self.get(series_id, value_type, "unit_slip_factor", 100))

    def min_history(self, freq: str) -> int:
        table = self.defaults.get("min_history", {}) or {}
        return int(table.get(freq, 24 if freq == "M" else 8))

    def revision_flood(self) -> dict:
        return self.defaults.get("revision_flood", {}) or {"max_per_release": 6, "max_fraction": 0.10, "panel_max_periods": 3}

    def sum_of_parts_rel_tol(self) -> float:
        return float(self.defaults.get("sum_of_parts_rel_tol", 0.02))

    def shock_periods(self, series_id: str | None = None) -> set[str]:
        periods = set(self.defaults.get("shock_periods", []) or [])
        if series_id:
            periods |= set(self.by_series.get(series_id, {}).get("shock_periods", []) or [])
        return periods

    def is_seasonal(self, series_id: str) -> bool:
        return bool(self._series_override(series_id, "seasonal"))

    def is_known_disagreement(self, *, series_id: str, period: str | None, check_id: str) -> KnownDisagreement | None:
        for entry in self.known_disagreements:
            if entry.applies(series_id=series_id, period=period, check_id=check_id):
                return entry
        return None


def load_validation_config(path: Path | None = None) -> ValidationConfig:
    raw = _load_yaml(path or DEFAULT_VALIDATION_CONFIG_PATH)
    source_pairs = [
        SourcePair(primary=sp.get("primary"), secondary=sp.get("secondary"), tol=float(sp.get("tol", 0.0)), series=sp.get("series", []) or [])
        for sp in (raw.get("source_pairs") or [])
    ]
    known_disagreements = [
        KnownDisagreement(
            series=kd.get("series"),
            periods=kd.get("periods"),
            checks=kd.get("checks"),
            note=kd.get("note", ""),
        )
        for kd in (raw.get("known_disagreements") or [])
    ]
    return ValidationConfig(
        defaults=raw.get("defaults") or {},
        by_value_type=raw.get("by_value_type") or {},
        by_series=raw.get("by_series") or {},
        source_pairs=source_pairs,
        known_disagreements=known_disagreements,
        source_reliability=raw.get("source_reliability") or {},
        orphan_ok=raw.get("orphan_ok") or [],
    )


@dataclass
class CalendarWindow:
    freq: str = "M"
    window_days: tuple[int, int] | None = None
    lag_days: int | None = None
    quarterly_months: list[int] = field(default_factory=list)
    grace_days: int = 0


@dataclass
class ReleaseCalendar:
    sources: dict[str, CalendarWindow] = field(default_factory=dict)

    def get(self, key: str) -> CalendarWindow | None:
        return self.sources.get(key)


def load_release_calendar(path: Path | None = None) -> ReleaseCalendar:
    raw = _load_yaml(path or DEFAULT_RELEASE_CALENDAR_PATH)
    sources = {}
    for key, entry in raw.items():
        entry = entry or {}
        window = entry.get("window_days")
        sources[key] = CalendarWindow(
            freq=entry.get("freq", "M"),
            window_days=tuple(window) if window else None,
            lag_days=entry.get("lag_days"),
            quarterly_months=entry.get("quarterly_months", []) or [],
            grace_days=int(entry.get("grace_days", 0)),
        )
    return ReleaseCalendar(sources=sources)


# archive_source (runner.py SOURCES[...]["archive_source"]) -> release_calendar.yaml key
ARCHIVE_SOURCE_TO_CALENDAR_KEY = {
    "nbs-cpi": "cpi_ppi",
    "nbs-retail": "nbs_activity",
    "pbc-money": "pbc_money",
    "nbs-pmi": "pmi",
    "customs": "trade",
    "pbc-lpr": "lpr",
    "nbs-confidence": "consumer_confidence",
    "spb-express": "spb_post",
}
