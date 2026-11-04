# Repository layout

## How it fits together

`cf` (`scripts/cf.py`) dispatches to one module per command in `scripts/cfcmd/`.
Those call stage packages under `scripts/`, which share `scripts/cflib/` (paths,
JSON, hashing, the source registry).

The **audit** runs `onboard` (which runs `extract`) and `pair`, then `detect-proxies`
(no model) or `plan-run`, `run` and `harvest` (the findings gate), then `tally`,
`adjudicate` (models, through `workflows/`), `route` and `report`. It writes only to
a workspace directory outside this repository, never to a companion.

The **overlay** (`scripts/overlay/`, `scripts/annotations/export/`) reads a
companion's Lean and curation files, writes Literature Links annotations to its
`annotations/`, and copies the viewer (`templates/overlay/runtime/`) into its
`pdf-overlay/`. It needs no workspace, and any faithfulness verdicts it shows come
from files committed in the companion, never from an audit workspace.

**Locate** plans from a sourceset and a universe, never from a companion, and writes
to a workspace (`scripts/corpus/`, `scripts/locate/`, `scripts/bench/`, and
`scripts/sources/` for the web sources).

`ingest` and `inventory` are the first two stages of an experimental PDF-to-companion
pipeline (`docs/pipeline.md`). `vendor/` holds Literature Links 1.0.1 and the legacy
annotation format the exporters write as an intermediate step.

## What is in here

```text
SKILL.md          when to use it, the default program, the command index, the hard rules
README.md         what the project is, how to install it and try it, what has been exercised
CONTRIBUTING.md   how to send a change; CLAUDE.md is the same ground for an agent
CHANGELOG.md      what a version number promises, and what changed
POLICY.md         the copyright rule the registry and the exports implement
docs/design/      stage_map.md (the current spec) and archive/ (the design record)
docs/proposals/   designs: pdf-to-companion.md (the PDF-to-companion pipeline, not yet built),
                  wikipedia-locate.md (locate; its section 1 lists what is built),
                  statement-cut.md (how locate's inventory cuts passages)
                  and informalize.md (an informal companion written from a Lean
                  library, not yet built)
docs/running-an-audit.md
                  the audit's program, an onboarding transcript, the overrides
docs/workspace.md the workspace directory model and the rules it enforces
docs/generating-an-overlay.md
                  the overlay step by step, its refusals, the viewer
docs/overlay.md   the contract for the generated companion PDF overlay (`cf overlay`)
docs/exporters.md the companion exporters behind `cf export`, and `cf review-tags`
docs/annotations.md
                  the legacy annotation format those exporters write
docs/pipeline.md  what of the pipeline is built: S0 (cf ingest) and S1 (cf inventory)
docs/adding-an-adapter.md
                  a walkthrough for a new companion convention, and what a new book needs
docs/status.md    what has run, on what, and where the record is
docs/testing.md   test commands, environment variables, CI jobs
docs/layout.md    this file
references/       method, vocabulary, schemas (findings-schema-v2), review patterns,
                  traps, the server runbook, the onboarding contract, workflow patterns,
                  and locate's documents and benchmark
registry/         the shipped source registry (metadata only, never book text)
vendor/           literature_links/ (Literature Links 1.0.1, which the overlay writes
                  and reads) and formal_companion_annotations/ (the legacy intermediate
                  format that `cf export` writes and `cf overlay export` converts to
                  Literature Links), byte for byte, each sealed by its manifest;
                  never edited here
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
scripts/corpus/   locate's universes of libraries and corpus.sqlite (`cf corpus`)
scripts/locate/   locate's candidates, planner, outcomes and export
                  (`cf locate`, `cf plan-locate`, `cf tally --locate`,
                  `cf export literature-links`); the web sources are
                  scripts/sources/ (`cf sourceset`), the gate
                  scripts/gate/verify_locate.py, campaigns `cf campaign`
scripts/bench/    locate's benchmark (`cf bench`; references/locate-bench.md)
templates/        workspace.yaml, adapter.yaml, conventions.md, prompts, tend config,
                  tasks, the adapter examples (adapters/), the campaign sequencer
                  (campaign/), the probe and its canary (corpus/) and the stage-table
                  skeletons (detect/)
templates/overlay/
                  runtime/, copied verbatim into a companion's pdf-overlay/ (the
                  viewer, its tools and schemas, PDF.js), and tests/ (its node suites)
templates/informalize/
                  runtime/, copied verbatim into an informalize output repository's
                  informal/ (fcmarkers.sty, STYLE.md, the build and check tools), and
                  project/, the templates `cf informalize init` renders once
                  (proposals/informalize.md section 7)
workflows/        Workflow-tool scripts (adjudicate, challenge, verify-claims, fleet-local,
                  and locate-label, the bench's frozen labeler)
tests/            the test suite; baseline regressions skip unless the env names the baseline
```

The repository holds **code, templates, docs and source metadata only**. Book text,
page images, statement tables, findings and ledgers live in a workspace directory
outside it (`CLAUDE.md` section 1, [The workspace](workspace.md)).
