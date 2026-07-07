// components/section.mjs — the scaffold: section label -> Tier-1 charts
// (takeaway heading, dek, chart, source line) -> Tier-2 small-multiples ->
// Tier-3 pulse rows -> (property only) the 70-city grid. VIZ-GUIDE §Page
// anatomy / §Component kit.

import { h, clear } from '../lib/dom.mjs';
import { formatPercent, formatNumber, panguJoin } from '../lib/format.mjs';
import { agencyZhFromId } from '../lib/color.mjs';
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

    const tier1Wrap = h('div', { class: 'tier1-stack' });
    container.appendChild(tier1Wrap);
    for (const entry of tier1) mountTier1Chart(tier1Wrap, entry);

    if (tier2.length) {
      const heading = h('h3', { class: 'tier-heading' }, '分项走势');
      const tier2Wrap = h('div', { class: 'tier2-wrap' });
      container.append(heading, tier2Wrap);
      mountSmallMultiples(tier2Wrap, {
        caption: '同比 %，各图共用一套纵轴',
        panels: tier2.map((e) => entryToPanel(e)),
      });
    }

    if (tier3.length) {
      const heading = h('h3', { class: 'tier-heading' }, '更多指标');
      const tier3Wrap = h('div', { class: 'tier3-wrap' });
      container.append(heading, tier3Wrap);
      mountPulseList(tier3Wrap, tier3);
    }
  }

  // The 70-city grid is panel-sourced, independent of whether `property`'s
  // own catalog-backed series list is populated yet — always offer it.
  if (meta.id === 'property') {
    const cityHeading = h('h3', { class: 'tier-heading' }, '70 城明细');
    const cityWrap = h('div', { class: 'city-grid-wrap' });
    container.append(cityHeading, cityWrap);
    mountCityGrid(cityWrap, { fetchPanel: () => fetchPanel(PANEL_ID_70CITY) });
  }
}

// -- Tier 1 --------------------------------------------------------------

function mountTier1Chart(wrap, entry) {
  const article = h('article', { class: 'chart-block' });
  wrap.appendChild(article);

  if (!entry.latest) {
    article.append(
      h('p', { class: 'dek' }, `${entry.name_zh} · ${entry.unit_zh}`),
      h('p', { class: 'chart-empty-note' }, '该指标数据接入中'),
    );
    return;
  }

  const seriesLevelNote = (entry.annotations || []).find((a) => a.period === null);

  const h3 = h('h3', { class: 'chart-headline' }, entry.takeaway || entry.name_zh);
  const dek = h('p', { class: 'dek' }, `${entry.name_zh} · ${entry.unit_zh}`);
  article.append(h3, dek);
  if (seriesLevelNote) {
    article.appendChild(h('p', { class: 'series-note' }, seriesLevelNote.text_zh || seriesLevelNote.text || ''));
  }

  const mount = h('div', { class: 'chart-mount' });
  article.appendChild(mount);

  const sourceLine = h('p', { class: 'source-line' }, buildSourceLine(entry, entry.headline ? entry.headline.caliber : 'single'));
  article.appendChild(sourceLine);

  const { valuesForChart, isPercent, decimals } = primarySeriesValues(entry);
  const isDerived = (entry.flags_latest || []).includes('derived');

  mountLineChart(mount, {
    ariaLabel: entry.takeaway || `${entry.name_zh}：${entry.latest.period_label_zh}`,
    seriesList: [
      {
        id: entry.id,
        name: entry.name_zh,
        values: valuesForChart,
        derived: isDerived,
        colorVar: '--accent-red',
      },
    ],
    valueFormatter: (v) => (isPercent ? formatPercent(v, decimals) : formatNumber(v, decimals)),
    annotations: (entry.annotations || []).filter((a) => a.period !== null),
    breaks: entry.breaks || [],
    caliber: buildCaliberOption(entry),
    onCaliberChange: (key) => {
      sourceLine.textContent = buildSourceLine(entry, key);
    },
  });
}

/**
 * Which array (yoy vs level) carries the chart's story, per series shape.
 * `decimals` is null (infer natural precision) except for value_type "count"
 * (70-city up-city counts etc.), which is always a whole number — the
 * bundle carries no decimals hint at all (see format.mjs's naturalDecimals
 * doc comment), and natural-precision inference alone would print "38.0".
 */
function primarySeriesValues(entry) {
  const isCount = entry.value_type === 'count';
  const yoyHasData = !isCount && entry.yoy_series.some((p) => p.yoy !== null && p.yoy !== undefined);
  const decimals = isCount ? 0 : null;
  if (yoyHasData) {
    return { valuesForChart: entry.yoy_series.map((p) => ({ period: p.period, value: p.yoy })), isPercent: true, decimals };
  }
  return { valuesForChart: entry.level_series.map((p) => ({ period: p.period, value: p.m })), isPercent: false, decimals };
}

/**
 * Bundle gap: yoy_series/level_series are resolved to ONE caliber (whichever
 * pipeline/build.py's _resolve_caliber() picked — "single" if the series has
 * it) — there is no second time-series array for the other caliber to plot.
 * Where a series carries both calibers, the toggle instead full-replaces the
 * printed endpoint readout between the 当月 and 累计 numbers already present
 * in the bundle's `latest` measure block. See build report.
 */
function buildCaliberOption(entry) {
  if (!entry.calibers || !entry.calibers.includes('single') || !entry.calibers.includes('ytd')) return null;
  const latest = entry.latest;
  if (latest.m === undefined || latest.ytd === undefined) return null;
  return {
    single: {
      label: '当月',
      valueText: formatNumber(latest.m) + (entry.unit_zh ? ` ${entry.unit_zh}` : ''),
      yoyText: latest.m_yoy === undefined ? null : `同比 ${formatPercent(latest.m_yoy, null, { sign: true })}`,
    },
    ytd: {
      label: '累计',
      valueText: formatNumber(latest.ytd) + (entry.unit_zh ? ` ${entry.unit_zh}` : ''),
      yoyText: latest.ytd_yoy === undefined ? null : `累计同比 ${formatPercent(latest.ytd_yoy, null, { sign: true })}`,
    },
  };
}

function buildSourceLine(entry, caliberKey) {
  const agency = agencyZhFromId(entry.id);
  const caliberZh = caliberKey === 'ytd' ? '累计口径' : '当月口径';
  const period = entry.latest ? entry.latest.period_label_zh : '';
  const revised = (entry.revisions_recent || []).length > 0;
  const base = panguJoin('资料来源：', agency, ' · 截至 ', period, ' · ', caliberZh);
  return revised ? `${base} ※ 历史数据已修订` : base;
}

// -- Tier 2 ----------------------------------------------------------------

function entryToPanel(entry) {
  const { valuesForChart, isPercent, decimals } = primarySeriesValues(entry);
  return {
    id: entry.id,
    title: entry.name_zh,
    values: valuesForChart,
    isPercent,
    decimals,
    derived: (entry.flags_latest || []).includes('derived'),
  };
}
