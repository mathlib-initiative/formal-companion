# The generated PDF overlay

A companion's PDF overlay shows a source PDF with its items outlined and, for
each item, the declarations that formalize it. It is **generated**: the
viewer, its tools and their schemas are copied verbatim from
`templates/overlay/runtime/` by `cf overlay sync`, and everything specific to
one companion is data the viewer and the tools read at run time:

| path in the companion | written by | what it holds |
|---|---|---|
| `annotations/<section>/[<item>/]<name>.json`, `_defaults.json` | `cf overlay export` | the companion's [Literature Links](../vendor/literature_links/) 1.0 annotations, one annotation per file, one folder per book section and per item with several, with folder defaults and end templates (section 7) |
| `pdf-overlay/overlay.config.json` | `cf overlay init`, then edited | presentation and local-build settings (section 3) |
| `pdf-overlay/annotations.bundle.json` | `cf overlay sync` | every annotation as the reader reads it, defaults applied, with its file's path, in file order: what the viewer reads, in one fetch (section 6) |
| `pdf-overlay/GENERATED.json` | `cf overlay sync` (and `export`) | the generator commit, the Literature Links release, the sha256 of every copied file, and the annotation files' count, manifest sha256 and bundle sha256 (section 6) |
| `pdf-overlay/verso.json` (optional, committed) | `tools/verso_extract.py --pages`, where a Verso build of the project is | hovers for a site with no Verso pages of its own (section 5) |

A companion's CI checks all of it with `python3 pdf-overlay/tools/check_generated.py`,
which needs nothing but CPython and the checkout. `docs/generating-an-overlay.md`
is the walkthrough; this file is the contract.

The overlay reads **Literature Links** 1.0.0: tag `v1.0.0`, commit
`ae8f2a65c8f665a4a747842a7e5bf8e1ae19054e`, vendored
under `vendor/literature_links/` (sealed by
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
   fetch it from the URLs the annotations' PDF ends name, verify it against
   their sha256 (and `bytes`), cache it under the config's ignored cache
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
7. **Hovers come from the built site, at build time.** `build_site.py` reads
   the site's own Verso pages and hover table and writes `<mount>/verso.json`
   (section 5); the viewer never fetches or parses a page's HTML. A site
   with no Verso page gets the companion's committed `pdf-overlay/verso.json`,
   or an empty file and plain statements. No setting turns this on or off.

## 2. Layout

```text
templates/overlay/
  overlay.config.json.example
  runtime/                     copied verbatim into <companion>/pdf-overlay/
    README.md THIRD_PARTY.md
    schema/                    overlay-config-v1.schema.json
    tools/serve.py build_site.py check_generated.py overlay_annotations.py verso_extract.py
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
are required; `lede`, `display.strip_prefix`, `display.minimal`, `storage_key`,
`pdf.cache_dir`, `pdf.route`, `site.output_root`, `site.index`, `site.mount`,
`site.link_text`, `build.commands` (default: none) and `serve` have defaults;
unknown keys are errors. `back_link` is optional: with none, or one whose `href`
the viewer refuses, the top bar shows no back link. `display.minimal: true`
keeps the passage, its links and their statements, and leaves out what describes
them: the status chip (kept for screen readers, back in sight when it has
keyboard focus or the PDF is not verified), the keys line (the toolbar buttons
name their shortcuts), the key and related counts and the name filter, a key
card's kind and badges, a related row's role and kind words, the review footer
and the ledger cluster, and the "Formalized" word. A record's note waits behind
one "More detail" control, closed until asked for. A word that qualifies the
links (support only, partial, a proof step's "Close match", "Related" or "No
match", ...), where a link goes ("module page", "no page"), the notice, the
pagers and the faithfulness verdicts with their "machine verdicts, unreviewed"
caveat stay. A relation says what a link claims, so it is no metadata about the
link: only `exactMatch`, the claim a link makes by being there, goes unmarked. `source_id` names the PDF the overlay shows:
the `source_id` in the overlay extension of its records' PDF ends (section 7).
`scripts/overlay/config.py`, `runtime/schema/overlay-config-v1.schema.json` and
`runtime/tools/check_generated.py` carry the same rules and patterns, held
together by `tests/test_overlay_config.py`.

## 4. The viewer (`runtime/viewer/`)

At start-up it fetches `../overlay.config.json`, then `../GENERATED.json`,
then the bundle its `annotations` block names (one fetch), checking it against
its recorded sha256. Each annotation's base URL is its own file's in the
published site (`<site root>/annotations/<section>/[<item>/]<name>.json`, the site
root `../` once per `site.mount` component, plus one), since Literature Links
resolves a relative reference against the file that holds the annotation; the
rendered pages are under the same site root. It keeps the subject ends that
are PDF ends with the config's `source_id`; they must all state one sha256.

From the records alone it builds:

- regions: each such end's `location.page` and `location.rect` (fractions of
  the displayed page from the top left, so drawn through the page's own
  rotation), or its extension's `quads`; one region per extension `region`
  id, its items in the extension's `region_items` order; a click opens the
  first item with a card;
- items: one per record `id`, labeled by its subject ends' `title`, with
  the record extension's `coverage`, else `status`, as its coverage word,
  `note` as its coverage note, the record extension's `locator` (its
  `text`, else its printed and physical pages, less the segment that repeats
  the label and cut at the first `;` or ` / `, on one line) and `verdicts` (each one line: kind, summary and resolution,
  shown only on request), and one footer line naming the link `provenance`
  methods and whether a person reviewed the links and verdicts;
- cards: one per object end of a record that is not a checked absence
  (`noMatch`), once per item however many records repeat it, in the order
  of the end's `role`: key, attributed, support, example, helper (then by
  declaration), named by `location.declaration`, else the end's `title`,
  else the extension's declaration `id`, with the end extension's snippet,
  destination resolution and fallback label, declaration kind and flags, and
  declaration verdict. A
  card links to its rendered page only when its end's extension has a
  `destination`: `<site root>/<location.module as a path>/index.html`, plus
  the declaration's anchor (its name with each `.` as `___`) when the
  resolution is `exact`, unless the destination gives its own `href`
  (relative to the site root, or `https:`) or `fragment`. An end with no
  overlay extension, from elsewhere, links its own `uri`. Either is a link
  only when it is `https:` or a page of the same origin. The panel shows one card per key
  declaration, each with its statement (eight, then a button for the rest;
  "N key declarations" when there are two or more, never a heading that says
  what they state), then the rest under "Related": one line each, a plain
  link to its source with no statement, headed by its count (and by the role
  more than half of them share). When `../verso.json` holds a statement for a
  key card's target (its page and anchor, before resolution, or for a tagged
  example its module's page and its tag) whose texts joined are the card's
  snippet exactly, the statement is drawn from its runs: each token tinted by
  its kind, a link when its target is a declaration's own anchor on a page
  of this site, and a hover card (its signature, a variable's as
  `name : type`, and its docstring's paragraphs and code as text) on the
  pointer, on keyboard focus and on a first tap. The pointer may move onto
  the card, which stays while it is there and closes shortly after it
  leaves; a tap inside the card keeps it, a click or a tap elsewhere closes
  it, and PageDown and PageUp scroll a long card while its token has focus.
  A card that keyboard focus opened comes back once the pointer has passed
  over another token, until Escape or the token loses focus. A statement is
  one tab stop, the arrow keys, Home and End (with no modifier held) move
  between its tokens, and Escape closes the card, or leaves the item when a
  scroll has already hidden it. Otherwise, and with no `verso.json`, the
  plain statement stays;
- an item with no card says so, and names its ledger clusters;
- proof steps: a record of `kind: proof_step` is a step, and its item is the
  one other item with records in its item folder. Section 7 says what a
  producer writes. A page's row of that item lists the steps on the page under
  it, one line each: the step's number in its proof, its title less the id it
  repeats, its relation, and its region's mark; if the item's own region is on
  another page, the row comes with the steps and opens that page. An item's
  panel lists all its steps under "Proof", each with its page when that is not
  this one, and a row opens its step on its page. Within a proof the stepper
  walks the steps, turning the page, and the panel's head leads back to the
  item. A step's box is drawn with no fill, so the page's items stand out. A
  step's panel shows, in place of the coverage word, its relation (`exactMatch`
  "Exact match", `closeMatch` "Close match", `relatedMatch` "Related",
  `broadMatch` "Broader", `narrowMatch` "Narrower", `hasPart` "In part",
  `noMatch` "No match", any other token as written, with what it claims as its
  title), its `note` with backticked code set as code, and one card per
  declaration in the record's order, with every range of it the record names:
  the end's snippet with its `location.lines` in a gutter, or the line numbers
  alone with no snippet, and how far a snippet with `truncated` runs. A step
  whose record is `noMatch` has no card: it says it was checked and lists the
  record's object ends, the Lean the absence is scoped to, by declaration else
  by module, with their lines; a declaration links only where a card of the same
  proof names it. A record of any other kind prints no relation and keeps this
  section's cards;
- the regions on a page are in reading order (top, then left), whatever
  order the files came in; with nothing selected the panel lists the page's
  items in that order, with how many a key declaration states, and a page
  with none points to the nearest pages that have some.

A region it cannot place is dropped with a notice, never guessed at; a record
with no region left adds nothing. Every string reaches the DOM through
`textContent` or attribute setters. `viewer-core.js` is the pure half;
`viewer.js` the controller.

## 5. Runtime tools (`runtime/tools/`)

Run from the companion root as `python3 pdf-overlay/tools/<tool>.py`; they find
`pdf-overlay/` from their own path and import nothing but their siblings, with
bytecode writing off.

- **`serve.py`**: the PDF identity is the first PDF subject end in the bundle
  with the config's `source_id`: its `uri` and its citation's `URL`, then the
  extension's `alternate_urls`, over HTTPS only; its `sha256` and the
  extension's `bytes`. Acquire, verify, cache, optionally build, serve on
  loopback.
- **`build_site.py`**: publish `viewer/`, `vendor/`, `overlay.config.json`,
  `GENERATED.json` and the bundle into `<output_root>/<mount>/`, and the
  annotation files and their `_defaults.json` (exactly the bundle's records)
  into `<output_root>/annotations/`, so that a card's rendered page,
  relative to the site root, is a page of `<output_root>`; replace both
  atomically (refusing a site directory that does not have the layout of
  section 7), insert the index link, write `receipt.json`. Beside the bundle
  it writes `verso.json` (`verso_extract.py`): for each object end whose
  rendered page is a page of the site with an anchor, the `div.code-box`
  holding the anchor (for a tagged example, the box whose code holds its tag
  comment, with its `example` keyword as the anchor) is parsed from its
  start (goal views, messages and
  inputs skipped, a line break between two `code.block` elements) and the
  snippet matched from the anchor's line or up to six lines above: whitespace
  runs match whitespace runs, comments are skipped on both sides, a trailing
  `…` is kept, and an internal `…`, a mismatch or a match that does not cover
  the anchor gives no statement. A statement is `[kind, text, hover,
  binding, href]` runs whose texts joined are the snippet; a link is kept
  only when its page is a file of the site that holds its anchor and that
  anchor is a declaration's own (the name after a declaration keyword, or a
  field or constructor of one the page declares), and only
  the hover entries the statements use are copied from the site's
  `-verso-docs.json`, as plain-text `signature` and `doc`. For a site with
  no Verso pages (no `-verso-docs.json` and no statement) it publishes the
  committed `pdf-overlay/verso.json` instead, a statement that no longer
  joins to its card's snippet, or whose card pins another commit, dropped;
  a file whose build commit no card pins is refused. So is a file with a
  statement whose card pins no full commit (a release tag, which Literature
  Links allows, or no `version`), naming those cards: a statement is of one
  tree, only the project's git could say which tree a tag is, and the
  statement would go for a spelling, so the producer gives the commit. A card
  with no statement in the file may pin what it likes. `verso_extract.py
  --pages <dir> --bundle <bundle> --out pdf-overlay/verso.json --commit
  <sha>` writes that file from a Verso build elsewhere: a card is found by
  its module's page and its declaration's anchor (the build's `xref.json`,
  else Verso's slug of the name), no token keeps a link, and `build`
  records the commit and the build's Verso revision; it stops before
  writing on a statement whose card pins no full commit. The receipt records
  `verso.json` with the other files, and its `verso.source` is `site`,
  `committed` (with the build commit) or `none`.
- **`check_generated.py`**: every file `GENERATED.json` lists, with its
  sha256, and nothing else in `pdf-overlay/` but the bundle and a committed
  `verso.json` in the form `--pages` writes, of a commit the cards pin, with
  no statement whose card pins a tag or no `version`; the
  `format`
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
  it (a committed `verso.json` is left alone). Files `export` wrote that
  changed since are refused, not recorded
  (named, when the last bundle holds the export); files nothing exported are
  recorded as they are.
- `sync --check` is read-only and includes the scan; `check` adds the config,
  the inputs' hashes and the overlay's rules (one PDF per `source_id`, pages
  within its `physical_page_count`, object ends' `uri` `https:` or relative,
  and a destination's own `href` `https:` or a path inside the site), and
  says of a committed `verso.json` what `check_generated.py` says, from this
  checkout's copy of that tool: a companion's copy of a tool is never run.

```json
{
  "schema": "formal-companion/overlay-generated",
  "schema_version": 3,
  "generator": {"name": "formal-companion", "version": "...", "commit": "<this repo's HEAD>"},
  "format": {"package": "literature-links", "version": "1.0.0", "commit": "<40 hex>", "tag": "v1.0.0"},
  "instance_files": ["overlay.config.json"],
  "files": [{"path": "tools/literature_links/companion.py", "sha256": "..."}],
  "annotations": {
    "directory": "annotations",
    "extension": "org.mathlib-initiative.formal-companion.overlay",
    "export": {"convention": "attribution-table", "commit": "<companion commit>", "source_id": "..."},
    "inputs": [{"path": "<curation file>", "sha256": "..."}],
    "count": 1085,
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

`format.tag` appears when the vendored revision is a tagged release, and
`format.branch` when it is a version not yet released (its manifest says
`"released": false`).
`export` and `inputs` appear when `cf overlay export` wrote the files; with
`export`, `check` and `check_generated.py` require a non-empty `inputs`.

## 7. The annotation files

`scripts/overlay/shards.py` converts the exporter's annotation document into
Literature Links 1.0 records:

| record field | from |
|---|---|
| one record | each (item, linked declaration) for roles key, attributed, support, example, helper; one record for an item that links nothing |
| `id` | the item's id, so the records about one item share it |
| `subject` | one `pdf` end per region of the item, in reading order: `{type: pdf, uri: <official URL>, sha256, citation, identifiers, location, title}`. `citation` is a CSL `book` item (`id` the source id, `title`, `edition`, `URL`, `accessed`, `ISBN`, `DOI`); further ISBNs and an arXiv id go in `identifiers`. `location` is `{page, rect: [x, y, width, height]}` from the `PdfRegionSelector` (`normalized` or `pt`, either origin, CropBox or MediaBox, taken as unrotated); a quad region becomes its bounding box. `title` is the printed label (kind, number, sub-part, title) |
| `object` | `[{type: lean, uri: <repository>, version: <commit>, location, role}]`: `location` is `{declaration, module}`, or for an anonymous declaration `{module, lines: [<the line it starts on>]}`, or, when the export could not place it on a line, `{profile: "urn:x-formal-companion:unlocated-example:v1", module, id}`; `role` is the link's role. An item that links nothing has `[]`, or, when its coverage is `not_formalized`, the repository at the commit with `relation: noMatch` |
| `relation` | the link's relation token: `exact` is `exactMatch`, `generalization` `broadMatch`, `special_case` `narrowMatch`, `part` `hasPart`, and `related`, `invocation`, `uses` and `supporting` `relatedMatch`; none for a link with no token or another token |
| `kind`, `status`, `note` | the item kind lower-cased; the item's coverage as a recommended status (`formalized`, `partial_only` as `partial`, `not_formalized`, and `support_only` as `not_formalized`; `example_only`, `private_only` and `out_of_scope` give none), else `formalized` for an item with a key link; the coverage note |

The repository is `https://<companion.repo>`, or for a local checkout the
companion root relative to the file. A declaration's line is not written
beside its name: it is the declaration's first line, and `snippet.line` keeps
it.

Everything else the viewer needs is under the extension key
`org.mathlib-initiative.formal-companion.overlay`, on the end it describes or
on the record:

- on the record: `locator` (the item's: `text`, its clusters' locators;
  `physical` and, from the source's `printed_page_offset`, `printed` pages of
  its regions), `verdicts` (the item's: `verdict`, `severity`, `status`,
  `summary`, `evidence`, `resolution`, `sub`, `provenance`: `actor`,
  `method`, `confirmed_by_human`, `note`), `provenance` (the link's:
  `actor`, `method`, `confirmed_by_human`), `relation` (a token its
  `relation` does not name alone), `coverage` (a coverage class its `status`
  does not name alone), and on a record that links nothing `clusters`;
- on a subject end: `source_id`, `bytes`, `physical_page_count`,
  `alternate_urls`, `region`, `region_items` (a region's items in the order
  the location's `x_item_order` names them, when it has several), `quads`
  (`[x0, y0, …, y3]` per quad) and `signoff`;
- on an object end: `declaration` (`id`, `kind`, `private`, `anonymous`,
  `sorry`), `snippet` (`text`, `line`, `truncated`), `destination`
  (`resolution`, `fallback_label`, and `href`, relative to the site root, or
  `fragment` when the module's page or the declaration's anchor is not the
  page) and `declaration_verdict` (`verdict`, `status`, `note`). A snippet's
  `text` is a theorem's statement through its `:=`, and a definition whole
  (body, fields, constructors) up to 60 lines (docs/exporters.md section 1).

The section folder is the item number's first two parts (`1.2.7` is `1.2`);
otherwise the item's section or a cluster locator's `Section N.M`; otherwise
`other` (a two-part number may be an equation's). An item with one record is
a file `<item>__<role>__<declaration>.json`, or `<item>.json` for a record
that links nothing; an item with several is a folder `<item>/` of
`<role>__<declaration>.json` files. Every character outside `[A-Za-z0-9._-]`
(and a leading `.`) is replaced by `-`. A name longer than 120 characters, a
Windows device name (`con`, `nul`, ...), `_defaults.json`, and every name
that equals another one in its folder ignoring case instead ends in `-` and
the first 10 hex digits of the sha256 of the full unsanitized key, truncated
to fit.

A producer of proof steps (this exporter writes none) gives each step one
record with `kind: proof_step`, a file in the item's folder beside at least one
record of the item, of any other kind; the folder holds no other item's records.
Its file names sort, numbers as numbers, in the order of the proof. Its `id` is
any id of its own: the viewer never parses one. A `title` that starts with
`<id>: ` shows what follows it. `relation` says what its lines claim; `note` is
optional. Each object end's `location` names `lines: [first, last]` of a
`declaration` or `module`, with the extension's `snippet` of those lines, and
one end per range. A `noMatch` step's object ends name the Lean it was checked
against, with no snippet. A step that breaks the folder rule stays an item of its
own, with its lines and relation, never guessed onto an item.

`scripts/overlay/defaults.py` writes the files with Literature Links folder
defaults: `annotations/_defaults.json` holds what all records share and an
item folder's `_defaults.json` what its records share beyond that (a member
equal in all, or an object in a strict majority, recursively; no field is
named), with an end template for each side whose ends mostly name one
document. At the top these are the PDF (`type`, `uri`, `sha256`, `citation`
and its extension) and the repository at the commit (`type`, `uri`,
`version`); in an item folder, what the item's ends share beyond that, such
as its title, or its region when it has one. Each record is written as the
merge patch over its defaults (`null` removes an inherited member), and each
end of the template's document as its patch over the template, a `location`
whole; an end whose patch would remove a template member is written whole,
with its own `uri`, and a side that is the template alone is left out.
References are compared resolved and written relative to each file. Files list
`$schema, id, kind, status, relation, note, subject, object, extensions` first,
an end's `type, uri, version, location, title, role, junk, sha256, citation,
identifiers, extensions`, and `type` first elsewhere, with a two-space indent,
UTF-8 and arrays of scalars on one line. The files are read back with the
vendored reader before anything is written; a record the conversion cannot
carry, or files that do not read back as the records, stop the export.
