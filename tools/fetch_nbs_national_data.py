from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from http.cookiejar import CookieJar


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "retail_release_archive.json"
DATA_PAGE = "https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData"
API_BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
ROOT_ID = "3913ce1309d04eb1bdf7d7b622b1d07c"
DATE_ROOT_ID = "fc982599aa684be7969d7b90b1bd0e84"
NATIONAL_AREA = "000000000000"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


OFFICIAL_SERIES = {
    "retail_total": {
        "cid": "d0cb882c7f27443ab6b3ef9421901961",
        "value": "1142a3a03e9045959e606a21822641ac",
        "yoy": "aaac57d54d2e465d91bc9f3ea1a8618e",
    },
    "urban": {
        "cid": "d5c7d1062a5742c69a02c39650c7c327",
        "value": "9ef40e1bd70e4fd1a94005ef9a3b9e6a",
        "yoy": "0a131939174d4d21885d3ce53cbe147f",
    },
    "rural": {
        "cid": "d5c7d1062a5742c69a02c39650c7c327",
        "value": "f32d705cc284404e82849c934011d6b0",
        "yoy": "dd474a9e7b7745fba458e648f1f013f6",
    },
    "catering": {
        "cid": "d9821f4ad1ec42ebbbd0554efb3e3772",
        "value": "446765807521445c8bbe7b7526501dc8",
        "yoy": "476cfe584e9849c2a2bac63a2fe1dd49",
    },
    "goods": {
        "cid": "d9821f4ad1ec42ebbbd0554efb3e3772",
        "value": "2d3e611af5214aa480b8a0a4f2c1785d",
        "yoy": "d76706323b3743da8b198c7f7d8c6a1c",
    },
    "above_quota_total": {
        "cid": "d0cb882c7f27443ab6b3ef9421901961",
        "value": "97281ec401c14706a7509672902106af",
        "yoy": "e576b095205e414c8a21e112792492ba",
    },
    "above_quota_catering": {
        "cid": "d9821f4ad1ec42ebbbd0554efb3e3772",
        "value": "24b382aea8224070a3562d8892e9c6d1",
        "yoy": "4c08c0eb48e0472ab2044c359e0d9a96",
    },
    "above_quota_goods": {
        "cid": "d9821f4ad1ec42ebbbd0554efb3e3772",
        "value": "55c1e5ef6a674368b5eb0d322726ff2d",
        "yoy": "75aa5bd0cba0413b86fdcb377cbba1fc",
    },
    "online_goods": {
        "cid": "ce144b3caaf9498aabcc713e671c1f33",
        "cumulative_value": "000aa31f692c4f1796dc4cbcefa6133d",
    },
}


def month_codes(start_year: int, end: str) -> list[str]:
    end_year, end_month = [int(part) for part in end.split("-")]
    codes = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            if year == end_year and month > end_month:
                break
            codes.append(f"{year}{month:02d}MM")
    return codes


def chunks(values: list[str], size: int = 36):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def to_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


class NationalDataClient:
    def __init__(self):
        jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(jar))

    def request(self, url: str, body: bytes | None = None) -> bytes:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://data.stats.gov.cn/dg/website/page.html",
        }
        if body is not None:
            headers["Content-Type"] = "application/json;charset=UTF-8"
        req = Request(url, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308) or "Location" not in error.headers:
                raise
            redirected = Request(urljoin(url, error.headers["Location"]), data=body, headers=headers)
            with self.opener.open(redirected, timeout=30) as response:
                return response.read()

    def warm(self) -> None:
        self.request(DATA_PAGE)

    def latest_period(self, fallback: str) -> str:
        url = f"{API_BASE}/new/queryDtByCid?cid=d0cb882c7f27443ab6b3ef9421901961&rootId={DATE_ROOT_ID}"
        try:
            data = json.loads(self.request(url).decode("utf-8"))
        except json.JSONDecodeError:
            return fallback
        dt_all = data["data"]["dt_all"]
        match = re.search(r"\d{6}", dt_all)
        if not match:
            return fallback
        dt = match.group(0)
        return f"{dt[:4]}-{dt[4:6]}"

    def indicator_values(self, cid: str, indicator_id: str, codes: list[str]) -> dict[str, float | None]:
        url = f"{API_BASE}/getEsDataByIndicatorIdAndDa"
        values = {}
        for group in chunks(codes):
            payload = {
                "cid": cid,
                "id": indicator_id,
                "da": NATIONAL_AREA,
                "dt": "",
                "rootId": ROOT_ID,
                "dts": group,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            data = None
            for attempt in range(3):
                raw = self.request(url, body).decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                    break
                except json.JSONDecodeError:
                    if attempt == 2:
                        snippet = raw[:120].replace("\n", " ")
                        raise RuntimeError(f"国家数据接口返回非 JSON：{indicator_id} {group[0]}-{group[-1]} {snippet}")
                    time.sleep(0.8 + attempt * 0.8)
            if not data.get("success"):
                raise RuntimeError(data.get("message") or "国家数据接口返回失败")
            for item in data.get("data") or []:
                dt = item["dt"].replace("MM", "")
                values[f"{dt[:4]}-{dt[4:6]}"] = to_number(item.get("v"))
            time.sleep(0.05)
        return values


def cumulative_to_period_values(values: dict[str, float | None]) -> dict[str, float | None]:
    result = {}
    for period in sorted(values):
        value = values[period]
        if value is None:
            result[period] = None
            continue
        year, month = [int(part) for part in period.split("-")]
        previous = values.get(f"{year}-{month - 1:02d}") if month > 1 else None
        result[period] = round(value - previous, 1) if previous is not None else value
    return result


def computed_yoy(values: dict[str, float | None]) -> dict[str, float | None]:
    result = {}
    for period, value in values.items():
        year, month = [int(part) for part in period.split("-")]
        previous = values.get(f"{year - 1}-{month:02d}")
        result[period] = round((value / previous - 1) * 100, 1) if value is not None and previous else None
    return result


def derive_month_values_from_ytd(records: dict[str, dict], series_id: str, version: str) -> None:
    month_key = f"{version}_month_value"
    ytd_key = f"{version}_ytd_value"
    yoy_key = f"{version}_month_yoy"

    for period in sorted(records):
        record = records[period]
        metric = record.get("metrics", {}).get(series_id)
        if not metric or metric.get(month_key) is not None or metric.get(ytd_key) is None:
            continue
        year, month = record["year"], record["month"]
        if month == 1:
            metric[month_key] = metric[ytd_key]
            continue
        previous = records.get(f"{year}-{month - 1:02d}", {}).get("metrics", {}).get(series_id, {}).get(ytd_key)
        if previous is not None:
            metric[month_key] = round(metric[ytd_key] - previous, 1)

    values = {
        period: record.get("metrics", {}).get(series_id, {}).get(month_key)
        for period, record in records.items()
    }
    for period, yoy in computed_yoy(values).items():
        metric = records[period].get("metrics", {}).get(series_id)
        if metric and metric.get(yoy_key) is None and yoy is not None:
            metric[yoy_key] = yoy


def add_metric(records: dict[str, dict], series_id: str, values: dict, yoys: dict) -> None:
    for period in sorted(set(values) | set(yoys)):
        value = values.get(period)
        yoy = yoys.get(period)
        if value is None and yoy is None:
            continue
        year, month = [int(part) for part in period.split("-")]
        record = records.setdefault(
            period,
            {
                "year": year,
                "month": month,
                "period": period,
                "title": f"国家数据 {period}",
                "url": DATA_PAGE,
                "published_at": None,
                "source": "国家统计局国家数据",
                "metrics": {},
            },
        )
        metric = record["metrics"].setdefault(series_id, {})
        metric["latest_month_value"] = value
        metric["latest_month_yoy"] = yoy
        metric["month_value"] = value
        metric["month_yoy"] = yoy


def ensure_version_fields(records: dict[str, dict]) -> None:
    for record in records.values():
        is_release = record.get("source") == "国家统计局数据发布"
        for metric in record.get("metrics", {}).values():
            if "latest_month_value" not in metric:
                metric["latest_month_value"] = metric.get("month_value")
            if "latest_month_yoy" not in metric:
                metric["latest_month_yoy"] = metric.get("month_yoy")
            if is_release and "published_month_value" not in metric:
                metric["published_month_value"] = metric.get("month_value")
            if is_release and "published_month_yoy" not in metric:
                metric["published_month_yoy"] = metric.get("month_yoy")
            if is_release and "published_ytd_value" not in metric:
                metric["published_ytd_value"] = metric.get("ytd_value")
            if is_release and "published_ytd_yoy" not in metric:
                metric["published_ytd_yoy"] = metric.get("ytd_yoy")


def add_derived(records: dict[str, dict]) -> None:
    for version in ("latest", "published"):
        derive_month_values_from_ytd(records, "online_goods", version)

    for period, record in records.items():
        metrics = record["metrics"]
        total = metrics.get("retail_total", {}).get("latest_month_value")
        ex_auto = metrics.get("retail_ex_auto", {}).get("latest_month_value")
        if total is not None and ex_auto is not None:
            metric = metrics.setdefault("auto_total", {})
            metric["latest_month_value"] = round(total - ex_auto, 1)
            metric["month_value"] = metric["latest_month_value"]

        online = metrics.get("online_goods", {}).get("latest_month_value")
        if online is not None and ex_auto not in (None, 0):
            metric = metrics.setdefault("online_ex_auto_share", {})
            metric["latest_month_value"] = round(online / ex_auto * 100, 2)
            metric["month_value"] = metric["latest_month_value"]

        published_online = metrics.get("online_goods", {}).get("published_month_value")
        published_ex_auto = metrics.get("retail_ex_auto", {}).get("published_month_value")
        if published_online is not None and published_ex_auto not in (None, 0):
            metrics.setdefault("online_ex_auto_share", {})["published_month_value"] = round(
                published_online / published_ex_auto * 100,
                2,
            )

    for series_id in ("auto_total", "online_ex_auto_share"):
        for version in ("latest", "published"):
            values = {
                period: record["metrics"].get(series_id, {}).get(f"{version}_month_value")
                for period, record in records.items()
            }
            yoys = computed_yoy(values)
            for period, yoy in yoys.items():
                if yoy is None or series_id not in records[period]["metrics"]:
                    continue
                records[period]["metrics"][series_id][f"{version}_month_yoy"] = yoy
                if version == "latest":
                    records[period]["metrics"][series_id]["month_yoy"] = yoy


def main() -> None:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    records = {record["period"]: record for record in payload["records"]}
    ensure_version_fields(records)
    fallback_latest = max(records)

    client = NationalDataClient()
    client.warm()
    latest = client.latest_period(fallback_latest)
    codes = month_codes(1985, latest)

    for series_id, config in OFFICIAL_SERIES.items():
        if "cumulative_value" in config:
            cumulative = client.indicator_values(config["cid"], config["cumulative_value"], codes)
            values = cumulative_to_period_values(cumulative)
            yoys = computed_yoy(values)
        else:
            values = client.indicator_values(config["cid"], config["value"], codes)
            yoys = client.indicator_values(config["cid"], config["yoy"], codes)
        add_metric(records, series_id, values, yoys)

    add_derived(records)
    payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    payload["coverage_note"] = (
        "新版国家数据接口已补入可取得的当期月度序列：社会消费品零售总额最早为 1985-01；"
        "除汽车以外、汽车拆分仍以发布稿可解析范围为准；网上商品零售额当期值由官方累计值按月差推导。"
    )
    payload["sources"] = sorted(
        set(payload.get("sources", []))
        | {
            DATA_PAGE,
            f"{API_BASE}/getEsDataByIndicatorIdAndDa",
        }
    )
    payload["records"] = sorted(records.values(), key=lambda record: record["period"])
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    first_total = next(
        record["period"]
        for record in payload["records"]
        if record["metrics"].get("retail_total", {}).get("month_value") is not None
    )
    print(f"Wrote {len(payload['records'])} records; retail_total starts at {first_total}, latest {latest}.")


if __name__ == "__main__":
    main()
