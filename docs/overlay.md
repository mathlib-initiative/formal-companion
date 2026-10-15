# The generated PDF overlay

A companion's PDF overlay shows a source PDF with its items outlined and, for
each item, the declarations that formalize it. It is **generated**: the
viewer, its tools and their schemas are copied verbatim from
`templates/overlay/runtime/` by `cf overlay sync`, and everything specific to
one companion is data the viewer and the tools read at run time:

| path in the companion | written by | what it holds |
|---|---|---|
| `annotations/<section>/[<item>/]<name>.json`, `_defaults.json` | `cf overlay export` | the companion's [Literature Links](../vendor/literature_links/) annotations, one link per file, one folder per book section, with folder defaults (section 7) |
| `pdf-overlay/overlay.config.json` | `cf overlay init`, then edited | presentation and local-build settings (section 3) |
| `pdf-overlay/annotations.bundle.json` | `cf overlay sync` | every annotation as the reader reads it, defaults applied, with its file's path, in file order: what the viewer reads, in one fetch (section 6) |
| `pdf-overlay/GENERATED.json` | `cf overlay sync` (and `export`) | the generator commit, the Literature Links release, the sha256 of every copied file, and the annotation files' count, manifest sha256 and bundle sha256 (section 6) |

A companion's CI checks all of it with `python3 pdf-overlay/tools/check_generated.py`,
which needs nothing but CPython and the checkout.

The overlay reads **Literature Links** at commit
`3ad716af885ca7ebe8091f824fca829e03492f00` only (untagged: version 1.0.0 with
unreleased changes), vendored under `vendor/literature_links/` (sealed by
`vendor/literature_links.manifest.json`) and shipped to companions as
`pdf-overlay/tools/literature_links/`. The skill's other tools keep the
annotation document format of `vendor/formal_companion_annotations/`;
`cf overlay export` converts one into the other.

**Contents**: 1 rules · 2 layout · 3 `overlay.config.json` · 4 the viewer ·
5 the runtime tools · 6 `cf overlay` and `GENERATED.json` · 7 the annotation files

## 1. Rules

1. **No book in the templates.** No file under `templates/overlay/` or
   `scripts/overlay/` names a book, an edition, a page count, a PDF hash, a
   Lean namespace, a section number or a companion. Tests use synthetic data.
2. **Verbatim copies.** `sync` never substitutes values into a template.
3. **Standard library only** for every Python file, and no build step for the
   JavaScript.
4. **The PDF is never copied, committed or served off loopback.** The tools
   fetch it from the URLs the annotations' source names, verify it against the
   pinned sha256 (and `bytes`), cache it under the config's ignored cache
   directory and serve it only on `127.0.0.1`; the viewer verifies it again.
5. **One exception to "never write to a companion repo".** `cf overlay init`,
   `export` and `sync`, run by a person by name, write
   `<companion>/pdf-overlay/` and (`export`) `<companion>/annotations/`, and
   nowhere else; they refuse either directory when it is a symlink or resolves
   outside the companion, refuse to write through a symlinked component, and
   run no git in the companion (CLAUDE.md section 4, CONTRIBUTING.md
   invariant 3, SKILL.md rule 10).
6. **Hardening carries over**: TLS verification, HTTPS-only redirects, atomic
   writes, loopback-only binding, the CSP, no HTML-string injection, and PDF.js
   with `isEvalSupported: false`.

## 2. Layout

```text
templates/overlay/
  overlay.config.json.example
  runtime/                     copied verbatim into <companion>/pdf-overlay/
    README.md THIRD_PARTY.md
    schema/                    overlay-config-v1.schema.json
    tools/serve.py build_site.py check_generated.py overlay_annotations.py
    viewer/index.html viewer.js viewer-core.js viewer.css
    vendor/pdfjs/
  tests/                       node --test suites (not copied)
scripts/overlay/
  config.py   sync.py   export.py   shards.py   defaults.py
scripts/cfcmd/overlay.py
```

A companion after `export` and `sync`:

```text
annotations/1.1/definition-1.1.1__key__Notes.Box.json 1.1/... 1.2/... ...
pdf-overlay/GENERATED.json annotations.bundle.json overlay.config.json README.md THIRD_PARTY.md
            schema/ tools/ (with tools/literature_links/) viewer/ vendor/
```

The annotation files live at the repository root, not in `pdf-overlay/`: they
are the companion's annotations, which any Literature Links reader can scan,
and the overlay is one reader of them. `annotations/` holds a `_defaults.json`,
section folders of annotation files and nothing else.

## 3. `overlay.config.json`, version 1

Unchanged: `schema`, `schema_version`, `source_id`, `title`, `pdf` and `site`
are required; `lede`, `display.strip_prefix`, `storage_key`,
`pdf.cache_dir`, `pdf.route`, `site.output_root`, `site.index`, `site.mount`,
`site.link_text`, `build.commands` (default: none) and `serve` have defaults;
unknown keys are errors. `back_link` is optional: with none, or one whose
`href` the viewer refuses, the top bar shows no back link. `source_id` names the PDF the overlay shows: the
`source_id` in its records' source extension (section 7). `scripts/overlay/config.py`,
`runtime/schema/overlay-config-v1.schema.json` and `runtime/tools/check_generated.py`
carry the same rules and patterns, held together by `tests/test_overlay_config.py`.

## 4. The viewer (`runtime/viewer/`)

At start-up it fetches `../overlay.config.json`, then `../GENERATED.json`,
then the bundle its `annotations` block names (one fetch), checking it against
its recorded sha256. Each annotation's base URL is its own file's in the
published site (`<site root>/annotations/<section>/[<item>/]<name>.json`, the site
root `../` once per `site.mount` component, plus one), since Literature Links
resolves a relative reference against the file that holds the annotation.
It keeps the records whose `source` is a PDF with the config's `source_id`;
they must all pin one sha256.

From the records alone it builds:

- regions: `location.page` and `location.rect` (fractions of the displayed
  page from the top left, so drawn through the page's own rotation), or the
  extension's `quads`; one region per extension `region` id, its items in
  the extension's `region_items` order; a click opens the first item with a
  card;
- items: one per extension `item`, labelled by `label`, with `status` as its
  coverage word, `note` as its coverage note, the extension's `locator` (its
  `text`, else its printed and physical pages, less the segment that repeats
  the label and cut at the first `;` or ` / `, on one line) and `verdicts` (each one line: kind, summary and resolution,
  shown only on request), and one footer line naming the link `provenance`
  methods and whether a person reviewed the links and verdicts;
- cards: one per record with a `target`, once per item however many regions
  repeat it, in the order key, attributed, support, example, helper (then by
  record id), with the extension's snippet, destination resolution and
  fallback label, declaration kind and flags, and declaration verdict. The
  target resolves against the annotation file's URL and becomes a link only
  when it is `https:` or a page of the same origin, and only when the
  extension says it is a rendered page. The panel shows one card per key
  declaration, each with its statement (eight, then a button for the rest;
  "N key declarations" when there are two or more, never a heading that says
  what they state), then the rest under "Related": one line each, a plain
  link to its source with no statement, headed by its count (and by the role
  more than half of them share);
- an item with no card says so, and names its ledger clusters;
- the regions on a page are in reading order (top, then left), whatever
  order the files came in; with nothing selected the panel lists the page's
  items in that order, with how many a key declaration states, and a page
  with none points to the nearest pages that have some.

A record it cannot place is dropped with a notice, never guessed at. Every
string reaches the DOM through `textContent` or attribute setters.
`viewer-core.js` is the pure half; `viewer.js` the controller.

## 5. Runtime tools (`runtime/tools/`)

Run from the companion root as `python3 pdf-overlay/tools/<tool>.py`; they find
`pdf-overlay/` from their own path and import nothing but their siblings, with
bytecode writing off.

- **`serve.py`**: the PDF identity is the `source` of the first annotation in
  the bundle with the config's `source_id`: `uri` and the book pin's `url`, then the
  extension's `alternate_urls`, over HTTPS only; the pin's `sha256` and the
  extension's `bytes`. Acquire, verify, cache, optionally build, serve on
  loopback.
- **`build_site.py`**: publish `viewer/`, `vendor/`, `overlay.config.json`,
  `GENERATED.json` and the bundle into `<output_root>/<mount>/`, and the
  annotation files and their `_defaults.json` (exactly the bundle's records)
  into `<output_root>/annotations/`: their relative targets are relative
  to this published site layout, not to the repository; replace both
  atomically (refusing a site directory that does not have the layout of
  section 7), insert the index link, write `receipt.json`.
- **`check_generated.py`**: every file `GENERATED.json` lists, with its
  sha256, and nothing else in `pdf-overlay/` but the bundle; the `format`
  block against the shipped package's version; the config; `annotations/`
  holding only the layout of section 7, each file as a recorded export writes
  it, as many annotations as recorded and every file at the manifest sha256;
  the bundle at its recorded sha256 and equal to the records the shipped
  `literature_links` scan reads (each one that differs, is missing or is
  extra is named); the scan finding no problem, with a record of the config's
  PDF; every recorded curation input (at least one when an export is
  recorded) at its recorded sha256, so a stale export fails CI.
- **`overlay_annotations.py`**: reads `GENERATED.json`'s annotations block,
  the bundle and the files for the other three.

## 6. `cf overlay` and `GENERATED.json`

```sh
cf overlay init <companion> --source-id ID --title T [--file-name F] [--force]
cf overlay export <companion> --convention attribution-table --commit SHA [--source-id ID] [-- <cf export args>]
cf overlay sync <companion> [--check]
cf overlay check <companion>
```

- `init` writes `pdf-overlay/overlay.config.json`. Commit it before `export`,
  which refuses a dirty checkout; SHA is that commit.
- `export` runs `cf export` into a temporary file, validates the document,
  converts it (section 7), scans every file with the vendored
  `literature_links`, and only then replaces the contents of `annotations/`
  exactly: files it no longer writes are removed first, so a name that
  changed only in case survives a case-insensitive filesystem (and a
  `<section>.jsonl` shard of the earlier layout that a previous
  `GENERATED.json` lists, at its sha256); anything else there stops it before
  it writes. It records the export, the files' count and manifest sha256 in
  `GENERATED.json` before it removes or writes a file, so `sync` refuses what
  an export that died left.
- `sync` copies the runtime and `vendor/literature_links/` (as
  `tools/literature_links/`), removes what a previous record listed and the
  templates dropped, refuses unlisted files, writes the bundle and records
  it. Files `export` wrote that changed since are refused, not recorded
  (named, when the last bundle holds the export); files nothing exported are
  recorded as they are.
- `sync --check` is read-only and includes the scan; `check` adds the config,
  the inputs' hashes and the overlay's rules (one PDF per `source_id`, pages
  within its `physical_page_count`, targets `https:` or relative and inside
  the site).

```json
{
  "schema": "formal-companion/overlay-generated",
  "schema_version": 3,
  "generator": {"name": "formal-companion", "version": "...", "commit": "<this repo's HEAD>"},
  "format": {"package": "literature-links", "version": "1.0.0", "commit": "<40 hex>"},
  "instance_files": ["overlay.config.json"],
  "files": [{"path": "tools/literature_links/companion.py", "sha256": "..."}],
  "annotations": {
    "directory": "annotations",
    "extension": "org.mathlib-initiative.formal-companion.overlay",
    "export": {"convention": "attribution-table", "commit": "<companion commit>", "source_id": "..."},
    "inputs": [{"path": "<curation file>", "sha256": "..."}],
    "count": 1332,
    "manifest_sha256": "<sha256 of `sha256sum` lines of the files, `_defaults.json` included, sorted by path>",
    "bundle": {"path": "annotations.bundle.json", "sha256": "..."}
  }
}
```

The manifest is `cd <companion> && find annotations -type f | LC_ALL=C sort |
xargs sha256sum | sha256sum`. The bundle is
`{"schema": "formal-companion/overlay-annotations", "schema_version": 1,
"directory", "count", "annotations": [{"path", "annotation"}, ...]}`, one
annotation per line, in path order, folder defaults applied.

`format.tag` appears when the vendored revision is a tagged release.
`export` and `inputs` appear when `cf overlay export` wrote the files; with
`export`, `check` and `check_generated.py` require a non-empty `inputs`.

## 7. The annotation files

`scripts/overlay/shards.py` converts the exporter's annotation document:

| record field | from |
|---|---|
| one record | each (region, item, linked declaration) for roles key, attributed, support, example, helper; one target-less record per region of an item that links nothing |
| `source` | `{type: pdf, uri: <official URL>, pin: {type: book, title, edition, url, retrieved, sha256, isbn, identifiers}}`, only the keys the source record has |
| `location` | `{page, rect: [x, y, width, height]}` from the `PdfRegionSelector` (`normalized` or `pt`, either origin, CropBox or MediaBox, taken as unrotated); a quad region becomes its bounding box |
| `target` | `{uri, pin: {type: repository, url, commit, path, line, module, declaration}}`; `uri` is the rendered page relative to the annotation's file as the site publishes it (`../../<href>#<fragment>` from `<output_root>/annotations/<section>/`, the fallback page, or an `https:` URL), else the declaration's source file |
| `kind`, `label` | the item kind lower-cased; the printed label (kind, number, sub-part, title) |
| `status` | the item's coverage (`partial_only` becomes `partial`), else `formalized` for an item with a key link; a target-less `not_formalized` record carries `{value, pin: {type: repository, url, commit}}` |
| `note`, `id` | the coverage note; `<item>/<role>/<declaration>`, or `<item>` |

Everything else the viewer needs is under the extension key
`org.mathlib-initiative.formal-companion.overlay`:

- on the record: `item`, `region`, `role`, `relation`, `declaration` (`id`,
  `kind`, `private`, `anonymous`, `sorry`), `snippet` (`text`, `line`,
  `truncated`), `destination` (`resolution`, `fallback_label`), `verdicts`
  (the item's: `verdict`, `severity`, `status`, `summary`, `evidence`,
  `resolution`, `sub`, `provenance`: `actor`, `method`,
  `confirmed_by_human`, `note`), `declaration_verdict` (`verdict`, `status`,
  `note`), `provenance` (the link's: `actor`, `method`, `confirmed_by_human`),
  `locator` (the item's: `text`, its clusters' locators; `physical` and, from
  the source's `printed_page_offset`, `printed` pages of its regions),
  `region_items` (a region's items in the order the location's
  `x_item_order` names them, when it has several), `quads`
  (`[x0, y0, …, y3]` per quad), `signoff`, and on a target-less record
  `clusters`;
- on `source`: `source_id`, `bytes`, `physical_page_count`, `alternate_urls`.

The section folder is the item number's first two parts (`1.2.7` is `1.2`);
otherwise the item's section or a cluster locator's `Section N.M`; otherwise
`other` (a two-part number may be an equation's). An item with one record is
a file `<item>__<role>__<declaration>.json`, or `<item>.json` for a record
with no target; an item with several is a folder `<item>/` of
`<role>__<declaration>.json` files. Every character outside `[A-Za-z0-9._-]`
(and a leading `.`) is replaced by `-`. A name longer than 120 characters, a
Windows device name (`con`, `nul`, ...), `_defaults.json`, and every name
that equals another one in its folder ignoring case instead ends in `-` and
the first 10 hex digits of the sha256 of the full unsanitised key, truncated
to fit; the key is followed by `@<region>` when one item drawn in several
regions gives several records the same key.

`scripts/overlay/defaults.py` writes the files with Literature Links folder
defaults: `annotations/_defaults.json` holds what all records share and an
item folder's `_defaults.json` what its records share beyond that (a member
equal in all, or an object in a strict majority, recursively; no field is
named). Each record is written as the merge patch over its defaults (`null`
removes an inherited member). References are compared resolved and written
relative to each file. Files list `$schema, id, kind, label, status, note,
source, location, target, extensions` first, `type` first below, two-space
indent, UTF-8, arrays of scalars on one line. The files are read back with
the vendored reader before anything is written; a record the conversion
cannot carry, or files that do not read back as the records, stop the export.
