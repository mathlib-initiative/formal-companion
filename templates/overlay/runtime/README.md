# `pdf-overlay/` — generated; do not edit by hand

This directory is **generated**. The viewer, its tools, its schemas and the
vendored PDF.js were copied here byte for byte by the formal-companion skill's
`cf overlay sync`, and `GENERATED.json` records the sha256 of every one of
them. An edit to any of those files is reverted by the next sync and reported
by the check below in the meantime.

Two files in here are **yours**, and nothing overwrites them without being
asked:

| file | what it is | written by |
|---|---|---|
| `overlay.config.json` | this overlay's presentation and local-build settings | `cf overlay init`, then edited by you |
| `annotations.json` | the annotation document: which declarations formalise which items of the source, and where those items sit | `cf overlay export` |

Everything the viewer shows comes from those two. No page count, page size,
PDF hash, URL, section number or declaration name is written into the viewer or
the tools; they read it at run time, which is why the same generated directory
serves every companion.

## Reading the overlay locally

```sh
python3 pdf-overlay/tools/serve.py
```

That fetches the source PDF from the URLs in the document's source record over
HTTPS, verifies its bytes against the record's `sha256`, caches it under the
ignored directory `overlay.config.json` names (`pdf.cache_dir`), and serves the
built site on the loopback address and port under `serve`. The PDF is reachable
only at `pdf.route`, only from this machine, and it is never copied into the
repository or committed. Stop the server with Ctrl-C.

Useful flags:

```sh
python3 pdf-overlay/tools/serve.py --build       # run the build commands first
python3 pdf-overlay/tools/serve.py --build-only  # build and publish, do not serve
python3 pdf-overlay/tools/serve.py --refresh-pdf # re-fetch even if the cache verifies
python3 pdf-overlay/tools/serve.py --help
```

To publish the viewer into an already-built site without serving anything:

```sh
python3 pdf-overlay/tools/build_site.py
```

It copies the viewer, the vendored PDF.js and the two instance files into
`<site.output_root>/<site.mount>/`, writes a `receipt.json` of what it wrote,
and inserts one link block into the site's index page. It runs no build tool of
its own.

## Checking the directory in CI

```sh
python3 pdf-overlay/tools/check_generated.py
```

Exit status 0 or 1, one line per problem, standard library only. It verifies
that every file `GENERATED.json` lists is here with its recorded sha256, that
nothing else is here but the two instance files, that `overlay.config.json`
follows the configuration schema in `schema/`, and that `annotations.json`
parses and carries the source the config names. It is cheap enough to run on
every push, and it is what notices a hand edit, a half-finished merge or a
stale copy.

## Regenerating

From a checkout of the formal-companion skill, with this repository's path as
`<companion>`:

```sh
cf overlay sync <companion>            # refresh this directory from the templates
cf overlay sync <companion> --check    # read-only: name what differs
cf overlay check <companion>           # the above, plus the config and the document
cf overlay export <companion> --convention attribution-table --commit <sha>
```

`cf overlay` is the only part of that skill that writes anything into a
companion repository, and it writes only inside this directory.

## What is in here

```text
GENERATED.json        every generated file with its sha256, and the commit that wrote it
overlay.config.json   yours: presentation and local-build settings
annotations.json      yours: the annotation document
README.md             this file
THIRD_PARTY.md        the vendored PDF.js, its version and its licence
schema/               JSON Schemas for the two instance files
tools/                serve.py, build_site.py, check_generated.py
viewer/               the viewer: index.html, viewer.js, viewer-core.js, viewer.css
vendor/pdfjs/         PDF.js, unmodified (see THIRD_PARTY.md)
```

The two schemas are Draft 2020-12 and need no network: validate the document
and the config offline with any validator you like.

## Requirements

CPython 3.11 or newer for the three tools, and a browser for the viewer. There
is nothing to install and no build step: the viewer is plain ES modules.
