// components/small-multiples.mjs — Tier-2 "tighter layout": a grid of small
// panels sharing ONE scale (VIZ-GUIDE rule 11), the panel title standing in
// for a legend (rule 8: "no legends... panel title is the label"), endpoint
// value colored 红涨绿跌.

import { h, clear } from '../lib/dom.mjs';
import { extent, niceTicks, linearScale } from '../lib/scale.mjs';
import { linePathD } from '../lib/path.mjs';
import { periodOrdinal, withinRangeYears } from '../lib/period.mjs';
import { upDownColor } from '../lib/color.mjs';
import { formatPercent, formatNumber } from '../lib/format.mjs';
import { getRangeYears, onRangeChange, onThemeChange } from '../store.mjs';
import { svgEl } from '../lib/dom.mjs';

const PANEL_HEIGHT = 88;

/**
 * @param panels [{ id, title, values:[{period,value}], derived, isPercent }]
 */
export function mountSmallMultiples(container, { panels, caption }) {
  clear(container);
  const shell = h('div', { class: 'small-multiples' });
  if (caption) shell.appendChild(h('p', { class: 'small-multiples-caption' }, caption));
  const grid = h('div', { class: 'small-multiples-grid' });
  shell.appendChild(grid);
  container.appendChild(shell);

  function render() {
    clear(grid);
    const withVisible = panels.map((p) => ({ ...p, pts: visible(p.values) }));
    const allVals = withVisible.flatMap((p) => p.pts.map((pt) => pt.value)).filter((v) => v !== null && v !== undefined);
    if (!allVals.length) {
      grid.appendChild(h('p', { class: 'chart-empty-note' }, '数据接入中'));
      return;
    }
    const { domain } = niceTicks(...extent(allVals), 4);

    const sorted = [...withVisible].sort((a, b) => latestOf(b) - latestOf(a));
    for (const p of sorted) grid.appendChild(buildPanel(p, domain));
  }

  function visible(values) {
    if (!values.length) return [];
    const withData = values.filter((v) => v.value !== null && v.value !== undefined);
    const latestPeriod = withData.length ? withData[withData.length - 1].period : values[values.length - 1].period;
    return values.filter((v) => withinRangeYears(v.period, latestPeriod, getRangeYears()));
  }

  function latestOf(p) {
    const last = [...p.pts].reverse().find((pt) => pt.value !== null && pt.value !== undefined);
    return last ? last.value : -Infinity;
  }

  function buildPanel(p, domain) {
    const card = h('div', { class: 'sm-panel' });
    card.appendChild(h('p', { class: 'sm-panel-title' }, p.title));
    const surface = h('div', { class: 'sm-panel-surface' });
    card.appendChild(surface);
    const last = [...p.pts].reverse().find((pt) => pt.value !== null && pt.value !== undefined);
    const decimals = p.decimals ?? null;
    const valueText = last ? (p.isPercent ? formatPercent(last.value, decimals) : formatNumber(last.value, decimals)) : '—';
    card.appendChild(h('p', { class: 'sm-panel-value', style: { color: upDownColor(last ? last.value : null) } }, valueText));

    if (p.pts.length) {
      // Fixed abstract viewBox (260x88); preserveAspectRatio="none" lets the
      // SVG stretch to whatever width CSS Grid actually gives the panel.
      // Safe here because these panels draw pure geometry only (no <text>) —
      // rule 13's "never scale text via viewBox" doesn't apply to a line +
      // a dot, and no ResizeObserver is needed since nothing is measured.
      drawPanelSvg(surface, p, domain, 260);
    }
    return card;
  }

  function drawPanelSvg(surface, p, domain, width) {
    clear(surface);
    const height = PANEL_HEIGHT;
    const svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: 'none',
      class: 'chart-svg',
      'aria-hidden': 'true',
    });
    surface.appendChild(svg);
    const ordinals = p.pts.map((pt) => periodOrdinal(pt.period));
    const xScale = linearScale([Math.min(...ordinals), Math.max(...ordinals)], [4, width - 4]);
    const yScale = linearScale(domain, [height - 6, 6]);

    const zeroInDomain = domain[0] < 0 && domain[1] > 0;
    if (zeroInDomain) {
      const y = yScale(0);
      svg.appendChild(svgEl('line', { x1: 0, x2: width, y1: y, y2: y, class: 'grid-line grid-line--zero' }));
    }
    const points = p.pts.map((pt) => ({ x: xScale(periodOrdinal(pt.period)), y: pt.value }));
    const d = linePathD(points);
    if (d) {
      svg.appendChild(
        svgEl('path', { d, class: p.derived ? 'series-line series-line--derived' : 'series-line', style: { stroke: 'var(--context)' } }),
      );
    }
    const last = [...p.pts].reverse().find((pt) => pt.value !== null && pt.value !== undefined);
    if (last) {
      svg.appendChild(
        svgEl('circle', {
          cx: xScale(periodOrdinal(last.period)),
          cy: yScale(last.value),
          r: 2.5,
          class: 'endpoint-dot',
          style: { fill: upDownColor(last.value) },
        }),
      );
    }
  }

  render();
  onRangeChange(() => render());
  onThemeChange(() => render());
}
