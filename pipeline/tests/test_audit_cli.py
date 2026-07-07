"""End-to-end tests for pipeline/audit/cli.py (run_audit orchestration) and
unit tests for report.py / diary.py's output shape.

The full run_audit() end-to-end tests use `repo_root=REAL_REPO_ROOT` (not the
fixture dir) for the same reason test_audit_build_determinism.py does:
gate_b.build_determinism's subprocess needs `pipeline.build` to actually
resolve, which only happens from the real checkout -- `--data`/`--site-data`
still point at a throwaway copy of clean_repo, and `labels` is injected as the
synthetic TEST_LABELS table (see cli.run_audit's own docstring for why both
are injectable).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from pipeline.audit import diary, report
from pipeline.audit.cli import build_arg_parser, run_audit
from pipeline.audit.models import CheckReport, Finding
from pipeline.tests.test_audit_helpers import CLEAN_REPO, TEST_LABELS, copy_clean_repo, make_ctx

REAL_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _args(repo_dir: Path, report_dir: Path, **overrides) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(
        ["--data", str(repo_dir / "data"), "--site-data", str(repo_dir / "site-data"), "--report-dir", str(report_dir)]
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_run_audit_end_to_end_against_the_clean_fixture(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    report_dir = tmp_path / "reports"
    args = _args(repo_dir, report_dir, seed="e2e-seed", run_id="e2e-run")

    exit_code, report_payload, diary_payload = run_audit(args, repo_root=REAL_REPO_ROOT, labels=TEST_LABELS)

    assert exit_code == 0, json.dumps(
        [
            {"check": c["check"], "blocks": [f for f in c["findings"] if f["status"] == "block"]}
            for c in report_payload["checks"]
            if c["summary"].get("block")
        ],
        ensure_ascii=False,
        indent=2,
    )
    assert len(report_payload["checks"]) == 9  # all 9 gate_b.* checks ran

    json_path = report_dir / "e2e-run.json"
    md_path = report_dir / "e2e-run.md"
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "e2e-run"
    assert "Gate B" in md_path.read_text(encoding="utf-8")

    diary_run_path = repo_dir / "site-data" / "diary" / "e2e-run.json"
    diary_latest_path = repo_dir / "site-data" / "diary" / "latest.json"
    assert diary_run_path.exists() and diary_latest_path.exists()
    on_disk_diary = json.loads(diary_latest_path.read_text(encoding="utf-8"))
    assert on_disk_diary == diary_payload
    for key in ("what_changed", "changelog_zh", "warnings", "blocked_checks", "freshness", "series_snapshot"):
        assert key in diary_payload


def test_run_audit_second_run_sees_no_new_observations_when_nothing_changed(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    report_dir = tmp_path / "reports"
    args = _args(repo_dir, report_dir, seed="fixed-seed", run_id="run-1")
    run_audit(args, repo_root=REAL_REPO_ROOT, labels=TEST_LABELS)

    args2 = _args(repo_dir, report_dir, seed="fixed-seed", run_id="run-2")
    _exit_code, _report, diary_payload_2 = run_audit(args2, repo_root=REAL_REPO_ROOT, labels=TEST_LABELS)

    assert diary_payload_2["what_changed"]["new_observations"] == []


def test_exit_code_is_2_when_a_check_finds_a_real_block(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    # Corrupt a bundle value so takeaway_numbers (a cheap, always-run check)
    # produces a genuine block, independent of network/archive availability.
    path = repo_dir / "site-data" / "sections" / "consumption.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    entry = next(s for s in bundle["series"] if s["id"] == "test-retail-total")
    entry["takeaway"] = entry["takeaway"].replace("0.6%", "77.7%")
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    report_dir = tmp_path / "reports"
    args = _args(repo_dir, report_dir, seed="block-seed", run_id="block-run")
    exit_code, report_payload, diary_payload = run_audit(args, repo_root=REAL_REPO_ROOT, labels=TEST_LABELS)

    assert exit_code == 2
    assert "gate_b.takeaway_numbers" in diary_payload["blocked_checks"]
    assert report_payload["exit_code"] == 2


# =====================================================================================
# report.py / diary.py unit tests
# =====================================================================================


def _sample_check_reports() -> list[CheckReport]:
    return [
        CheckReport(
            check="gate_b.fake_check",
            findings=[
                Finding(check="gate_b.fake_check", status="pass", series="a", note="ok"),
                Finding(check="gate_b.fake_check", status="block", series="b", period="2026-05", expected=1.0, observed=2.0, note="bad"),
                Finding(check="gate_b.fake_check", status="warn", series="c", note="gap"),
            ],
            duration_seconds=0.01,
        )
    ]


def test_report_payload_shape_and_exit_code():
    payload = report.build_report_payload(
        run_id="r1", seed="s1", generated_at="2026-07-08T00:00:00Z", catalog_version="1.0.0",
        check_reports=_sample_check_reports(), exit_code=2,
    )
    assert payload["exit_code"] == 2
    assert payload["checks"][0]["summary"]["block"] == 1
    assert payload["checks"][0]["summary"]["pass"] == 1
    assert payload["checks"][0]["summary"]["warn"] == 1


def test_write_json_and_md_reports(tmp_path):
    payload = report.build_report_payload(
        run_id="r1", seed="s1", generated_at="2026-07-08T00:00:00Z", catalog_version="1.0.0",
        check_reports=_sample_check_reports(), exit_code=2,
    )
    json_path, md_path = tmp_path / "r1.json", tmp_path / "r1.md"
    report.write_json_report(payload, json_path)
    report.write_md_report(payload, md_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "r1"
    md_text = md_path.read_text(encoding="utf-8")
    assert "gate_b.fake_check" in md_text
    assert "b: expected `1.0`, observed `2.0`" in md_text or "expected `1.0`" in md_text


def test_diary_changelog_line_follows_typesetting_rules():
    # diary.build_diary_payload is pure (reads ctx, writes nothing to disk),
    # so this can run straight against the read-only committed fixture.
    ctx = make_ctx(CLEAN_REPO)
    payload = diary.build_diary_payload(ctx, [], exit_code=0)
    lines = payload["changelog_zh"]
    assert lines, "expected at least one changelog line for a first-ever run"
    retail_line = next(line for line in lines if "测试_社会消费品零售总额" in line)
    # DATA-CONTRACT §12: Arabic numerals, pangu space between CJK and the
    # following digit/percent-sign run, curly quotes (none needed here).
    assert retail_line == "测试_社会消费品零售总额 新增 2026 年 5 月，当月同比 -0.6%"


def test_diary_new_observations_and_snapshot_shape():
    ctx = make_ctx(CLEAN_REPO)
    payload = diary.build_diary_payload(ctx, [], exit_code=0)
    ids = {row["id"] for row in payload["what_changed"]["new_observations"]}
    assert "test-retail-total" in ids
    assert payload["series_snapshot"]["test-retail-total"]["period"] == "2026-05"


def test_diary_second_run_with_identical_snapshot_has_no_new_observations():
    ctx = make_ctx(CLEAN_REPO)
    first = diary.build_diary_payload(ctx, [], exit_code=0)
    ctx.previous_diary = first
    second = diary.build_diary_payload(ctx, [], exit_code=0)
    assert second["what_changed"]["new_observations"] == []


def test_diary_blocked_checks_lists_checks_with_a_block_or_error():
    ok = CheckReport(check="gate_b.ok", findings=[Finding(check="gate_b.ok", status="pass")])
    broken = CheckReport(check="gate_b.broken", findings=[Finding(check="gate_b.broken", status="block", series="x")])
    errored = CheckReport(check="gate_b.errored", error="boom")
    ctx = make_ctx(CLEAN_REPO)
    payload = diary.build_diary_payload(ctx, [ok, broken, errored], exit_code=2)
    assert set(payload["blocked_checks"]) == {"gate_b.broken", "gate_b.errored"}
