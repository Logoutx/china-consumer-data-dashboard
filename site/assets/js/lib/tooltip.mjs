// lib/tooltip.mjs — pure functions for line-chart's hover tooltip: nearest-
// point snapping (by x, never interpolated) and period/value formatting.
// No DOM here; components/line-chart.mjs owns all the pointer-event wiring.

import { periodShape } from './period.mjs';
import { formatPercent, formatNumber } from './format.mjs';

/**
 * Index of the ordinal in `ordinals` (ascending) nearest to `target`.
 * Binary search, so correct for uneven spacing (a time-range change, a
 * quarterly series with annual-supplement rows, ...) — never assumes
 * uniform point spacing. Returns -1 for an empty array; clamps to the
 * first/last index rather than ever returning out of range.
 */
export function nearestIndexByOrdinal(ordinals, target) {
  if (!ordinals.length) return -1;
  let lo = 0;
  let hi = ordinals.length - 1;
  if (target <= ordinals[0]) return 0;
  if (target >= ordinals[hi]) return hi;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ordinals[mid] === target) return mid;
    if (ordinals[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  const before = lo - 1;
  if (before < 0) return lo;
  return target - ordinals[before] <= ordinals[lo] - target ? before : lo;
}

/**
 * Tooltip line 1: a period label in the bundle's own style ("2026 年 5 月").
 * `cumulative` (true for a series resolved to the "ytd" caliber) renders a
 * monthly period as a "1-N 月" range instead — this is what a to-date print
 * actually reads as, and subsumes the Jan-Feb combined-print case (which is
 * reported under exactly this kind of cumulative caliber, not a special
 * format of its own). Hyphen per VIZ-GUIDE §12 ("ranges with hyphen"), not
 * the em dash pipeline/build.py's own period_label_zh happens to use
 * server-side for the same shape — a known, flagged inconsistency between
 * the two (see takeaways.py's own module docstring); this client-composed
 * label follows the house style doc.
 */
export function tooltipPeriodLabel(period, { cumulative = false } = {}) {
  const shape = periodShape(period);
  if (shape === 'annual') return `${period} 年`;
  if (shape === 'quarterly') {
    const [year, q] = period.split('-Q');
    return `${year} 年 ${q} 季度`;
  }
  const [year, month] = period.split('-');
  const m = Number(month);
  if (cumulative && m > 1) return `${year} 年 1-${m} 月`;
  return `${year} 年 ${m} 月`;
}

/** Tooltip line 2 (or one per series): value formatted exactly like the dek's plotted unit. */
export function tooltipValueLabel(value, { isPercent, decimals = null, unitLabel = '' } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (isPercent) return formatPercent(value, decimals);
  return unitLabel ? `${formatNumber(value, decimals)} ${unitLabel}` : formatNumber(value, decimals);
}
