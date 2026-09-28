# Review brief: pull request 16 (M1: the source side, stages S0 and S1)

Branch `m1-source-side`, head 0755f1a, base master a55ccdd. It implements milestone
M1 of `docs/proposals/pdf-to-companion.md` (stages S0 and S1, sections 4.1 to 4.6
and the M1 acceptance criteria near line 1170) for the annotation format of
`docs/annotations.md` (sections 3, 5 and 7), `schema/annotations-v1.schema.json`
and `scripts/annotations/validate.py`.

Check out the branch (`git fetch origin m1-source-side && git checkout m1-source-side`),
read the whole diff against master, CLAUDE.md, CONTRIBUTING.md, SKILL.md,
`docs/pipeline.md`, `scripts/pipeline/{ingest,inventory,source_gates,recall}.py`,
the registry change (`edition_status`), the `cflib.inference` change,
`tests/test_m1_source_side.py`, `tests/test_m1_calibration.py` and
`tests/data/m1/`. The Tao PDF is not reachable from your environment (the sandbox's
proxy refuses it), so the calibration test skips there; its figures from the
maintainer's machine are in the comment on pull request 16 and copied here:

- numbered items vs measure_theory (page, kind, number): 407/407, recall 1.000;
  5 labels excluded before matching as equation numbers, "not a printed heading kind";
- numbered items vs tao-measure-lean Chapter 1 tags: 383/383, recall 1.000;
- passages vs tao-measure-lean "In-text, book p. N" tags: 51/222, recall 0.230,
  against the proposal's bar of 0.90; no passage file was supplied, so every hit
  is from the heuristic finder, and all 171 misses carry the reason "no passage on
  that page or beside its item";
- offset 16 at confidence 0.9959, 265 pages; every gate True; errata 453 entries,
  85 linked, 26 page-only, 10 out of scope, 332 with neither item nor page.

Review adversarially and read-only: do not edit tracked files, do not commit to or
push this branch. For every candidate finding, try to refute it yourself first.
The synthetic booklet (`tests/e2e/make_pdf.py`) builds with poppler and standard
tooling; use it for every probe.

Lenses:

1. S0 `cf ingest`: edition control by registry lookup (the draft PDF's sha
   `62d95658...` must be refused, an unknown sha must not be silently accepted),
   the physical-to-printed page offset and its confidence, what the receipt
   records and what it leaves as a pending manual gate. Probe: a PDF with no
   printed page numbers, one whose printed numbers restart, one with the offset
   planted differently on one page.
2. S1 `cf inventory`: for each numbered item, the id (`docs/annotations.md` 5.2),
   the page span (does it really run to the page before the next heading, and what
   happens at the last item of the scope, at a chapter boundary, and when two
   headings share a page), the `TextQuoteSelector` (32 characters of context each
   side, the 12-word `exact` cap applied before the id is minted, a heading whose
   text occurs twice on its page), the erratum items and their `same_as` links, the
   passage file (a quote not on its page must be rejected and listed), the
   heuristic finder's provenance and `relations.near`. Probe each with a variation
   of the booklet and report what the code does.
3. The gates in `scripts/pipeline/source_gates.py`: each must fail closed. Plant
   one defect per gate (a missing slot, a non-contiguous numbering without a signed
   reason, a selector whose exact text is not on the page, a duplicate id, an
   unplaced heading, a non-canonical document, an erratum quote that is not on the
   errata page) and confirm each is reported and blocks the receipt.
4. `scripts/pipeline/recall.py` and the calibration: does numbered matching test
   what the proposal says, `(page, kind, number)` with the offset applied, and could
   it over-count (matching by number alone, ignoring the page, counting an item
   found on the wrong page as a hit)? Is the exclusion of the 5 equation labels the
   only exclusion, and is it justified by the truth data rather than by the code's
   convenience (read the truth files under `tests/data/m1/` and the exclusion code
   side by side)? Does passage matching "by page or by neighbour" accept anything
   that should not count as a hit? State whether the 0.230 passage figure is the
   deterministic finder's honest ceiling as documented, or whether a bug in the
   finder or the matcher depresses it; say which In-text tags the finder should
   have caught by its own rules and did not, if any.
5. Repo rules: standard library only (PyYAML optional, as on master); no absolute
   paths, host names or user names in the diff (grep for `/home/`, `/Users/`, IP
   addresses); the SKILL.md command index rows; the docs match the code (every
   command line, flag and field in `docs/pipeline.md` exists); every commit carries
   the Co-Authored-By trailer; the suite's exact `Ran N tests / OK` line with and
   without PyYAML; the docs tests.
6. The pull request body's claims and the calibration comment's claims: every
   number checked against the code and the data. Say plainly whether merging S0
   and S1 with the passage bar recorded as not yet met is sound, or whether
   something in the branch must change first.

Return, as your final message, a review record in GitHub markdown (no em-dashes,
headers at most level 3): a one-line verdict (merge, merge after fixes, or do not
merge) with the reason; what was checked; confirmed problems each with file:line,
the fix and a severity (blocker, should-fix, nit); refuted candidates worth
recording; what is left for a human. Also write the same record to
`docs/briefs/records/review-pr16-m1.md` on a new branch `records/pr16` created from
master, commit it with the trailer `Co-Authored-By: Claude Opus 5.5
<noreply@anthropic.com>` and push that branch.
