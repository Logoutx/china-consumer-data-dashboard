# data/archive/ — as-published truth

Immutable raw captures, written by `pipeline.fetch.fetch_and_archive` before any
parsing happens. See `docs/DATA-CONTRACT.md` §8 for the full contract.

Layout: `data/archive/<source>/<YYYY-MM-DD>_<slug>.html` (one file per fetched
release page; `<source>` matches a `ParsedRelease.source` value, e.g. `nbs-cpi`,
`nbs-retail`, `pbc-money`).

This directory is empty as of the acquisition-pipeline build (2026-07-08): no live
release fetches were run against it (only fixture-driven parser tests and one
polite `discover.py` listing-page check, which does not archive anything). It
fills in once `pipeline/runner.py` is run for real, or the GitHub Actions poller
(DATA-CONTRACT §11.2) is wired up.

Archive files are append/write-once and are only ever read by later stages
(normalize/validate/audit) -- never rewritten by the build.
