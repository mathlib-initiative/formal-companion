# Review brief: branch `m1-source-side-clean` (M1 rebuilt, second round)

Branch `m1-source-side-clean` on origin, at its current head, base master a55ccdd.
It replaces `m1-source-side` (0755f1a), whose review record is
`origin/records/pr16:docs/briefs/records/review-pr16-m1.md`; read that record
first. The rebuild removed the private documents, added
`scripts/pipeline/derive_truth.py` and the two derived tables under
`tests/data/m1/` (only `{kind, number, page}` and `{page, near}`), applied the
record's items 2 to 12 with probe tests, and fixed one more defect the
maintainer's calibration found: a heading whose parenthesised title wraps onto
the next line now counts as a heading shape, and a period followed by `)` does
not (`_HEADING_SHAPE` in `scripts/pipeline/inventory.py`, test
`test_a_wrapped_title_beats_a_closing_parenthesis_cross_reference`).

The maintainer's calibration on the Tao PDF at this head: numbered 407/407 and
383/383; passages one to one 19/222, reported as an upper bound because the
truth carries no quotes; every gate True. The PDF is not reachable from your
environment; the synthetic booklet (`tests/e2e/make_pdf.py`, needs poppler,
install it with apt if absent) is your instrument.

Review adversarially and read-only: do not edit tracked files, do not commit
to or push this branch. For every candidate finding, try to refute it first.

Lenses:

1. Each of the record's items 2 to 12: is the fix what the record asked for,
   does its probe test really fail on 0755f1a's code and pass now (check by
   reading both versions, `git show origin/m1-source-side:<file>`), and did the
   fix introduce a new failure mode? Pay attention to item 4 (spans stopping at
   an out-of-scope opening: what happens when the scope is chapter 1 and the
   text mentions "Chapter 2" mid-page, or when a section opening of the same
   chapter follows the last item), item 5 (the new heading shape: can a wrapped
   title of up to 200 characters swallow the next heading; can a line-start
   cross-reference followed by a plain period still win), item 6 (whole-quote
   check: normalisation, quotes spanning a line break), item 7 (one-to-one
   matching: greedy order, a reference passage with `near` but on another
   page).
2. `derive_truth.py`: confirm it can write nothing but the five allowed fields
   and refuses otherwise; that the two committed tables contain nothing else
   (no quote, declaration, verdict, title); that a table is a fact about the
   edition and not a copy of a companion's curation beyond item pages.
3. `tests/test_m1_calibration.py`: reads the tables, skips cleanly without the
   PDF, downloads only with `CF_M1_DOWNLOAD=1`; the bars only with
   `CF_M1_ENFORCE_BARS=1`; the report wording for the passage figure is honest.
4. Repo rules: standard library only (PyYAML optional); no absolute paths, host
   names or user names in the diff; no `.gz` under `tests/data/m1` in any
   commit reachable from the branch; SKILL.md rows; docs match code
   (`docs/pipeline.md`, CLAUDE.md section 7, `tests/data/m1/README.md`); every
   commit carries the Co-Authored-By trailer; the suite's exact `Ran N tests`
   line with and without PyYAML (unset the sandbox's `GIT_CONFIG_*` first).
5. The pull request body's claims (CHANGELOG and docs carry the same): every
   number checked.

Return, as your final message, a review record in GitHub markdown (no em-dashes,
headers at most level 3): a one-line verdict (merge, merge after fixes, or do
not merge) with the reason; what was checked; confirmed problems each with
file:line, the fix and a severity (blocker, should-fix, nit); refuted
candidates worth recording; what is left for a human. Also write the same
record to `docs/briefs/records/review-m1-clean.md` on a new branch
`records/m1-clean` created from master, commit it with the trailer
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and push that branch.
