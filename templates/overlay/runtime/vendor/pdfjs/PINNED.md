# Vendored PDF.js runtime

This directory contains the two browser runtime files needed by the local PDF
viewer. They are copied without modification from the npm distribution
`pdfjs-dist@6.2.108` published by the Mozilla PDF.js project.

- Upstream project: <https://github.com/mozilla/pdf.js>
- Distribution: <https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.2.108.tgz>
- npm package: `pdfjs-dist`
- Version: `6.2.108`
- License: Apache License 2.0 (see `LICENSE`)
- npm SHA-1: `1e0ce0f4b3a034f953dbbe2334ab01fbddf0eb30`
- npm SRI: `sha512-YxFb+SQcodN2rnX9Tn3dHYlqfb7NjlzzfONPpJd+AKoKtUjEdevTfbC07d5TcczzOK6261auRkP/M8OBHs9vFQ==`
- Tarball SHA-256: `b3e68d5cda70551a90b3f771419d379e20fc788ce056fa32de73608e01df47f4`

Selected files and their SHA-256 digests:

```text
e0be3863c23c8af2305b16548febd58e7f8874a460253317d7771cddbc1c0f6d  pdf.min.mjs
0613f41490dd6aaceed7a93fbbd38c85e6d6aa60474b6588c6e7709cfbe18cb3  pdf.worker.min.mjs
0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594  LICENSE
```

Reproduce the extraction in a temporary directory with:

```sh
npm pack pdfjs-dist@6.2.108
tar -xzf pdfjs-dist-6.2.108.tgz
```

Then compare `package/build/pdf.min.mjs`,
`package/build/pdf.worker.min.mjs`, and `package/LICENSE` against the files in
this directory. The viewer imports only these local assets and does not use a
CDN at runtime.
