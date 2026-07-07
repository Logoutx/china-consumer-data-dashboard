// components/chart-core.mjs — shared rendering primitives for every chart in
// the kit (line-chart, small-multiples, city-grid, pulse-row).
//
// Split by medium, per VIZ-GUIDE rule 13 ("chart text is real text... fixed
// px font sizes... resize must not shrink labels"): the <svg> draws ONLY
// geometry (gridlines, line paths, dots) sized to the container's measured
// CSS-pixel width/height with a 1:1 viewBox (so nothing in the SVG is ever
// scaled — a "20" coordinate is 20 real px). All text — tick values,
// endpoint readouts, annotation leaders, break notes — is plain HTML in an
// absolutely-positioned overlay <div> sized to match, positioned with the
// exact same scales used to draw the SVG. This guarantees text is real,
// selectable, ≥12px, and never shrinks under a viewBox transform, while
// keeping the geometry code (which DOES want a coordinate system) simple.

import { svgEl, h, clear } from '../lib/dom.mjs';
import { niceTicks, linearScale } from '../lib/scale.mjs';
import { linePathD } from '../lib/path.mjs';

/** Build the svg+overlay pair inside `container`. Clears it first. */
export function mountSurface(container, { height, className = '' }) {
  clear(container);
  const wrap = h('div', { class: `chart-surface ${className}`.trim() });
  wrap.style.height = `${height}px`;
  const svg = svgEl('svg', { class: 'chart-svg', 'aria-hidden': 'true' });
  const overlay = h('div', { class: 'chart-overlay', 'aria-hidden': 'true' });
  wrap.appendChild(svg);
  wrap.appendChild(overlay);
  container.appendChild(wrap);
  return { wrap, svg, overlay };
}

/**
 * Compute x/y scales for a chart area given measured pixel size, the ordinal
 * x-domain, and the combined value extent. Also emits the y niceTicks so
 * gridlines + drawn values agree exactly (same domain either result gives).
 */
export function computeScales({ width, height, xDomain, yExtent, padding }) {
  const pad = { top: 10, right: 8, bottom: 8, left: 44, ...padding };
  const { ticks, domain: yDomain, step } = niceTicks(yExtent[0], yExtent[1], 4);
  const xScale = linearScale(xDomain, [pad.left, Math.max(pad.left + 1, width - pad.right)]);
  const yScale = linearScale(yDomain, [Math.max(pad.top + 1, height - pad.bottom), pad.top]);
  return { xScale, yScale, yTicks: ticks, yDomain, yStep: step, pad };
}

/** Draw horizontal gridlines (VIZ-GUIDE rule 10): round steps, 1px, zero line strong. */
export function drawGridlines(svg, { width, yScale, yTicks, pad, showLabels = true, formatTick, overlay }) {
  for (const tick of yTicks) {
    const y = yScale(tick);
    const isZero = Math.abs(tick) < 1e-9;
    svg.appendChild(
      svgEl('line', {
        x1: pad.left,
        x2: width - pad.right,
        y1: round(y),
        y2: round(y),
        class: isZero ? 'grid-line grid-line--zero' : 'grid-line',
      }),
    );
    if (showLabels && overlay && formatTick) {
      const label = h('span', { class: 'tick-label', style: { top: `${y}px` } }, formatTick(tick));
      overlay.appendChild(label);
    }
  }
}

/** Draw one series' line (+ optional dashed style for derived values). */
export function drawLine(svg, { points, xScale, yScale, color, dashed = false }) {
  const scaled = points.map((p) => ({ x: xScale(p.x), y: p.y === null || p.y === undefined ? null : yScale(p.y) }));
  const d = linePathD(scaled);
  if (!d) return null;
  return svgEl('path', {
    d,
    class: dashed ? 'series-line series-line--derived' : 'series-line',
    style: { stroke: color },
  });
}

/** Endpoint dot at the last non-null point. */
export function drawEndpointDot(svg, { x, y, color }) {
  return svgEl('circle', { cx: round(x), cy: round(y), r: 3, class: 'endpoint-dot', style: { fill: color } });
}

/** A thin vertical leader line (annotation pin / break marker seam). */
export function drawLeader(svg, { x, top, bottom, strong = false }) {
  return svgEl('line', {
    x1: round(x),
    x2: round(x),
    y1: round(top),
    y2: round(bottom),
    class: strong ? 'leader-line leader-line--break' : 'leader-line',
  });
}

/** Position an HTML label in the overlay at a given pixel point. */
export function placeLabel(overlay, { x, y, text, className = '', anchor = 'start' }) {
  const span = h('span', { class: `chart-label ${className}`.trim() }, text);
  span.style.left = `${x}px`;
  span.style.top = `${y}px`;
  if (anchor === 'end') span.style.transform = 'translate(-100%, -50%)';
  else if (anchor === 'middle') span.style.transform = 'translate(-50%, 0)';
  else span.style.transform = 'translate(0, -50%)';
  overlay.appendChild(span);
  return span;
}

function round(n) {
  return Math.round(n * 100) / 100;
}
