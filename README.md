# formal-companion

Two tools for Lean 4 / Mathlib **book companions**: an audit that checks a
companion against the sources it formalizes, and a generator for its PDF
overlay, which shows a source with each item linked to the declarations that
formalize it. It ships the `cf` CLI and installs as the Claude Code skill
`companion-faithfulness`.

A companion is a Lean repository whose declarations claim to formalize the
numbered items of one or more textbooks. The audit finds the declarations that
are provable but are not the source's statement — an extra hypothesis, a missing
quantifier, a vacuous theorem, a proxy definition — plus the items nobody
formalized, and drives them through an adversarial review before anyone is
told about them.

**The audit reports findings only**: it stops at challenged findings, routed to
each repo's owner, and a report whose every number is recomputed from the
corpus. Fix-PR authoring, merge review and camera-ready audits are out of
scope, with the extension points left clean. The overlay generator is the one
part that writes into a companion, and it writes only the companion's
`pdf-overlay/` and `annotations/`.

## PDF overlay

`cf overlay` generates a companion's PDF overlay: a viewer, served on
loopback, that shows the source PDF with each item outlined and the
declarations linked to it. It writes two directories in the companion:

- `annotations/`: the companion's
  [Literature Links](https://github.com/mathlib-initiative/literature-links)
  annotations, one link per file and one folder per book section;
- `pdf-overlay/`: the viewer, its tools and PDF.js, copied verbatim, plus the
  annotation bundle the viewer reads and `GENERATED.json`, which records the
  sha256 of every copied file and of the annotations.

```sh
cf overlay init <companion> --source-id <id> --title <title>   # pdf-overlay/overlay.config.json
cf overlay export <companion> --convention attribution-table --commit <sha>   # annotations/
cf overlay sync <companion>       # copy the runtime, write the bundle and GENERATED.json
cf overlay check <companion>      # read-only: the copies, the bundle and the curation inputs
```

A companion's CI runs `python3 pdf-overlay/tools/check_generated.py`, which
needs only CPython and the checkout. The PDF is never copied or committed.
`docs/overlay.md` is the contract.

## How it fits together

`cf` (`scripts/cf.py`) dispatches to one module per command in
`scripts/cfcmd/`. Those call stage packages under `scripts/`, which share
`scripts/cflib/` (paths, JSON, hashing, the source registry). The **audit**
runs `onboard` (which runs `extract`) and `pair`, then `detect-proxies` (no
model) or `plan-run`, `run` and `harvest` (the findings gate), then `tally`,
`adjudicate` (models, through `workflows/`), `route` and `report`. It writes
only to a workspace directory outside this repository, never to a companion.
The **overlay** (`scripts/overlay/`, `scripts/annotations/export/`) reads a
companion's Lean and curation files, writes Literature Links annotations to
its `annotations/`, and copies the viewer (`templates/overlay/runtime/`) into
its `pdf-overlay/`. It needs no workspace and reads no audit results. `ingest`
and `inventory` are the first two stages of an experimental PDF-to-companion
pipeline (`docs/pipeline.md`). `vendor/` holds Literature Links 1.0.0 and the
legacy annotation format the exporters write as an intermediate step.

## Companions, sources and workspaces

The unit of organization is a **companion with a list of sources**, not a book
with a list of companions. A **workspace** holds N companions and M sources; a
differential is possible exactly where two companions' source lists overlap.
**Nothing about a source is typed in**: `cf onboard <repo>` reads the
repository, fingerprints the source bytes, infers the page offset and the item
kinds from the text, finds the errata page and prepares the source. Sources are
identified by content (the sha256 of the bytes) and remembered in a **registry**
shipped with the skill, so the second workspace that meets an edition knows
everything the first one learned.

## What is in here

```
SKILL.md          when to use it, the default program, the command index, the hard rules
CONTRIBUTING.md   how to send a change; CLAUDE.md is the same ground for an agent
POLICY.md         the copyright rule the registry and the exports implement
docs/design/      stage_map.md (the current spec) and archive/ (the design record)
docs/proposals/   designs not yet built: pdf-to-companion.md, the PDF-to-companion pipeline
docs/pipeline.md  what of that pipeline is built: S0 (cf ingest) and S1 (cf inventory)
docs/overlay.md   the contract for the generated companion PDF overlay (`cf overlay`)
references/       method, vocabulary, schemas, review patterns, traps, server runbook
registry/         the shipped source registry (metadata only, never book text)
vendor/           literature_links/ (Literature Links 1.0.0, which the overlay
                  writes and reads) and formal_companion_annotations/ (the
                  legacy intermediate format that `cf export` writes and
                  `cf overlay export` converts to Literature Links), byte for
                  byte, each sealed by its manifest; never edited here
evals/            trigger evals: prompts as data, run by hand against the skill
bin/cf            the PATH shim; it resolves back to this checkout
scripts/cf.py     the workspace CLI; subcommands live in scripts/cfcmd/
scripts/cflib/    shared library: workspace.yaml, registry, discovery, inference,
                  state.json, paths, item grammar, vocabulary
scripts/<stage>/  adapters, sources, pair, plan, gate, run, harvest, ledger, route,
                  detect, report, recheck, score, share, cost (a fleet's tokens
                  and dollars from the agents' transcripts), pipeline (the
                  PDF-to-companion pipeline: pipeline.yaml, stage receipts, and
                  stages S0 ingest and S1 inventory)
scripts/annotations/
                  the skill's tooling around the legacy format (not a stage):
                  the two companion exporters behind `cf export`, the tag review
                  behind `cf review-tags` (docs/exporters.md) and sync_format.py,
                  which vendors a release; the legacy format itself (schema,
                  checker, id rules) is vendor/formal_companion_annotations/
scripts/overlay/  the overlay generator behind `cf overlay`: config, export,
                  sync, annotation files and folder defaults (docs/overlay.md)
templates/        workspace.yaml, adapter.yaml, conventions.md, prompts, tend config, tasks
templates/overlay/
                  runtime/, copied verbatim into a companion's pdf-overlay/ (the
                  viewer, its tools and schemas, PDF.js), and tests/ (its node suites)
workflows/        Workflow-tool scripts (adjudicate, challenge, verify-claims, fleet-local)
tests/            stdlib unittest; baseline regressions skip unless the env names the baseline
```

The repo holds **code, templates, docs and source metadata only**. Book text,
page images, statement tables, findings and ledgers live in a workspace
directory outside it.

## Install

```sh
SKILL=~/skills/formal-companion               # anywhere you like
git clone <this repo's url> "$SKILL"

# 1. let Claude Code load it as a skill
ln -s "$SKILL" ~/.claude/skills/companion-faithfulness

# 2. put `cf` on PATH -- a symlink, not a copy: the shim resolves back to this
#    checkout, and every command the skill prints is spelled `cf <command>`
mkdir -p ~/.local/bin && ln -s "$SKILL/bin/cf" ~/.local/bin/cf

cf doctor            # python floor, git, poppler, curl, and the PDF search roots
```

Requirements: Python 3.11+ (stdlib only; PyYAML is used when importable and a
fallback reader covers the skill's own files), `git`, and for source
preparation `pdftotext`/`pdftoppm` (poppler) and `curl` (errata). Server fleets
additionally need `tend` and ssh access to a host you are allowed to use;
neither `tend` nor `agentbox` ships here (see "What has been exercised").
`cf doctor` reports each of these and exits 2 when a required one is missing.

Nothing is installed into Python. Without the PATH shim every command also
works by path:

```sh
python3 "$SKILL/scripts/cf.py" --help
```

## Quick start

```sh
cf init <workspace>
cd <workspace>
cf onboard <path to a companion repo>
```

That is the whole of stage 0 for one companion. No `--book`, no `--pdf`, no
`--offset`, no `--errata-url`. Below is an **abridged illustration**, not a
promise: one real run, captured 2026-09-21 against `tao-measure-lean` at
`250b856`, with the paths replaced by placeholders and the checks that passed
quietly cut. Every count in it belongs to that commit — another checkout prints
other numbers, and that is drift, not breakage. The transcript you can
reproduce is `bash tests/e2e/synthetic.sh`, which builds its own booklet and two
companions of it and checks about thirty artifacts against pinned figures.

```
repo      : <tao-measure-lean>
adapter   : docstring_tags                     confidence 0.93   (42 of 44 sampled
            .lean files with a docstring open one with a book tag (110 carry no docstring))

sources discovered
  tao-measure-2011-draft         sha 62d95658…  registry: skill (confirmed)
      from     graph.json#metadata.sources, CLAUDE.md#sha256_prefix,
               sources/tao-measure-theory.pdf, README.md#links
      pdf      <companions>/tao-measure-lean/sources/tao-measure-theory.pdf   found in: repo_declared
      offset   16                                 confidence 0.996  (running_head_mode)
  tao-measure-gsm126-published   sha 64498329…  registry: skill (confirmed)
      from     formalization.yaml#sources, CLAUDE.md#sha256_prefix, …
      pdf      <companions>/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf
      offset   16                                 confidence 0.996  (running_head_mode)
      errata   https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
               confidence 1.00

ok   prepared  sources/tao-measure-2011-draft/   text 265 pages, 441 items, offset 16
ok   prepared  sources/tao-measure-gsm126-published/   text 265 pages, 442 items, offset 16
ok   errata    …  HTTP 200  <n> entries  (registry, confidence 1.00)
ok   extracted companions/tao-measure-lean/statements.jsonl   1131 rows
ok   scope     chapters [1, 2]   from the items the companions actually tag

anchor edition
  tao-measure-gsm126-published                 confidence 0.96
      anchor agreement  tao-measure-2011-draft             0.68 (202 ok / 95 mismatched)
      anchor agreement  tao-measure-gsm126-published       0.91 (271 ok / 27 mismatched)
      reason: anchor_agreement
      corroborated by prose: "Book-page anchors in docstrings follow the PUBLISHED pagination…"
  the repo BUNDLES tao-measure-2011-draft but does not anchor to it -> bundled_sources
```

Note what happened there without a flag: the repo **ships** the 2011 draft and
**anchors** to the published edition, and the decision was made by scoring the
repo's own page anchors against both editions, not by believing the sentence in
its `CLAUDE.md`. The sentence only corroborated it.

Then the rest of the program:

```sh
# 2. the second companion of the same book
cf onboard <path to the other companion repo>

# then EDIT companions/*/conventions.md and have each owner confirm it: that
# list is what separates CONVENTION from CONTENT, an unconfirmed list is the
# largest single source of false positives, and `cf plan-run` refuses a
# companion whose file carries no `confirmed_by:` stamp.

# 3. the map, at any point
cf status
cf sources list

# 4. pairing and the free findings
cf pair                              # one pairs/<source_id>/ tree per shared source
cf detect-proxies tao-measure-lean   # coverage + proxies, no model, no cost
                                     # -> corpus/detect-…-cheap/ (gated: `mechanical`)

# 5. a run (server fleets need an allowlisted host and recorded consent)
cf plan-run --preset attack-vac --companion tao-measure-lean \
    --run-id attack-vac-2026-09-20 --host <alias> --i-have-consent
cf run attack-vac-2026-09-20 --host <alias> --i-have-consent   # dry run without --execute
cf harvest attack-vac-2026-09-20      # the findings gate runs by default
#   add --transcripts <path> to price the run from the agents' own transcripts
#   (or from a `cf cost --export` file made on the host): RECEIPT.json then
#   carries the tokens by model and the dollars, and tally and report print
#   the cost per task
cf tally --union --agreed

# 5-local. no allowlisted host? omit --host — it defaults to `local`, and
#   plan-run then prints a note when the plan is above `local_fleet_max_items`
#   (the two worked companions are 506 and 178 items), so either raise that
#   key in workspace.yaml or accept `cf run <id> --local --force` and a resume.
#   Then `cf run <id> --local`, and `cf harvest <id> --journal <workflow dir>`
#   once the Workflow has finished. SKILL.md's fleet table and
#   references/workflow-patterns.md section 6 have the detail.

# 5b. findings produced before this skill existed
#     --repo-map says which companion their repo names are: the union key holds
#     the repo, so an unmapped import joins nothing this workspace runs
cf import-corpus --run-id findings-diff --from <a directory of findings> \
    --preset differential-legacy --repo-map ours=tao-measure-lean
#     an attack corpus goes in under its own preset -- `--mode attack --preset
#     attack-legacy` (tests/e2e/multisource.sh) -- because the two legacy presets
#     gate different things: differential-legacy keeps PAGE_ANCHOR in its kinds,
#     attack-legacy carries the witness convert_v1 lifted out of `reasoning`
# batch-1 rows were judged against the 2011 draft, so they say so:
cf import-corpus --run-id batch1 --from <a directory of findings> \
    --preset differential-legacy \
    --source tao-measure-2011-draft --source-sha <that edition's full sha256> \
    --allow-edition-mismatch

# 6. challenge, route, report
cf adjudicate --companion tao-measure-lean
cf route
cf report
```

`cf status` is the map at every point: the sources table, the one line saying
where a differential is possible, each companion's `state.json`, any source
nobody has confirmed, and whether the workspace repo has acquired a remote. It
prints nothing it did not read from a file, so a missing number means a stage
has not run.

The findings gate runs **by default** in `cf harvest` and `cf import-corpus`.
An import of a directory this workspace never planned has no gate config to
build unless you name a `--preset` or a `--gate-config`: it then records
`gate: null`, warns, and `cf tally` repeats that warning for every run it
counts.

## The end-to-end demonstration

It needs no private checkout, and it is what CI runs:

```sh
bash tests/e2e/synthetic.sh
```

It generates a public-domain booklet and two synthetic Lean companions of it
carrying eight planted defects, onboards them with no flags, runs the
model-free detectors and scores them against `tests/e2e/answer-key.json`:

```sh
cf detect-proxies notes-a --stages tests/e2e/stages.anonymous-elementary-sets.json
cf detect-proxies notes-b --stages tests/e2e/stages.anonymous-elementary-sets.json
cf score --run detect-notes-a-cheap --run detect-notes-b-cheap \
         --run anchor-check-anonymous-elementary-sets \
         --answer-key tests/e2e/answer-key.json
```

The recorded score for the mechanical tier is **precision 1.00, recall 0.50,
F1 0.6667**: it finds every defect of the three kinds it can see and none of
the four it cannot, which is the point — that is the free detector's recall
ceiling. It is also the only figure in this repo with a recall denominator,
because a defect nobody raised leaves no artifact behind. Both the script and
`tests/test_rf_benchmark.py` recompute it rather than trusting it.

## When discovery cannot decide

It stops and names the override rather than guessing.

```sh
cf sources set <id> offset=16              # inferred offset confidence was below 0.9
cf sources set <id> chapters=1,2           # pin the scope instead of deriving it
cf sources set <id> numbering=3,4          # the book numbers deeper than its headings showed
cf sources set <id> confirmed=true         # you checked a discovered entry
cf onboard <repo> --anchor-source <id>     # two editions scored too close to call
cf onboard <repo> --adapter docstring_tags # no adapter scored above 0.5
cf onboard <repo> --source <file>          # the repo names no source, or not this one
cf sources add <source> --for <companion>  # the repo names a source it does not identify
cf sources remove <id>                     # undo one that should not be here
cf onboard <repo> --download               # fetch from the author's own site (licence permitting)
```

Every override stamps its own trace: `offset_method: manual`,
`scope_method: manual`, `numbering_method: manual`, `completeness_method:
manual`, `confirmed: false` until a human says otherwise.

## The workspace directory model

```
<workspace>/
  workspace.yaml             companions (repo, adapter, sources[], anchor_source,
                             bundled_sources[]), sources (sha, offset, kinds, errata,
                             licence, page map, role), hosts, models, defaults,
                             budget, policy, library_paths
  registry.yaml              this workspace's overlay on the skill's source registry
  sources/<source_id>/       GITIGNORED: text.txt, pages/, images/, items.json,
                             meta.json, errata.md, errata.raw.html, errata.json
  companions/<name>/         adapter.yaml, conventions.md, statements.jsonl, state.json
  pairs/<source_id>/         pairs.jsonl, coverage.md, queue.json, targets, def-coverage,
                             anchor-check.json
  runs/<run-id>/             run.yaml, tasks/, prompts/, gate.json, tend-root/ (gitignored)
  corpus/<run-id>/           findings + RECEIPT.json  (committed: this is the baseline;
                             the receipt's cost block is the run's priced tokens)
  pipelines/<id>/            pipeline.yaml, doc.vN.json checkpoints and
                             stages/<stage>.stage-receipt.json: the PDF-to-companion
                             pipeline's records, written by `cf ingest` (S0) and
                             `cf inventory` (S1); repo/ is the generated companion,
                             its own git repository (gitignored here)
  ledger/                    accepted/refuted per companion, routed/findings.<name>.jsonl
  detect/<name>/             proxy-detection artifacts
  reports/                   tally.<run>.json, union.<name>.json, coverage.json,
                             score.<run>.json, handoff.<date>.md
  fixes/  reviews/           created by `cf init` and written by nothing in v1: they
                             are the extension points for the out-of-scope stages
```

`docs/design/stage_map.md` §2.1 is the full version, including what carries the
source sha and the companion pin.

Rules the directory enforces:

- **no remote.** `cf init` runs `git init` and REFUSES a workspace repo that
  already has a remote unless `policy.allow_remote` says otherwise; that
  refusal is the control, because the corpus quotes the source and holds
  unchallenged findings. The pre-push hook it installs is a reminder on top of
  it, not the control: `git push --no-verify` skips it, a fresh clone does not
  carry it, and a remote added after `cf init` never passes through it — which
  is why `cf status` prints, every run, whether the repo has a remote and
  whether the hook is still in place. `cf init --no-remote-guard` adds the one
  guard a bare `git push --no-verify` does not get past (`remote.pushDefault`
  to a remote that does not exist and `push.default nothing`, both in that
  repo's local config); an explicit `git push <remote> <ref>` still works, so it
  is a guard and not the control either. Staying local is not the same as being
  unshareable: `cf share export` writes the campaign's conclusions — ledger
  rows, routed findings, tallies, receipts, source metadata — with every quote
  redacted, and `cf share import` merges such a bundle into another workspace
  without overwriting a verdict.
- **copyright.** `sources/`, `pages/`, `images/`, `crops/` and `*.pdf` are
  gitignored; only the sha256 and the metadata are recorded. `corpus/` is
  deliberately *not* ignored — it is the calibration baseline. `POLICY.md` is
  the whole rule.
- **one edition per source id.** Every artifact carries `source_id` and
  `source_sha`; a mismatch is refused.
- **consent.** `cf run` refuses hosts outside `hosts.allowlist` and requires
  `--i-have-consent`, which is recorded in `run.yaml`.

## What has been exercised, and what has not

This is one book's audit generalised, not a tool proven over many. Being exact
about which is which:

- **Exercised on two real companions of one book**, two editions and a shared
  source: discovery, inference, extraction through the `docstring_tags` and
  `attribution_table` adapters, pairing, def-coverage, the mechanical
  page-anchor check, `detect-proxies` (cheap tier), the gate, `import-corpus`,
  the tally, adjudication, routing and the report. Every pinned regression
  number in the test suite comes from that corpus.
- **Exercised on a synthetic second book**: all of the above, from nothing,
  over a generated public-domain booklet and two companions of it
  (`tests/e2e/synthetic.sh`; `tests/e2e/multisource.sh` adds the Tao half).
  That is what shows the pipeline is not wired to one title.
- **Written from one campaign's history and still frozen to it**: the eight
  `--preset` rows are a record of how the five 2026-09 runs differed, and three
  of them (`differential-legacy`, `attack-legacy`, `mechanical`) describe
  imported corpora rather than runs — `cf plan-run` exits 2 on them. The stage
  tables `detect-proxies --lean` needs are per-source and hand-written, and the
  shipped one covers one book.
- **Never exercised end to end**: the server substrate. `cf run --execute`
  drives `tend` + `agentbox`, neither of which ships here or is pinned by
  anything here, and the five runs that produced the baseline pre-date this
  code. The local Workflow fleet (`cf run <id> --local`, under ~100 items) is
  the fleet path that has been driven from this repo.
- **Not generalised yet, and where a second book will hurt first**: a numbering
  grammar that is not `<Kind> <n>.<n>`, a source whose items are not headed by
  a capitalised kind word, and a companion convention no adapter recognises.
  `SKILL.md`'s "Experimental and unverified" section is the short list.

## Tests

```sh
python3 -m unittest discover -s "$SKILL/tests"        # the documented gate
python3 -m pytest -q "$SKILL/tests"                   # same suite; conftest pins HOME
node --test "$SKILL"/templates/overlay/tests/*.test.mjs   # the overlay viewer's suite
```

Regression tests that read local baselines are skipped unless you name them:

```sh
CF_BASELINE_CORPUS=<a harvested corpus> \
CF_BASELINE_DIFF=<the differential tree> \
CF_TML=<one companion checkout> \
CF_MT=<the other companion checkout> \
CF_BOOK_TXT=<the pinned book text> \
python3 -m unittest discover -s "$SKILL/tests"
```

The exporter reproductions (`tests/test_export.py`) also take `CF_TAO_PDF`
(the published edition's PDF), `CF_M3_REFERENCE_DIR` (the maintainer's
reference documents, never in this tree) and, optionally, `CF_TML_SITE` and
`CF_MT_BUILD`; `docs/exporters.md` says what they compare.

`.github/workflows/tests.yml` runs the suite on Python 3.11, 3.12 and 3.13,
once more with PyYAML absent (its fallback reader is what parses `run.yaml` on
a bare host), the documented `unittest` command, the synthetic end-to-end
script, and `cf sources validate-registry`.

## Status

The audit reports findings only; `cf overlay` writes only a companion's
`pdf-overlay/` and `annotations/`. `fix`, `review-pr`, `camera-ready` and
`publish-verdicts` are designed and documented but not implemented. Of the PDF-to-companion
pipeline (`docs/proposals/pdf-to-companion.md`), the deterministic parts of S0
and S1 are built (`docs/pipeline.md`); its agents and stages S2 to S9 are not. See `SKILL.md` for the hard
rules, `docs/design/stage_map.md` for the data model, `CONTRIBUTING.md` for how
to send a change, and `CHANGELOG.md` for what a version number promises.

Licensed under Apache-2.0 (`LICENSE`). The licence covers this code and the
source **metadata**; it never covers the sources themselves — `POLICY.md` is
that rule.
