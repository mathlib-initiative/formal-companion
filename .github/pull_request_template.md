<!--
The checklist a reviewer works through is in CONTRIBUTING.md, "Reviewing a
pull request". The first item is the one a review once skipped.
-->

## What changed and why

## Evidence

<!-- The commands you ran and their counts, on this head. Say how each fix's
test was confirmed to fail before it. -->

## Checklist

- [ ] **No private-companion data and no book text, in any commit.** Nothing
      from a private companion (Lean, declaration names, signatures,
      docstrings, snippets), nothing an exporter or a workspace wrote
      (annotation documents, export reports, site indexes, statements,
      findings, ledgers, reports), no book or errata text, page image, crop or
      PDF, and no absolute home path, host name, run id, session or turn id or
      `Claude-Session:` trailer. Synthetic text only. Checked with
      `CF_SCRUB_RANGE=origin/master..HEAD python3 -m pytest -q tests/test_rf_scrub.py`,
      and this description and my comments quote none of it either.
- [ ] Every fix has a test that fails before it.
- [ ] `python3 -m unittest discover -s tests` is green, and the counts above
      are from this head.
- [ ] Commit messages say what changed and why, and end with a
      `Co-Authored-By:` line if a model wrote the change.
