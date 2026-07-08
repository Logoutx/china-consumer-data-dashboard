// components/section.mjs — the scaffold: section label -> Tier-1 charts
// (takeaway heading, dek, chart, source line) -> Tier-2 small-multiples ->
// Tier-3 pulse rows -> (property only) the 70-city grid. VIZ-GUIDE §Page
// anatomy / §Component kit.
//
// Design-review hardening (item 1): every entry that gets its own DOM
// subtree (each tier-1 chart; the tier-2/tier-3/city-grid blocks as a whole)
// is now wrapped via renderSafely() — one bad series can no longer take out
// the rest of the section, and every failure console.errors instead of
// failing silently. See lib/safe.mjs's module comment for the employment-
// bug root-cause investigation this responds to.
//
// Second design-review pass — unit-label bug: the dek used to always print
// entry.unit_zh (the catalog's LEVEL unit) regardless of which array
// primarySeriesValues() actually chose to plot. plottedUnitLabel() below
// derives the label from the SAME resolution the chart itself uses, so the
// two can never drift apart again by construction.

import { h, clear, onIntersectOnce } from '../lib/dom.mjs';
import { formatPercent, formatNumber, panguJoin } from '../lib/format.mjs';
import { agencyZhFromId } from '../lib/color.mjs';
import { renderSafely } from '../lib/safe.mjs';
import { mountLineChart } from './line-chart.mjs';
import { mountSmallMultiples } from './small-multiples.mjs';
import { mountPulseList } from './pulse-row.mjs';
import { mountCityGrid } from './city-grid.mjs';
import { fetchPanel } from '../data/loader.mjs';
import { PANEL_ID_70CITY } from '../config.mjs';

/** Render one section into `container` (already positioned in page flow, has id=section.id). */
export function renderSection(container, { meta, bundle }) {
  clear(container);
  container.appendChild(h('h2', { class: 'section-title', id: `${meta.id}-title` }, meta.name_zh));

  const series = bundle && Array.isArray(bundle.series) ? bundle.series : [];

  if (series.length === 0) {
    container.appendChild(h('p', { class: 'section-empty-note' }, '数据接入中 · 该板块序列正在补充'));
  } else {
    const tier1 = series.filter((s) => s.tier === 1);
    const tier2 = series.filter((s) => s.tier === 2);
    const tier3 = series.filter((s) => s.tier === 3);

    // Eager heroes (coordinator hardening): on a dedicated section page the
    // tier-1 charts ARE the page's whole point — render them immediately,
    // synchronously, no observer gate. Kills the blank-hero flash at first
    // paint. Tier-2/3 are genuinely below-the-fold extras, so THEY lazy-load
    // via onIntersectOnce, same as the 70-city panel below.
    if (tier1.length) {
      const tier1Wrap = h('div', { class: 'tier1-stack' });
      container.appendChild(tier1Wrap);
      for (const entry of tier1) {
        renderSafely(tier1Wrap, displayName(entry), () => buildTier1Chart(entry));
      }
    }

    if (tier2.length) {
      const heading = h('h3', { class: 'tier-heading' }, '分项走势');
      const tier2Wrap = h('div', { class: 'tier2-wrap' });
      container.append(heading, tier2Wrap);
      onIntersectOnce(tier2Wrap, () => {
        renderSafely(tier2Wrap, '分项走势', () => {
          const panels = tier2.map((e) => entryToPanel(e));
          mountSmallMultiples(tier2Wrap, { caption: sharedAxisCaption(panels), panels });
        });
      });
    }

    if (tier3.length) {
      const heading = h('h3', { class: 'tier-heading' }, '更多指标');
      const tier3Wrap = h('div', { class: 'tier3-wrap' });
      container.append(heading, tier3Wrap);
      onIntersectOnce(tier3Wrap, () => {
        renderSafely(tier3Wrap, '更多指标', () => mountPulseList(tier3Wrap, tier3));
      });
    }
  }

  // The 70-city grid is panel-sourced, independent of whether `property`'s
  // own catalog-backed series list is populated yet — always offer it.
  if (meta.id === 'property') {
    const cityHeading = h('h3', { class: 'tier-heading' }, '70 城明细');
    const cityWrap = h('div', { class: 'city-grid-wrap' });
    container.append(cityHeading, cityWrap);
    renderSafely(cityWrap, '70 城明细', () => mountCityGrid(cityWrap, { fetchPanel: () => fetchPanel(PANEL_ID_70CITY) }));
  }
}

// -- shared name/source/decimals feature-detection (item 6) ----------------

/** Prefer the bundle's own name_short (design-review item 6) in deks/panel titles/direct labels. */
export function displayName(entry) {
  return entry.name_short || entry.name_zh;
}

/** entry.source = {agency_zh, url} landed in the bundle after this site's first build; prefer it, fall back to the id-prefix guess. */
export function agencyFor(entry) {
  return (entry.source && entry.source.agency_zh) || agencyZhFromId(entry.id);
}

/**
 * entry.decimals landed in the bundle after this site's first build. Applies
 * only to the LEVEL measure (m/ytd) — the catalog's per-series precision was
 * designed for a series' primary value, and real YoY percents need their own
 * natural-precision inference regardless (e.g. a series declaring
 * decimals:0 for a whole-yuan level still prints m_yoy like 3.6, which would
 * wrongly round to "4%" if decimals:0 were applied there too).
 */
export function levelDecimals(entry) {
  return typeof entry.decimals === 'number' ? entry.decimals : null;
}

/**
 * PMI-style fallback (design-review item 6): when there's no real takeaway
 * sentence, the OLD behavior rendered the series name in BOTH the headline
 * and the dek ("{name}" then "{name} · {unit}") — a visible duplicate. Rule:
 * headline = name, dek = unit ONLY, never both. Pulled out as its own pure
 * function (rather than left inline in buildTier1Chart) specifically so this
 * exact decision is unit-testable without a DOM.
 */
export function resolveHeadlineAndDek(entry, unitLabel) {
  const name = displayName(entry);
  const hasTakeaway = !!entry.takeaway && entry.takeaway !== entry.name_zh && entry.takeaway !== name;
  return {
    headlineText: hasTakeaway ? entry.takeaway : name,
    dekText: hasTakeaway ? `${name} · ${unitLabel}` : unitLabel,
  };
}

/**
 * Second design-review bug, root cause: the dek printed entry.unit_zh (the
 * catalog's LEVEL unit, e.g. "亿元" for M1) unconditionally, even on renders
 * where primarySeriesValues() chose to plot yoy_series instead (a percent) —
 * "M1 · 亿元" next to a 同比 % line. Fix: derive the label from the SAME
 * `plottedKind` primarySeriesValues() already resolved, so the two can never
 * disagree again:
 *   - plottedKind "yoy"   -> "同比 %" (yoy_series chosen, OR a value_type
 *     "yoy_pct" series whose level_series field IS already a yoy number —
 *     nbs-fai, nbs-industrial-va; see pipeline/build.py's plot_kind, which
 *     flags exactly this case and confirms the client doesn't need to read
 *     plot_kind itself to get this right, per docs/OPERATIONS.md)
 *   - plottedKind "mom"   -> "环比 %" (level_series chosen for a value_type
 *     "mom_pct" series — DATA-CONTRACT §3.3: its `m` IS the 环比 value)
 *   - plottedKind "level" -> the catalog's own unit_zh (a genuine level,
 *     index, count, rate, or share — unit_zh is already correct for these)
 */
export function plottedUnitLabel(plottedKind, unitZh) {
  if (plottedKind === 'yoy') return '同比 %';
  if (plottedKind === 'mom') return '环比 %';
  return unitZh;
}

// -- Tier 1 --------------------------------------------------------------

function buildTier1Chart(entry) {
  const article = h('article', { class: 'chart-block' });

  if (!entry.latest) {
    article.append(h('p', { class: 'dek' }, entry.unit_zh), h('p', { class: 'chart-empty-note' }, '该指标数据接入中'));
    return article;
  }

  const name = displayName(entry);
  const seriesLevelNote = (entry.annotations || []).find((a) => a.period === null);
  const { valuesForChart, isPercent, decimals, plottedKind } = primarySeriesValues(entry);
  const unitLabel = plottedUnitLabel(plottedKind, entry.unit_zh);
  const { headlineText, dekText } = resolveHeadlineAndDek(entry, unitLabel);

  const h3 = h('h3', { class: 'chart-headline' }, headlineText);
  const dek = h('p', { class: 'dek' }, dekText);
  article.append(h3, dek);
  if (seriesLevelNote) {
    article.appendChild(h('p', { class: 'series-note' }, seriesLevelNote.text_zh || seriesLevelNote.text || ''));
  }

  const mount = h('div', { class: 'chart-mount' });
  article.appendChild(mount);

  const sourceLine = h('p', { class: 'source-line' }, buildSourceLine(entry, entry.headline ? entry.headline.caliber : 'single'));
  article.appendChild(sourceLine);

  const isDerived = (entry.flags_latest || []).includes('derived');

  mountLineChart(mount, {
    ariaLabel: entry.takeaway || `${name}（${unitLabel}）：${entry.latest.period_label_zh}`,
    seriesList: [
      {
        id: entry.id,
        name,
        values: valuesForChart,
        derived: isDerived,
        colorVar: '--accent-red',
        decimals,
      },
    ],
    valueFormatter: (v) => (isPercent ? formatPercent(v, decimals) : formatNumber(v, decimals)),
    isPercent,
    unitLabel,
    annotations: (entry.annotations || []).filter((a) => a.period !== null),
    breaks: entry.breaks || [],
    caliber: buildCaliberOption(entry),
    onCaliberChange: (key) => {
      sourceLine.textContent = buildSourceLine(entry, key);
    },
  });

  return article;
}

/**
 * Which array (yoy vs level) carries the chart's story, per series shape,
 * PLUS `plottedKind` ("yoy" | "mom" | "level") describing what that chosen
 * array actually represents — the single source of truth plottedUnitLabel()
 * and the chart's own tick "%" suffix both key off, so the label and the
 * plotted numbers can never disagree.
 *
 * `decimals` prefers the bundle's own entry.decimals (item 6 feature-detect)
 * for the LEVEL lane, when that lane is a genuine level (not a yoy_pct
 * series falling through to level_series — those want natural-precision
 * percent formatting, same as any other percent, not entry.decimals, which
 * describes the catalog's level-precision convention and doesn't apply).
 */
export function primarySeriesValues(entry) {
  const isCount = entry.value_type === 'count';
  const isInherentlyYoy = entry.value_type === 'yoy_pct'; // nbs-fai, nbs-industrial-va: the level/ytd field IS already a growth rate
  const isMom = entry.value_type === 'mom_pct'; // 70-city price entries: `m` IS the 环比 value
  const yoyHasData = !isCount && entry.yoy_series.some((p) => p.yoy !== null && p.yoy !== undefined);

  if (yoyHasData) {
    return {
      valuesForChart: entry.yoy_series.map((p) => ({ period: p.period, value: p.yoy })),
      isPercent: true,
      decimals: null,
      plottedKind: 'yoy',
    };
  }

  const decimals = isCount ? 0 : isInherentlyYoy ? null : levelDecimals(entry);
  return {
    valuesForChart: entry.level_series.map((p) => ({ period: p.period, value: p.m })),
    isPercent: isInherentlyYoy,
    decimals,
    plottedKind: isInherentlyYoy ? 'yoy' : isMom ? 'mom' : 'level',
  };
}

/**
 * Tier-2 small-multiples share ONE caption above the whole grid (VIZ-GUIDE
 * rule 11: "shared scale... stated once"). Design-review fix: it used to
 * hardcode "同比 %" regardless of what each panel actually resolved to —
 * correct for the common case (nearly every tier-2 series has yoy data) but
 * a real mismatch risk the moment one panel falls through to a level/mom
 * lane. Now derives the caption from what the panels actually share: one
 * label if they agree, a unit-free caption if they don't (never asserts a
 * unit that isn't true for every panel in the grid).
 */
function sharedAxisCaption(panels) {
  const labels = new Set(panels.map((p) => p.unitLabel));
  const shared = labels.size === 1 ? [...labels][0] : null;
  return shared ? `${shared}，各图共用一套纵轴` : '各图共用一套纵轴（各序列口径不同，见各图数值）';
}

/**
 * Bundle gap (as of this site's first build): yoy_series/level_series are
 * resolved to ONE caliber (whichever pipeline/build.py's _resolve_caliber()
 * picked — "single" if the series has it) — there is no second time-series
 * array for the other caliber to plot. Where a series carries both
 * calibers, the toggle full-replaces the printed endpoint readout between
 * the 当月 and 累计 numbers already present in the bundle's `latest` block.
 *
 * Design-review item 9 (feature-detected, unverified — see line-chart.mjs's
 * module comment): if the bundle starts additionally carrying
 * yoy_series_ytd/level_series_ytd (a guess at the field names, mirroring the
 * existing ytd/ytd_yoy observation-field convention), each caliber option
 * also gets a `series: {values, isPercent}` — line-chart.mjs then swaps the
 * whole plotted line + endpoint on toggle instead of just the readout text.
 */
export function buildCaliberOption(entry) {
  if (!entry.calibers || !entry.calibers.includes('single') || !entry.calibers.includes('ytd')) return null;
  const latest = entry.latest;
  if (latest.m === undefined || latest.ytd === undefined) return null;

  const singleSeries = seriesForCaliber(entry, 'single');
  const ytdSeries = seriesForCaliber(entry, 'ytd');

  return {
    single: {
      label: '当月',
      valueText: formatNumber(latest.m, levelDecimals(entry)) + (entry.unit_zh ? ` ${entry.unit_zh}` : ''),
      yoyText: latest.m_yoy === undefined ? null : `同比 ${formatPercent(latest.m_yoy, null, { sign: true })}`,
      ...(singleSeries ? { series: singleSeries } : {}),
    },
    ytd: {
      label: '累计',
      valueText: formatNumber(latest.ytd, levelDecimals(entry)) + (entry.unit_zh ? ` ${entry.unit_zh}` : ''),
      yoyText: latest.ytd_yoy === undefined ? null : `累计同比 ${formatPercent(latest.ytd_yoy, null, { sign: true })}`,
      ...(ytdSeries ? { series: ytdSeries } : {}),
    },
  };
}

/** Best-guess feature-detection for item 9's future per-caliber series arrays. Returns null when absent (today's reality). */
export function seriesForCaliber(entry, caliberKey) {
  const suffix = caliberKey === 'ytd' ? '_ytd' : '';
  const yoyKey = `yoy_series${suffix}`;
  const levelKey = `level_series${suffix}`;
  const yoyArr = entry[yoyKey];
  const levelArr = entry[levelKey];
  if (!Array.isArray(yoyArr) && !Array.isArray(levelArr)) return null;
  if (Array.isArray(yoyArr) && yoyArr.some((p) => p.yoy !== null && p.yoy !== undefined)) {
    return { values: yoyArr.map((p) => ({ period: p.period, value: p.yoy })), isPercent: true };
  }
  if (Array.isArray(levelArr)) {
    return { values: levelArr.map((p) => ({ period: p.period, value: p.m })), isPercent: false };
  }
  return null;
}

export function buildSourceLine(entry, caliberKey) {
  const agency = agencyFor(entry);
  const caliberZh = caliberKey === 'ytd' ? '累计口径' : '当月口径';
  const period = entry.latest ? entry.latest.period_label_zh : '';
  const revised = (entry.revisions_recent || []).length > 0;
  const base = panguJoin('资料来源：', agency, ' · 截至 ', period, ' · ', caliberZh);
  return revised ? `${base} ※ 历史数据已修订` : base;
}

// -- Tier 2 ----------------------------------------------------------------

function entryToPanel(entry) {
  const { valuesForChart, isPercent, decimals, plottedKind } = primarySeriesValues(entry);
  return {
    id: entry.id,
    title: displayName(entry),
    values: valuesForChart,
    isPercent,
    decimals,
    unitLabel: plottedUnitLabel(plottedKind, entry.unit_zh),
    derived: (entry.flags_latest || []).includes('derived'),
  };
}
