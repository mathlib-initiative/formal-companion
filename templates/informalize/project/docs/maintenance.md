# Maintenance

This repository describes `{{ theorem }}` in
[{{ library_name }}]({{ library_url }}) at commit `{{ library_commit }}`.
The current pins and file ownership are recorded in
`informal/informalize.json` and the generated manifests.

## Layout and ownership

```text
informal/ source and curation
    → canonical PDF and markers
    → annotations/ (Literature Links)
    → pdf-overlay/ bundle and hovers
    → site/ → published website
```

`informal/items.json` records statement links and deliberate specializations.
`informal/declarations.json` is the pinned Lean inventory.
Project-specific proof-step exporters, if needed, belong in `tools/`, with
their tests in `tests/`. Reuse Literature Links fields and existing viewer
capabilities before adding extensions.

The root documents, Makefile, tools, and tests are project-owned. Files listed
in a `GENERATED.json` are producer-owned. The overlay command also manages
`site/index.html`, `pdf-overlay/overlay.config.json`, and its two workflows.
Regenerate through formal-companion when changing shared runtime behavior.

## Build and check

```sh
make check
make pdf
```

Checks require Python 3.11 or later, Git, and Make on Linux or macOS.
The development PDF is `build/pdf/{{ job }}.pdf`. Install TeX Live with
`pdflatex`, `latexmk`, Latin Modern, and the packages listed in
`informal/informalize.json`. Install any additional packages the text uses.

A development PDF does not update the published annotations. Their coordinates
belong to the exact PDF whose hash they record. The canonical build requires
Poppler's `pdftotext`, `pdftoppm`, and `pdfinfo` as well as TeX, and compares two
clean builds on the same installation.

## Regenerate with formal-companion

In the owning workspace, continue the existing `{{ id }}` informalization:

1. Update and commit the text and curation. Run the item checks and keep the
   applicable item and reference approvals current.
2. Use `cf informalize pdf-command {{ id }} --host HOST`, run the printed
   canonical-build instructions, and fetch the result with
   `cf informalize fetch-pdf {{ id }} --host HOST --execute --i-have-consent`.
3. Inspect the PDF and proof sheets and record their review through
   `cf informalize approve {{ id }} proof-sheets --by NAME`.
4. Choose a new versioned PDF asset URL and run
   `cf informalize overlay {{ id }} --pdf-url URL`. Regenerate hovers if
   needed, using `cf informalize verso-command {{ id }} --host HOST` and
   its printed instructions.
5. Run `cf informalize check-site {{ id }}` and `make check`. Complete the
   publication review for this revision.

Replace `HOST`, `NAME`, and `URL` with the project's configured values. These
commands run with the owning workspace selected; use `cf --workspace PATH`
when invoking them elsewhere. Follow formal-companion's informalization
runbook for host setup and the full stage contracts.

## Publish

Publish the canonical PDF as a new immutable release asset before deploying
annotations that refer to it. Keep older assets available. Add the real
published-viewer and versioned-download links near the top of the README;
do not infer a website URL from the source library's repository.

Once the overlay exists, `make site` runs the checks, downloads the released
PDF, verifies its hash, and writes `site/`. Test this from a clean checkout.
Only `site/index.html` is tracked; the rest of `site/` is build output.

The Pages workflow runs the reusable checks on Python 3.11 and 3.13 before
building and deploying. Enable GitHub Pages with GitHub Actions when publishing
on GitHub. Push ready changes to `main`, wait for deployment, and inspect the
published site and its “Verified PDF” status.

The homepage opens the annotated PDF directly, preserving query strings and
page or selection fragments. Use the published URL for review after publication
has been authorized. Documentation-only changes need no new PDF release.
