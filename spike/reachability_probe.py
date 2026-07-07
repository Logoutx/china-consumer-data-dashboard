from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import requests

# Phase 0 spike: is data.stats.gov.cn / www.stats.gov.cn / pbc.gov.cn / customs.gov.cn
# reachable from wherever this script runs? Meant to be run in two places and
# diffed by hand: (1) this Mac (currently on a China-egress connection) and
# (2) a plain GitHub Actions ubuntu-latest runner (US egress). See
# .github/workflows/spike-reachability.yml for the CI side.
#
# Every entry in PROBES is independent and wrapped so one failure (timeout,
# TLS error, WAF block, DNS failure) never stops the others -- we want one
# complete result row per URL no matter what each site does.

# Reuse the exact UA already proven to work against these NBS endpoints
# elsewhere in this repo (see tools/audit_official_data.py, tools/fetch_nbs_national_data.py)
# rather than inventing a new one for this spike.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TIMEOUT_SEC = 25
RETRIES = 2  # additional attempts after the first -> 3 tries total per URL
RETRY_BACKOFF_SEC = 2  # be polite to WAF-protected gov sites between retries

# Hand-picked 2026-07-07 from https://www.stats.gov.cn/sj/zxfb/ -- title:
# "2026年5月份居民消费价格同比上涨1.2%" (May 2026 CPI, +1.2% YoY). Verified locally:
# HTTP 200, contains "同比上涨1.2%" x7 and 96 <tr> rows, so it's a solid fixture
# for the parse test below.
NBS_CPI_ARTICLE_URL = "https://www.stats.gov.cn/sj/zxfb/202606/t20260610_1963923.html"

PROBES: list[dict[str, Any]] = [
    {
        "name": "nbs_release_listing",
        "url": "https://www.stats.gov.cn/sj/zxfb/",
    },
    {
        "name": "nbs_cpi_article",
        "url": NBS_CPI_ARTICLE_URL,
        "parse_check": True,
    },
    {
        "name": "nbs_easyquery_api",
        "url": (
            "https://data.stats.gov.cn/easyquery.htm?m=QueryData&dbcode=hgyd"
            "&rowcode=zb&colcode=sj&wds=%5B%5D&dfwds=%5B%7B%22wdcode%22%3A%22zb"
            "%22%2C%22valuecode%22%3A%22A01010101%22%7D%5D"
        ),
        "note": "Expected to fail: known WZWS anti-bot WAF returns 403 with reason=UrlACL.",
    },
    {
        "name": "pbc_survey_stats_dept",
        "url": "http://www.pbc.gov.cn/diaochatongjisi/116219/index.html",
        "note": "Verified live 2026-07-07: redirects http->https, 200, title '调查统计司'.",
    },
    {
        "name": "customs_monthly_report_en",
        "url": "https://english.customs.gov.cn/statics/report/monthly.html",
    },
    {
        "name": "chinadata_live_mirror",
        "url": "https://chinadata.live/api/v2/data/china-retail-sales",
    },
    {
        "name": "chinadata_live_cpi",
        "url": "https://chinadata.live/api/v2/data/china-cpi",
        "json_check": True,
        "note": (
            "Confirmed live 2026-07-08 from this Mac: 200, frequency=yearly, "
            "16 points 2010-2025. Slug guesses china-cpi-monthly/china-monthly-cpi/"
            "china-consumer-price-index/cpi all 404 -- china-cpi is the only hit."
        ),
    },
    {
        "name": "chinadata_live_cpi_monthly_slug_guess",
        "url": "https://chinadata.live/api/v2/data/china-cpi-monthly",
        "note": "Expected 404: no monthly-CPI slug found on chinadata.live as of 2026-07-08.",
    },
]

# Matches things like 同比上涨1.2% / 环比下降0.3% / 同比增长4.5%
CJK_PERCENT_PATTERN = re.compile(r"(?:同比|环比)(?:上涨|上升|下降|下跌|增长|回落)[0-9.]+%")
TABLE_ROW_PATTERN = re.compile(r"<tr[\s>]", re.IGNORECASE)


@dataclass
class ProbeResult:
    name: str
    url: str
    attempts_used: int
    status: int | None
    elapsed_sec: float | None
    final_url_after_redirects: str | None
    first_300_chars_of_body: str | None
    body_length: int | None
    error: str | None
    note: str | None = None
    parse_check: dict[str, Any] | None = None
    json_check: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items()}


def run_parse_check(body: str) -> dict[str, Any]:
    """Proves the response is real article content, not a WAF interstitial,
    by counting CJK percent-change phrases and <table> rows."""
    matches = CJK_PERCENT_PATTERN.findall(body)
    rows = TABLE_ROW_PATTERN.findall(body)
    return {
        "cjk_percent_matches": len(matches),
        "table_rows": len(rows),
        "sample_matches": matches[:5],
    }


def run_json_check(body: str) -> dict[str, Any]:
    """For chinadata.live-style JSON series: records the declared frequency
    and whether any data point's date string looks monthly (e.g. "2024-01",
    len > 4) rather than yearly-only ("2024", len == 4)."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return {"error": f"non-JSON response: {exc}"}
    data = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    points = data.get("data") or []
    dates = [point.get("date", "") for point in points if isinstance(point, dict)]
    return {
        "frequency_field": data.get("frequency"),
        "total_points": len(points),
        "date_range": data.get("meta", {}).get("dateRange") if isinstance(data.get("meta"), dict) else None,
        "sample_dates": dates[:3],
        "has_monthly_looking_date": any(len(d) > 4 for d in dates),
    }


# --- Phase 0b: DG portal (data.stats.gov.cn/dg/...) probes -----------------
# nbs_easyquery_api above (the OLD API) is known WAF-blocked everywhere. This
# section asks the same reachability question of the NEW portal's actual
# backend, which a China-egress vantage already showed is open and serves
# full history back to 1985. Endpoint shapes below are copied verbatim from
# tools/fetch_nbs_national_data.py (NationalDataClient), which already runs
# this successfully from China, plus one endpoint (queryIndexTreeAsync) found
# by reading the SPA's own lazy-loaded JS chunk
# (static/js/project/project_national_datatree.js, Vue component
# DsfNationalTree) -- that tool never calls it, but it is how the site's own
# indicator picker discovers GUIDs, and it turned out to need no browser
# session, just plain HTTP GETs.

DG_API_BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
DG_DATA_PAGE = "https://data.stats.gov.cn/dg/website/page.html#/pc/national/monthData"
# Two different "root" constants from tools/fetch_nbs_national_data.py, one level
# apart in the tree (confirmed by hand 2026-07-08, see probe_dg_tree_enumeration):
# DG_DATE_ROOT_ID ("月度数据"/monthly-data catalog) is the sole node returned by
# the tree endpoint's true root (pid omitted); DG_ROOT_ID ("国内贸易"/domestic
# trade) is one of DG_DATE_ROOT_ID's children, and is what the tool passes as
# `rootId` in the actual data-fetch call.
DG_DATE_ROOT_ID = "fc982599aa684be7969d7b90b1bd0e84"  # tool's DATE_ROOT_ID
DG_ROOT_ID = "3913ce1309d04eb1bdf7d7b622b1d07c"  # tool's ROOT_ID, aka "国内贸易"
DG_NATIONAL_AREA = "000000000000"

# retail_total series, copied from tools/fetch_nbs_national_data.py::OFFICIAL_SERIES
# so this probe exercises the exact indicator the real fetch tool depends on.
DG_RETAIL_TOTAL_CID = "d0cb882c7f27443ab6b3ef9421901961"
DG_RETAIL_TOTAL_VALUE_ID = "1142a3a03e9045959e606a21822641ac"

# 1985-01 is the earliest month the tool ever asks for (see month_codes(1985, ...)
# in the tool); 2026-05 is the latest period already in retail_release_archive.json
# as of 2026-07-08, so a truncated-history response would show up as a gap here
# rather than as "everything came back null".
DG_PROBE_MONTH_CODES = ["198501MM", "198512MM", "202605MM"]

DG_TREE_URL = f"{DG_API_BASE}/new/queryIndexTreeAsync"
# From tabsCode in project_national_datatree.js: monthData -> code "1". This is
# the same "monthData" section tools/fetch_nbs_national_data.py targets.
DG_MONTHLY_SECTION_CODE = "1"

# Other external/ paths spotted by name (not shape) while reading the SPA's
# lazy chunks (project_national_datapage.js). Recorded for completeness only --
# the enumeration verdict below does not depend on these resolving to anything.
DG_SIBLING_ENDPOINT_GUESSES = [
    "getAllProvince",
    "getDaCatalogTreeByIndicatorCid",
    "coll/getMyCollCatTree",
]

DG_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://data.stats.gov.cn/dg/website/page.html",
    "Accept": "application/json, text/plain, */*",
}


def probe_dg_data_api() -> dict[str, Any]:
    """Mirrors NationalDataClient.warm() + .indicator_values() in
    tools/fetch_nbs_national_data.py exactly: warm a session against the SPA
    shell (for the JSESSIONID cookie), then POST the same
    getEsDataByIndicatorIdAndDa call the real fetch tool makes for
    retail_total, asking for 1985-01 (earliest claimed history) alongside a
    known-recent month."""
    name = "dg_data_api_retail_total"
    session = requests.Session()
    session.headers.update(DG_HEADERS)

    warm_status: int | None = None
    warm_error: str | None = None
    try:
        warm_resp = session.get(DG_DATA_PAGE, timeout=TIMEOUT_SEC)
        warm_status = warm_resp.status_code
    except requests.exceptions.RequestException as exc:
        warm_error = f"{type(exc).__name__}: {exc}"
        print(f"[{name}] warm request failed (non-fatal, continuing): {warm_error}", file=sys.stderr)

    payload = {
        "cid": DG_RETAIL_TOTAL_CID,
        "id": DG_RETAIL_TOTAL_VALUE_ID,
        "da": DG_NATIONAL_AREA,
        "dt": "",
        "rootId": DG_ROOT_ID,
        "dts": DG_PROBE_MONTH_CODES,
    }

    last_exc: Exception | None = None
    attempt = 0
    while attempt <= RETRIES:
        attempt += 1
        start = time.monotonic()
        try:
            resp = session.post(
                f"{DG_API_BASE}/getEsDataByIndicatorIdAndDa",
                json=payload,
                timeout=TIMEOUT_SEC,
            )
        except requests.exceptions.RequestException as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            print(
                f"[{name}] attempt {attempt}/{RETRIES + 1} failed after {elapsed:.1f}s: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if attempt <= RETRIES:
                time.sleep(RETRY_BACKOFF_SEC)
            continue

        elapsed = time.monotonic() - start
        # Force UTF-8 explicitly -- same mojibake risk as the HTML probes above;
        # this endpoint's JSON is UTF-8 but the Content-Type header omits charset.
        resp.encoding = "utf-8"
        body = resp.text or ""
        print(
            f"[{name}] attempt {attempt}/{RETRIES + 1} -> HTTP {resp.status_code} "
            f"in {elapsed:.2f}s ({len(body)} chars)",
            file=sys.stderr,
        )

        result: dict[str, Any] = {
            "name": name,
            "warm_status": warm_status,
            "warm_error": warm_error,
            "requested_dt_codes": DG_PROBE_MONTH_CODES,
            "attempts_used": attempt,
            "status": resp.status_code,
            "elapsed_sec": round(elapsed, 3),
            "first_300_chars_of_body": body[:300],
            "error": None,
        }

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            result["error"] = f"non-JSON response: {exc}"
            result["api_success_field"] = None
            result["has_1985_datapoint"] = False
            result["values_by_dt"] = {}
            return result

        values_by_dt = {item["dt"]: item.get("v") for item in (data.get("data") or []) if "dt" in item}
        result["api_success_field"] = data.get("success")
        result["api_message"] = data.get("message")
        result["values_by_dt"] = values_by_dt
        result["has_1985_datapoint"] = values_by_dt.get("198501MM") not in (None, "")
        return result

    return {
        "name": name,
        "warm_status": warm_status,
        "warm_error": warm_error,
        "requested_dt_codes": DG_PROBE_MONTH_CODES,
        "attempts_used": attempt,
        "status": None,
        "elapsed_sec": None,
        "first_300_chars_of_body": None,
        "error": f"{type(last_exc).__name__}: {last_exc}",
        "api_success_field": None,
        "has_1985_datapoint": False,
        "values_by_dt": {},
    }


def _dg_tree_request(session: requests.Session, *, pid: str | None, code: str = DG_MONTHLY_SECTION_CODE) -> dict[str, Any]:
    params = {"code": code}
    if pid:
        params["pid"] = pid
    try:
        resp = session.get(DG_TREE_URL, params=params, timeout=TIMEOUT_SEC)
    except requests.exceptions.RequestException as exc:
        return {"status": None, "error": f"{type(exc).__name__}: {exc}", "nodes": []}
    resp.encoding = "utf-8"
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        return {"status": resp.status_code, "error": f"non-JSON response: {exc}", "nodes": []}
    return {
        "status": resp.status_code,
        "error": None,
        "success_field": data.get("success"),
        "nodes": data.get("data") or [],
    }


def probe_dg_tree_enumeration() -> dict[str, Any]:
    """Answers: are indicator lists/GUIDs programmatically enumerable, or does
    harvesting them require a real browser session?

    Reading the SPA's own lazy-loaded JS chunk showed the indicator picker
    tree is populated by recursive GETs to
    new/queryIndexTreeAsync?code=<section>&pid=<parent _id, omitted at root>.
    Confirmed by hand 2026-07-08 that this is a 3-level walk down to a known
    indicator, not 2: the true root (pid omitted) returns exactly one node,
    DG_DATE_ROOT_ID ("月度数据"); expanding *that* returns ~14 catalogs
    including DG_ROOT_ID ("国内贸易"); expanding DG_ROOT_ID in turn returns
    DG_RETAIL_TOTAL_CID ("社会消费品零售总额") among its children. If all three
    known IDs line up, the whole indicator catalog is walkable with nothing
    but plain HTTP GETs -- no login/session harvesting needed."""
    name = "dg_tree_enumeration"
    session = requests.Session()
    session.headers.update(DG_HEADERS)

    root = _dg_tree_request(session, pid=None)
    root_ids = {node.get("_id") for node in root["nodes"]}
    found_date_root = DG_DATE_ROOT_ID in root_ids

    level1: dict[str, Any] = {
        "status": None,
        "error": "skipped: DG_DATE_ROOT_ID not found at true root",
        "nodes": [],
    }
    found_known_rootid = False
    if found_date_root:
        level1 = _dg_tree_request(session, pid=DG_DATE_ROOT_ID)
        level1_ids = {node.get("_id") for node in level1["nodes"]}
        found_known_rootid = DG_ROOT_ID in level1_ids

    level2: dict[str, Any] = {
        "status": None,
        "error": "skipped: DG_ROOT_ID not found one level down",
        "nodes": [],
    }
    found_known_indicator_cid = False
    if found_known_rootid:
        level2 = _dg_tree_request(session, pid=DG_ROOT_ID)
        level2_ids = {node.get("_id") for node in level2["nodes"]}
        found_known_indicator_cid = DG_RETAIL_TOTAL_CID in level2_ids

    sibling_checks = []
    for path in DG_SIBLING_ENDPOINT_GUESSES:
        url = f"{DG_API_BASE}/{path}"
        try:
            resp = session.get(url, timeout=TIMEOUT_SEC)
            resp.encoding = "utf-8"
            sibling_checks.append(
                {"path": path, "status": resp.status_code, "first_120_chars": resp.text[:120], "error": None}
            )
        except requests.exceptions.RequestException as exc:
            sibling_checks.append(
                {"path": path, "status": None, "first_120_chars": None, "error": f"{type(exc).__name__}: {exc}"}
            )

    enumerable = found_date_root and found_known_rootid and found_known_indicator_cid
    return {
        "name": name,
        "tree_endpoint": DG_TREE_URL,
        "root_status": root["status"],
        "root_error": root["error"],
        "root_node_count": len(root["nodes"]),
        "root_sample_names": [n.get("name") or n.get("_name") for n in root["nodes"][:5]],
        "found_known_date_root_id_at_root": found_date_root,
        "level1_status": level1["status"],
        "level1_error": level1["error"],
        "level1_node_count": len(level1["nodes"]),
        "level1_sample_names": [n.get("name") or n.get("_name") for n in level1["nodes"][:5]],
        "found_known_rootid_in_level1": found_known_rootid,
        "level2_status": level2["status"],
        "level2_error": level2["error"],
        "level2_node_count": len(level2["nodes"]),
        "found_known_retail_total_cid_in_level2": found_known_indicator_cid,
        "sibling_endpoint_guesses": sibling_checks,
        "verdict": (
            "Indicator tree IS programmatically enumerable via plain HTTP GET, no "
            "browser session needed: walked root -> DG_DATE_ROOT_ID -> DG_ROOT_ID -> "
            "found retail_total's own cid three levels down."
            if enumerable
            else "Could not confirm programmatic enumeration this run; see root/level1/level2 status and error fields above."
        ),
    }


def probe_one(entry: dict[str, Any]) -> ProbeResult:
    name = entry["name"]
    url = entry["url"]
    note = entry.get("note")
    wants_parse_check = entry.get("parse_check", False)
    wants_json_check = entry.get("json_check", False)

    last_exc: Exception | None = None
    attempt = 0
    while attempt <= RETRIES:
        attempt += 1
        start = time.monotonic()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SEC, allow_redirects=True)
        except requests.exceptions.RequestException as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            print(
                f"[{name}] attempt {attempt}/{RETRIES + 1} failed after {elapsed:.1f}s: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if attempt <= RETRIES:
                time.sleep(RETRY_BACKOFF_SEC)
            continue
        except Exception as exc:  # belt and suspenders: never let one probe kill the run
            elapsed = time.monotonic() - start
            last_exc = exc
            print(
                f"[{name}] attempt {attempt}/{RETRIES + 1} raised unexpected "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if attempt <= RETRIES:
                time.sleep(RETRY_BACKOFF_SEC)
            continue

        elapsed = time.monotonic() - start
        # These sites all declare UTF-8 internally (HTML <meta charset="UTF-8">,
        # or JSON which is UTF-8 by spec) but several omit charset from the HTTP
        # Content-Type header, which makes requests fall back to a wrong guess
        # (observed: mojibake, e.g. "同比上涨1.2%" decoded as "åæ¯ä¸æ¶¨1.2%").
        # Force UTF-8 explicitly so first_300_chars_of_body and the parse check
        # below see real text instead of garbage.
        resp.encoding = "utf-8"
        body = resp.text or ""
        print(
            f"[{name}] attempt {attempt}/{RETRIES + 1} -> HTTP {resp.status_code} "
            f"in {elapsed:.2f}s ({len(body)} chars)",
            file=sys.stderr,
        )

        parse_result = None
        if wants_parse_check:
            parse_result = run_parse_check(body)
            print(
                f"[{name}] parse check: {parse_result['cjk_percent_matches']} CJK percent-change "
                f"phrase(s), {parse_result['table_rows']} <tr> row(s)",
                file=sys.stderr,
            )

        json_result = None
        if wants_json_check:
            json_result = run_json_check(body)
            print(f"[{name}] json check: {json_result}", file=sys.stderr)

        return ProbeResult(
            name=name,
            url=url,
            attempts_used=attempt,
            status=resp.status_code,
            elapsed_sec=round(elapsed, 3),
            final_url_after_redirects=resp.url,
            first_300_chars_of_body=body[:300],
            body_length=len(body),
            error=None,
            note=note,
            parse_check=parse_result,
            json_check=json_result,
        )

    # Every attempt failed with an exception (timeout, connection error, TLS error, ...).
    return ProbeResult(
        name=name,
        url=url,
        attempts_used=attempt,
        status=None,
        elapsed_sec=None,
        final_url_after_redirects=None,
        first_300_chars_of_body=None,
        body_length=None,
        error=f"{type(last_exc).__name__}: {last_exc}",
        note=note,
        parse_check=(
            {"cjk_percent_matches": 0, "table_rows": 0, "sample_matches": [], "error": "fetch failed"}
            if wants_parse_check
            else None
        ),
        json_check=({"error": "fetch failed"} if wants_json_check else None),
    )


def main() -> int:
    results: list[ProbeResult] = []
    for entry in PROBES:
        print(f"probing {entry['name']}: {entry['url']}", file=sys.stderr)
        try:
            results.append(probe_one(entry))
        except Exception as exc:  # a probe must never take the whole run down with it
            print(f"[{entry['name']}] probe_one itself crashed: {exc}", file=sys.stderr)
            results.append(
                ProbeResult(
                    name=entry["name"],
                    url=entry["url"],
                    attempts_used=0,
                    status=None,
                    elapsed_sec=None,
                    final_url_after_redirects=None,
                    first_300_chars_of_body=None,
                    body_length=None,
                    error=f"probe_one crashed: {exc}",
                )
            )

    print("\n===== PROBE RESULTS (JSON) =====")
    print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))

    print(f"probing {DG_API_BASE}/getEsDataByIndicatorIdAndDa (retail_total, 1985 check)", file=sys.stderr)
    try:
        dg_data_result = probe_dg_data_api()
    except Exception as exc:  # this probe must never take the whole run down with it
        print(f"[dg_data_api_retail_total] crashed: {exc}", file=sys.stderr)
        dg_data_result = {"name": "dg_data_api_retail_total", "error": f"crashed: {exc}"}
    print("\n===== DG DATA API PROBE (JSON) =====")
    print(json.dumps(dg_data_result, indent=2, ensure_ascii=False))

    print(f"probing {DG_TREE_URL} (indicator-tree enumeration)", file=sys.stderr)
    try:
        dg_tree_result = probe_dg_tree_enumeration()
    except Exception as exc:  # ditto
        print(f"[dg_tree_enumeration] crashed: {exc}", file=sys.stderr)
        dg_tree_result = {"name": "dg_tree_enumeration", "error": f"crashed: {exc}"}
    print("\n===== DG TREE ENUMERATION PROBE (JSON) =====")
    print(json.dumps(dg_tree_result, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
