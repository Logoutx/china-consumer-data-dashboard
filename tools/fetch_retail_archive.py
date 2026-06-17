from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lxml import html


BASE = "https://www.stats.gov.cn/sj/zxfb/"
SEARCH_API = "https://api.so-gov.cn/query/s"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT
WORK_DIR = ROOT / "_cache" / "retail_pages"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


SERIES_ALIASES = {
    "社会消费品零售总额": "retail_total",
    "其中：除汽车以外的消费品零售额": "retail_ex_auto",
    "除汽车以外的消费品零售额": "retail_ex_auto",
    "城镇": "urban",
    "乡村": "rural",
    "餐饮收入": "catering",
    "商品零售额": "goods",
    "其中：网上商品零售额": "online_goods",
    "网上商品零售额": "online_goods",
    "其中：实物商品网上零售额": "online_goods",
    "实物商品网上零售额": "online_goods",
    "限额以上单位消费品零售额": "above_quota_total",
    "限额以上单位餐饮收入": "above_quota_catering",
    "限额以上单位商品零售额": "above_quota_goods",
    "粮油、食品类": "grain_food",
    "饮料类": "beverage",
    "烟酒类": "tobacco_alcohol",
    "服装、鞋帽、针纺织品类": "garments",
    "化妆品类": "cosmetics",
    "金银珠宝类": "gold_jewelry",
    "日用品类": "daily_goods",
    "体育、娱乐用品类": "sports_entertainment",
    "书报杂志类": "books_magazines",
    "家用电器和音像器材类": "household_appliances",
    "中西药品类": "medicine",
    "文化办公用品类": "cultural_office",
    "家具类": "furniture",
    "通讯器材类": "communication",
    "石油及制品类": "petroleum",
    "汽车类": "auto_total",
    "建筑及装潢材料类": "building_materials",
}


SERIES_META = {
    "retail_total": {"name": "社会消费品零售总额", "group": "总览", "level": 1},
    "retail_ex_auto": {"name": "除汽车以外的消费品零售额", "group": "汽车拆分", "level": 2},
    "auto_total": {"name": "汽车消费零售额", "group": "汽车拆分", "level": 2},
    "urban": {"name": "城镇消费品零售额", "group": "城乡", "level": 3},
    "rural": {"name": "乡村消费品零售额", "group": "城乡", "level": 3},
    "online_goods": {"name": "网上商品零售额", "group": "线上", "level": 4},
    "online_ex_auto_share": {"name": "网上商品零售额占除汽车零售额比重", "group": "线上", "level": 4},
    "catering": {"name": "餐饮收入", "group": "消费类型", "level": 5},
    "goods": {"name": "商品零售额", "group": "消费类型", "level": 5},
    "above_quota_total": {"name": "限额以上单位消费品零售额", "group": "限额以上", "level": 6},
    "above_quota_catering": {"name": "限额以上单位餐饮收入", "group": "限额以上", "level": 6},
    "above_quota_goods": {"name": "限额以上单位商品零售额", "group": "限额以上", "level": 6},
    "grain_food": {"name": "粮油、食品类", "group": "商品分类", "level": 7},
    "beverage": {"name": "饮料类", "group": "商品分类", "level": 7},
    "tobacco_alcohol": {"name": "烟酒类", "group": "商品分类", "level": 7},
    "garments": {"name": "服装、鞋帽、针纺织品类", "group": "商品分类", "level": 7},
    "cosmetics": {"name": "化妆品类", "group": "商品分类", "level": 7},
    "gold_jewelry": {"name": "金银珠宝类", "group": "商品分类", "level": 7},
    "daily_goods": {"name": "日用品类", "group": "商品分类", "level": 7},
    "sports_entertainment": {"name": "体育、娱乐用品类", "group": "商品分类", "level": 7},
    "books_magazines": {"name": "书报杂志类", "group": "商品分类", "level": 7},
    "household_appliances": {"name": "家用电器和音像器材类", "group": "商品分类", "level": 7},
    "medicine": {"name": "中西药品类", "group": "商品分类", "level": 7},
    "cultural_office": {"name": "文化办公用品类", "group": "商品分类", "level": 7},
    "furniture": {"name": "家具类", "group": "商品分类", "level": 7},
    "communication": {"name": "通讯器材类", "group": "商品分类", "level": 7},
    "petroleum": {"name": "石油及制品类", "group": "商品分类", "level": 7},
    "building_materials": {"name": "建筑及装潢材料类", "group": "商品分类", "level": 7},
}


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        return resp.read()


def post_json(url: str, params: dict) -> dict:
    body = urlencode(params).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://www.stats.gov.cn/search/s?qt=%E7%A4%BE%E4%BC%9A%E6%B6%88%E8%B4%B9%E5%93%81%E9%9B%B6%E5%94%AE%E6%80%BB%E9%A2%9D",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u3000", "").replace("\xa0", ""))


def to_number(value: str):
    value = clean_text(value).replace(",", "")
    if value in {"", "-", "--", "—"}:
        return None
    value = value.replace("下降", "-").replace("增长", "")
    try:
        return float(value)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else None


def parse_period(title: str):
    match = re.search(r"(?P<year>\d{4})年(?:(?:1|１)[—\-－](?P<end>\d{1,2})月(?:份)?|(?P<month>\d{1,2})月份)", title)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("end") or match.group("month"))
    return {
        "year": year,
        "month": month,
        "period": f"{year}-{month:02d}",
        "title_period": match.group(0),
    }


def parse_pub_date(doc):
    node = doc.xpath("//meta[@name='PubDate']/@content")
    return node[0] if node else None


def extract_links() -> list[dict]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    links = {}
    for index in range(67):
        name = "index.html" if index == 0 else f"index_{index}.html"
        url = urljoin(BASE, name)
        path = WORK_DIR / name
        if not path.exists():
            try:
                path.write_bytes(fetch(url))
            except HTTPError as error:
                if error.code == 404:
                    break
                raise
            time.sleep(0.05)
        doc = html.fromstring(path.read_bytes())
        for anchor in doc.xpath("//a[@title and @href]"):
            title = anchor.get("title", "")
            if "社会消费品零售总额" not in title:
                continue
            if "解读" in title or "走势图" in title:
                continue
            period = parse_period(title)
            if not period:
                continue
            href = urljoin(url, anchor.get("href"))
            links[href] = {"url": href, "title": title, **period}
    for item in extract_search_links():
        links[item["url"]] = item
    return sorted(links.values(), key=lambda x: x["period"])


def extract_search_links() -> list[dict]:
    links = {}
    page_size = 100
    for year in range(2000, 2027):
        for page in range(1, 20):
            payload = post_json(
                SEARCH_API,
                {
                    "siteCode": "bm36000002",
                    "tab": "",
                    "qt": "社会消费品零售总额",
                    "page": page,
                    "pageSize": page_size,
                    "sort": "dateDesc",
                    "adv": 1,
                    "timeOption": 2,
                    "startDateStr": f"{year}-01-01",
                    "endDateStr": f"{year}-12-31",
                },
            )
            docs = payload.get("resultDocs") or []
            if not docs:
                break
            for doc in docs:
                data = doc.get("data") or {}
                title = data.get("titleO") or clean_text(data.get("title", ""))
                if "社会消费品零售总额" not in title:
                    continue
                if "解读" in title or "日程表" in title or "走势图" in title:
                    continue
                period = parse_period(title)
                if not period:
                    continue
                url = data.get("url") or data.get("myValues", {}).get("URL")
                if not url or "stats.gov.cn" not in url:
                    continue
                links[url] = {"url": url, "title": title, **period}
            total = payload.get("totalHits") or 0
            if page * page_size >= total:
                break
    return sorted(links.values(), key=lambda x: x["period"])


def row_values(row):
    cells = [clean_text("".join(cell.itertext())) for cell in row.xpath("./th|./td")]
    cells = [cell for cell in cells if cell]
    if len(cells) < 2:
        return None
    label = cells[0]
    raw_nums = [to_number(cell) for cell in cells[1:]]
    nums = [num for num in raw_nums if num is not None]
    if len(nums) < 2:
        return None
    if len(nums) >= 4:
        return label, nums[0], nums[1], nums[2], nums[3]
    if len(raw_nums) >= 4 and raw_nums[0] is None and raw_nums[1] is None:
        return label, None, None, raw_nums[2], raw_nums[3]
    if len(raw_nums) == 3 and raw_nums[0] is None:
        return label, None, None, raw_nums[1], raw_nums[2]
    return None


def parse_article(item: dict) -> dict | None:
    filename = re.sub(r"[^0-9A-Za-z_.-]+", "_", item["url"].split("/")[-1])
    path = WORK_DIR / filename
    if not path.exists():
        try:
            path.write_bytes(fetch(item["url"]))
        except HTTPError as error:
            if error.code == 404:
                return None
            raise
        time.sleep(0.05)
    doc = html.fromstring(path.read_bytes())
    tables = doc.xpath("//table[.//*[contains(normalize-space(.), '社会消费品零售总额')]]")
    if not tables:
        return None
    table = tables[0]
    metrics = {}
    for row in table.xpath(".//tr"):
        parsed = row_values(row)
        if not parsed:
            continue
        label, month_value, month_yoy, ytd_value, ytd_yoy = parsed
        key = SERIES_ALIASES.get(label)
        if not key:
            continue
        metrics[key] = {
            "month_value": month_value,
            "month_yoy": month_yoy,
            "ytd_value": ytd_value,
            "ytd_yoy": ytd_yoy,
        }
    if "retail_total" in metrics and "retail_ex_auto" in metrics and "auto_total" not in metrics:
        total = metrics["retail_total"]
        ex_auto = metrics["retail_ex_auto"]
        derived = {}
        for scope in ("month", "ytd"):
            value = total.get(f"{scope}_value")
            ex_value = ex_auto.get(f"{scope}_value")
            total_yoy = total.get(f"{scope}_yoy")
            ex_yoy = ex_auto.get(f"{scope}_yoy")
            if value is not None and ex_value is not None:
                auto_value = value - ex_value
                derived[f"{scope}_value"] = round(auto_value, 1)
                if total_yoy is not None and ex_yoy is not None:
                    prev_total = value / (1 + total_yoy / 100)
                    prev_ex = ex_value / (1 + ex_yoy / 100)
                    prev_auto = prev_total - prev_ex
                    derived[f"{scope}_yoy"] = round((auto_value / prev_auto - 1) * 100, 1) if prev_auto else None
        metrics["auto_total"] = derived
    article = {
        **item,
        "published_at": parse_pub_date(doc),
        "source": "国家统计局数据发布",
        "metrics": metrics,
    }
    return article


def add_share_series(records: list[dict]) -> None:
    by_period = {record["period"]: record for record in records}
    for record in records:
        metrics = record["metrics"]
        online = metrics.get("online_goods", {}).get("ytd_value")
        ex_auto = metrics.get("retail_ex_auto", {}).get("ytd_value")
        if online is None or ex_auto in (None, 0):
            continue
        value = round(online / ex_auto * 100, 2)
        prev_period = f"{record['year'] - 1}-{record['month']:02d}"
        prev = by_period.get(prev_period, {}).get("metrics", {}).get("online_ex_auto_share", {}).get("ytd_value")
        metrics["online_ex_auto_share"] = {
            "month_value": None,
            "month_yoy": None,
            "ytd_value": value,
            "ytd_yoy": round(value - prev, 2) if prev is not None else None,
        }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    links = extract_links()
    records = []
    for link in links:
        article = parse_article(link)
        if article and article["metrics"]:
            records.append(article)
    add_share_series(records)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "coverage_note": "发布稿归档与官方站内搜索当前可抓取范围为 2013-10 至最新，但 2021 年前存在索引缺月；全历史建议接入国家数据 hgyd/A0H 接口并保存版本快照。",
        "sources": [
            "https://www.stats.gov.cn/sj/zxfb/",
            "https://www.stats.gov.cn/sj/zxfb/202605/t20260518_1963727.html",
            "https://data.stats.gov.cn/easyquery.htm?cn=A01",
        ],
        "series": SERIES_META,
        "records": records,
    }
    (OUT_DIR / "retail_release_archive.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(records)} records to {OUT_DIR / 'retail_release_archive.json'}")


if __name__ == "__main__":
    main()
