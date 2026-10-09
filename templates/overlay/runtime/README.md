# `pdf-overlay/` — generated; do not edit by hand

This directory is **generated** by the formal-companion skill's
`cf overlay sync`: the viewer, its tools, the configuration schema, PDF.js and
a copy of the Literature Links package (`tools/literature_links/`).
`GENERATED.json` records the sha256 of every one of them, and the shard list.

What the viewer shows comes from two places that are yours:

| path | what it is | written by |
|---|---|---|
| `pdf-overlay/overlay.config.json` | presentation and local-build settings | `cf overlay init`, then edited by you |
| `annotations/<section>.jsonl` (at the repository root) | Literature Links annotations, one per line, one file per book section | `cf overlay export` |

No page count, PDF hash, URL, section number or declaration name is written
into the viewer or the tools.

## Reading the overlay locally

```sh
python3 pdf-overlay/tools/serve.py            # fetch and verify the PDF, serve the built site
python3 pdf-overlay/tools/serve.py --build    # run the build commands and build_site.py first
python3 pdf-overlay/tools/build_site.py       # publish into an already-built site
```

The PDF is fetched over HTTPS from the URLs the annotations' source names,
verified against its pinned sha256, cached under `pdf.cache_dir` and served
only on loopback at `pdf.route`; it is never committed. `build_site.py` copies
the viewer into `<site.output_root>/<site.mount>/` and the shards into
`<site.output_root>/annotations/`: the shards' relative targets are relative
to that published layout, not to the repository.

## Checking it in CI

```sh
python3 pdf-overlay/tools/check_generated.py
```

Standard library only. It checks every generated file against
`GENERATED.json`, the config, every shard's sha256 and the Literature Links
scan of it, and that the curation files the shards were exported from have
not changed since.

## Regenerating

From a checkout of the formal-companion skill:

```sh
cf overlay export <companion> --convention attribution-table --commit <sha>
cf overlay sync <companion>
cf overlay check <companion>
```

`cf overlay` writes only here and in `annotations/`.
