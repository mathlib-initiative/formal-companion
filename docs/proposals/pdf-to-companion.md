# From a PDF to a Lean companion: the pipeline design

**Status: proposal, 2026-09-26. Nothing below is built unless section 1 says
so.** This document designs a pipeline that takes one PDF and produces a
companion of the kind the measure_theory companion for Tao's measure theory
book is: Lean statements tagged to book items, proofs, a PDF overlay, a
literate site and a verdict layer. Swarms of agents do the work. Deterministic
gates and adversarial review decide what is accepted, both for the
formalisation and for the tags.

The pipeline is called the lean-companion pipeline here. Commands written
`lc ...` are proposed names; no `lc` program exists. Commands written `cf ...`
are this repository's CLI and exist unless the sentence says otherwise. Where
`lc` lives is open (section 8, question 4). The default is this repository
after its rename, which needs the no-write rules of section 1.1 narrowed.

**Contents**: 0 conventions of this document · 1 what exists today · 2 the
pipeline · 3 the quality model · 4 data flow through the annotation document ·
5 milestones · 6 costs · 7 risks · 8 open questions for the maintainer

## 0. Conventions of this document

Paths into this repository are written as they are (`scripts/...`). Paths into
other trees start with a placeholder, because an absolute path on one machine
means nothing to a second reader (`CLAUDE.md` §1). The placeholders are:

| placeholder | tree | state read for this design |
|---|---|---|
| `$MT` | measure_theory, the reference companion (thematic modules, proved exercises, PDF overlay) | master 00a2438 |
| `$TML` | tao-measure-lean, the second Tao companion (one file per subsection, exercises as `sorry`) | main f158509 |
| `$AUTOFORM` | the installed Autoform plugin (MIT) | 0.4.4; a 0.6.1 checkout exists at 57d268a, not installed |
| `$WIKILEAN` | WikiLean | local checkout |
| `$FFCORE` | ff-core | 4a60715 |
| `$FFREPORT` | the archive of the ff-core Tao campaign (prompts, orchestrator databases, notes) | not a repository |
| `$TD` | the three-way Tao differential workspace | not a repository |
| `$MARATHON` | marathon-v2 | 6819566 |
| `$BENCH` | lean-statement-bench | 73d659f |
| `$NOTES` | the maintainer's notes directory (camera-ready tools, annotation prototypes, server handoffs) | not a repository |
| `$LEAN` | the maintainer's local Lean projects, two of them reviewed by Autoform | not a repository |

"Tao" means the published edition of the measure theory book, sha256
`64498329...`, 265 pages (`registry/sources.yaml`). Every figure carries its
source in parentheses. A figure marked *estimate* was not measured. A figure
marked *computed* was counted read-only over the named files on 2026-09-26.

People are named by role, as `CLAUDE.md` §1 asks: **the maintainer** owns this
repository and signs the human gates, **the measure_theory author** wrote
`$MT`, and **a host's administrator** controls the accounts on a shared server.

## 1. What exists today

### 1.1 In this repository

This skill is an audit pipeline. It reads a companion and reports findings; it
never writes Lean (`SKILL.md`). Its parts are the quality layer of the
pipeline below.

| part | path | reused for |
|---|---|---|
| source preparation, offset and kind inference, item grammar | `scripts/sources/prepare_source.py`, `scripts/sources/render_pages.py`, `scripts/cflib/inference.py`, `scripts/cflib/items.py`, `scripts/cflib/registry.py` | S0, S1 |
| raw errata fetch | `scripts/sources/fetch_errata.py` | S0 |
| docstring-tag extraction | `scripts/adapters/docstring_tags.py` | S5, S7 gates |
| coverage, definition coverage, page-anchor check | `scripts/pair/anchor_check.py`, `scripts/pair/def_coverage.py` | S5, S7 gates |
| proxy detector and dependency dump | `scripts/detect/` (`DumpDeps.lean.tmpl`, `dump_deps.sh`, `stages.py`) | S5, S6, S8 |
| presets, prompts, tend roots | `scripts/plan/presets.py`, `templates/prompts/`, `templates/tend/config.yaml.tmpl` | S2, S5, S9 fleets |
| server bridges | `scripts/run/bridge-agentbox.sh`, `scripts/run/bridge-claude-p.sh` | every fleet |
| findings gate and changed-files rule | `scripts/gate/verify_findings.py`, `scripts/gate/check_changed_files.py` | every adversarial pass |
| adjudication, challenge, claims check | `workflows/adjudicate.workflow.js`, `workflows/challenge.workflow.js`, `workflows/verify-claims.workflow.js` | S3 to S9 |
| revision pins and recheck | `scripts/recheck/` | loops, S9 |
| planted mutations and scoring | `scripts/score/mutate.py`, `scripts/score/answer_key.py`, `scripts/score/score.py`, `tests/e2e/synthetic.sh` | calibration |
| redacted exchange | `scripts/share/redact.py` | the release document |
| annotation format v1.0 | `schema/annotations-v1.schema.json`, `scripts/annotations/validate.py`, `docs/annotations.md` | the spine of the pipeline |

What this repository does not have:

- No `cf` command writes companion Lean or runs lake. The operator-run
  `scripts/detect/dump_deps.sh` runs `lake env lean --run` in an already-built
  companion and never builds. The generated tend config sets
  `seed_worktree_build: false` (`templates/tend/config.yaml.tmpl`).
- Writing Lean is against its standing rules: `CLAUDE.md` §4 ("Never write to
  a companion repo"), `CONTRIBUTING.md` invariant 3 ("never build one") and
  `SKILL.md` rule 10. S5 and S6 write and build a Lean repository, so the
  pipeline needs either those rules narrowed to repositories it did not
  generate, or a separate home (section 8, question 4).
- The changed-files rule admits only `findings/<id>.json` and
  `tasks/<id>.yaml` (`scripts/gate/check_changed_files.py`). A worker that
  writes `.lean` is refused.
- There is no PDF-only entry point. `cf onboard` needs a companion repository
  (`scripts/cfcmd/onboard.py`). `cf sources add` needs its `--for` companion
  (`scripts/cfcmd/sources.py`). Scope is derived from the companion's tags, so
  an unwritten companion has none (`scripts/cflib/inference.py`).
- There is no geometry, site, viewer or tag curation. Curate, lint and
  publish-verdicts are documented in `SKILL.md` and not implemented.
- No producer or consumer reads or writes the annotation format yet; only the
  checker does (`docs/annotations.md` section 11).
- Cost is not recorded. tend's counters read zero under agentbox
  (`references/traps.md` C2; issue #11).
- Issues #6, #7, #9, #10 and #11 are open. Issue #9 (a copy-status line
  bypasses the changed-files check) becomes a real hole once `.lean` writes are
  allowed.

### 1.2 In other trees

| tree | what the pipeline can take | what it lacks |
|---|---|---|
| tend and agentbox | the worker, gate, reviewer and merge loop that `cf run` is written to drive | not shipped or pinned; `cf run --execute` has never been exercised against them from this repository, the tend-agent bridge `scripts/run/bridge-tend-agent.sh` is a stub, and the `TEND_TASK_ID` export is unverified (`SKILL.md`, "Experimental and unverified"); tend's model layer is text-only |
| `$AUTOFORM` | the honesty-gate structure in `$AUTOFORM/servers/prover/verify.py`; the rubric texts `$AUTOFORM/internal/rubrics/proof_integrity.json` and `faithfulness.json`; `$AUTOFORM/agents/mathlib-checker.md`; the planning-swarm pattern | no Lean statements stage ("planned, not built", `$AUTOFORM/docs/pipeline.md`); its gate rejects any literal `sorry` and any `sorryAx`; its prose rules drop book numbering; all six of its MCP servers failed to connect on 2026-09-26 |
| `$MT` | output-stage scripts: `$MT/scripts/overlay_geometry_from_layout.py`, `$MT/scripts/pdf_overlay.py`, `$MT/scripts/extract_declaration_snippets.py`, `$MT/scripts/build_combined_site.py`, `$MT/scripts/official_pdf_workflow.py`, `$MT/pdf-overlay/viewer/` | all hard-coded to Tao (265 pages, the PDF and ledger shas, counts 77/2884/5093, the `MeasureTheory.Tao.` prefix); its front half is external and absent |
| `$TML` | the skeleton-campaign pattern; the PR #5 verification recipe; `$TML/scripts/update_status_table.py`, `$TML/scripts/build_site.sh`, `$TML/scripts/slim_large_pages.py` | no geometry, no machine-readable verdicts |
| `$WIKILEAN` | the enumerator prompt (`$WIKILEAN/site/batch_annotate.py`, `AGENT1_SYSTEM`); all-or-nothing re-anchoring (`$WIKILEAN/site/update_from_upstream.py`) | its two agents are sequential, not adversarial; its `decl_exists` (`$WIKILEAN/site/search_tools.py`) answers from WikiLean's decl-index shards or the live mathlib4_docs declaration data, not from a pinned Mathlib |
| `$FFCORE`, `$FFREPORT` | measured failure modes; the adversarial reviewer prompt `$FFREPORT/prompts/reviewer-system-adversarial.md`; the faithfulness-first worker text `$FFCORE/src/ff_core/prompts/worker/v6/system.md` | frozen; text-only model layer (`$FFCORE/src/ff_core/llm/models/messages.py`); the reviewer prompt is Tao-specific and must be rewritten before reuse (section 2.6) |
| `$MARATHON` | an Aristotle fill backend (`$MARATHON/marathon/fill.py`) and leaves-first ordering (`$MARATHON/marathon/prover/dag.py`) | the prover has been off since 2026-07-18 (`.marathon/conductor/prover-control.json` in the live project); no measured fill rate; pricing undocumented |
| `$BENCH` | bridgebench, a statement-equivalence checker | uncalibrated, rung 5 off, pinned to Lean v4.28.0 |

### 1.3 Two notes on the reference companions

The measure_theory companion has 0 `sorry` (computed over `$MT/MeasureTheory/`)
and proves every exercise it states. Exercise 1.6.6 is not formalised at all
(`$MT` PR #19). Exercises as permanent `sorry` is tao-measure-lean's
convention: 514 `sorry`, of which 508 are exercise stubs and 6 are in-text gaps
(`$TML/scripts/update_status_table.py` classification at f158509). Even there,
six exercise-tagged theorems carry no `sorry`: Exercise 1.1.17 is proved as a
deliberate exception, and five others bridge or derive rather than solve
(`$TML/README.md`, "The `sorry` convention").

Who generated `$MT`'s front half is unconfirmed. That front half is the ledger,
geometry batches a to c and the verifier ids, and behind them a blueprint
`.tex`, a plan, `coverage.json` and a correspondence state. An ff-core instance
run by the measure_theory author is likely:

- 91 `$MT` commits from 2026-08-13 to 2026-08-19, among them eb6e9de (the
  ledger) and a5d5ca0 (the overlay with batches a to d), are authored
  `ff-agent <ff-agent@ff-server>`, and `ff-agent` is ff-core's own console
  script (`$FFCORE/pyproject.toml`).
- The verifier ids carry the prefix `verifier:ff-measure-theory--`.
- ff-core lands work by fast-forward (`$FFCORE/src/ff_core/orchestrator/orchestrator.py`,
  the worktree merge and the `--ff-only` publish), so a history with no merge
  commits is what it would leave.
- Its `worker_agent_command` (`$FFCORE/src/ff_core/orchestrator/config.py`)
  runs external workers, and a Claude Code worker can read page images and mint
  any id.

The local checkout cannot settle it. It mints only uuid4 ids
(`$FFCORE/src/ff_core/orchestrator/control_store.py`), while the verifier ids
are UUIDv7, and its model layer is text-only. But its newest upstream commit is
from 2026-07-15, a month before those commits, and the server copy was not
checked. Until the measure_theory author confirms, this document says only that
the generator is unconfirmed. `$NOTES/companion-annotations-2026-09-26/REPORT.md`
now opens with a note to the same effect.

## 2. The pipeline

Ten stages, S0 to S9, turn one PDF into a companion. Every stage reads one
checkpointed annotation document and writes the next one, from doc v0 (S0) to
doc v9 (S9), as `pipelines/<id>/doc.vN.json`; S6 and S7 run side by side and
merge by the rule of section 4.1. Every stage also writes a stage receipt
(section 4.1). No stage trusts its own swarm. Each stage ends in a
deterministic, fail-closed gate, and every stage that uses models also has an
adversarial pass.

A pipeline run is one directory, `pipelines/<id>/`, with its own
`pipeline.yaml`. It holds the checkpoints, the stage receipts and the
pipeline-wide settings. A stage that uses a fleet plans one or more ordinary
`cf plan-run` runs under `runs/<run-id>/`, and `pipeline.yaml` lists their run
ids. A fleet's `run.yaml` is never the place for a pipeline setting:
`cf plan-run` rewrites it whole (`scripts/cfcmd/plan_run.py`).

**One decision comes first.** The two Tao companions disagree on exercises:
`$MT` proves them and `$TML` stubs them (section 1.3). "Like the measure_theory
companion" could mean either its artifact shape with stubbed exercises, or its
artifact shape with proved exercises. The pipeline therefore has no default.
`pipeline.yaml` carries an `exercise_policy` field (`stub` or `prove`) that
starts unset, and S3 refuses to proceed while it is blank: S3 signs the
conventions file, whose §4 Sorry policy is copied from this field. Per-item
exceptions are allowed and listed in that §4, each signed by the maintainer.
Section 8 asks the question.

| stage | reads | adds to the document | swarm | gate, in short | built today |
|---|---|---|---|---|---|
| S0 ingest and pin | one PDF, optional errata URL, licence note, explicit scope | `sources[]`, provenance, the generated repository's first commit | one image spot-checker | offset confidence, registry edition check, raw errata, no PDF in git, spot check with a planted page | source preparation and the raw errata fetch |
| S1 inventory | doc v0, prepared text | `items[]` with structured ids, short `TextQuoteSelector` anchors and erratum links | enumerators, missed-item hunters, a completeness critic | exact selector check, unique ids, slot contiguity, planted-deletion recall | numbered-heading extraction and slot completeness only |
| S2 geometry | doc v1, the PDF | `locations[]`, one `PdfRegionSelector` per item and page, with `signoff` | two vision checkers per region, a reconciler, a neighbour pass | region sanity, planted-mutation recall, local geometry receipt | the §1.7 generator, Tao-only |
| S3 statement parse and conventions | doc v2, per-item text, page images | provenance, a root `x_` pointer to the blueprint and conventions shas | parser, refuter, reconciler; conventions drafter and two refuters; three flag-only reviewers | every statement item parsed, planted notation flips, conventions in the template's 7 sections and signed | the conventions template and the `cf plan-run` refusal |
| S4 plan | doc v3, blueprint, conventions, pinned Mathlib | `clusters[]` (book sections), `memberships[]`, `x_mathlib` on items; `tasks/*.yaml`; a draft stage table | mathlib-checkers and refuters | every cited declaration exists at the pin; stage table signed; task DAG valid | no |
| S5 statements | doc v4, tasks, the signed stage table, Lake template | `declarations[]`, key `links[]`, `unattributed[]`; `statement-freeze.json` | one author per section, a reviewer, then attack-vac and attack-hunt on every item, challenge, adjudication | build, bijection, key-link tag gate, printed-page anchors, `detect-proxies --lean`, freeze | attack presets, challenge and adjudication, tag extraction, the anchor check, `detect-proxies` |
| S6 proofs | the frozen repo, doc v5 | proved declarations, per-proof provenance | provers, a proof-integrity judge, a laundering-audit fleet | freeze unchanged, clean-room build, `detect-proxies --lean` rerun, DumpDeps sorry-source gate, laundering check | the DumpDeps dump |
| S7 attribution | doc v5, frozen Lean, page images | support, helper and example `links[]`, `artifacts[]` | curator, refute-by-default verifier, reconciler, consistency pass | key-link tag gate, bijection, anchors, no key on a projection or structure field, planted-mistag recall | tag extraction and the anchor check |
| S8 publish | doc v7, built Lean | `destinations[]`, `snippets[]`, the site source, changelog | claims check, vision smoke test | CI suite, no PDF in output, page-size cap, planted claim errors | the claims check only |
| S9 reduced audit | doc v8, the S5 attack corpus, independent companions | `verdicts[]` | differential, sampled attacks, challenge, adjudication, completeness critic | receipts, route refuses unchallenged, pinned verdicts | differential and attack fleets, challenge and adjudication, recheck |

### 2.1 S0: ingest and pin

Input is one PDF, an optional errata URL, a licence note and an explicit scope
(`chapters=...`). No companion repository is needed. Scope must be explicit,
because `scripts/cflib/inference.py` derives it from tags that do not exist
yet.

Output, doc v0:

- `companion {name, repo, commit, generated_at, generator}`. S0 initialises the
  generated repository from the Lake template and commits it once, so `commit`
  is a real 40-hex id and `repo` is `local/<name>` until the repository has a
  remote; later checkpoints move `commit` to its head. The v1.0 schema requires
  both (`schema/annotations-v1.schema.json`), so a `commit: null` companion
  would fail S0's own gate.
- One `pdf` source with sha256, bytes, `physical_page_count`,
  `printed_page_offset`, page sizes and the text layer `{extractor: pdftotext,
  version}`.
- A provenance record (actor `tool`, method `pipeline`).
- Workspace-local and never committed: `sources/<id>/{text.txt, pages/,
  images/, errata.*, meta.json}`.
- A fresh sandbox run root (section 4.5) holding only those files, the
  document and the generated repository.

Agents: one image-capable spot-checker compares the rendered running head and
page number of 10 random pages with the inferred offset, plus one planted page
whose page number is shifted by one. It runs on the server through the agentbox
claude bridge, or through `claude -p` locally, because S0 reads no reference
companion.

Gate, fail-closed:

- Offset confidence at least 0.9 (`scripts/cflib/inference.py`; `CLAUDE.md` §4
  thresholds).
- No truncated sha adopted as identity (`references/traps.md` A2b).
- **Edition check.** S0 looks the input sha up in `registry/sources.yaml`. If it
  is a known draft or non-anchor edition, or the registry knows a newer edition
  of the same work, S0 refuses without a recorded override. For an unknown book,
  S0 records the PDF's own edition and copyright-page facts in `sources[]`, and
  the maintainer's S1 sign-off must name the edition. In PDF-only mode this is
  the only defence against the one-page shift: the draft and published Tao
  editions share the +16 offset and about 65 items sit one page apart
  (`$TD/HANDOFF.md` section 3; `references/traps.md` A1b).
- Errata are optional. When a URL exists they are fetched by curl, the raw HTML
  is kept and suspect entries are named (`scripts/sources/fetch_errata.py`).
  When none exists the run records "none found". A summarising fetch once
  dropped the entry for Definition 1.5.11 (the `scripts/sources/fetch_errata.py`
  module docstring; `references/traps.md` A3). An entry-count comparison is not
  a gate: both counts would come from the same parser, so it could not fail.
- The document passes `validate.py --canonical`.
- The PDF is in no git tree.
- The spot check agrees on the 10 real pages and flags the planted one, or a
  manual override is stamped `*_method: manual`.

New work: `lc ingest --pdf <file> --scope chapters=...`; a `sources[]` exporter;
the generated-repository initialiser; the sandbox run-root builder whose
transfer manifest lists only workspace-local files, the document and the
generated repository.

### 2.2 S1: inventory

Output, doc v1, adds `items[]`:

- Numbered items with structured ids `(source, kind, number, sub)`, pages
  `{first, last, basis}` and a `TextQuoteSelector` on the text layer.
- Unnumbered passages (in-text proofs, displayed claims, remarks, "in-text
  p. N" results) with `quote_hash` or `page_title_hash` ids and
  `relations.near` to the neighbouring numbered item.
- Erratum items. Each fetched erratum entry becomes an item of the errata
  source. An entry that names a book item is linked to it through
  `relations.same_as` (`docs/annotations.md` section 3.2), deterministically by
  the item grammar. An entry that names no item is listed for S3.
- The item id is the join key of every later bijection gate.
- The `exact` text of a selector is capped (section 4.6) before any id is
  minted. Full statement text stays in the workspace.

Agents:

- One passage enumerator per section, with no tools, copying snippets character
  for character, one statement per annotation. The prompt ports
  `$WIKILEAN/site/batch_annotate.py` `AGENT1_SYSTEM` from HTML to PDF text.
- One missed-item hunter per chapter. A finding is a page and a verbatim quote
  of an item absent from the list.
- One chapter completeness critic.

Polarity: this is a completeness task, where a missed item is the costly error.
A hunter's item is accepted when the exact selector check below finds its
heading or quote on its page. Refuters only contest its classification (kind,
number, numbered or not). No refuter has to fail before an item is accepted:
that rule would drop real items systematically.

Gate:

- Slot completeness at least 0.6 (`MIN_ITEM_COMPLETENESS` in
  `scripts/cflib/inference.py`, refused by `scripts/cfcmd/onboard.py`). This
  floor catches only catastrophic failure: Tao with one more missed heading
  still scores about 0.99.
- **Slot contiguity**, per section: every numbered slot from 1 to the section's
  highest number is present, or listed with a reason the maintainer signs. It
  uses the per-prefix groups that `item_completeness` already computes
  (`scripts/cflib/inference.py`). This is the deterministic defence against a
  heading the regex missed, as `Exercise 1.3.19` once was (`references/traps.md`
  B2). Comparing the item count with the headings found is not a gate here: both
  come from the same regex, and B2's fix reconciled against
  `items.json ∩ any-repo`, which a PDF-only run does not have.
- **Exact selector check.** Every `TextQuoteSelector` `exact` occurs verbatim
  in its own page's text under the `text_layer` normalisation (NFKC and
  whitespace collapse, `docs/annotations.md` section 4.6), with no length floor
  and an exact page match. The findings gate cannot establish this: it
  normalises lossily, accepts a 60% word window, binds pages within ±2 and
  refuses quotes under 25 characters (`scripts/gate/verify_findings.py`).
- Unique ids (`docs/annotations.md` section 5.2).
- **Recall calibration.** Each run deletes a planted sample of known items,
  numbered items and passages alike, from the enumerators' view and reports how
  many the hunters recover. For Tao the known items are the two companions'
  tags. For any other book they are defined on the synthetic booklet
  (`tests/e2e/make_pdf.py`), whose passages are planted, so passage recall is
  measured outside Tao too.
- Human gate: the maintainer confirms the scope, the edition and the item list.

New work: the exact selector check; the slot-contiguity gate; passage items
(source preparation emits only numbered `<Kind> n.n` headings; the item grammar
of `scripts/cflib/items.py` also parses `In-text p.<n>` labels, but nothing
extracts passages from a PDF); erratum items and their links; an `items[]`
exporter; the hunter prompt.

### 2.3 S2: geometry

Output, doc v2, adds `locations[]`: one location per item and page, each a
`PdfRegionSelector` (normalised top-left rects or quads). A `PdfRegionSelector`
names one page, and 75 of `$MT`'s 431 items span two or more pages (computed
over `$MT/pdf-overlay/data/geometry-batch-{a,b,c,d}.json`; batch d has 49 items
in 60 regions). Each location carries
`signoff {method, verifier, evidence_id, evidence_sha256, at}`. v1.1 adds the
actor kind and the checklist (section 4.3). The 100 dpi evidence crops stay in
the workspace; only their hashes enter the document.

Proposal is model-free: `pdftotext -bbox-layout` line boxes through the rules
of `$MT/scripts/overlay_geometry_from_layout.py` (heading, end, paragraph and
right-edge rules). A continuation region starts at the top of the next page's
text column, as that script already does. A passage has no heading for these
rules to find: its region is the union of the line boxes that hold its S1
quote, from the same bbox output.

Sign-off is a new `cf run` preset, `signoff-geometry`, with text and images on
the agentbox claude bridge:

- two independent vision agents per region, each answering a four-edge
  checklist: top starts at the heading (or, for a continuation, at the top of
  the page's text); bottom does not overrun the next item; left and right are
  not clipped; continuation pages are present;
- a reconciler;
- a per-page neighbour pass for overlaps and gaps.

The four-edge checklist is the fix for a measured miss. The 2026-09-08 sign-off
asked only whether a crop began at the heading, took in the next item or was
cut off at the bottom, which are vertical bounds
(`$NOTES/camera-ready-2026-09-08-tools/batch-d-visual-signoff.workflow.js`).
10 of the 60 §1.7 regions passed it wrong: nine right-edge clips and the
Exercise 1.7.13 overrun (`$MT` commits 73efa81 and fb67a73).

Gate:

- In CI: positive area inside the page; a first-page region contains its
  item's heading box (continuation and passage regions have none, and the
  neighbour pass checks them); no overlap with the next item's heading line;
  invariant I7; schema.
- Workspace-local, because the PDF and the crops never reach CI: a proposed
  `lc verify-geometry` runs the byte-regeneration check and re-hashes every
  kept crop. It writes a receipt with the input PDF sha and the crop-manifest
  sha. CI checks that the receipt is present and fresh.
- The label `manual_visual` is banned on agent work. `$MT/scripts/pdf_overlay.py`
  hard-codes it (line 271) and must accept an agent method.
- Planted-mutation calibration on every run, with a recall floor: right edge
  shrunk by 8%, bottom extended into the next item, a continuation page dropped.
- Crops must differ per region. In `$MT` batch b, 187 regions share 64 evidence
  hashes (computed), so its evidence cannot tell a right box from a wrong one on
  the same page.

Re-anchoring after a new PDF sha or new errata uses the all-or-nothing rule of
`$WIKILEAN/site/update_from_upstream.py`, plus a statement-hash check: it
re-pins only if every anchor matches and no item's statement hash changed;
otherwise it writes nothing and reports.

New work: remove the Tao constants from the generator, which are the heading
patterns (`KINDS` and the heading regex), the column bounds `X0, X1`, the 0.19
to 0.95 body band, the 0.27 to 0.30 indent window and `PAGE_PX`, a letter page
at 100 dpi; the passage proposer; the four-edge preset and prompt; crop
retention; `lc verify-geometry`. The 265-page count and the batch partitions
live in `$MT/scripts/pdf_overlay.py` and belong to S8 (section 2.9).

### 2.4 S3: statement parse and conventions

Output:

- A workspace-local informal blueprint: per item the verbatim statement, kind,
  and hypotheses and conclusion as a checklist. Book numbering is kept. This
  inverts Autoform's uncited universal-voice rule
  (`$AUTOFORM/internal/runbooks/planning.md`). For an item with a linked
  erratum, the blueprint states the corrected statement beside the printed one,
  with the conventions' disposition, so S5 formalises what the disposition says
  and not only what the page says.
- `companions/<name>/conventions.md` in the 7-section contract of
  `templates/conventions.md.example`, under its own headings, because
  `scripts/plan/render_prompts.py` matches them by keyword: 1 Total functions
  and junk values; 2 Standing type and structure conventions (set functions as
  junk-value totals or subtypes, indexed families); 3 Scope; 4 Sorry policy (the
  `exercise_policy` from `pipeline.yaml`, never defaulted, and any per-item
  exceptions); 5 Naming and attribution (the tag grammar, module granularity,
  printed-page anchors); 6 Errata disposition (one entry per linked erratum);
  7 Known-and-accepted deltas, left empty at S3, because a delta is something
  an owner has seen in Lean and accepted, and no Lean exists yet. It carries
  `confirmed_by`.
- In doc v3: provenance and a root `x_` pointer to the shas of the blueprint
  and the conventions.

Agents:

- A statement parser per item.
- A refuting verifier that compares the parse with the page image, because
  pdftotext loses notation (`references/traps.md` A4). It uses S0's whole-page
  images, not S2's regions.
- A reconciler.
- A conventions drafter.
- Two convention refuters. Each hunts for a convention that would make a book
  Definition a proxy by fiat. Their witnesses are the `elementaryMeasure :=
  volume` shape (`$MT` issue #5) and the Definition 1.2.2 volume proxy (`$MT`
  PR #9). They also test every §1 junk-value entry against the blueprint's
  hypotheses: an entry that would let a statement drop a hypothesis the book
  states is refused.
- Three flag-only holistic reviewers (`$AUTOFORM/agents/holistic-reviewer.md`,
  prose rules reversed).

Gate:

- Every item has a parse or an explicit "not a statement" reason. The parsed
  items are the domain of every later bijection (section 2.6).
- The exact selector check of section 2.2 on every hypothesis and conclusion
  fragment.
- **Notation plants.** Each run flips notation in a planted sample of parses (a
  bar, a prime, sup for limsup) and reports how many the page-image verifier
  catches.
- `exercise_policy` is set in `pipeline.yaml`.
- `conventions.md` has the template's 7 sections, an empty §7, a §6
  disposition for every linked erratum, and a `confirmed_by` stamp.
  `cf plan-run` already refuses an unconfirmed one (`templates/PRESETS.md`).
- Human gate: the maintainer signs `conventions.md` after reading the
  refuters' findings. A generator never confirms its own conventions.

New work: the blueprint format and its exporter; the parser, verifier and
refuter prompts; the notation plants; erratum linkage into the blueprint and
conventions §6.

### 2.5 S4: plan

S4 is deliberately small. It derives structure deterministically and uses
agents only for Mathlib grounding.

Output, doc v4:

- `clusters[]`, one per book section, derived from the chapter and section of
  each item. `memberships[]` place items in them.
- `x_mathlib` on each item record: a status and the grep-verified Mathlib
  declarations that ground it.
- `tasks/*.yaml`, one per section. Deliverables are the section's item ids, the
  sorry policy comes from `pipeline.yaml`, and `depends_on` is the previous
  section.
- In the workspace, a draft Mathlib stage table for `detect-proxies`.

S4 extracts no book-level dependency edges and fills no `relations.depends_on`
field: nothing downstream needs them. Task order comes from section order. The
Lean dependency graph comes from a DumpDeps dump after S5
(`scripts/detect/DumpDeps.lean.tmpl`), which gives constant-level memberships
and support edges deterministically. It emits constants only, so module docs,
`#check` commands and anonymous examples, which leave no constant, need a
source scan to become `artifacts[]`. The order-violation check is
`detect-proxies --lean`, which uses Lean dependencies, not book edges.

Agents: Autoform's mathlib-checker text (`$AUTOFORM/agents/mathlib-checker.md`),
which may cite only declarations its grep returned, and one refuter per
"in Mathlib" claim.

Gate:

- Every item is in exactly one cluster.
- Every `x_mathlib` declaration exists at the pinned Mathlib: `#check` through
  `lake env lean` in the generated repository, or a name scan of the `.olean`
  and `.ilean` files of its pinned `.lake/packages/mathlib`. WikiLean's
  `decl_exists` is not used: it answers from WikiLean's decl-index shards or the
  live mathlib4_docs declaration data, which track Mathlib master and need
  network (`$WIKILEAN/site/search_tools.py`).
- **The stage table is drafted, then signed.** Placing a Mathlib module in a
  book section needs S4's grounding, so the draft comes from the `x_mathlib`
  groundings plus section membership, in the format of
  `scripts/detect/stages.py`. The maintainer edits and signs it, as the existing
  contract requires: `cf detect-proxies --init-stage-table` writes only a
  skeleton and never guesses a module's section (`scripts/cfcmd/detect_proxies.py`).
  Today one hand-written table ships
  (`scripts/detect/mathlib_stages/tao-measure-gsm126-published.json`). Its
  completeness is checked at the S5 freeze (section 2.6).
- The task DAG validates.

New work: the cluster, membership, `x_mathlib` and task exporters; the pin
check; the stage-table drafter and its completeness check; the artifact source
scan.

### 2.6 S5: statements, attacks, freeze

Input: doc v4, the blueprint, `conventions.md`, `tasks/*.yaml`, the signed
stage table and the generated repository. Its Lake template pins the toolchain,
puts mathlib and verso at the same tag, and sets `Elab.async=false` (verso#963,
fixed in `$TML` PR #5), `autoImplicit=false` and `doc.verso=true`. A
`literate.toml` template sets `docstrings_as_text = true`, a Verso key rather
than a Lake option (`$TML/literate.toml`).

Output:

- A Lean repository with one module per source section, each with a module
  docstring.
- A **named** declaration for every `(item, sub)` in the bijection's domain,
  whose docstring opens with the backticked book tag and, for a numbered item,
  its printed page. Anonymous `example` is forbidden for items. In `$MT`, 161
  of the 204 fallback items are formalised only by anonymous examples, so no
  card can be tied to them (`$MT` PR #19).
- Exercise bodies are `sorry` stubs under either policy, except the exceptions
  signed in conventions §4; under `prove`, S6 proves them. In-text results end
  with `sorry -- TODO(lc): in-text proof`.
- doc v5 adds `declarations[]` (ids from `docs/annotations.md` section 5.1,
  `statement_sha256`, sorry class `exercise`, `gap` or `none`), key links
  derived from the tags, and `unattributed[]` with class `api` or
  `infrastructure`.
- `statement-freeze.json`, which records for every key declaration:
  - for a `def`, `abbrev`, `structure`, `class`, `inductive` or `instance`,
    the hash of the whole comment-stripped command, body included;
  - for a theorem, the hash of its comment-stripped signature;
  - the `variable`, `open` and namespace context in scope at the declaration;
  - a hash of the constant's elaborated type, which DumpDeps can emit because
    it already walks `ci.type` (`scripts/detect/DumpDeps.lean.tmpl`).

  The v1 `statement_sha256` alone is not enough. It covers the header only, so
  `def elementaryMeasure ... := <native>` and `:= volume` hash the same, a
  structure has none, and a binder added to a `variable` line changes a
  theorem's type but not its hash (`docs/annotations.md` section 5.1, "Known
  limits").

Agents, on tend and agentbox through `cf run` with a new `author` preset, once
M0b's smoke test has run:

- One author per section, chained on the predecessor's `.olean` files, as in
  the `$TML` chapter-1 skeleton campaign (a Workflow script kept in session
  history, not in a repository).
- A reviewer on a book-neutral rewrite of
  `$FFREPORT/prompts/reviewer-system-adversarial.md`. The source prompt is
  Tao-specific: it quotes the measure_theory author's blueprint slices against
  the Lean, tells the reviewer to read them from disk, and contains
  `\MathlibDecl`, a leak-canary string (section 4.5). The rewrite pairs the S3
  blueprint's statement with the Lean per declaration and keeps the verdicts
  FAITHFUL, WEAKENED, STRENGTHENED or MISTAGGED.
- Then attack-vac (codex, a cross-model lens) and attack-hunt (claude, text and
  images) on every item, witness mandatory (`templates/prompts/attack-vac.md.tmpl`,
  `templates/prompts/attack-hunt.md.tmpl`). The attack prompts carry each
  item's linked erratum and its disposition.
- Survivors go through challenge (`cf adjudicate --challenge-only`: two
  refuters, refuted by default) and adjudication (three lenses, a reconciler,
  two opposing skeptics).
- An item with a CONFIRMED_CONTENT finding goes back to its author with the
  witness.

Every prompt ported into S1 to S7 passes the leak canary before its first run.

API recall uses grep over the pinned Mathlib and, if one is built from the pin,
a local loogle; public loogle tracks Mathlib master and needs network.
leanfinder indexes only up to v4.28.0 and leansearch returned HTTP 500 when
probed on 2026-09-26, so a compiler loop is mandatory.

Gate, run out of tree:

- `lake env lean <file>`, then `lake build <module>` with a forced recompile,
  because Lake replays logs; capped with `LEAN_NUM_THREADS`.
- **Bijection.** The domain is every `(item, sub)` that S3 parsed as a
  statement. Each has exactly one key declaration through its structured id,
  and none may carry a coverage class instead. An item S3 marked "not a
  statement" is outside the domain and carries `not_formalized` or
  `out_of_scope` with the S3 reason, signed by the maintainer at the S3 gate;
  invariant I5 then forbids it a key link (`docs/annotations.md` section 6).
  Each declaration maps to an item or to an explicit `unattributed` class.
- **Key tags equal key links.** The leading docstring tag of every declaration,
  re-extracted with `scripts/adapters/docstring_tags.py`, equals the set of
  `role: key` links. Support, helper and example links live in the document
  only. This gate checks consistency. It does not check that a key link is
  right; that comes from the attacks here and from S7.
- **Printed-page anchors.** Every numbered item's docstring carries its printed
  page, and `scripts/pair/anchor_check.py` reports 0 PAGE_ANCHOR findings. This
  checks transcription only: the pages are copied from S1's inventory, the
  check compares against that same inventory, and it skips in-text rows. The
  defence against a wrong edition is S0's registry check.
- **Proxy gate.** `cf detect-proxies --lean` runs at the freeze with the signed
  S4 stage table and a DumpDeps dump, for every Definition's key declaration.
  The cheap tier's `BODY_PROXY` scan catches the literal `def elementaryMeasure
  ... := volume` shape when rows carry a body and a stage table exists
  (`scripts/detect/def_coverage.py`). The Lean tier's order-violation and
  same-stage-primitive analysis catches proxies reached through the closure,
  such as the Definition 1.2.2 case (`$MT` PR #9; `$TD/adam-detector/README.md`).
- **The stage table is complete.** Every Mathlib module in the closure of a key
  Definition's declaration is placed in the table or explicitly marked
  foundations. A module the table does not place defaults to stage 0, which
  silently turns off ORDER VIOLATION for that concept.
- **No pasted book text.** A docstring window of 12 or more words that matches
  `text.txt` is flagged (section 4.6).
- A `cf lint` pass for detectors P9 to P13 (specified in
  `references/review-patterns.md`, not implemented).
- No declaration removed, no conflict markers, and the exercise count equals
  S1's less the §4 exceptions. The exercise count is the number of Exercise
  `(item, sub)` in the bijection's domain whose key declaration has sorry class
  `exercise`. A count of this kind, falling from 19 to 18, exposed the
  `$TML` PR #5 mis-targets (`$TML` commit 1bd0fbc).
- The changed-files rule admits only the section's `.lean` file and its task
  file. Issue #9 is fixed first.
- **Freeze.** `statement-freeze.json` is written only when every surviving
  attack finding is fixed or adjudicated CONVENTION.

Docstrings are outside every frozen hash (comments are stripped, and
`docs/annotations.md` section 5.1 keeps the docstring out of the statement
hash), so S7 can edit tags without breaking the freeze.

New work: the author task template and changed-files rule; a Lean validation
command in the tend config; the bijection and key-tag gates; the freeze with
full commands, context and elaborated-type hashes; the stage-table completeness
check; the `declarations[]` exporter; the prompt rewrite; `cf lint` (not
implemented today); optionally a bridgebench adapter.

### 2.7 S6: proofs

Input: the frozen repository, `statement-freeze.json`, doc v5 and the gap
targets. Targets come from `declarations[]` with sorry class `gap`, never from
a script that scrapes docstrings. That trap put 2 exercises on the `$TML` PR #5
target list (`$TML` commit 1bd0fbc). With `exercise_policy: prove`, exercises
are targets too, in their own cost line.

Output: proved declarations merged per declaration, work harvested from killed
sessions, and doc v6 with updated sorry classes and per-proof provenance
(actor, backend, model, cost). A changelog `restated` entry is forbidden in
this stage.

Provers behind one interface:

- A Claude worker on tend and agentbox, the proposed default, not yet measured:
  `cf run --execute` has never run against that substrate (`SKILL.md`). `$TML`
  PR #5 closed its 42 in-text gaps with local Claude Code Workflow runs on
  2026-08-31 (merge d21dbac), which shows a Claude worker can do the job, not
  that this substrate can.
- The tooled autoform-worker, which has proved single results of 564 to 1,198
  lines in one session (`$BENCH/dataset/fable-stumpers-v2/HANDOFF.md`).
- marathon fill (Aristotle), only after M4 measures it.

After each merge batch: a proof-integrity judge on
`$AUTOFORM/internal/rubrics/proof_integrity.json`, and the laundering audit
below.

**What the DumpDeps gate can and cannot do.** The gate walks the constants each
declaration uses (`scripts/detect/DumpDeps.lean.tmpl`, run by
`scripts/detect/dump_deps.sh`). It must attribute compiler auxiliaries
(`_proof_n`, `match_n`, `_aux`) to their parent and follow theorem values. The
existing index filters auxiliaries out and follows repo theorems through their
type only (`scripts/detect/deps.py`), so both are new gate code. A *sorry
source* is a declaration whose own value references `sorryAx`. The gate
enforces one thing: no `sorry` comes from anywhere undeclared.

- A sorry source is accepted only if it is a declared exercise stub (kind
  Exercise, class `exercise`) or the declaration's own open gap.
- A touched declaration whose closure reaches any other sorry source is
  rejected.
- A closure that reaches a *different open gap* is rejected too. Only exercise
  stubs may be inherited. Otherwise a result "proved" by citing an unproved
  gap would count as closed.

The gate cannot catch laundering. The two laundered corollaries in `$TML` PR #5
were one-line `.out` projections of the `sorry`'d Exercise 1.1.5 TFAE
(`$TML` commit 9265eeb). Their only sorry source is a declared exercise, so the
gate accepts them. They were introduced by a hand review (0008d26), not by the
prover run (5bce2a6), as `$TML` commit f5593c4 records. Laundering needs its
own check:

- flag a proof whose term is a projection, `.mp`, `.mpr` or `.out` of a single
  `sorry`'d exercise;
- flag a proof shorter than a set token count that has a `sorry`'d dependency;
- route both to a laundering-audit fleet with a witness requirement, the
  12-agent pattern of `$TML` PR #5.

The rest of the gate forks ideas from `$AUTOFORM/servers/prover/verify.py`: the
unsafe-directive regex and the standard-axiom whitelist. Its sorry logic is
replaced by the walk above. In full:

1. Every frozen record (full commands, signatures, the `variable`, `open` and
   namespace context, and the elaborated-type hashes) is byte-identical to
   `statement-freeze.json`.
2. Clean-room `lake build` with `.olean` files deleted.
3. `cf detect-proxies --lean` reruns on every Definition's key declaration at
   every merge batch.
4. The DumpDeps sorry-source walk.
5. Literal `sorry` appears only in exercise or open-gap bodies.
6. Only standard axioms.
7. The unsafe-directive scan.
8. The exercise count does not decrease (or, under `prove`, the stub count does
   not increase), and no declaration is removed.

New work: the gate; the laundering check; per-declaration harvest (the ff-core
gaps run completed 0 of 14 tasks and stranded 1,074 inserted lines,
`$FFREPORT/FFCORE_FABLE_NOTES.md`); stall detection; backend adapters; a
backend-neutral cost record.

### 2.8 S7: attribution

S7 starts from doc v5 at the S5 freeze and runs beside S6. Its additions merge
onto S6's doc v6 to make doc v7 (section 4.1).

Output: support, helper and example `links[]` with a core `relation` (`exact`,
`special_case`, `generalization`, `invocation` or `related`,
`docs/annotations.md` section 3.4), `sub`, pages and note; `memberships[]` with
role `support`, and `artifacts[]` from the source scan of section 2.5;
provenance with method `agent_annotation` or `adjudication` and
`confirmed_by_human: false`.

Agents, per item: a curator proposes support links and reviews the key link; a
refute-by-default verifier checks every key link; a reconciler decides; a
cross-item pass checks that no declaration is key for two unrelated items, that
sibling subs agree, and that a Definition's key is a def. This generalises the
`$MT` target-refinement curation (PRs #2, #7, #17, #19).

The verifier applies the five defect shapes of `references/method.md` section 5
to every key link, each needing a witness (a book quote and a Lean signature):
total-function junk values, a witness hidden behind an existential, a proxy
definition by definitional fiat, a stronger hypothesis (a weaker theorem), and
a statement that contradicts the documented conventions. S7 runs after the
freeze and edits only docstrings and the document, so it cannot repair a
statement. A statement-shape finding goes back through the S5 loop of section
4.7: the author restates, attacks and adjudication rerun for that item, and a
new freeze entry and a changelog `restated` entry are written. What S7 fixes
itself is attribution: a tag on the wrong declaration, a key on an existential
wrapper while the named witness is untagged, a key on a Mathlib stand-in
instead of a native def or a proved bridge.

Gate:

- Key tags equal key links; the bijection; printed-page anchors on the anchor
  edition.
- Every item in the bijection's domain has a key link, and every item outside
  it has the coverage class S3 signed.
- No key link to an auto-generated projection, structure field or instance. In
  `$MT`, 142 of 1,066 primary edges targeted projections and structure fields
  (`$MT` PR #1).
- Every key declaration's frozen records match the freeze.
- Invariants I1 to I13.
- Planted mistags on every run, to measure recall: adjacent-tag swap, a printed
  page off by one, key moved to a helper or a projection. These are new
  mutation classes. `wrong_page_anchor` in `scripts/score/mutate.py` shifts a
  page by -6, 4 or -3, never by one, and edits Lean docstrings rather than
  document links.

Curate is out of scope today (`SKILL.md`). Whether naming each declaration at
authoring time makes S7 cheaper than the `$MT` curation is unmeasured.

New work: the curator, verifier and reconciler prompts; the S7 mutation
classes; the route from an S7 finding back to S5; the v6 and v7 merge.

### 2.9 S8: publish

Output, doc v8: `declarations[]` for every helper from a DumpDeps dump;
memberships from that dump and artifacts from the source scan, as the coarse
graph (pinned in `companion.graph`); `destinations[]` from the Verso site
index; `snippets[]` by the section 5.1 signature cut; `SitePageSelector`
locations; the site as a web source with `page_hashes`; a changelog against the
previous release.

Also generated: the Verso literate site (`lake query :literateHtml` and a slim
step); viewer data compiled from the document; `formalization.yaml` v0.4 and a
status table, both derived from the document and both with `--check`; a README
with provenance and a not-human-reviewed statement; CI. The PDF appears in no
output. The site is local-only by default.

Agents: the claims check (`workflows/verify-claims.workflow.js`) over the
README, landing page and `formalization.yaml` counts, and a vision smoke test
that opens the viewer on sampled regions and checks that the card shown is the
key declaration's snippet. Each run plants one wrong count in the README for
the claims check and one swapped card for the smoke test, and both must be
caught.

CI gate: `validate.py --canonical`; a changelog entry for every retired,
restated or renamed id; `lake build`; status `--check`; the schema half of the
geometry checks and a fresh geometry receipt (section 2.3); the snippets check;
URL hardening and I10; no PDF or symlink in the site; a page-size cap after
slimming; a site receipt; key tags equal key links. `$MT`'s CI runs lean-action
only (`$MT/.github/workflows/lean_action_ci.yml`).

The document and viewer data are regenerated on every build and never
re-pinned by hand. One re-pin in `$MT` touched 14 files (commit 6688082).

New work: parameterise the viewer and overlay (today `$MT/scripts/pdf_overlay.py`
and the viewer hard-code the PDF sha, 265 pages of 612x792 points, the batch
partitions `BATCH_PAGE_RANGES`, the ledger sha, the counts, the namespace
prefix, and `FORBIDDEN_MANIFEST_GRAPH_KEYS` rejects `sources`); a
document-to-viewer compiler; a site-index producer; derivation of
`formalization.yaml` and the status table; the claim and card plants; a CI
template.

### 2.10 S9: reduced final audit

Attacks are paid once, before the freeze. S9 is cheaper:

- (a) A differential against each independent companion on every item that at
  least one of them formalises. For Tao Chapter 1 that is about 385 items:
  `$TD/coverage.md` has 385 Chapter 1 rows, 225 of them covered by at least two
  of `$TML`, `$MT` and teorth/analysis and 76 by all three (computed). The
  reference companions are read only here, after generation, outside the
  sandbox.
- (b) Sampled attacks on every item that `cf recheck` labels changed or gone
  since the freeze, plus a random 10% of unchanged items as a drift check.
- (c) Challenge and adjudication for survivors, with the blind-definition-reader
  variant for definitions.
- (d) A completeness critic.

If the unchanged sample yields any new CONFIRMED_CONTENT, the full attack pass
reruns for that section.

Gate: harvest receipt count equals task count (a 762-of-763 harvest once lost a
real finding, `references/traps.md` B1); the gate runs out of tree; route
refuses UNCHALLENGED; verdict rows join real finding ids and a pinned source
sha; recheck labels are recorded. Human gate: publish-verdicts, not implemented,
moves a verdict to `human_confirmed` only by the maintainer's hand.

New work: publish-verdicts; the `verdicts[]` exporter; a recheck pin on the
freeze file, because `scripts/recheck/` fingerprints header-only signatures and
would label a changed def body unchanged; the sampled-attack selector.

## 3. The quality model

### 3.1 Principles

- Deterministic gates decide acceptance. Model review never does on its own.
- The evidence for this is measured. ff-core's original reviewer approved 174
  of 176 reviews, including five Exercise deletions; the adversarial reviewer
  written in response approved 55 of 63 (recomputed from
  `$FFREPORT/orchestrator-dbs/`; `$FFREPORT/FFCORE_FABLE_NOTES.md`). Autoform's
  jury gave faithfulness 5 to all 31 nodes it reviewed across two local
  projects (`$LEAN/alternating-analytic-formalization/review_status.json`, 20
  nodes; `$LEAN/sunflower-formalization/review_status.json`, 11 nodes).
- Every defect finding needs a witness and a quote bound to the item's page
  (the findings gate binds within ±2 pages).
- Polarity follows the cost of the error. Defect findings are refuted by
  default, because a false positive is costly. Completeness findings (missed
  items, missing geometry) are accepted on a mechanical check, because a false
  negative is costly.
- Attacks beat differentials. Two attack runs found 11 and 6 substantive items;
  three differentials found 4 of 21, 2 of 16 and 4 of 15 (`references/method.md`
  section 1). The ratio is partly definitional, because the attack gate bans
  anchor kinds.
- First-pass findings are often wrong. Adjudication refuted 4 of 10 as
  convention (`references/method.md` section 4; n=10).
- Every gate a model decides is tested with planted mutations, so it cannot
  pass vacuously: S0's spot check gets a page off by one, S3's verifier gets
  notation flips, S8's claims check gets a wrong count (section 3.4). The
  findings gate itself once passed a flipped inequality and a running head with
  a placeholder witness
  (`$NOTES/companion-faithfulness-review-2026-09-19/REVIEW.md`).
- Model-tier recall is unmeasured today. The only recall figure in this
  repository is the model-free tier on the synthetic booklet: precision 1.00,
  recall 0.50 (`README.md`). M2 and M3 measure it.

### 3.2 Swarm roles per stage

| stage | workers | refuters | reconciler | critic or consistency pass | calibration |
|---|---|---|---|---|---|
| S0 | spot-checker (10 pages) | none | none | none | a planted page off by one |
| S1 | section enumerators | classification refuters | none | chapter completeness critic; missed-item hunters | planted deletions, numbered and passages |
| S2 | two vision checkers per region | each other | yes | per-page neighbour pass | planted box mutations |
| S3 | parser; conventions drafter | page-image verifier; two convention refuters | yes | three flag-only reviewers | planted notation flips; human sign-off |
| S4 | mathlib-checkers | one per "in Mathlib" claim | none | none | the pin check is deterministic; the stage table is signed |
| S5 | section authors; reviewer | attack-vac, attack-hunt, challenge | adjudication | none | planted statement mutations and decoys (M2a) |
| S6 | provers | proof-integrity judge | none | laundering-audit fleet | planted launderings and freeze breaks (M4) |
| S7 | curators | verifiers | yes | cross-item consistency pass | planted mistags |
| S8 | none | claims-check lenses | none | numbers lens, completeness critic; vision smoke test | a planted wrong count and a swapped card |
| S9 | differential, sampled attacks | challenge | adjudication | completeness critic | 10% unchanged sample |

### 3.3 Deterministic gates

| gate | stages | exists today |
|---|---|---|
| `validate.py --canonical` | every boundary | yes, `scripts/annotations/validate.py` |
| exact selector check | S1, S3 | no; `scripts/gate/verify_findings.py` is lossy and binds within ±2 pages |
| findings quote and witness gate | S5, S7, S9 | yes, `scripts/gate/verify_findings.py` |
| offset and edition checks | S0 | offset yes; registry edition refusal no |
| slot contiguity | S1 | no; `item_completeness` computes the groups |
| harvest receipt count | S9 | yes, harvest receipts |
| region sanity (CI) and geometry receipt (local) | S2, S8 | sanity in `$MT/scripts/pdf_overlay.py`, Tao-only; receipt no |
| declaration exists at the pin | S4 | no |
| stage table signed and complete | S4, S5 | the skeleton and the unclassified refusal of `cf detect-proxies` yes; drafter and completeness check no |
| build with forced recompile | S5, S6, S8 | no |
| item-declaration bijection | S5 to S9 | no |
| key tags equal key links | S5 to S9 | extraction yes; the gate no |
| printed-page anchors | S5, S7 | the check yes, `scripts/pair/anchor_check.py`; the docstring requirement no |
| `detect-proxies --lean` | S5, S6 | the command yes; stage-table drafting no |
| pasted-text window check | S5 | no |
| statement freeze (full commands, context, elaborated types) | S5 to S9 | no |
| DumpDeps sorry-source walk and laundering check | S6 | the dump yes; the gate no |
| changed-files rule for `.lean` | S5, S6 | no; issue #9 first |
| selector length cap | S1, S8 | no |

### 3.4 Calibration bars

Every bar below is provisional until measured. A measured value replaces it and
is recorded in the stage's receipt.

| what | mutations | bar | status |
|---|---|---|---|
| S0 spot check | one page with its number shifted by one | caught on every run | unmeasured |
| S1 hunter recall | numbered items and passages deleted from the enumerators' view; the synthetic booklet outside Tao | to be set in M1 | unmeasured |
| S2 sign-off recall | right edge -8%, bottom into next item, continuation dropped; plus the 10 real §1.7 misses | 10/10 real, at least 90% planted | unmeasured |
| S3 parse verifier | notation flips (bar, prime, sup for limsup) | to be set in M2a | unmeasured |
| S5 attack recall | the four classes of `scripts/score/mutate.py` the attack gate can report (`strengthened_hypothesis`, `vacuity`, `dropped_dimension`, `reversed_inequality`) in fixture copies, with `convention_rename` decoys | recall 0.8, provisional; false positives on decoys reported | unmeasured |
| S5 other gates | `proxy_definition` against the proxy gate, including constants the stage table leaves unplaced; `missing_item` against the bijection; `wrong_page_anchor` against `anchor_check.py` | every plant caught | unmeasured |
| S6 gate and audit | re-planted launderings, mis-targeted exercises, helper with `sorry`, `native_decide`, a def body change, a `variable` edit | every plant blocked or flagged | unmeasured |
| S7 tag recall | adjacent swap, printed page off by one, key to helper or projection | at least 90% | unmeasured |
| S8 claims and smoke test | a wrong README count, a swapped card | every plant caught | unmeasured |

The attack gate admits only CONTENT, VACUITY and SUSPECTED_ERRATA
(`references/findings-schema-v2.md` section 7), so a PROXY, COVERAGE or
PAGE_ANCHOR plant can never count toward attack recall; each is scored against
the gate that can report it.

## 4. Data flow through the annotation document

### 4.1 Checkpoints and receipts

One document moves through checkpoints doc v0 to doc v9, one per stage, each
written once to `pipelines/<id>/doc.vN.json`. `pipelines/<id>/pipeline.yaml`
holds the pipeline-wide settings (`exercise_policy`, scope, consent) and the
run id of every fleet a stage planned. The release copy is
`companions/<name>/annotations.json`.

S6 and S7 both start from doc v5. S6 writes doc v6. S7 writes its additions
against v5 and merges them onto v6 to make v7. The merge is deterministic
because the two stages write disjoint fields: S6 writes declaration sorry
classes and proof provenance; S7 writes non-key links, memberships, artifacts
and attribution provenance. A merge that finds both stages changing one field
fails and asks the maintainer. In the Lean, S6 changes proof bodies and S7
changes docstrings in the same section files, so each section file has one
lock: a stage takes it for a merge, rebases onto the other stage's last merge,
reruns the four boundary gates (section 4.4) and releases it.

Each stage writes a stage receipt,
`pipelines/<id>/stages/<stage>.stage-receipt.json`, named apart from a fleet's
harvest receipt `corpus/<run-id>/RECEIPT.json`:

```
{input_doc_sha256, output_doc_sha256, pipeline_yaml_sha256,
 fleet_runs: [{run_id, run_yaml_sha256, receipt_sha256}],
 prompt_shas, model_ids, gate_result, consent, cost_usd, tokens, wall_clock_s}
```

`consent` records the `--include-source-text` consent a stage needed to send
page text or images to the shared host. Fleet cost follows issue #11's design:
harvest writes each fleet's priced totals into its `RECEIPT.json`, with prices
in `workspace.yaml`, and the stage receipt sums its fleets.

Findings live in `corpus/<run-id>/` with `RECEIPT.json`. The document refers to
them by finding id. Everything downstream is compiled from the release document
and never hand-copied: viewer data, `formalization.yaml` status, the status
table and README counts.

### 4.2 What each stage writes

| stage | records |
|---|---|
| S0 | `sources[]`, provenance, `companion` at the generated repository's first commit |
| S1 | `items[]` including erratum items and their `same_as` links, `TextQuoteSelector` locations |
| S2 | `PdfRegionSelector` locations, one per item and page, with `signoff` |
| S3 | provenance; root `x_` pointer to blueprint and conventions shas |
| S4 | `clusters[]`, `memberships[]`, `x_mathlib` on items |
| S5 | `declarations[]`, key `links[]`, `unattributed[]`, coverage for items outside the bijection's domain |
| S6 | declaration sorry classes, per-proof provenance |
| S7 | support, helper and example `links[]`, `support` memberships, `artifacts[]`, adjudication provenance |
| S8 | `destinations[]`, `snippets[]`, the site source, `SitePageSelector` locations, changelog, `companion.graph` |
| S9 | `verdicts[]` |

### 4.3 Annotations v1.1: three optional keys in two records

The format needs three optional keys, in two records, proposed as one small
minor version because the maintainer has rejected long spec changes before:

- `signoff.actor {kind: human | agent | tool}` and `signoff.checklist` (for
  geometry: top, bottom, left, right, continuation);
- `provenance.cost {usd, tokens, source}`.

Nothing else is proposed. Book-level dependency edges are not needed downstream
(section 2.5), S7 uses the core relation tokens (section 2.8), and S0 fills
`companion` from the template commit (section 2.1).

Until v1.1 is approved, the fallbacks are:

- `x_` keys, which the format allows on records, on `companion` and on the root
  (`docs/annotations.md` section 2.2);
- `signoff` takes no extension keys (the schema sets
  `additionalProperties: false`), so the actor kind goes in a verifier-string
  prefix (`agent:`, `human:`, `tool:`) and the checklist in a provenance
  record.

### 4.4 Four gates at every boundary from S5

1. `validate.py --canonical`.
2. Key tags equal key links, by re-extraction with the docstring-tag adapter.
3. The item-declaration bijection over the items S3 parsed as statements.
4. The frozen records of section 2.6 (full commands, signatures, context and
   elaborated-type hashes) equal the freeze, unless a changelog entry explains
   the change.

### 4.5 The sandbox boundary

Generation must not see the answers. The generator and review agents of S1 to
S7 must not read `$MT`, `$TML`, `$TD`, teorth/analysis (on disk or on GitHub)
or the measure_theory author's blueprint slices.

Tool allowlists cannot enforce this:

- The worker's default tools include `Bash(git:*)`
  (`scripts/run/bridge-agentbox.sh`), which permits `git clone` of
  teorth/analysis.
- S5 adds lake. `lake env <cmd>` runs any command, and a Lean file can `#eval`
  IO at elaboration time, before any gate scans it.
- The Codex bridge uses `--sandbox workspace-write`, which confines writes, not
  reads.
- A local Workflow on the laptop can read `$MT` directly.

So isolation has to come from the operating system, and what can be enforced
depends on the host:

- On the shared `cf run` host, a 96-core box (`references/runbook-server.md`
  §5), whether the maintainer holds administrative rights is unrecorded, so
  the plan assumes not. A VM user or a system container
  there needs the host's administrator; a rootless container needs
  unprivileged user namespaces, which the host may not allow. The same holds on
  the ff-core box, which the maintainer does not administer either. Until an
  administrator provisions a
  sandbox account, or generation moves to a host the maintainer administers,
  none of the rules below is enforced. That provisioning is M0c's first
  dependency.
- The sandbox holds the agents, not only their tools. Claude Code and Codex run
  Bash and Read in the model client's own process, so the client runs inside
  the sandbox with its own agentbox install and a logged-in profile, which `cf`
  cannot log in (`references/runbook-server.md` §2). A fresh account gets its
  own per-account session cap.
- Its filesystem holds only the run root. Its network reaches the model
  endpoints its hosts use (Claude and Codex) and a Mathlib cache mirror, and
  nothing else: no GitHub, no docs sites, no public loogle.
- Local Workflows are forbidden for S1 to S7 generation and sign-off unless they
  run in that sandbox. S5's challenge and adjudication are local Workflows
  (`scripts/cfcmd/adjudicate.py` prints the script to run in a Claude Code
  session), so they run there too. Calibration runs (M2, M3) run outside the
  sandbox by design, because they read the ground truth. M1 runs inside.
- A leak canary greps every transcript for strings unique to the references.
  The set covers `$MT` and `$TML` identifiers (`MeasureTheory.Tao`,
  `TaoMeasure`, `TODO(autoform)`, `namespace Companions`), teorth/analysis
  identifiers, and the blueprint TeX macros (`\MathlibDecl`, `semanticnote`,
  `mathlibnote`). Any hit voids the run.
- The `cf run` host may hold earlier read-only companion copies from
  `--include-sources` audit runs, and the measure_theory author's blueprint
  slices sat under `/home/<user>/blueprint-sections/` on the ff-core box
  (`$FFREPORT/prompts/worker-system.md`). The run root is therefore always
  fresh.

Pretraining contamination cannot be sandboxed. teorth/analysis is public.
Agreement with a reference is weaker evidence than disagreement.

### 4.6 Copyright

The PDF, full statement text, crops and the blueprint stay in the workspace.
`POLICY.md` §1 says no quotation leaves a workspace. The release document is an
exception: it ships short `exact` anchors, as `docs/annotations.md` section 11
already allows for a companion's repository. This proposal asks for that
exception to be named in `POLICY.md` §1 (section 8, question 3). Three rules
close the remaining gaps:

- A selector's `exact` text is capped at a fixed number of words. S1 applies
  the cap before any id is minted and S8 re-checks it, because the release
  document ships with the companion. There is no hashed form: v1.0 has no field
  for one, and none is proposed.
- S5 flags any docstring window of 12 or more words that matches `text.txt`,
  since docstrings render on the site.
- Finding references in the release document are redacted the way `cf share`
  redacts quotes (`scripts/share/redact.py`).

### 4.7 Loops

- A Lean change reruns S5 to S9 for the touched declarations only. The
  changelog marks retired, restated and renamed ids. `cf recheck` decides which
  verdicts reopen and which items get a sampled attack in S9.
- An S7 statement-shape finding reruns S5 for that item: restatement, attacks,
  adjudication and a new freeze entry (section 2.8).
- A PDF or errata change reruns S0 to S2 under the all-or-nothing re-anchoring
  rule with the statement-hash check. Moved items go back to S2 sign-off. An
  exact text match can survive an "if" to "iff" edit, which is why the hash
  check is required (`$WIKILEAN/docs/ROADMAP.md`).

## 5. Milestones

Efforts are estimates. The acceptance tests compare against Tao ground truth on
keys that can be joined mechanically, and adjudicate a mismatch rather than
scoring raw agreement, because the ground truth is imperfect (section 7).

| id | goal | depends on | effort (estimate) |
|---|---|---|---|
| M0a | cost tool copied and extended, stage-receipt writer, pre-fix `$TML` commit identified | none | 2 to 3 days |
| M0b | first live `cf run --execute` on tend and agentbox as a smoke test; attack cost measured on 20 items | M0a | under 1 week |
| M0c | a sandbox account provisioned by a host administrator, then isolation and egress probes | host provisioning | about 1 week after provisioning |
| M1 | S0 and S1 on the Tao PDF, Chapter 1, in the sandbox, doc v1 | M0c | 1 to 1.5 weeks |
| M2a | §1.7 statements from the PDF: S3, S4, S5 and the minimal gates | M0a, M0c, M1 | 2 to 3 weeks |
| M2b | §1.7 geometry, attribution and viewer: S2, S7, a minimal viewer | M2a | about 2 weeks |
| M3 | exporters and tag review reproduce known answers | M0a, M1 | 2 to 3 weeks, beside M2 |
| M4 | the proving gate, validated by replaying `$TML` PR #5 | M0a, the M2a freeze tool, the M3 `$TML` exporter | about 2 weeks |
| M5 | all of Tao Chapter 1: S2 to S5 and S7 | M2b, M3, M0b | about 3 weeks, plus the geometry sample |
| M6 | publish from the document | M3, M5 | 1 to 2 weeks |
| M7 | Chapter 1 end to end, S0 to S9 | M4, M5, M6 | 3 to 4 weeks |
| M8 | a second, non-Tao source | M7 | about 2 weeks |

Three asks run in parallel. Two gate nothing: the v1.1 proposal (section 4.3),
and a message for the maintainer to send the measure_theory author asking who
generated `$MT`'s front half (the 91 `ff-agent` commits, section 1.3) and for
the pipeline workspace behind it (blueprint, plan, `coverage.json`,
correspondence state). The third gates M0c: a host administrator provisions the
sandbox account (section 4.5).

### 5.1 M0a, M0b, M0c: foundations

M0a:

- The cost tool moves into this repository. Today it is the 30-line
  `reports/fleet-cost.py` of the §1.7 re-run workspace, which issue #11 names
  (as 40 lines). That workspace sits in a session scratch directory on the
  laptop, which a purge would lose, so a copy now sits in the maintainer's
  notes (`$NOTES/companion-annotations-2026-09-26/prototypes/fleet-cost.py`).
  It reads only Claude transcripts at one Opus price. It is extended to
  parse Codex session logs (attack-vac runs on codex, `scripts/plan/presets.py`)
  and to price by model id.
- It follows issue #11's design: harvest writes priced totals into each fleet's
  `RECEIPT.json`, with prices in `workspace.yaml`.
- It reproduces a recorded total within 1% from retained transcripts: the §1.7
  re-run's $358.10, or failing that the first run's $2,698.82 or the paused
  s17 run's $219.55 (issue #11). It also prices one Codex session, checked
  against a hand computation from that session's token counts and the
  published rate. There is no fallback without a recorded total.
- A stage-receipt writer (section 4.1) passes its tests.
- The `$TML` commit before the camera-ready fix stack is identified from git
  log.

M0b is the first `cf run --execute` against tend and agentbox from this
repository, so it runs as the runbook's smoke test
(`references/runbook-server.md` §4.4). attack-vac and attack-hunt each run on 20
`$TML` §1.7 items at that commit. $/task and model ids are recorded per preset.
This figure replaces the $7 placeholder in every budget line.

M0c starts once a host administrator has provisioned the sandbox account or
container (section 4.5). Before the probes, a canary file is planted at every
forbidden path on the host where the sandbox runs, so no probe can pass for
want of a target.

- Negative probes: agents in the sandbox try to list or read `/home/<user>/*`,
  other users' run roots, the planted canaries (at the blueprint-slice path and
  the companion-copy paths), and to `git clone` and `curl` teorth/analysis.
  Every attempt fails, and the leak canary finds 0 hits. The real blueprint
  slices are on the ff-core box, so a probe of them means something only in a
  sandbox on that box.
- Positive probes: one S5 author task and one challenge Workflow complete
  inside the sandbox, through its allowed egress.

### 5.2 M1: the source side

Input is only the PDF with sha `64498329...` and scope `chapters=1`. S1's
agents run inside the M0c sandbox, and the canary finds 0 hits. The run:

- infers offset 16 at confidence at least 0.99 with 265 pages (the onboarding
  transcript in `README.md` records 0.996);
- recovers every numbered label of `$MT/pdf-overlay/data/geometry-batch-{a,b,c,d}.json`,
  matched on `(page, kind, number)`. 24 of those 431 labels are free prose
  (computed); they are matched by a page-plus-quote overlap test, and every miss
  is adjudicated;
- reaches at least 99% numbered-item recall against `$TML`'s Chapter 1
  numbered docstring tags (its `Section_2_*.lean` files are out of scope), with
  every miss explained;
- recovers at least 90% of `$TML`'s Chapter 1 "In-text, book p. N" tags as
  passages near the right item and page;
- fetches the errata raw, with the Definition 1.5.11 entry present and every
  entry that names an item linked to it;
- passes `validate.py --canonical` with no warnings and writes a receipt.

Edition control: given the draft PDF (sha `62d95658...`), S0 refuses it by
registry lookup. No anchor-scoring control is run: it needs a companion's page
anchors, which a PDF-only run does not have.

### 5.3 M2a and M2b: the §1.7 vertical slice

§1.7 has the densest ground truth: `$MT` geometry batch d (49 items, 60
regions), the 10 known-bad regions, `$TML/TaoMeasure/Section_1_7_*.lean`, the
`$MT` PR #17 curation, and the §1.7 differential re-run.

M2a is the first generated Lean. It skips S2, so its doc v2 is doc v1
unchanged; S3 reads text and whole-page images, not regions. S4 runs in full,
and the maintainer signs its stage table before S5's proxy gate uses it. At the
end of M2a:

- Calibration, outside the sandbox. The four attack-reportable classes of
  `scripts/score/mutate.py` are planted into at least 30 fixture copies of
  `$TML` §1.7 statements, with `convention_rename` decoys among them (mutate.py
  refuses a real companion path without `--fixture-root`). Attack recall is
  reported with a Wilson interval, and every reported decoy counts as a false
  positive. `proxy_definition` is planted into generated §1.7 defs, some with
  constants the stage table leaves unplaced, to test the proxy gate;
  `missing_item` tests the bijection and `wrong_page_anchor` tests
  `anchor_check.py`. S3's notation plants are scored.
- Before generation, every ported prompt passes the leak canary.
- Generation, in the sandbox, canary 0 hits: `lake build` is clean; every §1.7
  statement item has a named, tagged declaration; the bijection, key-tag,
  printed-page-anchor and proxy gates pass; the exercise count matches S1.
- Comparison, outside the sandbox: for each item, does the generated key
  declaration state it? A stratified sample of 40 items is adjudicated against
  `$TML` and `$MT`, and every CONTENT claim is adjudicated. No bar is set on the
  number of confirmed CONTENT findings, because no sourced per-section count
  exists to set it against. Both the generated slice and `$TML` §1.7 are
  attacked with the same presets and the same number of runs, and the two rates
  are reported side by side.
- A disk and worktree budget per Lake worktree. `disk_budget()` in
  `scripts/run/manifest.py` counts only transferred entrypoint bytes and has no
  model of a `.lake` tree, so this is new work: a per-worktree `.lake` term, or
  a shared read-only packages mirror.

M2b:

- Sign-off recall. The pre-fix batch-d geometry (before 73efa81 and fb67a73)
  goes to the four-edge swarm, which flags 10 of 10 known-bad regions and at
  least 90% of at least 30 fresh planted mutations. Dollars per region are
  measured.
- Geometry agreement: IoU at least 0.9, matched per (item, page), against
  adjudicated post-fix batch d for at least 95% of the 60 regions, and none of
  the 10 bad shapes reproduced.
- Tags against `$MT` PR #17 (19 entries, 16 items, 32 declarations), at item
  level. Item-level agreement means the same coverage class, the same key
  relation and the same key declaration kind (theorem, def, structure).
  Generated names never match `$MT`'s, so names are not compared. Agreement is
  reported and every mismatch adjudicated.
- A document-driven viewer opens the pinned PDF on loopback. A vision smoke test
  shows the key declaration's card for 20 of 20 sampled regions.
- Receipts total the slice's cost.

### 5.4 M3: calibration at chapter scale

Exporters, promoted from `$NOTES/companion-annotations-2026-09-26/prototypes/`:

- The documents validate.
- `$MT`: 432 items, 3,565 declarations, 627 links, 509 locations, 2,884
  destinations. `$TML`: 651 items, 1,544 declarations, 1,169 links, 2,201
  locations (`docs/annotations.md` section 10).
- The `$MT` document-to-viewer compiler reproduces the item-to-key mapping of
  `$MT/pdf-overlay/data/target-refinement.json` (229 entries over 194 items),
  and the regions and destinations of
  `$MT/pdf-overlay/data/overlay-manifest.json`, which has no key field (`$MT`
  PR #17: the refinement table is not one of its inputs). Region-exact
  reproduction is not required: the export merges the 35 extra refinement
  entries into their items and replaces the quads region with a bounding box
  (section 10).
- A regression test shows that a `structure` docstring does not attach to the
  next theorem (the cause of the PR #5 mis-targets, `$TML` commit 1bd0fbc).

Tag review reproduces known answers: the 2 ATTRIBUTION_GAP rows in the
adjudication ledger (`references/method.md`); no key link to a projection or
structure field, with the 142 such primary edges of `$MT` PR #1 as the fixture;
at least 90% recall on at least 30 planted mistags; verdict kinds covering the
12 entries of `$MT/pdf-overlay/data/faithfulness-verdicts.json`.

Attack reproducibility: attack-vac and attack-hunt rerun on the pre-fix `$TML`
checkout. The camera-ready pass fixed 15 of the 16 CONTENT and VACUITY items
its five runs found; Remark 1.5.12 was left as printed on purpose
(`$TML/docs/OPEN_FAITHFULNESS_FINDINGS.md`, the tally). Of the 15, the attack
runs found 14 and a differential alone found Exercise 1.7.8 (the tally script
run per run directory), so the presets can reach at most 14. Recall over those 14 is reported per preset and labelled
**reproducibility**, not recall, because the same presets found them. The
provisional bar is 11 of 14. Recall comes from the planted defects above.

### 5.5 M4: the proving gate

A replay on a clone of `$TML` truncated at the parent of merge d21dbac, with no
later refs and no gh, targets the 48 in-text gaps.

- At least 37 close, the PR #5 automated-run count (`$TML` commit f5593c4).
- 0 changes to any frozen record; the exercise count is unchanged; the build is
  clean-room.
- The gate rejects: a planted literal `sorry` in a gap, a planted
  `native_decide`, a planted helper lemma with `sorry`, a proof that cites
  another open gap, a planted change to a frozen def body, a planted `variable`
  edit that adds a hypothesis to a frozen theorem, and the 2 re-planted
  mis-targeted exercises.
- The laundering check, not the gate, flags the 2 re-planted `.out`
  projections.
- Legitimate proofs whose `sorryAx` traces only to exercise stubs pass.
- Two backends run through the same interface and gate.
- Per-declaration harvest recovers work from a deliberately killed session.
- Cost per gap is logged. The comparison is gaps closed against PR #5, which
  has no dollar figure.

### 5.6 M5 to M8

M5, Chapter 1 statements, tags and geometry in the sandbox:

- `lake build` clean; canary 0 hits; every statement item has a named, tagged
  declaration and every other item the coverage class S3 signed; the four
  boundary gates hold.
- Every Definition is native or bridged. `$TML`'s figure is 32 of 36 native and
  4 bridged (`references/method.md` section 6).
- The `elementaryMeasure := volume` proxy and the Definition 1.2.2 proxy do not
  recur or are caught by a gate, and so do the camera-ready CONTENT and VACUITY
  items that fall in Chapter 1.
- Geometry IoU at least 0.9, matched per (item, page), for at least 95% of the
  reference regions. The reference is batch d's 60 adjudicated regions plus a
  stratified sample of 60 regions from batches a to c, adjudicated first:
  batches a to c were never reviewed, and their generator is unconfirmed
  (section 1.3). That adjudication is sized here, 60 regions, and priced once
  M2b has measured the cost per region.
- Links against `$MT`'s 229 target-refinement entries, by M2b's item-level
  measure.
- Statement agreement by the M2a sampling method.
- The Chapter 1 baseline rate is recomputed on a Chapter 1 denominator. The
  camera-ready figure of 16 CONTENT and VACUITY items is a tally over all five
  runs (`$TML/docs/HANDOFF.md` at 01d77e6, section 7), not over the two
  763-item attack runs, whose corpora included 42 Chapter 2 numbered items (the
  same file, section 8). The figure also counts sub-parts and includes a
  Chapter 2 defect.
- Measured attack $/item stays within 25% of M0b, or the maintainer re-approves
  the budget.

M6, publish: the site builds with the slim step and no page exceeds the cap; at
least 90% of named declarations resolve to an exact destination and every key
declaration does (`$TML`'s site-index export got 1,505 exact and 39 fallback,
`docs/annotations.md` section 10); the viewer smoke test passes on 50 of 50
regions and catches its planted card; `formalization.yaml` and the status table
are derived and checked in CI; the claims check reports 0 WRONG and catches its
planted count; no PDF bytes in any output.

M7, Chapter 1 end to end: every item has a key link or its signed coverage
class; build green; no in-text `sorry` beyond an adjudicated hard-tail list,
reported against `$TML`'s 6 hard gaps (`$TML/docs/HANDOFF.md` section 3); under
`prove`, no exercise stub outside conventions §4's signed exceptions; the
reduced S9 leaves 0 UNCHALLENGED; the 10% unchanged sample yields 0 new
CONFIRMED_CONTENT or its sections are re-attacked; the verdict layer is
compared with `$MT`'s 12 entries at item level; the cost ledger totals every
stage; `cf recheck` after a one-declaration restatement marks exactly the
affected verdicts. Edge recall against the `$MT` ledger is not measured:
generated declarations have no mapping to `$MT` declaration ids.

M8, generality: grep of the pipeline code for `265`, `64498329`,
`MeasureTheory.Tao`, `TaoMeasure` and `1.7` returns nothing; on the synthetic
booklet (`tests/e2e/make_pdf.py`) with its 8 planted defects plus planted
geometry and tag mutations, model-tier recall is reported for the first time;
one chapter of a licensed second book reaches M5-level acceptance, with
adjudication-only comparisons.

## 6. Costs

### 6.1 Measured anchors

| figure | source | caveat |
|---|---|---|
| first full differential run: $2,698.82 for 383 tasks | issue #11 for the dollars; the cost report in the §1.7 re-run workspace for the task count | Opus 5 list prices; cache reads 85% of the bill; $7.05 a task |
| §1.7 differential re-run: $358.10 for 47 tasks | the same two sources | same pricing; $7.62 a task; a first attempt, s17, was paused at 4 of 47 tasks after $219.55 |
| attack cost per task | none | unmeasured; attack-vac runs on codex, which the $7.05 to $7.62 does not price |
| ff-core Tao campaign: $1,639.46 recorded | `$FFREPORT/orchestrator-dbs/` | the notes say about $985 or $1,050; input-token accounting was broken (about 74 input tokens and no cache per session) |
| ff-core pilot: $528.14 | `$FFREPORT/orchestrator-dbs/` | the section count comes from another source; refined an existing draft |
| ff-core gapfill: $304.31 recorded; 23 gaps closed at a $102.44 mid-run snapshot | `$FFREPORT/orchestrator-dbs/` (recorded); `$FFREPORT/FFCORE_FABLE_NOTES.md`, the 07:20Z entry (snapshot) | 81 to 90% infrastructure mortality over the run; the separate gaps3 run reached 96% (`$FFREPORT/INDEX.md`) |
| `$TML` PR #5: 42 proofs | `$TML` merge d21dbac | local Claude Code Workflow runs on a Max subscription; no dollar figure |
| WikiLean enumeration and matching: $1.32 to $1.54 per article | `$WIKILEAN/docs/token_budget.md` | article-sized units, not book sections |
| adjudication of 10 findings: 225 agents, 5.9 h | `references/workflow-patterns.md` | local Workflow, limit failures included |

### 6.2 The Chapter 1 envelope

These are estimates. The S5 attack and S9 lines use $7 a task as a placeholder
until M0b; the recorded differential rates are $7.05 and $7.62 a task, and $7
is not a bound. Item counts are Chapter 1's: about 605 items and 226 exercise
items (computed over the `$TML` prototype export in
`$NOTES/companion-annotations-2026-09-26/prototypes/` and over `$TML`'s
Exercise tags; the 651-item total includes Chapter 2).

| stage | estimate | basis |
|---|---|---|
| S0 | under $2 | 11 image calls |
| S1 | $20 to $60 | about 30 section enumerators plus hunters and critics |
| S2 | unpriced | about 509 regions times 3 calls; M2b measures $/region |
| S3 | unpriced, plus under $20 for conventions | about 605 items times 2 or more calls |
| S4 | $150 to $500 | mathlib-checks and their refuters only; no dependency-edge extraction |
| S5 authoring | $500 to $1,500 | ff-core figures above |
| S5 attacks | about $8,500 at the placeholder | 2 presets times about 605 items times $7 |
| S6 | $1,000 to $2,500 | about 250 in-text gaps; under `prove`, about 226 more Chapter 1 exercise items (`$TML` exercise tags), roughly doubling it |
| S7 | $600 to $2,000 | about 3 agents per item; `$MT` PR #19's curation ran 374 agents for 207 items (207 curators, 166 verifiers where a named candidate existed, one consistency pass; counted from its workflow, since the PR body gives no total) |
| S8 | $20 to $80 | one claims check plus the smoke test |
| S9 | about $2,700 plus sampled attacks | about 385 differential tasks at $7 |

The listed lines sum to about $13,500 to $17,800. Not in that sum: S2, S3
parsing, the M0b, M2, M3 and M4 calibration runs, adjudication in M2 and M5
(including M5's geometry sample), S9's sampled attacks, and exercise proving
under `prove`.

### 6.3 Wall clock

Server tasks take about 10 to 15 minutes each (`references/runbook-server.md`
§5). The differential preset runs 8 workers and 5 reviewers; the attack presets
run 5 and 3 (`_ATTACK_COMMON` in `scripts/plan/presets.py`). The cap is 16
concurrent hosted sessions per account (`references/traps.md` C3), on a host
shared with other users.

| stage | tasks | fleet time (estimate) |
|---|---|---|
| S5 attacks | about 1,210 | 40 to 61 h with the two presets one after the other; 20 to 30 h with both at once, which takes 2 × (5 + 3) = 16 sessions, the whole per-account cap, so nothing else runs on the account meanwhile |
| S9 differential | about 385 | 8 to 12 h at 8 workers |
| S2 sign-off | about 1,500 image calls | unmeasured; M2b measures it |
| S7 | about 1,800 calls | unmeasured |

### 6.4 Recording

Receipts sum into a per-campaign cost file in the workspace. Every stage
has a cap. A stage that would exceed its cap stops at a drain point and asks the
maintainer.

## 7. Risks

- **The ground truth is imperfect.** `$MT`'s ledger marked all 77 nodes
  verified and missed two proxies. 10 of 60 §1.7 regions passed sign-off wrong,
  and batches a to c were never reviewed. `$MT`'s target refinement is
  unconfirmed, and 238 of 432 items stay on the cluster fallback
  (`docs/annotations.md` section 10). `$TML/scripts/review_audit.py` reports
  264 findings at f158509 (A1 175, A3 42, A6 20, A4 16, A5 6, A2 5), and A3's
  entries flag whole numbered ranges as absent repo-wide. Every acceptance test
  adjudicates mismatches.
- **Leakage.** The sandbox is untested until M0c, and M0c cannot start until a
  host administrator provisions it. Pretraining contamination cannot be
  removed.
- **Rubber-stamp review** (section 3.1). Deterministic gates and planted
  mutations are the mitigation. The provisional bars (0.8, 11 of 14) may have
  to move.
- **Cost.** Attack cost is unmeasured and dominates the envelope. The cost tool
  exists only in a session scratch directory today.
- **Unknown provenance** of `$MT`'s front half: its generator is unconfirmed
  (section 1.3). The measure_theory author's workspace could change the data
  model or save weeks.
- **Substrate fragility.** Open issues #6, #7, #9, #10, #11; `cf run --execute`
  has never run against tend and agentbox from this repository, so M0b is its
  smoke test; tend was checked only shallowly and still enforces strict tool
  schemas; fleets died at 129 of 225 and 70 of 114 on session limits
  (`references/traps.md` D1). Lake builds per worktree need a thread cap: on
  the ff-core box (16 cores, 30 GB) ten workers with unbounded threads reached
  load 52 (`$NOTES/CLOUDFLARE-SERVER-HANDOFF.md` sections 1 and 5), and on the 96-core
  `cf run` host the limit is the session cap, not CPU
  (`references/runbook-server.md` §5).
- **The laundering check may miss shapes** it was not written for, and the
  DumpDeps walk may misattribute auxiliaries or `have ... := sorry`. The M4
  plants must stay blocking for every backend.
- **v1.1 may be refused.** The fallbacks then hide the actor kind and the cost
  from document-only consumers.
- **Tooling pins drift**, as probed on 2026-09-26: leanfinder stops at v4.28.0
  while the companions pin v4.33.0; leansearch returned HTTP 500; bridgebench
  is pinned to v4.28.0; all six Autoform MCP servers failed to connect; Autoform
  is installed at 0.4.4 with 0.6.1 checked out.
- **Overfitting to Tao.** The item grammar, the shared-counter numbering and the
  §1.7 regexes are all Tao-shaped. M8 is the check.
- **Copyright.** `$TML` is private at Mathlib's request. Generated sites stay
  local by default. The release document's capped anchors need a named
  exception to `POLICY.md` §1 (section 4.6).
- **Human bottleneck and scale.** The maintainer signs scope, conventions, the
  stage table, coverage classes, waivers and human-confirmed verdicts. Verso
  pages reach 449 to 536 MB before slimming (`$MT` local site; `$TML/README.md`).

## 8. Open questions for the maintainer

1. **Exercise policy.** Should the pipeline prove exercises, as `$MT` does, or
   stub them, as `$TML` does? `$TML`'s reason is the reader: the book leaves
   exercises to the reader, and so does the companion (`$TML/README.md`).
   Under `prove`, a published companion is a book of worked solutions
   (question 12). The choice also changes S6's targets, its invariant and its
   cost. Either way, conventions §4 lists signed per-item exceptions. There is
   no default until this is answered.
2. Will you send the measure_theory author the request: who generated `$MT`'s
   front half (the 91 `ff-agent` commits), and may the pipeline see the
   workspace behind it?
3. Annotations v1.1: approve the three keys, or keep the fallbacks? And may
   `POLICY.md` §1 name the release document's capped anchors as an exception?
4. **Where `lc` lives.** The default is this repository after the
   lean-companion rename, with `CLAUDE.md` §4, `CONTRIBUTING.md` invariant 3
   and `SKILL.md` rule 10 narrowed in the same commit so that "never write to a
   companion repo" means a repository the pipeline did not generate. The
   alternative is a separate repository that calls this one's gates by path and
   leaves those rules as they are. Does the rename happen before or after M0?
5. Budget: what per-chapter cap applies once M0b measures attack cost? If the
   cap binds, do pre-freeze attacks cover every item, or in-text items and
   Definitions first?
6. Substrate: should all fleets run on the shared host through `cf run`, with
   local Workflows kept for calibration outside the sandbox? Who provisions the
   sandbox account there, or should generation run on a host you administer? Is
   the 16-session cap acceptable at chapter scale, given that both attack
   presets at once take all of it (section 6.3)?
7. Who signs generator-authored conventions and moves verdicts to
   `human_confirmed`? Is there a second human for a generated companion?
8. Module granularity and set functions: one file per subsection with junk-value
   total functions (tags round-trip mechanically), or thematic modules with
   subtype set functions?
9. Viewer: replace `$MT`'s viewer with a document-driven one, or keep compiling
   `$MT`-format viewer data? Offer the M3 `$MT` export to its author as a PR?
10. Aristotle: an S6 backend after M4 measures it, or dropped?
11. Should acceptance require a cross-model verifier? attack-vac already runs on
    codex; `SKILL.md` rule 8 puts subagents on Opus.
12. Which licensed second source for M8? Should generated companions ever be
    published?
