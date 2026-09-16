export const meta = {
  name: 'cf-fleet-local',
  description: 'Local fallback fleet for small runs (<100 items): one worker per book item with a self-run quote gate, then two refuters per content ruling',
  whenToUse: 'Only when a run is too small to be worth the server (tend + agentbox). Build args with `cf plan-run --local` / `cf run --local`. Hundreds of items belong on the server: this substrate is capped at min(16, CPUs-2) concurrent agents and dies on the subscription session limit mid-run.',
  phases: [
    { title: 'Work', detail: 'one agent per item: read the book, compare the statements, self-run the quote gate' },
    { title: 'Challenge', detail: 'two independent refuters per content-level ruling', model: 'opus' },
  ],
}

// Ported from: recovered/b174aa13_L5600_Workflow.txt (the batch-1
// three-way-faithfulness-differential fleet: 199 agents, 129 of 225 items done
// before the session limit killed it).
// Changes the record forces:
//   * inputs come from `args` -- repos, book paths, conventions and the gate
//     command were constants in the original;
//   * the worker SELF-GATES: a Workflow script has no filesystem access, so the
//     quote gate cannot run "inside the script after each agent" (the design's
//     original wording). The worker runs the campaign's own gate command on its
//     own draft and reports the result; the authoritative gate runs afterwards,
//     out of tree, at harvest.
//   * the return value carries the post-hoc recovery recipe (harvest_journal ->
//     gate -> requeue), because this substrate is expected to die mid-run: the
//     journal is the durable artifact, not the script's return value.

const A = args || {}
const C = A.campaign || {}
const ITEMS = A.items || []
const REPOS = A.repos || []
const MODE = A.mode || 'differential'
const GATE = A.gate || {}
const CHALLENGE = A.challenge !== false
const MODEL = A.model || undefined      // omit -> inherit the session model; set 'opus' for adjudication-grade work
const CHALLENGE_MODEL = A.challenge_model || 'opus'

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

const CONTEXT = `Lean 4 formalizations of ${C.book_title || C.book_id} are on this machine:
${repoLines}

READ-ONLY. Do NOT edit any file in any repository, do not run a build, do not change git state. You produce findings only.

${MODE_BRIEF[MODE] || MODE_BRIEF.differential}

DATA
- Statement tables (one JSON row per declaration, with file:line and the signature):
${REPOS.map(r => '    ' + r.name + ': ' + r.statements).join('\n')}
${A.pairs ? '- Paired rows for this item: grep -F \'"item": "<ITEM>"\' ' + A.pairs : ''}
- The book: ${C.book_layout_txt} (pdftotext of the pinned edition, sha256 ${C.book_sha}; pages separated by form feed)
  and one file per page at ${C.book_pages_dir}/pNNN.txt (PDF page = printed page + ${C.pdf_page_offset}).
  Locate the item (e.g. grep -n 'Exercise 1.2.4') and READ THE ACTUAL PASSAGE; never work from memory of the book.
${C.errata ? '- The published errata: ' + C.errata + '. A statement that differs from the printed text may be following an erratum.' : ''}

CONVENTION deltas are NOT faithfulness bugs. Per repository they are listed above; in addition, never report: a
strictly MORE general statement that specialises to the book's (record it, mark CONVENTION_ONLY, note "strictly
stronger"); bundled vs unbundled hypotheses; naming; one book item split across several declarations; an exercise
deliberately left as a permanent sorry.

WHAT IS A CONTENT DELTA: different hypotheses (missing, extra, weaker, stronger); different generality that does not
specialise correctly; a weaker or different conclusion; different quantifier scope; a wrong endpoint or normalisation
convention; a vacuous or trivially-true statement; a "definition" that is not the book's definition.

KINDS you may report: ${(GATE.kinds || ['CONTENT', 'VACUITY', 'SUSPECTED_ERRATA', 'COVERAGE']).join(', ')}.
${(GATE.kinds || []).indexOf('PAGE_ANCHOR') === -1 ? 'PAGE_ANCHOR is NOT one of them: page anchors are compared mechanically against the pinned edition, not by you.' : ''}
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
book_sha matches ${C.book_sha}. Fix whatever it rejects and run it again. Report the command you ran and its final
output in the \`gate\` field. If you cannot make a finding pass the gate, DROP the finding and say so in coverage_notes
-- a finding that cannot be evidenced does not exist. The authoritative gate runs again, out of tree, at harvest.` : ''

const ADJ_SCHEMA = {
  type: 'object',
  required: ['item', 'book_located', 'matches', 'disagreements', 'coverage_notes'],
  properties: {
    item: { type: 'string' },
    book_located: { type: 'boolean', description: 'you found and read the actual passage in the book text' },
    book_passage_quote: { type: 'string', description: 'VERBATIM 1-3 sentences from the book (machine-verified)' },
    matches: { type: 'array', items: { type: 'object', required: ['concept', 'decls', 'verdict'], properties: {
      concept: { type: 'string' },
      decls: { type: 'array', items: { type: 'object', required: ['repo', 'decl'], properties: {
        repo: { type: 'string' }, decl: { type: 'string' } } } },
      verdict: { type: 'string', enum: ['AGREE', 'CONVENTION_ONLY', 'CONTENT_DELTA', 'SINGLE_SOURCE_OK', 'SINGLE_SOURCE_DELTA', 'NO_DEFECT_FOUND'] },
      note: { type: 'string' },
    } } },
    disagreements: { type: 'array', items: { type: 'object',
      required: ['concept', 'kind', 'repos_involved', 'faithful', 'book_quote', 'reasoning', 'severity'],
      properties: {
        concept: { type: 'string' },
        kind: { type: 'string', enum: ['CONTENT', 'VACUITY', 'PROXY', 'SUSPECTED_ERRATA', 'COVERAGE'] },
        repos_involved: { type: 'array', items: { type: 'string' } },
        faithful: { type: 'array', items: { type: 'string' }, description: 'every repo whose statement IS faithful here (a LIST, possibly empty)' },
        unfaithful_decls: { type: 'array', items: { type: 'string' }, description: 'repo:decl for each side judged unfaithful' },
        book_quote: { type: 'string', description: 'VERBATIM from the book -- the sentence that decides it' },
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
  required: ['concept', 'ruling_survives', 'evidence'],
  properties: {
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
   Quote 1-3 sentences VERBATIM into book_passage_quote -- this is verified mechanically, so copy exactly.
3. Match declarations by mathematical concept. One book item often contains several concepts and one repository may
   split it differently. Unmatched declarations go in coverage_notes, not disagreements.
4. For each matched concept decide AGREE / CONVENTION_ONLY / CONTENT_DELTA (or SINGLE_SOURCE_OK / SINGLE_SOURCE_DELTA /
   NO_DEFECT_FOUND in the one-repository modes). Be precise about what differs.
5. For every content-level delta write a disagreement: which repositories, which are faithful (a LIST), which
   declarations are unfaithful (repo:decl), the deciding book sentence VERBATIM, your reasoning, severity, a concrete
   fix${GATE.witness_required ? ', and a WITNESS object -- required in this mode' : ''}.
6. If you cannot locate the passage, set book_located=false and say why. Do not guess the book's wording.${SELF_GATE}`,
    { label: 'work:' + t.item, phase: 'Work', schema: ADJ_SCHEMA, model: MODEL, effort: 'high' }),

  (adj, t) => {
    if (!adj) return null
    const content = (adj.disagreements || []).filter(d => d.kind !== 'PAGE_ANCHOR')
    if (!CHALLENGE || !content.length) return { adj, challenges: [] }
    return parallel(content.flatMap((d, di) => [0, 1].map(k => () => agent(`${CONTEXT}

A first-pass worker ruled on a disagreement for book item ${adj.item}:

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
found even when the ruling stands.`,
      { label: 'chal' + k + ':' + adj.item + '#' + di, phase: 'Challenge', schema: CHAL_SCHEMA, model: CHALLENGE_MODEL, effort: 'high' })
    ))).then(cs => ({ adj, challenges: cs.filter(Boolean) }))
  }
).then(rs => rs.filter(Boolean))

const findings = results.flatMap(r => (r.adj.disagreements || []).map(d => Object.assign(
  { item: r.adj.item, run_id: A.run_id || '', book_sha: C.book_sha },
  d,
  { challenge_verdicts: r.challenges
      .filter(c => c.concept === d.concept || r.challenges.length <= 2)
      .map(c => ({ survives: c.ruling_survives, classification: c.classification, evidence: c.evidence, corrected: c.corrected_ruling })) }
)))

const located = results.filter(r => r.adj.book_located).length
const gated = results.filter(r => r.adj.gate && r.adj.gate.passed).length
log('items done: ' + results.length + '/' + ITEMS.length + '; book located: ' + located +
    '; self-gate passed: ' + gated + '; findings: ' + findings.length)

const doneItems = results.map(r => r.adj.item)
const remaining = ITEMS.filter(t => doneItems.indexOf(t.item) === -1).map(t => t.item)
if (remaining.length) log('NOT done (requeue these): ' + remaining.length + ' items')

return {
  schema: 'cf-fleet-local-v2',
  mode: MODE,
  run_id: A.run_id || '',
  book_sha: C.book_sha,
  summary: {
    items_in: ITEMS.length,
    items_done: results.length,
    book_located: located,
    self_gate_passed: gated,
    findings: findings.length,
    by_kind: findings.reduce((m, f) => (m[f.kind] = (m[f.kind] || 0) + 1, m), {}),
    by_severity: findings.reduce((m, f) => (m[f.severity] = (m[f.severity] || 0) + 1, m), {}),
    remaining,
  },
  findings,
  quotes: results.map(r => ({ item: r.adj.item, quote: r.adj.book_passage_quote, located: r.adj.book_located })),
  coverage: results.map(r => ({ item: r.adj.item, notes: r.adj.coverage_notes })),
  gate_reports: results.map(r => ({ item: r.adj.item, gate: r.adj.gate || null })),
  // This substrate dies mid-run (the session limit killed batch 1 at 129/225).
  // The journal is the durable artifact; findings are only real once the
  // out-of-tree gate has passed them, which happens here:
  next: {
    note: 'Workflow scripts cannot write files. Save this return value, then run the post-hoc harvest -> gate -> requeue loop.',
    harvest: 'python3 scripts/cf.py harvest --from-journal <transcriptDir>/journal.jsonl --run ' + (A.run_id || '<run-id>'),
    gate: 'python3 scripts/cf.py harvest --run ' + (A.run_id || '<run-id>') + ' --verify   # runs the gate OUT OF TREE and assigns finding_id',
    requeue: 'python3 scripts/cf.py plan-run --resume ' + (A.run_id || '<run-id>') + '   # rebuilds args for the items above that are missing or gate-rejected',
  },
}
