// app.mjs — page bootstrap. Masthead -> global range control -> 8 sections
// (first loaded eagerly, rest lazy on scroll) -> footer. VIZ-GUIDE §Page
// anatomy. This is the only module that touches `document` at the top level.

import { h, onIntersectOnce } from './lib/dom.mjs';
import { formatDateZh } from './lib/format.mjs';
import { RANGE_OPTIONS, DEFAULT_RANGE_KEY } from './lib/period.mjs';
import { AGENCY_LIST_ZH } from './lib/color.mjs';
import { setRangeKey } from './store.mjs';
import { fetchIndex, fetchSection } from './data/loader.mjs';
import { renderSection } from './components/section.mjs';
import { SITE_NAME, GITHUB_URL, SECTION_ORDER_FALLBACK } from './config.mjs';

async function main() {
  const root = document.getElementById('app');
  root.appendChild(buildMasthead());
  root.appendChild(buildRangeControl());

  const sectionsHost = h('div', { class: 'sections' });
  root.appendChild(sectionsHost);
  root.appendChild(buildFooter());

  let index;
  try {
    index = await fetchIndex();
  } catch (err) {
    sectionsHost.appendChild(h('p', { class: 'section-empty-note' }, '数据索引加载失败，请刷新重试。'));
    return;
  }

  updateFreshnessStamp(index);
  const sections = (index.sections && index.sections.length ? index.sections : SECTION_ORDER_FALLBACK.map((id) => ({ id, name_zh: id })))
    .slice()
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  buildNav(sections);

  sections.forEach((meta, i) => {
    const container = h('section', { class: 'data-section', id: meta.id, 'aria-labelledby': `${meta.id}-title` });
    container.appendChild(h('p', { class: 'section-loading-note' }, '加载中…'));
    sectionsHost.appendChild(container);

    const load = async () => {
      let bundle = null;
      try {
        bundle = await fetchSection(meta.id);
      } catch (err) {
        renderSectionError(container, meta);
        return;
      }
      renderSection(container, { meta, bundle });
    };

    // Network discipline: only the FIRST section bundle loads eagerly with
    // index.json; every other section fetches lazily when scrolled near.
    if (i === 0) load();
    else onIntersectOnce(container, load);
  });
}

function buildMasthead() {
  return h('header', { class: 'masthead' }, [
    h('h1', { class: 'site-name' }, SITE_NAME),
    h('p', { class: 'freshness-stamp', id: 'freshnessStamp' }, ''),
  ]);
}

function updateFreshnessStamp(index) {
  const el = document.getElementById('freshnessStamp');
  if (el) el.textContent = `数据截至 ${formatDateZh(index.generated_at)}`;
}

function buildNav(sections) {
  const nav = h(
    'nav',
    { class: 'section-nav', 'aria-label': '板块导航' },
    sections.map((s) => h('a', { href: `#${s.id}` }, s.name_zh)),
  );
  document.querySelector('.masthead').appendChild(nav);
}

function buildRangeControl() {
  const group = h('div', { class: 'range-control', role: 'group', 'aria-label': '时间范围' });
  const buttons = RANGE_OPTIONS.map((opt) =>
    h(
      'button',
      {
        type: 'button',
        class: opt.key === DEFAULT_RANGE_KEY ? 'active' : '',
        onClick: (e) => {
          setRangeKey(opt.key);
          for (const b of group.querySelectorAll('button')) b.classList.remove('active');
          e.currentTarget.classList.add('active');
        },
      },
      opt.label,
    ),
  );
  group.append(...buttons);
  return group;
}

function renderSectionError(container, meta) {
  container.textContent = '';
  container.appendChild(h('h2', { class: 'section-title' }, meta.name_zh));
  container.appendChild(h('p', { class: 'section-empty-note' }, '该板块数据加载失败，请刷新重试。'));
}

function buildFooter() {
  return h('footer', { class: 'site-footer' }, [
    h('p', { class: 'footer-sources' }, `资料来源：${AGENCY_LIST_ZH.join(' · ')}`),
    h('nav', { class: 'footer-links', 'aria-label': '页脚链接' }, [
      h('a', { href: `${GITHUB_URL}/blob/main/docs/VIZ-GUIDE.md`, target: '_blank', rel: 'noreferrer' }, '方法论'),
      h('a', { href: `${GITHUB_URL}/commits/main/data`, target: '_blank', rel: 'noreferrer' }, '数据日志'),
      h('a', { href: GITHUB_URL, target: '_blank', rel: 'noreferrer' }, 'GitHub'),
    ]),
  ]);
}

main();
