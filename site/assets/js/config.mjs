// config.mjs — the one place that knows where data lives (task instruction:
// "compute relative paths carefully or make them configurable at one
// constant"). Default is relative to *this document*, so it's correct
// whether the repo is served from "/", a GitHub Pages project subpath, or
// any other root — "../site-data" from site/index.html always lands on
// site-data/ next to site/, regardless of domain.
//
// Override with ?data=<path> to point the whole page at a different bundle
// tree without touching source — e.g. "?data=../site-data-fixtures" to QA
// break markers / annotations / revision flags against the synthetic
// pipeline/tests/fixtures/build bundle (the real data/ tree has none of
// those yet; see the build report).

// Guarded so this module can also be imported under plain Node (tests.mjs)
// without a browser `location` global.
const params = new URLSearchParams(typeof location !== 'undefined' ? location.search : '');

export const DATA_BASE = (params.get('data') || '../site-data').replace(/\/$/, '');

export const SITE_NAME = '中国消费数据看板';
export const GITHUB_URL = 'https://github.com/Logoutx/china-consumer-data-dashboard';

// Narrative order per VIZ-GUIDE §Page anatomy. index.json's own `sections`
// array already carries this order (catalog `order` field), so this is only
// a fallback if that array is ever missing an entry.
export const SECTION_ORDER_FALLBACK = [
  'prices',
  'consumption',
  'income-confidence',
  'employment',
  'property',
  'money-credit',
  'macro',
  'high-frequency',
];

export const PANEL_ID_70CITY = 'nbs-70city-price';
