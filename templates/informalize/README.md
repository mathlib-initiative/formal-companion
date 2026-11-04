# Informalize templates

What `cf informalize` puts in an output repository's `informal/` (and its root), as
`docs/proposals/informalize.md` section 7 specifies.

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
| `tools/extract_markers.py` | the boxes, fitted to the ink of a 288 dpi render: `markers.json` |
| `tools/check_items.py` | the build against `items.json`, `declarations.json` and `hypotheses.json` (rules 1 to 10) |
| `tools/proof_sheets.py` | the boxes drawn over the pages, and the checks a person would make by eye |
| `tools/fccommon.py` | what the three Python tools share, and the two commands `build_pdf.sh` runs |
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
| `theorem_tex` | `main.tex` | the theorem's full Lean name, as `\texttt{...}` with `_` escaped |
| `library_name_tex` | `main.tex` | the library's name (the last component of `library.url`), escaped for LaTeX |
| `library_url` | `main.tex`, `README.md` | `library.url`, as it is (`\url` reads it verbatim) |
| `library_commit` | `main.tex`, `README.md`, `STYLE-project.md` | the 40-hex `library.commit` |
| `reference_bibitem_tex` | `main.tex` | `\bibitem{reference}` and `reference.work` escaped for LaTeX, or nothing when there is no reference work |
| `title` | `README.md`, `STYLE-project.md` | `text.title`, as Markdown text |
| `theorem` | `README.md`, `STYLE-project.md` | the theorem's full Lean name |
| `library_name` | `README.md`, `STYLE-project.md` | the library's name |
| `license_name` | `README.md` | the license's name, such as `the Apache License, Version 2.0` |
| `reference_rules` | `STYLE-project.md` | a paragraph: the reference work's citation line and that the text cites its numbers only, through `\fcref`; or that the text cites no reference work and uses no `\fcref` |

`notation.sty`, `sections/00-conventions.tex` and `gitignore` have no slot.

The license texts `init` writes as the root's `LICENSE`, one for each license
`text.license` may name, are not here yet: component 3 adds them with `init`
(`docs/proposals/informalize.md` sections 7.1 and 11).
