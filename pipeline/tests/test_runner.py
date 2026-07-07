"""Light smoke tests for pipeline/runner.py's CLI orchestration. The parser and
normalize contract tests already cover correctness in depth; this file only
checks that the pieces are wired together and that the "nothing new yet" /
unknown-source paths behave as the task requires (exit 0, no crash)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pipeline import runner as runner_module
from pipeline.discover import Candidate


def test_unknown_source_returns_exit_code_3(capsys):
    """Exit codes standardized 2026-07-08 (docs/OPERATIONS.md): 0 ok/no-op,
    2 Gate A block, 3 usage/unknown source -- previously both this case and a
    Gate A block returned 2, which collided once Gate A was wired in."""
    exit_code = runner_module.run("not_a_real_source", dry_run=True)
    assert exit_code == 3


def test_no_candidates_exits_zero_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(runner_module, "discover_nbs", lambda pattern: [])
    exit_code = runner_module.run("nbs_cpi", dry_run=True)
    assert exit_code == 0
    assert "no new release found" in capsys.readouterr().out


def test_happy_path_dry_run_wires_fetch_parse_normalize(monkeypatch, tmp_path, capsys):
    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html"
    ).read_text(encoding="utf-8")

    @dataclass
    class FakeFetchResult:
        text: str
        archive_path: Path

    def fake_discover_nbs(pattern):
        return [Candidate(url="https://example.invalid/cpi", title="2026年5月份居民消费价格同比上涨1.2%", period_hint="2026-05")]

    def fake_fetch_and_archive(url, *, source, slug, session=None):
        archive_path = tmp_path / "archive" / f"{source}.html"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(fixture, encoding="utf-8")
        return FakeFetchResult(text=fixture, archive_path=archive_path)

    monkeypatch.setattr(runner_module, "discover_nbs", fake_discover_nbs)
    monkeypatch.setattr(runner_module, "fetch_and_archive", fake_fetch_and_archive)
    monkeypatch.setattr(runner_module, "SERIES_DIR", tmp_path / "series")  # empty -- no real series files

    exit_code = runner_module.run("nbs_cpi", dry_run=True)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "period: 2026-05" in out
    assert "mapped series file(s) not found on disk" in out  # tmp_path/series is empty, as expected


# -- --fixture: offline field_map proof, no discover/fetch, no network -----------


def test_fixture_flag_bypasses_discover_and_fetch_entirely(monkeypatch, tmp_path, capsys):
    """--fixture must never call discover_nbs/fetch_and_archive at all -- both
    are monkeypatched to raise, so this test fails loudly if either is
    reached, proving the fixture path is a genuinely separate code path from
    the live discover -> fetch flow, not just a stubbed-out FakeFetchResult."""

    def _boom(*args, **kwargs):
        raise AssertionError("must not be called when --fixture is set")

    monkeypatch.setattr(runner_module, "discover_nbs", _boom)
    monkeypatch.setattr(runner_module, "fetch_and_archive", _boom)
    monkeypatch.setattr(runner_module, "SERIES_DIR", tmp_path / "series")

    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html"
    exit_code = runner_module.run("nbs_cpi", dry_run=True, fixture=fixture_path)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"using fixture: {fixture_path}" in out
    assert "period: 2026-05" in out


def test_fixture_flag_is_ignored_with_a_warning_for_dg_refresh(monkeypatch, tmp_path, capsys):
    """dg_refresh has its own, separate offline-testing story (a --lookback/
    --today override on pipeline.dg_refresh.run, not a raw HTML fixture) --
    passing --fixture alongside it must not crash, just warn and proceed."""
    monkeypatch.setattr(runner_module.dg_refresh, "run", lambda *, dry_run, no_gate: 0)

    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "nbs_cpi" / "2026-05_cpi.html"
    exit_code = runner_module.run("dg_refresh", dry_run=True, fixture=fixture_path)

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "--fixture has no effect for dg_refresh" in err
