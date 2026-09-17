export const meta = {
  name: 'cf-challenge',
  description: 'Refute-by-default adversarial challenge of unchallenged faithfulness findings: two independent refuters per finding, a finding survives only if both fail to refute it',
  whenToUse: 'Before any finding leaves the workspace directory. `cf route` refuses to emit an upstream report while a routed file is UNCHALLENGED; this is the run that changes that. Build args with `cf adjudicate --companion <name> --challenge-only --emit-args`.',
  phases: [
    { title: 'Challenge', detail: 'two independent opus refuters per finding', model: 'opus' },
  ],
}

// Ported from: recovered/challenge-batch1-findings-wf_c3f0e5d0-d65.js
// (identical to transcript b174aa13 L5953). Generalised:
//   * the three repo paths, the book path, the errata path and the convention
//     list were constants in the original and are `args` fields here;
//   * findings arrive with a stable `finding_id`; the original matched a
//     finding by "item + first 40 characters of the concept", which is what
//     made challenge<->finding linking fragile;
//   * the per-finding result carries the finding_id through, so
//     `cf adjudicate --transcribe --source challenge` can write ledger rows.
// The rule that must NOT be generalised away: DEFAULT TO REFUTED. A first-pass
// finding in this pipeline is wrong about 40% of the time, always by calling a
// house convention a content bug.

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
  '\n    errata: ' + s.errata).join('\n')
const COMP = A.companion || {}
const PEERS = A.peers || []
const FINDINGS = A.findings || []
const REFUTERS = typeof A.refuters === 'number' ? A.refuters : 2
const MODEL = A.model || 'opus'

const peerLines = PEERS.map(p => '  ' + p.name + ' = ' + p.repo).join('\n')

const CTX = `A first-pass adjudicator (UNCHALLENGED) ruled that the Lean 4 companion "${COMP.name}" (${COMP.repo}) is
UNFAITHFUL to ${(SOURCES[0] || {}).title || 'the source(s) listed below'} on a point. YOUR JOB IS TO REFUTE THAT RULING.

READ-ONLY: edit nothing, build nothing, change no git state.

Sources:
- The SOURCES, one block each. A finding names its source_id; read THAT text and no other -- an item number can sit
  on a different printed page in a different edition, so the wrong edition refutes nothing.
${sourceLines}
  READ THE ACTUAL PASSAGE -- never work from memory of the book.
- The companion's statement table: ${COMP.statements}
${peerLines ? '- Peer formalizations of the same item:\n' + peerLines : ''}
- Each source's published errata (listed above). An erratum may explain a deviation from the printed text; check it
  before ruling either side wrong.

CONVENTION deltas are NOT unfaithfulness. For this companion they are:
${COMP.conventions || '(none supplied)'}
Also never bugs: a strictly MORE general statement that specialises to the book's; bundled vs unbundled hypotheses;
naming; splitting one book item across several declarations; an exercise deliberately left as a permanent sorry.

Default to REFUTED unless the ruling survives your own independent reading of the Lean and of the book. Quote the
deciding book sentence VERBATIM. If you cannot check something, say so -- an unverifiable ruling does not survive.
${A.notes ? '\nCampaign notes:\n' + A.notes : ''}`

const SCHEMA = {
  type: 'object',
  required: ['finding_id', 'item', 'ruling_survives', 'evidence'],
  properties: {
    finding_id: { type: 'string', description: 'copy the finding_id you were given, unchanged' },
    item: { type: 'string' },
    ruling_survives: { type: 'boolean' },
    classification: { type: 'string', enum: ['CONTENT', 'VACUITY', 'CONVENTION', 'ERRATA', 'ATTRIBUTION', 'UNSURE'] },
    confidence: { type: 'number' },
    evidence: { type: 'string', description: 'file:line for the Lean, page + verbatim sentence for the book' },
    book_quote: { type: 'string' },
    corrected_ruling: { type: 'string' },
  },
}

function findingBlock(f) {
  return [
    'finding_id : ' + f.finding_id,
    'item       : ' + f.item,
    'kind       : ' + f.kind + '   severity: ' + f.severity + '   run: ' + (f.run_id || '(unknown)'),
    'concept    : ' + (f.concept || ''),
    'ruling     : faithful = ' + JSON.stringify(f.faithful || []) + '; unfaithful = ' +
      ((f.unfaithful_decls || []).join(', ') || '(none named)'),
    'book quote : ' + JSON.stringify(f.book_quote || ''),
    'reasoning  : ' + (f.reasoning || ''),
    f.witness ? 'witness    : ' + JSON.stringify(f.witness) : '',
    f.decls && f.decls.length ? 'declaration bodies:\n' + JSON.stringify(f.decls, null, 1) : '',
  ].filter(Boolean).join('\n')
}

log('Challenging ' + FINDINGS.length + ' findings with ' + REFUTERS + ' refuters each (' +
    FINDINGS.length * REFUTERS + ' agents)')

const results = await pipeline(
  FINDINGS,
  (f, _o, i) => parallel(Array.from({ length: REFUTERS }, (_, k) => () =>
    agent(`${CTX}

FINDING UNDER CHALLENGE (refuter #${k + 1} of ${REFUTERS}; you are working independently of the others):
${findingBlock(f)}

Read the statements and the book passage yourself, then rule. Copy finding_id through unchanged.`,
      { label: 'chal' + k + ':' + f.item, phase: 'Challenge', schema: SCHEMA, model: MODEL, effort: 'high' })
  )).then(vs => {
    const verdicts = vs.filter(Boolean)
    const missing = vs.length - verdicts.length
    return {
      finding_id: f.finding_id,
      item: f.item,
      kind: f.kind,
      severity: f.severity,
      concept: f.concept,
      run_id: f.run_id,
      unfaithful_decls: f.unfaithful_decls || [],
      // a null vote is MISSING, never a pass: an unanswered challenge cannot let a ruling survive
      survives_all: verdicts.length === vs.length && verdicts.length > 0 && verdicts.every(v => v.ruling_survives),
      missing_votes: missing,
      verdicts,
    }
  })
).then(rs => rs.filter(Boolean))

const survived = results.filter(r => r.survives_all).length
log('challenged ' + results.length + ' findings: ' + survived + ' survived, ' +
    (results.length - survived) + ' refuted or unresolved')

// Shape consumed by `cf adjudicate --transcribe <file> --source challenge`.
return {
  schema: 'cf-challenge-v2',
  companion: COMP.name,
  sources: SOURCES.map(s => ({ source_id: s.source_id, source_sha: s.source_sha })),
  challenged: results.length,
  survived,
  findings: results,
}
