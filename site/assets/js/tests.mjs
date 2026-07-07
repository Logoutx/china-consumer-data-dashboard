// tests.mjs — unit tests for this app's PURE functions, runnable with:
//   node --test site/assets/js/tests.mjs
//
// Scope, deliberately: only modules that never touch `document`/`window` at
// import time. store.mjs (touches `window.matchMedia`) and config.mjs (touches
// `location`) are guarded to no-op under Node (see their own files) so they
// COULD be imported here too, but the DOM-driving modules (app.mjs,
// components/line-chart.mjs, components/section.mjs, ...) call
// document.createElement etc. as soon as their mount functions run — those
// are exercised by the manual browser checklist in the build report instead,
// not here. This file covers exactly the "scale math, tick rounding,
// pangu-format helpers, path building" the task asked to extract and test.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { isCJK, needsPanguSpace, panguJoin, formatNumber, formatPercent, formatPP, formatDateZh } from './lib/format.mjs';
import { periodShape, periodOrdinal, withinRangeYears } from './lib/period.mjs';
import { extent, niceTicks, linearScale } from './lib/scale.mjs';
import { linePathD } from './lib/path.mjs';
import { upDownColor, upDownClass, agencyZhFromId } from './lib/color.mjs';
import { buildCityRows } from './components/city-grid.mjs';

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
