# Review brief: pull request 14 (M0a: cost tool, receipts, calibration registry)

Branch `m0a-cost-and-receipts`, head 253fb77, base master 8122f86. It implements
milestone M0a of `docs/proposals/pdf-to-companion.md` (sections 4.1 and 5.1) and the
cost-tool design of issue 11: a run's cost is recovered from the agents' Claude Code
transcripts, whose assistant messages carry `message.model` and `message.usage`
(`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`); harvest writes priced totals into `RECEIPT.json`; prices
live in `workspace.yaml` under `budget.prices`; the Opus 5 list prices are $5 input,
$25 output, $6.25 cache write and $0.50 cache read per million tokens.

Check out the branch (`git fetch origin m0a-cost-and-receipts && git checkout
m0a-cost-and-receipts`), read the whole diff against master (`git diff
master...HEAD`), CLAUDE.md, CONTRIBUTING.md, SKILL.md, the tests and
`tests/data/m0a/README.md`.

Review adversarially and read-only: do not edit tracked files, do not commit to or
push this branch. For every candidate finding, try to refute it yourself first.

Lenses:

1. Correctness of `scripts/cost/`. Build tiny transcripts and Codex rollout logs
   under /tmp and probe: a response split over several content-block lines with
   identical usage (must count once); two different responses with equal usage
   (must count twice); a message without usage; an unknown model (reported as
   unpriced, never priced); a Codex thread whose cumulative counters decrease or
   restart; `cached_input_tokens` larger than `input_tokens`; a log with no
   `turn_context`; an empty directory; a gzipped export. Recompute the s17b
   fixture total (`tests/data/m0a/s17b-usage.jsonl.gz`) with your own script and
   compare with the tool's 358.099941 and the recorded $358.10.
2. `scripts/pipeline/`: probe missing and extra fields, wrong sha lengths, negative
   numbers, booleans, a stage outside S0..S9, unknown keys at every level, and that
   nothing is written on refusal; check `sum_fleets` against a hand-built
   `run.yaml` and `RECEIPT.json`.
3. Integration: `cf harvest`, `cf tally` and `cf report` behave identically when no
   transcripts are given (compare outputs on a synthetic workspace, using the
   repo's fixtures or tests/e2e if runnable); the SKILL.md command index and every
   doc claim match the code; CHANGELOG, README, runbook and stage-map edits are
   accurate; no host name, user name or absolute path entered the repo (grep the
   diff for `/home/`, `/Users/`, IP addresses).
4. Repo rules: standard library only; the suite's exact `Ran N tests / OK` line;
   `python3 -m unittest tests.test_rf_docs tests.test_rf_scrub
   tests.test_rf_final_docs`; every commit carries the Co-Authored-By trailer.
5. The pull request body's claims (read it with `gh pr view 14` if `gh` works;
   otherwise the same claims are in CHANGELOG and docs): every number and statement
   checked against the diff and your runs.

Return, as your final message, a review record in GitHub markdown (no em-dashes,
headers at most level 3): a one-line verdict (merge, merge after fixes, or do not
merge) with the reason; what was checked; confirmed problems each with file:line,
the fix and a severity (blocker, should-fix, nit); refuted candidates worth
recording; what is left for a human. Also write the same record to
`docs/briefs/records/review-pr14-m0a.md` on a new branch `records/pr14` created
from master, commit it with the trailer `Co-Authored-By: Claude Opus 5.5
<noreply@anthropic.com>` and push that branch, so the record survives even if this
session's output is truncated.
