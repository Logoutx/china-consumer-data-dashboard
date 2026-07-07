// lib/color.mjs — the one fixed binary mapping (VIZ-GUIDE rule 14): 红涨绿跌.
// Returns CSS custom-property references so light/dark tokens apply for free.

export function upDownColor(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'var(--context)';
  if (value > 0) return 'var(--accent-red)';
  if (value < 0) return 'var(--fall-green)';
  return 'var(--ink-soft)';
}

export function upDownClass(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'flat';
  if (value > 0) return 'up';
  if (value < 0) return 'down';
  return 'flat';
}

// Series id -> publishing agency, per DATA-CONTRACT §2's id scheme
// ("<agency>-<slug>[-<qualifier>]"). The site bundle does not carry a
// `source` object per series (see DEV note in the final report) — the agency
// is recovered from the id prefix, which is immutable and always present.
const AGENCY_ZH = {
  nbs: '国家统计局',
  pbc: '中国人民银行',
  mof: '财政部',
  mohurd: '住房和城乡建设部',
  customs: '海关总署',
  caam: '中国汽车工业协会',
  safe: '国家外汇管理局',
  cflp: '中国物流与采购联合会',
};

export function agencyZhFromId(id) {
  const prefix = String(id).split('-')[0];
  return AGENCY_ZH[prefix] || '官方数据';
}

export const AGENCY_LIST_ZH = Object.values(AGENCY_ZH);
