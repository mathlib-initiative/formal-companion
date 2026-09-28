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
    [--lean-build <dir>] [--report <file>] [--name <name>] [--repo-id host/org/name]
```

The convention `adapters/attribution_table.py` reads: a PDF-overlay companion.
The exporter reads, at the pinned commit,

| input | becomes |
|---|---|
| `pdf-overlay/data/geometry-batch-?.json` | one `location` per region (`PdfRegionSelector`, plus an RFC 8118 `highlight=` fallback), its visual sign-off as `signoff`; one `item` per geometry label (numbered through the item grammar, an unnumbered label a passage titled by the producer); one `provenance` record per batch |
| `pdf-overlay/data/target-refinement.json` | `key` and `attributed` `links`, merged per item; the curation note's per-item classes as `coverage` |
| `source-formalization-ledger.json` | `clusters` (source nodes), `memberships` (edges), `artifacts` (module docs, checks, figures), the declarations the nodes name, `companion.graph` |
| `pdf-overlay/data/declaration-snippets.json` | `snippets` |
| `pdf-overlay/data/destinations.json` | `destinations` and `cluster.related_to` |
| `pdf-overlay/data/faithfulness-verdicts.json` | `verdicts`, with a `resolution` read from the summary prose and the commits it names |
| `pdf-overlay/official-source.json` | the PDF source's URLs, bytes and retrieval time |
| every tracked `.lean` file | every declaration (`adapters.leanparse`), the statement-hash ids and signatures of anonymous ones (`annotations.ids`), the section 5.1 cut for named ones |
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
document exits 1 after writing.

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
| the Lean files the adapter config globs (`--adapter-config`, default `templates/adapters/docstring_tags.tao-measure-lean.yaml`) | one `key` link per tagged declaration (the tag's parentheses as the link's `sub`, its `book p. N` as the link's `pages`, the tag verbatim as `raw_label`, the rest of the parenthetical as `note`); a `helper` link for `In-text proof of <item>`; every declaration, tagged or not, with its sorry class; `unattributed` rows for the untagged, with `(API)` and `(infrastructure` markers as the claim |
| `In-text` tags | passages: one per tag, keyed by a docstring quote verified once on its page (`quote_hash`) or by printed page and title (`page_title_hash`); a passage placed by a numbered neighbour takes its page from that neighbour's heading and names it in `relations.near` |
| `--pdf`, through `pdftotext` | the anchor edition's text layer: every numbered item's heading line becomes its location (a `TextQuoteSelector` under `page=`), docstring quotes are verified before a selector is written; `pdfinfo` gives the page box |
| `--xref` | `destinations` and, on the site source, the anchor locations of named declarations (`SitePageSelector` plus an RFC 3236 fragment) and of anonymous ones (a `CssSelector` on the keyword's `data-binding`) |
| `--site-pages` | the site source: one `scope: site` source pinned by the sha256 of its page manifest, with the manifest as `page_hashes`, its id, title, path and render time |
| `--site` | the rendered pages, checked against the manifest, then every anchor checked to occur once on its page; without it the index is trusted and the site provenance says so |

The site pin is a small JSON file: `{"source_id", "title", "path",
"rendered_at", "site_sha256", "pages": [{"path", "sha256", "bytes"}]}`;
`tests/data/m3/tml-site-pages.json` is the one for the reference companion.
The PDF is named through the registry as in section 1.

## 3. What is lossy

Each exporter's report lists, under `lossy`, the source data the format
cannot carry and, under `bent`, what it carries in a shape the format did not
intend (an `x_` key, a value parsed from prose). The buckets over the two
reference companions, with the counts of the shipped reports:

**attribution-table** (`tests/data/m3/measure_theory.export-report.json`), lossy:

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
| `verdict.replaced_by_unresolved` | 1 | a `<name> (<File>.lean:<line>)` mention that resolves to no declaration |
| `verdict.replaced_by_evidence_only` | 1 | a declaration the evidence names and the note does not |
| `source.path` | 1 | the ledger's workspace-relative PDF path |
| `source.printed_page_offset_from_prose` | 1 | the offset is read from locator prose and the registry, not a companion field |

and bent: the ledger's scope and snapshot tree (`x_graph_scope`, 1), the source
nodes' plan ids and statuses (`cluster.x_plan_status`, 77), the refinement's
method name and curation count (`provenance.x_refinement_fields`, 1), the PDF's
download checks (`source.x_download_checks`, 1), the ledger's own words for the
edition (`source.x_companion_edition`, 1), the 64 comment tags refused as
declared ids and kept as `legacy_ids` (`declaration.comment_tag_as_legacy_id`),
one geometry label naming two items (`item.split_merged_label`), the 33
coverage classes read from the curation note (`item.coverage_from_prose`), and
the verdicts' sub-parts and replacing declarations read from prose
(`verdict.sub_from_prose`, 2; `verdict.replaced_by_from_prose`, 4).

**docstring-tags** (`tests/data/m3/tao-measure-lean.export-report.json`): no
lossy bucket, one bent bucket (`source.x_note`, why the site is pinned by
content). Its counts record what could not be located rather than lost: 6 of
32 passage docstring quotes not found, ambiguous or found only on the next or
previous page, 14 of 21 link docstring quotes not verified, 1 tag the item
grammar refused (an `unattributed` signal), and 1 index anchor the page lacks
(a `fallback` destination). Without `--site` a further bucket,
`site.anchors_unverified`, counts the anchors that were not checked.

## 4. `cf review-tags`

```sh
cf review-tags <document.json[.gz]> [--json] [--limit N] [--keep-pattern RE] [--hide-pattern RE]
```

Read-only over a document, no checkout. Five checks, each a defect shape the
audits of the two companions met:

| check | what it reports | the known answer |
|---|---|---|
| `attribution_gap` | an item with a location or page anchor, no `key` link and no `coverage` class: keyed only to where it sits | measure_theory: 205 items (the fallback items of sections 1.1 to 1.6; before its PR #17 every section 1.7 item was one); tao-measure-lean: `remark-1.4.50` |
| `key_on_structure_member` | a `key` link, or a `primary` membership, on a `field` or `projection` | measure_theory: 10 key links over 3 items and the 142 primary memberships its PR #1 counted |
| `borrowed_key` | a numbered item whose every key declaration is another item's key (`via: links`), or, with no key of its own, whose cluster fallback shows only other items' keys (`via: clusters`) | measure_theory: 19 and 127, among them Exercise 1.1.2, whose entry its PR #19 records as stating Lemma 1.1.2(ii) |
| `hidden_spelling` | a docstring-tag link whose raw label matches the fold script's proof-plumbing rule (`--hide-pattern`, default `\bIn-text proof`) or carries none of the book anchors it keeps (`--keep-pattern`) | tao-measure-lean: the 5 `In-text proof of Lemma 1.1.2` helper links (14 book-content tags spelled that way were respelled in its PR #12) |
| `adjudicated_attribution_gap` | the document's own verdicts of class `attribution_gap` | measure_theory: Exercises 1.2.21 and 1.3.4, both fixed |

`tests/test_review_tags.py` plants each defect in a small document and lists
what the two reference documents report today.

## 5. Reproducing the references

The references in `tests/data/m3/` are the exporters' own output. With the
pinned checkouts on disk (`measure_theory` at `00a2438`, `tao-measure-lean` at
`f158509`), the published edition's PDF and the rendered site whose pages the
manifest pins:

```sh
M3=tests/data/m3                     # the pinned site index and page manifest
cf export attribution-table <measure_theory checkout> --commit 00a2438 \
    --out measure_theory.annotations.json --lean-build <its .lake/build/lib/lean>
cf export docstring-tags <tao-measure-lean checkout> --commit f158509 \
    --pdf <the published PDF> --xref "$M3/tml-site-xref.json.gz" \
    --site-pages "$M3/tml-site-pages.json" --site <its _site> \
    --out tao-measure-lean.annotations.json
gzip -n -9 measure_theory.annotations.json tao-measure-lean.annotations.json
```

`python3 -m unittest tests.test_export` does the same comparison record for
record when the environment names the inputs: `CF_MT` and `CF_TML` (the
checkouts), `CF_TAO_PDF` (the PDF), and optionally `CF_TML_SITE` (default
`<CF_TML>/_site`) and `CF_MT_BUILD` (default `<CF_MT>/.lake/build/lib/lean`;
without the one `.ilean` that names anonymous instances, three declarations
lack `lean_name` and the test allows exactly that). The `pdftotext` version is
the one environment-dependent field and is normalised before the comparison.
Without those variables the reproduction tests skip and the synthetic
companions still run everywhere.

## 6. Deliberate differences from the prototype documents

Section 10 of `docs/annotations.md` was written from two prototype documents.
The shipped references differ from them in three ways, all deliberate and
recorded in `tests/test_export.py` (`DELIBERATE`):

1. `companion.generator` names the command and the skill's version.
2. The PDF source is named through the registry: one id
   (`tao-measure-gsm126-published`), title and edition for both companions,
   where the prototypes wrote `tao-measure-pdf` and `tao-gsm126-published` for
   the same bytes. The ledger's own words for the edition stay on the
   measure_theory source as `x_companion_edition`.
3. The site source's title and `retrieved_at` come from the page manifest,
   not from the build's file times.

Every other record is byte for byte what the prototypes wrote.
