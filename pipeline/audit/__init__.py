"""pipeline/audit — Gate B: the post-build independent audit (DATA-CONTRACT §11's
"audit" stage, the second of the two accuracy gates the pipeline runs — gate #1 is
pipeline/validate/ at ingest time; gate B / gate #2 is this package, run AFTER
build, BEFORE deploy).

    python -m pipeline.audit --site-data site-data/ [--offline] [--seed X]
                              [--samples-per-section N]

Independence (the entire point of Gate B): this package must never import
pipeline.parsers, pipeline.normalize, pipeline.build, or pipeline.takeaways, and
must never read pipeline/config/field_map.yaml. Those are the modules/config that
*produced* site-data/ in the first place — an audit that re-derives its expected
values through the same code path it is supposed to be checking would not catch a
shared bug. Every check here re-verifies a built value through a DIFFERENT
extraction paradigm:

  - flat-text fuzzy matching over archived HTML (kernel.py), ported from the
    legacy tools/audit_official_data.py, instead of the build's lxml positional
    parsers;
  - direct JSON payload lookups into data/archive/dg/ for DG-sourced series,
    instead of the normalize stage's field-mapping;
  - independent re-derivation of aggregation formulas (sum, ratio, mean-of-cities,
    ytd-differencing) from DATA-CONTRACT §6's documented rules, applied to raw
    data/series/*.json and data/panels/*.json inputs, instead of trusting
    whatever the build already computed.

test_audit_independence.py enforces the import constraint mechanically (it walks
every module under this package's AST for forbidden imports/reads), so this is
not just a docstring promise.

Module map:
    models.py            Finding / CheckReport / AuditContext dataclasses shared
                          by every check.
    kernel.py             ported fuzzy-matching + numeric-tolerance kernel.
    sampling.py           seed derivation (git HEAD short SHA via subprocess) +
                          the strata builders for check 1.
    site_data.py          read-only JSON loaders for data/ and site-data/.
    labels.py / labels.yaml   hand-curated series_id -> Chinese label + unit
                          table used for fuzzy matching.
    dg_archive.py         indexes/looks up data/archive/dg/*.json.
    html_archive.py       indexes/looks up HTML archive captures (or fixture
                          pages, for tests) for the flat-text fuzzy scan.
    release_calendar.py   loads pipeline/config/release_calendar.yaml, or falls
                          back to a small embedded table, for the freshness check.
    checks/*.py           one module per gate_b.* check id.
    report.py             JSON + Markdown report writers.
    diary.py              builds the public "data diary" payload.
    cli.py / __main__.py  orchestration + exit-code logic.
"""
