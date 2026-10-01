# M1 reference data

The ground truth for milestone M1's calibration test
(`tests/test_m1_calibration.py`, docs/proposals/pdf-to-companion.md section
5.2): one **derived truth table** per Tao companion, about Chapter 1 of the
published edition (registry id `tao-measure-gsm126-published`, sha256
`64498329...`).

- `measure_theory.truth.json`
- `tao-measure-lean.truth.json`

A table holds only item numbers, kinds and page numbers, no text:

- `numbered`: `{kind, number, page}` for each numbered item, `page` being the
  first physical page the companion places it on;
- `passages`: `{page, near}` for each passage, `near` being the ids of the
  numbered items it sits beside;
- a header: `format` (`cf-m1-truth/1`), `companion`, `source_sha256`.

No quote, title, declaration, signature, snippet or verdict: the companions'
annotation documents carry those and stay in the companions' repositories or
a workspace, never here (CLAUDE.md section 1, docs/annotations.md section 11).
`scripts/pipeline/derive_truth.py` refuses to write any other field.

The tao-measure-lean pages come from the same `pdftotext` heading match S1
uses, so only measure_theory is an independent check of S1's numbered items.

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
