# Status

formal-companion is at version 0.2.0, with no tagged release (`CHANGELOG.md`). It is
one book's audit generalized, not a tool proven over many. Workspaces and hosts are not
in this tree, so each claim about a real run names the record it was read from, and was
not rerun here.

## Audit

**Two real companions of one book.** `tao-measure-lean` at `250b856` and
`measure_theory` at `9afe70f`, two editions and a shared source: discovery, inference,
extraction through the `docstring_tags` and `attribution_table` adapters, pairing,
def-coverage, the mechanical page-anchor check, `detect-proxies` (cheap tier), the
gate, `import-corpus`, the tally, adjudication, routing and the report. Every pinned
regression number in the test suite comes from that corpus
(`tests/baseline/regression-figures.json`, 29 figures). Those tests skip without
private inputs.

**A synthetic second book.** `tests/e2e/synthetic.sh` goes from nothing through
onboarding, pairing, the model-free detectors and `cf score`, over a generated
public-domain booklet and two companions of it, in CI. `cf tally`, `cf route` and
`cf report` ran over that workspace by hand with no model, and have tests of their own
(`tests/test_ledger_route.py`, `tests/test_rf_route_report.py`). `tests/e2e/multisource.sh`
adds the Tao half and needs two private checkouts. The synthetic run is what shows the
pipeline is not wired to one title.

**Server fleets.** `cf run --execute` has driven `tend` and `agentbox` on one private
host, under tend 0.1.0: differential runs over chapter 1 and section 1.7, including a
first finding merged after a 1/1 smoke test. Closed issues #6, #7, #10 and #11 record
what those runs found, and commits `8dea3c7`, `22471a0`, `f12d90a`, `a4c7008` and
`0187d45` are fixes made during them. Issue #11 prices three such runs from their
transcripts at list prices: $2,698.82 for chapter 1 (383 tasks), then for section 1.7
$219.55 for an attempt paused at 4 of 47 tasks and $358.10 for the re-run of all 47. Those
totals count one usage per content block, so they are probably about
twice the per-response cost: the last figure's rows collapse to about $168.91
(`tests/data/m0a/README.md`, which holds that usage, and
`docs/proposals/pdf-to-companion.md` section 6.1). The repository holds no record of an
attack preset through `cf run --execute`, so that is not shown either way. The five runs
that produced the baseline corpus predate this code. Nothing here pins `tend` or `agentbox`,
and `cf doctor` checks neither.

**The local fleet.** `cf run <id> --local` stages a planned run of up to
`local_fleet_max_items` items and prints the Workflow to launch. Tests cover the args,
the labels the journal reader keys on, and the harvest (`tests/test_rf_localfleet.py`,
`tests/test_harvest.py`). The repository holds no receipt of a completed Workflow fleet.
The adjudication Workflow has run: issue #10 transcribes its result on a real companion.

**Frozen to one campaign.** The eight `--preset` rows are a record of how the five
2026-09 runs differed, and three of them (`differential-legacy`, `attack-legacy`,
`mechanical`) describe imported corpora rather than runs, so `cf plan-run` exits 2 on
them. The stage tables `detect-proxies --lean` needs are per-source and hand-written,
and the shipped one covers one book.

**Adapters.** `docstring_tags` and `attribution_table` have run on the two companions
above. `blueprint_tex` was measured on one real leanblueprint project: commit `b3ea375`
records 293 of 391 statement environments, score 0.86, and 310 rows over 293 items, and
commit `e5c92ba` a defect reproduced on it. No full audit of that project is recorded here.

**Not generalized yet.** A second book is likeliest to fail at a source whose items are
not headed by a capitalized kind word, or at a companion convention no adapter recognizes.
The numbering grammar is `<Kind> <n>[.<n>...]`, one to four levels, with the depth
inferred per source.

**Not implemented.** `fix`, `review-pr`, `camera-ready`, `publish-verdicts`, `curate`
and `lint` are designed and documented. The audit reports findings only.

## PDF overlay

**Built and tested.** `cf overlay init`, `export`, `sync` and `check`; the runtime
tools; the viewer. `templates/overlay/tests/` holds 158 tests under Node, with a fake
DOM and a fake PDF.js, and six Python modules test the generator
(`tests/test_overlay_*.py`). All run in CI. CI tests the parts separately:
`tests/test_export.py` runs the real exporters on synthetic companions, and
`tests/test_overlay_cli.py` runs `cf overlay` with a stand-in for the export child.

**Run by hand.** The whole path on a synthetic companion: init, export under
`attribution-table`, sync, check, `check_generated.py`, a Literature Links scan,
`serve.py`, and the viewer in Chromium, including proof steps and `display.minimal`.

**Real companions.** Three private companions carry overlays generated at `50e7649`, as
their `pdf-overlay/GENERATED.json` records: `measure_theory`, 1,085 annotations through
the `attribution-table` export, and two whose annotations come from producers of their
own, one of them with 1,197 proof steps among its 3,677 annotations. PR #43 records
`cf overlay check` and `check_generated.py` passing on all three at an earlier commit, each
checked in a browser. PR #45 records a re-export of `measure_theory` at `0297cc6`,
byte-identical to that companion's own PR #30. PRs #48 and #50 record the viewer driven
against the companion with proof steps, #50 in Chromium 152 and Firefox 159, and that
companion's re-sync at `4125474` records both checks passing. The re-syncs to `50e7649`
change one comment in the viewer and the Literature Links version, not the annotations;
`measure_theory`'s PR #34 and one other companion's PR #4 record both checks passing there.
The exporters also reproduce
reference documents for `measure_theory` at `2cd263a` and `tao-measure-lean` at `677ada1`
where the private inputs are present (`docs/exporters.md` section 5). Those tests skip
without them.

**Limits.** The `docstring-tags` exporter writes no PDF URL, so
`--convention docstring-tags` is refused. `cf overlay export` writes no proof steps. The
viewer stops in Safari 17.4. PR #50 records it in Chromium 152 and Firefox 159, with one
comment in its files changed since, and no other browser has been driven. JPEG 2000 figures draw as empty
frames. [Generating an overlay](generating-an-overlay.md) has each of these.

## Locate

**Built.** The code of milestones L-M0 to L-M3 (`docs/proposals/wikipedia-locate.md`
section 12). `tests/e2e/locate_synthetic/` drives it through every stage on synthetic
inputs: a made-up WikiProject in recorded API responses, two made-up libraries and a
synthetic probe index, through the universe, a sourceset adopted from a producer's
delivery of those responses, candidates, the three presets, the out-of-tree gate over
good and bad documents, harvest, tally, the sealed export and the bench.
`tests/test_e2e_locate_synthetic.py` replays the enumeration and the fetch from the
same responses, offline, beside it.

**On real data, on one private host.** `CHANGELOG.md` and the commit messages record a
universe of 48 libraries built on the run host by the probe drivers, the first run
against real pages, and smoke runs with workers, gate, reviewers and merges. The smoke-2
audit of 2026-10-19 found unchallenged supporting records about 65% right and their
strong relations about 45% right, with every error an overclaim. Smoke 4 had 2 of 76
reviews rejected in each run (commit `0514feb`). Inventory pilots ran over 83 articles
(commit `84e40ba`).

**Not done.** No record shows the campaign sequencer or the spend watchdog running on a
host, and none shows frozen bench gold. Milestones L-M4 to L-M6 are open, and no coverage
figure for Wikipedia appears in this repository.

## Informalization

**Built.** Components 1 to 5a of `docs/proposals/informalize.md` section 11, the
`cf run` and `cf harvest` half of 5b, and the exporter of 7: the specification with
`tests/test_informalize_spec.py`, which holds it to the code it names; the LaTeX
runtime and the project templates
(`templates/informalize/`); `cf informalize init | sync | status | build-command |
fetch-library | scope | check-items | approve | reference-checklist`; `cf
plan-informalize` with the out-of-tree gate `scripts/gate/verify_informal.py`, the
phase prompts and the tend config; and `cf export informal-markers`, which turns a
canonical PDF's boxes into one annotation document.

**Tested on synthetic inputs only.** `tests/e2e/informalize_synthetic/driver.py` takes
an informalize id over a fake library through every phase: the planner, contributions
written in worktrees of an entrypoint made as `cf run` makes one, the staged gate run as
a run host runs it (a copy of `gate/`, `python3 -I`, a bare environment) admitting good
contributions and refusing bad ones under the rule they break, a task ended at the
attempt cap, and `cf harvest` between phases. `tests/test_informalize_runtime.py` builds
a two-section document of five items and six parts with pdflatex and checks the boxes
against the lines the pilot's got wrong. Those builds, and the fleet phases that build,
skip without TeX; CI's `informalize-tex` job runs them with `CF_REQUIRE_TEX=1`.

**Not run.** No Lean library has been built or probed for this mode, no phase has run
under `tend` on a host, no item table or canonical PDF of a real theorem exists, and no
informalization companion has been published. No cost figure for a phase is recorded
here: the pilot that did this once by hand and with ad-hoc workflows used 69 agents and
about 11.3M tokens for 96 items and logged no dollars, which the specification's
section 12 says nothing reliable follows from.

**Not implemented.** `cf informalize apply` and the spend watchdog of component 5b; the
canonical PDF of I6 (`pdf-command`, `fetch-pdf`, `canonical_pdf.sh`); and the overlay
stage of I7 (`cf informalize overlay`, `check-site`, the site and workflow templates).
Until `apply` exists, the files a phase wrote are copied out of `corpus/<run>/tree/` by
hand, which is what the synthetic driver does.

## PDF-to-companion pipeline

`docs/proposals/pdf-to-companion.md` designs ten stages, and the deterministic parts of
S0 (`cf ingest`) and S1 (`cf inventory`) are built (`docs/pipeline.md`). Their agents
and stages S2 to S9 are not. Measured passage recall on Tao's chapter 1 is 19 of 222.

## Never exercised end to end

Locate's campaign sequencer and spend watchdog on a host, the benchmark against real
gold, the informalize mode (nothing of it has run outside the synthetic driver), and
the `tend-agent` bridge (`scripts/run/bridge-tend-agent.sh`), which is a stub
whose own TODO says its CLI contract has not been exercised. S2 to S9 are not built.

## Older statements

`SKILL.md`'s paragraph on locate in "Experimental and unverified", the banner and section 1
of `docs/proposals/wikipedia-locate.md`, and what `docs/proposals/pdf-to-companion.md` says
of `cf run --execute` were written before the server and smoke runs above, and still say
those paths have not run.
Where they differ from this page on what has run, this page has the newer record.
`SKILL.md` remains the short list for the rest, such as the codex presets, `TEND_TASK_ID`
and `detect-proxies --lean`.
