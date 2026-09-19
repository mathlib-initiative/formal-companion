export const meta = {
  name: 'cf-adjudicate',
  description: 'Adjudicate routed faithfulness findings for one companion: three lenses, a reconciler, two opposing skeptics, blind definition readers and a completeness critic',
  whenToUse: 'After `cf route` has produced an UNCHALLENGED routed file, or whenever findings must be turned into ledger rows. Build args with `cf adjudicate --companion <name> --emit-args`.',
  phases: [
    { title: 'Findings — lenses', detail: 'book-literal, Lean-semantics and convention-policy readings per finding', model: 'opus' },
    { title: 'Findings — reconcile', detail: 'one reconciler per finding decides between the lenses', model: 'opus' },
    { title: 'Findings — skeptics', detail: 'two opposing skeptics attack each reconciled verdict', model: 'opus' },
    { title: 'Definitions — blind readers', detail: 'two independent readers compare each key definition body to the book', model: 'opus' },
    { title: 'Definitions — reconcile', detail: 'merge reader disagreements', model: 'opus' },
    { title: 'Uncovered definitions', detail: 'inventory what stands in for book definitions with no native definition', model: 'opus' },
    { title: 'Assessments', detail: 'optional systemic assessments supplied by the workspace', model: 'opus' },
    { title: 'Completeness critic', detail: 'what is missing or inconsistent across all results', model: 'opus' },
  ],
}

// Ported from: recovered/adam-faithfulness-adjudication-wf_60a87e92-dd5.js.
// Generalised as the design requires:
//   * every path, convention list and hard-wired Group-C prompt is now an `args`
//     field (the original embedded the measure_theory repo, its encoding
//     conventions and three Adam-specific detector prompts in the script);
//   * findings arrive as DATA with a stable `finding_id`, so the result links
//     back to the corpus by id instead of by a 22-character label prefix;
//   * `faithful` is a list, not an enum;
//   * dissent below the contested threshold is returned as well as the flag,
//     because the 0.6-confidence skeptic on Exercise 1.3.12 was dropped by hand;
//   * every agent runs on opus (the batch-1 Fable-default runs lost votes to
//     session limits).
// Inputs are built by `python3 scripts/cf.py adjudicate --companion <name> --emit-args`
// and contain workspace paths only -- never a session scratchpad path.

const A = args || {}
const W = A.workspace || {}
const SOURCES = W.sources || []
// A companion is audited against a LIST of sources; every finding row names the
// one it read. `srcOf` is how an agent gets from that id to a path it can open.
const srcOf = id => SOURCES.filter(s => s.source_id === id)[0] || SOURCES[0] || {}
const sourceLines = SOURCES.map(s =>
  '  ' + s.source_id + ' -- ' + (s.title || s.source_id) + (s.edition ? ' (' + s.edition + ')' : '') +
  '\n    text: ' + s.text + '  (sha256 ' + s.source_sha + ')' +
  '\n    pages: ' + s.pages_dir + '/pNNN.txt   (PDF page = printed page + ' + s.page_offset + ')' +
  // an errata page whose URL came from the audited repo and that nobody
  // confirmed is NOT authoritative: the prompt says so instead of naming it
  '\n    errata: ' + (s.errata || s.errata_withheld || '(none)')).join('\n')
const COMP = A.companion || {}
const PEERS = A.peers || []
const FINDINGS = A.findings || []
const DEFS = A.definitions || []
const UNCOVERED = A.uncovered_definitions || []
const ASSESSMENTS = A.assessments || []
const THRESHOLD = typeof A.contested_threshold === 'number' ? A.contested_threshold : 0.7
const MODEL = A.model || 'opus'

const peerLines = PEERS.map(p => '  ' + p.name + ' = ' + p.repo).join('\n')

const COMMON = `
You are adjudicating FAITHFULNESS -- statement- and definition-level fidelity to the book -- for the Lean 4
repository "${COMP.name}" (clone: ${COMP.repo}${COMP.commit ? ', pinned at ' + COMP.commit : ''}), a companion to
${COMP.sources && COMP.sources.length > 1 ? 'the sources listed below' : ((SOURCES[0] || {}).title || 'the source listed below')}. Proofs are machine-checked and out of scope: only what is STATED or DEFINED matters.
The owner wants faithfulness bugs, not style bugs.

READ-ONLY. Never edit a file in any repository, never run a build (no lake), never run a git command that changes
state. Use absolute paths; the session working directory may move.

Resources:
- The SOURCES this companion is audited against. Every finding row carries a source_id: open THAT source, never
  another, and any conclusion about pagination is about that edition only.
${sourceLines}
  Grep the text for the item label (e.g. "Exercise 1.2.23"). Check each source's errata for every item you rule on;
  a deviation from the printed text may be the companion following the erratum.
  Page anchors in this companion follow the source ${COMP.anchor_source || (SOURCES[0] || {}).source_id},
  which is not necessarily an edition the repository ships.
- The companion's statement table (one JSON row per declaration): ${COMP.statements}
${peerLines ? '- Peer formalizations of the same book, for comparison only:\n' + peerLines : ''}

HOUSE CONVENTIONS of this companion (these are CONVENTION, never CONTENT):
${COMP.conventions || '(none supplied -- treat only universally-recognised encoding choices as conventions)'}

Verdict categories:
- CONFIRMED_CONTENT: read with the conventions above, the Lean statement asserts something weaker, differently
  hypothesised, or different from the book's claim such that the book's claim is NOT recoverable by unfolding the
  conventions plus routine steps; or a main clause of the book item is missing.
- ATTRIBUTION_GAP: a faithful statement exists in the repo (possibly an anonymous \`example\` or an unattributed
  theorem) but the declaration attributed to the item does not state the item; the fix is naming, not mathematics.
- CONVENTION: the difference is entirely an encoding choice, a degenerate-case convention or the book's own standing
  assumption, and the book's claim is recoverable by routine unfolding -- STATE THE UNFOLDING EXPLICITLY.
- ERRATA: the book's printed text is wrong and the companion follows the erratum (cite the errata entry).
- REFUTED: the finding's factual premise is wrong (misread Lean, misread the book, wrong page, wrong edition).
- UNSURE: you could not decide with the sources available; say exactly what would decide it.

Severity: high = mathematically different theorem or a missing main clause; medium = weaker but the same theorem
family; low = trivial excluded case, naming or attribution. Fix effort: trivial (<10 lines), small (<60), medium, large.
Always cite evidence: the exact book sentence(s) with PDF page number, and the exact Lean file:line and the binder or
clause at issue. A false positive costs a human hour, and about 40% of first-pass findings in this pipeline were wrong
-- always by calling a house convention a content bug.
${A.notes ? '\nCampaign notes:\n' + A.notes : ''}
`

const LENS = {
  book: `LENS = BOOK-LITERAL. Establish precisely what the book claims for this item: quantifiers, hypotheses, the
domain of every object, which cases are included (degenerate intervals, d = 0, unbounded sets), and any standing
convention from the surrounding definitions or the section opening. Read the surrounding page(s), not just the quoted
line, and read the errata. Only at the END compare with the Lean statement.`,
  lean: `LENS = LEAN-SEMANTICS. Open the Lean declaration(s) and read them with their definitions unfolded (follow every
repo definition they mention). Establish exactly what is asserted, including implicit typeclass facts. Search the same
module and its neighbours (grep) for companion declarations or anonymous \`example\`s that state the missing part or the
general case. Only at the END compare with the book.`,
  policy: `LENS = CONVENTION-VS-CONTENT POLICY. Your job is the classification itself. For each clause the finding says
differs, decide whether the book's claim is recoverable from the Lean statement by routine unfolding of the house
conventions (CONVENTION) or not (CONTENT). Write the recovery argument explicitly when you claim CONVENTION; name the
concrete lost case or lost clause when you claim CONTENT. Be strict in both directions.`,
}
const LENS_KEYS = ['book', 'lean', 'policy']

const VERDICT_ENUM = ['CONFIRMED_CONTENT', 'ATTRIBUTION_GAP', 'CONVENTION', 'ERRATA', 'REFUTED', 'UNSURE']

const LENS_SCHEMA = {
  type: 'object',
  properties: {
    item: { type: 'string' },
    verdict: { type: 'string', enum: VERDICT_ENUM },
    severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
    confidence: { type: 'number' },
    book_claim: { type: 'string', description: 'what the book literally asserts, with PDF page cites' },
    lean_claim: { type: 'string', description: 'what the Lean declaration(s) assert, with file:line cites' },
    delta: { type: 'string', description: 'the precise difference, or why there is none' },
    errata_relevant: { type: 'string' },
    fix: { type: 'string' },
    fix_effort: { type: 'string', enum: ['none', 'trivial', 'small', 'medium', 'large'] },
  },
  required: ['item', 'verdict', 'severity', 'confidence', 'book_claim', 'lean_claim', 'delta', 'fix', 'fix_effort'],
}

const RECONCILE_SCHEMA = {
  type: 'object',
  properties: {
    item: { type: 'string' },
    verdict: { type: 'string', enum: VERDICT_ENUM },
    severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
    confidence: { type: 'number' },
    rationale: { type: 'string', description: 'self-contained justification with book and Lean cites; resolves lens disagreements explicitly' },
    lens_disagreement: { type: 'string' },
    recovery_argument: { type: 'string', description: 'REQUIRED for CONVENTION: the routine unfolding that recovers the book claim' },
    refutation_text: { type: 'string', description: 'if REFUTED/CONVENTION/ERRATA: the paragraph to send back to the run that produced the finding' },
    pr_plan: { type: 'string', description: 'if CONFIRMED_CONTENT/ATTRIBUTION_GAP: the concrete change (declarations, files, new statement)' },
    fix_effort: { type: 'string', enum: ['none', 'trivial', 'small', 'medium', 'large'] },
    pr_recommended: { type: 'boolean' },
  },
  required: ['item', 'verdict', 'severity', 'confidence', 'rationale', 'fix_effort', 'pr_recommended'],
}

const SKEPTIC_SCHEMA = {
  type: 'object',
  properties: {
    overturned: { type: 'boolean' },
    confidence: { type: 'number' },
    argument: { type: 'string', description: 'concrete evidence (book page quotes, Lean file:line) for or against the verdict' },
    proposed_verdict: { type: 'string', enum: VERDICT_ENUM },
  },
  required: ['overturned', 'confidence', 'argument', 'proposed_verdict'],
}

function findingBlock(f) {
  return [
    'finding_id : ' + f.finding_id,
    'item       : ' + f.item,
    'kind       : ' + f.kind + '   severity(first pass): ' + f.severity + '   run: ' + (f.run_id || '(unknown)'),
    'concept    : ' + (f.concept || ''),
    'faithful   : ' + JSON.stringify(f.faithful || []),
    'named decls: ' + JSON.stringify(f.unfaithful_decls || []),
    'book quote : ' + JSON.stringify(f.book_quote || ''),
    'reasoning  : ' + (f.reasoning || ''),
    'suggested  : ' + (f.suggested_fix || ''),
    f.witness ? 'witness    : ' + JSON.stringify(f.witness) : '',
    f.decls && f.decls.length ? 'declaration bodies:\n' + JSON.stringify(f.decls, null, 1) : '',
    f.peer_rows ? 'peer rows (comparison only):\n' + JSON.stringify(f.peer_rows, null, 1) : '',
  ].filter(Boolean).join('\n')
}

log('Adjudicating ' + FINDINGS.length + ' findings, ' + DEFS.length + ' definition items, ' +
    UNCOVERED.length + ' uncovered definitions for ' + COMP.name)
if (!FINDINGS.length && !DEFS.length && !UNCOVERED.length) {
  log('nothing to adjudicate -- check that `cf adjudicate --emit-args` found routed rows')
}

// ---------------- findings: lenses -> reconciler -> two opposing skeptics ----------------
const findings = await pipeline(
  FINDINGS,
  (f, _orig, i) => parallel(LENS_KEYS.map(l => () =>
    agent(`${COMMON}
${LENS[l]}

YOUR FINDING (routed against ${COMP.name}; rule on whether the named declaration is unfaithful as claimed):
${findingBlock(f)}

Return the structured verdict.`,
      { label: 'F' + i + ':' + l + ':' + f.item, phase: 'Findings — lenses', schema: LENS_SCHEMA, model: MODEL, effort: 'high' })
  )).then(ls => ls.map((v, k) => v && Object.assign({ lens: LENS_KEYS[k] }, v)).filter(Boolean)),

  (lenses, f, i) => agent(`${COMMON}
ROLE = RECONCILER for finding ${f.finding_id} (${f.item}). Three independent lens readings are below. Re-read the book
page(s) and the Lean declaration(s) yourself wherever the lenses disagree or cite different facts; do not average --
decide. Produce the final verdict, a self-contained rationale, and either the refutation paragraph
(REFUTED/CONVENTION/ERRATA, with the recovery argument spelled out when the verdict is CONVENTION) or the concrete
change plan (CONFIRMED_CONTENT/ATTRIBUTION_GAP).

THE FINDING:
${findingBlock(f)}

LENS READINGS:
${JSON.stringify(lenses, null, 1)}`,
    { label: 'F' + i + ':reconcile:' + f.item, phase: 'Findings — reconcile', schema: RECONCILE_SCHEMA, model: MODEL, effort: 'high' })
    .then(r => ({ lenses, reconciled: r })),

  (prev, f, i) => parallel([
    () => agent(`${COMMON}
ROLE = SKEPTIC (direction: argue the finding IS a genuine CONTENT bug). A reconciler ruled on the finding below. Try to
overturn a CONVENTION/ERRATA/REFUTED verdict by exhibiting a concrete lost case, lost clause or misreading; if the
verdict is already CONFIRMED_CONTENT/ATTRIBUTION_GAP, check that its severity is not understated. Read the sources
yourself. If you cannot find concrete evidence, say overturned=false -- and still record your best counter-argument.

THE FINDING:
${findingBlock(f)}

RECONCILED VERDICT:
${JSON.stringify(prev.reconciled, null, 1)}`,
      { label: 'F' + i + ':skeptic-content:' + f.item, phase: 'Findings — skeptics', schema: SKEPTIC_SCHEMA, model: MODEL, effort: 'high' }),
    () => agent(`${COMMON}
ROLE = SKEPTIC (direction: argue the finding is NOT a content bug). A reconciler ruled on the finding below. Try to
overturn a CONFIRMED_CONTENT/ATTRIBUTION_GAP verdict by showing the book's claim is recoverable by routine unfolding of
the house conventions, that the book's own conventions exclude the case, that the errata changes the statement, or that
a faithful named declaration already exists elsewhere in the repo; if the verdict is already CONVENTION/ERRATA/REFUTED,
check that the recovery argument is actually valid. Read the sources yourself. If you cannot find concrete evidence,
say overturned=false -- and still record your best counter-argument.

THE FINDING:
${findingBlock(f)}

RECONCILED VERDICT:
${JSON.stringify(prev.reconciled, null, 1)}`,
      { label: 'F' + i + ':skeptic-convention:' + f.item, phase: 'Findings — skeptics', schema: SKEPTIC_SCHEMA, model: MODEL, effort: 'high' }),
  ]).then(sk => {
    const roles = ['skeptic-content', 'skeptic-convention']
    const skeptics = sk.map((v, k) => v && Object.assign({ role: roles[k] }, v)).filter(Boolean)
    const dissent = skeptics.filter(s => s.overturned)
    const contested = dissent.some(s => typeof s.confidence === 'number' && s.confidence >= THRESHOLD)
    const nulls = sk.filter(v => !v).length
    return {
      finding_id: f.finding_id,
      item: f.item,
      kind: f.kind,
      severity: f.severity,
      concept: f.concept,
      run_id: f.run_id,
      unfaithful_decls: f.unfaithful_decls || [],
      lenses: prev.lenses,
      reconciled: prev.reconciled,
      skeptics: skeptics,
      dissent: dissent,          // recorded even below the contested threshold
      contested: contested,
      missing_votes: nulls,      // a null vote is missing, never a pass
    }
  })
).then(rs => rs.filter(Boolean))

// ---------------- definitions: two blind readers -> reconciler ----------------
const DEF_READ_SCHEMA = {
  type: 'object',
  properties: {
    label: { type: 'string' },
    book_definition: { type: 'string', description: 'the book definition restated clause by clause with PDF page cites' },
    per_definition: { type: 'array', items: { type: 'object', properties: {
      name: { type: 'string' },
      verdict: { type: 'string', enum: ['NATIVE_FAITHFUL', 'NATIVE_DEVIATION', 'PROXY_WITH_BRIDGE', 'PROXY_NO_BRIDGE', 'NOT_THIS_DEFINITION'] },
      body_summary: { type: 'string' },
      deviation: { type: 'string', description: 'exact clause mismatch if any, else empty' },
      mathlib_dependency: { type: 'string' },
      bridge_theorem: { type: 'string' },
    }, required: ['name', 'verdict', 'body_summary', 'deviation'] } },
    missing_from_repo: { type: 'string', description: 'clauses of the book definition with no counterpart' },
    overall: { type: 'string', enum: ['FAITHFUL', 'DEVIATION', 'PROXY'] },
    notes: { type: 'string' },
  },
  required: ['label', 'book_definition', 'per_definition', 'missing_from_repo', 'overall'],
}

const definitions = await pipeline(
  DEFS,
  (d, _o, i) => parallel([0, 1].map(k => () =>
    agent(`${COMMON}
ROLE = DEFINITION READER #${k + 1} (blind; another reader works independently). Definitions are viral: an unfaithful
definition poisons every theorem about it, so read at the clause level. For EACH definition below, restate what its
body actually defines (unfold repo helpers by opening the Lean file), then compare clause by clause with the book's
definition on its page: objects, quantifiers, endpoint and degenerate conventions, boundedness, dimension, value type.
Decide NATIVE_FAITHFUL / NATIVE_DEVIATION / PROXY_WITH_BRIDGE (defined via the standard library's rendition of the same
or a later concept, with an equality theorem in the repo -- name it) / PROXY_NO_BRIDGE / NOT_THIS_DEFINITION. Also list
clauses of the book definition that no listed definition covers.
${k === 1 ? 'Reader #2: additionally check the book text two pages before and after for conventions the definition inherits.' : 'Reader #1: additionally verify each body against the exact Lean source rather than the supplied snippet.'}

ITEM:
${JSON.stringify(d, null, 1)}`,
      { label: 'D' + i + ':reader' + (k + 1) + ':' + (d.label || d.item || ''), phase: 'Definitions — blind readers', schema: DEF_READ_SCHEMA, model: MODEL, effort: 'high' })
  )).then(rs => rs.filter(Boolean)),

  (readers, d, i) => agent(`${COMMON}
ROLE = DEFINITION RECONCILER for ${d.label || d.item}. Two blind readers reported below. Where they disagree on any
per-definition verdict or on missing clauses, open the Lean source and the book page yourself and decide; where they
agree, spot-check one clause. Output the merged report in the same schema; put your resolution notes in "notes" and
name any clause either reader missed.

ITEM:
${JSON.stringify(d, null, 1)}

READER REPORTS:
${JSON.stringify(readers, null, 1)}`,
    { label: 'D' + i + ':reconcile:' + (d.label || d.item || ''), phase: 'Definitions — reconcile', schema: DEF_READ_SCHEMA, model: MODEL, effort: 'high' })
    .then(r => ({ item: d.item || d.label, readers, merged: r }))
).then(rs => rs.filter(Boolean))

// ---------------- book definitions with no native definition ----------------
const UNCOVERED_SCHEMA = {
  type: 'object',
  properties: {
    label: { type: 'string' },
    book_definition_summary: { type: 'string' },
    classification: { type: 'string', enum: ['NATIVE_DEF_EXISTS_UNCURATED', 'BRIDGE_THEOREM_ONLY', 'LIBRARY_STANDIN_ONLY', 'ABSENT'] },
    repo_native_def: { type: 'string', description: 'fully qualified name + file:line of a native definition, if any' },
    bridge_theorem: { type: 'string' },
    library_standin: { type: 'string', description: 'the library notion used in place of the book definition' },
    same_meaning: { type: 'string', description: 'on the nose, up to a standard theorem, or different? explain' },
    evidence: { type: 'string' },
  },
  required: ['label', 'book_definition_summary', 'classification', 'library_standin', 'same_meaning', 'evidence'],
}

const uncovered = await parallel(UNCOVERED.map((u, i) => () =>
  agent(`${COMMON}
ROLE = INVENTORY. The item below is a numbered book definition for which the companion's attribution contains no
repo-native definition (only theorems, or nothing). Read the book page to learn exactly what is defined. Then search the
repo (grep -rn under ${COMP.repo}) for (a) any definition or structure that defines that concept natively even if
unattributed, (b) any theorem bridging a library notion to the book's formulation, (c) which library notion the repo's
theorems use in its place. Classify and report; be exact about names and file:line. This is inventory for a
faithfulness report, so state plainly when the book's definition is simply the library's.

ITEM:
${JSON.stringify(u, null, 1)}`,
    { label: 'U' + i + ':' + (u.label || u.item || ''), phase: 'Uncovered definitions', schema: UNCOVERED_SCHEMA, model: MODEL, effort: 'medium' })
)).then(rs => rs.filter(Boolean))

// ---------------- optional workspace-supplied systemic assessments ----------------
const ASSESS_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    verdict: { type: 'string' },
    assessment: { type: 'string', description: 'self-contained assessment with cites' },
    change_plan: { type: 'array', items: { type: 'object', properties: {
      file: { type: 'string' }, line: { type: 'integer' }, current: { type: 'string' }, proposed: { type: 'string' },
    }, required: ['file', 'current', 'proposed'] } },
    risks: { type: 'string' },
    estimated_lines: { type: 'integer' },
    pr_recommended: { type: 'boolean' },
  },
  required: ['title', 'verdict', 'assessment', 'risks', 'pr_recommended'],
}

const assessments = await parallel(ASSESSMENTS.map((a, i) => () =>
  agent(`${COMMON}
ROLE = ASSESSOR: ${a.title}

${a.prompt}

Do not edit files. Return the structured assessment.`,
    { label: 'A' + i + ':' + (a.title || '').slice(0, 24), phase: 'Assessments', schema: ASSESS_SCHEMA, model: MODEL, effort: 'high' })
)).then(rs => rs.filter(Boolean))

// ---------------- completeness critic ----------------
const summary = {
  companion: COMP.name,
  findings: findings.map(f => ({
    finding_id: f.finding_id, item: f.item, verdict: f.reconciled && f.reconciled.verdict,
    severity: f.reconciled && f.reconciled.severity, contested: f.contested,
    dissent: f.dissent.map(d => ({ role: d.role, confidence: d.confidence, proposed: d.proposed_verdict })),
  })),
  definitions: definitions.map(d => ({ label: d.merged && d.merged.label, overall: d.merged && d.merged.overall, missing: d.merged && d.merged.missing_from_repo })),
  uncovered: uncovered.map(u => ({ label: u.label, classification: u.classification, standin: u.library_standin })),
  assessments: assessments.map(a => ({ title: a.title, verdict: a.verdict })),
}

const critic = await agent(`${COMMON}
ROLE = COMPLETENESS CRITIC. Below is the summary of an adjudication run over routed findings, key-definition readings
and uncovered book definitions for this companion. Ask what is missing: (a) contradictions between groups (a definition
ruled FAITHFUL whose item a finding ruled CONTENT); (b) verdicts whose rationale rests on a claim you can quickly
falsify by opening the book page or the Lean file -- do it for the three you find most suspicious; (c) numbered book
definitions in scope that appear in neither list (compare against ${SOURCES.map(s => s.items_json).join(', ')}); (d) anything a
faithfulness report for the repo owner would be embarrassed to have missed. Return plain text, most important first,
each point with evidence.

SUMMARY:
${JSON.stringify(summary, null, 1)}`,
  { label: 'critic', phase: 'Completeness critic', model: MODEL, effort: 'high' })

const byVerdict = {}
for (const f of findings) {
  const v = (f.reconciled && f.reconciled.verdict) || 'MISSING'
  byVerdict[v] = (byVerdict[v] || 0) + 1
}
log('adjudicated ' + findings.length + '/' + FINDINGS.length + ' findings; contested ' +
    findings.filter(f => f.contested).length + '; verdicts ' + JSON.stringify(byVerdict))

// Shape consumed by `python3 scripts/cf.py adjudicate --transcribe <this file>`.
return {
  schema: 'cf-adjudicate-v2',
  companion: COMP.name,
  sources: SOURCES.map(s => ({ source_id: s.source_id, source_sha: s.source_sha })),
  contested_threshold: THRESHOLD,
  counts: {
    findings_in: FINDINGS.length,
    findings_out: findings.length,
    contested: findings.filter(f => f.contested).length,
    by_verdict: byVerdict,
  },
  findings,
  definitions,
  uncovered,
  assessments,
  critic,
}
