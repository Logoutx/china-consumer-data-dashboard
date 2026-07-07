# VIZ-GUIDE — the design constitution

Binding rules for every chart and page in this dashboard. Derived from documented NYT graphics-desk practice (provenance in the published field guide, artifact "NYT Graphics Rules — Field Guide", 2026-07-08). When a rendering decision isn't covered here, ask "is it Timesian?" — restraint wins.

## The fifteen rules

1. **Static first.** Interaction must earn its keep. The page reads completely by scrolling. Exactly two controls are allowed globally: time range, and 当月/累计 caliber where a series has both. No tab mazes, no per-chart mode switches.
2. **Nothing essential in a tooltip.** Latest value, its YoY change, and period label are printed on the chart face. Tooltips are a desktop bonus only.
3. **Headline the takeaway, not the topic.** Every chart title is the generated takeaway sentence (from `pipeline/takeaways.py`), not the indicator name. The indicator name is the dek/label line.
4. **The annotation layer is the most important thing we do.** `data/annotations.json` entries render as thin-leader text pinned to (series, period). Human-authored, versioned like copy.
5. **Annotate specifics.** Break markers state the fact at the break ("2024 年 1 月起：不含在校生口径，与旧序列不可比"). No generic labels.
6. **Derived ≠ observed.** Values with `derived: true` (differenced single-months, TTM) draw dashed/lighter, footnoted. Revisions get ※ markers.
7. **Templates are a starting point.** The shared kit covers the 80% case; 70-city gets a small-multiple grid, CPI 分项 gets a category strip. Don't force a form.
8. **Direct labels, no legends.** Series named at the line's terminus in the series color. In small-multiple grids, the panel title is the label.
9. **Gray is the workhorse, color is the story.** Context series in `--context`; at most two accent lines per chart.
10. **Gridlines: few, round, thin.** 3–5 horizontal lines, 1px `--grid`, round steps; zero line darker (`--grid-strong`); no chart border, no vertical gridlines. Bars start at zero; lines may crop the domain honestly.
11. **Small multiples over spaghetti.** Shared scale within a grid, stated once. Sorted so readers find their unit.
12. **Phone scroll first.** Single column at 375px is the design; desktop is the enhancement. Chart text ≥ 12px real pixels at every width.
13. **Chart text is real text.** SVG `<text>`/HTML nodes; charts re-render at container width with fixed font sizes (no viewBox down-scaling of type). Resize must not shrink labels.
14. **Fix colors only for recurring binaries.** One accent per section. The one fixed mapping: 红涨绿跌 (red = 涨/up, green = 跌/down), Chinese financial convention, everywhere and forever.
15. **Every chart states source, period, caliber — and admits revisions.** Fixed format: `资料来源：国家统计局 · 截至 2026 年 5 月 · 当月口径`, plus `※ 历史数据已修订` when the bundle flags recent revisions.

## Tokens (light / dark)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#ffffff` | `#131311` | page & chart ground (no card boxes) |
| `--ink` | `#121212` | `#eceae4` | headlines, primary lines, zero line |
| `--ink-soft` | `#555451` | `#b0aea6` | deks, axis labels |
| `--ink-faint` | `#88867f` | `#85837b` | source lines |
| `--grid` | `#e2e2e2` | `#2c2c29` | gridlines (1px) |
| `--grid-strong` | `#bdbcb7` | `#4a4a45` | zero line, leaders |
| `--context` | `#b8b6b0` | `#5c5b55` | non-highlighted series |
| `--accent-red` | `#b13a2c` | `#e0654f` | story line; 涨 |
| `--accent-blue` | `#46708f` | `#82aac9` | second colored series |
| `--fall-green` | `#3e6b48` | `#8fc79c` | 跌 |

Dark mode: token-level `@media (prefers-color-scheme: dark)` plus `:root[data-theme=…]` overrides both directions.

## Type

- Display (takeaway headlines): serif — `Georgia, 'Songti SC', 'Source Han Serif SC', serif`; 20–24px bold, `text-wrap: balance`.
- Labels/annotations/axes: sans — `'Helvetica Neue', Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif`; 12–13.5px; never below 12px real pixels.
- Source line: sans 11.5px `--ink-faint`.
- All numerals `font-variant-numeric: tabular-nums`.

## Chinese typesetting (all display strings)

盘古之白 (half-width space between CJK and digits/latin; none before %, none at full-width punctuation); Arabic numerals; full-width curly quotes “” only; ranges with hyphen (1-5 月).

## Page anatomy

Masthead (site name + freshness stamp from index.json) → 8 sections in narrative order: 物价 / 消费 / 收入与信心 / 就业 / 楼市 / 钱与信贷 / 宏观大盘 / 高频脉搏. Each section: section label → Tier-1 charts (takeaway h2, dek = indicator + unit, chart, source line) → Tier-2 in tighter layout → anchors for nav. Footer: methodology + data diary link + GitHub link.

## Component kit

`line-chart` (1–2 series, direct end labels, endpoint dot + printed value, annotations, break markers, dashed-derived) · `small-multiples` (shared scale, sorted, endpoint value in 红涨绿跌) · `city-grid` (70 minis + plain text filter) · `pulse-row` (Tier-3: name, latest, tiny sparkline, 红涨绿跌 delta) · `section-scaffold`. Zero dependencies, ES modules, charts re-render on container resize.
