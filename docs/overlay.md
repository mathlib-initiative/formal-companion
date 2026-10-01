# The generated PDF overlay

A companion's `pdf-overlay/` directory is **generated**. Nothing in it is
written by hand for one book: the viewer, its tools, its schemas and the
vendored PDF.js are copied verbatim from `templates/overlay/runtime/` in this
repository by `cf overlay sync`, and everything specific to one companion
comes from two data files that the viewer and the tools read at run time:

| file | written by | what it holds |
|---|---|---|
| `overlay.config.json` | `cf overlay init`, then edited by the companion | presentation and local-build settings (section 3) |
| `annotations.json` | `cf overlay export` (any `cf export` convention) | the companion's annotation document (`docs/annotations.md`) |
| `GENERATED.json` | `cf overlay sync` | which formal-companion commit generated the directory, which annotation format release it was generated against, and the sha256 of every copied file |

Because the copied files are byte-identical to the templates, a companion's
CI can check the whole directory with `python3 pdf-overlay/tools/check_generated.py`,
which needs nothing but the standard library and the directory itself.

This document is the contract for `templates/overlay/` and `scripts/overlay/`.
The implementation it describes was ported from the measure_theory companion's
hand-written overlay; behaviour this document lists as kept must be kept. That
companion's own file names appear below as prose, because they are files of
its repository and never of this one.

**The annotation format is a separate specification, and this repository holds
exactly one pin on it.** Every "format section" reference below is a section of
formal-companion-annotations, the release vendored under
`vendor/formal_companion_annotations/` and sealed by
`vendor/formal_companion_annotations.manifest.json`
(`docs/annotations.md` here is only a pointer to it): records are its section
3, locations and selectors its section 4, ids its section 5. The vendored
package — its `formal_companion_annotations.vocab` and its schema — is
authoritative wherever this document and it could be read differently, and
**no file outside `vendor/` states the format's `schema` value or its major
version**. The format repository moves ahead on its own, so bumping the
vendored release is the only edit a format upgrade needs; see "Upgrading the
format" at the end of this document.

**Contents**: 1 rules · 2 layout · 3 `overlay.config.json` · 4 the viewer ·
5 the runtime tools · 6 `cf overlay` · 7 the producers · 8 what the port
leaves behind · upgrading the format

## 1. Rules

1. **No book in the templates.** No file under `templates/overlay/` or
   `scripts/overlay/` names a book, an edition, a page count, a page size, a
   PDF hash, a Lean namespace, a section number, a geometry batch or a
   companion. Those come from `annotations.json` and `overlay.config.json`.
   Tests use synthetic documents and synthetic PDFs only (CONTRIBUTING.md
   invariant 4).
2. **Verbatim copies.** `sync` never substitutes values into a template. A
   template that needs a per-companion value reads it at run time from the
   config or the document. (`init` is not a copy: it fills the example config
   in, which is the one file the companion then owns and edits.)
3. **Standard library only** for every Python file, and no build step for
   the JavaScript (plain ES modules). The runtime tools must run on a machine
   that has only CPython 3.11+ and, for the viewer, a browser.
4. **The PDF is never copied, committed or served off loopback.** The tools
   fetch it from the URLs in the document's source record, verify its bytes
   against the record's `sha256` (and `bytes` when present), cache it under
   the config's ignored cache directory, and serve it only on `127.0.0.1`
   at the config's route. The viewer verifies the bytes again before PDF.js
   renders them.
5. **One exception to "never write to a companion repo".** `cf overlay init`,
   `cf overlay export` and `cf overlay sync` write into
   `<companion>/pdf-overlay/` and nowhere else, only when a person runs them
   by name, never through git, and they refuse a `pdf-overlay` that is a
   symlink or resolves outside the companion, and refuse to write through a
   symlinked component inside it. Every other command keeps the rule
   (CLAUDE.md section 4, CONTRIBUTING.md invariant 3, SKILL.md rule 10).
6. **Hardening carries over.** The measure_theory implementation's TLS
   verification, redirect policy, atomic writes, path and href hardening,
   loopback-only binding, CSP and the PDF.js `isEvalSupported: false` option
   are kept. Its refusal of symlinked executables is not: a tool on `PATH`
   is resolved with `realpath` and the target must be a regular executable.

## 2. Layout

In this repository:

```text
templates/overlay/
  overlay.config.json.example  the example `cf overlay init` fills in
  runtime/                     copied verbatim into <companion>/pdf-overlay/
    README.md                  what the directory is and how to use it
    THIRD_PARTY.md             PDF.js, its version and its licence
    schema/
      overlay-config-v1.schema.json   the config, Draft 2020-12
    tools/
      serve.py             acquire + verify the PDF, optional build, loopback server
      build_site.py        integrate the viewer into a built site
      check_generated.py   verify GENERATED.json, the config and the document
    viewer/
      index.html viewer.js viewer-core.js viewer.css
    vendor/pdfjs/          PDF.js, unchanged
  tests/                   node --test suites for the viewer (not copied); run them with
                           `node --test templates/overlay/tests/*.test.mjs`
scripts/overlay/
  config.py                    load and check overlay.config.json
  sync.py                      init, sync, check
  export.py                    run a cf export convention into pdf-overlay/annotations.json
  geometry.py                  item regions from a PDF text layer, as annotation records
  destinations.py              declaration destinations from a built Verso literate site
scripts/cfcmd/overlay.py       cf overlay init|sync|check|export|geometry|destinations
```

In a companion, after `sync`:

```text
pdf-overlay/
  GENERATED.json overlay.config.json annotations.json
  README.md THIRD_PARTY.md schema/ tools/ viewer/ vendor/
```

`pdf-overlay/schema/annotations.schema.json` is **not** a template: `sync`
copies it from the vendored release itself, through
`formal_companion_annotations.validate.SCHEMA_PATH`, whatever that release
names its own file, and lists it in `GENERATED.json` `files` with its sha256
like every other synced file. A companion can therefore validate its document
with any Draft 2020-12 validator offline, and the schema it validates against
is the schema `cf overlay check` validates against — there is no second copy to
drift. `tests/test_format_pin.py` compares the two byte for byte.

The name carries no major version on purpose. The generated runtime reads the
document identity it accepts out of that file — the `schema` value from its
`properties.schema.const`, the accepted `schema_version` from a full match of
its `properties.schema_version.pattern` — so a release that renames the
identity changes the file's contents and nothing else. No file under
`templates/overlay/runtime/` may carry an annotation schema identity or a
format major as a literal, and `tests/test_format_pin.py` fails on one.

## 3. `overlay.config.json`, version 1

```json
{
  "schema": "formal-companion/overlay-config",
  "schema_version": 1,
  "source_id": "<the id of a kind:pdf source in annotations.json>",
  "title": "<page title and heading>",
  "lede": "<one sentence under the heading>",
  "back_link": {"href": "../../index.html", "text": "Back to the site"},
  "display": {"strip_prefix": "<optional declaration-name prefix to hide on cards>"},
  "storage_key": "formal-companion-overlay-page",
  "pdf": {
    "file_name": "<cache file name>",
    "cache_dir": ".lake/overlay-pdf",
    "route": "/__overlay_pdf__/<file name>"
  },
  "site": {
    "output_root": ".lake/build/literate-html",
    "index": "index.html",
    "mount": "pdf-overlay",
    "link_text": "<text of the link injected into the site index>"
  },
  "build": {"commands": [["lake", "build"], ["lake", "query", ":literateHtml"]]},
  "serve": {"host": "127.0.0.1", "port": 8000}
}
```

`schema`, `schema_version`, `source_id`, `title`, `pdf` and `site` are
required, and `pdf.file_name` is the only required key inside them. Everything
else has a default: the ones shown above for `back_link`, `storage_key`,
`pdf.cache_dir`, `pdf.route` (the directory `/__overlay_pdf__` plus the file
name), `site.output_root`, `site.index` and `site.mount`; `site.link_text`
defaults to `title`; `lede` and `display.strip_prefix` default to nothing
shown; and `build.commands` defaults to the **empty list**, because a generic
overlay knows no build tool. The `lake` commands above are an example a Lean
companion edits to suit itself, and they are what the shipped
`templates/overlay/overlay.config.json.example` carries.

The rules on the values:

- `pdf.file_name` is one plain file name (`^[A-Za-z0-9_][A-Za-z0-9._-]*$`).
- `pdf.route` MUST start with `/__`, name a file and contain no `..`.
- `pdf.cache_dir`, `site.output_root`, `site.index` and `site.mount` MUST be
  relative, normalised, traversal-free paths inside the companion, with no
  backslash and no control character.
- `serve.host` MUST be a loopback address, written `127.x.y.z` or `::1`.
- `serve.port` MUST be an integer in 1..65535.
- `build.commands` is an array of argv arrays; each is run with no shell.
- Unknown keys are an error, at every level.

`scripts/overlay/config.py`,
`templates/overlay/runtime/schema/overlay-config-v1.schema.json` and
`templates/overlay/runtime/tools/check_generated.py` agree on these rules.
The three carry the same four pattern strings, and
`tests/test_overlay_config.py` fails when one of the strings differs or when
the three disagree on any config in its table.
`templates/overlay/runtime/tools/serve.py` is a fourth reader of the same keys;
`config.py` follows it key for key and is stricter in exactly the two places
its docstring names, never laxer.

The PDF's identity is not in the config: `sha256`, `bytes`, `url`,
`alternate_urls`, `physical_page_count`, `page_size` and `page_sizes` come
from the source record named by `source_id`.

## 4. The viewer (`runtime/viewer/`)

`index.html` is static and generic: its title and heading are a neutral
placeholder that `viewer.js` replaces from the config. It keeps the CSP of
the measure_theory viewer (`default-src 'self'`, no inline script or style,
`connect-src 'self'`).

At start-up the viewer fetches `../overlay.config.json`, then
`../schema/annotations.schema.json`, then `../annotations.json`, all three
with the size-capped `checkedFetch` of the original. It refuses (an error
message on the page, never a blank page) a schema file that is missing or
states no `properties.schema.const` or `properties.schema_version.pattern`,
and a document whose `schema` is not the `const` that file states, whose
`schema_version` does not match that file's `pattern` end to end, or that has
no `kind: pdf` source with the config's `source_id` carrying `sha256` and
`physical_page_count`. `documentFormat(schemaJson)` in `viewer-core.js` is
that reading, and it is a pure function the node suite drives against a
synthetic format.

It then builds, purely from the document:

- page → regions: every `locations[]` entry whose `target.source` is the
  config's source and one of whose `target.selector` alternatives is, or is
  refined by (`refinedBy`), a `PdfRegionSelector`; `rect` and `quads`,
  `normalized` and `pt` units, `top_left` and `bottom_left` origins and the
  source's `page_size` / `page_sizes` are all honoured when converting to
  viewport space (format sections 4.2 and 4.4);
- region → items (the location's `items`), item → links by role, in the
  order `key`, `attributed`, `support`, `example`, `helper`; a link with
  `orphaned: true` is not shown;
- link → declaration → its snippet (`snippets[]`), its destination
  (`destinations[]`, `exact` with `href#fragment`, or `fallback` with its
  visible `fallback_label`), and the verdicts on its item (`verdicts[]`:
  `verdict`, `severity`, `status`, `summary`, per-declaration `declarations[]`
  and `resolution`, format section 3.7);
- item coverage: `coverage` and `coverage_note` are shown on the card with
  a generic one-line summary per format value (format section 3.2:
  `formalized`, `partial`, `not_formalized`, `out_of_scope`, `support_only`,
  `example_only`; the viewer also reads the spellings `partial_only` and
  `private_only`, which no 1.0 document may carry, so a document written to
  an older wording still reads);
- an item with no links but with `clusters` is shown through its clusters'
  `memberships`, labelled as a fallback (format section 3.3).

Kept from the measure_theory viewer: page navigation and the page input,
region highlighting and selection, the card list and its ordering, verdict
badges and their `fixed` / superseded display, keyboard shortcuts (ignored
when a modifier key is held or focus is in an input, so arrow keys still
scroll the page), the remembered page under `storage_key` (every
`localStorage` access in `try`/`catch`), loading, empty and error states.

Security: every string from the document reaches the DOM through
`textContent` or attribute setters, never `innerHTML`; an `href` is used only
if it is a relative `site_path` (no scheme, no `..`, no leading `/`) resolved
against `back_link`'s site root, or an `https:` URL; fragments are
percent-encoded; the PDF is requested only from `config.pdf.route`;
PDF.js runs with `isEvalSupported: false`.

`viewer-core.js` holds the pure functions (document indexing, coordinate
conversion, href checks, sha256 verification) so `node --test` can test them
without a DOM; `viewer.js` holds the DOM controller with the injectable
`fetch`/`document`/`storage` seams the original tests used. The suites are
`templates/overlay/tests/`, and they are not copied into a companion.

## 5. Runtime tools (`runtime/tools/`)

All three are standalone stdlib scripts, run from the companion root as
`python3 pdf-overlay/tools/<tool>.py`. They locate `pdf-overlay/` from their
own file, not from the working directory.

- **`serve.py`** (a port of measure_theory's own `official_pdf_workflow.py`):
  acquire the PDF from the source record's `url`, then `alternate_urls`,
  over HTTPS only (redirects allowed only to HTTPS), stream to a temporary
  file in the cache directory, verify `bytes` and `sha256`, move it into
  place atomically; on a warm run revalidate the cached file and re-fetch
  only when it fails (`--refresh-pdf` forces it). With `--build`, run each
  argv in `config.build.commands` (no shell) and then `build_site.py`. Then
  serve `site.output_root` on `serve.host:serve.port`, exposing the cached
  PDF only at `pdf.route`, refusing path traversal and directory listings,
  until interrupted. `--build-only` stops before serving. A second run that
  finds the port busy says so and exits. Exit codes and one-line `STOP`
  messages as in the original.
- **`build_site.py`** (a port of its `build_combined_site.py`): copy `schema/`,
  `viewer/`, `vendor/`, `overlay.config.json` and `annotations.json` into
  `<output_root>/<mount>/` — `schema/` because the viewer fetches the format
  schema from the mount — replace any previous copy atomically (never stop
  on stale output), insert or replace one link block between
  `<!-- formal-companion-overlay-link -->` and
  `<!-- /formal-companion-overlay-link -->` in `<output_root>/<index>`, and
  write `<output_root>/<mount>/receipt.json` with the sha256 of every file it
  wrote. It does not run Lake; `serve.py --build` does that first.
- **`check_generated.py`**: verify that every file listed in
  `GENERATED.json` exists with its sha256, that `pdf-overlay/` holds no
  other files except `overlay.config.json` and `annotations.json`, that
  `GENERATED.json`'s `format` block is well formed and agrees with the
  shipped schema, that the config satisfies section 3, and that
  `annotations.json` parses, carries the `schema` and a `schema_version` that
  schema states, and has the config's source as a `kind: pdf` source with
  `sha256`. Exit 0 or 1 with one line per problem. It does not run the
  format's own checker — that is a package this directory deliberately does
  not depend on, and `cf overlay check` runs it instead.

## 6. `cf overlay`

```sh
cf overlay init <companion> --source-id ID --title T [--file-name F] [--force]
cf overlay sync <companion>
cf overlay sync <companion> --check
cf overlay check <companion>
cf overlay export <companion> --convention attribution-table --commit SHA
cf overlay geometry --pdf F --first N --last M --source-id ID --verifier-id V --method agent_visual --out FRAGMENT
cf overlay destinations --site DIR --doc annotations.json --out FRAGMENT
```

| command | does |
|---|---|
| `init` | write `pdf-overlay/overlay.config.json` from the example, with `source_id`, `title`, the cache file name (default `<source-id>.pdf`) and the route filled in, refusing to overwrite without `--force` |
| `sync` | copy every file under `templates/overlay/runtime/` into `pdf-overlay/`, add the vendored format's own schema as `pdf-overlay/schema/annotations.schema.json`, delete files a previous `GENERATED.json` listed that are no longer generated, write `GENERATED.json`; refuse when `pdf-overlay/` holds a file that is neither listed nor an instance file (naming it) |
| `sync --check` | read-only: exit 1 naming every missing, changed or extra file, every disagreement between `GENERATED.json` and what `sync` would write, and a `format` block that is not this checkout's pin |
| `check` | `sync --check`, the config rules, `formal_companion_annotations` validation of `annotations.json` (`canonical=True`), and the overlay rules below |
| `export` | run that `cf export` subcommand to a temporary file outside the companion and, when it succeeds and validates, write it to `pdf-overlay/annotations.json`. Everything after a bare `--` is passed to `cf export` unchanged |
| `geometry` | item headings and regions from `pdftotext -bbox-layout` (or a saved XHTML, `--bbox-xhtml`) as annotation `items`, `locations` and `provenance` records |
| `destinations` | for every declaration in the document, an `exact` destination when the built Verso literate site has its anchor, else a `fallback` to its module page |

The overlay rules `check` adds to the format's own: the config's source exists
in the document, is `kind: pdf` and carries `sha256` and
`physical_page_count`; every `PdfRegionSelector` on that source names a page
within the page count; and every destination `href` passes the viewer's href
rules (a relative site path, or an `https:` URL).

`cf overlay` needs no workspace: an overlay is generated from this checkout and
the companion's own document.

`GENERATED.json`:

```json
{
  "schema": "formal-companion/overlay-generated",
  "schema_version": 1,
  "generator": {"name": "formal-companion", "version": "<cflib version>", "commit": "<this repo's HEAD>"},
  "format": {
    "package": "formal-companion-annotations",
    "version": "<the vendored release>", "tag": "v<that>", "commit": "<its upstream commit>",
    "schema": "<vocab.ANNOTATION_SCHEMA>", "schema_version": "<vocab.ANNOTATION_SCHEMA_VERSION>"
  },
  "instance_files": ["annotations.json", "overlay.config.json"],
  "files": [{"path": "viewer/viewer.js", "sha256": "<hex>"}]
}
```

`files` is sorted by `path` and excludes `GENERATED.json` itself and the
instance files. `generator.commit` is this checkout's HEAD, read read-only
through `cflib.gitsafe`; it is absent when the skill is not a git checkout,
which is a fact about the generator and not a problem with the directory.

`format` is the annotation format release the directory was generated against:
`version`, `tag` and `commit` are read from
`vendor/formal_companion_annotations.manifest.json`, and `schema` and
`schema_version` from the vendored `formal_companion_annotations.vocab`, so
the block is a statement *about* the pin and never a second place the identity
is typed in. All six keys are required. `cf overlay sync --check` and
`cf overlay check` compare it with this checkout's pin;
`tools/check_generated.py`, which has neither the manifest nor the package,
checks its shape and the two values the shipped schema settles.

## 7. Producers

`scripts/overlay/geometry.py` generalises measure_theory's
`overlay_geometry_from_layout.py`: the item kinds (`--kinds`), the numbering depth
(`--numbering`), the optional section prefix (`--section`) and the proof kinds
(`--proof-kinds`, default Theorem, Lemma, Proposition, Corollary) are
arguments, and the default kinds come from the source's registry record when
one exists. Headings are matched through `cflib.items`' grammar; an item ends
at the next numbered heading, a section heading, an unindented paragraph
after a vertical gap, or, for a proof kind, a line beginning `Proof.`; an item
that runs off a page continues at the top of the next page's text. Output
records follow the format exactly, with ids from
`formal_companion_annotations.ids` where the format defines them (format
section 5). Sign-off provenance records the method the user names
(`--method agent_visual` or `--method manual_visual`); there is no default, and
it is never `manual_visual` unless the caller says so. Crops are written only
to `--evidence-dir` and only when `pdftoppm` is present.

`scripts/overlay/destinations.py` maps a declaration name to its Verso
literate module page (`<Module/Path>/index.html`) and anchor (dots replaced by
`___`, the scheme the literate genre uses), and checks the anchor occurs
exactly once in the page before calling it `exact`. Anything else is a
`fallback` whose `fallback_label` says why: no page, an anonymous
declaration, an absent anchor or an ambiguous one.

Both write a document **fragment** — `{"items": …, "locations": …,
"provenance": …}` or `{"destinations": …}` — in canonical form, for an
exporter or a person to merge into the document. Neither writes into a
companion.

## 8. What the port leaves behind

measure_theory's own generator and validator, `pdf_overlay.py`, its three
schemas, its geometry batch partitions, its overlay manifest, its site
manifest and its dependencies file existed to lock one book's data. The
annotation document and its checker replace them, so they are not ported.
A companion that keeps data in its own convention (measure_theory's geometry
batches, target refinement, verdicts, destinations and snippets) keeps it
outside `pdf-overlay/` and turns it into `annotations.json` with
`cf overlay export`.

## Upgrading the format

The annotation format's repository moves ahead on its own, and its next major
renames the schema identity. Nothing here follows it automatically and nothing
here keeps the old identity behind your back: the whole dependency is the
vendored release, so the upgrade is

```sh
python3 scripts/annotations/sync_format.py <formal-companion-annotations checkout> vX.Y.Z
python3 -m unittest discover -s tests
node --test templates/overlay/tests/*.test.mjs
```

`sync_format.py` replaces `vendor/formal_companion_annotations/` and rewrites
its manifest; commit the two together. The suite is where a release that
changed something shows up — `tests/test_vendored_format.py` seals the bytes,
`tests/test_format_pin.py` holds the pin, and the exporters' tests say whether
the documents this skill writes still validate.

Then, in each companion, in this order:

```sh
cf overlay export <companion> --convention <convention> --commit <sha>
cf overlay sync <companion>
```

`export` rewrites `annotations.json` in the new format and `sync` replaces
`pdf-overlay/schema/annotations.schema.json` and the `format` block that
records it.
Between the two the directory is inconsistent, and `cf overlay check` says so;
that is the point of running them together. A companion that syncs without
exporting gets a document its own `check_generated.py` refuses, naming the
schema and the version the new schema states.
