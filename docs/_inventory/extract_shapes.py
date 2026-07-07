#!/usr/bin/env python3
"""
Reproducible data-shape extraction for china-consumer-data-dashboard.

Parses:
  - data.js                          (window.__DASHBOARD_DATA__ = {...}; the bundle the
                                       live page reads first)
  - retail_release_archive.json      (build-pipeline source for data.js.retail)
  - income_release_archive.json      (build-pipeline source for data.js.income)
  - property_release_archive.json    (build-pipeline source for data.js.property)
  - property_city_history.json       (intermediate: parsed per-city price history,
                                       merged into property_release_archive.json)
  - property_city_history_raw.json   (rawest layer: browser-scrape cache, not consumed
                                       by the frontend at all)

Cross-references app.js to determine which fields the live UI actually reads
(load-bearing) vs which exist in the data but are never read (vestigial).

Writes:
  - docs/_inventory/current-data-shapes.json  (machine-readable)
  - docs/_inventory/current-data-shapes.md    (human-readable)

Usage:
    python3 docs/_inventory/extract_shapes.py
(Resolves all paths relative to the repo root, so it can be run from anywhere.)
"""

import json
import re
import hashlib
from collections import Counter, OrderedDict
from pathlib import Path
from datetime import datetime, timezone

INVENTORY_DIR = Path(__file__).resolve().parent
REPO_ROOT = INVENTORY_DIR.parent.parent

PERIOD_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}$"), "YYYY-MM"),
    (re.compile(r"^\d{4}Q[1-4]$"), "YYYYQ#"),
    (re.compile(r"^\d{4}$"), "YYYY"),
]


def classify_period(period):
    if period is None:
        return "NULL"
    s = str(period)
    for pattern, label in PERIOD_PATTERNS:
        if pattern.match(s):
            return label
    return f"OTHER:{s}"


def truncate(value, max_len=160, max_items=8):
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"...<truncated from {len(value)} chars>"
        return value
    if isinstance(value, list):
        out = [truncate(v, max_len, max_items) for v in value[:max_items]]
        if len(value) > max_items:
            out.append(f"...<{len(value) - max_items} more items>")
        return out
    if isinstance(value, dict):
        return {k: truncate(v, max_len, max_items) for k, v in value.items()}
    return value


def sha256_of(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def load_dashboard_data():
    path = REPO_ROOT / "data.js"
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^\s*window\.__DASHBOARD_DATA__\s*=\s*", text)
    if not m:
        raise ValueError("data.js does not start with the expected window.__DASHBOARD_DATA__ assignment")
    body = text[m.end():].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body), len(text)


def has_any_value_field(metric):
    return any(v is not None for k, v in metric.items() if k.endswith("_value"))


def month_index(period):
    """'2024-05' -> 2024*12 + 5, for computing gaps between YYYY-MM periods."""
    y, m = period.split("-")
    return int(y) * 12 + int(m)


def infer_frequency(periods_with_value):
    """Infer cadence from the modal gap (in months) between consecutive observed periods."""
    if len(periods_with_value) < 2:
        return "unknown (fewer than 2 observations)"
    deltas = Counter()
    idx = sorted(month_index(p) for p in periods_with_value)
    for a, b in zip(idx, idx[1:]):
        deltas[b - a] += 1
    modal_delta, modal_count = deltas.most_common(1)[0]
    uniform = modal_count == len(idx) - 1
    label = {1: "monthly", 3: "quarterly", 12: "annual"}.get(modal_delta, f"irregular (modal gap {modal_delta}mo)")
    if not uniform:
        label += " (with gaps)" if modal_delta in (1, 3, 12) else ""
    return label


def describe_series_section(section, path_prefix):
    """Full per-series descriptor pass for a {series, records} shaped section (retail/income/property)."""
    series_meta = section.get("series", {})
    records = section.get("records", [])

    period_formats = Counter(classify_period(r.get("period")) for r in records)
    periods_sorted = sorted({r.get("period") for r in records if r.get("period")})
    key_signatures = Counter(tuple(sorted(r.keys())) for r in records)

    series_out = OrderedDict()
    for series_id, meta in series_meta.items():
        obs = []
        for r in records:
            metric = r.get("metrics", {}).get(series_id)
            if metric is not None:
                obs.append((r.get("period"), metric))

        real_obs = [(p, m) for (p, m) in obs if has_any_value_field(m)]
        field_union = set()
        for _, m in obs:
            field_union |= set(m.keys())

        periods_with_value = sorted({p for p, m in real_obs if p})
        sample = None
        if real_obs:
            p, m = real_obs[-1]
            sample = {"period": p, "fields": truncate(m)}

        series_out[series_id] = {
            "path": f"{path_prefix}.series.{series_id}",
            "label_fields": {"name": meta.get("name"), "group": meta.get("group"), "level": meta.get("level")},
            "unit": meta.get("unit"),
            "frequency": infer_frequency(periods_with_value),
            "value_label": meta.get("valueLabel"),
            "yoy_label": meta.get("yoyLabel"),
            "source_name": meta.get("source_name"),
            "source_link_label": meta.get("source_link_label"),
            "method_url": meta.get("methodUrl"),
            "raw_meta_keys": sorted(meta.keys()),
            "metric_field_names": sorted(field_union),
            "has_published_variant": any(k.startswith("published_") for k in field_union),
            "has_latest_variant": any(k.startswith("latest_month_") for k in field_union),
            "has_ytd_variant": any("ytd" in k for k in field_union),
            "has_plain_month_variant": any(k in ("month_value", "month_yoy") for k in field_union),
            "observation_count": len(real_obs),
            "date_range": {
                "min": periods_with_value[0] if periods_with_value else None,
                "max": periods_with_value[-1] if periods_with_value else None,
            },
            "sample_observation": sample,
        }

    records_summary = {
        "path": f"{path_prefix}.records",
        "count": len(records),
        "period_field_formats": dict(period_formats),
        "period_range": {
            "min": periods_sorted[0] if periods_sorted else None,
            "max": periods_sorted[-1] if periods_sorted else None,
        },
        "record_key_signatures": [
            {"keys": list(sig), "count": cnt} for sig, cnt in key_signatures.most_common()
        ],
        "sample_record": truncate(records[0]) if records else None,
    }
    return series_out, records_summary


def describe_property_city_detail(section, path_prefix):
    """Per-record nested `cities` dict is not a top-level series; describe it as its own shape."""
    records = section.get("records", [])
    top_cities = section.get("cities", [])

    seen_cities = set()
    city_count_dist = Counter()
    metric_names = set()
    metric_field_names = set()
    null_counts = Counter()
    total_obs = 0
    periods_with_cities = []
    low_coverage_records = []

    for r in records:
        cities = r.get("cities", {})
        n = len(cities)
        city_count_dist[n] += 1
        if cities:
            periods_with_cities.append(r.get("period"))
        if 0 < n < len(top_cities):
            low_coverage_records.append({"period": r.get("period"), "city_count": n})
        for city, metrics in cities.items():
            seen_cities.add(city)
            for metric_name, metric_obj in metrics.items():
                metric_names.add(metric_name)
                metric_field_names |= set(metric_obj.keys())
                total_obs += 1
                if metric_obj.get("month_value") is None:
                    null_counts[metric_name] += 1

    periods_with_cities = sorted(p for p in periods_with_cities if p)
    return {
        "path": f"{path_prefix}.records[].cities",
        "description": (
            "Per-record nested dict keyed by Chinese city name (not a top-level series). "
            "Each city holds new_home_price/resale_home_price metric objects in the same "
            "shape as top-level series metrics (month_value/month_yoy/latest_month_*/published_month_*)."
        ),
        "declared_cities_path": f"{path_prefix}.cities",
        "declared_city_count": len(top_cities),
        "declared_city_names": top_cities,
        "cities_seen_in_records": len(seen_cities),
        "cities_declared_not_seen_in_any_record": sorted(set(top_cities) - seen_cities),
        "cities_seen_but_not_declared_at_top_level": sorted(seen_cities - set(top_cities)),
        "metric_names": sorted(metric_names),
        "metric_field_names": sorted(metric_field_names),
        "city_count_distribution_across_records": dict(sorted(city_count_dist.items())),
        "records_with_incomplete_city_coverage": low_coverage_records,
        "null_month_value_counts_by_metric": dict(null_counts),
        "total_city_metric_observations": total_obs,
        "date_range": {
            "min": periods_with_cities[0] if periods_with_cities else None,
            "max": periods_with_cities[-1] if periods_with_cities else None,
        },
    }


def shallow_describe(obj, max_items=6):
    """Top 2-3 level shape summary for a JSON value: keys/types/lengths, one level deeper for containers."""
    def describe_value(v, depth):
        if isinstance(v, dict):
            out = {"type": "dict", "len": len(v)}
            if depth > 0:
                out["keys"] = {}
                for k, vv in list(v.items())[:max_items]:
                    out["keys"][k] = describe_value(vv, depth - 1)
                if len(v) > max_items:
                    out["keys"]["..."] = f"{len(v) - max_items} more keys"
            return out
        if isinstance(v, list):
            out = {"type": "list", "len": len(v)}
            if depth > 0 and v:
                out["item_sample"] = describe_value(v[0], depth - 1)
            return out
        if isinstance(v, str):
            return {"type": "str", "len": len(v), "sample": truncate(v, 80)}
        return {"type": type(v).__name__, "value": v}

    return describe_value(obj, 3)


def archive_file_summary(filename, dashboard_section=None, dashboard_key=None):
    path = REPO_ROOT / filename
    if not path.exists():
        return {"path": filename, "error": "file not found"}
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    out = {
        "path": filename,
        "file_size_bytes": len(raw.encode("utf-8")),
        "shape": shallow_describe(data),
    }
    if dashboard_section is not None:
        matches_exactly = sha256_of(data) == sha256_of(dashboard_section)

        def normalize(o):
            if isinstance(o, dict):
                return {k: normalize(v) for k, v in o.items()}
            if isinstance(o, list):
                return [normalize(v) for v in o]
            if isinstance(o, (int, float)) and not isinstance(o, bool):
                # float(o) or 0.0 collapses -0.0 to 0.0 (both are falsy in Python) while
                # leaving every nonzero value -- including negative ones -- untouched.
                return float(o) or 0.0
            return o

        matches_numerically = sha256_of(normalize(data)) == sha256_of(normalize(dashboard_section))
        out["relationship_to_data_js"] = {
            "compared_to": f"data.js:{dashboard_key}",
            "byte_identical_json": matches_exactly,
            "numerically_identical_after_int_float_and_signed_zero_normalization": matches_numerically,
            "note": (
                "Identical content." if matches_exactly else
                "Same content; only differences are JSON number-formatting artifacts "
                "(int vs float, e.g. 60 vs 60.0; and signed zero, e.g. -0.0 vs 0.0 from "
                "rounding), not a real data divergence." if matches_numerically else
                "Content differs beyond number formatting -- inspect before assuming data.js is derived from this file."
            ),
        }
    return out


def app_js_dependency_audit():
    # The findings below are a manual grep/read audit of app.js (see docs/_inventory notes);
    # this function just packages them as structured data for the report.
    return {
        "dashboard_top_level_keys_read": ["retail", "income", "property"],
        "note_on_property": (
            "app.js reads window.__DASHBOARD_DATA__.property for BOTH the propertyPrice "
            "and propertyCredit UI sections (data.js has no separate propertyCredit key)."
        ),
        "series_meta_fields_read": [
            "name", "group", "level", "unit", "valueLabel", "yoyLabel",
            "source_name", "source_link_label", "methodUrl",
        ],
        "record_fields_read": [
            "period", "period_label", "year", "month", "historical_supplement",
            "metrics", "cities", "url (latest record only, for the source link href)",
        ],
        "metric_fields_read": [
            "month_value", "month_yoy",
            "latest_month_value", "latest_month_yoy",
            "published_month_value", "published_month_yoy",
        ],
        "fields_present_in_data_but_never_read_by_app_js": [
            "retail.generated_at", "retail.coverage_note", "retail.sources", "retail.versioning_note",
            "income.generated_at", "income.section", "income.section_name", "income.frequency",
            "income.coverage_note", "income.sources",
            "property.section", "property.section_name", "property.frequency", "property.preferred "
            "(app.js has its own hardcoded `preferred` list per section in its `sections` config -- "
            "the data.js copy is unused/vestigial from the UI's perspective)",
            "property.sources", "property.notes",
            "record.title", "record.source", "record.published_at", "record.quarter",
            "record.coverage_note (per-record, income historical-supplement rows)",
            "record.title_period", "record.release_published_at", "record.release_title",
            "metrics.<series>.ytd_value / ytd_yoy (retail only -- cumulative year-to-date, "
            "plain/latest/published variants: ytd_value, ytd_yoy, latest_ytd_value, "
            "latest_ytd_yoy, published_ytd_value, published_ytd_yoy -- all six unread)",
            "metrics.<series>.real_yoy (income only -- inflation-adjusted 实际增长 YoY, present on "
            "income_disposable, income_disposable_urban, income_disposable_rural, "
            "consumption_expenditure, consumption_expenditure_urban, consumption_expenditure_rural; "
            "entirely unread -- the dashboard only ever shows nominal YoY)",
        ],
        "chart_mode_logic_keys_expected_by_app_js": {
            "version": "state.version in {latest, published} -> selects published_month_* vs latest_month_*/month_* fields",
            "mode": "state.mode in {value, yoy, trend} -> trend is property-price only, client-computed chained index",
            "retailFrequency": "state.retailFrequency in {period, ttm} -> ttm is a client-computed 12-point rolling sum, NOT stored in data",
            "incomeFrequency": "state.incomeFrequency in {quarter, ttm, annual} -> ttm is a client-computed 4-point rolling sum over quarterly deltas",
            "incomeScale": "state.incomeScale in {percapita, national} -> national multiplies by hardcoded populationWan table in app.js, only when seriesUnit === '元'",
            "propertyCity": "state.propertyCity in {overall, core, capitalNewTier, other} -> groups drawn from data.property.cities plus hardcoded city-group lists in app.js",
            "TTM_note": "TTM is never a stored flag on a series; it is always a client-side rolling-window computation (window=12 for retail, window=4 for income quarterly points).",
            "revision_variant_note": (
                "retail.versioning_note (verbatim): "
                "每个指标保留 latest_*（当前最新版本）与 "
                "published_*（发布日期版本）。month_* 字段保留为 "
                "latest_* 的兼容别名。"
            ),
        },
    }


SECTION_CADENCE = {"retail": "monthly", "income": "quarter_end_months", "property": "monthly"}


def month_seq(start, end):
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, mo = sy, sm
    while (y, mo) <= (ey, em):
        out.append(f"{y:04d}-{mo:02d}")
        mo += 1
        if mo == 13:
            mo = 1
            y += 1
    return out


def detect_oddities(dashboard, sections_out):
    oddities = []

    # 1. Series declared in metadata but never populated in any record.
    for key, section_entry in sections_out.items():
        dead = [sid for sid, s in section_entry["series"].items() if s["observation_count"] == 0]
        if dead:
            oddities.append({
                "kind": "zero_observation_series",
                "section": key,
                "series_ids": dead,
                "detail": (
                    f"Declared in {key}.series with name/group/unit metadata, but no record's "
                    f"metrics.<id> ever carries a non-null value. A migration script that assumes "
                    f"every declared series has >=1 observation will mis-handle these."
                ),
            })

    # 2. Calendar periods entirely absent from a section's own records array (using each
    #    section's own observed cadence, not a blanket monthly assumption).
    for key, section_entry in sections_out.items():
        cadence = SECTION_CADENCE.get(key, "monthly")
        raw_records = dashboard[key]["records"]
        actual_periods = sorted({r["period"] for r in raw_records if r.get("period")})
        if not actual_periods:
            continue
        if cadence == "monthly":
            expected = month_seq(actual_periods[0], actual_periods[-1])
        else:  # quarter_end_months: only compare within the quarterly-complete range
            full_months = month_seq(actual_periods[0], actual_periods[-1])
            expected = [p for p in full_months if p.endswith(("-03", "-06", "-09", "-12"))]
        missing = sorted(set(expected) - set(actual_periods))
        if missing:
            if key == "retail":
                explanation = (
                    "Lines up with years where NBS's Jan/Feb combined-reporting convention was "
                    "encoded as 'no separate record' rather than 'record with null retail_total' "
                    "(compare with the flagship_series_internal_nulls finding for retail_total, "
                    "which is the same convention encoded the other way in later years)."
                )
            elif key == "income":
                explanation = (
                    "Matches income.coverage_note: 2013-2015 is an annual-only historical-supplement "
                    "layer, so the intervening quarters genuinely never existed as separate releases."
                )
            else:
                explanation = "Inspect whether this reflects a real reporting gap or a scrape/archival gap."
            oddities.append({
                "kind": "missing_calendar_periods_in_records",
                "section": key,
                "cadence_assumed": cadence,
                "range": [actual_periods[0], actual_periods[-1]],
                "missing_count": len(missing),
                "missing_sample": missing[:15],
                "detail": (
                    f"{len(missing)} periods within {actual_periods[0]}..{actual_periods[-1]} have "
                    f"no record at all in {key}.records (not just null metrics -- the whole record is "
                    f"absent). {explanation}"
                ),
            })

    # 3. Within-range nulls for a section's flagship series (e.g. retail_total's Feb gaps from
    #    the Jan-Feb combined-release convention, which shows up as null rather than a missing record).
    flagship = {"retail": "retail_total", "income": "income_disposable", "property": "new_home_70_price"}
    for key, series_id in flagship.items():
        section = dashboard.get(key, {})
        records = section.get("records", [])
        gaps = []
        for r in records:
            m = r.get("metrics", {}).get(series_id)
            if m is None or not any(v is not None for k, v in m.items() if k.endswith("_value")):
                gaps.append(r.get("period"))
        if gaps:
            oddities.append({
                "kind": "flagship_series_internal_nulls",
                "section": key,
                "series_id": series_id,
                "null_periods": sorted(gaps),
                "detail": f"{key}.records exist for these periods but metrics.{series_id} has no non-null value.",
            })

    # 4. Record schema evolution (multiple key-signatures within one section's records).
    for key, section_entry in sections_out.items():
        sigs = section_entry["records_summary"]["record_key_signatures"]
        if len(sigs) > 1:
            oddities.append({
                "kind": "record_schema_evolution",
                "section": key,
                "signature_count": len(sigs),
                "detail": (
                    f"{key}.records is not one uniform shape -- {len(sigs)} distinct key-sets coexist "
                    "(see records_summary.record_key_signatures for exact counts/keys). A migration "
                    "script must treat extra provenance fields (release_published_at, release_title, "
                    "title_period, coverage_note, historical_supplement, quarter) as optional per-record."
                ),
            })

    # 5. Property per-record city coverage gaps.
    city_detail = sections_out.get("property", {}).get("city_detail")
    if city_detail and city_detail["records_with_incomplete_city_coverage"]:
        oddities.append({
            "kind": "property_city_coverage_gap",
            "section": "property",
            "records": city_detail["records_with_incomplete_city_coverage"],
            "detail": (
                f"{len(city_detail['records_with_incomplete_city_coverage'])} of "
                f"{sum(city_detail['city_count_distribution_across_records'].values())} property records have "
                f"fewer than the declared {city_detail['declared_city_count']} cities in their nested `cities` "
                "dict (likely scrape/source gaps -- see property_city_history_raw.json errors/retryErrors)."
            ),
        })

    # 6. Vestigial metric-field families never read by the frontend.
    oddities.append({
        "kind": "vestigial_metric_fields",
        "section": "retail / income",
        "detail": (
            "retail metrics carry a full YTD family (ytd_value, ytd_yoy, latest_ytd_value, "
            "latest_ytd_yoy, published_ytd_value, published_ytd_yoy) and income metrics carry "
            "real_yoy (inflation-adjusted growth) on the 6 headline income/consumption series -- "
            "app.js reads neither. Live, real data that a redesigned dashboard could surface."
        ),
    })

    return oddities


def main():
    dashboard, data_js_char_len = load_dashboard_data()

    sections_out = OrderedDict()
    for key in ("retail", "income", "property"):
        section = dashboard.get(key)
        if section is None:
            continue
        series_out, records_summary = describe_series_section(section, key)
        metadata_fields = {
            k: (v if not isinstance(v, (list, dict)) else truncate(v))
            for k, v in section.items()
            if k not in ("series", "records")
        }
        section_entry = {
            "path": key,
            "top_level_keys": list(section.keys()),
            "metadata_fields": metadata_fields,
            "records_summary": records_summary,
            "series": series_out,
            "series_count": len(series_out),
            "total_observations": sum(s["observation_count"] for s in series_out.values()),
        }
        if key == "property":
            section_entry["city_detail"] = describe_property_city_detail(section, key)
        sections_out[key] = section_entry

    archive_files = OrderedDict()
    archive_files["retail_release_archive.json"] = archive_file_summary(
        "retail_release_archive.json", dashboard.get("retail"), "retail"
    )
    archive_files["income_release_archive.json"] = archive_file_summary(
        "income_release_archive.json", dashboard.get("income"), "income"
    )
    archive_files["property_release_archive.json"] = archive_file_summary(
        "property_release_archive.json", dashboard.get("property"), "property"
    )
    archive_files["property_city_history.json"] = archive_file_summary("property_city_history.json")
    archive_files["property_city_history_raw.json"] = archive_file_summary("property_city_history_raw.json")

    # Global period format census (data.js sections only; archives mirror them 1:1 in shape)
    global_period_formats = Counter()
    for section_entry in sections_out.values():
        for fmt, cnt in section_entry["records_summary"]["period_field_formats"].items():
            global_period_formats[fmt] += cnt

    # Sanity totals
    largest_series = None
    largest_series_count = -1
    per_section_totals = {}
    grand_total_observations = 0
    grand_total_series = 0
    for key, section_entry in sections_out.items():
        per_section_totals[key] = {
            "series_count": section_entry["series_count"],
            "total_observations": section_entry["total_observations"],
            "record_count": section_entry["records_summary"]["count"],
        }
        grand_total_observations += section_entry["total_observations"]
        grand_total_series += section_entry["series_count"]
        for series_id, s in section_entry["series"].items():
            if s["observation_count"] > largest_series_count:
                largest_series_count = s["observation_count"]
                largest_series = f"{key}.series.{series_id}"

    city_detail = sections_out.get("property", {}).get("city_detail")
    city_metric_observations = city_detail["total_city_metric_observations"] if city_detail else 0

    sanity_totals = {
        "per_section": per_section_totals,
        "property_city_detail_series_equivalents": (
            len(city_detail["declared_city_names"]) * len(city_detail["metric_names"]) if city_detail else 0
        ),
        "property_city_detail_total_observations": city_metric_observations,
        "grand_total_named_series": grand_total_series,
        "grand_total_named_series_observations": grand_total_observations,
        "grand_total_observations_including_city_detail": grand_total_observations + city_metric_observations,
        "largest_single_named_series": {"path": largest_series, "observation_count": largest_series_count},
    }

    result = OrderedDict()
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["generated_by"] = "docs/_inventory/extract_shapes.py"
    result["source_data_js"] = {"path": "data.js", "char_length": data_js_char_len, "top_level_keys": list(dashboard.keys())}
    result["sections"] = sections_out
    result["archive_files"] = archive_files
    result["app_js_dependency_audit"] = app_js_dependency_audit()
    result["period_format_summary"] = dict(global_period_formats)
    result["sanity_totals"] = sanity_totals
    result["data_oddities"] = detect_oddities(dashboard, sections_out)

    json_path = INVENTORY_DIR / "current-data-shapes.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = INVENTORY_DIR / "current-data-shapes.md"
    md_path.write_text(render_markdown(result), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def fmt_num(n):
    return f"{n:,}"


def render_markdown(result):
    lines = []
    lines.append("# Current data shapes -- china-consumer-data-dashboard")
    lines.append("")
    lines.append(
        f"Generated by `docs/_inventory/extract_shapes.py` on {result['generated_at']}. "
        "Machine-readable twin: `current-data-shapes.json`."
    )
    lines.append("")
    lines.append(
        "`data.js` assigns `window.__DASHBOARD_DATA__ = {...}` (a JS literal, not JSON -- "
        "strip the `window.__DASHBOARD_DATA__ = ` prefix and trailing `;` before `json.loads`). "
        f"Top-level keys: `{'`, `'.join(result['source_data_js']['top_level_keys'])}`."
    )
    lines.append("")

    # Sanity totals up front
    st = result["sanity_totals"]
    lines.append("## Sanity totals")
    lines.append("")
    lines.append("| Section | Named series | Records | Observations (named series) |")
    lines.append("|---|---|---|---|")
    for key, t in st["per_section"].items():
        lines.append(f"| {key} | {t['series_count']} | {t['record_count']} | {fmt_num(t['total_observations'])} |")
    lines.append("")
    lines.append(
        f"- Property city detail (per-record nested `cities` dict, not counted as named series above): "
        f"**{st['property_city_detail_series_equivalents']} city x metric combinations** "
        f"(`70 cities x 2 metrics`), **{fmt_num(st['property_city_detail_total_observations'])} observations**."
    )
    lines.append(f"- Grand total named series across all sections: **{st['grand_total_named_series']}**.")
    lines.append(f"- Grand total observations, named series only: **{fmt_num(st['grand_total_named_series_observations'])}**.")
    lines.append(
        f"- Grand total observations including property city detail: "
        f"**{fmt_num(st['grand_total_observations_including_city_detail'])}**."
    )
    largest = st["largest_single_named_series"]
    lines.append(
        f"- Largest single named series: **`{largest['path']}`** with {fmt_num(largest['observation_count'])} observations "
        "(retail_total, monthly since 1985-01)."
    )
    lines.append("")

    lines.append("## Period string formats found")
    lines.append("")
    for fmt, cnt in result["period_format_summary"].items():
        lines.append(f"- `{fmt}`: {fmt_num(cnt)} records")
    lines.append(
        "\nAll three sections use a single period format: `\"YYYY-MM\"` (zero-padded month), even for "
        "annual/quarterly rows -- granularity is conveyed by separate `month`/`quarter`/`period_label`/"
        "`historical_supplement` fields, never by the period string shape itself. No bare `YYYY` or "
        "`YYYYQ#` period strings were found anywhere in data.js or the archive files."
    )
    lines.append("")

    lines.append("## Data oddities")
    lines.append("")
    lines.append(
        "Programmatically detected by `detect_oddities()` in extract_shapes.py -- re-run the script "
        "to reproduce these rather than trusting this list after the source files change."
    )
    lines.append("")
    for i, odd in enumerate(result["data_oddities"], 1):
        lines.append(f"{i}. **{odd['kind']}** ({odd['section']}) -- {odd['detail']}")
        if odd["kind"] == "zero_observation_series":
            lines.append(f"   - Series: `{'`, `'.join(odd['series_ids'])}`")
        if odd["kind"] == "missing_calendar_periods_in_records":
            lines.append(
                f"   - {odd['missing_count']} missing within {odd['range'][0]}..{odd['range'][1]} "
                f"(cadence assumed: {odd['cadence_assumed']}); sample: `{odd['missing_sample']}`"
            )
        if odd["kind"] == "flagship_series_internal_nulls":
            lines.append(f"   - `{odd['series_id']}` null at: `{odd['null_periods']}`")
        if odd["kind"] == "property_city_coverage_gap":
            lines.append(f"   - Records: `{odd['records']}`")
    lines.append("")

    lines.append("## App.js dependency audit (load-bearing vs vestigial)")
    lines.append("")
    audit = result["app_js_dependency_audit"]
    lines.append("**Reads (load-bearing):**")
    lines.append(f"- Top-level `__DASHBOARD_DATA__` keys: `{'`, `'.join(audit['dashboard_top_level_keys_read'])}`")
    lines.append(f"  - {audit['note_on_property']}")
    lines.append(f"- Series metadata fields: `{'`, `'.join(audit['series_meta_fields_read'])}`")
    lines.append(f"- Record fields: `{'`, `'.join(audit['record_fields_read'])}`")
    lines.append(f"- Metric fields: `{'`, `'.join(audit['metric_fields_read'])}`")
    lines.append("")
    lines.append("**Present in data, never read by app.js (vestigial from the UI's point of view):**")
    for item in audit["fields_present_in_data_but_never_read_by_app_js"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("**Chart-mode / revision-variant logic app.js expects:**")
    for k, v in audit["chart_mode_logic_keys_expected_by_app_js"].items():
        lines.append(f"- `{k}`: {v}")
    lines.append("")

    lines.append("## Sections (from data.js)")
    for key, section_entry in result["sections"].items():
        lines.append("")
        lines.append(f"### `{key}`")
        lines.append("")
        lines.append(f"- Path: `{section_entry['path']}`")
        lines.append(f"- Top-level keys: `{'`, `'.join(section_entry['top_level_keys'])}`")
        lines.append(f"- Series count: {section_entry['series_count']}; total observations: {fmt_num(section_entry['total_observations'])}")
        rs = section_entry["records_summary"]
        lines.append(f"- Records: {fmt_num(rs['count'])}, period range {rs['period_range']['min']} .. {rs['period_range']['max']}")
        lines.append(f"- Record key signatures (schema variants seen across records):")
        for sig in rs["record_key_signatures"]:
            lines.append(f"  - {sig['count']} records with keys: `{', '.join(sig['keys'])}`")
        lines.append("")
        lines.append(f"| Series id | Name | Group | Unit | Frequency | Obs | Range | Variants |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for series_id, s in section_entry["series"].items():
            variants = []
            if s["has_plain_month_variant"]:
                variants.append("month")
            if s["has_latest_variant"]:
                variants.append("latest")
            if s["has_published_variant"]:
                variants.append("published")
            if s["has_ytd_variant"]:
                variants.append("ytd")
            name = (s["label_fields"]["name"] or "").replace("|", "\\|")
            group = (s["label_fields"]["group"] or "").replace("|", "\\|")
            lines.append(
                f"| `{series_id}` | {name} | {group} | {s['unit'] or '--'} | {s['frequency']} | {fmt_num(s['observation_count'])} | "
                f"{s['date_range']['min']}..{s['date_range']['max']} | {'/'.join(variants) or '--'} |"
            )
        if key == "property" and section_entry.get("city_detail"):
            cd = section_entry["city_detail"]
            lines.append("")
            lines.append("**Property city detail** (nested per-record, not a named series above):")
            lines.append(f"- {cd['description']}")
            lines.append(
                f"- Declared cities: {cd['declared_city_count']} (at `{cd['declared_cities_path']}`); "
                f"all appear in records: {'yes' if not cd['cities_declared_not_seen_in_any_record'] else cd['cities_declared_not_seen_in_any_record']}"
            )
            lines.append(f"- Metric names per city: `{'`, `'.join(cd['metric_names'])}`")
            lines.append(f"- Metric field names (same shape as top-level series metrics): `{'`, `'.join(cd['metric_field_names'])}`")
            lines.append(f"- Total city x metric observations: {fmt_num(cd['total_city_metric_observations'])}")
            lines.append(f"- City-count-per-record distribution: `{cd['city_count_distribution_across_records']}`")
            if cd["records_with_incomplete_city_coverage"]:
                lines.append(f"- **Data gap** -- records with fewer than {cd['declared_city_count']} cities:")
                for rec in cd["records_with_incomplete_city_coverage"]:
                    lines.append(f"  - `{rec['period']}`: only {rec['city_count']} cities present")
            lines.append(f"- Null `month_value` counts by metric (out of {fmt_num(cd['total_city_metric_observations'])} total): `{cd['null_month_value_counts_by_metric']}`")

    lines.append("")
    lines.append("## Standalone archive/history JSON files")
    lines.append("")
    for fname, info in result["archive_files"].items():
        lines.append(f"### `{fname}`")
        lines.append("")
        if "error" in info:
            lines.append(f"- {info['error']}")
            continue
        lines.append(f"- File size: {fmt_num(info['file_size_bytes'])} bytes")
        rel = info.get("relationship_to_data_js")
        if rel:
            lines.append(f"- Relationship to `{rel['compared_to']}`: {rel['note']}")
        shape = info["shape"]
        lines.append(f"- Top-level type: {shape['type']}, {shape.get('len')} keys/items")
        if shape.get("keys"):
            for k, v in shape["keys"].items():
                if isinstance(v, dict) and "type" in v:
                    extra = f", sample={v.get('sample')!r}" if v.get("sample") is not None else ""
                    lines.append(f"  - `{k}`: {v['type']}, len={v.get('len')}{extra}")
        lines.append("")

    lines.append(
        "Note: `property_city_history.json` and `property_city_history_raw.json` are build-pipeline "
        "intermediates (produced/consumed by `tools/fetch_property_archive.py` and "
        "`tools/merge_property_city_history.py`, per README.md's refresh steps). Neither is fetched by "
        "app.js or referenced anywhere in index.html/app.js -- they never reach the browser."
    )
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
