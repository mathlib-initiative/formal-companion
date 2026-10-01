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
  measure_theory 00a2438.
- `measure_theory.export-report.json`: that export's report.
- `tao-measure-lean.annotations.json.gz`: `cf export docstring-tags` at
  tao-measure-lean f158509.
- `tao-measure-lean.export-report.json`: that export's report.
- `tml-site-xref.json.gz`: `_site/xref.json` of the tao-measure-lean literate
  site rendered from f158509 (git-ignored build output of that repository).
- `tml-site-pages.json`: the site pin, written by `cf export site-pages`
  (`scripts/annotations/export/site_pages.py`): the site's HTML pages with
  sha256 and bytes, the site digest (docs/annotations.md section 3.1), the
  site source's id and title, its path under the checkout and the render time
  (docs/exporters.md section 2 gives the shape).

**Render the site** in a tao-measure-lean checkout at f158509. These are the
steps of that commit's own `scripts/build_site.sh`, without its last step,
which packs a tarball outside the checkout:

```sh
cd <tao-measure-lean checkout>          # at f158509
lake build TaoMeasure
lake query :literateHtml
rm -rf _site && mkdir -p _site
cp -r .lake/build/literate-html/* _site/
python3 scripts/slim_large_pages.py --site _site
```

`slim_large_pages.py` must be the one at f158509. Later commits replace it
with a lazy loader that splits each section page into many fragment pages,
so a site rendered from them matches no pin. A render
from f158509 gives one page per module plus the landing page, and a re-render
in the PR #17 review matched all 38 pinned pages by sha256 and bytes. Its
`xref.json` was byte for byte the pinned index.

**Pin the site and copy its index** (from this repository's checkout):

```sh
M3="$CF_M3_REFERENCE_DIR"
SITE=<tao-measure-lean checkout>/_site
gzip -n -9 -c "$SITE/xref.json" > "$M3/tml-site-xref.json.gz"
python3 scripts/cf.py export site-pages "$SITE" --out "$M3/tml-site-pages.json" \
    --source-id <id> --title <title> --rendered-at <time>
```

The pages and the digest come from the render. The id, the title and the
render time are the maintainer's words about the render, not its bytes. When
re-pinning, pass the values the existing `tml-site-pages.json` holds, or the
site source's `title` and `retrieved_at` will differ from the reference. A new
pin can leave them out: at export the id then defaults to `<name>-site` and
the title to a generic one, and the writer takes the newest page's
modification time as the render time.

**Regenerate the documents and reports** from the pinned checkouts, the
published edition's PDF and the rendered site (docs/exporters.md section 5):

```sh
M3="$CF_M3_REFERENCE_DIR"
python3 scripts/cf.py export attribution-table <measure_theory checkout> --commit 00a2438 \
    --out "$M3/measure_theory.annotations.json" --lean-build <its .lake/build/lib/lean>
python3 scripts/cf.py export docstring-tags <tao-measure-lean checkout> --commit f158509 \
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
