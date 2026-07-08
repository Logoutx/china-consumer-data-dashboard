// components/line-chart.mjs — the Tier-1 chart: 1-2 series, direct end
// labels, endpoint dot + printed value, annotations as numbered footnote
// markers, break markers, dashed-derived lines, hover tooltip. VIZ-GUIDE
// rules 2/4/5/6/8/10/13. Also the engine behind property.html's city-detail
// expand and group-average charts (both feed it 2-series data).
//
// Design-review fixes (2026-07-08, two passes):
//  - Annotations/breaks print only a small circled numeral on the chart
//    face (never full text — that crowded the endpoint gutter and got
//    dropped on narrow viewports); the full text is a numbered footnote
//    list below the chart, in normal HTML flow, at every width.
//  - Gridline tick labels trim to whole numbers when the axis step is >=1.
//  - Every render (including later async resize/range/theme callbacks) is
//    wrapped: an exception logs via console.error and falls back to an
//    inline error note instead of silently leaving a blank/stuck chart.
//  - Stray vertical lines bug: .chart-surface/.chart-svg/.chart-overlay now
//    clip (overflow:hidden) instead of allowing geometry to escape the
//    chart's own box (see main.css) — a defensive fix regardless of the
//    exact original cause, which could not be reproduced as a JS-level
//    accumulation bug (render() clears the mount point fresh every call,
//    verified via a repeated-render idempotency test — see tests.dom.mjs).
//  - Unit-label bug: dek/tooltip/tick units are now driven by the SAME
//    isPercent/decimals/unitLabel resolution the chart plots, per series
//    (section.mjs's plottedUnitLabel/primarySeriesValues) — no more "M1 ·
//    亿元" next to a 同比 % line.
//  - Hover tooltip added (desktop-bonus, pointer devices only): snaps to
//    the nearest data point by x, shows a guide line + per-series dot +
//    a small period/value box. Purely additive — the printed endpoint
//    value stays the always-visible source of truth (rule 2).
//
// Endpoint readout: by default the bundle's yoy_series/level_series is a
// single array resolved to ONE caliber — so the toggle text-swaps the
// printed 当月/累计 readout only. Where the bundle ALSO carries the other
// caliber's series arrays (feature-detected via props.caliber.<key>.series;
// field names yoy_series_ytd/level_series_ytd are a best guess, unverified
// against a real bundle as of this fix), the toggle instead swaps the
// plotted line + domain + endpoint position, not just the caption text.

import { clear, onResize, h, supportsHover, ensureMeasuredWidth } from '../lib/dom.mjs';
import { extent, decimalsForStep } from '../lib/scale.mjs';
import { periodOrdinal, withinRangeYears } from '../lib/period.mjs';
import { circledNumeral } from '../lib/format.mjs';
import { nearestIndexByOrdinal, tooltipPeriodLabel, tooltipValueLabel } from '../lib/tooltip.mjs';
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

const GUTTER_MARGIN = 6; // px of breathing room before the reserved endpoint gutter
const MARKER_STACK_STEP = 15; // px vertical offset between stacked footnote markers
const ENDPOINT_KEEPOUT_X = 60; // px
const ENDPOINT_KEEPOUT_Y = 24; // px

/**
 * @param container HTMLElement
 * @param props {
 *   ariaLabel: string,
 *   seriesList: [{ id, name, values:[{period,value}], derived, colorVar,
 *                  isPercent?, decimals?, unitLabel?, cumulative? }],
 *   valueFormatter: (n) => string,   // endpoint/data-value formatting for series[0] (natural precision)
 *   isPercent: boolean,              // series[0]'s plotted kind (drives tick "%" suffix); overridable per-series above
 *   unitLabel?: string,              // series[0]'s plotted unit (tooltip fallback when not set per-series)
 *   annotations: [{period, text}],   // period-anchored only (series-level notes render elsewhere)
 *   breaks: [{effective, note}],
 *   caliber: null | {
 *     single: { label, valueText, yoyText, series?: {values, isPercent} },
 *     ytd:    { label, valueText, yoyText, series?: {values, isPercent} },
 *   },
 *   height?: number,
 *   ignoreRange?: boolean,           // skip the global time-range control — city-detail/group charts want full history
 * }
 */
export function mountLineChart(container, props) {
  const {
    ariaLabel,
    seriesList,
    valueFormatter,
    isPercent = false,
    unitLabel = '',
    annotations = [],
    breaks = [],
    caliber = null,
    height = 260,
    onCaliberChange,
    ignoreRange = false,
  } = props;

  // Feature-detect the "full swap" caliber shape (design-review item 9):
  // both calibers additionally carry their own {values, isPercent} series.
  const fullSwapAvailable = !!(caliber && caliber.single && caliber.ytd && caliber.single.series && caliber.ytd.series);
  const hoverEnabled = supportsHover();

  let activeCaliber = 'single';
  let disposeResize = null;
  let lastWidth = 0;

  function activeValuesAndFormat() {
    if (fullSwapAvailable) {
      const active = caliber[activeCaliber].series;
      return { values: active.values, isPercent: active.isPercent };
    }
    return { values: seriesList[0].values, isPercent };
  }

  function visiblePoints(values) {
    if (!values.length) return [];
    if (ignoreRange) return values;
    const withData = values.filter((v) => v.value !== null && v.value !== undefined);
    const latestPeriod = withData.length ? withData[withData.length - 1].period : values[values.length - 1].period;
    const years = getRangeYears();
    return values.filter((v) => withinRangeYears(v.period, latestPeriod, years));
  }

  function render(width) {
    try {
      renderUnsafe(width);
    } catch (err) {
      console.error(`[line-chart] render failed for series ${seriesList.map((s) => s.id).join(',')}:`, err);
      clear(container);
      container.appendChild(h('p', { class: 'render-error-note' }, '该图表渲染失败，请刷新重试。'));
    }
  }

  function renderUnsafe(width) {
    if (!width) return;
    const primaryConfig = activeValuesAndFormat();
    const activeSeriesList = [{ ...seriesList[0], values: primaryConfig.values }, ...seriesList.slice(1)];
    const activeIsPercent = primaryConfig.isPercent;

    const visSeries = activeSeriesList.map((s) => ({ ...s, pts: visiblePoints(s.values) }));
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

    const { xScale, yScale, yTicks, yStep, pad } = computeScales({
      width,
      height,
      xDomain,
      yExtent,
      padding: { left: 44, right: padRight, top: 14, bottom: 8 },
    });

    // Tick labels trim to the axis's own round step (item 3), independent of
    // the endpoint value's natural-precision formatter.
    const tickDecimals = decimalsForStep(yStep);
    const tickFormatter = (v) => `${v.toFixed(tickDecimals)}${activeIsPercent ? '%' : ''}`;
    drawGridlines(svg, { width, yScale, yTicks, pad, overlay, formatTick: tickFormatter });

    const gutterLeft = width - padRight;

    // Compute the primary series' endpoint FIRST so footnote markers (and
    // the tooltip's flip logic) can avoid it (item 2: "reserve a right-side
    // gutter for the endpoint dot+value; annotations must collision-avoid").
    const primaryPts = visSeries[0].pts;
    const primaryLast = [...primaryPts].reverse().find((p) => p.value !== null && p.value !== undefined);
    const endpoint = primaryLast ? { x: xScale(periodOrdinal(primaryLast.period)), y: yScale(primaryLast.value) } : null;

    renderFootnoteMarkers(svg, overlay, container, {
      breaks,
      annotations,
      xScale,
      xDomain,
      pad,
      height,
      gutterLeft,
      endpoint,
    });

    const seriesColors = [];
    visSeries.forEach((s, i) => {
      const color = `var(${s.colorVar || '--context'})`;
      seriesColors.push(color);
      const pathPoints = s.pts.map((p) => ({ x: periodOrdinal(p.period), y: p.value }));
      const line = drawLine(svg, { points: pathPoints, xScale, yScale, color, dashed: s.derived });
      if (line) svg.appendChild(line);

      const lastPt = [...s.pts].reverse().find((p) => p.value !== null && p.value !== undefined);
      if (!lastPt) return;
      const ex = xScale(periodOrdinal(lastPt.period));
      const ey = yScale(lastPt.value);
      svg.appendChild(drawEndpointDot(svg, { x: ex, y: ey, color }));

      if (i === 0) {
        renderPrimaryReadout(overlay, wrap, { x: ex, y: ey, color, lastValue: lastPt.value });
      } else {
        placeLabel(overlay, {
          x: ex + 6,
          y: ey,
          text: buildReadoutNode({ nameOnly: true, name: s.name, color }),
          className: 'end-label',
        });
      }
    });

    if (hoverEnabled) {
      wireTooltip(svg, overlay, wrap, {
        visSeries,
        seriesColors,
        xScale,
        yScale,
        width,
        height,
        pad,
        gutterLeft,
        topKeepout: pad.top + 20, // stay clear of footnote markers near the top
      });
    }
  }

  /**
   * Numbered footnote system (items 2+5): every break/annotation gets a
   * leader tick + a tiny circled-numeral marker on the chart face (never
   * full text), plus a matching <li> in a footnote list appended right
   * after the chart surface, at every viewport width. Renumbers each
   * render() call from whatever is currently visible, in chronological
   * order, so numbers always match what's on screen for the active range.
   */
  function renderFootnoteMarkers(svg, overlay, container, { breaks, annotations, xScale, xDomain, pad, height, gutterLeft, endpoint }) {
    const items = [
      ...breaks.filter((b) => b.effective).map((b) => ({ period: b.effective, text: b.note_zh || b.note_en || '口径调整', strong: true })),
      ...annotations.filter((a) => a.period).map((a) => ({ period: a.period, text: a.text_zh || a.text || '', strong: false })),
    ]
      .filter((item) => {
        const ord = periodOrdinal(item.period);
        return ord >= xDomain[0] && ord <= xDomain[1];
      })
      .sort((a, b) => periodOrdinal(a.period) - periodOrdinal(b.period));

    if (!items.length) return;

    let stackCount = 0;
    const footnoteEntries = [];
    for (const item of items) {
      const num = footnoteEntries.length + 1;
      const x = xScale(periodOrdinal(item.period));
      svg.appendChild(drawLeader(svg, { x, top: pad.top, bottom: height - pad.bottom, strong: item.strong }));

      const nearGutter = x > gutterLeft - GUTTER_MARGIN - 20;
      const markerX = Math.min(x + 4, gutterLeft - GUTTER_MARGIN);
      let markerY = pad.top + stackCount * MARKER_STACK_STEP;
      const collidesWithEndpoint =
        endpoint && Math.abs(markerX - endpoint.x) < ENDPOINT_KEEPOUT_X && Math.abs(markerY - endpoint.y) < ENDPOINT_KEEPOUT_Y;
      if (collidesWithEndpoint || nearGutter) {
        markerY += ENDPOINT_KEEPOUT_Y + MARKER_STACK_STEP;
      }
      stackCount += 1;

      placeLabel(overlay, {
        x: markerX,
        y: markerY,
        text: circledNumeral(num),
        className: item.strong ? 'break-label footnote-marker' : 'annotation-label footnote-marker',
        anchor: 'end',
      });
      footnoteEntries.push({ num, text: item.text });
    }

    const list = h(
      'ol',
      { class: 'chart-footnotes' },
      footnoteEntries.map((entry) => h('li', {}, `${circledNumeral(entry.num)} ${entry.text}`)),
    );
    container.appendChild(list);
  }

  /**
   * Hover tooltip (desktop-bonus, additive per VIZ-GUIDE rule 2 — the
   * printed endpoint value is the always-visible fact; this is a lookup
   * convenience on top). Idempotent by construction: it's built fresh
   * inside renderUnsafe's already-cleared overlay/svg every render, so
   * there is never more than one tooltip node per chart, and a redraw
   * can't leave a stale one behind.
   */
  function wireTooltip(svg, overlay, wrap, { visSeries, seriesColors, xScale, yScale, width, height, pad, gutterLeft, topKeepout }) {
    const guide = drawLeader(svg, { x: -1000, top: pad.top, bottom: height - pad.bottom });
    guide.classList.add('hover-guide');
    guide.style.opacity = '0';
    svg.appendChild(guide);

    const seriesDots = visSeries.map((s, i) => {
      const dot = drawEndpointDot(svg, { x: -1000, y: -1000, color: seriesColors[i] });
      dot.classList.add('hover-dot');
      dot.style.opacity = '0';
      svg.appendChild(dot);
      return dot;
    });

    const box = h('div', { class: 'chart-tooltip', 'aria-hidden': 'true' });
    box.style.opacity = '0';
    overlay.appendChild(box);

    const ordinalsBySeries = visSeries.map((s) => s.pts.map((p) => periodOrdinal(p.period)));

    function hide() {
      guide.style.opacity = '0';
      seriesDots.forEach((d) => (d.style.opacity = '0'));
      box.style.opacity = '0';
    }

    function show(clientX) {
      const rect = wrap.getBoundingClientRect();
      const px = clientX - rect.left;
      if (px < pad.left || px > width - 4) {
        hide();
        return;
      }
      const targetOrdinal = xScale.invert(px);
      const primaryIdx = nearestIndexByOrdinal(ordinalsBySeries[0], targetOrdinal);
      if (primaryIdx === -1) {
        hide();
        return;
      }
      const primaryPeriod = visSeries[0].pts[primaryIdx].period;
      const x = xScale(periodOrdinal(primaryPeriod));

      guide.setAttribute('x1', x);
      guide.setAttribute('x2', x);
      guide.style.opacity = '1';

      const rows = [];
      let anyY = height / 2;
      visSeries.forEach((s, i) => {
        const idx = nearestIndexByOrdinal(ordinalsBySeries[i], targetOrdinal);
        const pt = idx >= 0 ? s.pts[idx] : null;
        const dot = seriesDots[i];
        if (!pt || pt.value === null || pt.value === undefined) {
          dot.style.opacity = '0';
          return;
        }
        const py = yScale(pt.value);
        dot.setAttribute('cx', xScale(periodOrdinal(pt.period)));
        dot.setAttribute('cy', py);
        dot.style.opacity = '1';
        anyY = py;
        const seriesIsPercent = s.isPercent ?? isPercent;
        const valueText = tooltipValueLabel(pt.value, {
          isPercent: seriesIsPercent,
          decimals: s.decimals ?? null,
          unitLabel: seriesIsPercent ? '' : (s.unitLabel ?? unitLabel),
        });
        rows.push({ label: visSeries.length > 1 ? s.name : null, value: valueText, color: seriesColors[i] });
      });
      if (!rows.length) {
        hide();
        return;
      }

      const periodLabel = tooltipPeriodLabel(primaryPeriod, { cumulative: !!seriesList[0].cumulative });
      renderTooltipBox(box, periodLabel, rows);
      positionTooltipBox(box, { x, y: anyY, width, height, pad, gutterLeft, topKeepout });
    }

    wrap.addEventListener('pointermove', (e) => show(e.clientX));
    wrap.addEventListener('pointerleave', hide);
  }

  function renderTooltipBox(box, periodLabel, rows) {
    box.textContent = '';
    box.appendChild(h('div', { class: 'chart-tooltip-period' }, periodLabel));
    for (const row of rows) {
      box.appendChild(
        h('div', { class: 'chart-tooltip-row' }, [
          row.label ? h('span', { class: 'chart-tooltip-name', style: { color: row.color } }, row.label) : null,
          h('span', { class: 'chart-tooltip-value' }, row.value),
        ]),
      );
    }
  }

  function positionTooltipBox(box, { x, y, pad, gutterLeft, topKeepout }) {
    const flipLeft = x > gutterLeft - 90;
    const flipDown = y < topKeepout;
    box.style.left = `${x}px`;
    box.style.top = `${y}px`;
    box.style.opacity = '1';
    const horiz = flipLeft ? 'calc(-100% - 10px)' : '10px';
    const vert = flipDown ? '10px' : 'calc(-100% - 10px)';
    box.style.transform = `translate(${horiz}, ${vert})`;
  }

  function renderPrimaryReadout(overlay, wrap, { x, y, color, lastValue }) {
    // BUG FIX (design-review item 2, root cause): the endpoint printed NO
    // value at all for any series without a caliber toggle (every tier-1
    // series except retail-total). Fallback: format the series' own last
    // plotted value with valueFormatter when there's no caliber block.
    const valueText = caliber ? currentValueText() : valueFormatter(lastValue);
    const captionText = caliber ? currentCaptionText() : null;
    const node = buildReadoutNode({ color, valueText, captionText });
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

  function rerenderCurrentWidth() {
    if (lastWidth) render(lastWidth);
  }

  disposeResize = onResize(container, (width) => {
    lastWidth = width;
    render(width);
  });
  onRangeChange(() => rerenderCurrentWidth());
  onThemeChange(() => rerenderCurrentWidth());

  // Initial render at the container's current width (ResizeObserver also
  // fires once on observe(), but doing it eagerly avoids a blank flash
  // before the first callback tick). Zero-width mount fragility fix: a
  // container that's momentarily 0-width (fresh grid insert, a display:none
  // ancestor mid-transition, ...) used to just silently never render if
  // ResizeObserver was slow or inert — ensureMeasuredWidth retries via
  // requestAnimationFrame then a scroll/resize fallback until a real width
  // shows up.
  ensureMeasuredWidth(container, (width) => {
    lastWidth = width;
    render(width);
  });

  return {
    destroy() {
      if (disposeResize) disposeResize();
    },
  };
}
