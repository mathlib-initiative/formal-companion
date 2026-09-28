# Rebuild brief: pull request 16 (M1) without the private data, with the review fixes

Branch `m1-source-side`, head 0755f1a, base master a55ccdd. The review record
`docs/briefs/records/review-pr16-m1.md` (branch `records/pr16`) gives the verdict
"merge after fixes" with one blocker: `tests/data/m1/*.annotations.json.gz` are
the private companions' documents (Lean signatures, snippets, verdicts, book
text), forbidden by CLAUDE.md section 1, and the blobs must leave history. Read
the record in full first. The maintainer's decision on its open question: the
calibration truth becomes a **derived table** with no quote, declaration or
verdict in it, which is a fact about the edition in the sense of CLAUDE.md
section 1 (like the registry's page maps).

Do this:

1. `git fetch origin master m1-source-side records/pr16` and create
   `m1-source-side-clean` from `origin/master`; bring the tree over with
   `git merge --squash origin/m1-source-side` (no commit yet); delete the two
   `.gz` files.
2. Write `scripts/pipeline/derive_truth.py`: given a companion's annotation
   document (path from the command line, maintainer-side, never committed), it
   writes `tests/data/m1/<companion>.truth.json` holding only, for numbered
   items, `{kind, number, page}` (first physical page) and, for passages,
   `{page, near}`; nothing else, and it refuses to write any other field. The
   two truth files cannot be produced here (no documents), so write the script
   and its unit test on a synthetic document, make `tests/test_m1_calibration.py`
   read the truth files and skip when they are absent, and rewrite
   `tests/data/m1/README.md` to say how the maintainer regenerates them. The
   maintainer will run the script and commit the two tables.
3. Apply the record's items 2 to 12, each with a regression test on the
   synthetic booklet where the record planted a probe: the failing S1 re-run
   must remove the stale `doc.v1.json` and receipt; a `--source-id` naming a
   registry entry whose sha differs must be refused, and an unknown sha must add
   a pending `edition` gate to the receipt's `manual` list with a warning; the
   last in-scope span and the scope span stop before the first out-of-scope
   chapter or section opening; an ambiguous heading is placed by heading shape or
   reported under `headings_placed`; a supplied passage is checked whole against
   its page before capping; passage recall is one-to-one with page-plus-quote
   overlap where the reference has a quote and page-only matches reported
   separately (the derived truth has no quotes, so on it the one-to-one page rule
   applies and the report must say so); the calibration download is opt-in
   (`CF_M1_DOWNLOAD=1`); dissenting offset pages reach the receipt; a duplicate
   supplied passage is listed; the `infer_counters` sentence and the `cf ingest`
   synopsis in `docs/pipeline.md` are corrected; the new environment variables
   are listed in CLAUDE.md section 7.
4. Commit in logical commits (registry and inference; ingest; inventory and
   gates; recall and calibration; docs), each with the trailer
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; no absolute path,
   host name or user name anywhere.
5. Run `python3 -m unittest discover -s tests` with the sandbox's injected
   `GIT_CONFIG_COUNT` and `GIT_CONFIG_KEY_*` unset, and again with PyYAML hidden;
   report both exact `Ran N tests` / `OK` lines.
6. Confirm with `git log --all --stat -- tests/data/m1` that no commit reachable
   from the new branch adds a `.gz`, then force-push it over `m1-source-side` on
   origin (`git push --force-with-lease origin m1-source-side-clean:m1-source-side`).

Final message: the new head sha and commit list, what was applied per item with
file:line, anything not applied and why, the suite lines, and the exact command
the maintainer runs to regenerate the two truth tables.
