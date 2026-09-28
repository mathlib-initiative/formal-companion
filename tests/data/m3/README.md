# M3 reference data

The reference outputs of the two annotation exporters (`cf export`,
docs/exporters.md) over the two Tao companions, and the site facts the second
one needs. Format 1.0, docs/annotations.md.

- `measure_theory.annotations.json.gz`: `cf export attribution-table` at master
  00a2438 (432 items, 3,565 declarations, 61 artifacts, 5,093 memberships, 509
  locations, 627 links, 2,884 destinations, 542 snippets, 11 verdicts, 3
  changelog entries). Validates with `scripts/annotations/validate.py
  --canonical`, no warnings.
- `tao-measure-lean.annotations.json.gz`: `cf export docstring-tags` at main
  f158509 (651 items, 1,544 declarations, 2,201 locations, 1,169 links, 1,544
  destinations, 377 unattributed). Same validation.
- `measure_theory.export-report.json`, `tao-measure-lean.export-report.json`:
  the exporters' reports (counts, the lossy and bent buckets, problems).
- `tml-site-xref.json.gz`: `_site/xref.json` of the tao-measure-lean literate
  site rendered from f158509 (git-ignored build output; the cloud cannot
  rebuild it).
- `tml-site-pages.json`: the site pin: the 38 HTML pages of that site with
  sha256 and bytes, the site digest as docs/annotations.md section 3.1 defines
  it, the site source's id and title, its path under the checkout and the
  render time.

`tests/test_export.py` regenerates both documents from the pinned checkouts
when `CF_MT`, `CF_TML` and `CF_TAO_PDF` name them and compares record for
record. The documents were the prototype exporters' output until the commands
replaced them, with three deliberate differences the test's `DELIBERATE` list
and docs/exporters.md section 6 record. Regenerate with the commands in
docs/exporters.md section 5 and `gzip -n -9`.
