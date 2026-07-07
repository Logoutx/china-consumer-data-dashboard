"""History backfill from NBS's open DG national-data API -- see docs/ACQUISITION.md
and pipeline/backfill/REPORT.md (written by this script) for the full account.

Scope (per the task this script was built for): CPI, PPI, PMI, surveyed urban
unemployment (incl. the youth excl-student break), industrial value added, FAI,
customs trade, M0/M1/M2, and GDP (+ 三大需求 contribution). Consumer confidence and
社融 were searched for and are NOT in this DG tree (see SKIPPED below) -- and income/
consumption/retail are intentionally untouched because docs/MIGRATION-MAP.md already
covers them from the existing archive.

Protocol (see pipeline/backfill/dg_client.py's docstring for how this was
reverse-engineered): tree walk (pipeline/backfill/tree.py) -> queryIndicatorsByCid
-> getEsDataByIndicatorIdAndDa. Every raw response is archived under
data/archive/dg/ by DGClient itself.

Design choices worth flagging up front (also called out per-series in REPORT.md):

  * CPI/PPI are published in this database only as an index on a "same month/quarter
    last year = 100" basis (e.g. 100.1), never as a directly-labelled "涨跌幅%" line
    item. `m_yoy` (and, for GDP, `real_yoy`) is populated as round(index - 100, decimals)
    -- a *unit restatement* of NBS's own published index, not an independent
    recomputation from a level series. This is standard practice for this exact
    database (every downstream consumer of data.stats.gov.cn's CPI table does the
    same conversion) but it is still a derivation, so every series built this way says
    so in `coverage_note_zh` and is listed in REPORT.md's derivation-method section.
  * CPI's classified-index tables are split by NBS into up to 4 non-overlapping-looking
    "windows" (e.g. "(-2015)", "(2016-2020)", "(2021-2025)", "(2026-)"); empirically the
    *not*-oldest windows' own indicator ids already answer for material history before
    their nominal start (e.g. the "(2026-)" id answered back to 2000 in spot checks), so
    the four windows are fetched and merged (newest wins on overlap) rather than
    strictly sliced -- this gets maximum depth without guessing exact seams.
  * FAI and industrial value added are published by NBS *only* as a YoY growth rate --
    there is no absolute-level line item in this database (confirmed by checking every
    date-windowed FAI table back to 1998-2003) -- so both use value_type "yoy_pct" and
    never populate a level.
  * Customs trade is only available here in USD (千美元); the task asked for RMB. RMB
    monthly trade is a different NBS release (the activity-batch article, not this DG
    catalog) -- out of this agent's scope. Minted as `customs-exports-usd` /
    `customs-imports-usd` (an explicit qualifier) rather than claiming the bare
    `customs-exports`/`customs-imports` ids MIGRATION-MAP reserved for the RMB series.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.backfill.dg_client import DGClient, DGError, month_codes, quarter_codes  # noqa: E402
from pipeline.backfill.tree import DEFAULT_CACHE_PATH, TreeCache, TreePathError  # noqa: E402
from pipeline.migrate.jsonio import write_json  # noqa: E402
from pipeline.migrate.schema_validator import validate as schema_validate  # noqa: E402

SERIES_DIR = REPO_ROOT / "data" / "series"
SCHEMA_PATH = REPO_ROOT / "data" / "schemas" / "series.schema.json"
CATALOG_FRAGMENT_PATH = REPO_ROOT / "data" / "_backfill_catalog_fragment.json"
REPORT_PATH = Path(__file__).resolve().parent / "REPORT.md"
DG_PAGE_URL = "https://data.stats.gov.cn/dg/website/page.html"

GENERATED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Generous upper bounds for period requests -- a request past the true latest period
# just comes back blank (confirmed empirically), so there is no need to discover the
# exact latest period per series before fetching.
MONTH_END = "2026-06"
QUARTER_END = "2026-Q1"

MEASURE_ORDER = ["m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy"]

SCHEMA = __import__("json").loads(SCHEMA_PATH.read_text(encoding="utf-8"))

_indicator_cache: dict[str, list[dict]] = {}


# --------------------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def get_indicators(client: DGClient, cid: str) -> list[dict]:
    """Memoized queryIndicatorsByCid -- many concepts share one cid (e.g. all 13 CPI
    sub-categories live in one classified-index table), so this avoids re-listing."""
    if cid not in _indicator_cache:
        _indicator_cache[cid] = client.indicators_by_cid(cid)
    return _indicator_cache[cid]


def by_name(exact: str):
    target = _norm(exact)
    return lambda row: _norm(row.get("_name") or "") == target


def by_kj1(exact: str):
    target = _norm(exact)
    return lambda row: _norm(row.get("kj1_name") or "") == target


def by_kj2(exact: str):
    target = _norm(exact)
    return lambda row: _norm(row.get("kj2_name") or "") == target


def by_showname(contains: str, exclude: tuple[str, ...] = ()):
    def pred(row):
        name = _norm(row.get("i_showname") or row.get("_name") or "")
        if contains not in name:
            return False
        return not any(x in name for x in exclude)
    return pred


def by_all_showname(*parts: str, exclude: tuple[str, ...] = ()):
    """AND-match on several required substrings, independent of adjacency/spacing --
    NBS pads i_showname inconsistently (e.g. "货币和准货币 (M2) 供应量_期末值", with a
    space after the paren that a single concatenated query string would miss)."""
    def pred(row):
        name = _norm(row.get("i_showname") or row.get("_name") or "")
        if not all(p in name for p in parts):
            return False
        return not any(x in name for x in exclude)
    return pred


def find_indicator(rows: list[dict], predicate) -> dict | None:
    for row in rows:
        if predicate(row):
            return row
    return None


_WINDOW_RE = re.compile(r"\((\d{4})?-(\d{4})?\)\s*$")


def window_bounds(name: str, default_start: int = 1990, default_end: int = 2100) -> tuple[int, int]:
    match = _WINDOW_RE.search(name.strip())
    if not match:
        return (default_start, default_end)
    start = int(match.group(1)) if match.group(1) else default_start
    end = int(match.group(2)) if match.group(2) else default_end
    return (start, end)


def round_or_none(value, decimals):
    return None if value is None else round(value, decimals)


def make_observations(measures: dict[str, dict[str, float | None]], src: str,
                       flags_by_period: dict[str, list[str]] | None = None) -> list[dict]:
    """Build a schema-compliant, key-ordered observations array. A period is emitted
    only if at least one requested measure is non-null there (no leading/trailing
    all-null padding); a measure is written as `null` only for a period where the
    concept legitimately has other data (i.e. the row is emitted) but this one
    measure came back blank."""
    if not measures:
        return []
    all_periods = sorted(set().union(*(set(d) for d in measures.values())))
    rows = []
    for period in all_periods:
        values = {name: d[period] for name, d in measures.items() if period in d}
        if all(v is None for v in values.values()):
            continue
        row: dict = {"period": period}
        flags = (flags_by_period or {}).get(period)
        if flags:
            row["flags"] = flags
        for name in MEASURE_ORDER:
            if name in values:
                row[name] = values[name]
        row["src"] = src
        rows.append(row)
    return rows


def make_series(id_, name_zh, name_en, unit_zh, unit_en, value_type, freq, calibers,
                 decimals, observations, agency, dataset_zh, dataset_en,
                 coverage_note_zh=None, breaks=None, end=None,
                 url=DG_PAGE_URL) -> dict:
    doc = {
        "schema": "series/v1",
        "id": id_,
        "name_zh": name_zh,
        "name_en": name_en,
        "unit_zh": unit_zh,
        "unit_en": unit_en,
        "value_type": value_type,
        "freq": freq,
        "calibers": calibers,
        "decimals": decimals,
        "source": {"agency": agency, "dataset_zh": dataset_zh, "dataset_en": dataset_en, "url": url},
        "derived": None,
        "coverage_note_zh": coverage_note_zh,
        "observations": observations,
        "revisions": [],
        "breaks": breaks or [],
    }
    if end:
        doc["end"] = end
    doc["generated_at"] = GENERATED_AT
    return doc


def catalog_entry(doc: dict, section: str, tier: int, group: str | None = None,
                    supersedes: str | None = None, superseded_by: str | None = None) -> dict:
    obs = doc["observations"]
    entry = {
        "id": doc["id"],
        "name_zh": doc["name_zh"],
        "name_en": doc["name_en"],
        "section": section,
        "tier": tier,
        "unit_zh": doc["unit_zh"],
        "unit_en": doc["unit_en"],
        "value_type": doc["value_type"],
        "decimals": doc["decimals"],
        "freq": doc["freq"],
        "calibers": doc["calibers"],
        "source": doc["source"],
        "start": obs[0]["period"] if obs else None,
        "latest": obs[-1]["period"] if obs else None,
        "panel": False,
        "file": f"data/series/{doc['id']}.json",
    }
    if group:
        entry["group"] = group
    if doc.get("breaks"):
        entry["breaks"] = [{"effective": b["effective"], "kind": b["kind"], "no_yoy_across": b["no_yoy_across"]} for b in doc["breaks"]]
    # `supersedes` on the NEW series points back at the old id; `superseded_by` on the
    # OLD (frozen, has `end`) series points forward at the new one. Both explicit --
    # not inferred from `doc.get("end")`, which says nothing about *which* id replaced it.
    if supersedes:
        entry["supersedes"] = supersedes
    if superseded_by:
        entry["superseded_by"] = superseded_by
    return entry


class Report:
    def __init__(self):
        self.built: list[dict] = []       # {id, cids, ids, start, end, n_obs, note}
        self.skipped: list[dict] = []     # {label, reason}
        self.oddities: list[str] = []

    def add(self, doc: dict, cids: list[str], ind_ids: list[str], note: str = ""):
        obs = doc["observations"]
        self.built.append({
            "id": doc["id"],
            "cids": cids,
            "ind_ids": ind_ids,
            "start": obs[0]["period"] if obs else None,
            "end": obs[-1]["period"] if obs else None,
            "n_obs": len(obs),
            "note": note,
        })

    def skip(self, label: str, reason: str):
        self.skipped.append({"label": label, "reason": reason})

    def oddity(self, text: str):
        self.oddities.append(text)

    def total_observations(self) -> int:
        return sum(row["n_obs"] for row in self.built)

    def render(self) -> str:
        lines = [
            "# Backfill report -- NBS DG API",
            "",
            f"Generated {GENERATED_AT}. {len(self.built)} series built, "
            f"{self.total_observations()} total observations, {len(self.skipped)} targets skipped.",
            "",
            "## Series produced",
            "",
            "| id | cid(s) | indicator id(s) | range | obs | notes |",
            "|---|---|---|---|---|---|",
        ]
        for row in self.built:
            cids = ", ".join(row["cids"]) or "-"
            ids = ", ".join(row["ind_ids"]) or "-"
            rng = f"{row['start']}..{row['end']}" if row["start"] else "(empty)"
            lines.append(f"| `{row['id']}` | `{cids}` | `{ids}` | {rng} | {row['n_obs']} | {row['note']} |")
        lines += ["", "## Skipped / failed targets", ""]
        if not self.skipped:
            lines.append("(none)")
        for row in self.skipped:
            lines.append(f"- **{row['label']}**: {row['reason']}")
        lines += ["", "## Oddities flagged during the run", ""]
        if not self.oddities:
            lines.append("(none)")
        for text in self.oddities:
            lines.append(f"- {text}")
        return "\n".join(lines) + "\n"


def validate_and_write(doc: dict, report_errors: list[str]) -> bool:
    errors = schema_validate(doc, SCHEMA)
    if errors:
        report_errors.append(f"{doc['id']}: " + "; ".join(errors[:5]))
        return False
    write_json(SERIES_DIR / f"{doc['id']}.json", doc)
    return True


# --------------------------------------------------------------------------- CPI / PPI

CPI_CONCEPTS = [
    # (id, name_zh, name_en, kj2 query, tier)
    ("nbs-cpi-yoy", "居民消费价格指数", "CPI: All Items", "", 1),
    ("nbs-cpi-food-yoy", "食品烟酒及在外餐饮类居民消费价格指数", "CPI: Food, Tobacco, Liquor & Dining Out", "食品烟酒类", 2),
    ("nbs-cpi-nonfood-yoy", "非食品居民消费价格指数", "CPI: Non-Food", "非食品", 2),
    ("nbs-cpi-core-yoy", "居民消费价格指数（不包括食品和能源）", "CPI: Core (excl. Food & Energy)", "不包括食品和能源", 2),
    ("nbs-cpi-services-yoy", "服务居民消费价格指数", "CPI: Services", "服务", 2),
    ("nbs-cpi-goods-yoy", "消费品居民消费价格指数", "CPI: Consumer Goods", "消费品", 2),
]

CPI_REBASE_BREAK = {
    "effective": "2026-01", "kind": "rebase", "no_yoy_across": True, "yoy_valid_from": "2026-01",
    "note_zh": "CPI 定基调整至 2025 年基期，跨基期同比不可比。", "note_en": "CPI rebased to 2025 base; YoY not comparable across the seam.",
}
PPI_REBASE_BREAK = {
    "effective": "2026-01", "kind": "rebase", "no_yoy_across": True, "yoy_valid_from": "2026-01",
    "note_zh": "PPI 定基调整至 2025 年基期，跨基期同比不可比。", "note_en": "PPI rebased to 2025 base; YoY not comparable across the seam.",
}

INDEX_DERIVE_NOTE = (
    "DG 库仅提供“上年同月=100”定基指数，未见独立发布的“涨跌幅%”字段；"
    "m_yoy 由指数值减 100 换算得到——这是对同一官方发布数字的单位换算，"
    "不是从水平值重新推算同比（参见 pipeline/backfill/REPORT.md 的换算说明）。"
)


def build_cpi(client, cache, report, errors):
    code = 1
    price_root = cache.walk_path(client, code, ["价格指数"])["_id"]
    yoy_parent = cache.walk_path(client, code, ["价格指数", "居民消费价格分类指数 (上年同月=100)"])
    windows = cache.children_matching(
        client, code, yoy_parent["_id"],
        predicate=lambda n: _norm(n["name"]).startswith("全国居民消费价格分类指数 (上年同月=100) ("),
    )
    windows.sort(key=lambda n: window_bounds(n["name"])[0])
    if not windows:
        report.skip("CPI (all concepts)", "no date-windowed leaves found under 居民消费价格分类指数(上年同月=100)")
        return

    for id_, name_zh, name_en, kj2_query, tier in CPI_CONCEPTS:
        merged: dict[str, float] = {}
        used_cids, used_ids = [], []
        for node in windows:
            rows = get_indicators(client, node["_id"])
            row = find_indicator(rows, by_kj2(kj2_query))
            if row is None:
                continue
            start, end = window_bounds(node["name"])
            end = min(end, int(MONTH_END[:4]))
            end_period = f"{end}-12" if end < int(MONTH_END[:4]) else MONTH_END
            codes = month_codes(max(start, 1990), end_period)
            values = client.indicator_values(node["_id"], row["_id"], price_root, codes)
            for period, v in values.items():
                if v is not None:
                    merged[period] = v
            used_cids.append(node["_id"])
            used_ids.append(row["_id"])
        if not merged:
            report.skip(id_, f"kj2_name {kj2_query!r} not found in any CPI YoY window")
            continue
        m_map = {p: round(v, 1) for p, v in merged.items()}
        yoy_map = {p: round(v - 100, 1) for p, v in merged.items()}
        measures = {"m": m_map, "m_yoy": yoy_map}

        # headline also gets a MoM-basis pull for the `mom` measure (task asks for
        # "headline YoY + MoM"; sub-categories only asked for YoY).
        if id_ == "nbs-cpi-yoy":
            mom_parent = cache.walk_path(client, code, ["价格指数", "居民消费价格分类指数 (上月=100)"])
            mom_windows = cache.children_matching(
                client, code, mom_parent["_id"],
                predicate=lambda n: _norm(n["name"]).startswith("全国居民消费价格分类指数 (上月=100) ("),
            )
            mom_windows.sort(key=lambda n: window_bounds(n["name"])[0])
            mom_merged: dict[str, float] = {}
            for node in mom_windows:
                rows = get_indicators(client, node["_id"])
                row = find_indicator(rows, by_kj2(""))
                if row is None:
                    continue
                start, end = window_bounds(node["name"])
                end = min(end, int(MONTH_END[:4]))
                end_period = f"{end}-12" if end < int(MONTH_END[:4]) else MONTH_END
                codes = month_codes(max(start, 1990), end_period)
                values = client.indicator_values(node["_id"], row["_id"], price_root, codes)
                for period, v in values.items():
                    if v is not None:
                        mom_merged[period] = v
                used_cids.append(node["_id"])
                used_ids.append(row["_id"])
            if mom_merged:
                measures["mom"] = {p: round(v - 100, 1) for p, v in mom_merged.items()}

        obs = make_observations(measures, src=f"dg:{used_ids[-1]}")
        doc = make_series(
            id_, name_zh, name_en, "%", "%",
            "index", "M", ["single"], 1, obs,
            "nbs", f"国家统计局数据发布 · 居民消费价格分类指数（{name_zh}，上年同月=100）",
            f"NBS DG data -- CPI classified index ({name_en}, prior-year-month=100)",
            coverage_note_zh=INDEX_DERIVE_NOTE,
            breaks=[CPI_REBASE_BREAK] if id_ == "nbs-cpi-yoy" else [],
        )
        if validate_and_write(doc, errors):
            report.add(doc, sorted(set(used_cids)), sorted(set(used_ids)), note="m=index(100 basis); m_yoy derived")
            fragments.append(catalog_entry(doc, "prices", tier))


def build_ppi(client, cache, report, errors):
    code = 1
    price_root = cache.walk_path(client, code, ["价格指数"])["_id"]
    headline_cid_node = cache.walk_path(
        client, code,
        ["价格指数", "工业生产者出厂价格分类指数", "工业生产者出厂价格指数 (上年同月=100)"],
    )
    mom_cid_node = cache.walk_path(
        client, code,
        ["价格指数", "工业生产者出厂价格分类指数", "工业生产者出厂价格指数 (上月=100)"],
    )
    # NOTE: `_name` is identical ("工业生产者出厂价格指数") across all 3 rows in this
    # cid -- exactly like CPI's kj2_name pattern. But unlike CPI, the classification
    # slot NBS uses is not fixed: the headline row puts the basis label ("上年同月=100")
    # in kj1_name and leaves kj2_name null, while the two category splits put the
    # category ("生产资料"/"生活资料") in kj1_name and push the basis label into
    # kj2_name. So headline is identified by kj2_name=="" (only one row qualifies),
    # not by kj1_name=="" -- confirmed empirically (kj1_name=="" matched zero rows).
    ppi_concepts = [
        ("nbs-ppi-yoy", by_kj2(""), "工业生产者出厂价格指数", "PPI: All Industrial Products", 1),
        ("nbs-ppi-producer-yoy", by_kj1("生产资料"), "生产资料工业生产者出厂价格指数", "PPI: Means of Production", 2),
        ("nbs-ppi-consumer-yoy", by_kj1("生活资料"), "生活资料工业生产者出厂价格指数", "PPI: Consumer Goods", 2),
    ]
    rows = get_indicators(client, headline_cid_node["_id"])
    mom_rows = get_indicators(client, mom_cid_node["_id"])
    end_period = MONTH_END
    codes = month_codes(1990, end_period)
    for id_, predicate, name_zh, name_en, tier in ppi_concepts:
        row = find_indicator(rows, predicate)
        if row is None:
            report.skip(id_, f"{name_zh!r} row not found in PPI YoY-index cid (kj1/kj2 predicate matched nothing)")
            continue
        values = client.indicator_values(headline_cid_node["_id"], row["_id"], price_root, codes)
        m_map = {p: round(v, 1) for p, v in values.items() if v is not None}
        yoy_map = {p: round(v - 100, 1) for p, v in m_map.items()}
        used_cids, used_ids = [headline_cid_node["_id"]], [row["_id"]]

        measures = {"m": m_map, "m_yoy": yoy_map}
        if id_ == "nbs-ppi-yoy":
            mom_row = find_indicator(mom_rows, predicate)
            if mom_row is not None:
                mom_values = client.indicator_values(mom_cid_node["_id"], mom_row["_id"], price_root, codes)
                measures["mom"] = {p: round(v - 100, 1) for p, v in mom_values.items() if v is not None}
                used_cids.append(mom_cid_node["_id"])
                used_ids.append(mom_row["_id"])
        obs = make_observations(measures, src=f"dg:{row['_id']}")
        if not obs:
            report.skip(id_, "matched an indicator row but every pulled value was blank")
            continue
        doc = make_series(
            id_, name_zh, name_en, "%", "index points", "index", "M", ["single"], 1, obs,
            "nbs", f"国家统计局数据发布 · 工业生产者出厂价格分类指数（{name_zh}，上年同月=100）",
            f"NBS DG data -- PPI classified index ({name_en}, prior-year-month=100)",
            coverage_note_zh=INDEX_DERIVE_NOTE,
            breaks=[PPI_REBASE_BREAK] if id_ == "nbs-ppi-yoy" else [],
        )
        if validate_and_write(doc, errors):
            report.add(doc, used_cids, used_ids, note="m=index(100 basis); m_yoy derived; no date windows needed (single cid to 1996)")
            fragments.append(catalog_entry(doc, "prices", tier))


# --------------------------------------------------------------------------------- PMI

def build_pmi(client, cache, report, errors):
    code = 1
    pmi_root = cache.walk_path(client, code, ["采购经理指数"])["_id"]
    mfg_node = cache.walk_path(client, code, ["采购经理指数", "制造业采购经理指数"])
    nonmfg_node = cache.walk_path(client, code, ["采购经理指数", "非制造业采购经理指数"])
    codes = month_codes(2000, MONTH_END)

    targets = [
        ("cflp-pmi-mfg", mfg_node, by_showname("制造业采购经理指数"), "制造业采购经理指数（PMI）", "Manufacturing PMI"),
        ("cflp-pmi-nonmfg", nonmfg_node, by_showname("非制造业商务活动指数"), "非制造业商务活动指数", "Non-Manufacturing Business Activity Index"),
    ]
    for id_, node, predicate, name_zh, name_en in targets:
        rows = get_indicators(client, node["_id"])
        row = find_indicator(rows, predicate)
        if row is None:
            report.skip(id_, f"headline row not found in {node['name'].strip()!r}")
            continue
        values = client.indicator_values(node["_id"], row["_id"], pmi_root, codes)
        m_map = {p: round(v, 1) for p, v in values.items() if v is not None}
        obs = make_observations({"m": m_map}, src=f"dg:{row['_id']}")
        if not obs:
            report.skip(id_, "matched row but all values blank")
            continue
        doc = make_series(
            id_, name_zh, name_en, "%", "%", "index", "M", ["single"], 1, obs,
            "cflp", f"国家统计局/中国物流与采购联合会 · {name_zh}", f"NBS/CFLP -- {name_en}",
            coverage_note_zh="扩散指数（50 为荣枯线），officially 无同比口径；仅取当月值。",
        )
        if validate_and_write(doc, errors):
            report.add(doc, [node["_id"]], [row["_id"]], note="diffusion index, no YoY published")
            fragments.append(catalog_entry(doc, "macro", 1))


# --------------------------------------------------------------------- unemployment

def build_unemployment(client, cache, report, errors):
    code = 1
    unemp_root = cache.walk_path(client, code, ["城镇调查失业率"])["_id"]
    leaf = cache.walk_path(client, code, ["城镇调查失业率", "城镇调查失业率"])
    rows = get_indicators(client, leaf["_id"])
    codes = month_codes(2015, MONTH_END)

    simple_targets = [
        ("nbs-urban-unemp", by_showname("全国城镇调查失业率"), "全国城镇调查失业率", "Surveyed Urban Unemployment Rate (National)", 1),
        ("nbs-urban-unemp-31city", by_showname("31个大城市城镇调查失业率"), "31个大城市城镇调查失业率", "Surveyed Urban Unemployment Rate (31 Major Cities)", 2),
    ]
    for id_, predicate, name_zh, name_en, tier in simple_targets:
        row = find_indicator(rows, predicate)
        if row is None:
            report.skip(id_, "row not found in 城镇调查失业率 indicator list")
            continue
        values = client.indicator_values(leaf["_id"], row["_id"], unemp_root, codes)
        m_map = {p: round(v, 1) for p, v in values.items() if v is not None}
        obs = make_observations({"m": m_map}, src=f"dg:{row['_id']}")
        if not obs:
            report.skip(id_, "matched row but all values blank")
            continue
        doc = make_series(
            id_, name_zh, name_en, "%", "%", "rate_pct", "M", ["single"], 1, obs,
            "nbs", f"国家统计局数据发布 · {name_zh}", f"NBS -- {name_en}",
        )
        if validate_and_write(doc, errors):
            report.add(doc, [leaf["_id"]], [row["_id"]])
            fragments.append(catalog_entry(doc, "employment", tier))

    # --- youth 16-24, with the exclude-students methodology break (2023-12) ---
    youth_row = find_indicator(rows, by_showname("16—24岁"))  # em-dash: 16—24岁
    if youth_row is None:
        report.skip("nbs-urban-unemp-youth-1624(+exstudent)", "16—24岁 row not found in 城镇调查失业率 indicator list")
        return
    full_codes = month_codes(2018, MONTH_END)
    values = client.indicator_values(leaf["_id"], youth_row["_id"], unemp_root, full_codes)
    m_map = {p: round(v, 1) for p, v in values.items() if v is not None}
    old_periods = sorted(p for p in m_map if p <= "2023-07")
    gap_periods = sorted(p for p in m_map if "2023-08" <= p <= "2023-11")
    new_periods = sorted(p for p in m_map if p >= "2023-12")
    if gap_periods:
        report.oddity(f"nbs-urban-unemp-youth-1624: unexpected data present for suspended months {gap_periods}")

    old_obs = make_observations({"m": {p: m_map[p] for p in old_periods}}, src=f"dg:{youth_row['_id']}")
    if old_obs:
        old_doc = make_series(
            "nbs-urban-unemp-youth-1624", "全国城镇16—24岁劳动力失业率（含在校生，旧口径）",
            "Surveyed Urban Unemployment Rate, Age 16-24 (incl. students, old basis)",
            "%", "%", "rate_pct", "M", ["single"], 1, old_obs,
            "nbs", "国家统计局数据发布 · 全国城镇16—24岁劳动力失业率（旧口径，含在校生）",
            "NBS -- Age 16-24 unemployment rate, old basis (included students), suspended 2023-08",
            coverage_note_zh=(
                "2023 年 8-11 月停止发布；2023 年 12 月起改为不含在校生口径并使用新 id "
                "nbs-urban-unemp-youth-1624-exstudent。本序列在 2023-07 冻结。"
            ),
            breaks=[{
                "effective": "2023-08", "kind": "suspended", "no_yoy_across": True,
                "superseded_by": "nbs-urban-unemp-youth-1624-exstudent",
                "note_zh": "2023年8月起暂停发布，同年12月起以不含在校生口径的新序列恢复。",
                "note_en": "Publication suspended Aug 2023; resumed Dec 2023 under a new excl.-students methodology as a new id.",
            }],
            end="2023-07",
        )
        if validate_and_write(old_doc, errors):
            report.add(old_doc, [leaf["_id"]], [youth_row["_id"]], note="frozen old-basis series, superseded_by exstudent id")
            fragments.append(catalog_entry(old_doc, "employment", 2, superseded_by="nbs-urban-unemp-youth-1624-exstudent"))

    new_obs = make_observations({"m": {p: m_map[p] for p in new_periods}}, src=f"dg:{youth_row['_id']}")
    if new_obs:
        new_obs[0].setdefault("flags", []).append("break_first")
        new_doc = make_series(
            "nbs-urban-unemp-youth-1624-exstudent", "全国城镇16—24岁劳动力失业率（不含在校生）",
            "Surveyed Urban Unemployment Rate, Age 16-24 (excl. students)",
            "%", "%", "rate_pct", "M", ["single"], 1, new_obs,
            "nbs", "国家统计局数据发布 · 全国城镇16—24岁劳动力失业率（不含在校生）",
            "NBS -- Age 16-24 unemployment rate, excluding students (new methodology from 2023-12)",
            coverage_note_zh=(
                "DG 库中与旧口径共用同一 indicator id（NBS 未改 id，仅在 2023-12 起改变口径并在 "
                "i_annotation 中说明：“2023年12月起，失业率年龄分组进行了调整，往月数据包含在校生”）。"
                "本表按 DATA-CONTRACT 规则拆分为新 id，起点 2023-12。"
            ),
            breaks=[{
                "effective": "2023-12", "kind": "methodology", "no_yoy_across": True, "yoy_valid_from": "2024-12",
                "supersedes": "nbs-urban-unemp-youth-1624",
                "note_zh": "自 2023-12 起改为不含在校生口径，与旧序列不可比。",
                "note_en": "From 2023-12, excludes students -- not comparable with the old series.",
            }],
        )
        if validate_and_write(new_doc, errors):
            report.add(new_doc, [leaf["_id"]], [youth_row["_id"]], note="new-basis series, break_first flagged on 2023-12")
            fragments.append(catalog_entry(new_doc, "employment", 2, supersedes="nbs-urban-unemp-youth-1624"))
    if not old_obs and not new_obs:
        report.skip("nbs-urban-unemp-youth-1624(+exstudent)", "16-24 row matched but produced no observations at all")


# ---------------------------------------------------------------------------------- IVA

def build_iva(client, cache, report, errors):
    code = 1
    industry_root = cache.walk_path(client, code, ["工业"])["_id"]
    leaf = cache.walk_path(client, code, ["工业", "规上工业增加值增长速度"])
    rows = get_indicators(client, leaf["_id"])
    m_row = find_indicator(rows, by_showname("同比增长"))
    ytd_row = find_indicator(rows, by_showname("累计增长"))
    if m_row is None:
        report.skip("nbs-industrial-va", "同比增长 row not found under 规上工业增加值增长速度")
        return
    codes = month_codes(2015, MONTH_END)
    m_values = client.indicator_values(leaf["_id"], m_row["_id"], industry_root, codes)
    measures = {"m": {p: round(v, 1) for p, v in m_values.items() if v is not None}}
    used_ids = [m_row["_id"]]
    if ytd_row is not None:
        ytd_values = client.indicator_values(leaf["_id"], ytd_row["_id"], industry_root, codes)
        measures["ytd"] = {p: round(v, 1) for p, v in ytd_values.items() if v is not None}
        used_ids.append(ytd_row["_id"])
    obs = make_observations(measures, src=f"dg:{m_row['_id']}")
    if not obs:
        report.skip("nbs-industrial-va", "matched rows but all values blank")
        return
    doc = make_series(
        "nbs-industrial-va", "规模以上工业增加值同比增长", "Industrial Value Added, YoY Growth (above-designated-size)",
        "%", "%", "yoy_pct", "M", ["single", "ytd"] if ytd_row is not None else ["single"], 1, obs,
        "nbs", "国家统计局数据发布 · 规模以上工业增加值增长速度",
        "NBS -- Industrial value added growth rate (above-designated-size enterprises)",
        coverage_note_zh="国家统计局仅公布同比/累计同比增速，不公布绝对值；m 与 ytd 均为百分比而非水平值。",
    )
    if validate_and_write(doc, errors):
        report.add(doc, [leaf["_id"]], used_ids, note="growth-rate-only series (no level published)")
        fragments.append(catalog_entry(doc, "macro", 1))


# ---------------------------------------------------------------------------------- FAI

def build_fai(client, cache, report, errors):
    code = 1
    fai_root = cache.walk_path(client, code, ["固定资产投资 (不含农户)"])["_id"]
    leaf = cache.walk_path(client, code, ["固定资产投资 (不含农户)", "固定资产投资概况"])
    rows = get_indicators(client, leaf["_id"])
    row = find_indicator(rows, by_showname("固定资产投资额累计增长", exclude=("民间", "第一产业", "第二产业", "第三产业")))
    if row is None:
        report.skip("nbs-fai", "headline 固定资产投资额累计增长 row not found")
        return
    codes = month_codes(2015, MONTH_END)
    values = client.indicator_values(leaf["_id"], row["_id"], fai_root, codes)
    ytd_yoy_map = {p: round(v, 1) for p, v in values.items() if v is not None}
    obs = make_observations({"ytd_yoy": ytd_yoy_map}, src=f"dg:{row['_id']}")
    if not obs:
        report.skip("nbs-fai", "matched row but all values blank")
        return
    doc = make_series(
        "nbs-fai", "固定资产投资完成额累计增长（不含农户）", "Fixed Asset Investment, YTD YoY Growth (excl. rural households)",
        "%", "%", "yoy_pct", "M", ["ytd"], 1, obs,
        "nbs", "国家统计局数据发布 · 固定资产投资概况",
        "NBS -- Fixed asset investment overview",
        coverage_note_zh=(
            "自查遍 1998-2003/2004-2011/2012-2017/2018- 各期分行业表，DG 库均只有"
            "“累计增长(%)”，未见绝对额（亿元）字段——国家统计局近年固定资产投资确实只发布同比增速，"
            "不发布绝对值；ytd（水平值）留空，仅填 ytd_yoy。"
        ),
    )
    if validate_and_write(doc, errors):
        report.add(doc, [leaf["_id"]], [row["_id"]], note="YTD-YoY only, no level published anywhere in the FAI tree")
        fragments.append(catalog_entry(doc, "macro", 2))


# --------------------------------------------------------------------------------- trade

def build_trade(client, cache, report, errors):
    code = 1
    trade_root = cache.walk_path(client, code, ["对外经济"])["_id"]
    leaf = cache.walk_path(client, code, ["对外经济", "货物进出口总额"])
    rows = get_indicators(client, leaf["_id"])
    codes = month_codes(2000, MONTH_END)

    def pull(prefix):
        # "出口总值..." (exports) is a *substring* of "进出口总值..." (grand total) --
        # e.g. "出口总值当期值" in "进出口总值当期值" is True -- so without excluding
        # the grand-total rows, exports would silently resolve to the total-trade
        # indicator instead (imports doesn't have this collision, but exclude
        # defensively for both so the fix doesn't depend on which prefix is passed).
        exclude = ("进出口",)
        m = find_indicator(rows, by_showname(f"{prefix}总值当期值", exclude=exclude))
        m_yoy = find_indicator(rows, by_showname(f"{prefix}总值同比增长", exclude=exclude))
        ytd = find_indicator(rows, by_showname(f"{prefix}总值累计值", exclude=exclude))
        ytd_yoy = find_indicator(rows, by_showname(f"{prefix}总值累计增长", exclude=exclude))
        return m, m_yoy, ytd, ytd_yoy

    targets = [
        ("customs-exports-usd", "出口", "出口总值（美元）", "Exports (USD)", 1),
        ("customs-imports-usd", "进口", "进口总值（美元）", "Imports (USD)", 1),
    ]
    for id_, prefix, name_zh, name_en, tier in targets:
        m_row, yoy_row, ytd_row, ytdyoy_row = pull(prefix)
        if m_row is None:
            report.skip(id_, f"{prefix}总值当期值 row not found under 货物进出口总额")
            continue
        measures = {}
        used_ids = []
        for key, row in (("m", m_row), ("m_yoy", yoy_row), ("ytd", ytd_row), ("ytd_yoy", ytdyoy_row)):
            if row is None:
                continue
            values = client.indicator_values(leaf["_id"], row["_id"], trade_root, codes)
            # raw unit is 千美元 (thousand USD); store as 亿美元 (100M USD) for readability.
            if key in ("m", "ytd"):
                measures[key] = {p: round(v / 100000, 2) for p, v in values.items() if v is not None}
            else:
                measures[key] = {p: round(v, 1) for p, v in values.items() if v is not None}
            used_ids.append(row["_id"])
        obs = make_observations(measures, src=f"dg:{used_ids[0]}")
        if not obs:
            report.skip(id_, "matched rows but all values blank")
            continue
        doc = make_series(
            id_, name_zh, name_en, "亿美元", "100M USD", "level", "M", ["single", "ytd"], 2, obs,
            "customs", f"国家统计局国家数据（转引海关总署）· {name_zh}",
            f"NBS DG data (redistributing GACC customs data) -- {name_en}",
            coverage_note_zh=(
                "任务要求人民币计价月度进出口，但本 DG 目录（对外经济→货物进出口总额）仅提供美元（千美元）计价；"
                "人民币计价月度数据在 NBS 月度活动稿（社零同批次文章）而非本 DG 目录中，超出本次 DG-only 补采范围。"
                "id 加 -usd 后缀以避免冒领 MIGRATION-MAP 预留给人民币版本的裸 id。原始单位千美元，已换算为亿美元。"
            ),
        )
        if validate_and_write(doc, errors):
            report.add(doc, [leaf["_id"]], used_ids, note="USD only (千美元->亿美元); RMB variant not in this DG tree")
            fragments.append(catalog_entry(doc, "macro", tier))


# --------------------------------------------------------------------------------- money

def build_money(client, cache, report, errors):
    code = 1
    finance_root = cache.walk_path(client, code, ["金融"])["_id"]
    leaf = cache.walk_path(client, code, ["金融", "货币供应量"])
    rows = get_indicators(client, leaf["_id"])
    codes = month_codes(1999, MONTH_END)

    targets = [
        ("pbc-m0", "(M0)", "流通中现金 (M0)", "Cash in Circulation (M0)", 2),
        ("pbc-m1", "(M1)", "货币 (M1)", "Narrow Money (M1)", 1),
        ("pbc-m2", "(M2)", "货币和准货币 (M2)", "Broad Money (M2)", 1),
    ]
    m1_levels_for_report: dict[str, float] = {}
    for id_, tag, name_zh, name_en, tier in targets:
        # NBS pads inconsistently -- e.g. "货币和准货币 (M2) 供应量_期末值" has a space
        # after the paren, so a single concatenated f"{tag}供应量..." string (no space)
        # never matches. Require the tag and the measure word as independent substrings.
        level_row = find_indicator(rows, by_all_showname(tag, "期末值"))
        yoy_row = find_indicator(rows, by_all_showname(tag, "同比增长"))
        if level_row is None:
            report.skip(id_, f"{tag} 期末值 row not found under 货币供应量")
            continue
        level_values = client.indicator_values(leaf["_id"], level_row["_id"], finance_root, codes)
        m_map = {p: round(v, 2) for p, v in level_values.items() if v is not None}
        measures = {"m": m_map}
        used_ids = [level_row["_id"]]
        if yoy_row is not None:
            yoy_values = client.indicator_values(leaf["_id"], yoy_row["_id"], finance_root, codes)
            measures["m_yoy"] = {p: round(v, 1) for p, v in yoy_values.items() if v is not None}
            used_ids.append(yoy_row["_id"])
        if id_ == "pbc-m1":
            m1_levels_for_report = m_map

        note_zh = None
        breaks = []
        if id_ == "pbc-m1":
            jump = _describe_m1_jump(m_map, report)
            note_zh = (
                "M1 于 2025-01 起改用新口径（计入个人活期存款及非银行支付机构备付金）。"
                f"实际观察：{jump}"
            )
            breaks = [{
                "effective": "2025-01", "kind": "redefinition", "no_yoy_across": True, "yoy_valid_from": "2025-01",
                "note_zh": "M1 口径调整（新增个人活期存款、非银行支付机构客户备付金），跨口径同比不可比。",
                "note_en": "M1 redefined (adds personal demand deposits & non-bank payment institution reserves); YoY not comparable across the seam.",
            }]
        obs = make_observations(measures, src=f"dg:{used_ids[0]}")
        if not obs:
            report.skip(id_, "matched rows but all values blank")
            continue
        doc = make_series(
            id_, name_zh + "供应量", name_en, "亿元", "100M CNY", "level", "M", ["single"], 2, obs,
            "pbc", f"中国人民银行/国家统计局国家数据 · {name_zh}供应量",
            f"PBoC (via NBS DG data) -- {name_en} supply",
            coverage_note_zh=note_zh,
            breaks=breaks,
        )
        if validate_and_write(doc, errors):
            report.add(doc, [leaf["_id"]], used_ids)
            fragments.append(catalog_entry(doc, "money-credit", tier))
    return m1_levels_for_report


def _describe_m1_jump(m1_levels: dict[str, float], report: Report) -> str:
    """Empirically check whether 2024-12->2025-01 shows a level jump (old-basis before,
    new-definition first-print in 2025-01) or whether earlier history already sits at
    the new, higher magnitude (retroactively restated). Records the finding in the
    report regardless of which way it goes -- this is exactly the ambiguity the task
    asked to resolve by observation, not assumption."""
    dec = m1_levels.get("2024-12")
    jan = m1_levels.get("2025-01")
    prior_deltas = []
    months = sorted(m1_levels)
    for a, b in zip(months, months[1:]):
        if a >= "2023-01" and b < "2025-01":
            prior_deltas.append(abs(m1_levels[b] - m1_levels[a]) / m1_levels[a])
    typical_mom = (sum(prior_deltas) / len(prior_deltas)) if prior_deltas else None
    if dec is None or jan is None:
        text = f"2024-12 或 2025-01 数据缺失（dec={dec}, jan={jan}），无法判断。"
        report.oddity(f"pbc-m1: {text}")
        return text
    jump_pct = (jan - dec) / dec * 100
    typical_pct = (typical_mom * 100) if typical_mom else None
    if typical_pct is not None and abs(jump_pct) > max(5 * typical_pct, 3):
        verdict = (
            f"OLD-BASIS then re-definition: 2024-12={dec:.1f}亿元 -> 2025-01={jan:.1f}亿元 "
            f"({jump_pct:+.1f}%，远超此前典型环比 ~{typical_pct:.2f}%）——历史看起来是旧口径，"
            f"2025-01 是新口径下的第一个印数，DG 库未见对 2024-01~2024-12 的追溯改写。"
        )
    else:
        verdict = (
            f"RESTATED（未见跳升）: 2024-12={dec:.1f}亿元 -> 2025-01={jan:.1f}亿元 "
            f"({jump_pct:+.1f}%，与此前典型环比 ~{typical_pct if typical_pct else 'NA'}% 相近）——"
            f"历史数据看起来已按新口径重述，或口径调整本身对总量影响很小。"
        )
    report.oddity(f"pbc-m1 basis check: {verdict}")
    return verdict


# ----------------------------------------------------------------------------------- GDP

def build_gdp(client, cache, report, errors):
    code = 2
    accounts_root = cache.walk_path(client, code, ["国民经济核算"])["_id"]
    level_leaf = cache.walk_path(client, code, ["国民经济核算", "国内生产总值 (现价)"])
    index_leaf = cache.walk_path(client, code, ["国民经济核算", "国内生产总值指数"])
    contrib_leaf = cache.walk_path(client, code, ["国民经济核算", "三大需求对国内生产总值增长的贡献率"])

    codes = quarter_codes(1992, QUARTER_END)

    level_rows = get_indicators(client, level_leaf["_id"])
    index_rows = get_indicators(client, index_leaf["_id"])
    m_row = find_indicator(level_rows, by_showname("国内生产总值当季值"))
    ytd_row = find_indicator(level_rows, by_showname("国内生产总值累计值"))
    idx_row = find_indicator(index_rows, by_showname("国内生产总值指数 (上年同期=100) 当季值"))

    measures, used_ids = {}, []
    if m_row is not None:
        values = client.indicator_values(level_leaf["_id"], m_row["_id"], accounts_root, codes)
        measures["m"] = {p: round(v, 1) for p, v in values.items() if v is not None}
        used_ids.append(m_row["_id"])
    if ytd_row is not None:
        values = client.indicator_values(level_leaf["_id"], ytd_row["_id"], accounts_root, codes)
        measures["ytd"] = {p: round(v, 1) for p, v in values.items() if v is not None}
        used_ids.append(ytd_row["_id"])
    real_yoy_map = {}
    if idx_row is not None:
        values = client.indicator_values(index_leaf["_id"], idx_row["_id"], accounts_root, codes)
        real_yoy_map = {p: round(v - 100, 1) for p, v in values.items() if v is not None}
        measures["real_yoy"] = real_yoy_map
        used_ids.append(idx_row["_id"])

    obs = make_observations(measures, src=f"dg:{used_ids[0]}" if used_ids else "dg:unknown")
    if not obs:
        report.skip("nbs-gdp", "GDP level/index rows not found or all blank")
    else:
        doc = make_series(
            "nbs-gdp", "国内生产总值", "Gross Domestic Product",
            "亿元", "100M CNY", "level", "Q", ["single", "ytd"], 1, obs,
            "nbs", "国家统计局数据发布 · 国内生产总值（现价、不变价指数）",
            "NBS -- GDP (current price level; constant-price index for real YoY)",
            coverage_note_zh=(
                "m/ytd 为现价（名义）水平值；real_yoy 由“国内生产总值指数（上年同期=100，当季值）”减 100 换算，"
                "是对官方发布不变价指数的单位换算，非重新计算。名义同比未见 DG 独立字段，未填写 m_yoy。"
            ),
        )
        if validate_and_write(doc, errors):
            report.add(doc, [level_leaf["_id"], index_leaf["_id"]], used_ids, note="real_yoy derived from constant-price index(100 basis)")
            fragments.append(catalog_entry(doc, "macro", 1))

    contrib_rows = get_indicators(client, contrib_leaf["_id"])
    contrib_targets = [
        ("nbs-gdp-contribution-consumption", "最终消费支出对国内生产总值增长贡献率", "最终消费支出", "Final Consumption Expenditure", 2),
        ("nbs-gdp-contribution-investment", "资本形成总额对国内生产总值增长贡献率", "资本形成总额", "Gross Capital Formation", 2),
        ("nbs-gdp-contribution-netexports", "货物和服务净出口对国内生产总值增长贡献率", "货物和服务净出口", "Net Exports of Goods & Services", 2),
    ]
    for id_, prefix, short_zh, short_en, tier in contrib_targets:
        m_row = find_indicator(contrib_rows, by_showname(f"{prefix}当季值"))
        ytd_row = find_indicator(contrib_rows, by_showname(f"{prefix}累计值"))
        if m_row is None:
            report.skip(id_, f"{prefix}当季值 row not found")
            continue
        measures, used_ids = {}, []
        values = client.indicator_values(contrib_leaf["_id"], m_row["_id"], accounts_root, codes)
        measures["m"] = {p: round(v, 1) for p, v in values.items() if v is not None}
        used_ids.append(m_row["_id"])
        if ytd_row is not None:
            values = client.indicator_values(contrib_leaf["_id"], ytd_row["_id"], accounts_root, codes)
            measures["ytd"] = {p: round(v, 1) for p, v in values.items() if v is not None}
            used_ids.append(ytd_row["_id"])
        obs = make_observations(measures, src=f"dg:{used_ids[0]}")
        if not obs:
            report.skip(id_, "matched rows but all values blank")
            continue
        doc = make_series(
            id_, f"{short_zh}对国内生产总值增长贡献率", f"Contribution to GDP Growth: {short_en}",
            "%", "pp", "ratio", "Q", ["single", "ytd"], 1, obs,
            "nbs", f"国家统计局数据发布 · 三大需求对国内生产总值增长的贡献率（{short_zh}）",
            f"NBS -- Contribution to GDP growth, three demand components ({short_en})",
            coverage_note_zh="“贡献率”为对当期 GDP 增速的拉动百分点占比，非该分项自身的同比增速。",
        )
        if validate_and_write(doc, errors):
            report.add(doc, [contrib_leaf["_id"]], used_ids)
            fragments.append(catalog_entry(doc, "macro", tier))


# ------------------------------------------------------------------------------------ main

fragments: list[dict] = []


def main():
    client = DGClient()
    cache = TreeCache.load(DEFAULT_CACHE_PATH)
    report = Report()
    errors: list[str] = []

    steps = [
        ("CPI", build_cpi),
        ("PPI", build_ppi),
        ("PMI", build_pmi),
        ("Unemployment", build_unemployment),
        ("Industrial value added", build_iva),
        ("FAI", build_fai),
        ("Trade", build_trade),
        ("Money supply", build_money),
        ("GDP", build_gdp),
    ]
    for label, fn in steps:
        try:
            fn(client, cache, report, errors)
        except (DGError, TreePathError) as exc:
            report.skip(label, f"hard failure, aborted this family: {exc}")
        finally:
            cache.save(DEFAULT_CACHE_PATH)

    # explicitly-searched-and-not-found targets (per task: skip + flag, don't brute force)
    report.skip(
        "Consumer confidence index (+ sub-indices)",
        "Searched all 14 monthly-tree domains (价格指数/工业/能源/固定资产投资/服务业生产指数/"
        "城镇调查失业率/房地产/国内贸易/对外经济/交通运输/邮电通信/采购经理指数/财政/金融) plus the "
        "quarterly tree's 国民经济核算/人民生活/文化/国内贸易 branches -- no 景气指数 or 消费者信心 node "
        "anywhere in this DG catalog. Likely not mirrored into data.stats.gov.cn's public DG tree at all "
        "(NBS may only publish it via a non-DG channel). Not brute-forced further per budget guidance.",
    )
    report.skip(
        "社融 (aggregate financing to the real economy) stock + flow",
        "金融 (Finance) domain under 月度数据 has exactly one leaf, 货币供应量 (M0/M1/M2) -- "
        "confirmed by re-querying its children directly (count=1). 社融 is a PBoC-specific release "
        "not mirrored into this NBS DG catalog; per docs/ACQUISITION.md Group 6 it needs the "
        "wzdig.pbc.gov.cn search+parse route instead, out of scope for a DG-only backfill.",
    )
    report.skip(
        "Income / consumption expenditure (national/urban/rural, quarterly YTD)",
        "Per task instructions: docs/MIGRATION-MAP.md §6b already covers all income_disposable / "
        "consumption_expenditure series (and their urban/rural splits) from the existing archive, "
        "2013-> quarterly. Confirmed present in the quarterly DG tree too (人民生活 domain) but "
        "intentionally not duplicated here -- would collide with the migration agent's ids.",
    )
    report.skip(
        "Retail family (社会消费品零售总额 etc.)",
        "Owned by docs/MIGRATION-MAP.md §6a / the migration agent; not touched by this backfill agent "
        "(explicit instruction not to duplicate ids the migration already covers).",
    )
    report.oddity(
        "docs/MIGRATION-MAP.md §8 lists nbs-urban-unemp-2534/nbs-urban-unemp-3159 assuming a 25-34 age "
        "bracket; the DG tree's actual age brackets are 25—29岁, 30—59岁, and 25—59岁 (no 25-34, no 34 "
        "at all). Flagging the mismatch for whoever wires that part of the catalog -- not minted here "
        "since the task only asked for national/31-city/youth-exstudent unemployment."
    )

    if errors:
        report.oddity("SCHEMA VALIDATION FAILURES (series NOT written): " + " | ".join(errors))

    write_json(CATALOG_FRAGMENT_PATH, fragments)
    REPORT_PATH.write_text(report.render(), encoding="utf-8")

    print(f"Built {len(report.built)} series, {report.total_observations()} total observations.")
    print(f"Skipped {len(report.skipped)} targets.")
    print(f"Schema validation errors: {len(errors)}")
    if errors:
        for e in errors:
            print("  ERROR:", e)
    print(f"Total DG HTTP requests this run: {client.request_count}")
    return report, errors


if __name__ == "__main__":
    main()
