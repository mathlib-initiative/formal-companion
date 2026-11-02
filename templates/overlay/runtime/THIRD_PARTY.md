# Third-party code in `pdf-overlay/`

Two third-party components are vendored here: PDF.js and Literature Links.
Everything else under this directory was written for the formal-companion
overlay.

## PDF.js

- Project: Mozilla PDF.js, <https://github.com/mozilla/pdf.js>
- Distributed as the npm package `pdfjs-dist`
- Version: **6.2.108**
- License: **Apache License 2.0**, reproduced verbatim in
  `vendor/pdfjs/LICENSE`
- Files, copied without modification from `pdfjs-dist@6.2.108`, the two
  JavaScript files from its legacy build (`legacy/build/`):

  ```text
  9fab0c910bf1484835c5c2aeb68f7eb3dfce7f9eb435a004526c5af86d70890c  vendor/pdfjs/pdf.min.mjs
  bc0d1b88ea0b66196b1d36a58ac243c6d92adfe725624e2a9fdd381bdf8ef434  vendor/pdfjs/pdf.worker.min.mjs
  0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594  vendor/pdfjs/LICENSE
  ```

- `vendor/pdfjs/PINNED.md` records the npm tarball's own digests and the two
  commands that reproduce the extraction, so the bytes above can be checked
  against the published package without trusting this directory. It also says
  why the legacy build.
- Bundled in both files by the legacy build: core-js 3.49.0,
  <https://github.com/zloirock/core-js>, under the MIT License
  (<https://github.com/zloirock/core-js/blob/v3.49.0/LICENSE>), Copyright (c)
  2013–2025 Denis Pushkarev (zloirock.ru) and Copyright (c) 2025–2026 CoreJS
  Company (core-js.io). Each file carries that copyright line and licence URL.

The viewer loads `pdf.min.mjs` and `pdf.worker.min.mjs` from this directory
only: no CDN is contacted at run time, and the page's Content-Security-Policy
allows no other origin. PDF.js is started with `isEvalSupported: false`.

Nothing here is modified, so the Apache 2.0 notice requirement is met by
shipping `vendor/pdfjs/LICENSE` beside the files it covers; this file is the
notice that says which files it covers and where they came from.

## Literature Links

- Project: Literature Links, <https://github.com/mathlib-initiative/literature-links>
  at tag `v1.0.0`, commit `ae8f2a65c8f665a4a747842a7e5bf8e1ae19054e`
- Version: **1.0.0**
- License: **Apache License 2.0**; Copyright 2026 the Literature Links contributors
- Files: `tools/literature_links/`, copied without modification and pinned by
  sha256 in `GENERATED.json`
