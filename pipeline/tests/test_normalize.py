"""Tests for pipeline/normalize.py against synthetic series fixtures under
pipeline/tests/fixtures/series/ -- NEVER the real data/series/ tree, which a
concurrent agent is writing in this same rebuild.

test-retail-total.json starts at the exact pre-revision state of DATA-CONTRACT
§3.4's own worked example (2026-04 ytd=165000, later revised to 165021), so the
revision-detection test below reproduces that documented scenario byte-for-byte.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import ParsedRelease, ParsedRow
from pipeline.normalize import apply_parsed_release, dump_series

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "series"

FIELD_MAP = {
    "test-source": {
        "测试总额": "test-retail-total",
        "测试指数": "test-cpi-rebase",
        "未知字段": None,  # placeholder; overwritten below to actually be absent
    }
}
# An unmapped field must simply be *absent* from field_map, not mapped to None.
del FIELD_MAP["test-source"]["未知字段"]


@pytest.fixture()
def series_dir(tmp_path: Path) -> Path:
    """A private, mutable copy of the committed synthetic fixtures for one test."""
    target = tmp_path / "series"
    target.mkdir()
    for source in FIXTURES_DIR.glob("*.json"):
        shutil.copy(source, target / source.name)
    return target


def _load(series_dir: Path, series_id: str) -> dict:
    return json.loads((series_dir / f"{series_id}.json").read_text(encoding="utf-8"))


def _release(rows: list[ParsedRow], *, release_id: str = "rel:test") -> ParsedRelease:
    return ParsedRelease(
        source="test-source",
        release_id=release_id,
        url="https://example.invalid/test",
        published_at="2026-06-16",
        period_hint="2026-05",
        rows=rows,
    )


def test_new_observation_is_appended(series_dir):
    release = _release(
        [
            ParsedRow(source_field="测试总额", raw_label="m", value=41090, unit_raw="亿元", caliber_hint="m", period="2026-05"),
            ParsedRow(source_field="测试总额", raw_label="m_yoy", value=-0.6, unit_raw="%", caliber_hint="m_yoy", period="2026-05"),
            ParsedRow(source_field="测试总额", raw_label="ytd", value=206031, unit_raw="亿元", caliber_hint="ytd", period="2026-05"),
            ParsedRow(source_field="测试总额", raw_label="ytd_yoy", value=1.4, unit_raw="%", caliber_hint="ytd_yoy", period="2026-05"),
        ]
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP)

    assert report.new_observations == [("test-retail-total", "2026-05")]
    assert report.revisions == []
    assert report.missing_series == []
    assert report.unmapped_fields == []

    series = _load(series_dir, "test-retail-total")
    periods = [obs["period"] for obs in series["observations"]]
    assert periods == ["2026-02", "2026-03", "2026-04", "2026-05"]  # stays sorted
    new_obs = series["observations"][-1]
    assert new_obs["m"] == 41090
    assert new_obs["m_yoy"] == -0.6
    assert new_obs["ytd"] == 206031
    assert new_obs["ytd_yoy"] == 1.4


def test_revision_is_logged_and_observation_updated(series_dir):
    """Reproduces DATA-CONTRACT §3.4's worked revision exactly: 2026-04 ytd
    165000 -> 165021, revised_on the release date, old print not lost."""
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="ytd", value=165021, unit_raw="亿元", caliber_hint="ytd", period="2026-04")],
        release_id="rel:20260616",
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP, revised_on="2026-06-16")

    assert report.new_observations == []
    assert len(report.revisions) == 1
    revision = report.revisions[0]
    assert revision == {
        "period": "2026-04",
        "measure": "ytd",
        "old": 165000,
        "new": 165021,
        "revised_on": "2026-06-16",
        "source": "rel:20260616",
        "series_id": "test-retail-total",
    }

    series = _load(series_dir, "test-retail-total")
    updated = next(obs for obs in series["observations"] if obs["period"] == "2026-04")
    assert updated["ytd"] == 165021  # observation holds the CURRENT value...
    assert series["revisions"] == [
        {"period": "2026-04", "measure": "ytd", "old": 165000, "new": 165021, "revised_on": "2026-06-16", "source": "rel:20260616"}
    ]  # ...and the first print survives only in the revisions log, not lost


def test_rerun_with_same_value_is_idempotent(series_dir):
    """Re-running the exact same release twice must not produce a second
    revision, or every scheduled poll would double-log history forever."""
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="ytd", value=165021, unit_raw="亿元", caliber_hint="ytd", period="2026-04")],
        release_id="rel:20260616",
    )
    first = apply_parsed_release(release, series_dir, FIELD_MAP, revised_on="2026-06-16")
    second = apply_parsed_release(release, series_dir, FIELD_MAP, revised_on="2026-06-16")

    assert len(first.revisions) == 1
    assert second.revisions == []
    assert second.new_observations == []

    series = _load(series_dir, "test-retail-total")
    assert len(series["revisions"]) == 1  # not two


def test_first_print_of_a_new_measure_is_not_a_revision(series_dir):
    """A measure key that was simply absent before (never published) getting its
    first value is NOT a revision -- DATA-CONTRACT §4.1: "First prints are not
    logged as revisions.\""""
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="real_yoy", value=0.9, unit_raw="%", caliber_hint="real_yoy", period="2026-03")]
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP)
    assert report.revisions == []
    series = _load(series_dir, "test-retail-total")
    march = next(obs for obs in series["observations"] if obs["period"] == "2026-03")
    assert march["real_yoy"] == 0.9


def test_unmapped_field_is_reported_not_raised(series_dir):
    release = _release([ParsedRow(source_field="从未见过的字段", raw_label="x", value=1.0, unit_raw="%", caliber_hint="m", period="2026-05")])
    report = apply_parsed_release(release, series_dir, FIELD_MAP)
    assert report.unmapped_fields == ["从未见过的字段"]
    assert report.new_observations == []


def test_missing_series_file_is_reported_not_raised(tmp_path):
    empty_dir = tmp_path / "empty_series"
    empty_dir.mkdir()
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=1.0, unit_raw="亿元", caliber_hint="m", period="2026-05")])
    report = apply_parsed_release(release, empty_dir, FIELD_MAP)
    assert report.missing_series == ["test-retail-total"]
    assert report.new_observations == []


def test_dry_run_reports_but_does_not_write(series_dir):
    before = (series_dir / "test-retail-total.json").read_text(encoding="utf-8")
    release = _release([ParsedRow(source_field="测试总额", raw_label="m", value=99999, unit_raw="亿元", caliber_hint="m", period="2026-06")])
    report = apply_parsed_release(release, series_dir, FIELD_MAP, dry_run=True)
    assert report.new_observations == [("test-retail-total", "2026-06")]
    after = (series_dir / "test-retail-total.json").read_text(encoding="utf-8")
    assert before == after  # file on disk is untouched


# -- Jan-Feb rule --------------------------------------------------------------


def test_jan_feb_flagged_observation_is_created_correctly(series_dir):
    empty_dir = series_dir  # test-retail-total has no 2025 Jan/Feb yet
    release = _release(
        [
            ParsedRow(
                source_field="测试总额", raw_label="jan-feb m", value=70000, unit_raw="亿元",
                caliber_hint="m", period="2025-02", span=2, flags=["jan_feb"],
            )
        ]
    )
    report = apply_parsed_release(release, empty_dir, FIELD_MAP)
    assert report.new_observations == [("test-retail-total", "2025-02")]
    series = _load(series_dir, "test-retail-total")
    obs = next(o for o in series["observations"] if o["period"] == "2025-02")
    assert obs["span"] == 2
    assert obs["flags"] == ["jan_feb"]


def test_jan_feb_flag_must_anchor_to_end_month(series_dir):
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="bad", value=1.0, unit_raw="亿元", caliber_hint="m", period="2026-03", span=2, flags=["jan_feb"])]
    )
    with pytest.raises(ValueError):
        apply_parsed_release(release, series_dir, FIELD_MAP)


def test_jan_feb_flag_requires_span_2(series_dir):
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="bad", value=1.0, unit_raw="亿元", caliber_hint="m", period="2026-02", span=1, flags=["jan_feb"])]
    )
    with pytest.raises(ValueError):
        apply_parsed_release(release, series_dir, FIELD_MAP)


def test_standalone_january_after_jan_feb_merge_is_rejected(series_dir):
    """test-retail-total.json already has a jan_feb-flagged 2026-02; a later
    attempt to write a standalone 2026-01 for the same series would double-count
    January and must be rejected."""
    release = _release(
        [ParsedRow(source_field="测试总额", raw_label="stray jan", value=1.0, unit_raw="亿元", caliber_hint="m", period="2026-01")]
    )
    with pytest.raises(ValueError):
        apply_parsed_release(release, series_dir, FIELD_MAP)


# -- Break respect (no_yoy_across) ---------------------------------------------


def test_yoy_blocked_inside_break_window(series_dir):
    """test-cpi-rebase.json has a break: effective=2026-01, yoy_valid_from=2027-01,
    no_yoy_across=true. A YoY value for a period inside that window must never be
    persisted, even if a (misbehaving) source row supplies one."""
    release = _release(
        [ParsedRow(source_field="测试指数", raw_label="blocked", value=5.0, unit_raw="%", caliber_hint="m_yoy", period="2026-06")]
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP)
    assert report.new_observations == []  # nothing to create -- the only row was blocked
    series = _load(series_dir, "test-cpi-rebase")
    assert not any(obs["period"] == "2026-06" for obs in series["observations"])


def test_yoy_allowed_once_yoy_valid_from_is_reached(series_dir):
    release = _release(
        [ParsedRow(source_field="测试指数", raw_label="allowed", value=3.3, unit_raw="%", caliber_hint="m_yoy", period="2027-01")]
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP)
    assert report.new_observations == [("test-cpi-rebase", "2027-01")]
    series = _load(series_dir, "test-cpi-rebase")
    obs = next(o for o in series["observations"] if o["period"] == "2027-01")
    assert obs["m_yoy"] == 3.3


def test_level_measure_is_not_blocked_by_break(series_dir):
    """no_yoy_across only concerns m_yoy/ytd_yoy/real_yoy -- a plain level ("m")
    inside the same window is still real data and must be stored."""
    release = _release(
        [ParsedRow(source_field="测试指数", raw_label="level", value=102.3, unit_raw="index", caliber_hint="m", period="2026-06")]
    )
    report = apply_parsed_release(release, series_dir, FIELD_MAP)
    assert report.new_observations == [("test-cpi-rebase", "2026-06")]


# -- dump_series formatting (DATA-CONTRACT §9) ---------------------------------


def test_dump_series_round_trips_and_is_one_observation_per_line():
    series = json.loads((FIXTURES_DIR / "test-retail-total.json").read_text(encoding="utf-8"))
    text = dump_series(series)
    assert json.loads(text) == series
    assert text.endswith("\n")

    lines = text.split("\n")
    obs_start = next(i for i, line in enumerate(lines) if line.strip() == '"observations": [')
    obs_lines = []
    for line in lines[obs_start + 1 :]:
        if line.strip() == "]," or line.strip() == "]":
            break
        obs_lines.append(line)
    assert len(obs_lines) == len(series["observations"])  # exactly one line per observation
    assert all(line.strip().startswith("{") for line in obs_lines)
