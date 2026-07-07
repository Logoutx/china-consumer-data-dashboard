# Operations runbook — automated data pipeline

How the dashboard's data stays current without a human running scripts by
hand: a scheduled poller that runs the acquisition pipeline inside its own
release windows, two accuracy gates that decide whether anything gets to
land or ship, and a deploy step that only runs once the owner has cut over
Pages to GitHub Actions. This file is the operational counterpart to
`docs/DATA-CONTRACT.md` (what the data looks like) and `docs/ACQUISITION.md`
(where each release comes from and how it's parsed).

---

## 1. How polling works

`.github/workflows/update-data.yml` runs on a schedule, six times a day:

| UTC  | Beijing (Asia/Shanghai) |
|------|--------------------------|
| 00:00 | 08:00 |
| 02:00 | 10:00 |
| 04:00 | 12:00 |
| 06:00 | 14:00 |
| 08:00 | 16:00 |
| 10:00 | 18:00 |

(China does not observe DST, so this UTC↔Beijing mapping is exact year-round.
Six fixed cron lines were used instead of a single `0 0-10/2 * * *` step
expression so each run shows one exact, legible time in the Actions history.)

Each run calls `python -m pipeline.schedule --due`, which decides which
release types ("sources") are worth checking right now. A source fires when
**both**:

1. Today (Asia/Shanghai date) falls inside that source's release window, plus
   a 3-day grace period on the close of the window (to tolerate a late
   release, e.g. a weekend push-back).
2. That source's representative series' own `data/series/<id>.json` (the max
   period actually present in its `observations[]`, scanned directly — see
   below) is older than the period the window implies should now be live.

If a source is outside its window, it is skipped entirely regardless of
freshness — the scheduler does not "catch up" on staleness outside a real
release window, it just waits for the next one.

**Steady state, corrected 2026-07-08 (adversarial review, CRITICAL bug 2).**
Freshness used to be read from `data/catalog.json`'s own `latest` field per
series — but the runtime write path (`pipeline.normalize`, called from both
`pipeline.runner` and `pipeline.dg_refresh`) never advances that cached
field; only `pipeline.migrate`'s one-time historical-seed step does, and
that never runs again after the initial rebuild. Practical consequence: the
moment a source's window opened, it fired, actually landed fresh data into
`data/series/<id>.json` — and then fired again on the *next* cron tick two
hours later, and the one after that, for the source's entire remaining
window, because the catalog's cached `latest` never moved to reflect what
had just landed. For `dg_refresh` (whose own window is open most days — see
§1.1) this meant re-pulling ~56 DG API requests roughly every 2 hours instead
of once per genuinely new period — thousands of redundant hits a month on
exactly the upstream API this project most needs to stay polite with.
`pipeline.schedule` now scans `data/series/<id>.json` directly for exactly
this reason: the steady state between releases is genuinely "checked the
window, already current, did nothing" from the very next cron tick after a
source lands, not for the rest of that source's open window.

### Window table (day-of-month, Asia/Shanghai; +3 days grace on the close)

| Source group | Window | Reports |
|---|---|---|
| CPI / PPI | 9–13 | previous month |
| PMI | 25–(month end), spilling to day 1 of the next month | current month (PMI posts on the last calendar day of the month it reports) |
| Trade | 7–14 | previous month |
| PBoC money & credit | 10–15 | previous month |
| NBS activity batch (retail, IVA, FAI, 70-city, …) | 14–18 | previous month |
| Quarterly GDP / income | 14–18, **only in Jan / Apr / Jul / Oct** | the quarter that just ended (Jan reports Q4 of the *prior* year) |

This table is transcribed directly into `pipeline/schedule.py`'s `WINDOWS`
dict. **`pipeline/config/release_calendar.yaml` now exists** (landed
2026-07-08) and is `pipeline/validate`'s (`gate_a.calendar_window`) and
`pipeline/audit`'s (`gate_b.freshness`, via `pipeline/audit/release_calendar.py`
— see §6) real, natively-read config — but `schedule.py`'s own `WINDOWS`/
`SOURCES` are **still** the hand-transcribed literals above, not a loader
reading that file; the `TODO(release_calendar.yaml)` in `schedule.py`'s
module docstring marking where to wire one in is still open (a separate,
not-yet-done migration — out of scope for the 2026-07-08 wiring pass that
restricted `--due` to runner-implemented sources and added `dg_refresh`,
below). The CLI surface (`--due`, `--explain`) won't need to change when it
does land.

### Which sources `--due` actually emits

`pipeline/schedule.py`'s `SOURCES` list is restricted (2026-07-08) to exactly
what `pipeline.runner.SOURCES` implements — `nbs_cpi`, `nbs_retail`,
`pboc_money`, and `dg_refresh` — checked by an import-time assertion so the
two can never silently drift apart again. Every other named source from the
rebuild brief's original window table (`nbs_ppi`, `nbs_pmi`, `customs_trade`,
`nbs_iva`, `nbs_fai`, `nbs_gdp`) is commented out in `schedule.py`, not
deleted — its window/cadence/series_id data is kept on file, ready to
uncomment. **Most of them don't actually need a new parser at all**:
`dg_refresh` (see §1.1 below) already keeps CPI/PPI, PMI, IVA, FAI, trade, and
GDP current directly from the DG API. Only `nbs_70city` (the 70-city panel)
and `nbs_income` (owned by `docs/MIGRATION-MAP.md`'s migration agent) are
genuinely still waiting on a not-yet-written press-release parser.

**Before this fix**, a scheduled run touching one of the not-yet-implemented
names filed a **false** "Gate A blocked" issue — `pipeline.runner` exits 2 for
"unrecognized `--source`", the same code Gate A itself used for a real block,
so the workflow couldn't tell the two apart from the exit code alone. That
collision no longer exists at all now that unrecognized-source moved to exit
**3** (§4's exit-code table); `schedule.py`'s own restriction is an
independent, belt-and-suspenders fix for the same underlying problem.

### 1.1 `dg_refresh`: keeping DG-mirrored series current without a parser

`pipeline/dg_refresh.py` re-pulls the last few periods for every catalog
series whose provenance is NBS's DG national-data API directly (`src`
starting `"dg:"` on its observations — see `pipeline/backfill/backfill.py`'s
one-time historical sweep): CPI/PPI (9 concepts), PMI (2), surveyed
unemployment (3 live ids), industrial value added, FAI, customs trade (2),
M0/M1/M2 (3), and GDP + its three 贡献率 shares (4) — 26 series, none of
which need (or will ever get) their own HTML press-release parser. Wired in
as `--source dg_refresh`, through the exact same stage → Gate A → write flow
as every other source.

`dg_refresh` doesn't ride one release window the way `nbs_cpi`/`nbs_retail`/
`pboc_money` do — it covers nine concepts spanning five different NBS/PBoC/
CFLP/GACC release schedules. Rather than a single hand-picked day-of-month
range (which would either miss a real window or be right only by
coincidence), `schedule.py` treats it as due whenever **any** of those five
window groups (`cpi_ppi`, `pmi`, `trade`, `pboc_money`, `nbs_activity`) is
both open and stale for its own DG-sourced representative series
(`DG_REFRESH_CHECKPOINTS` in `schedule.py`) — reusing the exact same "in
window AND stale" test every other source already uses, just OR'd across
five groups instead of checked against one. This was the more correct of the
two designs considered (the other being a fixed `[14,18] ∪ [1,3]` range):
CPI's day 9-13 and PBoC money's day 10-15 windows aren't inside `[14,18] ∪
[1,3]` at all, so that range would have left `dg_refresh` unable to catch a
CPI- or money-supply-specific staleness gap for most of the month.

### Bypassing the schedule

`workflow_dispatch` on **Update data** takes two inputs:

- `source` — run this one source directly, skipping `pipeline.schedule`
  entirely (includes `dg_refresh`). Useful for backfilling a source you know
  just published, or for testing a new parser.
- `no_gate` — passes `--no-gate` to `pipeline.runner`, bypassing Gate A.
  **Emergency use only** (see §4).

### Debugging what the scheduler thinks

```bash
python -m pipeline.schedule --explain                    # today, human-readable, every source
python -m pipeline.schedule --explain --date 2026-07-25   # any date, for testing a window boundary
python -m pipeline.schedule --due                         # what update-data.yml actually consumes
```

---

## 2. How the gates decide

Two independent gates, per `docs/DATA-CONTRACT.md` §11:

- **Gate A — ingest validation (`pipeline.validate`), runs *inside*
  `pipeline.runner`.** Schema validation, caliber sanity, span checks,
  YTD-diff sanity, no-YoY-across-a-break. `pipeline.runner --source X` exits
  **2** when Gate A blocks. Blocks a **data commit** — the bad release simply
  never reaches `data/`.
- **Gate B — independent audit (`pipeline.audit`), runs in `deploy.yml`
  before every deploy.** Re-verifies built `site-data/` against
  `data/archive/`: release-text spot checks, YTD-diff recompute, 70-city
  average/up-count recompute. `pipeline.audit --site-data site-data/
  --offline` exits **2** when it blocks. Blocks a **deploy** — the previous
  Pages deployment simply stays live.

### Where reports/artifacts/issues land

| | Gate A | Gate B |
|---|---|---|
| Report path (assumed) | `validate_reports/` | `audit_reports/` (existing convention, see README.md) |
| Workflow artifact | `gate-a-validation-reports` | `gate-b-audit-report` |
| Tracking issue | "数据门禁拦截 (Gate A)", label `data-audit` | "数据审计拦截 (Gate B)", label `data-audit` |
| Issue behavior | comments on the existing open issue if one exists, else opens a new one | same |

**`validate_reports/` is confirmed wrong, not just an unconfirmed guess**
(updated 2026-07-08 — `pipeline/validate/` exists now, so this is no longer
an open question). `pipeline/validate/gate.py`'s `run_gate()` writes
`gate_report.json`/`gate_report.md` **inside the per-run STAGED temp
directory** (`pipeline/validate/staging.py`'s `stage_release()` — a fresh
`tempfile.mkdtemp()` per invocation), not into any fixed top-level
`validate_reports/` folder — there is nothing at `validate_reports/` for
`update-data.yml`'s artifact-upload step to ever find. Both workflows also
attach the raw captured stdout/stderr unconditionally (`/tmp/run-artifacts/
blocked_output.txt`), which is why this has never surfaced as a broken
artifact in practice — that capture already carries the full
`gate_report.to_markdown()` text (`pipeline.runner` prints it), just not as
a separately-browsable JSON file. `.github/workflows/update-data.yml` isn't
this wiring pass's file to fix, but reconciling its `path:` (either point it
at nothing and drop the artifact upload for Gate A, or have `pipeline.runner`
copy the staged report to a fixed location before the staged dir is
discarded) is flagged here for whoever picks it up next. `pipeline.runner`
now also prints a machine-readable `GATE_BLOCKED` marker line to stderr on a
block (§4), independent of this report-path question, for a workflow to grep
if it wants a tighter signal than "the whole captured stdout/stderr blob."

### The needs-ack flow for legitimate revisions

NBS/PBoC do sometimes revise a previously published number (base-period
rebase, methodology change, a genuine correction). Gate A's job is to catch
*surprising* deltas, which means a real, legitimate revision will also trip
it. Until `pipeline.validate` grows a persisted acknowledgment mechanism, the
manual path is:

1. Open the Gate A issue's artifact, confirm the flagged delta against the
   official release page (the URL is in the runner's captured output).
2. If it's a real revision: re-dispatch **Update data** for that one source
   with `no_gate: true` to let it land, and note in the commit/issue why the
   jump is legitimate (e.g. "PBoC redefined M1 in Jan 2025 — see
   `docs/ACQUISITION.md` Group 6"). Close the issue.
3. If it's a parser bug (format drift, wrong column): fix the parser, add a
   regression fixture under `pipeline/fixtures/raw/`, re-dispatch normally
   (no `no_gate` needed once the fix is in).

---

## 3. Cutover procedure (flipping Pages to GitHub Actions)

`deploy.yml` only runs its deploy job when `vars.PAGES_LIVE == '1'` (manual
`workflow_dispatch` is always allowed, for testing ahead of cutover). Until
that variable is set, scheduled data commits land in `data/` normally but
never trigger a Pages deploy — the existing root-level site keeps serving.

**How a data commit actually reaches a deploy (corrected 2026-07-08 —
adversarial review, CRITICAL bug 1).** `update-data.yml`'s commit step
pushes using the built-in `GITHUB_TOKEN`, and a `GITHUB_TOKEN`-authored push
**never fires another workflow's own `on: push` trigger** — a deliberate
GitHub anti-recursion rule, not a misconfiguration in either workflow's
YAML. `deploy.yml`'s `on: push` therefore only ever fires for a *human's*
own direct push to `data/**`/`site/**`/`pipeline/**`, never for the
scheduler's commits, which is nearly all real traffic once cutover happens.
`update-data.yml` closes this gap itself: after a successful, actually-
pushed commit, it explicitly runs `gh workflow run deploy.yml` (needs
`actions: write` in its `permissions:`, which it has), gated on the exact
same `vars.PAGES_LIVE == '1'` check `deploy.yml`'s own job already enforces
— so pre-cutover this dispatch call is a harmless no-op (the job's own `if:`
skips it), and post-cutover it's the only thing that actually connects the
two workflows for the scheduler's own commits.

To cut over:

```bash
# 1. Point Pages at the Actions build instead of a branch/folder.
gh api repos/{owner}/{repo}/pages -X PUT -f build_type=workflow
# (equivalent to: repo Settings → Pages → Source = "GitHub Actions")

# 2. Flip the gate so update-data.yml's re-dispatch (and any human push) actually deploys.
gh variable set PAGES_LIVE -b 1

# 3. Confirm a real deploy:
gh workflow run deploy.yml
```

After cutover, retire the old root-level static site files (`index.html`,
`app.js`, `data.js`, `styles.css`, etc. at repo root) once the new
`site/`-based Pages deployment is confirmed serving correctly — they are
legacy inputs per `docs/MIGRATION-MAP.md`, not read by any workflow here.

---

## 4. Manual commands

```bash
# Run one source locally (writes to data/ for real; add --dry-run to preview)
python -m pipeline.runner --source nbs_cpi
python -m pipeline.runner --source nbs_cpi --dry-run

# dg_refresh: same runner, same flags -- re-pulls the last few periods for
# every DG-mirrored series (§1.1) directly from the DG API, live.
python -m pipeline.runner --source dg_refresh --dry-run

# --fixture: offline proof against a COMMITTED fixture file, no live fetch or
# discovery at all -- how field_map.yaml's mappings get verified end-to-end
# without a network call. Not applicable to --source dg_refresh (its own
# offline story is pipeline.dg_refresh.run()'s own --lookback/--today, not a
# raw HTML fixture).
python -m pipeline.runner --source nbs_cpi --fixture pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html --dry-run

# Run Gate A / Gate B locally
python -m pipeline.validate --staged <staged-dir> --batch <batch.json>   # standalone Gate A (pipeline.runner runs this inline; see above)
python -m pipeline.audit --site-data site-data/ --offline

# Regenerate site-data/ bundles from data/
python -m pipeline.build --out site-data/

# Ask the scheduler what it would do, without running anything
python -m pipeline.schedule --explain
```

### `pipeline.runner` exit codes

Standardized 2026-07-08 (previously exit 2 meant both "Gate A blocked" *and*
"unrecognized `--source`", a collision `docs/OPERATIONS.md` used to flag as a
known rough edge — resolved by splitting the second case out to exit 3):

| Exit | Meaning | `data/` effect |
|---|---|---|
| `0` | ok — wrote a change (or would, under `--dry-run`), or a clean no-op (nothing due / nothing new) | written (or previewed) |
| `1` | genuine fetch or parse failure (network error, format drift) | untouched |
| `2` | Gate A **BLOCKED** — also printed as a `GATE_BLOCKED` marker line on stderr | untouched (unless `--no-gate`) |
| `3` | usage error — unrecognized `--source` | untouched |

`update-data.yml`'s own `case "$code" in 2) ...; 0) ...; *) ...; esac` already
treats every non-0/non-2 code identically (a generic `::error::`, never filed
as a Gate A issue) — the exit-3 split needed **no workflow change** to be
safe; it was already only exit 2 the workflow specifically special-cased.

---

## 5. Failure playbook

**Gate A issue filed ("数据门禁拦截 (Gate A)")**
1. Download the `gate-a-validation-reports` artifact from the linked run.
2. Read the failing-checks output — it's the runner's own captured stdout
   for the blocked source(s).
3. Decide: parser bug (fix + regression fixture + re-dispatch) or legitimate
   revision (ack per §2, re-dispatch with `no_gate: true`).
4. Close the issue once the next run for that source succeeds.

**Gate B issue filed ("数据审计拦截 (Gate B)")**
1. The previous deployment is still live — there is no user-facing impact
   yet, but the site is now stale relative to `data/` until this is fixed.
2. Download `gate-b-audit-report`, find what recomputation disagreed with
   the built value.
3. Fix forward in `data/` or the build step, push to `main` — `deploy.yml`
   re-runs automatically on the next push touching `data/**`/`site/**`/`pipeline/**`.

**Nothing runs / scheduled job didn't fire**
GitHub Actions can delay scheduled workflows under platform load; this is a
known GitHub limitation, not a bug here. Confirm with `gh run list
--workflow update-data.yml`; if a run is simply missing (not delayed),
dispatch manually and investigate.

---

## 6. Known assumptions (read before debugging something that "should" work)

This automation was built against contracts owned by concurrent workstreams,
several of which landed since this runbook was first written. Recorded here
so a future debugging session doesn't mistake a landed-but-different (or
now-resolved) contract for a bug in the workflows:

**Resolved 2026-07-08 (the final wiring pass):**

- **`pipeline.validate` and `pipeline.audit` now exist on disk**, with the CLI
  flags this runbook always assumed (`python -m pipeline.validate --staged
  <dir> --batch <batch.json>`, `python -m pipeline.audit --site-data
  site-data/ --offline`) and the exit-code-2-means-blocked contract intact —
  see §4's exit-code table for `pipeline.runner`'s own (now-standardized)
  codes.
- **`pipeline.runner` has had `--no-gate` (and now `--fixture`) since Gate A
  landed** — both flags are documented in §4.
- **Exit code 2 no longer means two different things.** Unrecognized
  `--source` moved to exit **3**; exit 2 is Gate A BLOCKED, exclusively (§4).
  `pipeline/schedule.py`'s `SOURCES` is now also restricted to exactly what
  `pipeline.runner.SOURCES` implements (§1 "Which sources `--due` actually
  emits") — a second, independent fix for the same false-positive-issue
  problem this exit-code collision used to cause.
- **`validate_reports/` as Gate A's report path was a guess, and is now
  confirmed wrong** — see §2's table; `update-data.yml`'s artifact-upload
  `path:` for it still needs reconciling (not this pass's file to fix).
- **DATA-CONTRACT.md §11.2's PR-based flow was a stale draft, not the shipped
  design** — rewritten 2026-07-08 to match: scheduled runner → staged Gate A
  → direct commit to `main` → `deploy.yml` rebuilds + runs Gate B before
  deploying. No PR step, by decision.
- **`pipeline/config/release_calendar.yaml` now exists**, keyed by release
  CONCEPT (`cpi_ppi`, `pbc_money`, ...), not by agency. `pipeline/audit`'s
  freshness check (`gate_b.freshness` / `pipeline/audit/release_calendar.py`)
  originally assumed an agency-keyed schema that never actually landed that
  way — it used to (wrongly) treat the real file as "incompatible" and
  silently fall back to a generic default for every agency. Fixed: it now
  reads the real, concept-keyed file natively, bridging agency → concept
  key(s) → budget via a small `AGENCY_TO_CALENDAR_KEYS` table (most lenient
  of every concept key that agency publishes under). `pipeline/schedule.py`'s
  own `WINDOWS` dict is a *separate* consumer and still does not read this
  file at all (§1) — that migration is still open.
- **`field_map.yaml`'s placeholder ids are now reconciled** against the real
  `data/catalog.json` (105 series) and the three parsers' actual output. Most
  placeholders matched by luck or a one-word id difference (e.g. `限额以上单位
  消费品零售额` → `nbs-retail-above-quota`, not `-above-quota-total`); a
  meaningful minority of source fields the parsers extract have **no**
  catalog series at all (NBS's CPI urban/rural split and 7 of 8 category
  breakdowns; the new 2026 online-retail combined/services-only splits;
  PBoC's entire TSF/loan/deposit family beyond M0/M1/M2) — those are commented
  out in `field_map.yaml` with a one-line reason each, not silently dropped.
  `pipeline.runner`'s own "unmapped source fields (add to
  pipeline/config/field_map.yaml)" print line is the honest, non-alarming
  signal for exactly this case.
- **`pipeline.build`'s bundle gained a `plot_kind` field** (`"level"` or
  `"yoy"`) on every series entry, plus a fix for two structurally-empty-bundle
  bugs: `nbs-fai`/`nbs-industrial-va` (`value_type=="yoy_pct"` — NBS/DG
  publish no absolute level for these at all, only a growth rate, which used
  to leave `latest`/`headline`/`takeaway`/`yoy_series` permanently null or
  empty) and the three `nbs-gdp-contribution-*` shares (`value_type=="ratio"`
  — widened into the level-only takeaway path, with the 荣枯线 boom-bust-line
  clause explicitly disabled for a share, since crossing 50% means nothing
  for a contribution rate). `site/**`'s frontend isn't required to read
  `plot_kind` for this fix to be correct (`yoy_series`/`level_series` are
  populated correctly either way), but it's there for whoever wires up the
  chart to know a `"yoy"`-kind bundle's numbers are a growth rate, not a
  level, without re-deriving that from `value_type` client-side.

**Resolved 2026-07-08, second pass (independent adversarial review of the
automation seam — 7 findings, all fixed):**

- **CRITICAL — a `GITHUB_TOKEN` push never triggered `deploy.yml`.** See §3's
  rewritten "how a data commit actually reaches a deploy" note.
  `update-data.yml` now explicitly re-dispatches `deploy.yml` after a
  successful push (needs `actions: write`, added to its `permissions:`).
- **CRITICAL — freshness read `data/catalog.json`'s never-advancing
  `latest`.** See §1's rewritten steady-state note. `pipeline.schedule` now
  scans `data/series/<id>.json` directly.
- **HIGH — `pipeline.dg_refresh`'s per-family loop caught only
  `(DGError, TreePathError)`.** Any other exception (a raw `requests` error,
  a `KeyError` on a malformed tree node, a JSON error) used to abort the
  *entire* refresh before stage/gate/promote ever ran, losing every OTHER
  family's perfectly good data. Now catches `Exception` per family; the
  all-families-failed guard (exit 1) is unchanged.
- **MEDIUM — a crashing `pipeline.schedule --due` looked identical to
  "nothing due."** `update-data.yml`'s `due="$(...)"` is now
  `if ! due="$(...)"; then echo "::error::..."; exit 1; fi`.
- **MEDIUM — `validate_reports/` had nothing writing to it (a second,
  independent instance of the same gap §2's table already flagged).**
  `pipeline.runner` and `pipeline.dg_refresh` now both copy their staged
  `gate_report.json`/`.md` to `validate_reports/<source>/` before the staged
  temp dir is ever discarded.
- **LOW — `deploy.yml`'s config.mjs rewrite guard hardcoded single quotes
  and ran unscoped across the whole file.** Now quote-agnostic
  (`grep -Eq` with a `["']` class) and scoped to the `DATA_BASE` line only.
- **LOW — a rebase conflict on `update-data.yml`'s push killed the commit
  with no retry.** Now a 3-attempt `git pull --rebase && git push` loop.
- **Housekeeping check (already satisfied, no fix needed):** both workflows
  already had `concurrency` groups preventing self-overlap
  (`update-data.yml`: `group: update-data`; `deploy.yml`: `group: pages`,
  both `cancel-in-progress: false`).

**Still open:**

- **Reachability of `.cn` hosts from a GitHub-hosted (US) runner is still an
  open question** — `docs/ACQUISITION.md` calls this "the single riskiest
  assumption" in the whole rebuild. Not resolved by this pass: `pipeline.
  dg_refresh`'s live test (2026-07-08, this pass) completed 56 real DG API
  requests successfully from *this* environment (22 of the 25 series it
  actually targets returned data — 26 total DG-sourced catalog ids, one of
  which, the frozen pre-2023-12 youth-unemployment id, is intentionally never
  requested at all; the other 3 not returning data were M0/M1/M2, which
  legitimately had nothing new — DG's own money-supply table hadn't caught up
  to May 2026 yet even as of this live check) — but that only confirms this
  environment's own egress, which may not match the actual `ubuntu-latest`
  GitHub Actions runner's. If a real scheduled run shows `dg_refresh` failing
  to reach DG from CI, `update-data.yml`'s `runs-on` will need to move to a
  self-hosted or CN-egress runner.
- **`pipeline/schedule.py`'s `WINDOWS`/`SOURCES` still don't read
  `pipeline/config/release_calendar.yaml`** — see this section's entry above
  and `schedule.py`'s own `TODO(release_calendar.yaml)`. The CLI surface
  (`--due`, `--explain`) is designed not to need to change when this lands.
- **`nbs_70city` and `nbs_income`** are the only two sources left in the
  original rebuild brief's window table with no runnable source at all yet
  (not even via `dg_refresh` — see §1.1) — genuinely waiting on a
  press-release parser (70-city) or owned by the migration agent (income,
  `docs/MIGRATION-MAP.md`).
