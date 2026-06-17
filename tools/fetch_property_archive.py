#!/usr/bin/env python3
"""Build the property dashboard dataset from official Chinese sources."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from lxml import html


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "_cache" / "property_pages"
OUT = ROOT / "property_release_archive.json"
CITY_HISTORY = ROOT / "property_city_history.json"

UA = "Mozilla/5.0"


SERIES = {
    "new_home_70_price": {
        "name": "70城新房价格",
        "group": "70城房价",
        "level": 1,
        "unit": "%",
        "valueLabel": "环比",
        "yoyLabel": "同比",
        "source_name": "中国国家统计局",
        "methodUrl": "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
        "source_link_label": "统计方法说明",
    },
    "resale_home_70_price": {
        "name": "70城二手房价格",
        "group": "70城房价",
        "level": 1,
        "unit": "%",
        "valueLabel": "环比",
        "yoyLabel": "同比",
        "source_name": "中国国家统计局",
        "methodUrl": "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
        "source_link_label": "统计方法说明",
    },
    "new_home_up_cities": {
        "name": "70城新房环比上涨城市数",
        "group": "70城房价",
        "level": 2,
        "unit": "个",
        "valueLabel": "城市数",
        "source_name": "中国国家统计局",
        "methodUrl": "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
        "source_link_label": "统计方法说明",
    },
    "resale_home_up_cities": {
        "name": "70城二手房环比上涨城市数",
        "group": "70城房价",
        "level": 2,
        "unit": "个",
        "valueLabel": "城市数",
        "source_name": "中国国家统计局",
        "methodUrl": "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
        "source_link_label": "统计方法说明",
    },
    "real_estate_loan_balance": {
        "name": "人民币房地产贷款余额",
        "group": "房贷余额",
        "level": 3,
        "unit": "亿元",
        "valueLabel": "余额",
        "yoyLabel": "同比",
        "source_name": "中国人民银行",
        "methodUrl": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026042910121416386/index.html",
        "source_link_label": "贷款投向统计报告",
    },
    "mortgage_balance": {
        "name": "个人住房贷款余额",
        "group": "房贷余额",
        "level": 3,
        "unit": "亿元",
        "valueLabel": "余额",
        "yoyLabel": "同比",
        "source_name": "中国人民银行",
        "methodUrl": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026042910121416386/index.html",
        "source_link_label": "贷款投向统计报告",
    },
    "property_development_loan_balance": {
        "name": "房地产开发贷款余额",
        "group": "房贷余额",
        "level": 3,
        "unit": "亿元",
        "valueLabel": "余额",
        "yoyLabel": "同比",
        "source_name": "中国人民银行",
        "methodUrl": "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026042910121416386/index.html",
        "source_link_label": "贷款投向统计报告",
    },
    "land_transfer_revenue": {
        "name": "国有土地使用权出让收入",
        "group": "财政土地",
        "level": 4,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "real_estate_tax_total": {
        "name": "地产相关税收合计",
        "group": "财政土地",
        "level": 4,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "deed_tax": {
        "name": "契税收入",
        "group": "地产相关税收",
        "level": 5,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "property_tax": {
        "name": "房产税收入",
        "group": "地产相关税收",
        "level": 5,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "urban_land_use_tax": {
        "name": "城镇土地使用税收入",
        "group": "地产相关税收",
        "level": 5,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "land_vat": {
        "name": "土地增值税收入",
        "group": "地产相关税收",
        "level": 5,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
    "farmland_occupation_tax": {
        "name": "耕地占用税收入",
        "group": "地产相关税收",
        "level": 5,
        "unit": "亿元",
        "valueLabel": "报告期",
        "yoyLabel": "同比",
        "source_name": "财政部",
        "methodUrl": "http://www.mof.gov.cn/gkml/caizhengshuju/",
        "source_link_label": "财政收支情况",
    },
}


PREFERRED = [
    "new_home_70_price",
    "resale_home_70_price",
    "new_home_up_cities",
    "resale_home_up_cities",
    "real_estate_loan_balance",
    "mortgage_balance",
    "property_development_loan_balance",
    "land_transfer_revenue",
    "real_estate_tax_total",
    "deed_tax",
    "property_tax",
    "urban_land_use_tax",
    "land_vat",
    "farmland_occupation_tax",
]


def fetch(url: str, *, data: bytes | None = None) -> bytes:
    req = Request(
        url,
        data=data,
        headers={
            "User-Agent": UA,
            "Referer": "https://www.stats.gov.cn/" if "so-gov" in url else url,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    return urlopen(req, timeout=30).read()


def cached(url: str, name: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        path.write_bytes(fetch(url))
        time.sleep(0.12)
    return path.read_text(encoding="utf-8", errors="ignore")


def normalize_url(url: str) -> str:
    return url.replace("http://www.stats.gov.cn/", "https://www.stats.gov.cn/")


def number(value: str) -> float | None:
    value = value.replace(",", "").strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return float(value)
    return None


def signed_yoy(direction: str, value: str) -> float:
    result = float(value)
    return -result if direction in ("下降", "减少") else result


def metric(value: float | None = None, yoy: float | None = None) -> dict:
    out: dict[str, float] = {}
    if value is not None:
        out["month_value"] = round(float(value), 2)
        out["latest_month_value"] = round(float(value), 2)
        out["published_month_value"] = round(float(value), 2)
    if yoy is not None:
        out["month_yoy"] = round(float(yoy), 2)
        out["latest_month_yoy"] = round(float(yoy), 2)
        out["published_month_yoy"] = round(float(yoy), 2)
    return out


def get_record(records: dict[str, dict], period: str, year: int, month: int, url: str, source: str) -> dict:
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
    if period >= record["period"]:
        record["url"] = url
        record["source"] = source
    return record


def nbs_search_pages() -> list[tuple[str, str]]:
    results: dict[str, str] = {}
    pattern = re.compile(r"(\d{4})年(\d{1,2})月份70个大中城市商品住宅销售价格变动情况")
    for page in range(1, 9):
        body = urlencode(
            {
                "qt": "70个大中城市商品住宅销售价格变动情况",
                "tab": "all",
                "siteCode": "bm36000002",
                "page": str(page),
                "pageSize": "100",
            }
        ).encode()
        raw = fetch("https://api.so-gov.cn/query/s", data=body).decode("utf-8", errors="ignore")
        payload = json.loads(raw)
        for doc in payload.get("resultDocs", []):
            data = doc.get("data", {})
            title = re.sub(r"<[^>]+>", "", data.get("titleO") or data.get("title") or "")
            match = pattern.fullmatch(title.strip())
            if not match:
                continue
            year, month = map(int, match.groups())
            if year < 2016:
                continue
            period = f"{year}-{month:02d}"
            results[period] = normalize_url(data["url"])
        if len(results) >= 130:
            break
        time.sleep(0.1)
    return sorted(results.items())


def parse_city_table(table) -> dict | None:
    cells = ["".join(cell.xpath(".//text()")).strip().replace(" ", "") for cell in table.xpath(".//td|.//th")]
    entries = []
    for idx in range(len(cells) - 3):
        mom = number(cells[idx + 1])
        yoy = number(cells[idx + 2])
        avg = number(cells[idx + 3])
        city = cells[idx]
        if not city or number(city) is not None or mom is None or yoy is None or avg is None:
            continue
        if not (70 <= mom <= 130 and 50 <= yoy <= 150):
            continue
        entries.append((city, mom, yoy))
    unique = []
    seen = set()
    for city, mom, yoy in entries:
        if city in seen:
            continue
        seen.add(city)
        unique.append((city, mom, yoy))
    if len(unique) < 60:
        return None
    rows = unique[:70]
    mom_changes = [item[1] - 100 for item in rows]
    yoy_changes = [item[2] - 100 for item in rows]
    return {
        "avg_mom": sum(mom_changes) / len(mom_changes),
        "avg_yoy": sum(yoy_changes) / len(yoy_changes),
        "up": sum(1 for _, mom, _ in rows if mom > 100),
        "flat": sum(1 for _, mom, _ in rows if mom == 100),
        "down": sum(1 for _, mom, _ in rows if mom < 100),
        "count": len(rows),
        "cities": {city: metric(mom - 100, yoy - 100) for city, mom, yoy in rows},
    }


def merge_city_metric(record: dict, city: str, series_id: str, data: dict) -> None:
    record.setdefault("cities", {}).setdefault(city, {})[series_id] = data


def city_aggregate(city_items: list[dict], series_id: str) -> dict:
    values = [
        item[series_id]["month_value"]
        for item in city_items
        if series_id in item and item[series_id].get("month_value") is not None
    ]
    yoys = [
        item[series_id]["month_yoy"]
        for item in city_items
        if series_id in item and item[series_id].get("month_yoy") is not None
    ]
    value = sum(values) / len(values) if values else None
    yoy = sum(yoys) / len(yoys) if yoys else None
    return metric(value, yoy)


def city_up_count(city_items: list[dict], series_id: str) -> dict:
    values = [
        item[series_id]["month_value"]
        for item in city_items
        if series_id in item and item[series_id].get("month_value") is not None
    ]
    return metric(sum(1 for value in values if value > 0)) if values else {}


def add_nbs_city_history(records: dict[str, dict]) -> list[str]:
    if not CITY_HISTORY.exists():
        return []
    payload = json.loads(CITY_HISTORY.read_text(encoding="utf-8"))
    cities = payload.get("cities", [])
    for source_record in payload.get("records", []):
        period = source_record["period"]
        year, month = map(int, period.split("-"))
        record = get_record(
            records,
            period,
            year,
            month,
            payload.get("source_url") or "https://data.stats.gov.cn/dg/website/page.html#/pc/national/mainMonthData",
            "nbs_data",
        )
        for city, city_data in source_record.get("cities", {}).items():
            if "new_home_price" in city_data:
                merge_city_metric(record, city, "new_home_price", city_data["new_home_price"])
            if "resale_home_price" in city_data:
                merge_city_metric(record, city, "resale_home_price", city_data["resale_home_price"])

        city_items = list(record.get("cities", {}).values())
        new_home = city_aggregate(city_items, "new_home_price")
        resale = city_aggregate(city_items, "resale_home_price")
        if new_home:
            record["metrics"]["new_home_70_price"] = new_home
        if resale:
            record["metrics"]["resale_home_70_price"] = resale
        new_up = city_up_count(city_items, "new_home_price")
        resale_up = city_up_count(city_items, "resale_home_price")
        if new_up:
            record["metrics"]["new_home_up_cities"] = new_up
        if resale_up:
            record["metrics"]["resale_home_up_cities"] = resale_up
    return cities


def add_nbs_70_city(records: dict[str, dict]) -> None:
    for period, url in nbs_search_pages():
        year, month = map(int, period.split("-"))
        name = f"nbs_70_city_{period}.html"
        try:
            raw = cached(url, name)
            doc = html.fromstring(raw)
            parsed = [parse_city_table(table) for table in doc.xpath("//table")]
            parsed = [item for item in parsed if item]
            if len(parsed) < 2:
                continue
            new_home, resale = parsed[0], parsed[1]
        except Exception as exc:
            print(f"skip NBS {period}: {exc}")
            continue

        record = get_record(records, period, year, month, url, "nbs")
        record["metrics"]["new_home_70_price"] = metric(new_home["avg_mom"], new_home["avg_yoy"])
        record["metrics"]["resale_home_70_price"] = metric(resale["avg_mom"], resale["avg_yoy"])
        record["metrics"]["new_home_up_cities"] = metric(new_home["up"])
        record["metrics"]["resale_home_up_cities"] = metric(resale["up"])
        for city, city_metric in new_home["cities"].items():
            merge_city_metric(record, city, "new_home_price", city_metric)
        for city, city_metric in resale["cities"].items():
            merge_city_metric(record, city, "resale_home_price", city_metric)


def pbc_search(query: str) -> str | None:
    url = "https://wzdig.pbc.gov.cn/search/pcRender?" + urlencode(
        {
            "sr": "score desc",
            "pageId": "c177a85bd02b4114bebebd210809f691",
            "ext": "",
            "pNo": "1",
            "q": query,
        }
    )
    raw = fetch(url).decode("utf-8", errors="ignore")
    doc = html.fromstring(raw)
    for link in doc.xpath("//a"):
        title = "".join(link.xpath(".//text()")).strip().replace("\n", " ")
        href = link.get("href") or ""
        if title == query and href.startswith("http"):
            return href
    return None


def article_text(raw: str) -> str:
    doc = html.fromstring(raw)
    nodes = doc.xpath('//div[@id="zoom"]//text() | //div[contains(@class,"TRS_Editor")]//text() | //td[contains(@class,"content")]//text()')
    text = "\n".join(item.strip() for item in nodes if item.strip())
    if len(text) < 100:
        text = "\n".join(item.strip() for item in doc.xpath("//text()") if item.strip())
    return re.sub(r"\s+", "", text)


def wan_yuan_match(text: str, label: str) -> tuple[float, float] | None:
    pattern = re.compile(rf"{re.escape(label)}余额([0-9.]+)万亿元，同比(增长|下降|减少)([0-9.]+)%")
    found = pattern.search(text)
    if not found:
        return None
    return float(found.group(1)) * 10000, signed_yoy(found.group(2), found.group(3))


def add_pbc_loans(records: dict[str, dict]) -> None:
    quarter_names = [(3, "一季度"), (6, "二季度"), (9, "三季度"), (12, "四季度")]
    for year in range(2016, 2027):
        for month, quarter in quarter_names:
            if year == 2026 and month > 3:
                continue
            query = f"{year}年{quarter}金融机构贷款投向统计报告"
            url = pbc_search(query)
            if not url:
                continue
            try:
                raw = cached(url, f"pbc_loan_{year}_{month:02d}.html")
                text = article_text(raw)
            except Exception as exc:
                print(f"skip PBC {year} {quarter}: {exc}")
                continue

            period = f"{year}-{month:02d}"
            record = get_record(records, period, year, month, url, "pbc")
            mappings = {
                "real_estate_loan_balance": "人民币房地产贷款",
                "mortgage_balance": "个人住房贷款",
                "property_development_loan_balance": "房地产开发贷款",
            }
            for series_id, label in mappings.items():
                parsed = wan_yuan_match(text, label)
                if parsed:
                    value, yoy = parsed
                    record["metrics"][series_id] = metric(value, yoy)
                    SERIES[series_id]["methodUrl"] = url


def fiscal_month(title: str) -> tuple[int, int] | None:
    year_match = re.match(r"(\d{4})年", title)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if "一季度" in title:
        return year, 3
    if "上半年" in title:
        return year, 6
    if "前三季度" in title:
        return year, 9
    if re.search(r"\d{4}年财政收支情况", title):
        return year, 12
    found = re.search(r"(?:1[-—－])?(\d{1,2})月财政收支情况", title)
    if found:
        return year, int(found.group(1))
    return None


def fiscal_pages() -> list[tuple[str, str, int, int]]:
    pages = []
    bases = ["http://www.mof.gov.cn/gkml/caizhengshuju/"] + [
        f"http://www.mof.gov.cn/gkml/caizhengshuju/index_{idx}.htm" for idx in range(1, 20)
    ]
    for page_url in bases:
        try:
            raw = fetch(page_url).decode("utf-8", errors="ignore")
        except Exception:
            continue
        doc = html.fromstring(raw)
        for link in doc.xpath("//a"):
            title = "".join(link.xpath(".//text()")).strip().replace("\n", " ")
            if "财政收支情况" not in title:
                continue
            parsed = fiscal_month(title)
            if not parsed:
                continue
            year, month = parsed
            if year < 2016:
                continue
            pages.append((title, urljoin(page_url, link.get("href") or ""), year, month))
    dedup = {(year, month): (title, url, year, month) for title, url, year, month in pages}
    return [dedup[key] for key in sorted(dedup)]


def yuan_yoy_match(text: str, label: str) -> tuple[float, float] | None:
    pattern = re.compile(rf"{re.escape(label)}(?:收入)?([0-9.]+)亿元，(?:同比|比上年)(增长|下降)([0-9.]+)%")
    found = pattern.search(text)
    if not found:
        return None
    return float(found.group(1)), signed_yoy(found.group(2), found.group(3))


def add_mof_fiscal(records: dict[str, dict]) -> None:
    mappings = {
        "deed_tax": "契税",
        "property_tax": "房产税",
        "urban_land_use_tax": "城镇土地使用税",
        "land_vat": "土地增值税",
        "farmland_occupation_tax": "耕地占用税",
        "land_transfer_revenue": "国有土地使用权出让收入",
    }
    for title, url, year, month in fiscal_pages():
        period = f"{year}-{month:02d}"
        try:
            raw = cached(url, f"mof_fiscal_{period}.html")
            text = article_text(raw)
        except Exception as exc:
            print(f"skip MOF {period}: {exc}")
            continue
        record = get_record(records, period, year, month, url, "mof")
        for series_id, label in mappings.items():
            parsed = yuan_yoy_match(text, label)
            if not parsed:
                continue
            value, yoy = parsed
            record["metrics"][series_id] = metric(value, yoy)
            SERIES[series_id]["methodUrl"] = url

        tax_ids = [
            "deed_tax",
            "property_tax",
            "urban_land_use_tax",
            "land_vat",
            "farmland_occupation_tax",
        ]
        if all(series_id in record["metrics"] for series_id in tax_ids):
            value = sum(record["metrics"][series_id]["month_value"] for series_id in tax_ids)
            record["metrics"]["real_estate_tax_total"] = metric(value)
            SERIES["real_estate_tax_total"]["methodUrl"] = url

    by_period = records
    for period, record in sorted(by_period.items()):
        current = record["metrics"].get("real_estate_tax_total")
        if not current:
            continue
        year, month = map(int, period.split("-"))
        previous = by_period.get(f"{year - 1}-{month:02d}", {}).get("metrics", {}).get("real_estate_tax_total")
        if previous and previous.get("month_value"):
            yoy = (current["month_value"] / previous["month_value"] - 1) * 100
            current.update(metric(current["month_value"], yoy))


def build() -> dict:
    records: dict[str, dict] = {}
    cities = add_nbs_city_history(records)
    add_nbs_70_city(records)
    add_pbc_loans(records)
    add_mof_fiscal(records)
    final_records = [records[key] for key in sorted(records)]
    return {
        "section": "property",
        "section_name": "房价和房贷",
        "frequency": "mixed",
        "series": SERIES,
        "preferred": PREFERRED,
        "cities": cities,
        "records": final_records,
        "sources": {
            "nbs_70_city": "国家统计局《70个大中城市商品住宅销售价格变动情况》",
            "pbc_loans": "中国人民银行《金融机构贷款投向统计报告》",
            "mof_fiscal": "财政部《财政收支情况》",
        },
        "notes": [
            "70城房价为国家统计局70个城市指数的简单平均，环比/同比均由指数减100得到。城市明细来自国家数据“主要城市月度价格”历史层和后续发布稿补充。",
            "房贷余额为央行季度贷款投向统计报告期末余额，单位由万亿元换算为亿元。",
            "地产相关税收和土地出让收入为财政部累计报告期数据，非单月值。",
        ],
    }


def main() -> None:
    payload = build()
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"records={len(payload['records'])}")
    for series_id in PREFERRED:
        count = sum(1 for record in payload["records"] if series_id in record["metrics"])
        first = next((record["period"] for record in payload["records"] if series_id in record["metrics"]), "--")
        last = next((record["period"] for record in reversed(payload["records"]) if series_id in record["metrics"]), "--")
        print(f"{series_id}: {count} {first}..{last}")


if __name__ == "__main__":
    main()
