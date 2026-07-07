"""pipeline/runner.py — CLI entrypoint: discover -> fetch -> parse -> normalize -> report.

Usage:
    python -m pipeline.runner --source nbs_cpi [--dry-run]

Designed for an unattended scheduler (DATA-CONTRACT §11.2's GitHub Actions
poller): idempotent (normalize's value-unchanged check absorbs a re-run against an
already-ingested release), and exits 0 with a "no new release" message when
discovery finds nothing -- the expected steady state between release windows, not
a failure worth a non-zero exit code. Only a genuine parse/format-drift error
(ParseError) or an unmapped `--source` produces a non-zero exit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import ParseError
from pipeline.discover import discover_nbs, discover_pbc
from pipeline.fetch import FetchError, fetch_and_archive
from pipeline.normalize import apply_parsed_release, load_field_map
from pipeline.parsers import nbs_cpi, nbs_retail, pboc_money

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"

SOURCES = {
    "nbs_cpi": {
        "archive_source": "nbs-cpi",
        "title_pattern": r"^\d{4}年\d{1,2}月份居民消费价格",
        "parser": nbs_cpi.parse,
        "engine": "nbs",
    },
    "nbs_retail": {
        "archive_source": "nbs-retail",
        "title_pattern": r"^\d{4}年(?:1[—\-－]\d{1,2}月份)?社会消费品零售总额",
        "parser": nbs_retail.parse,
        "engine": "nbs",
    },
    "pboc_money": {
        "archive_source": "pbc-money",
        "query_title": "金融统计数据报告",
        "parser": pboc_money.parse,
        "engine": "pbc",
    },
}


def run(source_key: str, *, dry_run: bool) -> int:
    config = SOURCES.get(source_key)
    if config is None:
        print(f"unknown --source {source_key!r}; choices: {sorted(SOURCES)}", file=sys.stderr)
        return 2

    if config["engine"] == "pbc":
        candidates = discover_pbc(config["query_title"])
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

    release_id = result.archive_path.stem if result.archive_path else candidate.period_hint or "unknown"
    try:
        parsed = config["parser"](result.text, url=candidate.url, release_id=release_id)
    except ParseError as error:
        print(f"[{source_key}] parse failed: {error}", file=sys.stderr)
        return 1

    field_map = load_field_map()
    report = apply_parsed_release(parsed, SERIES_DIR, field_map, dry_run=dry_run)

    mode = "would change" if dry_run else "changed"
    print(f"[{source_key}] period: {parsed.period_hint}")
    print(f"[{source_key}] {mode}: {len(report.new_observations)} new observation(s), {len(report.revisions)} revision(s)")
    if report.unmapped_fields:
        print(f"[{source_key}] unmapped source fields (add to pipeline/config/field_map.yaml): {sorted(set(report.unmapped_fields))}")
    if report.missing_series:
        print(f"[{source_key}] mapped series file(s) not found on disk, skipped: {sorted(set(report.missing_series))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="China consumer dashboard acquisition runner")
    parser.add_argument("--source", required=True, choices=sorted(SOURCES), help="which release type to run")
    parser.add_argument("--dry-run", action="store_true", help="parse and report; do not write series files")
    args = parser.parse_args(argv)
    return run(args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
