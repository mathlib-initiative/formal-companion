# Review brief: pull request 15 (M3: companion exporters and the tag review)

Branch `m3-exporters`, head b4cafb0, base master 8122f86. It implements milestone
M3 of `docs/proposals/pdf-to-companion.md` (sections 2.8 and 5.4) for the
annotation format of `docs/annotations.md` (sections 3, 5, 6, 7 and 10),
`schema/annotations-v1.schema.json` and `scripts/annotations/validate.py`.

Check out the branch (`git fetch origin m3-exporters && git checkout m3-exporters`),
read the whole diff against master, CLAUDE.md, CONTRIBUTING.md, SKILL.md,
`docs/exporters.md`, `tests/test_export.py`, `tests/test_review_tags.py` and
`tests/data/m3/README.md`. The reference companions (private repositories
mathlib-initiative/measure_theory at 00a2438 and mathlib-initiative/tao-measure-lean
at f158509) may not be reachable from your environment; if a clone works, use it,
otherwise review from the shipped reference documents
`tests/data/m3/*.annotations.json.gz` and the synthetic tests, and say which.

Review adversarially and read-only: do not edit tracked files, do not commit to or
push this branch. For every candidate finding, try to refute it yourself first.

Lenses:

1. The exporters under `scripts/annotations/export/`: read each module end to end;
   check that every record they emit obeys the format's invariants, the
   named-declaration rule and the key-link rules; validate both reference documents
   with `python3 scripts/annotations/validate.py <doc> --canonical`; run
   `cf review-tags` on both and compare with the figures the docs and tests claim;
   probe the synthetic tests' inputs with variations (a tag with a sub-part range, a
   region with quads, a refinement entry with an empty key list, a declaration the
   ledger lacks, a private declaration, an anonymous example with a declared tag,
   a page outside the source's range) and report what the exporters do.
2. Faithfulness to the prototypes: the branch claims record-for-record
   reproduction with three deliberate differences; confirm from the tests and the
   documents that no fourth difference slipped in (diff the reference documents at
   the input commit 7adc3be against the regenerated ones at b4cafb0 by id and
   field and classify every difference).
3. `cf review-tags`: does each check test what its name says (attribution gap,
   key on a structure member, borrowed key, hidden spelling, adjudicated gap);
   could it miss the defects the audits found (section 1.7 items keyed only to
   page anchors before PR 17 of measure_theory; the borrowed-key remarks of its
   PR 19; the `In-text proof` spellings of tao-measure-lean before its PR 12)?
   Plant one of each in a small document and check it is reported.
4. Repo rules: standard library only; no absolute paths, host names or user names
   (grep the diff for `/home/`, `/Users/`, IP addresses); the SKILL.md command
   index rows; the docs match the code; every commit carries the Co-Authored-By
   trailer; the suite's exact `Ran N tests / OK` line and the docs tests.
5. The pull request body's claims (`gh pr view 15` if `gh` works; otherwise
   CHANGELOG and `docs/exporters.md` carry the same claims): every number checked.

Return, as your final message, a review record in GitHub markdown (no em-dashes,
headers at most level 3): a one-line verdict (merge, merge after fixes, or do not
merge) with the reason; what was checked; confirmed problems each with file:line,
the fix and a severity (blocker, should-fix, nit); refuted candidates worth
recording; what is left for a human. Also write the same record to
`docs/briefs/records/review-pr15-m3.md` on a new branch `records/pr15` created from
master, commit it with the trailer `Co-Authored-By: Claude Opus 5.5
<noreply@anthropic.com>` and push that branch.
