// components/line-chart.mjs — the Tier-1 chart: 1-2 series, direct end
// labels, endpoint dot + printed value, annotations as numbered footnote
// markers, break markers, dashed-derived lines. VIZ-GUIDE rules 2/4/5/6/8/
// 10/13.
//
// Design-review fixes (2026-07-08):
//  - Annotations/breaks used to print their FULL text inline on the chart
//    face, which could crowd the endpoint value (rule 2 says the latest
//    value must ALWAYS be visible) and got dropped entirely on narrow
//    viewports. Fixed uniformly at every width: the chart face gets only a
//    leader tick + a small circled numeral (①②...), clamped to never enter
//    the endpoint's reserved right-side gutter; the full text renders as a
//    numbered footnote list below the chart, in normal HTML flow, always.
//  - Gridline tick labels now trim to whole numbers when the axis step is
//    >=1 (no more "4.0%" next to "0%"); the endpoint/data value keeps its
//    own natural-precision formatting (formatNumber/formatPercent, chosen in
//    section.mjs) since a printed observation's actual precision is a
//    different question from the axis's round step.
//  - Every render (including ones triggered asynchronously later by resize/
//    range/theme callbacks, which previously had no error boundary at all)
//    is now wrapped: an exception logs via console.error and falls back to
//    an inline error note instead of silently leaving a blank/stuck chart.
//
// Endpoint readout: by default the bundle's yoy_series/level_series is a
// single array resolved to ONE caliber (build.py's _resolve_caliber picks
// "single" when a series has it) — so the toggle text-swaps the printed
// 当月/累计 readout only. Where the bundle ALSO carries the other caliber's
// series arrays (feature-detected via props.caliber.<key>.series — see
// section.mjs's buildCaliberOption; field names yoy_series_ytd/
// level_series_ytd are a best guess per the design-review note, unverified
// against a real bundle as of this fix), the toggle instead swaps the
// plotted line + domain + endpoint position, not just the caption text.

import { clear, onResize, h } from '../lib/dom.mjs';
import { extent, decimalsForStep } from '../lib/scale.mjs';
import { periodOrdinal, withinRangeYears } from '../lib/period.mjs';
import { circledNumeral } from '../lib/format.mjs';
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
 *   seriesList: [{ id, name, values:[{period,value}], derived, colorVar }],
 *   valueFormatter: (n) => string,   // endpoint/data-value formatting (natural precision)
 *   isPercent: boolean,              // whether seriesList[0].values are already a percent (drives tick "%" suffix)
 *   annotations: [{period, text}],   // period-anchored only (series-level notes render elsewhere)
 *   breaks: [{effective, note}],
 *   caliber: null | {
 *     single: { label, valueText, yoyText, series?: {values, isPercent} },
 *     ytd:    { label, valueText, yoyText, series?: {values, isPercent} },
 *   },
 *   height?: number,
 * }
 */
export function mountLineChart(container, props) {
  const {
    ariaLabel,
    seriesList,
    valueFormatter,
    isPercent = false,
    annotations = [],
    breaks = [],
    caliber = null,
    height = 260,
    onCaliberChange,
  } = props;

  // Feature-detect the "full swap" caliber shape (design-review item 9):
  // both calibers additionally carry their own {values, isPercent} series.
  const fullSwapAvailable = !!(caliber && caliber.single && caliber.ytd && caliber.single.series && caliber.ytd.series);

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
    const withData = values.filter((v) => v.value !== null && v.value !== undefined);
    const latestPeriod = withData.length ? withData[withData.length - 1].period : values[values.length - 1].period;
    const years = getRangeYears();
    return values.filter((v) => withinRangeYears(v.period, latestPeriod, years));
  }

  function render(width) {
    try {
      renderUnsafe(width);
    } catch (err) {
      // Design-review item 1: a render exception (including ones raised
      // asynchronously from a later resize/range/theme callback, which had
      // no error boundary at all before this fix) must never fail silently.
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

    // Compute the primary series' endpoint FIRST so footnote markers can
    // avoid it (item 2: "reserve a right-side gutter for the endpoint
    // dot+value; annotations must collision-avoid").
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
  }

  /**
   * Numbered footnote system (items 2+5): every break/annotation gets a
   * leader tick + a tiny circled-numeral marker on the chart face (never
   * full text — that's what crowded the endpoint gutter and got dropped on
   * narrow viewports), plus a matching <li> in a footnote list appended
   * right after the chart surface, at every viewport width. Renumbers each
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

  function renderPrimaryReadout(overlay, wrap, { x, y, color, lastValue }) {
    // BUG FIX (design-review item 2, root cause): currentValueText()/
    // currentCaptionText() previously returned null whenever a series has no
    // caliber toggle — which is every tier-1 series except retail-total (CPI,
    // PMI, 70-city, M1, ...). That meant the endpoint printed NO value at all
    // for nearly the whole site, not merely "crowded by an annotation" on
    // CPI specifically. Fallback: when there's no caliber block, format the
    // series' own last plotted value with valueFormatter — this is exactly
    // rule 2's "latest value... printed on the chart face", and since the
    // plotted value for most series already IS the YoY (see
    // section.mjs's primarySeriesValues preferring yoy_series), it also
    // covers "its YoY change" without a separate caption.
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
