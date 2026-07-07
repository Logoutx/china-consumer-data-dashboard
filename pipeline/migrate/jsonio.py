"""Deterministic JSON writer matching DATA-CONTRACT.md section 9's diff conventions:

    - stable key order (whatever order the dict was built in -- Python dicts
      preserve insertion order, so callers control this by construction)
    - one observation/revision/break/cell-row per line (compact), the
      containing array otherwise pretty-printed one element per line
    - UTF-8, no BOM, LF line endings, trailing newline
    - numbers as JSON numbers, no thousands separators

Rule used to decide "compact vs pretty": a list whose elements are all dicts
(observations, revisions, breaks, catalog sections/series, panel revisions)
is rendered with each dict compacted onto its own line. A list of scalars or
nested lists (periods, dimensions.city, a panel measure array of numbers) is
rendered as a single compact line. Everything else (dicts) is pretty-printed
with 2-space indentation.
"""
from __future__ import annotations

import json


def _is_list_of_dicts(lst):
    return len(lst) > 0 and all(isinstance(x, dict) for x in lst)


def _compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def render(obj, indent=0):
    pad = "  " * indent
    pad1 = "  " * (indent + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [f'{pad1}"{k}": {render(v, indent + 1)}' for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if _is_list_of_dicts(obj):
            items = [pad1 + _compact(x) for x in obj]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        return _compact(obj)
    return json.dumps(obj, ensure_ascii=False)


def write_json(path, obj):
    text = render(obj, 0) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
