// data/loader.mjs — fetch + in-memory cache for site-data bundles.
// Network discipline (task spec): initial load = index.json + first section
// bundle only; every other section and the 70-city panel are fetched lazily
// via IntersectionObserver in app.mjs / city-grid.mjs. This module never
// prefetches anything on its own — it just dedupes concurrent/repeat
// requests for whatever the caller does ask for.

import { DATA_BASE } from '../config.mjs';

const cache = new Map();

export function fetchJSON(path) {
  const url = `${DATA_BASE}/${path}`;
  if (cache.has(url)) return cache.get(url);
  const promise = fetch(url, { credentials: 'omit' }).then((res) => {
    if (!res.ok) throw new Error(`fetch failed (${res.status}) for ${url}`);
    return res.json();
  });
  cache.set(url, promise);
  promise.catch(() => cache.delete(url)); // don't poison the cache with a failed fetch
  return promise;
}

export const fetchIndex = () => fetchJSON('index.json');
export const fetchSection = (sectionId) => fetchJSON(`sections/${sectionId}.json`);
export const fetchPanel = (panelId) => fetchJSON(`panels/${panelId}.json`);
