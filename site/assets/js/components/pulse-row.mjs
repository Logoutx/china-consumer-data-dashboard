// components/pulse-row.mjs — Tier-3: compact row of name, latest value, a
// tiny sparkline, delta in 红涨绿跌. The most restrained item in the kit —
// no chart surface, no gridlines, just the four facts VIZ-GUIDE asks for.

import { h } from '../lib/dom.mjs';
import { upDownColor } from '../lib/color.mjs';
import { formatPercent, formatNumber } from '../lib/format.mjs';
import { buildSparklineSvg } from './sparkline.mjs';

/**
 * @param entry a section-bundle series entry (tier 3)
 */
export function buildPulseRow(entry) {
  const isCount = entry.value_type === 'count';
  const useYoy = !isCount && entry.yoy_series.some((p) => p.yoy !== null);
  const trend = useYoy ? entry.yoy_series.map((p) => p.yoy) : entry.level_series.map((p) => p.m);
  const latest = entry.latest;
  const delta = entry.headline ? entry.headline.latest_yoy : null;

  const latestValueText = latest
    ? isCount
      ? formatNumber(latest.m, 0)
      : formatNumber(latest.m ?? latest.ytd)
    : '—';

  const row = h('div', { class: 'pulse-row' });
  row.appendChild(h('span', { class: 'pulse-name' }, entry.name_zh));
  const sparkWrap = h('span', { class: 'pulse-spark' }, buildSparklineSvg(trend.slice(-24), { color: 'var(--context)' }));
  row.appendChild(sparkWrap);
  row.appendChild(h('span', { class: 'pulse-value' }, latestValueText));
  row.appendChild(
    h('span', { class: 'pulse-delta', style: { color: upDownColor(delta) } }, delta === null ? '—' : formatPercent(delta, null, { sign: true })),
  );
  return row;
}

export function mountPulseList(container, entries) {
  const list = h('div', { class: 'pulse-list' });
  for (const entry of entries) {
    if (!entry.latest) continue; // per-series empty state: quietly omit rather than show a broken row
    list.appendChild(buildPulseRow(entry));
  }
  container.appendChild(list);
}
