# Third-party code in `pdf-overlay/`

Two third-party components are vendored here: PDF.js and Literature Links.
Everything else under this directory was written for the formal-companion
overlay.

## PDF.js

- Project: Mozilla PDF.js, <https://github.com/mozilla/pdf.js>
- Distributed as the npm package `pdfjs-dist`
- Version: **6.2.108**
- Licence: **Apache License 2.0**, reproduced verbatim in
  `vendor/pdfjs/LICENSE`
- Files, copied without modification from `pdfjs-dist@6.2.108`:

  ```text
  e0be3863c23c8af2305b16548febd58e7f8874a460253317d7771cddbc1c0f6d  vendor/pdfjs/pdf.min.mjs
  0613f41490dd6aaceed7a93fbbd38c85e6d6aa60474b6588c6e7709cfbe18cb3  vendor/pdfjs/pdf.worker.min.mjs
  0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594  vendor/pdfjs/LICENSE
  ```

- `vendor/pdfjs/PINNED.md` records the npm tarball's own digests and the two
  commands that reproduce the extraction, so the bytes above can be checked
  against the published package without trusting this directory.

The viewer loads `pdf.min.mjs` and `pdf.worker.min.mjs` from this directory
only: no CDN is contacted at run time, and the page's Content-Security-Policy
allows no other origin. PDF.js is started with `isEvalSupported: false`.

Nothing here is modified, so the Apache 2.0 notice requirement is met by
shipping `vendor/pdfjs/LICENSE` beside the files it covers; this file is the
notice that says which files it covers and where they came from.

## Literature Links

- Project: Literature Links, <https://github.com/mathlib-initiative/literature-links>
  at tag `v1.0.0` (commit `3d5f60c14cc0057e99fed837f84ed07c4fe71da1`)
- Version: **1.0.0**
- Licence: **Apache License 2.0**; Copyright 2026 the Literature Links contributors
- Files: `tools/literature_links/`, copied without modification and pinned by
  sha256 in `GENERATED.json`
