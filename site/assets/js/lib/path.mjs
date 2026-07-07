// lib/path.mjs — pure SVG path building. No DOM.

/**
 * Build an SVG path "d" string from ascending {x, y} points, where x/y are
 * already-scaled pixel coordinates. A null/NaN y lifts the pen (new "M"
 * subpath after it) — this is how a no_yoy_across break's inserted null
 * renders as a genuine gap in the line, never a connecting segment.
 */
export function linePathD(points) {
  let d = '';
  let penDown = false;
  for (const p of points) {
    if (p.y === null || p.y === undefined || Number.isNaN(p.y)) {
      penDown = false;
      continue;
    }
    const cmd = penDown ? 'L' : 'M';
    d += `${d ? ' ' : ''}${cmd} ${round2(p.x)} ${round2(p.y)}`;
    penDown = true;
  }
  return d;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
