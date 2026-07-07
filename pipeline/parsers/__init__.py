"""Source-specific parsers. Each module exposes a single pure function

    parse(html_text: str, *, url: str = "", release_id: str = "") -> ParsedRelease

that turns one release page's raw HTML into source-vocabulary rows (see
pipeline/__init__.py for the ParsedRelease/ParsedRow shapes). `period_hint` and
`published_at` are extracted from the page itself (title / <meta name="PubDate">),
since every fixture under pipeline/fixtures/raw/ carries both -- `url`/`release_id`
are metadata the caller (runner.py, from a RawCapture) passes through unchanged.
"""
