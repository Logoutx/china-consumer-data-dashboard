"""python -m pipeline.validate --staged <dir> --batch <parsed_release.json>

Standalone Gate A entrypoint: run every gate_a.* check against an
already-staged directory (see pipeline/validate/staging.py for how runner.py
produces one) and a NormalizedBatch JSON file (pipeline/validate/batch.py's
dump_batch() shape). Exit codes: 0 = pass (warnings allowed), 2 = blocked.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.validate.batch import empty_batch, load_batch
from pipeline.validate.config import load_release_calendar, load_validation_config
from pipeline.validate.gate import run_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate A -- ingest validation for the China consumer dashboard pipeline")
    parser.add_argument("--staged", required=True, help="staged directory (expects <dir>/series/*.json)")
    parser.add_argument("--batch", help="NormalizedBatch JSON file (pipeline/validate/batch.py's dump_batch() shape)")
    parser.add_argument("--data", help="real data/ directory to fall back to for context series and the catalog (default: repo's data/)")
    parser.add_argument("--config", help="validation.yaml path override")
    parser.add_argument("--calendar", help="release_calendar.yaml path override")
    parser.add_argument("--archive-source", help="override the archive_source used for gate_a.calendar_window / gate_a.archive_release_identity")
    parser.add_argument("--quiet", action="store_true", help="suppress the Markdown report on stdout (still written to <staged>/gate_report.md)")
    args = parser.parse_args(argv)

    batch = load_batch(Path(args.batch)) if args.batch else empty_batch()
    config = load_validation_config(Path(args.config)) if args.config else load_validation_config()
    calendar = load_release_calendar(Path(args.calendar)) if args.calendar else load_release_calendar()
    real_data_dir = Path(args.data) if args.data else None

    report = run_gate(
        Path(args.staged),
        batch=batch,
        real_data_dir=real_data_dir,
        config=config,
        calendar=calendar,
        archive_source=args.archive_source,
    )

    if not args.quiet:
        print(report.to_markdown())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
