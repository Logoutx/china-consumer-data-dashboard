// components/city-grid.mjs — the 70-city panel grid (楼市). Lazy-loaded on
// scroll into view (fetches site-data/panels/nbs-70city-price.json only
// then), sorted by latest MoM descending, plain <input> text filter, each
// mini: city name, MoM sparkline, latest value 红涨绿跌.
//
// Depth restore (owner: "keep the specific data dimensions I ordered to
// build earlier, especially the 70 cities data"), porting the old root
// app.js's exact city-depth features:
//  - Click a city -> an inline detail expand: 新房+二手房, 环比 and 同比,
//    full history (2011->), reusing line-chart.mjs's 2-series direct labels
//    + the new hover tooltip. Sanctioned interactivity per the owner
//    ("city expand and filter are the sanctioned lookups") — not counted
//    against VIZ-GUIDE rule 1's two-control budget.
//  - A grouped view: per-group (北上广深 / 省会和新一线 / 其他城市) average
//    lines, computed client-side from the panel's own cells over
//    lib/city-groups.mjs's exact ported group lists, as two small charts
//    side by side (新房 groups, 二手房 groups) — chosen over reusing the
//    caliber-toggle mechanism because it adds ZERO new interactive
//    controls (more restrained, stays inside the two-controls rule).
//
// Metric choice for the MINI grid specifically: the panel carries two
// metrics (new_home, resale_home) but VIZ-GUIDE rule 1 caps interactive
// controls — new_home is shown as the sparkline/sort key (matches
// nbs-70city-newhome-mom being the section's first tier-1 chart);
// resale_home is still surfaced as a second static value chip per card.

import { h, clear, onIntersectOnce } from '../lib/dom.mjs';
import { upDownColor } from '../lib/color.mjs';
import { formatPercent } from '../lib/format.mjs';
import { buildSparklineSvg } from './sparkline.mjs';
import { mountLineChart } from './line-chart.mjs';
import { cityGroups } from '../lib/city-groups.mjs';

const SPARK_WINDOW = 24; // last 24 months, matches the bundle's own tile spark length
const GROUP_COLORS = ['--accent-red', '--accent-blue', '--context'];

export function mountCityGrid(container, { fetchPanel, primaryMetric = 'new_home', secondaryMetric = 'resale_home' }) {
  clear(container);
  const shell = h('div', { class: 'city-grid-shell' });
  const filterInput = h('input', {
    type: 'text',
    class: 'city-filter',
    placeholder: '筛选城市…',
    'aria-label': '筛选城市',
  });
  const status = h('p', { class: 'chart-empty-note' }, '滚动到此处以加载 70 城数据…');
  const grid = h('div', { class: 'city-grid' });
  const detail = h('div', { class: 'city-detail' });
  detail.style.display = 'none';
  const groupView = h('div', { class: 'city-group-view' });
  shell.append(filterInput, status, grid, detail, groupView);
  container.appendChild(shell);

  let cities = [];
  let panelData = null;
  let expandedCity = null;

  onIntersectOnce(container, async () => {
    status.textContent = '正在加载 70 城数据…';
    try {
      const panel = await fetchPanel();
      panelData = panel;
      cities = buildCityRows(panel, primaryMetric, secondaryMetric);
      status.remove();
      renderGrid(cities);
      renderGroupViewSafely(panel);
      filterInput.addEventListener('input', () => {
        const q = filterInput.value.trim();
        renderGrid(q ? cities.filter((c) => c.city.includes(q)) : cities);
      });
    } catch (err) {
      // Design-review item 1: was silently swallowed (no console.error) —
      // exactly the "cost me a debugging round" complaint from review.
      console.error('[city-grid] panel fetch/build failed:', err);
      status.textContent = '70 城数据加载失败，请刷新重试';
    }
  });

  function renderGrid(list) {
    clear(grid);
    const sorted = [...list].sort((a, b) => (b.latestPrimary ?? -Infinity) - (a.latestPrimary ?? -Infinity));
    if (!sorted.length) {
      grid.appendChild(h('p', { class: 'chart-empty-note' }, '没有匹配的城市'));
      return;
    }
    for (const c of sorted) {
      // Design-review item 1: one malformed city row must not blank the grid.
      try {
        grid.appendChild(buildMini(c));
      } catch (err) {
        console.error(`[city-grid] mini failed for ${c.city}:`, err);
        grid.appendChild(h('p', { class: 'render-error-note' }, `${c.city}：渲染失败`));
      }
    }
  }

  function buildMini(c) {
    const card = h('button', { type: 'button', class: 'city-mini', 'aria-expanded': expandedCity === c.city ? 'true' : 'false' });
    card.appendChild(h('span', { class: 'city-name' }, c.city));
    card.appendChild(buildSparklineSvg(c.sparkValues, { color: upDownColor(c.latestPrimary) }));
    const values = h('span', { class: 'city-values' }, [
      h('span', { class: 'city-value', style: { color: upDownColor(c.latestPrimary) } }, `新房 ${formatPercent(c.latestPrimary)}`),
      h('span', { class: 'city-value', style: { color: upDownColor(c.latestSecondary) } }, `二手 ${formatPercent(c.latestSecondary)}`),
    ]);
    card.appendChild(values);
    card.addEventListener('click', () => toggleCity(c.city));
    return card;
  }

  function toggleCity(city) {
    if (expandedCity === city) {
      expandedCity = null;
      detail.style.display = 'none';
      clear(detail);
      renderGrid(cities); // refresh aria-expanded state
      return;
    }
    expandedCity = city;
    // BUG FIX: must be visible BEFORE mounting the charts, not after —
    // mountLineChart measures its container's width synchronously at mount
    // time, and a display:none ancestor forces that measurement to 0
    // (a display:none subtree has no box at all). Rendering into a still-
    // hidden `detail` was exactly the "momentarily 0-width container" class
    // of bug, just self-inflicted via ordering rather than a timing race.
    detail.style.display = '';
    try {
      renderCityDetail(detail, panelData, city, primaryMetric, secondaryMetric);
    } catch (err) {
      console.error(`[city-grid] detail failed for ${city}:`, err);
      clear(detail);
      detail.appendChild(h('p', { class: 'render-error-note' }, `${city}：详情渲染失败`));
    }
    renderGrid(cities); // refresh aria-expanded state
  }

  function renderGroupViewSafely(panel) {
    try {
      renderGroupView(groupView, panel, primaryMetric, secondaryMetric);
    } catch (err) {
      console.error('[city-grid] group view failed:', err);
      clear(groupView);
      groupView.appendChild(h('p', { class: 'render-error-note' }, '分组走势渲染失败'));
    }
  }

  return {
    /** For tests / debugging: expose the built rows without touching the DOM. */
    _debugRows: () => cities,
  };
}

/** Pure-ish transform: panel bundle -> per-city rows. Exported for testing. */
export function buildCityRows(panel, primaryMetric, secondaryMetric) {
  const cities = panel.dimensions.city;
  const latestBy = panel.latest_by_city || {};
  return cities.map((city) => {
    const primarySeries = (panel.cells[city]?.[primaryMetric]?.m || []).slice(-SPARK_WINDOW);
    return {
      city,
      sparkValues: primarySeries,
      latestPrimary: latestBy[city]?.[primaryMetric]?.m ?? null,
      latestSecondary: latestBy[city]?.[secondaryMetric]?.m ?? null,
    };
  });
}

/** Full-history {period,value} series for one city+metric+measure. Exported for testing. */
export function cityMeasureSeries(panel, city, metric, measure) {
  const periods = panel.periods || [];
  const arr = (panel.cells[city] && panel.cells[city][metric] && panel.cells[city][metric][measure]) || [];
  return periods.map((period, i) => ({ period, value: arr[i] ?? null }));
}

/**
 * Full-history {period,value} series for a group's simple average of one
 * metric+measure, skipping missing cells per period (mirrors
 * pipeline/build.py's own simple_mean_of_cities rule, computed client-side
 * here per the owner's instruction). Exported for testing.
 */
export function groupAverageSeries(panel, group, metric, measure) {
  const periods = panel.periods || [];
  return periods.map((period, i) => {
    const vals = group.cities
      .map((city) => panel.cells[city] && panel.cells[city][metric] && panel.cells[city][metric][measure] && panel.cells[city][metric][measure][i])
      .filter((v) => v !== null && v !== undefined);
    const value = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    return { period, value };
  });
}

function renderCityDetail(container, panel, city, primaryMetric, secondaryMetric) {
  clear(container);
  container.appendChild(h('h4', { class: 'city-detail-title' }, `${city} · 新房与二手房价格`));
  const grid2 = h('div', { class: 'city-detail-grid' });
  container.appendChild(grid2);

  const momMount = h('div', {});
  const yoyMount = h('div', {});
  grid2.append(
    h('div', { class: 'city-detail-chart' }, [h('p', { class: 'dek' }, '环比 %'), momMount]),
    h('div', { class: 'city-detail-chart' }, [h('p', { class: 'dek' }, '同比 %'), yoyMount]),
  );

  const seriesFor = (measure) => [
    {
      id: `${city}-${primaryMetric}`,
      name: '新房',
      values: cityMeasureSeries(panel, city, primaryMetric, measure),
      colorVar: '--accent-red',
      isPercent: true,
      decimals: null,
    },
    {
      id: `${city}-${secondaryMetric}`,
      name: '二手房',
      values: cityMeasureSeries(panel, city, secondaryMetric, measure),
      colorVar: '--accent-blue',
      isPercent: true,
      decimals: null,
    },
  ];

  mountLineChart(momMount, {
    ariaLabel: `${city} 新房与二手房价格环比，2011 年至今`,
    seriesList: seriesFor('m'),
    valueFormatter: (v) => formatPercent(v),
    isPercent: true,
    ignoreRange: true,
    height: 220,
  });
  mountLineChart(yoyMount, {
    ariaLabel: `${city} 新房与二手房价格同比，2011 年至今`,
    seriesList: seriesFor('m_yoy'),
    valueFormatter: (v) => formatPercent(v),
    isPercent: true,
    ignoreRange: true,
    height: 220,
  });
}

function renderGroupView(container, panel, primaryMetric, secondaryMetric) {
  clear(container);
  const groups = cityGroups(panel.dimensions.city || []);
  if (!groups.length) return;

  container.appendChild(h('h3', { class: 'tier-heading' }, '70 城分组走势（环比，简单平均）'));
  const grid2 = h('div', { class: 'city-detail-grid' });
  container.appendChild(grid2);

  const seriesForMetric = (metric) =>
    groups.map((g, i) => ({
      id: g.key,
      name: g.label,
      values: groupAverageSeries(panel, g, metric, 'm'),
      colorVar: GROUP_COLORS[i % GROUP_COLORS.length],
      isPercent: true,
      decimals: null,
    }));

  const newHomeMount = h('div', {});
  const resaleMount = h('div', {});
  grid2.append(
    h('div', { class: 'city-detail-chart' }, [h('p', { class: 'dek' }, '新房价格 · 环比 %'), newHomeMount]),
    h('div', { class: 'city-detail-chart' }, [h('p', { class: 'dek' }, '二手房价格 · 环比 %'), resaleMount]),
  );

  mountLineChart(newHomeMount, {
    ariaLabel: '70 城分组新房价格环比走势，2011 年至今',
    seriesList: seriesForMetric(primaryMetric),
    valueFormatter: (v) => formatPercent(v),
    isPercent: true,
    ignoreRange: true,
    height: 220,
  });
  mountLineChart(resaleMount, {
    ariaLabel: '70 城分组二手房价格环比走势，2011 年至今',
    seriesList: seriesForMetric(secondaryMetric),
    valueFormatter: (v) => formatPercent(v),
    isPercent: true,
    ignoreRange: true,
    height: 220,
  });
}
