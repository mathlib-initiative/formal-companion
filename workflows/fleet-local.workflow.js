export const meta = {
  name: 'cf-fleet-local',
  description: 'Local fallback fleet for small runs (<100 items): one worker per book item with a self-run quote gate, then two refuters per content ruling',
  whenToUse: 'Only when a run is too small to be worth the server (tend + agentbox). Build args with `cf plan-run --preset <preset>` then `cf run <run-id> --local`. Hundreds of items belong on the server: this substrate is capped at min(16, CPUs-2) concurrent agents and dies on the subscription session limit mid-run.',
  phases: [
    { title: 'Work', detail: 'one agent per item: read the book, compare the statements, self-run the quote gate' },
    { title: 'Challenge', detail: 'two independent refuters per content-level ruling', model: 'opus' },
  ],
}

// Ported from: the v1 campaign's batch-1 three-way-faithfulness-differential
// fleet (199 agents, 129 of 225 items done before the session limit killed it),
// which does not travel with this repo.
// Changes the record forces:
//   * inputs come from `args` -- repos, book paths, conventions and the gate
//     command were constants in the original;
//   * the worker SELF-GATES: a Workflow script has no filesystem access, so the
//     quote gate cannot run "inside the script after each agent" (the design's
//     original wording). The worker runs the workspace's own gate command on its
//     own draft and reports the result; the authoritative gate runs afterwards,
//     out of tree, at harvest.
//   * the return value carries the post-hoc recovery recipe (harvest_journal ->
//     gate -> requeue), because this substrate is expected to die mid-run: the
//     journal is the durable artifact, not the script's return value.

const A = args || {}
const C = A.source || {}
const ITEMS = A.items || []
const REPOS = A.repos || []
const MODE = A.mode || 'differential'
const GATE = A.gate || {}
const CHALLENGE = A.challenge !== false
const MODEL = A.model || undefined      // omit -> inherit the session model; set 'opus' for adjudication-grade work
const CHALLENGE_MODEL = A.challenge_model || 'opus'
// The structured-output enums and the corpus schema are the RUN'S GATE, not a
// second hand-written copy of it: a worker constrained to `PROXY` or to
// `NO_DEFECT_FOUND` on a preset whose gate allows neither produced a corpus in
// which every single file failed `verify_findings.py` -- the command this script
// prints as the next step.
const KINDS = GATE.kinds && GATE.kinds.length ? GATE.kinds
  : ['CONTENT', 'VACUITY', 'SUSPECTED_ERRATA', 'COVERAGE']
const VERDICTS = GATE.verdicts && GATE.verdicts.length ? GATE.verdicts
  : ['AGREE', 'CONVENTION_ONLY', 'CONTENT_DELTA', 'SINGLE_SOURCE_OK', 'SINGLE_SOURCE_DELTA', 'NO_DEFECT_FOUND']
const SCHEMA_VERSION = typeof GATE.schema_version === 'number' ? GATE.schema_version : 2
// Two refuters per content-level ruling, and the stable id that joins their
// verdicts back to the disagreement they read. The index is the position in the
// UNFILTERED disagreement list, so a PAGE_ANCHOR row cannot shift it and collect
// verdicts about a different finding.
const REFUTERS = 2
const didOf = (item, di) => item + '#' + di

const repoLines = REPOS.map(r => '  ' + r.name + ' = ' + r.repo + (r.conventions ? '\n      conventions: ' + r.conventions.replace(/\n/g, '\n      ') : '')).join('\n')

const MODE_BRIEF = {
  differential: `MODE = DIFFERENTIAL. Two or more independent formalizations of the same book item disagree in
MATHEMATICAL CONTENT => at least one is unfaithful to the book. Match declarations ACROSS repositories by mathematical
concept, then compare each matched concept to the book. The compiler cannot do this: a statement over the reals where
the book says R^d, a dropped dimension hypothesis, or a weaker conclusion all typecheck.`,
  'single-source': `MODE = SINGLE-SOURCE. Only one repository formalises this item, so there is nothing to diff
against: compare the Lean statement DIRECTLY with the book passage, clause by clause. Report a disagreement only when
the book's claim is not recoverable from the Lean statement by routine unfolding of the house conventions.`,
  attack: `MODE = ATTACK. One repository, and your job is to find a concrete WITNESS that its statement is not the
book's: an object satisfying the Lean hypotheses but not the book's, a case the book covers and the statement excludes,
or a way the statement is trivially true (vacuity). Every disagreement you report MUST carry a structured witness
{object, why} -- a disagreement without one is not a finding. If you cannot construct a witness, return
NO_DEFECT_FOUND for the item: that is a successful attack run, not a failure.`,
}

const CONTEXT = `Lean 4 formalizations of ${C.title || C.source_id} are on this machine:
${repoLines}

READ-ONLY. Do NOT edit any file in any repository, do not run a build, do not change git state. You produce findings only.

${MODE_BRIEF[MODE] || MODE_BRIEF.differential}

DATA
- Statement tables (one JSON row per declaration, with file:line and the signature):
${REPOS.map(r => '    ' + r.name + ': ' + r.statements).join('\n')}
${A.pairs ? '- Paired rows for this item: grep -F \'"item": "<ITEM>"\' ' + A.pairs : ''}
- The source: ${C.text} -- \`${C.source_id}\` (pdftotext of the pinned edition, sha256 ${C.source_sha}; pages separated by form feed)
  and one file per page at ${C.pages_dir}/pNNN.txt (PDF page = printed page + ${C.page_offset}).
  Another edition of the same work may exist; every quote is checked against THIS text and no other.
  Locate the item (e.g. grep -n 'Exercise 1.2.4') and READ THE ACTUAL PASSAGE; never work from memory of the book.
${C.errata ? '- The published errata: ' + C.errata + '. A statement that differs from the printed text may be following an erratum.'
  : (C.errata_withheld ? '- The published errata: ' + C.errata_withheld : '')}

CONVENTION deltas are NOT faithfulness bugs. Per repository they are listed above; in addition, never report: a
strictly MORE general statement that specialises to the book's (record it, mark CONVENTION_ONLY, note "strictly
stronger"); bundled vs unbundled hypotheses; naming; one book item split across several declarations; an exercise
deliberately left as a permanent sorry.

WHAT IS A CONTENT DELTA: different hypotheses (missing, extra, weaker, stronger); different generality that does not
specialise correctly; a weaker or different conclusion; different quantifier scope; a wrong endpoint or normalisation
convention; a vacuous or trivially-true statement; a "definition" that is not the book's definition.

KINDS you may report: ${KINDS.join(', ')}.
${KINDS.indexOf('PAGE_ANCHOR') === -1 ? 'PAGE_ANCHOR is NOT one of them: page anchors are compared mechanically against the pinned edition, not by you.' : ''}
Be adversarial toward every repository equally; none is privileged. But do not manufacture disagreements out of
convention deltas -- a false positive costs a human hour, and about 40% of first-pass findings in this pipeline were
wrong in exactly that way.`

const SELF_GATE = GATE.command ? `

SELF-GATE (do this before you answer, it is part of the task)
Write your draft result as JSON to ${GATE.scratch_dir || '/tmp'}/${'${TASK_ID}'}.json (use your item id, with spaces and
dots replaced by '-', as TASK_ID), then run:
    ${GATE.command}
It checks: every book_quote is at least ${GATE.quote_min_chars || 25} normalised characters and actually occurs in the
book text; every named declaration exists; kinds and severities are in the vocabulary${GATE.witness_required ? '; every disagreement carries a witness' : ''};
source_sha matches ${C.source_sha} and source_id is \`${C.source_id}\`. Fix whatever it rejects and run it again. Report the command you ran and its final
output in the \`gate\` field. If you cannot make a finding pass the gate, DROP the finding and say so in coverage_notes
-- a finding that cannot be evidenced does not exist. The authoritative gate runs again, out of tree, at harvest.` : ''

const ADJ_SCHEMA = {
  type: 'object',
  required: ['schema_version', 'run_id', 'source_id', 'source_sha',
             'item', 'book_located', 'matches', 'disagreements', 'coverage_notes'],
  properties: {
    // The gate reads these four off the FILE, and harvest writes the worker's
    // own object as the corpus record, so the worker stamps them itself.
    schema_version: { type: 'integer', description: 'exactly ' + SCHEMA_VERSION },
    run_id: { type: 'string', description: 'exactly `' + (A.run_id || '') + '`' },
    source_id: { type: 'string', description: 'exactly `' + (C.source_id || '') + '`' },
    source_sha: { type: 'string', description: 'exactly `' + (C.source_sha || '') + '`' },
    item: { type: 'string' },
    book_located: { type: 'boolean', description: 'you found and read the actual passage in the book text' },
    book_passage_quote: { type: 'string', description: 'VERBATIM 1-2 sentences from the book, at most 600 characters (machine-verified)' },
    matches: { type: 'array', items: { type: 'object', required: ['concept', 'decls', 'verdict'], properties: {
      concept: { type: 'string' },
      decls: { type: 'array', items: { type: 'object', required: ['repo', 'decl'], properties: {
        repo: { type: 'string' }, decl: { type: 'string' } } } },
      verdict: { type: 'string', enum: VERDICTS },
      note: { type: 'string' },
    } } },
    disagreements: { type: 'array', items: { type: 'object',
      required: ['concept', 'kind', 'repos_involved', 'faithful', 'book_quote', 'reasoning', 'severity'],
      properties: {
        concept: { type: 'string' },
        kind: { type: 'string', enum: KINDS },
        repos_involved: { type: 'array', items: { type: 'string' } },
        faithful: { type: 'array', items: { type: 'string' }, description: 'every repo whose statement IS faithful here (a LIST, possibly empty)' },
        unfaithful_decls: { type: 'array', items: { type: 'string' }, description: 'repo:decl for each side judged unfaithful' },
        book_quote: { type: 'string', description: 'VERBATIM from the book -- the sentence that decides it, at most 600 characters' },
        witness: { type: 'object', properties: {
          object: { type: 'string', description: 'the concrete object or instance that separates the two statements' },
          why: { type: 'string', description: 'why it satisfies one and not the other' },
          d: { type: 'string', description: 'the dimension/parameter value, when the witness is parametric' },
        }, required: ['object', 'why'] },
        reasoning: { type: 'string' },
        severity: { type: 'string', enum: ['high', 'medium', 'low'] },
        suggested_fix: { type: 'string' },
      } } },
    coverage_notes: { type: 'string', description: 'declarations in one repo with no counterpart concept in the others; sub-parts present in one repo only; findings dropped at the gate' },
    gate: { type: 'object', properties: {
      command: { type: 'string' }, passed: { type: 'boolean' }, output: { type: 'string' },
    } },
  },
}

const CHAL_SCHEMA = {
  type: 'object',
  required: ['disagreement_id', 'concept', 'ruling_survives', 'evidence'],
  properties: {
    // The join key, not the concept: `concept` is free text the refuter was
    // never told to preserve, and matching on it silently dropped one verdict of
    // every paraphrasing pair.
    disagreement_id: { type: 'string', description: 'copy the disagreement_id you were given, unchanged' },
    concept: { type: 'string' },
    ruling_survives: { type: 'boolean' },
    classification: { type: 'string', enum: ['CONTENT', 'VACUITY', 'CONVENTION', 'ERRATA', 'ATTRIBUTION', 'UNSURE'] },
    evidence: { type: 'string' },
    book_quote: { type: 'string' },
    corrected_ruling: { type: 'string' },
  },
}

log('local fleet: ' + ITEMS.length + ' items, mode ' + MODE + (CHALLENGE ? ', challenge on' : ', challenge off'))
if (ITEMS.length > 100) {
  log('WARNING: ' + ITEMS.length + ' items on the local substrate. Runs this size belong on the server (tend + agentbox); expect session-limit deaths and plan to resume.')
}

const results = await pipeline(
  ITEMS,
  (t) => agent(`${CONTEXT}

YOUR ITEM: ${t.item}
REPOSITORIES THAT FORMALISE IT: ${(t.repos || REPOS.map(r => r.name)).join(', ')}
PDF PAGE HINTS (may be empty; if so, grep the book text): ${JSON.stringify(t.pdf_pages || [])}
${t.note ? 'ITEM NOTE: ' + t.note : ''}

Procedure:
1. Pull this item's rows from the statement tables above. List every declaration from every repository.
2. Find the item in the book and read the FULL passage (statement, hypotheses, any "Let ..." preamble, sub-parts).
   Quote 1-2 sentences VERBATIM into book_passage_quote, at most 600 characters -- this is verified mechanically, so copy
   exactly. The gate records anything longer as over-long and refuses a quote over 3,000 characters.
3. Match declarations by mathematical concept. One book item often contains several concepts and one repository may
   split it differently. Unmatched declarations go in coverage_notes, not disagreements.
4. For each matched concept decide AGREE / CONVENTION_ONLY / CONTENT_DELTA (or SINGLE_SOURCE_OK / SINGLE_SOURCE_DELTA /
   NO_DEFECT_FOUND in the one-repository modes). Be precise about what differs.
5. For every content-level delta write a disagreement: which repositories, which are faithful (a LIST), which
   declarations are unfaithful (repo:decl), the deciding book sentence VERBATIM (at most 600 characters), your reasoning, severity, a concrete
   fix${GATE.witness_required ? ', and a WITNESS object -- required in this mode' : ''}.
6. If you cannot locate the passage, set book_located=false and say why. Do not guess the book's wording.
7. Stamp the record: schema_version ${SCHEMA_VERSION}, run_id \`${A.run_id || ''}\`, source_id \`${C.source_id || ''}\`,
   source_sha \`${C.source_sha || ''}\`. The gate reads these off the file and refuses one that is missing any of them.${SELF_GATE}`,
    { label: 'work:' + t.item, phase: 'Work', schema: ADJ_SCHEMA, model: MODEL, effort: 'high' }),

  (adj, t) => {
    if (!adj) return null
    // `di` is the index in the UNFILTERED list: the id has to mean the same
    // thing here and in the findings reduction below.
    const content = (adj.disagreements || [])
      .map((d, di) => ({ d, di, did: didOf(adj.item, di) }))
      .filter(x => x.d.kind !== 'PAGE_ANCHOR')
    if (!CHALLENGE || !content.length) return { adj, challenges: [] }
    return parallel(content.flatMap(({ d, did }) => Array.from({ length: REFUTERS }, (_, k) => () => agent(`${CONTEXT}

A first-pass worker ruled on a disagreement for book item ${adj.item}:

  disagreement_id : ${did}
  concept   : ${d.concept}
  kind      : ${d.kind}
  repos     : ${(d.repos_involved || []).join(', ')}
  ruling    : faithful = ${JSON.stringify(d.faithful || [])}; unfaithful = ${(d.unfaithful_decls || []).join(', ') || '(none named)'}
  book quote: ${JSON.stringify(d.book_quote || '')}
  reasoning : ${d.reasoning}
${d.witness ? '  witness   : ' + JSON.stringify(d.witness) : ''}

YOUR JOB IS TO REFUTE THIS RULING. Read the declarations and the book passage yourself. Ask: is the "unfaithful" side
actually a convention delta that specialises correctly? Is the quote real, and does it say what the worker claims? Did
the worker misread a hypothesis? Is the "faithful" side itself wrong? Could an erratum explain the difference?
${d.witness ? 'Does the witness actually separate the two statements -- construct it and check.' : ''}

Default to REFUTED unless the ruling survives your own independent reading. Return the strongest counter-argument you
found even when the ruling stands. Copy disagreement_id through unchanged: it is what attaches your verdict to this
ruling rather than to another one on the same item.`,
      { label: 'chal' + k + ':' + did, phase: 'Challenge', schema: CHAL_SCHEMA, model: CHALLENGE_MODEL, effort: 'high' })
    ))).then(cs => ({ adj, challenges: cs.filter(Boolean) }))
  }
).then(rs => rs.filter(Boolean))

// Which source each item belongs to. A run is planned for one source, but the
// journal has to say so per row: a finding that names only `Exercise 1.3.19` is
// not addressable, because that label means a different item in another source.
const SRC_OF = ITEMS.reduce((m, t) => (m[t.item] = t.source_id || C.source_id, m), {})
const srcOf = it => SRC_OF[it] || C.source_id

// Verdicts attach by the id the refuter copied through, and a missing vote is
// MISSING, never a pass: without `survives_all` the challenge stage was
// decorative -- a finding both refuters killed still left this script as a
// finding, and no consumer of `cf-fleet-local-v2` applied refute-by-default.
const findings = results.flatMap(r => (r.adj.disagreements || []).map((d, di) => {
  const did = didOf(r.adj.item, di)
  const votes = r.challenges.filter(c => c.disagreement_id === did)
  const expected = (CHALLENGE && d.kind !== 'PAGE_ANCHOR') ? REFUTERS : 0
  return Object.assign(
    { item: r.adj.item, disagreement_id: did, run_id: A.run_id || '',
      source_id: srcOf(r.adj.item), source_sha: C.source_sha },
    d,
    {
      challenge_verdicts: votes.map(c => ({ survives: c.ruling_survives, classification: c.classification, evidence: c.evidence, corrected: c.corrected_ruling })),
      challenges_expected: expected,
      missing_votes: Math.max(0, expected - votes.length),
      survives_all: expected > 0 && votes.length === expected && votes.every(c => c.ruling_survives === true),
    })
}))

const attachedIds = {}
for (const f of findings) attachedIds[f.disagreement_id] = true
// Never discarded: a ruling that refers to nothing is evidence the refuter
// dropped the id, which is a defect in this run, not in the finding.
const unattachedChallenges = results.flatMap(r => r.challenges
  .filter(c => !attachedIds[c.disagreement_id])
  .map(c => ({ item: r.adj.item, disagreement_id: c.disagreement_id || '', concept: c.concept || '',
               survives: c.ruling_survives, evidence: c.evidence || '' })))
if (unattachedChallenges.length) {
  log('WARNING: ' + unattachedChallenges.length + ' challenge ruling(s) name no disagreement of their item ' +
      'and are NOT attached (the refuter dropped disagreement_id); see unattached_challenges')
}

const located = results.filter(r => r.adj.book_located).length
const gated = results.filter(r => r.adj.gate && r.adj.gate.passed).length
const challenged = findings.filter(f => f.challenges_expected > 0).length
const survived = findings.filter(f => f.survives_all).length
log('items done: ' + results.length + '/' + ITEMS.length + '; book located: ' + located +
    '; self-gate passed: ' + gated + '; findings: ' + findings.length +
    '; challenged: ' + challenged + '; survived all refuters: ' + survived)

const doneItems = results.map(r => r.adj.item)
const remaining = ITEMS.filter(t => doneItems.indexOf(t.item) === -1).map(t => t.item)
if (remaining.length) log('NOT done (requeue these): ' + remaining.length + ' items')

return {
  schema: 'cf-fleet-local-v2',
  schema_version: SCHEMA_VERSION,
  mode: MODE,
  run_id: A.run_id || '',
  source_id: C.source_id,
  source_sha: C.source_sha,
  summary: {
    items_in: ITEMS.length,
    items_done: results.length,
    book_located: located,
    self_gate_passed: gated,
    findings: findings.length,
    challenged,
    survived_all: survived,
    unattached_challenges: unattachedChallenges.length,
    by_kind: findings.reduce((m, f) => (m[f.kind] = (m[f.kind] || 0) + 1, m), {}),
    by_severity: findings.reduce((m, f) => (m[f.severity] = (m[f.severity] || 0) + 1, m), {}),
    remaining,
  },
  findings,
  unattached_challenges: unattachedChallenges,
  quotes: results.map(r => ({ item: r.adj.item, source_id: srcOf(r.adj.item), quote: r.adj.book_passage_quote, located: r.adj.book_located })),
  coverage: results.map(r => ({ item: r.adj.item, source_id: srcOf(r.adj.item), notes: r.adj.coverage_notes })),
  gate_reports: results.map(r => ({ item: r.adj.item, source_id: srcOf(r.adj.item), gate: r.adj.gate || null })),
  // This substrate dies mid-run (the session limit killed batch 1 at 129/225).
  // The journal is the durable artifact; findings are only real once the
  // out-of-tree gate has passed them, which happens here:
  next: {
    note: 'Workflow scripts cannot write files. Save this return value, then run the post-hoc harvest -> gate -> requeue loop.',
    // These are the REAL command lines; `cf harvest` takes the run id
    // positionally, `--journal <wf_* directory>`, and `--gate` (there is no
    // --from-journal, no --run and no --verify).
    harvest: 'python3 scripts/cf.py harvest ' + (A.run_id || '<run-id>') + ' --journal <transcriptDir> --gate',
    gate: 'python3 scripts/gate/verify_findings.py --gate-config ' + (GATE.config || '<run>/gate.local.json') + ' --root <workspace>/corpus/' + (A.run_id || '<run-id>') + ' --findings-dir .   # the gate OUT OF TREE',
    requeue: 'python3 scripts/cf.py plan-run --preset <preset> --run-id ' + (A.run_id || '<run-id>') + ' --force   # rebuilds tasks and args; re-run this script for the items listed in summary.remaining',
  },
}
