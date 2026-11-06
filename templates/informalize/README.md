# Informalize templates

What `cf informalize` puts in an output repository's `informal/` (and its root), as
`docs/proposals/informalize.md` section 7 specifies, and the two formats a person edits.

## `runtime/`: copied byte for byte

`cf informalize sync` copies every file under `runtime/` to `repo/informal/` unchanged and
records it in `informal/GENERATED.json`; nothing is substituted into it, and the output
repository never edits it. It skips `__pycache__/` and `*.pyc`, which an import of a tool
from this checkout could leave here; the tools themselves write no bytecode.

| file | what it is |
|---|---|
| `fcmarkers.sty` | items, parts, `\leanref`, `\fcdefhyp`, `\fcref`, `\itemref`, `\fcinputsection`, and the position markers the tools read back from the `.aux` |
| `STYLE.md` | the rules every text is written to; `STYLE-project.md` adds the project's |
| `tools/build_pdf.sh` | the reproducible build into a directory of its own |
| `tools/canonical_pdf.sh` | the canonical PDF of stage I6: two clean builds compared, the markers, the checks, the proof sheets and `pdf-receipt.json` |
| `tools/extract_markers.py` | the boxes, fitted to the ink of a 288 dpi render: `markers.json` |
| `tools/check_items.py` | the build against `items.json`, `declarations.json` and `hypotheses.json` (rules 1 to 10) |
| `tools/proof_sheets.py` | the boxes drawn over the pages, and the checks a person would make by eye |
| `tools/fccommon.py` | what the three Python tools share, the two commands `build_pdf.sh` runs and the `pdf-receipt` one `canonical_pdf.sh` runs |
| `LICENSE-runtime` | the Apache License 2.0, which the files above are under; a copy of this repository's `LICENSE`, so that the output repository carries it whatever license its text has |

The tools are standard-library Python 3.11 and POSIX shell, run from the output
repository's root (`python3 informal/tools/<tool>.py`, `bash informal/tools/build_pdf.sh`),
and import nothing but their siblings. Poppler (`pdfinfo`, `pdftoppm`, `pdftotext`) is
needed by `extract_markers.py`, `proof_sheets.py` and `check_items.py` without
`--aux-only`; TeX (`pdflatex`, `latexmk`, `kpsewhich`) by `build_pdf.sh`.
`tests/test_informalize_runtime.py` builds a two-section document with them.

## `project/`: rendered once

`cf informalize init` renders each file under `project/` once, with `cflib.templates.render`,
into the output repository: `main.tex`, `notation.sty`, `sections/00-conventions.tex` and
`STYLE-project.md` into `informal/`, `README.md` into the root, and `gitignore` as the
root's `.gitignore` (a file of that name here would be this repository's). From then on
those files are the project's, and `sync` never touches them. `{{{ name }}}` is a brace
group holding the slot `name`.

| slot | in | holds |
|---|---|---|
| `title_tex` | `main.tex` | the text's title (`text.title`), escaped for LaTeX |
| `author_tex` | `main.tex` | `text.author`, escaped for LaTeX |
| `license_tex` | `main.tex` | the license's name in a sentence, such as `the Apache License, Version~2.0` |
| `library_name_tex` | `main.tex` | the library's name (the last component of `library.url`), escaped for LaTeX |
| `library_url` | `main.tex`, `README.md` | `library.url`, as it is (`\url` reads it verbatim) |
| `library_commit` | `main.tex`, `README.md`, `STYLE-project.md` | the 40-hex `library.commit` |
| `reference_bibitem_tex` | `main.tex` | `\bibitem{reference}` and `reference.work` escaped for LaTeX, or nothing when there is no reference work |
| `title` | `README.md`, `STYLE-project.md` | the text's title as the title page prints it (`informalize.config.printed_title`), as Markdown text |
| `theorem` | `README.md`, `STYLE-project.md` | the theorem's full Lean name |
| `library_name` | `README.md`, `STYLE-project.md` | the library's name |
| `license_name` | `README.md` | the license's name, such as `the Apache License, Version 2.0` |
| `reference_rules` | `STYLE-project.md` | a paragraph: the reference work's citation line and that the text cites its numbers only, through `\fcref`; or that the text cites no reference work and uses no `\fcref` |

`notation.sty`, `sections/00-conventions.tex` and `gitignore` have no slot.

## `licenses/`: the text's license

`cf informalize init` writes `licenses/<text.license>.txt` as the output repository's
`LICENSE`, one file for each license `text.license` may name (`overlay.config.OPEN_LICENSES`):
`Apache-2.0.txt` (a copy of this repository's `LICENSE`), `CC-BY-4.0.txt`,
`CC-BY-SA-4.0.txt`, `CC0-1.0.txt` and `MIT.txt`. Each is the license's own text, unchanged;
only MIT's has slots, `year` (the year of `init`) and `holder` (`text.author`). The runtime
keeps its own Apache-2.0 notice, `runtime/LICENSE-runtime`, whatever the text's license is.

## `site/`: the landing page and the workflows

`cf informalize overlay` renders `index.html` into the output repository's `site/index.html`
and copies `templates/informalize/site/workflows/pages.yml` and its `check.yml` sibling
into the output repository's workflow directory
(`docs/proposals/informalize.md` section 10). They belong to stage I7, not to `init`, which is
why they are not under `project/`: the landing page is rewritten whenever the overlay is, so a
second `cf informalize overlay` writes the same bytes. The two workflows are copied whole (a
`${{ ... }}` expression is a workflow's, not a slot); `index.html` has these slots, each
already escaped for HTML:

| slot | holds |
|---|---|
| `title` | the text's title as the title page prints it (`informalize.config.printed_title`) |
| `theorem` | the theorem's full Lean name |
| `library_name` | the library's name (the last component of `library.url`) |
| `library_url` | `library.url` |
| `library_commit` | the 40-hex `library.commit` |
| `license_name` | the license's name in a sentence, such as `the Apache License, Version 2.0` |
| `viewer_href` | the viewer's href as the site index spells it (`pdf-overlay/viewer/index.html`) |
| `pdf_href` | the published PDF's href beside it |
| `link_block` | the overlay link block `build_site.py` would inject, written by that tool's own `link_block`, so the site build replaces it in place and the tracked file does not change |

## `schema/`: the formats, for a reader

`informalize.schema.json` (the workspace's `informalize/<id>/informalize.yaml`) and
`items.schema.json` (`informal/items.json`) state the two hand-edited formats as JSON Schemas
(`docs/proposals/informalize.md` sections 5.1 and 5.4). They are not copied anywhere and no
tool reads them: `scripts/informalize/config.py` and `scripts/informalize/itemtable.py` are the
implementation, and also check what a schema cannot (ids that match their section and run
without gaps, a key declaration for every part, each declaration against `declarations.json`
and the library's source). `tests/test_informalize_cli.py` holds the keys, enums and patterns
of each schema to those modules.
