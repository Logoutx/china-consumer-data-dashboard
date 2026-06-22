from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "_cache"
DEFAULT_OUTPUT_DIR = ROOT / "audit_reports"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

RELEASE_DATASETS = [
    ("retail", ROOT / "retail_release_archive.json"),
    ("income", ROOT / "income_release_archive.json"),
    ("property", ROOT / "property_release_archive.json"),
]

OFFICIAL_PAGE_VALUE_KEYS = [
    "published_month_value",
    "published_month_yoy",
    "published_ytd_value",
    "published_ytd_yoy",
    "real_yoy",
]

DERIVED_RATIO_SERIES = {"online_ex_auto_share"}
DIRECT_RELEASE_CHECK_NOTE = "same-source hard check: published fields are checked against the official release page text"
RETAIL_DIFF_CHECK_NOTE = (
    "diagnostic only: this check is limited to amount series; cumulative-difference checks can diverge when "
    "official tables revise prior cumulative values or report rounded current-month values"
)

CITY_RAW_ROWS = {
    ("new_home_price", "month_value"): "新建商品住宅销售价格指数 (上月=100)",
    ("new_home_price", "month_yoy"): "新建商品住宅销售价格指数 (上年同月=100)",
    ("resale_home_price", "month_value"): "二手住宅销售价格指数 (上月=100)",
    ("resale_home_price", "month_yoy"): "二手住宅销售价格指数 (上年同月=100)",
}


@dataclass
class AuditResult:
    check: str
    status: str
    dataset: str
    period: str
    series: str
    field: str
    expected: float | int | str | None = None
    observed: float | int | str | None = None
    tolerance: float | None = None
    source: str | None = None
    evidence: str | None = None
    note: str | None = None
    rule: str | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value is not None}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def numeric(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if cleaned in {"", "-", "--", "—"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def tolerance_for(value: float | int | None) -> float:
    if value is None:
        return 0.0
    value = abs(float(value))
    if value < 10:
        return 0.03
    if value < 100:
        return 0.08
    if value < 1000:
        return 0.2
    return max(1.0, value * 0.0008)


def close_enough(expected, observed, tolerance: float | None = None) -> bool:
    expected_number = numeric(expected)
    observed_number = numeric(observed)
    if expected_number is None or observed_number is None:
        return expected == observed
    tolerance = tolerance_for(expected_number) if tolerance is None else tolerance
    return abs(expected_number - observed_number) <= tolerance


def strip_html(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("\u3000", "").replace("\xa0", ""))


def format_number_candidates(value: float) -> list[str]:
    candidates = {
        f"{value:g}",
        f"{value:.1f}".rstrip("0").rstrip("."),
        f"{value:.2f}".rstrip("0").rstrip("."),
    }
    if abs(value - round(value)) < 0.000001:
        candidates.add(str(int(round(value))))
    if abs(value) >= 1000:
        candidates.add(str(int(round(value))))
    if value < 0:
        abs_value = abs(value)
        candidates.update(
            {
                f"下降{abs_value:g}",
                f"下降{abs_value:.1f}".rstrip("0").rstrip("."),
                f"负{abs_value:g}",
            }
        )
    return sorted(candidates, key=len, reverse=True)


def find_cached_page(url: str) -> Path | None:
    basename = Path(urlparse(url).path).name
    if not basename:
        return None
    candidates = list(CACHE_DIR.glob(f"**/{basename}"))
    if candidates:
        return candidates[0]
    stem = Path(basename).stem
    candidates = list(CACHE_DIR.glob(f"**/*{stem}*.html"))
    return candidates[0] if candidates else None


def fetch_page(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        target.write_bytes(response.read())
    time.sleep(0.2)
    return target


def official_page_text(url: str, refresh: bool) -> tuple[str | None, Path | None, str | None]:
    cached = find_cached_page(url)
    if cached and not refresh:
        return strip_html(cached.read_bytes()), cached, None
    if not refresh:
        return None, cached, "official page is not cached; rerun with --refresh-official to fetch it"
    basename = Path(urlparse(url).path).name or "official_page.html"
    target = CACHE_DIR / "audit_pages" / basename
    try:
        return strip_html(fetch_page(url, target).read_bytes()), target, None
    except Exception as exc:  # noqa: BLE001 - audit report should capture source failures.
        return None, cached, f"fetch failed: {exc}"


def source_contains_value(text: str, series_name: str, value: float) -> tuple[bool, str | None]:
    compact = compact_text(text).replace(",", "")
    candidates = format_number_candidates(value)
    name_index = compact.find(compact_text(series_name))
    search_windows = []
    if name_index >= 0:
        search_windows.append(compact[max(0, name_index - 220) : name_index + len(series_name) + 320])
    search_windows.append(compact)
    for candidate in candidates:
        for window in search_windows:
            index = window.find(candidate)
            if index >= 0:
                start = max(0, index - 50)
                end = min(len(window), index + len(candidate) + 50)
                return True, window[start:end]
    return False, None


def release_metric_items(datasets: dict[str, dict]) -> list[dict]:
    items = []
    for dataset, payload in datasets.items():
        series_meta = payload.get("series", {})
        for record in payload.get("records", []):
            metrics = record.get("metrics") or {}
            for series_id, metric in metrics.items():
                if series_id not in series_meta or not isinstance(metric, dict):
                    continue
                for field in OFFICIAL_PAGE_VALUE_KEYS:
                    value = numeric(metric.get(field))
                    if value is None:
                        continue
                    if series_id in DERIVED_RATIO_SERIES:
                        continue
                    items.append(
                        {
                            "dataset": dataset,
                            "period": record.get("period", ""),
                            "record": record,
                            "series_id": series_id,
                            "series_name": series_meta[series_id].get("name", series_id),
                            "field": field,
                            "value": value,
                        }
                    )
    return items


def check_release_metric(item: dict, refresh: bool) -> AuditResult:
    record = item["record"]
    url = record.get("url")
    if not url or "data.stats.gov.cn" in url:
        return AuditResult(
            check="official_page_value",
            status="skipped",
            dataset=item["dataset"],
            period=item["period"],
            series=item["series_id"],
            field=item["field"],
            expected=item["value"],
            source=url,
            note="this point comes from the 国家数据 interactive endpoint; page-text validation is not available in this offline audit",
            rule=DIRECT_RELEASE_CHECK_NOTE,
        )
    text, cached_path, error = official_page_text(url, refresh)
    if error:
        return AuditResult(
            check="official_page_value",
            status="skipped",
            dataset=item["dataset"],
            period=item["period"],
            series=item["series_id"],
            field=item["field"],
            expected=item["value"],
            source=url,
            note=error,
            rule=DIRECT_RELEASE_CHECK_NOTE,
        )
    if not text:
        return AuditResult(
            check="official_page_value",
            status="skipped",
            dataset=item["dataset"],
            period=item["period"],
            series=item["series_id"],
            field=item["field"],
            expected=item["value"],
            source=url,
            note="official page text unavailable",
            rule=DIRECT_RELEASE_CHECK_NOTE,
        )
    matched, evidence = source_contains_value(text, item["series_name"], item["value"])
    return AuditResult(
        check="official_page_value",
        status="pass" if matched else "fail",
        dataset=item["dataset"],
        period=item["period"],
        series=item["series_id"],
        field=item["field"],
        expected=item["value"],
        source=url,
        evidence=evidence or str(cached_path),
        note=None if matched else "expected number was not found near the official page text",
        rule=DIRECT_RELEASE_CHECK_NOTE,
    )


def check_retail_cumulative(payload: dict, rng: random.Random, sample_size: int) -> list[AuditResult]:
    records = sorted(payload.get("records", []), key=lambda record: record.get("period", ""))
    by_period = {record.get("period"): record for record in records}
    items = []
    for record in records:
        year = record.get("year")
        month = record.get("month")
        if not year or not month or month <= 1:
            continue
        previous = by_period.get(f"{year}-{month - 1:02d}")
        if not previous:
            continue
        for series_id, metric in (record.get("metrics") or {}).items():
            if series_id in DERIVED_RATIO_SERIES:
                continue
            previous_metric = (previous.get("metrics") or {}).get(series_id) or {}
            for ytd_field, month_field in [
                ("latest_ytd_value", "latest_month_value"),
                ("published_ytd_value", "published_month_value"),
            ]:
                current_ytd = numeric(metric.get(ytd_field))
                previous_ytd = numeric(previous_metric.get(ytd_field))
                month_value = numeric(metric.get(month_field))
                if current_ytd is None or previous_ytd is None or month_value is None:
                    continue
                items.append((record, series_id, ytd_field, month_field, current_ytd - previous_ytd, month_value))
    rng.shuffle(items)
    results = []
    for record, series_id, ytd_field, month_field, observed, expected in items[:sample_size]:
        tolerance = max(1.0, tolerance_for(expected))
        results.append(
            AuditResult(
                check="retail_cumulative_diff",
                status="pass" if close_enough(expected, observed, tolerance) else "warn",
                dataset="retail",
                period=record.get("period", ""),
                series=series_id,
                field=month_field,
                expected=round(expected, 4),
                observed=round(observed, 4),
                tolerance=tolerance,
                source=record.get("url"),
                note=None
                if close_enough(expected, observed, tolerance)
                else RETAIL_DIFF_CHECK_NOTE,
                rule=RETAIL_DIFF_CHECK_NOTE,
            )
        )
    return results


def check_property_aggregate(payload: dict, rng: random.Random, sample_size: int) -> list[AuditResult]:
    items = []
    for record in payload.get("records", []):
        cities = record.get("cities") or {}
        if not cities:
            continue
        for city_metric, aggregate_id in [
            ("new_home_price", "new_home_70_price"),
            ("resale_home_price", "resale_home_70_price"),
        ]:
            values = [numeric((city.get(city_metric) or {}).get("month_value")) for city in cities.values()]
            values = [value for value in values if value is not None]
            if values:
                observed_average = round(sum(values) / len(values), 2)
                expected_average = numeric(((record.get("metrics") or {}).get(aggregate_id) or {}).get("month_value"))
                if expected_average is not None:
                    items.append((record, aggregate_id, "month_value", expected_average, observed_average))
            up_count = sum(1 for value in values if value > 0)
            count_id = "new_home_up_cities" if city_metric == "new_home_price" else "resale_home_up_cities"
            expected_count = numeric(((record.get("metrics") or {}).get(count_id) or {}).get("month_value"))
            if expected_count is not None:
                items.append((record, count_id, "month_value", expected_count, up_count))
    rng.shuffle(items)
    results = []
    for record, series_id, field, expected, observed in items[:sample_size]:
        tolerance = 0.06 if "price" in series_id else 0
        results.append(
            AuditResult(
                check="property_70_city_recompute",
                status="pass" if close_enough(expected, observed, tolerance) else "fail",
                dataset="property",
                period=record.get("period", ""),
                series=series_id,
                field=field,
                expected=expected,
                observed=observed,
                tolerance=tolerance,
                source=record.get("url"),
            )
        )
    return results


def period_label(period: str) -> str:
    year, month = period.split("-")
    return f"{int(year)}年{int(month)}月"


def build_raw_city_lookup(raw_payload: dict) -> dict:
    lookup = {}
    for city, city_payload in (raw_payload.get("cities") or {}).items():
        tables = city_payload.get("tables") or []
        for index in range(0, len(tables) - 1, 2):
            if not tables[index] or not tables[index + 1]:
                continue
            header = tables[index][0]
            if not header or header[0] != "指标":
                continue
            for row in tables[index + 1]:
                row_name = row[0] if row else ""
                for column_index, header_value in enumerate(header[1:], start=1):
                    if column_index >= len(row):
                        continue
                    cell = numeric(row[column_index])
                    if cell is None:
                        continue
                    match = re.match(r"(\d{4})年(\d{1,2})月", header_value)
                    if not match:
                        continue
                    period = f"{int(match.group(1))}-{int(match.group(2)):02d}"
                    lookup[(city, period, row_name)] = cell
    return lookup


def city_history_items(city_payload: dict) -> list[dict]:
    items = []
    for record in city_payload.get("records", []):
        period = record.get("period", "")
        for city, city_metrics in (record.get("cities") or {}).items():
            for metric_id, metric in (city_metrics or {}).items():
                for field in ["month_value", "month_yoy"]:
                    value = numeric((metric or {}).get(field))
                    if value is None or (metric_id, field) not in CITY_RAW_ROWS:
                        continue
                    items.append(
                        {
                            "period": period,
                            "city": city,
                            "series_id": metric_id,
                            "field": field,
                            "value": value,
                            "raw_row": CITY_RAW_ROWS[(metric_id, field)],
                        }
                    )
    return items


def check_city_history_raw(city_payload: dict, raw_payload: dict, rng: random.Random, sample_size: int) -> list[AuditResult]:
    lookup = build_raw_city_lookup(raw_payload)
    items = city_history_items(city_payload)
    rng.shuffle(items)
    results = []
    for item in items[:sample_size]:
        raw_index = lookup.get((item["city"], item["period"], item["raw_row"]))
        if raw_index is None:
            results.append(
                AuditResult(
                    check="property_city_raw_table",
                    status="skipped",
                    dataset="property_city_history",
                    period=item["period"],
                    series=f'{item["city"]}.{item["series_id"]}',
                    field=item["field"],
                    expected=item["value"],
                    source=city_payload.get("source_url"),
                    note="国家数据原始表该单元格为空；可能由发布稿补齐或该期未在表格中开放",
                )
            )
            continue
        observed = round(raw_index - 100, 2)
        tolerance = 0.06
        results.append(
            AuditResult(
                check="property_city_raw_table",
                status="pass" if close_enough(item["value"], observed, tolerance) else "fail",
                dataset="property_city_history",
                period=item["period"],
                series=f'{item["city"]}.{item["series_id"]}',
                field=item["field"],
                expected=item["value"],
                observed=observed,
                tolerance=tolerance,
                source=city_payload.get("source_url"),
                evidence=f'{item["raw_row"]} / {period_label(item["period"])} = {raw_index}',
            )
        )
    return results


def sample(items: list, rng: random.Random, size: int) -> list:
    if len(items) <= size:
        return list(items)
    return rng.sample(items, size)


def summarize(results: list[AuditResult]) -> dict:
    summary = {"total": len(results), "pass": 0, "fail": 0, "warn": 0, "skipped": 0}
    by_check = {}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
        row = by_check.setdefault(result.check, {"total": 0, "pass": 0, "fail": 0, "warn": 0, "skipped": 0})
        row["total"] += 1
        row[result.status] = row.get(result.status, 0) + 1
    summary["by_check"] = by_check
    return summary


def escape_html(value) -> str:
    return html.escape("" if value is None else str(value))


def status_pill(status: str) -> str:
    return f'<span class="pill {escape_html(status)}">{escape_html(status)}</span>'


def table_rows(results: list[dict], status: str, limit: int | None = None) -> str:
    rows = [row for row in results if row.get("status") == status]
    if limit:
        rows = rows[:limit]
    if not rows:
        return '<tr><td colspan="10" class="empty">None</td></tr>'
    rendered = []
    for row in rows:
        rendered.append(
            f"""<tr>
<td>{status_pill(row.get("status", ""))}</td>
<td><code>{escape_html(row.get("check"))}</code></td>
<td>{escape_html(row.get("dataset"))}</td>
<td>{escape_html(row.get("period"))}</td>
<td>{escape_html(row.get("series"))}</td>
<td><code>{escape_html(row.get("field"))}</code></td>
<td>{escape_html(row.get("expected"))}</td>
<td>{escape_html(row.get("observed"))}</td>
<td class="note">{escape_html(row.get("rule") or "")}</td>
<td class="note">{escape_html(row.get("note") or row.get("evidence") or "")}</td>
</tr>"""
        )
    return "\n".join(rendered)


def write_html_report(payload: dict, html_path: Path) -> None:
    summary = payload["summary"]
    results = payload["results"]
    by_check = "\n".join(
        f"""<tr>
<td><code>{escape_html(name)}</code></td>
<td>{row.get("total", 0)}</td>
<td>{row.get("pass", 0)}</td>
<td>{row.get("fail", 0)}</td>
<td>{row.get("warn", 0)}</td>
<td>{row.get("skipped", 0)}</td>
</tr>"""
        for name, row in summary["by_check"].items()
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Official Data Audit Results</title>
<style>
:root {{ color-scheme: light; --ink:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7; --bg:#f5f5f7; --card:#fff; --green:#248a3d; --red:#b42318; --orange:#b25a00; --blue:#0066cc; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Helvetica Neue",Arial,sans-serif; background:var(--bg); color:var(--ink); }}
main {{ max-width:1180px; margin:0 auto; padding:40px 24px 72px; }}
header {{ display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; border-bottom:1px solid var(--line); padding-bottom:28px; margin-bottom:24px; }}
h1 {{ font-size:56px; line-height:.95; letter-spacing:0; margin:0; }}
.sub {{ color:var(--muted); font-size:17px; line-height:1.45; margin-top:14px; }}
.score {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:20px 24px; min-width:260px; box-shadow:0 18px 44px rgba(0,0,0,.06); }}
.score b {{ display:block; font-size:44px; line-height:1; }}
.score span {{ color:var(--muted); font-weight:700; }}
.grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin:24px 0; }}
.metric {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; }}
.metric strong {{ display:block; font-size:30px; }}
.metric span {{ color:var(--muted); font-weight:700; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:20px; margin-top:18px; overflow:auto; }}
h2 {{ font-size:24px; margin:0 0 14px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:10px 9px; border-top:1px solid #ececf0; vertical-align:top; }}
th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.02em; }}
code {{ font-family:"SF Mono",ui-monospace,Menlo,monospace; font-size:.92em; }}
.pill {{ display:inline-block; min-width:58px; text-align:center; color:white; border-radius:999px; padding:4px 8px; font-weight:800; font-size:12px; }}
.pass {{ background:var(--green); }} .fail {{ background:var(--red); }} .warn {{ background:var(--orange); }} .skipped {{ background:var(--muted); }}
.note {{ color:var(--muted); max-width:360px; }}
.empty {{ color:var(--muted); text-align:center; padding:24px; }}
@media (max-width: 860px) {{ header {{ grid-template-columns:1fr; }} h1 {{ font-size:40px; }} .grid {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<main>
<header>
<div>
<h1>Official Data Audit Results</h1>
<div class="sub">Generated at: <code>{escape_html(payload.get("generated_at"))}</code><br>Seed: <code>{escape_html(payload.get("seed"))}</code> · Samples per pool: <code>{escape_html(payload.get("samples_per_pool"))}</code> · Refresh official: <code>{escape_html(payload.get("refresh_official"))}</code></div>
</div>
<div class="score"><span>Hard failures</span><b>{summary.get("fail", 0)}</b></div>
</header>
<section class="grid">
<div class="metric"><span>Total</span><strong>{summary.get("total", 0)}</strong></div>
<div class="metric"><span>Pass</span><strong>{summary.get("pass", 0)}</strong></div>
<div class="metric"><span>Fail</span><strong>{summary.get("fail", 0)}</strong></div>
<div class="metric"><span>Warn</span><strong>{summary.get("warn", 0)}</strong></div>
<div class="metric"><span>Skipped</span><strong>{summary.get("skipped", 0)}</strong></div>
</section>
<section class="card"><h2>Check Summary</h2><table><thead><tr><th>Check</th><th>Total</th><th>Pass</th><th>Fail</th><th>Warn</th><th>Skipped</th></tr></thead><tbody>{by_check}</tbody></table></section>
<section class="card"><h2>Failures</h2><table><thead><tr><th>Status</th><th>Check</th><th>Dataset</th><th>Period</th><th>Series</th><th>Field</th><th>Expected</th><th>Observed</th><th>Rule</th><th>Note / Evidence</th></tr></thead><tbody>{table_rows(results, "fail")}</tbody></table></section>
<section class="card"><h2>Warnings</h2><table><thead><tr><th>Status</th><th>Check</th><th>Dataset</th><th>Period</th><th>Series</th><th>Field</th><th>Expected</th><th>Observed</th><th>Rule</th><th>Note / Evidence</th></tr></thead><tbody>{table_rows(results, "warn")}</tbody></table></section>
<section class="card"><h2>Skipped</h2><table><thead><tr><th>Status</th><th>Check</th><th>Dataset</th><th>Period</th><th>Series</th><th>Field</th><th>Expected</th><th>Observed</th><th>Rule</th><th>Note / Evidence</th></tr></thead><tbody>{table_rows(results, "skipped")}</tbody></table></section>
<section class="card"><h2>Passed Sample</h2><table><thead><tr><th>Status</th><th>Check</th><th>Dataset</th><th>Period</th><th>Series</th><th>Field</th><th>Expected</th><th>Observed</th><th>Rule</th><th>Evidence</th></tr></thead><tbody>{table_rows(results, "pass", 40)}</tbody></table></section>
</main>
</body>
</html>"""
    html_path.write_text(html_doc, encoding="utf-8")


def write_report(payload: dict, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"official_data_audit_{stamp}.json"
    md_path = output_dir / f"official_data_audit_{stamp}.md"
    html_path = output_dir / f"official_data_audit_{stamp}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = payload["summary"]
    failures = [row for row in payload["results"] if row["status"] == "fail"]
    skipped = [row for row in payload["results"] if row["status"] == "skipped"]
    lines = [
        "# Official Data Audit",
        "",
        f"- Seed: `{payload['seed']}`",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Total checks: {summary['total']}",
        f"- Passed: {summary.get('pass', 0)}",
        f"- Failed: {summary.get('fail', 0)}",
        f"- Warnings: {summary.get('warn', 0)}",
        f"- Skipped: {summary.get('skipped', 0)}",
        "",
        "## Checks",
        "",
    ]
    for name, row in summary["by_check"].items():
        lines.append(f"- `{name}`: {row['pass']} pass / {row['fail']} fail / {row['warn']} warn / {row['skipped']} skipped")
    if failures:
        lines.extend(["", "## Failures", ""])
        for row in failures[:50]:
            lines.append(
                f"- `{row['check']}` {row['dataset']} {row['period']} {row['series']} `{row['field']}`: "
                f"expected `{row.get('expected')}`, observed `{row.get('observed')}`; {row.get('note', '')}"
            )
    if skipped:
        lines.extend(["", "## Skipped Sample", ""])
        for row in skipped[:20]:
            lines.append(
                f"- `{row['check']}` {row['dataset']} {row['period']} {row['series']} `{row['field']}`: {row.get('note', '')}"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_html_report(payload, html_path)
    return json_path, md_path, html_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Randomly audit dashboard data against official cached/source data.")
    parser.add_argument("--samples-per-pool", type=int, default=35, help="random checks to run in each audit pool")
    parser.add_argument("--seed", default=dt.date.today().isoformat(), help="random seed for reproducible sampling")
    parser.add_argument("--refresh-official", action="store_true", help="fetch official HTML pages when cached pages are missing")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true", help="exit with status 1 when failures are found")
    args = parser.parse_args()

    rng = random.Random(str(args.seed))
    datasets = {name: load_json(path) for name, path in RELEASE_DATASETS}
    city_history = load_json(ROOT / "property_city_history.json")
    city_history_raw = load_json(ROOT / "property_city_history_raw.json")

    results: list[AuditResult] = []

    release_items = release_metric_items(datasets)
    for item in sample(release_items, rng, args.samples_per_pool):
        results.append(check_release_metric(item, args.refresh_official))

    results.extend(check_retail_cumulative(datasets["retail"], rng, args.samples_per_pool))
    results.extend(check_property_aggregate(datasets["property"], rng, args.samples_per_pool))
    results.extend(check_city_history_raw(city_history, city_history_raw, rng, args.samples_per_pool))

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "seed": args.seed,
        "samples_per_pool": args.samples_per_pool,
        "refresh_official": args.refresh_official,
        "summary": summarize(results),
        "results": [result.to_dict() for result in results],
    }
    json_path, md_path, html_path = write_report(payload, args.output_dir)

    summary = payload["summary"]
    print(f"Audit report: {json_path}")
    print(f"Markdown summary: {md_path}")
    print(f"HTML summary: {html_path}")
    print(
        f"Checks: {summary['total']} total, {summary.get('pass', 0)} pass, "
        f"{summary.get('fail', 0)} fail, {summary.get('warn', 0)} warn, {summary.get('skipped', 0)} skipped"
    )
    for name, row in summary["by_check"].items():
        print(f"  - {name}: {row['pass']} pass / {row['fail']} fail / {row['warn']} warn / {row['skipped']} skipped")

    if args.strict and summary.get("fail", 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
