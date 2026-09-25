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
  `confirmed: false`). See `registry/README.md`.
- A **companion** is a Lean repository whose declarations claim to formalise a
  source's numbered items. A companion is audited against a **list** of
  sources; one of them is its **anchor source**, the edition whose pagination
  its page anchors follow.
- A **workspace** is the directory the operator works in: N companions, M
  sources, in any arrangement. A **differential** is possible exactly where two
  companions' source lists overlap.
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
`In-text p.<n>`. The kind list is the **source's**, inferred from its own
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
                              statements.jsonl, state.json
  pairs/<source_id>/          committed: pairs.jsonl, coverage.md, index.json,
                              reconciliation.json, rejected.json, queue.json,
                              targets.<name>.json, def-coverage.<name>.json,
                              uncovered.<name>.json, anchor-check.json
  runs/<run-id>/              run.yaml, tasks/, prompts/, gate.json,
                              tend-root/ (gitignored)
  corpus/<run-id>/            findings + RECEIPT.json  (committed: the baseline)
  ledger/                     accepted.<name>.jsonl, refuted.<name>.jsonl,
                              adjudicated.<name>.md, routed/findings.<name>.jsonl
  detect/<name>/              proxy-detection artifacts
  reports/                    tally.<run>.json, union.<name>.json,
                              agreed_high.json, coverage.json, score.<run>.json,
                              pin.<run>.json, recheck.<name>.json,
                              handoff.<date>.md
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
  are in the corpus and are counted like any others.
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
  the companion repo. An entry that still reads as emptied — `, ,`,
  `should be ,`, an empty quotation, an unrecoverable formula, or one that kept
  under 90% of the characters the raw entry carried — is recorded in
  `errata.json` (`suspect`) and warned about by name. `cf` does not refuse the
  page over it: the other entries are real and the raw HTML is on disk, but a
  stripped entry reads as grammatical English with the substance removed, so it
  is never passed on silently.
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
| 2 | `cf extract` | `companions/<name>/statements.jsonl` — no-op when (repo commit, adapter key) is unchanged |
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

Three stages carry a gate of their own, and each one is a number that was once
wrong. The module docstrings cite these sentences, so they move with the code:

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
