"""Tests for pipeline/validate/staging.py: stage_release() only ever reads
data/series/ (never writes it), promote_to_real() is the one function that
does, and a full stage-without-promote sequence (what runner.py does on a
Gate A BLOCK) leaves the real tree byte-identical."""
from __future__ import annotations

import json

from pipeline import ParsedRelease, ParsedRow
from pipeline.validate.staging import SeriesStore, promote_to_real, stage_release

FIELD_MAP = {"test-source": {"测试总额": "test-series-a", "测试指数": "test-series-b"}}


def _series(series_id: str) -> dict:
    return {
        "schema": "series/v1", "id": series_id, "name_zh": "x", "name_en": "x",
        "unit_zh": "亿元", "unit_en": "100M CNY", "value_type": "level", "freq": "M",
        "calibers": ["single"], "source": {"agency": "nbs"}, "derived": None,
        "coverage_note_zh": None,
        "observations": [{"period": "2026-04", "m": 100}, {"period": "2026-05", "m": 105}],
        "revisions": [], "breaks": [], "generated_at": "2026-05-01T00:00:00Z",
    }


def _real_series_dir(tmp_path):
    series_dir = tmp_path / "real" / "series"
    series_dir.mkdir(parents=True)
    for series_id in ("test-series-a", "test-series-b"):
        with (series_dir / f"{series_id}.json").open("w", encoding="utf-8") as handle:
            json.dump(_series(series_id), handle, ensure_ascii=False)
    return series_dir


def _release(rows: list[ParsedRow], *, release_id: str = "rel:test") -> ParsedRelease:
    return ParsedRelease(source="test-source", release_id=release_id, url="https://example.invalid", published_at="2026/06/16 10:00", period_hint="2026-06", rows=rows)


def test_stage_release_copies_only_touched_series(tmp_path):
    real_series_dir = _real_series_dir(tmp_path)
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=110, unit_raw="亿元", caliber_hint="m", period="2026-06")])
    result = stage_release(release, FIELD_MAP, real_series_dir)
    assert result.touched_series == ["test-series-a"]
    assert (result.staged_series_dir / "test-series-a.json").exists()
    assert not (result.staged_series_dir / "test-series-b.json").exists()


def test_stage_release_never_writes_to_the_real_directory(tmp_path):
    real_series_dir = _real_series_dir(tmp_path)
    before_a = (real_series_dir / "test-series-a.json").read_text(encoding="utf-8")
    before_b = (real_series_dir / "test-series-b.json").read_text(encoding="utf-8")
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=999999999, unit_raw="亿元", caliber_hint="m", period="2026-06")])
    stage_release(release, FIELD_MAP, real_series_dir)
    assert (real_series_dir / "test-series-a.json").read_text(encoding="utf-8") == before_a
    assert (real_series_dir / "test-series-b.json").read_text(encoding="utf-8") == before_b


def test_a_block_scenario_leaves_data_byte_identical_because_promote_was_never_called(tmp_path):
    """Mirrors exactly what runner.py does on a Gate A BLOCK: stage, inspect,
    and simply never call promote_to_real. There is no rollback step because
    nothing was ever written -- this test is the "staged-vs-real isolation"
    guarantee the task spec calls for."""
    real_series_dir = _real_series_dir(tmp_path)
    before_a = (real_series_dir / "test-series-a.json").read_text(encoding="utf-8")
    before_b = (real_series_dir / "test-series-b.json").read_text(encoding="utf-8")
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=999999999, unit_raw="亿元", caliber_hint="m", period="2026-06")])
    result = stage_release(release, FIELD_MAP, real_series_dir)
    assert (result.staged_series_dir / "test-series-a.json").exists()  # staged copy DOES reflect the absurd value...
    assert (real_series_dir / "test-series-a.json").read_text(encoding="utf-8") == before_a  # ...but data/ never saw it
    assert (real_series_dir / "test-series-b.json").read_text(encoding="utf-8") == before_b


def test_promote_to_real_writes_the_staged_result(tmp_path):
    real_series_dir = _real_series_dir(tmp_path)
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=999, unit_raw="亿元", caliber_hint="m", period="2026-06")])
    result = stage_release(release, FIELD_MAP, real_series_dir)
    written = promote_to_real(result.staged_series_dir, real_series_dir, result.touched_series)
    assert written == ["test-series-a"]
    data = json.loads((real_series_dir / "test-series-a.json").read_text(encoding="utf-8"))
    assert any(o["period"] == "2026-06" and o["m"] == 999 for o in data["observations"])
    # the untouched sibling is completely unaffected by promotion
    other = json.loads((real_series_dir / "test-series-b.json").read_text(encoding="utf-8"))
    assert [o["period"] for o in other["observations"]] == ["2026-04", "2026-05"]


def test_idempotent_rerun_produces_no_new_revisions(tmp_path):
    real_series_dir = _real_series_dir(tmp_path)
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=200, unit_raw="亿元", caliber_hint="m", period="2026-07")])

    first = stage_release(release, FIELD_MAP, real_series_dir)
    promote_to_real(first.staged_series_dir, real_series_dir, first.touched_series)

    second = stage_release(release, FIELD_MAP, real_series_dir)  # same release, staged again against the now-updated real dir
    assert second.report.new_observations == []
    assert second.report.revisions == []


def test_series_store_falls_back_to_real_for_untouched_series(tmp_path):
    real_series_dir = _real_series_dir(tmp_path)
    staged_series_dir = tmp_path / "staged" / "series"
    staged_series_dir.mkdir(parents=True)
    store = SeriesStore(staged_series_dir, real_series_dir)
    assert store.load("test-series-b") is not None  # not staged, but resolves via the real fallback
    assert not store.is_staged("test-series-b")
    assert store.load_real_only("test-series-b") == store.load("test-series-b")
