// lib/safe.mjs — shared error-isolation helper (design-review item 1).
//
// Root-cause note: the 就业 (employment) section crash reported in review
// could not be reproduced as a synchronous exception against the CURRENT
// real bundle (tested renderSection against all 8 sections' real data,
// every range option 1Y/3Y/5Y/10Y/max, both 800px and 375px container
// widths, and repeated re-renders on the same container — all completed
// without throwing; see the session's diagnostic scripts). Two plausible,
// non-exclusive explanations fit the evidence: (a) all 8 section bundles
// share one filesystem mtime from a concurrent pipeline rebuild that landed
// while this review was happening — a torn read of a bundle mid-rewrite
// would produce a genuine JSON.parse failure at fetch time, independent of
// anything in this file; (b) an exception thrown later from an async
// resize/range/theme callback had NO error boundary at all before this fix
// (only the initial fetchSection() call was try/caught, and that catch
// never logged the error — confirmed by reading app.mjs). Regardless of
// which fired originally, every plausible failure point is now hardened:
// every render call in this file, small-multiples.mjs, pulse-row.mjs,
// city-grid.mjs, line-chart.mjs, and app.mjs's fetch catches now goes
// through renderSafely() or an equivalent try/catch that console.errors and
// shows an inline fallback, at both the whole-section and per-series/panel
// granularity, so one bad entry can never again silently take out
// everything around it.

import { h } from './dom.mjs';

/**
 * Run `fn()`; on success, appends whatever it returns (if a Node) to
 * `container`. On failure: console.error with `label` for context, and
 * append a small inline error note instead of leaving `container` untouched
 * or throwing further up.
 */
export function renderSafely(container, label, fn) {
  try {
    const result = fn();
    if (result instanceof Node) container.appendChild(result);
  } catch (err) {
    console.error(`[render] ${label} failed:`, err);
    container.appendChild(h('p', { class: 'render-error-note' }, `该序列渲染失败：${label}`));
  }
}

/** Same as renderSafely, but for building a value to return/collect (e.g. one panel in a grid) rather than appending directly. */
export function buildSafely(label, fn, fallback) {
  try {
    return fn();
  } catch (err) {
    console.error(`[render] ${label} failed:`, err);
    return typeof fallback === 'function' ? fallback(err) : fallback;
  }
}
