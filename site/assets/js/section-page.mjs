// section-page.mjs — bootstrap shared by all 8 section pages (prices.html,
// consumption.html, income-confidence.html, employment.html, property.html,
// money-credit.html, macro.html, high-frequency.html). Each page sets
// <body data-section="..."> to say which one it is; this script reads that,
// fetches index.json (for the shell's freshness stamp + nav) and that one
// section's own bundle, and renders exactly that section's content — no
// other section's data is ever fetched from a section page (restructure
// task 3: "each rendering exactly its current section content"). The
// 70-city panel (property.html only) still lazy-loads within the page via
// city-grid.mjs's onIntersectOnce (lib/dom.mjs's shared helper — there is
// no private observer anywhere in this codebase; grep for "new
// IntersectionObserver" turns up exactly one construction site).

import { h } from './lib/dom.mjs';
import { mountShell } from './page-shell.mjs';
import { fetchIndex, fetchSection } from './data/loader.mjs';
import { renderSection } from './components/section.mjs';

async function main() {
  const sectionId = document.body.dataset.section;
  const root = document.getElementById('app');

  let index = null;
  try {
    index = await fetchIndex();
  } catch (err) {
    console.error('[section-page] index.json fetch failed:', err);
  }

  const sections = index && index.sections && index.sections.length ? index.sections.slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)) : [];
  const meta = sections.find((s) => s.id === sectionId) || { id: sectionId, name_zh: sectionId };

  const footer = mountShell(root, { sections, currentPageId: sectionId, generatedAt: index?.generated_at });

  const container = h('section', { class: 'data-section', id: meta.id, 'aria-labelledby': `${meta.id}-title` });
  container.appendChild(h('p', { class: 'section-loading-note' }, '加载中…'));
  root.appendChild(container);
  root.appendChild(footer);

  try {
    const bundle = await fetchSection(sectionId);
    try {
      renderSection(container, { meta, bundle });
    } catch (err) {
      console.error(`[section-page] renderSection failed for "${sectionId}":`, err);
      renderError(container, meta);
    }
  } catch (err) {
    console.error(`[section-page] fetch failed for "${sectionId}":`, err);
    renderError(container, meta);
  }
}

function renderError(container, meta) {
  container.textContent = '';
  container.appendChild(h('h2', { class: 'section-title' }, meta.name_zh));
  container.appendChild(h('p', { class: 'section-empty-note' }, '该板块数据加载失败，请刷新重试。'));
}

main();
