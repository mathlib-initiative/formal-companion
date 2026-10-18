---
name: companion-faithfulness
description: Audit a Lean 4 / Mathlib "book companion" repository against the sources it formalizes — does the Lean actually match the book? — and produce challenged, routed findings. Use for a faithfulness or autoformalization audit of a companion, a blueprint or a formalization repo against a textbook, lecture notes or a preprint; for statement-level checks of whether definitions, lemmas, theorems and exercises say what the source says; to run differential or adversarial ("attack") fleets over a companion; to adjudicate, tally, route or report faithfulness findings; or to onboard a new companion repository into an audit workspace; and to locate which passages of a source (a Wikipedia article) which Lean library formalizes. It is not a proof checker, not a Lean linter and not a fixer — the audit reads repositories and reports findings, and never edits Lean or repairs a proof; the one command that writes into a companion is `cf overlay`, and it writes only pdf-overlay/ and annotations/.
---

# Companion faithfulness audits

A *companion* is a Lean repository whose declarations claim to formalize the
numbered items of one or more **sources** — textbooks, lecture notes, a
preprint. A *faithfulness* defect is a declaration that is
provable-but-not-the-source's-statement: a hypothesis the book does not have, a
quantifier the book does not have, a vacuous statement, a proxy definition, or
an item nobody formalized at all. This skill automates the audit that finds
them: discover each companion's sources, prepare them, extract each companion's
statement table, pair items per source, plan and run fleets, gate and harvest
the results, tally, adjudicate adversarially, route to owners and report with
verified numbers.

**The audit is findings-only.** It stops at challenged, routed findings and a
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
- Locating where a source's passages are formalized across a universe of Lean
  libraries (Mathlib, core, Batteries and others), and scoring that against gold.

Do **not** use it to fix Lean proofs, to review a PR, or to answer a single
"does this one lemma match the book" question that a direct read settles.

## Before you start

- The **skill directory** is the directory holding this file; everything below
  writes it `<skill>`. Set it once — `CF=<skill>/scripts/cf.py`, then
  `python3 $CF <command>` — or put the shim on PATH
  (`ln -s <skill>/bin/cf ~/.local/bin/cf`) and write `cf <command>`, which is
  the spelling the commands themselves print. `README.md` has the install.
- **Python 3.11+**, standard library only (PyYAML is used when importable).
- On PATH: `git`; `pdftotext` and `pdftoppm` (poppler) to prepare a PDF source;
  `curl` for errata. A `.tex`/`.md`/`.txt` manuscript needs no poppler.
- **`cf doctor`** checks all of that, prints what is missing and lists the PDF
  search roots. It needs no workspace: run it first on a new machine.
- A **workspace** directory (`cf init <dir>`), outside every companion repo.
- For a server fleet: an allowlisted host, the box owner's **consent**, and
  `tend` + `agentbox` on that host (see Substrate).

## Workspaces, companions and sources

The unit of organization is a **companion with a list of sources**, not a book
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

A source need not be a PDF. A manuscript that is already text (`.txt .md
.markdown .tex .html .htm .rst`) is a source too, located by heading path and
line instead of by printed page, and a **leanblueprint** repo's manuscript *is*
its `.tex` tree, which the `blueprint_tex` adapter claims for it. Caveat for a
mixed repo that ships both a blueprint and a real book: the paginated book wins
the anchor election, the manuscript registers as a second audited source that
receives no rows, and `cf onboard` warns that the tags were attributed to the
anchor.

**Nothing about a source is typed in.** `cf onboard <repo>` reads the
repository, fingerprints the source bytes wherever they can be found on this
machine, infers the page offset and the item kinds from the text, finds the
errata page, prepares the source and runs extraction — one command, no flags in
the common case. What it cannot decide it **refuses** and names the override,
because a guessed page offset is worse than no answer: an anchor wrong by one
page is exactly the defect class this audit exists to find.

| what used to be typed | where it comes from now |
|---|---|
| which book | the repo's `formalization.yaml` sources list, its ledger `source:` block, its `graph.json` metadata, its prose and its committed PDFs (seven repo signals, `references/onboarding-contract.md` §0) plus whatever the adapter itself claims — a leanblueprint's manuscript IS its `.tex` tree, so its adapter claims it |
| which edition | sha256 of the actual source bytes, looked up in `registry/sources.yaml` |
| the page offset | the mode of the running-head votes, with a confidence; below 0.9 it refuses (`cflib.inference.MIN_OFFSET_CONFIDENCE`) |
| the item kinds | the capitalized words that really head numbered items in this source's text (case-folded, so an AMS `THEOREM 1.2` head counts) |
| the numbering depth | how many numeric segments this source's own headings carry, stored as `numbering`; a deeper label is a reject, never a truncation (`cflib.inference.infer_numbering`) |
| whether the table is the book | items found against the slots the numbering itself counts; below 0.6 it refuses (`cflib.inference.MIN_ITEM_COMPLETENESS`) |
| the errata URL | the registry, corroborated by the book page the repo cites; fetched with `curl`, raw HTML kept. A URL the REPO supplies waits for `--fetch-errata`: the page reaches workers as authoritative text. The render keeps the **mathematics** — a formula served as an `<img>` becomes its TeX — and names any entry that still reads as emptied |
| the audited scope | the chapters the companions actually tag |
| the anchor edition | scoring the repo's own page anchors against each candidate edition; prose only corroborates |

Overrides exist and are the exception: `cf sources set <id> offset=16`,
`cf sources set <id> numbering=3,4`, `cf sources set <id> completeness_ok=true`,
`cf sources add <source> --for <companion>`, `cf onboard --source`,
`cf onboard --adapter`, `--anchor-source`.

## Two directories, never mixed

| | holds | git |
|---|---|---|
| the skill repo (`formal-companion/`) | code, templates, docs, the source **registry** (metadata only) | committed, may be shared |
| the workspace | source text, page images, statement tables, corpus, ledgers, reports | local repo, **no remote**, source material gitignored |

Source text, page images and crops never enter the skill repo. Workspace data
is never committed to a companion repo. Companion repos are never written to at
all. Staying local is not the same as being unshareable: `cf share export`
hands over the campaign's conclusions — ledger rows, routed findings, tallies,
receipts, source metadata — with every quote redacted to (page, normalized
length, sha256, and at most a quarter of the quote's words from each end, none
at all below fourteen words).

## The default program

Cheapest finders first; LLM slots spent only where mechanics cannot decide.

1. `cf init <dir>` — the workspace: `workspace.yaml`, an empty registry
   overlay, `.gitignore`, `git init` with no remote and a pre-push reminder.
2. `cf onboard <repo>` — per companion, and this is the whole of stage 0:
   detect the adapter, discover the sources, fingerprint them, infer offset /
   kinds / errata / anchor edition, prepare each source (text, page map,
   `items.json`, live errata), write `adapter.yaml` + `conventions.md`, extract
   the statement table and derive the scope. Every inference is printed with
   its confidence and the file it came from.
3. **Edit `companions/<name>/conventions.md` and have the owner confirm it.**
   The one manual step that matters: an unconfirmed convention list is the
   largest single source of false positives, and `cf plan-run` refuses a
   companion whose file carries no `confirmed_by:` stamp or has no filled
   section — `--conventions-unconfirmed` overrides it, and is recorded in
   `run.yaml`.
4. `cf pair` — per source: coverage, def-coverage, queue, **mechanical
   page-anchor check**. Def-coverage alone reports missing and bridge-only
   definitions as COVERAGE findings.
5. `cf detect-proxies <name>` — mechanical, model-free proxy and coverage
   detectors (no cost); add `--lean` once the companion has a built `.lake`.
   Its rows are published into `corpus/detect-<name>-<tier>/` behind the
   `mechanical` gate, so they reach the tally, the ledger and the report like
   any other finding (`--no-emit-corpus` to keep them out).
6. `cf plan-run --preset attack-vac` then `--preset attack-hunt` — each attack
   run found more real defects than all three differentials combined (5 runs,
   one chapter, one repo; the ratio is partly definitional, because the attack
   gate bans PAGE_ANCHOR).
7. `cf plan-run --preset differential-text` where two companions share a
   source — the only stage that sees specialisation and attribution deltas
   between repos.
8. `cf run <run-id> --i-have-consent --execute` → `cf harvest <run-id>` →
   `cf tally` — receipts, counts, union. **`cf run` is a dry run until
   `--execute`**: without it the plan is printed and nothing is transferred or
   started. `cf harvest` runs the findings **gate by default** — quote, witness
   and kinds, against the pinned `text.txt` — and fails the receipt when the
   gate fails. `--no-gate` is the documented escape; it records `gate: null`,
   and `cf tally` then names that run as ungated every time it counts it.
9. `cf adjudicate` — lenses → reconciler → refute-by-default skeptics. Assume a
   first-pass finding is wrong about 40% of the time (4 of 10 adjudicated rows,
   one chapter of one book). The model work runs in a Workflow, not inside
   `cf`: see **Workflow stages**.
10. `cf route` — per-owner files; challenged rows only.
11. `cf report` — every number recomputed from the corpus and re-verified.

`cf import-corpus` adopts findings produced before this skill, with the same
receipt and the same gate. A directory this workspace never planned has no gate
config to build, so it lands `gate: null` and warns; re-harvest it with
`--gate-config` when you want it checked.

## The locate program

The inverse question — which passages of a source are formalized, in which
library, at which commit (`docs/proposals/wikipedia-locate.md`; formats in
`references/locate-documents.md`). A library is never onboarded as a companion.

1. `cf corpus init <u>`, `cf corpus add <u> --library … --commit <40 hex>`, then
   on the host the pipeline `cf corpus probe-command` prints (**cf never builds a
   library**), `cf corpus ingest` (refused until the toolchain's canary passed)
   and `cf corpus build-db <u>` (`--blind` too, for the G1-blind arm).
2. `cf sourceset create <s>`, `enumerate-wikiproject` / `adopt`, `fetch` and
   `render` — append-only manifests; nothing reaches Wikipedia without `--execute`.
3. `cf locate candidates --sourceset <s> --universe <u>` — L2's curated joins and
   lexical hits, per source.
4. `cf plan-locate --preset locate-inventory`, then `--preset locate
   --inventory-run <r>`, then `--preset locate-challenge --challenge-of <r>`; each
   run goes through `cf run` (a spend cap is required) and `cf harvest`, whose
   locate gate runs out of tree over each task's own documents. Larger work is a
   `cf campaign` of shards.
5. `cf tally --locate --run <r> --challenge-run <c>` — passage outcomes and the
   derived status; then `cf export literature-links` — a sealed record set,
   every record checked by the vendored Literature Links checker, quotes capped
   and only from an open licence class.
6. `cf bench build | anchor | freeze | score | compare` — gold, metrics with
   article-clustered intervals, and the acceptance rule; labels never go to the
   host.

## Command index

Run everything as `cf <command> …`, or as
`python3 <skill>/scripts/cf.py <command> …` without the PATH shim. Global:
`--workspace <dir>` (defaults to `$CF_WORKSPACE`, else the nearest ancestor
holding `workspace.yaml`), `-v`, `-q`. `cf --help` lists what your checkout
actually has, and is generated from the parsers themselves.

| command | one line |
|---|---|
| `doctor [--json]` | check this machine's prerequisites (python floor, git, poppler's `pdftotext`/`pdftoppm`, `curl`, optional node and PyYAML) and print the PDF search roots. Exit 2 when a required one is missing, 0 when only optional ones are. Needs no workspace |
| `init <dir>` | create the workspace as a local git repo with no remote; write `workspace.yaml`, `registry.yaml`, `.gitignore`, pre-push hook. **No source flags**; only `--name`, `--no-git`, `--no-remote-guard`, `--allow-nested`, `--force` |
| `onboard <repo>` | discover → register → prepare → extract, printing every inference with its confidence. `--name`, `--adapter`, `--source` (names a PDF or text manuscript the repo does not name; repeatable), `--anchor-source`, `--download`, `--fetch-errata`, `--dry-run`, `--no-errata`, `--no-extract` are the exceptions, not the path. The repo is read as data: never written, never built, and never allowed to name a program (`cflib.gitsafe`) |
| `sources list` | one row per source: id, sha8, edition, offset ± confidence, kinds, items, errata, companions, role, confirmed |
| `sources show <id>` | the full record: provenance, licence, `same_work` siblings, the page map, the prepared artifacts |
| `sources add <source> --for <companion>` | add a source by hand; fingerprints, registers, prepares, appends to that companion's list. A source is a PDF or a manuscript that is already text (`.txt .md .tex .html`), which is located by heading path instead of printed page |
| `sources set <id> key=value …` | override a discovered field (`offset=16`, `errata_url=…`, `chapters=1,2`, `kinds=…`, `numbering=3,4`, `completeness_ok=true`, `confirmed=true`); sets the matching `*_method: manual` |
| `sources remove <id>` | drop a source that should never have been registered: workspace.yaml, the overlay row and the prepared directory. Loads even a workspace its own validator rejects — it is the repair path |
| `sources refresh [<id>]` | re-prepare text, page map, items and errata for one source or all of them; re-derives `errata.md` offline when the converter has moved on, `--no-errata` included. **This replaces the deleted `cf book`** |
| `sources validate-registry [--overlay]` | check the shipped registry (and with `--overlay` this workspace's): required fields, sha shape, licence enum, page-map symmetry, and **no free text outside the allowlisted fields** — the mechanical half of "metadata only, never a line of the book". CI runs it on every push |
| `sources promote <id>` | turn a confirmed workspace source into a **registry patch file** to send as a PR. It never edits the shipped registry in place from a workspace |
| `extract [--companion X]` | adapters → `statements.jsonl`; every row carries `source_id` + `source_sha`; no-op when (repo commit, adapter key) is unchanged. An export-driven adapter cross-checks its table against the Lean first, warns by name about the declarations the export omits (or, under `lean_scan.policy: off`, that no check ran), and writes the ones it cannot key on an item to `unattributed.jsonl` |
| `pair [--source ID]` | iterates the shared sources: one `pairs/<source_id>/` tree per source — pairs, coverage, queue, targets, def-coverage, uncovered, page-anchor differential |
| `plan-run --preset <p> [--source ID] --host <h> [--chapters 1] [--sections 1.7]` | tasks, prompts, `gate.json`, tend root; a run is scoped to one source and can be narrowed to chapters or to `<chapter>.<section>` labels (both narrow the entries and the reconciled universe alike); reconciles the task count against items ∩ any-repo. Refuses an unconfirmed `conventions.md`; `--allow-sandbox-bypass` is the recorded opt-in that stages the agent permission bypass |
| `plan-locate --preset <p> --sourceset S --universe U` | a **locate** run (presets `locate-inventory`, `locate`, `locate-challenge`; `references/locate-documents.md` §9), scoped to a sourceset pinned at one manifest version and one universe, never to a companion: one inventory task per source of a concept article (a page flagged redirect, disambiguation, list or biography — by its flags, or where it recorded none by its assessment class — is left out and named unless `--source` names it or `--include-flagged`), tasks of at most 8 `formal_math` passages of a frozen inventory (`--inventory-run`; `--subject-only` is the article tier), or the §5 challenge batches over `--challenge-of` runs (`challenge-plan.json`, seeded sample). At most 500 tasks a root; writes tasks, task manifests, prompts, `gate.json` and a tend root with the locate gate directory, and pins the skill commit, every staged file's sha and the arm in `run.yaml`. `--blind` makes the workers search `corpus.blind.sqlite`, `--tools-corpus-db` a planted database; the gate always checks the full one. The worker and reviewer limits are `--workers`/`--reviewers`, else the workspace's `defaults.max_concurrent_*`, else the preset's |
| `locate candidates --sourceset S --universe U` | L2: curated joins (a library's `@[wikidata]` tag on the article's QID, `--curated-1000`/`--curated-100` lists, Wikipedia URLs in docstrings, `--library-metadata`), each checked in `corpus.sqlite`'s exact table, plus lexical search over the title and bold terms. The curated lists name `--curated-library` (default: the universe's Mathlib); `--blind` retrieves from `corpus.blind.sqlite` and writes no curated hit (the G1-blind arm), `--tools-corpus-db` from a planted database, each into its own candidates directory |
| `run <run-id> --i-have-consent` | transfer a planned run to its host and drive the fleet — **a dry run that only prints the plan until `--execute`**. `--local` drives the Workflow fleet instead (under ~100 items); `--status` reads progress back. A locate run never runs `--local`, needs one spend cap (`run.yaml`'s, or an equal `--max-cost`), sends only its own sources, `corpus.sqlite` (and the tools database when it differs), this run's candidates and inventory, checks the databases' shas once, and records the tend, agentbox and agent CLI versions |
| `campaign plan \| render \| status` | sequence already-planned locate shards (≤ 500 tasks each, each capped) on one host: `campaigns/<id>/campaign.yaml`, a rendered host-side sequencer that runs shard k+1 after shard k beside a model-free spend watchdog (drain at the cap or a multiple of the pilot's p90), exports usage and prunes transcripts per shard, and a status table read from the shards' receipts. `plan --pilot <run>` names the smoke-tested run whose `run.smoke_verified` stands for every shard's, and `render` requires one (each shard goes only as far as `cf run <shard> --stage verify`); `--transcript-dirs` and `--canaries` name the host's transcript directories and the bench's label canaries. Nothing contacts the host |
| `status [--companion X] [--source ID] [--runs] [--json]` | the sources table, every sourceset's row from its `state.json` (locate's web sources), the differential line, every companion's `state.json`, the **unconfirmed sources** block, an errata file an older converter rendered or whose entries look emptied, and whether the workspace repo has a remote and still carries the pre-push reminder; prints nothing it did not read |
| `harvest <run-id>` | bring a run's findings into `corpus/<run-id>/` (recursively: a locate run lands `<task-id>/<source-id>.json`, is gated by `scripts/gate/verify_locate.py`, its receipt counts UNRESOLVED documents and passages apart, a remote harvest also brings the host's gate log, usage export and canary scan to `runs/<run-id>/host/`, and the run is recorded in the sourceset's `state.json`), run the findings **gate (the default; `--no-gate` records `gate: null`)**, write `RECEIPT.json` (count + sha manifest + `sources` + the gate block) and recompute the tally. `--transcripts <path>` prices the run from the agents' Claude Code transcripts or Codex rollout logs (or a `cf cost --export` file) with `workspace.yaml` `budget.prices` and writes the `cost` block: tokens by model, dollars, priced and unpriced models, the price table's sha, transcript and message counts |
| `cost <path>` | price a directory of Claude Code transcripts or Codex rollout logs, or a usage export, by model id: tokens and dollars per model, unpriced models named and never guessed at, and which price table did it (`budget.prices`, `--prices <file>`, or the shipped default outside a workspace). `--export <file>` writes one `{model, usage}` row per response so the usage can leave a host without the transcripts' text |
| `ingest --pdf <file> --scope chapters=1 --pipeline <id>` | pipeline stage S0 (`docs/pipeline.md`): pin one PDF (sha256, pages, page sizes, text layer version), infer the offset (below 0.9 it stops and names `--offset`), refuse a registry `draft` or `superseded` edition without `--override-edition` and a `--source-id` the registry pins to another sha, record an unknown sha as a pending edition gate, fetch the errata raw (`--errata`, `--no-errata`), create the generated repository with one commit, and write `pipeline.yaml`, `doc.v0.json` and the S0 receipt. The PDF and its text never enter git or the document |
| `inventory --pipeline <id>` | pipeline stage S1: numbered items with page spans and exact heading quotes, erratum items linked by `same_as`, passages from `--passages <file>` (accepted only when the whole quote is on its page) and a labelled heuristic finder, `relations.near`; gates for slot completeness, slot contiguity per counter (`--slot-reasons`), the exact selector check, unique ids and the 12-word cap; `doc.v1.json` and the S1 receipt only when all pass |
| `import-corpus --run-id <id> --from <dir> [--repo-map ours=<companion>]` | adopt an existing findings directory as a run: v1 → v2 on the way in, same receipt and gate, source never touched. `--repo-map` states which of this workspace's companions a foreign repo name is — the union key is `(source_id, repo, item, kind)`, so without it the imported rows join nothing here, and the import warns |
| `tally [--union] [--agreed] [--source ID]` | recompute every finding count from `corpus/`, grouped by `(source_id, repo)`, and name any ungated run, or run whose gate rejected it, that it counted; nothing downstream may count by hand. Prints each run's cost and cost per task from its receipt's `cost` block, and names the runs that have none. `--locate --run R [--challenge-run C]` tallies one locate run instead: its finding rows, passage outcomes and derived statuses, unresolved counted on its own (`reports/locate-outcomes.<run>.jsonl`) |
| `score --answer-key <file> [--run <id>]` | precision / recall / F1 against an answer key — the only measurement here with a **recall** denominator, because a defect nobody raised leaves no artifact behind. `tests/e2e/answer-key.json` ships one for the synthetic fixture |
| `bench build \| anchor \| freeze \| score \| compare` | the locate benchmark (`references/locate-bench.md`): `build` draws the frame, clusters, splits by cluster hash and samples G3 spans, with label-free host manifests; `anchor` resolves curated G1 at the universe's pin and runs the frozen labeler's emit/transcribe; `freeze` writes the irreversible gold manifest (`--plant` the ablated index); `score` gives every §8.2 metric with cluster-bootstrap intervals and a `measurement_id`, reading each run's harvested `runs/<run>/host/` gate log and canary scan by default and refusing an incomplete receipt, a canary in a transcript or a planted run whose tools database is not this bench's planted one; `compare` applies the §9 acceptance rule (`--replication BENCH:ENTRY` replicates an accepted claim on a fresh draw) and writes the loop ledger. Never touches `cf score` |
| `adjudicate [--companion X]` | **prepare, transcribe and validate** the adjudication. With no flags it prints how many rows are still unchallenged and the next command; `--emit-args` writes the Workflow args and prints the invocation *you* run; `--transcribe <file> --from {adjudicate,challenge}` turns the result into ledger rows; `--validate` machine-checks the ledger against the corpus. The lenses, reconciler and skeptics run in the Workflow, not in this process |
| `recheck [--companion X]` | re-extract the companion's statements at its **current** commit and label every open finding `unchanged` / `changed` / `gone` / `unpinned` against the `repo_commit` + `decl_fingerprints` it was pinned to. Writes `reports/recheck.<companion>.json` and a `recheck` block on the ledger rows; **changes no verdict** and calls no model |
| `detect-proxies <name> [--source ID]` | definition coverage and (with a built Lean env) dependency-stage analysis; publishes PROXY/COVERAGE rows into `corpus/detect-<name>-<tier>/` through the `mechanical` gate. It never runs `lake`: without the dump it prints the command and stops |
| `corpus init \| add \| probe-command \| ingest \| build-db \| status \| search \| show` | locate's universe of Lean libraries (`references/locate-documents.md` §8): pin libraries at 40-hex commits in `corpora/<u>/universe.yaml` (`add --exclude <reason>` records one that failed), print the host build-and-probe pipeline (`probe-command`, from each library's `--host-kind`/`--host-ref`/`--host-option`: a build, a published index, Lean core or a package inside a built workspace; **cf never builds a library**), ingest a probe index streamed with every proof status re-decided — refused until the toolchain's acceptance canary passed (`ingest --canary`), and a library's own published index only with `--accept-published <reason>` — and build `corpus.sqlite` (exact table, FTS5, tags; `build-db --blind` the G1-blind copy with no tags or docstring Wikipedia links) for word `search` and `show`. Every subcommand takes `--base <dir>` to work on `<dir>/<universe>/` with no workspace (a universe kept on the run host) |
| `route` | per-companion routed files with `_meta`, plus owner-message drafts. `--github` writes one draft issue body per accepted finding and the exact `gh` command line for each — it calls no network. How much of a quote a draft carries belongs to the **destination**: `--destination owner` the sentence, `--destination public` the printed page, a hash and at most a quarter of the quote's words at each end (none at all below fourteen words), with every other free-text field scrubbed of verbatim runs of the source, configured under `policy.destinations` and never inferred from a git remote |
| `report` | tally + coverage + handoff + the claims-verification args |
| `share export \| import` | hand a campaign to somebody else without handing over the book: ledger rows, routed findings, tallies, receipts and source metadata, every quote redacted. It refuses a companion whose routed `_meta.status` is not CHALLENGED unless `--skip-unchallenged` leaves those rows behind |
| `quotes hydrate` | put a redacted quote's text back from **your own** copy of that edition, verified by hash. Three outcomes, all counted: hydrated, not-found, ambiguous |
| `export attribution-table <checkout> --commit <sha> --out <file>` | write a PDF-overlay companion's geometry, target refinement, ledger, destinations and verdicts, with snippets cut from its Lean, as one annotation document (`docs/annotations.md`), read-only at the pinned commit, beside a report of what did not fit; `--lean-build <dir>` names a build whose `.ilean` files name anonymous instances. Needs no workspace; a document of a private companion belongs to its repo or a workspace, never this one; `docs/exporters.md` |
| `export docstring-tags <checkout> --commit <sha> [--pdf <file>] [--xref <file> --site-pages <file> [--site <dir>]] --out <file>` | the same for a docstring-tagged companion: the tags through the `docstring_tags` adapter, headings and quotes located in the PDF's text layer, the literate site's index and page manifest as one site source; with `--site` every anchor is checked against its page. Without `--pdf` the edition is a `pdf` source pinned by its identifiers (`--source-doi`, `--source-isbn`; an `other` source with `--source-media-type` when it has none), named by `--source-id`, `--source-title` and `--source-revision`, and numbered items are located by their numbers alone; without `--xref`/`--site-pages` there is no site source |
| `export site-pages <site> --out <file> [--source-id ID] [--title T] [--path P] [--rendered-at TIME] [--exclude GLOB]...` | write a rendered literate site's page manifest, the pin `export docstring-tags --site-pages` reads: every HTML page with its sha256 and bytes, less those an `--exclude` glob matches (generated fragments, `'*/_lazy/*'`), and the site digest; reads no checkout |
| `export literature-links --run R --challenge-run C --set-id ID` | a locate run's outcomes as a **sealed record set** (`references/locate-documents.md` §6): Literature Links 2.0 records checked with the vendored checker, capped quotes, absences with their receipts as sidecars, `SEAL.json`; refuses, by name, every passage whose challenge is owed, a source whose licence class is closed and any source over `--byte-budget`; `--out` writes only into a new or empty directory or an earlier set. Needs a workspace |
| `review-tags <document> [--json]` | the known attribution defects a document's key links carry: items keyed only to where they sit, keys on a structure field or projection, keys borrowed from a neighbouring item or shared with no owner, book-content tag spellings a fold script hides, the document's own attribution-gap verdicts; `--keep-pattern` and `--hide-pattern` are the fold script's rules |
| `sourceset create \| enumerate-wikiproject \| adopt \| fetch \| flag-biographies \| render \| status` | locate L1: a **sourceset** of web sources (`references/locate-documents.md` §7) — append-only manifest versions, one directory per page revision. `create --from <sourceset>` seeds a new one with another's sources and bytes and no layers (the way to a new renderer). `enumerate-wikiproject --project P` lists a WikiProject's articles at their latest revisions, flags redirects, disambiguation pages and lists, counts the rows that are no article, and resumes from its checkpoint after a failure (`--restart` starts over), `flag-biographies` flags biographies from Wikidata (P31 = Q5), `adopt --from <pins>` takes a consumer's pinned revisions, with their bytes when it holds them (a pin with only a title has its page resolved from its revision under `--execute`; a pinned page keeps its adopted revision and its page's assessment and QID), `fetch [--source ID | --select FILE]` gets each revision's parse output once (only the named sources or pages with a selection), `render` writes the Literature Links layer, head text, reading copy and its map, sections, blocks and the deterministic finders (renderer `web-html/3`; a recorded rendering is only completed, never rewritten), `status [--verify]` checks every recorded sha. Nothing touches the network without `--execute` (`--fixtures <dir>` replays recorded responses) |
| `overlay init \| sync \| check \| export` | generate a companion's `pdf-overlay/` and its Literature Links annotations, and keep both honest: `cf overlay init <companion> --source-id ID --title T` writes the config, which is committed before `export` because `export` refuses a dirty checkout (SHA is that commit), `cf overlay export <companion> --convention attribution-table --commit SHA` runs an exporter and writes one `annotations/<section>/[<item>/]<name>.json` per annotation, `cf overlay sync <companion>` copies the viewer, tools, schemas, PDF.js and the Literature Links package byte for byte and writes the annotation bundle and `GENERATED.json` (`--check` is read-only and includes the scan), and `cf overlay check <companion>` adds the curation inputs' hashes and the overlay rules. **The one command that writes into a companion repo**, and only under `pdf-overlay/` and `annotations/` (`docs/overlay.md` rule 5). Needs no workspace |

Deleted: `cf book` — a source is discovered, and re-prepared by
`cf sources refresh`. Out of scope in v1 (documented, not implemented): `fix`,
`review-pr`, `camera-ready`, `publish-verdicts`, `curate` (item-level curation
for `granularity: node` repos) and `lint` (repo-only mechanical detectors — the
model-free checks that exist today live in `detect-proxies` and `pair`).

## Workflow stages

Five stages do their model work in a **Workflow script** under `workflows/`,
which only a Claude Code session can launch: a Python CLI cannot start one. The
shape is the same in all five — `cf` emits the arguments, **you** invoke the
Workflow tool, `cf` transcribes the result.

| stage | emit | you run | transcribe |
|---|---|---|---|
| adjudication | `cf adjudicate --companion X --emit-args` | `Workflow({ scriptPath: <the copy it names>, args: … })` over `workflows/adjudicate.workflow.js` | `cf adjudicate --transcribe <result> --from adjudicate` |
| challenge | `cf adjudicate --companion X --challenge-only --emit-args` | the same, over `workflows/challenge.workflow.js` | `cf adjudicate --transcribe <result> --from challenge` |
| local fleet | `cf run <id> --local` | `workflows/fleet-local.workflow.js` | `cf harvest <id> --journal <wf dir>` |
| claims verification | `cf report` (writes `reports/verify-claims.args.json`) | `workflows/verify-claims.workflow.js` | read its result into the draft |
| locate gold | `cf bench anchor <id> --emit-args <mode>` | `workflows/locate-label.workflow.js` | `cf bench anchor <id> --transcribe <result>` |

The args are **embedded into a copy of the script**, and that copy is what
`scriptPath` points at, because the Workflow tool has no filesystem API and
cannot read an args file for itself. Each command prints the exact invocation;
`references/workflow-patterns.md` holds the skeletons and the survival rules.

**When the Workflow tool is not available** — no permission, or a
non-interactive session — say so and stop at that stage instead of working
around it. `cf route` refuses to export anything while a routed file says
UNCHALLENGED, so a skipped adjudication cannot leak upstream; the honest
hand-off is the emitted args file plus the count `cf adjudicate` prints.

## Experimental and unverified

Shipped, real code that has **not** been demonstrated end to end. Say so when
you use it.

- **The server substrate.** `cf run --execute` has never been exercised against
  `tend` + `agentbox` from this repo; the five recorded runs pre-date it.
- **The tend-agent bridge** (`scripts/run/bridge-tend-agent.sh`) is a stub, and
  its own TODO says that backend's CLI contract has not been exercised.
- **The codex presets** (`attack-vac`, `differential-gpt`) plan against a
  pinned non-Claude model id; a renamed tier fails on the host, per agent.
- **`TEND_TASK_ID`.** Whether the orchestrator exports it into a validation
  command is unverified here. Without it the gate's ownership check falls back
  to the weaker single-stem rule and says so in its last line — read that line
  once per tend version (`references/runbook-server.md` §4).
- **`detect-proxies --lean`** needs a `deps.json` from a built `.lake` and a
  per-source stage table a human has filled in; the generated skeleton
  deliberately counts as no table.
- **`detect-proxies` stage analysis for a new book** needs that table written
  from scratch; without one the cheap tier still runs and reports the scan as
  SKIPPED rather than clean.
- **Three presets cannot be planned at all**: `differential-legacy`,
  `attack-legacy` and `mechanical` describe imported corpora, and `cf plan-run`
  exits 2 on them.
- **Locate** (`docs/proposals/wikipedia-locate.md`) is built and tested on
  synthetic inputs only: no real page has been rendered against the consumer's
  text layer (`CF_WIKILEAN_LAYER`), no locate root has run under `tend` (the
  `orchestrator.sqlite` schema the gate reads a task id from is assumed), no
  campaign sequencer or watchdog has run on a host, and no bench gold exists.

## Substrate

Server fleets are driven by two tools that do **not** ship here: `tend` (the
orchestrator — worktrees, retries, validation, merge) and `agentbox` (the
hosted session the bridges shell out to). Nothing in this repo installs or
pins either, and the version last used is **not recorded**; the only check is
`hosts.<host>.tend_version` in your own `workspace.yaml`, which `cf run`'s
preflight compares against `tend --version` on the host and refuses on a
mismatch; with no spec recorded, preflight only prints the version and
continues. Without them the supported path is the **local Workflow fleet**
(`cf run <id> --local`, under ~100 items) plus every mechanical stage, which
needs neither.

## Hard rules

1. **Quote gate.** A finding that quotes a source must carry a `book_quote`
   that really occurs in that source's `sources/<source_id>/text.txt`
   (normalized, ≥25 characters). The gate runs from **outside** the tree the
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
   redistributed. Exports redact quotes to (page, normalized length, sha256,
   and at most a quarter of the quote's words from each end, none at all below
   fourteen words) by default. Quote at most a sentence or two of a source in
   any artifact. Every source has a **licence class**, `closed` unless its
   registry entry or web-source family names another (`open_attribution`,
   `open_share_alike` — Wikipedia's text — or `public_domain`); an open class
   lets an exported record quote twelve words with a citation and the licence
   named, and changes nothing else: no class puts source text in the skill
   repo or lets anything be redistributed. `cf export literature-links` is the
   export that applies it, and it quotes nothing from a closed source.
   `POLICY.md` is the whole rule and `registry/README.md` is how
   the registry implements it.
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
    and PDFs, and writes nothing back. The one exception is `cf overlay`
    `init`, `sync` and `export`, which a person runs by name: they write under
    `<companion>/pdf-overlay/` and `<companion>/annotations/` and nowhere else,
    refuse either when it is a symlink or resolves outside the companion, and
    run no git there
    (`docs/overlay.md` rule 5). No other command, and no stage, writes
    anything.
11. **Low confidence is a refusal.** An inferred offset below 0.9, an
    undecidable anchor edition (agreement under 0.80, or a margin under 0.05
    between two editions), an adapter score under 0.5, an item table under 0.6
    complete: each stops and names the flag that settles it. Nothing is guessed
    silently, and every confidence it did accept is written into
    `workspace.yaml` where a reader can see it.

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
  `open`; licence class: `closed` `open_attribution` `open_share_alike`
  `public_domain`
- locate (where a source is formalized): `references/vocabulary.md` §1.11; the
  stages are `sourceset`, `corpus`, `locate candidates`, `plan-locate`, the
  locate gate, `harvest`, `tally --locate`, `export literature-links`, `campaign`
  and `bench`, and their formats `references/locate-documents.md`

**An item is the pair `(source_id, item)`.** The item half follows one strict
grammar (`cflib/items.py`): `<Kind> <n>[.<n>…]`, one to four numeric levels, the
depth inferred per source and stored as `numbering`, with an optional sub-part —
or `In-text p.<n>`, where the kind list is the *source's*, inferred from its own
headings — or, for a passage of a source with neither numbers nor pages,
`In-text #<hex>`, the hash of its head (`scripts/cflib/passages.py`). A label
deeper than the source's numbering is a reject, never a truncation. Anything else is rejected with a reason and reported, never silently
turned into a key. Rows carry the two halves as two fields; where one string is
needed the spelling is `source_id::item` (`cflib.items.qualified_item`), and a
filename uses `qualified_slug`. A bare item string is never a cross-source key,
which is why `finding_id` hashes `(run_id, source_id, item, kind, sorted
decls)`.

## References

In the order an audit needs them. Each is loaded on demand: read the one whose
trigger you have just hit, not the list.

- `references/traps.md` — the trap → fix index. Read **before your first run on
  a new book**: the anchor edition is not the bundled edition, offset
  confidence, summarized errata, Workflow args, session limits, tend's strict
  schema.
- `references/onboarding-contract.md` — what a repo must expose for discovery
  to succeed, per convention, and the seven obligations of Stage 0. Read
  **when onboarding refuses**, or before teaching it a new convention.
- `templates/PRESETS.md` — what each `--preset` plans, and how the five
  recorded runs differed. Read **before choosing a preset**.
- `references/findings-schema-v2.md` — row, finding, ledger and task schemas,
  and the gate matrix. Read **when the gate refuses a document**.
- `references/vocabulary.md` — the enums above, with the v1 → v2 mapping tables.
  Read when a value is rejected as not in the vocabulary.
- `references/method.md` — why the audit works: disjoint evidence classes, the
  ~40% first-pass false-positive rate, "every bug came from a second reading".
  Read when deciding **what to run**.
- `references/workflow-patterns.md` — the Workflow skeletons and the survival
  rule for adversarial verification. Read **before editing `workflows/`**.
- `references/runbook-server.md` — the tend + agentbox procedure, the 1/1 smoke
  test and the failure modes. Read **before a server fleet**.
- `references/review-patterns.md` — the defect-pattern catalog (P9–P13) the
  attack prompts are built from.
- `registry/README.md` — the shipped source registry and a workspace's overlay;
  `POLICY.md` is the licence rule it implements.
- `README.md` — install, quick start, and an honest statement of what has and
  has not been exercised on a second book. For changing the skill:
  `CLAUDE.md`, `CONTRIBUTING.md`, and `docs/design/stage_map.md` for the data
  model and the stage map.
