// lib/format.mjs — pure string/number formatting helpers.
//
// Chinese typesetting per DATA-CONTRACT §12 / VIZ-GUIDE: Arabic numerals,
// full-width curly quotes only, pangu spacing (half-width space between CJK
// and adjacent Latin/digits, none before "%", none next to full-width
// punctuation). Bundle strings (takeaway, period_label_zh, name_zh, ...)
// already arrive pre-formatted — never re-format them. These helpers exist
// for the strings THIS site composes itself (source lines, deks, endpoint
// readouts) so those follow the same rules.
//
// panguJoin ports pipeline/takeaways.py's _join()/_is_cjk()/_needs_pangu_space()
// to JS so the client-composed strings follow the identical spacing rule the
// server-side takeaway generator uses.

const CJK_RANGES = [
  [0x4e00, 0x9fff],
  [0x3400, 0x4dbf],
  [0xf900, 0xfaff],
];

export function isCJK(ch) {
  if (!ch) return false;
  const cp = ch.codePointAt(0);
  return CJK_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

const isDigit = (ch) => !!ch && ch >= '0' && ch <= '9';

export function needsPanguSpace(left, right) {
  if (!left || !right) return false;
  return (isCJK(left) && isDigit(right)) || (isDigit(left) && isCJK(right));
}

/** Join fragments with one half-width space inserted at any CJK<->digit seam. */
export function panguJoin(...parts) {
  let result = '';
  for (const part of parts) {
    if (!part) continue;
    if (result && needsPanguSpace(result[result.length - 1], part[0])) result += ' ';
    result += part;
  }
  return result;
}

// The catalog's per-series `decimals` field is NOT threaded through to the
// site-data bundle (pipeline/build.py's _build_series_entry never copies it
// in) — real observations show it wouldn't even be a single constant per
// series regardless (e.g. 70-city m_yoy prints both -0.6 and -3.64). Instead
// this infers "natural precision" exactly like pipeline/takeaways.py's own
// _fmt(): 1 decimal by default, 2 only if the value actually needs it. This
// keeps chart-printed numbers consistent with the takeaway sentences' own
// number formatting without a decimals hint the bundle doesn't provide.
function naturalDecimals(value) {
  const abs = Math.abs(Math.round(value * 1e6) / 1e6);
  return Math.round(abs * 10) / 10 !== abs ? 2 : 1;
}

/** Render a number with thousands separators. decimals=null infers natural precision. */
export function formatNumber(value, decimals = null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const num = Number(value);
  const dec = decimals === null ? naturalDecimals(num) : decimals;
  const negative = num < 0;
  const fixed = Math.abs(num).toFixed(dec);
  const [intPart, fracPart] = fixed.split('.');
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const out = fracPart ? `${withThousands}.${fracPart}` : withThousands;
  return negative ? `-${out}` : out;
}

/** Percent formatting — no space before "%" (§12). decimals=null infers natural precision. */
export function formatPercent(value, decimals = null, { sign = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const num = Number(value);
  const dec = decimals === null ? naturalDecimals(num) : decimals;
  const prefix = sign && num > 0 ? '+' : '';
  return `${prefix}${num.toFixed(dec)}%`;
}

/** Percentage-point delta, always non-negative magnitude + unit word. */
export function formatPP(value, decimals = null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const dec = decimals === null ? naturalDecimals(value) : decimals;
  return `${Math.abs(value).toFixed(dec)} 个百分点`;
}

/** ISO datetime (as stored in a bundle's generated_at) -> "2026 年 7 月 8 日". */
export function formatDateZh(isoString) {
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getUTCFullYear()} 年 ${d.getUTCMonth() + 1} 月 ${d.getUTCDate()} 日`;
}
