// components/sparkline.mjs — a tiny fixed-size trend line for compact rows
// (pulse-row) and grid minis (city-grid). No gridlines, no ticks, no axis —
// a sparkline earns its keep by being small and quiet (VIZ-GUIDE rule 9:
// "gray is the workhorse"). Fixed pixel size by design: a grid of 70 city
// minis re-measuring/redrawing on every resize would be 70 ResizeObservers
// for a decorative trend line — the outer grid still reflows responsively
// via CSS, only the sparkline's own internal geometry stays constant.

import { svgEl } from '../lib/dom.mjs';
import { extent, linearScale } from '../lib/scale.mjs';
import { linePathD } from '../lib/path.mjs';

export function buildSparklineSvg(values, { width = 64, height = 22, color = 'var(--context)' } = {}) {
  const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, class: 'sparkline', 'aria-hidden': 'true' });
  const clean = values.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (clean.length < 2) return svg;

  const ext = extent(values);
  const pad = 2;
  const xScale = linearScale([0, values.length - 1], [pad, width - pad]);
  const yScale = linearScale(ext[0] === ext[1] ? [ext[0] - 1, ext[1] + 1] : ext, [height - pad, pad]);

  const points = values.map((v, i) => ({ x: xScale(i), y: v === null || v === undefined ? null : yScale(v) }));
  const d = linePathD(points);
  if (d) {
    svg.appendChild(svgEl('path', { d, class: 'sparkline-path', style: { stroke: color } }));
  }
  return svg;
}
