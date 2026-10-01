# The PDF-to-companion pipeline: what is built

`docs/proposals/pdf-to-companion.md` designs ten stages, S0 to S9, that turn
one PDF into a Lean companion. This page says what exists today: the source
side of milestone M1, S0 (ingest and pin) and S1 (inventory), deterministic
parts only. Everything else in the proposal is still a proposal.

A pipeline is one directory of the workspace, `pipelines/<id>/`:

```
pipelines/<id>/
  pipeline.yaml                 scope, exercise policy (unset), consent, fleets
  doc.v0.json                   S0's checkpoint: sources, provenance, companion
  doc.v1.json                   S1's checkpoint: plus items and locations
  slot-reasons.yaml             optional: why a numbering slot is empty
  repo/                         the generated companion, its own git repository
  stages/S0.stage-receipt.json
  stages/S1.gate.json           every S1 gate's evidence, written even on failure
  stages/S1.stage-receipt.json
```

The PDF, its text and its pages stay in `sources/<source id>/`, which the
workspace's git ignores; `repo/` is ignored too, being a repository of its own.
Every checkpoint is written in canonical form and passes the vendored checker
(`PYTHONPATH=vendor python3 -m formal_companion_annotations.validate --canonical`)
with no warnings, or it is not written at all (`scripts/pipeline/document.py`).

## S0: `cf ingest`

```sh
cf ingest --pdf <file> --scope chapters=1 --pipeline <id> [--errata <url> | --no-errata]
          [--licence <note>] [--override-edition <reason>] [--offset <n>]
          [--source-id <id>] [--companion <name>]
```

- **Pins the PDF**: sha256, bytes, page count and page sizes (`pdfinfo`), the
  text layer's extractor and version (`pdftotext -v`), and the PDF's own
  metadata (title, dates) as `x_pdf_info`.
- **Prepares the source** with the same code `cf onboard` uses
  (`scripts/sources/prepare_source.py`): `text.txt`, `pages/`, `items.json`,
  `meta.json`. The page offset is inferred from the running heads; below
  confidence 0.9 the stage stops and names `--offset`, which is then stamped
  `offset_method: manual`. The pages whose running head votes for another
  offset are listed in the receipt (`checks.offset_confidence.dissenting_pages`,
  and `look_at_pages` on the pending spot check) and in the source's
  `x_offset`. The item kinds are inferred too: the frequent
  heading words first, then the rare ones whose every number fills a slot the
  numbering leaves open (`cflib.inference.slot_filling_kinds`); the source
  record's `x_kinds` says which came from where.
- **Checks the edition** against the registry by sha256. An edition marked
  `edition_status: draft` or `superseded` (`registry/README.md`) is refused
  unless `--override-edition "<reason>"` is given, and the reason is kept in
  the receipt. An unknown sha is recorded as unknown, with a warning and a
  pending `edition` gate in the receipt's `manual` list: the maintainer's
  sign-off must then name the edition. A `--source-id` that names a registry
  entry pinned to another sha is refused: a registered id is never stamped on
  another file.
- **Fetches the errata raw** when a URL is given or the registry knows one
  (`scripts/sources/fetch_errata.py`: curl, the HTML kept verbatim); the
  errata page becomes a second, `web`, source of the document. `--no-errata`
  records that none was fetched.
- `--source-id` names the source of a PDF the registry does not know (default:
  derived from the PDF's title), and `--companion` the generated companion
  (default: the pipeline id).
- **Creates the generated repository**, `repo/`, and commits once, so
  `companion.commit` is a real 40-hex id and `companion.repo` is
  `local/<name>`. It ignores `*.pdf`, and the gate checks it tracks none.
- Writes `pipeline.yaml` (scope as given, `exercise_policy` unset, no consent
  to send source text anywhere), `doc.v0.json` and the S0 receipt. The
  receipt's `input_doc_sha256` is the PDF's sha256: S0 has no input document.

A second `cf ingest` of the same PDF changes nothing. A different PDF or scope
under an existing pipeline id is refused.

## S1: `cf inventory`

```sh
cf inventory --pipeline <id> [--passages <file>] [--slot-reasons <file>] [--no-heuristic]
```

- **Numbered items** from the headings source preparation found, in scope:
  id `<kind>-<number>` (`docs/annotations.md` section 5.2), `pages {first,
  last, basis: physical}` running to the page before the next heading, and a
  location `<item>@pNNN` whose `TextQuoteSelector` anchors the heading as
  printed, with 32 characters of context each side.
- **The scope ends where the next chapter or section opens.** An in-scope
  item's span, and with it the scope span, stops before the first
  out-of-scope opening that follows it: a short line `Chapter N`, or a short
  line starting with a section number and a capitalised title (a section
  heading or the running head of a page in that section). With no such line
  before an out-of-scope heading, the item ends on its own page and the
  pending sign-off extends it. The gate report names the opening under
  `scope_stops`. Page-only errata and the heuristic finder use the same span.
- **An ambiguous heading** (preparation found its label at the start of more
  than one line) is placed on the occurrence with a heading's shape, a
  parenthesised title or a period after the number; if none has it, the
  heading is reported under `headings_placed` rather than guessed.
- **Erratum items**: every entry of the errata page that names an in-scope item
  (by the item grammar, `cflib.items`) becomes an item of the errata source
  linked to it by `relations.same_as`; an entry that names only an in-scope
  printed page is kept unlinked, for S3. Entries about other chapters, and
  entries naming neither an item nor a page (navigation), are counted in the
  gate report and left out.
- **Passages**: supplied with `--passages <file>`, a JSON list (or `{"items":
  [...]}`) in the document's item shape (`form: passage`, `kind`, `title`,
  `pages {first, basis}`, `quote {exact}`); each is accepted only when its
  whole quote occurs on its page, and is capped only after that check; the
  rest are listed as rejected. A record whose capped quote repeats an accepted
  one's is listed under `duplicate_passages`. A heuristic
  finder adds the unnumbered passages the text marks itself: a line starting
  `Proof.`, `Proof of ...`, `Remark.`, `Claim.`, `Example (...)` and similar.
  Its items carry `x_finder: heuristic` and its locations the provenance
  record `prov-s1-heuristic-passages`. Bold type does not survive `pdftotext`,
  so an unnumbered bold heading without such a word is not found.
- **`relations.near`** on every passage: the numbered item it follows in
  reading order.
- Every `exact` is capped at 12 words before an id is minted (proposal section
  4.6): full statement text stays in the workspace.

The gates, all fail-closed (`scripts/pipeline/source_gates.py`):

| gate | rule |
|---|---|
| slot completeness | at least 0.6 of the numbering's slots, pooled across kinds |
| slot contiguity | per section and per counter, every slot from 1 to the highest number present, or listed in `slot-reasons.yaml` (keyed `1.3.7`, or `Exercise 1.3.7` for one counter); a missing slot is reported with any line that prints it under another word |
| exact selector check | every `exact` occurs verbatim on its own page under NFKC and whitespace collapse, no length floor, no neighbouring page |
| unique ids | no id twice among items or locations |
| selector length cap | no `exact` over 12 words |
| headings placed | every in-scope heading is found on the page preparation gave it |
| validate `--canonical` | no problem and no warning |
| errata quotes | every erratum quote occurs in the rendered errata page |

Counters are read off the numbers (`cflib.inference.infer_counters`), greedily:
kinds are taken largest first, and each joins the first counter none of whose
numbers it repeats, so two kinds that share no number can still sit in
different counters. Tao numbers
exercises apart from everything else, so its sections have two counters, and a
gap in one cannot hide behind the other. Contiguity cannot see a missing
*last* slot of a section; that is what the hunters of the proposal are for.

When a gate fails, `stages/S1.gate.json` says why, no `doc.v1.json` or
receipt is written, and any `doc.v1.json` and S1 receipt an earlier run left
are removed, so no later stage can proceed on them. The receipt's gate result lists two manual gates as
pending: the maintainer's sign-off (scope, edition, item list, slot reasons)
and the planted-deletion recall calibration.

## Deferred

- The S0 image spot check (ten random pages and one planted shifted page). The
  receipt records it as a pending manual gate.
- The S1 agents: one passage enumerator per section, one missed-item hunter per
  chapter, a completeness critic, and the planted-deletion recall they are
  calibrated with. `--passages` is the input they will write to.
- The sandbox run root and its transfer manifest (proposal section 4.5), and
  the M0c sandbox these stages are meant to run in.
- The Lake template: the generated repository holds a README and a
  `.gitignore` until S5.
- Stages S2 to S9.

## Calibration

`tests/test_m1_calibration.py` runs both stages on Tao's published PDF,
Chapter 1, and measures the offset, the numbered-item recall against the two
Tao companions, the passage recall against the tao-measure-lean "In-text"
tags, and the errata link for Definition 1.5.11, printing every miss. The
ground truth is two derived tables in `tests/data/m1/` (`{kind, number, page}`
and `{page, near}` only, written by `scripts/pipeline/derive_truth.py`; the
directory's README says how to regenerate them); a missing table skips the
figures it feeds. It reads the PDF from `CF_TAO_PDF` or an earlier cached
download, downloads the copy the author publishes only with
`CF_M1_DOWNLOAD=1`, and skips otherwise.

Passage recall (`scripts/pipeline/recall.py`) is one to one: each of our
passages recovers at most one reference passage. A reference passage with a
quote needs one of ours on its page whose quote overlaps it (a shared run of
four words); a page-only match is listed apart and not counted. The derived
tables carry no quotes, so on them the one-to-one page rule (or beside the
same item, within a page) applies, the figure is an upper bound on recovery,
and the report says so. Measured that way on Chapter 1 of the published
edition, the figure is 19 of 222 passages (0.086), an upper bound; the test
prints it and writes it to `CF_M1_REPORT`, and asserts the 90% bar only under
`CF_M1_ENFORCE_BARS=1`. The figure first quoted for the M1 branch, 0.230 (51 of
222), was measured under a many-to-one page rule and counted page
coincidences: it is superseded, not a recall.
`tests/test_m1_source_side.py` covers the same stages on the synthetic booklet
of `tests/e2e/make_pdf.py`, built with its planted passages.
