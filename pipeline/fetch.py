"""pipeline/fetch.py — one hardened HTTP layer used by every other stage.

- Browser-like User-Agent (bare-python UAs get treated worse by some .gov.cn WAFs).
- 30s timeout, 3 attempts total, exponential backoff + jitter between retries.
- Retries transient failures (timeouts, connection errors, 5xx) but fails fast on
  4xx -- retrying a 404 three times just triples the wait for the same answer.
- Forces `response.encoding = "utf-8"` for every `.cn` host. This project has
  already been bitten once by NBS mojibake: several stats.gov.cn templates omit a
  charset in their Content-Type header, so `requests`' encoding guess falls back to
  a Latin-1-family guess and every CJK byte comes out garbled. utf-8 is the correct
  encoding for all known sources here (NBS, PBoC), so we force it unconditionally
  rather than trust the sniffed guess.
- Archives every fetched page verbatim to data/archive/<source>/<date>_<slug>.html
  *before* any parsing happens, so the audit gate can always re-verify a built value
  against exactly what was downloaded (DATA-CONTRACT §8).
"""
from __future__ import annotations

import random
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_JITTER_SECONDS = 0.5

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = ROOT / "data" / "archive"


class FetchError(Exception):
    """Raised when a URL could not be fetched after all retries (or fails fast on
    a 4xx). Callers that treat "not published yet" as normal (discover.py) should
    catch this and return an empty result, not propagate it."""


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    encoding: str
    fetched_at: str
    archive_path: Optional[Path] = None


def _is_cn_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith(".cn")


def _slugify(value: str, *, max_len: int = 80) -> str:
    """Turn a title/URL into a filesystem-safe slug. Keeps CJK characters (they are
    valid in modern filesystems and make archive directories human-scannable)."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", value).strip("-")
    return value[:max_len] or "page"


def _sleep_backoff(attempt: int) -> None:
    delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, BACKOFF_JITTER_SECONDS)
    time.sleep(delay)


def fetch(url: str, *, session: requests.Session | None = None, extra_headers: dict | None = None) -> FetchResult:
    """GET url with retry/backoff. Forces utf-8 decoding for .cn hosts.

    Raises FetchError if every attempt fails, or immediately on a 4xx response
    (no point retrying a request the server actively rejected).
    """
    client = session if session is not None else requests
    headers = {**DEFAULT_HEADERS, **(extra_headers or {})}
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
            status = response.status_code
            if 400 <= status < 500:
                raise FetchError(f"failed to fetch {url}: HTTP {status} (not retrying a client error)")
            response.raise_for_status()
            if _is_cn_host(url):
                response.encoding = "utf-8"
            return FetchResult(
                url=url,
                status_code=status,
                text=response.text,
                encoding=response.encoding or "utf-8",
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        except FetchError:
            raise
        except requests.RequestException as error:
            last_error = error
            if attempt == MAX_ATTEMPTS:
                break
            _sleep_backoff(attempt)

    raise FetchError(f"failed to fetch {url} after {MAX_ATTEMPTS} attempts: {last_error}") from last_error


def archive_path_for(source: str, slug: str, *, when: datetime | None = None) -> Path:
    when = when or datetime.now(timezone.utc)
    date_str = when.strftime("%Y-%m-%d")
    return ARCHIVE_ROOT / source / f"{date_str}_{_slugify(slug)}.html"


def fetch_and_archive(
    url: str,
    *,
    source: str,
    slug: str,
    session: requests.Session | None = None,
) -> FetchResult:
    """Fetch url and write the verbatim response body to
    data/archive/<source>/<date>_<slug>.html before returning.

    This is the only sanctioned way to bring a release page into the pipeline --
    a parser should never run against a page that bypassed archiving, or the audit
    gate loses its ability to re-verify "what the release page actually said."
    Re-fetching the same release should be byte-stable except for `fetched_at`
    (DATA-CONTRACT §8); this function does not attempt de-duplication itself --
    that is the runner's job, since only the runner knows whether a release was
    already ingested.
    """
    result = fetch(url, session=session)
    path = archive_path_for(source, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.text, encoding="utf-8")
    result.archive_path = path
    return result
