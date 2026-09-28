# M3 inputs (2026-09-27)

Reference outputs and site facts for the exporters that turn the two Tao companions
into annotation documents (docs/annotations.md, format 1.0):

- `measure_theory.annotations.json.gz`: the prototype export of measure_theory at
  master 00a2438 (432 items, 3,565 declarations, 61 artifacts, 5,093 memberships,
  509 locations, 627 links, 2,884 destinations, 542 snippets, 11 verdicts). Validates
  with `scripts/annotations/validate.py --canonical`, no warnings.
- `tao-measure-lean.annotations.json.gz`: the prototype export of tao-measure-lean at
  main f158509 (651 items, 1,544 declarations, 2,201 locations, 1,169 links, 1,544
  destinations, 377 unattributed). Same validation.
- `measure_theory.export-report.json`, `tao-measure-lean.export-stats.json`: the
  prototypes' own reports (counts, lossy buckets, bends).
- `tml-site-xref.json.gz`: `_site/xref.json` of the tao-measure-lean literate site
  rendered from f158509 (git-ignored build output; the cloud cannot rebuild it).
- `tml-site-pages.json`: the 38 HTML pages of that site with sha256 and bytes, and
  the site digest as docs/annotations.md section 3.1 defines it.

The prototypes themselves are under `prototypes/m3/` (export_mt.py, export_tml.py,
anon_ids.py); they were written against local checkouts at
/Users/jack/Desktop/measure_theory and /Users/jack/Desktop/tao-measure-lean and read
the site under `_site/`. They are inputs to be turned into repository commands, then
deleted.
