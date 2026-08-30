"""pipeline/dg_refresh.py -- keep every DG-sourced catalog series current
between backfill runs, with no new HTML parser.

pipeline/backfill/backfill.py did a ONE-TIME historical sweep of NBS's DG
national-data API (pipeline/backfill/dg_client.py's three-endpoint protocol)
for every series whose provenance is that API rather than a press-release
HTML parser: CPI/PPI (9 concepts), PMI (2), surveyed unemployment (3 live
ids), industrial value added (1), FAI (1), customs trade (2), M0/M1/M2 (3),
and GDP + its three 三大需求 contribution shares (4) -- 26 series total,
recognizable on disk by their `observations[].src` field starting with
`"dg:"` (backfill's own convention; see FAMILY_STEPS below). None of those 26
concepts has (or needs) an HTML press-release parser of its own -- this
module is how they stay current going forward: re-pull just the last
`LOOKBACK_PERIODS` periods for each, on a schedule, through the SAME
stage -> Gate A -> write flow every other `pipeline.runner --source ...`
uses, via `python -m pipeline.runner --source dg_refresh`.

How this reuses backfill's own logic (per the rebuild brief: "reuse backfill's
dg_client + cached tree", not duplicate it): every locator function below
(``_pull_cpi``, ``_pull_ppi``, ...) imports and calls
`pipeline.backfill.backfill`'s own tree-walk helpers (`get_indicators`,
`by_kj1`, `by_kj2`, `by_showname`, `by_all_showname`, `find_indicator`,
`window_bounds`) and re-uses `pipeline.backfill.tree.TreeCache`'s on-disk
cache (`tree_cache.json`, already warm from the initial backfill run, so a
re-walk here is almost entirely cache hits, not fresh network calls) and
`pipeline.backfill.dg_client.DGClient`'s HTTP/politeness/archiving layer
unchanged. The one thing NOT reusable from backfill.py is the literal tree
PATH each concept lives at (e.g. `["价格指数", "居民消费价格分类指数
(上年同月=100)"]` for CPI) -- backfill.py's own `build_*` functions are
side-effecting (write files, mutate a shared `report`/`fragments`) with no
extraction seam that returns a resolved (cid, indicator_id, root_id) triple,
so that data (which concept lives at which tree path, under which predicate)
is necessarily restated here, the same way field_map.yaml necessarily
restates each source's own field->series_id table. No tree-walking,
HTTP-client, throttling, archiving, or period-code logic is duplicated.

The "identity field_map" trick: normalize.py's apply_parsed_release() merges
a ParsedRelease into data/series/ via `field_map[source][row.source_field] ->
series_id`. This module already resolves each row straight to its OWN series
id (no raw Chinese label to look up) -- so it builds a trivial IDENTITY
field_map (`{"dg-refresh": {series_id: series_id for series_id in ...}}`) and
hands runner.py's OWN `stage_release` / `batch_from_parsed_release` /
`run_gate` / `promote_to_real` functions a ParsedRelease shaped exactly like
any other source's. Nothing downstream of "parse" needed a single line of new
code to support this source.

Jan-Feb spans (DATA-CONTRACT §3.2): NBS publishes no standalone January print
for FAI/industrial-va's cumulative measures -- confirmed empirically (every
existing DG-sourced observation for nbs-fai/nbs-industrial-va has zero "-01"
periods, ever, and none carry `flags`/`span`). DG's own raw API mirrors this
exactly: a "YYYYMMMM" request for month 01 on either of THESE TWO concepts
simply returns no value at all (not a zero, not a real number under the wrong
label) -- `_pull_fai`/`_pull_iva` below therefore need no special jan_feb
tagging logic of their own; a period with nothing returned is silently
dropped, matching every other family's "blank means not published, not an
error" handling (mirrors backfill.py's own make_observations philosophy) and
matching the EXISTING (untagged) shape of every real Feb observation already
on file for these two ids -- inventing a NEW span=2/flags=["jan_feb"]
convention on top of that would make the series internally inconsistent
(old Februaries untagged, new ones tagged) for no benefit, since normalize.py
has no other row to reconcile a synthetic January against anyway. Every
OTHER DG-sourced concept (CPI/PPI, PMI, unemployment, trade, money, GDP)
genuinely publishes a standalone January value, so no special-casing is
needed there either.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline import ParsedRelease, ParsedRow
from pipeline.backfill import backfill as bf
from pipeline.backfill.dg_client import DGClient
from pipeline.backfill.tree import DEFAULT_CACHE_PATH, TreeCache, TreePathError
from pipeline.validate.batch import batch_from_parsed_release
from pipeline.validate.gate import run_gate
from pipeline.validate.staging import promote_to_real, stage_release

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover -- stdlib on Python 3.9+; defensive only
    ZoneInfo = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"
CATALOG_PATH = ROOT / "data" / "catalog.json"
VALIDATE_REPORTS_DIR = ROOT / "validate_reports"
SHANGHAI_TZ_NAME = "Asia/Shanghai"


def _persist_gate_report(staged_dir: Path, source_key: str) -> None:
    """Copy the staged Gate A report to validate_reports/<source_key>/ --
    a local duplicate of pipeline.runner's own helper of the same name (not
    imported from there: pipeline.runner imports this module to dispatch
    `--source dg_refresh`, so the reverse import would complete the same
    3-module circular-import risk _shanghai_today's docstring already flags
    for pipeline.schedule). See pipeline.runner._persist_gate_report's own
    docstring for the MEDIUM bug this fixes (update-data.yml's artifact
    upload step pointed at validate_reports/, but nothing ever wrote there)."""
    dest_dir = VALIDATE_REPORTS_DIR / source_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("gate_report.json", "gate_report.md"):
        src = staged_dir / name
        if src.exists():
            shutil.copy2(src, dest_dir / name)


def _shanghai_today() -> date:
    """Deliberately a local copy of pipeline/schedule.py's own helper of the
    same name, not an import from it -- pipeline.schedule imports
    pipeline.runner (to introspect SOURCES) and pipeline.runner imports this
    module (to dispatch `--source dg_refresh`), so importing schedule.py from
    here would complete a 3-module circular import. This is a tiny, fully
    self-contained utility (China does not observe DST, so a fixed UTC+8
    offset is exact when zoneinfo/tzdata isn't available) -- not worth
    breaking either direction of that cycle to share five lines."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(SHANGHAI_TZ_NAME)).date()
        except Exception:
            pass
    return (datetime.utcnow() + timedelta(hours=8)).date()

SOURCE_NAME = "dg-refresh"
DG_PAGE_URL = "https://data.stats.gov.cn/dg/website/page.html"

# "Last 3 periods" per the task spec: the current calendar month/quarter plus
# the two before it, counted back from "today" (not from whatever a series'
# own stored `latest` happens to be) -- matching backfill.py's own "a request
# past the true latest period just comes back blank, no need to discover the
# exact latest period first" philosophy. The current period will usually come
# back blank (nothing published yet for it); that is the expected, harmless
# steady state, not a bug.
LOOKBACK_PERIODS = 3

# NBS/DG indicator id this series' Feb-onward youth-unemployment pull always
# routes to: the DG tree reuses the SAME indicator id across the 2023-12
# methodology seam (NBS didn't mint a new one; only the catalog splits it into
# two ids -- see pipeline/backfill/backfill.py's own coverage_note_zh on
# nbs-urban-unemp-youth-1624-exstudent). Since this module only ever requests
# recent periods (always >> 2023-12 in practice), every period it pulls
# belongs to the POST-break id -- the pre-break id is frozen (`"end":
# "2023-07"` in the catalog) and must never receive new data.
_YOUTH_UNEMP_FROZEN_ID = "nbs-urban-unemp-youth-1624"
_YOUTH_UNEMP_LIVE_ID = "nbs-urban-unemp-youth-1624-exstudent"


# -- period-code helpers (distinct problem shape from backfill.py's
#    month_codes/quarter_codes, which build a full start_year..end run -- this
#    module wants just the last N periods ending at "today") -----------------


def _recent_month_codes(n: int, today: date) -> list[str]:
    year, month = today.year, today.month
    months: list[tuple[int, int]] = []
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    months.reverse()
    return [f"{y}{m:02d}MM" for y, m in months]


def _recent_quarter_codes(n: int, today: date) -> list[str]:
    year, quarter = today.year, (today.month - 1) // 3 + 1
    quarters: list[tuple[int, int]] = []
    for _ in range(n):
        quarters.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    quarters.reverse()
    return [f"{y}{q:02d}SS" for y, q in quarters]


def _values(client: DGClient, cid: str, indicator_id: str, root_id: str, codes: list[str]) -> dict[str, float]:
    return {p: v for p, v in client.indicator_values(cid, indicator_id, root_id, codes).items() if v is not None}


Pulled = dict[str, dict[str, dict[str, float]]]  # series_id -> period -> {measure: value}


def _add(pulled: Pulled, series_id: str, measure: str, values: dict[str, float]) -> None:
    bucket = pulled.setdefault(series_id, {})
    for period, value in values.items():
        bucket.setdefault(period, {})[measure] = value


# -- per-family locators (tree paths/predicates mirror backfill.py's own
#    build_* functions exactly -- see module docstring) ----------------------


def _pull_cpi(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    price_root = cache.walk_path(client, code, ["价格指数"])["_id"]
    yoy_parent = cache.walk_path(client, code, ["价格指数", "居民消费价格分类指数 (上年同月=100)"])
    windows = cache.children_matching(
        client, code, yoy_parent["_id"],
        predicate=lambda n: bf._norm(n["name"]).startswith("全国居民消费价格分类指数 (上年同月=100) ("),
    )
    windows.sort(key=lambda n: bf.window_bounds(n["name"])[0])
    if not windows:
        raise TreePathError("no date-windowed CPI YoY leaves found under 居民消费价格分类指数(上年同月=100)")
    newest = windows[-1]  # per backfill.py: the newest window's own indicator id already answers recent history

    pulled: Pulled = {}
    rows = bf.get_indicators(client, newest["_id"])
    for id_, _name_zh, _name_en, kj2_query, _tier in bf.CPI_CONCEPTS:
        row = bf.find_indicator(rows, bf.by_kj2(kj2_query))
        if row is None:
            continue
        level = _values(client, newest["_id"], row["_id"], price_root, month_codes)
        _add(pulled, id_, "m", level)
        _add(pulled, id_, "m_yoy", {p: round(v - 100, 1) for p, v in level.items()})

    mom_parent = cache.walk_path(client, code, ["价格指数", "居民消费价格分类指数 (上月=100)"])
    mom_windows = cache.children_matching(
        client, code, mom_parent["_id"],
        predicate=lambda n: bf._norm(n["name"]).startswith("全国居民消费价格分类指数 (上月=100) ("),
    )
    mom_windows.sort(key=lambda n: bf.window_bounds(n["name"])[0])
    if mom_windows:
        mom_newest = mom_windows[-1]
        mom_rows = bf.get_indicators(client, mom_newest["_id"])
        mom_row = bf.find_indicator(mom_rows, bf.by_kj2(""))
        if mom_row is not None:
            mom_level = _values(client, mom_newest["_id"], mom_row["_id"], price_root, month_codes)
            _add(pulled, "nbs-cpi-yoy", "mom", {p: round(v - 100, 1) for p, v in mom_level.items()})
    return pulled


def _pull_ppi(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    price_root = cache.walk_path(client, code, ["价格指数"])["_id"]
    headline_cid_node = cache.walk_path(client, code, ["价格指数", "工业生产者出厂价格分类指数", "工业生产者出厂价格指数 (上年同月=100)"])
    mom_cid_node = cache.walk_path(client, code, ["价格指数", "工业生产者出厂价格分类指数", "工业生产者出厂价格指数 (上月=100)"])
    rows = bf.get_indicators(client, headline_cid_node["_id"])
    mom_rows = bf.get_indicators(client, mom_cid_node["_id"])

    ppi_concepts = [
        ("nbs-ppi-yoy", bf.by_kj2("")),
        ("nbs-ppi-producer-yoy", bf.by_kj1("生产资料")),
        ("nbs-ppi-consumer-yoy", bf.by_kj1("生活资料")),
    ]
    pulled: Pulled = {}
    for id_, predicate in ppi_concepts:
        row = bf.find_indicator(rows, predicate)
        if row is None:
            continue
        level = _values(client, headline_cid_node["_id"], row["_id"], price_root, month_codes)
        _add(pulled, id_, "m", level)
        _add(pulled, id_, "m_yoy", {p: round(v - 100, 1) for p, v in level.items()})
        if id_ == "nbs-ppi-yoy":
            mom_row = bf.find_indicator(mom_rows, predicate)
            if mom_row is not None:
                mom_level = _values(client, mom_cid_node["_id"], mom_row["_id"], price_root, month_codes)
                _add(pulled, id_, "mom", {p: round(v - 100, 1) for p, v in mom_level.items()})
    return pulled


def _pull_pmi(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    pmi_root = cache.walk_path(client, code, ["采购经理指数"])["_id"]
    targets = [
        ("cflp-pmi-mfg", ["采购经理指数", "制造业采购经理指数"], bf.by_showname("制造业采购经理指数")),
        ("cflp-pmi-nonmfg", ["采购经理指数", "非制造业采购经理指数"], bf.by_showname("非制造业商务活动指数")),
    ]
    pulled: Pulled = {}
    for id_, path, predicate in targets:
        node = cache.walk_path(client, code, path)
        rows = bf.get_indicators(client, node["_id"])
        row = bf.find_indicator(rows, predicate)
        if row is None:
            continue
        _add(pulled, id_, "m", _values(client, node["_id"], row["_id"], pmi_root, month_codes))
    return pulled


def _pull_unemployment(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    unemp_root = cache.walk_path(client, code, ["城镇调查失业率"])["_id"]
    leaf = cache.walk_path(client, code, ["城镇调查失业率", "城镇调查失业率"])
    rows = bf.get_indicators(client, leaf["_id"])

    pulled: Pulled = {}
    simple_targets = [
        ("nbs-urban-unemp", bf.by_showname("全国城镇调查失业率")),
        ("nbs-urban-unemp-31city", bf.by_showname("31个大城市城镇调查失业率")),
    ]
    for id_, predicate in simple_targets:
        row = bf.find_indicator(rows, predicate)
        if row is None:
            continue
        _add(pulled, id_, "m", _values(client, leaf["_id"], row["_id"], unemp_root, month_codes))

    # Youth 16-24: DG reuses one indicator id across the 2023-12 methodology
    # seam (see _YOUTH_UNEMP_LIVE_ID docstring above) -- always routes to the
    # live (exstudent) id here, never the frozen pre-break one, since this
    # module only ever asks for recent periods.
    youth_row = bf.find_indicator(rows, bf.by_showname("16—24岁"))
    if youth_row is not None:
        _add(pulled, _YOUTH_UNEMP_LIVE_ID, "m", _values(client, leaf["_id"], youth_row["_id"], unemp_root, month_codes))
    return pulled


def _pull_iva(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    industry_root = cache.walk_path(client, code, ["工业"])["_id"]
    leaf = cache.walk_path(client, code, ["工业", "规上工业增加值增长速度"])
    rows = bf.get_indicators(client, leaf["_id"])
    m_row = bf.find_indicator(rows, bf.by_showname("同比增长"))
    ytd_row = bf.find_indicator(rows, bf.by_showname("累计增长"))
    if m_row is None:
        return {}
    pulled: Pulled = {}
    _add(pulled, "nbs-industrial-va", "m", _values(client, leaf["_id"], m_row["_id"], industry_root, month_codes))
    if ytd_row is not None:
        _add(pulled, "nbs-industrial-va", "ytd", _values(client, leaf["_id"], ytd_row["_id"], industry_root, month_codes))
    return pulled


def _pull_fai(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    fai_root = cache.walk_path(client, code, ["固定资产投资 (不含农户)"])["_id"]
    leaf = cache.walk_path(client, code, ["固定资产投资 (不含农户)", "固定资产投资概况"])
    rows = bf.get_indicators(client, leaf["_id"])
    row = bf.find_indicator(rows, bf.by_showname("固定资产投资额累计增长", exclude=("民间", "第一产业", "第二产业", "第三产业")))
    if row is None:
        return {}
    pulled: Pulled = {}
    _add(pulled, "nbs-fai", "ytd_yoy", _values(client, leaf["_id"], row["_id"], fai_root, month_codes))
    return pulled


def _pull_trade(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    trade_root = cache.walk_path(client, code, ["对外经济"])["_id"]
    leaf = cache.walk_path(client, code, ["对外经济", "货物进出口总额"])
    rows = bf.get_indicators(client, leaf["_id"])

    pulled: Pulled = {}
    for id_, prefix in (("customs-exports-usd", "出口"), ("customs-imports-usd", "进口")):
        exclude = ("进出口",)
        specs = [
            ("m", bf.by_showname(f"{prefix}总值当期值", exclude=exclude), True),
            ("m_yoy", bf.by_showname(f"{prefix}总值同比增长", exclude=exclude), False),
            ("ytd", bf.by_showname(f"{prefix}总值累计值", exclude=exclude), True),
            ("ytd_yoy", bf.by_showname(f"{prefix}总值累计增长", exclude=exclude), False),
        ]
        for measure, predicate, needs_unit_convert in specs:
            row = bf.find_indicator(rows, predicate)
            if row is None:
                continue
            values = _values(client, leaf["_id"], row["_id"], trade_root, month_codes)
            if needs_unit_convert:  # raw unit 千美元 -> stored unit 亿美元, matches backfill.py exactly
                values = {p: round(v / 100000, 2) for p, v in values.items()}
            else:
                values = {p: round(v, 1) for p, v in values.items()}
            _add(pulled, id_, measure, values)
    return pulled


def _pull_money(client: DGClient, cache: TreeCache, month_codes: list[str], _quarter_codes: list[str]) -> Pulled:
    code = 1
    finance_root = cache.walk_path(client, code, ["金融"])["_id"]
    leaf = cache.walk_path(client, code, ["金融", "货币供应量"])
    rows = bf.get_indicators(client, leaf["_id"])

    pulled: Pulled = {}
    for id_, tag in (("pbc-m0", "(M0)"), ("pbc-m1", "(M1)"), ("pbc-m2", "(M2)")):
        level_row = bf.find_indicator(rows, bf.by_all_showname(tag, "期末值"))
        yoy_row = bf.find_indicator(rows, bf.by_all_showname(tag, "同比增长"))
        if level_row is None:
            continue
        _add(pulled, id_, "m", {p: round(v, 2) for p, v in _values(client, leaf["_id"], level_row["_id"], finance_root, month_codes).items()})
        if yoy_row is not None:
            _add(pulled, id_, "m_yoy", {p: round(v, 1) for p, v in _values(client, leaf["_id"], yoy_row["_id"], finance_root, month_codes).items()})
    return pulled


def _pull_gdp(client: DGClient, cache: TreeCache, _month_codes: list[str], quarter_codes: list[str]) -> Pulled:
    code = 2
    accounts_root = cache.walk_path(client, code, ["国民经济核算"])["_id"]
    level_leaf = cache.walk_path(client, code, ["国民经济核算", "国内生产总值 (现价)"])
    index_leaf = cache.walk_path(client, code, ["国民经济核算", "国内生产总值指数"])
    contrib_leaf = cache.walk_path(client, code, ["国民经济核算", "三大需求对国内生产总值增长的贡献率"])

    pulled: Pulled = {}
    level_rows = bf.get_indicators(client, level_leaf["_id"])
    index_rows = bf.get_indicators(client, index_leaf["_id"])
    m_row = bf.find_indicator(level_rows, bf.by_showname("国内生产总值当季值"))
    ytd_row = bf.find_indicator(level_rows, bf.by_showname("国内生产总值累计值"))
    idx_row = bf.find_indicator(index_rows, bf.by_showname("国内生产总值指数 (上年同期=100) 当季值"))
    if m_row is not None:
        _add(pulled, "nbs-gdp", "m", {p: round(v, 1) for p, v in _values(client, level_leaf["_id"], m_row["_id"], accounts_root, quarter_codes).items()})
    if ytd_row is not None:
        _add(pulled, "nbs-gdp", "ytd", {p: round(v, 1) for p, v in _values(client, level_leaf["_id"], ytd_row["_id"], accounts_root, quarter_codes).items()})
    if idx_row is not None:
        idx_values = _values(client, index_leaf["_id"], idx_row["_id"], accounts_root, quarter_codes)
        _add(pulled, "nbs-gdp", "real_yoy", {p: round(v - 100, 1) for p, v in idx_values.items()})

    contrib_rows = bf.get_indicators(client, contrib_leaf["_id"])
    contrib_targets = [
        ("nbs-gdp-contribution-consumption", "最终消费支出对国内生产总值增长贡献率"),
        ("nbs-gdp-contribution-investment", "资本形成总额对国内生产总值增长贡献率"),
        ("nbs-gdp-contribution-netexports", "货物和服务净出口对国内生产总值增长贡献率"),
    ]
    for id_, prefix in contrib_targets:
        m_row = bf.find_indicator(contrib_rows, bf.by_showname(f"{prefix}当季值"))
        ytd_row = bf.find_indicator(contrib_rows, bf.by_showname(f"{prefix}累计值"))
        if m_row is not None:
            _add(pulled, id_, "m", {p: round(v, 1) for p, v in _values(client, contrib_leaf["_id"], m_row["_id"], accounts_root, quarter_codes).items()})
        if ytd_row is not None:
            _add(pulled, id_, "ytd", {p: round(v, 1) for p, v in _values(client, contrib_leaf["_id"], ytd_row["_id"], accounts_root, quarter_codes).items()})
    return pulled


FAMILY_STEPS = [
    ("CPI", _pull_cpi),
    ("PPI", _pull_ppi),
    ("PMI", _pull_pmi),
    ("Unemployment", _pull_unemployment),
    ("Industrial value added", _pull_iva),
    ("FAI", _pull_fai),
    ("Trade", _pull_trade),
    ("Money supply", _pull_money),
    ("GDP", _pull_gdp),
]


def _load_catalog_by_id() -> dict[str, dict]:
    if not CATALOG_PATH.exists():
        return {}
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in raw.get("series", []) if "id" in entry}


def _write_archive_manifest(archive_dir: Path, release_id: str, generated_at: str, captures: list[str]) -> Path:
    """data/archive/dg/manifest_<release_id>.json -- links this run's raw DG
    response captures (data/archive/dg/{indicators,values,tree}_<hash>_
    <timestamp>.json -- DGClient's own archiving, with no reference back to
    any release_id at all) to the release_id THIS run's staged observations
    carry, so gate_a.archive_release_identity has something to match
    against.

    Bug fixed 2026-07-14 (Gate A correctly blocked the first dg_refresh run
    that landed genuinely new observations): DGClient archives every raw
    response under data/archive/dg/ keyed only by an indicator/tree hash and
    a fetch timestamp -- nothing there ever recorded WHICH release_id those
    captures belonged to, so archive_release_identity's "does a capture
    matching this release_id exist" check could never find one for
    dg_refresh, no matter how much real data actually landed. This manifest
    is the missing link: `interface coordinated with the validate owner, who
    is updating the check in parallel to accept it` -- {release_id,
    generated_at, captures}, `generated_at` anchored to the run's own date
    context (not fresh wall-clock randomness) so two manifests for the same
    release_id are reproducible.

    Written into data/archive/dg/ itself (under data/, so the workflow's own
    `git add data/` picks it up in the same commit as the observations it
    backs) by the caller, BEFORE stage_release()/run_gate() run -- the
    identity check must see it in the same pass it verifies, not after.
    """
    manifest = {"release_id": release_id, "generated_at": generated_at, "captures": sorted(captures)}
    path = archive_dir / f"manifest_{release_id}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _rows_from_pulled(pulled: Pulled, catalog_by_id: dict[str, dict]) -> tuple[list[ParsedRow], list[str]]:
    """(rows, skipped_ids). A pulled series id with no catalog entry, or one
    that's frozen/superseded (catalog `end`/`superseded_by`), is skipped
    rather than written -- the frozen youth-unemployment id in particular
    must never receive fresh data (see _YOUTH_UNEMP_FROZEN_ID)."""
    rows: list[ParsedRow] = []
    skipped: list[str] = []
    for series_id, by_period in pulled.items():
        entry = catalog_by_id.get(series_id)
        if entry is None or entry.get("end") or entry.get("superseded_by"):
            if series_id not in skipped:
                skipped.append(series_id)
            continue
        for period, measures in by_period.items():
            for measure, value in measures.items():
                rows.append(
                    ParsedRow(
                        source_field=series_id,
                        raw_label=series_id,
                        value=value,
                        unit_raw=None,
                        caliber_hint=measure,
                        period=period,
                    )
                )
    return rows, skipped


def run(*, dry_run: bool, no_gate: bool = False, lookback: int = LOOKBACK_PERIODS, today: date | None = None) -> int:
    today = today or _shanghai_today()
    release_id = f"dg-refresh-{today.isoformat()}"
    month_codes = _recent_month_codes(lookback, today)
    quarter_codes = _recent_quarter_codes(lookback, today)
    print(f"[dg_refresh] re-pulling last {lookback} period(s) as of {today.isoformat()}: months={month_codes} quarters={quarter_codes}")

    client = DGClient()
    cache = TreeCache.load(DEFAULT_CACHE_PATH)
    # Snapshot the archive pool before this run's own HTTP calls -- diffed
    # against the same pool after the family loop, below, to know exactly
    # which capture files THIS run produced (see the manifest write after
    # the loop). client.archive_dir is DGClient's own data/archive/dg/,
    # already accumulating every prior run's captures too -- a snapshot diff
    # is the only way to isolate "just this run's" without DGClient itself
    # tracking or returning archived paths (pipeline.backfill is reuse-only,
    # not mine to modify).
    archived_before = set(client.archive_dir.glob("*.json"))
    pulled: Pulled = {}
    family_errors: list[str] = []
    for label, step in FAMILY_STEPS:
        try:
            for series_id, by_period in step(client, cache, month_codes, quarter_codes).items():
                pulled.setdefault(series_id, {}).update(
                    {p: {**pulled.get(series_id, {}).get(p, {}), **m} for p, m in by_period.items()}
                )
        except Exception as error:  # noqa: BLE001 -- deliberately broad, see docstring below
            # HIGH bug fixed 2026-07-08 (adversarial review): this used to
            # catch only (DGError, TreePathError) -- the two exception types
            # THIS module's own code deliberately raises. But each family
            # step also calls into pipeline.backfill's tree-walk/HTTP layer,
            # which can just as easily raise something else entirely: a raw
            # `requests`/`urllib` network exception DGClient didn't wrap, a
            # KeyError from an unexpected/malformed tree node shape, a
            # json.JSONDecodeError from a bad response body, etc. Narrowly
            # catching only two types meant any OTHER exception from one
            # family (e.g. FAI's tree walk hitting a malformed node) aborted
            # the entire refresh before it ever reached stage/gate/promote --
            # 8 unrelated, perfectly healthy families' worth of data lost
            # because one family raised a type nobody had enumerated ahead of
            # time. Catching Exception here and recording it in
            # family_errors (same as before) means one bad family degrades
            # gracefully -- exactly this function's own "continue on a
            # per-family basis" design already intended -- while the
            # all-families-failed guard below still fires correctly regardless
            # of which exception type(s) actually populated family_errors.
            family_errors.append(f"{label}: {type(error).__name__}: {error}")
        finally:
            cache.save(DEFAULT_CACHE_PATH)

    for error in family_errors:
        print(f"[dg_refresh] family failed, skipped: {error}", file=sys.stderr)

    catalog_by_id = _load_catalog_by_id()
    rows, skipped_ids = _rows_from_pulled(pulled, catalog_by_id)
    if skipped_ids:
        print(f"[dg_refresh] pulled data for uncataloged/frozen id(s), not written: {sorted(skipped_ids)}")

    if not rows:
        if len(family_errors) == len(FAMILY_STEPS):
            print("[dg_refresh] every family failed to reach the DG API -- treating as a fetch error", file=sys.stderr)
            return 1
        print("[dg_refresh] no data returned for any tracked DG series in the lookback window -- exiting cleanly")
        return 0

    # Manifest: link this run's raw DG captures to release_id, BEFORE
    # staging/gating so gate_a.archive_release_identity can see it in the
    # same pass -- see _write_archive_manifest's docstring.
    archived_after = set(client.archive_dir.glob("*.json"))
    new_captures = [p.name for p in (archived_after - archived_before)]
    manifest_path = _write_archive_manifest(client.archive_dir, release_id, today.isoformat(), new_captures)
    print(f"[dg_refresh] wrote archive manifest {manifest_path} ({len(new_captures)} capture(s))")

    parsed = ParsedRelease(
        source=SOURCE_NAME,
        release_id=release_id,
        url=DG_PAGE_URL,
        published_at=None,
        period_hint=today.strftime("%Y-%m"),
        rows=rows,
    )
    # Identity field_map: this module already resolved each row to its own
    # series id (no raw Chinese label to look up) -- see module docstring.
    field_map = {SOURCE_NAME: {series_id: series_id for series_id in pulled if series_id not in skipped_ids}}

    stage_result = stage_release(parsed, field_map, SERIES_DIR)
    batch = batch_from_parsed_release(parsed, field_map)
    real_data_dir = SERIES_DIR.parent

    report = stage_result.report
    mode = "would change" if dry_run else "changed"
    print(f"[dg_refresh] {mode}: {len(report.new_observations)} new observation(s), {len(report.revisions)} revision(s)")
    if report.new_observations:
        # update-data.yml greps `period: \K\S+` to build the commit title
        # (press-release sources print this via runner.py; without it every
        # dg_refresh commit is titled "@unknown").
        print(f"[dg_refresh] period: {max(p for _, p in report.new_observations)}")
    if stage_result.missing_series:
        print(f"[dg_refresh] mapped series file(s) not found on disk, skipped: {stage_result.missing_series}")

    gate_report = run_gate(
        stage_result.staged_dir,
        batch=batch,
        real_data_dir=real_data_dir,
        touched_series=stage_result.touched_series,
        requested_series=stage_result.requested_series,
        missing_series=stage_result.missing_series,
        normalize_report=stage_result.report,
        archive_source=SOURCE_NAME,
    )
    _persist_gate_report(stage_result.staged_dir, "dg_refresh")
    print(gate_report.to_markdown())

    if gate_report.blocked and not no_gate:
        print("[dg_refresh] GATE_BLOCKED", file=sys.stderr)
        print(
            "[dg_refresh] Gate A BLOCKED -- data/ left untouched. Fix the finding(s) above, or pass --no-gate to force a write.",
            file=sys.stderr,
        )
        return 2
    if gate_report.blocked and no_gate:
        print(
            "[dg_refresh] *** --no-gate override in effect: Gate A BLOCKED but writing anyway. ***",
            file=sys.stderr,
        )

    if not dry_run:
        written = promote_to_real(stage_result.staged_series_dir, SERIES_DIR, stage_result.touched_series)
        if written:
            print(f"[dg_refresh] wrote {len(written)} series file(s) to {SERIES_DIR}")

    return 0
