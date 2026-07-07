// components/line-chart.mjs — the Tier-1 chart: 1-2 series, direct end
// labels, endpoint dot + printed value, annotations as thin-leader labels,
// break markers, dashed-derived lines. VIZ-GUIDE rules 2/4/5/6/8/10/13.
//
// Endpoint readout: the bundle's yoy_series/level_series is a single array
// resolved to ONE caliber (build.py's _resolve_caliber picks "single" when a
// series has it, else "ytd" — see pipeline/build.py docstring) — there is no
// second time-series array for the other caliber, so the LINE never
// changes on toggle. What toggles is the printed endpoint readout: full
// text-replace between the "当月" and "累计" numbers already present in the
// bundle's `latest`/`prev` measure blocks. Flagged in the build report.

import { clear, onResize, h } from '../lib/dom.mjs';
import { extent } from '../lib/scale.mjs';
import { periodOrdinal, withinRangeYears } from '../lib/period.mjs';
import { getRangeYears, onRangeChange, onThemeChange } from '../store.mjs';
import {
  mountSurface,
  computeScales,
  drawGridlines,
  drawLine,
  drawEndpointDot,
  drawLeader,
  placeLabel,
} from './chart-core.mjs';

/**
 * @param container HTMLElement
 * @param props {
 *   ariaLabel: string,
 *   seriesList: [{ id, name, values:[{period,value}], derived, colorVar }],
 *   valueFormatter: (n) => string,   // tick + endpoint value formatting
 *   annotations: [{period, text}],   // period-anchored only (series-level notes render elsewhere)
 *   breaks: [{effective, note}],
 *   caliber: null | { single:{label,valueText,yoyText}, ytd:{label,valueText,yoyText} },
 *   height?: number,
 * }
 */
export function mountLineChart(container, props) {
  const { ariaLabel, seriesList, valueFormatter, annotations = [], breaks = [], caliber = null, height = 260, onCaliberChange } = props;

  let activeCaliber = 'single';
  let disposeResize = null;

  function visiblePoints(values) {
    if (!values.length) return [];
    const withData = values.filter((v) => v.value !== null && v.value !== undefined);
    const latestPeriod = withData.length ? withData[withData.length - 1].period : values[values.length - 1].period;
    const years = getRangeYears();
    return values.filter((v) => withinRangeYears(v.period, latestPeriod, years));
  }

  function render(width) {
    if (!width) return;
    const visSeries = seriesList.map((s) => ({ ...s, pts: visiblePoints(s.values) }));
    const hasData = visSeries.some((s) => s.pts.some((p) => p.value !== null && p.value !== undefined));

    clear(container);
    if (!hasData) {
      container.appendChild(h('p', { class: 'chart-empty-note' }, '该指标数据接入中'));
      return;
    }

    const multiSeries = visSeries.length > 1;
    const padRight = multiSeries ? 108 : 68;
    const { svg, overlay, wrap } = mountSurface(container, { height });
    wrap.setAttribute('role', 'img');
    if (ariaLabel) wrap.setAttribute('aria-label', ariaLabel);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    const allOrdinals = visSeries.flatMap((s) => s.pts.map((p) => periodOrdinal(p.period)));
    const xDomain = [Math.min(...allOrdinals), Math.max(...allOrdinals)];
    const yVals = visSeries.flatMap((s) => s.pts.map((p) => p.value)).filter((v) => v !== null && v !== undefined);
    const yExtent = extent(yVals) || [0, 1];

    const { xScale, yScale, yTicks, pad } = computeScales({
      width,
      height,
      xDomain,
      yExtent,
      padding: { left: 44, right: padRight, top: 14, bottom: 8 },
    });

    drawGridlines(svg, { width, yScale, yTicks, pad, overlay, formatTick: valueFormatter });

    for (const brk of breaks) {
      if (!brk.effective) continue;
      const x = xScale(periodOrdinal(brk.effective));
      if (x < pad.left - 1 || x > width - pad.right + 1) continue;
      svg.appendChild(drawLeader(svg, { x, top: pad.top, bottom: height - pad.bottom, strong: true }));
      placeLabel(overlay, { x: Math.min(x + 5, width - padRight), y: pad.top, text: brk.note_zh || brk.note_en || '口径调整', className: 'break-label' });
    }

    for (const ann of annotations) {
      if (!ann.period) continue;
      const x = xScale(periodOrdinal(ann.period));
      if (x < pad.left - 1 || x > width - pad.right + 1) continue;
      svg.appendChild(drawLeader(svg, { x, top: pad.top, bottom: height - pad.bottom }));
      placeLabel(overlay, {
        x: Math.min(x + 5, width - padRight),
        y: pad.top + 16,
        text: ann.text_zh || ann.text || '',
        className: 'annotation-label',
      });
    }

    visSeries.forEach((s, i) => {
      const color = `var(${s.colorVar || '--context'})`;
      const pathPoints = s.pts.map((p) => ({ x: periodOrdinal(p.period), y: p.value }));
      const line = drawLine(svg, { points: pathPoints, xScale, yScale, color, dashed: s.derived });
      if (line) svg.appendChild(line);

      const lastPt = [...s.pts].reverse().find((p) => p.value !== null && p.value !== undefined);
      if (!lastPt) return;
      const ex = xScale(periodOrdinal(lastPt.period));
      const ey = yScale(lastPt.value);
      svg.appendChild(drawEndpointDot(svg, { x: ex, y: ey, color }));

      if (i === 0) {
        renderPrimaryReadout(overlay, wrap, { x: ex, y: ey, color, s, lastPt });
      } else {
        placeLabel(overlay, {
          x: ex + 6,
          y: ey,
          text: buildReadoutNode({ nameOnly: true, name: s.name, color }),
          className: 'end-label',
        });
      }
    });
  }

  function renderPrimaryReadout(overlay, wrap, { x, y, color }) {
    const node = buildReadoutNode({ color, valueText: currentValueText(), captionText: currentCaptionText() });
    placeLabel(overlay, { x: x + 6, y, text: node, className: 'end-label end-label--primary' });

    if (caliber) {
      const toggle = h('div', { class: 'caliber-toggle', role: 'group', 'aria-label': '当月/累计切换' }, [
        h(
          'button',
          {
            type: 'button',
            class: activeCaliber === 'single' ? 'active' : '',
            onClick: () => {
              activeCaliber = 'single';
              rerenderCurrentWidth();
              if (onCaliberChange) onCaliberChange('single');
            },
          },
          '当月',
        ),
        h(
          'button',
          {
            type: 'button',
            class: activeCaliber === 'ytd' ? 'active' : '',
            onClick: () => {
              activeCaliber = 'ytd';
              rerenderCurrentWidth();
              if (onCaliberChange) onCaliberChange('ytd');
            },
          },
          '累计',
        ),
      ]);
      wrap.appendChild(toggle);
      toggle.setAttribute('aria-hidden', 'false');
    }
  }

  function currentValueText() {
    if (caliber) return caliber[activeCaliber].valueText;
    return null;
  }
  function currentCaptionText() {
    if (caliber) return caliber[activeCaliber].yoyText;
    return null;
  }

  function buildReadoutNode({ color, valueText, captionText, nameOnly = false, name }) {
    if (nameOnly) {
      return h('span', { class: 'end-label-name', style: { color } }, name || '');
    }
    const children = [];
    if (valueText) children.push(h('span', { class: 'end-label-value', style: { color } }, valueText));
    if (captionText) children.push(h('span', { class: 'end-label-caption' }, captionText));
    return h('span', { class: 'end-label-group' }, children);
  }

  let lastWidth = 0;
  function rerenderCurrentWidth() {
    if (lastWidth) render(lastWidth);
  }

  disposeResize = onResize(container, (width) => {
    lastWidth = width;
    render(width);
  });
  onRangeChange(() => rerenderCurrentWidth());
  onThemeChange(() => rerenderCurrentWidth());

  // Initial synchronous render at the container's current width (ResizeObserver
  // also fires once on observe(), but doing it eagerly avoids a blank flash
  // before the first callback tick).
  const initialWidth = container.getBoundingClientRect().width;
  if (initialWidth) {
    lastWidth = initialWidth;
    render(initialWidth);
  }

  return {
    destroy() {
      if (disposeResize) disposeResize();
    },
  };
}
