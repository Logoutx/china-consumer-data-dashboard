# MIGRATION MAP — current `data.js` → new series files

Outline for the mechanical migration script (written by another agent). It maps every
top-level key and every series in the current `data.js` (and the legacy archive JSONs) to a
destination under the new contract (`docs/DATA-CONTRACT.md`). This is a *map*, not the
script; it fixes destinations, id names, field remaps, and the checks the script must pass.

**Read-only inputs** (never modified): `data.js`, the `*_release_archive.json`,
`property_city_history*.json`, `retail_dashboard_data.xlsx`, `tools/*`.

---

## 1. Current shape (verified)

`window.__DASHBOARD_DATA__` has **3 top-level keys**: `retail`, `income`, `property`.
Each is `{ …metadata, series:{id→meta}, records:[…] }`, where the actual time series lives in
`records[].metrics[series_key]` (a "wide records" layout). The new model **pivots** this to
one file per series (`records[].metrics[k]` → `data/series/<id>.json` observations).

Per-metric value object (current) → new observation measures:

| Current field | New field | Notes |
|---|---|---|
| `month_value` | `m` (usually) | 当月/单期 headline. **Remapped to `ytd` for cumulative-caliber series** — see §4. |
| `month_yoy` | `m_yoy` (usually) | Published 同比, verbatim. → `ytd_yoy` for cumulative series. |
| `ytd_value` | `ytd` | 累计 level. |
| `ytd_yoy` | `ytd_yoy` | Published cumulative 同比, verbatim. |
| `real_yoy` | `real_yoy` | Income only. |
| `latest_*` | **the current observation value** | `versioning_note` confirms `month_*` is an alias of `latest_*`; use `latest_*` as the current value. |
| `published_*` | **seed archive + revision log** | If `published_* != latest_*`, (a) write the `published_*` value into the reconstructed `data/archive/` capture for that release, and (b) append a `revisions` entry `{period, measure, old:published, new:latest, revised_on:null, source:"legacy-migration"}`. If equal, drop (no revision). |

`published_*`/`latest_*` twins are **not** carried into observations — they collapse to
"current value + revision log + archive" (DATA-CONTRACT §4). Legacy revision dates are
unknown → `revised_on:null`.

---

## 2. Top-level keys → destinations

| Current key | Destination |
|---|---|
| `retail`, `income`, `property` (containers) | dissolved; their `records[].metrics` pivot into `data/series/*` and `data/panels/*` |
| `*.series[k]` (`{name, group, level, unit?}`) | catalog `entry` per series: `name`→`name_zh`, `group`→`group`, `level`→`tier` (see §6 tiering), `unit`→`unit_zh` |
| `*.records[].metrics[k]` | `data/series/<id>.json` `observations[]` |
| `record.{year,month,period,quarter,title_period,period_label}` | observation `period` (+ `span`/`flags` for Jan-Feb, §3) |
| `record.{url,title,published_at,source}` | reconstructed `data/archive/<source>/<release-id>.json` + observation `src` pointer |
| `retail.coverage_note`, `property.notes`, `income.coverage_note` | per-series `coverage_note_zh`, or `data/annotations.json` where period-specific |
| `retail.versioning_note` | **not migrated** — superseded by the revisions model; behaviour documented in DATA-CONTRACT §4 |
| `*.sources` | catalog `source` + reconstructed archive provenance |
| `property.preferred` | tier-1 / ordering hint in catalog |
| `property.cities` (70 names) | panel `dimensions.city` |
| `property.records[].cities` + `property_city_history.json` | `data/panels/nbs-70city-price.json` (§5) |

---

## 3. Jan-Feb & frequency handling (applies during pivot)

**Jan-Feb is encoded inconsistently in the current data — the rebuild must impose one rule.**
Verified reality:
- Modern Feb records (2018-02 … 2026-02) exist but carry **only** `online_goods` in
  `metrics`; `retail_total` and every other retail series have **no Jan-Feb observation at
  all** (a genuine gap — see `docs/_inventory/current-data-shapes.md`, "flagship_series_internal_nulls").
- `online_goods` stores the 1—2月 combined in `month_value` (e.g. 2026-02 = 20812) with
  `ytd_value: null` — the derived-single convention.
- Some older years (e.g. 2012, 2013) have **no Jan and no Feb record** at all (different
  encoding again).

**Canonical target rule** (apply uniformly):
- Jan-Feb combined → observation `period:"YYYY-02"`, `span:2`, `flags:["jan_feb"]`.
- The 1—2月 combined total is both single-period and cumulative → set `m == ytd` and
  `m_yoy == ytd_yoy`. For `online_goods`, copy `month_value`→`m` **and** `ytd` (both equal),
  clear the `null` ytd.
- **Fill the `retail_total` (and siblings') Jan-Feb gap** from the 1—2月 release capture in
  `data/archive/` (the values exist in NBS releases; the current `data.js` just dropped them).
  Where a release value is unavailable, leave the row absent rather than fabricate.
- Never synthesize a `YYYY-01` observation.

**Frequency:**
- Retail: `freq:"M"`.
- Income: source encodes quarters as `period:"YYYY-MM"` (quarter-end month) + `quarter` +
  `period_label`. Map `一季度/上半年/前三季度`→ `YYYY-Q1/Q2/Q3` (cumulative). **`period_label:"全年"`
  / `historical_supplement:true` rows (2013-2016) are annual-only** → `freq:"A"`,
  `period:"YYYY"` (per-obs freq override; the series default stays `Q`). Do not mislabel 全年
  as `Q4` even though its value equals the Q4 cumulative.
- Property loans/fiscal quarters → `freq:"Q"`.
- Pre-1990s retail records carry only `month_value` (no YoY) → observation has `m` only.

---

## 4. Caliber remaps (the important non-obvious ones)

The current data stuffs several cumulative series into `month_value`. The migration must
re-file them to the correct caliber:

| Group | Current storage | New caliber | Reason |
|---|---|---|---|
| **Income** (all 21) | value in `month_value`/`month_yoy` | `ytd`/`ytd_yoy`, `calibers:["ytd"]`, `freq:"Q"` | 一季度/上半年 disposable income is cumulative-to-quarter, not a single month. |
| **MoF fiscal** (land-transfer, all property taxes) | `month_value` | `ytd`/`ytd_yoy`, `calibers:["ytd"]` | `property.notes`: “财政部累计报告期数据，非单月值”. |
| **PBC loan balances** (real-estate / mortgage / development) | `month_value` | `m`, `calibers:["single"]`, `value_type:"level"`, `freq:"Q"` | Period-end **stock** (余额); `m` = the stock at that quarter-end. Unit already 亿元 (converted from 万亿 upstream — keep). |
| **Property price** (70-city + per-city) | `month_value`=环比, `month_yoy`=同比 | `m`/`m_yoy`, `value_type:"mom_pct"`, `calibers:["single"]` | Stored as index−100; `m` is the 环比 %, `m_yoy` the 同比 %. |
| **Retail** (levels) | `month_value`,`ytd_value` | `m`,`ytd`, `calibers:["single","ytd"]` | Straight remap. |

---

## 5. 70-city panel migration

Source: `property.records[].cities.<city>.{new_home_price,resale_home_price}` (national release
window) **merged with** `property_city_history.json` (2011-01…2026-04 national-data history).
Destination: **one** `data/panels/nbs-70city-price.json` (DATA-CONTRACT §5), NOT 140 series.

- `dimensions.city` = the 70 names (`property.cities`); `dimensions.metric` = `["new_home","resale_home"]`; `measures` = `["m","m_yoy"]`.
- `periods` = union of history + release periods, ascending.
- `cells[city][metric][measure]` = period-aligned array; missing = `null`.
- Cell revisions where history vs release disagree → panel `revisions[]`.
- The 4 national aggregates become **derived series** recomputed from the panel:

| Current key | New id | derived rule |
|---|---|---|
| `new_home_70_price` | `nbs-70city-newhome-mom` | `simple_mean_of_cities` (new_home, m) |
| `resale_home_70_price` | `nbs-70city-resale-mom` | `simple_mean_of_cities` (resale_home, m) |
| `new_home_up_cities` | `nbs-70city-newhome-up-count` | `count_cities_gt_zero` (new_home, m) |
| `resale_home_up_cities` | `nbs-70city-resale-up-count` | `count_cities_gt_zero` (resale_home, m) |

---

## 6. Series id map

Tier from current `level`: level 1 → tier 1; level 2-4 → tier 2; level ≥5 → tier 3
(headline / secondary / detail). Adjust by judgment where a level-2 is really a headline.

### 6a. `retail.*` → section **consumption** (agency nbs)

| Current key | New id | tier |
|---|---|---|
| `retail_total` | `nbs-retail-total` | 1 |
| `retail_ex_auto` | `nbs-retail-ex-auto` | 2 |
| `auto_total` | `nbs-retail-auto` | 2 |
| `urban` | `nbs-retail-urban` | 2 |
| `rural` | `nbs-retail-rural` | 2 |
| `online_goods` | `nbs-retail-online-goods` | 2 · **derived** `single_from_ytd` |
| `online_ex_auto_share` | `nbs-retail-online-share` | 2 · **derived** `ratio`, `value_type:"ratio"` |
| `catering` | `nbs-retail-catering` | 2 |
| `goods` | `nbs-retail-goods` | 2 |
| `above_quota_total` | `nbs-retail-above-quota` | 3 |
| `above_quota_catering` | `nbs-retail-above-quota-catering` | 3 |
| `above_quota_goods` | `nbs-retail-above-quota-goods` | 3 |
| `grain_food` | `nbs-retail-cat-grain-food` | 3 |
| `beverage` | `nbs-retail-cat-beverage` | 3 |
| `tobacco_alcohol` | `nbs-retail-cat-tobacco-alcohol` | 3 |
| `garments` | `nbs-retail-cat-garments` | 3 |
| `cosmetics` | `nbs-retail-cat-cosmetics` | 3 |
| `gold_jewelry` | `nbs-retail-cat-gold-jewelry` | 3 |
| `daily_goods` | `nbs-retail-cat-daily-goods` | 3 |
| `sports_entertainment` | `nbs-retail-cat-sports-entertainment` | 3 |
| `books_magazines` | `nbs-retail-cat-books-magazines` | 3 |
| `household_appliances` | `nbs-retail-cat-household-appliances` | 3 |
| `medicine` | `nbs-retail-cat-medicine` | 3 |
| `cultural_office` | `nbs-retail-cat-cultural-office` | 3 |
| `furniture` | `nbs-retail-cat-furniture` | 3 |
| `communication` | `nbs-retail-cat-communication` | 3 |
| `petroleum` | `nbs-retail-cat-petroleum` | 3 |
| `building_materials` | `nbs-retail-cat-building-materials` | 3 |

`cat-*` are the 限额以上单位 by-commodity breakdown — note that in `name_zh`. The
online-retail indicator replacement in 2026 (per task) lands as a **new id** + break on
`nbs-retail-online-goods` when it occurs (§8, not present in current data).

### 6b. `income.*` → section **income-confidence** (agency nbs; all `freq:Q`, `calibers:["ytd"]`, `unit:元`, `value_type:level`)

| Current key | New id | tier |
|---|---|---|
| `income_disposable` | `nbs-income-disposable` | 1 · has `real_yoy` |
| `income_disposable_urban` | `nbs-income-disposable-urban` | 2 · `real_yoy` |
| `income_disposable_rural` | `nbs-income-disposable-rural` | 2 · `real_yoy` |
| `income_wage` | `nbs-income-wage` | 3 |
| `income_business` | `nbs-income-business` | 3 |
| `income_property` | `nbs-income-property` | 3 |
| `income_transfer` | `nbs-income-transfer` | 3 |
| `income_median` | `nbs-income-median` | 2 |
| `income_median_urban` | `nbs-income-median-urban` | 3 |
| `income_median_rural` | `nbs-income-median-rural` | 3 |
| `consumption_expenditure` | `nbs-consumption-expenditure` | 1 |
| `consumption_expenditure_urban` | `nbs-consumption-expenditure-urban` | 2 |
| `consumption_expenditure_rural` | `nbs-consumption-expenditure-rural` | 2 |
| `consumption_food_tobacco_alcohol` | `nbs-consumption-food-tobacco-alcohol` | 3 |
| `consumption_clothing` | `nbs-consumption-clothing` | 3 |
| `consumption_housing` | `nbs-consumption-housing` | 3 |
| `consumption_household_services` | `nbs-consumption-household-services` | 3 |
| `consumption_transport_communication` | `nbs-consumption-transport-communication` | 3 |
| `consumption_education_culture` | `nbs-consumption-education-culture` | 3 |
| `consumption_healthcare` | `nbs-consumption-healthcare` | 3 |
| `consumption_other` | `nbs-consumption-other` | 3 |

Judgment call: `consumption_expenditure_*` come from the 居民收入与支出 household survey, so
they sit in **income-confidence** (not the retail-based 消费 section). Flag if the owner wants
per-capita spending grouped under 消费 instead.

### 6c. `property.*` → section **property**

| Current key | New id | section | notes |
|---|---|---|---|
| `new_home_70_price` | `nbs-70city-newhome-mom` | property | derived from panel |
| `resale_home_70_price` | `nbs-70city-resale-mom` | property | derived from panel |
| `new_home_up_cities` | `nbs-70city-newhome-up-count` | property | derived from panel, `value_type:count` |
| `resale_home_up_cities` | `nbs-70city-resale-up-count` | property | derived from panel, `value_type:count` |
| (per-city cells) | panel `nbs-70city-price` | property | §5 |
| `real_estate_loan_balance` | `pbc-real-estate-loan-balance` | property* | stock, `freq:Q` |
| `mortgage_balance` | `pbc-mortgage-balance` | property* | stock, `freq:Q` |
| `property_development_loan_balance` | `pbc-property-development-loan-balance` | property* | stock, `freq:Q` |
| `land_transfer_revenue` | `mof-land-transfer-revenue` | property | cumulative, `calibers:["ytd"]` |
| `real_estate_tax_total` | `mof-real-estate-tax-total` | property | **derived** `sum` of the 5 taxes below |
| `deed_tax` | `mof-deed-tax` | property | cumulative |
| `property_tax` | `mof-property-tax` | property | cumulative |
| `urban_land_use_tax` | `mof-urban-land-use-tax` | property | cumulative |
| `land_vat` | `mof-land-vat` | property | cumulative |
| `farmland_occupation_tax` | `mof-farmland-occupation-tax` | property | cumulative |

\* The three PBC property-loan balances could instead live in **money-credit**. Left in
**property** to match the current dashboard framing; flagged as an owner decision (§9).

---

## 7. Legacy files → `data/archive/` and inputs

| Current file | Destination |
|---|---|
| `retail_release_archive.json` (mirror w/ records) | split by release → `data/archive/nbs-release/<release-id>.json`; also the source for reconstructing `published_*` captures |
| `income_release_archive.json` | `data/archive/nbs-release/<release-id>.json` (income releases) |
| `property_release_archive.json` (7.2M) | `data/archive/nbs-release/<release-id>.json` (price releases) |
| `property_city_history.json` (6.8M) | current values → panel `data/panels/nbs-70city-price.json` |
| `property_city_history_raw.json` (18M) | `data/archive/nbs-natdata/<capture-id>.json` (raw national-data table captures) |
| `retail_dashboard_data.xlsx` | keep as legacy input; not part of the data model |
| `_cache/` | stays as the fetch/audit cache (pipeline `fetch/` + `audit/` inputs) |
| `audit_reports/` | superseded by `pipeline/audit/` output location; keep historical reports |

Because the current archives are **mirror snapshots** (verified: no duplicate periods), the
true as-published values for legacy periods are reconstructed from each record's inline
`published_*` fields keyed by `record.url`/`published_at`. That is how `published_*` is
preserved rather than lost.

---

## 8. New sections — green-field (no current data)

These 5 sections have **no source in the current `data.js`**; the new pipeline creates them.
Listed so the catalog scaffold and `field_map.yaml` are complete. Break metadata (§2.1 of the
contract) attaches here.

| Section | Example series (ids) | Break notes |
|---|---|---|
| **prices** 物价 | `nbs-cpi`, `nbs-cpi-core`, `nbs-cpi-food`, `nbs-ppi` | CPI/PPI **rebase 2026-01** → same id + `break{rebase, no_yoy_across, yoy_valid_from:"2026-01"}` |
| **employment** 就业 | `nbs-urban-unemp`, `nbs-urban-unemp-youth-1624`, `nbs-urban-unemp-youth-1624-exstudent`, `nbs-urban-unemp-2534`, `nbs-urban-unemp-3159` | youth-unemp **new id** post-2023 methodology; old frozen `end:"2023-07"`, linked |
| **money-credit** 钱与信贷 | `pbc-m1`, `pbc-m2`, `pbc-aggregate-financing` (社融), `pbc-new-loans`, `pbc-loan-balance` | M1 **redefinition 2025-01** → same id + `break{redefinition, yoy_valid_from:"2025-01"}`, pre-2024-01 obs `flags:["old_caliber"]` |
| **macro** 宏观大盘 | `nbs-gdp`, `nbs-fai` (**`calibers:["ytd"]` only** — FAI publishes YTD), `nbs-industrial-va`, `cflp-pmi-mfg`, `cflp-pmi-nonmfg`, `customs-exports`, `customs-imports` | FAI is the canonical YTD-only example |
| **high-frequency** 高频脉搏 | weekly/daily indicators (define per source) | frequency may be `W`/`D` — extend `freq` enum when added |

`freq` currently allows `M|Q|A`; extend to `W|D` when high-frequency series are wired
(schema change, versioned).

---

## 8b. Verified data oddities to handle

Cross-reference `docs/_inventory/current-data-shapes.md` (machine-generated inventory).
Confirmed cases the migration must not trip on:

1. **Empty declared series:** `books_magazines` has metadata but **0 observations**. Emit a
   catalog entry with an empty `observations:[]` (reserves the id `nbs-retail-cat-books-magazines`)
   rather than dropping it or assuming ≥1 obs.
2. **Retail carries no `unit`** (source `unit` = "--"). Assign `unit_zh:"亿元"` to all retail
   level series; `nbs-retail-online-share` is `unit_zh:"%"`, `value_type:"ratio"`.
3. **`real_yoy` exists on exactly 6 income series** — `income_disposable(_urban/_rural)`,
   `consumption_expenditure(_urban/_rural)`. Migrate to `real_yoy`; other income series omit it.
4. **Record schema evolution:** retail records have 3 key-signatures, income 2. Treat
   provenance fields (`release_published_at`, `release_title`, `title_period`, `coverage_note`,
   `historical_supplement`, `quarter`) as **optional** per record.
5. **Property city coverage gaps:** 3 records have <70 cities (`2012-12`=1, `2019-05`=65,
   `2022-11`=62) and ~92 null price cells. In the panel these become `null` cells, not zeros.
6. **`property_release_archive.json` / `income_release_archive.json`** differ from `data.js`
   only by number-formatting artifacts (int-vs-float, signed-zero); treat as the same content
   when reconstructing archives — do not log spurious revisions from `60` vs `60.0` or `-0.0`.
7. **Version toggle is load-bearing in the current UI:** `app.js` reads
   `published_month_*` vs `latest_month_*` for a {published, latest} switch. If the rebuild
   keeps that feature, the section **bundle must carry the `revisions` log** (small) so the
   client can reconstruct the as-published vintage; otherwise the toggle silently breaks.
   Flagged in DATA-CONTRACT §10 and as open question §10.5.

## 9. Migration invariants (the script must satisfy)

1. **Value fidelity:** for every (series, period, measure), the migrated current value equals
   the current `latest_*` (== `month_*`) in `data.js`. No YoY recomputed.
2. **Revision completeness:** every `published_* != latest_*` produces exactly one seed
   revision entry and one reconstructed archive value; no silent drops.
3. **Caliber correctness:** income + MoF series land in `ytd`; PBC stocks in `m`; property
   price in `mom_pct` `m` (§4). A validator asserts no cumulative series has a bare `m`.
4. **Jan-Feb:** every `YYYY-02` record with no `YYYY-01` sibling gets `span:2` + `flags:["jan_feb"]` and `m==ytd`.
5. **Schema valid:** every emitted file validates against its schema; the catalog's
   `start`/`latest`/`file`/`panel` agree with the actual files.
6. **Round-trip check:** rebuilding `site-data/` from migrated `data/` reproduces the current
   dashboard's headline numbers (spot-check retail-total, income-disposable, 70-city means).
7. **Idempotent:** re-running the migration is byte-stable.

---

## 10. Open questions for the owner

1. **PBC property loans** → property or money-credit? (§6c) Default: property.
2. **Per-capita consumption expenditure** → income-confidence or 消费? (§6b) Default: income-confidence.
3. **`site-data/` deploy topology** → CI-built (recommended) vs committed bundles (DATA-CONTRACT §10.3).
4. **Tiering** of level-2 retail sub-series (auto/online/catering) — some may deserve tier 1 on the landing view.
5. **Keep the published/latest version toggle?** If yes, bundles carry the `revisions` log so
   the client can reconstruct the as-published vintage (DATA-CONTRACT §10.2 note). If the
   feature is dropped, bundles can omit revisions and stay smaller.
