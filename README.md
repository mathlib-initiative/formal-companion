# companion-faithfulness

A Claude Code skill that audits Lean 4 / Mathlib **book companions** against the
book they formalise, and produces challenged, routed findings.

A companion is a Lean repository whose declarations claim to formalise a
specific textbook's numbered items. This skill finds the declarations that are
provable but are not the book's statement — an extra hypothesis, a missing
quantifier, a vacuous theorem, a proxy definition — plus the items nobody
formalised, and drives them through an adversarial review before anyone is
told about them.

**Version one is findings-only**: it stops at challenged findings, routed to
each repo's owner, and a report whose every number is recomputed from the
corpus. Fix-PR authoring, merge review and camera-ready audits are out of
scope, with the extension points left clean.

## What is in here

```
SKILL.md          when to use it, the default program, the command index, the hard rules
references/       method, vocabulary, schemas, review patterns, traps, server runbook
scripts/cf.py     the campaign CLI; subcommands live in scripts/cfcmd/
scripts/cflib/    shared library: campaign.yaml, state.json, paths, item grammar, vocabulary
scripts/<stage>/  adapters, book, pair, plan, gate, run, harvest, ledger, route, detect, report
templates/        campaign.yaml, adapter.yaml, conventions.md, prompts, tend config, task yaml
workflows/        Workflow-tool scripts (adjudication, challenge, verification)
tests/            stdlib unittest; baseline regressions skip unless the env names the baseline
```

The repo holds **code, templates and docs only**. Book text, page images,
statement tables, findings and ledgers live in a campaign directory outside it.

## Install

```sh
git clone <this repo> ~/Desktop/companion-faithfulness
ln -s ~/Desktop/companion-faithfulness ~/.claude/skills/companion-faithfulness
```

Requirements: Python 3.11+ (stdlib only; PyYAML is used when importable and a
fallback reader covers the skill's own files), `git`, and for book preparation
`pdftotext`/`pdftoppm` (poppler). Server fleets additionally need `tend` and
ssh access to a host you are allowed to use.

Everything runs as a path, so the CLI works with or without the symlink:

```sh
python3 ~/Desktop/companion-faithfulness/scripts/cf.py --help
```

## Quick start: the Tao measure-theory campaign

Two companions of Terence Tao's *An Introduction to Measure Theory*:
`tao-measure-lean` (book tags in docstrings) and `measure_theory` (an
attribution table). Both are private; neither is written to.

```sh
CF=~/Desktop/companion-faithfulness/scripts/cf.py

# 1. one campaign per book, outside every repo, local git, no remote
python3 $CF init ~/Desktop/tao-differential \
    --book tao-measure \
    --pdf ~/Desktop/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf \
    --offset 16 \
    --errata-url https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
export CF_CAMPAIGN=~/Desktop/tao-differential

# 2. the book: text, page map, items.json, live errata (raw HTML kept)
python3 $CF book tao-measure

# 3. the companions
python3 $CF onboard ours --repo ~/Desktop/tao-measure-lean --adapter docstring_tags
python3 $CF onboard adam --repo ~/Desktop/measure_theory  --adapter attribution_table \
    --owner external --write-policy read_only
# then EDIT companions/*/conventions.md and have each owner confirm it:
# that list is what separates CONVENTION from CONTENT, and an unconfirmed list
# is the largest single source of false positives.

# 4. tables, pairing, the free findings
python3 $CF extract
python3 $CF pair
python3 $CF detect-proxies ours          # coverage + proxies, no model, no cost
                                        # -> corpus/detect-ours-cheap/ (gated: `mechanical`)
python3 $CF status

# 5. a run (server fleets need an allowlisted host and recorded consent)
python3 $CF plan-run --preset attack-vac --companion ours --host <alias> --i-have-consent
python3 $CF run attack-vac-ours --host <alias> --i-have-consent   # dry run without --execute
python3 $CF harvest attack-vac-ours --gate
python3 $CF tally --union --agreed

# 5b. findings produced before this skill existed
python3 $CF import-corpus --run-id findings-hunt --from ~/Desktop/tao-diff-run/findings-hunt \
    --allow-edition-mismatch   # v1 rows carry no book_sha until convert_v1 stamps them

# 6. challenge, route, report
python3 $CF adjudicate --companion ours
python3 $CF route
python3 $CF report
```

`cf status` is the map at every point: it prints each companion's
`state.json` and nothing else, so a missing number means a stage has not run.

## The campaign directory model

```
<campaign>/
  campaign.yaml              book id, pinned sha256, page offset, errata url,
                             companions, host allowlist, models, defaults, budget
  book/<book-id>/            GITIGNORED: book.txt, pages/, images/, items.json, errata
  companions/<name>/         adapter.yaml, conventions.md, statements.jsonl, state.json
  pairs/                     pairs.jsonl, coverage.md, queue.json, def-coverage, anchors
  runs/<run-id>/             run.yaml, tasks/, prompts/, gate.json, tend-root/ (gitignored)
  corpus/<run-id>/           findings + RECEIPT.json  (committed: this is the baseline)
  ledger/                    accepted/refuted per companion, routed/findings.<name>.jsonl
  detect/<name>/             proxy-detection artifacts
  reports/                   tally.<run>.json, union.<name>.json, handoff.<date>.md
```

Rules the directory enforces:

- **no remote.** `cf init` runs `git init` with no remote and installs a
  pre-push hook, because the corpus quotes the book and holds unchallenged
  findings.
- **copyright.** `book/`, `pages/`, `images/`, `crops/` and `*.pdf` are
  gitignored; only the PDF's sha256 is recorded. `corpus/` is deliberately
  *not* ignored — it is the calibration baseline.
- **one edition.** Every artifact carries `book_sha`; a mismatch is refused.
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
rules and `CLAUDE.md` for contributor conventions.
