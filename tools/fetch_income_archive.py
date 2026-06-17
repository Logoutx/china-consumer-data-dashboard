from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from lxml import html


SEARCH_API = "https://api.so-gov.cn/query/s"
ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "income_release_archive.json"
WORK_DIR = ROOT / "_cache" / "income_pages"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
LATEST_RELEASE_URL = "https://www.stats.gov.cn/sj/zxfb/202604/t20260416_1963323.html"


SERIES_META = {
    "income_disposable": {"name": "居民人均可支配收入", "group": "收入", "level": 1, "unit": "元"},
    "income_disposable_urban": {"name": "城镇居民人均可支配收入", "group": "收入", "level": 2, "unit": "元"},
    "income_disposable_rural": {"name": "农村居民人均可支配收入", "group": "收入", "level": 2, "unit": "元"},
    "income_wage": {"name": "工资性收入", "group": "收入来源", "level": 3, "unit": "元"},
    "income_business": {"name": "经营净收入", "group": "收入来源", "level": 3, "unit": "元"},
    "income_property": {"name": "财产净收入", "group": "收入来源", "level": 3, "unit": "元"},
    "income_transfer": {"name": "转移净收入", "group": "收入来源", "level": 3, "unit": "元"},
    "income_median": {"name": "居民人均可支配收入中位数", "group": "中位数", "level": 4, "unit": "元"},
    "income_median_urban": {"name": "城镇居民人均可支配收入中位数", "group": "中位数", "level": 4, "unit": "元"},
    "income_median_rural": {"name": "农村居民人均可支配收入中位数", "group": "中位数", "level": 4, "unit": "元"},
    "consumption_expenditure": {"name": "居民人均消费支出", "group": "消费支出", "level": 5, "unit": "元"},
    "consumption_expenditure_urban": {"name": "城镇居民人均消费支出", "group": "消费支出", "level": 6, "unit": "元"},
    "consumption_expenditure_rural": {"name": "农村居民人均消费支出", "group": "消费支出", "level": 6, "unit": "元"},
    "consumption_food_tobacco_alcohol": {"name": "食品烟酒消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_clothing": {"name": "衣着消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_housing": {"name": "居住消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_household_services": {"name": "生活用品及服务消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_transport_communication": {"name": "交通通信消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_education_culture": {"name": "教育文化娱乐消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_healthcare": {"name": "医疗保健消费支出", "group": "消费类别", "level": 7, "unit": "元"},
    "consumption_other": {"name": "其他用品及服务消费支出", "group": "消费类别", "level": 7, "unit": "元"},
}


SOURCE_LABELS = {
    "工资性收入": "income_wage",
    "经营净收入": "income_business",
    "财产净收入": "income_property",
    "转移净收入": "income_transfer",
    "食品烟酒": "consumption_food_tobacco_alcohol",
    "衣着": "consumption_clothing",
    "居住": "consumption_housing",
    "生活用品及服务": "consumption_household_services",
    "交通通信": "consumption_transport_communication",
    "教育文化娱乐": "consumption_education_culture",
    "医疗保健": "consumption_healthcare",
    "其他用品及服务": "consumption_other",
    "其他用品和服务": "consumption_other",
}


SEED_LINKS = [
    ("2017年一季度居民收入和消费支出情况", "https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/201708/t20170821_1767979.html"),
    ("2023年居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202401/t20240116_1946622.html"),
    ("2024年一季度居民收入和消费支出情况", "https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202404/t20240416_1948580.html"),
    ("2024年上半年居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202407/t20240715_1955615.html"),
    ("2024年前三季度居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202410/t20241018_1957037.html"),
    ("2024年居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202501/t20250117_1958325.html"),
    ("2025年一季度居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202504/t20250416_1959322.html"),
    ("2025年上半年居民收入和消费支出情况", "https://www.stats.gov.cn/zwfwck/sjfb/202507/t20250715_1960406.html"),
    ("2025年前三季度居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfbhjd/202510/t20251020_1961604.html"),
    ("2025年居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202601/t20260119_1962321.html"),
    ("2026年一季度居民收入和消费支出情况", "https://www.stats.gov.cn/sj/zxfb/202604/t20260416_1963323.html"),
]


HISTORICAL_SUPPLEMENTS = [
    {
        "title": "2013年国民经济和社会发展统计公报",
        "url": "https://www.stats.gov.cn/sj/zxfb/202302/t20230203_1898221.html",
        "year": 2013,
        "month": 12,
        "period_label": "全年",
        "metrics": {
            "income_disposable": (18311, 10.9),
            "consumption_expenditure": (13220, None),
        },
    },
    {
        "title": "2014年国民经济在新常态下平稳运行",
        "url": "https://www.stats.gov.cn/sj/zxfb/202302/t20230203_1898674.html",
        "year": 2014,
        "month": 12,
        "period_label": "全年",
        "metrics": {
            "income_disposable": (20167, 10.1),
            "income_disposable_urban": (28844, None),
            "income_disposable_rural": (10489, None),
            "income_transfer": (3427, 12.6),
            "consumption_expenditure": (14491, 7.5),
        },
    },
    {
        "title": "居民收入快速增长 人民生活全面提高——十八大以来居民收入及生活状况",
        "url": "https://www.stats.gov.cn/sj/sjjd/202302/t20230202_1896995.html",
        "year": 2015,
        "month": 12,
        "period_label": "全年",
        "metrics": {
            "income_disposable": (21966, None),
            "income_disposable_urban": (31195, None),
            "income_disposable_rural": (11422, None),
            "income_wage": (12459, None),
            "income_business": (3956, None),
            "income_property": (1740, None),
            "income_transfer": (3812, None),
            "consumption_expenditure": (15712, None),
            "consumption_food_tobacco_alcohol": (4814, None),
        },
    },
    {
        "title": "一季度国民经济开局良好",
        "url": "https://www.stats.gov.cn/sj/zxfb/202302/t20230203_1899089.html",
        "year": 2016,
        "month": 3,
        "period_label": "一季度",
        "metrics": {
            "income_disposable": (6619, 8.7),
            "income_disposable_urban": (9255, 8.0),
            "income_disposable_rural": (3578, 9.1),
            "income_wage": (3757, 7.7),
            "income_business": (1207, 7.6),
            "income_property": (545, 13.6),
            "income_transfer": (1111, 11.3),
            "income_median": (5670, 8.7),
            "consumption_expenditure": (4454, None),
            "consumption_expenditure_urban": (5970, None),
            "consumption_expenditure_rural": (2703, None),
        },
    },
    {
        "title": "上半年国民经济运行总体平稳、稳中有进",
        "url": "https://www.stats.gov.cn/sj/xwfbh/fbhwd/202302/t20230203_1899187.html",
        "year": 2016,
        "month": 6,
        "period_label": "上半年",
        "metrics": {
            "income_disposable": (11886, 8.7),
            "income_disposable_urban": (16957, 8.0),
            "income_disposable_rural": (6050, 8.9),
            "income_wage": (6846, 8.5),
            "income_business": (1999, 8.2),
            "income_property": (963, 9.2),
            "income_transfer": (2078, 9.8),
            "income_median": (10505, 8.3),
            "consumption_expenditure": (8211, None),
            "consumption_expenditure_urban": (11185, None),
            "consumption_expenditure_rural": (4788, None),
        },
    },
    {
        "title": "王萍萍：前三季度全国居民收入和消费保持稳定增长",
        "url": "https://www.stats.gov.cn/sj/sjjd/202302/t20230202_1895783.html",
        "year": 2016,
        "month": 9,
        "period_label": "前三季度",
        "metrics": {
            "income_disposable": (17735, 8.4),
            "income_wage": (10128, 7.9),
            "income_business": (3029, 8.0),
            "income_property": (1421, 8.2),
            "income_transfer": (3158, 10.3),
            "consumption_expenditure": (12247, 8.5),
            "consumption_food_tobacco_alcohol": (3664, 7.7),
            "consumption_clothing": (867, 2.3),
        },
    },
    {
        "title": "2016年全国居民收入稳步增长 居民消费进一步改善",
        "url": "https://www.stats.gov.cn/sj/sjjd/202302/t20230202_1895814.html",
        "year": 2016,
        "month": 12,
        "period_label": "全年",
        "metrics": {
            "income_disposable": (23821, 8.4),
            "income_disposable_urban": (33616, 7.8),
            "income_disposable_rural": (12363, 8.2),
            "income_wage": (13455, 8.0),
            "income_business": (4218, 6.6),
            "income_property": (1889, 8.6),
            "income_transfer": (4259, 11.7),
            "consumption_expenditure": (17111, 8.9),
            "consumption_expenditure_urban": (23079, 7.9),
            "consumption_expenditure_rural": (10130, 9.8),
        },
    },
]


def fetch(url: str, body: bytes | None = None, referer: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if referer:
        headers["Referer"] = referer
    req = Request(url, data=body, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def post_json(url: str, params: dict) -> dict:
    body = urlencode(params).encode("utf-8")
    query = params.get("qt", "")
    raw = fetch(
        url,
        body=body,
        referer=f"https://www.stats.gov.cn/search/s?qt={quote(query)}",
    )
    return json.loads(raw.decode("utf-8", errors="replace"))


def clean_text(value: str) -> str:
    value = value.replace("\u3000", "").replace("\xa0", "").replace("\u2002", "")
    return re.sub(r"\s+", "", value)


def to_number(value: str):
    value = clean_text(value).replace(",", "").replace("下降", "-")
    if value in {"", "-", "--", "—"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def parse_growth(value: str):
    text = clean_text(value)
    nums = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", text.replace("下降", "-"))]
    if not nums:
        return None, None
    nominal = nums[0]
    real = nums[1] if "（" in text and len(nums) > 1 else None
    return nominal, real


def parse_period(title: str):
    match = re.match(r"(?P<year>\d{4})年(?P<label>一季度|上半年|前三季度)?居民收入和消费支出情况$", clean_text(title))
    if not match:
        return None
    year = int(match.group("year"))
    label = match.group("label") or "全年"
    month = {"一季度": 3, "上半年": 6, "前三季度": 9, "全年": 12}[label]
    quarter = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}[month]
    return {
        "year": year,
        "month": month,
        "quarter": quarter,
        "period": f"{year}-{month:02d}",
        "period_label": label,
    }


def infer_period(title: str, published_at: str | None):
    period = parse_period(title)
    if period or not published_at:
        return period
    label_match = re.match(r"(?P<label>一季度|上半年|前三季度)居民收入和消费支出情况$", clean_text(title))
    if not label_match:
        return None
    year_match = re.match(r"(?P<year>\d{4})", published_at)
    if not year_match:
        return None
    year = year_match.group("year")
    return parse_period(f"{year}年{label_match.group('label')}居民收入和消费支出情况")


def extract_links() -> list[dict]:
    links = {}
    labels = ("一季度", "上半年", "前三季度", "")

    def add_docs(docs: list[dict]) -> None:
        for doc in docs:
            data = doc.get("data") or {}
            title = data.get("titleO") or clean_text(data.get("title", ""))
            title = re.sub(r"<[^>]+>", "", title)
            if "解读" in title or "说明" in title:
                continue
            period = parse_period(title)
            if not period:
                continue
            url = data.get("url") or data.get("myValues", {}).get("URL")
            if not url or "www.stats.gov.cn/" not in url:
                continue
            if "/sjjd/" in url or "/zxfbhjd/" in url:
                continue
            if (
                "/sj/zxfb/" not in url
                and "/xxgk/sjfb/zxfb2020/" not in url
                and "/zwfwck/sjfb/" not in url
            ):
                continue
            links[url] = {"url": url, "title": title, **period}

    for year in range(2010, 2027):
        for page in range(1, 13):
            payload = post_json(
                SEARCH_API,
                {
                    "siteCode": "bm36000002",
                    "tab": "",
                    "qt": "居民收入和消费支出情况",
                    "page": page,
                    "pageSize": 20,
                    "sort": "relevance",
                    "adv": 1,
                    "timeOption": 2,
                    "startDateStr": f"{year}-01-01",
                    "endDateStr": f"{year}-12-31",
                },
            )
            docs = payload.get("resultDocs") or []
            if not docs:
                break
            add_docs(docs)
            if page * 20 >= (payload.get("totalHits") or 0):
                break
            time.sleep(0.03)

        for label in labels:
            query = f"{year}年{label}居民收入和消费支出情况"
            for page in range(1, 8):
                payload = post_json(
                    SEARCH_API,
                    {
                        "siteCode": "bm36000002",
                        "tab": "",
                        "qt": query,
                        "page": page,
                        "pageSize": 20,
                        "sort": "relevance",
                        "adv": 1,
                        "timeOption": 2,
                        "startDateStr": f"{year}-01-01",
                        "endDateStr": f"{year}-12-31",
                    },
                )
                docs = payload.get("resultDocs") or []
                if not docs:
                    break
                add_docs(docs)
                if page * 20 >= (payload.get("totalHits") or 0):
                    break
                time.sleep(0.03)

    for title, url in SEED_LINKS:
        period = parse_period(title)
        if period:
            links[url] = {"url": url, "title": title, **period}
    return sorted(links.values(), key=lambda x: x["period"])


def parse_pub_date(doc):
    node = doc.xpath("//meta[@name='PubDate']/@content")
    return node[0] if node else None


def row_cells(row) -> list[str]:
    cells = [clean_text("".join(cell.itertext())) for cell in row.xpath("./th|./td")]
    return [cell for cell in cells if cell]


def row_to_metric(label: str, current_block: str | None) -> tuple[str | None, str | None]:
    label = label.replace("（一）", "").replace("（二）", "").replace("（三）", "")
    label = label.replace("（四）", "").replace("（五）", "").replace("（六）", "")
    if label == "全国居民人均可支配收入":
        return "income_disposable", "income_disposable"
    if label == "全国居民人均可支配收入中位数":
        return "income_median", "income_median"
    if label == "全国居民人均消费支出":
        return "consumption_expenditure", "consumption_expenditure"
    if label == "城镇居民":
        if current_block == "income_disposable":
            return "income_disposable_urban", current_block
        if current_block == "income_median":
            return "income_median_urban", current_block
        if current_block == "consumption_expenditure":
            return "consumption_expenditure_urban", current_block
    if label == "农村居民":
        if current_block == "income_disposable":
            return "income_disposable_rural", current_block
        if current_block == "income_median":
            return "income_median_rural", current_block
        if current_block == "consumption_expenditure":
            return "consumption_expenditure_rural", current_block
    return SOURCE_LABELS.get(label), current_block


def article_from_doc(item: dict, doc) -> dict | None:
    tables = doc.xpath("//table[.//*[contains(normalize-space(.), '全国居民收支主要数据')]]")
    if not tables:
        tables = doc.xpath("//table[.//*[contains(normalize-space(.), '全国居民人均可支配收入')]]")
    if not tables:
        return None

    metrics = {}
    current_block = None
    for row in tables[0].xpath(".//tr"):
        cells = row_cells(row)
        if len(cells) < 3:
            continue
        label = cells[0]
        if label.startswith("按"):
            continue
        key, current_block = row_to_metric(label, current_block)
        if not key:
            continue
        value = to_number(cells[1])
        yoy, real_yoy = parse_growth(cells[2])
        if value is None and yoy is None:
            continue
        metrics[key] = {
            "month_value": value,
            "month_yoy": yoy,
            "latest_month_value": value,
            "latest_month_yoy": yoy,
            "published_month_value": value,
            "published_month_yoy": yoy,
        }
        if real_yoy is not None:
            metrics[key]["real_yoy"] = real_yoy

    if not metrics:
        return None
    published_at = parse_pub_date(doc)
    return {
        **item,
        "published_at": published_at,
        "source": "国家统计局数据发布",
        "metrics": metrics,
    }


def parse_article(item: dict) -> dict | None:
    filename = re.sub(r"[^0-9A-Za-z_.-]+", "_", item["url"].split("/")[-1])
    path = WORK_DIR / filename
    if not path.exists():
        path.write_bytes(fetch(item["url"]))
        time.sleep(0.05)
    doc = html.fromstring(path.read_bytes())
    return article_from_doc(item, doc)


def parse_cached_article(path: Path) -> dict | None:
    doc = html.fromstring(path.read_bytes())
    title = clean_text(doc.xpath("string((//h1|//h2)[1])") or doc.xpath("string(//title)") or "")
    published_at = parse_pub_date(doc)
    period = infer_period(title, published_at)
    if not period:
        return None
    item = {
        "url": f"cache:{path.name}",
        "title": title,
        **period,
    }
    return article_from_doc(item, doc)


def historical_article(item: dict) -> dict:
    metrics = {}
    for key, (value, yoy) in item["metrics"].items():
        metrics[key] = {
            "month_value": float(value),
            "latest_month_value": float(value),
            "published_month_value": float(value),
        }
        if yoy is not None:
            metrics[key]["month_yoy"] = float(yoy)
            metrics[key]["latest_month_yoy"] = float(yoy)
            metrics[key]["published_month_yoy"] = float(yoy)

    month = item["month"]
    quarter = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}[month]
    period = f"{item['year']}-{month:02d}"
    return {
        "url": item["url"],
        "title": item["title"],
        "year": item["year"],
        "month": month,
        "quarter": quarter,
        "period": period,
        "period_label": item["period_label"],
        "published_at": None,
        "source": "国家统计局历史补充",
        "historical_supplement": True,
        "coverage_note": "非完整发布稿口径；仅补入官方页面可核验的指标，缺项留空。",
        "metrics": metrics,
    }


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    links = extract_links()
    records_by_period = {}
    for link in links:
        article = parse_article(link)
        if article:
            records_by_period[article["period"]] = article
    for path in sorted(WORK_DIR.glob("*.html")):
        article = parse_cached_article(path)
        if article and article["period"] not in records_by_period:
            records_by_period[article["period"]] = article
    for item in HISTORICAL_SUPPLEMENTS:
        article = historical_article(item)
        if article["period"] not in records_by_period:
            records_by_period[article["period"]] = article
    records = sorted(records_by_period.values(), key=lambda record: record["period"])

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "section": "income",
        "section_name": "居民收入和支出",
        "frequency": "quarter",
        "coverage_note": "2017年一季度起为完整居民收入和消费支出发布稿口径；2013-2016年为历史补充层，仅补入官方页面可核验的指标，缺项留空。",
        "sources": [
            "https://www.stats.gov.cn/sj/zxfb/",
            LATEST_RELEASE_URL,
        ],
        "series": SERIES_META,
        "records": records,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    first = payload["records"][0]["period"] if payload["records"] else "n/a"
    last = payload["records"][-1]["period"] if payload["records"] else "n/a"
    print(f"Wrote {len(payload['records'])} income records; coverage {first} to {last}.")


if __name__ == "__main__":
    main()
