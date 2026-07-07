// tests.mjs — unit tests for this app's PURE functions, runnable with:
//   node --test site/assets/js/tests.mjs
//
// Scope: modules that never touch `document`/`window` at import time.
// store.mjs (touches `window.matchMedia`) and config.mjs (touches
// `location`) are guarded to no-op under Node (see their own files), which
// means every module that only imports THOSE plus other pure modules is now
// safely importable here too, including components/section.mjs (its pure
// decision functions — resolveHeadlineAndDek, primarySeriesValues,
// seriesForCaliber, buildCaliberOption, buildSourceLine — are exported
// specifically so the design-review fixes are unit-testable, not just
// eyeballed). Still out of scope: anything that calls document.createElement
// etc. as soon as it RUNS (app.mjs's main(), any component's mount*
// function actually invoked) — those need a real DOM and are exercised by
// the manual browser checklist in site/DEV-NOTES.md instead.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { isCJK, needsPanguSpace, panguJoin, formatNumber, formatPercent, formatPP, formatDateZh, circledNumeral } from './lib/format.mjs';
import { periodShape, periodOrdinal, withinRangeYears } from './lib/period.mjs';
import { extent, niceTicks, linearScale, decimalsForStep } from './lib/scale.mjs';
import { linePathD } from './lib/path.mjs';
import { upDownColor, upDownClass, agencyZhFromId } from './lib/color.mjs';
import { buildCityRows } from './components/city-grid.mjs';
import {
  resolveHeadlineAndDek,
  displayName,
  agencyFor,
  levelDecimals,
  primarySeriesValues,
  seriesForCaliber,
  buildCaliberOption,
  buildSourceLine,
} from './components/section.mjs';

// -- format.mjs ---------------------------------------------------------------

test('isCJK distinguishes CJK ideographs from ASCII/digits', () => {
  assert.equal(isCJK('中'), true);
  assert.equal(isCJK('年'), true);
  assert.equal(isCJK('a'), false);
  assert.equal(isCJK('5'), false);
  assert.equal(isCJK(''), false);
});

test('needsPanguSpace fires only at a CJK<->digit seam', () => {
  assert.equal(needsPanguSpace('年', '5'), true);
  assert.equal(needsPanguSpace('5', '年'), true);
  assert.equal(needsPanguSpace('年', '月'), false); // CJK-CJK: no space
  assert.equal(needsPanguSpace('5', '6'), false); // digit-digit: no space
  assert.equal(needsPanguSpace('%', '5'), false); // ASCII punctuation, not a digit-CJK seam
  assert.equal(needsPanguSpace('', '5'), false);
});

test('panguJoin inserts exactly one half-width space at CJK<->digit seams', () => {
  assert.equal(panguJoin('用 GPT-4 做'), '用 GPT-4 做'); // pass-through, already spaced
  assert.equal(panguJoin('覆盖', '80', '% 用户'), '覆盖 80% 用户');
  assert.equal(panguJoin('2026', '年'), '2026 年');
  assert.equal(panguJoin('截至 ', '2026-05'), '截至 2026-05'); // already has trailing space; no double space
  assert.equal(panguJoin('', '资料来源', null, undefined, '：国家统计局'), '资料来源：国家统计局');
});

test('formatNumber: thousands separators + natural precision (no bundle decimals hint)', () => {
  assert.equal(formatNumber(41090, 1), '41,090.0');
  assert.equal(formatNumber(-3.64), '-3.64'); // natural precision escalates to 2 decimals
  assert.equal(formatNumber(-0.6), '-0.6'); // stays at 1 decimal
  assert.equal(formatNumber(1234567.89, 2), '1,234,567.89');
  assert.equal(formatNumber(38, 0), '38'); // count-type override, no trailing ".0"
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(undefined), '—');
  assert.equal(formatNumber(NaN), '—');
});

test('formatPercent: no space before "%", optional explicit "+"', () => {
  assert.equal(formatPercent(-0.6), '-0.6%');
  assert.equal(formatPercent(3.6, null, { sign: true }), '+3.6%');
  assert.equal(formatPercent(-3.64), '-3.64%');
  assert.equal(formatPercent(0), '0.0%');
  assert.equal(formatPercent(null), '—');
});

test('formatPP: always non-negative magnitude + unit word', () => {
  assert.equal(formatPP(-6.5), '6.5 个百分点');
  assert.equal(formatPP(0.04), '0.04 个百分点');
});

test('formatDateZh: ISO datetime -> Chinese date label', () => {
  assert.equal(formatDateZh('2026-07-08T00:00:00Z'), '2026 年 7 月 8 日');
  assert.equal(formatDateZh('not-a-date'), '—');
});

// -- period.mjs -----------------------------------------------------------------

test('periodShape dispatches on the period STRING, never a declared freq', () => {
  assert.equal(periodShape('2026-05'), 'monthly');
  assert.equal(periodShape('2026-Q2'), 'quarterly');
  assert.equal(periodShape('2026'), 'annual');
});

test('periodOrdinal is linear within and across shapes', () => {
  assert.equal(periodOrdinal('2026-06') - periodOrdinal('2026-05'), 1);
  assert.equal(periodOrdinal('2027-01') - periodOrdinal('2026-12'), 1);
  assert.equal(periodOrdinal('2026-Q2') - periodOrdinal('2026-Q1'), 3);
  assert.equal(periodOrdinal('2027') - periodOrdinal('2026'), 12);
});

test('withinRangeYears is an inclusive N-year window ending at latestPeriod', () => {
  assert.equal(withinRangeYears('2021-05', '2026-05', 5), true); // exactly 60 months back
  assert.equal(withinRangeYears('2021-04', '2026-05', 5), false); // 61 months: outside
  assert.equal(withinRangeYears('1985-01', '2026-05', Infinity), true);
  assert.equal(withinRangeYears('2026-06', '2026-05', 5), false); // future point relative to "latest"
});

// -- scale.mjs --------------------------------------------------------------------

test('extent ignores null/undefined/NaN and returns null for an all-empty input', () => {
  assert.deepEqual(extent([1, 2, null, 3, undefined, NaN]), [1, 3]);
  assert.equal(extent([]), null);
  assert.equal(extent([null, undefined]), null);
});

test('niceTicks stays within VIZ-GUIDE rule 10 (3-5 round-step gridlines) across real data ranges', () => {
  const domains = [
    [-0.6, 5.9], // retail YoY spark
    [-5.93, -0.2], // 70-city resale YoY
    [0, 45396], // retail level (亿元)
    [100, 101.2], // CPI index level
    [-41090, 0], // negative-only level domain
    [5, 5], // degenerate single-value domain
  ];
  for (const [min, max] of domains) {
    const { ticks } = niceTicks(min, max, 4);
    assert.ok(ticks.length >= 3 && ticks.length <= 5, `domain [${min},${max}] produced ${ticks.length} ticks`);
  }
});

test('niceTicks never leaks binary-float representation noise (e.g. -0.6000000000000001)', () => {
  const { ticks } = niceTicks(-0.72, -0.06, 4);
  for (const t of ticks) {
    assert.equal(t, Math.round(t * 1e9) / 1e9, `tick ${t} has float noise beyond 1e-9`);
  }
});

test('niceTicks domain always brackets [min, max]', () => {
  const { domain } = niceTicks(3.6, 4.9, 4);
  assert.ok(domain[0] <= 3.6 && domain[1] >= 4.9);
});

test('linearScale maps domain endpoints to range endpoints and inverts', () => {
  const scale = linearScale([0, 10], [100, 300]);
  assert.equal(scale(0), 100);
  assert.equal(scale(10), 300);
  assert.equal(scale(5), 200);
  assert.equal(scale.invert(200), 5);
});

// -- path.mjs -----------------------------------------------------------------------

test('linePathD draws a continuous path through non-null points', () => {
  const d = linePathD([
    { x: 0, y: 0 },
    { x: 10, y: 5 },
    { x: 20, y: 10 },
  ]);
  assert.equal(d, 'M 0 0 L 10 5 L 20 10');
});

test('linePathD lifts the pen at a null (no_yoy_across break gap), starting a new subpath', () => {
  const d = linePathD([
    { x: 0, y: 1 },
    { x: 1, y: null },
    { x: 2, y: 3 },
  ]);
  assert.equal(d, 'M 0 1 M 2 3');
});

test('linePathD returns empty string when every point is null', () => {
  assert.equal(linePathD([{ x: 0, y: null }, { x: 1, y: undefined }]), '');
});

// -- color.mjs ------------------------------------------------------------------------

test('upDownColor is the fixed 红涨绿跌 mapping', () => {
  assert.equal(upDownColor(3.6), 'var(--accent-red)'); // 涨 = red
  assert.equal(upDownColor(-0.6), 'var(--fall-green)'); // 跌 = green
  assert.equal(upDownColor(0), 'var(--ink-soft)');
  assert.equal(upDownColor(null), 'var(--context)');
});

test('upDownClass mirrors upDownColor as a CSS-class-friendly string', () => {
  assert.equal(upDownClass(1), 'up');
  assert.equal(upDownClass(-1), 'down');
  assert.equal(upDownClass(0), 'flat');
  assert.equal(upDownClass(null), 'flat');
});

test('agencyZhFromId recovers the publishing agency from the id prefix (DATA-CONTRACT §2)', () => {
  assert.equal(agencyZhFromId('nbs-retail-total'), '国家统计局');
  assert.equal(agencyZhFromId('pbc-m1'), '中国人民银行');
  assert.equal(agencyZhFromId('mof-land-transfer-revenue'), '财政部');
  assert.equal(agencyZhFromId('unknown-agency-slug'), '官方数据');
});

// -- city-grid.mjs: buildCityRows (pure transform of a panel bundle) ------------------

test('buildCityRows reads latest_by_city + cells for the primary/secondary metrics', () => {
  const panel = {
    dimensions: { city: ['北京', '上海'], metric: ['new_home', 'resale_home'] },
    cells: {
      北京: { new_home: { m: [-0.3, -0.2] }, resale_home: { m: [-0.6, -0.5] } },
      上海: { new_home: { m: [0.1, 0.2] }, resale_home: { m: [-0.1, null] } },
    },
    latest_by_city: {
      北京: { new_home: { m: -0.2, m_yoy: -2.1 }, resale_home: { m: -0.5, m_yoy: -3.0 } },
      上海: { new_home: { m: 0.2, m_yoy: 1.2 }, resale_home: { m: null, m_yoy: null } },
    },
  };
  const rows = buildCityRows(panel, 'new_home', 'resale_home');
  assert.equal(rows.length, 2);
  const beijing = rows.find((r) => r.city === '北京');
  assert.equal(beijing.latestPrimary, -0.2);
  assert.equal(beijing.latestSecondary, -0.5);
  assert.deepEqual(beijing.sparkValues, [-0.3, -0.2]);
  const shanghai = rows.find((r) => r.city === '上海');
  assert.equal(shanghai.latestSecondary, null); // missing cell stays null, never fabricated
});

test('buildCityRows tolerates a city missing from latest_by_city entirely', () => {
  const panel = {
    dimensions: { city: ['深圳'], metric: ['new_home', 'resale_home'] },
    cells: { 深圳: { new_home: { m: [0.1] } } },
    latest_by_city: {},
  };
  const rows = buildCityRows(panel, 'new_home', 'resale_home');
  assert.equal(rows[0].latestPrimary, null);
  assert.equal(rows[0].latestSecondary, null);
});

// -- design-review fixes ------------------------------------------------------------

test('circledNumeral: ①..⑳ then a bracketed fallback', () => {
  assert.equal(circledNumeral(1), '①');
  assert.equal(circledNumeral(2), '②');
  assert.equal(circledNumeral(20), '⑳');
  assert.equal(circledNumeral(21), '(21)');
});

test('decimalsForStep (item 3): whole numbers when step>=1, else the step\'s own decimal count', () => {
  assert.equal(decimalsForStep(20), 0);
  assert.equal(decimalsForStep(1), 0);
  assert.equal(decimalsForStep(0.5), 1);
  assert.equal(decimalsForStep(0.2), 1);
  assert.equal(decimalsForStep(0.05), 2);
  assert.equal(decimalsForStep(0), 0);
});

test('niceTicks (item 4 regression): CPI domain [-0.8, 2.8] no longer over-pads to [-2, 4]', () => {
  // The exact case from design review: rendered on [-2,4] (span 6) before
  // the fix; the direct single-stage step formula tightens this to a span
  // of 4, still bracketing the data with round numbers.
  const { domain, ticks } = niceTicks(-0.8, 2.8, 4);
  assert.deepEqual(domain, [-1, 3]);
  assert.deepEqual(ticks, [-1, 0, 1, 2, 3]);
  assert.ok(domain[1] - domain[0] < 6, 'must be tighter than the old 6-unit span');
});

test('niceTicks: escalates step to stay within the 3-5 tick cap even for domains the direct formula would overshoot', () => {
  const { ticks } = niceTicks(-41090, 0, 4);
  assert.ok(ticks.length >= 3 && ticks.length <= 5, `got ${ticks.length} ticks`);
});

test('niceTicks: 3-5 ticks holds across every real data range this app renders', () => {
  const domains = [
    [-0.6, 5.9],
    [-5.93, -0.2],
    [0, 45396],
    [100, 101.2],
    [-206031, -160000],
    [0, 70],
    [3.6, 4.9],
  ];
  for (const [min, max] of domains) {
    const { ticks } = niceTicks(min, max, 4);
    assert.ok(ticks.length >= 3 && ticks.length <= 5, `domain [${min},${max}] produced ${ticks.length} ticks`);
  }
});

// -- section.mjs pure decision functions -----------------------------------------------

function makeEntry(overrides = {}) {
  return {
    id: 'nbs-test-series',
    name_zh: '测试序列',
    name_en: 'Test series',
    unit_zh: '%',
    value_type: 'index',
    calibers: ['single'],
    latest: { period: '2026-05', period_label_zh: '2026 年 5 月', m: 101.2, m_yoy: 1.2 },
    headline: { caliber: 'single' },
    takeaway: null,
    yoy_series: [{ period: '2026-05', yoy: 1.2 }],
    level_series: [{ period: '2026-05', m: 101.2 }],
    annotations: [],
    breaks: [],
    flags_latest: [],
    revisions_recent: [],
    ...overrides,
  };
}

test('resolveHeadlineAndDek (item 6): no takeaway -> headline=name, dek=unit ONLY (never duplicated)', () => {
  const entry = makeEntry({ takeaway: null, name_zh: '制造业 PMI' });
  const { headlineText, dekText } = resolveHeadlineAndDek(entry);
  assert.equal(headlineText, '制造业 PMI');
  assert.equal(dekText, '%'); // unit only -- NOT "制造业 PMI · %" (that would repeat the headline)
});

test('resolveHeadlineAndDek: real takeaway -> headline=takeaway, dek="name · unit"', () => {
  const entry = makeEntry({ takeaway: '2026 年 5 月制造业 PMI 为 50.2，高于荣枯线', name_zh: '制造业 PMI', name_short: 'PMI' });
  const { headlineText, dekText } = resolveHeadlineAndDek(entry);
  assert.equal(headlineText, '2026 年 5 月制造业 PMI 为 50.2，高于荣枯线');
  assert.equal(dekText, 'PMI · %'); // prefers name_short
});

test('resolveHeadlineAndDek: takeaway identical to the name is treated as "no takeaway" (still no duplicate)', () => {
  const entry = makeEntry({ takeaway: '测试序列', name_zh: '测试序列' });
  const { headlineText, dekText } = resolveHeadlineAndDek(entry);
  assert.equal(headlineText, '测试序列');
  assert.equal(dekText, '%');
});

test('displayName/agencyFor/levelDecimals (item 6): prefer bundle-provided fields, fall back gracefully', () => {
  assert.equal(displayName(makeEntry({ name_short: 'PMI', name_zh: '制造业 PMI' })), 'PMI');
  assert.equal(displayName(makeEntry({ name_short: undefined, name_zh: '制造业 PMI' })), '制造业 PMI');

  assert.equal(agencyFor(makeEntry({ source: { agency_zh: '中国物流与采购联合会' } })), '中国物流与采购联合会');
  assert.equal(agencyFor(makeEntry({ source: undefined, id: 'pbc-m1' })), '中国人民银行'); // falls back to id-prefix guess

  assert.equal(levelDecimals(makeEntry({ decimals: 2 })), 2);
  assert.equal(levelDecimals(makeEntry({ decimals: undefined })), null);
});

test('primarySeriesValues: prefers entry.decimals for the LEVEL lane, never for the YoY lane', () => {
  const levelOnly = makeEntry({
    value_type: 'level',
    decimals: 0,
    yoy_series: [{ period: '2026-05', yoy: null }],
    level_series: [{ period: '2026-05', m: 7955 }],
  });
  const { decimals, isPercent } = primarySeriesValues(levelOnly);
  assert.equal(isPercent, false);
  assert.equal(decimals, 0); // uses entry.decimals for the level lane

  const yoyCase = makeEntry({ decimals: 0, yoy_series: [{ period: '2026-05', yoy: 3.6 }] });
  const yoyResult = primarySeriesValues(yoyCase);
  assert.equal(yoyResult.isPercent, true);
  assert.equal(yoyResult.decimals, null); // YoY lane always infers natural precision, ignoring entry.decimals
});

test('seriesForCaliber (item 9, feature-detected): "single" always resolves (it is just today\'s plain yoy_series/level_series); "ytd" is null until the bundle grows the new suffixed fields', () => {
  const entry = makeEntry(); // no yoy_series_ytd/level_series_ytd -- today's reality
  assert.deepEqual(seriesForCaliber(entry, 'single'), { values: [{ period: '2026-05', value: 1.2 }], isPercent: true });
  assert.equal(seriesForCaliber(entry, 'ytd'), null);
});

test('seriesForCaliber: detects yoy_series_ytd/level_series_ytd when present and prefers the yoy lane', () => {
  const entry = makeEntry({
    yoy_series_ytd: [{ period: '2026-05', yoy: 4.7 }],
    level_series_ytd: [{ period: '2026-05', m: 206031 }],
  });
  const result = seriesForCaliber(entry, 'ytd');
  assert.deepEqual(result, { values: [{ period: '2026-05', value: 4.7 }], isPercent: true });
});

test('buildCaliberOption: null when the series only has one caliber', () => {
  assert.equal(buildCaliberOption(makeEntry({ calibers: ['single'] })), null);
});

test('buildCaliberOption: builds single/ytd blocks (and stays text-only for the toggle when no ytd-suffixed series arrays exist)', () => {
  const entry = makeEntry({
    calibers: ['single', 'ytd'],
    value_type: 'level',
    unit_zh: '亿元',
    decimals: 1, // matches the real nbs-retail-total sample seen in the built bundle
    latest: { period: '2026-05', period_label_zh: '2026 年 5 月', m: 41090, m_yoy: -0.6, ytd: 206031, ytd_yoy: 1.4 },
  });
  const caliber = buildCaliberOption(entry);
  assert.equal(caliber.single.valueText, '41,090.0 亿元');
  assert.equal(caliber.single.yoyText, '同比 -0.6%');
  assert.equal(caliber.ytd.valueText, '206,031.0 亿元');
  // "single" always resolves (today's plain yoy_series), so the toggle's
  // fullSwapAvailable gate (line-chart.mjs) correctly still requires BOTH
  // single AND ytd series to be present -- ytd stays absent until the
  // bundle grows yoy_series_ytd/level_series_ytd, so the toggle is
  // text-only today even though .single.series now exists.
  assert.ok(caliber.single.series);
  assert.equal(caliber.ytd.series, undefined);
});

test('buildSourceLine: prefers entry.source.agency_zh, flags revisions with ※', () => {
  const entry = makeEntry({ source: { agency_zh: '中国物流与采购联合会' }, revisions_recent: [{ period: '2026-04', measure: 'm' }] });
  const line = buildSourceLine(entry, 'single');
  assert.equal(line, '资料来源：中国物流与采购联合会 · 截至 2026 年 5 月 · 当月口径 ※ 历史数据已修订');
});
