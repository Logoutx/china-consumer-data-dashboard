# DATA CONTRACT — China Consumer / Economy Dashboard (rebuild)

Binding schema and layout contract for the rebuilt dashboard. Everything that reads or
writes dashboard data — the Python pipeline, the static site, the audit gate, and any
future importer — MUST conform to this file. Where this document and a JSON Schema in
`data/schemas/` disagree, **the JSON Schema wins** (it is machine-enforced at the ingest
gate); open a PR to fix whichever is wrong.

Scope target: ~100 series across 8 sections
(物价 / 消费 / 收入与信心 / 就业 / 楼市 / 钱与信贷 / 宏观大盘 / 高频脉搏),
frequencies monthly / quarterly / annual, back to 1985 for the oldest series.

---

## 0. Design axioms (why the shape is what it is)

1. **Published YoY is data, not a computation.** NBS computes 同比 on a comparable-caliber
   base we cannot reconstruct from published levels (base-period revisions, seasonal-caliber
   adjustments, Jan-Feb merges). Every published growth rate is stored verbatim in its own
   field and is **never** recomputed from levels — not in the pipeline, not in the client.
2. **Caliber is explicit, always.** 当月 (single-period) and 累计 (YTD-cumulative) are
   different variables that happen to share a name. They live in separate fields and are
   labelled in the catalog. A series states which calibers it carries.
3. **The as-published truth is immutable.** Raw captures in `data/archive/` are the source
   of record for "what the release page actually said on day X." Series files hold current
   best values plus a revision log; they are derived from the archive and may be rebuilt.
4. **Every file is a reviewable git diff.** One observation per line, stable key order,
   sorted arrays. A monthly refresh should read as a handful of appended/changed lines, and
   a reviewer should be able to see exactly which number moved.
5. **Smallest thing that works.** One series-file schema for all value types; semantics
   (unit, what `m` means) travel in the catalog, not in bespoke per-domain schemas.

---

## 1. Directory layout

```
data/
  catalog.json                       # manifest: every series + panel + section metadata
  series/<id>.json                   # one file per non-panel series (see §3)
  panels/<panel-id>.json             # matrix/panel series, e.g. 70-city (see §5)
  panels/<panel-id>/<shard>.json     # OPTIONAL shards of a large panel
  annotations.json                   # human-authored notes, keyed by series+period (see §7)
  archive/<source>/<release-id>.json # immutable raw captures — as-published truth (see §8)
  schemas/
    series.schema.json               # draft-07, enforced at ingest gate
    catalog.schema.json              # draft-07
    panel.schema.json                # draft-07 (panel variant of a series file)
site-data/                           # BUILD OUTPUT — consumed by the browser (see §10)
  index.json                         # landing tiles (headline series only)
  <section-id>.json                  # one bundle per section
  panels/<panel-id>.json             # lazy-loaded panel bundle(s)
pipeline/
  fetch/  parse/  normalize/  validate/  build/  audit/   # stages (see §11)
  config/
    field_map.yaml                   # source_field -> (series_id, measure) — human-owned
    release_calendar.yaml            # release windows for the GitHub Actions poller
docs/
  DATA-CONTRACT.md   MIGRATION-MAP.md
```

`data/` is the human-reviewed source of truth. `site-data/` is generated and never
hand-edited. See §10 for whether `site-data/` is committed vs built in CI.

The existing top-level `*_release_archive.json`, `property_city_history*.json`,
`retail_dashboard_data.xlsx`, and `_cache/` are legacy inputs; they map into
`data/archive/` and are read-only during migration (see MIGRATION-MAP.md).

---

## 2. Series ID scheme

**Format:** `<agency>-<slug>[-<qualifier>]`, lowercase kebab-case, ASCII only.

- `agency` — publishing body: `nbs` (国家统计局), `pbc` (人民银行), `mof` (财政部),
  `mohurd` (住建部), `customs` (海关总署), `caam`, `safe`, `cflp` (PMI). One agency per id
  (the agency that *publishes* the number, not a re-distributor).
- `slug` — English concept, hyphenated: `retail-total`, `cpi`, `income-disposable`,
  `m1`, `land-transfer-revenue`, `urban-unemp-youth-1624`.
- `qualifier` — optional disambiguator: geography/segment (`-urban`, `-rural`, `-core`)
  or a post-break basis (`-exstudent`). See §2.1.

**Rules**

1. An id is **immutable** once minted and appears in a release. Never renumber, never reuse.
2. IDs, field names, enum values, section ids: **English only**. Display strings: Chinese
   (`name_zh`) + English (`name_en`), per §12 typesetting.
3. `panel` ids follow the same scheme and additionally set `panel` in the catalog (§5).

### 2.1 Break policy — same id + break marker vs new id (DECISION)

A **series break** is any discontinuity that makes a value on one side not comparable to a
value on the other. The decision rule is a single test:

> **Can the old and the new numbers sit in one column and be read straight down without
> lying?**
> - If NBS provides (or the rebase implies) a valid splice of the *same concept* →
>   **keep the same id** and record a `break` entry. The series stays one file.
> - If NBS **discontinues** the old series and starts a **new, non-back-linked** one, or the
>   thing being counted changes identity → **mint a new id**; freeze the old series with an
>   `end` and link the two with `supersedes` / `superseded_by`.

Rationale: the id is the identity of *a comparable variable through time*. Splicing across a
break NBS refuses to splice would fabricate a comparison; forking a new id when NBS itself
rebased the same concept would litter the catalog and break long charts for no reason.

Applying the rule to the known breaks:

| Event | Decision | Mechanics |
|---|---|---|
| CPI / PPI rebase to 2025 base (eff. 2026-01) | **Same id** (`nbs-cpi`, `nbs-ppi`) | `break{kind:"rebase", no_yoy_across:true, yoy_valid_from:"2026-01"}`. Do **not** mint `-2025base`. |
| M1 redefinition (2025-01, NBS/PBC revised history back to 2024-01) | **Same id** (`pbc-m1`) | `break{kind:"redefinition", effective:"2025-01", no_yoy_across:true, yoy_valid_from:"2025-01"}` — YoY valid once 12 months of restated base exist. Pre-2024-01 obs flagged `old_caliber`. |
| Youth unemployment excl-students (old suspended 2023-08; new methodology from 2023-12 / 2024-01) | **New id** | old `nbs-urban-unemp-youth-1624` gets `end:"2023-07"` + `break{kind:"suspended"}`; new `nbs-urban-unemp-youth-1624-exstudent` starts 2023-12. `superseded_by` / `supersedes` link them. **No cross-id YoY, ever.** |
| Online-retail indicator replacement (2026) | **New id** | old online indicator frozen with `end`; new indicator new id; linked. |

`no_yoy_across` is a hard instruction to the build stage: at the seam, YoY series get a
`null` so the line does not connect and no delta is shown (§10.2).

---

## 3. Series file schema (`data/series/<id>.json`)

A series file is: identity header → `observations` (current values) → `revisions` (change
log) → `breaks`. Full JSON Schema: `data/schemas/series.schema.json`.

### 3.1 Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema` | string | `"series/v1"` — schema tag for forward migration. |
| `id` | string | Matches §2, equals the filename stem. |
| `name_zh`, `name_en` | string | Display names. `name_zh` per §12. |
| `unit_zh`, `unit_en` | string | e.g. `亿元`/`100M CNY`, `元`/`CNY`, `%`, `个`/`cities`. |
| `value_type` | enum | What `m` / `ytd` **mean**: `level` \| `index` \| `mom_pct` \| `yoy_pct` \| `rate_pct` \| `count` \| `ratio`. Drives rendering; see §3.3. |
| `freq` | enum | `M` \| `Q` \| `A`. Default frequency; a per-observation `freq` may override on a switch. |
| `calibers` | array | Subset of `["single","ytd","mom"]` this series populates. FAI → `["ytd"]`; retail-total → `["single","ytd"]`; property price → `["single"]` (its single value is a MoM %). |
| `decimals` | int | Display precision. |
| `observations` | array | Current best values, one object per period, ascending by `period`. §3.2. |
| `revisions` | array | Append-only change log. §4. |
| `breaks` | array | Break entries. §2.1 / §4.2. |
| `source` | object | `{agency, dataset_zh, dataset_en, url}`. Release cadence lives in the catalog. |
| `derived` | object \| null | `null` if primary; else `{rule, inputs:[ids], caliber?}`. §6. |
| `coverage_note_zh` | string \| null | Free-text caveat (migrated from the current `coverage_note`). |
| `generated_at` | string | ISO-8601 build timestamp. |

### 3.2 Observation object

One per period. **Emit each observation as a single compact line** (see §9) so a revision is
one changed line. Populate only the measures the series carries; omit the rest (do not write
`null` for a caliber the series never publishes — reserve `null` for "expected but missing").

| Field | Type | Meaning |
|---|---|---|
| `period` | string | Anchor period. `"YYYY-MM"` (monthly), `"YYYY-Qn"` (quarterly), `"YYYY"` (annual). For a merged single-period print (Jan-Feb) the anchor is the **end** month (`"YYYY-02"`). |
| `freq` | enum? | Present only when it differs from the series default (a mid-life frequency switch). |
| `span` | int? | # of base periods the **single-period** value aggregates. Default 1. Jan-Feb combined → `2`. Absent ⇒ 1. Does not apply to the `ytd` lane (YTD extent is implied by `period`). |
| `flags` | array? | Zero or more tags: `jan_feb` (merged Jan-Feb print), `provisional`, `estimated`, `old_caliber` (pre-break basis), `break_first` (first obs of a new basis). |
| `m` | number? | Single-period (当月/单季/单期) headline, in the unit `value_type` declares. For `value_type:"mom_pct"` (property price) this is the 环比 %. |
| `m_yoy` | number? | **Published** YoY (同比) printed alongside `m`. Verbatim. Never computed. |
| `ytd` | number? | Cumulative-to-date value (累计), same unit as `m`. |
| `ytd_yoy` | number? | **Published** YoY on the cumulative caliber. Verbatim. |
| `mom` | number? | **Published** 环比 %, only when it is an *extra* measure beyond `m` (e.g. CPI publishes a headline plus a separate 环比). For property, the 环比 **is** `m`, so `mom` is unused. |
| `real_yoy` | number? | **Published** real (inflation-adjusted) YoY. Verbatim. (Income series carry this.) |
| `src` | string? | Provenance pointer: `release-id` of the archive capture this value came from. |

**Absent vs null:** a measure key is *absent* if the series never publishes it. It is
`null` if the period exists but that value is genuinely missing/blank in the source (e.g. a
city with no print that month). The audit distinguishes these.

### 3.3 How `value_type` maps to the caliber lanes

| `value_type` | `m` holds | `m_yoy` | `ytd` | Example series |
|---|---|---|---|---|
| `level` | 当月 level (亿元 / 元) | 当月 同比 | 累计 level | retail-total, income (ytd only) |
| `index` | index level (e.g. CPI, 上年同月=100) | 同比 % | — | nbs-cpi |
| `mom_pct` | 环比 % (index change) | 同比 % | — | 70-city price cells |
| `rate_pct` | the rate itself (%) | pp change (if NBS prints it) | — | urban unemployment |
| `count` | integer count | — | — | 70城上涨城市数 |
| `ratio` | a share (%) | pp change | share (ytd) | online-share (derived) |

The catalog's `value_type` + `unit` tell the renderer whether `m` is currency, an index, a
percent, or a count. The series schema does not branch on type — one shape fits all.

### 3.4 Full worked example — `nbs-retail-total` (社零总额)

Exercises 当月 + 累计, published YoY on both, a Jan-Feb combined print, a break-free level
series, and a seeded legacy revision.

```json
{
  "schema": "series/v1",
  "id": "nbs-retail-total",
  "name_zh": "社会消费品零售总额",
  "name_en": "Total retail sales of consumer goods",
  "unit_zh": "亿元",
  "unit_en": "100M CNY",
  "value_type": "level",
  "freq": "M",
  "calibers": ["single", "ytd"],
  "decimals": 1,
  "source": {
    "agency": "nbs",
    "dataset_zh": "国家统计局数据发布 · 社会消费品零售总额",
    "dataset_en": "NBS Data Release — Total retail sales",
    "url": "https://www.stats.gov.cn/sj/zxfb/"
  },
  "derived": null,
  "coverage_note_zh": "1985-01 起为国家数据月度序列；发布稿口径的当月/累计从可解析范围起补入。",
  "observations": [
    { "period": "1985-01", "m": 338.9, "src": "natdata:monthly" },
    { "period": "1985-02", "m": 320.6, "src": "natdata:monthly" },
    { "period": "2026-02", "span": 2, "flags": ["jan_feb"], "m": 83726, "m_yoy": 4.0, "ytd": 83726, "ytd_yoy": 4.0, "src": "rel:20260318" },
    { "period": "2026-03", "m": 40940, "m_yoy": 5.9, "ytd": 124671, "ytd_yoy": 4.6, "src": "rel:20260416" },
    { "period": "2026-04", "m": 40200, "m_yoy": 5.1, "ytd": 165021, "ytd_yoy": 4.7, "src": "rel:20260519" },
    { "period": "2026-05", "m": 41090, "m_yoy": -0.6, "ytd": 206031, "ytd_yoy": 1.4, "src": "rel:20260616" }
  ],
  "revisions": [
    { "period": "2026-04", "measure": "ytd", "old": 165000, "new": 165021, "revised_on": "2026-06-16", "source": "rel:20260616", "note": "累计值随下一期发布修订" }
  ],
  "breaks": [],
  "generated_at": "2026-07-08T00:00:00Z"
}
```

Notes on the example:
- The `2026-02` row is the **Jan-Feb combined** print (`span:2`, `flags:["jan_feb"]`). In the
  1—2月 社零 release the combined total is *simultaneously* the single-period figure and the
  cumulative, so `m == ytd` and `m_yoy == ytd_yoy` by construction; there is no `2026-01`
  observation. **This row's values are illustrative:** in the *current* `data.js`,
  `retail_total` has a Jan-Feb gap for 2018-2026 (only `online_goods` is stored at Feb), so
  the rebuild fills it from the 1—2月 release — see MIGRATION-MAP.md §3. For a series where
  NBS prints **only** the 累计, omit `m`/`m_yoy` and populate `ytd`/`ytd_yoy` only; for a
  series whose 当月 is **derived** by YTD-differencing, the Jan-Feb `m` equals the 1—2月 累计
  (no prior YTD to subtract), so `m == ytd` again with `derived:true`.
- Every `*_yoy` is the number NBS printed; none is derived from the `m`/`ytd` levels.
- The `revisions` entry shows the compact revision model (§4): the observation itself already
  holds the *current* `ytd` (165021); the log records that the first print was 165000, revised
  on the next release. The full as-published capture lives in `data/archive/`.

---

## 4. Revisions & breaks model

### 4.1 Revisions (compact vintage)

NBS revises history (routine next-release tweaks + periodic benchmark revisions). We do
**not** store per-fetch full snapshots in the series file (that doubles a 40-year series).
Instead:

- `observations[]` always holds the **current** best value (what the site shows).
- `revisions[]` is an **append-only** log; each entry is one measure of one period changing:

  ```json
  { "period": "2026-04", "measure": "ytd", "old": 165000, "new": 165021,
    "revised_on": "2026-06-16", "source": "rel:20260616", "note": "…optional" }
  ```

  `measure` ∈ the observation measure fields (`m`,`m_yoy`,`ytd`,`ytd_yoy`,`mom`,`real_yoy`).
  `revised_on` is the date the revising release was published (may be `null` for
  legacy-seeded entries where the date is unknown — mark `source:"legacy-migration"`).

**Build behavior:** when a fetch yields a value that differs from the current observation for
an existing period, the build appends a revision entry **and** updates the observation
in place. First prints are not logged as revisions (the observation *is* the first print
until something changes it).

**First-print recovery** (for the audit): the value "as originally published" is the raw
capture in `data/archive/<source>/<release-id>.json`, not a field in the series file. This is
deliberate — the archive is immutable truth; the series file is current truth + a diff log.

This is strictly more information than today's inline `published_*`/`latest_*` twins (which
keep only two vintages and no date), while keeping the series file compact and diffable.

### 4.2 Breaks (first-class metadata)

`breaks[]` in the series file, mirrored into the catalog for filtering:

```json
{ "effective": "2026-01", "kind": "rebase",
  "no_yoy_across": true, "yoy_valid_from": "2026-01",
  "supersedes": null, "superseded_by": null,
  "note_zh": "CPI 定基调整至 2025 年基期，跨基期同比不可比。",
  "note_en": "CPI rebased to 2025 base; YoY not comparable across the seam." }
```

`kind` ∈ `rebase` | `redefinition` | `methodology` | `indicator_replacement` |
`suspended` | `resumed`. For new-id breaks, the *old* series carries `end` + a `break` with
`superseded_by`, and the *new* series' first obs carries `flags:["break_first"]`.

**Invariant enforced at build:** no YoY value — stored or rendered — may span a break with
`no_yoy_across:true`. The build inserts nulls at the seam in every derived YoY series.

---

## 5. The 70-city panel (DECISION: panel format, not 140 series)

The 70-city price data is **one catalog entry backed by a panel/matrix file**, not
70×2 individual catalog series.

**Shape** — `data/panels/nbs-70city-price.json`:

```json
{
  "schema": "panel/v1",
  "id": "nbs-70city-price",
  "name_zh": "70 个大中城市商品住宅销售价格",
  "name_en": "70-city residential sales price",
  "value_type": "mom_pct",
  "freq": "M",
  "dimensions": {
    "city": ["北京","天津","石家庄", "…70 total"],
    "metric": ["new_home","resale_home"]
  },
  "measures": ["m","m_yoy"],
  "periods": ["2011-01", "…", "2026-05"],
  "cells": {
    "北京": {
      "new_home":    { "m":    [ /* one number per period, index-aligned to periods[] */ ], "m_yoy": [ … ] },
      "resale_home": { "m":    [ … ], "m_yoy": [ … ] }
    },
    "…": {}
  },
  "revisions": [ { "city":"北京","metric":"new_home","period":"2026-04","measure":"m","old":-0.3,"new":-0.4,"revised_on":"2026-05-18","source":"rel:…" } ],
  "breaks": [],
  "source": { "agency":"nbs", "dataset_zh":"70 个大中城市商品住宅销售价格变动情况", "url":"…" },
  "generated_at": "…"
}
```

Cells are **period-aligned arrays** (index i ↔ `periods[i]`), which keeps the file compact
and makes random `(city, metric, period)` audit sampling trivial. Missing cells are `null`.
Full schema: `data/schemas/panel.schema.json`.

**Rationale, against the four criteria:**

- **Small-multiples rendering:** the 70-city view is a grid of 70 sparklines drawn together
  — the renderer wants the whole matrix in one fetch. 70+ separate requests is the anti-goal.
- **Per-city lazy load:** rare. The whole panel gz-compresses small and caches once. If per-
  city loading is ever needed, shard to `data/panels/nbs-70city-price/<city>.json` — still a
  *panel namespace*, still one catalog entry, not 140 first-class series.
- **Catalog bloat:** 140 near-identical rows would swamp a ~100-series catalog and drown the
  8-section structure. One panel entry keeps the catalog readable.
- **Audit sampling:** the existing audit recomputes the 70-city average and the up-cities
  count from the per-city cells — it *wants* the matrix. Index-aligned arrays make sampling a
  cell a two-index lookup.

**The 4 national aggregates stay first-class series** in 楼市 — `nbs-70city-newhome-mom`,
`nbs-70city-resale-mom`, `nbs-70city-newhome-up-count`, `nbs-70city-resale-up-count` — each
`derived` from the panel (`rule:"simple_mean_of_cities"` / `count_cities_gt_zero`). The audit
independently recomputes them from the panel; this is a live accuracy check, not redundancy.

**Size:** ~184 periods × 70 cities × 2 metrics × 2 measures ≈ 52k numbers. A lean
current-values panel is well under the multi-MB legacy file; it MAY exceed the ~150 KB
per-*series* guideline (that guideline is for `series/`; the panel is the documented
exception). If a panel exceeds ~500 KB raw, shard by `metric` first, then by city tier.

---

## 6. Derived values

Any value the pipeline computes (not printed by the source) is a **derived series** or a
derived cell, and MUST carry provenance:

- Series-level: `derived: { "rule": "<name>", "inputs": ["<id>", …], "caliber": "single" }`.
- The build computes derived values **from stored source values only**, at build time, and
  never lets a derived YoY cross a break.

Known derived cases (from current data):

| Series | Rule | Inputs |
|---|---|---|
| `nbs-retail-online-goods` (当月) | `single_from_ytd` — 当月 = YTD(t) − YTD(t−1); Jan-Feb print → 当月 = YTD | published `ytd` of online goods |
| `nbs-retail-online-share` | `ratio` — online 商品零售 ÷ (社零 − 汽车) | two source series |
| `nbs-70city-*-mom` / `*-up-count` | `simple_mean_of_cities` / `count_cities_gt_zero` | `nbs-70city-price` panel |
| `mof-real-estate-tax-total` | `sum` — 契税+房产税+城镇土地使用税+土地增值税+耕地占用税 | 5 MoF tax series |

`single_from_ytd` is the classic caliber trap: the derived 当月 is only valid where a prior
YTD exists and no break/rebase intervenes; at the Jan-Feb print the 当月 equals the YTD
(2-month) value and gets `span:2`.

---

## 7. Annotations (`data/annotations.json`)

Human-authored context, kept **out** of series files so value diffs stay clean and notes are
editable without touching data. Structure keyed by series id, then period (or `_series` for
a series-wide note):

```json
{
  "nbs-cpi": {
    "_series": [ { "text_zh":"2026 年起采用 2025 定基。", "text_en":"2025 base from 2026.", "kind":"caveat", "author":"jet", "added_on":"2026-07-08" } ],
    "2026-01": [ { "text_zh":"定基切换首月，环比与历史不完全可比。", "kind":"caveat", "author":"jet", "added_on":"2026-07-08" } ]
  },
  "nbs-retail-total": {
    "2020-02": [ { "text_zh":"疫情冲击，社零当月同比 −20.5%。", "kind":"event", "author":"jet", "added_on":"2026-07-08" } ]
  }
}
```

Each note: `{ text_zh, text_en?, kind, author, added_on, link? }`;
`kind` ∈ `context` | `caveat` | `event` | `policy`. The build merges the relevant notes into
each section bundle (§10). `text_zh` follows §12.

---

## 8. Archive (`data/archive/`) — as-published truth

Immutable raw captures, the source of record for revisions and the audit. One file per
`(source, release-id)`:

`data/archive/<source>/<release-id>.json` where `<source>` ∈ `nbs-release`, `nbs-natdata`,
`pbc`, `mof`, … and `<release-id>` is a stable key (release doc id / date, e.g.
`20260616_1963949`).

```json
{ "source":"nbs-release", "release_id":"20260616_1963949",
  "url":"https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963949.html",
  "title":"2026年1—5月份社会消费品零售总额增长1.4%",
  "published_at":"2026/06/16 10:00",
  "fetched_at":"2026-06-17T02:22:10Z",
  "content_hash":"sha256:…",
  "payload": { /* parsed-but-verbatim values exactly as the page stated */ } }
```

Archive files are **append/write-once**; a re-fetch of the same release must be byte-stable
except `fetched_at`. This directory absorbs the current `*_release_archive.json` and
`property_city_history_raw.json` (see MIGRATION-MAP.md). Archives are never rewritten by the
build; they are only read.

---

## 9. Formatting & diff conventions

- **One observation / revision / cell-row per line** (compact JSON), array otherwise
  pretty-printed. A monthly refresh = a few appended lines + a few changed lines.
- **Stable key order** in every object (schema field order above). Arrays sorted:
  `observations` and `periods` ascending by period; `revisions` ascending by
  `(period, measure)`; `breaks` ascending by `effective`.
- **UTF-8, no BOM, `\n` line endings, trailing newline.** Numbers as JSON numbers (never
  quoted); no thousands separators; `null` only for genuinely-missing.
- A build must be **idempotent**: re-running on unchanged inputs produces a byte-identical
  tree (so real diffs are only real changes).

---

## 10. Site consumption — build-time bundles (avoid 100 requests)

The browser must not fetch ~100 series files. The build emits **per-section bundles** the
client loads on demand, plus a tiny landing index. All heavy math is done at build time so
the client renders without recomputing anything caliber-sensitive.

### 10.1 Files

- `site-data/index.json` — landing tiles: for each **tier-1** (headline) series, the latest
  block + a short spark. Loaded once on first paint.
- `site-data/<section-id>.json` — one bundle per section (8 total). Loaded when the user
  opens that section. Each ~tens of KB up to ~150 KB.
- `site-data/panels/<panel-id>.json` — the render-ready 70-city panel, **lazy**: fetched only
  when the 楼市 city grid opens.

### 10.2 Section bundle contents (per series)

```json
{
  "section": "consumption",
  "generated_at": "…",
  "catalog_version": "…",
  "series": [
    {
      "id": "nbs-retail-total",
      "name_zh": "社会消费品零售总额", "name_en": "…",
      "unit_zh": "亿元", "value_type": "level", "freq": "M", "tier": 1,
      "calibers": ["single","ytd"],
      "latest": { "period":"2026-05", "period_label_zh":"2026 年 5 月", "m":41090, "m_yoy":-0.6, "ytd":206031, "ytd_yoy":1.4 },
      "prev":   { "period":"2026-04", "m":40200, "m_yoy":5.1, "ytd":165021, "ytd_yoy":4.7 },
      "headline": { "caliber":"single", "direction":"down", "latest_yoy":-0.6, "delta_pp_vs_prev":-6.5, "streak":1, "period_label_zh":"2026 年 5 月" },
      "yoy_series": [ { "period":"2018-01", "yoy":9.7 }, "…", { "period":"2026-05", "yoy":-0.6 } ],
      "level_series": [ { "period":"1985-01", "m":338.9 }, "…" ],
      "spark": [ /* downsampled last N points for the tile */ ],
      "breaks": [],
      "annotations": [ { "period":"2020-02", "text_zh":"…", "kind":"event" } ],
      "flags_latest": []
    }
  ]
}
```

**Pre-computation done at build (the client does none of it):**

- `latest` / `prev` blocks for the stat tile and delta arrow — resolved to the correct
  comparable prior period (respects Jan-Feb: prev of `2026-02[jan_feb]` is `2025-02[jan_feb]`).
- `yoy_series` — extracted from stored `m_yoy`/`ytd_yoy` **verbatim**; a `null` is inserted at
  any `no_yoy_across` break so the line breaks and no cross-seam growth is implied.
- `level_series` / `spark` — for the default chart and tile sparkline, downsampled as needed.
- `headline` — **structured inputs** for the takeaway sentence (direction, latest YoY,
  pp-change vs prev, streak length, period label), not prose. The client (or an editor)
  templates the sentence; the numbers are authoritative and pre-labelled.
- `period_label_zh` — human label following §12 (`2026 年 1—5 月` for a cumulative print;
  `2026 年 5 月` for a single month; note the Jan-Feb / cumulative forms differ).
- `annotations` — the merged notes for that series (§7).
- `flags_latest` — e.g. `jan_feb`, `derived`, `break_recent`, surfaced as a tooltip badge.
- `revisions` (optional) — include the series' revision log in the bundle **only if** the UI
  keeps the current published/latest version toggle; the client walks it backward to
  reconstruct the as-published vintage. Omit to keep bundles smaller if the toggle is dropped
  (MIGRATION-MAP §10.5).

The panel bundle mirrors the panel file but pre-computes the national aggregates and
per-city latest cells for the grid.

### 10.3 Deploy topology (OPEN — needs owner decision)

Two options; pick one:
- **(A) CI-built Pages:** `main` holds only `data/` + `pipeline/` + `docs/`; GitHub Actions
  runs the build and publishes `site-data/` to the Pages artifact. Cleanest history (no
  generated diffs), but Pages requires the Actions build to succeed.
- **(B) Committed bundles:** commit `site-data/` too, so Pages serves straight from the repo
  with no build step. Simpler serving; noisier diffs. Mitigation: reviewers read `data/`
  diffs; `site-data/` diffs are derivative and can be collapsed in review.

Recommendation: **(A)** for a private-repo → public-Pages setup, matching the existing
`.wrangler`/Pages intent. Flagged for the orchestrator.

---

## 11. Pipeline stages & exchange contracts

Six stages under `pipeline/`, each a pure function of its input + config. The three data
contracts between stages are frozen here.

```
fetch → parse → normalize → validate → build → (audit)
```

- **fetch/** — per-source fetchers (evolve `tools/fetch_*.py`). Writes immutable
  `data/archive/<source>/<release-id>.json`. Emits a `RawCapture` handoff.
- **parse/** — turn a RawCapture's payload (HTML tables / natdata JSON) into flat source-
  vocabulary rows. Emits `ParsedRelease`.
- **normalize/** — map source rows to canonical observations via `config/field_map.yaml`;
  assign `id`, `period`, `span`, `caliber`, unit conversions (e.g. 万亿→亿元). Emits
  `NormalizedBatch`.
- **validate/** — **ingest gate (accuracy gate #1)**: schema-validate, caliber sanity, span
  checks, YTD-diff sanity, no-YoY-across-break, revision-diff preview for human review.
- **build/** — merge NormalizedBatch into `data/series/*` + `data/panels/*` (append
  revisions, update observations), regenerate `data/catalog.json`, emit `site-data/*`.
- **audit/** — **independent gate (accuracy gate #2)**: re-verify built values against
  `data/archive/` (evolve `tools/audit_official_data.py`): release-text spot checks, YTD-diff
  recompute, 70-city average/up-count recompute, per-city raw-table recheck. Read-only.

### 11.1 Stage exchange contracts

**RawCapture** (fetch → parse; also what lands in `data/archive/`):
```json
{ "source":"nbs-release", "release_id":"…", "url":"…", "title":"…",
  "published_at":"…", "fetched_at":"…", "content_hash":"sha256:…", "payload": { … } }
```

**ParsedRelease** (parse → normalize) — still in source vocabulary, no series ids yet:
```json
{ "source":"nbs-release", "release_id":"…", "url":"…", "published_at":"…",
  "period_hint":"2026-05",
  "rows": [ { "source_field":"社会消费品零售总额", "raw_label":"当月", "value":41090,
              "unit_raw":"亿元", "caliber_hint":"single", "period":"2026-05", "city":null } ] }
```

**NormalizedBatch** (normalize → validate → build) — canonical, id-assigned, one per
(series, period):
```json
{ "release_id":"…",
  "series": [ { "series_id":"nbs-retail-total",
                "obs": { "period":"2026-05", "span":1, "m":41090, "m_yoy":-0.6, "ytd":206031, "ytd_yoy":1.4, "src":"rel:20260616" },
                "provenance": { "release_id":"…", "source_field":"社会消费品零售总额" } } ],
  "panels": [ { "panel_id":"nbs-70city-price", "cells":[ { "city":"北京","metric":"new_home","period":"2026-05","m":-0.2,"m_yoy":-2.1 } ] } ] }
```

`field_map.yaml` (`source_field` → `series_id` + `measure` + `caliber`) is the human-owned
mapping table and the only place a new source field is wired to a series. Adding a series =
add a catalog entry + a field_map line + (optionally) an annotation.

### 11.2 GitHub Actions polling

`config/release_calendar.yaml` lists per-source release windows (NBS retail ~mid-month 10:00,
income quarterly, PBC/MoF cadence). The poller runs `fetch → … → validate` inside each window
and opens a PR with the resulting `data/` diff for human review; `build` + deploy run on
merge. Values never reach the site without passing gate #1, and the audit (gate #2) runs on a
schedule against the built tree.

---

## 12. Chinese display-string typesetting (applies to every `*_zh` field)

All Chinese strings written into data (`name_zh`, `unit_zh`, notes, labels) MUST follow:

1. **Quotes:** full-width curly only — “ ” outer, ‘ ’ inner. Never `「」`/`『』` or ASCII
   `"`/`'` (ASCII allowed only inside code, URLs, English).
2. **Numerals:** Arabic (`16 个城市`, not `十六个城市`; `60-70B`). Keep Chinese numerals only
   for very short spoken small counts (两个、三五个) and idioms.
3. **Pangu spacing:** one half-width space between CJK and adjacent Latin/digits
   (`用 GPT-4 做`, `覆盖 80% 用户`, `2026 年 5 月`). **No** space between a number and a unit
   glyph/percent (`80%`, `$50`), or next to full-width punctuation.

Example period labels: single month `2026 年 5 月`; cumulative `2026 年 1—5 月`; Jan-Feb
`2026 年 1—2 月`; quarter `2026 年一季度`. IDs, field names, and enum values remain English
regardless.
