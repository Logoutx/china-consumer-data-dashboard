"""`--live` support: a capped (default <=10), polite re-pull of the live NBS DG
endpoint for a handful of already-sampled DG points, self-contained (stdlib
`urllib` only -- no dependency on pipeline.backfill.dg_client, keeping this
package's runtime independent of that concurrent agent's module even though it
isn't on the forbidden-import list; the wire protocol itself is a public HTTP
API, documented in dg_client.py's own docstring, which is fair to reimplement).

Default mode is --offline (no network at all, per task spec); this module is
only ever imported/called when the caller explicitly passes --live. A network
failure on any single point degrades to a WARN for that point, never an
exception that would take down the rest of the audit.

Simplification, stated explicitly: the real DG POST payload wants a `rootId`
(the frequency-and-domain ancestor id from the category tree) alongside `cid`
+ `id`. dg_client.py's own docstring notes rootId "empirically did not change
the result in spot checks" against the same cid — this module uses the
indicator's own `cid` as a rootId stand-in rather than re-walking the category
tree just to resolve the "correct" one, which would be a lot of machinery for
a capped, best-effort supplementary check.
"""
from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen

BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external"
PAGE_URL = "https://data.stats.gov.cn/dg/website/page.html"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
NATIONAL_AREA = "000000000000"
SLEEP_SECONDS = 1.5


def _date_code_for(period: str) -> str | None:
    if len(period) == 7 and period[4] == "-":  # "YYYY-MM"
        return f"{period[:4]}{period[5:7]}MM"
    if "-Q" in period:
        year, q = period.split("-Q")
        return f"{year}{int(q):02d}SS"
    return None


def fetch_live_value(cid: str, indicator_id: str, period: str, *, timeout: float = 15.0) -> float | None:
    """One polite POST for one (indicator, period). Returns None on ANY
    failure (network error, non-success envelope, missing period, unparseable
    value) -- callers must treat that as "couldn't confirm live", not as a
    mismatch."""
    date_code = _date_code_for(period)
    if date_code is None:
        return None
    payload = {"cid": cid, "id": indicator_id, "da": NATIONAL_AREA, "dt": "", "rootId": cid, "dts": [date_code]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{BASE}/getEsDataByIndicatorIdAndDa",
        data=body,
        headers={"User-Agent": USER_AGENT, "Referer": PAGE_URL, "Content-Type": "application/json;charset=UTF-8"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
        time.sleep(SLEEP_SECONDS)
        envelope = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(envelope, dict) or not envelope.get("success"):
            return None
        for item in envelope.get("data") or []:
            if item.get("dt") == date_code and item.get("v") not in (None, ""):
                return float(item["v"])
    except Exception:  # noqa: BLE001 -- a live-network hiccup must never crash the audit
        return None
    return None
