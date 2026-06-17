#!/usr/bin/env python3
"""Merge cached 70-city history into the property dashboard dataset."""

from __future__ import annotations

import json
from pathlib import Path

from lxml import html

import fetch_property_archive as helper


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "property_release_archive.json"
CITY_HISTORY = ROOT / "property_city_history.json"
CACHE = ROOT / "_cache" / "property_pages"


def normalize_city(city: str) -> str:
    return "".join(str(city).split())


def get_record(records: dict[str, dict], period: str, url: str, source: str) -> dict:
    year, month = map(int, period.split("-"))
    record = records.setdefault(
        period,
        {
            "period": period,
            "year": year,
            "month": month,
            "url": url,
            "source": source,
            "metrics": {},
        },
    )
    record.setdefault("metrics", {})
    if url:
        record["url"] = url
    record["source"] = source
    return record


def merge_city(record: dict, city: str, series_id: str, metric: dict) -> None:
    city = normalize_city(city)
    record.setdefault("cities", {}).setdefault(city, {})[series_id] = metric


def normalize_record_cities(record: dict) -> None:
    cities = record.get("cities")
    if not cities:
        return
    normalized: dict[str, dict] = {}
    for city, metrics in cities.items():
        target = normalized.setdefault(normalize_city(city), {})
        target.update(metrics)
    record["cities"] = normalized


def recompute_aggregate(record: dict, *, overwrite: bool = False) -> None:
    normalize_record_cities(record)
    city_items = list(record.get("cities", {}).values())
    metrics = record.setdefault("metrics", {})
    updates = {
        "new_home_70_price": helper.city_aggregate(city_items, "new_home_price"),
        "resale_home_70_price": helper.city_aggregate(city_items, "resale_home_price"),
        "new_home_up_cities": helper.city_up_count(city_items, "new_home_price"),
        "resale_home_up_cities": helper.city_up_count(city_items, "resale_home_price"),
    }
    for series_id, metric in updates.items():
        if metric and (overwrite or series_id not in metrics):
            metrics[series_id] = metric


def merge_history(records: dict[str, dict], city_history: dict) -> None:
    source_url = city_history.get("source_url", "")
    for source_record in city_history.get("records", []):
        record = get_record(records, source_record["period"], source_url, "nbs_data")
        for city, city_data in source_record.get("cities", {}).items():
            if "new_home_price" in city_data:
                merge_city(record, city, "new_home_price", city_data["new_home_price"])
            if "resale_home_price" in city_data:
                merge_city(record, city, "resale_home_price", city_data["resale_home_price"])
        recompute_aggregate(record, overwrite=False)


def merge_cached_releases(records: dict[str, dict]) -> None:
    for path in sorted(CACHE.glob("nbs_70_city_*.html")):
        period = path.stem.removeprefix("nbs_70_city_")
        raw = path.read_text(encoding="utf-8", errors="ignore")
        doc = html.fromstring(raw)
        parsed = [helper.parse_city_table(table) for table in doc.xpath("//table")]
        parsed = [item for item in parsed if item]
        if len(parsed) < 2:
            continue

        existing_url = records.get(period, {}).get("url", "")
        record = get_record(records, period, existing_url, "nbs")
        new_home, resale = parsed[0], parsed[1]
        record["metrics"]["new_home_70_price"] = helper.metric(new_home["avg_mom"], new_home["avg_yoy"])
        record["metrics"]["resale_home_70_price"] = helper.metric(resale["avg_mom"], resale["avg_yoy"])
        record["metrics"]["new_home_up_cities"] = helper.metric(new_home["up"])
        record["metrics"]["resale_home_up_cities"] = helper.metric(resale["up"])
        for city, city_metric in new_home["cities"].items():
            merge_city(record, city, "new_home_price", city_metric)
        for city, city_metric in resale["cities"].items():
            merge_city(record, city, "resale_home_price", city_metric)


def main() -> None:
    payload = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    city_history = json.loads(CITY_HISTORY.read_text(encoding="utf-8"))
    records = {record["period"]: record for record in payload["records"]}
    for record in records.values():
        normalize_record_cities(record)

    merge_history(records, city_history)
    merge_cached_releases(records)

    payload["cities"] = list(dict.fromkeys(normalize_city(city) for city in city_history.get("cities", [])))
    payload["records"] = [records[key] for key in sorted(records)]
    payload.setdefault("sources", {})["nbs_city_history"] = city_history.get("source_scope", "")
    notes = payload.setdefault("notes", [])
    note = "70城城市明细历史层来自国家数据“主要城市月度价格”；2011-01至2026-04使用国家数据，2026-05及后续优先使用发布稿缓存。"
    if note not in notes:
        notes.insert(0, note)
    ARCHIVE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {ARCHIVE}")
    print(f"cities={len(payload['cities'])}")
    print(f"records={len(payload['records'])} {payload['records'][0]['period']}..{payload['records'][-1]['period']}")
    for series_id in ("new_home_70_price", "resale_home_70_price"):
        points = [record for record in payload["records"] if series_id in record.get("metrics", {})]
        print(f"{series_id}: {len(points)} {points[0]['period']}..{points[-1]['period']}")


if __name__ == "__main__":
    main()
