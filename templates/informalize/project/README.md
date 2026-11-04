# {{ title }}

An informal account, without proofs, of the definitions and results on which the theorem
`{{ theorem }}` depends in the Lean library [{{ library_name }}]({{ library_url }}) at
commit `{{ library_commit }}`. Each numbered statement, and each lettered part of one, is
a box in the PDF linked to the Lean declaration that states it.

- `informal/`: the LaTeX source. `main.tex` is the document, `sections/` its sections,
  `items.json` the table of items and their declarations, `STYLE.md` and
  `STYLE-project.md` the rules it is written to.
- `annotations/`: the links from each box of the PDF to the Lean declarations, in the
  Literature Links format.
- `pdf-overlay/`: the viewer that shows the PDF with those links.

Build the PDF from this directory with TeX Live (pdflatex, latexmk):

```sh
bash informal/tools/build_pdf.sh build/pdf
```

Two builds on one TeX installation give the same bytes; the published PDF is the one whose
sha256 the annotations pin.

The text and the PDF are released under {{ license_name }} (`LICENSE`).
