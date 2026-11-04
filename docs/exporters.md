# The companion exporters: `cf export` and `cf review-tags`

Two commands turn a companion's own attribution data into an annotation
document (`docs/annotations.md`, format 1.0, release candidate 1.0.0rc2 as
vendored), and one reads such a document back for the attribution defects
the audits found. They are milestone M3 of
`docs/proposals/pdf-to-companion.md` (section 5.4): the exporters reproduce
the two documents the format was first written from, record for record, and
the tag review reproduces the known answers.

Neither command needs a workspace. Both read a checkout at a pinned commit
through `cflib.gitsafe` and write nothing under it. Each document declares
in its root `profiles` exactly the format profiles its keys use
(`annotations.export.common.declare_profiles`, invariant I14): `graph` for
clusters, memberships, artifacts and `companion.graph`; `site` for
destinations and snippets; `audit` for verdicts (a source's `role` is core,
so a document with no verdict declares no `audit`); `history` for the
changelog. A document with none of those is core only and carries no
`profiles`. Before the command returns, the document is
checked canonically and with the format's `external-urls` lint, which the
exporters keep their documents clean of: a URL in a note, a title, a tag or a
coverage note is dropped and reported rather than written.

**Contents**: 1 attribution-table · 2 docstring-tags · 3 what is lossy · 4 the
tag review · 5 reproducing the references · 6 deliberate differences · 7 the
locate record set · 8 informal-markers

`cf export literature-links` is the one export that is not a companion's: it
needs a workspace and writes a locate run's sealed record set (section 7).
`cf export informal-markers` (section 8) reads a companion the skill wrote
itself, an informalization companion's output repository, and is the one
export whose `companion` is another repository than the checkout: the Lean
library its text is about.

## 1. `cf export attribution-table`

```sh
cf export attribution-table <checkout> --commit <sha> --out <doc.json> \
    [--lean-build <dir>] [--report <file>] [--name <name>] [--repo-id host/org/name] \
    [--site-name <site kind>] [--source-id <id>] [--source-title <title>] \
    [--source-edition <edition>] [--page-offset <n>] \
    [--data-dir <dir>] [--ledger <file>] [--official-source <file>]
```

Both subcommands take the same common flags. `--site-name` is the
destination site kind, written as every destination's `site` (and, by
attribution-table, as the prefix of its id; default `verso_literate`). `--out` and `--report` may not lie inside the
checkout: the command refuses them rather than write there.

The convention `adapters/attribution_table.py` reads: a PDF-overlay companion.
The exporter reads, at the pinned commit,

| input | becomes |
|---|---|
| `<data-dir>/geometry-batch-?.json` | one `location` per region (`PdfRegionSelector`, plus an RFC 8118 `highlight=` fallback), its visual sign-off as `signoff`; one `item` per geometry label (numbered through the item grammar, an unnumbered label a passage titled by the producer; the words after `Equation (n):` a `title` with `title_source: producer`); one `provenance` record per batch |
| `<data-dir>/target-refinement.json` (schema version 2) | `key` and `attributed` `links`, merged per item; its *coverage entries* (an entry with a `coverage` class, no key ids, a `note`) as the item's `coverage` and `coverage_note`, their declarations as the links the class admits (below) |
| `<ledger>` | `clusters` (source nodes), `memberships` (edges), `artifacts` (module docs, checks, figures), the declarations the nodes name, `companion.graph` |
| `<data-dir>/destinations.json` | `destinations` and `cluster.related_to` |
| `<data-dir>/faithfulness-verdicts.json` | `verdicts`, with a `resolution` read from the summary prose and the commits it names |
| `<official-source>` | the PDF source's URLs, bytes and retrieval time |
| every tracked `.lean` file | every declaration (`adapters.leanparse`), the statement-hash ids and signatures of anonymous ones (`formal_companion_annotations.ids`), the section 5.1 cut for named ones |
| the tracked Lean file the ledger names for each id the refinement lists | its `snippet` (`adapters.leansnippets`, the rule of `extract_declaration_snippets.py` at measure_theory ef0a424 except that a definition is shown whole: a theorem's keyword through the `:=` of its proof, the first one outside brackets, comments and strings that closes no `let` or `have` (a `let x ←`, or an alternative's `=>`, closes one too); a `def`, `abbrev`, `instance`, `structure`, `class`, `inductive` or `opaque` with its body, fields or constructors, up to the next command (a body opened by `:=`, `=>`, `do`, `by` or `where` may run on at column 0, and a comment ends it only when a command follows); a field at its line under its structure's header, with the deeper lines that continue its type; an `example:<slug>` by its tag comment; past 14 lines, or 60 for a definition, cut and marked `…`, `truncated`); an id it cannot locate gets none and is listed in the report's `snippets_unlocated` |
| a Lake build's `lib/lean` (`--lean-build`, default `<checkout>/.lake/build/lib/lean`) | an anonymous instance's generated name as `lean_name`, when the `.ilean` postdates the file's last commit |

When that build's trace hashes match a file's text, its declarations are read
from the build (`.ilean`, and the literate JSON beside `lib/`) instead of
parsed; the report's `lean_source` counts files by origin. A
`<data-dir>/declaration-snippets.json` is not read; the report's
`ignored_inputs` names it, and `cf export` and `cf overlay export` warn of it
and of any `snippets_unlocated`.

Three of those paths are flags, so a companion that keeps the same data
somewhere else needs no new convention. Each is **relative to the checkout**,
read at the pinned commit like every other input; an absolute path, or one that
climbs out with `..`, is refused.

| flag | default | moves |
|---|---|---|
| `--data-dir` | `pdf-overlay/data` | the geometry batches, the target refinement, the verdicts and the destinations |
| `--ledger` | `source-formalization-ledger.json` | the source-formalization ledger, which is also `companion.graph.path` and every provenance input that pins it |
| `--official-source` | `pdf-overlay/official-source.json` | the PDF's download record |

The PDF source is named through the registry by the sha256 the overlay
records: id, title, edition and printed-page offset come from
`registry/sources.yaml` (`--source-id`, `--source-title`, `--source-edition`
and `--page-offset` supply them for an edition the registry does not know; the
ledger's locator prose is cross-checked against the offset). `companion.name`
and `companion.repo` come from the `origin` remote, or `--name` and
`--repo-id`.

The document validates canonically before the command returns. The report
(`<out>` with `.export-report.json`, or `--report`) carries the counts, the
lossy and bent buckets of section 3 and any problems; a problem or an invalid
document exits 1 after writing. A formalization id the refinement,
destinations, verdicts or a ledger edge name and the ledger does not list is a
refusal naming the file and the id, as is an unnumbered passage before printed
page 1 (section 5.2 of the format needs such a passage declared), a quad with
fewer than four points, or an unnumbered label with no title. Any other input
the exporter does not check for (a `KeyError`, `ValueError`, `IndexError` or a
failed tool) ends the command with one error line, never a traceback.

A geometry label's sub-part goes through the renderer both exporters share
(`annotations.export.common.render_sub`, over the item grammar's groups):
nested groups join with `.` (`(i)(a)` is `i.a`), a range stays a range
(`(i)-(iii)` is `i-iii`) and a list a list (`(i), (iii)` is `i,iii`), so an
item is never narrowed to its first part, and a primed part keeps its prime
(`(b')` is `b'`, and the item id is `remark-1.1.4-b'`). A parenthetical
attached to the number that the grammar refuses (`1.2.3(ii*)`) is dropped
and reported as a problem. An item the table keys to an anonymous `example`
carries `coverage: formalized`: the example states it (section 5.2 of the
format), and the class says so, where an `example_only` entry's examples
are `example` links.

**Coverage.** The table's coverage entries (schema version 2; a version 1
table, which classified the items with no entry in its provenance note's
prose, is refused) give an item its class and note. The declarations such an
entry lists become the links invariant I5 admits for the class: a
`partial_only` entry's are `attributed` links with `relation: part`, or
`special_case` when the note says so; a `support_only` or `not_formalized`
entry's are `support` links; an `example_only` entry's anonymous examples
are `example` links and a `private_only` entry's private declarations `key`
links (anything else under those classes is a `support` link, reported).
Entries of one item's regions must agree on the class; a key id on a coverage
entry is a problem. An item with no entry and no ledger-derived link is shown
through its clusters: the format writes no flag for that (a reader derives
it from `clusters` and the absence of links), and such an item carries no
`coverage` and is counted under `item.coverage_unclassified`. An item whose
only links are ledger support edges carries the class they imply
(`example_only` when every one is an example, `support_only` otherwise).

## 2. `cf export docstring-tags`

```sh
cf export docstring-tags <checkout> --commit <sha> --pdf <the anchor edition> \
    --xref <xref.json[.gz]> --site-pages <pages.json> [--site <rendered site>] \
    [--adapter-config <adapter.yaml>] --out <doc.json>
cf export docstring-tags <checkout> --commit <sha> --source-id <id> --source-title <title> \
    --source-revision <edition stamp> [--source-media-type <type>] [--source-doi <doi>] \
    [--source-isbn <isbn13>]... [--adapter-config <adapter.yaml>] --out <doc.json>
```

The PDF and the site are each optional. Without `--pdf` the edition is a
source whose bytes the producer does not hold: `kind: pdf` pinned by the
`identifiers` from `--source-doi` and `--source-isbn` (section 5.1 of the
format; no `sha256`, page count, offset or text layer), with the `revision`
given as `--source-revision` (an edition stamp); with no identifier, or a
`--source-media-type` that is not `application/pdf`, it is `kind: other`
with that `media_type` and the revision as its identity. A numbered item is
then located by its number alone (no location, quote or pages of its own: I4
is for passages), a bare tag's parenthetical becomes the item's `title` with
`title_source: producer` when the item's tags agree on it
(`item.title_from_tag`, a bent bucket), a tag's page is kept on its link
unchecked when the medium is a PDF and dropped otherwise, and an in-text
passage is keyed by its printed page and title or, with no page, reported
and left out. The report's `source.bytes_unpinned` bucket says that nothing
in the document is verified against the edition. Without `--xref` and
`--site-pages` (which come together) the document has no site source and no
destinations.

A tag that names two numbered items (`Lemma 2.3.2 / Exercise 2.3.1`: the
exercise is to prove the lemma) gives the declaration a `key` link per item,
and the two items are related with `relations.same_as` (counted in the
report as `items.items_paired_by_one_declaration`). A `key` link whose
declaration is an anonymous `example` says the example states the item, so
the item carries `coverage: formalized` (never `example_only`, which is for
an item linked to examples with role `example` alone).

The convention `adapters/docstring_tags.py` reads: every declaration that
formalises a book item opens its docstring with the item's tag. The exporter
reads

| input | becomes |
|---|---|
| the Lean files the adapter config globs (`--adapter-config`, default `templates/adapters/docstring_tags.tao-measure-lean.yaml`) | one `key` link per tagged declaration (the tag's parentheses as the link's `sub`, through the shared renderer of section 1, its `book p. N` as the link's `pages`, the tag verbatim as `raw_label`, the rest of the parenthetical as `note`); a `helper` link for `In-text proof of <item>`; every declaration, tagged or not, with its sorry class; `unattributed` rows for the untagged, with `(API)` and `(infrastructure` markers as the claim. Every re-read of a tag reads it the way the adapter config does (`tag.require_backticks`, `tag.pattern`, `tag.position` and the keywords) |
| `In-text` tags | passages: one per tag, keyed by a docstring quote verified once on its page, or on any page of a `pp.` range (`quote_hash`), or by printed page and title (`page_title_hash`); a numbered item the tag mentions is named in `relations.near`, and a passage with no page of its own takes its page from that neighbour's heading; the tag's own single word (`definition`, `convention`) is the passage's `kind`, anything longer leaves it `passage` |
| `--pdf`, through `pdftotext` | the anchor edition's text layer: every numbered item's heading line becomes its location (a `TextQuoteSelector` under `page=`), docstring quotes are verified before a selector is written; `pdfinfo` gives the page box |
| `--xref` | `destinations` and, on the site source, the anchor locations of named declarations (`SitePageSelector` plus an RFC 3236 fragment) and of anonymous ones (a `CssSelector` on the keyword's `data-binding`) |
| `--site-pages` | the site source: one `scope: site` source whose `sha256` is the digest of its page manifest, carried as `page_hashes`, with its id, title, path and render time. The producer chooses the pages it pins: the manifest must list every page a location names and may leave generated fragments (lazy states, search indexes) out |
| `--site` | the pinned pages checked against their hashes, then every anchor checked to occur once on its page; pages on disk the manifest leaves out are counted (`site.html_pages_on_disk_not_pinned`), not refused; without `--site` the index is trusted and the site provenance says so |

A numbered item whose heading line the text layer does not show takes its
`pages` from its earliest key link that has them (counted in the report as
`headings.pages_from_key_link_items`). A tag's page outside the PDF, or a
reversed range, a sub-part the grammar refuses, or a URL in a note, a
passage title or a tag (the format's `external-urls` lint) is dropped and
reported as a problem naming the declaration, so the document stays valid
and the command still exits 1.

The site pin is a small JSON file: `{"source_id", "title", "path",
"rendered_at", "site_sha256", "pages": [{"path", "sha256", "bytes"}]}`, each
`path` a file (`TaoMeasure/Section_1_1_1/index.html`, never a directory).
`cf export site-pages <rendered site> --out <pages.json> [--source-id ID]
[--title T] [--path P] [--rendered-at TIME] [--exclude GLOB]...` writes it:
every HTML page under the site but those whose path matches an `--exclude`
glob (`'*/_lazy/*'` leaves out generated fragments), `path` defaulting to the
site directory's name and `rendered_at` to the newest pinned page's mtime. The
reference companion's is `tml-site-pages.json` in the maintainer's
`CF_M3_REFERENCE_DIR` (`tests/data/m3/README.md`); it is not in this tree.
The PDF is named through the registry as in section 1.

## 3. What is lossy

Each exporter's report lists, under `lossy`, the source data the format
cannot carry and, under `bent`, what it carries in a shape the format did not
intend (an `x_` key, a value parsed from prose). The buckets over the two
reference companions, with the counts of the maintainer's reports (kept in
`CF_M3_REFERENCE_DIR`, never in this tree):

**attribution-table** (`measure_theory.export-report.json`), lossy:

| bucket | count | what is lost |
|---|---|---|
| `declaration.head_not_in_ledger` | 93 | named or private declarations at HEAD that the ledger snapshot does not list are not exported |
| `declaration.private_line_stale` | 451 | the ledger's positional private ids name lines that moved; resolved by module and name, the old id in `legacy_ids` |
| `declaration.private_field_kind_guessed` | 6 | a private structure member the ledger gives no kind is exported as `field` |
| `membership.realization` | 1995 | the edges' `realization` (constant per role, so derivable) |
| `ledger.node_class_and_text_hash` | 3181 | the nodes' class and verification-text hash (recoverable from the pinned ledger) |
| `ledger.integrity_blocks` | 1 | the ledger's `files[]`, signatures, counts and authority (recoverable from the pinned ledger) |
| `item.coverage_unclassified` | 1 | an item with no refinement entry and no link, shown through its clusters, whose class the curation has not given (the equation split off Remark 1.1.3's label) |
| `item.passage_label` | 20 | the verbatim `Unnumbered ...` geometry label, which became a producer title |
| `link.region_granularity` | 75 | the per-region keying of refinement entries, merged per item |
| `geometry.batch_order` | 4 | the in-batch order of items and the batch page partitions |
| `location.quads_highlight` | 1 | the RFC 8118 fallback of a quads region is its bounding box |
| `destinations.header` | 1 | the destinations file's lock blocks and relation id |
| `verdict.commit_roles` | 8 | which of a resolution's commits is the merge |
| `verdict.replaced_by_unresolved` | 1 | a `<name> (<File>.lean:<line>)` mention that resolves to no declaration (a repo path or a tail of one) |
| `verdict.replaced_by_evidence_only` | 1 | a declaration the evidence names and the note does not, counted over the fixed verdicts with a `replaced_by` (absent when there is none) |
| `source.path` | 1 | the ledger's workspace-relative PDF path |
| `source.printed_page_offset_from_prose` | 1 | the offset is read from locator prose and the registry, not a companion field |

Three lossy buckets and two counts appear only when their input does:
`link.key_outside_formalization_ids` (a refinement key id its own entry's
`formalization_ids` does not list; links follow `formalization_ids`),
`link.pages_without_pdf` and `source.bytes_unpinned` (docstring-tags without
a PDF, section 2), and the counts
`ledger_support_edges.same_link_from_another_node` (one support edge reached
through two single-item nodes gives one link) and
`ledger_support_edges.already_listed_by_refinement` (a support edge a
coverage entry already lists).

and bent: the ledger's scope and snapshot tree (`x_graph_scope`, 1), the source
nodes' plan ids and statuses (`cluster.x_plan_status`, 77), the refinement's
method name and curation count (`provenance.x_refinement_fields`, 1), the PDF's
download checks (`source.x_download_checks`, 1), the ledger's own words for the
edition (`source.x_companion_edition`, 1), the 19 comment tags refused as
declared ids and kept as `legacy_ids` (`declaration.comment_tag_as_legacy_id`),
one geometry label naming two items (`item.split_merged_label`; their order is `location.x_item_order`), the 55 items
classified by the table's coverage entries (`item.coverage_from_table`: 23
`support_only`, 19 `partial_only`, 13 `not_formalized`, giving 50 support and
32 attributed `part` links), the adjudication ledger's own verdict class read
from the evidence prose (`verdict.x_ledger_verdict`, 1), and the verdicts'
sub-parts and replacing declarations read from prose
(`verdict.sub_from_prose`, 2; `verdict.replaced_by_from_prose`, 4). Four bent
buckets appear only when their input does: `item.coverage_from_support_edges`
(an item linked only by support edges), `link.relation_from_note` (a
`partial_only` note that says "special case"),
`link.support_role_from_coverage_class` (a coverage entry listing a
declaration its class does not name) and, without a PDF,
`item.title_from_tag` (section 2).

**docstring-tags** (`tao-measure-lean.export-report.json`): no
lossy bucket and three bent buckets: `source.x_note` (1, why the site is
pinned by content), `location.x_page_basis` (16, how a passage's page was
placed) and `location.x_heading_candidates` (2, the pages of every heading
line of an item the text layer shows twice). Its counts record what could
not be located rather than lost: 6 of 32 passage docstring quotes not found,
ambiguous or found only on the next or previous page, 14 of 21 link
docstring quotes not verified, 1 tag the item grammar refused (an
`unattributed` signal), 1 index anchor the page lacks (a `fallback`
destination) and 29,729 `_lazy/` fragments on disk the manifest leaves
out. Without `--site` a further bucket, `site.anchors_unverified`, counts the
anchors that were not checked.

## 4. `cf review-tags`

```sh
cf review-tags <document.json[.gz]> [--json] [--limit N] [--keep-pattern RE] [--hide-pattern RE]
```

Read-only over a document, no checkout. Six checks, each a defect shape the
audits of the two companions met. An invalid `--keep-pattern` or
`--hide-pattern` exits 2 with one error line.

| check | what it reports | the known answer |
|---|---|---|
| `attribution_gap` | an item with no `key` link to a declaration (a key on a cluster does not count) and no `coverage` class that excludes one (`not_formalized`, `out_of_scope`, `support_only`, `example_only`, or `partial_only` with the `attributed` links that are its partial statements): a `formalized` item with no key, or a `partial_only` one with no attributed link, is reported, since a claimed formalization no link names is the defect | measure_theory: 1 item at 2cd263a (`equation-1.1`, split off Remark 1.1.3's label; at 00a2438 it was 205, the fallback items of sections 1.1 to 1.6 the curation has since classified); tao-measure-lean: `remark-1.4.50` |
| `key_on_structure_member` | a `key` link, or a `primary` membership, on a `field` or `projection` | measure_theory: 10 key links over 3 items and the 142 primary memberships its PR #1 counted |
| `borrowed_key` | a numbered item whose every key declaration is another item's key, where that other item (the owner) also has a key this one lacks (`via: links`), or, with no key of its own, whose cluster fallback (an item with `clusters` and no link; the format writes no flag) shows only other items' keys (`via: clusters`) | measure_theory: 10 and 13, among them Exercise 1.1.2, whose entry its PR #19 records as stating Lemma 1.1.2(ii) |
| `shared_key` | the same sharing with no owner: items keyed only to declarations they share with items that hold nothing more, each listed once and neither called a borrower | measure_theory: 42, mutual groups such as Definitions 1.4.34 and 1.4.35, or Corollary 1.6.35 with Exercises 1.6.40 and 1.6.41 |
| `hidden_spelling` | a book-content docstring-tag link (`key` or `attributed`) whose raw label matches the fold script's proof-plumbing rule (`--hide-pattern`, default `\bIn-text proof`) or carries none of the book anchors it keeps (`--keep-pattern`); a `helper` link is proof plumbing the script is right to hide | tao-measure-lean: none. Its 14 book-content tags spelled `In-text proof (book p. N)` were respelled in its PR #12; the 5 links still spelled `In-text proof of Lemma 1.1.2` are helper links |
| `adjudicated_attribution_gap` | the document's own verdicts of class `attribution_gap`, or whose `x_ledger_verdict` (the adjudication ledger's class, which the format carries under no field of its own) is `ATTRIBUTION_GAP` | measure_theory: Exercises 1.2.21 and 1.3.4, both fixed |

`tests/test_review_tags.py` plants each defect in a small document and lists
what the two reference documents report today (when `CF_M3_REFERENCE_DIR`
names them; otherwise that class skips).

## 5. Reproducing the references

The references are the exporters' own output over the two private companions,
so they are maintainer-side: a directory named by `CF_M3_REFERENCE_DIR`, whose
contents `tests/data/m3/README.md` lists. No file derived from a private
companion is committed to this repository. With the
pinned checkouts on disk (`measure_theory` at `2cd263a`, the first commit whose
refinement table is version 2; `tao-measure-lean` at `677ada1`), the published
edition's PDF and the rendered site whose module pages the manifest pins:

```sh
M3="$CF_M3_REFERENCE_DIR"            # the pinned site index and page manifest
cf export attribution-table <measure_theory checkout> --commit 2cd263a \
    --out measure_theory.annotations.json --lean-build <its .lake/build/lib/lean>
cf export docstring-tags <tao-measure-lean checkout> --commit 677ada1 \
    --pdf <the published PDF> --xref "$M3/tml-site-xref.json.gz" \
    --site-pages "$M3/tml-site-pages.json" --site <its _site> \
    --out tao-measure-lean.annotations.json
gzip -n -9 measure_theory.annotations.json tao-measure-lean.annotations.json
```

The references written against the withdrawn tag now named `v0.1.0` (at
`00a2438` and `f158509`) do not validate under the release candidate and must be
regenerated at these pins; the reproduction tests skip until they are. A
measure_theory reference written while snippets were read from its
`declaration-snippets.json` differs at `2cd263a` in two definition snippets
(that file predates the rule that stops a body at an `example` tag comment)
and must be regenerated too.

`python3 -m unittest tests.test_export` does the same comparison record for
record when the environment names the inputs: `CF_M3_REFERENCE_DIR` (the
references, the site index and the page manifest), `CF_MT` and `CF_TML` (the
checkouts), `CF_TAO_PDF` (the PDF), and optionally `CF_TML_SITE` (default
`<CF_TML>/_site`) and `CF_MT_BUILD` (default `<CF_MT>/.lake/build/lib/lean`;
without the one `.ilean` that names anonymous instances, three declarations
lack `lean_name` and the test allows exactly that). The `pdftotext` version is
the one environment-dependent field and is normalised before the comparison.
Without those variables, or with a reference file missing, the reproduction
tests, the reference checks and `tests/test_review_tags.py`'s known answers skip and the synthetic
companions still run everywhere.

## 6. Deliberate differences from the prototype documents

The format was first written from two prototype documents. The references
differ from them in five ways, all deliberate and recorded in
`tests/test_export.py` (`DELIBERATE`):

1. `companion.generator` names the command and the skill's version.
2. The PDF source is named through the registry: one id
   (`tao-measure-gsm126-published`), title and edition for both companions,
   where the prototypes wrote `tao-measure-pdf` and `tao-gsm126-published` for
   the same bytes. The ledger's own words for the edition stay on the
   measure_theory source as `x_companion_edition`.
3. The site source's title and `retrieved_at` come from the page manifest,
   not from the build's file times.
4. An equation's title is marked `title_source: producer` (measure_theory:
   `equation-1.2` and `equation-1.3`).
5. A definition's snippet is the whole declaration up to the next command
   (at most 60 lines), not its first 14 lines; a theorem's runs past a `:=`
   in a named argument, a structure instance, a `let` or a `have` to its
   proof's; a field's keeps the lines that continue its type (measure_theory).

Every other record is byte for byte what the prototypes wrote.

## 7. `cf export literature-links`

```
cf export literature-links --run <locate run> [--challenge-run <run>]... --set-id <id> \
    [--byte-budget N] [--out DIR]
```

A workspace's locate run (`docs/proposals/wikipedia-locate.md` §2.7 and §11.1),
not a companion's: `--run` names the harvested, gated locate run and
`--challenge-run` each challenge run whose verdicts apply; the set is written to
`exports/<set-id>/` (or `--out`) in the layout of `references/locate-documents.md`
§6, and what was refused to `reports/export.<set-id>.json`. `--byte-budget` is a
per-source cap on `records/<source-id>.jsonl` and its receipts for a consumer
that has one (default 0: no cap): a source over it is refused whole, never
truncated, and `SEAL.json` records the budget that applied. Every record is checked
with the vendored Literature Links checker before the set is sealed; a passage
whose challenge is owed is refused by name (hard rule 5), and the command exits 1
after writing the rest.

## 8. `cf export informal-markers`

```sh
cf export informal-markers <output repository> --commit <sha> \
    --pdf <the canonical PDF> --pdf-url <its release URL> \
    --library-src <the library's source at the commit> --out <doc.json> \
    [--source-title <the text's title>] [--source-id <id>] [--page-offset <n>] \
    [--items-approved] [--name <name>] [--repo-id host/org/name]
```

The informalize mode's own export (`docs/proposals/informalize.md` section
10): the checkout is an informalization companion's **output repository**, and
what it describes is the PDF the fleet wrote, box by box. It reads the four
files `informal/informalize.json`, `items.json`, `declarations.json` and
`markers.json`, the canonical PDF whose sha256 `markers.json` pins, and the
library's source under `--library-src` for the snippets a card shows. It
refuses a PDF the markers were not measured on, and an `informal/` that
changed since the commit the PDF was built from (`markers.source_commit`),
`markers.json` itself excepted.

Nothing pins `--library-src` to the commit `informalize.json` names, so the
export asks the item table's own question of it: every **key** declaration
must stand at the keyword line `declarations.json` gives, by the same
`fcitems.keyword_problem` that `cf informalize check-items` and the gate use,
and a source that disagrees is refused rather than quoted. A helper is exempt
there as it is there, since one may be a declaration a command such as
`@[simps]` generated; a helper whose snippet is cut at another line is the
`snippet.line_differs` bend, which the command prints as a warning. A Lean
file `--library-src` does not hold at all is a refusal naming that flag.

| input | becomes |
|---|---|
| `informalize.json`'s `library` | the document's `companion`: the **library** at its own commit, because the overlay makes every Lean end from `companion.repo` at `companion.commit`. The output repository's commit is the export's, which `cf overlay export` records as `export.commit` beside `export.lean` (`docs/overlay.md` section 6) |
| the PDF and `--pdf-url` | one `kind: pdf` source: its sha256, bytes, page count and page size, titled by `--source-title` (else its id, which is the informalize id) |
| each entry of `markers.json` | one item per part, and one per item without parts: `id_source: label`, so its id is `ids.item_label_id` (`theorem-1.2-a`), its `number` the printed number, its `sub` the letter, its `title` the item's (a part carries its item's, as the printed label repeats it) and its `section` the section the text prints it in. An item with parts is boxed around them, so its own box is not annotated. One location per box, a `PdfRegionSelector` of the fractions the markers give |
| the `\leanref`s the build recorded in a box | one link each, with the role and the relation `items.json` gives the declaration (`exactMatch` as `exact`, `broadMatch` as `generalization`, `narrowMatch` as `special_case`, `relatedMatch` as `related`), and the declaration from `declarations.json` |
| `declarations.json` and `--library-src` | one declaration record per linked declaration, its statement cut by `adapters.leansnippets` as a snippet, and a destination on the library's git host: `https://<host>/<org>/<lib>/blob/<commit>/<path>` with `resolution: exact` and the fragment `L<keyword line>` (site kind `other`: a blob page is none of the rendered Lean sites) |
| the items approval | one provenance record, `actor: agent`, `method: agent_annotation`, `tool.name` `cf informalize`, and `confirmed_by_human` true with `--items-approved`, which the maintainer passes when the items approval pins the exported `items.json` (`cf informalize overlay`, which will pass it for him, is not implemented yet) |

Lossy: `link.junk` (the junk values `items.json` lists have no field in the
format; the text states each in a sentence of its own), `link.relation` (a
relation no legacy token converts back to), `markers.leanrefs_outside_a_part`
(a `\leanref` in the text of an item with parts, outside every part, which no
box annotates), `item.title` (a title holding a URL, which the
`external-urls` lint refuses) and `source.page_size` (markers with no page
size in points).
Bent: `link.role_not_in_the_table` (a `\leanref` the item's table does not
list is linked as a helper, with no relation), `snippet.line_differs` and
`source.title` (no file of the output repository carries the text's title, so
without `--source-title` the source is titled by its id). A box that names no
declaration, and a key declaration no box names, are problems: the command
exits 1. A declaration the Lean file `declarations.json` names for it does not
hold keeps its link and its destination and gets no snippet, counted in
`counts.snippets_unlocated`, which `cf overlay export` prints as a warning;
so is `counts.snippets_line_differs`.
