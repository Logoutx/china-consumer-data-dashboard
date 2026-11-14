"""Narrow, fail-closed corroboration of legitimate repeated price prints.

Equality with last month is a suspicion, not release identity. Only the exact
capture(s) belonging to this batch can clear it, and every numeric proposed
measure must match a source row bearing the requested period.
"""
from __future__ import annotations

import json
import re

from lxml.etree import ParserError

from pipeline import ParseError
from pipeline.parsers import nbs_cpi
from pipeline.validate.util import is_number

MEASURES = ("m", "m_yoy", "ytd", "ytd_yoy", "mom", "real_yoy")


def corroborates_repeated_price(ctx, series_id: str, period: str, data: dict) -> bool:
    obs = next((o for o in data.get("observations", []) if o.get("period") == period), {})
    proposed = {key: obs[key] for key in MEASURES if is_number(obs.get(key))}
    if not proposed or ctx.archive_dir is None:
        return False
    try:
        if ctx.effective_archive_source == "nbs-cpi":
            return _cpi_capture(ctx, series_id, period, proposed)
        if (ctx.batch.release_id or "").startswith("dg-refresh"):
            return _dg_price_capture(ctx, series_id, period, proposed)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, ParseError, ParserError):
        # Broken/malformed or unsupported evidence must never grant an exception.
        return False
    return False


def _cpi_capture(ctx, series_id, period, proposed):
    fields = {item.source_field for item in ctx.batch.items_for(series_id)
              if item.period == period and item.source_field}
    if len(fields) != 1:
        return False
    source_field = next(iter(fields))
    captures = [p for p in (ctx.archive_dir / "nbs-cpi").glob("*.html")
                if p.stem == ctx.batch.release_id]
    if len(captures) != 1:
        return False
    parsed = nbs_cpi.parse(captures[0].read_text(encoding="utf-8"))
    values = {}
    for row in parsed.rows:
        if row.period == period and row.source_field == source_field:
            values.setdefault(row.caliber_hint, []).append(row.value)
    return all(key in values and all(abs(actual - value) < 1e-8 for actual in values[key])
               for key, value in proposed.items())


def _dg_price_capture(ctx, series_id, period, proposed):
    # Price index m and its m_yoy=m-100 are the SAME source measurement.
    # Do not extend this exception to arbitrary rates/levels or extra fields.
    if not series_id.startswith(("nbs-cpi", "nbs-ppi")) or set(proposed) != {"m", "m_yoy"}:
        return False
    if abs(proposed["m"] - 100 - proposed["m_yoy"]) > 1e-8:
        return False
    real = ctx.load_real(series_id) or {}
    # Require an unambiguous known original indicator identity. No fuzzy label
    # match, and no "any value in the archive happens to equal our number".
    ids = {o["src"][3:] for o in real.get("observations", [])
           if re.fullmatch(r"dg:[0-9a-f]{32}", o.get("src", ""))}
    if len(ids) != 1:
        return False
    indicator_id = next(iter(ids))
    dg_dir = ctx.archive_dir / "dg"
    manifest = json.loads((dg_dir / f"manifest_{ctx.batch.release_id}.json").read_text())
    if manifest.get("release_id") != ctx.batch.release_id:
        return False
    matches = []
    for filename in manifest.get("captures", []):
        if not isinstance(filename, str) or "/" in filename or "\\" in filename:
            return False
        if not filename.startswith("values_"):
            continue
        payload = json.loads((dg_dir / filename).read_text())
        if payload.get("request", {}).get("id") != indicator_id:
            continue
        for row in payload.get("response", {}).get("data", []):
            if row.get("dt") == period.replace("-", "") + "MM":
                matches.append(float(row["v"]))
    return bool(matches) and all(abs(value - proposed["m"]) < 1e-8 for value in matches)
