"""pipeline/runner.py — CLI entrypoint: discover -> fetch -> parse -> stage -> Gate A -> write.

Usage:
    python -m pipeline.runner --source nbs_cpi [--dry-run] [--no-gate]
    python -m pipeline.runner --source nbs_cpi --fixture pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html --dry-run
    python -m pipeline.runner --source dg_refresh [--dry-run] [--no-gate]

Designed for an unattended scheduler (DATA-CONTRACT §11.2's GitHub Actions
poller): idempotent (normalize's value-unchanged check absorbs a re-run against an
already-ingested release), and exits 0 with a "no new release" message when
discovery finds nothing -- the expected steady state between release windows, not
a failure worth a non-zero exit code. A genuine parse/format-drift error
(ParseError) or a fetch failure produces exit 1; an unmapped `--source` produces
exit 3; a Gate A BLOCK produces exit 2 (see "Exit codes" below).

Gate A (pipeline/validate/) sits between parse and write, per this milestone's
binding architecture:

    discover -> fetch(+archive) -> parse -> stage -> Gate A -> write (on pass)

`stage` (pipeline/validate/staging.py) dry-run-merges the ParsedRelease into a
private temp copy of only the series files it touches -- data/series/ is never
opened in write mode until Gate A has passed and promote_to_real() runs.
`--no-gate` is a loud, logged escape hatch that writes anyway on a BLOCK; use it
only to force through a release Gate A is misjudging.

`--fixture <path>` bypasses discover_nbs/discover_pbc AND fetch_and_archive
entirely, reading a COMMITTED fixture file's text directly and feeding it
straight to the configured parser -- an offline way to prove field_map.yaml's
mappings resolve correctly end-to-end (parse -> stage -> Gate A -> (dry)
write) against a known, checked-in release, without a live network call.
It still registers an archive capture, though: the fixture's own bytes are
written to data/archive/<archive_source>/<release_id>.<ext> (release_id ==
the fixture's filename stem) before parsing -- honestly the same thing a
real fetch_and_archive() call does, just sourced from a committed file
instead of a live fetch, so gate_a.archive_release_identity sees a real
match instead of blocking on a --fixture-mode-only artifact. Not applicable
to `--source dg_refresh`, which has its own, separate offline-testing story
(see pipeline/dg_refresh.py) -- passing `--fixture` alongside it is a no-op,
loudly warned about, not an error.

Exit codes (standardized 2026-07-08, matches docs/OPERATIONS.md):
    0 -- ok: either a real change was staged/written (or would be, under
         --dry-run), or nothing was due (a clean no-op) -- both are success.
    1 -- a genuine fetch or parse failure (format drift, network error): a
         real problem, but not Gate A's verdict on the data itself.
    2 -- Gate A BLOCKED (data/ left untouched unless --no-gate). Also printed
         as a machine-readable `GATE_BLOCKED` marker line on stderr, so a
         caller can grep for it instead of (or in addition to) relying on the
         exit code.
    3 -- usage error: an unrecognized `--source`. Split out from exit 2
         2026-07-08 (previously both cases returned 2, which collided with
         "Gate A blocked" once Gate A was wired in -- see docs/OPERATIONS.md
         §6's former "known assumption" about this, now resolved).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from pipeline import ParseError
from pipeline import dg_refresh
from pipeline.discover import discover_mot_post, discover_nbs, discover_pbc
from pipeline.fetch import FetchError, fetch_and_archive
from pipeline.normalize import load_field_map
from pipeline.parsers import nbs_cpi, nbs_retail, pboc_money, spb_express
from pipeline.validate.batch import batch_from_parsed_release
from pipeline.validate.gate import run_gate
from pipeline.validate.staging import promote_to_real, stage_release

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"
VALIDATE_REPORTS_DIR = ROOT / "validate_reports"


def _persist_gate_report(staged_dir: Path, source_key: str) -> None:
    """Copy the staged Gate A report (gate_report.json/.md -- written by
    pipeline.validate.gate.run_gate into the per-run STAGED TEMP directory,
    see pipeline/validate/staging.py's stage_release) to a stable,
    repo-relative path: validate_reports/<source_key>/.

    MEDIUM bug fixed 2026-07-08 (adversarial review): update-data.yml's
    "Upload Gate A validation reports" step has always pointed at
    validate_reports/ -- but nothing ever wrote there. run_gate() only ever
    writes into the staged directory, a fresh tempfile.mkdtemp() per
    invocation that nothing then copies anywhere durable; the artifact
    upload step silently warned-and-uploaded-nothing on every single Gate A
    block. Called unconditionally (pass or block) right after run_gate()
    returns, before the staged dir is ever cleaned up or simply left for the
    OS to reap on its own schedule.
    """
    dest_dir = VALIDATE_REPORTS_DIR / source_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("gate_report.json", "gate_report.md"):
        src = staged_dir / name
        if src.exists():
            shutil.copy2(src, dest_dir / name)


SOURCES = {
    "nbs_cpi": {
        "archive_source": "nbs-cpi",
        "title_pattern": r"^\d{4}年\d{1,2}月份居民消费价格",
        "parser": nbs_cpi.parse,
        "engine": "nbs",
    },
    "nbs_retail": {
        "archive_source": "nbs-retail",
        # 上半年 = NBS's alternate phrasing for the Jan-Jun release (missed
        # live 2026-07-15, leaving a 2026-06 hole -- see nbs_retail.py).
        "title_pattern": r"^\d{4}年(?:1[—\-－]\d{1,2}月份|上半年)?社会消费品零售总额",
        "parser": nbs_retail.parse,
        "engine": "nbs",
    },
    "spb_express": {
        "archive_source": "spb-express",
        # Anchor-text (not @title) on the MOT mirror listing; the period part
        # varies (1-N月/上半年/一季度/前三季度/N月份/bare year) so only the
        # stable frame is pinned here -- spb_express.period_from_title owns
        # the period grammar.
        "title_pattern": r"^国家邮政局公布\d{4}年.*邮政行业运行情况$",
        "parser": spb_express.parse,
        "engine": "mot_post",
    },
    "pboc_money": {
        "archive_source": "pbc-money",
        "query_title": "金融统计数据报告",
        "parser": pboc_money.parse,
        "engine": "pbc",
    },
    "dg_refresh": {
        "engine": "dg_refresh",
    },
}


def run(source_key: str, *, dry_run: bool, no_gate: bool = False, fixture: Path | None = None) -> int:
    config = SOURCES.get(source_key)
    if config is None:
        print(f"unknown --source {source_key!r}; choices: {sorted(SOURCES)}", file=sys.stderr)
        return 3

    if config["engine"] == "dg_refresh":
        if fixture is not None:
            print(
                f"[{source_key}] --fixture has no effect for dg_refresh (it has its own offline-testing story) -- ignoring",
                file=sys.stderr,
            )
        return dg_refresh.run(dry_run=dry_run, no_gate=no_gate)

    if fixture is not None:
        text = Path(fixture).read_text(encoding="utf-8")
        candidate_url = f"fixture://{fixture}"
        candidate_title = Path(fixture).stem
        release_id = Path(fixture).stem
        print(f"[{source_key}] using fixture: {fixture}")
        # Register the fixture itself as this run's archived capture
        # (data/archive/<archive_source>/<release_id>.<ext>) -- honestly the
        # SAME bytes the parser is about to see, at the SAME (source,
        # release_id)-keyed path a real fetch_and_archive() call would use
        # (release_id there is likewise the archive file's own stem). Without
        # this, gate_a.archive_release_identity finds no matching capture and
        # BLOCKs every new observation purely because --fixture bypassed live
        # fetch_and_archive() -- a --fixture-mode-only artifact, not a real
        # accuracy problem, so it shouldn't cost this offline proof a clean
        # Gate A pass. Only this branch writes to data/archive/; a live run's
        # own fetch_and_archive() call (the `else` branch below) is unchanged
        # and stays exactly as strict as before.
        # Derived from SERIES_DIR (not a separate module constant) so it
        # always agrees with real_data_dir/archive_dir below (and with
        # whatever a test monkeypatches SERIES_DIR to) -- one monkeypatch
        # point, not two that could drift apart.
        archive_dir = SERIES_DIR.parent / "archive" / config["archive_source"]
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_ext = Path(fixture).suffix or ".html"
        (archive_dir / f"{release_id}{archive_ext}").write_text(text, encoding="utf-8")
    else:
        if config["engine"] == "pbc":
            candidates = discover_pbc(config["query_title"])
        elif config["engine"] == "mot_post":
            candidates = discover_mot_post(config["title_pattern"])
        else:
            candidates = discover_nbs(config["title_pattern"])

        if not candidates:
            print(f"[{source_key}] no new release found (discovery returned nothing) -- exiting cleanly")
            return 0

        candidate = candidates[-1]  # most recent by (period_hint, url) sort
        print(f"[{source_key}] candidate release: {candidate.title} ({candidate.url})")

        try:
            result = fetch_and_archive(candidate.url, source=config["archive_source"], slug=candidate.title)
        except FetchError as error:
            print(f"[{source_key}] fetch failed: {error}", file=sys.stderr)
            return 1
        text = result.text
        candidate_url = candidate.url
        candidate_title = candidate.title
        release_id = result.archive_path.stem if result.archive_path else candidate.period_hint or "unknown"

    try:
        parsed = config["parser"](text, url=candidate_url, release_id=release_id)
    except ParseError as error:
        print(f"[{source_key}] parse failed: {error}", file=sys.stderr)
        return 1

    field_map = load_field_map()

    # stage: dry-run w.r.t. data/series/ (never opened in write mode below),
    # a real merge against a private temp copy (pipeline/validate/staging.py).
    stage_result = stage_release(parsed, field_map, SERIES_DIR)
    batch = batch_from_parsed_release(parsed, field_map)
    real_data_dir = SERIES_DIR.parent  # ROOT/"data" in production; a monkeypatched SERIES_DIR in tests

    report = stage_result.report
    mode = "would change" if dry_run else "changed"
    print(f"[{source_key}] period: {parsed.period_hint}")
    print(f"[{source_key}] {mode}: {len(report.new_observations)} new observation(s), {len(report.revisions)} revision(s)")
    if stage_result.unmapped_fields:
        print(f"[{source_key}] unmapped source fields (add to pipeline/config/field_map.yaml): {stage_result.unmapped_fields}")
    if stage_result.missing_series:
        print(f"[{source_key}] mapped series file(s) not found on disk, skipped: {stage_result.missing_series}")

    # Gate A: the ingest accuracy gate. Only on pass (or an explicit --no-gate
    # override) does anything get written to the real data/series/ tree.
    gate_report = run_gate(
        stage_result.staged_dir,
        batch=batch,
        real_data_dir=real_data_dir,
        touched_series=stage_result.touched_series,
        requested_series=stage_result.requested_series,
        missing_series=stage_result.missing_series,
        normalize_report=stage_result.report,
        archive_source=config["archive_source"],
    )
    _persist_gate_report(stage_result.staged_dir, source_key)
    print(gate_report.to_markdown())

    if gate_report.blocked and not no_gate:
        print(f"[{source_key}] GATE_BLOCKED", file=sys.stderr)
        print(
            f"[{source_key}] Gate A BLOCKED -- data/ left untouched. Fix the finding(s) above, or pass --no-gate to force a write (loud, logged, not recommended).",
            file=sys.stderr,
        )
        return 2
    if gate_report.blocked and no_gate:
        print(
            f"[{source_key}] *** --no-gate override in effect: Gate A BLOCKED but writing anyway. This bypasses the ingest accuracy gate -- verify the finding(s) above by hand. ***",
            file=sys.stderr,
        )

    if not dry_run:
        written = promote_to_real(stage_result.staged_series_dir, SERIES_DIR, stage_result.touched_series)
        if written:
            print(f"[{source_key}] wrote {len(written)} series file(s) to {SERIES_DIR}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="China consumer dashboard acquisition runner")
    parser.add_argument("--source", required=True, choices=sorted(SOURCES), help="which release type to run")
    parser.add_argument("--dry-run", action="store_true", help="parse and report; do not write series files")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help=(
            "path to a committed fixture file (e.g. pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html); "
            "bypasses discovery + live fetch entirely and parses this file's text directly. Offline "
            "end-to-end proof of field_map.yaml against a known release. Not applicable to --source dg_refresh."
        ),
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help=(
            "DANGEROUS escape hatch: write series files even if Gate A (pipeline/validate) "
            "reports a BLOCK. Prints a loud warning; use only to force through a release "
            "Gate A is misjudging, never as a routine way to silence it."
        ),
    )
    args = parser.parse_args(argv)
    if args.no_gate:
        print(f"[{args.source}] WARNING: --no-gate is set -- a Gate A BLOCK will NOT stop this run from writing to data/.", file=sys.stderr)
    return run(args.source, dry_run=args.dry_run, no_gate=args.no_gate, fixture=args.fixture)


if __name__ == "__main__":
    sys.exit(main())
