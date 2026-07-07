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

/**
 * Round-step tick values spanning [min, max], 3-5 lines (VIZ-GUIDE rule 10).
 * Returns { ticks, domain: [niceMin, niceMax] } — the chart's y-domain is the
 * nice-rounded span, not the raw data extent, so gridlines exactly bound the
 * plot with no arbitrary padding.
 *
 * maxTicks defaults to 4, not 5: the classic ceil/floor bracketing below is
 * inclusive of both ends, so asking for "5" ticks routinely yields 6 for
 * ordinary domains (e.g. [0,100] at step 20 -> 0,20,40,60,80,100) — verified
 * empirically against this app's actual data ranges. Targeting 4 keeps the
 * real output at 3-5 across every domain checked.
 */
export function niceTicks(min, max, maxTicks = 4) {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = niceNum(max - min, false);
  const step = niceNum(span / (maxTicks - 1), true);
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
  return { ticks, domain: [niceMin, niceMax] };
}

/** Linear scale: domain [d0,d1] -> range [r0,r1], with .invert(). */
export function linearScale([d0, d1], [r0, r1]) {
  const domainSpan = d1 - d0 || 1;
  const scale = (v) => r0 + ((v - d0) / domainSpan) * (r1 - r0);
  scale.invert = (px) => d0 + ((px - r0) / (r1 - r0)) * domainSpan;
  return scale;
}
