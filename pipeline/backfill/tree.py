"""Name-path walker over the DG indicator tree (``queryIndexTreeAsync``), cached.

The DG tree has no stable machine-readable path syntax -- every node is addressed by
its GUID ``_id`` ("cid"), which is only discoverable by walking down from the
frequency root and matching on the (human, Chinese, occasionally trailing-space-
padded) ``name``/``_name`` field at each level. This module does that walk once per
distinct path and remembers the result on disk (``tree_cache.json``) so re-running
``backfill.py`` -- or a future extension of it -- never re-walks a path it already
resolved.

Cache shape (flat, keyed by the exact API call it memoizes -- deliberately not a
nested tree, so a partial/aborted run still has usable entries):

    {"<code>:<pid-or-root>": [ <raw child node dict>, ... ], ...}

Usage::

    client = DGClient()
    cache = TreeCache.load(CACHE_PATH)
    node = cache.walk_path(client, code=1, names=["价格指数", "居民消费价格分类指数 (上年同月=100)"])
    windows = cache.children_matching(client, code=1, pid=node["_id"],
                                       predicate=lambda n: n["name"].strip().startswith("全国居民消费价格分类指数 (上年同月=100) ("))
    cache.save(CACHE_PATH)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from pipeline.backfill.dg_client import DGClient

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "tree_cache.json"


class TreePathError(RuntimeError):
    """A name in a requested path had no matching child -- includes the sibling list."""


def _normalize(name: str) -> str:
    # NBS node names carry inconsistent trailing full-width/half-width spaces and
    # double spaces (e.g. "固定资产投资 (不含农户) "); collapse whitespace for matching.
    return re.sub(r"\s+", " ", name).strip()


def _best_match(children: list[dict], query: str) -> dict | None:
    """Exact match wins outright. Substring containment is only a fallback, and even
    then an exact-length-conscious one -- NBS names are not prefix-safe (e.g.
    "非制造业采购经理指数" contains "制造业采购经理指数" as a suffix), so a naive
    "b in a or a in b" would silently match the wrong sibling. Falling back to
    substring match ONLY when nothing matched exactly, and picking the closest-length
    candidate among substring hits, avoids that trap while still tolerating the
    genuine partial-name cases this tree needs (e.g. matching a window-suffixed name
    by its un-suffixed prefix)."""
    q = _normalize(query)
    for child in children:
        if _normalize(child["name"]) == q:
            return child
    candidates = [c for c in children if q in _normalize(c["name"]) or _normalize(c["name"]) in q]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(len(_normalize(c["name"])) - len(q)))


class TreeCache:
    """In-memory + on-disk memoization of queryIndexTreeAsync calls."""

    def __init__(self, entries: dict[str, list[dict]] | None = None):
        self.entries: dict[str, list[dict]] = entries or {}
        self.dirty = False

    @classmethod
    def load(cls, path: Path = DEFAULT_CACHE_PATH) -> "TreeCache":
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")))
        return cls({})

    def save(self, path: Path = DEFAULT_CACHE_PATH) -> None:
        if not self.dirty:
            return
        path.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.dirty = False

    def _key(self, code: int, pid: str | None) -> str:
        return f"{code}:{pid or 'root'}"

    def children(self, client: DGClient, code: int, pid: str | None) -> list[dict]:
        """Children of `pid` under frequency `code`, from cache or one network call."""
        key = self._key(code, pid)
        if key not in self.entries:
            self.entries[key] = client.tree_children(code, pid)
            self.dirty = True
        return self.entries[key]

    def walk_path(self, client: DGClient, code: int, names: list[str]) -> dict:
        """Descend from the frequency root matching `names` in sequence; return the
        final node (its `_id` is the cid to hand to indicators_by_cid / getEsData...).
        Raises TreePathError with the sibling list if any step has no match.

        `pid=""` (the API's own root call) returns a single WRAPPER node -- e.g.
        "月度数据" for code=1, "季度数据" for code=2 -- not the domain list (价格指数,
        工业, ...). `names` addresses paths *under* that wrapper, so this method
        transparently descends through it first before matching names[0]."""
        root_children = self.children(client, code, None)
        if len(root_children) != 1:
            raise TreePathError(
                f"expected exactly one frequency-root wrapper node for code={code}, "
                f"got {[n['name'] for n in root_children]!r}"
            )
        pid: str | None = root_children[0]["_id"]
        node: dict | None = root_children[0]
        walked: list[str] = [root_children[0]["name"].strip()]
        for name in names:
            kids = self.children(client, code, pid)
            match = _best_match(kids, name)
            if match is None:
                siblings = [k["name"].strip() for k in kids]
                raise TreePathError(
                    f"no child matching {name!r} under path {walked!r} (code={code}); "
                    f"siblings were: {siblings!r}"
                )
            node = match
            pid = match["_id"]
            walked.append(match["name"].strip())
        assert node is not None  # names is never empty in practice
        return node

    def children_matching(
        self, client: DGClient, code: int, pid: str, predicate: Callable[[dict], bool]
    ) -> list[dict]:
        """All children of `pid` for which `predicate(node)` is true (e.g. every
        date-windowed sibling of a classification table)."""
        return [k for k in self.children(client, code, pid) if predicate(k)]
