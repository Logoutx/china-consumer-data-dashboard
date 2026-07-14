"""Tests for pipeline/dg_refresh.py -- focused on the two things the
2026-07-08 adversarial review flagged: per-family exception isolation (HIGH
bug 3) and gate-report persistence (MEDIUM bug 5). Network-free: FAMILY_STEPS
is monkeypatched to synthetic step functions, never DGClient's own HTTP
methods, so these tests never touch the live DG API. DGClient()/TreeCache.load()
themselves are still constructed for real (their __init__ paths only touch
local files -- data/archive/dg/'s own mkdir and the committed tree_cache.json
-- never the network; only a real step function's own client.tree_children()/
indicator_values() calls would).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipeline import dg_refresh as dgr
from pipeline.backfill.dg_client import DGClient as RealDGClient


def _write_series(path: Path, series_id: str, period: str, value: float) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "series/v1", "id": series_id, "name_zh": "x", "name_en": "x",
                "unit_zh": "%", "unit_en": "%", "value_type": "yoy_pct", "freq": "M",
                "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
                "coverage_note_zh": None,
                "observations": [{"period": period, "m": value, "src": "dg:test"}],
                "revisions": [], "breaks": [], "generated_at": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _wire_isolated_repo(monkeypatch, tmp_path: Path) -> Path:
    """A private data/series/ + data/catalog.json, isolated from the real
    repo, with exactly one series ("test-a") for the "ok" fake family below
    to report against."""
    series_dir = tmp_path / "data" / "series"
    series_dir.mkdir(parents=True)
    _write_series(series_dir / "test-a.json", "test-a", "2026-01", 1.0)

    catalog = {
        "schema": "catalog/v1", "version": "test", "generated_at": "2026-01-01T00:00:00Z",
        "sections": [{"id": "macro", "name_zh": "x", "name_en": "x", "order": 0}],
        "series": [
            {
                "id": "test-a", "name_zh": "x", "name_en": "x", "section": "macro", "tier": 1,
                "unit_zh": "%", "unit_en": "%", "value_type": "yoy_pct", "freq": "M",
                "calibers": ["single"], "source": {"agency": "nbs"}, "file": "data/series/test-a.json",
            }
        ],
    }
    catalog_path = tmp_path / "data" / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(dgr, "SERIES_DIR", series_dir)
    monkeypatch.setattr(dgr, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dgr, "VALIDATE_REPORTS_DIR", tmp_path / "validate_reports")
    return series_dir


def _fake_ok(client, cache, month_codes, quarter_codes):
    return {"test-a": {"2026-02": {"m": 2.0}}}


def _fake_ok_alt(client, cache, month_codes, quarter_codes):
    return {"test-a": {"2026-03": {"m": 3.0}}}


def _fake_raises_keyerror(client, cache, month_codes, quarter_codes):
    node = {"name": "x"}
    return node["_id"]  # KeyError -- NOT DGError/TreePathError, the old bug's blind spot


def _fake_raises_value_error(client, cache, month_codes, quarter_codes):
    raise ValueError("malformed tree node shape")


# -- HIGH 3: per-family exception isolation --------------------------------------


def test_one_family_raising_a_non_dg_exception_does_not_abort_the_others(monkeypatch, tmp_path, capsys):
    """Regression, 2026-07-08: the family loop used to catch only
    (DGError, TreePathError). A KeyError (or any other exception) from one
    family used to propagate out of run() entirely, losing every OTHER
    family's perfectly good data. Two families here raise different,
    non-DG-specific exceptions; a third succeeds -- the successful one's
    data must still reach stage/gate, and the run must not be treated as an
    all-failed fetch error."""
    _wire_isolated_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(
        dgr,
        "FAMILY_STEPS",
        [("Broken A", _fake_raises_keyerror), ("Broken B", _fake_raises_value_error), ("OK", _fake_ok)],
    )

    # no_gate=True: this test's minimal synthetic fixture has no schema file
    # / archive dir wired in (irrelevant to what's under test -- exception
    # isolation in the family loop, not Gate A's own separate findings).
    exit_code = dgr.run(dry_run=True, no_gate=True)

    assert exit_code == 0  # NOT the "every family failed" exit 1 -- one family DID succeed
    err = capsys.readouterr().err
    assert "Broken A" in err and "KeyError" in err
    assert "Broken B" in err and "ValueError" in err


def test_all_families_failing_is_still_treated_as_a_fetch_error(monkeypatch, tmp_path, capsys):
    _wire_isolated_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(dgr, "FAMILY_STEPS", [("Broken A", _fake_raises_keyerror), ("Broken B", _fake_raises_value_error)])

    exit_code = dgr.run(dry_run=True)

    assert exit_code == 1
    assert "every family failed" in capsys.readouterr().err


def test_two_healthy_families_both_land_despite_a_third_raising(monkeypatch, tmp_path):
    """Confirms the isolated family's data doesn't just avoid crashing --
    it's actually collected and staged alongside every other healthy family."""
    _wire_isolated_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(
        dgr, "FAMILY_STEPS", [("OK 1", _fake_ok), ("Broken", _fake_raises_keyerror), ("OK 2", _fake_ok_alt)]
    )

    exit_code = dgr.run(dry_run=False, no_gate=True)

    assert exit_code == 0
    data = json.loads((dgr.SERIES_DIR / "test-a.json").read_text(encoding="utf-8"))
    periods = {o["period"] for o in data["observations"]}
    assert {"2026-01", "2026-02", "2026-03"} <= periods  # original + both healthy families' new periods


# -- MEDIUM 5: gate report persistence -------------------------------------------


def test_gate_report_is_persisted_to_validate_reports(monkeypatch, tmp_path):
    _wire_isolated_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(dgr, "FAMILY_STEPS", [("OK", _fake_ok)])

    dgr.run(dry_run=True)

    dest_dir = tmp_path / "validate_reports" / "dg_refresh"
    assert (dest_dir / "gate_report.json").exists()
    assert (dest_dir / "gate_report.md").exists()


def test_persist_gate_report_is_a_noop_when_no_report_files_exist(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    dest = tmp_path / "validate_reports"
    import pipeline.dg_refresh as dgr_mod

    original = dgr_mod.VALIDATE_REPORTS_DIR
    try:
        dgr_mod.VALIDATE_REPORTS_DIR = dest
        dgr_mod._persist_gate_report(staged, "dg_refresh")
    finally:
        dgr_mod.VALIDATE_REPORTS_DIR = original
    assert not list((dest / "dg_refresh").glob("*")) if (dest / "dg_refresh").exists() else True


# -- archive manifest (2026-07-14): links raw DG captures to release_id ----------


def _fake_ok_with_capture(client, cache, month_codes, quarter_codes):
    """Simulates what a real family step's DGClient calls do: archive a raw
    response under client.archive_dir (DGClient._archive()'s own job, not
    reproduced here -- just its observable side effect for this test)."""
    (client.archive_dir / "indicators_deadbeef_20260714T000000000000Z.json").write_text("{}", encoding="utf-8")
    return {"test-a": {"2026-02": {"m": 2.0}}}


def test_manifest_written_before_staging_lists_every_capture_and_matches_release_id(monkeypatch, tmp_path):
    """Bug fixed 2026-07-14: Gate A correctly blocked the first dg_refresh
    run with genuinely new observations -- gate_a.archive_release_identity
    found no archive capture matching the run's release_id, because DGClient
    archives raw responses keyed only by an indicator hash + fetch
    timestamp, with no release_id anywhere. The manifest is the missing
    link: release_id, generated_at (from the run's own date context, not
    wall-clock randomness), and every capture filename this run produced."""
    _wire_isolated_repo(monkeypatch, tmp_path)
    archive_dir = tmp_path / "archive" / "dg"
    monkeypatch.setattr(dgr, "DGClient", lambda: RealDGClient(archive_dir=archive_dir))
    monkeypatch.setattr(dgr, "FAMILY_STEPS", [("OK", _fake_ok_with_capture)])

    exit_code = dgr.run(dry_run=True, no_gate=True, today=date(2026, 7, 14))

    assert exit_code == 0
    manifest_path = archive_dir / "manifest_dg-refresh-2026-07-14.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["release_id"] == "dg-refresh-2026-07-14"
    assert manifest["generated_at"] == "2026-07-14"  # run's own date context, not datetime.now()
    assert manifest["captures"] == ["indicators_deadbeef_20260714T000000000000Z.json"]


def test_manifest_release_id_matches_the_staged_batch(monkeypatch, tmp_path):
    """The other half of the fix: the manifest's own release_id must be
    exactly what the staged ParsedRelease/Batch carries, since that's what
    the (parallel) archive_release_identity update will match against."""
    from pipeline.validate.batch import batch_from_parsed_release
    from pipeline.validate.staging import stage_release

    _wire_isolated_repo(monkeypatch, tmp_path)
    archive_dir = tmp_path / "archive" / "dg"
    monkeypatch.setattr(dgr, "DGClient", lambda: RealDGClient(archive_dir=archive_dir))
    monkeypatch.setattr(dgr, "FAMILY_STEPS", [("OK", _fake_ok_with_capture)])

    calls = {}
    original_stage_release = stage_release

    def _spy_stage_release(parsed, field_map, series_dir, **kwargs):
        calls["release_id"] = parsed.release_id
        return original_stage_release(parsed, field_map, series_dir, **kwargs)

    monkeypatch.setattr(dgr, "stage_release", _spy_stage_release)

    dgr.run(dry_run=True, no_gate=True, today=date(2026, 7, 14))

    manifest = json.loads((archive_dir / "manifest_dg-refresh-2026-07-14.json").read_text(encoding="utf-8"))
    assert calls["release_id"] == manifest["release_id"] == "dg-refresh-2026-07-14"


def test_manifest_is_written_before_the_staged_gate_report(monkeypatch, tmp_path):
    """Ordering matters: the manifest must exist before stage/gate run, not
    just eventually. Proven by checking it's on disk the moment
    stage_release() is first called (see the spy above's pattern)."""
    from pipeline.validate.staging import stage_release

    _wire_isolated_repo(monkeypatch, tmp_path)
    archive_dir = tmp_path / "archive" / "dg"
    monkeypatch.setattr(dgr, "DGClient", lambda: RealDGClient(archive_dir=archive_dir))
    monkeypatch.setattr(dgr, "FAMILY_STEPS", [("OK", _fake_ok_with_capture)])

    seen_manifest_before_staging = {}
    original_stage_release = stage_release

    def _spy_stage_release(parsed, field_map, series_dir, **kwargs):
        seen_manifest_before_staging["exists"] = (archive_dir / f"manifest_{parsed.release_id}.json").exists()
        return original_stage_release(parsed, field_map, series_dir, **kwargs)

    monkeypatch.setattr(dgr, "stage_release", _spy_stage_release)

    dgr.run(dry_run=True, no_gate=True, today=date(2026, 7, 14))

    assert seen_manifest_before_staging["exists"] is True
