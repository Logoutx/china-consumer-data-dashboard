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

/** Fires cb() once, the first time el scrolls near the viewport. Returns a disposer. */
export function onIntersectOnce(el, cb, opts = {}) {
  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        io.unobserve(el);
        cb();
      }
    }
  }, { rootMargin: '200px 0px', ...opts });
  io.observe(el);
  return () => io.disconnect();
}
