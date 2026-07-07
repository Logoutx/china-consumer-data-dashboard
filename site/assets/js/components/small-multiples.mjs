// components/small-multiples.mjs — Tier-2 "tighter layout": a grid of small
// panels sharing ONE scale (VIZ-GUIDE rule 11), the panel title standing in
// for a legend (rule 8: "no legends... panel title is the label"), endpoint
// value colored 红涨绿跌.
//
// Design-review fix (item 7): the value used to be a separate <p> BELOW the
// svg box, in plain block flow — with panels of differing data amplitude
// (inherent to a shared scale, rule 11) that read as "line crammed at the
// top with a large void, value floating at bottom-left" once the box wasn't
// full. Fixed: the value now lives INSIDE the surface, absolutely positioned
// at a consistent top-right corner (same physical spot on every panel
// regardless of that panel's own line position), and the surface uses a
// fixed CSS aspect-ratio matching its svg viewBox exactly (no stretch
// distortion). Title now wraps to 2 lines instead of ellipsis-truncating
// (also item 7), and prefers name_short (item 6) via section.mjs's
// entryToPanel.

import { h, clear } from '../lib/dom.mjs';
import { extent, niceTicks, linearScale } from '../lib/scale.mjs';
import { linePathD } from '../lib/path.mjs';
import { periodOrdinal, withinRangeYears } from '../lib/period.mjs';
import { upDownColor } from '../lib/color.mjs';
import { formatPercent, formatNumber } from '../lib/format.mjs';
import { getRangeYears, onRangeChange, onThemeChange } from '../store.mjs';
import { svgEl } from '../lib/dom.mjs';

const PANEL_WIDTH = 260; // abstract viewBox units, matches --sm-panel-aspect in main.css
const PANEL_HEIGHT = 88;

/**
 * @param panels [{ id, title, values:[{period,value}], derived, isPercent, decimals }]
 */
export function mountSmallMultiples(container, { panels, caption }) {
  clear(container);
  const shell = h('div', { class: 'small-multiples' });
  if (caption) shell.appendChild(h('p', { class: 'small-multiples-caption' }, caption));
  const grid = h('div', { class: 'small-multiples-grid' });
  shell.appendChild(grid);
  container.appendChild(shell);

  function render() {
    try {
      renderUnsafe();
    } catch (err) {
      console.error('[small-multiples] render failed:', err);
      clear(grid);
      grid.appendChild(h('p', { class: 'render-error-note' }, '该板块图表渲染失败，请刷新重试。'));
    }
  }

  function renderUnsafe() {
    clear(grid);
    const withVisible = panels.map((p) => ({ ...p, pts: visible(p.values) }));
    const allVals = withVisible.flatMap((p) => p.pts.map((pt) => pt.value)).filter((v) => v !== null && v !== undefined);
    if (!allVals.length) {
      grid.appendChild(h('p', { class: 'chart-empty-note' }, '数据接入中'));
      return;
    }
    const { domain } = niceTicks(...extent(allVals), 4);

    const sorted = [...withVisible].sort((a, b) => latestOf(b) - latestOf(a));
    for (const p of sorted) {
      // Design-review item 1: one malformed panel must not blank the whole
      // grid — isolate per panel, console.error, inline fallback card.
      try {
        grid.appendChild(buildPanel(p, domain));
      } catch (err) {
        console.error(`[small-multiples] panel failed for ${p.id}:`, err);
        grid.appendChild(buildErrorPanel(p));
      }
    }
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

  function buildErrorPanel(p) {
    const card = h('div', { class: 'sm-panel sm-panel--error' });
    card.appendChild(h('p', { class: 'sm-panel-title' }, p.title || p.id));
    card.appendChild(h('p', { class: 'render-error-note' }, '该序列渲染失败'));
    return card;
  }

  function buildPanel(p, domain) {
    const card = h('div', { class: 'sm-panel' });
    card.appendChild(h('p', { class: 'sm-panel-title' }, p.title));
    const surface = h('div', { class: 'sm-panel-surface' });
    card.appendChild(surface);

    const last = [...p.pts].reverse().find((pt) => pt.value !== null && pt.value !== undefined);
    const decimals = p.decimals ?? null;
    const valueText = last ? (p.isPercent ? formatPercent(last.value, decimals) : formatNumber(last.value, decimals)) : '—';
    const valueLabel = h('p', { class: 'sm-panel-value', style: { color: upDownColor(last ? last.value : null) } }, valueText);
    surface.appendChild(valueLabel); // absolutely positioned by CSS, top-right of the surface — consistent baseline across all panels

    if (p.pts.length) {
      // Fixed abstract viewBox (260x88), CSS aspect-ratio on .sm-panel-surface
      // matches it exactly so no stretch distortion is needed; kept
      // preserveAspectRatio="none" as a defensive belt-and-suspenders for any
      // sub-pixel rounding mismatch. Safe to skip a ResizeObserver here (no
      // <text> inside — rule 13's "never scale text via viewBox" doesn't
      // apply to a line + a dot).
      drawPanelSvg(surface, p, domain, PANEL_WIDTH);
    }
    return card;
  }

  function drawPanelSvg(surface, p, domain, width) {
    const height = PANEL_HEIGHT;
    const svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: 'none',
      class: 'chart-svg',
      'aria-hidden': 'true',
    });
    surface.insertBefore(svg, surface.firstChild); // svg fills the box; value label (already appended) stays on top
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
