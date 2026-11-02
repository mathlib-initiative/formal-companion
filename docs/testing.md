# Testing

Run these from the root of a full clone. Tests that compare this checkout with a
frozen base commit skip in a shallow clone, so compare results from a full one.

```sh
python3 -m unittest discover -s tests
python3 -m pytest -q tests/
node --test templates/overlay/tests/*.test.mjs
bash tests/e2e/synthetic.sh
python3 tests/e2e/locate_synthetic/driver.py
```

In order: the documented gate; the same suite under pytest, which is installed
separately; the overlay viewer's suite; the end-to-end demonstration; and the same for
locate, which the suite also runs as `tests/test_e2e_locate_synthetic.py`.
`tests/_hermetic.py` pins HOME at a temporary directory, and every test module that
builds a workspace imports it first, so neither runner reads a real home.

The Python suite is about 5,000 tests. Counts vary by platform and runner: at commit
`4125474`, `unittest` on Python 3.13 ran 4,952 with 217 skipped, and pytest on 3.11,
3.12 and 3.13 passed 4,741 with 242 skipped. The viewer's suite is 158 tests under
Node's built-in runner, with a fake DOM and a fake PDF.js.

## Environment variables

Regression tests that read local baselines skip cleanly unless these name them, so
the suite passes on a machine with none set. The bridges and the host tools read other
`CF_*` variables of their own, which the tests set for themselves.

| Variable | What it names or does |
|---|---|
| `CF_BASELINE_CORPUS` | A harvested corpus |
| `CF_BASELINE_DIFF` | The differential tree |
| `CF_TML` | One companion checkout |
| `CF_MT` | The other companion checkout |
| `CF_BOOK_TXT` | The pinned book text |
| `CF_TAO_PDF` | The published edition's PDF, for the exporter reproductions and `tests/test_m1_calibration.py` |
| `CF_M3_REFERENCE_DIR` | The maintainer's reference documents, never in this tree |
| `CF_TML_SITE`, `CF_MT_BUILD` | Optional inputs to the exporter reproductions; `docs/exporters.md` says what they compare |
| `CF_M1_DOWNLOAD=1` | Opts in to downloading the PDF for the M1 tests; nothing is fetched by default |
| `CF_M1_ENFORCE_BARS=1` | Asserts the proposal's recall bars instead of only reporting them |
| `CF_M1_REPORT=<file>` | Writes the calibration figures as JSON |
| `CF_ERRATA_MD` | A workspace's rendered `errata.md`, for the scan that no tracked file quotes a fetched errata page |
| `CF_TEST_TMP`, `CF_TMPDIR` | The parent of the tests' temporary directories; each module reads one or the other |
| `CF_TEST_KEEP_HOME=1` | Runs against the real HOME deliberately, instead of the temporary one |
| `CF_REQUIRE_BASE_COMMITS=1` | Makes the frozen-base-commit skip a failure (`tests/_base_commits.py`) |
| `CF_REQUIRE_POPPLER=1` | Makes `tests/e2e/synthetic.sh` fail rather than skip without `pdftotext` |
| `CF_SCRUB_RANGE` | A commit range for `tests/test_rf_scrub.py` to scan, such as `origin/master..HEAD` |

Set them on the command line:

```sh
CF_TML=/path/to/one CF_MT=/path/to/other python3 -m unittest discover -s tests
```

## CI

`.github/workflows/tests.yml` runs 9 jobs on every push and pull request.

| Job | Runs | Full history, and `CF_REQUIRE_BASE_COMMITS=1` |
|---|---|---|
| overlay viewer | `node --test templates/overlay/tests/*.test.mjs` on Node 22 | No |
| pytest, 3 jobs | The suite on Python 3.11, 3.12 and 3.13, with PyYAML | Yes |
| pytest (no PyYAML) | The suite on Python 3.11 with PyYAML absent: the fallback reader is what parses `run.yaml` on a bare host | Yes |
| unittest discover | The documented command on Python 3.13, which loads no `tests/conftest.py` | Yes |
| registry | `cf sources validate-registry` in a clone, with nothing installed | No |
| shellcheck | The run bridges, the dependency dump and the e2e scripts, at `--severity=error` | No |
| synthetic end-to-end | `tests/e2e/synthetic.sh` with poppler installed and `CF_REQUIRE_POPPLER=1` | No |

CI does not cover the baseline-backed tests (they need private corpora and skip),
`cf run --execute`, `render_pages.render()` and the `pdftoppm` path, the Tao half of
`tests/e2e/multisource.sh` (two private checkouts), or anything touching a real
companion. The workflow file's header says the same.

`CONTRIBUTING.md` has what a change must carry, including a test that fails before
the fix.
