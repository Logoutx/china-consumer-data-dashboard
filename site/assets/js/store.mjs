// store.mjs — the page's only global interactive state: the time-range
// control (VIZ-GUIDE rule 1: "exactly two controls are allowed globally").
// The per-series 当月/累计 toggle is local to each chart instance instead —
// it affects only that one series' printed readout, not a shared axis.
//
// Charts read getRangeYears() at first render (so a lazily-mounted section
// picks up whatever range is currently selected) and subscribe via
// onRangeChange() to redraw live when the control changes.

import { RANGE_OPTIONS, DEFAULT_RANGE_KEY } from './lib/period.mjs';

const DEFAULT_OPTION = RANGE_OPTIONS.find((o) => o.key === DEFAULT_RANGE_KEY);

let currentKey = DEFAULT_OPTION.key;
let currentYears = DEFAULT_OPTION.years;
const listeners = new Set();

export function getRangeKey() {
  return currentKey;
}

export function getRangeYears() {
  return currentYears;
}

export function setRangeKey(key) {
  const option = RANGE_OPTIONS.find((o) => o.key === key);
  if (!option || option.key === currentKey) return;
  currentKey = option.key;
  currentYears = option.years;
  for (const fn of listeners) fn(currentYears, currentKey);
}

/** Subscribe to range changes; returns an unsubscribe function. */
export function onRangeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// -- theme (dark mode) rerender hook -----------------------------------------
// No manual toggle UI (rule 1 caps controls at two + the city filter) — this
// only reacts to the OS-level prefers-color-scheme change so already-drawn
// chart SVGs (which read color tokens at draw time, not via CSS alone,
// because line/dot stroke colors are baked into path attributes) redraw with
// the new palette.

const themeListeners = new Set();
// Guarded so this module can also be imported under plain Node (tests.mjs)
// without a browser `window`/matchMedia global.
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
if (media) {
  media.addEventListener('change', () => {
    for (const fn of themeListeners) fn();
  });
}

export function onThemeChange(fn) {
  themeListeners.add(fn);
  return () => themeListeners.delete(fn);
}
