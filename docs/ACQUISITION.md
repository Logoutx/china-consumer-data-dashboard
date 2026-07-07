# Data Acquisition Strategy — China Official Statistics Dashboard

_Authored 2026-07-08. Live checks in this doc were run from an egress that routes
through a **China IP** (confirmed: every `.cn` host reachable, `fred.stlouisfed.org`
returned `000`). So "verified-live" here means **reachable from a CN vantage**. A
separate agent is testing reachability from the intended **US CI runner**, which will
have the inverse profile — FRED/DBnomics/OECD/IMF trivial, the `.cn` primaries the open
question. Read every confidence tag with that split in mind._

## The three reachability regimes (read first)

| Endpoint | From CN egress (here) | Role |
|---|---|---|
| `www.stats.gov.cn/sj/zxfb/` (HTML listing + articles) | ✅ 200, clean | Ongoing discovery + parse |
| `api.so-gov.cn/query/s` (gov-wide search JSON) | ✅ 200 | Discovery of deep article URLs by title+date |
| `data.stats.gov.cn/dg/website/publicrelease/web/external/*` (DG national-data JSON) | ✅ 200, success | **Backfill engine, monthly to 1985** |
| `data.stats.gov.cn/easyquery.htm` | ❌ 403 WZWS | Dead — do not use |
| `wzdig.pbc.gov.cn/search/pcRender` + `www.pbc.gov.cn` | ✅ 200 | PBoC discovery + parse |
| `english.customs.gov.cn` | ✅ 200 but **JS shell, no data** | Weak; use fallback |
| `fred.stlouisfed.org` / DBnomics / OECD / IMF | ❌ 000 from here | Cross-check — will be easy from US runner |

**Headline engineering finding:** the WAF block is specific to the *old* `easyquery.htm`
API. The *new* DG "publicrelease" API (`/dg/website/publicrelease/web/external/…`) is a
different, currently-open endpoint. It returned retail `202605MM = 41090.0` (exactly the
figure in the May release page) and deep history `198501MM = 338.9`, `199001MM = 657.6`
in a live test today. It is the backfill keystone for every NBS series — **if** it is
also reachable from the US runner (the one thing this vantage cannot tell you).

---

## Summary table

Confidence: ✅ verified-live (CN egress) · 🟡 probable · ⚪ unverified. Depth = realistic
monthly history achievable.

| # | Group | Ongoing source | Backfill source (primary) | Depth | Conf. |
|---|---|---|---|---|---|
| 1 | CPI / PPI | `/sj/zxfb/` headline article, ~9–10th | DG API (CPI/PPI index) + FRED/IMF x-check | CPI 1993→, PPI 1996→ | ✅ engine / 🟡 GUIDs |
| 2 | Activity batch (retail, IVA, FAI, unemp, 70-city, resi sales) | `/sj/zxfb/` components, ~15–18th | DG API per series | retail 1985→, IVA 1994→, unemp **2018→** | ✅ retail / 🟡 rest |
| 3 | GDP (+consumption contrib.) & income/expenditure | `/sj/zxfb/` quarterly | DG API + existing income archive (2013→) | GDP 1992→ (Q), income 2013→ (Q) | ✅ income / 🟡 GDP contrib |
| 4 | PMI (mfg + non-mfg, NBS/CFLP) | `/sj/zxfb/` last day of month | DG API | mfg 2005→, non-mfg 2007→ | ✅ engine |
| 5 | Consumer confidence (NBS) | **DG API poll only** (no press release), lagged | DG API | 1998→ (monthly) | 🟡 |
| 6 | PBoC M1/M2/社融/loans/deposits | `wzdig` search → report article, ~10–15th | `wzdig` back-catalog + DBnomics/FRED | M2 1998→, 社融存量 2015→ | ✅ |
| 7 | Customs exports/imports | GACC Chinese (412, browser) **or** DBnomics `CN` | DBnomics/IMF DOTS/FRED (USD, monthly) | 1990s→ | 🟡 |
| 8a | State Post parcels | `spb.gov.cn` listing, monthly ~mid-month | Listing pagination | ~2015→ | ✅ |
| 8b | 文旅部 holiday tourism | `mct.gov.cn/whzx/whyw`, **per-holiday** | Listing pagination | ~2015→ (holidays) | 🟡 |
| 8c | Box office | 灯塔/猫眼 realtime; no official monthly | Third-party scrape / manual | weak | ⚪ |
| 8d | Autos (CPCA/CAAM) | `cpcaauto.com` / `caam.org.cn/tjsj`, monthly | Listing / articles | ~2010→ | 🟡 |
| 8e | Aviation (CAAC) + rail | `caac.gov.cn/…/TJSJ` monthly; rail via MOT | Listing pagination | CAAC ~2010→, rail patchy | 🟡 / ⚪ |

---

## Reusable engines (all three already exist in `tools/` — reuse, don't rebuild)

- **Engine A — NBS listing discovery.** `GET https://www.stats.gov.cn/sj/zxfb/` then
  `index_1.html … index_N.html`. Each page = 45 titled `<a>` links ≈ 3 weeks. Filter
  `href` to `/sj/zxfb/`, match `title` on a **stem** (see per-group regex), reject
  `解读/走势图/日程/答记者问`. URL shape: `/sj/zxfb/YYYYMM/tYYYYMMDD_<7digits>.html`.
  This tree only retains ~2–3 years; older releases migrate to `/xxgk/sjfb/zxfb2020/`
  and `/zwfwck/sjfb/`, which is why history needs Engine C or the DG API, not deep
  listing pagination.
- **Engine B — DG national-data API (backfill).** `POST …/getEsDataByIndicatorIdAndDa`
  with `{cid, id, da:"000000000000", rootId:"3913ce1309d04eb1bdf7d7b622b1d07c",
  dts:["198501MM",…]}`, `Content-Type: application/json`, `Referer:
  https://data.stats.gov.cn/dg/website/page.html`. Latest period via
  `…/new/queryDtByCid?cid=…&rootId=fc982599aa684be7969d7b90b1bd0e84`. Returns
  `{success:true,data:[{dt:"202605MM",v:41090.0}]}`. **The only per-series manual input
  is the `cid`+indicator-`id` GUID pair** (see the one-time seed section). Existing
  `tools/fetch_nbs_national_data.py` already has the retail GUID map and the chunking
  (36 date-codes/request), YTD→month differencing, and YoY derivation — extend its
  `OFFICIAL_SERIES` dict, don't rewrite it.
- **Engine C — gov-wide search.** `POST https://api.so-gov.cn/query/s`, form body
  `siteCode=bm36000002&qt=<phrase>&page&pageSize&sort=dateDesc&adv=1` (+`timeOption=2&
  startDateStr&endDateStr` to bracket a year — **required**, because unbracketed
  `dateDesc` is swamped by Weibo/news noise). `resultDocs[].data.{titleO,url,docDate}`.
  Filter `url` to the host you want. For PBoC use `wzdig.pbc.gov.cn/search/pcRender?
  pageId=c177a85bd02b4114bebebd210809f691&q=<exact title>`.

---

## Group 1 — CPI & PPI

**Ongoing.** Both publish together ~9th–10th, 09:30 (May data: CPI 2026/06/10
`t20260610_1963923`, PPI `t20260610_1963922`). Titles are dynamic headlines, e.g.
`2026年5月份居民消费价格同比上涨1.2%` / `…工业生产者出厂价格同比上涨3.9% 环比上涨0.5%`.
Discovery regex (stem, not "变动情况"):
`^\d{4}年\d{1,2}月份居民消费价格` and `^\d{4}年\d{1,2}月份工业生产者`.
_Parse:_ the headline CPI article **contains the full 48-row table** (cols: 环比涨跌幅% /
同比涨跌幅% / 1—N月同比涨跌幅%) covering 居民消费价格 → 城市/农村 → the 8 categories
(食品烟酒, 衣着, 居住, 生活用品及服务, 交通通信, 教育文化娱乐, 医疗保健, 其他) and
sub-items. **Core CPI (不包括食品和能源) and the food/services split live in the prose,
not the table** — regex `核心CPI…同比上涨([\d.]+)%`, `食品价格.*?(上涨|下降)([\d.]+)%`,
`服务价格.*?(上涨|下降)([\d.]+)%`. PPI: headline + 生产资料/生活资料 split in prose,
industry detail in a table. Fixture: `pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html`.
**Window:** monthly, day 9–11.

**Backfill.** DG API. Harvest CPI/PPI GUIDs once; NBS monthly CPI YoY runs to 1993 (and
a 1987 splice), PPI to ~1996. Cross-check FRED `CHNCPIALLMINMEI` / IMF from the US
runner. Depth **1993→ CPI, 1996→ PPI**, monthly. Confidence: ✅ engine, 🟡 until GUIDs
harvested.

## Group 2 — NBS monthly activity batch

**Ongoing.** One mid-month batch (~15th–18th; May data 2026/06/16). Each indicator is a
**separate** `/sj/zxfb/` article with consecutive IDs the same day: 社零 `…_1963949`,
FAI `…_1963951`, 工业增加值 `…_1963953`, 70-city home prices `…_1963946`. The umbrella
`X月份国民经济运行情况` is posted on the press-conference tree (`/sj/xwfbh/`), not
`/sj/zxfb/`. Discovery stems: `社会消费品零售总额`, `规模以上工业增加值`,
`固定资产投资`, `70个大中城市`, `全国房地产开发投资和销售情况`.
_Parse specifics:_
- **Retail** (hard case — see Sketch A): main table row = `label · 绝对量(月)亿元 ·
  同比%(月) · 绝对量(累计) · 同比%(累计)`; `-` where only YTD exists (网上); section
  headers 按经营地分/按消费类型分/按商品类别分 have no numbers. Second table = 18
  限额以上 commodity categories (粮油食品…金银珠宝…汽车类). Reuse `SERIES_ALIASES`
  from `tools/fetch_retail_archive.py`.
- **Industrial value-added**: headline YoY in prose; sub-sector & product tables.
- **FAI**: reported **YTD cumulative only** (no single-month absolute) — 累计 value +
  YoY; private/manufacturing/infra/real-estate splits in prose.
- **Surveyed urban unemployment**: not a standalone article — it is in the umbrella
  presser prose + a short 分年龄组 release. `全国城镇调查失业率为([\d.]+)%`; **youth**
  `不包含在校生的16—24岁劳动力失业率为([\d.]+)%` (25–29 and 30–59 alongside). Note the
  16–24 series was **suspended Jul–Dec 2023** and **resumed Jan 2024 on a new
  (student-excluded) caliber** — a hard series break to annotate, not smooth over.
- **Residential sales**: `全国房地产开发投资和销售情况` — 开发投资, 商品房销售面积,
  商品房销售额 (YTD + YoY, prose + table).

**Backfill.** DG API per series (retail GUIDs already in repo — 1985 proven live). IVA
~1994, FAI YTD ~1995, 70-city 2005 (repo already holds the raw city history in
`property_city_history_raw.json`). **Surveyed unemployment only exists from 2018** (and
monthly only from 2018); pre-2018 there is no comparable series — cap the chart, don't
fabricate. Depth: retail **1985→**, IVA **1994→**, unemployment **2018→**. Confidence:
✅ retail, 🟡 others (GUID harvest).

## Group 3 — Quarterly GDP & household income/expenditure

**Ongoing.** GDP: `X季度国内生产总值初步核算结果` (2026Q1 `t20260417_1963336`), ~17th of
the month after quarter-end; table of 绝对额/增速 by industry. **Final-consumption
contribution to growth** (`最终消费支出对经济增长的贡献率为([\d.]+)%`,
`拉动GDP增长([\d.]+)个百分点`) appears in the umbrella 国民经济运行 presser prose and
the expenditure-side release — prose, quarterly/annual. Income:
`X居民收入和消费支出情况` (Q1 `t20260416_1963323`); table `全国居民收支主要数据`
(value · 名义增长% · (实际增长%)) then urban/rural and the 8 consumption categories.
Fixtures: `nbs_income/2026Q1_income.html`. `tools/fetch_income_archive.py` already parses
this exact table; reuse it. **Window:** ~day 16–17 after quarter-end.

**Backfill.** Income: existing archive is authoritative **2013→ quarterly** (with a
2013–2016 hand-verified supplement layer already coded). GDP: DG API (quarterly GDP to
1992) + FRED/IMF from US runner. Contribution-to-growth is mostly annual and patchy —
DG/annual `统计公报` only. Depth: income **2013→**, GDP **1992→ (Q)**, contribution
**annual**. Confidence: ✅ income, 🟡 GDP/contrib.

## Group 4 — PMI (manufacturing + non-manufacturing)

**Ongoing.** `X月中国采购经理指数运行情况`, **last calendar day of the month**, 09:30
(June `t20260630_1964032`). Single article, three headline numbers **in prose**:
`制造业采购经理指数（PMI）为50.3%`, `非制造业商务活动指数为…`,
`综合PMI产出指数为50.6%` — plus tables for the 12 manufacturing sub-indices (生产, 新订单,
新出口订单, 原材料库存, 从业人员, 供货商配送…), construction, and services. **Watch the
inline-tag split**: `综合PMI产出指数` renders as `综合<span>PMI</span>产出指数` — match on
`"".join(node.itertext())`, never the raw HTML string. Fixture: `nbs_pmi/2026-06_pmi.html`.
**Window:** last day of month.

**Backfill.** DG API (mfg PMI 2005→, non-mfg 2007→). CFLP (`cflp.org.cn`) mirrors the
same official numbers; Caixin PMI (S&P Global) is a **separate** private series — keep it
distinct, don't merge. Depth **2005→**. Confidence: ✅ engine.

## Group 5 — Consumer confidence index (NBS)

**Ongoing.** NBS does **not** issue a press release for this. It appears only in the DG
national-data monthly database (景气指数 → 消费者信心指数), **lagged ~5–6 weeks**. So the
"ongoing" mechanism is a **DG API poll on a schedule**, keyed off `queryDtByCid` latest
period, not listing discovery. **Window:** ~6 weeks after reference month.

**Backfill.** DG API (monthly to ~1998). Depth **1998→**. Confidence: 🟡 (needs GUID;
inherently lagged; the caveat card should say "as of <ref month>, N weeks stale").

## Group 6 — PBoC money & credit

**Ongoing.** Three monthly reports, ~10th–15th:
- `X月金融统计数据报告` (May `…/2026061214273613328/index.html`, 2026/06/12) — M2, M1,
  人民币贷款余额, 本外币存款, plus flows (新增人民币贷款, 住户/企业 breakdown).
- `X月社会融资规模增量统计数据报告` — 社融 **flow** (增量), YTD + month.
- `X月社会融资规模存量统计数据报告` — 社融 **stock** (存量).
Discovery: `wzdig` search on the exact title, keep `www.pbc.gov.cn/goutongjiaoliu/113456/
113469/…` (national) and drop province mirrors (厦门/深圳/广东…). See Sketch B for the
prose regexes and the half/full-width punctuation trap (both appear in one report:
`余额353.67万亿元,同比增长8.6%` vs `存量为458.81万亿元，同比增长7.7%`). Household
mortgage balance (个人住房贷款余额) is **not** here — it is quarterly, in
`金融机构贷款投向统计报告`, already parsed by `tools/fetch_property_archive.py`. Fixture:
`pboc_money/2026-05_finstats.html`. **Window:** day 10–15.

**Backfill.** `wzdig` back-catalog reaches the early 2000s (M2/loans) and 2015 for 社融
存量 (official start; flow backcast to 2002). DBnomics/FRED (`MYAGM2CNM189N`) from the US
runner for a clean M2 spine. Reuse the existing `pbc_search` + `wan_yuan_match` helpers.
Depth: M2/loans **1998→**, 社融存量 **2015→**. Confidence: ✅ (search + parser proven in
repo). **Series break to flag:** M1 was redefined Jan 2025 (added personal demand
deposits + non-bank payment balances) — a step change, not a data error.

## Group 7 — Customs exports/imports

**Ongoing.** `english.customs.gov.cn` `preliminary.html`/`monthly.html` are **JS nav
shells** — values are injected by `/Scripts/statistic.js`, no server-side table, no
static JSON (verified; fixture `customs/2026_preliminary.html` retained as evidence).
Realistic paths, in order:
1. **DBnomics `CN` trade / IMF DOTS / FRED** monthly exports & imports (USD, YoY) — clean
   JSON, trivial from the US runner. **Recommended default** for a dashboard that needs
   only the two headline aggregates.
2. **GACC Chinese** `www.customs.gov.cn/.../302275/index.html` returns **HTTP 412**
   (anti-bot precondition) to a plain client — a real browser / the US runner with a
   cookie-priming step passes it; gives USD + RMB, monthly, by trade partner.
3. **NBS activity batch** `货物进出口` (RMB, monthly YoY) as an always-available proxy
   already inside Group 2's fetch.
**Window:** ~7th–9th (GACC express), NBS batch mid-month.

**Backfill.** DBnomics/IMF DOTS to the 1990s (USD). Depth **1990s→**. Confidence: 🟡
(primary English HTML unusable; fallback solid but US-egress-dependent).

## Group 8 — Tier-3 pulse (best-effort)

- **State Post Bureau — ✅ high.** Listing `spb.gov.cn/gjyzj/c100015/c100016/`, monthly
  `国家邮政局公布YYYY年1-N月邮政行业运行情况` (URL `…/YYYYMM/<32hex>.shtml`, e.g.
  `202606/b2d7c77013a64fe7a73f527c2a41d15f.shtml`). Prose: 邮政行业业务收入,
  快递业务量(件), 快递业务收入. ~mid-month, YTD cumulative. Backfill: listing pagination
  ~2015→.
- **文旅部 holiday tourism — 🟡 medium.** `mct.gov.cn/whzx/whyw/YYYYMM/tYYYYMMDD_<id>.htm`,
  **per-holiday not monthly** (元旦/春节/清明/五一/端午/中秋/国庆). Prose
  `国内出游([\d.]+)亿人次` + `国内出游总花费([\d.]+)亿元` (+YoY). Discovery: listing or
  search `<year>年<holiday>假期国内出游`. Reliability limited by holiday cadence + prose.
- **Box office — ⚪ low.** No official monthly release; 国家电影局
  (`chinafilm.gov.cn/xwzx/gzdt/`) posts irregular milestone news. Real numbers are
  live-tracked by 灯塔/猫眼专业版 and `zgdypw.cn` (中国电影数据信息网, official but
  annual/summary). Treat as scrape-or-manual; mark low reliability.
- **Autos — 🟡 medium.** CPCA `cpcaauto.com` (乘用车 retail/wholesale/NEV, ~1st week
  preliminary + mid-month final) and CAAM `caam.org.cn/tjsj` (production/sales). Both
  publish partly inside articles/PDF; structure changes — parse defensively, expect
  breakage. Distinct series (CPCA retail ≠ CAAM wholesale) — keep separate.
- **Aviation + rail — 🟡 / ⚪.** CAAC `caac.gov.cn/XXGK/XXGK/TJSJ/` monthly
  `中国民航YYYY年M月份主要生产指标统计` (旅客运输量, table). Rail passenger volume
  (国铁集团) is irregular news; MOT `mot.gov.cn/tongjishuju` has a monthly transport
  bulletin — use it as the rail proxy. Reliability medium/low.

Tier-3 has **no DG-API path** — history is only as deep as each listing paginates
(mostly ~2015→). Mark every tier-3 series with an explicit reliability flag in the UI.

---

## Parser sketch A — NBS activity-batch release (numbers in running prose + tables)

**Target:** `nbs_activity/2026-05_retail.html` (retail is the representative worst case;
same shape for IVA/FAI/income).

1. **Locate the data table, not the wrapper.** `//table[.//*[contains(
   normalize-space(.), '社会消费品零售总额')]]`. The article-body div class drifted
   `zoom → detail-text-content`; the table anchor is stable. Fall back to prose regex if
   no table (Jan/Feb combined releases sometimes ship prose-only).
2. **Normalise every cell** with `re.sub(r'\s+','', ''.join(cell.itertext()))` (kills
   `　`, `\xa0`, and inline-tag splits). Drop empty cells.
3. **Column model.** After the two header rows (`指标/月/累计`, then
   `绝对量（亿元）/同比增长（%）×2`), each data row = `[label, m_val, m_yoy, ytd_val,
   ytd_yoy]`. Map `-/--/—/空 → None`. Rows with <2 numbers = section headers
   (按经营地分…) → skip. Map `label` via the alias dict (reuse repo's `SERIES_ALIASES`);
   strip `其中：` prefixes before lookup.
4. **Prose supplements** for what the table omits: `实物商品网上零售额([\d.]+)亿元，
   (增长|下降)([\d.]+)%`; headline YoY from `<title>`/first `<p>` as a cross-check.
5. **Unit/ sign normalisation:** table is 亿元; prose 万亿→×10000. `增长→+`, `下降→−`,
   `持平→0`. `个百分点` is an additive change, keep separate from `%` levels.

**Three ways it breaks:** (1) **header-row count flips** 1↔2 → columns shift by one;
guard by detecting the `绝对量`/`同比增长` header explicitly and aligning to it, not by
fixed index. (2) **NBS renames/splits a row** (e.g. adds a category) → silent alias miss;
guard with a completeness assertion (`≥ N expected labels present`) and log every unknown
label. (3) **Jan/Feb combined "1—2月" releases** carry no single-month column → period
logic must treat the Feb release as YTD-only and not invent a January month value.

## Parser sketch B — PBoC money/credit report (all numbers in prose)

**Target:** `pboc_money/2026-05_finstats.html`.

1. **Extract body text** with punctuation intact:
   `''.join(node.itertext())` over `//div[contains(@class,"detail-text-content")]`
   (fallbacks `//div[@id="zoom"]`, `//div[contains(@class,"TRS_Editor")]`), then collapse
   only whitespace, **not** commas.
2. **Per-metric regexes, tolerant to half/full-width punctuation** (both occur in one
   report):
   - M2 `广义货币[（(]M2[）)]余额([\d.]+)万亿元[，,]同比增长([\d.]+)%`
   - M1 `狭义货币[（(]M1[）)]余额([\d.]+)万亿元[，,]同比(增长|下降)([\d.]+)%`
   - 社融存量 `社会融资规模存量为([\d.]+)万亿元[，,]同比增长([\d.]+)%`
   - 人民币贷款(stock) `人民币贷款余额([\d.]+)万亿元`
   - 存款 `本外币存款余额([\d.]+)万亿元`
   - household `住户(贷款|存款)(增加|减少)([\d.]+)(万?亿)元` (these are **flows**)
   - new RMB loans (flow) `人民币贷款增加([\d.]+)万亿元` (YTD) / month figure nearby.
3. **Unit normalisation:** `万亿→×10000` to 亿元; keep `%` and `个百分点` distinct.
   `增长/增加→+`, `下降/减少/少增→−`.

**Three ways it breaks:** (1) **stock vs flow capture** — 社融 and 贷款 each quote a
`余额/存量` (stock) **and** a `增加/增量` (flow), often in adjacent sentences; a loose
regex grabs the wrong one. Bind stock patterns to `余额|存量` and flow patterns to
`增加|增量|新增`, and note the 社融 **flow** lives in a *separate* 增量 report article.
(2) **Punctuation/paren drift** (`,` vs `，`, `(` vs `（`, occasional space) — allow the
character classes above or matches silently drop. (3) **Definitional breaks** (M1 recast
Jan 2025) produce a real level jump — the parser should still parse, but the pipeline must
carry a `caliber_break` flag so the chart annotates rather than "corrects" it.

---

## Fixtures downloaded (contract tests)

Under `pipeline/fixtures/raw/` (see its `README.md` for the trim log). All real,
2026-07-08, trimmed to the article body where noted, kept < 300KB:

`nbs_cpi/2026-05_cpi.html` (84K) · `nbs_activity/2026-05_retail.html` (84K) ·
`nbs_income/2026Q1_income.html` (88K) · `nbs_pmi/2026-06_pmi.html` (172K) ·
`pboc_money/2026-05_finstats.html` (44K) · `customs/2026_preliminary.html` (24K, shell
evidence). A DG-API JSON sample is not saved as a file — its contract is the live pull
documented above (`getEsDataByIndicatorIdAndDa` → `{dt,v}` array).

## The one-time owner's-Mac / browser seed

Only one thing genuinely needs a real browser session on the owner's network:
**harvesting the DG `cid`+indicator-`id` GUID map for every series we don't already have**
(the repo has retail's). Sketch:
1. Open `https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData` in a real
   browser (passes the WAF via a normal session; owner's network may egress CN).
2. Navigate to each indicator (CPI, PPI, IVA, FAI, unemployment, GDP, PMI, consumer
   confidence…). In the Network tab, each click fires
   `getEsDataByIndicatorIdAndDa` / `queryEsDataByIndicatorId` with the `cid`, `id`,
   `rootId` in the JSON payload. Copy those GUIDs.
3. Drop them into `OFFICIAL_SERIES` in `tools/fetch_nbs_national_data.py`. From then on CI
   pulls full 1985→ monthly history headlessly (no browser) via Engine B — **provided the
   DG endpoint is reachable from the runner**. If the US runner is WAF-blocked on DG, this
   same browser step, run once locally, can also **export the full history to JSON**
   (loop `month_codes(1985, latest)`), committing a static backfill seed the pipeline
   thereafter only appends to.

This seed is a **one-off** producing a static GUID map (and optionally a one-time history
dump). Everything else — ongoing monthly discovery/parse and, if DG is reachable,
incremental backfill — is fully automatable in CI.

## Single riskiest assumption

That the **DG publicrelease API and the `.cn` HTML hosts are reachable from the US CI
runner**. From this CN-egress vantage they are all green and FRED is unreachable; the US
runner may see exactly the reverse. The entire "verified-live" column is contingent on the
parallel US-reachability test. Mitigation already designed in: if the `.cn` primaries are
US-blocked, (a) ongoing parse can run on a CN-egress worker or via the owner's Mac on a
schedule, and (b) the one-time browser seed converts DG history into a committed static
JSON so CI never needs the live API for backfill — only the cross-check sources
(DBnomics/FRED/OECD/IMF), which are US-friendly, stay in CI.
