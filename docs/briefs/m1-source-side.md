# Implementation brief: milestone M1, the source side (S0 ingest and pin, S1 inventory)

Work on a new branch `m1-source-side` created from master (8122f86). Commit as you
go with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
push to `origin m1-source-side`. Do not merge and do not open a pull request.

## Read first

- `docs/proposals/pdf-to-companion.md` sections 0, 2.1 (S0), 2.2 (S1), 3.3 (gates),
  4.1, 4.2, 4.6 (copyright: a selector's `exact` text is capped; full statement
  text stays in the workspace and never enters the document) and 5.2 (M1's
  acceptance tests).
- `docs/annotations.md` (items, `TextQuoteSelector` locations, `pages`, passage
  ids in section 5.2, `relations`, the invariants, the canonical form),
  `schema/annotations-v1.schema.json`, `scripts/annotations/validate.py`,
  `scripts/annotations/ids.py`.
- CLAUDE.md, CONTRIBUTING.md, SKILL.md (the command index rule), README.md.
- `scripts/cfcmd/onboard.py` and `scripts/sources/` (source preparation:
  `pdftotext` extraction, page offset inference, `items.json`, errata fetch),
  `scripts/cflib/inference.py` (offset confidence, `MIN_ITEM_COMPLETENESS`, the
  `item_completeness` groups), `scripts/cflib/items.py` (the item grammar),
  `registry/sources.yaml` (the Tao entry: sha256 starting 64498329, 265 pages,
  offset 16, the canonical PDF URL), `tests/` (layout; the rules in
  `tests/test_rf_docs.py` and `tests/test_rf_scrub.py`), `tests/e2e/make_pdf.py`
  (the synthetic booklet with planted passages).
- Two branches that are not merged yet: `origin/m0a-cost-and-receipts` has
  `scripts/pipeline/` (the `pipeline.yaml` loader and the stage-receipt writer)
  and `origin/m3-exporters` has `tests/data/m3/*.annotations.json.gz`, the
  exported annotation documents of the two Tao companions, which carry every
  numbered item and passage of Chapter 1 with kind, number, pages and quotes: the
  ground truth for the acceptance tests. Do not merge those branches; copy the
  fixtures you need under `tests/data/m1/` (documented in a README there) and, for
  the receipt, write it in the section 4.1 shape with a small local writer if the
  module is absent on master, noting that it will be replaced by the M0a module.

## Build

1. `cf ingest --pdf FILE --scope chapters=1 [--errata URL] [--licence NOTE]
   --pipeline ID` (S0): pins the PDF (sha256, bytes, page count, page sizes, text
   layer extractor and version), infers the printed page offset and its confidence,
   looks the sha up in `registry/sources.yaml` and refuses a known draft or
   superseded edition without `--override-edition`, fetches errata raw when a URL
   is given, initialises the pipeline directory (`pipelines/<id>/pipeline.yaml`,
   `doc.v0.json` with `sources[]`, `provenance[]` and a `companion` block: create
   the generated repository as an empty git repository under the workspace and
   commit once so `commit` is a real 40-hex id; `repo` is `local/<name>`), writes
   the S0 stage receipt, and keeps the PDF and page texts workspace-local (never in
   git, never in the document). The spot-check agent step is out of scope here:
   record it as a manual gate in the receipt.
2. `cf inventory --pipeline ID` (S1): from the page texts, the numbered items
   (structured ids from the item grammar, `pages {first, last, basis}`, a
   `TextQuoteSelector` on the heading with prefix and suffix, capped `exact`),
   erratum items with `relations.same_as` to the items they name, and the
   deterministic gates: slot completeness, slot contiguity per section (every
   numbered slot from 1 to the section's highest number present or listed with a
   reason), the exact selector check (every `exact` occurs verbatim on its own page
   under NFKC and whitespace collapse, no length floor), unique ids; then
   `doc.v1.json` validated with `validate.py --canonical` and the S1 receipt.
   Passages (unnumbered in-text results) are found by agents in the proposal;
   here implement the deterministic part only: a `--passages FILE` input in the
   document's item shape (so a later agent stage can supply them) plus the
   `relations.near` linking, and a heuristic passage finder for displayed claims
   that the text marks (for example lines starting with `Proof.`, `Remark`, or
   an unnumbered bold heading) clearly labelled as heuristic in its provenance.
3. Calibration against the Tao ground truth, as a test that skips without the
   PDF: download the PDF from the registry URL if `CF_TAO_PDF` is unset and the
   network allows (the file is public), run ingest and inventory, and measure:
   offset 16 at confidence at least 0.9; recall of the numbered items of
   `tests/data/m1/measure_theory.annotations.json.gz` matched on (page, kind,
   number); recall of the numbered items of the tao-measure-lean document in
   Chapter 1; every miss printed with its page and label. Report the figures in
   your final message; the proposal's bars are 99 percent for numbered items and
   90 percent for passages, and a miss is a finding, not a failure to hide.
4. Synthetic tests that run everywhere: ingest and inventory on
   `tests/e2e/make_pdf.py`'s booklet (it has planted passages), the gates' failure
   cases (a missing slot, a selector that does not match its page, a draft edition
   sha, a duplicate id), and the receipt shape.
5. Docs: SKILL.md rows, README, CHANGELOG (Unreleased), a short `docs/pipeline.md`
   that says what S0 and S1 do today and what is deferred (the spot-check agent,
   the passage enumerators and hunters, the sandbox).

## Rules

Standard library only, plus `pdftotext` and `pdfinfo` as external tools (install
poppler-utils if the environment lacks them and say so). House style: plain prose,
no em-dashes, no host names or user names, no absolute paths in the repo. Run
`python3 -m unittest discover -s tests` before every push and report the exact
`Ran N tests / OK` line. If the network refuses the PDF, ship everything else and
say so.

## Return

Files added and changed, the commands, the calibration figures with every miss
listed, the suite result, and anything you could not do.
