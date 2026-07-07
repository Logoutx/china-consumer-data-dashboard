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


def probe_one(entry: dict[str, Any]) -> ProbeResult:
    name = entry["name"]
    url = entry["url"]
    note = entry.get("note")
    wants_parse_check = entry.get("parse_check", False)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
