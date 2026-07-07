# DEV-NOTES — manual verification checklist

`site/assets/js/tests.mjs` (`node --test site/assets/js/tests.mjs`, 38 cases) covers every pure
function — scale math, tick rounding/trimming, pangu formatting, path building, and the
design-review decision functions pulled out of `section.mjs` (headline/dek fallback, caliber
feature-detection, source-line construction). This file is for everything that needs an actual
browser: layout, collision avoidance, interaction, dark mode.

## How to run locally

```
python3 -m http.server 8123   # from the repo root
open http://127.0.0.1:8123/site/
```

`?data=../site-data-fixtures` points the page at the synthetic fixtures bundle instead of the
real one — useful below, since real data currently has zero breaks/annotations and only one
series with both calibers (`nbs-retail-total`).

## Regression: a section already intersecting when its observer attaches

Repro: load `/site/#employment` directly (not by scrolling there) — 就业 hung at "加载中…"
forever. `fetch` returned 200 with a healthy, freshly-rebuilt bundle; zero console output;
`renderSection` never ran at all (not thrown — never called). Root cause: `app.mjs` only builds
a section's container element after `fetchIndex()` resolves, so a URL fragment naming that
section has nothing to find at the moment the browser would normally try to scroll to it — that
attempt fails once, silently, and browsers don't retry a same-page fragment scroll indefinitely
as content appears later via JS. Whatever scroll position that leaves the page at, the deeper
issue is general, not specific to anchors: `onIntersectOnce` (`lib/dom.mjs`) relied solely on
`IntersectionObserver`'s first callback, which is always asynchronous — even for a target that's
ALREADY within the load margin the instant `observe()` is called. Anything that can put a lazy
section at/near the viewport before its observer attaches (a direct anchor load, a very short
page before other sections' content grows in, fast scroll-restoration on back/forward) hits the
same class of gap.

Fixed two ways, deliberately overlapping:

1. `onIntersectOnce` now does a synchronous `getBoundingClientRect()` check the moment it's
   called and fires immediately if the element is already in range, instead of waiting on the
   observer's async tick at all. A `fired` guard makes it safe if the observer's own callback
   ALSO later reports intersecting — whichever path gets there first wins, verified in isolation
   (`onIntersectOnce` given a controllable rect + a deliberately-delayed fake
   `IntersectionObserver`: fires synchronously when already in view, correctly waits when not,
   fires exactly once when both paths would otherwise trigger it).
2. `app.mjs` now reads `location.hash` once, after all section containers exist, and if it names
   a section, explicitly `scrollIntoView()`s and loads it directly — this is the more targeted
   fix for deep-linking specifically (`#employment` in a shared URL, or any future `#section`
   link), independent of whatever the browser's native fragment handling did or didn't do.

Verify: cold load (`/site/`, scroll down manually) and a direct `/site/#employment` load must
both end with all 8 sections rendered (charts, not "加载中…" or an error card) — checked via a
Node DOM-shim end-to-end run of `main()` for both cases (all 8 sections reach `hasTier1`/expected
svgCount, employment shows 4: the tier-1 unemployment-rate chart + 3 tier-2 panels — 31-city, the
frozen pre-2023-08 youth series, and the break-first post-methodology-change youth series); a
real-browser click-through pass is still worth doing when browser tooling is available in-session
(it was not, this pass).

## 2026-07-08 design-review fixes — what to re-check visually

1. **Employment section crash.** Could not reproduce a synchronous render exception against the
   current `site-data/sections/employment.json` — tested `renderSection` against all 8 real
   section bundles, every range option (1Y/3Y/5Y/10Y/max), both 375px and 800px container
   widths, and repeated re-renders on the same container, via a hand-rolled Node DOM shim; all
   completed without throwing. Two non-exclusive hypotheses fit the evidence: (a) all 8 section
   bundles share one filesystem mtime from a concurrent pipeline rebuild — a torn read of a
   bundle mid-rewrite would surface as a genuine `JSON.parse` failure at fetch time, unrelated to
   this file; (b) an exception from a later async resize/range/theme callback had no error
   boundary at all before this fix. Regardless of the original trigger: reload the page a few
   times against 就业 specifically and confirm it never again shows the fetch-failure text while
   the Network tab shows a 200 for `sections/employment.json` — if it does, the browser console
   will now name the exact series and error (previously silent).
2. **Endpoint value visibility.** Open any tier-1 chart that has NO caliber toggle (i.e. not
   retail-total) — CPI, PMI, 70-city, M1 — and confirm a value is printed at the line's terminus.
   This was silently empty for every such chart before this fix (the real, pervasive version of
   "the value must always be visible" — worse than just being crowded by an annotation).
3. **Break/annotation footnote markers.** Load `?data=../site-data-fixtures` — `物价` has a
   `no_yoy_across` break, `消费` has a period annotation + a series-level note + a revision flag.
   Confirm: a small circled numeral (①) sits on the chart face (not full sentence text), a
   matching numbered line appears below the chart, and the numeral never sits inside the same
   horizontal band as the endpoint value. Try narrow width (~375px) — the footnote list must
   still be there (this used to disappear entirely on narrow viewports).
4. **Tick label decimals.** On the same fixtures page, `物价`'s axis (step 0.2) should show one
   decimal consistently ("1.8%, 2.0%, 2.2%..."); `消费`'s axis (step 2) should show none ("-2%,
   0%, 2%, 4%..."). Spot-check a real chart too (e.g. CPI once it has enough history to force a
   sub-1 step) once `prices` has more backfilled series.
5. **Domain fit.** Compare a chart's gridline span against its actual data range by eye — it
   should no longer look padded by roughly double.
6. **PMI-style duplicate name.** Any series with no takeaway (`macro`'s PMI series, most of
   `employment`) should show the name ONCE (as the headline), with the dek showing only the
   unit — not the name again.
7. **Small-multiples panel layout.** Open a section with tier-2 series (`consumption`,
   `income-confidence`, `property`) and confirm: panel titles wrap to up to 2 lines (never an
   ellipsis mid-word unless a name is pathologically long), all panels' sparkline boxes start at
   the same y regardless of whether their own title wrapped to 1 or 2 lines, and the value sits
   in the same top-right corner on every panel (not floating wherever the line happens to end).
8. **City-grid filter.** Type a city name substring into the 楼市 section's filter — the grid
   should narrow live. (Verified by code read + the `buildCityRows` unit tests, not by hand in a
   real browser this pass.)
9. **Caliber full-swap (item 9) — INERT, unverified.** `line-chart.mjs`/`section.mjs` now
   feature-detect `entry.yoy_series_ytd` / `entry.level_series_ytd` (a guess at the field names
   the next pipeline change might use) and, if present, make the 当月/累计 toggle swap the whole
   plotted line + endpoint, not just the printed readout text. No real bundle carries these
   fields yet, so this path is untested against real data — once the pipeline lands the per-
   caliber arrays, re-open `nbs-retail-total` (or whichever series gets both arrays first),
   toggle 当月/累计, and confirm the LINE itself changes, not just the corner readout. If the
   actual field names differ from `yoy_series_ytd`/`level_series_ytd`, update
   `section.mjs`'s `seriesForCaliber()` — that's the only place the guess lives.
10. **Dark mode context vs. grid.** `--context` (dark) moved from `#5c5b55` to `#7a7770`
    (2.06:1 → 3.13:1 against `--grid`, measured via the WCAG relative-luminance formula). Open a
    tier-2 small-multiples grid in dark mode and confirm the gray trend line is legible against
    the gridline, especially where it crosses the zero line. Light mode measured 1.57:1 by the
    same formula (also technically low) but review explicitly passed it — left unchanged; flagged
    here rather than silently touched.

## Known constraints unchanged

Zero dependencies, ES modules, chart text ≥12px real pixels, exactly two global-ish controls
(time range, per-series 当月/累计 toggle) plus the city filter — no new interactive controls were
added by this pass.
