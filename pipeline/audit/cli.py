"""gate_b — orchestration + exit-code logic.

    python -m pipeline.audit --site-data site-data/ [--offline] [--seed X]
                              [--samples-per-section N]

Runs after build, before deploy. Exit 0 = deployable (warnings ok), exit 2 =
block deploy (at least one check produced a "block"-status finding, or a
check itself raised).
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import date
from pathlib import Path

from pipeline.audit.checks import CHECK_MODULES
from pipeline.audit.diary import build_diary_payload
from pipeline.audit.labels import load_labels
from pipeline.audit.models import AuditContext, CheckReport
from pipeline.audit.report import build_report_payload, write_json_report, write_md_report
from pipeline.audit.sampling import derive_seed
from pipeline.audit.site_data import load_all_section_bundles, load_catalog, load_json, make_panel_bundle_loader

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = Path(__file__).resolve().parent / "reports"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gate B -- post-build independent audit")
    parser.add_argument("--site-data", type=Path, default=REPO_ROOT / "site-data", help="site-data/ directory to audit")
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data", help="data/ directory (source of record)")
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--offline", action="store_true", help="no live network calls (default)")
    network.add_argument("--live", action="store_true", help="enable a capped (<=10) re-pull of random DG points")
    parser.add_argument("--seed", default=None, help="sampling seed (default: git HEAD short SHA + catalog version)")
    parser.add_argument("--samples-per-section", type=int, default=25, help="random samples per section (stratum f) and per DG series")
    parser.add_argument("--run-id", default=None, help="diary/report run id (default: the derived seed)")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="where to write the JSON+MD audit report")
    parser.add_argument("--as-of", default=None, help="ISO date override for freshness calculations (default: today)")
    parser.add_argument("--live-dg-cap", type=int, default=10, help="max points re-pulled live when --live is set")
    return parser


def _load_previous_diary(site_data_dir: Path) -> dict | None:
    path = site_data_dir / "diary" / "latest.json"
    if not path.exists():
        return None
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


def run_audit(args: argparse.Namespace, *, repo_root: Path | None = None, labels: dict | None = None) -> tuple[int, dict, dict]:
    """(exit_code, report_payload, diary_payload) -- factored out of main() so
    tests can drive a full audit run in-process against a tmp_path tree
    without going through argv/sys.exit. `repo_root`/`labels` are injectable
    purely for tests exercising this orchestration against a synthetic fixture
    tree (whose series ids obviously aren't in the real labels.yaml, and whose
    "repo" for git-seed/build-determinism purposes isn't this real repo);
    production usage (main()) always uses the real repo root and labels.yaml.
    """
    repo_root = repo_root or REPO_ROOT
    catalog = load_catalog(args.data)
    section_bundles = load_all_section_bundles(args.site_data, catalog)
    panel_loader = make_panel_bundle_loader(args.site_data)
    labels = load_labels() if labels is None else labels
    previous_diary = _load_previous_diary(args.site_data)

    seed = derive_seed(args.seed, repo_root=repo_root, catalog_version=catalog.get("version", "unknown"))
    run_id = args.run_id or seed
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    ctx = AuditContext(
        repo_root=repo_root,
        data_dir=args.data,
        site_data_dir=args.site_data,
        catalog=catalog,
        section_bundles=section_bundles,
        panel_bundle_loader=panel_loader,
        labels=labels,
        rng=random.Random(seed),
        seed=seed,
        run_id=run_id,
        offline=not args.live,
        samples_per_section=args.samples_per_section,
        as_of=as_of,
        previous_diary=previous_diary,
        live_dg_cap=args.live_dg_cap,
    )

    check_reports: list[CheckReport] = []
    for module in CHECK_MODULES:
        start = time.monotonic()
        try:
            check_reports.append(module.run(ctx))
        except Exception as exc:  # noqa: BLE001 -- one check's bug must not silently skip the rest
            check_reports.append(CheckReport(check=module.CHECK_ID, error=f"{type(exc).__name__}: {exc}", duration_seconds=time.monotonic() - start))

    exit_code = 2 if any(report.has_block() for report in check_reports) else 0

    report_payload = build_report_payload(
        run_id=run_id,
        seed=seed,
        generated_at=catalog.get("generated_at", ""),
        catalog_version=catalog.get("version", ""),
        check_reports=check_reports,
        exit_code=exit_code,
    )
    diary_payload = build_diary_payload(ctx, check_reports, exit_code=exit_code)

    write_json_report(report_payload, args.report_dir / f"{run_id}.json")
    write_md_report(report_payload, args.report_dir / f"{run_id}.md")

    diary_dir = args.site_data / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)
    import json

    (diary_dir / f"{run_id}.json").write_text(json.dumps(diary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (diary_dir / "latest.json").write_text(json.dumps(diary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return exit_code, report_payload, diary_payload


def _print_summary(report_payload: dict) -> None:
    print(f"[gate_b] run_id={report_payload['run_id']} seed={report_payload['seed']}")
    for check in report_payload["checks"]:
        s = check["summary"]
        flag = " <-- BLOCK" if s.get("block") else ""
        print(f"  {check['check']}: {s.get('pass', 0)} pass / {s.get('warn', 0)} warn / {s.get('block', 0)} block / {s.get('skip', 0)} skip{flag}")
    print(f"[gate_b] exit_code={report_payload['exit_code']} ({'BLOCK DEPLOY' if report_payload['exit_code'] else 'deployable'})")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    exit_code, report_payload, _diary = run_audit(args)
    _print_summary(report_payload)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
