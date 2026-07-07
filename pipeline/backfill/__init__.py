"""History backfill via NBS's open DG national-data API (docs/ACQUISITION.md).

This package is independent of ``pipeline.migrate`` (the data.js migration, owned by
another agent) and ``pipeline.{fetch,discover,parse,normalize}`` (the ongoing-release
poller). It only ever writes new series ids under ``data/series/`` plus
``data/archive/dg/`` and ``data/_backfill_catalog_fragment.json`` -- see
``pipeline/backfill/REPORT.md`` for what was produced and why.

Modules:
    dg_client   -- polite HTTP wrapper for the three DG endpoints used here.
    tree        -- name-path walker over queryIndexTreeAsync, with an on-disk cache.
    backfill    -- the declarative target list + the script that runs it.
    merge_fragment -- one-shot, idempotent merge of the catalog fragment into
                      data/catalog.json (NOT run by this agent -- for the orchestrator).
"""
