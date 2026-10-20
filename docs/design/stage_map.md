# The stage map and the data model

**Status: current.** This is the short description of what the skill *is* —
the data model, the on-disk layout and the eleven stages — and every normative
sentence in it was checked against the code on the branch that added it. It
replaces `docs/design/archive/multi-source-model.md`, which is the design
record the implementation was written from and is now history.

Three documents, three jobs, and none of them re-states another:

| document | audience | holds |
|---|---|---|
| `SKILL.md` | the agent driving an audit | when to use it, the program, the command index, **the hard rules** |
| this file | anyone changing the code | the data model, the layout, the stages, what carries what |
| `CLAUDE.md` | a contributor | the invariants, how to add a command / an adapter / a source |

The hard rules are enumerated **only** in `SKILL.md`. Re-enumerating them here
is what let an earlier copy fall one rule behind without anyone noticing.

---

## 1. The data model

- A **source** is one edition of one work: a PDF, or a manuscript that is
  already text. Its identity is the sha256 of its bytes (`source_id`,
  `source_sha`), and what is known about it lives in `registry/sources.yaml`
  (shipped, curated) or a workspace's `registry.yaml` overlay (discovered,
  `confirmed: false`). See `registry/README.md`. Every source has a licence
  class (`cflib.vocab.LICENCE_CLASSES`, `closed` unless its entry says
  otherwise; `POLICY.md` §4.1), and the registry's `families:` map holds
  web-source families, one row per site and none per revision (an overlay
  row cannot change a confirmed family's `confirmed`, `licence_class` or
  `licence`, and is reported). A **web source** — one rendering of one page
  revision, `<family>-<pageid>-<revision>` — lives in a **sourceset**
  (`cf sourceset`; `references/locate-documents.md` §7), never in
  `workspace.yaml`: twenty thousand of them fit no file every command validates.
- A **companion** is a Lean repository whose declarations claim to formalise a
  source's numbered items. A companion is audited against a **list** of
  sources; one of them is its **anchor source**, the edition whose pagination
  its page anchors follow.
- A **workspace** is the directory the operator works in: N companions, M
  sources, in any arrangement. A **differential** is possible exactly where two
  companions' source lists overlap.
- **Locate** asks the inverse question, over libraries rather than companions
  (`docs/proposals/wikipedia-locate.md` §2). A **library** is a Lean package at a
  40-hex commit; a **universe** is a named, frozen set of them
  (`corpora/<u>/universe.yaml`, `universe_sha256`) that spells each repository
  one way (`references/locate-documents.md` §8); a **passage** is a span of a
  web source keyed `In-text #<hex>`; a **location** is (passage,
  declaration@commit, relation), keyed run-free by `cflib.hashing.location_key`;
  an **absence** is a checked statement that a passage was searched for in a
  universe and nothing was found. A passage ends located, absent or unresolved,
  and its derived status is `cflib.vocab.derive_status` over what survived the
  challenge.
- An **item** is the pair `(source_id, item)`. Two sources may legitimately
  both have an `Exercise 1.3.19`, so a bare item string is never a cross-source
  key. One string spelling exists — `source_id::item`
  (`cflib.items.qualified_item`), `qualified_slug` for a filename.
- A **finding** is content-addressed: `finding_id = sha256(run_id, source_id,
  item, kind, sorted unfaithful_decls)`, five parts joined with `\n`, the item
  through `items.clean()` first (`cflib.hashing.finding_id`). It is frozen: an
  existing corpus is keyed on it.
- Sources are prepared **once per source**, not once per companion: two
  companions of the same book are compared against the same bytes.

### 1.1 The item grammar

`<Kind> <n>[.<n>…]`, one to four numeric levels
(`cflib.items.MAX_NUMBER_SEGMENTS = 4`), with an optional sub-part — or
`In-text p.<n>` — or `In-text #<hex>`, 8 to 16 lowercase hex digits, a
passage of a source with neither numbers nor pages keyed by the hash of its
head (`cflib.passages`; a malformed hash is the reject `bad_passage_id`; the
production is tried after the page and the cross-reference, so it takes only
a label those refuse). The
kind list is the **source's**, inferred from its own
headings; the depth is inferred per source and stored as `numbering`
(`cflib.inference.DEFAULT_NUMBERING = (2, 3)`). A label deeper than the
source's numbering is a **reject**, never a truncation. Parsing is
whitespace-tolerant and produces a reject list callers must report: a
single-space regex once dropped `Exercise    1.3.19` from a chapter of 411
items and published the denominator 410.

Two normalisers exist and they are not interchangeable: `items.clean()` is the
ID transformation (whitespace only, hashed, frozen), `items.join_key()` is the
key two corpus generations join on. Anything that MATCHES uses `join_key`;
anything that WRITES or HASHES leaves the label as written.

## 2. Two directories, never mixed

| | holds | git |
|---|---|---|
| the skill repo | code, templates, docs, the source registry (metadata only) | committed, may be shared |
| the workspace | source text, page images, statement tables, corpus, ledgers, reports | local repo, **no remote** |

`scripts/sources/` is code; `sources/` is workspace data. The skill repo's own
`.gitignore` carries the workspace patterns, so the `!scripts/sources/`
negation is what stops `sources/` hiding the whole stage-1 package. Those two
lines move together or not at all.

### 2.1 The workspace layout

```
<workspace>/
  workspace.yaml              committed: companions, sources, hosts, models,
                              defaults, budget, policy, library_paths
  registry.yaml               committed: this workspace's registry overlay
  .gitignore                  written by `cf init`

  sources/<source_id>/        GITIGNORED (copyrighted)
    text.txt                  the pinned text (`pdftotext -layout`, or the
                              manuscript's own bytes)
    pages/pNNN.txt            one file per page, when the source is paginated
    images/pNNN.png           only when rendered
    items.json                {source_id, source_sha, offset, kinds, numbering,
                              pagination, locator, count, by_kind, by_chapter,
                              ambiguous, rejects, items:[…]}
    meta.json                 identity + source_format + pagination + locator +
                              inference + provenance
    errata.md / .raw.html / .json    the rendered errata, the bytes curl
                              returned, and the fetch record
  companions/<name>/          committed: adapter.yaml, conventions.md,
                              statements.jsonl, state.json, and -- for an
                              export-driven adapter with omissions it cannot
                              key on an item -- unattributed.jsonl
  pairs/<source_id>/          committed: pairs.jsonl, coverage.md, index.json,
                              reconciliation.json, rejected.json, queue.json,
                              targets.<name>.json, def-coverage.<name>.json,
                              uncovered.<name>.json, anchor-check.json
  runs/<run-id>/              run.yaml, tasks/, prompts/, gate.json,
                              tend-root/ (gitignored); a locate run also
                              gate.local.json, planned.json (every task
                              manifest), challenge-plan.json, gate-local-log/
                              (this machine's gate calls) and host/ (what
                              `cf harvest` brings home: gate.jsonl,
                              usage.jsonl, canary.json)
  corpus/<run-id>/            findings + RECEIPT.json  (committed: the baseline)
  pipelines/<id>/             pipeline.yaml, doc.vN.json, stages/<stage>.stage-receipt.json
                              (docs/proposals/pdf-to-companion.md section 4.1; the
                              loader and the receipt writer exist, no stage does yet)
  ledger/                     accepted.<name>.jsonl, refuted.<name>.jsonl,
                              adjudicated.<name>.md, routed/findings.<name>.jsonl
  detect/<name>/              proxy-detection artifacts
  reports/                    tally.<run>.json, union.<name>.json,
                              agreed_high.json, coverage.json, score.<run>.json,
                              pin.<run>.json, recheck.<name>.json,
                              handoff.<date>.md, locate-outcomes.<run>.jsonl,
                              locate-tally.<run>.json, export.<set>.json
  sourcesets/<id>/            locate's web sources: manifest-<n>.jsonl (append-only,
                              committed), state.json, candidates/<u>/<source>.json
                              (a blind or planted arm's under candidates/<u>/<kind>/),
                              sources/<source_id>/ (GITIGNORED: raw bytes, body,
                              layer, head text, reading copy, map, sections,
                              blocks, finders)
  corpora/<u>/                universe.yaml and conventions/ (committed);
                              index/<lib>/, corpus.sqlite, corpus.blind.sqlite
                              (GITIGNORED: large, derived, rebuildable)
  exports/<set>/              sealed Literature Links record sets
  benches/<id>/               the locate benchmark (references/locate-bench.md);
                              host/ is the only label-free part
  campaigns/<id>/             campaign.yaml and the rendered host sequencer
  fixes/  reviews/            created by `cf init`, written by nothing in v1:
                              they are the extension points for the
                              out-of-scope `fix` and `review-pr` stages
                              (`cflib/paths.py`, "v1: reports only")
```

`companions/<name>/lint.json` has a path in `cflib/paths.py` and no writer:
`lint` is documented and out of scope in v1.

### 2.2 What carries the source, and what carries the companion

A finding is a claim about a source item **and** about a companion revision, so
both are pinned:

- `statements.jsonl` rows, pairs rows, findings documents and every
  `disagreements[]` row, ledger rows, routed rows, tally rows, union entries:
  `source_id` **and** `source_sha`.
- `pairs/<source_id>/*`: the source is in the path *and* in each row.
- `runs/<id>/gate.json`: `sources: {<id>: {text, sha256, text_sha256,
  page_offset, images_dir, item_pages}}` — the gate picks the text by the
  document's `source_id`. `"item_pages": {item: pdf_page}` is the page OF THE
  PINNED TEXT (`items.json`'s `pdf_page`, never its printed `page`), the first
  of the three routes the gate uses to bind a quote to the page its item is
  stated on. A top-level
  `max_match_pages` says how many pages the matched text may occur on before it
  is refused as a running head (0 turns that off).
- `corpus/<run>/RECEIPT.json`: `sources: {<id>: <sha>}`, a per-source count, a
  sha manifest, and the gate block (`gate: null` for an ungated import — `cf
  tally` then warns `ungated run(s): <id>` every time it counts that run, and
  the handoff carries the same list). A gate that RAN and rejected is a third
  state, not the second: `gate.ok == false` keeps the run out of `ungated` and
  puts it in `gate_failed` instead. The two lists are disjoint in
  `reports/tally.<run>.json` and in `reports/coverage.json` alike, and `cf tally`
  and the handoff each name them in separate sentences — a rejected run's rows
  are in the corpus and are counted like any others. The receipt also carries
  `cost`: null for a run harvested without `--transcripts`, otherwise the
  priced totals of the agents' transcripts (`tokens` by model id, `usd`,
  `priced`, `unpriced`, `price_table_sha256`, `transcript_count`,
  `message_count`), priced with `workspace.yaml` `budget.prices`. `cost` never
  changes `complete`; an unpriced model makes `usd` a lower bound and is named
  wherever the figure is printed.
- `runs/<id>/run.yaml`: `companion_pins: {<name>: {repo_commit, dirty,
  method}}`, read through `cflib.gitsafe`.
- `reports/pin.<run>.json`: written by `cf harvest`, never by a worker — the
  commit from `run.yaml` plus `{repo:decl → decl_fingerprint}` over the
  statement table extracted **at that commit**. A declaration the pin cannot
  vouch for is listed under `unpinned` rather than fingerprinted against HEAD.
  It is a report and not a corpus file: a new file under `corpus/<run>/` would
  be read as a finding and would change the receipt manifest.
- `reports/recheck.<name>.json`: one row per open finding, status ∈
  `unchanged | changed | gone | unpinned` (`references/vocabulary.md` §1.8).

## 3. Discovery and inference (stage 0)

**Nothing about a source is typed in.** `cf onboard <repo>` reads the
repository, fingerprints the bytes wherever they can be found on this machine,
infers what the text supports, and **refuses** what it cannot decide, naming
the override. A guessed page offset is worse than no answer: an anchor wrong by
one page is exactly the defect class this audit exists to find.

- **The adapter.** Each convention's own class scores the repo
  (`adapters.api.Adapter.detect` → `(score, evidence)`);
  `cflib.discovery.detect_scores` is the ranking loop over
  `adapters.api.registry()`, and `cf onboard` takes the highest, refusing below
  `MIN_ADAPTER_CONFIDENCE` (0.5) with one "looked for / found" line per kind.
  Implemented: `docstring_tags`, `attribution_table`, `blueprint_tex`.
- **The sources.** Seven repo signals (`references/onboarding-contract.md` §0)
  plus whatever the adapter itself claims — a leanblueprint's manuscript IS its
  `.tex` tree, so its adapter claims it. One sha is one source: two claims on
  the same bytes fold into one row.
- **Where a PDF may be opened from.** The repo itself, the workspace's
  `sources/` store, and the `library_paths` roots. Onboarding
  `<parent>/<companion>` adds two derived roots, `<parent>/**/sources` and
  `<parent>/**/.lake/official-pdf` — never the bare parent — and the repo being
  onboarded contributes its own (`cflib.workspace.derived_roots_for`).
- **The page offset**: the mode of the running-head votes, with a confidence;
  below `MIN_OFFSET_CONFIDENCE` (0.9) it refuses.
- **The item kinds**: the capitalised words that really head numbered items in
  this source's text, case-folded so an AMS `THEOREM 1.2` head counts.
- **The numbering depth**: how many numeric segments this source's own headings
  carry, kept as `numbering`; a deeper label is a reject
  (`cflib.inference.infer_numbering`, `MIN_DEPTH_SHARE = 0.05`).
- **Item completeness**: items found against the slots the numbering itself
  counts; below `MIN_ITEM_COMPLETENESS` (0.6) it refuses, and
  `cf sources set <id> completeness_ok=true` is the recorded escape.
- **Source format and pagination**: a PDF, or one of
  `.txt .md .markdown .tex .html .htm .rst` (`sources/locator.py`
  `TEXT_SUFFIXES`). A text with no page breaks is **unpaginated**: `pages: 0`,
  `offset: 0`, `offset_method: none`, items located by heading path and line
  (`locator: section`), and every page-dependent feature refuses on it by name
  rather than compute a page nothing supports.
- **The errata page**: from the registry, corroborated by the book page the
  repo cites; fetched with `curl` and the raw HTML kept. A URL the *repo*
  supplies waits for `--fetch-errata`, because the page reaches workers as
  authoritative text. `errata.md` is **derived** from those bytes, and the
  mathematics is part of it: a formula served as an `<img>` (WordPress writes
  `latex.php?latex=<url-encoded TeX>` and repeats the TeX in `alt`) is rendered
  as `$TeX$`, keyed on that pattern and on `class="latex"`, never on a host.
  The renderer carries a version (`fetch_errata.RENDERER_VERSION`); a bump
  re-derives every already-ingested `errata.md` from the kept HTML, offline, on
  the next `cf sources refresh` — and invalidates nothing else, because no
  adapter reads `errata.md` and an extract key's `key_files` are paths inside
  the companion repo. The re-render reads `errata.raw.html` as the UTF-8 the
  fetch wrote, and re-derives only a render by an OLDER converter. An entry
  that still reads as emptied — `, ,`, `should be ,`, an empty quotation, an
  unrecoverable formula image, or one that kept under 90% of the characters
  the renderer saw in it (its text, the TeX of every formula the page marks as
  one, and the body of every `<script type="math/…">`, which is never
  emitted) — is recorded in `errata.json` (`suspect`) and warned about by name,
  once per source. `cf` does not refuse the page over it: the other entries
  are real and the raw HTML is on disk, but a stripped entry reads as
  grammatical English with the substance removed, so it is never passed on
  silently. The guard is not exhaustive: an image the page does not mark as a
  formula is not counted (on the live pages those are commenters' avatars),
  nor is an `<svg>`, so maths served that way is caught only by the sentence
  shapes, and an empty `suspect` list is not proof nothing was lost.
- **The anchor edition**: the repo's own page anchors are scored against each
  candidate edition; prose only corroborates. There are two elections, and they
  do not behave alike: the first (which edition is the anchor) **warns and
  keeps its 0.6 pick**; the second (a rival pair inside the anchor's own
  cluster) **refuses**, below `MIN_ANCHOR_AGREEMENT` (0.80) or with a margin
  under `MIN_ANCHOR_MARGIN` (0.05), naming `--anchor-source`.

Every override stamps its own trace (`offset_method: manual`, `scope_method`,
`numbering_method`, `completeness_method`), so a number a human typed is never
later mistaken for one the text supports.

## 4. The stages

The numbers are the ones the module docstrings use.

| # | command | writes |
|---|---|---|
| 0 | `cf init`, `cf onboard`, `cf doctor` | `workspace.yaml`, `registry.yaml`, `companions/<name>/{adapter.yaml, conventions.md}` |
| 1 | (inside `onboard`) `cf sources refresh` | `sources/<id>/{text.txt, pages/, items.json, meta.json, errata.*}` |
| 2 | `cf extract` | `companions/<name>/statements.jsonl` — no-op when (repo commit, adapter key) is unchanged; an export-driven adapter cross-checks its table against the Lean sources first |
| 3 | `cf pair` | `pairs/<source_id>/*`: pairs, coverage, queue, targets, def-coverage, uncovered, and the mechanical page-anchor check |
| 4 | `cf plan-run` | `runs/<id>/{run.yaml, tasks/, prompts/, gate.json, tend-root/}`; reconciles the task count against `items.json ∩ any-repo` |
| 5 | `cf run` | nothing in the workspace but `run.yaml`'s records: it transfers and drives the fleet (**dry run until `--execute`**) |
| 6 | `cf harvest` / `cf import-corpus`, `cf tally` | `corpus/<run>/` + `RECEIPT.json`, `reports/tally.<run>.json`, `union`, `agreed_high` |
| 7 | `cf adjudicate` | the Workflow args, then (`--transcribe`) `ledger/{accepted,refuted}.<name>.jsonl` + `adjudicated.<name>.md` |
| 8 | `cf detect-proxies` | `pairs/<id>/def-coverage.<name>.json`, `detect/<name>/*`, and PROXY/COVERAGE rows into `corpus/detect-<name>-<tier>/` through the `mechanical` gate |
| 9 | `cf route` | `ledger/routed/findings.<name>.jsonl` + `message.<name>.md`; refuses an upstream export while a file is UNCHALLENGED |
| — | `cf recheck`, `cf score`, `cf share`, `cf quotes` | `reports/recheck.<name>.json`, `reports/score.<run>.json`, a redacted bundle, a hydrated quote file |
| 14 | `cf report` | `reports/handoff.<date>.md`, `coverage.json`, `unexamined.<id>.json`, `verify-claims.args.json` |

`cf share` calls itself stage 14 too, beside `cf report`; `recheck`, `score`
and `quotes` carry no number because they sit beside the pipeline rather than
in it. `fix`, `review-pr`, `camera-ready` and `publish-verdicts` (which
`ledger/compat.py` numbers stage 13) are documented and **not implemented**:
v1 is findings-only, and `fixes/` and `reviews/` are where they would write.

Four stages carry a gate of their own, and each one is a number that was once
wrong. The module docstrings cite these sentences, so they move with the code:

- **Stage 2.** An adapter that builds its rows from an **export** of a companion
  rather than from the companion cross-checks the two, and the disagreement is
  never silent. `attribution_table` reads the Lean sources for the two signals
  the repository itself gives — a docstring whose LEADING item mention names a
  book item (through `cflib.items.find_items`, with the source's own kind list,
  never a second regex; a leading mention the grammar refuses keys nothing and
  is reported, and the next mention along is never used instead) and
  membership of a named `section` **or `namespace`** the convention files book
  statements under (`lean_scan.regions`, default `Companions`; one repo may
  spell the same block both ways) — and compares them by declaration name
  against the rows the export produced.
  Omissions go two ways, because most cannot be keyed on an item: under
  `lean_scan.policy: union` one that names a book item becomes a row stamped
  `provenance: "lean_scan"`, and one that names none — a block headed by prose
  such as `Companion 3: the gadget hierarchy`, with no number anywhere — is
  written to `companions/<name>/unattributed.jsonl`, which `cf plan-run` stages
  beside the statement tables and both prompts name as the list to read before
  any "no counterpart exists" claim. The item grammar never guesses an item for
  those. Under `report` nothing is unioned, and the file still holds exactly
  the omissions that name no item. Under `union` and `report`, `cf extract` and
  `cf onboard` name the omitted declarations in one warning (the first 25 on
  the terminal; the dry-run report has all); under `off` no scan runs, and both
  commands warn on every run that the table was not cross-checked. A bare
  `policy: off` is YAML false and is read as `off`. The scan always excludes
  `.lake/**` — without that it reads Mathlib and calls its declarations the
  companion's; a custom `lean_scan.exclude` adds to it — and its content is in
  the extract key, so a commit that adds a declaration to one of those regions
  re-extracts. Anonymous declarations are counted and never claimed: an export
  names them by curated slug, not by position, so there is no name to compare.
  The rule this enforces is that **absence from an export is not absence from a
  repository**: a COVERAGE finding saying a companion states some identity
  nowhere, read off a table-only corpus, is a claim about the export, and a
  public, sorry-free theorem the export left out makes it false.
- **Stage 7.** `ledger/validate_ledger.py` refuses — never warns about — a
  ledger row that does not answer a real finding by `finding_id` (a 22-char
  label prefix is how batch-1 challenge results were mislinked); that uses a
  verdict, severity, kind or `resolution` outside `references/vocabulary.md`;
  that omits `source_id` / `source_sha`, or names a sha the workspace did not
  pin for that source — the source gate at the ledger boundary; that returns a
  verdict in `VERDICTS_REQUIRING_ARGUMENT` with under `MIN_RECOVERY_CHARS` (40)
  of written recovery argument; or that drops a skeptic's sub-threshold dissent.
- **Stage 8.** A detector hit counts only once it is emitted as a finding row of
  kind `PROXY` (with `bridge: proved|fiat|none`, `cflib.vocab.BRIDGE_KINDS`), so
  it joins the corpus and the ledger like any other finding: rows left in
  `detect/<name>/` are read by nothing downstream. Every emitted document is
  validated against the `mechanical` gate preset before it is written, and the
  run refuses while `unclassified_modules` is non-empty, because an unclassified
  module suppresses every hit that goes through it (`--allow-unclassified` is the
  recorded escape).
- **Stage 9.** Routing is on the `unfaithful_decls` prefix and nothing else
  (`route.ROUTING_RULE`). The fallback it replaced also admitted a row where the
  repo merely appeared in `repos_involved`, and that is what produced the
  "31 findings" scare for a repo named in none of them.

Two properties hold across all of them. **Idempotence**: every command may be
re-run — extraction keys on (repo commit, adapter key), planning is
deterministic given `run.yaml`, harvest diffs against the previous receipt, a
second `cf onboard` re-derives rather than duplicating. **One state file per
companion**: `companions/<name>/state.json` (version 2, a per-source
`{sha, rows, items}` map and an `anchor_source`), written through
`cflib.state`'s atomic read-modify-write; `cf status` prints only what it read.

### 4.0 The locate stages

Locate plans from a **sourceset and a universe**, never from a companion, and
its stages share the run, gate, bridge, harvest and cost machinery above
(`docs/proposals/wikipedia-locate.md` §3; the formats are
`references/locate-documents.md`).

| stage | command | writes |
|---|---|---|
| L0 universe | `cf corpus init / add / probe-command / ingest / build-db` | `corpora/<u>/{universe.yaml, index/<lib>/, corpus.sqlite}`; `probe-command` prints the host pipeline and runs nothing |
| L1 sources | `cf sourceset create / enumerate-wikiproject / adopt / fetch / flag-biographies / render / status` | `sourcesets/<id>/manifest-<n>.jsonl` (append-only versions, page flags), `state.json`, and each source's rendered files (`web-html/3`; `create --from` seeds a new sourceset for a new renderer) |
| L2 candidates | `cf locate candidates` | `sourcesets/<id>/candidates/<u>/<source>.json`; `--blind` or `--tools-corpus-db` write their own `<kind>/` directory under it (a planted one names no hit its database lacks) |
| L3a/L3b/L5 | `cf plan-locate --preset locate-inventory / locate / locate-challenge` | `runs/<id>/{run.yaml (a locate block), tasks/, prompts/, gate.json, gate.local.json, challenge-plan.json, tend-root/}` |
| L4 gate | `scripts/gate/verify_locate.py`, out of tree (tend's validation `--changed-files`, its pre-merge `--changed-files --pre-merge`; an agent may run only the `--findings` check, and a command line that could fake the label is exit 3) | nothing but its own log line, which names its `caller`; exit 0 / 1 / 3 |
| — | `cf run`, `cf campaign plan / render / status` | `run.yaml`'s records; `campaigns/<id>/` (`campaign.yaml` holds the watchdog's thresholds and `unpriced_usd_per_hour`, the flag's or the pilot's highest priced rate, checked again at every `plan`; `render` refuses a campaign without that rate or a smoke-tested pilot) |
| L6 | `cf harvest` (re-gated by the run's own staged gate), `cf tally --locate`, `cf export literature-links` | `corpus/<run>/<task>/<source>.json` + `RECEIPT.json` (its gate block's `gate_code` names the gate by hash), `reports/locate-{outcomes,tally}.<run>.*`, `exports/<set>/` |
| bench | `cf bench build / anchor / freeze / score / compare` | `benches/<id>/` |

Three rules hold for every locate stage. **Nothing about a declaration is a
worker's**: `proof_status` is re-decided at ingest by
`cflib.vocab.decide_proof_status`, joined from `corpus.sqlite` downstream, and
refused in any document a worker wrote; the commit is the universe's. **A
locate run is pinned by content**: the sourceset by `(id, version, manifest
sha)`, the universe by `universe_sha256` (a `corpus.sqlite` is matched to it by
its `meta.universe_sha256`, since two builds of one universe are not byte
identical), the renderer by its id. What its gate reads is pinned by bytes: the
CHECK database by `gate.json` `corpus_db.sha256` (so a database rebuilt after
planning needs a re-plan), each candidates file the plan read by
`candidates_sha256`, each source's layer by `layer_sha256` and the two tables
beside it by the pin's `views`; the gate compares each and stops (exit 3) on a
mismatch, and the harvest re-gates with the run's own gate copy, checked against
`run.yaml` `locate.gate_files` and run from its sources only, with a
`gate.local.json` that is the staged `gate/gate.json` but for the layout's paths
and the task manifests `runs/<run>/planned.json` records (`references/locate-documents.md` §9 and §10). A rule the gate gained after a run was planned binds the run only when its `gate.json` names the rule's version — `judgment_rules` for rule 9, `span_end_rule` for rule 2's end check on an inventory passage — which `cf plan-locate` writes into every run it plans, so any re-gate, by the run's own gate or the checkout's, judges an older run by the rules it ran under. A run names two databases: the full `corpus.sqlite`
that the gate, the outcomes, the export and the bench check, and the tools
database its workers search (`gate.json` `tools_corpus_db`, recorded in
`run.yaml` `locate.universe.tools_corpus_db`: the full one, the blind build or
a planted file; a challenge searches the tools database its challenged runs
searched). **Its state lives in the sourceset**:
`sourcesets/<id>/state.json`, written only through
`sources.sourceset.Sourceset.record_state`, replaces
`companions/<name>/state.json` for locate.

### 4.1 The four stages a Python process cannot finish

`adjudicate`, `challenge`, the local fleet and `verify-claims` run their model
work in a **Workflow script** (`workflows/*.workflow.js`), which is launched by
the Claude Code session, not by `cf`. The commands emit `args` and print the
invocation; the session runs it and hands the result back through
`--transcribe`. `SKILL.md` §"Workflow stages" is the operator-facing version of
this, and `references/workflow-patterns.md` holds the skeletons.

## 5. The substrate

Server fleets are driven by `tend` (the orchestrator) and `agentbox` (the
hosted-session bridge). Neither ships here, neither is pinned by anything in
this repo, and the version last used is **not recorded**: the only version
check is `hosts.<host>.tend_version` in a workspace's own `workspace.yaml`,
which `cf run`'s preflight compares against `tend --version` on the host and
refuses on a mismatch; with no spec recorded, preflight only prints the version
and continues (`run/remote.py::_tend_version_step`). Without them the supported
path is the local Workflow fleet under ~100 items.
`references/runbook-server.md` §0 says the same thing to an operator.
