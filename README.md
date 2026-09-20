# companion-faithfulness

A Claude Code skill that audits Lean 4 / Mathlib **book companions** against the
sources they formalise, and produces challenged, routed findings.

A companion is a Lean repository whose declarations claim to formalise the
numbered items of one or more textbooks. This skill finds the declarations that
are provable but are not the source's statement — an extra hypothesis, a missing
quantifier, a vacuous theorem, a proxy definition — plus the items nobody
formalised, and drives them through an adversarial review before anyone is
told about them.

**Version one is findings-only**: it stops at challenged findings, routed to
each repo's owner, and a report whose every number is recomputed from the
corpus. Fix-PR authoring, merge review and camera-ready audits are out of
scope, with the extension points left clean.

## The model in one paragraph

The unit of organisation is a **companion with a list of sources**, not a book
with a list of companions. A **workspace** holds N companions and M sources; a
differential is possible exactly where two companions' source lists overlap.
**Nothing about a source is typed in**: `cf onboard <repo>` reads the
repository, fingerprints the PDF bytes, infers the page offset and the item
kinds from the text, finds the errata page and prepares the source. Sources are
identified by content (the sha256 of the PDF) and remembered in a **registry**
shipped with the skill, so the second workspace that meets an edition knows
everything the first one learned.

## What is in here

```
SKILL.md          when to use it, the default program, the command index, the hard rules
docs/design/      the binding spec: multi-source-model.md
references/       method, vocabulary, schemas, review patterns, traps, server runbook
registry/         the shipped source registry (metadata only, never book text)
scripts/cf.py     the workspace CLI; subcommands live in scripts/cfcmd/
scripts/cflib/    shared library: workspace.yaml, registry, discovery, inference,
                  state.json, paths, item grammar, vocabulary
scripts/<stage>/  adapters, sources, pair, plan, gate, run, harvest, ledger, route,
                  detect, report
templates/        workspace.yaml, adapter.yaml, conventions.md, prompts, tend config, tasks
workflows/        Workflow-tool scripts (adjudication, challenge, verification)
tests/            stdlib unittest; baseline regressions skip unless the env names the baseline
```

The repo holds **code, templates, docs and source metadata only**. Book text,
page images, statement tables, findings and ledgers live in a workspace
directory outside it.

## Install

```sh
git clone <this repo> ~/Desktop/companion-faithfulness
ln -s ~/Desktop/companion-faithfulness ~/.claude/skills/companion-faithfulness
```

Requirements: Python 3.11+ (stdlib only; PyYAML is used when importable and a
fallback reader covers the skill's own files), `git`, and for source
preparation `pdftotext`/`pdftoppm` (poppler) and `curl` (errata). Server fleets
additionally need `tend` and ssh access to a host you are allowed to use.

Everything runs as a path, so the CLI works with or without the symlink:

```sh
python3 ~/Desktop/companion-faithfulness/scripts/cf.py --help
```

## Quick start

```sh
CF=~/Desktop/companion-faithfulness/scripts/cf.py

python3 $CF init ~/Desktop/tao-workspace
cd ~/Desktop/tao-workspace
python3 $CF onboard ~/Desktop/tao-measure-lean
```

That is the whole of stage 0 for one companion. No `--book`, no `--pdf`, no
`--offset`, no `--errata-url`. Abridged, `onboard` prints what it decided and
why (a real run on the machine this was written on):

```
repo      : /Users/jack/Desktop/tao-measure-lean
adapter   : docstring_tags                     confidence 0.93   (42 of 44 sampled
            .lean files with a docstring open one with a book tag (110 carry no docstring))

sources discovered
  tao-measure-2011-draft         sha 62d95658…  registry: skill (confirmed)
      from     graph.json#metadata.sources, CLAUDE.md#sha256_prefix,
               sources/tao-measure-theory.pdf, README.md#links
      pdf      …/tao-measure-lean/sources/tao-measure-theory.pdf   found in: repo_declared
      offset   16                                 confidence 0.996  (running_head_mode)
  tao-measure-gsm126-published   sha 64498329…  registry: skill (confirmed)
      from     formalization.yaml#sources, CLAUDE.md#sha256_prefix, …
      pdf      …/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf
      offset   16                                 confidence 0.996  (running_head_mode)
      errata   https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
               confidence 1.00

ok   prepared  sources/tao-measure-gsm126-published/   text 265 pages, 442 items, offset 16
ok   errata    …  HTTP 200  461 entries  (registry, confidence 1.00)
ok   extracted companions/tao-measure-lean/statements.jsonl   1463 rows
ok   scope     chapters [1, 2]   from the items the companions actually tag

anchor edition
  tao-measure-gsm126-published                 confidence 0.94
      anchor agreement  tao-measure-2011-draft             0.66 (202 ok / 103 mismatched)
      anchor agreement  tao-measure-gsm126-published       0.89 (271 ok / 35 mismatched)
      reason: anchor_agreement
      corroborated by prose: "Book-page anchors in docstrings follow the PUBLISHED pagination…"
  the repo BUNDLES tao-measure-2011-draft but does not anchor to it -> bundled_sources
```

Note what happened there without a flag: the repo **ships** the 2011 draft and
**anchors** to the published edition, and the decision was made by scoring the
repo's own page anchors against both editions, not by believing the sentence in
its `CLAUDE.md`. The sentence only corroborated it.

Then the rest of the program:

```sh
# 2. the second companion of the same book
python3 $CF onboard ~/Desktop/measure_theory

# then EDIT companions/*/conventions.md and have each owner confirm it:
# that list is what separates CONVENTION from CONTENT, and an unconfirmed list
# is the largest single source of false positives.

# 3. the map, at any point
python3 $CF status
python3 $CF sources list

# 4. pairing and the free findings
python3 $CF pair                              # one pairs/<source_id>/ tree per shared source
python3 $CF detect-proxies tao-measure-lean   # coverage + proxies, no model, no cost
                                              # -> corpus/detect-…-cheap/ (gated: `mechanical`)

# 5. a run (server fleets need an allowlisted host and recorded consent)
python3 $CF plan-run --preset attack-vac --companion tao-measure-lean \
    --run-id attack-vac-2026-09-20 --host <alias> --i-have-consent
python3 $CF run attack-vac-2026-09-20 --host <alias> --i-have-consent   # dry run without --execute
python3 $CF harvest attack-vac-2026-09-20 --gate
python3 $CF tally --union --agreed

# 5b. findings produced before this skill existed
#     --repo-map says which companion their repo names are: the union key holds
#     the repo, so an unmapped import joins nothing this workspace runs
python3 $CF import-corpus --run-id findings-hunt --from ~/Desktop/tao-diff-run/findings-hunt \
    --repo-map ours=tao-measure-lean
# batch-1 rows were judged against the 2011 draft, so they say so:
python3 $CF import-corpus --run-id batch1 --from ~/Desktop/tao-differential/batch1 \
    --source tao-measure-2011-draft --source-sha <that edition's full sha256> \
    --allow-edition-mismatch

# 6. challenge, route, report
python3 $CF adjudicate --companion tao-measure-lean
python3 $CF route
python3 $CF report
```

`cf status` is the map at every point: the sources table, the one line saying
where a differential is possible, each companion's `state.json`, and any source
nobody has confirmed. It prints nothing it did not read from a file, so a
missing number means a stage has not run.

## When discovery cannot decide

It stops and names the override rather than guessing.

```sh
cf sources set <id> offset=16              # inferred offset confidence was below 0.9
cf sources set <id> chapters=1,2           # pin the scope instead of deriving it
cf sources set <id> numbering=3,4          # the book numbers deeper than its headings showed
cf sources set <id> confirmed=true         # you checked a discovered entry
cf onboard <repo> --anchor-source <id>     # two editions scored too close to call
cf onboard <repo> --adapter docstring_tags # no adapter scored above 0.5
cf sources add <source> --for <companion>  # the repo names a source it does not identify
cf sources remove <id>                    # undo one that should not be here (loads a broken workspace)
cf onboard <repo> --download               # fetch from the author's own site (licence permitting)
```

Every override stamps its own trace: `offset_method: manual`,
`scope_method: manual`, `numbering_method: manual`, `completeness_method:
manual`, `confirmed: false` until a human says otherwise.

## The workspace directory model

```
<workspace>/
  workspace.yaml             companions (repo, adapter, sources[], anchor_source,
                             bundled_sources[]), sources (sha, offset, kinds, errata,
                             licence, page map, role), hosts, models, defaults,
                             budget, policy, library_paths
  registry.yaml              this workspace's overlay on the skill's source registry
  sources/<source_id>/       GITIGNORED: text.txt, pages/, images/, items.json,
                             meta.json, errata.md, errata.raw.html, errata.json
  companions/<name>/         adapter.yaml, conventions.md, statements.jsonl, state.json
  pairs/<source_id>/         pairs.jsonl, coverage.md, queue.json, targets, def-coverage,
                             anchor-check.json
  runs/<run-id>/             run.yaml, tasks/, prompts/, gate.json, tend-root/ (gitignored)
  corpus/<run-id>/           findings + RECEIPT.json  (committed: this is the baseline)
  ledger/                    accepted/refuted per companion, routed/findings.<name>.jsonl
  detect/<name>/             proxy-detection artifacts
  reports/                   tally.<run>.json, union.<name>.json, handoff.<date>.md
```

Rules the directory enforces:

- **no remote.** `cf init` runs `git init` with no remote and installs a
  pre-push hook, because the corpus quotes the source and holds unchallenged
  findings.
- **copyright.** `sources/`, `pages/`, `images/`, `crops/` and `*.pdf` are
  gitignored; only the PDF's sha256 and the metadata are recorded. `corpus/` is
  deliberately *not* ignored — it is the calibration baseline.
- **one edition per source id.** Every artifact carries `source_id` and
  `source_sha`; a mismatch is refused.
- **consent.** `cf run` refuses hosts outside `hosts.allowlist` and requires
  `--i-have-consent`, which is recorded in `run.yaml`.

## Tests

```sh
python3 -m unittest discover -s ~/Desktop/companion-faithfulness/tests
```

Regression tests that read local baselines are skipped unless you name them:

```sh
CF_BASELINE_CORPUS=~/Desktop/tao-diff-run \
CF_BASELINE_DIFF=~/Desktop/tao-differential \
CF_TML=~/Desktop/tao-measure-lean \
CF_MT=~/Desktop/measure_theory \
CF_BOOK_TXT=~/Desktop/tao-differential/book.official.txt \
python3 -m unittest discover -s ~/Desktop/companion-faithfulness/tests
```

## Status

v1: findings only. `fix`, `review-pr`, `camera-ready` and `publish-verdicts`
are designed and documented but not implemented. See `SKILL.md` for the hard
rules, `docs/design/multi-source-model.md` for the data model, and `CLAUDE.md`
for contributor conventions.
