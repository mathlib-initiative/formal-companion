# Changelog

`cf --version` prints the number in `scripts/cflib/__init__.py`. This file
says what a change to it promises.

## What a version number means here

The thing that must not break is **a corpus**. `corpus/<run>/` is committed,
content-addressed and used as a calibration baseline, so the compatibility
promise is about the keys a corpus and a ledger are joined on, not about the
Python API.

- Judgment rule PD4 (the maintainer's decision of 2026-10-19). A declaration that proves the
  passage's conclusion, or a stronger one, under stronger hypotheses that some of the passage's
  own cases meet (global continuity where the passage has local) is `narrowMatch`, a special
  case (R3), so the passage counts as `partial`. R2 says a stronger hypothesis makes a record at
  most `narrowMatch`, and R5's incomparable case no longer covers it. Literature Links' relation
  field is free text, so no new relation word was needed. The bench labeler is now
  `locate-label/4`, so gold labelled under /3 is relabelled before scoring.
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

### Added

- **`cf plan-informalize`** (component 5a of `docs/proposals/informalize.md`, its section 8):
  one tend run per fleet phase (`items`, `conventions`, `draft`, `attack`, `judge`, `edit`),
  planned from the output repository's HEAD and, for `judge` and `edit`, a harvested
  `--from-run`; its gate, `scripts/gate/verify_informal.py`, runs out of tree with the
  runtime beside it as `tex/` and nine rules: the task's owned paths, the build with the
  gate's own `build_pdf.sh` and a lint against forged `.aux` records, `check_items.py
  --aux-only --strict`, the item table, the hypotheses, the attack, ruling and edit
  documents, and no run of 12 words copied from the library's docstrings. The prompts, the
  tend config and the task file are templates (`templates/prompts/informalize-*.md.tmpl`,
  `templates/tend/config-informalize.yaml.tmpl`, `templates/tasks/informalize.yaml.tmpl`).
  The agentbox bridge gains a second switch for this gate (a read-only reviewer git, a
  `--findings` gate entry, the tamper guard), `attempt_cap.py` an informalize form, and
  `verify_findings.py` the orchestrator lookup both gates use. The item table's rules moved
  to the runtime's `tools/fcitems.py`, which `informalize.itemtable` imports.
  `tests/e2e/informalize_synthetic/driver.py` runs every phase on a fake library with the
  staged gate run as the host runs it.
- **`cf informalize build-command | fetch-library | scope`** (component 4 of
  `docs/proposals/informalize.md`, its section 6): the Lean side, with no Lean on the
  operator's machine. `build-command` copies the host drivers into `library/to-host/` and
  prints the lines that start `scripts/informalize/library_host.sh` on the host, which runs
  the universe driver on one library line, the probe's canary on the library's toolchain (in
  the module system's form when a module of the library uses it) and DumpDeps, and collects
  the results with `git archive` of the source's `.lean` files and the sha256 of the drivers it
  ran; `fetch-library` brings them back under consent from the host folder
  `hosts.<host>.remote_root`, which must be `informalize-<id>`, and checks them (the drivers
  against `to-host/`, HEAD, a whole index through the new `corpus.ingest.check_index`, the
  canary, the dump and its proof bodies, the archive, the license files against a table of
  SPDX fingerprints) before they replace the last fetch, and records the commit's time for the
  PDF build; `scope` writes the theorem's exact closure over DumpDeps' type and value
  dependencies (a constant two modules realized is one) and `informal/declarations.json`. The universe line gains `LIBRARY_CACHE` (default 1, today's
  behaviour; 0 skips `<library>/scripts/lake-cache-get.sh`), and the disk floor of a
  one-library build is `--min-free-gb` (default 60), not the driver's 450.
- **`cf informalize init | sync | status | check-items | approve | reference-checklist`**
  (component 3 of `docs/proposals/informalize.md`): `informalize/<id>/informalize.yaml`, the
  output repository `informalize/<id>/repo/` with the runtime, the project files and the text's
  `LICENSE` (one text per open license, `templates/informalize/licenses/`), `sync --check`
  against `informal/GENERATED.json`, the item table's rules and its check against
  `declarations.json` and the library's source, the items approval that renumbers the table
  before the first draft and freezes the ids after it, the reference checklist and its approval,
  stage receipts and approvals pinned by sha256, the six enums in `cflib.vocab` and the item id
  `cflib.items.INFORMAL_ITEM_ID`, and JSON Schemas of `informalize.yaml` and `items.json` for a
  reader. SKILL.md rule 10 and its three copies gain a second exception: `cf informalize` writes
  the repository it creates, and only that one.
- **The informalize mode's LaTeX runtime and project templates** (`templates/informalize/`,
  component 2 of `docs/proposals/informalize.md`): the marker package `fcmarkers.sty`, the
  drafting rules, a reproducible build into a directory of its own, the boxes of every item
  and part fitted to the ink of a render, the checks of a build against `items.json`,
  `declarations.json` and `hypotheses.json`, and proof sheets. `tests/test_informalize_runtime.py`
  builds a synthetic document with them, and CI's `informalize-tex` job runs it with TeX.
- **A specification for the informalize mode** (`docs/proposals/informalize.md`), not yet
  built: from a Lean library at a commit and one theorem, an item table, an informal LaTeX text
  written and checked by a fleet, a reproducible PDF with a box per item and part, Literature
  Links annotations and a published overlay; `tests/test_informalize_spec.py` holds it to the
  code it names.
- **An overlay may publish an openly licensed PDF** (`docs/overlay.md` rule 4 and section 3), so
  its built site works from a static host under a project path, such as
  `https://<owner>.github.io/<repository>/` on GitHub Pages, where the `/__` route at the root of
  the domain does not. `pdf.publish: {"license": "<SPDX id>"}` in `overlay.config.json` opts in,
  with one of `Apache-2.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC0-1.0` and `MIT` and a `file_name`
  that ends in `.pdf`; any other license, or none, is refused. `build_site.py` then copies the
  cached PDF, checked again against the records' sha256 and `bytes`, to `<mount>/<file_name>`
  beside `overlay.config.json`; the viewer fetches it from `../<file_name>`, relative to its own
  page, and its status note names the license; `serve.py` serves the cache there. The PDF is
  still never committed while `site.output_root` stays ignored, and a config without
  `pdf.publish` behaves as before. `serve.py --build-only` now checks the built site as serving
  does, so it refuses any other PDF there, such as a cache inside `site.output_root` or a PDF
  left under an earlier mount. `POLICY.md` section 3 records the exception. `config.py`, the
  schema, `check_generated.py`, `serve.py`, `build_site.py` and the viewer carry the same license
  list, held together by `tests/test_overlay_config.py`. The viewer now resolves the PDF's URL
  against its own page before it fetches, which sends the same request for a route. `cf overlay
  check` asks an older overlay to re-sync.
- **Judgment rules 4, the flip: the smoke-4 audit's calls in the rules and the prompts**
  (`references/vocabulary.md` §1.11; `templates/prompts/locate.md.tmpl`,
  `locate-challenge.md.tmpl`, `locate-worker-system.md.tmpl`,
  `workflows/locate-label.workflow.js`). `vocab.JUDGMENT_RULES` is `RULES_SMOKE4` (4) and
  `STATUS_RULE` is `STATUS_RULE_BROAD` (3), so a run planned now names judgment rules 4 in its
  gate.json and, for a locate run, status rule 3 in its run.yaml; a run planned before keeps its
  versions and prompts and is gated, tallied and exported as before. The rules, word for word in
  vocabulary.md, both locate prompts and the labeler: **PD11** (an instance argument that
  excludes only a trivial case, `[Nontrivial R]` and the zero ring, does not narrow), with Fix
  2a's r3 test amended to match ("no extra instance argument that excludes a case with real
  content"); **R-EQ** (an equivalence is judged by the structure the passage asserts: more is
  `broadMatch`, less `narrowMatch` or `hasPart`, the inverse no extra structure); **R-NAT**
  (natural-number moduli for integer ones are an encoding, and PD2 says so); **R-ASSOC**
  (`Associated` against equality in ℕ is PD10); **R-CHAIN** (the inventory's chain rule, a = b = c
  asserts a = c, carried into locate and the challenge); **R-PD7** (a larger input domain with
  content is `broadMatch`); **R-DEFFORM** (a theorem stating a defined object's formula on a
  definition passage is `closeMatch`, so partial there, with Fix 3a amended to leave it);
  **R-TABLE** (the default taken: the inventory statement sets a table's domain); **Fix 6d**
  (PD6 judged with the readings the parts are credited with, a characterisation is one location,
  quantifier order, a `hasPart` part); and, in the challenge prompt only, **Fix 6e** (a
  downgrade's bridge, a confirm's `part_conventions` and `part_limits`). FM7 applies R-BROAD and
  judges an equivalence by R-EQ. The prompts ask for the rules-4 fields (`pd6`, `bridge` or
  `incomparable`, `part_conventions`, `part_limits`, a `hasPart` conjunct relation), and the
  system prompt's gate list names their refusals. The labeler is `locate-label/7`, which
  `bench.gold.LABELER_RULES` maps to 4 and whose gold is derived under status rule 3; its label
  schema admits a `hasPart` conjunct relation, so gold of `/6` scores only runs of rules 3 and
  the bench's gold is relabelled before a run planned now is scored. No inventory prompt changed
  (it carries the chain rule already), so no byte ceiling moved. Two words the flip left behind
  are corrected to match PD1, R-PD7 and PD11: Fix 4b's defined object is `exactMatch` or
  `closeMatch` (R7), or `broadMatch` when it generalises the passage's object, never a part; and
  the locate prompt's step 7 and the challenger's coverage claim count an extra instance argument
  against a part only when it excludes a case with real content. The labeler stays
  `locate-label/7` (no gate check moved), its file sha with the text.
- **Judgment rules 4, the gate's checks** (`scripts/gate/verify_locate.py`,
  `cflib.vocab.RULES_SMOKE4`; `references/locate-documents.md` §3, §4 and §10,
  `references/vocabulary.md` §1.11). The gate knows judgment rules 4 before the planner
  writes them (`KNOWN_JUDGMENT_RULES` 4), and each check binds only a gate.json naming 4: a
  part's `conjunct_relation` may say `hasPart` (`CONJUNCT_RELATIONS_PART`), owing a `step`
  and no `tests`, and no bundle stands on it; **PD6's shape tripwire** on an
  `existence_uniqueness` or `iff` bundle, where a part whose declaration concludes with `↔`,
  `≃`, `∃!` or `Bijective` (`verify_locate.conclusion`, read off the signature) writes
  `pd6`, and a confirm of the bundle `part_limits`, saying which conjunct it does not state
  alone; a coverage confirm's `part_conventions` for each part its `part_relations` credit
  with `closeMatch` (fix 6d's mechanical half); and a challenge downgrade of a strong record
  to `relatedMatch` giving exactly one of `bridge: {library, declaration, why}` (a lemma of
  the corpus, rule 3) or `incomparable`. The outcomes keep `pd6`, `bridge`, `incomparable`,
  `coverage_part_conventions` and `coverage_part_limits` where a document wrote them, on a
  run of status rule 2 or 3, and a verdict's `hasPart` conjunct relation lapses the bundle
  standing on that part, by name. The smoke-4 audit's measure: both of run A's PD6 misses
  were an iff characterisation entered as a part, and 4 of its 12 broadMatch-to-relatedMatch
  downgrades took an encoding for a bridge. Under judgment rules 4 the ℕ-for-ℤ tripwire's
  refusal also says that natural-number moduli for integer ones are R-NAT's encoding, which
  the same note answers; under rules 3 its words are unchanged.
- **Status rule 3, R-BROAD** (`cflib.vocab.STATUS_RULE_BROAD`; `references/vocabulary.md`
  §1.11, the maintainer's call of 2026-10-28). `derive_status(…, status_rule=3)` gives
  `formalized` for a `broadMatch` to a proved theorem or to data (a definition without the
  statement flag, an equivalence) on a theorem, conjecture, example or other passage, and to a
  definition on a definition, construction or notation passage (a finished `broadMatch` bundle's
  stand-in among them); everything else is status rule 2. The status rule a run of judgment rules
  `r` is derived under is `vocab.status_rule_of_judgment(r)` (3 from judgment rules 4, 2 for 3,
  1 before), which `cf plan-locate` writes as `locate.status_rule` and the bench derives a
  labeler's gold by; this change writes no new value yet (judgment rules are still 3). The
  outcomes, the challenge plan, the records and the export read status rule 3 as status rule 2
  (`vocab.reads_bundles`), and a run of it exports `cf-locate-recordset/2` with the seal's
  `status_rule` 3, so a consumer that reads that key derives by the rule it names. New
  hand-written table `tests/data/locate/status-parity-3.json` (`cf-locate-status-parity/3`, 81
  cases: status rule 2's again with their rule-2 expectation beside each, and 23 of R-BROAD's
  own); the `/1` and `/2` tables are unchanged.
- **An unaudited locate run keeps its turn records, and the harvest reads them**
  (`scripts/run/review_record.py`, `scripts/harvest/turn_records.py`;
  `references/locate-documents.md` §9 and §10). Smoke 4 found a worker's `needs_review`
  written by the bridge and read by nothing (tend 0.1.0 merges it like `completed`, and the
  result file dies with the worktree), and a reviewer that edited the document it reviewed
  with nothing between it and tend's merge of the worktree's HEAD. The bridge now writes
  each call's normalised contract, with HEAD and the git blob of each of the task's
  documents, to `<tend-root>/contracts/` (`cf-turn-contract/1`); a reviewer whose worker
  said `needs_review` gets the worker's summary in its prompt and the review goes to
  `needs-review.jsonl`; and the turn machine's tamper guard runs around every unaudited
  reviewer call, putting the worker's commit and branch back, keeping the verdict and
  writing `tamper.jsonl`. `cf harvest` fetches all three and the receipt carries
  `needs_review` (listed and warned about, never refusing: a `needs_review` merges as
  before), `worker_documents` and `merged_not_worker` (each merged document's blob against
  its worker's last contract; a mismatch is INCOMPLETE) and `reviewer_tampered`;
  `contracts/` is an expected artifact of a run whose `run.yaml` pins
  `bin/review_record.py`. A run planned before has none of them and harvests as it did.
- **The receipt of a rule-3 audited run counts the gaps** (`scripts/harvest/audit_summary.py`,
  `references/locate-documents.md` §13.10 and §13.12). When a verdicts file is `audit/3`, the
  receipt's `audit` block gains `gaps`: round 1's gap reports summed (`before`), the reviewed
  round's (`after`), each with its gaps and words by section, and each document's two whole; `cf
  harvest` prints one line of it. A rule-3 run keeps rule 2's `skips` block. Reported, never
  refusing; a rule-1 or rule-2 receipt has no such key and is what it was.
- **The audit's prompts under rule 3** (`templates/prompts/locate-inventory-{audit-system,audit,
  skeptic,check,revision}.md.tmpl`, `scripts/locate/prompts.py`; `references/locate-documents.md`
  §13.12). Rule 3's text sits in `{{#if audit_gaps}}` blocks that render empty under rules 1 and
  2: a rule-2 plan's prompts are 66f4936's byte for byte (a test plans both and compares). Under
  rule 3 the rule card says what a gap is and the three readings of the cut the inventory prompt
  states (C4's "need not", C6's commentary and instructions, C7's notation before context); the
  auditor reads the GAP lines and the `commentary` hint, judges every gap in `gaps`, answers the
  kind rule with `should_be_expository` when a formal `other` passage asserts nothing, and names
  in an expository flag's action the statements it means; the skeptic hunts the gaps; the checker
  reads the GAP VERDICT lines; the revision applies a gap verdict with `add` and `set`.
- **Audit rule 3 in the host tools and the turn machine, dormant: the gaps judged**
  (`scripts/gate/inventory_audit.py`, `inventory_render.py`, `inventory_edit.py`,
  `scripts/run/audit_turn.py`; `references/locate-documents.md` §13.12; the smoke-4 audit). A gap
  is a run of a block's own text of the body, outside the back matter, of eight words or more,
  that no passage and no skip holds, named `r<k>-g<nnn>`. Under `--rule 3`
  (`cf-inventory-audit/3`, `audit/3`, whose table is `audit/2`'s) the rendering draws the layer no
  passage holds block by block, each gap a line of its own in full to 1,200 characters (the first
  renderer clipped a whole gap to its first and last 200, which hid smoke 4's lost paragraph); the
  auditor judges every gap once, `clean` or `gap_statements` with the statements it holds; the
  skeptic hunts every gap no flag names and upholds a gap flag only with evidence inside the gap;
  the merge turns it into a gap verdict the worker repairs with `add` and `set` (no new
  operation); every verdicts file carries the round's `gap_report`; the diff gains GAPS and GAP
  VERDICT lines. The kind rule reads every formal `other` passage, not only one in a proof or an
  example section, and its action names the expository answer (PD5); the advisory `commentary`
  hint marks a formal passage that reads as a method's commentary, an instruction or a meta
  remark. The turn machine accepts `CF_AUDIT_RULE=3` (strips with `--gate-config`, names each
  part's gaps, the gap hunt, combined `examined_gaps` and `missed_gaps`, a gap verdict weighing
  max(1, its statements)). No planner writes rule 3 yet; rules 1 and 2 render, validate, strip
  and merge byte for byte as 66f4936 did (a test renders both pages with 66f4936's renderer).
- **The prose floor: no paragraph of the body left out silently** (gate rule 8,
  `scripts/gate/verify_locate.py`; `references/locate-documents.md` §2; the smoke-4 audit).
  Both statements smoke 4's inventory lost were whole formula-free paragraphs that no passage and
  no skip held, and the coverage floor reads only math paragraphs, so nothing asked for a
  record. An inventory whose `gate.json` names `prose_floor_rule` 1 (`cflib.vocab.PROSE_FLOOR_RULE`,
  which `cf plan-locate` now writes into every inventory, and only an inventory) holds every other
  `paragraphs.json` block outside the back matter with at least eight words of the layer to the
  same floor: a passage or a skip covers it, History sections included, a short block and the
  back matter exempt. The receipt's `skipped` block gains `prose_paragraphs` (summed only when a
  gate counted them). The worker prompt says it in one sentence after the coverage floor's; it is
  no cut rule, so `references/vocabulary.md` §1.11 and the bench labeler are unchanged. The
  rendered worker prompt is 17,643 bytes with a real plan's paths, over the 17,500 ceiling, which
  is now 17,700. A `gate.json` without the key, every inventory planned before it, is gated, re-gated
  and summed as it was. The synthetic e2e worker leaves its formula-free paragraphs out with a
  skip and its reason.
- **The receipt of a rule-2 audited run counts the skips** (`scripts/harvest/audit_summary.py`,
  `references/locate-documents.md` §13.10 and §13.11). When a verdicts file is `audit/2`, the
  receipt's `audit` block gains `skips`: round 1's skip reports summed (`before`), the reviewed
  round's (`after`), and each document's two whole, with the math paragraphs under skips by
  section and the mostly skipped sections; `cf harvest` prints one line of it. Reported, never
  refusing; a rule-1 receipt (inv-pilot-3's) has no such key and is byte for byte what it was.
- **The audit's prompts carry audit rule 2** (`templates/prompts/locate-inventory-{audit-system,audit,skeptic,check,revision}.md.tmpl`,
  `locate.prompts.audit_context`; `references/locate-documents.md` §13.11). The rule card says when
  a skip is right (furniture, text with no statement and no math paragraph) and that a proof, a
  derivation or a worked example is never skipped whole; the auditor reads the SKIP blocks and
  writes one `skips` row per skip; the skeptic upholds a skip flag only with evidence inside the
  skip and hunts every unflagged skip with math; the checker reads the diff's SKIPS section and
  SKIP VERDICT lines and `skip_edit` collateral; the revision applies a `skipped_statements`
  verdict with `unskip` and adds (the narration expository, never a skip), weighed sessions, and
  every host tool line names `--rule 2`. The text sits in `{{#if audit_skips}}` blocks a rule-1
  plan renders empty, so a rule-1 audited plan is byte for byte 84e40ba's but for the staged
  tools and the templates' shas (`tests/test_locate_audit_plan.py` plans one with the base
  commit and with this one under each rule); the planner still writes rule 1.
- **The turn machine runs audit rule 2** (`scripts/run/audit_turn.py`, `references/locate-documents.md`
  §13.9 and §13.11; dormant until a plan exports `CF_AUDIT_RULE=2`). It accepts rule 1 or 2,
  records the rule in `state.json` and refuses a state of the other, passes `--rule 2` to every
  host tool call and into every validator line it gives an agent, names each part's skips in
  the call's block, places a skip flag in the part that shows its skip, runs the round-1 skip
  hunt's skeptic call for a part with an unflagged skip over math no passage holds (even with
  no flag and no formal passage), combines the parts' `examined_skips` and `missed_skips`,
  stages the last round's verdicts for the diff's SKIP VERDICT lines, and weighs the revision's
  sessions: a skip verdict weighs max(1, its statements, the math paragraphs under it), up to
  40 a session. Under rule 1 its calls, prompts, command lines and round files are the base
  commit's: `tests/test_audit_turn_skips.py` runs one rule-1 run through 84e40ba's turn machine
  and this one and compares them, and drives a skip verdict through fake agents end to end
  (flagged, upheld with evidence inside the skip, repaired with `unskip` and adds through the
  staged edit tool, the id tool and the gate, checked applied, approved), the skip rule and
  `skip_ok`, and the random-outcome property under rule 2.
- **Audit rule 2: the inventory audit's host tools judge the skips** (`references/locate-documents.md`
  §13.11; dormant: no plan writes rule 2 yet, and every tool called without `--rule` is rule 1,
  byte for byte the base commit's). inv-pilot-3's CRT worker recorded its proofs and worked
  examples as skips, and the stage audited passages only, so it could not see them. Under
  `--rule 2`, files carry `schema: cf-inventory-audit/2`, and:
  `inventory_render.py` draws every skip in place as a SKIP block (its id `r<k>-s<nnn>` in span
  order, its blocks with their spans, the math paragraphs no passage holds, its sections, the
  worker's reason and the hints `skip-rule`, `in-proof`, `in-example`, `mostly-skipped`, `partial`
  and `furniture`), adds the header's Skips and ATTENTION lines (the mostly skipped sections:
  at least 2 math paragraphs under skips and more than half of the section's, the lead and back
  matter excluded), the index's `skips` with the part that shows each, `--show <skip id>`, and
  the diff's SKIPS section and SKIP VERDICT lines; `inventory_audit.py` holds the auditor to one
  row per skip (`clean` or the new `skipped_statements`, with the statements it hides or the
  passage it finishes, `skip_ok` the exemption), the skeptic to evidence inside the skip and a
  hunt over every unflagged skip with math, and the merge adds the mechanical skip rule (a skip
  over math no passage holds in a proof, an example or a mostly skipped section), carries a skip
  verdict by its span onto the next snapshot's skips, reports `skip_edit` collateral, decides by
  `audit/2` (a middle round also rejects for a skip verdict when it made progress) and writes
  each round's `skip_report`; `inventory_edit.py` gains `unskip` (shrink, split or drop a skip,
  never widen one). The gate is unchanged: its rule 8 and coverage floor judge a repair. The
  vocabulary is `cflib.vocab`'s (`INVENTORY_AUDIT_RULES`, `INVENTORY_AUDIT_SCHEMAS`,
  `AUDIT_SKIP_*`, `AUDIT_BACK_MATTER`, `AUDIT_MOSTLY_SKIPPED_*`), pinned against the host copy.
  `tests/_locate_fixtures.py` gains a made-up "Widget bounds" page with the design's fixtures
  (a skipped proof, legitimate skips, a skipped display, a skip inside a paragraph), and
  `tests/test_locate_inventory_skips.py` holds the tools to them, rule 1 to the base commit's
  renderer on each.
- **`cf harvest` of an audited inventory run carries an `audit` block** (`references/locate-documents.md`
  §10 and §13.10). The remote harvest also fetches the tend root's `audit/` (its `*.json`: the
  turn machine's state and round files) to `runs/<run>/host/audit/`, an expected artifact, and
  `scripts/harvest/audit_summary.py` reads it beside the landed corpus. The receipt's `audit`
  block gives each document's outcome by its sha256 (`approved`, `reverted`, `audit_failed`,
  `not_audited`, `capped`, `stale`, `unaudited`), the rounds' verdicts files summed, the residual
  verdicts of the round that reviewed the harvested bytes per formal passage with the kind
  rule's counts before and after, the turn machine's counts, and the envelopes' dollars by step.
  The envelope cost block gains `by_step` (`cost.fleet.envelope_by_step`) when any envelope names
  a step. A run whose `run.yaml` names no audit fetches nothing more and gets neither key, so its
  receipt is what it was. The locate e2e driver runs an audited inventory through the planned
  wrappers, the bridge and the turn machine with a fake agentbox: round 1 rejects, the fake
  worker applies the operations, round 2 approves, and the receipt carries the block.
- **`cf plan-locate --audit-rounds 2|3` plans an audited inventory** (`references/locate-documents.md`
  §9 and §13.9). The flag belongs to the locate-inventory preset, and a re-plan keeps a run's
  audit. `run.yaml` records `locate.audit: {rule: 1, review_rounds: R}`, and
  `config_shas.agents` its `audit`. The plan renders seven prompts: the worker's two as before,
  and five new templates under `templates/prompts/locate-inventory-*.md.tmpl`. Those are the
  reviewer's system prompt (the rule card, the statement cut's rules 1-10 of
  `docs/proposals/statement-cut.md` §3.4 as C1-C10 with the kind rule, and what the gate's bound
  allows under the statement-cut rule the plan writes), the auditor, the checker (who also judges
  every `widened` passage), the skeptic (refute by default, with evidence from the layer), and
  the revision (operations that cite their verdicts, applied with `inventory_edit.py --base`,
  then `passage_ids.py` and the gate). The plan stages the audit's three host tools in the gate
  directory, and both wrappers export `CF_AUDIT_RULE` and `CF_AUDIT_ROUNDS`. The worker's
  allowlist adds the edit tool, the rendering's `--show` and `inventory_audit.py validate`. The
  reviewer's becomes Read, Glob, Grep, `git diff|show|log|status`, the gate's `--findings`
  check, `--show` and the validator. A plan without the flag is byte for byte the base commit's,
  apart from the skill commit and the staged files whose source changed (a test plans it with
  both).
- **The audited inventory's turn machine, dormant without `CF_AUDIT_RULE`**
  (`references/locate-documents.md` §13.9). No plan exports the variable yet, and without it
  the bridge makes the calls and prints the contracts it did (a test runs the base commit's
  bridge beside it). With it, `bridge-agentbox.sh` hands every turn of a task to
  `scripts/run/audit_turn.py`, which keeps the round and its files in
  `<tend-root>/audit/<task-id>/` outside the worktree, stages `.tend/audit/` before every call,
  runs the audit, check and skeptic in parts of the rendering through `agent_call.py --step`
  (envelopes `<task>.<role>.<step>.<stamp>.<pid>.json`, which the watchdog reads beside the old
  name), re-calls an invalid output once and then approves the round as `audit_failed`, reuses a
  step valid for the same document, undoes a reviewer's change to the worktree, resets a revision
  the gate refuses three times to the reviewed commit, and reverts to the best reviewed snapshot
  with no agent call. The round moves only on the merge's rejection, so a task takes at most
  R − 1 genuine rejections; in an audited run the attempt cap restores the best snapshot instead
  of writing UNRESOLVED, and `attempt_cap.py count` prints the count. `audit_turn.py` is staged
  in every locate root's `bin/` (`BRIDGE_HELPERS`). `inventory_audit.py validate` holds a skeptic
  part to its part's hunt (`--render-index --part`) and a check part to the collateral ids its
  `--expect` names.
- **The inventory audit's host tools, dormant** (`references/locate-documents.md` §13,
  `cf-inventory-audit/1`). Nothing plans an audited run yet, so an unaudited plan stages,
  renders and gates exactly as before. `scripts/gate/inventory_render.py` renders a document
  against its pinned layer (exit 3 on a layer or table that is not the pin): whole, as parts of
  about 50 KB cut between passages with an index, one passage with `--show`, and a diff against
  the last snapshot with `--previous`, with the hints `kind-other`, `kind-rule`, `in-proof`,
  `in-example`, `connective-start` and `widened`. `scripts/gate/inventory_edit.py` applies
  `merge`, `split`, `set`, `add` and `drop`, each citing a verdict (an operation that cites none
  is refused), with `concept` and `quote_end` among the fields, and writes only the document it
  is named. `scripts/gate/inventory_audit.py` holds the one schema: `validate` (callable by an
  agent), `strip` (what the skeptic sees), `collateral` (every change the cited operations do not
  produce), and `merge`, the decision table (`audit/1`) with the mechanical `wrong_kind` verdict
  on a formal `other` passage in a proof or example section and the revert to the best reviewed
  snapshot. The vocabulary is `cflib.vocab`'s new audit block; `locate.tend_root.AUDIT_GATE_FILES`
  stages the three only when `stage(audited=True)`, which no planner passes yet.

### Changed
- The overlay reads Literature Links 1.0.1 at its tag `v1.0.1`, commit `789af9a`. The vendored
  copy differs from 1.0.0 in `__version__` and the schema's `$id` alone, and accepts and rejects
  what it did. 1.0.1's specification lets a `lean` end's `version` name a release tag, which a
  committed `verso.json` now refuses (see Fixed). `cf overlay check` asks an older overlay to
  re-sync.
- The overlay vendors PDF.js's legacy build, `legacy/build/` of the same `pdfjs-dist@6.2.108`, in
  place of its default build. The default build calls builtins without checking for them (the
  global `Iterator` at load, then `Promise.try`, `URL.parse`, `Math.sumPrecise` and others), so in
  Safari 17.4, which has none of them, the viewer stopped with `Can't find variable: Iterator` and
  showed no page. The legacy build carries polyfills (core-js 3.49.0, MIT, named in
  `THIRD_PARTY.md`) and leaves a browser's own builtins in place. Measured on a 404-page companion:
  Safari 17.4, Chrome and Firefox draw the same 2,512 boxes, Chrome and Firefox render every page
  as they did and turn pages as fast, and the first page takes about 15 ms longer in Chrome (of
  250) and 9 ms in Firefox (of 525). The two files grow by 108 KB (35 KB gzipped). CI has no Safari,
  so `templates/overlay/tests/pdfjs-build.test.mjs` removes those builtins and then loads each
  file. `cf overlay check` asks an older overlay to re-sync.
- The README is rewritten for a reader new to the project and cut from 497 lines to about 170,
  and what only a maintainer needs moved out of it: the onboarding transcript, the program and the
  overrides to `docs/running-an-audit.md`, the workspace model and its rules to
  `docs/workspace.md`, the overlay walkthrough to `docs/generating-an-overlay.md`, the inventory
  to `docs/layout.md`, the test matrix to `docs/testing.md`, and the detail of what has run to
  `docs/status.md`. Their shell blocks read shell variables and paste into an interactive zsh,
  which the old install block did not. Closed issues #6, #7, #10 and #11 and the locate smoke runs
  record real hosts, so the README stops saying `cf run --execute` and locate have never met one.
  Only the shipped registry crosses workspaces, so it stops saying a second workspace knows what
  the first discovered. The old program also left out the spend ceiling `cf run` asks for and the
  `--execute` a harvest from a host needs. Merged pull requests record the overlay's checks and
  browsers on three private companions, whose overlays are generated at `50e7649`, so
  `docs/status.md` names those runs. The transcript's tests moved with it:
  `tests/test_rf_scrub.py` reads the new document, and `tests/test_rf_docs.py`,
  `tests/test_rf_final_docs.py` and `tests/test_rf_items_sources.py` also check the guides that
  took the README's flags and figures. The test that pins the directory inventory now reads
  `docs/layout.md`, where the inventory moved.
- The overlay reads Literature Links 1.0.0 at its tag `v1.0.0`. That is the release tagged
  `v2.0.0`, renumbered: upstream renamed its tags and rewrote its history, so every commit id
  changed. The vendored copy differs in `__version__` and the schema's `$id` alone, and accepts
  and rejects what it did. `cf overlay check` fails on an older overlay until it is re-synced.
  The legacy format's tag is now `v0.1.1rc2`, at `0b20472`. `sync_format.py` refuses that tag, so
  its manifest took the new id by hand and keeps the tag's original name. Older entries here use
  the names of their day.
- The overlay reads Literature Links 2.0.0 at its tag `v2.0.0`, which replaces the unreleased
  revision `2bb328c` of `format-2.0`. A generated overlay's `GENERATED.json` now names the tag,
  not a branch, so `cf overlay check` asks an older overlay to re-sync. The vendored copy's
  checker and reader change as upstream did; annotations valid at `2bb328c` stay valid unless a
  reference holds one of the invisible characters the schema now refuses.
- **The two calls on the cut, finished: the hypothesis rule wins, a bare "and" cuts nothing, the
  R-INSTANCE pieces have their own text, and the rule card states the calls itself**
  (`templates/prompts/locate-inventory.md.tmpl`, `locate-inventory-audit-system.md.tmpl`,
  `locate-inventory-audit.md.tmpl`; `references/vocabulary.md` §1.11,
  `references/locate-documents.md` §13.12, `docs/design/stage_map.md`;
  `scripts/locate/outcomes.py`). A review of the calls of 2026-10-28 left five things open, and
  each is settled with the smallest change that makes the code, the prompts and the docs agree.
  **The hypothesis rule wins over R-SPLIT.** The call said facts are independent "even about one
  subject or under one hypothesis", where the same prompt said never to cut between a hypothesis
  and its conclusion, the Context rule said a statement is never cut from its hypothesis and the
  worker's check said never after a supposition. Those three stand: facts that share one
  hypothesis or supposition their sentence states once are one passage of shape `conclusions`,
  with a conjunct and a part per fact, as facts that share one clause or one formula are, and
  the `, and` example is no longer a hypothesis and its conclusion ("W has a spanning gadget, and
  it is sprocketed" is two). **A bare `and` or a `but` cuts nothing**: the cut list's connective
  is the comma's (`locate_text.cut_points` counts a bare `and` as none, and no list holds `but`),
  so the facts they join are one passage with a conjunct each. **Two R-INSTANCE pieces never
  share their span text**, which gate rule 10 (g) refuses because two passages with one span text
  have one `passage_key`: where a sentence names one structure twice, the earlier piece ends with
  an `until` that takes in the comma or the word after it, the neighbouring word that tells the
  two apart. The gate is unchanged. **The rule card states the calls in C2, C3, C4 and C8**
  themselves, in the worker prompt's words — C3 with the formula exception, that a formula with
  no space before it cannot start a passage — and its rule-3 block now says only what the calls
  make of a verdict. **The rank after a challenge** is by relation strength again
  (`outcomes._renumbered`), so a downgrade moves its card below every card it is now weaker than
  and rank 1 is the strongest card left; among cards of equal strength the worker's order stands,
  since no record says which declaration is stated for the passage's own objects and nothing
  downstream may re-decide it — the challenger reports a general card above a specific one as a
  recall miss instead. Like the rest of R-SPECIFIC that re-rank is **judgment rules 4's**: it
  binds a run derived under status rule 3 and no older one (`vocab.ranks_by_strength`), so a
  re-tally or a re-export of a run judged under judgment rules 1, 2 or 3 — which was asked only
  for its cards "best first" — keeps the ranks its worker gave, as
  `docs/design/stage_map.md`'s rule that an older run is judged by the version it ran under
  requires; the renumbering that leaves a refuted card no gap still binds every run. All of it is audit rule 3's: an unaudited plan's prompts and a rule-1 or
  rule-2 plan's are byte for byte what they were, and the bench labeler and its gold are
  unchanged. No gate check is added or changed. The rendered rule-3 worker prompt is 22,184 bytes
  with a real plan's paths (`WORKER_MD_RULE3_MAX_BYTES` 22,250), the unaudited one 18,886 as
  before. Every example is synthetic.
- **R-SPECIFIC: a passage stated for specific objects is linked to the declaration stated for
  them, ranked first among cards of equal strength** (`references/vocabulary.md` §1.11, the
  smoke-4 audit's rules; `templates/prompts/locate.md.tmpl`, `locate-challenge.md.tmpl`,
  `workflows/locate-label.workflow.js`, `scripts/locate/records.py`, `scripts/gate/
  verify_locate.py`; the maintainer's notes of 2026-10-28). The smoke-4 run linked passages
  stated for the integers to lemmas of a general structure, in bundles too, where lemmas stated
  for ℕ state them. The rule, word for word in both locate prompts and the labeler after R-TABLE:
  search for the declaration stated for the passage's own objects and record it; rank the cards
  by relation strength first and, among cards of equal strength, the specific declaration first;
  a general theorem that holds of them keeps its relation (R6), below a specific card of equal
  strength, alone only when the library states none; a bundle's parts are the specific
  declarations where they exist, and a bundle that states the whole passage is a whole-passage
  card at its bundle relation, its parts ranked together there against a general whole-passage
  card by strength; for one structure of a family the specific declaration is its instance of
  the property, an instance Lean's instance resolution reaches through a chain included (the link
  names the chain's last instance with the chain in its `note`, no PD8 bridge); and the general
  notion is linked to its definition (R-INSTANCE's links). The locate worker's search step and
  its ranking line say it; the challenger hunts for the specific declaration and, since a
  challenge cannot add a location or move a rank, reports a recall miss for a general card
  ranked above a specific one of equal strength, or standing with no specific declaration where
  the library states one, a general part of a bundle included, which is never ground to refute
  the bundle: the last sentence of its verdict's witness (`R-SPECIFIC miss: <library>
  <declaration>.`) and its summary; the miss stays free text until the owed redesign of the
  `owed` markers records it. A downgrade's note never takes that sentence:
  `locate_records.downgrade_note`, which picks the witness sentence a `closeMatch` downgrade with
  no note exports as its note, passes over any sentence that carries the mark
  (`is_specific_miss`), and the gate's rule 1 refuses such a downgrade whose witness says nothing
  else, since the export would refuse a `closeMatch` with no note. Every example is synthetic. No
  gate check of the rule (general against specific is no fact a name gives), so `JUDGMENT_RULES`
  stays 4 and the labeler `locate-label/7`; its file sha moved, so args emitted before this
  transcribe no more.
- **Two calls on the cut: independent facts are separate passages, and a sentence naming
  several structures is cut per structure** (`templates/prompts/locate-inventory.md.tmpl`,
  `locate-inventory-audit-system.md.tmpl`, `locate-inventory-audit.md.tmpl`,
  `locate-inventory-skeptic.md.tmpl`, `scripts/locate/prompts.py`; `references/vocabulary.md`
  §1.11, "Two calls on the cut"; the maintainer's notes of 2026-10-28). One cut rule.
  **R-SPLIT**: facts that each stand as a result of their own are independent, even about one
  subject or under one hypothesis, and the clauses of one statement are not (the existence and
  uniqueness of one object, the directions or conditions of one equivalence, the cases of one law
  or definition in one sentence, display or list); independent facts in separate sentences,
  independent clauses, list items or formulas of a display are separate passages, each
  `statement` carrying the hypotheses they share ("the series converges, and its sum is s" is
  two), and independent facts one clause or one formula states stay one passage of shape
  `conclusions` or `equations`, a conjunct and a part per fact, since no cut falls inside a clause
  or a formula. A display of separate formulas that each state an independent fact is cut before
  each formula after the first, the words that introduce it opening the first. **R-INSTANCE**: a
  sentence that names several structures having one property is cut per structure, the one cut
  inside a clause, each piece a passage however short (no single word of PD5's term-link line,
  and never `statement_misstates` for saying the sentence's property of its structure), and the
  general notion with its definition is a `definition` passage of its own. Both are audit rule
  3's: only a plan audited under it renders them, in the worker prompt too (`cut_calls`, in
  `{{#if cut_calls}}` blocks: the single-word line, Units, Displays, Inside a sentence — which
  keeps the clauses of one statement together where it kept "conjuncts about one subject", and
  cuts at `, and` before a clause with a subject of its own — Conjuncts, the worker's check and a
  paragraph after the two readings), and in the rule card beside C1, C2, C3, C4 and C8, the
  auditor's `under_cut` and `statement_misstates` glosses and the skeptic's example of a refuted
  flag (existence and uniqueness, C8, for two conjuncts about one subject, C4). An unaudited
  plan's prompts and a rule-1 or rule-2 plan's are byte for byte what they were, so a re-planned
  rule-1 or rule-2 run's worker cuts by the rules its reviewers judge, and the bench labeler and
  its gold are unchanged (R-INSTANCE's passages are often under the bench join's four-word floor,
  and a gold compound passage still joins one of the run's pieces). No gate check: whether two
  facts are independent is judgment. The rendered rule-3 worker prompt is 21,444 bytes with a
  real plan's paths (`WORKER_MD_RULE3_MAX_BYTES` 21,500), the unaudited one 18,886 as before.
  Every example is synthetic.
- **The challenge reviewer and worker, after smoke 4's four rejections**
  (`templates/prompts/locate-reviewer-system.md.tmpl`, `locate-challenge.md.tmpl`,
  `scripts/gate/verify_locate.py`, both bridges, `scripts/locate/tend_root.py`). Every
  reviewer rejection in smoke 4 asked for a verdict on a conditional record, under a rule
  card older than the conditional rule, and three of the four read the gate's ownership line
  as "coverage not checked"; the two workers who complied overturned a prominence to do it,
  both wrongly. The challenge card now says its approval is final, that the gate counts the
  owed records itself (its refusals quoted, a test holding them to the gate's words) and the
  conditional rule; every locate card reads the ownership line as the change set's and names
  `git diff --name-only <target>...HEAD`. A challenge's `OK:` line says `each of the <n>
  record(s) the task owes has one verdict` and the conditional records it did not owe. The
  challenge worker never overturns a prominence to meet a request, and on a locate run the
  revision preamble lets a worker decline feedback its rules forbid and finish `needs_review`
  instead of "do not re-litigate" (the faithfulness pipeline's revision is unchanged). A
  locate reviewer's git is `git diff`, `git show`, `git log` and `git status`, the audited
  reviewer's, never `Bash(git:*)`. A new plan is needed: the prompt and gate pins change.
- **The flip of the gap audit: a new audited plan writes audit rule 3** (`cflib.vocab.INVENTORY_AUDIT_RULE`
  is 3; `references/locate-documents.md` §13.12). `cf plan-locate --audit-rounds 2|3` now writes
  `locate.audit.rule: 3` and `CF_AUDIT_RULE="3"` in both wrappers and renders the five audit
  prompts with rule 3's blocks; an inventory planned under rule 1 or 2 (inv-pilot-3, inv-pilot-3b,
  smoke 4) keeps its rule on the host, at re-harvest and at re-gate, and a re-plan of one is
  refused as before. Under rule 3 the kind rule reads every formal `other` passage: the synthetic
  e2e's widget page gains that verdict in round 1 (three actionable, three applied).
- **The inventory prompt says three readings of the cut** (`templates/prompts/locate-inventory.md.tmpl`;
  the smoke-4 audit). Five of the seven narration pieces smoke 4 kept formal were a method's
  commentary or an instruction of a computation, one was a "need not" clause cut from the
  condition that replaces it, and a notation sentence was read as set-up and left in no passage.
  A bullet after the narration bullet says that commentary on a method (which may be used, which
  is faster) and an instruction ("proceed by adding 6 = 2 × 3", a number written as a product
  naming a step) are narration, unless a bound or a complexity class is stated; a paragraph after
  PD9 says that a sentence naming a symbol later statements use is a `notation` passage scoped
  `formal_math` (the smoke-4 gold's reading of such a passage), with the context sentence before
  it in its paragraph, while an example's objects are context, and that a
  "need not" clause and the weaker condition are one statement. They are the prompt's own words,
  not edits of the shared cut rules, so `references/vocabulary.md` §1.11, the bench labeler and its
  gold are unchanged. The rendered prompt is 18,886 bytes with a real plan's paths; the ceiling is
  18,900. Every example is synthetic. The rule-3 card says the same reading.
- **Review fixes to the skip audit (audit rule 2)**, before any run names it
  (`references/locate-documents.md` §13.11). *The diff's SKIP VERDICT line counts
  `paragraphs.json` entries* (two entries of one span, a theorem box and its paragraph, are two,
  as the skip report counts them), and `cflib.vocab` says a repair session's bound is a weight
  under rule 2 (5bb1c0f, which had no entry here). *Overlapping and nested skips* (the gate admits
  both): a verdict that judges one skip of the round (an upheld flag, a missed skip, the skip rule)
  names that skip alone, so two such skips are two verdicts whose weights are their own, an upheld
  flag no longer settles the skip it overlaps, and the repair unskips each skip once (the edit
  tool refuses a skip unskipped twice); a verdict carried by its span still names every skip of the
  new snapshot there, and the revision prompt says a skip two verdicts name is one `unskip`.
  *The SKIP blocks after the last passage count toward its part*, as a gap's skips count toward
  theirs (rule 1's parts are unchanged). *`validate --gate-config` reads the layer*: a skip
  statement whose `at` is not in the skip's text (often the sentence that introduces a skipped
  display, which lies before the skip) and skip evidence from outside the skip are named, where the
  merge dropped or refuted them with no word to the agent; the auditor's and the skeptic's
  validator lines carry `--gate-config`, and the turn's own acceptance check does not, so a slip
  costs a statement, never the round. *The `revert` of a harmful `skip_edit`* names the skip in
  the merged round's numbering, the id the next revision's `unskip` takes, with the `keep` that
  shrinks a widened skip back. *The soft threshold is at least half* of a section's math
  paragraphs under skips (and at least 2): "more than half" missed a section of inv-pilot-3's
  CRT skipped 6 of 12, by one paragraph; over pilots 1–3 the change raises the skip rule on that
  section's two skips and on no other. *A skip of no block that ends before the
  first block of a section* (a short description, an infobox) is furniture. *The rule card says a
  skip is right* on furniture and, outside a proof, a derivation or a worked example, on text that
  holds no statement, formulas or none, as the validator and the skeptic have it; nothing in a
  proof or a worked example is skipped, and the skip rule raises a skip of its narration over a
  math paragraph. *The rendering is smaller*: the SKIP blocks' legend only in a document with a
  skip, and a math block of the back matter clipped as prose. *A re-plan names the audit rule*:
  re-planning a run planned audited at rule 1 (inv-pilot-3) is refused, as it has been since the
  flip, and the refusal now says both rules. `inventory_edit.named_skips`, which nothing called, is
  gone. Rule 1 is unchanged, byte for byte.
- **The flip of the skip audit: a new audited plan writes audit rule 2** (`cflib.vocab.INVENTORY_AUDIT_RULE`
  2; `references/locate-documents.md` §13.11, `docs/design/stage_map.md` §4.0's fifth versioned key,
  `locate.audit.rule`). `cf plan-locate --audit-rounds` now records `locate.audit: {rule: 2, …}`,
  exports `CF_AUDIT_RULE=2` in both wrappers and renders the audit's prompts with their skip
  text, so the stage judges every skip, raises the skip rule, repairs with `unskip` and counts
  the skips in the receipt. No gate file changes: an unaudited plan is what it was, and a run
  planned before (inv-pilot-3, rule 1) runs, re-gates, re-harvests, tallies, exports and carries
  as it did. The locate e2e driver's audited run is now rule 2's: the widget page's worker skips
  a corollary, round 1 flags and upholds the skip, the fake revision unskips it and adds its
  statement, round 2 approves, and the receipt's `audit.skips` holds one skip of one math
  paragraph before and none after.
- **Review fixes to the audited inventory and the wide bound**, before any run names either.
  *The wide bound's kinds guard reads the run's head* (`locate_text.Blocks.runs_on(…, wide=True)`,
  docs/proposals/statement-cut.md §3.1): a `dt` term or a quotation no longer runs on into a
  second `dd`, or into a paragraph that opens with a formula, once an introducing `dd` sits
  between; a quotation's sentence still runs on into a paragraph that continues it. Over the
  pilots' 83 articles no wide run and no wide extent moves (969 runs, every block start), and
  inv-pilot-2 re-gated under (3, 3, 3) still counts 0 widened, 15 wide starts, 1 unheld.
  *The rule card says what the gate allows*: a text-only `dd` that starts with a capital stays
  refused (one that goes on lower-case is admitted), and a mark is crossed before any block that
  finishes the sentence; C7 says `statement` carries the hypotheses and objects so it reads on
  its own (and `statement_misstates` names C7), and C9 that a theorem stated only in parts takes
  the passage that states its first part. *A merge that drops one passage twice* is refused by
  `validate --kind ops` and by `inventory_edit.apply` (it once raised in `list.remove`, which
  crashed `collateral` and left the reviewer turn exiting 1 on every retry), and a rendering or
  collateral that fails on its input ends the round as `audit_failed` instead of exit 1.
  *No progress is mechanical*: a reviewer turn whose document is the bytes the last round
  rejected (a gate-loop reset, or a revision that changed nothing) approves with no agent call,
  counted as `no_progress`, so no check is paid for and the stale ops file is never re-applied
  as collateral. *A `revert` verdict reaches the worker it is meant for*: the revision stages
  `r<k>.collateral.json` and its prompt says how to put an entry's `before` back. Tests cover
  every refusal of `audit_turn.py`'s configuration (exit 78), the missing prompt (66), a `.tend`
  symlink and the revert-attempt limit (exit 1). The unaudited path is unchanged.
- **The flip of the wide bound: `cf plan-locate` writes statement-cut rule 3**
  (`docs/proposals/statement-cut.md` §7). Every gate.json it plans now names `judgment_rules` 3,
  `span_end_rule` 3 and `statement_cut_rule` 3 (`cflib.vocab.STATEMENT_CUT_RULE =
  STATEMENT_CUT_WIDE`, and the gate's inlined copy; `KNOWN_STATEMENT_CUT_RULE` stays 3). A newly
  planned inventory's rule 10 (b) holds an end to the wide bound, so an `until` that keeps a
  sentence's words across a footnote mark, into an indented line that finishes it, or into a
  paragraph that opens with a formula is admitted, and its receipt's `cut` block carries
  `widened`, `wide_starts` and `wide_unheld`. No default end, `passage_key` or carry moves (rule
  3 reads span-end rule 3, as rule 2 does), and no prompt word changes: the worker's prompts ask
  for no end past the loose bound, which the wide bound contains, and an audited plan's rule card
  says what the wide bound allows. An unaudited plan differs from the one the commit before the
  audit made only in `statement_cut_rule` in its three gate.json files, the shas of the staged
  files whose source changed, and the worker prompt's kind sentence, and a test holds it there.
  A run planned before keeps the versions its gate.json names and re-gates, re-tallies and
  re-exports as it did; a locate run planned now over inv-pilot-2's (3, 3, 2) inventory is
  admitted. The e2e driver's runs name (3, 3, 3), and it checks the receipt's rule-3 counts
  (0 widened: its recorded pages hold none of the three shapes). The version pins move to
  (3, 3, 3); `tests/_locate_fixtures.before_the_wide` plans as the planner did before.
- **The inventory prompt says the kind rule in one sentence** (`templates/prompts/locate-inventory.md.tmpl`,
  "What a passage is"): a step of a proof, a derivation or a worked example that asserts a fact
  or an equation is kind `theorem`, never `other`. It is the rule the audit's merge applies
  mechanically to a formal `other` passage in a proof or example section
  (`references/locate-documents.md` §13.7), told to the worker where it picks a kind, audited or
  not. Every inventory planned from now on renders a `worker.md` with that sentence and a new
  template sha; nothing else in a plan moves. It is not a cut rule: `references/vocabulary.md`
  §1.11 and the bench labeler are unchanged. The rendered prompt is 17,373 bytes with a real
  plan's paths, under the 17,500 ceiling.
- **The gate and the receipt know statement-cut rule 3** (docs/proposals/statement-cut.md
  §2.2 and §3.3). The planner still writes (3, 3, 2), so nothing a run planned now is held to
  changes. `cflib.vocab.STATEMENT_CUT_WIDE` is 3 and `STATEMENT_CUT_SPAN_END` is
  `{1: 2, 2: 3, 3: 3}`, with the gate's inlined copies and `KNOWN_STATEMENT_CUT_RULE` 3. Rule 3
  reads span-end rule 3, as rule 2 does, so no default end, `passage_key` or carry moves, and
  `passage_ids.py` is unchanged. It is rule 2 with rule 10 (b) held to the wide extent
  (`locate_text.Blocks.extent(…, wide=True)`), so an `until` across a footnote mark, into a
  `dd` that finishes the sentence or into a paragraph that opens with a formula is admitted.
  Its refusal has words of its own. Rule 1's and rule 2's checks, messages and counts are the
  ones they were, and so is the refusal of span-end rule 3 without a statement-cut rule, which
  only a broken gate.json prints. The pair (3, 3) is admitted beside (2, 1) and (3, 2);
  statement-cut rule 3 with span-end rule 1 or 2, (1, 3) and (2, 3), is exit 3.
  Under rule 3 the receipt's `cut` block adds `widened` (formal passages past their loose
  extent: the merges only the wide bound admits), `wide_starts` and `wide_unheld` (over
  `Blocks.wide_tails`), in `harvest.gate_corpus.CUT_COUNTS_3`, with a `cut, wide:` receipt
  line of its own. Tests cover each of the three shapes admitted under rule 3 and refused in
  rule 2's exact words, a refusal past the wide bound in rule 3's, the unmoved default end, the
  counts and the pairs. `tests/_locate_fixtures.before_the_wide` pins a (3, 3, 2) plan.
- **The wide bound, as a library** (docs/proposals/statement-cut.md §3.1). inv-pilot-2's
  repair prototype made 16 merges that rule 10 (b)'s loose bound refuses, 12 of them at three
  shapes it stops at: a footnote mark between a sentence and what goes on with it, a `dd` of text that
  finishes the sentence, and a paragraph that opens with a formula and continues it.
  `locate_text` gains `FOOTNOTE_MARK`, `Blocks.next(…, marks=True)`, `opens_with_formula`,
  `finishes`, `runs_on`/`chain`/`extent` with `wide=True` (and the module `extent`), and
  `wide_tails`. Nothing reads it yet: the gate learns it as statement-cut rule 3 in a later
  commit. Every default (`wide=False`, `marks=False`) is the code it was, so no end, verdict,
  receipt or export of any run moves; tests pin next() and runs on(D) verbatim, hold extent ≤
  continued ≤ loose ≤ wide at every start of every test page, and pin each mark shape, each
  guard, the shapes left refused (a text-only `dd` with a capital, a "Then" line between two
  displays, "They" after a display, a `dt`'s `dd`, a list after a colon) and the one known false
  positive (a missing full stop before a sentence that opens with a formula).
- Review fixes to inv-pilot-1's fixes, before any run names (3, 2). **The continued extent
  never runs past the loose bound:** `locate_text.Blocks.ends_with_display` reads a block's
  kinds before it reads it as a display, so a theorem box that holds only a display is a unit
  and no continuation leaves it. Before, span-end rule 3's default end ran on out of such a box
  while rule 10 (b)'s loose bound stopped at it, so the gate refused the end `passage_ids.py`
  wrote. A test now holds extent ≤ continued extent ≤ loose extent at every start of every
  test page. **A capitalised abbreviation starts a sentence:** `continues` compares the word as
  written, so "E.g." or "Cf." after a display is no continuation. **The receipt counts the
  loose-only tails** under statement-cut rule 2: `loose_starts` and `loose_unheld`
  (`Blocks.loose_tails`, `harvest.gate_corpus.CUT_COUNTS_2`). These are the paragraphs only
  the loose bound reaches (after a display the gate cannot see, say), which the default end
  stops before, so a tail no `until` kept is no longer invisible. **The prompt:**
  - the Context rule, in all three places and spec rule 7, counts the paragraph that finishes a
    sentence after its displays as the sentence's own, and lets anything that comes between (not
    only narration) leave the sentence opening nothing (reading 1);
  - the end paragraph names running a sentence over the words that finish it as `until`'s third
    use, and makes a list item the one place those words start a passage of their own, because
    the loose bound never leaves one (the check says the same);
  - the scope bullet says the subject goes on the passage that states the theorem while L2 holds
    a curated hit, and gate rule 4's refusal adds that a lead that only describes it is
    `expository`.

  The gate's module docstring names the loose bound and (h), and `references/locate-documents.md`
  §2 names end rule 3. New gate-level tests cover rule 10 (b)'s loose bound on a display the gate
  cannot see and the receipt's `connective_starts`. The prompt's size test now renders host paths
  as long as a real plan's. Placeholders had hidden about 320 bytes, so the run's `worker.md` is
  17,252 bytes (16,933 with placeholders), about 3.0 KB (21%) over inv-pilot-1's 14,243. The
  ceiling is 17,500.
- The flip of inv-pilot-1's fixes (`docs/proposals/statement-cut.md` §7): `cf plan-locate` now
  writes `judgment_rules` 3, `span_end_rule` 3 and `statement_cut_rule` 2 into every gate.json it
  plans (`cflib.vocab.SPAN_END_RULE = SPAN_END_CONTINUED`, `STATEMENT_CUT_RULE =
  STATEMENT_CUT_CONTINUED`, and the gate's inlined copies), so a newly planned inventory ends a
  passage that gives no `until` at its continued extent, which runs on over the paragraph that
  finishes a sentence after its display, and rule 10 holds it to the loose bound, refuses two
  `formal_math` subjects (h) and counts the continuations, subjects and connectives in the
  receipt. The words for span-end rule 3 join the inventory prompt with it. The Units rule, in
  the prompt, `references/vocabulary.md` §1.11 and the labeler's inventory mode, lets a passage
  run into the paragraph that finishes its sentence after its displays (its meaning only: no
  heuristic and no `until`). The end paragraph says the same, gives `until` where narration
  follows in that paragraph, and, where the tool's end stops before the words that finish such a
  sentence (a display the gate cannot see), gives `until` at the sentence's last word and never
  a skip (reading K1). The check before the gate says the words after a display or a footnote
  marker are their sentence's, and the gate's refusals name two passages marked `subject` and
  say what the gate counts. The rendered inventory `worker.md` is now 16,748 bytes, and its
  test ceiling rises from 16,500 to 17,000. The labeler stays `locate-label/6` (reading 8). The
  e2e driver's runs name (3, 3, 2), its ends are rule 3's, it checks the receipt's rule-2
  counts and refuses a document with two formal subjects; its recorded pages hold no
  continuation, so their figures stay (reading 9). A run planned before keeps the versions its
  gate.json names and re-gates, re-tallies and re-exports as it did; inv-pilot-1's (2, 1)
  inventory still admits a locate plan over it.
- The inventory prompt's fixes for inv-pilot-1's faults 2, 3 and 5, in words only
  (`docs/proposals/statement-cut.md` §3.4 rules 6, 7 and 9; the maintainer's readings of
  2026-10-21). The shared cut rules change in `references/vocabulary.md` §1.11, the template and
  the labeler's `CUT_RULES` together, one physical line each, and a test now refuses a wrapped
  one. **Proofs:** a proof or derivation is judged a sentence at a time and is never kept,
  skipped or scoped `expository` whole for being a proof; a worked example's sentence that
  asserts its own equation is a passage, never a conjunct of the example (K2). **Context:** a
  set-up sentence is no `formal_math` passage of its own. When the statement that uses it lies
  in another paragraph, or narration comes between, it opens nothing and is `expository` where
  the coverage floor needs it. A symbol defined for later use is a `notation`. **The subject:**
  a sentence describes when it would stay true whatever the result concluded, and the marker
  goes on at most one passage, the first in reading order that states the theorem or
  definition (K4). The template alone gains a skip sentence (a skip never drops a statement no
  passage keeps, a proof, a derivation or a worked example) and a check before the gate
  (connectives, PD9's reasons with the `until` a leading "Since …," needs, context and the
  subject). The PD5 bullet no longer says "or a span around it". The labeler's enumerators
  also say a statement inside a proof is never left out as narration. The labeler stays
  `locate-label/6` (`scripts/bench/gold.py` says why). The words for span-end rule 3 wait for
  the flip. A plan made now renders the new words, and the template and labeler shas move. A
  run planned before keeps the prompts it rendered, and no re-gate, tally or export reads them.
- The locate prompts keep no pre-flip text (reading F4's consequence, `docs/proposals/
  statement-cut.md` §9). The rules-2 branches of the locate, challenge and worker-system
  templates (`{{#unless judgment_rules_statement_cut}}`) and the inventory prompt's version-1 end
  paragraph (`{{#unless span_end_statement_cut}}`) are deleted, with both slots and the `if`
  blocks around the statement cut's text: no plan made since the flip could render them, and no
  re-gate, tally or export renders a prompt. A plan made now renders its four prompts byte for
  byte as before (every combination of the templates' other slots, and the e2e's runs, checked);
  the template files' shas move, so `locate.config_shas.prompts` differs for a run planned after
  this change. A run planned before the flip keeps the prompts it rendered. The tests that held
  the deleted text (`TestThePromptsBeforeTheFlip`, the rules-2 coverage words, version 1's end
  paragraph) go with it; `TestThePromptsAreTheFlipsOnly` holds the templates to the versions the
  planner writes. `references/vocabulary.md` §1.11 says this in the past tense.
- `SKILL.md`'s locate workflow says what a run planned now is held to: step 4 named each
  passage's claim list and a challenger's own one under gate rule 9, which a gate of judgment
  rules 3 refuses; it now describes the statement cut's inventory (rule 10, span-end rule 2) and
  judgment rules 3 (the inventory's conjunct ids, a bundle, both instantiation tests), and says
  that a run planned before keeps the rules it names; step 5 names status rule 2 and
  `cf-locate-recordset/2`. `docs/proposals/wikipedia-locate.md` gives the challenge's
  `absence_sample_rate` default as 0.5 (0.25 before the flip).
- The statement cut, tidied after the flip. The inventory's Lists rule says what the chain that
  stops at a list item implies (the maintainer's reading C4): a display inside a list item
  belongs to that item, never to the list's lead-in sentence — in `references/vocabulary.md`
  §1.11, the inventory prompt and the labeler word for word (the labeler's file sha moves; no
  gold of `locate-label/6` is labelled yet), and in the proposal's §3.4. `cf locate carry
  --rows`'s help says a row is held to the conjunct test unless it says `"reads_conjuncts":
  false`; `cflib.vocab.CUT_CONNECTIVES` says the span-end rule 2 reads it for the separator; the
  proposal's §5 says the challenge holds PD6 beside a bundle confirm, its §6.3 what the carry
  package built, and its §9 that the maintainer settled every pre-flip reading on 2026-10-21
  (and that the gold bundle's, taken after, is not among them); two CHANGELOG bullets below say
  what their changes did, and docs that spoke of the flip as ahead speak of it as made.
- **The statement cut's flip** (`docs/proposals/statement-cut.md` §7). What changes for a run
  planned now:
  - its `gate.json` names `judgment_rules` 3, `span_end_rule` 2 and `statement_cut_rule` 1 in
    every mode (`cflib.vocab.JUDGMENT_RULES = RULES_STATEMENT_CUT`, `SPAN_END_RULE =
    SPAN_END_STATEMENT_CUT`), and a locate run's `run.yaml` names `locate.status_rule` 2;
  - an inventory is cut one passage per statement and held to gate rule 10, its passages ending,
    without `until`, at the next passage's start or their extent (span-end rule 2), as its
    prompt now says;
  - a locate or challenge task's manifest carries the inventory's `statement`, `conjuncts` and
    `shape`, and its prompts and gate are judgment rules 3's: no claim list, the inventory's
    conjunct ids, a coverage claim only as a bundle on a compound statement, the smoke-3 audit's
    fixes (`tests`, `instantiation`, the ℕ-for-ℤ tripwire, `special_case`, `fills`, conditional
    records, `cut_flag`);
  - its outcomes are derived under status rule 2 (a survived bundle is one more location of its
    weakest part's relation; a located passage the challenge leaves with nothing that counts is
    owed a hunt) and it exports `cf-locate-recordset/2`;
  - a challenge samples supporting absences at 0.5 by default (`--absence-sample-rate`, fix 5c;
    a plan made before recorded the 0.25 it drew at);
  - `cf plan-locate --preset locate` refuses an inventory whose `gate.json` names no
    `statement_cut_rule`, and `--preset locate-challenge` a locate run of `judgment_rules`
    below 3: inventory again and locate again, since no new run of the earlier flow is offered;
  - the bench labeler is `locate-label/6`, which `bench.gold.LABELER_RULES` maps to 3, so gold
    of `/5` scores no run planned from now on until it is relabelled, and gold of `/6` no run
    planned before (its label rows keep a `hasPart`'s `conjunct_relation` and the labeler's
    bundle, its statuses status rule 2's);
  - `config_shas.gate_files` moves (the gate's inlined versions) and the prompts it renders are
    rules 3's, so a run planned now is no A/A replicate of one planned before.
  Nothing a run planned before the flip names changes: every workspace run re-gates with its own
  `gate.local.json`, and smoke 2 and 3 re-tally and re-export, byte for byte on the base and here.
  Tests of the earlier flow plan their runs as the planner did before the flip
  (`tests/_locate_fixtures.before_the_flip`); the synthetic e2e driver runs the new flow end to
  end (a compound statement, a bundle confirmed part by part, the record set `/2`) and re-cuts
  its sourceset for `cf locate carry` and `cf bench carry`. A frozen gold row with no
  `labeler_version` is refused as labelled before `locate-label/5`, the first version whose rows
  name it, rather than before this checkout's version.
- The statement cut's bench gold (`docs/proposals/statement-cut.md` §6.2, the carry package's
  `scripts/bench/gold.py` share, which needed the fixes package's `derive_status(…,
  status_rule=2)`). Gold of a labeler that labels by judgment rules 3 is transcribed as a run of
  those rules is derived: a `hasPart` keeps its `conjunct_relation` (another value drops it with
  the reason), the label's new `bundle` names the span's bundle, which stands on two or more
  distinct `hasPart`s kept at the pin, each strong to its claims, as a survived bundle of its
  weakest part's relation, and the statuses are status rule 2's (`bench.gold.gold_bundle`,
  `gold_status_rule`); a G3 row adds `bundle` and `bundle_problem`. No labeler version maps to
  judgment rules 3 yet, so gold labelled now is transcribed as before, keys and statuses alike.
  The labeler's label mode asks for the bundle (`bundle`, `[]` for none), so its file's sha
  moves; its version stays `locate-label/5` until the flip.
- The statement cut's four packages merged (`docs/proposals/statement-cut.md` §6: cut, gate,
  fixes, carry), the flip (§7) not made: no planner writes the new versions. Every workspace
  run re-gates, smoke 2 and 3 re-tally and re-export, and a re-plan of chal-smoke-2,
  chal-smoke-3 or loc-smoke-3 writes the same tasks, plans and prompts, on the base and here
  (`run.yaml`'s config shas and the staged gate code aside). An inventory planned now renders
  the cut rules (its end paragraph stays version 1's), which its gate, naming no
  `statement_cut_rule`, does not hold it to, so no run is planned before the flip. Gate rule
  2's refusal of an inventory end under span-end rule 2 names the separator the end leaves out
  ("the start of the separator before the next passage"), as the cut package's
  `locate_text.passage_end` ends it; locate-documents §2 no longer says rule 10 is unbuilt, and
  the proposal's status line says what is built.
- The statement cut's inventory side (`docs/proposals/statement-cut.md` §3, the "cut" package):
  the end rule's version 2 and the cut rules. `locate_text.passage_end(…, rule=2, next_start=…,
  formulas=…)` ends a passage that gives no `until` at the earlier of its extent and the next
  passage's start, and `locate_text.Blocks` gives the definitions the end rule and gate rule 10
  read (an offset's outermost block, the next block, a display block, a block whose last
  sentence introduces what follows, the displays it introduces, a run of list items, the
  extent), with `locate_text.units` for the receipt's sentence and display counts.
  `passage_ids.py` places every passage before it ends any and ends them by the `span_end_rule`
  of the `gate.json` beside it (else `--span-end-rule`, default 1), writing `conjuncts` and
  `shape` back as it found them. The inventory prompt and the bench labeler's inventory mode
  carry the cut rules word for word with `references/vocabulary.md` §1.11 ("How the inventory
  cuts": the test, units, displays and chains, the connectives inside a sentence, lists,
  proofs, context in `statement`, conjuncts and the closed list of shapes, the subject marker on
  the stating passage) and PD9 in its new words; the labeler's passages carry `statement`,
  `conjuncts` and `shape`. `references/locate-documents.md` §2 documents both end rules and the
  inventory passage's two new fields. Rule 1 is today's function, unchanged: no run names
  `span_end_rule` 2 until the proposal's flip, so every run places, gates, tallies and exports
  as before (every workspace run re-gated and smoke 2 and 3 re-tallied and re-exported, byte
  for byte, on the base and on this change). The labeler's file sha moves; its version stays
  `locate-label/5` until the flip.
  After review, version 2 leaves the **separator** before the next passage's start out of both
  passages (`Blocks.separator_start`: whitespace, a connective of `CUT_CONNECTIVES`, restated in
  `locate_text` and pinned, and a comma or semicolon, never inside a formula), so a statement
  has the span, and the `passage_key`, an `until` at its last word gives, whether or not its
  neighbour is cut. A formula's last character is read past `\\`, `\end{…}` and closing braces,
  and a block's past closing brackets and quotes after a full stop; a formula's `!` is a
  factorial and ends no sentence (`FORMULA_STOPS`), so `x^{n!}` stops no chain; a chain stops
  at a list item. The outermost block stays the bound, an upper one, inside a blockquote, a
  `dd` or a list item holding other blocks, and two adjacent lists are one run (the proposal's
  §3.1 says so). The inventory prompt says where a passage ends by the run's own end rule (a
  `span_end_statement_cut` slot, version 1's paragraph until the flip, so a run planned before
  it gets no rule-2 prompt), its context example is synthetic, and its conjuncts are held to
  twelve words alone or read one after the other; the labeler asks for no `until`, requires
  `statement` and admits only `id` and `text` in a conjunct.
- The statement cut's gate (`docs/proposals/statement-cut.md`, its gate package), bound only by a
  gate.json that names its versions, which no planner writes before the proposal's flip, so every
  run planned before it gates, tallies and exports as it did (re-gating every workspace run and
  re-tallying and re-exporting smoke 2 and 3 gives identical results on the base and here). The
  gate knows `judgment_rules` 3, `span_end_rule` 2 and the new `statement_cut_rule` 1, and refuses
  a gate.json naming the cut with an earlier end rule. **Span-end rule 2** ends an inventory
  passage without `until` at the earlier of its extent and the next passage's start, the
  separator before that start in neither passage (`locate_text.Blocks`, `passage_end(...,
  rule=2)`: the cut package's `locate_text.py`, which the gate is built against). **Rule 10**
  refuses an inventory's overlapping spans, a span run from one block into the next outside a
  theorem box, the displays its sentence introduces or a compound statement's list, an end inside
  a formula, conjuncts other than none or 2-5 of a closed shape, conjuncts on a passage scoped
  out, a passage scoped out that is not `supporting`, and two passages of one text; it counts each
  formal passage's compound statements and their conjuncts, sentences and displays into the
  harvest receipt's new `cut` block and refuses nothing on them. **Rule 9 under judgment rules
  3** checks locate and challenge documents against the inventory's conjunct ids (no `claims`), a
  part's `conjunct_relation`, a coverage claim as a bundle on a compound statement (one part per
  conjunct, none stating them all, each strong), the audit's fixes 1a, 2a-2d, 4a and 5b, a
  `cut_flag` on an unresolved passage, a challenge's `part_relations`, `unstated` and
  `part_reasons`, `fills` for a located passage its verdicts empty (A1, A2; an absent fill
  accepts no candidate) and a prominence sample's conditional records (A3). A part is held by its
  `conjunct_relation`: one whose relation to its conjuncts is strong owes fixes 2a-2d and R1's
  generality words as a strong location does, since the bundle takes its weakest part's relation
  (the FM1/FM4 over-claim cannot pass by being written as a bundle). A bundle confirm beside the
  same document's verdict giving one part every conjunct is refused (PD6, which rules 2 held
  through `part_clauses`). The tripwire's words are `cflib.vocab.NAT_FOR_INT_INTEGER_WORDS` and
  `NAT_FOR_INT_NATURAL_WORDS`, and the gate's copy of `REFUTE_BY_DEFAULT_PROMINENCE` is pinned
  with the others. The planner writes
  `statement_cut_rule` beside the other keys, `locate.status_rule` into a locate run's run.yaml,
  the inventory's statement, conjuncts and shape into the manifests, and refuses a locate plan over
  an inventory without the cut and a challenge of a locate run of earlier rules, all from the flip
  on (`locate.plan.statement_cut_planned`). The e2e driver gates good and bad documents under the
  cut's versions.
- The statement cut, the fixes package (`docs/proposals/statement-cut.md` §2, §5 and §6.2): the
  bundle's relation, the owed hunt, the record set of status rule 2, the smoke-3 audit's
  failure-mode fixes in the judgment prompts, the challenge plan and the outcomes. Everything
  binds only what names its version, and no planner writes one before the flip, so every run
  planned so far plans, gates, tallies and exports as before (re-gating every workspace run,
  re-tallying and re-exporting smoke 2 and 3, and re-planning their challenges give identical
  results on the base and here: the challenge plans, every task record and the rendered
  prompts, byte for byte).
  - **Status rule 2** (`run.yaml` `locate.status_rule: 2`; a run.yaml without it is status rule
    1): `derive_status(…, status_rule=2)` reads a coverage claim as a bundle, `relation`
    required, and a survived bundle counts as one more location of its weakest part's relation
    where status rule 1 makes it one more `exactMatch` — so a definition passage whose bundle is
    `broadMatch` is `partial` (the maintainer's answer to question 9). The cases are a new
    hand-written table, `tests/data/locate/status-parity-2.json` (`cf-locate-status-parity/2`);
    the round-3 table is untouched and still passes, the keyword given or not. The outcomes
    compute the bundle's relation (the weaker of each part's `conjunct_relation` and a
    confirm's `part_relations` entry for it), copy the inventory's `statement`, `conjuncts`
    and `shape` onto each row, keep an unresolved passage's `cut_flag`, and owe a hunt to a located passage
    the challenge leaves with nothing that counts and no absence (challenge
    `challenge_pending`, `owed: "hunt"`, refused by the export; anomalies 1 and 2), applying a
    challenge document's `fills` first; the counts gain `owed_hunt`, `by_cut_flag` and
    `bundles`. Such a run exports `cf-locate-recordset/2`: the seal's `status_rule`, the
    revision-free `passage_key` on every record and `pair_key` on a location's (restated in
    `locate.records` in lockstep with `cflib.hashing`), and a bundle with its relation, its
    conjuncts each beside its part and its `bundle_key`. What judgment rules 3 add to a
    document is read on such a run only: a location's `conjunct_relation` and `tests`, a
    verdict's own (its `tests` replacing the worker's, its `conjunct_relation` never
    stronger), a confirm's `part_relations`, a refute's `unstated` and `part_reasons` and an
    uphold's `special_case`; a location keeps `tests` while it is held to a strong relation, a
    `hasPart` by its `conjunct_relation` (`outcomes.held_relation`), so a confirm of a strong
    part no longer drops them. A run of status rule 1 derives the rows it always did whatever
    keys its documents carry (the proposal's §7).
  - **Conditional records** (anomaly 3): a challenge plan made under judgment rules 3 puts every
    unrequired location, absence and unresolved record of a supporting passage whose prominence
    sample is required in that passage's task, `conditional: "prominence"`, its planned value
    unchanged; such a verdict counts only beside an overturn of that prominence to `subject` or
    `central` in the same challenge run. A plan made now marks none. A challenge record carries
    the passage's `statement`, `conjuncts` and `shape`, and a coverage record's part its
    `clauses` and `conjunct_relation`, only over a locate run of status rule 2 (the proposal's
    §1.2): the parts of a run of judgment rules 1 or 2 name claim ids too, which a coverage
    record never showed, so a challenge re-planned over one writes the task records it always
    wrote.
  - **The judgment prompts under rules 3**: `templates/prompts/locate.md.tmpl` and
    `locate-challenge.md.tmpl` (and the locate system prompt's gate list) describe the
    statement cut's flow — no claim list, the inventory's conjunct ids, a `hasPart`'s
    `conjunct_relation`, the bundle and its under-claim rule, `tests` on every strong location
    and verdict, `instantiation` on every strong location, the ℕ-for-ℤ tripwire, the longer
    generality list, an absence's and an uphold's `special_case`, a confirm's `part_relations`,
    a refute's `unstated` and `part_reasons`, `fills`, `cut_flag` and conditional records — and
    carry the five new prompt rules (fixes 2a, 2e, 3a, 4b and 5a) word for word with
    `references/vocabulary.md` §1.11 and the labeler, as do PD9's reworded line (a stated reason
    is a passage of its own) and FM7's (one clause list, the inventory's). A `hasPart` is held
    by its `conjunct_relation`, as the gate holds it: a strong part writes its `instantiation`
    and `tests` and is held to the tripwire and the generality words, a verdict that keeps one
    gives its own `tests`, and the worked bundle and downgrade examples do so. The labeler's
    label mode gives a `hasPart` its `conjunct_relation`; its version moves at the flip.
    The prompts render this text only for a plan whose gate.json names judgment rules 3 (the
    slot `judgment_rules_statement_cut`, from `vocab.JUDGMENT_RULES`): a plan made before the
    flip, a challenge re-planned over an old run among them, renders the rules-2 prompts it
    rendered before, byte for byte, so no prompt contradicts its gate. For that the prompt
    renderer (`scripts/plan/render.py`) nests a block inside one of its own kind; a template
    with no such nest renders as before.
- Locate judgment rules: PD10's exception. A link that is itself what the passage asserts is no
  encoding: when the lemma that links the two notions is the passage's own theorem, a
  declaration stated with the notion whose definition already contains that theorem's claim
  presupposes the passage, and it is `relatedMatch` (R5). Read without it, PD10 would make such
  a declaration a formalization of the theorem it presupposes, and reverse the reading PD8's
  named-lemma bridge gave it before PD10; the maintainer's example links two definitions the
  passage takes as given, so it keeps its encoding. The rule line is word for word in the four
  rule files (`references/vocabulary.md` §1.11, both locate prompts, the labeler), and the PD8
  instructions that point at PD10 name the exception. It awaits the maintainer's confirmation.
  As for PD10 itself: no gate check, `JUDGMENT_RULES` stays 2, the labeler stays
  `locate-label/5` (it has still labelled no gold), and the two prompt templates' shas move.
- Locate judgment rules: PD7's test for "trivial", and PD10 on encodings (the maintainer's later
  answers of 2026-10-20; `references/vocabulary.md` §1.11 has both, and his words on
  `riemannZeta` verbatim beside his earlier ones on PD7).
  - **PD7**: a value the declaration gives where the passage leaves one undefined (a junk or
    default value) is trivial when the passage's mathematics does not depend on it; in the
    maintainer's words, "it shouldn't matter what the value of riemannZeta 1 is". So
    `riemannZeta` at its pole s = 1 is again R1's example of a junk value (`exactMatch`) against
    a passage that defines the zeta function away from s = 1, beside x/0 = 0 and quadratic
    reciprocity's p = q, though Mathlib proves a closed form there (`riemannZeta_one`). This
    reverses the earlier text that left it to the record's note, and the test that held it out
    of R1. The test is of values, the subject of his first and last answers: an extra case of
    the statement itself (a ring that is not a field, where the passage has a field) is trivial
    when it reduces to a tautology, and one with real mathematical content is still a
    generalisation (`broadMatch`), although the passage's mathematics does not depend on it
    either. Read as a test of every case, it would have made every generalisation an
    `exactMatch`. R1, PD7, the exactMatch instructions of both prompts and the labeler's
    evidence line state it so.
  - **PD10, encodings**: a library lemma that links Mathlib's standard notion to the passage's
    notion of the same thing (`IsCoprime` and "no common factor other than 1", through
    `Int.isCoprime_iff_gcd_eq_one`) is an encoding difference (R1: a `closeMatch` by `encoding`
    when nothing else differs), not a PD8 bridge. PD8 governs bridges between statements, not
    between two definitions of one notion, so PD10 sharpens R1 and limits PD8. The prompts' and
    the labeler's PD8 instructions say so; PD8 is otherwise as encoded, and a weakening stays
    R2's. The maintainer named the convention; the `closeMatch` is the relation table's
    definition (the same up to a convention), not a further ruling of his.
  - Neither is checked mechanically. No gate check changes, `JUDGMENT_RULES` stays 2 and
    `verify_locate.py` is unchanged, so `config_shas.gate_files` does not move. The prompt
    templates `locate.md.tmpl` and `locate-challenge.md.tmpl` changed, so
    `locate.config_shas.prompts` differs for a run planned after this change: such a run is no
    A/A replicate of one planned before it.
  - **The labeler stays `locate-label/5`.** Its text changed and its sha moved, but a version
    matters to the bench through the judgment rules `bench.gold.LABELER_RULES` maps it to, which
    the score holds gold and runs to, and through one version per gold, which freeze holds. With
    no gate check added the runs stay at rules 2, and a `/6` would map to 2 as well: the score
    would refuse nothing it does not refuse now. `/5` had labelled no gold yet (bench v0's
    relabel under it is still to come), and the transcriber refuses a result whose args were
    emitted from another labeler sha, so gold of `/5` is labelled by this text. Once gold has
    been labelled under a version, a change to its rules bumps it. Neither the version nor the
    rules tell a run of rules 2 planned from the earlier text (no PD10, no test for a value)
    from one planned from this: the score holds a run to its `judgment_rules` and pins its
    `config_shas.prompts` without comparing them with the gold's labeler, and a `/6` would map
    to 2 as well. None had been planned; one planned from the earlier text is planned again
    before it is scored.
- Locate judgment rules PD5–PD9 (the maintainer's policy calls of 2026-10-20, after the smoke-3
  audit; `references/vocabulary.md` §1.11 has each rule, what it replaces or sharpens, and his
  words on PD7 verbatim). Gate rule 9 is now version 2 (`cflib.vocab.JUDGMENT_RULES`,
  `RULES_PD5_PD9`), and `cf plan-locate` writes `judgment_rules: 2` into every gate.json it
  plans; a run that names 1, or none, is gated as before, at harvest and by the checkout's gate.
  - **PD5**: only definitions, theorems, equations and declarative statements about mathematical
    objects are `formal_math` passages. The inventory scopes proof narration and commentary
    `expository` or leaves it out, so it is never located and has no status; inside a proof each
    statement is a passage of its own, an asserted equation or fact of kind `theorem`. The
    inventory prompt says so, and the locate and challenge prompts keep narration off the claim
    list. A locate worker cannot re-scope a passage, so narration an inventory scoped
    `formal_math` still ends `unresolved` (its reason naming PD5): one `UNRESOLVED` record, no
    status, counted in the unresolved rate, until the source is inventoried again. Term links
    are a later feature.
  - **PD6**: a coverage part that alone states every clause is one location with its own
    relation, not a part. Under rules 2 the gate refuses a coverage claim one of whose parts
    names every claim (from the `clauses` rule 9 already reads), and a challenger's coverage
    confirm whose `part_clauses` give one part every claim of its own list. A challenge never
    raises a relation, so that part stays a `hasPart` and the passage `partial`: a challenge
    planned now of a locate run planned under rules 1 can lower a passage's status that the
    run's coverage claim had raised. Locating it again under rules 2 gives the one location.
  - **PD7** replaces R1's junk values. A declaration that agrees with the passage on the
    passage's domain is `exactMatch` when every case outside it is mathematically trivial (a junk
    or default value, x/0 = 0; an extra case that reduces to a tautology), `broadMatch` when one
    has real content (a generalisation), and `closeMatch` only for a difference where both sides
    are defined. "Trivial" is a judgment, so the record's note says why; `riemannZeta` at its
    pole is junk by the maintainer's test for "trivial" (the entry above).
    `junk_value` leaves `CLOSE_MATCH_CONVENTIONS`;
    `LEGACY_CLOSE_MATCH_CONVENTIONS` keeps it for runs of rules 1. Under rules 2 the gate
    refuses it, except on a challenger's confirm that keeps a record's own `junk_value` (naming
    no convention, or naming it again), since a challenge cannot raise that record to the
    `exactMatch` PD7 would make it. The
    challenge prompt takes such a record through all three cases: confirm it as it stands when
    every outside case is trivial, downgrade it to `broadMatch` when one has content, and, when
    the two differ where both are defined, confirm it under the real convention (which replaces
    the record's) or downgrade it. The prompts' closeMatch example ("Lean returns 0 at n = 0")
    is now an exactMatch's note, and so is the gate's hint for a note that admits a gap.
  - **PD8**: `broadMatch` by instantiation only when what is left closes by decidable arithmetic
    (`omega`, `norm_num`, `decide`); a bridge through a named lemma is `relatedMatch`. It is read
    of instantiation, as recorded ("after instantiating"): R2's weakenings, a strict derivative
    for a plain one among them, stay `broadMatch`.
  - **PD9**: in "it holds because X", X is a claim only when it is a declarative mathematical
    statement no neighbouring passage already states.
  - Not made mechanical, because each could misfire: PD8 (a bridge lemma named in a
    `broadMatch`'s `instantiation` cannot be told from the instances and hypotheses that line
    names by right), PD9, PD5 and PD7's "trivial", and a `hasPart` naming every claim outside a
    coverage claim (a weakening credited with its clause, or a coarse claim list).
  - **Config shas move.** The prompt templates (`locate.md.tmpl`, `locate-challenge.md.tmpl`,
    `locate-inventory.md.tmpl`, `locate-worker-system.md.tmpl`) and the staged gate
    (`verify_locate.py`) changed, so `run.yaml` `locate.config_shas.prompts` and
    `config_shas.gate_files` differ for every run planned after this change: such a run is no
    A/A replicate of one planned before it.
  - **The bench gold needs a relabel.** The labeler is now `locate-label/5` (the new rules word
    for word). Each transcribed row records its `labeler_version`, and `cf bench freeze` takes
    the gold's version from the rows, not from the checkout's constant. Rows from before the
    field keep the version their frozen manifest recorded, take this checkout's version when its
    labeler wrote them, and are otherwise refused. A gold whose tiers name two versions is
    refused. `bench.gold.LABELER_RULES` names each version literally, so a later bump of the
    labeler leaves `/5` labelling by rules 2. The labeler's scope definitions are now the
    inventory prompt's word for word: history and applications told about mathematics are
    `expository` in the gold, as they are in the runs it scores, where the labeler had said
    `non_math`. `cf bench score` refuses gold whose labeler labels by other judgment rules than
    any run of the score (inventory, locate, a challenge that required something of the scored
    run, harvested for another run or not), from rules 2 on, on either side; rules 0 and 1 are
    compared as before. **Bench v0's gold must be labelled again under `locate-label/5`** before
    it scores any run planned after this change: gold of `locate-label/4` or earlier is refused
    against such a run.
- The locate gate-log line gains `pre_merge`, true for tend's `--pre-merge` call. Both of the
  log's readers, the watchdog's gate-failure rate and the bench's `gate_rejections`, count the
  validation lines alone (`changed_files: true`, `pre_merge` not true): a contribution that tend
  re-checks in its merge batch was counted as a second attempt, and a batch conflict as a
  worker's rejection. The pre-merge calls are counted apart (the watchdog's `pre_merge_calls`
  and `pre_merge_failed`, the bench's `gate_rejections.pre_merge: {calls, rejected, broken}`),
  and a line from an older gate, which has no `pre_merge`, reads as false. The field changes
  the gate's file sha, so `config_shas.gate_files` differs for a run planned after it.
- Locate B5, after its review (F1, F6): the article's QID now rests on a judged label.
  - **Every subject prominence is challenged.** The article's QID rides on every record of a
    `subject` passage, and the plan challenged only `supporting` prominences, so a passage
    mislabelled `subject` kept its QID with no verdict that could take it away. The plan now
    gives every `subject` passage a prominence record, `refute_by_default` under stratum
    `prominence:subject`, required whatever the rates. An uphold keeps the QID, and an overturn
    to `central` or `supporting` removes it from every record of the passage. That is one more
    required verdict per subject passage, about one per article. An article may still have
    several subject passages, and each one is judged. The challenger's prompt says how to judge
    the record.
  - **No QID on a record nobody judged.** A record whose `challenge` is `challenge_pending`
    carries no `identifiers.wikidata`. It is a location, absence or unresolved outcome that a
    prominence overturn to `subject` reopened, and no challenger judged it under that rule. The
    passage's judged records carry the QID, so the rule is per record
    (`records.article_qid(…, challenge=)`). Smoke 2 is unchanged: all 31 of its QID records
    survived their challenge. `references/locate-documents.md` §6 now also says that the QID
    names the subject end's article, not the declaration. A subject passage's records carry
    every relation, so the Brain's fold reads the relation before it draws an edge.
  - **Planning moves.** A challenge planned after this writes one more `challenge-plan.json`
    record per subject passage. Its `counts.required` rises by that number, and its task
    batches can regroup. Two config shas also change for runs planned after it:
    `config_shas.prompts` for the `locate-challenge` template, and `config_shas.gate_files` for
    every locate preset, since `scripts/locate/records.py` is staged as `locate_records.py` and
    the gate itself changes (below). So no run planned after this is an A/A replicate of one
    planned before it. A plan written before it has no `prominence:subject` record, and its
    records export as they did, apart from the `challenge_pending` rule. Its subject
    prominences are judged by a new challenge run, never by re-planning the old one in place.
  - **A harvested challenge is not re-planned into another plan.** `cf plan-locate` refuses to
    re-plan a challenge run that has a `RECEIPT.json` when the new `challenge-plan.json` would
    change any record's planned value, and names the count newly required. Its verdicts answer
    the plan they were planned under: re-planned in place under this planner (the review's
    probe), a harvested challenge gained the `prominence:subject` records with no verdict, so
    the export refused every subject passage, the ones carrying a QID, and still exited 0. A
    re-plan that keeps every planned value passes as before (`references/locate-documents.md`
    §9).
  - **A prominence overturn names another prominence.** Now that every subject prominence is a
    required record, an `overturn` naming the record's own prominence marked the passage
    `downgraded` while its label and QID stayed. The gate refuses it (rule 1, against the task
    manifest's `prominence`), and the outcomes leave such a record owed, so the export refuses
    the passage. No verdict of smoke 2's or smoke 3's challenge harvests is one: their three
    prominence overturns are `supporting` to `central`.
  - Tests pin `records.SUBJECT` and `records.CHALLENGE_PENDING` to `cflib.vocab`, and
    `records._QID` to `sources.websource._QID`. A source whose `wikidata` is no QID is refused
    on a `challenge_pending` record of a subject passage too, so whether the passage exports
    never turns on its challenge. The proposal's examples carry no real QID: the record
    example's subject end now carries the QID that B5 gives a subject passage, as a
    placeholder, and §2.1's sourceset row names it the same way.
- A universe and its YAML, after the brain4 review (F2, F3, F5). Nothing about a run, a
  gate or the record set's format changes.
  - **One spelling per repository.** `cf corpus add` and loading a universe refuse a library
    whose repo is another library's repository spelled another way: the host's case, a
    `www.`, a trailing `.git` in any case, or on github.com the owner's or name's case
    (`corpus.universe.repo_key`). The refusal names the spelling the universe uses.
    Everything downstream still compares a repo exactly as written, an absence's one end per
    `(repo, commit)` included, and the rule keeps that correct. A second spelling of lean4
    was refused before, wrongly, as a `brain_lib` "naming two repositories". It is now
    refused as a spelling.
  - **`brain_lib` tokens.** The export report lists as `brain_lib_not_token` every library
    whose written `brain_lib` is no `BRAIN_LIB` token, the id fallback included (`lean-core`).
    The set still exports, and u1's `FF.<Root>` names still export. A re-add whose
    `--brain-lib` is no token is refused, even when it is the name the row records; a re-add
    without the flag keeps that name. Tokens are compared without case across repositories:
    `Mathlib` and `MATHLIB` both passed before, and now the second is refused, the id
    fallback included.
  - **YAML look-alikes.** The writer quoted `0x10` and `.inf` but not an exponent, so
    `1.5e-3` reloaded as 0.0015. It now quotes every plain scalar either reader would type,
    in a value or a mapping key: a number of any base or with an exponent, a bool or null
    word in any case, a date or timestamp, and PyYAML's `<<` and `=`, which PyYAML refuses to
    load at all. A float is written as both readers read a float back: a point in the mantissa
    and a sign on the exponent (`1.0e-05`, `1.0e+16`), and `.inf`, `-.inf` and `.nan`.
    `str()` gave `1e-05`, `1e+16` and `inf`, which PyYAML and the fallback reader both read
    back as strings, so a campaign's `--unpriced-usd-per-hour 1e-5` was refused by its own
    render. The fallback reader reads `.inf` and `.nan`, and a sequence item whose first key
    is quoted. A library id and a
    universe id now start with a letter and are none of YAML's bool and null words (`yes`,
    `no`, `true`, `false`, `on`, `off`, `null`), so no id reads as anything but itself
    even written bare (`corpus.universe.LIBRARY_ID`). Loading a universe now checks an
    exclusion's id too, and names an id YAML read as another type (a bare `no` as False).
    No shipped id or fixture id breaks, and neither does u1's.
- The locate gate before Tier A: what it reads is pinned, its end check is the tool's, and its
  log line's label cannot be faked by an agent.
  - **The span end.** Gate rule 2 recomputes an inventory passage's end with the rule
    `passage_ids.py` writes (`locate_text.passage_end`: its `until`, else its paragraph, else
    its quote) and refuses any other: an offset edited by hand, an end past the paragraph or
    into the next kept passage with no `until` that puts it there, or a UTF-16 count on a layer
    with astral-plane characters. `passage_ids.py` now keeps `until` in the document so the
    end can be recomputed (a second run places the same spans). The bench classes the refusal
    as `rule_2:span_end`. The check binds a run whose `gate.json` names `span_end_rule`
    (`cflib.vocab.SPAN_END_RULE`, 1), which `cf plan-locate` writes into every gate.json as it
    writes `judgment_rules`, and no other. The earlier `passage_ids.py` dropped `until` once it
    had placed the end, so an inventory it wrote has no end to recompute: applied to every
    inventory, the check refused 117 passages of a gated smoke inventory when the checkout's
    gate re-gated it (`cf harvest --checkout-gate`), which is how a run planned before the
    staged-gate pins is re-gated at all. A gate.json without the key keeps the bound it was
    gated under (the end inside the layer, after the quote). A locate or challenge document
    carries no span of its own (the gate reads each passage's span from the task manifest), so
    the key binds nothing there, and a locate run planned now over an older inventory
    (`--inventory-run`, which reads its harvest receipt and never re-gates it) plans and gates
    as before.
  - **The label.** The gate's command line refuses, with exit 3 and no log line, `--findings`
    with `--changed-files`, `--pre-merge` without `--changed-files`, an abbreviated flag, a
    repeated `--gate-config` and, in a staged gate directory, any `--gate-config` but its own
    `gate.json` (a hand check of a staged copy runs with that file; the checkout's
    `scripts/gate/` takes any). The bridge's gate entry depends on the gate: a locate agent,
    worker or reviewer (`CF_REVIEWER_TOOLS` too), may run only
    `<gate command> --findings`, never tend's `--changed-files` call; a faithfulness worker
    keeps the bare `verify_findings.py`. Each log line names its `caller` (`validation`,
    `pre_merge`, `findings_check`, `directory`); `pre_merge` and the readers are unchanged.
  - **The pins.** The gate compares `corpus.sqlite` with `gate.json` `corpus_db.sha256` when it
    first opens it, so a doctored database no longer passes a phantom declaration (it stored
    the sha and never compared it); a database rebuilt after planning now needs a re-plan. An
    inventory or locate plan pins each candidates file it read (`candidates_sha256`), and each
    source pin carries `views`, the shas of `formulas.json` and `paragraphs.json` copied from
    `render.json`; the gate and `cf run`'s verify stage hold the files to them. A gate.json
    without the new keys is gated as before (still `cf-gate-locate/1`); `--self-check` compares
    the database and the pinned candidates files.
  - **The harvest's gate.** `cf harvest` re-gates a locate corpus with the run's own staged
    gate (`runs/<id>/tend-root/gate/`, each file checked against `run.yaml`
    `locate.gate_files`) rather than whatever gate the checkout holds, and the receipt's gate
    block names it by hash (`gate_code`); `--checkout-gate` uses the checkout's, and says so.
    The copy runs from its pinned sources only (a `.pyc` planted in its `__pycache__/` never
    runs), and the modules it sets aside are read off its directory. The re-gate also holds
    `gate.local.json` to the staged `gate/gate.json` in every field but the layout's paths,
    its `corpus_db.sha256` to `run.yaml` `locate.universe.corpus_db_sha256`, and every task
    manifest to `runs/<id>/planned.json`, so a repinned config or an emptied `curated` list
    no longer passes. A gate that could not run says what settles it (`--checkout-gate`, or
    the plan's file back, or a re-plan), and the harvest no longer blames the findings.
  - The staged `verify_locate.py`, `locate_text.py`, `passage_ids.py` and the bridge change, so
    `config_shas.gate_files` differs for a run planned after this: an A/A pair is planned on
    one side of it. Its `gate.json` gains `span_end_rule`, which `config_shas` does not hash.
- A campaign's watchdog has a rate for unpriced calls from the first call. Before a shard's
  first priced envelope with a duration, the watchdog had no rate to estimate a call that left
  no priced envelope (a timeout, a signal) at, and read it as $0. `cf campaign plan` records
  `watchdog.unpriced_usd_per_hour`: `--unpriced-usd-per-hour`, kept on a re-plan, else the
  pilot's highest priced dollars per hour among its harvested envelopes that carry
  `duration_ms` (`runs/<pilot>/host/envelopes/`, read by the watchdog's own ledger; measured
  again at every plan, and named in `watchdog.unpriced_usd_per_hour_pilot`). The sequencer
  passes it to every watchdog (`_watchdog_flags`), and `cf campaign render` refuses a campaign
  with neither the flag nor a pilot rate, or with a rate that is not positive and finite. A
  campaign planned before this has no rate, so it is planned again before it is rendered.
  A rate measured on the same pilot is kept when that pilot has no such envelope any more; a
  pilot it was not measured on leaves it null. A kept rate is checked at every plan as the
  flag is, so a hand edit that is not a positive, finite number is refused by name before
  campaign.yaml is rewritten. The watchdog's ledger gains `usd_per_hour`, taken from the
  unrounded rate, so a pilot's exact $20 an hour is recorded as 20.0, not 20.000002; that
  changes the watchdog's file sha, so `config_shas.gate_files` differs for a run planned after
  it.
- `references/runbook-server.md` §9: a kept run's sourceset (Tier A) is created new and filled
  only by `adopt` from the consumer feed's first delivery, never by `cf sourceset create --from
  wpm3`. A source adopted without the producer's `layer_sha256` never gets one (a recorded
  `adopted_from` never changes, and `create --from` copies it), so a seeded source is never held
  to the producer's layer. Adopt's refusal of a layer another renderer recorded, and
  `references/locate-documents.md` §7, still name the `create --from` migration for that case,
  and now say what it cannot do and point to the runbook. The code that lifts this is not built,
  and is due before contributors or runs 2–10 (a TODO in the runbook): `adopt` filling a missing
  `adopted_from.layer_sha256` once, in a new version, when the pin's bytes hash to the recorded
  `raw_sha256`; the delivering pins file's sha recorded beside it; and an immediate comparison
  into `parity_refusals` when a layer already exists.
- The A/A check (the locate bench's acceptance rule, condition 5) also requires that the two
  runs' outcomes are not shown to disagree. The rule alone is one-sided: it only asks that it
  would not accept the replicate over its baseline, so a replicate that came out clearly
  worse, or one that differed on a metric the claim does not name, passed. Now every metric of
  every tier both scores hold is paired cluster by cluster, and the runs disagree when the
  whole 95% interval of the difference lies beyond 0.03, or the cost per article's beyond 20%
  of the baseline's (`conditions.aa.agreement`); a comparison whose replicate disagrees is
  rejected. The burden of proof is on the difference, so this is no equivalence test: each row
  carries a `verdict` (`differs`, `within` when the whole interval lies inside the tolerance,
  or `unresolved`, the new `BENCH_AA_VERDICTS`), and the agreement names the rows `within` and
  `unresolved` and its `reach`, the largest difference the intervals leave possible, which on
  an A/A of a few dozen articles is far past 0.03. A planning refusal names the key, its
  stage, its `cf plan-locate` flag and both values, where it said only `planning differ`.
  `cf bench compare <id> <baseline> --aa <replicate> --claim …`, with no candidate, is the
  check alone: it answers `aa ok` (exit 0) or `aa FAILS` (exit 1), spends no alpha and writes
  nothing (`--json` prints a `cf-bench-aa/1` document); it prints every row with its verdict,
  difference and interval. The challenge seed stays recorded and compared by nothing. The
  synthetic e2e gains the A/A stage: `loc-2`, planned exactly as `loc-1`, answers `aa ok`, and
  `loc-3`, challenged at another `--strong-sample-rate`, is refused naming it.
  `scripts/bench/*.py` changed, so the scorer sha did too: re-score both sides before
  comparing scores written before this.
- Locate judgment, after three reviews of the rules above (gate, prompts and
  plan). Still nothing changes for a run planned before the rules, and the
  record set's format is unchanged.
  - **Every basis.** A `supporting` passage keeps its status while any location
    that sets it on its own stands, so the plan now requires every such strong
    location (`challenge_plan.bases`), not only the best-ranked one. A passage's
    required records always go to one task, and each location record shows the
    passage's other locations (`passage_locations`, read-only) for the FM7 rule.
  - **A query of its own (FM5).** An absence tags every query and gives each claim
    (and a worked example's `general`) a distinct query of its own, a matching,
    so one query tagged with every claim no longer passes; it rejects no
    candidate for stating a part, a case or a generalisation of the passage
    (`cflib.vocab.CANDIDATE_PART_PHRASES`); a challenger's uphold lists its own
    queries, tagged the same way.
  - **A part names what the declaration entails (FM3).** Every `hasPart` and
    `narrowMatch`, on any passage kind, carries `step`; no other relation does,
    and only a `closeMatch` carries a `convention`. A one-line field is one line
    under every line break (`\r` and U+2028 too) and at least three words with a
    letter ("n/a" is no instantiation), and is checked wherever it appears; the
    outcomes drop a field the relation a verdict leaves does not take.
  - **The challenger's claim list (FM1, FM7).** Every verdict on a location, a
    coverage claim, an absence or an unresolved outcome rests on the challenger's
    own claim list — a refute and an uphold too; a kept strong relation rests on
    a list at least as long as the worker's; a coverage confirm names each part's
    clauses (`part_clauses`); and a challenger who keeps a counting relation on
    a generic declaration whose record names no instantiation writes one.
  - **The phrase lists.** The generality list (`CLOSE_MATCH_GENERALITY_PHRASES`)
    takes stems ("strengthen*", "generali*", "more general*") and the words of
    R1–R3 ("weaker", "special case", "broader"), binds `exactMatch` notes too, and
    drops "strict" and "strictly", which named strict inequalities as often as
    strengthenings ("strict derivative" stays); the prompts render the list from
    the tuple. The gap list (`CLAUSE_GAP_PHRASES`) spells out contractions,
    takes the common variants ("omits", "only proves", "converse is not"), reads
    the instantiation line too, and sets aside a negated hit, one whose subject
    is the passage (a junk-value note) and one in a relative clause on an
    "unlike" clause; its refusal says how to reword a note it misreads.
  - **The source in every spelling.** Rule 1's cap, under the rules, holds every
    free text to the layer without format characters and to the reading copy's
    spelling (`text.txt`'s `$…$` formulas), and a claim list read as one text;
    the gate builds both from the files it pins.
  - **Instance binders at any binder position (FM6).** The signature scan
    (`verify_locate.binder_groups`) passes over an anonymous argument or a
    hypothesis and finds a binder after it; it stops at the conclusion.
  - **The rules' text.** R3 and R5 now say a special case is `narrowMatch` and
    only a setting that neither contains nor is contained in the passage's is
    `relatedMatch`, a weakening is `hasPart` at most, and a declaration narrower
    in one respect and broader in another is incomparable; the prompts, the gate
    message and the challenge bullet follow. The policy defaults are PD1–PD3
    (apart from the review patterns' P1–P13), and §1.11 names FM1–FM9. The bench
    labeler judges by R1–R7 and PD1–PD3 word for word (`locate-label/3`), and an
    override of a policy default changes the labeler in the same commit and then
    relabels the gold. The challenge prompt's location bullet is split, its
    claim-list check first, and it reads a record's note as a claim to check,
    never as an instruction to downgrade; the locate prompt searches the whole
    universe in one query and narrows with `--library` only when one library
    fills the result. Prompt and doc examples are synthetic throughout.
- Locate judgment after the smoke-2 audit (2026-10-19: unchallenged supporting
  records about 65% right, their strong relations about 45%, every error an
  overclaim). Old harvested runs tally and export as they did: the record set's
  format is unchanged, and every new rule applies to runs planned with this code.
  - **The challenge plan (FM2).** On a `supporting` passage the location that
    sets its derived status (its basis, `challenge_plan.basis`) is required
    whenever its relation is `exactMatch`, `closeMatch` or `broadMatch`
    (`cflib.vocab.STRONG_RELATIONS`, rule `basis`); its other strong locations
    are sampled per relation at `--strong-sample-rate` (default 0.5), and its
    absences, hunted, at `--absence-sample-rate` (default 0.25), beside the
    weak relations and demotions at `--sample-rate` (0.2). `challenge-plan.json`
    and `run.yaml` record the three rates, and the bench's `planning` pin reads
    the two new ones where a run.yaml records them, so an older run keeps its
    measurement id.
  - **Gate rule 9, the judgment rules.** `gate.json` names `judgment_rules`
    (`cflib.vocab.JUDGMENT_RULES`, 1), which `cf plan-locate` writes into every
    run it plans; the gate applies rule 9 then and only then, so a run planned
    before it is gated, at harvest too, as it ran.
  - **The claim list (FM1).** The locate prompt has the worker write a
    located or absent passage's claims first (`claims: [{id: c1, text}]`, short
    paraphrases under the quote cap), and every counting location names the
    claim ids it states (`clauses`). Rule 9 refuses a strong relation that
    leaves a claim out, a strong one whose note admits a gap (the closed
    `cflib.vocab.CLAUSE_GAP_PHRASES`), a `relatedMatch` that names clauses and a
    coverage claim whose parts leave a claim unstated; the challenge plan
    refutes a gap-admitting strong record by default in any run. A challenge
    document carries the challenger's own claim lists, which its confirms,
    downgrades and overturn locations name. The outcomes keep `claims`,
    `clauses`, `challenge_claims` and `challenge_clauses` where a document wrote
    them, and a challenge manifest shows the worker's to the challenger with the
    location's note; nothing new is exported.
  - **A query for every clause (FM5).** The prompt asks for at least one query
    per claim, tagged in the receipt's `query_clauses` with the claim ids it
    targets, a worked example's general theorem (tagged `general`) and every
    library; rule 9 refuses an absence that leaves a claim without a query, or
    an example's with no `general` query. The tags are not exported: the
    receipt sidecar is built from `queries` and `candidates` alone.
  - **The instantiation line (FM6).** The prompt has the worker instantiate a
    declaration at the passage's objects, name the instance or hypothesis that
    makes it apply and check it exists at the pin, and call a declaration whose
    hypotheses the objects cannot meet `relatedMatch`. Rule 9 reads the
    declaration's signature from `corpus.sqlite` (`decls.signature`), takes its
    leading binders (`verify_locate.instance_binders`) and, when one is
    instance-implicit, requires the counting location's `instantiation` line; a
    declaration with no recorded signature is not held to it.
  - **A part is what the declaration alone entails (FM3).** The prompts say
    `hasPart` and `narrowMatch` need the declaration alone, instantiated, to
    entail a clause of this passage, and call a proof ingredient, a bridge
    lemma or a neighbouring passage's fact `relatedMatch`. Rule 9 has a
    `hasPart` on a proof or remark passage (`passage_kind` `other`) name the
    displayed step it states (`step`), and a challenge verdict that leaves one
    there name it too; the outcomes keep the step, a confirm's or an applied
    downgrade's replacing the worker's.
  - **A closeMatch names its convention (FM4).** A `closeMatch` location names
    one of `cflib.vocab.CLOSE_MATCH_CONVENTIONS` (notation, encoding,
    junk_value, normalisation, renaming, unfolding, bundling) as its
    `convention`, and rule 9 refuses one without, or whose note says
    "stronger", "strict" or "more general" (`CLOSE_MATCH_GENERALITY_PHRASES`);
    a challenge verdict that leaves a `closeMatch` names its convention too, and
    a downgrade's own note is held to the same words. Rule 9 reads a
    challenger's own words only: a record's note is its worker's, which the plan
    refutes by default when it admits a gap and which never forces a verdict.
  - **Rules R1–R7, the policy defaults and the challenger's rule.**
    `references/vocabulary.md` §1.11 states the audit resolver's relation
    rules R1–R7, the maintainer's overridable policy defaults (P1: a definition
    over a general type parameter where the passage has a specific one is
    `broadMatch`; P2: a statement over ℕ for one over ℤ is `narrowMatch`; P3: a
    trivial or degenerate special case is `narrowMatch`, never vacuous) and the
    FM7 challenger rule (check against your claim list before a downgrade or
    refute, apply D1, keep definitionally linked declarations at one relation
    within a task). Both prompts carry the rules word for word, the challenge
    prompt the FM7 rule too, and `tests/test_locate_judgment.py` holds them
    together.
- An absence record names each repository the universe searched once, at its
  commit: two universe libraries split from one repository at one commit
  (lean-core's `Init` and `Std`, proposal §2.3) gave two identical lean ends,
  and the record set's consumer checks the ends against the universe's distinct
  `(repo, commit)` pairs, each once. It agreed (2026-10-19) to admit several
  libraries per repository, so a universe may split lean-core by root. A
  universe whose repositories are all distinct writes the same ends as before.
- A `subject` passage's records carry the article's QID, the source's
  `wikidata`, as the subject end's `identifiers.wikidata`; no other passage's
  records carry one (the maintainer's decision B5, proposal §2.2 and §11.3).
  No record carried a QID before, so a consumer could draw no concept-level
  edge from a record set at all, and the article's QID on every passage welds
  the wrong concepts. The skill does not check the QID's enwiki sitelink (no
  network in library code); the consumer does before any concept-level edge.
  A source whose `wikidata` is no QID is refused per subject passage.
- A locate record's subject end carries the passage's whole `span` (its
  half-open code-point offsets on the `web-html/3` layer, never text) and the
  `layer_sha256` of that layer, the source's own in `sources.jsonl`, beside
  `span_sha256`, so a consumer holding the layer highlights the whole passage
  and not only the capped quote. The record set's consumer agreed the shape
  (both optional to it, together or not at all); the export writes both on
  every record, and the gate's rule-1 preview builds them from `gate.json`'s
  pin. A span that is not two integers `0 <= start < end`, or that leaves the
  layer, is refused by name (`records.passage_span`). `records.py` is staged
  in the gate directory, so `locate.config_shas.gate_files` changes: a run
  planned before this is no A/A replicate of one planned after.
- `cf corpus add` holds a new library's `brain_lib` to the consumer's library
  token grammar, a letter, then letters, digits and `_`, at most 64
  (`corpus.universe.BRAIN_LIB`): u1's `FF.<Root>` passed (any name of at most
  300 characters did) and the consumer's Brain refuses it. A token outside it
  is refused naming `--brain-lib` and a spelling it admits (`FF_<Root>`), and
  an id that is no token (`lean-core`) needs `--brain-lib`. A universe written
  before still loads, re-adds and exports as it stands.
- Locate after a four-lens review of the integrated tree (contract, gate
  adversary, measurement, ops), its fixes ported onto this line, each verified
  by reproducing the original failure first.
  - **Gate.** Rule 1 holds a worker's free text (location notes, absence
    queries, candidate reasons, challenge notes) to the export's verbatim-source
    cap, the note a downgrade to `closeMatch` exports (its `note`, else a
    witness sentence) included: the note helpers moved from `locate.outcomes`
    into the staged `locate.records`, so the gate computes the outcomes' note. Rule 7 reads the task file: `tasks/<id>.yaml` only, never added,
    changed in its `status:` line alone, which holds one status word (a flow
    mapping such as `status: {id: …, priority: 99}`, an unparseable value or a
    YAML tag on that line passed before); a worktree's change set is one task's,
    and tend's pre-merge command carries the gate's new `--pre-merge`, the only
    call that admits several tasks. Rule 6's duplicate check is linear and a
    call keeps at most 200 problem lines. Rule 8 refuses a skip that holds a
    kept passage's whole span, and the inventory receipt's gate block counts
    what skips removed (`skipped`), which `cf tally --locate` prints.
    `unresolved.by: attempt_cap` is accepted only in the bridge's form with its
    counter at the cap. The corpus host tools take `--db` once, refuse
    abbreviations, and in a gate directory read only the database `gate.json`
    pins. At the cap the bridge puts every other path back as it is at the
    merge base (`CF_MERGE_TARGET`), and only a rejection the reviewer stated
    counts toward it. The gate log is unchanged; its readers (the watchdog and
    the bench) count only `changed_files: true` lines.
  - **Planning and transfer.** `run.yaml` records `locate.config_shas` (the
    prompt templates, the run-independent staged files and what the agent
    wrappers run with: agent host, model, sandbox bypass, agentbox profile,
    attempt cap and merge target; equal across run ids). A planted candidates
    file checks its curated joins in the full `corpus.sqlite` and withholds
    every hit its own database lacks. A `locate-challenge` plan refuses a tools
    database other than its challenged runs'. A re-plan keeps
    `locate.campaign`. A docstring URL joins a title with a `?`, `#`, `!` or
    quote mark. `cf run` sends the blind arm's candidates,
    checks every source layer on the host against `gate.json` at verify, names
    the sources it expects on the host when sent without
    `--include-source-text`, and its `hosts.<host>.tend_version` check now runs.
  - **Bench.** An A/A replicate is compared on `locate.config_shas`, not on the
    bytes a plan renders per run, so `cf bench compare` can accept, and its
    `agents` is a pin of its own (so every measurement has a new
    `measurement_id`, and a score from before is refused as another scorer's:
    re-score both sides; the A/A check refuses a run that records no `agents`:
    re-plan it), and so is `planning`, each run's `run.yaml` `locate.{per_task,
    subject_only, sample_rate}`, which the template shas cannot see (a replicate
    planned with `--per-task 8` of a baseline planned with `--per-task 1` was
    accepted); every plan records those keys, so this needs a re-score, not a
    re-plan. The challenge seed is a recorded pin only (`challenge_seed`): it picks one draw at
    the sample rate, and another seed's draw is the sampling variance an A/A
    replicate measures; a score
    refuses outcome rows tallied before the last harvest (the tally records each
    `receipt_sha256`), and so refuses any tally that records none -- every tally
    written before this change, even one written after its run's last harvest:
    re-run `cf tally --locate` before scoring; P is stratified by seen; strata
    count their units for the n >= 30 rule; two cost bases fail the cost
    condition; a host canary scan must name the bench's canary
    (`canary_sha256`); a measurement scored two ways writes two files
    (`<split>[-subset]-<id16>-<s8>`); an unanchored G1 miss on an unresolved or
    gate-failed article is E-UNRESOLVED; a planted score refuses a challenge of
    another tools database.
  - **Campaigns.** A restarted shard keeps the transcript baseline it first
    took and settles what its earlier attempt left before anything is
    relaunched; a canary report with a hit is never replaced; a stopped shard is
    never relaunched as it stands (only `CF_ACCEPT=<run-id>` passes a canary
    hit, a higher cap only a stop at the cap); a transcript is a shard's only if
    it names the shard's root, entrypoint or gate directory; `~/` in a host path
    is the host's home; a killed sequencer's watchdog leaves within seconds of
    its tend run and is stopped before its shard relaunches; `cf campaign
    render` refuses a shard re-planned since the plan and its transfer creates
    `<remote root>/campaigns/<id>`; `probe_host.sh` needs only bash 4.3.
  - **Sources.** `cf sourceset adopt` reads a producer's optional `fetch` and
    `layer_sha256` per pin (on the existing pin grammar) and records the
    producer's sha in `adopted_from`. It now refuses pins files it accepted
    before, each refusal refusing the whole file (nothing is adopted, exit 2): a
    field it does not know, `bytes` outside the pins file's directory, a `fetch`
    or `layer_sha256` in a row with no `bytes`, a `wikidata` that is no QID
    (`""` included, which was read as null), and two rows that pin one source
    and disagree. `cf sourceset render` refuses, by name, a layer that is not the
    producer's, and the refusal stands in `state.json` `parity_refusals`, which
    `cf locate candidates` (refusing a `--source` that names one, reporting
    one apart from the sources not rendered yet otherwise), `cf plan-locate`
    (`locate.skipped`: `parity refused`) and `cf bench build` (its frame line
    included) name as such, with the corrected delivery in a new sourceset as
    the step that settles it. A source recorded
    under another renderer is refused by candidates, the planner and the export.
    `tests/data/locate/text-layer.json`, generated by the consumer's own
    `text_layer`, pins `web-html/3`'s layer to it (recorded divergences
    included) and replaces the env-gated `CF_WIKILEAN_LAYER` test. The
    synthetic e2e builds its sourceset by adopt. `web-html/3`'s raw-text sets
    are the renderer's own, not the interpreter's: CPython 3.14.0 reads only
    `script` and `style` as raw text where 3.14.5 adds `xmp`, `iframe`,
    `noembed` and `noframes` (and reads `plaintext` to the end), so one body
    gave two layers under one renderer id. The tree builder now pins the six
    raw-text and two escapable elements of its docstring, reads `plaintext` and
    `noscript` as ordinary elements, and refuses, naming the interpreter, an
    `html.parser` with no escapable raw-text mode. The rule is the one the id
    already stated, so the id is unchanged.
- The derived status follows the maintainer's decision D1 (2026-10-18): a theorem
  passage's `exactMatch` or `closeMatch` to data — an equivalence or other
  definition without the `statement` flag that states the theorem, such as a
  theorem that two quotient rings are isomorphic, as a `RingEquiv` — is `formalized` (it was
  `partial`), and a located passage may carry a **coverage claim**, its `hasPart`
  locations together stating every clause, which makes it `formalized` (or
  `statement_only`) once a challenger confirms it. Every claim is challenged
  whatever the passage's prominence (target `coverage`, `confirm` or `refute`),
  the gate refuses a malformed one, and an exported record carries a standing
  claim as `extensions[‹locate›].coverage`. `vocab.derive_status` takes the claim
  as an optional fourth argument; `tests/data/locate/status-parity.json` is the
  parity table a consumer's own derivation runs.
- `vocab.derive_status` takes the coverage claim exactly as a record carries it
  (`COVERAGE_RECORD_KEYS`: `complete`, `parts`, `challenge`) and refuses what no
  record says, as the consumer's derivation does: a claim with no challenge value
  (an owed claim, which the outcomes now pass as no claim — the same status), a
  claim with any other key (its note), and one `location_key` on two locations.
  The outcomes derive the status from the export's own projection
  (`records.coverage_ext`), and the export refuses a claim whose challenge is
  none of the record values. Parity case C14 is refused (round 1 expected
  `partial`) and X11–X16 are new.
- Parity round 3 settles the reading of D1 and resolves the open questions
  Q1–Q3 of `references/vocabulary.md` §1.11: a survived coverage claim is one
  more `exactMatch` location, judged by the passage's row like any other —
  `proved` to the theorem row and a `definition` to the definition row when
  every part is finished (`proved`, or data), `statement_only` on both rows when
  any part is unfinished (`statement_only`, `wanted`, `axiom_dependent`, or a
  `Prop`-valued definition). An `example` or `other` passage now takes the
  better of the two rows for its claim too (Q1), a data part counts as finished
  and a `Prop`-valued definition part does not (Q2), and a definition passage's
  claim with an unfinished part is `statement_only` (Q3); rounds 1 and 2 left
  each of these `partial`, and D04 moves from `formalized` to `statement_only`.
  The claim is a fixed stand-in, not an `exactMatch` to its least finished
  part: read that way, a definition passage's claim of `proved` parts would be
  `partial` and one with a `Prop`-valued definition part `formalized`, where the
  stand-in makes them `formalized` and `statement_only` (the docs had called it
  the "weakest" part's; the code never did).
  A confirmed claim also confirms its parts: a part whose own record is
  `not_required` or `sampled_out` is exported `survived`
  (`outcomes.confirm_parts`), a part a verdict judged keeps that verdict, and a
  refuted claim confirms nothing. A prominence overturn to `subject` or
  `central` in the same run wins over the confirmation: the outcomes reopen the
  passage first and confirm the parts after, so a part the reopen made
  `challenge_pending` stays pending, owed its own verdict under the stricter
  rule, while the claim stays `survived` and the status is unchanged (the parts
  had been confirmed before the reopen ran and exported `survived`, where the
  consumer kept them pending). The challenger is told the same: a confirm
  stands in only for the part records the plan did not require. The parity
  table is 118 cases, and the cases that carry `expected_challenges` pin each
  part's exported challenge, P05 a reopened part's.
- A record set's `set_id` and `sourceset.id` are tokens of the consumer's grammar
  (`cflib.vocab.RECORDSET_TOKEN`: letters, digits and `._:-`, at most 128): the
  export refuses any other before a file is written, and `cf sourceset create`
  refuses a new sourceset id outside it, where it is chosen (a `--set-id smoke+2`
  or a sourceset `wpm+math` had exported cleanly and been refused whole by the
  consumer).
- The export refuses a universe with a library that records no licence, naming
  it and the `cf corpus add … --licence` that settles it (`universe.json` had
  carried `licence: null`, which the consumer refuses), and a re-add with
  `--licence` now records a missing licence (it exited 0 and changed nothing); a
  re-add naming another licence or `brain_lib` than recorded is refused.
- `agent_call.py` keeps a stdout that is JSON but no object (an array) as `.out`
  beside its `.json` record; it had been kept as `.json` and then replaced by the
  record written under the same name in the same second.
- `cf bench score` refuses a receipt whose `cost.usd` is a lower bound (a call
  that left no priced envelope, e.g. a timeout; or a model in no price table)
  unless `--article-costs` prices every article: an envelope-priced run whose only
  call timed out had read as $0.00. `cf harvest` warns `NO RESPONSES priced` and
  `LOWER BOUND` on the receipt's own cost block, envelopes included.
- A location's lean end always names `module`, `path` and `line` (only
  `end_line` is optional): a location joined to an index row without one is
  refused per passage, by name, where it had been exported without a path and
  the consumer refused the whole set.
- An absence receipt's `queries` and `candidates` counts are the lengths of the
  sidecar's lists: an exactly repeated query is listed once (the record had said
  `queries: 3` beside a sidecar listing 4, and the consumer refused the set).
- `universe.json` writes a library's `display_name` as its `brain_lib` only when
  that is a display name the consumer shows (printable ASCII, `,.()/+:'&_-·`, at
  most 100), and null otherwise: a `brain_lib` such as `Beta!` had made the
  consumer refuse the whole set.
- `cf corpus add` (and loading a universe) refuses a `brain_lib`, `toolchain` or
  root over 300 characters and an exclusion reason over 1000, the caps the record
  set's consumer applies to `universe.json`, with the value's length in the message.
- A library's `repo` follows the record set consumer's repository grammar (a
  dotted host, one to four path segments of `[A-Za-z0-9_.~-]`, no `.`, `..`, empty
  segment or trailing slash), refused at `cf corpus add` and when a universe is
  loaded: `https://example.org/beta/` had been pinned, exported and refused whole
  by the consumer, and a pinned repo cannot be changed by a re-add.
- `cf plan-locate` refuses a `--run-id` or `--challenge-of` id the export's run
  token cannot spell (`cflib.vocab.RUN_TOKEN_ID`: letters, digits and `._-`, at
  most 64), and `cf campaign plan` a campaign id over 128 characters: a run
  planned as `loc+2` ran to completion and could then never be exported outside
  a campaign, nor renamed.
- A remote locate harvest brings the bridge's kept agent envelopes home
  (`runs/<run>/host/envelopes/`) and, with no `--transcripts` and no campaign
  usage export, prices the receipt from them: their own `total_cost_usd`,
  tokens by model from `modelUsage`, the envelope count, `basis: envelopes`
  (the second smoke's receipts all had `cost: null`). `cf tally --locate`
  prints each run's cost line and records it in the tally report.
- Every locate gate-log line records the call's `ownership` line,
  `task_id_from` (`--task-id`, `$TEND_TASK_ID` or `orchestrator.sqlite`) and
  `changed_files`, and the ownership line says where the id came from: an
  agent's own calls carry `TEND_TASK_ID`, so a logged task id was no proof the
  validation lookup worked. `references/runbook-server.md` §4 step 5 says
  which record to read.
- `agent_call.py` keeps a record in `usage/` for a call that left no envelope
  (a timeout, a signal, an empty or non-JSON stdout: `total_cost_usd: null`,
  its elapsed time), and the spend watchdog estimates each at the root's
  highest dollars-per-second (or `--unpriced-usd-per-hour`), so a timed-out
  call is no longer spend it never saw. `spend_usd` is the floor plus that
  estimate; `spend_floor_usd` is the priced floor alone.
- After a challenge, a passage's locations are ranked 1..n again (a refute had
  left ranks `[1, 3]`), and the locations an absence overturn adds carry
  `challenge: survived`, not `downgraded`.
- A challenge's prominence overturn to `subject` or `central` reopens the
  passage: each of its records no verdict judged becomes `challenge_pending`
  (a new `CHALLENGE_VERDICTS` value, exported, read as unchallenged) instead of
  keeping `not_required`; the second smoke had exported two central absences
  nobody hunted.
- `cf harvest` fetches a locate run's host artifacts by asking the host
  whether each is there (`ssh test -f`) and copying it with a plain `rsync -a`:
  `--ignore-missing-args` is rsync 3.1's, macOS's 2.6.9 rejected it, and the
  gate log never came home while the harvest said COMPLETE. The gate log is
  now an expected artifact: a harvest without it is INCOMPLETE and exits 1
  (`host_artifacts_missing` in the receipt).
- The sealed record set is the shape its consumer reads
  (`references/locate-documents.md` §6): `SEAL.json` carries exactly `schema`,
  `set_id`, a `campaign` that is never null (`run:<run>[+<challenge run>]`
  outside a campaign, refused with two challenge runs or an id the token
  grammar does not admit), `universe: {id, universe_sha256}`, `sourceset: {id,
  manifest_sha256}`, `skill_commit`, `created`, the `byte_budget` applied and
  `files` (the runs and the manifest version moved to the export report: the
  consumer refuses any other seal key);
  `universe.json` lists its libraries (each with `roots` and `display_name`)
  under `universe_sha256` with `frozen`; every record carries its passage's
  `section` and `prominence`; a `closeMatch` a challenge produced keeps a note
  (the challenger's, else the first sentence of its witness that quotes the
  article within the quote cap: the re-export of the second smoke had refused
  FTC #19a718e1, whose witness opened by quoting 19 words of the article);
  an absence's lean ends are `{type, uri, version}` only; and no per-source
  byte budget applies unless `--byte-budget` asks for one (it had refused 4 of
  10 sources of the second smoke at 256 KiB).
- `corpus_search.py --library L` (and `cf corpus search --library`, L2's
  lexical retrieval) reads the FTS5 index first and filters the library on the
  rows it found: SQLite 3.43's planner had walked every row of the library
  through `decls`' index and re-run the match per row, about 90 s a search on
  the field universe against 0.05 s unfiltered. No schema change; existing
  databases are read as they are.
- The renderer is **`web-html/3`** (`references/locate-documents.md` §7). The
  layer, head text, reading copy and sections are `/2`'s character for
  character, so no passage id moves. The paragraph-label rule no longer takes a
  named concept or statement for a label ("property (T)", "Axiom A",
  "Theorem (Widget lemma)." are `bold_definition` terms; `render.json` counts
  what the rule still drops as `bold_labels_dropped`), and `render.json` records
  every view's sha under `views`, which `cf sourceset status --verify` and the
  planner check. `cf sourceset render` only completes a recorded rendering: a
  missing view is restored, a differing one refused by name. A `/2` rendering
  moves by `create --from` and `render`, as a `/1` one does. The label rule's
  fingerprint is pinned to the renderer id.
- The worker's `corpus_search.py` and L2's lexical retrieval rank the
  declaration a query names as an identifier first (the name is the query; its
  words are; its last components' are), then by `bm25`: on the field universe
  the query `IsCompact` had put `IsCompact` about 452nd.
- `cf plan-locate --preset locate-inventory` plans no page that is no concept
  article (a redirect, disambiguation page, list or biography) unless named by
  `--source` or `--include-flagged`, and a plain `cf sourceset fetch` reads a
  null flag off the entry's assessment class (`websource.effective_flags`), so
  a sourceset seeded from one enumerated before the flags existed no longer
  fetches and plans its stubs.
- `cf sourceset create --from` builds the new sourceset in a staging directory
  and renames it into place, so an interrupted seeding is re-run as it was;
  `cf sourceset adopt` resolves a pinned revision the sourceset holds without a
  request; `cf bench build` refuses a drawn article of another renderer.
- The locate gate reads `orchestrator.sqlite` immutably only where that read is
  complete (no `-wal`) or the only one possible (an unwritable directory, said
  on the line), and the single-stem ownership line says why no task id was found.
- `cflib.vocab` defines the tools-database kinds (`TOOLS_DB_KINDS`) and the
  bench tier orders (`BENCH_TIER_ORDER`, `BENCH_LABELLED_TIERS`,
  `BENCH_CLAIM_TIERS`), which three modules had defined for themselves.
- One relation order for a challenge's `downgrade`, `cflib.vocab.RELATION_STRENGTH`
  (`exactMatch` > `closeMatch` > `broadMatch` > `narrowMatch` = `hasPart` >
  `relatedMatch`, the order `references/locate-documents.md` §4 states). The
  outcomes used to rank `broadMatch` level with `narrowMatch` and `hasPart`, so a
  broad-to-narrow downgrade the gate passed was ignored and its record owed
  again. The gate keeps its inlined copy, now pinned by a test. The locate
  prompts state rule 4 as the gate checks it: a curated hit is answered by a
  location whose relation counts, or by a reasoned rejection, and an `accepted`
  candidate must be a location. `cf tally` records in a companion's
  `state.json` only the runs it counted, never a locate run it left out. The
  bench transcriber names a quote of the wrong shape instead of reporting it
  as no quote. The synthetic locate end-to-end test
  (`tests/e2e/locate_synthetic/`) runs every stage in the suite.
- The locate planner meets the other locate packages through their owners
  (`references/locate-documents.md` §5, §6, §9, §10): the sourceset through
  `sources.sourceset.Sourceset` (one source per page, `Manifest.preferred()`;
  a source whose files moved is refused at plan time; a missing view is never
  read as empty), the views through `sources.render_html`, the universe through
  `corpus.universe.Universe`, the database through `corpus.query`, accepted by
  the universe sha it was built for, never its bytes, and the state file
  through `Sourceset.record_state`. A run names two databases: the full
  `corpus.sqlite` the gate checks, and the tools database its workers search
  — `--blind` the G1-blind build, `cf plan-locate`/`cf locate candidates
  --tools-corpus-db` a planted one — with `--db` inside the allowlisted prefix
  and one candidates directory per kind. The staged gate directory and `bin/`
  are whole (`verify_findings.py`, both corpus tools, `agent_call.py`,
  `attempt_cap.py` for every run, `watchdog.py`), a missing file is a planning
  error, and `run.yaml` pins `skill_commit`, every staged file's sha, the
  tools database and the arm (a plan of another arm than its inventory or
  challenged runs is refused); `budget.max_cost` comes only from `--max-cost`.
  Task manifests carry each source's curated hits once (`curated`) and where to
  read each passage (`text_span`, `section`, `quote`), and are kept in
  `runs/<run>/planned.json`. Candidates join titles only on the title, by base
  QID for suffixed `1000.yaml` keys, never across another QID. The outcomes
  apply a challenge only to the run and records it planned, count a refuted
  location as judged, accept only a weaker downgrade, re-rank an overturn and
  stamp every row with its document's shas; the export refuses a closed
  licence class, problem rows and source text past the cap in receipts, hashes
  each receipt as written, and never writes into the checkout, a companion or a
  directory that is not a record set.
- The locate gate, harvest, `cf run` and campaigns after the wave-2 seam and
  package reviews (`references/locate-documents.md` §1-§4 and §9-§11). The
  gate builds rule 1's records with the staged `locate_records.py`, takes rule
  4's hits from the task manifest's `curated` list, turns a missing sibling
  module into exit 3, opens SQLite through quoted read-only URIs, reads a
  document only from a regular file (a symlink is refused), refuses a hostile
  shape (a list where a word belongs, deep nesting, an overflowing number, a
  lone surrogate) as rule 1 instead of a traceback, and requires the file name
  to be the `source_id`, a document for every source of the task, one location
  per declaration, a downgrade weaker than the record's relation and, for a
  curated hit, a location whose relation counts or a reasoned rejection (an
  `accepted` candidate is a location; a `relatedMatch` discharges nothing); a
  malformed `gate.json` value is exit 3. The bridges' fallback gates only the
  task's own documents and the reviewer may run the gate; `agent_call.py`
  forwards TERM/INT/HUP to the agent's group before freeing its slot. A remote
  `cf harvest` of a locate run brings the host's gate log, a campaign shard's
  usage export and canary scan to `runs/<run>/host/`, prices from that export,
  records the run in the sourceset's `state.json`, writes no gate-log line of
  its own, refuses JSON off the `<task-id>/<source-id>.json` layout, lands no
  symlink and fails `--verify-sha` closed; status, monitor and tally count
  nested corpora, and the faithfulness tally leaves locate corpora out.
  `cf run` refuses a locate run planned for `local`, sends the tools database
  and only this run's candidates files but no inventory, keeps one spend cap,
  records `versions: {tend, agentbox, agent_cli}` and the source layers it
  sent. `cf campaign` stamps `locate.campaign` into its shards, needs a
  smoke-tested `--pilot` and `cf run <shard> --stage verify` instead of a smoke,
  quotes every rendered path, and its sequencer holds one lock, refuses a root
  with a live tend run, keys its state by run id, scans (new
  `scripts/run/canary_scan.py`), exports and prunes only the transcripts each
  shard wrote, and stops on any reason to drain a drain did not act on.
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
- Locate L1, after the first run against real pages
  (`references/locate-documents.md` §5, §7). The renderer is `web-html/2`: the
  layer is the Literature Links text layer — no `style`, `script` or `template`
  text, every `White_Space` run one space — so it equals the consumer's on real
  pages; a self-closing MathML or SVG element holds nothing; a `{{math}}`
  (`texhtml`) formula makes a `math` block; a bold that only labels its
  paragraph ("Proof.", `cflib.vocab.BOLD_LABELS`) or is a symbol makes no
  `bold_definition`; `layer_map.json` names its renderer and a golden test pins
  every view to the id. A `/1` sourceset is re-rendered through `cf sourceset
  create <new> --from <old>`. Manifest versions are snapshots or deltas
  (`cf-sourceset-delta/1`). `fetch --source/--select` fetches a pilot;
  `adopt` takes pins without bytes and title pins (their page resolved from
  the revision under `--execute`), and an adopted revision takes its page's
  assessment and QID. Entries carry page `flags` (redirect, disambiguation,
  list, and biography from Wikidata through `cf sourceset flag-biographies`);
  the enumeration counts the rows that are no article, follows page-info
  continuations and resumes from a checkpoint. `state.json` counts pages and
  keeps a run history, `cf sourceset status` writes only with `--verify`, and
  `cf status` lists sourcesets. L2 queries only bold terms that are terms, and
  the inventory prompt keeps one passage where finders share a start.
- `cf bench` reads every other package's artifact through its owner
  (`references/locate-bench.md` §4, §8, §10). `anchor` and `freeze --plant`
  open `corpus.sqlite` through `corpus.query` and refuse one that does not
  describe the universe as it is now; gold stamps the recomputed universe sha
  and refuses a new state under existing labels without `--supersede`; the
  curated library is `Universe.mathlib_library()`; 100.yaml joins by its QID
  and by `locate.candidates.normalise_title`, as L2 does; a deprecated alias
  also accepts its new name (`deprecated_to`). `score` refuses an ungated
  receipt, a run of another universe state, mixed arms across the inventory,
  locate and challenge runs, a subject-only run, and a `formal_math` passage
  with no outcome row (a `--source` or shard run, never scored as gate
  failures); it pins each run's own `skill_commit` and `gate_files`, the tools
  database and the tally's challenge runs, reports the scorer's code sha apart
  as `scorer`, counts `coverage.missing_rows` and `challenge_owed`, spreads
  each run's dollars over its own `source_ids` (`cost.by_stage`, `runs`,
  `locate_and_challenge`), scores a run whose tools database is `planted` on
  tier P alone, and reads gate-log reasons by `by_rule` and `[rule N]` per run
  from the harvested `runs/<run>/host/gate.jsonl` (worker calls only, exit 3
  apart) and the canary from `runs/<run>/host/canary.json`. `compare` refuses
  two scorers, two tools-database kinds or two challenge states, and judges
  cost on locate and challenge spend when the inventory is shared. The bench
  fixtures are built by `Sourceset`, `render_html`, `Universe` and
  `build_db`, in the harvested layout `corpus/<run>/<task>/<source>.json`.
  Gold is tighter too: a gold span holds at least four whole words, lettered
  1000.yaml keys join their base QID, `to_additive` twins are the whole image
  and renames widen a surviving alias, nested blocks are one span unit, a
  partial transcription keeps every earlier row and never mixes labelers (the
  manifest names the labeler from the rows; labeler `locate-label/2`), h is
  tag-blind end to end and over every anchored passage, GX needs both
  enumerators, a refused build or `freeze --plant` writes nothing, and the
  frozen P tier and canary are verified. The score refuses a canary check
  that read nothing and costs that are not dollars, pins the runs' own
  renderer, scores E-MEMORY wherever a removed declaration is cited, counts a
  missing locate result as unresolved, and stratifies every metric. `compare`
  requires a distinct A/A replicate of the baseline's configuration, a
  replication of another bench's accepted claim (`--replication
  BENCH:ENTRY`; `--ledger` is gone), a resolvable alpha in (0, 0.5), and
  holds the ledger under a lock; a missing baseline safety rate or a
  zero-cost baseline is no longer a pass.

### Added
- The statement cut's *continued* extent, as a library only (`docs/proposals/statement-cut.md`
  §3.1; inv-pilot-1's first fault). The renderer puts the words that finish a sentence after its
  display in a paragraph of their own, and `Blocks.chain` stopped at the display, so the end rule
  and gate rule 10 (b) cut "…such that [display] for all x." after the display.
  `scripts/gate/locate_text.py` now defines `Blocks.ends_with_display`, `Blocks.continues` (the
  first word goes on with the sentence: lower case and two letters or more, "a", or an
  abbreviation; never a lone letter, an item label such as "(ii)", a capital, a digit, a footnote
  bracket or a formula), `Blocks.continuation` (a plain `p` after a `p`, `dd` or display that
  ends with a display and no full stop; never out of a quotation, a theorem box or a list item),
  and a `continued` flag on `Blocks.chain`, `Blocks.extent` and `extent` that joins it, with the
  displays it introduces in turn. `Blocks.cut_points` lists the connectives of `CUT_CONNECTIVES`
  strictly inside a span, an upper bound on where the rules cut, and each is one
  `separator_start` sets aside. Nothing reads any of it: `continued` defaults to off,
  `SPAN_END_RULES` is still `(1, 2)`, and every end, gate verdict and receipt is the one it was.
  Span-end rule 3 and statement-cut rule 2 will read them.
- The continued cut's versions, known to the host before the planner writes them (inv-pilot-1's
  fixes; `docs/proposals/statement-cut.md` §2.2, §3.1–§3.3). **Span-end rule 3**
  (`cflib.vocab.SPAN_END_CONTINUED`) is rule 2 with the continued extent: a passage with no
  `until` runs on over the paragraph that finishes its sentence after its display.
  `locate_text.SPAN_END_RULES` is `(1, 2, 3)`, and `passage_ids.py` ends a passage by it when the
  gate.json beside it names 3. **Statement-cut rule 2** (`STATEMENT_CUT_CONTINUED`) holds rule 10
  (b) to a loose continued bound (`Blocks.runs_on`, the maintainer's reading K1): a sentence may
  run on into the paragraph that finishes it whether or not the gate sees the display between,
  which admits only a longer `until`. It adds (h), which refuses two or more `formal_math`
  passages of a source marked `prominence: subject` and names them all without choosing one
  (reading K3). The receipt's `cut` block gains `subjects`, `continued`, `continuation_starts`,
  `continuations_unheld`, `connective_points`, `formal_with_connective`, `connective_starts`
  (mid-sentence starts only, reading 7) and `by_connective`
  (`harvest.gate_corpus.CUT_COUNTS_2`, `cut_summary(…, rule=2)`). Each statement-cut rule reads
  one span-end rule (`STATEMENT_CUT_SPAN_END`: 1 reads 2, 2 reads 3), and span-end rule 3 binds
  only beside statement-cut rule 2, so any other pair in a gate.json is exit 3. The gate's
  refusal for statement-cut rule 1 with a span-end rule below 2 no longer says "or later". The
  planner wrote `(3, 2, 1)` until the flip of these fixes (Changed, above). Every pair a run has named (no key, 1, 2, and (2, 1)) is
  gated, ended and counted byte for byte as before: rule 1's checks, messages and receipt block
  are unchanged.
- `cf plan-locate --agent-timeout <duration>`: a plan sets the agent timeout for its run. tend
  runs in the environment of the session that launches it, so the plan stages
  `export CF_AGENT_TIMEOUT=<duration>` into the run's two wrappers (`bin/worker-agent.sh`,
  `bin/reviewer-agent.sh`), which overrides that environment's value for every call
  `agent_call.py` makes. The value is one the bridge reads (`100m`, `90s`, `2h` or seconds, no
  space) within `cflib.vocab.AGENT_TIMEOUT_MIN` (`5m`) and `AGENT_TIMEOUT_MAX` (`3h`), else the
  plan is refused before anything is written; `run.yaml` records it as `locate.agent_timeout`
  (null without the option) and `locate.config_shas.agents.agent_timeout`, so a run planned with
  it is no A/A replicate of one planned without. The default is unchanged (`AGENT_TIMEOUT`,
  `45m`), and a plan without the option stages the same wrappers and pins the same
  `config_shas` as before it; no gate file, bridge or helper changed, so every run planned
  before re-gates, re-tallies and re-exports as it did.
- `cf locate carry --from <inventory run> --to <inventory run>` and `cf bench carry`, the
  statement cut's carry-over map (`docs/proposals/statement-cut.md` §4): a re-cut is a routine
  operation, and what was judged on the old cut carries wherever its passage's text is unchanged.
  The map (`scripts/locate/carry.py`, `cf-locate-carry/1` in `references/locate-documents.md`
  §12) keys every passage of both runs' cuts of a page by `passage_key` and gives each old passage
  its fate from spans and span text only (`unchanged`, `end_moved`, `split`, `merged`,
  `reshaped`, `removed`, or `changed` when a new revision does not hold its text exactly once) and
  lists the new passages, refusing a page whose cut holds one text twice or whose document was
  made on another layer; it is written to `reports/locate-carry/<from>/<to>.json`, the same bytes
  on a re-run, and `--rows` copies rows keyed by `passage_key` (known answers, a verdict export)
  with each one's fate and whether it carries (`locate.carry.carries`, the proposal's §4.4: the
  passage unchanged, the new passage `formal_math`, and the same conjuncts unless the row says
  `"reads_conjuncts": false`). `cf bench carry` writes `gold/carry-<n>.json` (`cf-bench-carry/1`,
  `references/locate-bench.md` §7), each frozen gold row `carried`, `reanchor` or `relabel`
  (`vocab.GOLD_CARRY_STATES`): carried when the passages over its span did not move (every one
  unchanged and still the passages there, which for a span inside one old passage is stricter
  than the proposal's letter, since a nested new passage changes what the score joins it
  against), re-anchored when the same holds on a new revision or rendering, its labels kept and
  its span moved to the report's `anchor`, and relabelled otherwise and across a labeler bump; no
  label file is edited, and `bench.score.scorer()` leaves `carry.py` out of the scorer's identity
  (`NOT_SCORING`), though the edits to `__init__.py`, `layout.py` and `score.py` change its sha
  once: re-score both sides of a `cf bench compare` made across this change.
  `tests/data/locate/carry-parity.json` holds the hand-written cases a consumer's port runs.
  Nothing else reads the map, so every run plans, gates, tallies and exports as before. The
  proposal's status line, §4, §6.2, §7 and §9 say what of the carry package is built, the
  readings it took (gold carried more strictly than §4.4's letter, `reanchor` with no tool yet
  writing the new frame, no `state.json` record), and that its `scripts/bench/gold.py` share
  waits for the fixes package's `derive_status(…, status_rule=2)`; §8.3 tells WikiLean that a
  verdict that read the conjuncts (a bundle's, a bundle part's) carries only while the map's
  `conjuncts_changed` is false.
- The statement cut's specification, `docs/proposals/statement-cut.md` (the maintainer's answers
  of 2026-10-21 on statement-level extraction: one passage per statement, a coverage claim only
  as a bundle on a compound statement taking its weakest part's relation, the smoke-3 audit's
  failure-mode fixes, and a versioned cut with a carry-over map), and the names its four packages
  build against: `cflib.vocab`'s statement-cut block (the target versions `RULES_STATEMENT_CUT`,
  `SPAN_END_STATEMENT_CUT`, `STATEMENT_CUT_RULE` and `STATUS_RULE`; `COMPOUND_SHAPES`, the
  conjunct bounds, `CONJUNCT_RELATIONS`, `CUT_FLAGS`, `CARRY_FATES` and the rest), the
  revision-free keys in `cflib.hashing` (`span_sha256`, `passage_key`, `pair_key`, `bundle_key`,
  `cut_id`), `conjunct_relation` and `tests` at the end of `JUDGMENT_LOCATION_FIELDS`, and the
  prompt slots the new prompts will render. No stage read them when this landed (the four
  packages that read them followed, and the planner writes the new versions from the proposal's
  flip on), so every run planned before the flip plans, gates, tallies and exports as before, and
  a location of an earlier document carries neither new field.
- `cf bench score --subset <ids>|<file>`: a pre-registered subset of the split's
  articles, for a run that covers fewer than the whole split (the second smoke
  covered 4 of 36 eval articles and could not be scored). The document says
  `subset: true` with the list and its sha, the list is a pin of
  `measurement_id`, the file is `<split>-subset-<id16>-<s8>.json`, and
  `cf bench compare` refuses a subset score. Gold must still be frozen.
- Locate L1, web sources (`docs/proposals/wikipedia-locate.md` §2.1, §2.2, §3;
  `references/locate-documents.md` §7). `cf sourceset create | enumerate-wikiproject
  | adopt | fetch | render | status` keeps a sourceset of page revisions as
  append-only, chained manifest versions (a dropped source, a changed recorded
  value or a second writer is refused) with one directory per revision.
  `scripts/sources/wikinet.py` is the one module that reaches Wikipedia — the
  PageAssessments listing, page infos fifty at a time, and each revision's
  `action=parse` output once with one fixed parameter set, through curl with
  `maxlag`, a polite interval and retries, only under `--execute`; recorded
  responses replay with `--fixtures`. `scripts/sources/websource.py` is the
  identity (`params_sha256`, the manifest entry and its checks, the permalink
  from the family's template). `scripts/sources/render_html.py`, frozen as
  `web-html/1`, renders the canonical body into the Literature Links layer
  (`textContent`, ASCII whitespace collapsed), the formulas, the head text, the
  reading copy and its layer map (`cf-layer-map/2`, formula segments flagged),
  sections, blocks and the three deterministic finders, both measured over the
  text the reading copy keeps; comments are removed as the HTML tokenizer
  delimits them, a byte-order mark is dropped, and a body with a lone surrogate
  or nesting deeper than 256 is refused. A page a consumer pinned keeps its
  adopted revision (`Manifest.preferred()`); a parity test against the
  consumer's text layer is pinned by `tests/data/locate/text-layer.json`,
  which the consumer's own `text_layer` generated.
- Locate's corpus (`docs/proposals/wikipedia-locate.md` §2.3–§2.5 and §6,
  `references/locate-documents.md` §8): `cf corpus init | add | probe-command |
  ingest | build-db | status | search | show`. A universe is
  `corpora/<u>/universe.yaml` (`scripts/corpus/universe.py`): libraries pinned at
  40-hex commits with their toolchain, roots, licence and brain_lib token,
  exclusions with their reasons, per-toolchain canary results, and
  `universe_sha256` over (id, commit, index sha). `probe-command` prints the host
  pipeline over the probe drivers (disk floor, `ulimit -n 65536`, caches, build,
  probe, ingest, prune) and runs nothing. `ingest` streams a probe's `index.jsonl`
  (or a published `autoform-library-index/v0` file) line by line, sorts by name
  through temporary chunks when the input is not sorted, refuses a repeated name,
  a foreign library or commit and a toolchain whose acceptance canary
  (`templates/corpus/canary/`, `ingest --canary`) has not passed, reads
  `@[wikidata]`/`@[stacks]`/`@[kerodon]` attributes from a checkout's source
  spans and Wikipedia URLs from docstrings, and **re-decides every proof status
  in the §2.4 order** with `cflib.vocab.decide_proof_status` — the probe as run
  decides an axiom by its kind first, has no `axiom_dependent` and lets a failed
  `isProp` fall through to `proved`; ingest gives that last case the conservative
  `statement_only`, flagged. `build-db` writes `corpus.sqlite` with the §8 schema
  (exact table, FTS5 over name words, signature and docstring, tags) in
  `journal_mode=DELETE`, atomically, refusing an index whose bytes are not the
  pinned ones and doing nothing when the database is current.
  `scripts/corpus/corpus_search.py` and `corpus_show.py` are the stdlib host tools
  a locate worker runs from the gate directory (no FTS5 syntax reaches the
  database from a query); `scripts/corpus/query.py` is the read API the other
  locate stages call over the same functions. The WIP probe drivers pin git with
  gitsafe's settings, inlined because they run on the host without cflib.
- The locate planner (`scripts/locate/`, `docs/proposals/wikipedia-locate.md` §3, §5,
  §7, §2.7 and §11.1). `cf locate candidates` writes L2: curated joins (a library's
  `@[wikidata]` tag on the article's QID, Mathlib-shaped `1000.yaml` and `100.yaml` lists,
  Wikipedia URLs in docstrings, a library's main results), every one checked in
  `corpus.sqlite`'s exact table and a phantom dropped by name, plus lexical retrieval over the
  title and the lead's bold terms; `--blind` is the G1-blind arm. `cf plan-locate` plans the
  three presets `locate-inventory`, `locate` (at most 8 `formal_math` passages of a frozen,
  gated inventory; `--subject-only` for the article tier) and `locate-challenge` (the §5 rules
  and a seeded stratified sample, recorded per record in `challenge-plan.json`), at most 500
  tasks a root, deterministically: tasks, task manifests, the locate prompts, `gate.json` and
  `gate.local.json`, and a tend root whose gate directory carries the head-text helper, a
  passage-id tool the inventory worker runs instead of counting offsets, the record builder and
  both vendored packages; `cf plan-run` refuses the locate presets by name. `cf tally --locate`
  turns a harvested run into the finding rows of `references/locate-documents.md` §10 (keyed by
  `finding_id` unchanged), applies challenge verdicts, joins `proof_status` from the index and
  derives each passage's status; an unresolved passage is counted on its own, and a planned
  passage no document answered is kept as missing. `cf export literature-links` writes the sealed
  record set: location, absence and unresolved records checked with the vendored checker, quotes
  capped at 12 words and 32 characters of context, receipts as sidecars, `SEAL.json`; a passage
  whose challenge is owed and a source over the byte budget are refused by name, and a re-export
  is byte-identical.
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
  (with `--include-source-text`), `corpus.sqlite` and the L2 candidates to the
  host paths its `gate.json` names; the verify stage
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
- `cf bench build | anchor | freeze | score | compare`, the locate benchmark
  (`docs/proposals/wikipedia-locate.md` §8 and §9; formats in
  `references/locate-bench.md`). `build` resolves a tier list against a pinned
  sourceset -- each page at the revision a run reads, a consumer's adopted pin
  first -- refusing an article it lacks; draws the uniform and curated samples
  over the whole frame with recorded inclusion probabilities, so only the drawn
  articles need a render (a drawn unrendered one is refused with the list to
  fetch and render, or, with `--exclude-unrendered`, recorded in `excluded` with
  its probabilities); reads Mathlib's 1000.yaml (suffixed keys naming their base
  QID) and 100.yaml (resolved through 1000.yaml, else by folded title) as
  curated lists, one reader for the stratum and for G1; clusters
  articles by shared QIDs and G1 accept sets, splits by each cluster's hash and
  samples the G3 spans from `paragraphs.json`, writing a label-free host
  manifest. `anchor` resolves Mathlib's curated lists and the `@[wikidata]` tags
  at the universe's commit through a rename table, widens the accept set with
  `to_additive` twins, and drives the frozen labeler
  (`workflows/locate-label.workflow.js`: G1 anchors, G3 blind labels with
  independent hunters re-checked at the pin, the tag-blind recall *h*, GX
  inventories) by emit and transcribe, refusing a result from another labeler.
  `freeze` writes the gold manifest, irreversibly (a change is a new version),
  and `--plant` the module-ablated index. `score` joins runs to gold by span
  overlap (four whole words, one to one), computes every §8.2 metric per tier
  with article-cluster bootstrap intervals, the false-not-formalized causes and
  each miss's error code, and a `measurement_id` over every pin; it refuses an
  incomplete receipt, edited gold, an uncovered split and a transcript holding a
  label canary. `compare` refuses a model or renderer change and applies the §9
  acceptance rule (paired intervals, safety bounds, strata, cost, the A/A run,
  alpha-spending), claiming a decision only past 150 eval articles or 400 gold
  positives, and records it in the loop ledger. `cflib.vocab` gains the
  labeler's modes. `cf score` is unchanged.
- The synthetic end-to-end driver runs every staged tool as the run host does:
  the gate and `passage_ids.py` from a copy of the planned root's `gate/` with
  `python3 -I`, a bare environment and a scratch working directory, so a gate
  directory missing a module fails it (field defect P2); each bad document
  must be refused as a verdict under the rule it breaks, and the inventory's
  are written under their own task, where rule 7 used to refuse them first.
- The shared interfaces the locate packages meet at (integration wave 1). The corpus's
  read API (`scripts/corpus/query.py`) is what every consumer calls instead of a
  private reader: `check_universe` refuses a database built for another state of the
  universe, `declaration` takes the exact-table `commit`, `tagged` and the new
  `tags_of_kind` return each tag's declaration path, line and value, and `search`
  (and `corpus_search.search`, `cf corpus search --all-words`) has an all-words mode.
  `corpus.sqlite` gains a nullable `decls.deprecated_to` (a deprecated alias's new
  name) and `meta.blind`; `cf corpus build-db --blind` writes `corpus.blind.sqlite`,
  the G1-blind copy with no tags and no Wikipedia links in its docstrings, recorded
  as `corpus_db_blind` (`cflib.paths.WorkspacePaths.corpus_db_blind`).
  `Universe.mathlib_library()` is the one rule for which library Mathlib's curated
  lists name. `Sourceset.record_state(section, payload, key=None)` merges one keyed
  row, as the sourceset state file's one writer. `cflib.vocab` now defines the
  values packages had kept privately: `MAX_TASKS_PER_ROOT`, `LOCATE_ATTEMPT_CAP`,
  `SESSION_SLOTS`, `AGENT_TIMEOUT`, `UNRESOLVED_BY`, `CHALLENGE_RULES`,
  `CHALLENGE_PLANNED` and `INDEX_FLAGS`. `cf init`'s `.gitignore` also ignores the
  blind database, the bench's planted database copy and the labeler's excerpts.
- Review fixes to the corpus. Ingest reads the library, commit and toolchain from
  the index's own header and manifest (which may not disagree, and a missing
  toolchain is refused), refuses an index whose bytes or record counts are not
  the ones the probe wrote, a dirty probed checkout, a record without its closure
  or contradicting itself, an index with no module path, and a `--checkout` that
  is not the clean pinned commit; drops private declarations whose user names
  collide (counted) instead of refusing the library; and stores Wikipedia tags in
  one canonical page-URL form (apostrophes, balanced parentheses and percent
  encoding kept as the title). A published `autoform-library-index/v0` index no
  longer bypasses any of this: it is refused unless accepted with
  `--accept-published <reason>`, recorded in `universe.yaml`. Names and queries
  are split into words by one rule (`natDegree` finds `Polynomial.natDegree`),
  the host tools exit 2 on an argument that is not UTF-8, and `cf corpus add
  --conventions` no longer overwrites a conventions file on a refused add.
  `probe-command` sets `ATLAS_ROOT` once (an unset one made the clean-up `rm -rf
  /libs/...`), validates the toolchain, quotes what it prints and builds the
  canary in a clean, niced environment. **Two host-driver changes**:
  `build_universe_host.sh` now passes a library's `SRC_DIR` to the probe (it was
  dropped by the clean environment, so a library whose sources sit under it was
  indexed with every path null; `index-ok` now re-probes such an index), and its
  disk floor measures the fullest of `$ATLAS_ROOT`, `/` and `$HOME`, refuses a
  non-numeric `MIN_FREE_GB` and stops when `df` cannot answer. The universe
  built on the run host before these fixes predates them.
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
  L-M0: the vocabulary and the keys the stages above were then built on). `cflib.vocab` gains the three locate run modes
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
  `CLAUDE.md` §3 names the Wikipedia fetcher (`scripts/sources/wikinet.py`)
  as a network exception.
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
- `cf overlay check` and `check_generated.py` match a config's patterns against the whole
  value, so they refuse a file name, route, path or host that ends in a newline, as the
  schema does. `serve.py` refused such a file name, so `cf overlay check` could pass a
  config it would then refuse.
- The overlay's hover card. A key held with Alt, Ctrl, Meta or Shift on a
  statement's token is the browser's again. A card that keyboard focus opened
  comes back after the pointer passes over another token, and Escape or the
  token losing focus forgets it, while a mouse click no longer pins a card. On
  touch, a tap inside the card keeps it. A token scrolled out of the panel
  hides its card, and the next Escape leaves the item. A token links only to
  a page under the site's own path.
- `verso_extract.py`: a card whose `version` is a release tag, which Literature
  Links allows, or absent. A committed `verso.json` keeps a statement for a
  card that pins the build's commit, and a tag never equals a commit, so where
  only some cards named a tag their statements went with nothing said (on one
  real companion's annotations 245 of 4,197 statements and 1,851 of 14,545
  hovers published, and every check ok); where all did, the message sent the
  producer to `--commit`, which refuses a tag. A tag may or may not name the
  build's commit and only the project's git could tell, so `build_site.py`,
  `check_generated.py` and `--pages` now refuse such a statement, name the
  cards and say to pin each by a full commit. A card with no statement in the
  file may still pin a tag, and one that pins another full commit keeps no
  statement, as before. `check_generated.py` now also prints the remedy for a
  build no card pins. Companions take this at their next `cf overlay sync`.
- `cf overlay check` never read a committed `verso.json`, so it said ok on a
  file `check_generated.py` and `build_site.py` refuse, and a producer following
  `docs/generating-an-overlay.md` found out in CI. It now says what
  `check_generated.py` says, from that tool's own function in this checkout, so
  the two cannot differ. A companion's copy of a tool is never run.
- `verso_extract.py`: a token whose link names no anchor gets no link, as
  documented; an encoded NUL in a card's or a token's link is skipped; and a
  committed `verso.json` whose run kind is not text is refused by
  `build_site.py` and reported by `check_generated.py` instead of crashing
  them.
- The overlay's strict JSON readers (`serve.py`, `build_site.py`,
  `check_generated.py`, `overlay_annotations.py` and `scripts/overlay/config.py`)
  report an integer too long for Python, or nesting too deep, as invalid JSON
  instead of crashing, and refuse a number too large for a float, which would
  read as Infinity.
- Lean snippets (`adapters.leansnippets`): a comment, a term, a `do` block or
  a `mk ::` line at column 0 inside a declaration no longer ends it; a `let x
  ←`, or a `let rec` given by alternatives, no longer runs a statement into
  its proof; the character `'\"'` opens no string; and a declaration whose
  attribute holds brackets or a string is found.
- `cf bench anchor --transcribe` of gold of judgment rules 3 (`locate-label/6`) no longer aborts
  when the labeler lists one `library:declaration` twice in a G3 span whose bundle stands: status
  rule 2 keys a location by that pair, and `vocab.derive_status` refused it held twice, for the
  whole transcription. `bench.gold._recheck` now keeps a declaration once: a repeat with the same
  relation and `conjunct_relation` is dropped (the first kept), and listings that disagree are all
  dropped, so a bundle naming that declaration does not stand, whatever the listings' order (it
  stood or not by their order before). Each dropped listing is in the row's `dropped` with its
  reason. Gold of earlier labelers is transcribed exactly as before, and no `/6` gold existed.
- Corpus fixes from building a 48-library universe on the run host (the field
  defects D1-D12). **Host-driver changes**: a module whose name needs «»
  escaping (`Alpha/Problems/1.lean` is `Alpha.Problems.«1»`) is indexed instead
  of skipped with a warning and a successful exit, the probe template finds its
  source file (`probeRevision` 2), and a file no module name can express stops
  the expansion; the universe list gains the kinds `core` (Lean's `Init`/`Std`
  at the workspace toolchain's `Lean.githash`, lean4 repository paths) and
  `package-in-workspace` (a Lake package at the workspace manifest's rev), both
  probed inside a built `WORKSPACE` and recorded at their own commit; the bash
  driver's git runs with gitsafe's pins and content-filter blanks. `cf corpus`:
  every subcommand takes `--base <dir>` for a universe outside any workspace;
  `add --host-kind/--host-ref/--host-option` records how the driver indexes a
  library, and `probe-command` prints that library's own kind, ref and
  options, the universe's whole driver list (never one shared one-line list),
  and lines that run as printed (placeholders only in comments, `$CF_DRIVERS`
  for the drivers). Ingest maps Autoform's published `complete` by kind
  (`proved` for a theorem, `definition` for data) and its `lemma` to
  `theorem`; tags come from whole attribute lists (nested brackets, multi-line
  `to_additive` lists, a twin's `(attr := …)`) and from `attribute […] Name`
  commands, resolved through the enclosing namespaces. `cflib.jsonio` writes a
  new file with the umask's mode instead of mkstemp's 0600 and keeps an
  existing file's mode. The default universe's scope is stated (proposal
  §2.3): Mathlib with its `Wanted` library, Lean core without `Lean`,
  formal-conjectures with `FormalConjecturesForMathlib`.
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
