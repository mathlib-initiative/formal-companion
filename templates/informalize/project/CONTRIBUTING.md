# Contributing

Use the published PDF viewer to review the text and its Lean counterparts once
the site is available. Include the item number or selected-passage link when
reporting a problem.

## Where to edit

| Change | Source |
| --- | --- |
| Mathematical prose and diagrams | `informal/sections/*.tex` |
| Shared notation | `informal/notation.sty` |
| Statement links and specializations | `informal/items.json` |
| Viewer presentation | `pdf-overlay/overlay.config.json` |
| Project-specific exporters and checks | `tools/` and `tests/` |

Read [STYLE.md](informal/STYLE.md) and the
[project editorial guide](informal/STYLE-project.md) before editing the text.
Keep the README short; place detailed maintenance instructions in `docs/`.

## Validate

Run `make check` from the repository root. It checks the recorded runtime,
runs the project tests, and validates the overlay when present. Before the
overlay stage, this checks the source scaffold only. `make site` requires the
overlay and runs the checks before building it.

Add regression tests for changes to source-range validation, highlight geometry,
or export behavior. Put additional project checks in `make check` so local
builds and publication run the same checks. Prefer the Python standard library.

The files listed in `informal/GENERATED.json` and
`pdf-overlay/GENERATED.json` come from formal-companion. Change the producer
and regenerate its output rather than hand-editing copied runtimes or hashes.
Edit curated inputs rather than their exported annotations and bundle.

Project documentation, the Makefile, tools, and tests are written once and
then belong to the project. Runtime synchronization does not overwrite them.
The overlay stage manages its homepage, viewer configuration, and workflows;
change shared defaults in formal-companion.

## Publish and review

Follow [the maintenance guide](docs/maintenance.md) to rebuild the PDF and
annotations together. Keep release assets immutable and versioned. Generated
build output, cached PDFs, and intermediate TeX files stay outside Git.

After publication is authorized and configured, use the published site for
review. Wait for its deployment and verify the affected PDF pages, annotation
boxes, and Lean cards before reporting an update complete.

Mechanical checks verify the recorded sources and generated data. They do not
establish the mathematical correctness of an informal proof or certify a
human review of its correspondence.
