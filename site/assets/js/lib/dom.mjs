// lib/dom.mjs — small DOM/SVG construction helpers. Zero dependencies.

const SVG_NS = 'http://www.w3.org/2000/svg';

function applyAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
}

function appendChildren(node, children) {
  const arr = Array.isArray(children) ? children : [children];
  for (const c of arr) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
}

/** Create an HTML element. */
export function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

/** Create a namespaced SVG element. */
export function svgEl(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** True on pointer devices only (mouse/trackpad) — gates the hover tooltip; zero behavior on touch. */
export function supportsHover() {
  return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
}

export function debounce(fn, wait = 150) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** Fires cb(width, height) on container resize, debounced. Returns a disposer. */
export function onResize(el, cb, wait = 150) {
  const handler = debounce((entries) => {
    const entry = entries[entries.length - 1];
    const { width, height } = entry.contentRect;
    if (width > 0) cb(width, height);
  }, wait);
  const ro = new ResizeObserver(handler);
  ro.observe(el);
  return () => ro.disconnect();
}

const LAZY_MARGIN_PX = 200;

/**
 * Fires cb() once, the first time el scrolls near the viewport. Returns a
 * disposer.
 *
 * Regression fix (2026-07-08): a direct load at a URL fragment (e.g.
 * /site/#employment) targets a section container that doesn't exist yet —
 * app.mjs only builds it after fetchIndex() resolves — so the browser's
 * native "scroll to fragment" has nothing to find at the moment it would
 * normally try, and never retries once the element appears later via JS.
 * Relying solely on IntersectionObserver's first (always-async) callback
 * left that section's data never loading in this repro. This adds a
 * synchronous geometry check at the moment observe() is called: if the
 * element is ALREADY within the lazy-load margin right now — which can
 * happen for more reasons than the fragment case above (a short initial
 * page before other sections' content grows in, a very fast scroll before
 * earlier sections finish loading, browser scroll-restoration on
 * back/forward, ...) — cb() fires immediately rather than waiting on the
 * observer's async tick at all. `fired` makes firing from both paths safe:
 * whichever happens first wins, the other is a no-op.
 */
function inLazyRange(el) {
  const rect = el.getBoundingClientRect();
  const vh = window.innerHeight || document.documentElement.clientHeight || 800;
  return rect.bottom >= -LAZY_MARGIN_PX && rect.top <= vh + LAZY_MARGIN_PX;
}

export function onIntersectOnce(el, cb, opts = {}) {
  let fired = false;
  const fireOnce = () => {
    if (fired) return;
    fired = true;
    cb();
  };

  if (inLazyRange(el)) fireOnce();

  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        io.unobserve(el);
        fireOnce();
      }
    }
  }, { rootMargin: `${LAZY_MARGIN_PX}px 0px`, ...opts });
  io.observe(el);

  // Third trigger path (hardening): some embeds/webviews report a 0-height
  // viewport to IntersectionObserver/innerHeight (seen in an automation
  // context this session) — inert there, not broken in real browsers, but
  // cheap to guard anyway. A passive scroll/resize listener re-runs the same
  // bounding-rect check and removes itself the moment it fires once.
  const onScroll = () => {
    if (inLazyRange(el)) {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      fireOnce();
    }
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });

  return () => {
    io.disconnect();
    window.removeEventListener('scroll', onScroll);
    window.removeEventListener('resize', onScroll);
  };
}

/**
 * Calls cb(width) once `el` measures a nonzero width. Handles the
 * "mounted into a momentarily 0-width container" class of bug (the
 * city-detail bug above was a self-inflicted instance — rendering into a
 * still-display:none ancestor — but a fresh grid insert or a CSS transition
 * can produce the same 0-width mount without a display:none ordering bug
 * behind it): tries immediately, then once more after a
 * requestAnimationFrame (layout may simply not have run yet), then falls
 * back to the same passive scroll/resize retry onIntersectOnce's third path
 * uses, detaching itself the moment a nonzero width arrives.
 */
export function ensureMeasuredWidth(el, cb) {
  const measure = () => el.getBoundingClientRect().width;

  const immediate = measure();
  if (immediate) {
    cb(immediate);
    return;
  }

  const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (fn) => setTimeout(fn, 16);
  raf(() => {
    const afterFrame = measure();
    if (afterFrame) {
      cb(afterFrame);
      return;
    }

    const onEvent = () => {
      const width = measure();
      if (width) {
        window.removeEventListener('scroll', onEvent);
        window.removeEventListener('resize', onEvent);
        cb(width);
      }
    };
    window.addEventListener('scroll', onEvent, { passive: true });
    window.addEventListener('resize', onEvent, { passive: true });
  });
}
