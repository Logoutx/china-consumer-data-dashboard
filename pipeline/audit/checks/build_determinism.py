"""gate_b.build_determinism — reruns the build stage as a SUBPROCESS
(`sys.executable -m pipeline.build --data <data_dir> --out <tmp_dir>`) and
byte-compares every file it writes against the audited site-data/ tree.
Subprocess, not an in-process import, is deliberate: DATA-CONTRACT §9 requires
the build to be idempotent (unchanged inputs -> byte-identical tree), and a
subprocess boundary lets this check exercise that guarantee without ever
importing pipeline.build into the audit package's own process (the forbidden-
import list exists precisely so this audit can't "check its own homework" --
subprocess isolation is what makes rerunning the real build stage compatible
with that constraint). Any diff (missing file, extra file, differing bytes)
BLOCKs.

`site-data/diary/` is excluded from the comparison on both sides: it is THIS
package's own output (diary.py / cli.py), written into the same site-data/
directory build.py writes to, but never produced by build.py itself -- caught
empirically by running this audit twice in a row against the same site-data/:
the second run's rerun (correctly) had no diary/ at all, while the first
run's own diary/*.json files were still sitting in the "audited" tree from
moments earlier, producing two spurious "file missing from rerun" blocks that
had nothing to do with build.py's determinism.

The audit passes its explicit as_of date through to the rebuild so time-relative
revision and break flags use the same reference date, including fixture tests.
"""
from __future__ import annotations

import filecmp
import subprocess
import sys
import time
from pathlib import Path

from pipeline.audit.models import AuditContext, CheckReport, Finding

CHECK_ID = "gate_b.build_determinism"


def _all_files(root: Path, *, exclude_dirs: tuple[str, ...] = ("diary",)) -> set[Path]:
    if not root.exists():
        return set()
    out = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in exclude_dirs:
            continue
        out.add(rel)
    return out


def run(ctx: AuditContext, *, tmp_dir: Path | None = None) -> CheckReport:
    start = time.monotonic()
    findings: list[Finding] = []

    import tempfile

    owns_tmp = tmp_dir is None
    tmp_dir = tmp_dir or Path(tempfile.mkdtemp(prefix="gate_b_build_determinism_"))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pipeline.build", "--data", str(ctx.data_dir), "--out", str(tmp_dir), "--as-of", ctx.as_of.isoformat()],
            cwd=ctx.repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    note="rerunning `python -m pipeline.build` failed",
                    evidence=(result.stderr or "")[-2000:],
                )
            )
            return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)

        rerun_files = _all_files(tmp_dir)
        audited_files = _all_files(ctx.site_data_dir)

        missing = sorted(str(p) for p in audited_files - rerun_files)
        extra = sorted(str(p) for p in rerun_files - audited_files)
        for rel in missing:
            findings.append(
                Finding(check=CHECK_ID, status="block", field=rel, note="file present in audited site-data/ but not produced by the rerun")
            )
        for rel in extra:
            findings.append(
                Finding(check=CHECK_ID, status="block", field=rel, note="rerun produced a file not present in audited site-data/")
            )

        common = sorted(str(p) for p in (audited_files & rerun_files))
        differing = []
        for rel in common:
            if not filecmp.cmp(tmp_dir / rel, ctx.site_data_dir / rel, shallow=False):
                differing.append(rel)
        for rel in differing:
            findings.append(
                Finding(
                    check=CHECK_ID,
                    status="block",
                    field=rel,
                    note="byte-for-byte diff between rebuilt output and audited site-data/",
                )
            )

        if not findings:
            findings.append(
                Finding(check=CHECK_ID, status="pass", note=f"rebuild byte-identical across {len(common)} file(s)")
            )
    finally:
        if owns_tmp:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)

    return CheckReport(check=CHECK_ID, findings=findings, duration_seconds=time.monotonic() - start)
