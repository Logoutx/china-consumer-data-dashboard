// app.mjs — the front page (site/index.html). Restructure task 3: "one
// block per section: section name + its lead tier-1 takeaway + mini + link
// — build from the existing index.json tiles."
//
// Data-shape note: index.json's tiles/freshness arrays don't carry a
// `section` back-reference (they're a flat cross-section list — see
// DATA-CONTRACT §10.1), so there's no way to filter them down to "this
// section's lead tile" without either a fragile id-prefix guess or
// hardcoding a maintained id->section map in site code (both rejected —
// see DEV-NOTES.md). Instead this fetches each section's OWN bundle (8
// small-to-medium requests, once, on the one page whose whole job is to
// summarize all 8) and reads its first tier-1 entry directly — the
// authoritative source, not a guess. Each block fetches lazily (below-the-
// fold extra, like small-multiples/pulse-rows/the city panel — see
// lib/dom.mjs's onIntersectOnce) and renders as soon as ITS fetch resolves,
// not gated on the others.

import { h, clear, onIntersectOnce } from './lib/dom.mjs';
import { mountShell } from './page-shell.mjs';
import { fetchIndex, fetchSection } from './data/loader.mjs';
import { upDownColor } from './lib/color.mjs';
import { buildSparklineSvg } from './components/sparkline.mjs';
import { primarySeriesValues, displayName } from './components/section.mjs';

async function main() {
  const root = document.getElementById('app');

  let index;
  try {
    index = await fetchIndex();
  } catch (err) {
    console.error('[app] index.json fetch failed:', err);
    root.appendChild(h('p', { class: 'section-empty-note' }, '数据索引加载失败，请刷新重试。'));
    return;
  }

  const sections = (index.sections || []).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const footer = mountShell(root, { sections, currentPageId: 'index', generatedAt: index.generated_at });

  const grid = h('div', { class: 'overview-grid' });
  root.appendChild(grid);
  root.appendChild(footer);

  for (const meta of sections) {
    const block = h('a', { class: 'overview-block', href: `${meta.id}.html` });
    block.appendChild(h('h2', { class: 'overview-title' }, meta.name_zh));
    const body = h('div', { class: 'overview-body' }, h('p', { class: 'chart-empty-note' }, '加载中…'));
    block.appendChild(body);
    grid.appendChild(block);

    // Coordinator hardening: front-page minis are below-the-fold extras on
    // a grid page (unlike a section page's tier-1 hero), so they lazy-load
    // like small-multiples/pulse-rows/the city panel, not eagerly for all 8.
    onIntersectOnce(block, () => {
      fetchSection(meta.id)
        .then((bundle) => fillOverviewBlock(body, bundle))
        .catch((err) => {
          console.error(`[app] overview fetch failed for "${meta.id}":`, err);
          clear(body);
          body.appendChild(h('p', { class: 'render-error-note' }, '该板块数据加载失败'));
        });
    });
  }
}

function fillOverviewBlock(body, bundle) {
  clear(body);
  const series = Array.isArray(bundle?.series) ? bundle.series : [];
  const lead = series.find((s) => s.tier === 1 && s.latest);
  if (!lead) {
    body.appendChild(h('p', { class: 'chart-empty-note' }, '数据接入中 · 该板块序列正在补充'));
    return;
  }

  const name = displayName(lead);
  const takeawayText = lead.takeaway && lead.takeaway !== lead.name_zh && lead.takeaway !== name ? lead.takeaway : name;
  body.appendChild(h('p', { class: 'overview-takeaway' }, takeawayText));

  const { isPercent } = primarySeriesValues(lead);
  const delta = lead.headline ? lead.headline.latest_yoy : null;
  const sparkColor = isPercent ? upDownColor(delta) : 'var(--context)';
  const spark = Array.isArray(lead.spark) ? lead.spark : [];
  if (spark.length > 1) {
    body.appendChild(h('div', { class: 'overview-spark' }, buildSparklineSvg(spark, { width: 120, height: 32, color: sparkColor })));
  }
  body.appendChild(h('span', { class: 'overview-link-label' }, '查看详情 →'));
}

main();
