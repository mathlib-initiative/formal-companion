# M1 reference data

The ground truth for milestone M1's calibration test
(`tests/test_m1_calibration.py`, docs/proposals/pdf-to-companion.md section
5.2): one **derived truth table** per Tao companion, about Chapter 1 of the
published edition (registry id `tao-measure-gsm126-published`, sha256
`64498329...`).

- `measure_theory.truth.json`
- `tao-measure-lean.truth.json`

A table holds only facts about the edition, like the registry's page maps:

- `numbered`: `{kind, number, page}` for each numbered item, `page` being the
  first physical page the companion places it on;
- `passages`: `{page, near}` for each passage, `near` being the ids of the
  numbered items it sits beside;
- a header: `format` (`cf-m1-truth/1`), `companion`, `source_sha256`.

No quote, title, declaration, signature, snippet or verdict: the companions'
annotation documents carry those and stay in the companions' repositories or
a workspace, never here (CLAUDE.md section 1, docs/annotations.md section 11).
`scripts/pipeline/derive_truth.py` refuses to write any other field.

## What each table checks

The two tables are not two independent ground truths for the numbered items.

- **tao-measure-lean** gets its pages from the docstring-tags exporter, which
  places each numbered item on the first line-start match of `Kind N` in the
  book's `pdftotext` layer (`locate_headings`, through `PdfText.headings` in
  `scripts/annotations/export/pdftext.py`). S1 finds headings with essentially
  the same regex on the same text layer (`_HEADING_SHAPE` in
  `scripts/pipeline/inventory.py`), so its numbered-item figure measures two
  heading regexes agreeing. It can catch a place where they differ (it caught
  the wrapped title of Exercise 1.2.11), not an error they share, such as a
  cross-reference at line start that precedes the real heading.
- **measure_theory** gets its pages from region geometry signed off against
  rendered pages, so its numbered-item figure is the independent check on S1,
  and it alone supports the 407/407 figure. Part of that geometry was first
  laid out from the text layer and then checked visually, so it is not wholly
  free of `pdftotext` either.

The passage rows of tao-measure-lean are where that companion chose to tag a
passage, not a list of the book's passages, which is one more reason the
passage figure is an upper bound on recovery and not a recall.

## Regenerating the tables

The maintainer exports each companion's annotation document (the M3
exporters), keeps it outside this repository, and runs from the repository's
root:

```sh
python3 scripts/pipeline/derive_truth.py <measure_theory document>.annotations.json.gz --companion measure_theory
python3 scripts/pipeline/derive_truth.py <tao-measure-lean document>.annotations.json.gz --companion tao-measure-lean
```

Each writes `tests/data/m1/<companion>.truth.json` for the registry's
`tao-measure-gsm126-published` sha (`--sha256` or `--source-id` names another
edition, `--out` another path) and prints what it left out: numbered items
whose kind the producer supplied, items whose kind or number is not a word and
a dotted number, and `near` ids that name no numbered item. Commit the two
tables, and nothing else from the export.

Without the tables the calibration test still runs the two stages on the PDF
and skips the recall figures. Because the tables carry no quotes, the passage
figure is matched one to one by page (or beside the item), which is an upper
bound on recovery; the report says so.
