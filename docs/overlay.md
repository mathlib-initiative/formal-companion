# The generated PDF overlay

A companion's PDF overlay shows a source PDF with its items outlined and, for
each item, the declarations that formalise it. It is **generated**: the
viewer, its tools and their schemas are copied verbatim from
`templates/overlay/runtime/` by `cf overlay sync`, and everything specific to
one companion is data the viewer and the tools read at run time:

| path in the companion | written by | what it holds |
|---|---|---|
| `annotations/<section>.jsonl` | `cf overlay export` | the companion's [Literature Links](../vendor/literature_links/) annotations, one link per line, one shard per book section (section 7) |
| `pdf-overlay/overlay.config.json` | `cf overlay init`, then edited | presentation and local-build settings (section 3) |
| `pdf-overlay/GENERATED.json` | `cf overlay sync` (and `export`) | the generator commit, the Literature Links release, the sha256 of every copied file, and the shard list (section 6) |

A companion's CI checks all of it with `python3 pdf-overlay/tools/check_generated.py`,
which needs nothing but CPython and the checkout.

The overlay reads **Literature Links 1.0.0** only, vendored under
`vendor/literature_links/` (sealed by `vendor/literature_links.manifest.json`)
and shipped to companions as `pdf-overlay/tools/literature_links/`. The
skill's other tools keep the annotation document format of
`vendor/formal_companion_annotations/`; `cf overlay export` converts one into
the other.

**Contents**: 1 rules · 2 layout · 3 `overlay.config.json` · 4 the viewer ·
5 the runtime tools · 6 `cf overlay` and `GENERATED.json` · 7 the shards ·
8 producers

## 1. Rules

1. **No book in the templates.** No file under `templates/overlay/` or
   `scripts/overlay/` names a book, an edition, a page count, a PDF hash, a
   Lean namespace, a section number or a companion. Tests use synthetic data.
2. **Verbatim copies.** `sync` never substitutes values into a template.
3. **Standard library only** for every Python file, and no build step for the
   JavaScript.
4. **The PDF is never copied, committed or served off loopback.** The tools
   fetch it from the URLs the shards' source names, verify it against the
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
    tools/serve.py build_site.py check_generated.py overlay_shards.py
    viewer/index.html viewer.js viewer-core.js viewer.css
    vendor/pdfjs/
  tests/                       node --test suites (not copied)
scripts/overlay/
  config.py   sync.py   export.py   shards.py   geometry.py   destinations.py
scripts/cfcmd/overlay.py
```

A companion after `export` and `sync`:

```text
annotations/1.1.jsonl 1.2.jsonl ...
pdf-overlay/GENERATED.json overlay.config.json README.md THIRD_PARTY.md
            schema/ tools/ (with tools/literature_links/) viewer/ vendor/
```

The shards live at the repository root, not in `pdf-overlay/`: they are the
companion's annotations, which any Literature Links reader can scan, and the
overlay is one reader of them. `annotations/` holds shards and nothing else.

## 3. `overlay.config.json`, version 1

Unchanged: `schema`, `schema_version`, `source_id`, `title`, `pdf` and `site`
are required; `lede`, `back_link`, `display.strip_prefix`, `storage_key`,
`pdf.cache_dir`, `pdf.route`, `site.output_root`, `site.index`, `site.mount`,
`site.link_text`, `build.commands` (default: none) and `serve` have defaults;
unknown keys are errors. `source_id` names the PDF the overlay shows: the
`source_id` in its records' source extension (section 7). `scripts/overlay/config.py`,
`runtime/schema/overlay-config-v1.schema.json` and `runtime/tools/check_generated.py`
carry the same rules and patterns, held together by `tests/test_overlay_config.py`.

## 4. The viewer (`runtime/viewer/`)

At start-up it fetches `../overlay.config.json`, then `../GENERATED.json`,
then every shard the `annotations` block lists, from the site root
(`../` once per `site.mount` component, plus one), checking each against its
recorded sha256. It keeps the records whose `source` is a PDF with the
config's `source_id`; they must all pin one sha256.

From the records alone it builds:

- regions: `location.page` and `location.rect` (fractions of the displayed
  page from the top left, so drawn through the page's own rotation), or the
  extension's `quads`; one region per extension `region` id, its items in
  the extension's `region_items` order; a click opens the first item with a
  card;
- items: one per extension `item`, labelled by `label`, with `status` as its
  coverage line, `note` as its coverage note, the extension's `locator` (its
  `text`, else its printed and physical pages) and `verdicts` (each ending
  `[<provenance method>, <status>; <resolution>]`), and a line naming the
  key and attributed counts and each link `provenance` (method, confirmed
  or unconfirmed);
- cards: one per record with a `target`, once per item however many regions
  repeat it, in the order key, attributed, support, example, helper, with the
  extension's snippet, destination resolution and fallback label, declaration
  kind and flags, and declaration verdict. The target resolves against the
  shard's URL (Literature Links' rule for relative references) and becomes a
  link only when it is `https:` or a page of the same origin, and only when
  the extension says it is a rendered page;
- an item with no card says so, and names its ledger clusters.

A record it cannot place is dropped with a notice, never guessed at. Every
string reaches the DOM through `textContent` or attribute setters.
`viewer-core.js` is the pure half; `viewer.js` the controller.

## 5. Runtime tools (`runtime/tools/`)

Run from the companion root as `python3 pdf-overlay/tools/<tool>.py`; they find
`pdf-overlay/` from their own path and import nothing but their siblings, with
bytecode writing off.

- **`serve.py`**: the PDF identity is the `source` of the first record with
  the config's `source_id`: `uri` and the book pin's `url`, then the
  extension's `alternate_urls`, over HTTPS only; the pin's `sha256` and the
  extension's `bytes`. Acquire, verify, cache, optionally build, serve on
  loopback.
- **`build_site.py`**: publish `viewer/`, `vendor/`, `overlay.config.json` and
  `GENERATED.json` into `<output_root>/<mount>/`, and the shards (each at its
  recorded sha256) into `<output_root>/annotations/`: their relative targets
  are relative to this published site layout, not to the repository; replace
  both atomically (refusing a site directory that holds anything but shards),
  insert the index link, write `receipt.json`.
- **`check_generated.py`**: every file `GENERATED.json` lists, with its
  sha256, and nothing else in `pdf-overlay/`; the `format` block against the
  shipped package's version; the config; exactly the listed shards in
  `annotations/`, each at its sha256 and passing the shipped `literature_links`
  scan, with a record of the config's PDF; every recorded curation input (at
  least one when an export is recorded) at its recorded sha256, so a stale
  export fails CI.
- **`overlay_shards.py`**: reads `GENERATED.json`'s shard list and the shards
  for the other three.

## 6. `cf overlay` and `GENERATED.json`

```sh
cf overlay init <companion> --source-id ID --title T [--file-name F] [--force]
cf overlay export <companion> --convention attribution-table --commit SHA [--source-id ID] [-- <cf export args>]
cf overlay sync <companion> [--check]
cf overlay check <companion>
cf overlay geometry ... ; cf overlay destinations ...
```

- `export` runs `cf export` into a temporary file, validates the document,
  converts it (section 7), scans every shard with the vendored
  `literature_links`, and only then replaces `annotations/` (refusing any
  file there that is not a shard) and records the export in `GENERATED.json`.
- `sync` copies the runtime and `vendor/literature_links/` (as
  `tools/literature_links/`), removes what a previous record listed and the
  templates dropped, refuses unlisted files, and records the shard list. A
  shard `export` wrote that has changed since is refused, not recorded; shards
  nothing exported are recorded as they are.
- `sync --check` is read-only; `check` adds the config, the scan, the inputs'
  hashes and the overlay's rules (one PDF per `source_id`, pages within its
  `physical_page_count`, targets `https:` or relative and inside the site).

```json
{
  "schema": "formal-companion/overlay-generated",
  "schema_version": 2,
  "generator": {"name": "formal-companion", "version": "...", "commit": "<this repo's HEAD>"},
  "format": {"package": "literature-links", "version": "1.0.0", "commit": "<40 hex>"},
  "instance_files": ["overlay.config.json"],
  "files": [{"path": "tools/literature_links/companion.py", "sha256": "..."}],
  "annotations": {
    "directory": "annotations",
    "extension": "org.mathlib-initiative.formal-companion.overlay",
    "export": {"convention": "attribution-table", "commit": "<companion commit>", "source_id": "..."},
    "inputs": [{"path": "<curation file>", "sha256": "..."}],
    "shards": [{"path": "annotations/1.2.jsonl", "records": 87, "sha256": "..."}]
  }
}
```

`format.tag` appears when the vendored revision is a tagged release.
`export` and `inputs` appear when `cf overlay export` wrote the shards; with
`export`, `check` and `check_generated.py` require a non-empty `inputs`.

## 7. The shards

`scripts/overlay/shards.py` converts the exporter's annotation document:

| record field | from |
|---|---|
| one record | each (region, item, linked declaration) for roles key, attributed, support, example, helper; one target-less record per region of an item that links nothing |
| `source` | `{type: pdf, uri: <official URL>, pin: {type: book, title, edition, url, retrieved, sha256, isbn, identifiers}}`, only the keys the source record has |
| `location` | `{page, rect: [x, y, width, height]}` from the `PdfRegionSelector` (`normalized` or `pt`, either origin, CropBox or MediaBox, taken as unrotated); a quad region becomes its bounding box |
| `target` | `{uri, pin: {type: repository, url, commit, path, line, module, declaration}}`; `uri` is the rendered page relative to the shard as the site publishes it (`../<href>#<fragment>` from `<output_root>/annotations/`, the fallback page, or an `https:` URL), else the declaration's source file |
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

A shard is `<section>.jsonl`: the item number's first two parts (`1.2.7` is
`1.2`); otherwise the item's section or a cluster locator's `Section N.M`;
otherwise `other` (a two-part number may be an equation's). Lines are sorted
by page, position, region, the item's place in the region, item, role and id,
written as canonical JSON (sorted keys, no spaces), each ending in a newline. A
record the conversion cannot carry stops the export.

## 8. Producers

`cf overlay geometry` (item regions from a PDF text layer) and
`cf overlay destinations` (declaration anchors in a built Verso site) write
fragments of the annotation document format for an exporter to read; they
touch no companion.
