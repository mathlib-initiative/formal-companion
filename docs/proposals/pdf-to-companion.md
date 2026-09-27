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
are this repository's CLI and exist unless the sentence says otherwise.

## 0. Conventions of this document

Paths into this repository are written as they are (`scripts/...`). Paths into
other trees start with a placeholder, because an absolute path on one machine
means nothing to a second reader (`CLAUDE.md` S1). The placeholders are:

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
| `$NOTES` | the maintainer's notes directory (camera-ready tools, annotation prototypes) | not a repository |

"Tao" means the published edition of the measure theory book, sha256
`64498329...`, 265 pages (`registry/sources.yaml`). Every figure carries its
source in parentheses. A figure marked *estimate* was not measured. A figure
marked *computed* was counted read-only over the named files on 2026-09-26.

People are named by role, as `CLAUDE.md` S1 asks: **the maintainer** owns this
repository and signs the human gates, and **the measure_theory author** wrote
`$MT`.

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

- No stage writes Lean or runs lake. The generated tend config sets
  `seed_worktree_build: false` (`templates/tend/config.yaml.tmpl`).
- The changed-files rule admits only `findings/<id>.json` and
  `tasks/<id>.yaml` (`scripts/gate/check_changed_files.py`). A worker that
  writes `.lean` is refused.
- There is no PDF-only entry point. `cf onboard` needs a companion repository
  (`scripts/cfcmd/onboard.py`). `cf sources add` needs its `--for` companion
  (`scripts/cfcmd/sources.py`). Scope is derived from the companion's tags, so
  an unwritten companion has none (`scripts/cflib/inference.py`).
- There is no geometry, site, viewer or tag curation. Curate, lint and
  publish-verdicts are documented in `SKILL.md` and not implemented.
- Nothing reads or writes the annotation format yet (`docs/annotations.md`
  section 11).
- Cost is not recorded. tend's counters read zero under agentbox
  (`references/traps.md` C2; issue #11).
- Issues #6, #7, #9, #10 and #11 are open. Issue #9 (a copy-status line
  bypasses the changed-files check) becomes a real hole once `.lean` writes are
  allowed.

### 1.2 In other trees

| tree | what the pipeline can take | what it lacks |
|---|---|---|
| tend and agentbox | the worker, gate, reviewer and merge loop that `cf run` already drives | not shipped or pinned (`SKILL.md`); tend's model layer is text-only |
| `$AUTOFORM` | the honesty-gate structure in `$AUTOFORM/servers/prover/verify.py`; the rubric texts `$AUTOFORM/internal/rubrics/proof_integrity.json` and `faithfulness.json`; `$AUTOFORM/agents/mathlib-checker.md`; the planning-swarm pattern | no Lean statements stage ("planned, not built", `$AUTOFORM/docs/pipeline.md`); its gate rejects any literal `sorry` and any `sorryAx`; its prose rules drop book numbering; all six of its MCP servers failed to connect on 2026-09-26 |
| `$MT` | output-stage scripts: `$MT/scripts/overlay_geometry_from_layout.py`, `$MT/scripts/pdf_overlay.py`, `$MT/scripts/extract_declaration_snippets.py`, `$MT/scripts/build_combined_site.py`, `$MT/scripts/official_pdf_workflow.py`, `$MT/pdf-overlay/viewer/` | all hard-coded to Tao (265 pages, the PDF and ledger shas, counts 77/2884/5093, the `MeasureTheory.Tao.` prefix); its front half is external and absent |
| `$TML` | the skeleton-campaign pattern; the PR #5 verification recipe; `$TML/scripts/update_status_table.py`, `$TML/scripts/build_site.sh`, `$TML/scripts/slim_large_pages.py` | no geometry, no machine-readable verdicts |
| `$WIKILEAN` | the enumerator prompt (`$WIKILEAN/site/batch_annotate.py`, `AGENT1_SYSTEM`); `decl_exists` (`$WIKILEAN/site/search_tools.py`); all-or-nothing re-anchoring (`$WIKILEAN/site/update_from_upstream.py`) | its two agents are sequential, not adversarial |
| `$FFCORE`, `$FFREPORT` | measured failure modes; the adversarial reviewer prompt `$FFREPORT/prompts/reviewer-system-adversarial.md`; the faithfulness-first worker text `$FFCORE/src/ff_core/prompts/worker/v6/system.md` | frozen; text-only model layer (`$FFCORE/src/ff_core/llm/models/messages.py`) |
| `$MARATHON` | an Aristotle fill backend (`$MARATHON/marathon/fill.py`) and leaves-first ordering (`$MARATHON/marathon/prover/dag.py`) | the prover has been off since 2026-07-18 (`.marathon/conductor/prover-control.json` in the live project); no measured fill rate; pricing undocumented |
| `$BENCH` | bridgebench, a statement-equivalence checker | uncalibrated, rung 5 off, pinned to Lean v4.28.0 |

### 1.3 Two corrections to earlier notes

The measure_theory companion proves every exercise and has 0 `sorry` (computed
over `$MT/MeasureTheory/`). Exercises as permanent `sorry` is tao-measure-lean's
convention: 514 `sorry`, of which 508 are exercise stubs and 6 are in-text gaps
(`$TML/scripts/update_status_table.py` classification at f158509).

ff-core did not produce `$MT`'s ledger, geometry batches a to c or verifier
ids. The verifier ids are UUIDv7, and ff-core never mints UUIDv7
(`$FFCORE/src/ff_core/orchestrator/control_store.py`). ff-core's model layer
cannot read images, `$MT` has never had a `tasks/` directory, and its history
has none of ff-core's merge commits. The generator of that front half (a
blueprint `.tex`, a plan, `coverage.json`, a correspondence state) is
unidentified. `$NOTES/companion-annotations-2026-09-26/REPORT.md` lines 16,
112, 197 and 261 say otherwise and should be corrected.

## 2. The pipeline

Ten stages, S0 to S9, turn one PDF into a companion. Every stage reads one
checkpointed annotation document and writes the next one,
`runs/<run-id>/doc.vN.json`. Every stage also writes a receipt (section 4.1).
No stage trusts its own swarm. Each stage ends in a deterministic, fail-closed
gate, and every stage that uses models also has an adversarial pass.

**One decision comes first.** The two Tao companions disagree on exercises:
`$MT` proves them and `$TML` stubs them (section 1.3). "Like the measure_theory
companion" could mean either its artifact shape with stubbed exercises, or its
artifact shape with proved exercises. The pipeline therefore has no default.
`run.yaml` carries an `exercise_policy` field (`stub` or `prove`) that starts
unset, and S3 refuses to proceed while it is blank. Section 8 asks the question.

| stage | reads | adds to the document | swarm | gate, in short | built today |
|---|---|---|---|---|---|
| S0 ingest and pin | one PDF, optional errata URL, licence note, explicit scope | `sources[]`, provenance | one image spot-checker | offset confidence, registry edition check, raw errata, no PDF in git | source preparation only |
| S1 inventory | doc v0, prepared text | `items[]` with structured ids and short `TextQuoteSelector` anchors | enumerators, missed-item hunters, a completeness critic | quote gate, unique ids, count reconciliation, planted-deletion recall | numbered items only |
| S2 geometry | doc v1, the PDF | `locations[]` (`PdfRegionSelector`) with `signoff` | two vision checkers per region, a reconciler, a neighbour pass | region sanity, planted-mutation recall, local geometry receipt | the §1.7 generator, Tao-only |
| S3 statement parse and conventions | doc v2, per-item text | provenance, a root `x_` pointer to the blueprint and conventions shas | parser, refuter, reconciler; conventions drafter and two refuters; three flag-only reviewers | every item parsed, 7-section conventions signed by the maintainer | the conventions contract only |
| S4 plan | doc v2, blueprint, conventions, pinned Mathlib | `clusters[]` (book sections), `memberships[]`, `x_mathlib` on items; `tasks/*.yaml` | mathlib-checkers and refuters | every cited declaration exists at the pin; task DAG valid | no |
| S5 statements | doc v3, tasks, Lake template | `declarations[]`, key `links[]`, `unattributed[]`; `statement-freeze.json` | one author per section, a reviewer, then attack-vac and attack-hunt on every item, challenge, adjudication | build, bijection, key-link tag gate, anchors, `detect-proxies --lean`, freeze | attack presets only |
| S6 proofs | the frozen repo | proved declarations, per-proof provenance | provers, a proof-integrity judge, a laundering-audit fleet | freeze unchanged, clean-room build, DumpDeps sorry-source gate, laundering check | no |
| S7 attribution | doc v4/v5, frozen Lean, page images | support, helper and example `links[]`, `artifacts[]` | curator, refute-by-default verifier, reconciler, consistency pass | key-link tag gate, bijection, anchors, no key on a projection, planted-mistag recall | no |
| S8 publish | doc v6, built Lean | `destinations[]`, `snippets[]`, the site source, changelog | claims check, vision smoke test | CI suite, no PDF in output, page-size cap | no |
| S9 reduced audit | doc v7, the S5 attack corpus, independent companions | `verdicts[]` | differential, sampled attacks, challenge, adjudication, completeness critic | receipts, route refuses unchallenged, pinned verdicts | differential and attack fleets, recheck |

### 2.1 S0: ingest and pin

Input is one PDF, an optional errata URL, a licence note and an explicit scope
(`chapters=...`). No companion repository is needed. Scope must be explicit,
because `scripts/cflib/inference.py` derives it from tags that do not exist
yet.

Output, doc v0:

- `companion {name, commit: null}`.
- One `pdf` source with sha256, bytes, `physical_page_count`,
  `printed_page_offset`, page sizes and the text layer `{extractor: pdftotext,
  version}`.
- A provenance record (actor `tool`, method `pipeline`).
- Workspace-local and never committed: `sources/<id>/{text.txt, pages/,
  images/, errata.*, meta.json}`.
- A fresh sandbox run root (section 4.5) holding only those files, the
  document and a Lake template.

Agents: one image-capable spot-checker compares the rendered running head and
page number of 10 random pages with the inferred offset. It runs on the server
through the agentbox claude bridge, or through `claude -p` locally, because S0
reads no reference companion.

Gate, fail-closed:

- Offset confidence at least 0.9 (`scripts/cflib/inference.py`; `CLAUDE.md`
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
  dropped the entry for Definition 1.5.11 (`references/method.md` section 8;
  `references/traps.md` A3).
  The entry-count comparison of design A is dropped: both counts came from the
  same parser, so it could not fail.
- The document passes `validate.py --canonical`.
- The PDF is in no git tree.
- The spot check agrees on 10 of 10 pages, or a manual override is stamped
  `*_method: manual`.

New work: `lc ingest --pdf <file> --scope chapters=...`; a `sources[]` exporter;
the sandbox run-root builder whose transfer manifest lists only workspace-local
files, the document and the Lake template.

### 2.2 S1: inventory

Output, doc v1, adds `items[]`:

- Numbered items with structured ids `(source, kind, number, sub)`, pages
  `{first, last, basis}` and a `TextQuoteSelector` on the text layer.
- Unnumbered passages (in-text proofs, displayed claims, remarks, "in-text
  p. N" results) with `quote_hash` or `page_title_hash` ids and
  `relations.near` to the neighbouring numbered item.
- The item id is the join key of every later bijection gate.
- The `exact` text of a selector is capped (section 4.6). Full statement text
  stays in the workspace.

Agents:

- One passage enumerator per section, with no tools, copying snippets character
  for character, one statement per annotation. The prompt ports
  `$WIKILEAN/site/batch_annotate.py` `AGENT1_SYSTEM` from HTML to PDF text.
- One missed-item hunter per chapter. A finding is a page and a verbatim quote
  of an item absent from the list.
- One chapter completeness critic.

Polarity: this is a completeness task, where a missed item is the costly error.
A hunter's item is accepted when the quote gate verifies its heading or quote on
its page. Refuters only contest its classification (kind, number, numbered or
not). Design A required two refuters to fail before accepting an item, which
would have dropped real items systematically.

Gate:

- Slot completeness at least 0.6 (`scripts/sources/prepare_source.py`).
- The whitespace-tolerant heading regex; a regex that missed `Exercise
  1.3.19` once cost an item (`references/traps.md` B2).
- Every quote verbatim on its page (`scripts/gate/verify_findings.py` quote
  gate).
- Unique ids (`docs/annotations.md` section 5.2).
- Item count equals headings found, or the stage fails loudly
  (`scripts/plan/make_tasks.py` reconciliation).
- **Recall calibration.** Each run deletes a planted sample of known items from
  the enumerators' view and reports how many the hunters recover.
- Human gate: the maintainer confirms the scope, the edition and the item list.

New work: passage items (source preparation emits only `<Kind> n.n` and
`In-text p.<n>`, `SKILL.md`); an `items[]` exporter; the hunter prompt.

### 2.3 S2: geometry

Output, doc v2, adds `locations[]`: one `PdfRegionSelector` per item
(normalised top-left rects or quads). Each carries
`signoff {method, verifier, evidence_id, evidence_sha256, at}`. v1.1 adds the
actor kind and the checklist (section 4.3). The 100 dpi evidence crops stay in
the workspace; only their hashes enter the document.

Proposal is model-free: `pdftotext -bbox-layout` line boxes through the rules
of `$MT/scripts/overlay_geometry_from_layout.py` (heading, end, paragraph and
right-edge rules).

Sign-off is a new `cf run` preset, `signoff-geometry`, with text and images on
the agentbox claude bridge:

- two independent vision agents per region, each answering a four-edge
  checklist: top starts at the heading; bottom does not overrun the next item;
  left and right are not clipped; continuation pages are present;
- a reconciler;
- a per-page neighbour pass for overlaps and gaps.

The four-edge checklist is the fix for a measured miss. The 2026-09-08 sign-off
checked vertical bounds only, and 10 of the 60 §1.7 regions passed it wrong:
nine right-edge clips and the Exercise 1.7.13 overrun (`$MT` commits 73efa81
and fb67a73).

Gate:

- In CI: positive area inside the page, the region contains its heading box, no
  overlap with the next item's heading line, invariant I7, schema.
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

New work: remove the Tao constants from the generator (heading patterns, batch
partitions, 265 pages, 612x792); the four-edge preset and prompt; crop
retention; `lc verify-geometry`.

### 2.4 S3: statement parse and conventions

Output:

- A workspace-local informal blueprint: per item the verbatim statement, kind,
  and hypotheses and conclusion as a checklist. Book numbering is kept. This
  inverts Autoform's uncited universal-voice rule
  (`$AUTOFORM/internal/runbooks/planning.md`).
- `companions/<name>/conventions.md` in the 7-section contract
  (`templates/conventions.md.example`): exercise policy (copied from
  `run.yaml`, never defaulted), set functions (junk values or subtypes),
  indexed families, module granularity, tag grammar, errata disposition,
  accepted deltas. It carries `confirmed_by`.
- In the document: provenance and a root `x_` pointer to the shas of the
  blueprint and the conventions.

Agents:

- A statement parser per item.
- A refuting verifier that compares the parse with the page image, because
  pdftotext loses notation (`references/traps.md` A4).
- A reconciler.
- A conventions drafter.
- Two convention refuters. Each hunts for a convention that would make a book
  Definition a proxy by fiat. Their witnesses are the `elementaryMeasure :=
  volume` shape (`$MT` issue #5) and the Definition 1.2.2 volume proxy (`$MT`
  PR #9).
- Three flag-only holistic reviewers (`$AUTOFORM/agents/holistic-reviewer.md`,
  prose rules reversed).

Gate:

- Every item has a parse or an explicit "not a statement" reason.
- The quote gate on every hypothesis and conclusion fragment.
- `exercise_policy` is set in `run.yaml`.
- `conventions.md` has all 7 sections and a `confirmed_by` stamp. `cf plan-run`
  already refuses an unconfirmed one (`templates/PRESETS.md`).
- Human gate: the maintainer signs `conventions.md` after reading the
  refuters' findings. A generator never confirms its own conventions.

### 2.5 S4: plan

S4 is deliberately small. It derives structure deterministically and uses
agents only for Mathlib grounding.

Output, doc v3:

- `clusters[]`, one per book section, derived from the chapter and section of
  each item. `memberships[]` place items in them.
- `x_mathlib` on each item record: a status and the grep-verified Mathlib
  declarations that ground it.
- `tasks/*.yaml`, one per section. Deliverables are the section's item ids, the
  sorry policy comes from `run.yaml`, and `depends_on` is the previous section.

Dropped from design A: LLM-extracted book-level dependency edges and the
`relations.depends_on` field. Nothing downstream needs them. Task order comes
from section order. The Lean dependency graph comes from a DumpDeps dump after
S5 (`scripts/detect/DumpDeps.lean.tmpl`), which gives memberships, artifacts
and support edges deterministically. The order-violation check is
`detect-proxies --lean`, which uses Lean dependencies, not book edges.

Agents: Autoform's mathlib-checker text (`$AUTOFORM/agents/mathlib-checker.md`),
which may cite only declarations its grep returned, and one refuter per
"in Mathlib" claim.

Gate:

- Every item is in exactly one cluster.
- Every `x_mathlib` declaration exists at the pinned Mathlib (`decl_exists`,
  `$WIKILEAN/site/search_tools.py`).
- The Mathlib stage table for `detect-proxies` is generated from section order
  in the format of `scripts/detect/stages.py`. Today one hand-written table
  ships (`scripts/detect/mathlib_stages/tao-measure-gsm126-published.json`).
- The task DAG validates.

### 2.6 S5: statements, attacks, freeze

Input: doc v3, the blueprint, `conventions.md`, `tasks/*.yaml` and a Lake
template. The template pins the toolchain, puts mathlib and verso at the same
tag, and sets `Elab.async=false` (verso#963, fixed in `$TML` PR #5),
`autoImplicit=false`, `doc.verso=true` and `lean_lib docstrings_as_text=true`.

Output:

- A Lean repository with one module per source section, each with a module
  docstring.
- A **named** declaration for every `(item, sub)`, whose docstring opens with
  the backticked book tag. Anonymous `example` is forbidden for items. In
  `$MT`, 204 of 207 uncovered overlay items could not be tagged because they
  are anonymous examples (`$MT` PR #19).
- Exercise bodies follow `exercise_policy`. In-text results end with
  `sorry -- TODO(lc): in-text proof`.
- doc v4 adds `declarations[]` (ids from `docs/annotations.md` section 5.1,
  `statement_sha256`, sorry class `exercise`, `gap` or `none`), key links
  derived from the tags, and `unattributed[]` with class `api` or
  `infrastructure`.
- `statement-freeze.json`: comment-stripped signature hashes.

Agents, on tend and agentbox through `cf run` with a new `author` preset:

- One author per section, chained on the predecessor's `.olean` files, as in
  the `$TML` chapter-1 skeleton campaign (a Workflow script kept in session
  history, not in a repository).
- A reviewer on `$FFREPORT/prompts/reviewer-system-adversarial.md`: quoted
  book-versus-Lean pairs per declaration, verdicts FAITHFUL, WEAKENED,
  STRENGTHENED or MISTAGGED.
- Then attack-vac (codex, a cross-model lens) and attack-hunt (claude, text and
  images) on every item, witness mandatory (`templates/prompts/attack-vac.md.tmpl`,
  `templates/prompts/attack-hunt.md.tmpl`).
- Survivors go through challenge (`cf adjudicate --challenge-only`: two
  refuters, refuted by default) and adjudication (three lenses, a reconciler,
  two opposing skeptics).
- An item with a CONFIRMED_CONTENT finding goes back to its author with the
  witness.

API recall uses loogle and grep over the pinned Mathlib. leanfinder indexes
only up to v4.28.0 and leansearch returned HTTP 500 when probed on
2026-09-26, so a compiler loop is mandatory.

Gate, run out of tree:

- `lake env lean <file>`, then `lake build <module>` with a forced recompile,
  because Lake replays logs; capped with `LEAN_NUM_THREADS`.
- **Bijection.** Each `(item, sub)` has exactly one key declaration through its
  structured id. Each declaration maps to an item or to an explicit
  `unattributed` class.
- **Key tags equal key links.** The leading docstring tag of every declaration,
  re-extracted with `scripts/adapters/docstring_tags.py`, equals the set of
  `role: key` links. Support, helper and example links live in the document
  only. This gate checks consistency. It does not check that a key link is
  right; that comes from the attacks here and from S7.
- `scripts/pair/anchor_check.py` reports 0 PAGE_ANCHOR findings.
- **Proxy gate.** `cf detect-proxies --lean` runs at the freeze with the S4
  stage table and a DumpDeps dump, for every Definition's key declaration. The
  cheap definition-coverage tier alone would pass a tagged `def
  elementaryMeasure ... := volume`, because it counts a tagged native def as
  covered. The Lean tier's order-violation analysis is what found that proxy
  (`$TD/adam-detector/README.md`).
- **No pasted book text.** A docstring window of 12 or more words that matches
  `text.txt` is flagged (section 4.6).
- A `cf lint` pass for detectors P9 to P13 (specified in
  `references/review-patterns.md`, not implemented).
- No declaration removed, no conflict markers, and the exercise count equals
  S1's.
- The changed-files rule admits only the section's `.lean` file and its task
  file. Issue #9 is fixed first.
- **Freeze.** `statement_sha256` is recorded only when every surviving attack
  finding is fixed or adjudicated CONVENTION.

The docstring is outside the statement hash (`docs/annotations.md` section
5.1), so S7 can edit tags without breaking the freeze.

New work: the author task template and changed-files rule; a Lean validation
command in the tend config; the bijection and key-tag gates;
`statement-freeze.json`; the `declarations[]` exporter; `cf lint` (not
implemented today); optionally a bridgebench adapter.

### 2.7 S6: proofs

Input: the frozen repository, `statement-freeze.json` and the gap targets.
Targets come from `declarations[]` with sorry class `gap`, never from a script
that scrapes docstrings. That trap put 2 exercises on the `$TML` PR #5 target
list (`$TML` commit 1bd0fbc). With `exercise_policy: prove`, exercises are
targets too, in their own cost line.

Output: proved declarations merged per declaration, work harvested from killed
sessions, and doc v5 with updated sorry classes and per-proof provenance
(actor, backend, model, cost). A changelog `restated` entry is forbidden in
this stage.

Provers behind one interface:

- A Claude worker on tend and agentbox, the default. `$TML` PR #5 closed 42
  in-text gaps this way (merge d21dbac).
- The tooled autoform-worker, which has proved single results of 564 to 1,198
  lines in one session (`$BENCH/dataset/fable-stumpers-v2/HANDOFF.md`).
- marathon fill (Aristotle), only after M4 measures it.

After each merge batch: a proof-integrity judge on
`$AUTOFORM/internal/rubrics/proof_integrity.json`, and the laundering audit
below.

**What the DumpDeps gate can and cannot do.** The gate walks the constants each
declaration uses (`scripts/detect/DumpDeps.lean.tmpl`, run by
`scripts/detect/dump_deps.sh`). It attributes compiler auxiliaries
(`_proof_n`, `match_n`, `_aux`) to their parent. A *sorry source* is a
declaration whose own value references `sorryAx`. The gate enforces one thing:
no `sorry` comes from anywhere undeclared.

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
prover run (f5593c4). Laundering needs its own check:

- flag a proof whose term is a projection, `.mp`, `.mpr` or `.out` of a single
  `sorry`'d exercise;
- flag a proof shorter than a set token count that has a `sorry`'d dependency;
- route both to a laundering-audit fleet with a witness requirement, the
  12-agent pattern of `$TML` PR #5.

The rest of the gate forks ideas from `$AUTOFORM/servers/prover/verify.py`: the
unsafe-directive regex and the standard-axiom whitelist. Its sorry logic is
replaced by the walk above. In full:

1. Every signature hash is byte-identical to `statement-freeze.json`.
2. Clean-room `lake build` with `.olean` files deleted.
3. The DumpDeps sorry-source walk.
4. Literal `sorry` appears only in exercise or open-gap bodies.
5. Only standard axioms.
6. The unsafe-directive scan.
7. The exercise count does not decrease (or, under `prove`, the stub count does
   not increase), and no declaration is removed.

New work: the gate; the laundering check; per-declaration harvest (the ff-core
gaps run completed 0 of 14 tasks and stranded 1,074 inserted lines,
`$FFREPORT/FFCORE_FABLE_NOTES.md`); stall detection; backend adapters; a
backend-neutral cost record.

### 2.8 S7: attribution

S7 starts at the S5 freeze and runs beside S6.

Output, doc v6: support, helper and example `links[]` with `relation`
(`states`, `weakens`, `specializes`, `generalizes`...), `sub`, pages and note;
helper memberships and `artifacts[]`; provenance with method `agent_annotation`
or `adjudication` and `confirmed_by_human: false`.

Agents, per item: a curator proposes support links and reviews the key link; a
refute-by-default verifier checks every key link against the five defect shapes
below; a reconciler decides; a cross-item pass checks that no declaration is key
for two unrelated items, that sibling subs agree, and that a Definition's key
is a def. This generalises the `$MT` target-refinement curation (PRs #2, #7,
#17, #19).

The five shapes, each needing a witness (a book quote and a Lean signature):

1. a junk-value key;
2. a key on an existential wrapper while the named witness is untagged;
3. a key on a Mathlib stand-in instead of a native def or a proved bridge;
4. a `states` link that is weaker, which must become `weakens` plus a verdict;
5. a role or tag that contradicts `conventions.md`.

Gate:

- Key tags equal key links; the bijection; anchors on the anchor edition.
- Every item has a key link or an explicit coverage class.
- No key link to an auto-generated projection or instance. In `$MT`, 142 of
  1,066 primary edges pointed at projections (`$MT` PR #7).
- Every key declaration's `statement_sha256` matches the freeze.
- Invariants I1 to I13.
- Planted mistags on every run, to measure recall: adjacent-tag swap, page off
  by one, key moved to a helper or a projection. This needs new mutation
  classes in `scripts/score/mutate.py`.

Curate is out of scope today (`SKILL.md`). Whether naming each declaration at
authoring time makes S7 cheaper than the `$MT` curation is unmeasured.

### 2.9 S8: publish

Output, doc v7: `declarations[]` for every helper from a DumpDeps dump;
memberships and artifacts as the coarse graph (pinned in `companion.graph`);
`destinations[]` from the Verso site index; `snippets[]` by the section 5.1
signature cut; `SitePageSelector` locations; the site as a web source with
`page_hashes`; a changelog against the previous release.

Also generated: the Verso literate site (`lake query :literateHtml` and a slim
step); viewer data compiled from the document; `formalization.yaml` v0.4 and a
status table, both derived from the document and both with `--check`; a README
with provenance and a not-human-reviewed statement; CI. The PDF appears in no
output. The site is local-only by default.

Agents: the claims check (`workflows/verify-claims.workflow.js`) over the
README, landing page and `formalization.yaml` counts, and a vision smoke test
that opens the viewer on sampled regions and checks that the card shown is the
key declaration's snippet.

CI gate: `validate.py --canonical`; a changelog entry for every retired,
restated or renamed id; `lake build`; status `--check`; the schema half of the
geometry checks and a fresh geometry receipt (section 2.3); the snippets check;
URL hardening and I10; no PDF or symlink in the site; a page-size cap after
slimming; a site receipt; key tags equal key links. `$MT`'s CI runs lean-action
only (`$MT/.github/workflows/lean_action_ci.yml`).

The document and viewer data are regenerated on every build and never
re-pinned by hand. One re-pin in `$MT` touched 14 files (commit 6688082).

New work: parameterise the viewer and overlay (today they hard-code the PDF
sha, 265 pages, the ledger sha, the counts, the namespace prefix, and
`FORBIDDEN_MANIFEST_GRAPH_KEYS` rejects `sources`); a document-to-viewer
compiler; a site-index producer; derivation of `formalization.yaml` and the
status table; a CI template.

### 2.10 S9: reduced final audit

Attacks are paid once, before the freeze. S9 is cheaper:

- (a) A differential against each independent companion on shared items. For
  Tao that is about 224 items shared by `$TML`, `$MT` and teorth/analysis
  (`$TD/coverage.md`). The reference companions are read only here, after
  generation, outside the sandbox.
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

## 3. The quality model

### 3.1 Principles

- Deterministic gates decide acceptance. Model review never does on its own.
- The evidence for this is measured. ff-core's original reviewer approved 174
  of 176 reviews, and its adversarial reviewer 55 of 63, including five
  Exercise deletions (recomputed from `$FFREPORT/orchestrator-dbs/`). Autoform's
  jury gave faithfulness 5 to all 31 nodes it reviewed across two local
  projects.
- Every defect finding needs a witness and a verbatim quote bound to the item's
  page.
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
- Every gate is tested with planted mutations, so it cannot be vacuous. The
  findings gate itself once passed a flipped inequality and a running head with
  a placeholder witness
  (`$NOTES/companion-faithfulness-review-2026-09-19/REVIEW.md`).
- Model-tier recall is unmeasured today. The only recall figure in this
  repository is the model-free tier on the synthetic booklet: precision 1.00,
  recall 0.50 (`README.md`). M2 and M3 measure it.

### 3.2 Swarm roles per stage

| stage | workers | refuters | reconciler | critic or consistency pass | calibration |
|---|---|---|---|---|---|
| S0 | spot-checker (10 pages) | none | none | none | agreement 10/10 |
| S1 | section enumerators | classification refuters | none | chapter completeness critic; missed-item hunters | planted deletions |
| S2 | two vision checkers per region | each other | yes | per-page neighbour pass | planted box mutations |
| S3 | parser; conventions drafter | page-image verifier; two convention refuters | yes | three flag-only reviewers | none (human sign-off) |
| S4 | mathlib-checkers | one per "in Mathlib" claim | none | none | `decl_exists` is deterministic |
| S5 | section authors; reviewer | attack-vac, attack-hunt, challenge | adjudication | none | planted statement mutations (M2) |
| S6 | provers | proof-integrity judge | none | laundering-audit fleet | planted launderings (M4) |
| S7 | curators | verifiers | yes | cross-item consistency pass | planted mistags |
| S8 | none | claims-check lenses | none | numbers lens, completeness critic; vision smoke test | none |
| S9 | differential, sampled attacks | challenge | adjudication | completeness critic | 10% unchanged sample |

### 3.3 Deterministic gates

| gate | stages | exists today |
|---|---|---|
| `validate.py --canonical` | every boundary | yes, `scripts/annotations/validate.py` |
| quote gate and witness gate | S1, S3, S5, S7, S9 | yes, `scripts/gate/verify_findings.py` |
| offset and edition checks | S0 | offset yes; registry edition refusal no |
| count reconciliation | S1, S9 | yes, `scripts/plan/make_tasks.py`, harvest receipts |
| region sanity (CI) and geometry receipt (local) | S2, S8 | sanity in `$MT/scripts/pdf_overlay.py`, Tao-only; receipt no |
| `decl_exists` at the pin | S4 | in `$WIKILEAN/site/search_tools.py` |
| build with forced recompile | S5, S6, S8 | no |
| item-declaration bijection | S5 to S9 | no |
| key tags equal key links | S5 to S9 | extraction yes; the gate no |
| page anchors | S5, S7 | yes, `scripts/pair/anchor_check.py` |
| `detect-proxies --lean` at the freeze | S5 | the command yes; stage-table generation no |
| pasted-text window check | S5 | no |
| statement freeze | S5 to S9 | no |
| DumpDeps sorry-source walk and laundering check | S6 | the dump yes; the gate no |
| changed-files rule for `.lean` | S5, S6 | no; issue #9 first |
| selector length cap | S1, S8 | no |

### 3.4 Calibration bars

Every bar below is provisional until measured. A measured value replaces it and
is recorded in the stage's receipt.

| what | mutations | bar | status |
|---|---|---|---|
| S1 hunter recall | items deleted from the enumerators' view | to be set in M1 | unmeasured |
| S2 sign-off recall | right edge -8%, bottom into next item, continuation dropped; plus the 10 real §1.7 misses | 10/10 real, at least 90% planted | unmeasured |
| S5 attack recall | the six statement classes of `scripts/score/mutate.py` in fixture copies | 0.8, provisional | unmeasured |
| S5 proxy gate | `proxy_definition` planted into generated defs | every plant caught | unmeasured |
| S6 gate and audit | re-planted launderings, mis-targeted exercises, helper with `sorry`, `native_decide` | every plant blocked or flagged | unmeasured |
| S7 tag recall | adjacent swap, page off by one, key to helper or projection | at least 90% | unmeasured |

## 4. Data flow through the annotation document

### 4.1 Checkpoints and receipts

One document, `companions/<name>/annotations.json`, moves through checkpoints
doc v0 to doc v8. Each checkpoint is written once to `runs/<run-id>/doc.vN.json`.
Each stage writes a receipt:

```
{input_doc_sha256, output_doc_sha256, run_yaml_sha256, prompt_shas,
 model_ids, gate_result, consent, cost_usd, tokens, wall_clock_s}
```

`consent` records the `--include-source-text` consent a stage needed to send
page text or images to the shared host.

Findings live in `corpus/<run-id>/` with `RECEIPT.json`. The document refers to
them by finding id. Everything downstream is compiled from the release document
and never hand-copied: viewer data, `formalization.yaml` status, the status
table and README counts.

### 4.2 What each stage writes

| stage | records |
|---|---|
| S0 | `sources[]`, provenance |
| S1 | `items[]`, `TextQuoteSelector` locations |
| S2 | `PdfRegionSelector` locations with `signoff` |
| S3 | provenance; root `x_` pointer to blueprint and conventions shas |
| S4 | `clusters[]`, `memberships[]`, `x_mathlib` on items |
| S5 | `declarations[]`, key `links[]`, `unattributed[]` |
| S6 | declaration sorry classes, per-proof provenance |
| S7 | support, helper and example `links[]`, `artifacts[]`, adjudication provenance |
| S8 | `destinations[]`, `snippets[]`, the site source, `SitePageSelector` locations, changelog, `companion.graph` |
| S9 | `verdicts[]` |

### 4.3 Annotations v1.1: two small additions

The format needs two additions, proposed as one tiny minor version because the
maintainer has rejected long spec changes before:

- `signoff.actor {kind: human | agent | tool}` and `signoff.checklist` (for
  geometry: top, bottom, left, right, continuation);
- `provenance.cost {usd, tokens, source}`.

Design A proposed a third, `relations.depends_on`. It is dropped with the LLM
dependency edges (section 2.5).

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
3. The item-declaration bijection.
4. `statement_sha256` equals the freeze, unless a changelog entry explains the
   change.

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

So isolation is enforced by the operating system:

- Generation runs in a fresh container or VM user whose filesystem holds only
  the run root and whose network reaches only a Mathlib cache mirror.
- Local Workflows are forbidden for S1 to S7 generation and sign-off, unless
  they run in that same container. Calibration runs (M2, M3) run outside the
  sandbox by design, because they read the ground truth.
- A leak canary greps every transcript for strings unique to the references.
  The set covers `$MT` and `$TML` identifiers (`MeasureTheory.Tao`,
  `TaoMeasure`, `TODO(autoform)`, `namespace Companions`), teorth/analysis
  identifiers, and the blueprint TeX macros (`\MathlibDecl`, `semanticnote`,
  `mathlibnote`). Any hit voids the run.
- The host may hold earlier read-only companion copies from `--include-sources`
  audit runs, and the measure_theory author's blueprint slices sat under
  `/home/<user>/blueprint-sections/` on the ff-core box
  (`$FFREPORT/prompts/worker-system.md`). The run root is therefore always
  fresh.

Pretraining contamination cannot be sandboxed. teorth/analysis is public.
Agreement with a reference is weaker evidence than disagreement.

### 4.6 Copyright

The PDF, full statement text, crops and the blueprint stay in the workspace
(`POLICY.md`). Three rules close the remaining gaps:

- A selector's `exact` text is capped at a fixed number of words, or stored as
  a hash with a short prefix and suffix. S1 and S8 enforce the cap, because the
  release document ships with the companion.
- S5 flags any docstring window of 12 or more words that matches `text.txt`,
  since docstrings render on the site.
- Finding references in the release document are redacted the way `cf share`
  redacts quotes (`scripts/share/redact.py`).

### 4.7 Loops

- A Lean change reruns S5 to S9 for the touched declarations only. The
  changelog marks retired, restated and renamed ids. `cf recheck` decides which
  verdicts reopen and which items get a sampled attack in S9.
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
| M0a | cost tool, receipt writer, pre-fix `$TML` commit identified | none | 2 to 3 days |
| M0b | attack cost measured on 20 items | M0a | under 1 week |
| M0c | OS-level sandbox and egress probe | none | about 1 week |
| M1 | S0 and S1 on the Tao PDF, doc v1 | none | 1 to 1.5 weeks |
| M2a | §1.7 statements from the PDF: S3, S5 and the minimal gates | M0a, M0c, M1 | about 2 weeks |
| M2b | §1.7 geometry, attribution and viewer: S2, S4, S7, a minimal viewer | M2a | about 2 weeks |
| M3 | exporters and tag review reproduce known answers | M0a, M1 | 2 to 3 weeks, beside M2 |
| M4 | the proving gate, validated by replaying `$TML` PR #5 | M0a, the M2a freeze tool, the M3 `$TML` exporter | about 2 weeks |
| M5 | all of Tao Chapter 1: S2 to S5 and S7 | M2b, M3, M0b | about 3 weeks |
| M6 | publish from the document | M3, M5 | 1 to 2 weeks |
| M7 | Chapter 1 end to end, S0 to S9 | M4, M5, M6 | 3 to 4 weeks |
| M8 | a second, non-Tao source | M7 | about 2 weeks |

Two asks run in parallel and gate nothing: the v1.1 proposal (section 4.3),
and a message for the maintainer to send the measure_theory author asking for
the pipeline workspace behind `$MT`'s front half (blueprint, plan,
`coverage.json`, correspondence state) and for what produced geometry a to c.

### 5.1 M0a, M0b, M0c: foundations

M0a:

- The cost tool moves into this repository. Today it is a 30-line script in a
  session scratch directory on the laptop, which a purge would lose. It reads
  only Claude transcripts at one Opus price. It is extended to parse Codex
  session logs (attack-vac runs on codex, `scripts/plan/presets.py`) and to
  price by model id.
- It reproduces the recorded $358.10 of the §1.7 differential re-run within 1%
  from the same host transcripts if they still exist, or a fresh 5-task run.
- A per-stage receipt writer passes its tests.
- The `$TML` commit before the camera-ready fix stack is identified from git
  log.

M0b: attack-vac and attack-hunt each run on 20 `$TML` §1.7 items at that
commit. $/task and model ids are recorded per preset. This figure replaces the
$7 differential rate in every budget line.

M0c: probe agents in the sandbox try to list or read `/home/<user>/*` on the
real host, other users' run roots, the blueprint slices, and to `git clone` and
`curl` teorth/analysis. Every attempt fails, and the canary finds 0 hits. Design
A's probe of laptop paths on the server would have passed vacuously.

### 5.2 M1: the source side

Input is only the PDF with sha `64498329...`. The run:

- infers offset 16 at confidence at least 0.99 with 265 pages (the onboarding
  transcript in `README.md` records 0.996);
- recovers every numbered label of `$MT/pdf-overlay/data/geometry-batch-{a,b,c,d}.json`,
  matched on `(page, kind, number)`. 24 of those 431 labels are free prose
  (computed); they are matched by a page-plus-quote overlap test, and every miss
  is adjudicated;
- reaches at least 99% numbered-item recall against `$TML`'s numbered docstring
  tags, with every miss explained;
- recovers at least 90% of `$TML`'s "In-text, book p. N" tags as passages near
  the right item and page;
- fetches the errata raw, with the Definition 1.5.11 entry present;
- passes `validate.py --canonical` with no warnings and writes a receipt.

Edition control: given the draft PDF (sha `62d95658...`), S0 refuses it by
registry lookup. Design A's anchor-scoring control is dropped, because it needs
a companion's page anchors, which a PDF-only run does not have.

### 5.3 M2a and M2b: the §1.7 vertical slice

§1.7 has the densest ground truth: `$MT` geometry batch d (49 items, 60
regions), the 10 known-bad regions, `$TML/TaoMeasure/Section_1_7_*.lean`, the
`$MT` PR #17 curation, and the §1.7 differential re-run.

M2a, the first generated Lean, at the end of M2a:

- Calibration, outside the sandbox. The six classes of
  `scripts/score/mutate.py` are planted into at least 30 fixture copies of
  `$TML` §1.7 statements (mutate.py refuses a real companion path without
  `--fixture-root`). Attack recall is reported with a Wilson interval.
  `proxy_definition` is planted into generated §1.7 defs to test the proxy gate.
- Generation, in the sandbox, canary 0 hits: `lake build` is clean; every §1.7
  item has a named, tagged declaration; the bijection, key-tag, anchor and proxy
  gates pass; the exercise count matches S1.
- Comparison, outside the sandbox: for each item, does the generated key
  declaration state it? A stratified sample of 40 items is adjudicated against
  `$TML` and `$MT`, and every CONTENT claim is adjudicated. Design A's bar
  ("confirmed CONTENT at most `$TML`'s §1.7 count") is dropped, because no
  sourced count exists. Both the generated slice and `$TML` §1.7 are attacked
  with the same presets and the same number of runs, and the two rates are
  reported side by side.
- A disk and worktree budget per Lake worktree, from the estimator in
  `scripts/run/manifest.py`.

M2b:

- Sign-off recall. The pre-fix batch-d geometry (before 73efa81 and fb67a73)
  goes to the four-edge swarm, which flags 10 of 10 known-bad regions and at
  least 90% of at least 30 fresh planted mutations. Dollars per region are
  measured.
- Geometry agreement: IoU at least 0.9 against adjudicated post-fix batch d for
  at least 95% of the 60 regions, and none of the 10 bad shapes reproduced.
- Tags against `$MT` PR #17 (19 entries, 16 items, 32 declarations), joined at
  item level: agreement is reported and every disagreement adjudicated.
- A document-driven viewer opens the pinned PDF on loopback. A vision smoke test
  shows the key declaration's card for 20 of 20 sampled regions.
- Receipts total the slice's cost.

### 5.4 M3: calibration at chapter scale

Exporters, promoted from `$NOTES/companion-annotations-2026-09-26/prototypes/`:

- The documents validate.
- `$MT`: 432 items, 3,565 declarations, 627 links, 509 locations, 2,884
  destinations. `$TML`: 651 items, 1,544 declarations, 1,169 links, 2,201
  locations (`docs/annotations.md` section 10).
- The `$MT` document-to-viewer compiler reproduces the item-to-key-target
  mapping of `$MT/pdf-overlay/data/overlay-manifest.json`. Region-exact
  reproduction is not required: the export merges 35 refinement entries per
  item and replaces the quads region with a bounding box (section 10).
- A regression test shows that a `structure` docstring does not attach to the
  next theorem (the cause of the PR #5 mis-targets, `$TML` commit 1bd0fbc).

Tag review reproduces known answers: the 2 ATTRIBUTION_GAP rows in the
adjudication ledger (`references/method.md`); the `$MT` PR #7 projection trap;
at least 90% recall on at least 30 planted mistags; verdict kinds covering the
12 entries of `$MT/pdf-overlay/data/faithfulness-verdicts.json`.

Attack reproducibility: attack-vac and attack-hunt rerun on the pre-fix `$TML`
checkout. Recall over the 16 CONTENT and VACUITY items fixed in the camera-ready
pass is reported per preset and labelled **reproducibility**, not recall,
because those items were found largely by the same presets. The provisional bar
is 12 of 16. Recall comes from the planted defects above.

### 5.5 M4: the proving gate

A replay on a clone of `$TML` truncated at the parent of merge d21dbac, with no
later refs and no gh, targets the 48 in-text gaps.

- At least 37 close, the PR #5 automated-run count (`$TML` commit f5593c4).
- 0 signature changes; the exercise count is unchanged; the build is clean-room.
- The gate rejects: a planted literal `sorry` in a gap, a planted
  `native_decide`, a planted helper lemma with `sorry`, a proof that cites
  another open gap, and the 2 re-planted mis-targeted exercises.
- The laundering check, not the gate, flags the 2 re-planted `.out`
  projections.
- Legitimate proofs whose `sorryAx` traces only to exercise stubs pass.
- Two backends run through the same interface and gate.
- Per-declaration harvest recovers work from a deliberately killed session.
- Cost per gap is logged. The comparison is gaps closed against PR #5, which
  has no dollar figure.

### 5.6 M5 to M8

M5, Chapter 1 statements, tags and geometry in the sandbox:

- `lake build` clean; canary 0 hits; every item has a named, tagged declaration
  or a coverage class; the four boundary gates hold.
- Every Definition is native or bridged. `$TML`'s figure is 32 of 36 native and
  4 bridged (`references/method.md` section 6).
- The `elementaryMeasure := volume` proxy and the Definition 1.2.2 proxy do not
  recur or are caught by a gate, and so do the camera-ready CONTENT and VACUITY
  items that fall in Chapter 1.
- Geometry IoU at least 0.9 against the adjudicated 509 `$MT` regions for at
  least 95% of matched items.
- Links against `$MT`'s 229 target-refinement entries, joined at item level.
- Statement agreement by the M2a sampling method.
- The Chapter 1 baseline rate is recomputed on a Chapter 1 denominator. The
  figure of 16 in about 763 (`$TML/docs/HANDOFF.md` at 01d77e6, section 2)
  includes 42 Chapter 2 items, counts sub-parts, and includes a Chapter 2
  defect.
- Measured attack $/item stays within 25% of M0b, or the maintainer re-approves
  the budget.

M6, publish: the site builds with the slim step and no page exceeds the cap; at
least 90% of named declarations resolve to an exact destination and every key
declaration does (`$TML`'s site-index export got 1,505 exact and 39 fallback,
`docs/annotations.md` section 10); the viewer smoke test passes on 50 of 50
regions; `formalization.yaml` and the status table are derived and checked in
CI; the claims check reports 0 WRONG; no PDF bytes in any output.

M7, Chapter 1 end to end: every item has a key link or coverage record; build
green; no in-text `sorry` beyond an adjudicated hard-tail list, reported against
`$TML`'s 6 hard gaps (`$TML/docs/HANDOFF.md` section 3); the reduced S9 leaves 0
UNCHALLENGED; the 10% unchanged sample yields 0 new CONFIRMED_CONTENT or its
sections are re-attacked; the verdict layer is compared with `$MT`'s 12 entries
at item level; the cost ledger totals every stage; `cf recheck` after a
one-declaration restatement marks exactly the affected verdicts. Design A's edge
recall against the `$MT` ledger is dropped: generated declarations have no
mapping to `$MT` declaration ids.

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
| first full differential run: $2,698.82 for 383 tasks | issue #11 | Opus 5 list prices; cache reads 85% of the bill |
| §1.7 differential re-run: $358.10 for 47 tasks | issue #11 | same pricing; about $7 a task |
| attack cost per task | none | unmeasured; attack-vac runs on codex, which the $7 does not price |
| ff-core Tao campaign: $1,639.46 recorded | `$FFREPORT/orchestrator-dbs/` | the notes say about $985 or $1,050; input-token accounting was broken (about 74 input tokens and no cache per session) |
| ff-core pilot: $528.14 | `$FFREPORT/orchestrator-dbs/` | the section count comes from another source; refined an existing draft |
| ff-core gapfill: 23 gaps for $102.44 | `$FFREPORT/INDEX.md` | 81 to 96% infrastructure mortality |
| `$TML` PR #5: 42 proofs | `$TML` merge d21dbac | Max subscription; no dollar figure |
| WikiLean enumeration and matching: $1.32 to $1.54 per article | `$WIKILEAN/docs/token_budget.md` | article-sized units, not book sections |
| adjudication of 10 findings: 225 agents, 5.9 h | `references/workflow-patterns.md` | local Workflow, limit failures included |

### 6.2 The Chapter 1 envelope

These are estimates. The S5 attack line uses $7 a task as a placeholder until
M0b, and $7 is not a bound.

| stage | estimate | basis |
|---|---|---|
| S0 | under $2 | 10 image calls |
| S1 | $20 to $60 | about 30 section enumerators plus hunters and critics |
| S2 | unpriced | about 509 regions times 3 calls; M2b measures $/region |
| S3 | unpriced, plus under $20 for conventions | about 650 items times 2 or more calls |
| S4 | under design A's $150 to $500 | mathlib-checks only; edge extraction dropped |
| S5 authoring | $500 to $1,500 | ff-core figures above |
| S5 attacks | about $9,100 at the placeholder | 2 presets times about 650 items times $7 |
| S6 | $1,000 to $2,500 | about 250 in-text gaps; under `prove`, about 234 more exercise items (`$TML` exercise tags), roughly doubling it |
| S7 | $600 to $2,000 | about 3 agents per item; `$MT` PR #19 used 374 agents for 207 items |
| S8 | $20 to $80 | one claims check plus the smoke test |
| S9 | about $1,600 plus sampled attacks | about 224 differential tasks at $7 |

The listed lines sum to about $13,000 to $17,400. Not in that sum: S2, S3
parsing, the M0b, M2, M3 and M4 calibration runs, adjudication in M2 and M5,
S9's sampled attacks, and exercise proving under `prove`. Design A's "$8k to
$20k" had a lower bound below its own stage lines.

### 6.3 Wall clock

Server tasks take about 10 to 15 minutes each at 8 workers
(`references/runbook-server.md`), under a cap of 16 concurrent sessions per
account (`references/traps.md` C3), on a host shared with other users.

| stage | tasks | fleet time (estimate) |
|---|---|---|
| S5 attacks | about 1,300 | 27 to 41 h |
| S9 differential | about 224 | 5 to 7 h |
| S2 sign-off | about 1,500 image calls | unmeasured; M2b measures it |
| S7 | about 2,000 calls | unmeasured |

### 6.4 Recording

Receipts sum into a per-campaign cost file in the workspace. Every stage
has a cap. A stage that would exceed its cap stops at a drain point and asks the
maintainer.

## 7. Risks

- **The ground truth is imperfect.** `$MT`'s ledger marked all 77 nodes
  verified and missed two proxies. 10 of 60 §1.7 regions passed sign-off wrong.
  `$MT`'s target refinement is unconfirmed, and 238 of 432 items stay on the
  cluster fallback (`docs/annotations.md` section 10). `$TML/scripts/review_audit.py`'s
  A3 check reports 264 findings, mostly false. Every
  acceptance test adjudicates mismatches.
- **Leakage.** The sandbox is untested until M0c. Pretraining contamination
  cannot be removed.
- **Rubber-stamp review** (section 3.1). Deterministic gates and planted
  mutations are the mitigation. The provisional bars (0.8, 12 of 16) may have
  to move.
- **Cost.** Attack cost is unmeasured and dominates the envelope. The cost tool
  exists only outside any repository today.
- **Unknown provenance** of `$MT`'s front half. The measure_theory author's
  workspace could change the data model or save weeks.
- **Substrate fragility.** Open issues #6, #7, #9, #10, #11; tend was checked
  only shallowly and still enforces strict tool schemas; fleets died at 129 of
  225 and 70 of 114 on session limits (`references/traps.md` D1); Lake builds
  per worktree need a thread cap on a 16-core, 30 GB host.
- **The laundering check may miss shapes** it was not written for, and the
  DumpDeps walk may misattribute auxiliaries or `have ... := sorry`. The M4
  plants must stay blocking for every backend.
- **v1.1 may be refused.** The fallbacks then hide the actor kind and the cost
  from document-only consumers.
- **Tooling pins drift.** leanfinder stops at v4.28.0 while the companions pin
  v4.33.0; leansearch failed when probed; bridgebench is pinned to v4.28.0; all
  six Autoform MCP servers failed to connect; Autoform is installed at 0.4.4
  with 0.6.1 checked out.
- **Overfitting to Tao.** The item grammar, the shared-counter numbering and the
  §1.7 regexes are all Tao-shaped. M8 is the check.
- **Copyright.** `$TML` is private at Mathlib's request. Generated sites stay
  local by default.
- **Human bottleneck and scale.** The maintainer signs scope, conventions,
  waivers and human-confirmed verdicts. Verso pages reach 449 to 536 MB before
  slimming (`$MT` local site; `$TML/README.md`).

## 8. Open questions for the maintainer

1. **Exercise policy.** Should the pipeline prove exercises, as `$MT` does, or
   stub them, as `$TML` does? The choice changes S6's targets, its invariant and
   its cost. There is no default until this is answered.
2. Will you send the measure_theory author the request for the workspace behind
   `$MT`'s front half, and may `REPORT.md` lines 16, 112, 197 and 261 be
   corrected?
3. Annotations v1.1: approve the two additions, or keep the fallbacks? Does the
   lean-companion rename happen before or after M0?
4. Budget: what per-chapter cap applies once M0b measures attack cost? If the
   cap binds, do pre-freeze attacks cover every item, or in-text items and
   Definitions first?
5. Substrate: should all fleets run on the shared host through `cf run`, with
   local Workflows kept for calibration outside the sandbox? Is the 16-session
   cap acceptable at chapter scale?
6. Who signs generator-authored conventions and moves verdicts to
   `human_confirmed`? Is there a second human for a generated companion?
7. Module granularity and set functions: one file per subsection with junk-value
   total functions (tags round-trip mechanically), or thematic modules with
   subtype set functions?
8. Viewer: replace `$MT`'s viewer with a document-driven one, or keep compiling
   `$MT`-format viewer data? Offer the M3 `$MT` export to its author as a PR?
9. Aristotle: an S6 backend after M4 measures it, or dropped?
10. Should acceptance require a cross-model verifier? attack-vac already runs on
    codex; the standing rule puts subagents on Opus.
11. Which licensed second source for M8? Should generated companions ever be
    published?
