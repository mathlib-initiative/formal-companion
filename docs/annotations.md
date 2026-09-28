# Annotation documents: `lean-companion/annotations`, schema v1

One JSON document per companion says which Lean declarations formalise which
items of which sources, and where those items sit in the sources. It is the
standard format the adapters, a PDF overlay, a web viewer and a WikiLean-style
exporter can all read and write.

- Schema: `schema/annotations-v1.schema.json` (JSON Schema 2020-12), for
  `schema_version` `"1.0"`.
- Checker: `scripts/annotations/validate.py` (schema plus the invariants in section 6).
- Mechanical ids: `scripts/annotations/ids.py` (section 5).
- Examples: `examples/annotations/` (all synthetic; two are deliberately invalid).

The ids and the schema's `$id` use the name **lean-companion**. The `$id` is an
identifier that is never fetched: it names no file on any host, under the
current name or the new one, and nothing in the skill dereferences it. The
rename of this repository from companion-faithfulness to lean-companion is
pending.

**Contents**: 1 design rules · 2 the document · 3 records · 4 locations and
selectors · 5 ids · 6 invariants and warnings · 7 canonical form · 8 validating
· 9 versions and the changelog · 10 how the existing companions map in · 11
left out of v1

## 1. Design rules

1. **Everything that varies is an abstraction.** A location is a W3C Web
   Annotation target, so a PDF region, a quote on a web page, one page of a
   rendered site, a line range in a `.tex`, `.lean` or Markdown file, an offset
   into a PDF's text layer and a time range in a video are the same kind of
   record with a different selector. The list of fragment grammars
   (`conformsTo`) and of media (`kind: other` with a `media_type`) is open.
2. **No one-item-per-page rule.** An unnumbered passage is a first-class item;
   any number of items may share a page, and one item may have many locations.
3. **Highly structured.** Every root key is either a fixed header or an array
   of records; every record has a string `id`; every array is sorted by `id`;
   `additionalProperties` is false everywhere. Unions are discriminated: a
   selector by `type`, an item by `form`. The only open door is the `x_`
   extension keys on a record, `companion` and the root (section 2.2).
4. **Pinned sources.** A location is only meaningful against one edition, so a
   source names its bytes (`sha256`, `git_blob`; for a whole site, the sha256
   of its page manifest) or an immutable revision. A URL is optional where a
   hash or revision pins the source.
5. **Plain JSON on disk, JSON-LD on export.** The file is not JSON-LD; its
   `target` objects are shaped so that an exporter can emit a W3C
   `AnnotationPage` without loss (section 4.7).

## 2. The document

### 2.1 Root keys

| key | required | meaning |
|---|---|---|
| `schema` | yes | always `"lean-companion/annotations"` |
| `schema_version` | yes | `"<major>.<minor>"`, `"1.0"` for this schema (section 9) |
| `$schema` | no | the JSON Schema the document follows, an identifier that is never fetched, so the URL rule (I10) exempts it |
| `companion` | yes | `{name, repo, commit, generated_at, generator, graph?}`: which repo and commit the declarations describe, and what wrote the file. `repo` has no scheme (`host/org/name`), see I10. `commit` is a full 40- or 64-hex git id. `graph` pins the companion's own graph authority (a ledger), section 3.3 |
| `sources` | yes, at least 1 | the editions annotated |
| `items` | yes | numbered items and unnumbered passages |
| `declarations` | yes | the Lean declarations anything mentions |
| `links` | yes | item to declaration (or cluster) attributions |
| `provenance` | yes | how links, locations and memberships were produced |
| `artifacts` | no | formalization artifacts that are not declarations (module docs, `#check`, figures) |
| `memberships` | no | which declarations and artifacts belong to a cluster, with the graph's role |
| `locations` | no | where items (or declarations) sit in a source |
| `destinations` | no | where a declaration or artifact is rendered on a companion site |
| `snippets` | no | statement text for declaration cards |
| `verdicts` | no | adjudicated faithfulness verdicts on items |
| `unattributed` | no | declarations shown for the book but tied to no item |
| `clusters` | no | a companion's own coarse nodes (sections, ledger nodes) |
| `changelog` | no | id events: born, retired, restated, renamed, ordinal_assigned |

The core arrays may be empty. An optional array that is absent means "this
document does not say", not "there are none".

### 2.2 Extension keys

Extension keys exist only on records (the objects in the root arrays),
`companion` and the root. There, a key matching `^x_[a-z0-9_]+$` carries any
value; it is a producer's own data, never read by the checker, except that the
URL rule (I10) still applies to it. Every other object (`pages`, `signoff`,
`target`, the selectors and their `coordinate_space`, `resolution`,
`relations`, `graph`, an input, a page hash) takes no extension keys, so a key
there that happens to match the pattern, such as `coordinate_space.x_axis`, is
a core key. A consumer that strips extension keys strips them from records,
`companion` and the root only. A key that several producers need is a
candidate for the next minor version, not a permanent `x_` key.

## 3. Records

Every enum in the schema is a set in `scripts/cflib/vocab.py`, and a few reuse
sets from other sections (the verdict vocabulary, source roles, ledger
verdicts, finding kinds, sorry classes). All are listed in
`references/vocabulary.md` section 1.10. A test walks every `enum` in the
schema and fails when one has no set in that module or the two disagree.

### 3.1 Sources

**Source** `{id, kind, media_type?, scope?, title, edition?, sha256?, bytes?,
git_blob?, url?, alternate_urls?, revision?, retrieved_at?, path?,
page_hashes?, physical_page_count?, printed_page_offset?, page_size?,
page_sizes?, text_layer?, role?}`.

- `kind` is `pdf`, `web`, `tex` or `lean`, the named presets, or `other`,
  which names any other medium by its IANA `media_type` (`text/markdown`,
  `application/epub+zip`, `video/mp4`). The selector rules follow a family
  derived from the kind, or from `media_type` for `other` (section 4.2).
- Identity, enforced by the schema: a `pdf` needs `sha256`; a `web` source
  needs `sha256` or `revision` (a `url` is optional, so an unhosted build is
  pinned by its hash or commit and a repo-relative `path`); `tex` and `lean`
  need `sha256` or `git_blob`; `other` needs `sha256`, `git_blob` or
  `revision`. `retrieved_at` is extra information, never an identity.
- `scope: site` (web only) makes one source stand for a whole rendered site:
  each location then names its page with a `SitePageSelector` (section 4.2).
  The default is `page`.
- A site is not one byte stream, so its `sha256` is defined over its pages:
  the sha256 of the UTF-8 manifest with one line
  `"<sha256 of the page's bytes>  <path>\n"` (two spaces, as `sha256sum`
  writes it) for every HTML page of the site (every `.html` file under the
  site root), `path` relative to the site root with `/` separators, the lines
  ordered by path (section 7). The optional `page_hashes` lists those pages as
  `{path, sha256}`, sorted by path. When it is present with `sha256`, the
  checker recomputes the digest from it, and every `SitePageSelector` on the
  source must name a listed page (a path ending in `/` names its
  `index.html`). `validate.site_sha256` is the reference implementation.
- Physical page = printed page + `printed_page_offset`. `page_size`
  `{width_pt, height_pt}` is the page box in points, and `page_sizes` lists the
  physical page ranges `{first, last, width_pt, height_pt}` that differ from
  it (sorted, not overlapping). A page's size is its `page_sizes` range when
  one lists it, else `page_size`. The page fields apply to a PDF only: kind
  `pdf`, or `other` with `application/pdf`.
- `alternate_urls` lists other addresses of the same bytes (a canonical page,
  a mirror); `bytes` is the file's size.
- `text_layer` `{extractor?, version?, unicode, whitespace, case}` says how
  quotes and offsets on this source are matched (section 4.6). `extractor` and
  its `version` come together: both or neither.

### 3.2 Items

**Item** `{id, id_source, form, source, kind, kind_source?, number?, sub?,
title?, title_source?, label?, section?, pages?, quote?, coverage?,
cluster_fallback?, clusters?, relations?, legacy_ids?}`. `form` is the
discriminator.

- `form: numbered`: `kind` (`Definition`, `Exercise`, ...) and `number`
  (`1.1.21`) are required. `sub` is a part the source numbers or locates
  separately and that the producer wants as its own item (an exercise whose
  parts the source sets out as separate blocks); it is written without
  parentheses (`ii`,
  `3`, `ix,x`). `(source, kind, number, sub)` is the identity and must be
  unique; pairing across companions uses the source's `sha256` plus that
  tuple, never the item's `id`.
- `form: passage`: an unnumbered passage. It has no `number` and no `sub`, and
  a required `title`. Its `kind` is `passage`, or a word such as `Remark` or
  `definition` when the source (or producer) labels it without a number.
- A link or verdict about a part of an item carries its own `sub` (section
  3.4); that never splits the item. A link's `sub` is the usual place for "the
  tag's parentheses".
- `kind_source` and `title_source` say whose words `kind` and `title` are:
  `source` (the default) or `producer`. A book that prints a strategy as a bare
  `2.1.1.` gets `kind: Strategy, kind_source: producer, label: "2.1.1."`, and a
  passage titled from a docstring gets `title_source: producer`.
- `label` is the label as the source prints it; `section` is the source's own
  section the item sits in, as the source names it (WikiLean's `section`).
- `pages` `{first, last?, basis, anchor?, raw?}` is a page span without
  geometry (section 3.5). A `quote` `{exact, prefix?, suffix?}` is shorthand
  for a location on the item's source with one `TextQuoteSelector` and no page;
  a consumer that reads locations only may expand it so. Use a location
  whenever the quote needs a page or any other context.
- `coverage` is one of `formalized`, `partial`, `not_formalized`,
  `out_of_scope`, `support_only`, `example_only` (I5): the curation's class.
- `cluster_fallback: true` says a viewer shows the memberships of the item's
  `clusters` in place of item-level links. It needs a cluster and no `key`
  link, and when the document has `memberships[]`, one of those clusters has a
  member (I5). It is independent of `coverage`, so an item keeps its class
  (`example_only`, say) and the fallback together.
- `relations` `{same_as?, part_of?, near?}` holds typed, sorted lists of other
  item ids: `same_as` is the same content (an entry in another edition or in an
  errata list), `part_of` is containment (a proof step inside a lemma), `near`
  is placement by a neighbour: the numbered item a passage sits beside, as its
  tag names it. The passage's page may then be inferred from that item
  (`pages.anchor: inferred`), or be the passage's own.

### 3.3 Declarations, artifacts, clusters and memberships

**Declaration** `{id, id_source, name?, lean_name?, namespace?, signature?,
kind, parent?, package?, module, file, line?, doc_line?, private, anonymous,
unlocated?, sorry?, statement_sha256?, anchors?, replaced_by?, legacy_ids?}`.

- `kind` is the Lean keyword, or `field` / `projection` for a structure's
  field or projection, which then name their structure or class in `parent`
  (and only they have a `parent`).
- `namespace` is the enclosing namespace at the declaration (the empty string
  for the root). For an anonymous declaration it cannot be read off the id, so
  a producer that knows it writes it here. For a `field` or `projection` it is
  the parent's `name` (its full name), so `namespace` + `.` + the field's own
  name is its full name; the checker compares both.
- `signature` is the normalised header text that `statement_sha256` hashes
  (section 5.1), when the producer has it; the checker verifies the hash. A
  named declaration's signature uses the same section 5.1 cut, and it is
  omitted when no `:=` or `where` ends a typed header (a definition by
  pattern matching, an `axiom`, an untyped header) and for a `structure`,
  `class` or `inductive`, whose `where` opens fields or constructors, not a
  value. `ids.named_signature` is the reference implementation.
- `package` names a Lake package other than the companion (for example
  `mathlib`), and `file` is then relative to it.
- `line` and `doc_line` are locations and may change freely; they are never
  part of the id. `anchors` lists locations that show the declaration's own
  text: a Lean line range or a rendered page, on a `lean`, `web`, or other
  text/html source (kind `other` with a `text/*` media type or
  `application/xhtml+xml`; never `tex`, a PDF or another medium).
- `unlocated: true` marks a declared id (section 5.1) that the companion's
  graph names but that is not yet tagged in the Lean at `companion.commit`. It
  is an anonymous `example` or `instance` with `id_source: declared`, and it
  has no `statement_sha256`, `signature`, `line`, `doc_line` or `anchors`; its
  `module` and `file` are the ones the graph gives.
- `legacy_ids` keeps ids the declaration had in older formats (a ledger's
  positional private id, a comment tag) so consumers can join old files.
- `sorry` is `none`, `exercise` or `gap`.

**Artifact** `{id, kind, module, file, line?, title?}`: a formalization
artifact that is not a declaration. `kind` is `module_doc`, `check`, `figure`
or `other`.

**Cluster** `{id, title?, locator?, items?, related_to?}`: a companion's own
coarse node (a section, a ledger source node).

**Membership** `{id, cluster, declaration | artifact, role, provenance}`: one
member of a cluster with the role the companion's graph gives it, `primary`,
`support` or `consumer`. `(cluster, target, role)` is unique. A companion's
graph authority is pinned once in `companion.graph` `{path, sha256, bytes?,
snapshot_commit?}`; `snapshot_commit` is the commit the graph describes when it
lags `companion.commit`.

### 3.4 Links

**Link** `{id, item, declaration | cluster, role, relation?, note?, sub?,
pages?, locations?, provenance, confidence?, raw_label?, orphaned?}`.

- `role` is `key` (states the item), `attributed` (shown for the item, not
  key), `support`, `example` or `helper`.
- `relation` says how the declaration relates to the item's text. It is an
  open lower-case snake_case token (`^[a-z][a-z0-9_]*$`) with a documented
  core: `exact`, `generalization`, `special_case`, `invocation`, `related`
  (WikiLean's `match_kind`). Another token passes with a warning; a value that
  is not such a token (`Generalisation`, `match-kind`) fails the schema.
  `note` is a short free-text note.
- `sub` is the part of the item the link covers.
- `pages` is the page anchor the link's own tag gives (it may differ from the
  item's), and `locations` lists locations for this link alone, on the item's
  source: the spot inside a long item where one of several declarations sits,
  or the page of a proof step.
- `raw_label` keeps the companion's own tag verbatim. `confidence` is in
  [0, 1]. Recommended id: `<item>/<role>/<declaration or cluster>`;
  `(item, target, role, sub)` must be unique whatever the id is.
- `orphaned: true` marks a link to a retired declaration id (section 9).

### 3.5 Pages

`pages` `{first, last?, basis, anchor?, raw?}` appears on items, links and
locations, always on a PDF source. `basis` is `printed` or `physical`; `last`
defaults to `first`. `anchor` says where the page came from: `own` (the
thing's own anchor), `crossref` (a cross-reference to it) or `inferred` (from a
neighbour). `raw` is the anchor text as written (`book pp. 25-26`). On a
location, `pages` is a structured mirror of its selectors, so a consumer need
not parse `page=` strings; the checker verifies that every page the selectors
name lies inside it.

### 3.6 Provenance, locations, destinations, snippets

**Provenance** `{id, actor, method, agents?, tool?, inputs?,
confirmed_by_human, at?, note?}`. `actor` is `author`, `agent`, `human` or
`tool`; `method` is `docstring_tag`, `curation`, `blueprint_macro`,
`lean_scan`, `layout_extraction`, `text_match`, `manual`, `agent_annotation`
(an LLM or agent pipeline annotating), `pipeline` (a deterministic export),
`adjudication` (a differential or review that settles verdicts) or
`site_index` (a rendered site's cross-reference index). `inputs` lists what
the producer read: a file of the companion, `{path, sha256, bytes?, commit?}`,
or a source of this document, `{source}` (a PDF outside the repository is
pinned once, by its source record). File entries come first, sorted by path,
then source entries sorted by source.

**Location** `{id, items, target, pages?, provenance?, signoff?,
legacy_ids?}`, section 4. The location's source is `target.source`; there is
no second copy of it. Every item a location shows is on that source.
`signoff` `{method, verifier, evidence_id?, evidence_sha256?, at?}` records who
checked the location against the source.

**Destination** `{id, declaration | artifact, site, href, fragment?,
resolution, fallback_label?}`. `href` is an `https` URL or a site-relative
path in the one grammar `SitePageSelector.path` also uses (no leading slash,
no `..` segment, no `#`, no `:`). `exact` needs a `fragment`, `fallback` a
`fallback_label`. A destination is the navigation form of "where is this
rendered"; an anchor location on a site source is the W3C form of the same
place and, when both exist, is derivable from it (`SitePageSelector.path` is
`href`, the RFC 3236 fragment is `fragment`). Write the anchor only when a
consumer needs the W3C form.

**Snippet** `{id, declaration, text, line?, truncated?}`, at most one per
declaration.

### 3.7 Verdicts, unattributed, changelog

**Verdict** `{id, item, sub?, locations?, verdict, severity, status, summary,
evidence?, declarations?, resolution?, finding_ids?, ledger_verdict?,
finding_kind?, pinned_commit?, provenance?}`. `verdict`, `severity` and
`status` are the viewer vocabulary. `locations` lists the regions the verdict
is about (each must show the item). `evidence` is the ordered list of evidence
statements. `resolution` `{state, pr?, issue?, commits?, branch?,
replaced_by?, note?}` replaces "Fixed in PR #N" prose: `state` is `open`,
`fixed` (needs `pr` or `commits`), `in_progress` (needs `pr` or `branch`),
`refuted`, `argued_correct` or `out_of_scope`; `commits` holds the fix and
merge commits (abbreviations allowed here only) and `replaced_by` every
replacing declaration. `resolution` is optional: a verdict without one is
open, and a verdict with `status: fixed` needs a `fixed` resolution.

**Unattributed** `{id, declaration, claim, signals?}`, `claim` one of
`unattributed`, `api`, `infrastructure`. An unattributed declaration has no
live link (I13).

**Changelog entry** `{id, at, event, subject, subject_kind, reason?, commit?,
file?, line?, old_statement_sha256?, new_statement_sha256?, successor?,
possible_successor?, note?}`, section 9.

## 4. Locations and selectors

### 4.1 The target

```json
{"id": "definition-1.1@p007", "items": ["definition-1.1"], "pages": {"first": 7, "basis": "physical"},
 "target": {"source": "notes-pdf", "state": {"type": "TimeState", "sourceDate": "2026-09-01T08:00:00Z"},
            "selector": [ ... ]}}
```

`target` is a W3C `SpecificResource`. Its `source` is a source id and the
location's source; an exporter replaces it with that source's IRI. `state` is
optional and only `TimeState` is allowed. `selector` is always an array of
**alternatives** in preference order: each one selects the same span, and a
consumer uses the first it understands. It is never a union. An item that
spans two pages is a `RangeSelector` or two locations, never two selectors.

`refinedBy` (one selector) narrows its parent: a quote inside `page=7`, a text
offset inside `#mw-content-text`, a range of two quotes inside `page=9`.
Selector keys use the W3C spelling (`conformsTo`, `refinedBy`,
`startSelector`); the two extensions use this format's snake_case.

### 4.2 The selector vocabulary

Each source follows the rules of one family: `pdf` (kind `pdf`, or `other`
with `application/pdf`), `html` (kind `web`, or `other` with `text/html` or
`application/xhtml+xml`), `text` (kinds `tex` and `lean`, or `other` with any
other `text/*` type) and `generic` (any other medium).

| selector | keys | pdf | html | text | generic |
|---|---|---|---|---|---|
| `FragmentSelector` | `conformsTo?`, `value`, `refinedBy?` | yes | yes | yes | yes |
| `TextQuoteSelector` | `exact`, `prefix?`, `suffix?`, `refinedBy?` | yes | yes | yes | yes |
| `TextPositionSelector` | `start`, `end` (half-open, code points) | yes, inside a page, with a declared extractor | yes | yes | yes |
| `DataPositionSelector` | `start`, `end` (half-open, bytes) | no: PDF streams are compressed | no | yes | yes |
| `CssSelector`, `XPathSelector` | `value`, `refinedBy?` | no | yes | no | yes |
| `SvgSelector` | `value` (inline SVG) | no | yes | no | yes |
| `RangeSelector` | `startSelector`, `endSelector` (same class) | yes | yes | yes | yes |
| `PdfRegionSelector` (extension) | `page`, `coordinate_space`, `rect` or `quads` | yes | no | no | no |
| `SitePageSelector` (extension) | `path`, `refinedBy?` | no | site sources only | no | no |

**`conformsTo` is open.** These IRIs get a grammar check (section 4.5); any
other IRI passes with a warning and its value is not checked, and a
FragmentSelector with no `conformsTo` also passes with a warning (the W3C
model says it SHOULD have one). The IRIs are identifiers, never fetched.

| IRI | grammar | families |
|---|---|---|
| `http://tools.ietf.org/rfc/rfc8118` | PDF fragment | pdf |
| `http://tools.ietf.org/rfc/rfc3778` | the same: RFC 8118 obsoletes RFC 3778, which the W3C Recommendation's table names, and the parameters mean the same in both | pdf |
| `http://tools.ietf.org/rfc/rfc5147` | text lines and characters | text |
| `http://tools.ietf.org/rfc/rfc3236` | an HTML element id | html |
| `http://www.w3.org/TR/media-frags/` | Media Fragments `xywh=`, `t=`, `track=`, `id=` | html, generic |

`PdfRegionSelector` exists because the W3C model has no rectangle selector for
PDF pages. `page` is the 1-based physical page. `coordinate_space` is
`{origin, units, x_axis, y_axis, page_box}`: `top_left` with `y_axis: down`
(or `bottom_left` with `up`), `units` `normalized` ([0, 1] of the page box) or
`pt` (within the page's size when the source gives one: its `page_sizes` range,
else `page_size`). `rect` is `{x0, y0,
x1, y1}` with `x0 < x1` and `y0 < y1`; `quads` is a list of `{x0, y0, ..., x3,
y3}` corners in drawing order. Coordinates are objects, not nested arrays,
because JSON-LD flattens nested arrays into an unordered set. Inside a
`page=N` fragment, its `page` must be `N` (section 4.4).

`SitePageSelector` exists because one rendered site is one source, not one
source per page. `path` is the page relative to the site root, in the grammar
of a destination's relative `href` (no leading slash, no `..` segment, no `#`,
no `:`). On a
`scope: site` source every top-level alternative is a `SitePageSelector`, and
it is never a refinement; its `refinedBy` then picks the place on the page.

### 4.3 One example per location kind

All values are synthetic; they are taken from `examples/annotations/`.

**A region of a PDF page**, with a lossy standard fallback (`highlight=` is
left, right, top, bottom in points from the top-left corner) and a quote for
re-anchoring:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc8118", "value": "page=7",
   "refinedBy": {"type": "PdfRegionSelector", "page": 7,
                 "coordinate_space": {"origin": "top_left", "units": "normalized", "x_axis": "right",
                                      "y_axis": "down", "page_box": "CropBox"},
                 "rect": {"x0": 0.147059, "y0": 0.176768, "x1": 0.852941, "y1": 0.328283}}},
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc8118",
   "value": "page=7&highlight=90,522,140,260"},
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc8118", "value": "page=7",
   "refinedBy": {"type": "TextQuoteSelector", "exact": "Definition 1.1 (Boxes).",
                 "suffix": " A box is a product of"}}
]
```

**A page of a PDF, nothing finer**, with the page also given structurally
(`"pages": {"first": 10, "basis": "physical"}` on the location), here under the
RFC 3778 synonym:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc3778", "value": "page=10"}
]
```

**A prose range inside one PDF page**: a `RangeSelector` refining the page.

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc8118", "value": "page=9",
   "refinedBy": {"type": "RangeSelector",
                 "startSelector": {"type": "TextQuoteSelector", "exact": "For part (ii),"},
                 "endSelector": {"type": "TextQuoteSelector", "exact": "This concludes the proof"}}}
]
```

**An offset into a PDF's text layer.** Offsets are per page, so the
`TextPositionSelector` refines a `page=` fragment, and they only mean something
with the source's `text_layer.extractor` and `version`, which I7 requires:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc8118", "value": "page=10",
   "refinedBy": {"type": "TextPositionSelector", "start": 1412, "end": 1530}}
]
```

**A passage of a web page**, pinned by revision in the source (the shape of
WikiLean's `oldid` targets), with a quote and an offset whose base element is
explicit:

```json
"selector": [
  {"type": "TextQuoteSelector", "exact": "an ideal whose complement is closed under multiplication",
   "prefix": "a prime ideal is ", "suffix": ", and which is not the whole ring"},
  {"type": "CssSelector", "value": "#mw-content-text",
   "refinedBy": {"type": "TextPositionSelector", "start": 311, "end": 367}}
]
```

**A declaration on an unhosted rendered site.** The source is `kind: web,
scope: site` with a `path` and a `revision` and no `url`; the page is chosen by
path and the place by its HTML id:

```json
"selector": [
  {"type": "SitePageSelector", "path": "Notes/Section_2/",
   "refinedBy": {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc3236",
                 "value": "Notes___sum_le"}}
]
```

**A line range in a LaTeX file.** RFC 5147 positions are zero-based points
between lines, so editor lines 42 to 47 are `line=41,47`:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc5147", "value": "line=41,47"},
  {"type": "TextQuoteSelector", "exact": "\\begin{theorem}\\label{thm:sum-comm}"}
]
```

**A line range in a Lean file**, used as a declaration's anchor (the location's
`items` is empty and the declaration lists it in `anchors`). Editor lines 113
and 114:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://tools.ietf.org/rfc/rfc5147", "value": "line=112,114"},
  {"type": "TextQuoteSelector", "exact": "theorem sum_comm", "suffix": " (f : Fin m → Fin n → ℕ) :"}
]
```

**Other media.** A Markdown file is `kind: other, media_type: text/markdown`
and takes the text rules (an RFC 5147 line range); an e-book located by an EPUB
CFI uses that grammar's IRI, which this checker does not know, so it validates
with a warning; a lecture recording is `media_type: video/mp4` and takes Media
Fragments:

```json
"selector": [
  {"type": "FragmentSelector", "conformsTo": "http://www.w3.org/TR/media-frags/",
   "value": "t=1830,1905&xywh=percent:10,10,80,60"}
]
```

### 4.4 Where selectors may sit

The rules name a selector's structural parent: the selector it refines, or
the `RangeSelector` it ends. The checker tracks that parent.

- A `PdfRegionSelector` is a top-level alternative, an end of a
  `RangeSelector`, or the direct refinement of a PDF `page=` fragment. It
  never refines a quote or any other selector. Inside a `page=N` fragment, at
  any depth, its `page` is `N`.
- A `TextPositionSelector` on a PDF refines a `page=` fragment directly, or is
  an end of a `RangeSelector` that refines one directly, because its offsets
  count within one page. Its direct parent must be the `page=` fragment or a
  `RangeSelector` under one: `page=7` > quote > `TextPositionSelector` is
  refused, and so is `page=7` > quote > `RangeSelector` of two offsets, while
  `page=7` > quote > `page=7` > `TextPositionSelector` is accepted, because the
  inner fragment is the direct parent. The rule is asymmetric by design: a
  region may end a `RangeSelector` under a quote, an offset may not.
- A `RangeSelector` may refine any selector, including a `page=` fragment; its
  two ends are of one class and inherit the page.
- A `page=` fragment inside another names the same page.
- A `SitePageSelector` is a top-level alternative on a site source and nowhere
  else.

### 4.5 Fragment grammars the checker enforces

- **RFC 8118** (and RFC 3778): `&`- or `#`-separated `name=value` parameters
  from `page`, `nameddest`, `structelem`, `comment`, `ef`, `zoom` (1 or 3
  numbers: the RFC makes left and top optional together), `view`, `viewrect`
  (4), `highlight` (4, left <= right, top <= bottom), `search`, `fdf`. `page` is
  a positive integer within `physical_page_count`. A refined PDF fragment must
  name its `page=`.
- **RFC 5147**: `char=` or `line=`, then a position or a range `a,b` / `a,` /
  `,b`, then any `;length=N[,charset]` or `;md5=<32 hex>`. `a < b` (an empty
  or reversed range is refused rather than ignored).
- **RFC 3236**: an element id, at least one character and no whitespace or
  `#`.
- **Media Fragments**: `&`-joined dimensions: `xywh=[pixel:|percent:]x,y,w,h`
  (integers, positive width and height, percent regions within 100),
  `t=[npt:]start[,end]` or `t=[npt:],end` (each time `sec[.f]`, `mm:ss[.f]` or
  `h:mm:ss[.f]`, start before end), `track=` and `id=` (a name with no
  whitespace). As the Recommendation's processing rules say, a name that is no
  dimension and a repeated dimension (the last one wins) are warnings.

### 4.6 Text layers and units

Offsets count **Unicode code points**, as the W3C model and RFC 5147 do. Lean's
`String.Pos` counts bytes and the language server counts UTF-16 units; in a
Mathlib file with astral characters the three differ, so convert before
writing. Half-open ranges: `[start, end)`.

A PDF has no single text layer: two extractors give different offsets for the
same passage. So a `TextPositionSelector` on a PDF needs
`text_layer.extractor` and its `version`, and a quote is the better primary
anchor: quote prose,
not formulas, which extraction garbles. `text_layer.unicode` (`none`, `nfc`,
`nfkc`), `whitespace` (`exact`, `collapse`, `ignore`) and `case` (`sensitive`,
`insensitive`) record the matcher, so re-anchoring after a new revision is
mechanical. NFKC with whitespace ignored reconciles ligatures and the micro
sign against Greek mu.

### 4.7 Export to and import from W3C JSON-LD

The file stays plain JSON. An exporter emits one `AnnotationPage` per document:
each link becomes an `Annotation` with motivation `linking`, a
`SpecificResource` body for the declaration (identifier written as the CURIE
`dcterms:identifier`, because a bare `identifier` is dropped by JSON-LD
processors), and one target per location of the item. The source id is
replaced by `ni:///sha-256;<base64url>` for a PDF, `swh:1:cnt:<git_blob>` for a
file in git, or the pinned `url`. On a site source the exporter composes the
page IRI from the source and the `SitePageSelector` path and drops that
selector. `PdfRegionSelector` and `text_layer` need an embedded extension
context; they must not redefine any key of the W3C context. No exporter is
shipped in v1.

An importer normalises what the W3C model allows and this format writes one
way: a single `selector` object becomes a one-element array; several
`refinedBy` values (which W3C defines as alternatives) become several
top-level alternatives, each with its parent and one refinement; a missing
`conformsTo` is kept missing (and warned about); `rfc3778` may be kept or
rewritten to `rfc8118`, which mean the same.

## 5. Ids

Every record has an `id` that is unique in its array and never reused once
retired (I9). Locations and links use readable slugs (`<item>@p<page>` for a
PDF location, `<item>/<role>/<target>` for a link). Declarations (5.1) and
items (5.2) follow mechanical rules, each recorded in the record's
`id_source`.

### 5.1 Declarations

| declaration | `id_source` | id |
|---|---|---|
| named, public or protected | `name` | the fully qualified name |
| `private` | `private` | `private:<module>:<declared full name>` |
| `example`, anonymous `instance` | `statement_hash` | `example:<12 hex>` or `instance:<12 hex>`, `~k` for k >= 2 |
| any anonymous, with an override | `declared` | `example:<slug>` or `instance:<slug>` |

The private form is Lean's own `_private.<Module>.0.<Name>` without the
mangling. The module is part of it because Lean allows the same private name in
two modules.

**One grammar.** `scripts/cflib/vocab.py` defines the anonymous-id grammar once
(`HASH_ID_PATTERN`, `DECLARED_ID_PATTERN`) and both `ids.py`, which writes ids,
and `validate.py`, which checks them, compile it. A declared slug is letters of
either case, digits, `.`, `_` and `-`, starting with a letter or digit;
capitals are allowed because Lean names are case-sensitive and existing tags
use them. A slug of exactly 12 lowercase hex digits (with or without `~k`) is
reserved for statement-hash ids, so the two forms never look alike.

**Statement hash.** `statement_sha256` is the sha256 of the source text from
the keyword to the `:=` or the top-level `where` that ends a typed header,
with every comment and the docstring removed and each run of whitespace
collapsed to one space; that normalised text is the declaration's
`signature`. The id is the keyword, a colon and the first 12 hex digits. A
`let` or `have` inside the type does not end the header, and an instance's
header also ends at a match-alternative line. String and character literals
are opaque (except a string nested inside an `s!` or `m!` interpolation brace, which
the scanner does not follow): a `:=`, a bracket or a keyword inside one never counts, and their
text stays in the signature. An untyped header (`example (d : ℕ) := t`)
hashes the whole command, since the term decides the proposition; the command
ends before the next non-blank line indented no deeper than the keyword's
line. The scanner finds `local` and `scoped` instances and declarations behind
the one-line prefixes `open ... in`, `set_option ... in`, `variable ... in`,
`include ... in`, `omit ... in` and `#guard_msgs in`; the hash still starts at
the keyword. `scripts/annotations/ids.py` is the reference implementation, and
the checker verifies that the 12 digits are the prefix of `statement_sha256`
and that the `signature`, when present, hashes to it.

**Ordinals are positional, so they are recorded.** When two such declarations
of one companion normalise to the same text, the later ones in (file, line)
order take `~2`, `~3`. Adding or deleting a copy can move `~2` to another
declaration, so the checker refuses a `~k` id unless the changelog records its
assignment: an `ordinal_assigned` entry naming the id, the `commit`, the
declaration's `file` and optionally its `line`. The latest record for a live
`~k` id (greatest `at`; a bare date counts as the start of its day, 00:00Z, so a
timestamped record on the same day is later) must name the file the declaration is in, so a copy
added or deleted in another file, which moves the ordinal across files, fails
until the new assignment is recorded. A record whose id a later entry retires
or renames stays valid, as history. `ids.ordinal_ids()` lists the ids that
need a record. measure_theory has 3 such ids today; tao-measure-lean has none.

One document holds only the current assignment, so it cannot detect a move
that keeps every recorded id live in its recorded file: a reorder within one
file (two copies swap, or one is deleted and another added in the same file)
gives `~2` to a different declaration there, and deleting the first copy gives
its bare id (no `~k`) to the next one, which the checker sees only as a `~2`
record whose id is gone. Only a diff against the previous document shows those
moves. For a statement that has, or may get, a textual twin, declare an id
(below).

**Why a hash and not a line.** Lean gives an `example` no name at all: it
leaves no constant and no entry in the `.ilean` declaration tables. A line
number moves whenever anything above it changes. In the design survey
(2026-09-26), over the first-parent history of the two worked companions,
positional ids were retired 72 times over the 22 first-parent commits that
touch tao-measure-lean's Lean and 361 times over measure_theory's 92 such commits,
while statement-hash ids were retired 0 and 8 times, and all 8 were real
restatements or removals. The reference implementation finds the survey
prototype's anonymous declarations at the same files and lines on both
companions (38 and 802) and reproduces its statement hashes for all of them
but one: a measure_theory `example` whose typed header ends at `where`, which
the prototype cut at the first field's `:=`. An anonymous instance's generated
name is kept as `lean_name` when a build is available, but it is not the id: it
can carry a module suffix and an order-dependent `_1`.

**Declared ids.** An override is accepted from exactly two places: a comment
directly above the declaration whose whole content is the backticked tag,
``/- `example:<slug>` -/``, or a docstring line that is exactly
`lean-companion id: <id>`. Attributes, the one-line `... in` prefixes above
and other comments may sit between. The tag's keyword must match the
declaration's. A comment that starts like a tag but carries anything else, or
a malformed `lean-companion id:` line, is not an id: `ids.py` reports it in `rejected_tag`
and the declaration keeps its hash id. A docstring that merely mentions
`example:foo` is ignored. `statement_sha256` is still required, so a
restatement under a declared id is logged (section 9) and its links go back
for review. A Lean attribute is deliberately not the mechanism: an unregistered
attribute is a hard error, so it would add a package every companion must
depend on.

Measured on measure_theory at `00a2438`: each of its 64 tags is the first
line of a longer comment (42 block comments, 19 module docs, 3 docstrings)
whose tag line is followed by prose, so under this rule none of them declares
an id and all 64 are reported as rejected tags; the 802 anonymous declarations
keep their statement-hash ids. Each tag becomes a declared id once it is
written as its own comment, ``/- `example:<slug>` -/``, directly above the
example.

**Known limits.** The hash is syntactic: a binder rename or a notation change
retires the id, and `variable`, `open` and namespace context is not seen (the
optional `namespace` field records the last). Hashing the elaborated type would
fix both but needs a Lean-side exporter; it is future work, not part of v1.

### 5.2 Items

| item | `id_source` | id |
|---|---|---|
| numbered | `label` | `<kind>-<number>[-<sub>]`, the kind lower-cased with each run of other characters as `-`: `exercise-1.2.3` |
| passage with a quote | `quote_hash` | `passage-<first 8 hex of sha256 of the normalised quote>` |
| passage without a quote | `page_title_hash` | `passage-p<printed page>-<first 8 hex of sha256 of the normalised title>` |
| any, with an override | `declared` | any slug except the two mechanical passage shapes |

The quote is `item.quote.exact` when the item has one; otherwise it is the
`exact` of the first `TextQuoteSelector` of the item's first location
(locations in id order; alternatives in order, each followed by its
`refinedBy` chain). A quote that was verified only within its page belongs on
the location, not in `item.quote`, which stands for the whole source. The
normaliser is NFKC followed by collapsing each run of whitespace to one space
and stripping the ends; case is kept. NFKC comes first so that ligatures, the
micro sign and no-break spaces from different PDF extractors give the same id.
The printed page is `pages.first` on the printed basis, or the physical page
less the source's `printed_page_offset`; a passage without a quote and
without a derivable printed page must be declared. The same normalised quote
on the same source is the same passage: a producer that meets it twice (two
declarations quoting one sentence) writes one item with two links. Two
different passages that would still get the same mechanical id (the same
quote on two sources, or the same page and title) are a duplicate-id error
(I1), resolved by declaring one of them. `ids.passage_quote_id`,
`ids.passage_page_id` and `ids.item_label_id` are the reference
implementation. A mechanical passage id changes when its quote or its title
and page change; that is a `retired` and `born` pair in the changelog, like a
restated declaration.

## 6. Invariants and warnings

The schema checks shape; `scripts/annotations/validate.py` checks these.

| | invariant |
|---|---|
| I1 | ids are unique within each array |
| I2 | every root array is sorted by `id`; id lists (`items`, `anchors`, `clusters`, `relations.*`, `related_to`, `legacy_ids`, `agents`, `signals`, `finding_ids`, `locations`, `alternate_urls`, `resolution.commits`, `resolution.replaced_by`) are sorted; a verdict's `declarations` are sorted by `declaration`, `provenance.inputs` by `path` and then by `source`, a source's `page_hashes` by `path` and `page_sizes` by `first`, one entry each. Strings compare by UTF-16 code units, the order of object keys (section 7) |
| I3 | every reference resolves (an input's `source` included); a link names a live declaration, or is `orphaned: true` and names a declaration id the changelog retired; only a link to a declaration may be orphaned; a verdict's `locations` show its item; a verdict with `status: fixed` has a `fixed` resolution (a verdict without one is open); a declaration has at most one snippet; `(item, target, role, sub)` is unique among links and `(cluster, target, role)` among memberships; a `parent` is a structure or class; an item is not related to itself |
| I4 | every item has a location, a `quote` or `pages` |
| I5 | an item with (non-orphaned) links but no `key` link carries `coverage`; an item with an `attributed` link has a `key` link; `not_formalized` and `out_of_scope` exclude a `key` link; `cluster_fallback: true` needs a cluster, no `key` link and, when the document has `memberships[]`, a membership in one of the item's clusters |
| I6 | `(source, kind, number, sub)` is unique among numbered items; `pages` stay within the source's page count and only apply to a PDF; a source's page fields describe a PDF, and its `page_sizes` ranges are ordered, disjoint and within the page count; a site's `sha256` is the sha256 of the manifest its `page_hashes` give (section 3.1) |
| I7 | selectors: allowed for the source's family (section 4.2), a known `conformsTo` right for the family, a fragment value without a leading `#`, value grammar (section 4.5), placement by structural parent (section 4.4), `start < end`, `RangeSelector` ends of one class, a text offset into a PDF has a `text_layer.extractor` and `version`; a region: `top_left` goes with `y_axis: down` and `bottom_left` with `up`, finite coordinates, `x0 < x1` and `y0 < y1`, a quad has at least three distinct corners, `normalized` coordinates lie in [0, 1] and `pt` ones are at least 0 and within the page's size (its `page_sizes` range, else `page_size`); a location's items are on its source; a location's `pages` contain every page its selectors name; a location with no items anchors a declaration or is named by a link; a link's `locations` are on its item's source; an anchor is on a `lean`, `web`, or other text/html source (section 3.3); on a source with `page_hashes`, a `SitePageSelector` names a listed page |
| I8 | declaration ids follow section 5.1 for their `id_source` (name ids are names, private ids are `private:<module>:<name>`, hash ids match `statement_sha256` and `kind`, a `~k` id has an `ordinal_assigned` record and the latest one names the declaration's `file`, declared ids follow the grammar); the flags agree with `id_source` (`name` is neither private nor anonymous, `private` is private and not anonymous, `statement_hash` and `declared` are anonymous) and an anonymous declaration has no `name`; a `signature` is normalised (no comments, each run of whitespace one space) and hashes to `statement_sha256`; a declaration is not its own parent; a field's or projection's `namespace` is its parent's `name`, and its `name` is that namespace, a dot and its own name |
| I9 | every `at` is a real calendar date or UTC instant; a retired or renamed id is not live; a `successor` or `possible_successor` is live and differs from the subject; `restated` names a declaration whose id is declared or named (not a statement hash, which a restatement retires), live or retired or renamed by a later entry; `ordinal_assigned` names a `~k` statement-hash id that is live, or that a later entry (an `at` no earlier) retires or renames |
| I10 | no `http://` or `https://` outside `sources[]` and `destinations[]`, `x_` values included. `conformsTo` and the root `$schema` are exempt (identifiers, never fetched), and so is quoted source text (`exact`, `prefix`, `suffix` of a `TextQuoteSelector`, and `item.quote`), which is an anchor, not a link. A viewer can then ship everything else without an external-URL scan failing |
| I11 | with `--canonical`, the bytes are the canonical form (section 7) |
| I12 | item ids follow section 5.2 for their `id_source`, and the quote or title a mechanical id hashes is not only whitespace; a declared item id does not take a mechanical passage shape |
| I13 | an `unattributed` declaration has no non-orphaned link |

Beyond shapes and patterns, the schema states these rules. The list is a
summary; the schema is the full statement.

- Items: `form` discriminates them (a numbered item has a `number`, a passage
  has a `title` and no `number` or `sub`, `kind: passage` is a passage);
  `label` ids are numbered, `quote_hash` and `page_title_hash` ids are
  passages, and `page_title_hash` ids need `pages` (I12 finds the quote);
  `relations` has at least one key.
- Sources: identity (section 3.1); `scope: site` is web only and
  `page_hashes` is on a site only; the page fields are on a `pdf` or `other`
  source only (I6 narrows `other` to `application/pdf`); `page_hashes` and
  `page_sizes` have at least one entry; `text_layer.extractor` and `version`
  come together.
- Declarations: `field` and `projection` need a `parent` and nothing else has
  one; a `private` declaration has a `name`; a `statement_hash` declaration
  carries `statement_sha256`; an `unlocated` declaration is a declared
  anonymous `example` or `instance` with no `line`, `doc_line`, `anchors`,
  `signature` or `statement_sha256`; other anonymous declarations are
  `example` or `instance` and carry `statement_sha256`.
- References: an input is `{path, sha256, ...}` or `{source}`, never both; a
  membership and a destination name a `declaration` or an `artifact`, not both;
  an `exact` destination has a `fragment` and a `fallback` one a
  `fallback_label`.
- Verdicts: a `fixed` resolution names a `pr` or `commits`, an `in_progress`
  one a `pr` or `branch`.
- Changelog: `retired` has a `reason`, `renamed` a `successor`, `restated`
  both `old_statement_sha256` and `new_statement_sha256`, and
  `ordinal_assigned` a `commit`, a `file` and `subject_kind: declaration`.
- Commits: `companion.commit`, `pinned_commit`, `snapshot_commit`, an input's
  `commit` and changelog commits are full 40- or 64-hex ids; only
  `resolution.commits` may be abbreviated.

**Warnings** do not make a document invalid; the CLI prints them as
`<file>: warning: <path>: <message>`: an unknown `conformsTo` IRI, a
FragmentSelector without `conformsTo`, a Media Fragments name that is no
dimension or a repeated dimension, a link `relation` outside the core, and a
`schema_version` whose minor is newer than the checker's.

## 7. Canonical form

The canonical text follows RFC 8785 (JCS) for everything that decides bytes in
another language, and adds a fixed layout:

- **Order**: strings compare by their UTF-16 code units, the order RFC 8785
  gives object keys, and the same order sorts record arrays, id lists and
  keyed lists (I2). It is what JavaScript's default `Array.prototype.sort`
  does, and it differs from code point order only for a character above
  U+FFFF (a Mathematical Alphanumeric letter in a Lean name) against one in
  U+E000..U+FFFF. Numbers (`page_sizes.first`) compare by value.
- **Numbers**: the ECMAScript `Number::toString` spelling of the IEEE double:
  the shortest digits that round-trip, integers
  without `.0`, plain decimal notation for 1e-7 <= |x| < 1e21 and exponent
  notation (`1e+21`, `1e-7`) outside it, `-0` written `0`. So `1.0` is `1`,
  `0.00001` is `0.00001` and `0.247647` is `0.247647`; Python's `json.dumps`
  would write `1.0` and `1e-05`. An integer beyond 2^53 is written as the double
  it becomes.
- **Strings**: escaped as ECMAScript `JSON.stringify` does (`\"`, `\\`, `\b`,
  `\f`, `\n`, `\r`, `\t`, other control characters as `\u00xx` in lower-case
  hex), everything else, non-ASCII included, written as UTF-8.
- **Layout**: two-space indent, one member or element per line, `": "` after a
  key, `{}` and `[]` for empty containers, LF line ends and a final newline.
  JCS itself has no whitespace; this layout and the order of records below are
  what this format adds.
- **Order of records**: record arrays sorted by `id`, id lists sorted, the
  keyed lists sorted by their key (I2), all in the order above. Selector
  arrays are **not** sorted, because their order is a preference; `evidence`
  keeps its order.

`validate.canonical_dumps`, `validate.canonicalize`, `validate.order_key` and
`validate.jcs_number` are the reference implementation; the tests pin the cases above and the ECMAScript boundary
values. `--print-canonical` writes the canonical form of any document, so a
producer never has to hand-sort. The loader refuses duplicate keys and `NaN` or `Infinity`, which
JSON does not have.

## 8. Validating

```sh
python3 scripts/annotations/validate.py examples/annotations/mixed.json --canonical
python3 scripts/annotations/validate.py <document.json> --print-canonical > <document.json.new>
```

Exit status 0 means valid, 1 invalid (one `path: message` line per problem), 2
unreadable. The checker reads `schema_version`, takes its major version and
loads `schema/annotations-v<major>.schema.json`; a major version it has no
schema for is a problem. The schema runs first, through the in-repo evaluator
`scripts/annotations/schemacheck.py`, because the skill is standard library
only; the invariants run only on a document the schema accepts. When the
`jsonschema` package is installed, the test suite checks that it agrees with
the in-repo evaluator on every example and on random mutations of them; one CI
job (Python 3.13) installs it for that.

## 9. Versions and the changelog

`schema_version` is `"<major>.<minor>"`. Readers match the major version;
writers declare the minor they used. A minor version only adds optional keys
or enum values; a major version changes something a reader of the previous
major cannot ignore. Every object is closed, so a checker that knows `1.0`
refuses a key added in `1.1` rather than silently dropping it, and warns that
the document is newer than the checker. Unknown keys are therefore never
passed through: update the checker, or keep the new data in `x_` keys until
then.

The `changelog` array is the id history the document carries with it:

- `born`: an id appeared (optional; git history has it too).
- `retired`, with `reason` `statement_changed`, `removed`, `renamed`, `moved`
  or `other`: the id is gone for good. Links to it stay, marked
  `orphaned: true`, until a human re-attaches them; `possible_successor` may
  suggest a new id but nothing moves automatically.
- `restated`, with `old_statement_sha256` and `new_statement_sha256`: a
  declared or named id whose statement changed. Its links and verdicts need
  review.
- `renamed`, with `successor`: the same declaration under a new name. The old
  name is no longer live.
- `ordinal_assigned`, with `commit`, `file` and optionally `line`: which
  declaration took a `~k` statement-hash id (section 5.1). A new record
  supersedes an older one for the same id; a record stays in the changelog
  after its id is retired or renamed.

`subject_kind` is `declaration`, `item`, `location` or `artifact`.

## 10. How the existing companions map in

Two exporters, `cf export attribution-table` and `cf export docstring-tags`
(`docs/exporters.md`), read the worked companions read-only, at measure_theory
`00a2438` and tao-measure-lean `f158509`, and write version 1.0 documents; the
documents and the reports written beside them are kept maintainer-side, in
`CF_M3_REFERENCE_DIR` (`tests/data/m3/README.md`), never in this repository. Both
validate with `--canonical` and no warnings. The figures below are from those
documents and reports. (The paragraphs were written from the prototype
documents the commands replaced; the source ids they mention are now the
registry's, `docs/exporters.md` section 6.)

| root array | measure_theory | tao-measure-lean |
|---|---|---|
| `sources` | 1 | 2 |
| `provenance` | 8 | 3 |
| `items` | 432 | 651 |
| `declarations` | 3565 | 1544 |
| `links` | 627 | 1169 |
| `locations` | 509 | 2201 |
| `destinations` | 2884 | 1544 |
| `clusters`, `memberships`, `artifacts` | 77, 5093, 61 | absent |
| `snippets`, `verdicts`, `changelog` | 542, 11, 3 | absent |
| `unattributed` | absent (0 rows) | 377 |
| items by `id_source` | 412 `label`, 20 `page_title_hash` | 416 `label`, 210 `page_title_hash`, 25 `quote_hash` |
| declarations by `id_source` | 556 `name`, 1789 `private`, 802 `statement_hash`, 418 `declared` | 1376 `name`, 130 `private`, 38 `statement_hash` |

**measure_theory** (PDF overlay). Each geometry region is a location with a
`PdfRegionSelector` (508 `rect`, 1 `quads`; the coordinate space is already
top-left, normalised, y down), its `visual_signoff` is `signoff` and its old
id goes to `legacy_ids`. The 20 unnumbered geometry items are passages whose
producer label is the `title` (`title_source: producer`); one geometry item
that names two book items (Remark 1.1.3 and equation (1.1)) becomes two
numbered items sharing its location. target-refinement gives 420 `key` and 159
`attributed` links. The ledger is the graph authority, pinned in
`companion.graph`: its 77 source nodes are `clusters`, its 5093 edges are
`memberships` (primary 1066, support 629, consumer 3398), its module-doc,
`#check` and figure nodes are 61 `artifacts`, and a support edge also becomes
an `example` link where its node has one item (48). The 418 ledger
`example:<slug>` ids that name no Lean declaration at HEAD are `unlocated`
declared declarations (490 memberships point at them). Each is the ledger's
name for an anonymous example in a named file, which the export cannot match
to one example because the ledger gives no line or tag, so most of them
probably name a hash-id declaration a second time: in 29 of the 50 files that
hold them the file has exactly as many hash-id examples, and in 49 at least as
many. The 64 comment tags in the Lean are refused as declared ids (section
5.1), so their examples keep hash ids and the tag goes to `legacy_ids`. The
ledger's positional private ids go to `legacy_ids` beside
`private:<module>:<name>`. Its 70 structure members are `field` (22) and
`projection` (48) declarations whose `namespace` is the parent's full name.
Signatures: all 802 anonymous declarations and 2237 named ones. The 238 items
with no item-level link have `cluster_fallback: true`; the 33 of them in
section 1.7 also carry the curation's class as `coverage` (29 `example_only`,
3 `support_only`, 1 `not_formalized`). All 2884 rows of destinations.json map
(488 exact, 2396 fallback, 61 of them to artifacts). The 11 verdicts keep
their evidence and regions (Exercise 1.2.24's two regions are one verdict with
two locations); 8 have a `fixed` resolution with the PR, the commits their
prose names as full ids and any replacing declarations: the fix and merge
commits for 6, and for Exercises 1.3.6 and 1.3.13, whose prose names only the
merge, the merge commit alone. 1 is open with issue #16, and 2 unconfirmed
ones have no resolution, so they are open. The 3 `~2` ids have
`ordinal_assigned` records with file and line. Still in `x_` keys: the
ledger's scope and snapshot tree (root), its source nodes' plan item ids and
statuses (clusters), the refinement's method name and curation count
(provenance), and the PDF's download checks (source).

Lossy, per the report: the ledger's edge realisation (constant per role, so
derivable), node class and verification-text hash, and integrity blocks
(recoverable from the pinned ledger); 93 declarations at HEAD that the ledger
does not list; the in-batch order of geometry items; which of a verdict's
commits is the merge; the class of the 205 fallback items in sections 1.1 to
1.6, which the curation note does not give item by item; and the kind of 6
private structure members, guessed as `field`. The printed-page offset (16) is
read from ledger locator prose, and the verdicts' sub-parts and replacing
declarations from their prose. Also lossy: the fix commit of the 2 verdicts
whose prose names only the merge; the 248 positional private ids whose ledger
line is stale at HEAD (resolved by module and name, the 6 private structure
members by their structure, the old id kept in `legacy_ids`); the ledger's
workspace-relative PDF path; the 20 passages' verbatim geometry labels
(`Unnumbered ...`, now titles); the bounding box that stands in for the one
quads region in its RFC 8118 fallback; the region granularity of 35 refinement
entries merged per item; the destinations file's header locks; and one
`replaced_by` mention that resolves to no declaration. The export report lists
each bucket with its count. The document is a build input for the viewer's
current files, not a runtime file: the viewer refuses a manifest that carries
graph keys such as `sources` (`FORBIDDEN_MANIFEST_GRAPH_KEYS` in its
`viewer.js`), and the membership counts above are the ones that file expects
of the ledger.

**tao-measure-lean** (docstring tags). Each tagged declaration gives an item
(from the tag), a declaration and a `key` link; "In-text proof of" gives a
`helper` link (6), and the tag's parentheses give the link's `sub` (290
links), never a separate item. The tag itself is `raw_label`. A `book p. N` on
a tag is the link's `pages` (`anchor: own`, `raw` verbatim; 343 links), since
one item's declarations can anchor different pages. Unnumbered "In-text" tags
are 235 passages: 25 have a docstring quote verified on their page and take a
`quote_hash` id from their location's quote (the quote stays on the location,
since it is unique only within its page); 210 take `page_title_hash` ids. One
passage quoted by two declarations is one item with two links. The tag's word
("definition", "corollary") is the passage's `kind` with `kind_source:
producer`, and the docstring title has `title_source: producer`. 17 passages
name the numbered neighbour their tag gives in `relations.near`; 16 of them
take their page from it (`pages.anchor: inferred`), and one
(`passage-p188-133da8aa`) gives its own page. The Verso site is one `scope:
site` source with a `path` and no `url` or `revision`, pinned by the sha256 of
its 38 HTML pages, listed in `page_hashes` (35 of them carry anchors). Its
1505 named anchors are a `SitePageSelector` plus an RFC 3236 fragment and its
38 anonymous ones a `SitePageSelector` plus the `data-binding` `CssSelector`;
the cross-reference index gives the 1544 destinations (1505 exact, 39
fallback; method `site_index`). The PDF text match names the PDF as a
`{source}` input. `(API)` and `(infrastructure)` markers become `unattributed`
rows with that `claim` (2 and 212, and 163 plain `unattributed`). Signatures:
1530 declarations, omitted for 10 structures, 1 inductive and 3 definitions by
pattern matching. Still in `x_` keys: how 16 inferred pages were found, the
two heading pages of 2 locations, and a note on the site source. Not carried:
the title of the second tag on the passage two declarations quote
(`passage-75a7d8f9` keeps one declaration's title and drops the other's,
"after Theorem 1.3.26"), 1 tag the item grammar refuses, 6 of the 32 passage
docstring quotes (not found, ambiguous, or found only on the next or previous
page) and the 14 of 21 link docstring quotes that could not be verified.

**The skill's adapters.** `docstring_tags`, `attribution_table` and
`blueprint_tex` read today's three conventions into `statements.jsonl` rows
(`adapters.base.ROW_FIELDS`). An `annotations` adapter reading this format
re-parses the Lean with `adapters.leanparse` for anything the document does not
carry: the statement, the body, and the namespace when it is absent. Per
column:

| row column | from this document |
|---|---|
| `item_raw` | carried: `link.raw_label` |
| `item` | derived: kind and number of the item (or its passage label) |
| `sub` | carried: `link.sub` |
| `item_kind` | carried: `item.kind` |
| `source_id` | carried: `item.source` |
| `decl` | carried: the declaration `id` (section 5.1 replaces `example@<file>:<line>`) |
| `kind` | carried: `declaration.kind` (a row's `kind` is any non-empty string, so `field` and `projection`, which no keyword declares, pass through as they are) |
| `namespace` | carried when `declaration.namespace` is present, else re-parsed |
| `file`, `line` | carried |
| `statement` | re-parsed with `leanparse.signature`; `declaration.signature` is the section 5.1 cut, which differs from leanparse's for a `let` in the type and for an untyped example |
| `body` | re-parsed |
| `sorry` | derived: `declaration.sorry` is not `none` |
| `sorry_class` | carried: `declaration.sorry` |
| `page_anchor` | carried: `link.pages.first` (else `item.pages.first`) on the printed basis |
| `page_anchor_raw` | carried: `pages.raw` |
| `anchor_source` | carried: `pages.anchor` (`own`, `crossref`); no `pages` is `none`, and `inferred` has no row value |
| `repo` | carried: `companion.name` |
| `key` | derived: the link's `role` is `key` |
| `anonymous`, `private` | carried |
| `granularity` | derived: `item` for a row from a link, `node` for a row from a membership |
| `source_sha` | carried: `source.sha256` |
| `provenance` | derived from `provenance.method`: `docstring_tag` is `docstring`, `blueprint_macro` is `blueprint`, `lean_scan` is `lean_scan`, anything else `table` |

Its rows should be diffed against the current adapters' rows before anything
switches.

**WikiLean.** Its native annotations are one per statement. Field by field:
the annotation becomes a passage item on a web source pinned by `oldid` in
`revision` (and the URL); `section` becomes `item.section`; the snippet or
`theorem_box` anchor becomes a `TextQuoteSelector` location, a `prose_range`
anchor a `RangeSelector` from its `from` quote to its `to` quote, and each
further entry of `anchors[]` another location of the same item. A
`math_alttext` anchor names a formula by the `alttext` attribute of its
`<math>` element, which is how WikiLean's renderer finds it, so it becomes a
`CssSelector` `math[alttext="<value>"]` (the value escaped as a CSS string). A
`prose_range` that ends at a formula (`to_math` in place of `to`) has ends of
two classes, which a `RangeSelector` cannot join, so it becomes two locations,
the `from` quote and the formula's `CssSelector`, and the span between them is
lost. `kind` becomes the item's `kind` with `kind_source: producer`; `label`
becomes `title` with `title_source: producer`; `status` (`formalized`,
`partial`, `not_formalized`) becomes `coverage`, and `rejected` is a tombstone
and is not exported. Only the `.agent1.json` drafts, which WikiLean's own
tools skip and which are not exported, carry the statuses `pending`,
`in_mathlib` and `formalizable`. `mathlib.decl` and `mathlib.module` become a
declaration with `package: mathlib` and a link: role `key` when the status is
`formalized` or `partial`, and role `support` with relation `related` when it
is `not_formalized` (the declaration is the nearest Mathlib statement, not a
formalisation; I5 forbids a key link on a `not_formalized` item, and 16 tracked
WikiLean annotations carry a declaration with that status); `match_kind` becomes
`link.relation` when it is a lower-case snake_case token (section 3.4); `note`
becomes `link.note`; the provenance string (`ai`, `ai-agent1`, `ai-moderated`,
`human`) becomes a provenance record (`actor` `agent` with `method:
agent_annotation`, or `human`); the 12-hex native id can be kept in
`item.legacy_ids`. Its W3C export is the shape section 4.7 describes.

## 11. Left out of v1

- An importer, and a W3C exporter. The two companion exporters exist
  (`cf export`, `docs/exporters.md`); the adapters, the viewer and WikiLean do
  not read or write this format yet.
- A `cf` command for the checker, which runs by path, as above (`cf export`
  and `cf review-tags` exist).
- A Lean-side exporter for elaborated-type hashes, and the namespace in the
  statement hash: the normaliser is frozen, and adding context to the hash
  would re-key every anonymous declaration.
- Book text. A `quote` or a `TextQuoteSelector` holds a short anchor, and the
  document lives in the companion's repository or a workspace, never in this
  one (`POLICY.md`); the examples here are synthetic.
- A producer. `docs/proposals/pdf-to-companion.md` proposes the pipeline that
  would write this format from a PDF; none of it is built.
