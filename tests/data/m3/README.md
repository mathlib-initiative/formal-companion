# M3 reference data: not in this tree

The reference outputs of the two annotation exporters (`cf export`,
docs/exporters.md) are derived from the two private Tao companions, so they
are **maintainer-side**. CLAUDE.md section 1 forbids them here, and
docs/annotations.md section 11 says a document lives in the companion's
repository or a workspace. **No file derived from a private companion may be
committed to this repository**: not a document, a report, a site index or a
page manifest, gzipped or not.

A maintainer keeps them in one directory and names it with
`CF_M3_REFERENCE_DIR`. It holds exactly these six files:

- `measure_theory.annotations.json.gz`: `cf export attribution-table` at
  measure_theory 2cd263a (the first commit whose refinement table is schema
  version 2, which the exporter needs).
- `measure_theory.export-report.json`: that export's report.
- `tao-measure-lean.annotations.json.gz`: `cf export docstring-tags` at
  tao-measure-lean 677ada1.
- `tao-measure-lean.export-report.json`: that export's report.
- `tml-site-xref.json.gz`: `_site/xref.json` of the tao-measure-lean literate
  site rendered from 677ada1 (git-ignored build output of that repository).
- `tml-site-pages.json`: the site pin (written by `cf export site-pages`): the
  pages the producer pins (the 38 module, index and search pages; the `_lazy/`
  fragments are left out) with sha256 and bytes, the site digest
  (docs/annotations.md section 5.1), the site source's id and title, its path
  under the checkout and the render time (docs/exporters.md section 2 gives
  the shape).

Render the site in a tao-measure-lean checkout at 677ada1 (its
`scripts/build_site.sh` without the tarball step):

```sh
cd <tao-measure-lean checkout>          # at 677ada1
lake build TaoMeasure
lake query :literateHtml
rm -rf _site && mkdir -p _site
python3 scripts/lazy_literate.py --source .lake/build/literate-html --output _site
python3 scripts/check_lazy_pages.py --source .lake/build/literate-html --output _site
```

`lazy_literate.py` moves tactic states and proof bodies into `_lazy/`
fragments beside each page (29,729 at 677ada1). The pin leaves them out, and
`--site` counts them rather than refusing them. A render from another commit
need not match the pin.

Pin the site and copy its index (from this repository's checkout):

```sh
M3="$CF_M3_REFERENCE_DIR"
SITE=<tao-measure-lean checkout>/_site
gzip -n -9 -c "$SITE/xref.json" > "$M3/tml-site-xref.json.gz"
python3 scripts/cf.py export site-pages "$SITE" --out "$M3/tml-site-pages.json" \
    --exclude '*/_lazy/*' --source-id <id> --title <title> --rendered-at <time>
```

When re-pinning, pass the `source_id`, `title` and `rendered_at` the existing
`tml-site-pages.json` holds, or the reference's site source will differ.

Regenerate the documents and reports from the pinned checkouts, the published
edition's PDF and the rendered site (docs/exporters.md section 5):

```sh
M3="$CF_M3_REFERENCE_DIR"
python3 scripts/cf.py export attribution-table <measure_theory checkout> --commit 2cd263a \
    --out "$M3/measure_theory.annotations.json" --lean-build <its .lake/build/lib/lean>
python3 scripts/cf.py export docstring-tags <tao-measure-lean checkout> --commit 677ada1 \
    --pdf <the published PDF> --xref "$M3/tml-site-xref.json.gz" \
    --site-pages "$M3/tml-site-pages.json" --site <its _site> \
    --out "$M3/tao-measure-lean.annotations.json"
gzip -n -9 "$M3/measure_theory.annotations.json" "$M3/tao-measure-lean.annotations.json"
```

Each `cf export` writes its `.export-report.json` beside `--out`.

Tests that read these files (`tests/test_export.py`'s reproduction and
reference checks, `tests/test_review_tags.py`'s known answers) skip with a
message naming `CF_M3_REFERENCE_DIR` when it is unset or a file is missing;
the synthetic companions run everywhere.
