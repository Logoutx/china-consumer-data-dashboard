// tests.dom.mjs — DOM-dependent tests, runnable with:
//   node --test site/assets/js/tests.dom.mjs
// (tests.mjs stays pure-function-only by design; this file is specifically
// for the design-review ask that can't be verified without a real render
// path: "add a node --test that renders the same chart 3x through the
// resize path and asserts overlay child count is stable" — i.e. that
// mountLineChart's re-render is idempotent (clear-then-draw), not
// accumulating stray DOM nodes across repeated resize events. A minimal,
// self-contained DOM/browser shim is set up below (not a real browser —
// no ResizeObserver/IntersectionObserver spec compliance beyond what these
// tests need), scoped to this file only.

import { test } from 'node:test';
import assert from 'node:assert/strict';

// -- minimal DOM/browser shim (this file only) ---------------------------------------

class Node {}

class FakeStyle {
  setProperty() {}
  // `display` is a plain settable property (no getter/setter needed — JS
  // objects allow arbitrary property assignment by default); listed here
  // only for clarity that this shim intentionally tracks it, unlike other
  // style properties this file doesn't need to simulate.
}

class FakeClassList {
  constructor(el) {
    this.el = el;
  }
  add(c) {
    if (!this.el._classes.includes(c)) this.el._classes.push(c);
  }
  remove(c) {
    this.el._classes = this.el._classes.filter((x) => x !== c);
  }
}

class FakeElement extends Node {
  constructor(tag) {
    super();
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this._classes = [];
    this.style = new FakeStyle();
    this._text = '';
    this.classList = new FakeClassList(this);
    this._listeners = {};
  }
  appendChild(child) {
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  append(...kids) {
    for (const k of kids) this.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  }
  insertBefore(newNode, refNode) {
    const idx = refNode ? this.children.indexOf(refNode) : -1;
    if (idx === -1) this.children.push(newNode);
    else this.children.splice(idx, 0, newNode);
    newNode.parentNode = this;
    return newNode;
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
  }
  removeChild(child) {
    this.children = this.children.filter((c) => c !== child);
    return child;
  }
  get firstChild() {
    return this.children[0] || null;
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === 'class') this._classes = String(v).split(/\s+/).filter(Boolean);
  }
  getAttribute(k) {
    return this.attributes[k];
  }
  addEventListener(type, fn) {
    (this._listeners[type] ||= []).push(fn);
  }
  set textContent(v) {
    this._text = v;
    this.children = [];
  }
  get textContent() {
    return this._text;
  }
  set className(v) {
    this.setAttribute('class', v);
  }
  scrollIntoView() {}
  _hasHiddenAncestor() {
    let node = this;
    while (node) {
      if (node.style && node.style.display === 'none') return true;
      node = node.parentNode;
    }
    return false;
  }
  getBoundingClientRect() {
    // A display:none element (or descendant of one) has no box at all —
    // real browsers report an all-zero rect; this shim simulates that
    // specifically because the city-detail bug this file guards against was
    // exactly a container measured while its display:none ancestor hadn't
    // been flipped visible yet. Otherwise configurable via el._rect =
    // {top, bottom, ...}, defaulting to "in view".
    if (this._hasHiddenAncestor()) return { width: 0, height: 0, top: 0, bottom: 0, left: 0, right: 0 };
    return this._rect || { width: 800, height: 260, top: 0, bottom: 260, left: 0, right: 800 };
  }
}

class FakeTextNode extends Node {
  constructor(text) {
    super();
    this.nodeType = 3;
    this.textContent = text;
  }
}

global.Node = Node;
global.document = {
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_ns, tag) => new FakeElement(tag),
  createTextNode: (t) => new FakeTextNode(t),
  getElementById: () => null,
  querySelector: () => null,
  documentElement: { clientHeight: 800 },
};

// Controllable window event bus: lets a test simulate a `scroll`/`resize`
// event firing (the third onIntersectOnce trigger path — see lib/dom.mjs)
// by calling global.__fireWindowEvent(type).
const windowListeners = {};
global.__fireWindowEvent = (type) => {
  for (const fn of windowListeners[type] || []) fn();
};
global.window = {
  innerHeight: 800,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener: (type, fn) => {
    (windowListeners[type] ||= []).push(fn);
  },
  removeEventListener: (type, fn) => {
    windowListeners[type] = (windowListeners[type] || []).filter((f) => f !== fn);
  },
};
global.location = { search: '', hash: '' };
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);

// Controllable width: each onResize() call gets its OWN ResizeObserver
// instance; observe() fires synchronously with whatever width is currently
// set on `resizeWidths` for that call index, letting the test drive
// "render 3x through the resize path" deterministically.
let resizeCallIndex = 0;
const resizeWidthsQueue = [];
global.ResizeObserver = class {
  constructor(cb) {
    this.cb = cb;
    this.callIndex = resizeCallIndex++;
  }
  observe() {
    const width = resizeWidthsQueue[this.callIndex] ?? 800;
    this.cb([{ contentRect: { width, height: 260 } }]);
  }
  disconnect() {}
};
// Controllable (this file's default, used by the idempotency test below):
// never fires on its own. The city-grid test further below installs its
// OWN instance that records itself so the test can manually fire
// isIntersecting:true to simulate a real scroll -- a real browser's
// IntersectionObserver fires asynchronously on genuine scroll, which this
// class deliberately does NOT emulate automatically, so a test must fire it
// explicitly and can distinguish "fired via the sync in-view pre-check"
// from "fired via a simulated scroll".
global.IntersectionObserver = class {
  constructor(cb) {
    this.cb = cb;
    global.__lastIO = this; // lets a test fire the most recently constructed observer, simulating a real scroll
  }
  observe(el) {
    this.el = el;
  }
  unobserve() {}
  disconnect() {}
};

function countOverlayChildren(container) {
  // container -> .chart-mount div -> [.chart-surface, .chart-footnotes?]
  let total = 0;
  const walk = (node) => {
    total += (node.children || []).length;
    for (const c of node.children || []) walk(c);
  };
  walk(container);
  return total;
}

// -- the actual test --------------------------------------------------------------

const { mountLineChart } = await import('./components/line-chart.mjs');

test('mountLineChart re-render is idempotent: rendering the same chart 3x through the resize path does not accumulate overlay/marker DOM nodes (design-review stray-lines fix)', () => {
  const container = new FakeElement('div');

  const values = [
    { period: '2024-11', value: 1.8 },
    { period: '2024-12', value: 1.9 },
    { period: '2025-01', value: null }, // inside a no_yoy_across break window
    { period: '2025-02', value: null },
    { period: '2025-03', value: 5.0 },
    { period: '2025-04', value: 5.1 },
    { period: '2025-05', value: 5.0 },
  ];
  const breaks = [{ effective: '2025-01', kind: 'redefinition', no_yoy_across: true, note_zh: 'M1 口径调整。' }];

  const props = {
    ariaLabel: 'M1 同比增长',
    seriesList: [{ id: 'pbc-m1', name: 'M1', values, derived: false, colorVar: '--accent-red' }],
    valueFormatter: (v) => `${v}%`,
    isPercent: true,
    unitLabel: '同比 %',
    annotations: [],
    breaks,
    caliber: null,
  };

  mountLineChart(container, props);
  const countAfterMount = countOverlayChildren(container);
  assert.ok(countAfterMount > 0, 'sanity: something was actually drawn');

  // Simulate 3 additional resize events (e.g. sidebar toggling, font
  // loading reflow, orientation change) by re-creating the chart against
  // freshly-queued widths -- the resize path is exercised via the
  // ResizeObserver shim firing on each new mountLineChart() call below,
  // mirroring what onResize's debounced handler does inside the real
  // component on each actual resize.
  resizeWidthsQueue.push(800, 600, 900, 800);
  resizeCallIndex = 0;

  const counts = [];
  for (let i = 0; i < 4; i++) {
    mountLineChart(container, props); // each call clears + rebuilds container fresh, like a real re-render
    counts.push(countOverlayChildren(container));
  }

  // The bug this guards against: overlay/marker elements accumulating on
  // re-render instead of being cleared first. Every one of these repeated
  // renders (at the SAME or DIFFERENT widths) must produce the same DOM
  // node count -- if any earlier render's nodes had leaked through, later
  // counts would only grow, never shrink back to match.
  const allEqual = counts.every((c) => c === counts[0]);
  assert.ok(allEqual, `overlay child count must be stable across repeated renders, got: ${JSON.stringify(counts)}`);
  assert.equal(counts[0], countAfterMount, 're-render must reproduce the exact same node count as the first mount, not more');
});

// -- city-grid lazy-load regression: the panel URL must actually be requested -----------
//
// Coordinator's report: property.html's 70-city grid never loads in a real
// browser (panel URL never appears in the network log). Hypothesis was a
// private IntersectionObserver bypassing the onIntersectOnce sync-check fix
// — grep ("new IntersectionObserver" across site/assets/js) finds exactly
// ONE construction site, inside lib/dom.mjs's onIntersectOnce, and
// city-grid.mjs already imports and calls that shared helper (no private
// observer exists to fix). This test is the browser-faithful check the
// coordinator asked for regardless: it asserts the PANEL FETCH actually
// fires (not just that cells eventually render, which a lenient shim could
// paper over) in both the "below the fold, needs a scroll" and "already in
// view at load" cases.

const MINI_PANEL = {
  dimensions: { city: ['北京', '上海'], metric: ['new_home', 'resale_home'] },
  measures: ['m', 'm_yoy'],
  periods: ['2026-04', '2026-05'],
  cells: {
    北京: { new_home: { m: [-0.2, -0.3], m_yoy: [-2.1, -2.5] }, resale_home: { m: [-0.5, -0.6], m_yoy: [-3.0, -3.2] } },
    上海: { new_home: { m: [0.1, 0.2], m_yoy: [1.0, 1.2] }, resale_home: { m: [-0.1, null], m_yoy: [-0.5, null] } },
  },
  latest_by_city: {
    北京: { new_home: { m: -0.3, m_yoy: -2.5 }, resale_home: { m: -0.6, m_yoy: -3.2 } },
    上海: { new_home: { m: 0.2, m_yoy: 1.2 }, resale_home: { m: null, m_yoy: null } },
  },
};

const { mountCityGrid } = await import('./components/city-grid.mjs');
const { onIntersectOnce } = await import('./lib/dom.mjs');

function fakeFetchPanel(calls) {
  return () => {
    calls.push('panels/nbs-70city-price.json');
    return Promise.resolve(MINI_PANEL);
  };
}

test('onIntersectOnce: vh falls back through innerHeight -> documentElement.clientHeight -> 800 (never crashes/stalls when the first two report 0, a real webview quirk seen this session)', () => {
  const savedInnerHeight = global.window.innerHeight;
  const savedClientHeight = global.document.documentElement.clientHeight;
  global.window.innerHeight = 0;
  global.document.documentElement.clientHeight = 0;
  try {
    const el = new FakeElement('div');
    el._rect = { top: 100, bottom: 360 }; // "in view" by any real viewport
    let fired = false;
    onIntersectOnce(el, () => {
      fired = true;
    });
    // Even with innerHeight AND clientHeight both reporting 0, the sync
    // check's `|| 800` fallback must still recognize this as in-view rather
    // than silently never firing.
    assert.equal(fired, true, 'the vh fallback chain must still recognize an in-view element when both real metrics report 0');
  } finally {
    global.window.innerHeight = savedInnerHeight;
    global.document.documentElement.clientHeight = savedClientHeight;
  }
});

test('onIntersectOnce: third trigger path — a passive scroll event fires cb() for a genuinely below-the-fold element once it scrolls into range, even with IntersectionObserver inert', () => {
  const savedIO = global.IntersectionObserver;
  global.IntersectionObserver = class {
    observe() {} // inert -- never calls back, matching the reported webview quirk
    unobserve() {}
    disconnect() {}
  };

  try {
    const el = new FakeElement('div');
    el._rect = { top: 5000, bottom: 5260 }; // genuinely far below the fold -- neither the sync check nor a real IO should fire yet
    let fired = false;
    onIntersectOnce(el, () => {
      fired = true;
    });
    assert.equal(fired, false, 'must not fire yet: element is far below the fold and IntersectionObserver is inert');

    global.__fireWindowEvent('scroll'); // element hasn't moved -- still below the fold
    assert.equal(fired, false, 'a scroll event alone does not fire it -- the element must actually BE in range');

    el._rect = { top: 100, bottom: 360 }; // the user has now scrolled it into view
    global.__fireWindowEvent('scroll');
    assert.equal(fired, true, 'once the element is actually in range, the scroll-listener fallback must fire cb() without any IntersectionObserver support');
  } finally {
    global.IntersectionObserver = savedIO;
  }
});

test('city-grid: panel fetch does NOT fire while the grid is below the fold at cold load', () => {
  const container = new FakeElement('div');
  container._rect = { top: 5000, bottom: 5260 }; // far below a typical viewport
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  assert.equal(calls.length, 0, 'the sync in-view check must not fire for an off-screen container');
});

test('city-grid: scrolling the grid into view fires the panel fetch (simulated IntersectionObserver callback)', async () => {
  const container = new FakeElement('div');
  container._rect = { top: 5000, bottom: 5260 };
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  assert.equal(calls.length, 0);

  // Find the IntersectionObserver instance onIntersectOnce created for this
  // container and fire it, simulating the browser reporting a real scroll.
  // (This shim's IntersectionObserver is a singleton "last instance"
  // recorder in the base case; city-grid mounts exactly one observer, so
  // grabbing the shim's shared `global.__lastIO` set below is sufficient.)
  assert.ok(global.__lastIO, 'onIntersectOnce must have constructed an IntersectionObserver for the grid container');
  global.__lastIO.cb([{ isIntersecting: true, target: container }]);
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(calls.length, 1, 'the panel fetch must fire exactly once after a simulated scroll-into-view');
});

test('city-grid: panel fetch fires IMMEDIATELY when the grid is already in the viewport at load (no scroll needed)', () => {
  const container = new FakeElement('div');
  container._rect = { top: 100, bottom: 360 }; // within the 800px viewport used by this shim
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  assert.equal(calls.length, 1, 'an already-in-view grid must fetch its panel synchronously, without waiting on a scroll event');
});

test('city-grid: group-average charts and city click-expand render after a successful panel load', async () => {
  const container = new FakeElement('div');
  container._rect = { top: 100, bottom: 360 };
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  await new Promise((r) => setTimeout(r, 10)); // let the panel promise resolve and the grid render

  const groupView = findByClass(container, 'city-group-view');
  assert.ok(groupView && groupView.children.length > 0, 'the grouped-average view must render once the panel loads');

  const grid = findByClass(container, 'city-grid');
  const firstMini = grid.children.find((c) => c._classes.includes('city-mini'));
  assert.ok(firstMini, 'at least one city mini must render');

  // Simulate a click-expand: city-grid.mjs wires this via addEventListener,
  // so invoke the registered handler directly (this shim doesn't dispatch
  // real events).
  const clickHandlers = firstMini._listeners.click || [];
  assert.ok(clickHandlers.length > 0, 'each city mini must have a click handler wired for expand');
  clickHandlers[0]();

  const detail = findByClass(container, 'city-detail');
  assert.ok(detail && detail.children.length > 0, 'clicking a city mini must render its detail expand');
});

function findByClass(root, className) {
  if (root._classes && root._classes.includes(className)) return root;
  for (const c of root.children || []) {
    const found = findByClass(c, className);
    if (found) return found;
  }
  return null;
}

function findAllByClass(root, className, out = []) {
  if (root._classes && root._classes.includes(className)) out.push(root);
  for (const c of root.children || []) findAllByClass(c, className, out);
  return out;
}

// -- zero-width mount fragility (coordinator finding #2) --------------------------------

const { ensureMeasuredWidth } = await import('./lib/dom.mjs');

test('ensureMeasuredWidth: mounts into a 0-width container, then fires once the container widens and a resize event dispatches', async () => {
  const el = new FakeElement('div');
  el._rect = { width: 0, height: 0, top: 0, bottom: 0, left: 0, right: 0 };

  let calledWith = null;
  ensureMeasuredWidth(el, (width) => {
    calledWith = width;
  });
  assert.equal(calledWith, null, 'must not fire while the container is genuinely 0-width');

  await new Promise((r) => setTimeout(r, 5)); // let the requestAnimationFrame retry run -- still 0-width, still must not fire
  assert.equal(calledWith, null);

  el._rect = { width: 640, height: 260, top: 0, bottom: 260, left: 0, right: 640 }; // container widened
  global.__fireWindowEvent('resize');
  assert.equal(calledWith, 640, 'the scroll/resize fallback must fire cb(width) once a nonzero width is measurable');
});

// -- city-detail full-width + toggle regression (coordinator finding #1) ----------------

test('city-grid detail expand: charts mount with a REAL width, not 0 (display:none/visible ordering fix)', async () => {
  const container = new FakeElement('div');
  container._rect = { top: 100, bottom: 360 };
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  await new Promise((r) => setTimeout(r, 10));

  const firstMini = findByClass(container, 'city-mini');
  firstMini._listeners.click[0](); // expand the first (top-sorted) city

  const detail = findByClass(container, 'city-detail');
  assert.equal(detail.style.display, '', 'detail must be visible (not display:none) once expanded');
  const svgCount = findAllByClass(detail, 'chart-svg').length;
  assert.ok(svgCount > 0, `expected chart SVGs to render into a REAL (nonzero) width, got ${svgCount}`);
});

test('city-grid detail expand: switching cities replaces the detail (exactly 2 chart slots, never 4); re-clicking the same city closes it', async () => {
  // Scoped to `.city-detail` specifically, NOT the whole container: the
  // group-average view (always-on, renders as soon as the panel loads)
  // reuses the SAME `.city-detail-chart` layout class for its own two
  // charts, so counting matches across the whole grid double-counts those
  // — a false "4 slots" that isn't the accumulation bug being tested here.
  const container = new FakeElement('div');
  container._rect = { top: 100, bottom: 360 };
  const calls = [];
  mountCityGrid(container, { fetchPanel: fakeFetchPanel(calls) });
  await new Promise((r) => setTimeout(r, 10));

  const grid = findByClass(container, 'city-grid');
  const minis = grid.children.filter((c) => c._classes.includes('city-mini'));
  assert.ok(minis.length >= 2, 'need at least 2 cities for this test');
  const detail = findByClass(container, 'city-detail');

  minis[0]._listeners.click[0](); // expand city A
  let chartSlots = findAllByClass(detail, 'city-detail-chart');
  assert.equal(chartSlots.length, 2, 'exactly 2 chart slots (环比/同比) after the first expand, not accumulated');

  minis[1]._listeners.click[0](); // expand city B -- must REPLACE, not append
  chartSlots = findAllByClass(detail, 'city-detail-chart');
  assert.equal(chartSlots.length, 2, 'switching to a different city must still be exactly 2 slots, never 4');
  assert.equal(detail.style.display, '', 'detail stays open when switching cities');

  minis[1]._listeners.click[0](); // re-click the SAME (currently open) city -- must close
  assert.equal(detail.style.display, 'none', 'clicking the currently-open city again must close the detail');
  chartSlots = findAllByClass(detail, 'city-detail-chart');
  assert.equal(chartSlots.length, 0, 'closing must clear the detail content, not just hide it');
});
