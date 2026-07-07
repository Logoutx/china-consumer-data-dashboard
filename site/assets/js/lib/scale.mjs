// lib/scale.mjs — pure scale math: extent, "nice" round-step ticks, linear scale.
// No DOM. VIZ-GUIDE rule 10: 3-5 horizontal gridlines, round steps.

/** [min, max] over non-null numeric values, or null if none. */
export function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v === null || v === undefined || Number.isNaN(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === Infinity) return null;
  return [min, max];
}

/** Classic "nice number" (1/2/5/10 * 10^n) — round() snaps to the nearest of those. */
function niceNum(range, round) {
  if (range === 0) return 1;
  const exponent = Math.floor(Math.log10(range));
  const fraction = range / 10 ** exponent;
  let niceFraction;
  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
    else niceFraction = 10;
  } else if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  else niceFraction = 10;
  return niceFraction * 10 ** exponent;
}

function ticksForStep(min, max, step) {
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const ticks = [];
  const epsilon = step / 1e6;
  for (let v = niceMin; v <= niceMax + epsilon; v += step) {
    // Two-stage rounding: snap to the step grid, then kill residual binary
    // float representation error (e.g. steps of 0.2 otherwise print
    // -0.6000000000000001) by rounding to 9 decimal places.
    const snapped = Math.round(v / step) * step;
    ticks.push(Math.round(snapped * 1e9) / 1e9);
  }
  return { ticks, niceMin, niceMax };
}

/**
 * Round-step tick values spanning [min, max], 3-5 lines (VIZ-GUIDE rule 10).
 * Returns { ticks, domain: [niceMin, niceMax], step } — the chart's y-domain
 * is the nice-rounded span, not the raw data extent, so gridlines exactly
 * bound the plot with no arbitrary padding.
 *
 * Domain fit (design-review fix): step is derived DIRECTLY from the raw span
 * (`niceNum(span / (maxTicks-1), true)`), not via an intermediate
 * `niceNum(span, false)` rounding pass — that extra stage was amplifying
 * headroom for domains that don't land near a round span (e.g. CPI's
 * [-0.8, 2.8] rendered on [-2, 4], a 6-unit box for a 3.6-unit span) because
 * the intermediate rounding pushed the derived step a full tier too coarse.
 * The direct formula still floor/ceils outward (so gridlines always bracket
 * the data), but the outward padding is now at most one step per side, as
 * intended, verified empirically against 18 domains spanning this app's
 * actual data (CPI, retail, 70-city, PMI, M1, ...).
 *
 * maxTicks defaults to 4, not 5: the classic ceil/floor bracketing below is
 * inclusive of both ends, so asking for "5" ticks routinely yields 6 for
 * ordinary domains (e.g. [0,100] at step 20 -> 0,20,40,60,80,100). Targeting
 * 4 keeps the typical output at 3-5, and the escalation loop below is a
 * belt-and-suspenders cap for the rare domain (e.g. a large negative-only
 * level series) where even that still overshoots to 6: it steps up to the
 * next nice tier (via niceNum(step*1.5, true), which reliably lands one tier
 * coarser) until the count is back within the 3-5 rule.
 */
export function niceTicks(min, max, maxTicks = 4) {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  let step = niceNum((max - min) / (maxTicks - 1), true);
  let result = ticksForStep(min, max, step);
  let guard = 0;
  while (result.ticks.length > 5 && guard < 10) {
    step = niceNum(step * 1.5, true);
    result = ticksForStep(min, max, step);
    guard += 1;
  }
  return { ticks: result.ticks, domain: [result.niceMin, result.niceMax], step };
}

/**
 * How many decimals a tick VALUE needs given the axis step (design-review
 * item 3): step >= 1 -> whole numbers ("4%" not "4.0%"); step < 1 -> the
 * number of decimal places the step itself carries (0.5 -> 1, 0.05 -> 2),
 * so ticks stay visually aligned ("3.5%, 4.0%, 4.5%") instead of some
 * showing a trailing ".0" inconsistently. niceNum only ever emits steps of
 * the form {1,2,5}*10^n, so this is exact, not a heuristic.
 */
export function decimalsForStep(step) {
  if (!(step > 0) || step >= 1) return 0;
  return Math.max(1, Math.ceil(-Math.log10(step)));
}

/** Linear scale: domain [d0,d1] -> range [r0,r1], with .invert(). */
export function linearScale([d0, d1], [r0, r1]) {
  const domainSpan = d1 - d0 || 1;
  const scale = (v) => r0 + ((v - d0) / domainSpan) * (r1 - r0);
  scale.invert = (px) => d0 + ((px - r0) / (r1 - r0)) * domainSpan;
  return scale;
}
