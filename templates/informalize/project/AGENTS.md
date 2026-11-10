# Working in this repository

- Read CONTRIBUTING.md and docs/maintenance.md for file ownership, checks, and
  release steps. Keep the README focused on reading the text and getting started.
- Read informal/STYLE.md and informal/STYLE-project.md before editing mathematics.
  Introduce notation as needed, give actual arguments, and link specific
  sentences or equations when tagging proof steps.
- Prefer existing Literature Links fields and viewer capabilities. Add an
  extension only for a concrete unmet need.
- Do not hand-edit generated runtimes or hashes to bypass validation. Update
  formal-companion and regenerate. Keep project tools in tools/ and tests in tests/.
- Run `make check` and, after the overlay exists, `make site` before publication.
  Add regression tests for changes to export behavior or annotation geometry.
- Keep PDF assets immutable and versioned. Rebuild the PDF and its annotations
  together when the mathematical text changes.
- Use the maintainer's published site for previews and review once publication
  is authorized. Verify the deployed result; do not leave requested updates
  available only on localhost. Do not invent a public URL or change repository
  visibility as part of generation.
