// components/city-grid.mjs — the 70-city panel grid (楼市). Lazy-loaded on
// scroll into view (fetches site-data/panels/nbs-70city-price.json only
// then), sorted by latest MoM descending, plain <input> text filter, each
// mini: city name, MoM sparkline, latest value 红涨绿跌.
//
// Metric choice: the panel carries two metrics (new_home, resale_home) but
// VIZ-GUIDE rule 1 caps interactive controls at time-range + caliber toggle
// + this filter — there is no budget for a third (metric-switch) control.
// new_home is shown as the sparkline/sort key (matches nbs-70city-newhome-mom
// being the section's first tier-1 chart); resale_home is still surfaced as
// a second static value chip per card so the information isn't lost. Flagged
// for visual review in the build report.

import { h, clear, onIntersectOnce } from '../lib/dom.mjs';
import { upDownColor } from '../lib/color.mjs';
import { formatPercent } from '../lib/format.mjs';
import { buildSparklineSvg } from './sparkline.mjs';

const SPARK_WINDOW = 24; // last 24 months, matches the bundle's own tile spark length

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
  shell.append(filterInput, status, grid);
  container.appendChild(shell);

  let cities = [];

  onIntersectOnce(container, async () => {
    status.textContent = '正在加载 70 城数据…';
    try {
      const panel = await fetchPanel();
      cities = buildCityRows(panel, primaryMetric, secondaryMetric);
      status.remove();
      renderGrid(cities);
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
    const card = h('div', { class: 'city-mini' });
    card.appendChild(h('span', { class: 'city-name' }, c.city));
    card.appendChild(buildSparklineSvg(c.sparkValues, { color: upDownColor(c.latestPrimary) }));
    const values = h('span', { class: 'city-values' }, [
      h('span', { class: 'city-value', style: { color: upDownColor(c.latestPrimary) } }, `新房 ${formatPercent(c.latestPrimary)}`),
      h('span', { class: 'city-value', style: { color: upDownColor(c.latestSecondary) } }, `二手 ${formatPercent(c.latestSecondary)}`),
    ]);
    card.appendChild(values);
    return card;
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
