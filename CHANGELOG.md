# Changelog

`cf --version` prints the number in `scripts/cflib/__init__.py`. This file
says what a change to it promises.

## What a version number means here

The thing that must not break is **a corpus**. `corpus/<run>/` is committed,
content-addressed and used as a calibration baseline, so the compatibility
promise is about the keys a corpus and a ledger are joined on, not about the
Python API.

- **Major** — a corpus or ledger written by an older version no longer joins.
  The two frozen definitions are `cflib.hashing.finding_id` (five parts,
  `\n`-joined, the item through `items.clean()` first) and
  `cflib.items.clean()` itself. Changing either re-keys every finding ever
  recorded, so it is a major version and a documented migration, never a
  bugfix. `finding_id` is defined twice — `cflib.hashing.finding_id` and the
  copy `gate/verify_findings.py` inlines because `cf run` transfers it to a
  host with no cflib; the three compat shims delegate.
  `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` pins all of it.
- **Minor** — a new command, a new stage artifact, a new optional row key, a
  new adapter kind, a new registry field. A reader of an older artifact keeps
  working; a reader of a *newer* artifact may need the newer version.
- **Patch** — a fix that changes no artifact shape.

Two things that are deliberately *not* covered: the pinned regression figures
(they are measurements of one corpus, and a change to them is a finding, not a
release), and `docs/design/archive/`, which is history and does not move.

## Unreleased

Nothing is released yet: there is no tag, and the version has been `0.2.0`
since the multi-source rewrite. The entries below exist so that the first
release has something to say.

### Changed
- The overlay writes and reads Literature Links 2.0.0, unreleased. A
  record's `subject` ends are the item's regions of the PDF and its `object`
  end is the declaration in the repository at the export commit, so an item
  drawn in several regions is one record per link; its `id` is the item's.
  A link's relation token becomes `relation` (`part` is `hasPart`), and an
  item that links nothing with coverage `not_formalized` is a `noMatch` over
  the repository at that commit. Folder defaults carry end templates. The
  viewer takes regions from the PDF ends and cards from the Lean ends, and
  derives a card's page from the module and declaration under the site root;
  `serve.py` reads the PDF's `sha256` and URLs from its end. Companions
  re-export and re-sync; the viewer shows the same regions, items and cards.
- `status` holds only Literature Links' recommended values: `support_only`
  is `not_formalized`, and `example_only`, `private_only` and `out_of_scope`
  give no status. Such a class stays in the record extension as `coverage`,
  which the viewer reads first. A card whose location names no declaration
  (an end from elsewhere) is named by its end's `title`.
- `scripts/annotations/sync_format.py --branch` vendors a commit of a version
  not yet released; the manifest records the branch and `"released": false`,
  and `GENERATED.json`'s `format` names the branch.
- The attribution-table convention no longer has a `declaration-snippets.json`.
  `cf export attribution-table` and the `attribution_table` adapter cut each
  snippet from the Lean file the ledger names (`adapters.leansnippets`, a port
  of `extract_declaration_snippets.py` at measure_theory ef0a424; same text,
  lines and `…` truncation), so the file is not an input in `GENERATED.json`
  and the adapter's `declaration_snippets` setting is gone. A leftover file is
  ignored and named (`ignored_inputs` in the export report, `ignored` in the
  dry run); `cf export` and `cf overlay export` warn of it and of any id they
  cannot locate (`snippets_unlocated`).
  The adapter now needs the ledger, and its key hashes the Lean it cuts from
  (`snippet_files_sha` for `snippets_sha`).
- A definition's snippet (`def`, `abbrev`, `instance`, `structure`, `class`,
  `inductive`, `opaque`) is the whole declaration, up to the next command and
  at most 60 lines; a theorem's is still its statement through the `:=` of
  its proof, now past a `:=` in brackets, a comment, a string, a `let` or a
  `have`. A field's snippet keeps the lines that continue its type. A
  declaration with an attribute or an instance priority on its line is
  located, one inside a comment is not, and a `mutual` block's `end` keeps
  the namespace. The viewer clamps a statement only past 20 lines, to 14.
- The vendored annotation format is the release candidate
  `formal-companion-annotations v1.0.0rc2` (identifier
  `formal-companion/annotations`; the withdrawn `v1.0.0` is not compatible).
  `scripts/annotations/sync_format.py` accepts `vX.Y.ZrcN` tags. The
  producers write the rc: root `profiles` declared for exactly the profile
  keys a document uses (`annotations.export.common.declare_profiles`), no
  `cluster_fallback` (a reader derives it: `clusters` and no links),
  `coverage` classes `partial_only` and `private_only` with `coverage_note`,
  the `part` relation, site sources whose `sha256` is the digest of the pages
  `page_hashes` pins (generated fragments may be left out), the ledger's own
  verdict class under `x_ledger_verdict`, named declarations cut with
  `ids.signature_at`, and the checker's `Problem` records (`path: message
  [code]`) in every message. The `external-urls` lint, formerly invariant
  I10, is run by the exporters' self-check and the pipeline's checkpoint
  writer, so the documents stay as the viewer's deployment needs them.
- `cf export attribution-table` reads the refinement table's version 2
  *coverage entries* (`coverage`, the declarations the class admits, `note`)
  and refuses a version 1 table; the prose-note classifier is gone. At
  measure_theory `2cd263a` the export succeeds again (it failed on an
  `attributed`-only item) and the document validates without a warning.
- `cf export docstring-tags` runs without a PDF (a `pdf` source pinned by
  its identifiers from `--source-doi` and `--source-isbn`, or an `other`
  source by `--source-media-type` and `--source-revision`, named by
  `--source-id`, `--source-title` and `--source-revision`; numbered items
  located by their numbers alone, bare tags' parentheticals as producer
  titles) and without a site (`--xref` and `--site-pages` optional,
  together); with `--site`, pages on disk the manifest leaves out are
  counted, not refused.
- Both exporters write what the release candidate's last changes admit: a
  primed sub-part keeps its prime (`(b')` is `sub: b'`, item id
  `proposition-6.2.5-b'`; `cflib.items` accepts the prime as a sub-part
  group), a declaration tagged with two numbered items gets two `key` links
  and the items `relations.same_as`, an item keyed to an anonymous `example`
  carries `coverage: formalized`, and `profiles` no longer names `audit` for
  a source's `role` (core now): a docstring-tags document with no verdict
  declares `site` alone, or nothing.
- `cf review-tags` derives the cluster fallback, treats `partial_only` with
  attributed links as the curation's answer, and reads `x_ledger_verdict`.
- `tests/test_vendored_format.py` checks that every value the skill writes
  into a document is in the format's set, instead of equality of nine sets
  the format no longer exports. The reference pins move to measure_theory
  `2cd263a` and tao-measure-lean `677ada1`; references written against
  `v1.0.0` must be regenerated.

### Added
- `cf campaign plan | render | status` (proposal §6): an ordered list of
  already-planned locate runs as shards (each at most 500 tasks, each with a
  spend cap in its own `run.yaml`, all for one allowlisted host) and the
  watchdog's thresholds in `campaigns/<id>/campaign.yaml`; a rendered host-side
  sequencer (shell, no cflib) that starts shard k+1 when shard k ends, runs the
  watchdog beside each shard, exports usage and prunes that shard's
  transcripts, and stops when a watchdog drains; and a status table read from
  the shards' receipts. `scripts/run/watchdog.py` is the model-free, stdlib
  watchdog: spend from the kept envelopes, the gate-failure rate from the gate
  log and the cost per merged task from the entrypoint's merge target, drained
  with `tend-control drain` at the shard's cap or a multiple of the pilot's p90.
- `cf run` for a locate run: `--execute` needs a spend cap recorded in its
  `run.yaml` or given as `--max-cost` (a workspace default does not count and
  `--no-budget-cap` is refused); the transfer sends this run's rendered sources
  (with `--include-source-text`), `corpus.sqlite`, the L2 candidates and the
  frozen inventory to the host paths its `gate.json` names; the verify stage
  checks `corpus.sqlite`'s sha256 once and that the locate gate is out of tree,
  and the tend config is checked for `verify_locate.py`.
- Recursive harvest (proposal §3, §6): the listing (`find -print0` on a host),
  the transfer and the checksums descend into subdirectories, a receipt names a
  file by its path relative to the corpus, and `iter_finding_files` walks the
  tree, so a locate run's `<task-id>/<source-id>.json` documents land, are
  reconciled and are counted; a flat faithfulness corpus lists, copies and
  receipts exactly as before. `cf harvest` gates a locate run with
  `verify_locate.py` and its `gate.local.json`, audits its documents against
  the run's own source pins, adds a receipt `locate` block (documents by mode,
  UNRESOLVED documents, attempt-capped documents and unresolved passages
  counted on their own) and writes no faithfulness tally, pin or companion
  state for it.
- Bridge hardening (proposal §6). `scripts/run/agent_call.py` runs every
  agent call of `bridge-agentbox.sh` and `bridge-claude-p.sh` inside one of 15
  per-user `flock` session slots (`CF_SLOT_DIR`, `CF_SLOTS`), waiting for a
  free one rather than failing, under `CF_AGENT_TIMEOUT` (45 minutes) in its own
  process group; a timeout exits 124 and an empty or 409 envelope 75, with no
  contribution printed, so tend retries the worktree. Claude's JSON envelope is
  kept under `<tend-root>/usage/` instead of being deleted.
  `scripts/run/attempt_cap.py` counts genuine reviewer rejections per task under
  `<tend-root>/attempts/`; at `CF_ATTEMPT_CAP` the worker bridge writes and
  commits the task's schema-valid `UNRESOLVED` documents (`unresolved.by:
  attempt_cap`) without an agent call and the reviewer bridge approves exactly
  those mechanically. `CF_EXTRA_TOOLS` reaches the worker's allowlist,
  `CF_FINDINGS_GLOB` may name the task, and `cf run` ships any bridge helper a
  staged `bin/` lacks.
- The locate gate, `scripts/gate/verify_locate.py` (proposal §4): rules 1-8 out
  of tree over only the task's own documents — the change set's
  `findings/<task-id>/<source-id>.json`, or `--findings` — loading only the
  sources, L2 candidates files and task manifests they name. Schema per mode
  with the header pins and a strict JSON parse; every implied record through the
  vendored Literature Links checker, both vendored packages verified against
  their manifests and `gate.json`'s pins at start; quotes exactly once on the
  pinned layer at the passage start, ids recomputed over the whole inventory;
  every named declaration in `corpus.sqlite`'s exact table at the universe's
  commit, `proof_status`/`commit`/`version` refused; L2 curated hits read by the
  gate itself (off in the blind arm); three distinct queries per absence; at
  most five ranked locations; ownership by task id, by `orchestrator.sqlite`
  (an assumed schema, looked up defensively) or by the single-stem rule, named
  on the last line; the finder and coverage floor of inventories. Exit 0/1/3 as
  `verify_findings.py`, one JSON line per call in the gate log.
  `references/locate-documents.md` §2-§4 and §9 state each rule precisely.
- The locate contract the wave-2 packages are built against
  (`references/locate-documents.md` §7–§11): the sourceset manifest and a
  rendered web source's files (the Literature Links layer, `formulas.json`, the
  head text, the reading copy and its layer map, sections, paragraphs, the
  deterministic finders), the universe and library-index shapes and the
  `corpus.sqlite` schema, a locate run's task manifest, `gate.json` and
  `run.yaml` blocks, the harvested finding rows and passage outcomes, and
  campaigns. `cflib.vocab` gains the values those documents carry
  (`references/vocabulary.md` §1.11), `cflib.hashing.location_key` the run-free
  key of a location, `cflib.paths` the sourceset, universe, export, bench and
  campaign paths, and `cf init`'s `.gitignore` ignores library indexes and
  `corpus.sqlite`. `scripts/gate/locate_text.py` is the standard-library head
  text, offset map, head id and exact-once quote match every reader of a web
  source shares, the gate on a host included; its head id is
  `cflib.passages`' frozen rule restated in lockstep, which a test pins.
- The foundation of locate (`docs/proposals/wikipedia-locate.md`, milestone
  L-M0, vocabulary and keys only; every stage that uses them is not
  implemented). `cflib.vocab` gains the three locate run modes
  (`locate-inventory`, `locate`, `locate-challenge`) with their kinds
  (`PASSAGE`, `LOCATION`, `ABSENCE`, `UNRESOLVED`), the relations,
  `noMatch`, passage kinds, prominence, scopes, proof statuses with
  `decide_proof_status` in their decision order, the derived statuses with
  `derive_status` implementing the proposal's §2.6 table, challenge
  verdicts, licence classes and the extension namespace
  (`references/vocabulary.md` §1.11). `FINDING_KINDS` is unchanged: a locate
  kind is not a faithfulness kind, so the ledger, the answer key and the
  substantive kinds read exactly what they read before.
- One new item production, `In-text #<hex>` (8 to 16 lowercase hex digits):
  a passage keyed by the hash of its head, parsed whitespace-tolerantly, with
  the reject `bad_passage_id` for a malformed hash, and conversions to and
  from the hex (`passage_item`, `passage_hex`) and the vendored
  `passage-<8 hex>` id. `clean()` and `finding_id` are untouched, and the
  production is tried after the printed page and the cross-reference, so the
  only labels whose parse or join key changed are ones that were rejects
  before (`In-text #…`, then `in_text_no_target`): `In-text #3, book p. 71` is
  still the p.71 item. `cflib.passages` computes the hex: the vendored
  `passage_quote_id` over a renderer-independent head (formulas as
  `⟦normalized alttext⟧`, twelve words extended to forty until unique in the
  source, each passage starting at a word), refusing two passages that still
  collide, a duplicate offset and a start inside a word with
  `PassageIdError`. The head rule is frozen and stated in full in
  `references/vocabulary.md` §4.
- Licence classes (`closed` by default, `open_attribution`,
  `open_share_alike`, `public_domain`): an optional registry `licence_class`
  that every existing entry passes without, `POLICY.md` §4.1, hard rule 6.
  The registry gains `families:`, web-source families with no per-revision
  rows; it ships one, `enwiki` (`open_share_alike`, an attribution template
  and a permalink template), validated by `cf sources validate-registry`. A
  workspace overlay may add a family; over a confirmed shipped one it keeps
  `confirmed` and the curated `licence_class` and `licence`, and the shadow
  is reported by `Registry.validate()` and `cf status`.
  `CLAUDE.md` §3 names the Wikipedia fetcher, not implemented, as the next
  network exception.
- Verso hovers in the overlay viewer. `build_site.py` cuts each card's
  statement out of the built site's Verso pages (`tools/verso_extract.py`),
  a tagged example's by its tag comment on its module's page, and writes
  `<mount>/verso.json`: the snippet as runs with Verso's token kinds,
  bindings and links to a declaration's own anchor on the site, and the
  hover entries they use. The viewer draws a key card's matching statement
  from its runs, with its own hover card built with `textContent` (pointer,
  keyboard focus and tap; the pointer may move onto it; PageDown and PageUp
  scroll it; Escape or a click elsewhere closes it); the CSP is unchanged.
  A site with no Verso page, or a card with no matching statement, keeps the
  plain snippet.
- A companion whose site has no Verso pages can commit `pdf-overlay/verso.json`,
  written by `tools/verso_extract.py --pages` from a Verso build of its project
  made elsewhere: cards found by module and declaration (`xref.json`, else
  Verso's slug), no links, and the build's commit and Verso revision recorded.
  `build_site.py` publishes it for such a site, dropping each statement that no
  longer joins to its card's snippet, and refuses it when no card pins its
  build's commit; the receipt says where `verso.json` came from. `sync` leaves
  the file alone; `check_generated.py` checks its form and commit.
- `display.minimal` in `overlay.config.json` (default `false`): the viewer
  keeps the passage, its links and their statements, and leaves out what
  describes them: the status chip (still read by a screen reader, and back in
  sight when it has keyboard focus or the PDF is not verified), the keys line,
  the key and related counts and the name filter, a key card's kind and
  badges, a related row's role and kind words, the review footer, the ledger
  cluster and the "Formalized" word. Qualifying words, where a link goes
  ("module page", "no page"), the notice, the pagers and the faithfulness
  verdicts with their "unreviewed" caveat stay.
  `config.py`, `check_generated.py`, `serve.py`, `build_site.py` and the
  schema accept it, and `tests/test_overlay_config.py` now holds
  `build_site.py` to the table too.
- Each toolbar button names its shortcut in a tooltip and in
  `aria-keyshortcuts`, and a test presses every advertised key and clicks its
  button and requires the same result.
- `cf overlay init|sync|check|export`: a companion's
  `pdf-overlay/` is generated from `templates/overlay/runtime/` rather than
  written by hand for one book, and reads Literature Links (vendored as
  `vendor/literature_links/`, one annotation per `.json` file). `export` converts an
  exporter's document into one `annotations/<section>/[<item>/]<name>.json`
  file per annotation, one folder per book section and per item with several,
  shared members in `_defaults.json` (`overlay.shards`, `overlay.defaults`; the extension key
  `org.mathlib-initiative.formal-companion.overlay` carries what the core
  fields cannot) and records the curation inputs it read; `sync` copies the
  viewer, the tools, PDF.js and the Literature Links package byte for byte,
  writes `annotations.bundle.json` (every record, defaults applied, for the viewer's one fetch)
  and records them, the files' manifest sha256 and the bundle's in
  `GENERATED.json`; a companion's CI runs
  `python3 pdf-overlay/tools/check_generated.py`, which validates every file,
  names any the bundle does not hold, and fails on a stale export. `docs/overlay.md` is the contract. This is the
  **one exception** to "never write to a companion repo" (`CLAUDE.md`
  section 4, `CONTRIBUTING.md` invariant 3, `SKILL.md` rule 10): `init`,
  `sync` and `export` write under `pdf-overlay/` and `annotations/` only.
- `scripts/annotations/sync_format.py --package literature_links`, and a full
  commit id in place of a tag for a release not tagged yet.
- `cf export attribution-table` lists the curation files it read in its
  report (`counts.inputs`).
- `adapters.leanbuild`: declarations come from the companion's Lake build where it
  verifies, else from the text; `lean_source` counts which; `CF_LEAN_SOURCE=text` forces text.
- `cf export site-pages <site> --out <pages.json>`: writes the page manifest
  that `cf export docstring-tags --site-pages` reads; `--exclude GLOB` leaves
  out generated pages (`'*/_lazy/*'`), which `--site` then counts.
- `cf ingest` and `cf inventory`, stages S0 and S1 of the PDF-to-companion
  pipeline (milestone M1, `docs/pipeline.md`). S0 pins one PDF (sha256, pages,
  page sizes, `pdftotext` version), prepares it with the onboarding code,
  refuses an offset below 0.9 without `--offset`, refuses an edition the
  registry marks `draft` or `superseded` without `--override-edition`, fetches
  the errata raw, creates the generated repository with one commit and writes
  `pipeline.yaml`, `doc.v0.json` and its receipt. S1 adds the numbered items
  in scope with page spans and exact heading quotes, erratum items linked by
  `relations.same_as`, passages from `--passages` and a labelled heuristic
  finder, `relations.near`, and writes `doc.v1.json` and its receipt only when
  the gates pass: slot completeness, slot contiguity per section and counter
  (with `slot-reasons.yaml`), the exact selector check (verbatim on its own
  page, NFKC and whitespace collapse), unique ids and a 12-word cap on every
  `exact`. The two spot-check and hunter agents are deferred.
- `edition_status` (`current`, `draft`, `superseded`) on registry entries; the
  2011 Tao draft is `draft`.
- `cflib.inference.infer_counters` (the counters a numbering runs, read off
  its numbers) and `slot_filling_kinds` (a heading word too rare for
  `infer_kinds` is a kind when every number it carries fills a slot the
  numbering leaves open).
- `tests/e2e/make_pdf.py --passages`: the booklet with six planted unnumbered
  passages, five marked and one not; the default booklet is unchanged.
- The S0 and S1 review fixes: a failing S1 re-run removes the stale
  `doc.v1.json` and S1 receipt; a `--source-id` that names a registry entry
  pinned to another sha is refused, and an unknown sha adds a pending
  `edition` gate to the S0 receipt and prints a warning; the pages that
  dissent from the offset reach the receipt; an in-scope span stops before
  the next out-of-scope chapter or section opening; an ambiguous heading is
  placed by its shape or reported; a supplied passage is checked whole before
  it is capped, and a duplicate one is listed.
- `scripts/pipeline/derive_truth.py` and `tests/data/m1/`: the M1 calibration's
  ground truth as two derived tables, `{kind, number, page}` and `{page, near}`
  per Tao companion, regenerated by the maintainer from documents that stay
  outside the tree (the tables themselves are committed separately). Passage
  recall is now one to one, with a page-plus-quote overlap test where the
  reference has a quote; the calibration downloads the PDF only with
  `CF_M1_DOWNLOAD=1`.
- `cf init` ignores `pipelines/*/repo/`, the generated repository.
- `cf cost <path>` and `cf harvest --transcripts <path>` (milestone M0a of the
  pipeline proposal, after issue #11): a run's cost is read from the agents'
  own logs, since the orchestrator sees no token. `scripts/cost/usage.py`
  reads Claude Code transcripts (one response counted once, however many
  lines it spans), Codex rollout logs (charged by the advance of the
  cumulative token block, so a count reported twice is counted once, with the
  model id taken from the nearest `turn_context`) and usage exports, and sums
  tokens per model id; `scripts/cost/pricing.py` prices them with the table
  at `workspace.yaml` `budget.prices` (per model id: input, output,
  cache_write, cache_read in US dollars per million tokens, and a source
  note). A model the table does not list is reported as unpriced and never
  priced at a guess. The shipped table carries the Opus 5 list prices issue
  #11 records and nothing else. Harvest writes the priced totals into the
  receipt's new `cost` block (tokens by model, usd, priced and unpriced
  models, price_table_sha256, transcript_count, message_count) and records
  the run's dollars in `state.json`; `cf tally` prints cost and cost per task
  per run and records them in `reports/tally.<run>.json`, and `cf report`'s
  runs table gained two columns. A run harvested without transcripts is unchanged
  except for `cost: null`. `cf cost --export` writes one `{model, usage}` row
  per response, which is how usage leaves a host without the transcripts'
  text. `tests/data/m0a/` holds the fixtures: the section 1.7 re-run's 5,207
  exported rows are transcript lines, one per content block, so they sum to
  the line-summed recorded total of $358.10; collapsing identical adjacent
  rows gives $168.91.
- `scripts/pipeline/`: the loader and validator of `pipelines/<id>/pipeline.yaml`
  (exercise policy, scope, consent, the fleet run ids per stage; every unknown
  key refused) and the writer of `pipelines/<id>/stages/<stage>.stage-receipt.json`
  with the eleven fields of proposal section 4.1, which fails closed on a
  missing or extra field, a sha of the wrong length, a stage outside S0 to S9
  or a fleet whose receipt carries no cost block. `cflib.paths` gained the
  pipeline paths.
- `registry/calibration.yaml`: calibration facts the milestones replay against,
  starting with the tao-measure-lean revision before the camera-ready fix
  stack (`d8cdfd5`, the first parent of the stack's first merge) and how it
  was found.
- `cf export attribution-table` and `cf export docstring-tags`
  (`scripts/annotations/export/`, `docs/exporters.md`): the two prototype
  exporters `docs/annotations.md` section 10 was written from, as repository
  commands. Each reads a companion checkout at a pinned commit (through
  `cflib.gitsafe`, writing nothing under it) plus the outside inputs its
  convention needs (the anchor edition's PDF, a literate site's `xref.json`
  and page manifest), and writes a canonical annotation document beside a
  report of the lossy and bent buckets. The documents reproduce the
  maintainer's reference documents record for record (`tests/test_export.py`:
  against the pinned checkouts when `CF_MT`, `CF_TML`, `CF_TAO_PDF` and
  `CF_M3_REFERENCE_DIR` name them, on synthetic companions everywhere). The
  references are derived from the private companions and are not in this
  tree: `tests/data/m3/README.md` says what `CF_M3_REFERENCE_DIR` holds and
  how to regenerate it. Three
  deliberate differences from the prototype documents: `companion.generator`
  names the command; the PDF source is named through the registry (one id per
  edition, `tao-measure-gsm126-published`, where the prototypes wrote two ids
  for one edition); the site's title and `retrieved_at` come from the page
  manifest, not the build's file times. `annotations.ids` gained
  `comment_tags` on `AnonymousDecl` and a public `token_at`;
  `adapters.docstring_tags` gained `tagged_rows`, `classify_sorry` and a
  public `tag_of` (the exporter re-reads every tag the way the adapter config
  reads it); `cflib.items` gained a public `parse_subs`. Both exporters render
  a sub-part with one renderer (`annotations.export.common.render_sub`:
  ranges and lists whole, a refused one dropped and reported), refuse inputs
  the convention does not describe with an `ExportError` naming the file (a
  formalization id the ledger lacks, a front-matter passage, a malformed
  quad) and `cf export` turns any other `KeyError`, `ValueError`, `IndexError`
  or failed tool into one error line; an `--out` or `--report` inside the
  checkout is refused. The pre-merge review's fixes (the record of pull
  request 15) changed neither reference document.
- `cf review-tags <document>`: the tag-review half of M3, read-only over a
  document: items keyed only to where they sit (the section 1.7 attribution
  gap before measure_theory's PR #17), key links and primary memberships on a
  structure field or projection (the 142 edges its PR #1 counted), a numbered
  item whose keys are another item's that also states more (the borrowed keys
  its PR #19 recorded), items keyed only to declarations they share with no
  owner (`shared_key`, reported apart so neither side is called a borrower),
  book-content (`key`, `attributed`) tag spellings the literate site's fold
  script hides (`In-text proof`, respelled in tao-measure-lean's PR #12;
  `helper` links are proof plumbing and not reported), and the document's own
  `attribution_gap` verdicts. An item is keyed only by a key link to a
  declaration, and only `not_formalized`, `out_of_scope`, `support_only` and
  `example_only` excuse a missing key. `tests/test_review_tags.py` plants each
  defect and lists what the two reference documents report today.
- Annotation documents, schema v1
  (`formal-companion-annotations/schema/annotations-v1.schema.json`,
  `schema_version` "1.0"; spec `docs/annotations.md`): one JSON document per
  companion linking source items (numbered items, and unnumbered passages with
  mechanical quote- or page-and-title-hash ids) to Lean declarations, with W3C
  Web Annotation targets for locations (PDF regions and page ranges, web
  passages, pages of an unhosted rendered site, LaTeX, Lean and Markdown line
  ranges, PDF text offsets, media time ranges). `conformsTo` is open (known
  IRIs get grammar checks, others a warning). Clusters carry their members
  (`memberships`, including `artifacts` that are not declarations), links carry
  a `relation` and a `note`, and `example` / anonymous `instance` get
  statement-hash ids from one grammar in `cflib.vocab`, with a `~k` ordinal
  refused unless the changelog records which file took it. A rendered site is
  one source pinned by the sha256 of its page manifest. The canonical form
  follows RFC 8785 for order (UTF-16 code units, for keys and arrays alike) and
  numbers.
  `formal-companion-annotations/src/formal_companion_annotations/validate.py`
  checks the schema, selected by major version through an in-repo evaluator
  because the skill is stdlib only, plus the invariants a schema cannot state,
  and `--print-canonical` writes the canonical form. The adapters, the viewer
  and WikiLean do not read or write the format yet. The ids used the planned
  name lean-companion; the release candidate renamed the format to
  `formal-companion/annotations` (see Changed above).
- `docs/proposals/pdf-to-companion.md`: a design proposal, not built, for a
  pipeline that turns one PDF into a Lean companion through ten gated stages,
  with the annotation document as its spine and a v1.1 proposal of three
  optional keys in two records. The docs tests now cover `docs/proposals/`.
- `cf plan-run --sections 1.7`: narrow a run's task universe to one or more
  `<chapter>.<section>` labels, one level below `--chapters` and with the same
  discipline (applied to the entries AND the reconciled universe, refused by
  name for a preset that cannot narrow, recorded as `tasks.sections` in
  `run.yaml`). Written for the section 1.7 re-run of the Tao differential.
- `docs/design/stage_map.md`: the current data model, workspace layout and
  stage map, replacing the archived design record as the spec.
- `CONTRIBUTING.md`, `POLICY.md`, this file, and `evals/` (trigger evals as
  data).
- `cf sources validate-registry` and `cf sources promote <id>`: the registry
  is the contribution surface, and it now has a check a contributor can run
  and a command that writes their entry as a reviewable patch.
- `tests/test_rf_docs.py`: the docs-to-CLI contract — every `cf` command and
  flag a document names must parse, every command must be in SKILL.md's index,
  and every cited repo path must exist.
- `tests/test_rf_scrub.py`: the whole-tracked-tree check for machine-local
  content — absolute home paths, one person's home layout, host names,
  addresses, session and Workflow ids, and citations into a transcript
  directory this repo has never held — and for citations of files that are
  not here. The rules and their allowlist are spelled out in that file.
- **`attribution_table` cross-checks its table against the Lean sources**
  (`lean_scan`, `docs/design/stage_map.md` §4 Stage 2). This kind builds its
  rows from an *export* a companion publishes, and an export can omit
  declarations the repository carries. The adapter now reads the Lean for the
  two signals the repo itself gives — a docstring that names a book item
  (through `cflib.items`, with the source's own kind list) and membership of a
  named `section` or `namespace` the convention files book statements under
  (`lean_scan.regions`) — and compares them by declaration name against the
  rows the export produced. `lean_scan.policy: union` (the default) publishes
  each omission that names an item as a row and writes the rest to a new
  per-companion artifact, `companions/<name>/unattributed.jsonl`, which
  `cf plan-run` stages into `runs/<id>/data/` beside the statement tables and
  both prompts name; `report` adds nothing to the table but still writes the
  omissions that name no item to that file; `off` is a deviation written into
  `adapter.yaml` (a bare `off`, which YAML reads as false, means `off`). Under
  `union` and `report`, `cf extract` and `cf onboard` name the omitted
  declarations in one warning (the first 25 on the terminal, all of them in the
  dry-run report); under `off` no scan runs and the same commands warn that the
  table was not cross-checked. The scan always excludes `.lake/**` (a custom
  `lean_scan.exclude` adds to that) and its content is in the extract key.
- a new optional statement-row key, `provenance`
  (`cflib.vocab.ROW_PROVENANCE`: `table` | `lean_scan` | `docstring` |
  `blueprint`, or null): which artifact **of the companion** a row was read
  from. Every shipped adapter sets it; it is what lets a finding say whether it
  searched a repository or only an export of one.
- `cflib.items.find_items`: every numbered item named anywhere in a blob of
  prose, through the one item grammar and the source's own kind list. The
  cross-check needed it and must not have had a second item regex of its own.

### Changed
- The annotation format moved to its own repository,
  formal-companion-annotations, released as 1.0.0 (tag `v1.0.0`, commit
  `2458140`): the schema, the specification, the checker, the id rules, the
  nine examples and their tests (the 133 of
  `formal-companion-annotations/tests/test_annotations_schema.py`) now live
  there. The skill vendors that release under
  `vendor/formal_companion_annotations/`, byte for byte, sealed by
  `vendor/formal_companion_annotations.manifest.json` (upstream, tag, commit,
  version, license, a sha256 per file); `scripts/annotations/sync_format.py`
  writes both from a checkout of the tag, and `tests/test_vendored_format.py`
  fails on a vendored byte that drifts from the manifest, on an import of the
  format without the `cflib.vendored` bootstrap, and on a shared set whose
  copy in `cflib.vocab` is not the format's. Nothing is installed: the
  vendored package is standard library only. The checker accepts and rejects
  exactly what the skill's copy did, with the same messages, warnings, exit
  statuses and canonical bytes. Only its command line differs: it runs as
  `PYTHONPATH=vendor python3 -m formal_companion_annotations.validate`, its
  usage lines and argument errors name `formal-companion-annotations-validate`
  where the skill's said `validate.py`, and it has `--version`. The
  schema's `$id` is now the package's, the raw URL of the schema at
  `v1.0.0`. `docs/annotations.md` is a stub pointing at the specification's
  new home; the section numbers the skill cites are unchanged.
- `cf harvest` and `cf import-corpus` run the findings gate **by default**;
  `--no-gate` is the escape and records `gate: null`, which `cf tally` then
  names. Previously the gate was opt-in and `SKILL.md` did not mention it.
- the 1,941-line design record moved to
  `docs/design/archive/multi-source-model.md` with a banner; four statements in
  it are marked superseded in place.
- a multi-line warning goes to stderr as one block (`cflib.log.warn_block`).
  The continuation lines of fourteen warnings — including `cf status`'s
  unconfirmed-sources list, whose per-source rows carry the sha and the origin
  — used to print on stdout, so `cf … 1>out 2>err` split each warning from its
  own evidence.
- `tests/e2e/multisource.sh` takes `CF_TML` and `CF_MT` from the environment
  and names them when they are unset, instead of defaulting to two checkouts
  that exist on one machine.

### Fixed
- The viewer's shortcuts ignored a key typed with Shift, so `+` never zoomed
  on a layout that types it with Shift; Shift now disqualifies a named key
  such as ArrowRight only.
- `cf run --include-sources` re-transfers a companion with `--delete`, making the earlier read-only copy writable first, so the files a later commit deleted no longer stay on the host and fail the tree digest; a refused digest now names the extra, missing and changed paths (#6).
- `cf export attribution-table` marks an equation's title as the producer's
  (`title_source: producer`); in measure_theory `equation-1.2` and
  `equation-1.3` change, so the measure_theory reference must be regenerated.
- The findings gate's changed-files check now diffs a contribution from the
  point it branched (the merge base with the merge target), not from the
  target's tip. Against the tip, every finding merged after a worktree was cut
  read as that worker's deletion, so the gate refused nearly every first
  launch and each task needed a rework to pass (903 launches for 385
  worktrees in the first campaign). A landed finding a worker really deletes
  is still refused (and a history with several merge bases, or a rewrite
  of a finding already on the target, is refused too), and uncommitted edits
  still count.
- the errata converter deleted every mathematical symbol. On the pages these
  come from a formula is an `<img>` (`latex.php?latex=<url-encoded TeX>`, the
  same TeX in `alt`), so stripping tags left the prose and dropped the content:
  entries of the shape `In Corollary 3.2.4(b), should be , and the same` — a
  grammatical sentence that says nothing, shipped to workers as authoritative
  errata, while the symbols sat in the kept `errata.raw.html` the whole time.
  The formulas are now rendered as
  `$TeX$`, keyed on the `latex.php?latex=` pattern and `class="latex"` rather
  than on a host; `errata.json` records how many were recovered and how many
  were not. The renderer carries a version, so `cf sources refresh` re-derives
  an already-ingested `errata.md` from the bytes on disk with no network (read
  back as the UTF-8 the fetch wrote, whatever charset the page declared), and
  `cf status` names a source still holding an older render. A render by a
  NEWER converter is left alone. No extract key
  moves: `key_files` are paths inside the companion repo, and no adapter reads
  `errata.md` (`tests/test_sources_pair.py` pins both directions).
- an errata entry that still looks emptied — `, ,`, `should be ,`, an empty
  quotation, a formula the page carries only as image bytes, or TeX the page
  carries in a `<script type="math/…">` that the render does not emit (the
  entry keeps under 90% of the characters the renderer saw) — is now listed
  in `errata.json` and warned about once per source by `cf onboard` and
  `cf sources refresh`, naming the entries; `cf status` gives the count. It
  used to be emitted silently, which is the whole of the defect above. The
  guard is not exhaustive: maths in an image the page does not mark as a
  formula, or in an `<svg>`, is caught only when the sentence's shape gives it
  away.
- 44 docstring citations pointed at four design documents that were never
  written, and the reference docs sent a stuck reader to `cf ingest`, a command
  that never existed; `book/fetch_errata.py` and several other stale paths went
  the same way.
- seven of those citations were rewritten into quotations of
  `docs/design/stage_map.md`, which did not carry the sentences: the three stage
  gates they lean on (the Stage 7 ledger gate, Stage 8's corpus join, Stage 9's
  routing rule) are written down there now, `scripts/adapters/base.py` points at
  the document that really holds the statement-row field list, and the contract
  test checks a quotation as well as a path.
- `cf sources validate-registry` refused an ordinary edition contribution: the
  second-sentence check read an author's middle initial (`John B. Conway`), a
  volume numeral (`Analysis I. Fourth edition`) and an abbreviation
  (`2nd ed. Revised`) as prose. It now requires a real word before the sentence
  break, and with `--overlay` it reports the count it actually checked.
- a docstring told an operator recovering a killed run to look at
  `cf challenge`; the spelling is `cf adjudicate --challenge-only`, and the
  contract test now reads the `cf <word>` literals under `scripts/`,
  `workflows/` and `templates/` too.
- two line-range citations into files this repo rewrites (`references/traps.md`
  G5 into `CLAUDE.md`, `references/vocabulary.md` §2.2 into the gate script);
  both now cite a section, and a test refuses a new one.
- The changed-files check lost a new file when the worktree set `diff.renames=copies` (a copy line's destination was dropped) or a gitlink under `diff.ignoreSubmodules=all`, and replace refs, grafts or a shallow file could move its merge base past a landed finding; it now diffs with `--no-renames --ignore-submodules=none`, fails closed on a repository with any of those three, and on any status line it does not parse (#9).
- the findings gate ran `git` inside a worktree a worker can configure, so a
  content filter driver named from that worktree's `.git/info/attributes` made
  the gate run the worker's own program — and the gate still reported the
  contribution clean. Every filter driver the repo names is now blanked before
  the gate compares anything.
- `cf share export` and `cf route --github` redact prose with one redactor over
  every free-text field of a row, not the seven field names v1 happened to use.
  Book text used to survive in `summary`, `recovery_argument`, a dissent's
  argument and the witness — that is, in a bundle handed to somebody else and
  in the issue body drafted for a public tracker.
- leanparse did not see a `mutual` block, a `public`/`meta`/`@[expose]` section header or a block line with a trailing `--` comment, so the `end` closing it closed the enclosing namespace or section and later declarations got the wrong `full_name` or `sections`; each `end` now closes its own block (#24).
- a manuscript companion (`blueprint_tex`) has no item table by design, so the
  handoff gave it a coverage denominator of zero and a `TODO(coverage)` naming
  commands that could not fill it, while the same file said the run had
  hundreds of tasks. Its universe now comes from the pairing index, so the two
  halves of the report agree.
- a differential `cf plan-run` staged every companion in the workspace,
  including companions of other sources; it now stages only the companions
  bound to the source the run audits.
- a table-driven corpus was short of the repository it describes, and nothing
  said so: a worker who searches such a corpus for an identity, finds nothing
  and files a `COVERAGE` disagreement ("the companion states it nowhere") is
  making a claim about the export, and a public, sorry-free theorem in the
  repository can prove it false. Declarations inside a `Companions` region,
  spelled `section` or `namespace`, and declarations whose docstring names a
  book item can both be absent from the export, and most such omissions cannot
  be keyed on an item at all. The adapter now unions the ones that can, lists
  the rest for every worker, and warns about all of them (above), and the
  prompts carry the other half: absence from the statement table is
  never sufficient grounds for a `COVERAGE` finding, a worker must
  `grep -rn --include=*.lean` the repository for the mathematical content before
  any "no counterpart exists" claim and say what it ran, and a reviewer must
  open the Lean for every finding that rests on an absence
  (`references/traps.md` B6).
- `cf sources add` bound the source to the companion and saved the workspace
  before preparing it, so a source it then refused with exit 2 stayed in the
  audited scope; `cf onboard <repo> --source <file>` reached the same refusals
  and left a source row, a registry overlay row and a prepared directory behind,
  recoverable only with a `cf sources remove` nothing named. Both prepare
  first now: a refusal leaves no binding and no overlay row, and removes a
  directory it prepared for a source the workspace did not already hold. A
  source the workspace did already hold stays; `cf sources add` says so and names
  the command that drops it, and `cf onboard` does the same for one the operator
  named with `--source`.
- `cf status` exited 2 on a companion directory that has an adapter but no
  `workspace.yaml` entry — the state a half-finished onboard or a copied
  directory leaves — one line after printing it as `not onboarded`. It prints it
  and exits 0.
- `cf adjudicate --transcribe` rewrote the ledger from one result, so a second pass wiped the first; it now merges by `finding_id`, a new verdict replacing the old row in either file.
- the printed `cf adjudicate` and `cd` next steps interpolated the workspace
  path unquoted, so in a workspace whose directory name contains a space they
  could not be pasted. `cf route` writes one of them into the routed ledger and
  into the owner-message draft, so the broken command travelled upstream.
- `cf share export` told the operator that `cf share import` would refuse a
  bundle with no ledger rows. The import accepts that bundle and merges nothing;
  the export says what it does instead. Its summary also named three field names
  for the prose scan, which has covered every free-text field of a row, at any
  depth, since that scan was widened.
- the findings gate blanked the git filter drivers a companion's own config
  names by passing `git -c <driver>=`, which cannot express a driver whose name
  contains `=`; such a driver still ran during the gate, and the gate still
  reported the contribution clean. The blanks travel in the environment now.
- the batched pre-merge check refused every merge of two or more tasks: each
  task's own task file is modified rather than added, and the relaxation only
  fired when every changed path was new.
- a source text that exists but cannot be read — a directory left by a partial
  copy, or a file the run host's user may not open — ended the gate in a
  traceback instead of the exit code reserved for infrastructure faults.
- a quote spread over three or more pages of the source matched no page at all,
  which turned the item-page check off rather than refusing the finding; one
  folio-only page at the seam was enough to reach it.
- a quote whose edge carries a sign written apart from its operand (`- 1 ≤ …`
  where the source has `1 ≤ …`) was certified verbatim.
- `cf cost` and `cf harvest --transcripts` filed Claude Code's own API-error lines (model `<synthetic>`, zero usage) as an unpriced model, so an exact cost read as a lower bound; they are skipped now.
- `cf report` dropped a corpus document `cf tally` refuses to read, publishing
  coverage figures that were a lower bound with nothing in the report saying so;
  it also dropped every examined item outside the audited chapters from those
  figures, including the count of items with findings, while the tally counted
  them.
- `cf report` counted a manuscript companion's items with the default numbering
  depth rather than the source's own, so a deeper label was dropped and the
  coverage line could read "0 of 0 item(s) in scope examined" for a manuscript
  that was examined in full.
- the remedy printed for an ungated run named a `cf import-corpus` invocation
  that exits 2 in a workspace with more than one source, because `--source` was
  missing from it.
- the per-chapter "in task universe" column printed 0 where the total row
  printed a dash: nothing had computed the figure.
- on a host whose git predates 2.31 the blanking of a content filter driver
  whose name contains `=` reached it through neither channel, so such a driver
  still ran on a read. The gate now refuses to read that worktree at all (exit
  3, naming the driver and the git version) and `cflib.gitsafe` returns a
  refusal instead of a read; on git 2.31 and later nothing changes.
- a boundary dash was stripped as punctuation unless a DIGIT stood beside it, so
  `- m*(F) ≤ m*(E)`, `- (1 ≤ m(F))` and `- x ≤ y` were still stripped back to the
  book's own words and certified verbatim while the same flips written without
  the space were refused. A dash at a quote's edge is now punctuation only when
  the text beside it is an alphabetic word.
- an alphabetic word was not enough either: the words a display opens with
  (`lim`, `sup`, `inf`, `dist`, …) and the letter run inside a trailing LaTeX
  macro read as prose, so `- lim sup En …` and `… \alpha -` were stripped back
  to the book's own words and certified verbatim while the glued spellings were
  refused. Those are operands now, at both edges.
- `cf quotes hydrate --out` read a git read the safety boundary REFUSED as "no
  repository encloses this path", so a refusal became permission to write
  recovered book text into a tracked file — the one thing that check exists to
  stop. A refused read means "do not write" now, as a hung git already did, and
  `cf recheck`'s pin records the refusal's own reason instead of calling a
  checkout "not a git checkout".
- `cf doctor` reports `git --version` beside the presence check and warns below
  2.31, the floor the gate's content-filter blanking rests on;
  `references/runbook-server.md` §2 records it as a precondition. The
  requirement used to be met for the first time as a blocked run.
- `cf report` dropped a whole corpus run from section 0 when its documents named
  a source the workspace no longer holds — a `cf sources remove` after a
  harvest, or a corpus imported under another workspace's id. The run's items,
  findings, unreadable documents and ungated state left every section-0 figure
  and `reports/coverage.json` without a word, while `cf tally` went on counting
  the same rows. They are published as `unknown_source_documents` and named in
  the note, and `cf sources remove` says which corpus runs were judged against
  the id it is dropping, whether the receipt or the run's plan names it.
- the handoff said only one of its five caveats on the three shapes whose
  per-source note ends early (a source no companion is bound to, an unpaired
  manuscript, a source with no `items.json`): an ungated or gate-FAILED run, an
  examined item outside the audited chapters and an unstamped document went
  unmentioned. All five are said on every shape now, for a run that names no
  single source as well, and `cf report` echoes the same set on the terminal.
- `cf share export` tells you which of the two zero-ledger bundles you have
  written: one `cf share import` merges zero rows from, or a findings-only
  bundle the import refuses, which is read with `cf quotes hydrate --bundle
  <bundle>`. `cf share export --help` describes the redaction the command
  performs: every free-text field of a row at any depth, and a record of (page,
  normalised length, sha256, and at most a quarter of the quote's words from each
  end, none at all below fourteen words).
- `cf run --stage raise` re-sent `tasks/` over merged work and restarted tend; it now only raises, and a re-send onto a moved `main` needs `--force` (#7).

## 0.2.0 — the multi-source model

The rewrite from "one campaign per book" to **N companions × M sources**:
`workspace.yaml` (schema 2), sources identified by the sha256 of their bytes
and remembered in a shipped registry, `cf onboard` discovering and inferring
everything that used to be typed in, `(source_id, item)` as the item key, and
`source_sha` on every artifact. `cf book` was deleted in favour of
`cf sources refresh`. See `docs/design/archive/multi-source-model.md` for the
design record.

## 0.1.0 — the port

The first version: one campaign, one book, the stages that produced the
baseline corpus.
