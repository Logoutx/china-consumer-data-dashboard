"""Tests for pipeline/fetch.py. No real network calls -- a fake session object
stands in for requests.Session so retry/backoff/encoding-force logic can be
exercised deterministically and fast."""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from pipeline import fetch as fetch_module
from pipeline.fetch import FetchError, archive_path_for, fetch, fetch_and_archive


class FakeResponse:
    def __init__(self, status_code=200, text="ok", encoding="ISO-8859-1"):
        self.status_code = status_code
        self.text = text
        self.encoding = encoding

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakeSession:
    """Returns/raises one scripted item per call, in order."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(fetch_module.time, "sleep", lambda seconds: None)


def test_forces_utf8_for_cn_host():
    session = FakeSession([FakeResponse(text="内容", encoding="ISO-8859-1")])
    result = fetch("https://www.stats.gov.cn/sj/zxfb/", session=session)
    assert result.encoding == "utf-8"


def test_does_not_force_encoding_for_non_cn_host():
    session = FakeSession([FakeResponse(text="content", encoding="ISO-8859-1")])
    result = fetch("https://example.com/page", session=session)
    assert result.encoding == "ISO-8859-1"


def test_retries_transient_failure_then_succeeds():
    session = FakeSession(
        [requests.ConnectionError("boom"), requests.Timeout("slow"), FakeResponse(text="ok")]
    )
    result = fetch("https://example.cn/page", session=session)
    assert result.text == "ok"
    assert len(session.calls) == 3


def test_gives_up_after_max_attempts():
    session = FakeSession([requests.ConnectionError("boom")] * 3)
    with pytest.raises(FetchError):
        fetch("https://example.cn/page", session=session)
    assert len(session.calls) == 3


def test_4xx_fails_fast_without_retrying():
    session = FakeSession([FakeResponse(status_code=404)])
    with pytest.raises(FetchError):
        fetch("https://example.cn/missing", session=session)
    assert len(session.calls) == 1  # no retry burned on a 404


def test_5xx_is_retried():
    session = FakeSession([FakeResponse(status_code=502), FakeResponse(status_code=200, text="ok")])
    result = fetch("https://example.cn/page", session=session)
    assert result.text == "ok"
    assert len(session.calls) == 2


def test_archive_path_shape():
    from datetime import datetime, timezone

    when = datetime(2026, 6, 16, tzinfo=timezone.utc)
    path = archive_path_for("nbs-retail", "2026年1—5月份社会消费品零售总额增长1.4%", when=when)
    assert path.parent.name == "nbs-retail"
    assert path.name.startswith("2026-06-16_")
    assert path.suffix == ".html"


def test_fetch_and_archive_writes_verbatim_content(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_module, "ARCHIVE_ROOT", tmp_path)
    session = FakeSession([FakeResponse(text="<html>archived body</html>")])
    result = fetch_and_archive("https://www.stats.gov.cn/sj/zxfb/test.html", source="nbs-cpi", slug="test release", session=session)
    assert result.archive_path is not None
    assert result.archive_path.exists()
    assert result.archive_path.read_text(encoding="utf-8") == "<html>archived body</html>"
    assert result.archive_path.parent == tmp_path / "nbs-cpi"
