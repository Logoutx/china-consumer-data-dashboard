"""Tests for gate_b.build_determinism -- reruns `python -m pipeline.build` as
a subprocess (never an in-process import of pipeline.build) against the
audited data/, and byte-compares the result. Runs the REAL, current
pipeline/build.py (not a stub), which is the point: this check's whole job is
catching build.py actually drifting from what's deployed.

Context construction note: `python -m pipeline.build` must run with `cwd` set
to somewhere the `pipeline` package actually resolves from (the real repo
root) -- but `--data`/`--out` can point anywhere, including this synthetic
fixture's data/. So unlike every other check's tests (which use
test_audit_helpers.make_ctx and set `repo_root` to the fixture dir itself),
these tests build the AuditContext directly with `repo_root=REAL_REPO_ROOT`
and `data_dir`/`site_data_dir` pointing at the fixture -- exactly the split
build_determinism.py's own subprocess call is designed around.
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from pipeline.audit.checks import build_determinism
from pipeline.audit.models import AuditContext
from pipeline.tests.test_audit_helpers import CLEAN_REPO, copy_clean_repo

REAL_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _ctx_for(repo_dir: Path) -> AuditContext:
    return AuditContext(
        repo_root=REAL_REPO_ROOT,  # where the `pipeline` package actually resolves from
        data_dir=repo_dir / "data",
        site_data_dir=repo_dir / "site-data",
        catalog={},  # build_determinism.run() never reads ctx.catalog
        section_bundles={},
        panel_bundle_loader=lambda _pid: None,
        labels={},
        rng=random.Random(0),
        seed="unit-seed",
        run_id="unit-seed",
        offline=True,
        samples_per_section=5,
        as_of=date(2026, 7, 8),
        previous_diary=None,
    )


def test_passes_when_site_data_matches_a_fresh_rebuild():
    report = build_determinism.run(_ctx_for(CLEAN_REPO))
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]
    assert any(f.status == "pass" for f in report.findings)


def test_a_leftover_diary_directory_from_a_prior_audit_run_is_not_a_false_block(tmp_path):
    """Regression test: running the audit once writes site-data/diary/*.json;
    build.py itself never produces a diary/ directory at all, so comparing it
    against a fresh rebuild used to produce two spurious "missing from
    rerun" blocks that had nothing to do with build.py's own determinism."""
    repo_dir = copy_clean_repo(tmp_path)
    diary_dir = repo_dir / "site-data" / "diary"
    diary_dir.mkdir()
    (diary_dir / "latest.json").write_text("{}", encoding="utf-8")
    (diary_dir / "some-run-id.json").write_text("{}", encoding="utf-8")

    report = build_determinism.run(_ctx_for(repo_dir))
    assert not report.has_block(), [f.to_dict() for f in report.findings if f.status == "block"]


def test_catches_a_byte_diff_from_a_corrupted_site_data_file(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    path = repo_dir / "site-data" / "sections" / "consumption.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    entry = next(s for s in bundle["series"] if s["id"] == "test-retail-total")
    entry["latest"]["m"] = 999999.0  # doesn't match what data/series/ would rebuild to
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = build_determinism.run(_ctx_for(repo_dir))
    assert report.has_block()
    assert any("sections/consumption.json" in (f.field or "") for f in report.findings)


def test_catches_an_extra_file_not_produced_by_a_fresh_rebuild(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    extra = repo_dir / "site-data" / "sections" / "not-a-real-section.json"
    extra.write_text("{}", encoding="utf-8")

    report = build_determinism.run(_ctx_for(repo_dir))
    assert report.has_block()
    assert any("not-a-real-section.json" in (f.field or "") and "not produced by the rerun" in (f.note or "") for f in report.findings)


def test_rebuild_failure_itself_is_a_block(tmp_path):
    repo_dir = copy_clean_repo(tmp_path)
    # Corrupt catalog.json so `python -m pipeline.build` itself fails/crashes.
    (repo_dir / "data" / "catalog.json").write_text("{not valid json", encoding="utf-8")

    report = build_determinism.run(_ctx_for(repo_dir))
    assert report.has_block()
