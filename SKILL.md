---
name: companion-faithfulness
description: Audit a Lean 4 / Mathlib "book companion" repository against the sources it formalises, and produce challenged, routed findings. Use when asked to check whether formal statements faithfully match a book's definitions, lemmas, theorems and exercises; to run differential or adversarial ("attack") fleets over a companion; to adjudicate, tally, route or report faithfulness findings; or to onboard a new companion repository into an audit workspace.
---

# Companion faithfulness audits

A *companion* is a Lean repository whose declarations claim to formalise the
numbered items of one or more **sources** — textbooks, lecture notes, a
preprint. A *faithfulness* defect is a declaration that is
provable-but-not-the-source's-statement: a hypothesis the book does not have, a
quantifier the book does not have, a vacuous statement, a proxy definition, or
an item nobody formalised at all. This skill automates the audit that finds
them: discover each companion's sources, prepare them, extract each companion's
statement table, pair items per source, plan and run fleets, gate and harvest
the results, tally, adjudicate adversarially, route to owners and report with
verified numbers.

**Version one is findings-only.** It stops at challenged, routed findings and a
verified report. Authoring fix PRs, reviewing merges and camera-ready audits
are deliberately out of scope; the extension points exist but the commands do
not.

## When to use this

- "Is this Lean file faithful to Theorem 1.2.13?" for more than one or two
  items, or for a whole repository.
- Running a differential between two or more companions that share a source.
- Running an attack fleet (vacuity hunt, defect hunt) over one companion.
- Adjudicating, challenging, tallying, routing or reporting findings that a
  previous run produced.
- Onboarding a new companion repo into a workspace.

Do **not** use it to fix Lean proofs, to review a PR, or to answer a single
"does this one lemma match the book" question that a direct read settles.

## Workspaces, companions and sources

The unit of organisation is a **companion with a list of sources**, not a book
with a list of companions. A **workspace** is the directory you work in: it
holds N companions and M sources, in any arrangement. A companion is audited
against every source in its list; a **differential is possible exactly where
two companions' source lists overlap**, which `cf status` prints as one line.

One source in a companion's list is its **anchor source**: the edition whose
pagination its page anchors follow. That is not always an edition the repo
ships — a repo may commit a draft PDF for convenience and write its docstring
anchors against the published book. Editions the repo names or bundles but is
not audited against are recorded in `bundled_sources` with `role: reference`,
prepared so the page map between siblings can be checked, and audited by
nobody.

**Nothing about a source is typed in.** `cf onboard <repo>` reads the
repository, fingerprints the PDF bytes wherever they can be found on this
machine, infers the page offset and the item kinds from the text, finds the
errata page, prepares the source and runs extraction — one command, no flags in
the common case. What it cannot decide it **refuses** and names the override,
because a guessed page offset is worse than no answer: an anchor wrong by one
page is exactly the defect class this audit exists to find.

| what used to be typed | where it comes from now |
|---|---|
| which book | the repo's `formalization.yaml` sources list, its ledger `source:` block, its `graph.json` metadata, its prose and its committed PDFs (seven signals, `references/onboarding-contract.md` §0) |
| which edition | sha256 of the actual PDF bytes, looked up in `registry/sources.yaml` |
| the page offset | the mode of the running-head votes, with a confidence; below 0.9 it refuses (`cflib.inference.MIN_OFFSET_CONFIDENCE`) |
| the item kinds | the capitalised words that really head numbered items in this source's text |
| the errata URL | the registry, corroborated by the book page the repo cites; fetched with `curl`, raw HTML kept |
| the audited scope | the chapters the companions actually tag |
| the anchor edition | scoring the repo's own page anchors against each candidate edition; prose only corroborates |

Overrides exist and are the exception: `cf sources set <id> offset=16`,
`cf sources add <pdf> --for <companion>`, `cf onboard --adapter`,
`--anchor-source`.

## Two directories, never mixed

| | holds | git |
|---|---|---|
| the skill repo (`companion-faithfulness/`) | code, templates, docs, the source **registry** (metadata only) | committed, may be shared |
| the workspace (e.g. `~/Desktop/tao-workspace/`) | source text, page images, statement tables, corpus, ledgers, reports | local repo, **no remote**, source material gitignored |

Source text, page images and crops never enter the skill repo. Workspace data
is never committed to a companion repo. Companion repos are never written to at
all.

## The default program

Cheapest finders first; LLM slots spent only where mechanics cannot decide.

1. `cf init <dir>` — the workspace: `workspace.yaml`, an empty registry
   overlay, `.gitignore`, `git init` with no remote and a pre-push hook.
2. `cf onboard <repo>` — per companion, and this is the whole of stage 0:
   detect the adapter, discover the sources, fingerprint them, infer offset /
   kinds / errata / anchor edition, prepare each source (text, page map,
   `items.json`, live errata), write `adapter.yaml` + `conventions.md`, extract
   the statement table and derive the scope. Every inference is printed with
   its confidence and the file it came from.
3. **Edit `companions/<name>/conventions.md` and have the owner confirm it.**
   The one manual step that matters: an unconfirmed convention list is the
   largest single source of false positives.
4. `cf pair` — per source: coverage, def-coverage, queue, **mechanical
   page-anchor check**. Def-coverage alone reports missing and bridge-only
   definitions as COVERAGE findings.
5. `cf detect-proxies <name>` — mechanical, model-free proxy and coverage
   detectors (no cost); add `--lean` once the companion has a built `.lake`.
   Its rows are published into `corpus/detect-<name>-<tier>/` behind the
   `mechanical` gate, so they reach the tally, the ledger and the report like
   any other finding (`--no-emit-corpus` to keep them out).
6. `cf plan-run --preset attack-vac` then `--preset attack-hunt` — each attack
   run found more real defects than all three differentials combined.
7. `cf plan-run --preset differential-text` where two companions share a
   source — the only stage that sees specialisation and attribution deltas
   between repos.
8. `cf run <run-id>` → `cf harvest <run-id>` → `cf tally` — receipts, counts,
   union. (`cf import-corpus` adopts findings produced before this skill.)
9. `cf adjudicate` — lenses → reconciler → refute-by-default skeptics. Assume a
   first-pass finding is wrong about 40% of the time.
10. `cf route` — per-owner files; challenged rows only.
11. `cf report` — every number recomputed from the corpus and re-verified.

## Command index

Run everything as `python3 <skill>/scripts/cf.py <command> …` (`cf` below).
Global: `--workspace <dir>` (defaults to `$CF_WORKSPACE`, else the nearest
ancestor holding `workspace.yaml`), `-v`, `-q`. `cf --help` lists what your
checkout actually has.

| command | one line |
|---|---|
| `init <dir>` | create the workspace as a local git repo with no remote; write `workspace.yaml`, `registry.yaml`, `.gitignore`, pre-push hook. **No source flags**; only `--name`, `--no-git`, `--allow-nested`, `--force` |
| `onboard <repo>` | discover → register → prepare → extract, printing every inference with its confidence. `--name`, `--adapter`, `--anchor-source`, `--download`, `--dry-run`, `--no-errata`, `--no-extract` are the exceptions, not the path |
| `sources list` | one row per source: id, sha8, edition, offset ± confidence, kinds, items, errata, companions, role, confirmed |
| `sources show <id>` | the full record: provenance, licence, `same_work` siblings, the page map, the prepared artifacts |
| `sources add <pdf> --for <companion>` | add a source by hand; fingerprints, registers, prepares, appends to that companion's list |
| `sources set <id> key=value …` | override a discovered field (`offset=16`, `errata_url=…`, `chapters=1,2`, `kinds=…`, `confirmed=true`); sets the matching `*_method: manual` |
| `sources refresh [<id>]` | re-prepare text, page map, items and errata for one source or all of them. **This replaces the old `cf book`** |
| `extract [--companion X]` | adapters → `statements.jsonl`; every row carries `source_id` + `source_sha`; no-op when (repo commit, adapter key) is unchanged |
| `pair [--source ID]` | iterates the shared sources: one `pairs/<source_id>/` tree per source — pairs, coverage, queue, targets, def-coverage, uncovered, page-anchor differential |
| `plan-run --preset <p> [--source ID] --host <h>` | tasks, prompts, `gate.json`, tend root; a run is scoped to one source; reconciles the task count against items ∩ any-repo |
| `run <run-id> --i-have-consent` | drive the fleet (server via tend+agentbox, or a local Workflow under ~100 items) |
| `status [--companion X] [--source ID] [--json]` | the sources table, the differential line, every companion's `state.json`, and the **unconfirmed sources** block; prints nothing it did not read |
| `harvest <run-id>` | pull findings, write `RECEIPT.json` (count + sha manifest + `sources`), tally |
| `import-corpus --run-id <id> --from <dir>` | adopt an existing findings directory as a run: v1 → v2 on the way in, same receipt, source never touched |
| `tally [--union] [--agreed] [--source ID]` | recompute every finding count from `corpus/`, grouped by `(source_id, repo)`; nothing downstream may count by hand |
| `adjudicate [--companion X]` | lenses → reconciler → skeptics; challenge unchallenged rows; write ledger rows |
| `detect-proxies <name> [--source ID]` | definition coverage and (with a built Lean env) dependency-stage analysis; publishes PROXY/COVERAGE rows into `corpus/detect-<name>-<tier>/` through the `mechanical` gate |
| `route` | per-companion routed files with `_meta`, plus owner messages |
| `report` | tally + handoff + claims verification |

Deleted: `cf book` — a source is discovered, and re-prepared by
`cf sources refresh`. Out of scope in v1 (documented, not implemented): `fix`,
`review-pr`, `camera-ready`, `publish-verdicts`, `curate` (item-level curation
for `granularity: node` repos) and `lint` (repo-only mechanical detectors — the
model-free checks that exist today live in `detect-proxies` and `pair`).

## Hard rules

1. **Quote gate.** A finding that quotes a source must carry a `book_quote`
   that really occurs in that source's `sources/<source_id>/text.txt`
   (normalised, ≥25 characters). The gate runs from **outside** the tree the
   worker edits, and pre-merge also checks that the changed files are only that
   task's finding and task file. A gate the worker can edit proves nothing.
2. **Witness gate.** In attack mode every CONTENT/VACUITY finding carries a
   structured `witness` object. "It is vacuous" without an object is not a
   finding.
3. **Source gate.** Every artifact that quotes a source or judges an anchor
   carries `source_id` **and** `source_sha`. A mismatch with the workspace's
   sha for that source is refused, not warned about: batch-1 anchor findings
   were judged against a draft edition. `cf harvest` and `cf import-corpus`
   fail the receipt on a mismatch; the ONE documented override is
   `--allow-edition-mismatch` (or `--source-sha <hex>` on an import), which the
   receipt records so the report can say so.
4. **Count from the corpus.** Every number in every report is emitted by the
   tally over `corpus/`, never by reading files and counting by hand. "Nothing
   open" ships with the command that recomputes it. Counting errors have run
   toward "cleaner" every single time.
5. **Challenge before report.** Nothing leaves the workspace directory while
   `_meta.status` is `UNCHALLENGED`. Findings are never sent to an upstream
   owner, an issue tracker or a PR without an adversarial pass. Machine
   verdicts stay `confirmed_by_human: false`.
6. **Copyright boundary.** Source text, page images and crops stay in the
   gitignored `sources/` directory of the workspace. Never copy them into the
   skill repo or a companion repo. The registry holds metadata and URLs only.
   A download happens only with `--download` **and** a registry licence note
   that permits fetching from the author's own site; nothing is ever
   redistributed. Exports redact quotes to (page, hash) by default. Quote at
   most a sentence or two of a source in any artifact.
7. **Consent and allowlist for runs.** `cf run` refuses any host not in
   `workspace.yaml` `hosts.allowlist` and requires `--i-have-consent`, recorded
   in `run.yaml`. A run copies private Lean sources and copyrighted source text
   to that host; the box owner must agree first. Never point a run at a server
   the user did not name.
8. **Opus for subagents; never Fable.** Fable-default fleets lost agents to the
   session limit mid-run. Workers may be a cheaper tier only after a 1/1 smoke
   test confirms the model id.
9. **Session-limit awareness.** Local fleets die at the subscription session
   limit (129/225 in one batch, 70/114 in another). Size local work by *agent
   count*, pass every input through `args` so a resume is byte-identical, and
   treat resume as the normal path. Anything over ~100 items belongs on the
   server.
10. **Never modify a companion repo.** v1 reads companions; it does not write
    to them, build them, or touch their worktrees. Discovery is the load-
    bearing case: it *reads* `formalization.yaml`, ledgers, `CLAUDE.md`, caches
    and PDFs, and writes nothing back.
11. **Low confidence is a refusal.** An inferred offset below 0.9, an
    undecidable anchor edition (agreement under 0.80, or a margin under 0.05
    between two editions), an adapter score under 0.5: each stops and names the
    flag that settles it. Nothing is guessed silently, and every confidence it
    did accept is written into `workspace.yaml` where a reader can see it.

## Vocabulary

One set, defined in `scripts/cflib/vocab.py`, documented in
`references/vocabulary.md`:

- match verdict: `AGREE` `CONVENTION_ONLY` `CONTENT_DELTA` `SINGLE_SOURCE_OK`
  `SINGLE_SOURCE_DELTA` `NO_DEFECT_FOUND`
- finding kind: `CONTENT` `VACUITY` `PROXY` `SUSPECTED_ERRATA` `COVERAGE`
  `PAGE_ANCHOR` (mechanical only — an LLM that emits it fails the kinds gate)
- severity: `low` `medium` `high`
- ledger verdict: `CONFIRMED_CONTENT` `ATTRIBUTION_GAP` `CONVENTION` `ERRATA`
  `REFUTED` `UNSURE`
- resolution: `fixed:PR#N` `in_progress:<branch>` `refuted` `argued_correct`
  `out_of_scope`
- source role: `audited` `reference`; licence fetch: `none` `author_site`
  `open`

**An item is the pair `(source_id, item)`.** The item half follows one strict
grammar (`cflib/items.py`): `<Kind> <c>.<s>.<n>` with an optional sub-part, or
`In-text p.<n>`, where the kind list is the *source's* — inferred from its own
headings. Anything else is rejected with a reason and reported, never silently
turned into a key. Rows carry the two halves as two fields; where one string is
needed the spelling is `source_id::item` (`cflib.items.qualified_item`), and a
filename uses `qualified_slug`. A bare item string is never a cross-source key,
which is why `finding_id` hashes `(run_id, source_id, item, kind, sorted
decls)`.

## References

- `docs/design/multi-source-model.md` — the binding spec for workspaces,
  companions, sources, discovery and inference.
- `references/method.md` — why the audit works: disjoint evidence classes,
  calibration, "every bug came from a second reading".
- `references/vocabulary.md` — the enums above with mapping tables.
- `references/onboarding-contract.md` — what a repo must expose for discovery
  to succeed, and the seven obligations (Stage 0).
- `references/findings-schema-v2.md` — row, finding, ledger and task schemas.
- `references/review-patterns.md` — the pattern catalogue the lint stage runs.
- `references/traps.md` — the trap → fix index (the anchor edition is not the
  bundled edition, offset confidence, summarised errata, Workflow args, session
  limits, tend's strict schema).
- `references/runbook-server.md` — the tend + agentbox procedure.
- `references/workflow-patterns.md` — the workflow skeletons.
- `registry/README.md` — the shipped source registry and a workspace's overlay.
