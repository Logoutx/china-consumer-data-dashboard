"""pipeline/validate/staging.py -- the "stage" step of runner.py's
discover -> fetch(+archive) -> parse -> stage -> Gate A -> write flow
(docs/DATA-CONTRACT.md section 11, this milestone's binding architecture).

Staging means: dry-run with respect to the REAL data/series/ tree (never
opened for writing), but a REAL merge against a private temp copy, reusing
pipeline/normalize.py's own apply_parsed_release() rather than re-implementing
merge semantics here. Concretely:

    1. Figure out which series this release's rows map to (via field_map --
       exactly the lookup normalize.py itself performs).
    2. Copy just those series files from data/series/ into <staged>/series/.
    3. Run apply_parsed_release(..., dry_run=False) against the COPY.

The real data/series/ directory is only ever opened for reading in this
module (Path.exists()/shutil.copy2 as the read side); no code path here ever
opens a file under `real_series_dir` in write mode. That is what guarantees
"a BLOCK leaves data/ byte-identical" -- there is no rollback to perform
because nothing in data/ was ever written to begin with. promote_to_real()
is the one function that writes into the real tree, and callers (runner.py)
must only call it after Gate A has passed.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.normalize import NormalizeReport, apply_parsed_release


@dataclass
class StageResult:
    staged_dir: Path
    staged_series_dir: Path
    report: NormalizeReport
    touched_series: list[str]  # series ids that existed on disk and were staged
    requested_series: list[str]  # every series id this release's rows map to
    missing_series: list[str]  # mapped id with no file on disk (nothing to stage)
    unmapped_fields: list[str]  # source_fields absent from field_map


def _touched_series_ids(parsed, field_map: dict[str, dict[str, str]]) -> list[str]:
    source_map = field_map.get(parsed.source, {})
    ids: list[str] = []
    for row in parsed.rows:
        series_id = source_map.get(row.source_field)
        if series_id is not None and series_id not in ids:
            ids.append(series_id)
    return sorted(ids)


def stage_release(
    parsed,
    field_map: dict[str, dict[str, str]],
    real_series_dir: Path,
    *,
    staged_root: Path | None = None,
    revised_on: str | None = None,
) -> StageResult:
    """Stage one ParsedRelease against real_series_dir (read-only) into a
    fresh (or caller-supplied) temp directory, returning the merge report
    computed against the STAGED copy."""
    requested = _touched_series_ids(parsed, field_map)
    staged_root = Path(staged_root) if staged_root is not None else Path(tempfile.mkdtemp(prefix="gate-a-"))
    staged_series_dir = staged_root / "series"
    staged_series_dir.mkdir(parents=True, exist_ok=True)

    touched: list[str] = []
    for series_id in requested:
        src = real_series_dir / f"{series_id}.json"
        if src.exists():
            shutil.copy2(src, staged_series_dir / f"{series_id}.json")
            touched.append(series_id)

    report = apply_parsed_release(parsed, staged_series_dir, field_map, revised_on=revised_on, dry_run=False)

    return StageResult(
        staged_dir=staged_root,
        staged_series_dir=staged_series_dir,
        report=report,
        touched_series=touched,
        requested_series=requested,
        missing_series=sorted(set(report.missing_series)),
        unmapped_fields=sorted(set(report.unmapped_fields)),
    )


def promote_to_real(staged_series_dir: Path, real_series_dir: Path, touched_series: list[str]) -> list[str]:
    """Copy each touched series' staged (post-merge) file back onto the real
    tree. Only call this after Gate A has passed -- this is the one function
    in the whole validate package that writes into data/series/."""
    real_series_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for series_id in touched_series:
        src = staged_series_dir / f"{series_id}.json"
        if not src.exists():
            continue
        shutil.copy2(src, real_series_dir / f"{series_id}.json")
        written.append(series_id)
    return written


class SeriesStore:
    """Read-only accessor checks use to resolve a series id to a dict: staged
    copy first (this run's proposed new state), falling back to the real,
    pre-run file (for sibling series a check needs for context but that this
    run didn't touch -- e.g. sum-of-parts needing the other component
    series). `load_real_only` deliberately skips the staged copy -- it is
    what gate_a.revision_integrity uses to recover "the value on file before
    this run" once the staged file has already overwritten it in place."""

    def __init__(self, staged_series_dir: Path, real_series_dir: Path):
        self.staged_series_dir = Path(staged_series_dir)
        self.real_series_dir = Path(real_series_dir)
        self._cache: dict[str, dict | None] = {}
        self._real_cache: dict[str, dict | None] = {}

    @staticmethod
    def _read(path: Path) -> dict | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def load(self, series_id: str) -> dict | None:
        if series_id not in self._cache:
            data = self._read(self.staged_series_dir / f"{series_id}.json")
            if data is None:
                data = self._read(self.real_series_dir / f"{series_id}.json")
            self._cache[series_id] = data
        return self._cache[series_id]

    def is_staged(self, series_id: str) -> bool:
        return (self.staged_series_dir / f"{series_id}.json").exists()

    def load_real_only(self, series_id: str) -> dict | None:
        if series_id not in self._real_cache:
            self._real_cache[series_id] = self._read(self.real_series_dir / f"{series_id}.json")
        return self._real_cache[series_id]

    def all_staged_files(self) -> list[Path]:
        if not self.staged_series_dir.exists():
            return []
        return sorted(self.staged_series_dir.glob("*.json"))
