---
name: companion-faithfulness
description: Audit a Lean 4 / Mathlib "book companion" repository against the textbook it formalises, and produce challenged, routed findings. Use when asked to check whether formal statements faithfully match a book's definitions, lemmas, theorems and exercises; to run differential or adversarial ("attack") fleets over a companion; to adjudicate, tally, route or report faithfulness findings; or to onboard a new companion or a new book into an existing audit campaign.
---

# Companion faithfulness audits

A *companion* is a Lean repository whose declarations claim to formalise a
specific book's numbered items. A *faithfulness* defect is a declaration that
is provable-but-not-the-book's-statement: a hypothesis the book does not have,
a quantifier the book does not have, a vacuous statement, a proxy definition,
or an item nobody formalised at all. This skill automates the audit that finds
them: extract each companion's statement table, prepare the book, pair items
across companions, plan and run fleets, gate and harvest the results, tally,
adjudicate adversarially, route to owners and report with verified numbers.

**Version one is findings-only.** It stops at challenged, routed findings and a
verified report. Authoring fix PRs, reviewing merges and camera-ready audits
are deliberately out of scope; the extension points exist but the commands do
not.

## When to use this

- "Is this Lean file faithful to Theorem 1.2.13?" for more than one or two
  items, or for a whole repository.
- Running a differential between two or more companions of the same book.
- Running an attack fleet (vacuity hunt, defect hunt) over one companion.
- Adjudicating, challenging, tallying, routing or reporting findings that a
  previous run produced.
- Onboarding a new companion repo or a new book into a campaign.

Do **not** use it to fix Lean proofs, to review a PR, or to answer a single
"does this one lemma match the book" question that a direct read settles.

## Two directories, never mixed

| | holds | git |
|---|---|---|
| the skill repo (`companion-faithfulness/`) | code, templates, docs | committed, may be shared |
| the campaign dir (e.g. `~/Desktop/tao-differential/`) | book text, page images, statement tables, corpus, ledgers, reports | local repo, **no remote**, book material gitignored |

Book text, page images and crops never enter the skill repo. Campaign data is
never committed to a companion repo. One campaign per book; several companions
of that book share it.

## The default program per companion

Cheapest finders first; LLM slots spent only where mechanics cannot decide.

1. `cf onboard <name>` — adapter, conventions, dry-run counts.
2. `cf book <id>` — pin the edition, text, page map, items, live errata.
3. `cf extract` — statement table (`statements.jsonl`).
4. `cf lint <name>` — mechanical, book-free detectors (no model, no cost).
5. `cf pair` — coverage, def-coverage, queue, **mechanical page-anchor check**.
   Def-coverage alone reports missing and bridge-only definitions as COVERAGE
   findings.
6. `cf plan-run --mode attack:vac` then `--mode attack:hunt` — each attack run
   found more real defects than all three differentials combined.
7. `cf plan-run --mode differential` where two or more companions share items —
   the only stage that sees specialisation and attribution deltas between
   repos.
8. `cf run <run-id>` → `cf harvest <run-id>` — receipts, tally, union.
9. `cf adjudicate` — lenses → reconciler → refute-by-default skeptics. Assume a
   first-pass finding is wrong about 40% of the time.
10. `cf route` — per-owner files; challenged rows only.
11. `cf report` — every number recomputed from the corpus and re-verified.

## Command index

Run everything as `python3 <skill>/scripts/cf.py <command> …` (`cf` below).
Global: `--campaign <dir>` (defaults to `$CF_CAMPAIGN`, else the nearest
ancestor holding `campaign.yaml`), `-v`, `-q`. `cf --help` lists what your
checkout actually has.

| command | one line |
|---|---|
| `init <dir> --book <id> --pdf <p> --offset <n> --errata-url <u>` | create the campaign dir as a local git repo, pin the edition sha, write `campaign.yaml` |
| `onboard <name> --repo <path> --adapter <kind>` | write `adapter.yaml` + `conventions.md`, dry-run the parser, write `state.json` |
| `book <id>` | pdftotext + page map + `items.json` + live errata (curl, raw HTML kept) + optional page images |
| `extract [--companion X]` | adapters → `statements.jsonl`; no-op when (repo commit, adapter key) is unchanged |
| `curate <name>` | item-level curation for `granularity: node` repos, before extraction means anything |
| `lint <name>` | mechanical detectors over the repo alone: lost exercise sorries, anchor hygiene, pattern catalogue |
| `pair` | pairs, coverage, queue, targets, def-coverage, uncovered, page-anchor differential |
| `plan-run --mode <m> --host <h>` | tasks, prompts, `gate.json`, tend root; reconciles the task count against items ∩ any-repo |
| `run <run-id> --i-have-consent` | drive the fleet (server via tend+agentbox, or a local Workflow under ~100 items) |
| `status [--companion X] [--json]` | print every companion's `state.json`; prints nothing it did not read from state |
| `harvest <run-id>` | pull findings, write `RECEIPT.json` (count + sha manifest), commit the corpus, tally |
| `adjudicate [--companion X]` | lenses → reconciler → skeptics; challenge unchallenged rows; write ledger rows |
| `detect-proxies <name>` | definition coverage and (with a built Lean env) dependency-stage analysis |
| `route` | per-companion routed files with `_meta`, plus owner messages |
| `report` | tally + handoff + claims verification |

Out of scope in v1 (documented, not implemented): `fix`, `review-pr`,
`camera-ready`, `publish-verdicts`.

## Hard rules

1. **Quote gate.** A finding that quotes the book must carry a `book_quote`
   that really occurs in `book.txt` (normalised, ≥25 characters). The gate runs
   from **outside** the tree the worker edits, and pre-merge also checks that
   the changed files are only that task's finding and task file. A gate the
   worker can edit proves nothing.
2. **Witness gate.** In attack mode every CONTENT/VACUITY finding carries a
   structured `witness` object. "It is vacuous" without an object is not a
   finding.
3. **Edition gate.** Every artifact that quotes the book or judges an anchor
   carries `book_sha`. A mismatch is refused, not warned about: batch-1 anchor
   findings were judged against a draft edition.
4. **Count from the corpus.** Every number in every report is emitted by the
   tally over `corpus/`, never by reading files and counting by hand. "Nothing
   open" ships with the command that recomputes it. Counting errors have run
   toward "cleaner" every single time.
5. **Challenge before report.** Nothing leaves the campaign directory while
   `_meta.status` is `UNCHALLENGED`. Findings are never sent to an upstream
   owner, an issue tracker or a PR without an adversarial pass. Machine
   verdicts stay `confirmed_by_human: false`.
6. **Copyright boundary.** Book text, page images and crops stay in the
   gitignored `book/` directory of the campaign. Never copy them into the skill
   repo or a companion repo. Exports redact quotes to (page, hash) by default.
   Quote at most a sentence or two of the book in any artifact.
7. **Consent and allowlist for runs.** `cf run` refuses any host not in
   `campaign.yaml` `hosts.allowlist` and requires `--i-have-consent`, recorded
   in `run.yaml`. A run copies private Lean sources and copyrighted book text to
   that host; the box owner must agree first. Never point a run at a server the
   user did not name.
8. **Opus for subagents; never Fable.** Fable-default fleets lost agents to the
   session limit mid-run. Workers may be a cheaper tier only after a 1/1 smoke
   test confirms the model id.
9. **Session-limit awareness.** Local fleets die at the subscription session
   limit (129/225 in one batch, 70/114 in another). Size local work by *agent
   count*, pass every input through `args` so a resume is byte-identical, and
   treat resume as the normal path. Anything over ~100 items belongs on the
   server.
10. **Never modify a companion repo.** v1 reads companions; it does not write
    to them, build them, or touch their worktrees.

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

Item ids follow one strict grammar (`cflib/items.py`): `<Kind> <c>.<s>.<n>`
with an optional sub-part, or `In-text p.<n>`. Anything else is rejected with a
reason and reported — never silently turned into a key.

## References

- `references/method.md` — why the audit works: disjoint evidence classes,
  calibration, "every bug came from a second reading".
- `references/vocabulary.md` — the enums above with mapping tables.
- `references/onboarding-contract.md` — what a repo must provide (Stage 0).
- `references/findings-schema-v2.md` — row, finding, ledger and task schemas.
- `references/review-patterns.md` — the pattern catalogue the lint stage runs.
- `references/traps.md` — the trap → fix index (editions, offsets, summarised
  errata, Workflow args, session limits, tend's strict schema).
- `references/runbook-server.md` — the tend + agentbox procedure.
- `references/workflow-patterns.md` — the workflow skeletons.
