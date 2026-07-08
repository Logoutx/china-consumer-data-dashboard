// page-shell.mjs — shared chrome for every page: masthead (site name +
// freshness stamp), the one global range control, nav (marks the current
// page), and the footer. Used by app.mjs (the front-page overview) and
// section-page.mjs (the 8 per-section pages) — restructure task 3: "one
// page per section... nav bar on every page marks the current page; shared
// modules/CSS stay shared."

import { h } from './lib/dom.mjs';
import { formatDateZh } from './lib/format.mjs';
import { RANGE_OPTIONS } from './lib/period.mjs';
import { AGENCY_LIST_ZH } from './lib/color.mjs';
import { setRangeKey, getRangeKey } from './store.mjs';
import { SITE_NAME, GITHUB_URL, SECTION_ORDER_FALLBACK } from './config.mjs';

/** Every page's link target: the front page ("") plus one per section (its own .html). */
export function pageList(sections) {
  const list = sections && sections.length ? sections : SECTION_ORDER_FALLBACK.map((id) => ({ id, name_zh: id }));
  return [{ id: 'index', name_zh: '总览', href: 'index.html' }, ...list.map((s) => ({ ...s, href: `${s.id}.html` }))];
}

/**
 * Builds masthead + range control + nav into `root`, and returns the
 * footer (append it after the page's own content). `currentPageId` is
 * "index" or a section id — whichever nav link matches gets marked active
 * (aria-current) so every page states where you are.
 */
export function mountShell(root, { sections, currentPageId, generatedAt }) {
  const masthead = h('header', { class: 'masthead' }, [
    h('h1', { class: 'site-name' }, h('a', { href: 'index.html', class: 'site-name-link' }, SITE_NAME)),
    h('p', { class: 'freshness-stamp' }, generatedAt ? `数据截至 ${formatDateZh(generatedAt)}` : ''),
  ]);
  root.appendChild(masthead);

  const nav = h(
    'nav',
    { class: 'section-nav', 'aria-label': '板块导航' },
    pageList(sections).map((p) =>
      h(
        'a',
        {
          href: p.href,
          'aria-current': p.id === currentPageId ? 'page' : null,
          class: p.id === currentPageId ? 'active' : '',
        },
        p.name_zh,
      ),
    ),
  );
  masthead.appendChild(nav);

  root.appendChild(buildRangeControl());

  return buildFooter();
}

function buildRangeControl() {
  const activeKey = getRangeKey();
  const group = h('div', { class: 'range-control', role: 'group', 'aria-label': '时间范围' });
  const buttons = RANGE_OPTIONS.map((opt) =>
    h(
      'button',
      {
        type: 'button',
        class: opt.key === activeKey ? 'active' : '',
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

