# Workflow patterns

The five agent topologies that produced usable results in the 2026-09 audit, written as reusable
skeletons. They are the local (Workflow-tool) half of the pipeline: adjudication, challenge, PR
review, claims verification, and fleets under ~100 items. Fleets of hundreds of read-heavy tasks
belong on the server orchestrator instead (`references/runbook-server.md`).

Each skeleton is parameterised by `args` and ships in `workflows/`. The originals they were
distilled from are named per section.

---

## 0. The rules that apply to all of them

1. **`model: 'opus'` on every subagent.** The default-model runs lost agents to session limits
   mid-flight (70 of 114 in one case; two verify votes in a review run). Heavy reading,
   adjudication and proving go to Opus subagents; the main loop stays short.
   *Source:* `memory/model-budget-and-tool-quirks.md`.
2. **All inputs come through `args`, and `args` must be real JSON.** A launch with `[]` completes
   instantly, silently, with zero agents — so the first line of the script logs the input count.
3. **No clocks, no randomness, no ambient state.** Replay is keyed on the prompt bytes and the
   order of the preceding `agent()` calls. `Date.now()` in a prompt makes every resume a full
   re-run.
4. **Resume is the normal path, not recovery.** When a run dies on a rate limit, edit only the
   failed `agent()` calls' options (e.g. add `model: 'opus'`) so the unchanged calls replay from
   cache, and resume with `resumeFromRunId`.
5. **A killed run's results are still on disk.** `journal.jsonl` in the run directory holds every
   `result` event; `scripts/harvest/harvest_journal.py` reconstructs the outputs and writes the
   remaining work list. Agent labels are the only link between a result and its item, so **encode
   the item in the label** (`adj:Exercise_1_2_4`) and keep the label format stable.
6. **A null vote is missing, not a pass.** Filter `Boolean` on every `parallel()` result and count
   what returned; a refusal or a session death must never read as agreement.
7. **Schemas everywhere.** Every `agent()` that feeds a downstream step declares a JSON schema with
   `required` fields; prose answers cannot be tallied.
8. **The script is read-only toward every repository.** The COMMON preamble says so, and the
   campaign's hard rules back it: no edits, no `git` state changes, no builds.

Script shape:

```js
export const meta = {
  name: 'adjudicate',
  description: 'Adjudicate routed findings with adversarial verification',
  phases: [
    { title: 'Lenses',    detail: 'three independent readings per finding' },
    { title: 'Reconcile', detail: 'one reconciler per finding' },
    { title: 'Skeptics',  detail: 'two opposing skeptics per verdict' },
  ],
}
log(`inputs: ${args.findings.length}`)          // fails loudly on an empty args
```

---

## 1. Lens → refute + consequence (the PR-review skeleton)

Parallel lenses produce findings; each non-`note` finding is attacked twice, once on **truth** and
once on **materiality**. Distilled from `review-mt-pr13-wf_2b97cc16-c60.js` and
`review-tml-pr9-pr10-wf_cbc8f48e-98f.js`.

```js
const FINDINGS = { type:'object', required:['findings','verdict','summary'], properties:{
  findings:{ type:'array', items:{ type:'object', required:['title','severity','claim','evidence'],
    properties:{ title:{type:'string'}, severity:{enum:['high','medium','low','note']},
                 claim:{type:'string'}, evidence:{type:'string'}, suggested_action:{type:'string'} } } },
  verdict:{ enum:['MERGE','MERGE_WITH_NOTES','DO_NOT_MERGE'] }, summary:{type:'string'} } }

const VERDICT = { type:'object', required:['refuted','confidence','reasoning'], properties:{
  refuted:{type:'boolean'}, confidence:{type:'number'}, reasoning:{type:'string'} } }

phase('Review')
const reviews = await parallel(args.lenses.map(l => () =>
  agent(`${args.common}\n\nYOUR LENS:\n${l.prompt}`,
        { label:`review:${l.key}`, phase:'Review', schema:FINDINGS, model:'opus', effort:'high' })))

const all = []
reviews.filter(Boolean).forEach((r, i) =>
  (r.findings || []).forEach(f => all.push({ ...f, lens: args.lenses[i].key })))
log(`${reviews.filter(Boolean).length}/${args.lenses.length} lenses returned; ${all.length} raw findings`)

phase('Verify')
const judged = await parallel(all.filter(f => f.severity !== 'note').map(f => () =>
  parallel([
    () => agent(`${args.common}

ADVERSARIAL VERIFICATION. Another reviewer raised this. REFUTE it if you can.
TITLE: ${f.title}
CLAIM: ${f.claim}
THEIR EVIDENCE: ${f.evidence}

Re-derive from primary sources; do not restate their evidence. Default to refuted=true when
genuinely uncertain. refuted=false only if you independently reproduced the problem.`,
      { label:`refute:${f.lens}`, phase:'Verify', schema:VERDICT, model:'opus', effort:'high' }),
    () => agent(`${args.common}

CONSEQUENCE CHECK. Assume the claim is factually right.
TITLE: ${f.title}
CLAIM: ${f.claim}

Does it MATTER: does it make a statement unfaithful, unsound, vacuous or false; break a proof,
a build or a data pin? refuted=true if real but immaterial; refuted=false only if real AND material.`,
      { label:`consequence:${f.lens}`, phase:'Verify', schema:VERDICT, model:'opus', effort:'high' }),
  ]).then(votes => {
    const v = votes.filter(Boolean)                       // a null vote is missing, not a pass
    const truth = v[0], material = v[1]
    // SHIPPED RULE: truth must survive; materiality only sets scope/severity.
    const survives = truth ? !truth.refuted : false
    return { ...f, votes: v, survives, material: material ? !material.refuted : null }
  })))

const survivors = judged.filter(Boolean).filter(x => x.survives)
```

**The survival rule changed deliberately.** The originals used an OR rule
(`v.some(x => !x.refuted)`), which lets a factually **refuted** finding survive because it would
matter if it were true. The shipped rule separates the two votes: truth must survive the refuter,
and the materiality vote sets severity and scope. Anything comparing new results against the
baseline must state this, because the baseline was produced under the OR rule
(design `decisions.json`, "Survival rule for adversarial verification").

**Evidence bar for a survivor:** `file:line` at a named commit, a book line, or a command and its
output. A lens with no findings is a good result.

---

## 2. Three-vote majority (higher-rigour variant)

Where the cost is justified — adjudication of a finding that will go upstream — three
perspective-diverse skeptics vote and the majority decides, with sub-threshold dissent recorded.

```js
const ROLES = [
  { key:'content',    prompt:'Argue the finding IS a genuine content defect. Exhibit the lost case or clause.' },
  { key:'convention', prompt:'Argue it is NOT: show the book\'s claim is recoverable by routine unfolding, or that the errata changes it.' },
  { key:'sources',    prompt:'Ignore both arguments. Re-derive from the book page and the Lean source alone.' },
]
const votes = (await parallel(ROLES.map(r => () =>
  agent(`${args.common}\n\nROLE = SKEPTIC (${r.key}).\n${r.prompt}\n\nVERDICT UNDER TEST:\n${JSON.stringify(v, null, 1)}`,
        { label:`skeptic:${r.key}`, phase:'Skeptics', schema:SKEPTIC, model:'opus', effort:'high' })))).filter(Boolean)

const overturns = votes.filter(x => x.overturned && x.confidence >= args.threshold)   // default 0.7
const contested = overturns.length >= 2                        // 2-of-3 overturns the verdict
const dissent   = votes.filter(x => x.overturned && x.confidence < args.threshold)    // recorded anyway
```

Record `dissent` even below the threshold: a 0.6-confidence dissent was dropped from "contested"
once and had to be re-surfaced by hand (design `decisions.json`; `stage_map.md` Stage 7).

---

## 3. Lenses → reconciler → opposing skeptics (the adjudication skeleton)

The topology that produced the ledger. Distilled from
`adam-faithfulness-adjudication-wf_60a87e92-dd5.js`. Three readings of one finding — **book-literal**,
**Lean-semantics**, **convention-vs-content policy** — then a reconciler that *decides* rather than
averages, then two skeptics pushing in opposite directions.

```js
const LENS = {
  book:   `LENS = BOOK-LITERAL. Establish what the book claims: quantifiers, hypotheses, the domain of
every object, which degenerate cases are included, and any standing convention from its own earlier
definitions. Read the surrounding pages and the errata. Compare to the Lean only at the end.`,
  lean:   `LENS = LEAN-SEMANTICS. Open the declarations and read them with every repo definition they
mention unfolded, including implicit typeclass facts. Grep the module and its neighbours for companion
declarations or anonymous examples that state the missing part. Then compare to the book.`,
  policy: `LENS = CONVENTION-VS-CONTENT POLICY. For each clause the finding says differs, decide whether
the book's claim is recoverable by routine unfolding of the documented conventions (CONVENTION) or not
(CONTENT). Write the recovery argument explicitly when you claim CONVENTION; write the concrete lost
case when you claim CONTENT. Be strict in both directions.`,
}

const adjudicated = await pipeline(args.findings,
  f => parallel(Object.keys(LENS).map(k => () =>
        agent(`${args.common}\n${LENS[k]}\n\nYOUR FINDING:\n${JSON.stringify(f, null, 1)}`,
              { label:`${f.finding_id.slice(0,8)}:${k}`, phase:'Lenses', schema:LENS_SCHEMA, model:'opus' })))
       .then(ls => ({ f, lenses: ls.filter(Boolean) })),

  ({ f, lenses }) => agent(`${args.common}
ROLE = RECONCILER. Three independent lens readings are below. Re-read the book page and the Lean
yourself wherever they disagree or cite different facts; do not average — decide. Produce the final
verdict, a self-contained rationale, and either the refutation paragraph (REFUTED/CONVENTION) or the
concrete fix plan (CONFIRMED_CONTENT/ATTRIBUTION_GAP).

LENS READINGS:
${JSON.stringify(lenses, null, 1)}`,
    { label:`${f.finding_id.slice(0,8)}:reconcile`, phase:'Reconcile', schema:RECONCILE_SCHEMA, model:'opus' })
    .then(r => ({ f, lenses, reconciled: r })),

  ({ f, lenses, reconciled }) => parallel([
    () => agent(`${args.common}\nROLE = SKEPTIC (argue it IS a content defect). Try to overturn a
CONVENTION/REFUTED verdict with a concrete lost case or misreading; if the verdict is already
CONFIRMED_CONTENT, check the severity is not understated. Read the sources yourself. No concrete
evidence ⇒ overturned=false.\n\nVERDICT: ${JSON.stringify(reconciled, null, 1)}`,
      { label:`${f.finding_id.slice(0,8)}:sk-content`, phase:'Skeptics', schema:SKEPTIC, model:'opus' }),
    () => agent(`${args.common}\nROLE = SKEPTIC (argue it is NOT). Try to overturn a
CONFIRMED_CONTENT/ATTRIBUTION_GAP verdict by showing the claim is recoverable by routine unfolding,
that the book's own conventions exclude the case, that the errata changes the statement, or that a
faithful declaration already exists; if the verdict is already CONVENTION/REFUTED, check the recovery
argument is valid. No concrete evidence ⇒ overturned=false.\n\nVERDICT: ${JSON.stringify(reconciled, null, 1)}`,
      { label:`${f.finding_id.slice(0,8)}:sk-convention`, phase:'Skeptics', schema:SKEPTIC, model:'opus' }),
  ]).then(sk => {
    const s = sk.filter(Boolean)
    return { finding_id: f.finding_id, item: f.item, reconciled, lenses, skeptics: s,
             contested: s.some(x => x.overturned && x.confidence >= 0.7),
             dissent:   s.filter(x => x.overturned && x.confidence <  0.7) }
  }))
```

Two variants of the same shape, both worth keeping:

- **Blind readers → reconciler** for *definitions* (definitions are viral: an unfaithful definition
  poisons every theorem about it). Two independent readers compare the definition body clause by
  clause with the book, each with a different extra instruction (one verifies against the exact
  source rather than a snippet; the other reads two pages either side for inherited conventions);
  a reconciler merges.
- **Inventory** for book definitions with *no* native definition: classify what stands in its place
  (`references/vocabulary.md` §2.4) rather than adjudicating.

**Refute-by-default challenge** is the cut-down version for rows that only need a second reading:
two independent refuters per finding, `survives_all = every refuter agrees`, default REFUTED unless
the ruling survives the refuter's own independent reading
(`challenge-batch1-findings-wf_c3f0e5d0-d65.js`).

---

## 4. Claims-verify, with an independent second vote on the numbers

Before any report leaves the campaign, every claim in it is re-derived from primary sources by
lenses that are told to **refute**. Distilled from `verify-audit-summary-wf_9da8dddd-e4b.js`,
which found 19 wrong claims of 173 in one pass, and 36 in an earlier draft.

```js
const CLAIMS = { type:'object', required:['claims'], properties:{
  claims:{ type:'array', items:{ type:'object', required:['claim','verdict','evidence'], properties:{
    claim:{type:'string'}, verdict:{enum:['CORRECT','WRONG','UNVERIFIABLE']},
    evidence:{type:'string'}, correction:{type:'string'} } } }, notes:{type:'string'} } }

phase('Verify')
const results = await parallel([
  ...args.lenses.map(l => () => agent(common + l.prompt, { label:`verify:${l.key}`, phase:'Verify', schema:CLAIMS, model:'opus' })),
  () => agent(common + args.critic_prompt, { label:'critic', phase:'Critique', schema:CRIT, model:'opus' }),
])
```

Non-negotiable properties:

- **Verdict rules stated in the prompt:** `CORRECT` only if re-derived and matching; `WRONG` with
  the exact corrected value in `correction`; `UNVERIFIABLE` with what was tried. Every claim carries
  a path with line numbers or the exact command and its output.
- **A second, independent numbers lens** re-derives the same counts with its own script and pastes
  the output. One lens computing the numbers is one reading.
- **"Be exhaustive for your lens: list every distinct claim, not a sample."**
- **Tell the verifier the bias:** every counting error in this campaign ran toward "cleaner".
- **A completeness critic** runs beside the verifiers: what is missing, what is over-detailed, what
  is misleading (ratios without denominators, "100% signal"), what is jargon, plus a plain-prose
  TL;DR. It is told not to re-verify numbers.

---

## 5. Completeness critic (as a phase of any adjudication run)

A single agent reads the *summary* of everything the run produced and asks what is missing:
contradictions between groups (a definition ruled faithful whose item a finding ruled content); any
verdict whose rationale rests on a claim it can falsify quickly — **and it must do that for the
three it finds most suspicious**; items in neither list when compared against the book's own
numbering; anything the report's audience would be embarrassed to have missed. Plain text, most
important first, each with evidence.

```js
const critic = await agent(`${args.common}
ROLE = COMPLETENESS CRITIC. Below is the summary of this run.
(a) contradictions between groups; (b) verdicts resting on a quickly-falsifiable claim — falsify the
three most suspicious yourself; (c) items that appear in no group when compared against the book's own
numbering; (d) anything a report for the repository owner would be embarrassed to have missed.

SUMMARY:
${JSON.stringify(summary, null, 1)}`, { label:'critic', phase:'Completeness critic', model:'opus' })
```

---

## 6. Per-item fleet (local, < ~100 items)

The local analogue of a server run: `pipeline(args, item => agent(...), result => challenge(...))`,
one agent per item, its challenge stage chained onto the same pipeline so a finding is attacked in
the same run that produced it. Distilled from the batch-1 fleet (recovered transcript `b174aa13`
L5600) and `differential-batch2-wf_2decc288-5a7.js`.

Three things that skeleton must carry:

1. **The item id in the agent label**, so `harvest_journal.py` can rebuild the mapping.
2. **Worker self-gating** — the script cannot run the quote gate itself (no filesystem), so the
   agent is told to verify its own quotes, and the gate runs post-hoc at harvest, feeding failures
   back as a second run.
3. **Filter the challenge input**: only content-level rulings are challenged
   (`kind !== 'PAGE_ANCHOR' && !unsure`), or two thirds of the budget goes to pagination.

Sizing, from the record: 10 findings adjudicated with the §3 topology was **225 agents, 5.9 h wall,
75 limit failures and 3 resumes**; one PR review was 189 agents; a 199-agent fleet lost 68 to a
five-hour limit. Size by agent count, not item count.
