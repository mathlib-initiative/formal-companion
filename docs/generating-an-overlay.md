# Generating an overlay

A companion's PDF overlay is a viewer, served on loopback, that shows the source PDF
with each item outlined and the declarations linked to it. `cf overlay` writes two
directories in the companion, and nothing else:

- `annotations/`: the companion's
  [Literature Links](https://github.com/mathlib-initiative/literature-links) 1.0
  annotations, one annotation per file, one folder per book section and per item
  with several;
- `pdf-overlay/`: the viewer, its tools and PDF.js, copied verbatim, plus the
  annotation bundle the viewer reads and `GENERATED.json`, which records the sha256
  of every copied file and of the annotations.

It needs no workspace. Any faithfulness verdicts the viewer shows come from files
committed in the companion, never from an audit workspace. [docs/overlay.md](overlay.md)
is the contract, and [docs/exporters.md](exporters.md) says what the exporter reads.

**Contents**: the steps · what export needs · reading the overlay · what the viewer
shows · proof steps · browsers · checking it in CI

## The steps

`export` reads the companion at a commit and refuses a dirty checkout, so the config
is committed first and that commit is what `--commit` names. The exporter's defaults
point under `pdf-overlay/`, where `sync` refuses files it did not write, so this
companion keeps its curation in `curation/` and names it after `--`. The shipped
registry holds two editions, so most PDFs also need their id and title named there.

```sh
COMPANION=/path/to/companion
cf overlay init "$COMPANION" --source-id my-book --title "My book" &&
  git -C "$COMPANION" add pdf-overlay &&
  git -C "$COMPANION" commit -m "Add the overlay config"
COMMIT=$(git -C "$COMPANION" rev-parse HEAD)
cf overlay export "$COMPANION" --convention attribution-table --commit "$COMMIT" \
    -- --data-dir curation/data --official-source curation/official-source.json \
    --source-id my-book --source-title "My book" &&
  cf overlay sync "$COMPANION" &&
  cf overlay check "$COMPANION" &&
  git -C "$COMPANION" add annotations pdf-overlay &&
  git -C "$COMPANION" commit -m "Generate the overlay"
```

`cf overlay init` writes `pdf-overlay/overlay.config.json` from a template, and that file
is the companion's to edit; `docs/overlay.md` section 3 lists its keys.
`cf overlay sync` copies the runtime, writes the bundle and `GENERATED.json`,
and a second run changes nothing. `cf overlay sync --check` is read-only and includes
the scan. `cf overlay check` adds the curation inputs' hashes, the overlay's own
rules and a committed `verso.json`. The copied runtime comes to about 2 MB. `annotations.bundle.json`, and `verso.json`
where a companion commits one, grow with the annotations: the three private companions'
`pdf-overlay/` directories come to 5.5 MB, 9.3 MB and 40 MB. The PDF is never copied or
committed.

## What export needs

| Situation | What happens | What settles it |
|---|---|---|
| The checkout is dirty, a previous export's uncommitted output included | `has uncommitted changes` | Commit, then pass that commit to `--commit` |
| `--commit` is not where the checkout is | `is at <sha>, not at the pinned <sha>` | Name the commit the checkout is at |
| The checkout has no `origin` remote | `has no origin remote to name the companion by` | `--repo-id host/org/name` and `--name <name>`, after `--` |
| The registry does not know the PDF | `the registry does not know the PDF` | `--source-id` and `--source-title` after `--`, and `--page-offset` when the ledger's locators give no offset |
| `cf overlay init` named another source id than the document's | `the document has 0 pdf sources with id` | One id in both places; for a PDF the registry knows, its registry id |
| Curation sits under `pdf-overlay/`, the exporter's default | `cf overlay sync` stops with `holds a file no previous GENERATED.json listed` | Keep it elsewhere, and name it with `--data-dir` and `--official-source`, after `--` |
| `--convention docstring-tags` | `carries no url` | Nothing at this commit: that exporter writes no PDF URL, and the overlay needs one to fetch and verify the PDF |

Everything after a bare `--` goes to `cf export` unchanged, and
`cf export attribution-table --help` lists those flags.

## Reading the overlay

```sh
cd "$COMPANION" && python3 pdf-overlay/tools/serve.py --build
```

`serve.py` fetches the PDF over HTTPS from the URLs the annotations name, verifies it
against their sha256, caches it under `pdf.cache_dir` (default `.lake/overlay-pdf`)
and serves it on `127.0.0.1` only. It prints the addresses it serves: the site at
`/`, the viewer at `/pdf-overlay/viewer/index.html`, and the PDF at its route. A
python.org Python on macOS has no CA bundle until its `Install Certificates.command` has
run, and until then the fetch stops with `CERTIFICATE_VERIFY_FAILED`.

| Command | What it does |
|---|---|
| `python3 pdf-overlay/tools/serve.py` | Serve a site that is already built |
| `python3 pdf-overlay/tools/serve.py --build` | Run `build.commands` and `build_site.py` first |
| `python3 pdf-overlay/tools/serve.py --build-only` | Build and exit |
| `python3 pdf-overlay/tools/serve.py --port 8127` | Serve on another port than the default 8000 |
| `python3 pdf-overlay/tools/serve.py --refresh-pdf` | Fetch the PDF again |

`cf overlay init` writes `build.commands` as `lake build` and
`lake query :literateHtml`, and `site.output_root` as `.lake/build/literate-html`, so
the default path needs a Verso build of the companion. With no built site, `serve.py`
exits 1. Hovers come from that build; `tools/verso_extract.py` cuts them from a build
made elsewhere (`docs/overlay.md` section 5). A second `serve.py` on the port the first
holds stops with `is already in use`. One on another port, or one that only builds, waits
on `.overlay-serve.lock` until the first exits.

## What the viewer shows

- The source, with a "Verified PDF" chip once the sha256 the annotations pin has
  matched, and a panel listing the page's items in reading order.
- Each item's key declarations as cards with their statements, and the rest under
  "Related". Where the built site has Verso pages, a statement is drawn with tinted
  tokens, links and hover cards.
- Proof steps nested under their item, each with its number, title and relation
  ("Exact match", "Close match", "No match" and so on). A step's panel shows its
  locator, its note with backticked code set as code, and one card per declaration
  with its numbered Lean lines.
- Script characters in titles set as `sub` and `sup` elements, so no font has to hold
  them.

`display.minimal: true` in `overlay.config.json` keeps the passage, its links and
their statements, and leaves out what describes them. A record's note waits behind
one "More detail" control. An `exactMatch` prints no word, and the other relations
stay. `docs/overlay.md` section 3 has the whole list.

## Proof steps

`cf overlay export` writes no proof steps. A producer of its own writes one record
with `kind: proof_step` per step, in the item's folder, as `docs/overlay.md` section 7
specifies. `cf overlay sync` refuses a hand-written file beside exported annotations
(`not what cf overlay export wrote`). It records files nothing exported as they are,
so proof steps belong to a companion whose annotations come from that producer.

## Browsers

PR #50 records this viewer in Chromium 152 and Firefox 159 against a private companion,
with one comment in its own code changed since, and it has also been driven in Chromium
against a synthetic one. PR #54 records it in Safari 17.4, Chrome 154 and Firefox 159 on
a 404-page companion, where each draws the same 2,512 boxes. Before PR #54 it stopped in
Safari 17.4: the default build of PDF.js 6.2.108 reads the global `Iterator` when it
loads, and that browser has none (`ReferenceError: Can't find variable: Iterator`). The
vendored files are now the legacy build of the same release, which brings its own
`Iterator` and the other newer builtins PDF.js calls. JPEG 2000 and JBIG2 images still
draw as empty frames, with the warning `JpxError: OpenJPEG failed to initialize` or
`Jbig2Error: JBig2 failed to initialize`: PDF.js decodes them with WebAssembly files that
are not vendored, and the viewer gives it no `wasmUrl`. Commit `75fa8aa` records that
Firefox on macOS draws a box for some script characters after a letter, which is why
titles set scripts as markup. The 160 tests of `templates/overlay/tests/` run under Node,
all but two with a fake DOM and a fake PDF.js. The two in `pdfjs-build.test.mjs` load the
real vendored files with the builtins Safari 17.4 lacks removed. None of them runs a
browser.

## Checking it in CI

```sh
python3 pdf-overlay/tools/check_generated.py
```

A companion's CI needs only CPython 3.11+ and the checkout for this. It checks every
generated file, the config, the annotation files, the bundle, the Literature Links
scan, and that the curation files an export read have not changed since.
