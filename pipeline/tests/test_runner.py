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


def test_unknown_source_returns_exit_code_2(capsys):
    exit_code = runner_module.run("not_a_real_source", dry_run=True)
    assert exit_code == 2


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
