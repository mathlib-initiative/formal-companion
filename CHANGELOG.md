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

### Changed
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
