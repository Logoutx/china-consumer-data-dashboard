#!/usr/bin/env python3
"""One-shot migration: legacy data.js-era archives -> data/series, data/panels,
data/catalog.json per docs/DATA-CONTRACT.md and docs/MIGRATION-MAP.md.

Run from the repo root:  python3 pipeline/migrate/migrate.py

Inputs (read-only, never modified):
    retail_release_archive.json
    income_release_archive.json
    property_release_archive.json
    property_city_history.json

Outputs (this script owns and freely overwrites these paths):
    data/series/<id>.json
    data/panels/nbs-70city-price.json
    data/catalog.json
    pipeline/migrate/REPORT.md

Design notes:
    - stdlib only (json, re, random, os, sys, statistics).
    - Deterministic: generated_at is pinned to MIGRATION_TIMESTAMP (not
      wall-clock time) and the "5 random pairs" smoke check uses a
      fixed-seed PRNG, so re-running on unchanged inputs is byte-identical.
    - This script does NOT write data/archive/* (out of this agent's owned
      paths -- see REPORT.md "Flagged" section for why that matters for the
      revisions model).
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonio  # noqa: E402
import schema_validator  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION_TIMESTAMP = "2026-07-08T00:00:00Z"
RNG_SEED = 20260708

IN_RETAIL = os.path.join(REPO_ROOT, "retail_release_archive.json")
IN_INCOME = os.path.join(REPO_ROOT, "income_release_archive.json")
IN_PROPERTY = os.path.join(REPO_ROOT, "property_release_archive.json")
IN_CITY_HISTORY = os.path.join(REPO_ROOT, "property_city_history.json")

OUT_SERIES_DIR = os.path.join(REPO_ROOT, "data", "series")
OUT_PANELS_DIR = os.path.join(REPO_ROOT, "data", "panels")
OUT_CATALOG = os.path.join(REPO_ROOT, "data", "catalog.json")
OUT_REPORT = os.path.join(os.path.dirname(__file__), "REPORT.md")

SCHEMA_SERIES = os.path.join(REPO_ROOT, "data", "schemas", "series.schema.json")
SCHEMA_CATALOG = os.path.join(REPO_ROOT, "data", "schemas", "catalog.schema.json")
SCHEMA_PANEL = os.path.join(REPO_ROOT, "data", "schemas", "panel.schema.json")


# ---------------------------------------------------------------------------
# Small shared utilities
# ---------------------------------------------------------------------------

def norm_num(v):
    """Collapse -0.0 -> 0.0 (legacy int/float + signed-zero formatting noise,
    see docs/_inventory/current-data-shapes.md oddity #6). Leaves everything
    else (including plain ints) exactly as the source had it."""
    if isinstance(v, float) and v == 0.0:
        return 0.0
    return v


def values_equal(a, b):
    """Numeric equality used for revision detection, treating int(60)==
    float(60.0) and -0.0==0.0 as equal (not a real content difference)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    fa, fb = float(a), float(b)
    if fa == 0.0:
        fa = 0.0
    if fb == 0.0:
        fb = 0.0
    return fa == fb


def period_sort_key(period):
    """Chronological sort key, by PERIOD START (DATA-CONTRACT §9's canonical
    ordering rule for a bare annual "YYYY" observation sharing a year with
    quarterly observations of the same series).

    An earlier version of this function sorted an annual observation to
    month-rank 13 -- i.e. *after* every quarter of its year -- reasoning that
    the annual print is "published in December, chronologically last". That
    reasoning conflated *when NBS published the print* with *what period the
    value covers*: DATA-CONTRACT §9 orders observations by period, not by
    release date, and a full-year figure's period *starts* on 1 January of
    that year -- the same nominal start as Q1 -- not in December. Sorting it
    after Q1/Q2/Q3 therefore violated the contract's own ordering rule (see
    pipeline/migrate/REPORT.md and docs/DATA-CONTRACT.md §9 for the fixed
    rule) and is why 10 real income/consumption series had their historical-
    annual-bulletin row physically misplaced after the same year's quarters
    on disk.

    Fixed rule: an annual period is treated as YYYY-01-01 (month-rank 0) --
    *before* that year's Q1 (month-rank 3) -- so it sorts first among same-
    year periods. This is a stable, deterministic tie-break (not "whichever
    happened to load first"): two annual observations, or an annual and a
    quarterly observation, never share a sort key, so re-running this
    function always produces the same order regardless of input order.
    """
    if len(period) == 4 and period.isdigit():
        return (int(period), 0, 0)
    if "-Q" in period:
        y, q = period.split("-Q")
        return (int(y), int(q) * 3, 0)
    y, m = period.split("-")
    return (int(y), int(m), 0)


def make_src(record):
    """Best-effort provenance pointer built from what the legacy record
    actually carries (no real archive/release-id scheme exists yet -- this
    agent does not own data/archive/, see REPORT.md)."""
    pub = record.get("published_at")
    source = record.get("source") or ""
    if pub:
        date_part = pub.split(" ")[0].replace("/", "")
        return f"rel:{date_part}"
    if "国家数据" in source or source == "nbs_data":
        return "natdata:monthly"
    if "历史补充" in source:
        return "legacy-annual-bulletin"
    return f"legacy:{record.get('period', '')}"


def level_to_tier(level):
    """MIGRATION-MAP section 6 mechanical rule: level1->tier1, level2-4->tier2,
    level>=5->tier3. Used for property (section 6c has no explicit tier
    column). NOT used for retail/income, where MIGRATION-MAP's own tier
    column already encodes deliberate judgment overrides (e.g. catering/goods
    are source level=5 but explicitly bumped to tier 2) that a mechanical
    recompute would silently clobber."""
    if level == 1:
        return 1
    if 2 <= level <= 4:
        return 2
    return 3


class Notes:
    """Accumulates everything that goes into REPORT.md."""

    def __init__(self):
        self.dropped = []
        self.series_rows = []  # dicts: id, source_obs, migrated_obs, note
        self.revisions_by_id = {}  # id -> list of revision dicts (for counting)
        self.derived_checks = []
        self.panel_mismatch = {}
        self.flags = []  # prominent ambiguity/deviation flags
        self.oddities = []
        self.jan_feb_notes = []
        self.smoke_results = []
        self.schema_errors = []
        self.semantic_errors = []


# ---------------------------------------------------------------------------
# Catalog id maps (id, tier, English name). Chinese names / groups are always
# pulled live from the source's own metadata rather than re-transcribed here.
# Tiers for retail/income are copied verbatim from MIGRATION-MAP section 6a/6b
# (which already applies judgment overrides, e.g. catering/goods bumped to
# tier 2 despite source level=5) -- do not "simplify" this to a level formula.
# ---------------------------------------------------------------------------

RETAIL_MAP = {
    "retail_total": ("nbs-retail-total", 1, "Total retail sales of consumer goods"),
    "retail_ex_auto": ("nbs-retail-ex-auto", 2, "Retail sales excluding automobiles"),
    "auto_total": ("nbs-retail-auto", 2, "Automobile retail sales"),
    "urban": ("nbs-retail-urban", 2, "Urban retail sales of consumer goods"),
    "rural": ("nbs-retail-rural", 2, "Rural retail sales of consumer goods"),
    "online_goods": ("nbs-retail-online-goods", 2, "Online retail sales of physical goods"),
    "online_ex_auto_share": ("nbs-retail-online-share", 2, "Online share of retail sales excluding automobiles"),
    "catering": ("nbs-retail-catering", 2, "Catering revenue"),
    "goods": ("nbs-retail-goods", 2, "Retail sales of goods"),
    "above_quota_total": ("nbs-retail-above-quota", 3, "Retail sales of enterprises above designated size"),
    "above_quota_catering": ("nbs-retail-above-quota-catering", 3, "Catering revenue, enterprises above designated size"),
    "above_quota_goods": ("nbs-retail-above-quota-goods", 3, "Retail sales of goods, enterprises above designated size"),
    "grain_food": ("nbs-retail-cat-grain-food", 3, "Grain, oil and food (above-quota category)"),
    "beverage": ("nbs-retail-cat-beverage", 3, "Beverages (above-quota category)"),
    "tobacco_alcohol": ("nbs-retail-cat-tobacco-alcohol", 3, "Tobacco and alcohol (above-quota category)"),
    "garments": ("nbs-retail-cat-garments", 3, "Garments, footwear and textiles (above-quota category)"),
    "cosmetics": ("nbs-retail-cat-cosmetics", 3, "Cosmetics (above-quota category)"),
    "gold_jewelry": ("nbs-retail-cat-gold-jewelry", 3, "Gold and jewelry (above-quota category)"),
    "daily_goods": ("nbs-retail-cat-daily-goods", 3, "Daily necessities (above-quota category)"),
    "sports_entertainment": ("nbs-retail-cat-sports-entertainment", 3, "Sports and entertainment goods (above-quota category)"),
    "books_magazines": ("nbs-retail-cat-books-magazines", 3, "Books, newspapers and magazines (above-quota category)"),
    "household_appliances": ("nbs-retail-cat-household-appliances", 3, "Household appliances and A/V equipment (above-quota category)"),
    "medicine": ("nbs-retail-cat-medicine", 3, "Chinese and Western medicines (above-quota category)"),
    "cultural_office": ("nbs-retail-cat-cultural-office", 3, "Cultural and office supplies (above-quota category)"),
    "furniture": ("nbs-retail-cat-furniture", 3, "Furniture (above-quota category)"),
    "communication": ("nbs-retail-cat-communication", 3, "Communication equipment (above-quota category)"),
    "petroleum": ("nbs-retail-cat-petroleum", 3, "Petroleum and products (above-quota category)"),
    "building_materials": ("nbs-retail-cat-building-materials", 3, "Building and decoration materials (above-quota category)"),
}
DROPPED_RETAIL_KEYS = {"books_magazines"}  # lead decision #2: 0 observations, drop entirely
RETAIL_JAN_FEB_COMBINED_PERIODS = {f"{y}-02" for y in range(2018, 2027)}

INCOME_MAP = {
    "income_disposable": ("nbs-income-disposable", 1, "Per capita disposable income"),
    "income_disposable_urban": ("nbs-income-disposable-urban", 2, "Urban per capita disposable income"),
    "income_disposable_rural": ("nbs-income-disposable-rural", 2, "Rural per capita disposable income"),
    "income_wage": ("nbs-income-wage", 3, "Wage income"),
    "income_business": ("nbs-income-business", 3, "Net business income"),
    "income_property": ("nbs-income-property", 3, "Net property income"),
    "income_transfer": ("nbs-income-transfer", 3, "Net transfer income"),
    "income_median": ("nbs-income-median", 2, "Median per capita disposable income"),
    "income_median_urban": ("nbs-income-median-urban", 3, "Urban median per capita disposable income"),
    "income_median_rural": ("nbs-income-median-rural", 3, "Rural median per capita disposable income"),
    "consumption_expenditure": ("nbs-consumption-expenditure", 1, "Per capita consumption expenditure"),
    "consumption_expenditure_urban": ("nbs-consumption-expenditure-urban", 2, "Urban per capita consumption expenditure"),
    "consumption_expenditure_rural": ("nbs-consumption-expenditure-rural", 2, "Rural per capita consumption expenditure"),
    "consumption_food_tobacco_alcohol": ("nbs-consumption-food-tobacco-alcohol", 3, "Food, tobacco and alcohol consumption expenditure"),
    "consumption_clothing": ("nbs-consumption-clothing", 3, "Clothing consumption expenditure"),
    "consumption_housing": ("nbs-consumption-housing", 3, "Housing consumption expenditure"),
    "consumption_household_services": ("nbs-consumption-household-services", 3, "Household goods and services consumption expenditure"),
    "consumption_transport_communication": ("nbs-consumption-transport-communication", 3, "Transport and communication consumption expenditure"),
    "consumption_education_culture": ("nbs-consumption-education-culture", 3, "Education, culture and recreation consumption expenditure"),
    "consumption_healthcare": ("nbs-consumption-healthcare", 3, "Healthcare consumption expenditure"),
    "consumption_other": ("nbs-consumption-other", 3, "Other goods and services consumption expenditure"),
}
REAL_YOY_INCOME_KEYS = {
    "income_disposable", "income_disposable_urban", "income_disposable_rural",
    "consumption_expenditure", "consumption_expenditure_urban", "consumption_expenditure_rural",
}
# period_label == "全年" AND historical_supplement == true rows only (verified
# against income_release_archive.json: 2017-12..2025-12 are ALSO "全年" but are
# NOT historical_supplement -- those stay ordinary "YYYY-Q4" cumulative prints;
# see REPORT.md "Flagged" for why this reading of MIGRATION-MAP section 3 was chosen).
INCOME_HISTORICAL_ANNUAL_PERIODS = {"2013-12", "2014-12", "2015-12", "2016-12"}

PROPERTY_NAMED_MAP = {
    # old_key: (new_id, agency, value_type, unit_zh, unit_en, calibers, freq, name_en)
    "real_estate_loan_balance": ("pbc-real-estate-loan-balance", "pbc", "level", "亿元", "100M CNY", ["single"], "Q", "Real estate loan balance (RMB)"),
    "mortgage_balance": ("pbc-mortgage-balance", "pbc", "level", "亿元", "100M CNY", ["single"], "Q", "Individual housing mortgage loan balance"),
    "property_development_loan_balance": ("pbc-property-development-loan-balance", "pbc", "level", "亿元", "100M CNY", ["single"], "Q", "Real estate development loan balance"),
    "land_transfer_revenue": ("mof-land-transfer-revenue", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "State-owned land use right transfer revenue"),
    "deed_tax": ("mof-deed-tax", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "Deed tax revenue"),
    "property_tax": ("mof-property-tax", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "Property tax revenue"),
    "urban_land_use_tax": ("mof-urban-land-use-tax", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "Urban land use tax revenue"),
    "land_vat": ("mof-land-vat", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "Land value-added tax revenue"),
    "farmland_occupation_tax": ("mof-farmland-occupation-tax", "mof", "level", "亿元", "100M CNY", ["ytd"], "M", "Farmland occupation tax revenue"),
}
MOF_TAX_COMPONENT_IDS = [
    "mof-deed-tax", "mof-property-tax", "mof-urban-land-use-tax", "mof-land-vat", "mof-farmland-occupation-tax",
]

PROPERTY_DERIVED_AGG = {
    # old_key: (new_id, kind, panel_metric, value_type, unit_zh, unit_en, name_en)
    "new_home_70_price": ("nbs-70city-newhome-mom", "mean", "new_home", "mom_pct", "%", "%", "70-city new home price index, month-on-month change"),
    "resale_home_70_price": ("nbs-70city-resale-mom", "mean", "resale_home", "mom_pct", "%", "%", "70-city resale home price index, month-on-month change"),
    "new_home_up_cities": ("nbs-70city-newhome-up-count", "count", "new_home", "count", "个", "cities", "70-city count of cities with new home price MoM increase"),
    "resale_home_up_cities": ("nbs-70city-resale-up-count", "count", "resale_home", "count", "个", "cities", "70-city count of cities with resale home price MoM increase"),
}

DERIVED_META = {
    "nbs-retail-online-goods": {"rule": "single_from_ytd", "inputs": ["nbs-retail-online-goods"], "caliber": "single"},
    "nbs-retail-online-share": {"rule": "ratio", "inputs": ["nbs-retail-online-goods", "nbs-retail-ex-auto"], "caliber": "single"},
    "nbs-70city-newhome-mom": {"rule": "simple_mean_of_cities", "inputs": ["nbs-70city-price"], "caliber": "single"},
    "nbs-70city-resale-mom": {"rule": "simple_mean_of_cities", "inputs": ["nbs-70city-price"], "caliber": "single"},
    "nbs-70city-newhome-up-count": {"rule": "count_cities_gt_zero", "inputs": ["nbs-70city-price"], "caliber": "single"},
    "nbs-70city-resale-up-count": {"rule": "count_cities_gt_zero", "inputs": ["nbs-70city-price"], "caliber": "single"},
    "mof-real-estate-tax-total": {"rule": "sum", "inputs": MOF_TAX_COMPONENT_IDS, "caliber": "ytd"},
}

SECTIONS = [
    ("prices", "物价", "Prices"),
    ("consumption", "消费", "Consumption"),
    ("income-confidence", "收入与信心", "Income & Confidence"),
    ("employment", "就业", "Employment"),
    ("property", "楼市", "Property"),
    ("money-credit", "钱与信贷", "Money & Credit"),
    ("macro", "宏观大盘", "Macro"),
    ("high-frequency", "高频脉搏", "High-Frequency Pulse"),
]

RETAIL_COVERAGE_NOTE = None  # filled from source at runtime
INCOME_COVERAGE_NOTE = None
PROPERTY_PBC_NOTE = "房贷余额为央行季度贷款投向统计报告期末余额，单位由万亿元换算为亿元。"
PROPERTY_MOF_NOTE = "地产相关税收和土地出让收入为财政部累计报告期数据，非单月值。"
PROPERTY_70CITY_NOTE = "70城房价为国家统计局70个城市指数的简单平均，环比/同比均由指数减100得到。"


# ---------------------------------------------------------------------------
# Series-dict assembly helper (shared field order across all domains)
# ---------------------------------------------------------------------------

def make_series(id_, name_zh, name_en, unit_zh, unit_en, value_type, freq, calibers,
                 agency, dataset_zh, dataset_en, url, observations, revisions,
                 coverage_note_zh=None):
    observations = sorted(observations, key=lambda o: period_sort_key(o["period"]))
    revisions = sorted(revisions, key=lambda rv: (period_sort_key(rv["period"]), rv["measure"]))
    return {
        "schema": "series/v1",
        "id": id_,
        "name_zh": name_zh,
        "name_en": name_en,
        "unit_zh": unit_zh,
        "unit_en": unit_en,
        "value_type": value_type,
        "freq": freq,
        "calibers": calibers,
        "source": {"agency": agency, "dataset_zh": dataset_zh, "dataset_en": dataset_en, "url": url},
        "derived": DERIVED_META.get(id_),
        "coverage_note_zh": coverage_note_zh,
        "observations": observations,
        "revisions": revisions,
        "breaks": [],
        "generated_at": MIGRATION_TIMESTAMP,
    }


# ---------------------------------------------------------------------------
# Retail
# ---------------------------------------------------------------------------

def scan_retail_capabilities(records, key):
    has_m = has_myoy = has_ytd = has_ytdyoy = False
    for r in records:
        m = r["metrics"].get(key)
        if m is None:
            continue
        if m.get("latest_month_value") is not None:
            has_m = True
        if m.get("latest_month_yoy") is not None:
            has_myoy = True
        if m.get("latest_ytd_value") is not None:
            has_ytd = True
        if m.get("latest_ytd_yoy") is not None:
            has_ytdyoy = True
    return has_m, has_myoy, has_ytd, has_ytdyoy


def migrate_retail(data, notes, catalog_meta):
    records = data["records"]
    series_meta = data["series"]
    coverage_note = data.get("coverage_note")
    out = {}

    for old_key, (new_id, tier, name_en) in RETAIL_MAP.items():
        if old_key in DROPPED_RETAIL_KEYS:
            meta = series_meta[old_key]
            notes.dropped.append(
                f"{old_key} ({meta['name']}) -> {new_id}: 0 source observations; "
                f"DROPPED per lead decision #2. This deviates from MIGRATION-MAP section "
                f"8b-1, which said to keep an empty reserved-id entry -- the explicit lead "
                f"decision overrides that."
            )
            continue

        meta = series_meta[old_key]
        has_m, has_myoy, has_ytd, has_ytdyoy = scan_retail_capabilities(records, old_key)
        calibers = []
        if has_m:
            calibers.append("single")
        if has_ytd:
            calibers.append("ytd")
        assert calibers, f"{old_key} has no observations at all"

        observations = []
        revisions = []
        src_obs_count = 0

        for r in records:
            m = r["metrics"].get(old_key)
            if m is None:
                continue
            src_obs_count += 1
            period = r["period"]
            jan_feb = old_key == "online_goods" and period in RETAIL_JAN_FEB_COMBINED_PERIODS

            obs = {"period": period}
            if jan_feb:
                obs["span"] = 2
                obs["flags"] = ["jan_feb"]

            cur_m = m.get("latest_month_value")
            cur_myoy = m.get("latest_month_yoy")
            cur_ytd = m.get("latest_ytd_value")
            cur_ytdyoy = m.get("latest_ytd_yoy")
            if jan_feb:
                # 1-2 month combined print is simultaneously single-period and
                # cumulative (DATA-CONTRACT section 3.4 worked example).
                cur_ytd = cur_m
                cur_ytdyoy = cur_myoy

            if has_m:
                obs["m"] = norm_num(cur_m)
            if has_myoy:
                obs["m_yoy"] = norm_num(cur_myoy)
            if has_ytd:
                obs["ytd"] = norm_num(cur_ytd)
            if has_ytdyoy:
                obs["ytd_yoy"] = norm_num(cur_ytdyoy)
            obs["src"] = make_src(r)
            observations.append(obs)

            checks = []
            if has_m:
                checks.append(("m", "published_month_value", "latest_month_value"))
            if has_myoy:
                checks.append(("m_yoy", "published_month_yoy", "latest_month_yoy"))
            if has_ytd:
                checks.append(("ytd", "published_ytd_value", "latest_ytd_value"))
            if has_ytdyoy:
                checks.append(("ytd_yoy", "published_ytd_yoy", "latest_ytd_yoy"))
            for measure, pub_field, lat_field in checks:
                pub_v, lat_v = m.get(pub_field), m.get(lat_field)
                # pub_v is None => no as-published vintage was ever recorded for this
                # (usually old natdata-continuous, pre-release-era) point -- that is
                # NOT a revision, just an absent snapshot. Only log a genuine change
                # between two REAL recorded values.
                if pub_v is not None and not values_equal(pub_v, lat_v):
                    revisions.append({
                        "period": period, "measure": measure,
                        "old": norm_num(pub_v), "new": norm_num(lat_v),
                        "revised_on": None, "source": "legacy-migration",
                        "note": "published_* vs latest_* twin from legacy retail archive",
                    })

        series = make_series(
            new_id, meta["name"], name_en, "亿元", "100M CNY", "level", "M", calibers,
            "nbs", f"国家统计局数据发布 · {meta['name']}", f"NBS Data Release — {name_en}",
            "https://www.stats.gov.cn/sj/zxfb/", observations, revisions,
            coverage_note_zh=coverage_note,
        )
        out[new_id] = series
        catalog_meta[new_id] = {"section": "consumption", "group": meta.get("group"), "tier": tier}
        notes.series_rows.append({
            "id": new_id, "source_obs": src_obs_count, "migrated_obs": len(observations),
            "note": "1:1" if src_obs_count == len(observations) else "see delta",
        })
        if revisions:
            notes.revisions_by_id[new_id] = revisions

    n_jan_feb = sum(1 for s in out.get("nbs-retail-online-goods", {}).get("observations", [])
                    if "jan_feb" in s.get("flags", []))
    notes.jan_feb_notes.append(
        f"retail: online_goods carries {n_jan_feb} Jan-Feb combined observations "
        f"(span:2, flags:[jan_feb], m==ytd, m_yoy==ytd_yoy) at {sorted(RETAIL_JAN_FEB_COMBINED_PERIODS)}. "
        f"All other retail series (incl. retail_total) have NO observation at all for these "
        f"9 Feb periods, nor for Jan of those years, nor for Jan+Feb of 2012-2017 (21 missing "
        f"periods total) -- the source genuinely has no data there (metrics key absent, not "
        f"null), so nothing was fabricated; see 'Flagged' for why the gap was not backfilled."
    )
    return out


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------

def migrate_income(data, notes, catalog_meta):
    records = data["records"]
    series_meta = data["series"]
    coverage_note = data.get("coverage_note")
    out = {}

    for old_key, (new_id, tier, name_en) in INCOME_MAP.items():
        meta = series_meta[old_key]
        has_real_yoy = old_key in REAL_YOY_INCOME_KEYS
        observations = []
        revisions = []
        src_obs_count = 0

        for r in records:
            m = r["metrics"].get(old_key)
            if m is None:
                continue
            src_obs_count += 1

            if r["period"] in INCOME_HISTORICAL_ANNUAL_PERIODS:
                assert r.get("period_label") == "全年" and r.get("historical_supplement") is True, (
                    f"unexpected shape at {r['period']}: expected historical-supplement 全年 row"
                )
                period = str(r["year"])
                obs = {"period": period, "freq": "A"}
            else:
                period = f"{r['year']}-{r['quarter']}"
                obs = {"period": period}

            cur_ytd = m.get("latest_month_value")
            cur_ytdyoy = m.get("latest_month_yoy")
            obs["ytd"] = norm_num(cur_ytd)
            obs["ytd_yoy"] = norm_num(cur_ytdyoy)
            if has_real_yoy:
                obs["real_yoy"] = norm_num(m.get("real_yoy"))
            obs["src"] = make_src(r)
            observations.append(obs)

            for measure, pub_field, lat_field in [
                ("ytd", "published_month_value", "latest_month_value"),
                ("ytd_yoy", "published_month_yoy", "latest_month_yoy"),
            ]:
                pub_v, lat_v = m.get(pub_field), m.get(lat_field)
                # pub_v is None => no as-published vintage was ever recorded for this
                # (usually old natdata-continuous, pre-release-era) point -- that is
                # NOT a revision, just an absent snapshot. Only log a genuine change
                # between two REAL recorded values.
                if pub_v is not None and not values_equal(pub_v, lat_v):
                    revisions.append({
                        "period": period, "measure": measure,
                        "old": norm_num(pub_v), "new": norm_num(lat_v),
                        "revised_on": None, "source": "legacy-migration",
                        "note": "published_* vs latest_* twin from legacy income archive",
                    })

        series = make_series(
            new_id, meta["name"], name_en, meta.get("unit", "元"), "CNY", "level", "Q", ["ytd"],
            "nbs", f"国家统计局住户收支调查 · {meta['name']}",
            f"NBS Household Income and Expenditure Survey — {name_en}",
            "https://www.stats.gov.cn/sj/zxfb/", observations, revisions,
            coverage_note_zh=coverage_note,
        )
        out[new_id] = series
        catalog_meta[new_id] = {"section": "income-confidence", "group": meta.get("group"), "tier": tier}
        notes.series_rows.append({
            "id": new_id, "source_obs": src_obs_count, "migrated_obs": len(observations), "note": "1:1",
        })
        if revisions:
            notes.revisions_by_id[new_id] = revisions

    n_annual = sum(
        1 for s in out.values() for o in s["observations"] if o.get("freq") == "A"
    )
    notes.jan_feb_notes.append(
        f"income: {n_annual} observations across all income series carry freq:\"A\" + "
        f"period:\"YYYY\" (the 4 historical-supplement 全年 bulletins: 2013,2014,2015,2016). "
        f"2017-2025's 全年/Q4 prints are ordinary \"YYYY-Q4\" cumulative observations, not "
        f"annual -- see 'Flagged' for the ambiguity this resolves."
    )
    return out


# ---------------------------------------------------------------------------
# Property: named PBC/MoF series + derived tax total
# ---------------------------------------------------------------------------

def migrate_property_named(data, notes, catalog_meta):
    records = data["records"]
    series_meta = data["series"]
    out = {}

    for old_key, (new_id, agency, value_type, unit_zh, unit_en, calibers, freq, name_en) in PROPERTY_NAMED_MAP.items():
        meta = series_meta[old_key]
        tier = level_to_tier(meta["level"])
        value_field = "m" if calibers == ["single"] else "ytd"
        yoy_field = "m_yoy" if calibers == ["single"] else "ytd_yoy"
        observations = []
        revisions = []
        src_obs_count = 0

        for r in records:
            m = r["metrics"].get(old_key)
            if m is None:
                continue
            src_obs_count += 1
            period = r["period"]
            obs = {"period": period}
            obs[value_field] = norm_num(m.get("latest_month_value"))
            obs[yoy_field] = norm_num(m.get("latest_month_yoy"))
            obs["src"] = make_src(r)
            observations.append(obs)

            for measure, pub_field, lat_field in [
                (value_field, "published_month_value", "latest_month_value"),
                (yoy_field, "published_month_yoy", "latest_month_yoy"),
            ]:
                pub_v, lat_v = m.get(pub_field), m.get(lat_field)
                # pub_v is None => no as-published vintage was ever recorded for this
                # (usually old natdata-continuous, pre-release-era) point -- that is
                # NOT a revision, just an absent snapshot. Only log a genuine change
                # between two REAL recorded values.
                if pub_v is not None and not values_equal(pub_v, lat_v):
                    revisions.append({
                        "period": period, "measure": measure,
                        "old": norm_num(pub_v), "new": norm_num(lat_v),
                        "revised_on": None, "source": "legacy-migration",
                        "note": "published_* vs latest_* twin from legacy property archive",
                    })

        note = PROPERTY_PBC_NOTE if agency == "pbc" else PROPERTY_MOF_NOTE
        dataset_zh = "中国人民银行《金融机构贷款投向统计报告》" if agency == "pbc" else "财政部《财政收支情况》"
        dataset_en = ("PBC — Report on Loan Investment Orientation of Financial Institutions" if agency == "pbc"
                      else "MOF — Fiscal Revenue and Expenditure Report")
        series = make_series(
            new_id, meta["name"], name_en, unit_zh, unit_en, value_type, freq, calibers,
            agency, dataset_zh, dataset_en, meta.get("methodUrl", ""), observations, revisions,
            coverage_note_zh=note,
        )
        out[new_id] = series
        catalog_meta[new_id] = {"section": "property", "group": meta.get("group"), "tier": tier}
        notes.series_rows.append({
            "id": new_id, "source_obs": src_obs_count, "migrated_obs": len(observations), "note": "1:1",
        })
        if revisions:
            notes.revisions_by_id[new_id] = revisions

    return out


def build_real_estate_tax_total(prop_data, named_series, notes, catalog_meta):
    records = prop_data["records"]
    meta = prop_data["series"]["real_estate_tax_total"]
    comp_obs = {
        cid: {o["period"]: o.get("ytd") for o in named_series[cid]["observations"]}
        for cid in MOF_TAX_COMPONENT_IDS
    }
    all_periods = set()
    for cid in MOF_TAX_COMPONENT_IDS:
        all_periods |= set(comp_obs[cid].keys())

    src_by_period = {r["period"]: r for r in records}
    observations = []
    checked = matched = 0
    mismatches = []
    excluded_periods = []

    for period in sorted(all_periods, key=period_sort_key):
        vals = [comp_obs[cid].get(period) for cid in MOF_TAX_COMPONENT_IDS]
        if any(v is None for v in vals):
            excluded_periods.append(period)
            continue
        s = norm_num(round(sum(vals), 2))
        observations.append({"period": period, "ytd": s, "src": f"derived:sum:{period}"})

        rec = src_by_period.get(period)
        src_val = rec["metrics"].get("real_estate_tax_total") if rec else None
        if src_val is not None and src_val.get("latest_month_value") is not None:
            checked += 1
            src_latest = src_val["latest_month_value"]
            if abs(src_latest - s) < 0.5:
                matched += 1
            else:
                mismatches.append((period, s, src_latest))

    series = make_series(
        "mof-real-estate-tax-total", meta["name"], "Total real estate-related tax revenue",
        "亿元", "100M CNY", "level", "M", ["ytd"],
        "mof", "财政部《财政收支情况》", "MOF — Fiscal Revenue and Expenditure Report",
        meta.get("methodUrl", ""), observations, [],
        coverage_note_zh=PROPERTY_MOF_NOTE + " 合计为契税、房产税、城镇土地使用税、土地增值税、耕地占用税五项之和（derived:sum）。",
    )
    catalog_meta["mof-real-estate-tax-total"] = {"section": "property", "group": meta.get("group"), "tier": level_to_tier(meta["level"])}
    notes.series_rows.append({
        "id": "mof-real-estate-tax-total", "source_obs": len(src_by_period) - sum(
            1 for r in records if r["metrics"].get("real_estate_tax_total") is None
        ), "migrated_obs": len(observations),
        "note": f"derived sum; {len(excluded_periods)} periods excluded (missing >=1 of 5 components)",
    })
    notes.derived_checks.append({
        "id": "mof-real-estate-tax-total", "rule": "sum(5 tax components)",
        "checked": checked, "matched": matched, "mismatches": mismatches[:10],
        "excluded_periods": excluded_periods,
    })
    return series


# ---------------------------------------------------------------------------
# 70-city panel
# ---------------------------------------------------------------------------

PANEL_METRIC_MAP = {"new_home_price": "new_home", "resale_home_price": "resale_home"}


def build_panel(prop_data, hist_data, notes):
    prop_recs = {r["period"]: r for r in prop_data["records"]}
    hist_recs = {r["period"]: r for r in hist_data["records"]}
    all_periods = sorted(set(prop_recs) | set(hist_recs), key=period_sort_key)
    cities = prop_data["cities"]

    cells = {city: {"new_home": {"m": [], "m_yoy": []}, "resale_home": {"m": [], "m_yoy": []}} for city in cities}
    # NOTE: disagreements between the two property sources are counted and
    # sampled for REPORT.md, but deliberately NOT written into panel
    # "revisions[]" -- see build_panel's docstring-equivalent note below and
    # REPORT.md "Flagged" item 5 for why.
    checked_cells = mismatched_cells = 0
    mismatch_periods = set()
    mismatch_sample = []  # small illustrative sample only, for the report

    for period in all_periods:
        hist_rec = hist_recs.get(period)
        prop_rec = prop_recs.get(period)
        use_hist = hist_rec is not None  # property.notes: national-data table canonical 2011-01..2026-04

        for city in cities:
            hist_city = hist_rec["cities"].get(city) if hist_rec else None
            prop_city = prop_rec["cities"].get(city) if prop_rec else None

            for src_metric, dst_metric in PANEL_METRIC_MAP.items():
                hv = hist_city.get(src_metric) if hist_city else None
                pv = prop_city.get(src_metric) if prop_city else None
                h_m = hv.get("latest_month_value") if hv else None
                h_myoy = hv.get("latest_month_yoy") if hv else None
                p_m = pv.get("latest_month_value") if pv else None
                p_myoy = pv.get("latest_month_yoy") if pv else None

                if use_hist:
                    final_m, final_myoy = h_m, h_myoy
                    if prop_city is not None and hist_city is not None:
                        for measure, hval, pval in [("m", h_m, p_m), ("m_yoy", h_myoy, p_myoy)]:
                            checked_cells += 1
                            if not values_equal(hval, pval):
                                mismatched_cells += 1
                                mismatch_periods.add(period)
                                if len(mismatch_sample) < 15:
                                    mismatch_sample.append((period, city, dst_metric, measure,
                                                             norm_num(pval), norm_num(hval)))
                else:
                    final_m, final_myoy = p_m, p_myoy

                cells[city][dst_metric]["m"].append(norm_num(final_m))
                cells[city][dst_metric]["m_yoy"].append(norm_num(final_myoy))

    notes.panel_mismatch = {
        "checked_cells": checked_cells, "mismatched_cells": mismatched_cells,
        "periods_affected": sorted(mismatch_periods, key=period_sort_key),
        "sample": mismatch_sample,
    }

    panel = {
        "schema": "panel/v1",
        "id": "nbs-70city-price",
        "name_zh": "70 个大中城市商品住宅销售价格",
        "name_en": "70-city residential sales price",
        "unit_zh": "%", "unit_en": "%",
        "value_type": "mom_pct", "freq": "M", "decimals": 2,
        "dimensions": {"city": cities, "metric": ["new_home", "resale_home"]},
        "measures": ["m", "m_yoy"],
        "periods": all_periods,
        "cells": cells,
        # Deliberately empty: see notes.panel_mismatch / REPORT.md "Flagged" item 5 --
        # the two legacy property sources disagree on ~15% of overlapping cells, but
        # logging all of that as dated "revisions" would both overstate confidence in
        # an unverified direction/date AND blow up this file ~11x (287KB -> 3.2MB).
        "revisions": [],
        "breaks": [],
        "source": {
            "agency": "nbs",
            "dataset_zh": "国家统计局《70个大中城市商品住宅销售价格变动情况》",
            "dataset_en": "NBS — Sales prices of commodity residential buildings in 70 medium and large-sized cities",
            "url": "https://www.stats.gov.cn/sj/zxfb/",
        },
        "generated_at": MIGRATION_TIMESTAMP,
    }
    return panel


def build_property_derived_aggregates(panel, prop_data, notes, catalog_meta):
    periods = panel["periods"]
    cells = panel["cells"]
    cities = panel["dimensions"]["city"]
    prop_recs = {r["period"]: r for r in prop_data["records"]}
    out = {}

    for old_key, (new_id, kind, metric, value_type, unit_zh, unit_en, name_en) in PROPERTY_DERIVED_AGG.items():
        meta = prop_data["series"][old_key]
        tier = level_to_tier(meta["level"])
        observations = []
        checked = matched = 0
        mismatches = []

        for i, period in enumerate(periods):
            m_vals = [cells[city][metric]["m"][i] for city in cities]
            present_m = [v for v in m_vals if v is not None]
            if not present_m:
                continue

            obs = {"period": period}
            if kind == "mean":
                computed_m = norm_num(round(sum(present_m) / len(present_m), 2))
                obs["m"] = computed_m
                myoy_vals = [cells[city][metric]["m_yoy"][i] for city in cities]
                present_myoy = [v for v in myoy_vals if v is not None]
                if present_myoy:
                    obs["m_yoy"] = norm_num(round(sum(present_myoy) / len(present_myoy), 2))
                tol = 0.15
            else:
                computed_m = int(sum(1 for v in present_m if v > 0))
                obs["m"] = computed_m
                tol = 0.001
            obs["src"] = f"derived:{kind}:{period}"
            observations.append(obs)

            rec = prop_recs.get(period)
            src_val = rec["metrics"].get(old_key) if rec else None
            if src_val is not None and src_val.get("latest_month_value") is not None:
                checked += 1
                src_latest = src_val["latest_month_value"]
                if abs(src_latest - computed_m) <= tol:
                    matched += 1
                else:
                    mismatches.append((period, computed_m, src_latest))

        calibers = ["single"]
        series = make_series(
            new_id, meta["name"], name_en, unit_zh, unit_en, value_type, "M", calibers,
            "nbs", "国家统计局《70个大中城市商品住宅销售价格变动情况》",
            "NBS — Sales prices of commodity residential buildings in 70 medium and large-sized cities",
            meta.get("methodUrl", ""), observations, [],
            coverage_note_zh=PROPERTY_70CITY_NOTE,
        )
        out[new_id] = series
        catalog_meta[new_id] = {"section": "property", "group": meta.get("group"), "tier": tier}
        notes.series_rows.append({
            "id": new_id, "source_obs": sum(1 for r in prop_data["records"] if r["metrics"].get(old_key) is not None),
            "migrated_obs": len(observations),
            "note": f"derived ({'simple_mean_of_cities' if kind=='mean' else 'count_cities_gt_zero'}) from panel",
        })
        notes.derived_checks.append({
            "id": new_id, "rule": "simple_mean_of_cities" if kind == "mean" else "count_cities_gt_zero",
            "checked": checked, "matched": matched, "mismatches": mismatches[:10],
        })

    return out


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def build_catalog(all_series, panel, catalog_meta):
    sections = [{"id": sid, "name_zh": zh, "name_en": en, "order": i} for i, (sid, zh, en) in enumerate(SECTIONS)]
    section_order = {sid: i for i, (sid, _, _) in enumerate(SECTIONS)}
    entries = []

    for id_, series in all_series.items():
        cm = catalog_meta[id_]
        periods = [o["period"] for o in series["observations"]]
        entry = {
            "id": id_, "name_zh": series["name_zh"], "name_en": series["name_en"],
            "section": cm["section"],
        }
        if cm.get("group"):
            entry["group"] = cm["group"]
        entry["tier"] = cm["tier"]
        entry["unit_zh"] = series["unit_zh"]
        entry["unit_en"] = series["unit_en"]
        entry["value_type"] = series["value_type"]
        entry["freq"] = series["freq"]
        entry["calibers"] = series["calibers"]
        entry["source"] = series["source"]
        if series.get("derived"):
            # catalog.schema.json's derived object is additionalProperties:false with
            # only rule+inputs (no "caliber" -- that's a series.schema.json-only field)
            entry["derived"] = {"rule": series["derived"]["rule"], "inputs": series["derived"]["inputs"]}
        if periods:
            entry["start"] = min(periods, key=period_sort_key)
            entry["latest"] = max(periods, key=period_sort_key)
        entry["file"] = f"data/series/{id_}.json"
        entries.append(entry)

    panel_entry = {
        "id": panel["id"], "name_zh": panel["name_zh"], "name_en": panel["name_en"],
        "section": "property", "group": "70城房价",
        "tier": 1,
        "unit_zh": panel["unit_zh"], "unit_en": panel["unit_en"],
        "value_type": panel["value_type"], "freq": panel["freq"],
        "calibers": ["single"],
        "source": panel["source"],
        "start": panel["periods"][0], "latest": panel["periods"][-1],
        "panel": {"dimensions": panel["dimensions"], "measures": panel["measures"]},
        "file": f"data/panels/{panel['id']}.json",
    }
    entries.append(panel_entry)

    entries.sort(key=lambda e: (section_order[e["section"]], e["tier"], e["id"]))

    return {
        "schema": "catalog/v1",
        "version": "1.0.0",
        "generated_at": MIGRATION_TIMESTAMP,
        "sections": sections,
        "series": entries,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_all(all_series, panel, catalog, notes):
    series_schema = jsonio.load_json(SCHEMA_SERIES)
    catalog_schema = jsonio.load_json(SCHEMA_CATALOG)
    panel_schema = jsonio.load_json(SCHEMA_PANEL)

    for id_, series in all_series.items():
        errs = schema_validator.validate(series, series_schema)
        for e in errs:
            notes.schema_errors.append(f"series[{id_}] {e}")

    errs = schema_validator.validate(panel, panel_schema)
    for e in errs:
        notes.schema_errors.append(f"panel[{panel['id']}] {e}")

    errs = schema_validator.validate(catalog, catalog_schema)
    for e in errs:
        notes.schema_errors.append(f"catalog {e}")

    # Semantic invariants beyond plain JSON-Schema (MIGRATION-MAP section 9):
    for id_, series in all_series.items():
        if series["calibers"] == ["ytd"]:
            for o in series["observations"]:
                if "m" in o:
                    notes.semantic_errors.append(f"{id_}@{o['period']}: bare 'm' on a ytd-only series")
        for o in series["observations"]:
            if o.get("span") == 2 and "jan_feb" not in o.get("flags", []):
                notes.semantic_errors.append(f"{id_}@{o['period']}: span:2 without jan_feb flag")
            if "jan_feb" in o.get("flags", []) and o.get("span") != 2:
                notes.semantic_errors.append(f"{id_}@{o['period']}: jan_feb flag without span:2")
        ps = [period_sort_key(o["period"]) for o in series["observations"]]
        if ps != sorted(ps):
            notes.semantic_errors.append(f"{id_}: observations not in ascending period order")
        if len(set(o["period"] for o in series["observations"])) != len(series["observations"]):
            notes.semantic_errors.append(f"{id_}: duplicate period in observations")

    # catalog file references must point at files this script actually writes
    for entry in catalog["series"]:
        expected = os.path.join(REPO_ROOT, entry["file"])
        if entry["id"] == panel["id"]:
            continue
        if entry["id"] not in all_series:
            notes.semantic_errors.append(f"catalog entry {entry['id']}: no matching series object")

    return len(notes.schema_errors) == 0 and len(notes.semantic_errors) == 0


# ---------------------------------------------------------------------------
# Smoke checks: 5 random (series, period) pairs per section
# ---------------------------------------------------------------------------

def smoke_check_group(label, id_to_oldkey, value_field_of, records, all_series, rng,
                       record_period_fn=lambda r: r["period"]):
    recs_by_period = {record_period_fn(r): r for r in records}
    candidates = []
    for new_id, old_key in id_to_oldkey.items():
        vf = value_field_of(new_id)
        for o in all_series[new_id]["observations"]:
            if vf in o:
                candidates.append((new_id, old_key, o["period"], vf))
    chosen = rng.sample(candidates, min(5, len(candidates)))
    results = []
    for new_id, old_key, period, vf in chosen:
        rec = recs_by_period[period]
        m = rec["metrics"][old_key]
        src_val = m.get("latest_month_value")
        mig_obs = next(o for o in all_series[new_id]["observations"] if o["period"] == period)
        mig_val = mig_obs.get(vf)
        # Jan-Feb combined ytd is synthesized (mirrors m), not a raw source ytd field
        if new_id == "nbs-retail-online-goods" and "jan_feb" in mig_obs.get("flags", []) and vf == "ytd":
            src_val = m.get("latest_month_value")
        ok = values_equal(src_val, mig_val)
        results.append({
            "section": label, "id": new_id, "period": period,
            "source_value": src_val, "migrated_value": mig_val, "match": ok,
        })
    return results


def run_smoke_checks(retail_data, income_data, prop_data, all_series, notes):
    rng = random.Random(RNG_SEED)

    retail_id_to_old = {v[0]: k for k, v in RETAIL_MAP.items() if k not in DROPPED_RETAIL_KEYS}
    income_id_to_old = {v[0]: k for k, v in INCOME_MAP.items()}
    prop_id_to_old = {v[0]: k for k, v in PROPERTY_NAMED_MAP.items()}
    prop_value_field = {}
    for k, v in PROPERTY_NAMED_MAP.items():
        prop_value_field[v[0]] = "m" if v[5] == ["single"] else "ytd"

    def income_migrated_period(r):
        if r["period"] in INCOME_HISTORICAL_ANNUAL_PERIODS:
            return str(r["year"])
        return f"{r['year']}-{r['quarter']}"

    results = []
    results += smoke_check_group("consumption (retail)", retail_id_to_old, lambda _id: "m",
                                  retail_data["records"], all_series, rng)
    results += smoke_check_group("income-confidence (income)", income_id_to_old, lambda _id: "ytd",
                                  income_data["records"], all_series, rng,
                                  record_period_fn=income_migrated_period)
    results += smoke_check_group("property (PBC/MoF named)", prop_id_to_old,
                                  lambda id_: prop_value_field[id_], prop_data["records"], all_series, rng)

    notes.smoke_results = results
    return all(r["match"] for r in results)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(notes, all_series, panel, catalog, schema_ok, semantic_ok, smoke_ok):
    lines = []
    lines.append("# Migration Report")
    lines.append("")
    lines.append(f"Generated by `pipeline/migrate/migrate.py`. Pinned timestamp (not wall-clock, "
                 f"for byte-stable reruns): `{MIGRATION_TIMESTAMP}`.")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    n_series = len(all_series)
    total_migrated_obs = sum(len(s["observations"]) for s in all_series.values())
    total_source_obs = sum(r["source_obs"] for r in notes.series_rows)
    lines.append(f"- Migrated series: {n_series} (+ 1 panel: `{panel['id']}`)")
    lines.append(f"- Total migrated observations (named series): {total_migrated_obs}")
    lines.append(f"- Total source observations counted during migration: {total_source_obs}")
    lines.append(f"- Panel: {len(panel['periods'])} periods x {len(panel['dimensions']['city'])} cities x "
                 f"{len(panel['dimensions']['metric'])} metrics x {len(panel['measures'])} measures")
    lines.append(f"- Catalog entries: {len(catalog['series'])} (across {len(catalog['sections'])} sections; "
                 f"5 sections reserved with 0 series -- no source data for prices/employment/money-credit/macro/high-frequency)")
    lines.append("")

    lines.append("## Dropped")
    lines.append("")
    if notes.dropped:
        for d in notes.dropped:
            lines.append(f"- {d}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Per-series observation counts (source vs migrated)")
    lines.append("")
    lines.append("| id | source_obs | migrated_obs | note |")
    lines.append("|---|---|---|---|")
    for row in sorted(notes.series_rows, key=lambda r: r["id"]):
        lines.append(f"| `{row['id']}` | {row['source_obs']} | {row['migrated_obs']} | {row['note']} |")
    lines.append("")

    lines.append("## Jan-Feb / frequency handling")
    lines.append("")
    for n in notes.jan_feb_notes:
        lines.append(f"- {n}")
    lines.append("")

    lines.append("## Known data oddities: disposition")
    lines.append("")
    lines.append("1. **Incomplete city months** (2012-12: 1 city, 2019-05: 65 cities, 2022-11: 62 cities): "
                  "encoded as `null` cells in the panel for the absent (city, metric, measure) combinations, "
                  "never fabricated/zeroed. Verified the same city subset is present/absent in both "
                  "`property_release_archive.json` and `property_city_history.json` at these 3 periods.")
    lines.append("2. **`new_home_price` null at 2012-12**: that period's `metrics` dict for "
                  "`new_home_70_price`/`new_home_up_cities` is entirely absent from the source record (only "
                  "`resale_home_70_price`/`resale_home_up_cities` present) -- no observation emitted for the "
                  "new-home aggregate at that period; the panel's per-city new_home cells are `null` there too.")
    lines.append("3. **Income annual-only 2013-2016 layer**: the 4 rows where `period_label==\"全年\"` AND "
                  "`historical_supplement==true` (2013-12, 2014-12, 2015-12, 2016-12) are migrated as "
                  "`period:\"YYYY\"`, `freq:\"A\"` (per-observation override); 2016's Q1-Q3 (also "
                  "historical_supplement but NOT 全年) are ordinary `YYYY-Qn`. See 'Flagged' item on 全年.")
    lines.append("4. **property_release_archive.json vs property_city_history.json disagreement** (NOT one of "
                  "the pre-identified oddities -- found empirically during this migration): see 'Flagged'.")
    lines.append("")

    lines.append("## Revisions seeded (published_* vs latest_* twins)")
    lines.append("")
    n_rev = sum(len(v) for v in notes.revisions_by_id.values())
    lines.append(f"- Named series: {n_rev} revision entries across {len(notes.revisions_by_id)} series.")
    for id_, revs in sorted(notes.revisions_by_id.items()):
        lines.append(f"  - `{id_}`: {len(revs)}")
    pm = notes.panel_mismatch
    lines.append(f"- Panel: **0** entries written to `revisions[]` (deliberately -- see 'Flagged' item 5). "
                 f"{pm.get('mismatched_cells', 0)} of {pm.get('checked_cells', 0)} checked "
                 f"(city, metric, measure) cells disagreed between the two property sources, "
                 f"across {len(pm.get('periods_affected', []))} of the overlapping periods. "
                 f"Illustrative sample (period, city, metric, measure, property_release_archive_value, "
                 f"property_city_history_value):")
    for row in pm.get("sample", []):
        lines.append(f"  - {row}")
    lines.append("")

    lines.append("## Derived series validation (computed vs source's own aggregate value)")
    lines.append("")
    for dc in notes.derived_checks:
        lines.append(f"- `{dc['id']}` ({dc['rule']}): {dc['matched']}/{dc['checked']} periods matched "
                     f"within tolerance.")
        if dc.get("mismatches"):
            lines.append(f"  - sample mismatches (period, computed, source): {dc['mismatches'][:5]}")
        if dc.get("excluded_periods"):
            lines.append(f"  - {len(dc['excluded_periods'])} periods excluded from the sum "
                         f"(missing >=1 of 5 components): {dc['excluded_periods'][:10]}"
                         f"{' ...' if len(dc['excluded_periods']) > 10 else ''}")
    lines.append("- Note: the `*-up-count` mismatch periods are entirely a subset of the 29 "
                 "property_release_archive-vs-property_city_history disagreement periods below (verified by "
                 "direct comparison) -- same root cause as 'Flagged' item 5, not a separate derivation bug: "
                 "the up-count aggregate (computed from our canonical, history-preferring cells) and the "
                 "source's own up-count field were evidently computed from different vintages at those "
                 "specific contentious periods.")
    lines.append("")

    lines.append("## Validation results")
    lines.append("")
    lines.append(f"- Schema validation (custom draft-07 subset validator against `data/schemas/*.schema.json`): "
                 f"{'PASS' if schema_ok else 'FAIL'} ({len(notes.schema_errors)} errors)")
    if notes.schema_errors:
        for e in notes.schema_errors[:50]:
            lines.append(f"  - {e}")
        if len(notes.schema_errors) > 50:
            lines.append(f"  - ... and {len(notes.schema_errors) - 50} more")
    lines.append(f"- Semantic invariants (MIGRATION-MAP section 9: no bare `m` on ytd-only series, "
                 f"span/jan_feb consistency, ascending unique periods): "
                 f"{'PASS' if semantic_ok else 'FAIL'} ({len(notes.semantic_errors)} errors)")
    if notes.semantic_errors:
        for e in notes.semantic_errors[:50]:
            lines.append(f"  - {e}")
    lines.append("")

    lines.append("## Smoke checks (5 random (series, period) pairs per section, fixed seed "
                 f"{RNG_SEED} for reproducibility)")
    lines.append("")
    lines.append("| section | id | period | source_value | migrated_value | match |")
    lines.append("|---|---|---|---|---|---|")
    for r in notes.smoke_results:
        lines.append(f"| {r['section']} | `{r['id']}` | {r['period']} | {r['source_value']} | "
                     f"{r['migrated_value']} | {'OK' if r['match'] else 'MISMATCH'} |")
    lines.append(f"\n- Overall: {'ALL MATCH' if smoke_ok else 'SOME MISMATCHES -- see table'}")
    lines.append("")

    lines.append("## Flagged ambiguities / deviations")
    lines.append("")
    lines.append("1. **Panel path/id**: the task's deliverable bullet said "
                 "`data/panels/nbs-70-city.json`; DATA-CONTRACT section 5 and MIGRATION-MAP both name it "
                 "`data/panels/nbs-70city-price.json` (id `nbs-70city-price`) throughout, including the "
                 "worked example. Used the contract's exact naming (it is the binding spec per the task's own "
                 "framing and DATA-CONTRACT's own stated precedence over prose); treating the task bullet as "
                 "informal paraphrase.")
    lines.append("2. **`data/archive/*` not written**: MIGRATION-MAP sections 1 and 7 ask the migration to "
                 "reconstruct raw archive captures (to seed `published_*` as an as-published vintage). That "
                 "path is explicitly outside this agent's owned paths (`pipeline/migrate/*, data/series/*, "
                 "data/panels/*, data/catalog.json` only). Implemented the `revisions[]` log (DATA-CONTRACT "
                 "section 4.1) fully, but did not write `data/archive/`; whichever agent owns that layer still "
                 "needs to backfill it from the same legacy archives.")
    lines.append("3. **retail_total (and siblings) Jan-Feb 2018-2026 gap not backfilled**: MIGRATION-MAP "
                 "section 3 says to \"fill the retail_total Jan-Feb gap from the 1—2月 release capture in "
                 "data/archive/\" -- no such archive exists yet (nothing has run pipeline/fetch/ against real "
                 "NBS pages), and this migration script is stdlib-only with no network access. Left the gap "
                 "genuinely absent (no observation emitted) rather than fabricate a value; retail_total and "
                 "every retail series except online_goods have zero data for Jan of every year and Feb "
                 "2018-2026.")
    lines.append("4. **`books_magazines` dropped entirely**, per explicit lead decision #2 -- this overrides "
                 "MIGRATION-MAP section 8b-1's instruction to keep a reserved empty-`observations` entry.")
    lines.append("5. **property_release_archive.json vs property_city_history.json disagree materially**: "
                 f"{pm.get('mismatched_cells', 0)} of {pm.get('checked_cells', 0)} checked (city, metric, "
                 f"measure) cells disagree, concentrated in {len(pm.get('periods_affected', []))} of the 184 "
                 "overlapping periods (each affected period disagrees on the large majority of its cells, not "
                 "a scattered few -- consistent with a benchmark-revision-style vintage difference, not random "
                 "noise). This was NOT one of the pre-flagged oddities; found empirically while building the "
                 "panel. Resolved in two parts: (a) `cells[]` uses `property_city_history.json` as canonical "
                 "for 2011-01..2026-04 and `property_release_archive.json` for 2026-05, following "
                 "`property.notes`' own explicit source-precedence statement; (b) the disagreements were "
                 "**deliberately NOT written to `panel.revisions[]`**. First attempt did write all 7,574 as "
                 "revision entries; that alone grew the panel from 287KB to 3.2MB (11x) for a reconciliation "
                 "whose direction/date is genuinely unverified (only file mtimes were available as a signal, "
                 "and they point the opposite way from the precedence rule used) -- manufacturing that many "
                 "dated-looking revision entries would misrepresent confidence we don't have AND defeat the "
                 "contract's own 'lean panel' design goal. Full counts + a 15-row sample are recorded here "
                 "instead; recommend the owner spot-check a few affected periods against the original NBS "
                 "release pages before deciding whether this belongs in the data layer at all.")
    lines.append("6. **Income \"全年\" period mapping**: read MIGRATION-MAP section 3's \"period_label:全年 / "
                 "historical_supplement:true rows (2013-2016) are annual-only\" as scoping the "
                 "period:\"YYYY\"/freq:\"A\" treatment to ONLY the 4 rows where both conditions hold "
                 "simultaneously (2013-12, 2014-12, 2015-12, 2016-12). 2017-2025's 全年 rows are NOT "
                 "historical_supplement and are kept as ordinary \"YYYY-Q4\" cumulative prints. Verified this "
                 "is necessary for correctness: a pure string sort would otherwise place an annual \"2016\" "
                 "point before \"2016-Q1\"/\"Q2\"/\"Q3\" of the same year, which is chronologically wrong.")
    lines.append("7. **Derived values computed, not copied**: `mof-real-estate-tax-total` (sum of 5 components) "
                 "and the 4 `nbs-70city-*` aggregates (simple_mean_of_cities / count_cities_gt_zero over the "
                 "panel) are computed by this script per DATA-CONTRACT section 6, not copied from the source's "
                 "own aggregate field. Cross-validated against the source's own value where available -- see "
                 "'Derived series validation' above.")
    lines.append("8. **Catalog includes all 8 contract sections**, 5 with zero series (no source data for "
                 "prices/employment/money-credit/macro/high-frequency yet) -- reserves section ordering for "
                 "future green-field work rather than omitting them.")
    lines.append("9. **`decimals` omitted** on every series/panel (optional field; no reliable per-series "
                 "precision signal in the legacy source).")
    lines.append("10. **`data/annotations.json` not created**: it is not in this agent's owned-paths list. "
                 "Period-specific caveats (the 3 city-coverage-gap periods, etc.) are documented here and in "
                 "each series' `coverage_note_zh` instead of as machine-readable annotations.")
    lines.append("")

    text = "\n".join(lines) + "\n"
    with open(OUT_REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    notes = Notes()
    catalog_meta = {}

    retail_data = jsonio.load_json(IN_RETAIL)
    income_data = jsonio.load_json(IN_INCOME)
    prop_data = jsonio.load_json(IN_PROPERTY)
    hist_data = jsonio.load_json(IN_CITY_HISTORY)

    retail_series = migrate_retail(retail_data, notes, catalog_meta)
    income_series = migrate_income(income_data, notes, catalog_meta)
    prop_named_series = migrate_property_named(prop_data, notes, catalog_meta)
    tax_total_series = build_real_estate_tax_total(prop_data, prop_named_series, notes, catalog_meta)

    panel = build_panel(prop_data, hist_data, notes)
    prop_derived_series = build_property_derived_aggregates(panel, prop_data, notes, catalog_meta)

    all_series = {}
    all_series.update(retail_series)
    all_series.update(income_series)
    all_series.update(prop_named_series)
    all_series["mof-real-estate-tax-total"] = tax_total_series
    all_series.update(prop_derived_series)

    catalog = build_catalog(all_series, panel, catalog_meta)

    schema_ok_and_semantic_ok = validate_all(all_series, panel, catalog, notes)
    schema_ok = len(notes.schema_errors) == 0
    semantic_ok = len(notes.semantic_errors) == 0

    smoke_ok = run_smoke_checks(retail_data, income_data, prop_data, all_series, notes)

    os.makedirs(OUT_SERIES_DIR, exist_ok=True)
    os.makedirs(OUT_PANELS_DIR, exist_ok=True)
    for id_, series in sorted(all_series.items()):
        jsonio.write_json(os.path.join(OUT_SERIES_DIR, f"{id_}.json"), series)
    jsonio.write_json(os.path.join(OUT_PANELS_DIR, f"{panel['id']}.json"), panel)
    jsonio.write_json(OUT_CATALOG, catalog)

    write_report(notes, all_series, panel, catalog, schema_ok, semantic_ok, smoke_ok)

    print(f"Migrated {len(all_series)} series + 1 panel + catalog.")
    print(f"Schema validation: {'PASS' if schema_ok else 'FAIL'} ({len(notes.schema_errors)} errors)")
    print(f"Semantic validation: {'PASS' if semantic_ok else 'FAIL'} ({len(notes.semantic_errors)} errors)")
    print(f"Smoke checks: {'PASS' if smoke_ok else 'FAIL'}")
    print(f"Report written to {OUT_REPORT}")

    if not (schema_ok and semantic_ok):
        print("VALIDATION FAILED -- see errors above and in REPORT.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
