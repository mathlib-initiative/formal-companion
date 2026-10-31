# Vendored PDF.js runtime

This directory contains the two browser runtime files needed by the local PDF
viewer. They are the legacy build in the npm distribution `pdfjs-dist@6.2.108`
published by the Mozilla PDF.js project, copied without modification.

- Upstream project: <https://github.com/mozilla/pdf.js>
- Distribution: <https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.2.108.tgz>
- npm package: `pdfjs-dist`
- Version: `6.2.108`
- Build: `legacy/build/`
- License: Apache License 2.0 (see `LICENSE`)
- npm SHA-1: `1e0ce0f4b3a034f953dbbe2334ab01fbddf0eb30`
- npm SRI: `sha512-YxFb+SQcodN2rnX9Tn3dHYlqfb7NjlzzfONPpJd+AKoKtUjEdevTfbC07d5TcczzOK6261auRkP/M8OBHs9vFQ==`
- Tarball SHA-256: `b3e68d5cda70551a90b3f771419d379e20fc788ce056fa32de73608e01df47f4`

Selected files and their SHA-256 digests:

```text
9fab0c910bf1484835c5c2aeb68f7eb3dfce7f9eb435a004526c5af86d70890c  pdf.min.mjs
bc0d1b88ea0b66196b1d36a58ac243c6d92adfe725624e2a9fdd381bdf8ef434  pdf.worker.min.mjs
0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594  LICENSE
```

Reproduce the extraction in a temporary directory with:

```sh
npm pack pdfjs-dist@6.2.108
tar -xzf pdfjs-dist-6.2.108.tgz
```

Then compare `package/legacy/build/pdf.min.mjs`,
`package/legacy/build/pdf.worker.min.mjs`, and `package/LICENSE` against the
files in this directory. The viewer imports only these local assets and does not
use a CDN at runtime.

## Why the legacy build

The package's default build (`build/`) calls recent builtins without checking
that they exist: the global `Iterator` and its helpers, `Promise.try`,
`URL.parse`, `Map.prototype.getOrInsertComputed`, `Math.sumPrecise` and
`Uint8Array.prototype.toHex`, among others. Safari 17.4 has none of these, so the
default build stops there at load (`Can't find variable: Iterator`) and the
viewer shows no page. The legacy build is the same release compiled for older
browsers, carrying polyfills for them (core-js). A browser with its own builtin
keeps it, so in Chrome and Firefox the polyfills cost a little load time and
nothing after it.

A new release is vendored from `legacy/build/` too. The suite
`templates/overlay/tests/pdfjs-build.test.mjs` in formal-companion removes those
builtins, then loads each file and opens a document.
