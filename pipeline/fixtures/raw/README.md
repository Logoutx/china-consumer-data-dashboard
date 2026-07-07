# Raw source fixtures (parser contract tests)

Real pages captured **2026-07-08** for the data-acquisition rebuild. Each is an
unmodified download except where a `<!-- TRIMMED FIXTURE ... -->` / `<!-- FIXTURE
NOTE ... -->` comment at the top says otherwise. Use these to pin parser behaviour;
do **not** hand-edit the data body.

| File | Source page | Published | Orig → kept | What the parser keys on |
|---|---|---|---|---|
| `nbs_cpi/2026-05_cpi.html` | stats.gov.cn `/sj/zxfb/202606/t20260610_1963923.html` — "2026年5月份居民消费价格同比上涨1.2%" | 2026/06/10 | 229KB → 84KB | The 48-row `<table>` (MoM% / YoY% / YTD-YoY%); rows 居民消费价格·城市·农村·食品烟酒…8 categories |
| `nbs_activity/2026-05_retail.html` | `/sj/zxfb/202606/t20260616_1963949.html` — "2026年1—5月份社会消费品零售总额增长1.4%" | 2026/06/16 | 233KB → 84KB | Main 社零 table (label · 绝对量亿元 · 同比% · 累计绝对量 · 累计同比%) + 18-commodity 限额以上 table |
| `nbs_income/2026Q1_income.html` | `/sj/zxfb/202604/t20260416_1963323.html` — "2026年一季度居民收入和消费支出情况" | 2026/04/16 | 241KB → 88KB | 全国居民收支主要数据 table (value · 名义增长/实际增长) + 8-category consumption table |
| `nbs_pmi/2026-06_pmi.html` | `/sj/zxfb/202606/t20260630_1964032.html` — "2026年6月中国采购经理指数运行情况" | 2026/06/30 | 412KB → 172KB | Composite in prose ("制造业采购经理指数（PMI）为50.3%" / "综合PMI产出指数为50.6%") + manufacturing/non-manufacturing sub-index tables |
| `pboc_money/2026-05_finstats.html` | pbc.gov.cn `/goutongjiaoliu/113456/113469/2026061214273613328/index.html` — "2026年5月金融统计数据报告" | 2026/06/12 | 44KB (untrimmed) | Running prose: M2/M1/社融存量/人民币贷款/本外币存款 as `<label>余额N万亿元，同比增长P%` |
| `customs/2026_preliminary.html` | english.customs.gov.cn `/statics/report/preliminary.html` | n/a | 24KB (untrimmed) | **Dead-end evidence** — JS nav shell, no server-side data (see note in file) |

## Trimming method (NBS files only)
Kept `<title>`, `<meta name="PubDate">`, and every `div.detail-text-content`
block (the article body: prose + all data tables). Stripped `<head>` assets,
site header/nav/footer, and `<script>/<style>/<svg>/<img>/<link>`. No data cell,
table, or sentence in the article body was altered. The heavy originals were
~230–412KB almost entirely from inline base64 icons and scripts.

## Two parsing gotchas these fixtures exist to lock down
1. **Wrapper drift**: 2026 NBS pages use `class="detail-text-content"`, not the
   old `id="zoom"`. Anchor parsers on the **table whose header contains the series
   name**, never on the wrapper div id.
2. **Inline-tag splitting**: labels like `综合PMI产出指数` are split as
   `综合<span>PMI</span>产出指数` in the raw HTML. Match against
   `"".join(node.itertext())` (normalised), not the raw HTML string — a raw
   substring test returns a false negative.

## Not captured here (documented in ../../docs/ACQUISITION.md instead)
- **Customs real values**: the English site is a JS shell; use GACC Chinese via
  browser/US-runner (HTTP 412 anti-bot) or a DBnomics `CN` trade fallback.
- **DG backfill API sample**: `data.stats.gov.cn/dg/website/publicrelease/web/external/*`
  returns JSON, not HTML — its contract is covered in ACQUISITION.md, not here.
