"""Polite client for NBS's open DG national-data API (docs/ACQUISITION.md).

Endpoint family: ``https://data.stats.gov.cn/dg/website/publicrelease/web/external/*``.
Verified reachable (no WAF) from this Mac and documented as reachable from CI.

The real shape of this API is a **three-step protocol**, reverse-engineered from the
site's own lazy-loaded SPA bundles (``project_national_datatree`` /
``project_national_datapage``) plus a public write-up (zhihu.com/p/2020899586898707578)
found via web search -- it is not what ``tools/fetch_nbs_national_data.py`` alone would
suggest, because that script only ever needed indicator ids that had already been
hand-harvested for retail:

    1. ``new/queryIndexTreeAsync?code=<freq>&pid=<parent-cid>``
       Walk the category tree. ``code``: 1=monthly, 2=quarterly, 3=annual,
       4=provincial-monthly, 5=provincial-quarterly. ``pid=""`` returns the single
       frequency-root node (e.g. "月度数据"). A node's own ``_id`` is the ``pid`` for
       its children; ``isLeaf:true`` marks a queryable table ("cid").
    2. ``new/queryIndicatorsByCid?cid=<leaf-cid>``
       List the indicator ROWS inside that table -- e.g. a "社会消费品零售总额" leaf
       contains both the headline row and the 限额以上 row, each with its own
       ``_id`` (the "indicator id"), ``i_showname``/``_name`` (display name),
       ``du_name`` (unit). THIS is the step that is missing from
       ``tools/fetch_nbs_national_data.py`` and from ``docs/ACQUISITION.md``'s
       "one-time browser seed" plan -- it turns out the id/cid pairs are fully
       enumerable without a browser after all.
    3. ``getEsDataByIndicatorIdAndDa`` (POST) with ``{cid, id, da, dt:"", rootId, dts}``
       Returns ``{dt, v}`` pairs for the requested period codes. ``da`` is the region
       filter (``000000000000`` = national). ``rootId`` is the *frequency-and-domain*
       ancestor id (e.g. the monthly tree's "国内贸易" node for retail, "价格指数" for
       CPI/PPI) -- empirically it did not change the result in spot checks, but the
       SPA always sends the true domain ancestor, so this client does too.

Period code formats (confirmed empirically, not documented anywhere public):
    monthly   "YYYYMMMM"  e.g. "202605MM"
    quarterly "YYYYQQSS"  e.g. "202602SS" (season/quarter as a zero-padded 01-04)
    annual    "YYYYAA"    (used by analogy; not exercised by this backfill's targets)

Politeness: 1.5s minimum spacing between HTTP requests (both GET and POST), a real
browser UA, and a Referer matching the SPA's own page -- matching what
``tools/fetch_nbs_national_data.py`` already does for retail. Every raw JSON response
(or raw text, if decoding fails) is archived under ``data/archive/dg/`` so a human can
audit exactly what NBS returned for any pull in this backfill.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE_DIR = REPO_ROOT / "data" / "archive" / "dg"

BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
PAGE_URL = "https://data.stats.gov.cn/dg/website/page.html"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
NATIONAL_AREA = "000000000000"
DEFAULT_SLEEP_SECONDS = 1.5
CHUNK_SIZE = 36  # date-codes per getEsDataByIndicatorIdAndDa call (matches the existing retail tool)


class DGError(RuntimeError):
    """The DG API returned a non-success envelope, an HTTP error, or an unparseable body."""


def _to_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def month_codes(start_year: int, end_period: str) -> list[str]:
    """['YYYYMMMM', ...] ascending, start_year-01 .. end_period (inclusive), 'YYYY-MM'."""
    end_year, end_month = (int(part) for part in end_period.split("-"))
    codes = []
    for year in range(start_year, end_year + 1):
        last_month = end_month if year == end_year else 12
        for month in range(1, last_month + 1):
            codes.append(f"{year}{month:02d}MM")
    return codes


def quarter_codes(start_year: int, end_period: str) -> list[str]:
    """['YYYYQQSS', ...] ascending, start_year-Q1 .. end_period (inclusive), 'YYYY-Qn'."""
    end_year, end_q = end_period.split("-Q")
    end_year, end_q = int(end_year), int(end_q)
    codes = []
    for year in range(start_year, end_year + 1):
        last_q = end_q if year == end_year else 4
        for q in range(1, last_q + 1):
            codes.append(f"{year}{q:02d}SS")
    return codes


def _chunks(values: list[str], size: int = CHUNK_SIZE):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def period_from_dt(dt: str) -> str | None:
    """DG 'dt' code -> contract period string. '202605MM'->'2026-05', '202602SS'->'2026-Q2'."""
    match = re.match(r"^(\d{4})(\d{2})(MM|SS|AA)$", dt)
    if not match:
        return None
    year, unit, kind = match.groups()
    if kind == "MM":
        return f"{year}-{unit}"
    if kind == "SS":
        return f"{year}-Q{int(unit)}"
    return year  # AA (annual) -- unit is not meaningful


class DGClient:
    """Polite wrapper around the three DG endpoints described above.

    Every request sleeps at least ``sleep_seconds`` after the *previous* request
    returned (so archiving/parsing time isn't "stolen" from the politeness budget),
    sends a browser UA + Referer, forces UTF-8 decoding, and archives the raw response
    envelope under ``archive_dir``. Tolerant of the envelope shape
    ``{"data": ..., "success": bool, "state": int, "message": str}`` -- raises
    :class:`DGError` on ``success:false`` or a non-JSON body rather than returning a
    partial result.
    """

    def __init__(
        self,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        archive_dir: Path = DEFAULT_ARCHIVE_DIR,
        user_agent: str = USER_AGENT,
    ):
        self.sleep_seconds = sleep_seconds
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self._last_request_at: float | None = None
        self.request_count = 0

    # -- transport --

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.sleep_seconds:
            time.sleep(self.sleep_seconds - elapsed)

    def _send(self, url: str, body: bytes | None, extra_headers: dict) -> bytes:
        self._throttle()
        headers = {"User-Agent": self.user_agent, "Referer": PAGE_URL, **extra_headers}
        req = Request(url, data=body, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except (HTTPError, URLError) as exc:
            self._last_request_at = time.monotonic()
            self.request_count += 1
            raise DGError(f"request failed for {url}: {exc}") from exc
        self._last_request_at = time.monotonic()
        self.request_count += 1
        return raw

    def _archive(self, tag: str, record: dict) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", tag)[:120]
        path = self.archive_dir / f"{safe_tag}_{stamp}.json"
        path.write_text(
            json.dumps({**record, "fetched_at": stamp}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _envelope(self, raw: bytes, tag: str, url: str, request_body=None):
        text = raw.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            self._archive(tag, {"url": url, "request": request_body, "raw_text": text[:4000], "parse_error": str(exc)})
            raise DGError(f"non-JSON response from {url} ({tag}): {text[:200]!r}") from exc
        self._archive(tag, {"url": url, "request": request_body, "response": envelope})
        if not isinstance(envelope, dict) or not envelope.get("success"):
            message = envelope.get("message") if isinstance(envelope, dict) else None
            raise DGError(f"DG API success:false for {url} ({tag}): {message!r}")
        return envelope.get("data")

    def _get(self, path: str, params: dict[str, str], tag: str):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE}/{path}?{query}"
        raw = self._send(url, None, {})
        return self._envelope(raw, tag, url)

    def _post(self, path: str, payload: dict, tag: str):
        url = f"{BASE}/{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw = self._send(url, body, {"Content-Type": "application/json;charset=UTF-8"})
        return self._envelope(raw, tag, url, request_body=payload)

    # -- public API --

    def tree_children(self, code: int, pid: str | None) -> list[dict]:
        """One level of queryIndexTreeAsync. pid='' (or None) => the frequency root."""
        data = self._get(
            "new/queryIndexTreeAsync",
            {"code": str(code), "pid": pid or ""},
            f"tree_c{code}_{pid or 'root'}",
        )
        return data if isinstance(data, list) else []

    def indicators_by_cid(self, cid: str) -> list[dict]:
        """List indicator rows (each its own GUID) inside a leaf table cid."""
        data = self._get("new/queryIndicatorsByCid", {"cid": cid}, f"indicators_{cid}")
        if not isinstance(data, dict):
            return []
        return data.get("list") or []

    def latest_period(self, cid: str, root_id: str) -> str | None:
        """queryDtByCid -> the latest available contract period ('YYYY-MM' etc), or None."""
        data = self._get("new/queryDtByCid", {"cid": cid, "rootId": root_id}, f"latest_{cid}")
        dt_all = (data or {}).get("dt_all") or ""
        return period_from_dt(dt_all) if dt_all else None

    def indicator_values(
        self, cid: str, indicator_id: str, root_id: str, date_codes: list[str]
    ) -> dict[str, float | None]:
        """{'YYYY-MM'|'YYYY-Qn': value|None} for one indicator id, chunked politely."""
        values: dict[str, float | None] = {}
        for chunk in _chunks(date_codes):
            payload = {
                "cid": cid,
                "id": indicator_id,
                "da": NATIONAL_AREA,
                "dt": "",
                "rootId": root_id,
                "dts": chunk,
            }
            data = self._post("getEsDataByIndicatorIdAndDa", payload, f"values_{indicator_id}")
            for item in data or []:
                period = period_from_dt(item["dt"])
                if period is None:
                    continue
                values[period] = _to_number(item.get("v"))
        return values
