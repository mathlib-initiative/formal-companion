# The companion exporters: `cf export` and `cf review-tags`

Two commands turn a companion's own attribution data into an annotation
document (`docs/annotations.md`, format 1.0), and one reads such a document
back for the attribution defects the audits found. They are milestone M3 of
`docs/proposals/pdf-to-companion.md` (section 5.4): the exporters reproduce
the two documents that section 10 of the format was written from, record for
record, and the tag review reproduces the known answers.

Neither command needs a workspace. Both read a checkout at a pinned commit
through `cflib.gitsafe` and write nothing under it.

**Contents**: 1 attribution-table · 2 docstring-tags · 3 what is lossy · 4 the
tag review · 5 reproducing the references · 6 deliberate differences

## 1. `cf export attribution-table`

```sh
cf export attribution-table <checkout> --commit <sha> --out <doc.json> \
    [--lean-build <dir>] [--report <file>] [--name <name>] [--repo-id host/org/name] \
    [--site-name <site kind>] [--source-id <id>] [--source-title <title>] \
    [--source-edition <edition>] [--page-offset <n>]
```

Both subcommands take the same common flags. `--site-name` is the
destination site kind, written as every destination's `site` (and, by
attribution-table, as the prefix of its id; default `verso_literate`). `--out` and `--report` may not lie inside the
checkout: the command refuses them rather than write there.

The convention `adapters/attribution_table.py` reads: a PDF-overlay companion.
The exporter reads, at the pinned commit,

| input | becomes |
|---|---|
| `pdf-overlay/data/geometry-batch-?.json` | one `location` per region (`PdfRegionSelector`, plus an RFC 8118 `highlight=` fallback), its visual sign-off as `signoff`; one `item` per geometry label (numbered through the item grammar, an unnumbered label a passage titled by the producer; the words after an `Equation (n):` label's colon are the equation's `title` with `title_source: producer`, since a book prints an equation by its number alone); one `provenance` record per batch |
| `pdf-overlay/data/target-refinement.json` | `key` and `attributed` `links`, merged per item; the curation note's per-item classes as `coverage` |
| `source-formalization-ledger.json` | `clusters` (source nodes), `memberships` (edges), `artifacts` (module docs, checks, figures), the declarations the nodes name, `companion.graph` |
| `pdf-overlay/data/declaration-snippets.json` | `snippets` |
| `pdf-overlay/data/destinations.json` | `destinations` and `cluster.related_to` |
| `pdf-overlay/data/faithfulness-verdicts.json` | `verdicts`, with a `resolution` read from the summary prose and the commits it names |
| `pdf-overlay/official-source.json` | the PDF source's URLs, bytes and retrieval time |
| every tracked `.lean` file | every declaration (`adapters.leanparse`), the statement-hash ids and signatures of anonymous ones (`formal_companion_annotations.ids`), the section 5.1 cut for named ones |
| a Lake build's `lib/lean` (`--lean-build`, default `<checkout>/.lake/build/lib/lean`) | an anonymous instance's generated name as `lean_name`, when the `.ilean` postdates the file's last commit |

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
document exits 1 after writing. A formalization id the refinement, snippets,
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
item is never narrowed to its first part. A parenthetical attached to the
number that the grammar refuses (`1.2.3(ii')`) is dropped and reported as a
problem. An item whose only links are ledger support edges carries the class
they imply as `coverage` (`example_only` when every one is an example,
`support_only` otherwise; section I5).

## 2. `cf export docstring-tags`

```sh
cf export docstring-tags <checkout> --commit <sha> --pdf <the anchor edition> \
    --xref <xref.json[.gz]> --site-pages <pages.json> [--site <rendered site>] \
    [--adapter-config <adapter.yaml>] --out <doc.json>
```

The convention `adapters/docstring_tags.py` reads: every declaration that
formalises a book item opens its docstring with the item's tag. The exporter
reads

| input | becomes |
|---|---|
| the Lean files the adapter config globs (`--adapter-config`, default `templates/adapters/docstring_tags.tao-measure-lean.yaml`) | one `key` link per tagged declaration (the tag's parentheses as the link's `sub`, through the shared renderer of section 1, its `book p. N` as the link's `pages`, the tag verbatim as `raw_label`, the rest of the parenthetical as `note`); a `helper` link for `In-text proof of <item>`; every declaration, tagged or not, with its sorry class; `unattributed` rows for the untagged, with `(API)` and `(infrastructure` markers as the claim. Every re-read of a tag reads it the way the adapter config does (`tag.require_backticks`, `tag.pattern`, `tag.position` and the keywords) |
| `In-text` tags | passages: one per tag, keyed by a docstring quote verified once on its page, or on any page of a `pp.` range (`quote_hash`), or by printed page and title (`page_title_hash`); a numbered item the tag mentions is named in `relations.near`, and a passage with no page of its own takes its page from that neighbour's heading; the tag's own single word (`definition`, `convention`) is the passage's `kind`, anything longer leaves it `passage` |
| `--pdf`, through `pdftotext` | the anchor edition's text layer: every numbered item's heading line becomes its location (a `TextQuoteSelector` under `page=`), docstring quotes are verified before a selector is written; `pdfinfo` gives the page box |
| `--xref` | `destinations` and, on the site source, the anchor locations of named declarations (`SitePageSelector` plus an RFC 3236 fragment) and of anonymous ones (a `CssSelector` on the keyword's `data-binding`) |
| `--site-pages` | the site source: one `scope: site` source pinned by the sha256 of its page manifest, with the manifest as `page_hashes`, its id, title, path and render time |
| `--site` | the rendered pages, checked against the manifest, then every anchor checked to occur once on its page; without it the index is trusted and the site provenance says so |

A numbered item whose heading line the text layer does not show takes its
`pages` from its earliest key link that has them (counted in the report as
`headings.pages_from_key_link_items`). A tag's page outside the PDF, or a
reversed range, a sub-part the grammar refuses, or a URL in a note, a
passage title or a tag (I10) is dropped and reported as a problem naming the
declaration, so the document stays valid and the command still exits 1.

The site pin is a small JSON file: `{"source_id", "title", "path",
"rendered_at", "site_sha256", "pages": [{"path", "sha256", "bytes"}]}`.
`cf export site-pages <rendered site> --out <pages.json> [--source-id ID]
[--title T] [--path P] [--rendered-at TIME]` writes it: every `*.html` file
under the site (the same walk `--site` checks), the digest of section 3.1 of
`docs/annotations.md`, `path` defaulting to the site directory's name and
`rendered_at` to the newest page's modification time. It reads no checkout
and refuses an `--out` inside the site. The reference companion's is `tml-site-pages.json` in the maintainer's
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
| `declaration.private_line_stale` | 242 | the ledger's positional private ids name lines that moved; resolved by module and name, the old id in `legacy_ids` |
| `declaration.private_field_kind_guessed` | 6 | a private structure member the ledger gives no kind is exported as `field` |
| `membership.realization` | 1695 | the edges' `realization` (constant per role, so derivable) |
| `ledger.node_class_and_text_hash` | 2884 | the nodes' class and verification-text hash (recoverable from the pinned ledger) |
| `ledger.integrity_blocks` | 1 | the ledger's `files[]`, signatures, counts and authority (recoverable from the pinned ledger) |
| `item.coverage_unclassified` | 205 | the class of the fallback items of sections 1.1 to 1.6, which the curation note does not give item by item |
| `item.passage_label` | 20 | the verbatim `Unnumbered ...` geometry label, which became a producer title |
| `link.region_granularity` | 35 | the per-region keying of refinement entries, merged per item |
| `geometry.batch_order` | 4 | the in-batch order of items and the batch page partitions |
| `location.quads_highlight` | 1 | the RFC 8118 fallback of a quads region is its bounding box |
| `destinations.header` | 1 | the destinations file's lock blocks and relation id |
| `verdict.commit_roles` | 8 | which of a resolution's commits is the merge |
| `verdict.replaced_by_unresolved` | 1 | a `<name> (<File>.lean:<line>)` mention that resolves to no declaration (a repo path or a tail of one) |
| `verdict.replaced_by_evidence_only` | 1 | a declaration the evidence names and the note does not, counted over the fixed verdicts with a `replaced_by` (absent when there is none) |
| `source.path` | 1 | the ledger's workspace-relative PDF path |
| `source.printed_page_offset_from_prose` | 1 | the offset is read from locator prose and the registry, not a companion field |

Two lossy buckets appear only when their input does: `link.key_outside_formalization_ids`
(a refinement key id its own entry's `formalization_ids` does not list; links
follow `formalization_ids`) and, as a count rather than a bucket,
`ledger_support_edges.same_link_from_another_node` (one support edge reached
through two single-item nodes gives one link).

and bent: the ledger's scope and snapshot tree (`x_graph_scope`, 1), the source
nodes' plan ids and statuses (`cluster.x_plan_status`, 77), the refinement's
method name and curation count (`provenance.x_refinement_fields`, 1), the PDF's
download checks (`source.x_download_checks`, 1), the ledger's own words for the
edition (`source.x_companion_edition`, 1), the 64 comment tags refused as
declared ids and kept as `legacy_ids` (`declaration.comment_tag_as_legacy_id`),
one geometry label naming two items (`item.split_merged_label`), the 33
coverage classes read from the curation note (`item.coverage_from_prose`), and
the verdicts' sub-parts and replacing declarations read from prose
(`verdict.sub_from_prose`, 2; `verdict.replaced_by_from_prose`, 4). An item
linked only by support edges adds `item.coverage_from_support_edges` (none in
the reference).

**docstring-tags** (`tao-measure-lean.export-report.json`): no
lossy bucket and three bent buckets: `source.x_note` (1, why the site is
pinned by content), `location.x_page_basis` (16, how a passage's page was
placed) and `location.x_heading_candidates` (2, the pages of every heading
line of an item the text layer shows twice). The reference report predates the
last two buckets and lists those keys only under `counts.output.x_keys`; the
document itself is unchanged. Its counts record what could not be located rather than lost: 6 of
32 passage docstring quotes not found, ambiguous or found only on the next or
previous page, 14 of 21 link docstring quotes not verified, 1 tag the item
grammar refused (an `unattributed` signal), and 1 index anchor the page lacks
(a `fallback` destination). Without `--site` a further bucket,
`site.anchors_unverified`, counts the anchors that were not checked.

## 4. `cf review-tags`

```sh
cf review-tags <document.json[.gz]> [--json] [--limit N] [--keep-pattern RE] [--hide-pattern RE]
```

Read-only over a document, no checkout. Six checks, each a defect shape the
audits of the two companions met. An invalid `--keep-pattern` or
`--hide-pattern` exits 2 with one error line.

| check | what it reports | the known answer |
|---|---|---|
| `attribution_gap` | an item with no `key` link to a declaration (a key on a cluster does not count) and no `coverage` class that excludes one (`not_formalized`, `out_of_scope`, `support_only`, `example_only`): a `formalized` or `partial` item with no key is reported, since a claimed formalization no key names is the defect | measure_theory: 205 items (the fallback items of sections 1.1 to 1.6; before its PR #17 every section 1.7 item was one); tao-measure-lean: `remark-1.4.50` |
| `key_on_structure_member` | a `key` link, or a `primary` membership, on a `field` or `projection` | measure_theory: 10 key links over 3 items and the 142 primary memberships its PR #1 counted |
| `borrowed_key` | a numbered item whose every key declaration is another item's key, where that other item (the owner) also has a key this one lacks (`via: links`), or, with no key of its own, whose cluster fallback shows only other items' keys (`via: clusters`) | measure_theory: 4 and 127, among them Exercise 1.1.2, whose entry its PR #19 records as stating Lemma 1.1.2(ii) |
| `shared_key` | the same sharing with no owner: items keyed only to declarations they share with items that hold nothing more, each listed once and neither called a borrower | measure_theory: 15, mutual groups such as Definitions 1.4.34 and 1.4.35, or Equations (1.4) and (1.5) with Proposition 1.2.18 |
| `hidden_spelling` | a book-content docstring-tag link (`key` or `attributed`) whose raw label matches the fold script's proof-plumbing rule (`--hide-pattern`, default `\bIn-text proof`) or carries none of the book anchors it keeps (`--keep-pattern`); a `helper` link is proof plumbing the script is right to hide | tao-measure-lean: none. Its 14 book-content tags spelled `In-text proof (book p. N)` were respelled in its PR #12; the 5 links still spelled `In-text proof of Lemma 1.1.2` are helper links |
| `adjudicated_attribution_gap` | the document's own verdicts of class `attribution_gap` | measure_theory: Exercises 1.2.21 and 1.3.4, both fixed |

`tests/test_review_tags.py` plants each defect in a small document and lists
what the two reference documents report today (when `CF_M3_REFERENCE_DIR`
names them; otherwise that class skips).

## 5. Reproducing the references

The references are the exporters' own output over the two private companions,
so they are maintainer-side: a directory named by `CF_M3_REFERENCE_DIR`, whose
contents `tests/data/m3/README.md` lists. No file derived from a private
companion is committed to this repository. With the
pinned checkouts on disk (`measure_theory` at `00a2438`, `tao-measure-lean` at
`f158509`), the published edition's PDF and the rendered site whose pages the
manifest pins:

```sh
M3="$CF_M3_REFERENCE_DIR"            # the pinned site index and page manifest
cf export attribution-table <measure_theory checkout> --commit 00a2438 \
    --out measure_theory.annotations.json --lean-build <its .lake/build/lib/lean>
cf export docstring-tags <tao-measure-lean checkout> --commit f158509 \
    --pdf <the published PDF> --xref "$M3/tml-site-xref.json.gz" \
    --site-pages "$M3/tml-site-pages.json" --site <its _site> \
    --out tao-measure-lean.annotations.json
gzip -n -9 measure_theory.annotations.json tao-measure-lean.annotations.json
```

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

Section 10 of `docs/annotations.md` was written from two prototype documents.
The references differ from them in four ways, all deliberate and
recorded in `tests/test_export.py` (`DELIBERATE`):

1. `companion.generator` names the command and the skill's version.
2. The PDF source is named through the registry: one id
   (`tao-measure-gsm126-published`), title and edition for both companions,
   where the prototypes wrote `tao-measure-pdf` and `tao-gsm126-published` for
   the same bytes. The ledger's own words for the edition stay on the
   measure_theory source as `x_companion_edition`.
3. The site source's title and `retrieved_at` come from the page manifest,
   not from the build's file times.
4. An equation's title is marked `title_source: producer` (in measure_theory,
   `equation-1.2` and `equation-1.3`; the prototype left the field out, which
   reads as the book's words). The pre-merge review of the exporters found it
   (item 7), and its check of the published edition found both printed by
   number only.

Every other record is byte for byte what the prototypes wrote.
