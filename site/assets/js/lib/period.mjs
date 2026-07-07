// lib/period.mjs — period-string parsing shared by every chart's x-axis.
//
// Mirrors pipeline/build.py's _period_shape(): dispatch on the STRING's own
// literal shape ("YYYY" / "YYYY-Qn" / "YYYY-MM"), never on a series' declared
// freq — real catalog data mixes shapes within one nominally-quarterly series
// (annual-supplement rows), so shape-based dispatch is the only version that
// doesn't crash or mislabel those rows.

const ANNUAL_RE = /^\d{4}$/;

export function periodShape(period) {
  if (ANNUAL_RE.test(period)) return 'annual';
  if (period.includes('-Q')) return 'quarterly';
  return 'monthly';
}

/**
 * A single linear "months since epoch" ordinal for any period shape, so a
 * chart's x-scale is genuinely time-proportional (irregular gaps — e.g. an
 * annual-supplement row inside quarterly data — show as gaps, never get
 * silently compressed by index-based spacing).
 */
export function periodOrdinal(period) {
  const shape = periodShape(period);
  if (shape === 'annual') return parseInt(period, 10) * 12;
  if (shape === 'quarterly') {
    const [y, q] = period.split('-Q');
    return parseInt(y, 10) * 12 + (parseInt(q, 10) - 1) * 3;
  }
  const [y, m] = period.split('-');
  return parseInt(y, 10) * 12 + (parseInt(m, 10) - 1);
}

export const RANGE_OPTIONS = [
  { key: '1Y', label: '1 年', years: 1 },
  { key: '3Y', label: '3 年', years: 3 },
  { key: '5Y', label: '5 年', years: 5 },
  { key: '10Y', label: '10 年', years: 10 },
  { key: 'max', label: '最大', years: Infinity },
];

export const DEFAULT_RANGE_KEY = '5Y';

/** True if `period` falls within `years` of `latestPeriod` (inclusive). */
export function withinRangeYears(period, latestPeriod, years) {
  if (years === Infinity) return true;
  const diffMonths = periodOrdinal(latestPeriod) - periodOrdinal(period);
  return diffMonths <= years * 12 && diffMonths >= 0;
}
