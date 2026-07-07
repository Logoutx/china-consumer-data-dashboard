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
    console.error('[app] index.json fetch failed:', err);
    sectionsHost.appendChild(h('p', { class: 'section-empty-note' }, '数据索引加载失败，请刷新重试。'));
    return;
  }

  updateFreshnessStamp(index);
  const sections = (index.sections && index.sections.length ? index.sections : SECTION_ORDER_FALLBACK.map((id) => ({ id, name_zh: id })))
    .slice()
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  buildNav(sections);

  const records = []; // {id, container, load} per section, for the hash-deep-link handling below

  sections.forEach((meta, i) => {
    const container = h('section', { class: 'data-section', id: meta.id, 'aria-labelledby': `${meta.id}-title` });
    container.appendChild(h('p', { class: 'section-loading-note' }, '加载中…'));
    sectionsHost.appendChild(container);

    const load = async () => {
      let bundle = null;
      try {
        bundle = await fetchSection(meta.id);
      } catch (err) {
        // Design-review item 1: was silently swallowed (no console.error) —
        // the exact complaint from review ("cost me a debugging round").
        console.error(`[app] fetch failed for section "${meta.id}":`, err);
        renderSectionError(container, meta);
        return;
      }
      try {
        // CONFIRMED gap found during the employment-crash investigation:
        // renderSection() used to be called outside any try/catch here, so
        // any exception it threw (now or from any future data shape) became
        // an unhandled promise rejection — no console.error, and the section
        // was left half-built with no error message at all. Root-cause note:
        // could not reproduce a synchronous throw against the CURRENT
        // employment.json bundle across every range option, both mobile and
        // desktop widths, and repeated re-renders (see lib/safe.mjs's module
        // comment) — this catch, plus the per-series isolation added to
        // section.mjs/small-multiples.mjs/pulse-row.mjs/city-grid.mjs, is the
        // hardening fix regardless of the original trigger.
        renderSection(container, { meta, bundle });
      } catch (err) {
        console.error(`[app] renderSection failed for section "${meta.id}":`, err);
        renderSectionError(container, meta);
      }
    };

    records.push({ id: meta.id, container, load });

    // Network discipline: only the FIRST section bundle loads eagerly with
    // index.json; every other section fetches lazily when scrolled near.
    // (onIntersectOnce also has its own synchronous "already in view" check
    // now — see dom.mjs — as a second, independent safeguard for the same
    // failure mode this hash handling targets.)
    if (i === 0) load();
    else onIntersectOnce(container, load);
  });

  // Regression fix: a direct load at /site/#employment (or any #<section-id>
  // URL) targets an element that doesn't exist until this point — the
  // browser's native "scroll to fragment" has already had its one chance to
  // find it and failed, silently, well before this line runs. Handle it
  // ourselves: find the matching section (if the hash names one) and both
  // scroll to it and load it directly, rather than depending on whatever
  // scroll position the browser's failed native attempt left behind plus an
  // IntersectionObserver tick that may or may not follow. Calling load()
  // again for the i===0 section (already eager-loaded) is harmless —
  // fetchSection caches by URL and renderSection clears-then-rebuilds.
  const hashId = location.hash.replace(/^#/, '');
  if (hashId) {
    const target = records.find((r) => r.id === hashId);
    if (target) {
      target.container.scrollIntoView({ block: 'start' });
      target.load();
    }
  }
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
