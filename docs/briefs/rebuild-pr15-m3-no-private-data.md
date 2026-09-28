# Rebuild brief: pull request 15 (M3) without the private companions' data

Branch `m3-exporters`, head b39d03f, base master a55ccdd. The branch is complete
and reviewed (`docs/briefs/records/review-pr15-m3.md` on branch `records/pr15`,
fixes applied in b39d03f), but it ships files derived from the two private
companions under `tests/data/m3/`: `measure_theory.annotations.json.gz` and
`tao-measure-lean.annotations.json.gz` (3,039 and 1,530 Lean signatures, 542
snippets, 11 verdicts), the two `*.export-report.json`, `tml-site-pages.json`
and `tml-site-xref.json.gz`. CLAUDE.md section 1 forbids all of that here ("no
book text, no statement tables, no findings, no ledgers"; "Do not paste their Lean
source"), and `docs/annotations.md` section 11 says a document "lives in the
companion's repository or a workspace, never in this one". One commit (7adc3be)
also carries `/Users/...` machine paths in its message. Both must leave the
branch's history, not just its tip.

Do this:

1. `git fetch origin master m3-exporters` and create `m3-exporters-clean` from
   `origin/master`. Bring the branch's tree over with `git merge --squash
   origin/m3-exporters` (no commit yet).
2. Delete every file under `tests/data/m3/` except `README.md`. Rewrite that
   README: the reference documents and reports are maintainer-side, in a
   directory named by `CF_M3_REFERENCE_DIR`, produced by the exporters from the
   private companions; say exactly which files it must hold and how they are
   regenerated (`cf export ...` commands), and that no file derived from a
   private companion may be committed here.
3. Make every test that read those files (`tests/test_export.py`,
   `tests/test_review_tags.py`, any other) read them from `CF_M3_REFERENCE_DIR`
   and skip cleanly with a message when it is unset or a file is missing. The
   synthetic fixtures and tests must keep running unchanged. Add
   `CF_M3_REFERENCE_DIR` to CLAUDE.md section 7's list of environment variables.
   Update `docs/exporters.md`, CHANGELOG and SKILL.md wherever they say the
   reference documents ship in the tree.
4. Grep the whole tree for any other file derived from the companions (Lean
   signatures, declaration names in bulk, verdicts, page images, book quotes
   longer than an anchor) and report each; remove any you find under the same
   rule.
5. Commit in a few logical commits (exporters; tag review; tests and docs), each
   with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`,
   with no absolute path, host name or user name in any message or file.
6. Run `python3 -m unittest discover -s tests` (unset the sandbox's injected
   `GIT_CONFIG_COUNT` and `GIT_CONFIG_KEY_*` variables first) and report the exact
   `Ran N tests` / `OK` line; run it once more with PyYAML hidden.
7. Confirm with `git log --all --stat -- tests/data/m3` on the new branch that no
   commit reachable from it adds a `.gz` or export report, then force-push the
   new branch over `m3-exporters` on origin (`git push --force-with-lease origin
   m3-exporters-clean:m3-exporters`).

Final message: the new head sha and commit list, what each test that used the
references now does, the suite lines, and anything found in step 4.
