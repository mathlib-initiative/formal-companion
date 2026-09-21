export const meta = {
  name: 'cf-verify-claims',
  description: 'Adversarially verify every claim in a report draft against the primary sources, with an independent second vote on the numbers and a completeness critic',
  whenToUse: 'Before any handoff, weekly report or PR body derived from this workspace goes out. Build args with `cf report` (it writes `reports/verify-claims.args.json` unless `--no-verify-args`).',
  phases: [
    { title: 'Verify', detail: 'one verifier per claim lens re-derives the draft claims from the sources', model: 'opus' },
    { title: 'Numbers', detail: 'an independent second vote recomputes every number from the corpus', model: 'opus' },
    { title: 'Critique', detail: 'completeness and audience check', model: 'opus' },
  ],
}

// Ported from: the v1 campaign's verify-audit-summary workflow (six hard-wired
// lenses over the Tao workspace; it found 19 wrong claims out of 173), which
// does not travel with this repo.
// Generalised: the draft, the source list and the lens list all arrive in
// `args`, the numbers lens is generated from the workspace's own tally commands
// rather than being written by hand, and every counting bias observed so far --
// they ALL ran toward "cleaner" -- is stated to the verifier.

const A = args || {}
const DRAFT = A.draft || ''
const SOURCES = A.sources || []
const LENSES = A.lenses || []
const NUMBERS = A.numbers || {}
const EXTRA = A.extra || {}
const MODEL = A.model || 'opus'

const CLAIMS_SCHEMA = {
  type: 'object',
  properties: {
    claims: { type: 'array', items: { type: 'object', properties: {
      claim: { type: 'string' },
      verdict: { type: 'string', enum: ['CORRECT', 'WRONG', 'UNVERIFIABLE'] },
      evidence: { type: 'string', description: 'a file path with line numbers, or the exact command run and its output' },
      correction: { type: 'string' },
    }, required: ['claim', 'verdict', 'evidence'] } },
    notes: { type: 'string' },
  },
  required: ['claims'],
}

const CRIT_SCHEMA = {
  type: 'object',
  properties: {
    missing: { type: 'array', items: { type: 'string' } },
    overdetailed: { type: 'array', items: { type: 'string' } },
    misleading: { type: 'array', items: { type: 'string' } },
    jargon: { type: 'array', items: { type: 'string' } },
    suggested_tldr: { type: 'string' },
  },
  required: ['missing', 'overdetailed', 'misleading', 'jargon', 'suggested_tldr'],
}

const sourceLines = SOURCES.map(s => '- ' + (s.label ? s.label + ': ' : '') + s.path + (s.note ? ' -- ' + s.note : '')).join('\n')

const COMMON = `You are a read-only adversarial verifier. A DRAFT report about a Lean formalization faithfulness audit
is below. Your job is to try to REFUTE every claim in your assigned lens by re-deriving it from the primary sources.
Do not trust the draft; do not trust prose in handoff notes when a computable source exists -- compute. Never edit any
file, never run a build, never run a git command that changes state. Use absolute paths; the working directory may move.

Verdict rules: CORRECT only if you re-derived it and it matches. WRONG if your evidence contradicts it -- then give the
exact corrected value or wording in \`correction\`. UNVERIFIABLE if no source lets you check it (say what you tried).
Every claim you list carries evidence: a file path with line numbers, or the exact command you ran and its output. Be
exhaustive for your lens: list every distinct claim in it, not a sample.

Known bias to assume: in this project every counting error so far ran toward "cleaner" -- fewer open findings, fewer
rows, a shorter list. Weight your scepticism accordingly. A number is only as good as the corpus it was computed from:
check the corpus is complete (a receipt, a file count) before trusting a count taken from it.

PRIMARY SOURCES
${sourceLines || '(none supplied -- say so and mark everything UNVERIFIABLE)'}

=== DRAFT (the text under test) ===
${DRAFT}
=== END DRAFT ===
${EXTRA.appendix ? '\n=== APPENDIX UNDER TEST ===\n' + EXTRA.appendix + '\n=== END APPENDIX ===\n' : ''}
YOUR LENS: `

const numbersLens = {
  key: 'numbers-independent',
  prompt: `NUMBERS, INDEPENDENT SECOND VOTE. Another verifier is checking the same figures; do NOT read their work.
Recompute every number in the draft yourself, with your own script, from the corpus and the ledger:
${(NUMBERS.commands || []).map(c => '  $ ' + c).join('\n') || '  (no recompute commands supplied -- write your own over the sources above)'}
Paste each script and its output as evidence. Check specifically:
${(NUMBERS.checks || []).map(c => '  - ' + c).join('\n') || '  - every count, ratio and denominator in the draft'}
Also verify that any "nothing is open" style claim ships with the one-line command that recomputes it, and that the
command actually prints what the draft says it prints. If a count is taken over a corpus, verify the corpus is complete
(receipt count == file count) before agreeing with the number.`,
}

const lensList = LENSES.concat(NUMBERS.enabled === false ? [] : [numbersLens])

log('verifying a ' + DRAFT.length + '-character draft with ' + lensList.length + ' lenses + a critic')

const results = await parallel([
  ...lensList.map((l, i) => () => agent(COMMON + l.prompt, {
    label: 'verify:' + (l.key || i),
    phase: l.key === 'numbers-independent' ? 'Numbers' : 'Verify',
    schema: CLAIMS_SCHEMA,
    model: MODEL,
    effort: 'high',
  })),
  () => agent(COMMON + (A.critic_prompt || `COMPLETENESS AND AUDIENCE CRITIC. Read the draft and skim the sources
(another agent re-derives the numbers; you do not). Report: (1) MISSING -- facts the intended reader needs that the
draft omits or under-states, including what decision the reader is being asked to make; (2) OVERDETAILED -- passages
that belong in a PR body rather than this report, quoting the sentences to cut; (3) MISLEADING -- sentences a careful
reader could take the wrong way: over-claims, ratios without denominators, claims about other people's repositories
stronger than the sources support, or findings presented as confirmed when the ledger says unchallenged; (4) JARGON --
internal names that need a gloss or should be dropped; (5) a suggested five-sentence TL;DR leading with the outcome.
Quote the draft sentence you mean.`), {
    label: 'critic', phase: 'Critique', schema: CRIT_SCHEMA, model: MODEL, effort: 'high',
  }),
])

const verifiers = results.slice(0, lensList.length)
const critique = results[lensList.length]
const wrong = [], unverifiable = [], correct = []
verifiers.forEach((r, i) => {
  const key = lensList[i].key || String(i)
  if (!r) { log('verifier ' + key + ' returned nothing -- counted as MISSING, not as a pass'); return }
  for (const c of (r.claims || [])) {
    const row = Object.assign({ lens: key }, c)
    if (c.verdict === 'WRONG') wrong.push(row)
    else if (c.verdict === 'UNVERIFIABLE') unverifiable.push(row)
    else correct.push(row)
  }
})
log('claims: ' + correct.length + ' correct, ' + wrong.length + ' wrong, ' + unverifiable.length + ' unverifiable')

return {
  schema: 'cf-verify-claims-v1',
  lenses: lensList.map(l => l.key),
  missing_lenses: verifiers.map((r, i) => r ? null : (lensList[i].key || String(i))).filter(Boolean),
  wrong,
  unverifiable,
  correct,
  correct_count: correct.length,
  notes: verifiers.map((r, i) => ({ lens: lensList[i].key || String(i), notes: (r && r.notes) || '' })),
  critique,
}
