# `pdf-overlay/`: generated, do not edit by hand

This directory is **generated** by the formal-companion skill's
`cf overlay sync`: the viewer, its tools, the configuration schema, PDF.js and
a copy of the Literature Links package (`tools/literature_links/`).
`GENERATED.json` records the sha256 of every one of them, the annotation count
and the manifest sha256 of the annotation files, and the sha256 of
`annotations.bundle.json`, which holds every annotation, folder defaults
applied, with its file's path for the viewer.

What the viewer shows comes from these places that are yours:

| path | what it is | written by |
|---|---|---|
| `pdf-overlay/overlay.config.json` | presentation and local-build settings | `cf overlay init`, then edited by you |
| `annotations/<section>/[<item>/]<name>.json` (at the repository root) | Literature Links annotations, one per file, one folder per book section and per item with several, shared members in `_defaults.json` | `cf overlay export` |
| `pdf-overlay/verso.json` (optional) | hovers for a site with no Verso pages, cut from a Verso build of the project made elsewhere | `pdf-overlay/tools/verso_extract.py --pages <literate-html> --bundle <bundle> --out <file> --commit <sha>`, run where the build is |

No page count, PDF hash, URL, section number or declaration name is written
into the viewer or the tools.

## Reading the overlay locally

```sh
python3 pdf-overlay/tools/serve.py            # fetch and verify the PDF, serve the built site
python3 pdf-overlay/tools/serve.py --build    # run the build commands and build_site.py first
python3 pdf-overlay/tools/build_site.py       # publish into an already-built site
```

The PDF is fetched over HTTPS from the URLs the annotations' PDF ends name,
verified against their sha256, cached under `pdf.cache_dir` and served only on
loopback at `pdf.route`; it is never committed. A PDF under an open license
may be published with the site instead: with `pdf.publish` naming its license,
`build_site.py` also copies the verified PDF to `<site.mount>/<pdf.file_name>`,
and the built site works from a static host such as GitHub Pages
(`docs/overlay.md` section 3 in the formal-companion skill).

`build_site.py` copies the viewer and the bundle into
`<site.output_root>/<site.mount>/` and the annotation files into
`<site.output_root>/annotations/`; a declaration's rendered page is a page
of `<site.output_root>`. It also writes
`<site.mount>/verso.json` from the site's Verso pages: each card's
statement with its tokens and hovers (the viewer draws the key cards'). A
site without them gets your committed `pdf-overlay/verso.json`, less the
statements whose snippet changed since, or an empty one; a committed file
cut from a build of a commit no card pins is refused, and so is one with a
statement whose card pins a release tag rather than a full commit.

## Checking it in CI

```sh
python3 pdf-overlay/tools/check_generated.py
```

Standard library only. It checks every generated file against
`GENERATED.json`, the config, the annotation files against the recorded
manifest, the form the export writes and the bundle (naming any file or
record that differs), the Literature Links scan, and that the curation files
they were exported from have not changed since.

## Regenerating

From a checkout of the formal-companion skill:

```sh
cf overlay export <companion> --convention attribution-table --commit <sha>
cf overlay sync <companion>
cf overlay check <companion>
```

`cf overlay` writes only here and in `annotations/`.

## Reading the PDF alone

Choose **Read PDF** in the viewer's top bar to hide the annotation boxes and
Lean panel. Page, zoom and rotation controls still work, and the PDF text is
selectable. Choose **Show links**, or press Escape, to restore the annotations
and your selected passage. Reading mode lasts for the current visit.
