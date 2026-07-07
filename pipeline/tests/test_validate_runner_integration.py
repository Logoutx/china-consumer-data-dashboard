"""Integration tests for pipeline/runner.py's Gate A wiring: discover -> fetch
-> parse -> stage -> Gate A -> write. Exercises the three outcomes the task
spec calls for: a clean pass promotes to data/series/, a BLOCK leaves it
untouched, and --no-gate is a loud, logged override that writes anyway.

Uses the same real CPI fixture HTML + discover/fetch monkeypatching pattern
as pipeline/tests/test_runner.py, but with a non-empty SERIES_DIR (a
pre-existing "nbs-cpi.json" placeholder-id series, matching field_map.yaml's
own documented ids -- see its module docstring) so staging actually has
something to merge and Gate A something to evaluate."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from pipeline import runner as runner_module
from pipeline.discover import Candidate

FIXTURE_HTML = (Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html").read_text(encoding="utf-8")
REAL_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "schemas"


@dataclass
class FakeFetchResult:
    text: str
    archive_path: Path


def _wire_common(monkeypatch, tmp_path):
    def fake_discover_nbs(pattern):
        return [Candidate(url="https://example.invalid/cpi", title="2026年5月份居民消费价格同比上涨1.2%", period_hint="2026-05")]

    def fake_fetch_and_archive(url, *, source, slug, session=None):
        archive_path = tmp_path / "data" / "archive" / source / "20260610_test-release.html"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(FIXTURE_HTML, encoding="utf-8")
        return FakeFetchResult(text=FIXTURE_HTML, archive_path=archive_path)

    monkeypatch.setattr(runner_module, "discover_nbs", fake_discover_nbs)
    monkeypatch.setattr(runner_module, "fetch_and_archive", fake_fetch_and_archive)
    monkeypatch.setattr(runner_module, "load_field_map", lambda: {"nbs-cpi": {"居民消费价格": "nbs-cpi"}})

    series_dir = tmp_path / "data" / "series"
    series_dir.mkdir(parents=True)
    monkeypatch.setattr(runner_module, "SERIES_DIR", series_dir)

    schemas_dir = tmp_path / "data" / "schemas"
    schemas_dir.mkdir(parents=True)
    shutil.copy(REAL_SCHEMAS_DIR / "series.schema.json", schemas_dir / "series.schema.json")
    shutil.copy(REAL_SCHEMAS_DIR / "panel.schema.json", schemas_dir / "panel.schema.json")

    catalog = {
        "schema": "catalog/v1", "version": "1.0.0-test", "generated_at": "2026-06-01T00:00:00Z",
        "sections": [{"id": "prices", "name_zh": "物价", "name_en": "Prices", "order": 0}],
        "series": [
            {
                "id": "nbs-cpi", "name_zh": "x", "name_en": "x", "section": "prices", "tier": 1,
                "unit_zh": "%", "unit_en": "%", "value_type": "index", "freq": "M", "calibers": ["single"],
                "source": {"agency": "nbs"}, "file": "data/series/nbs-cpi.json",
            }
        ],
    }
    (tmp_path / "data" / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    return series_dir


def _write_cpi_series(series_dir: Path, *, value_type: str = "index") -> Path:
    series = {
        "schema": "series/v1", "id": "nbs-cpi", "name_zh": "x", "name_en": "x",
        "unit_zh": "%", "unit_en": "%", "value_type": value_type, "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2026-04", "m": 102.0, "m_yoy": 2.0, "src": "rel:test-04"}],
        "revisions": [], "breaks": [], "generated_at": "2026-05-01T00:00:00Z",
    }
    path = series_dir / "nbs-cpi.json"
    path.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_clean_release_passes_gate_a_and_writes_to_series_dir(monkeypatch, tmp_path, capsys):
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="index")  # matches the catalog -- nothing should block

    exit_code = runner_module.run("nbs_cpi", dry_run=False)

    assert exit_code == 0
    data = json.loads((series_dir / "nbs-cpi.json").read_text(encoding="utf-8"))
    assert any(o["period"] == "2026-05" for o in data["observations"])
    out = capsys.readouterr().out
    assert "Gate A report" in out
    assert "wrote 1 series file" in out


def test_gate_report_is_persisted_to_validate_reports_on_a_pass(monkeypatch, tmp_path):
    """MEDIUM bug 5 (2026-07-08 adversarial review): update-data.yml's
    artifact-upload step has always pointed at validate_reports/, but
    run_gate() only ever wrote into the staged temp dir -- nothing copied it
    anywhere durable, so the artifact was always empty. Must persist on
    EVERY run, not just a blocked one (a human debugging locally benefits
    from the passing report too, and the next test below covers the block
    case)."""
    monkeypatch.setattr(runner_module, "VALIDATE_REPORTS_DIR", tmp_path / "validate_reports")
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="index")

    runner_module.run("nbs_cpi", dry_run=False)

    dest = tmp_path / "validate_reports" / "nbs_cpi"
    assert (dest / "gate_report.json").exists()
    assert (dest / "gate_report.md").exists()
    assert "Gate A report" in (dest / "gate_report.md").read_text(encoding="utf-8")


def test_gate_report_is_persisted_to_validate_reports_on_a_block(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "VALIDATE_REPORTS_DIR", tmp_path / "validate_reports")
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="rate_pct")  # deliberately disagrees with the catalog -- guaranteed BLOCK

    exit_code = runner_module.run("nbs_cpi", dry_run=False)

    assert exit_code == 2
    dest = tmp_path / "validate_reports" / "nbs_cpi"
    report = json.loads((dest / "gate_report.json").read_text(encoding="utf-8"))
    assert report["blocked"] is True


def test_a_catalog_mismatch_blocks_gate_a_and_leaves_data_untouched(monkeypatch, tmp_path, capsys):
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="rate_pct")  # deliberately disagrees with the catalog's "index"
    before = (series_dir / "nbs-cpi.json").read_text(encoding="utf-8")

    exit_code = runner_module.run("nbs_cpi", dry_run=False)

    assert exit_code == 2
    after = (series_dir / "nbs-cpi.json").read_text(encoding="utf-8")
    assert after == before  # BLOCKED -- data/ must be byte-identical to before this run
    err = capsys.readouterr().err
    assert "Gate A BLOCKED" in err
    assert "GATE_BLOCKED" in err  # machine-readable marker (docs/OPERATIONS.md), grep-able independent of exit code


def test_no_gate_overrides_a_block_and_writes_anyway(monkeypatch, tmp_path, capsys):
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="rate_pct")  # same guaranteed BLOCK as above

    exit_code = runner_module.run("nbs_cpi", dry_run=False, no_gate=True)

    assert exit_code == 0  # overridden -- exits clean
    data = json.loads((series_dir / "nbs-cpi.json").read_text(encoding="utf-8"))
    assert any(o["period"] == "2026-05" for o in data["observations"])  # written despite the BLOCK
    err = capsys.readouterr().err
    assert "no-gate override in effect" in err


def test_fixture_flag_registers_the_archive_capture_so_release_identity_passes(monkeypatch, tmp_path):
    """Regression, 2026-07-08: before this fix, --fixture bypassed
    fetch_and_archive() entirely, so gate_a.archive_release_identity always
    BLOCKed any new observation purely because no matching data/archive/
    capture existed -- a --fixture-mode-only artifact, not a real finding.
    --fixture must now register its own bytes as the archived capture (same
    (source, release_id)-keyed path a real fetch would use) so this check
    passes honestly."""
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="index")  # matches the catalog -- nothing else should block
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html"

    exit_code = runner_module.run("nbs_cpi", dry_run=True, fixture=fixture_path)

    assert exit_code == 0
    archived = tmp_path / "data" / "archive" / "nbs-cpi" / f"{fixture_path.stem}.html"
    assert archived.exists()
    assert archived.read_text(encoding="utf-8") == fixture_path.read_text(encoding="utf-8")


def test_dry_run_does_not_write_even_when_gate_a_passes(monkeypatch, tmp_path):
    series_dir = _wire_common(monkeypatch, tmp_path)
    _write_cpi_series(series_dir, value_type="index")
    before = (series_dir / "nbs-cpi.json").read_text(encoding="utf-8")

    exit_code = runner_module.run("nbs_cpi", dry_run=True)

    assert exit_code == 0
    after = (series_dir / "nbs-cpi.json").read_text(encoding="utf-8")
    assert after == before  # --dry-run: Gate A ran (and passed), but nothing gets promoted
