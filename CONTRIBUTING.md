# Contributing

This repository is a Claude Code skill and a small stdlib-only Python CLI. It
is also edited by several agents at once, which is why `CLAUDE.md` exists:
that file is the same ground as this one, written for an agent. Read whichever
fits, and `docs/design/stage_map.md` for the data model.

## Getting set up

```sh
git clone <this repo's url> <dir> && cd <dir>
python3 scripts/cf.py doctor          # python 3.11+, git, poppler, curl
python3 -m unittest discover -s tests  # the documented gate; no env vars needed
```

There is nothing to install. `pyproject.toml` carries metadata only — the skill
is used from a checkout, deliberately, so that the skill directory a session
loads and the code a command runs are the same tree.

## The invariants

These are the ones a change cannot quietly break. `CLAUDE.md` §4 has them all;
these are the four that bite hardest.

1. **Stdlib only.** PyYAML is used when importable and `cflib.yamlio` has a
   fallback reader for the subset this skill writes. A new import that is not
   in the standard library is a change to the whole design, not a detail. CI
   runs the suite once with PyYAML absent for that reason.
2. **`finding_id` and `items.clean()` are frozen.** They are hashed into a
   committed corpus, and `finding_id` is inlined once more, in
   `scripts/gate/verify_findings.py`, because the gate is transferred to a run
   host where `cflib` cannot be imported. The two change in lockstep or not at
   all; see `CHANGELOG.md` for what that costs.
3. **A companion repository is read-only data.** Never write to one, never
   build one, and run every `git` against one through `cflib.gitsafe` — a repo
   that can set `core.fsmonitor` can name a program for you to execute. One
   command is exempt, narrowly: `cf overlay init`, `cf overlay sync` and
   `cf overlay export` write `<companion>/pdf-overlay/` and the annotation
   directory `<companion>/annotations/` and nowhere else, refusing either when
   it is a symlink or resolves outside the companion, and running no git
   there at all (`docs/overlay.md` rule 5).
4. **Metadata only in this repo.** No book text, no page images, no statement
   tables, no findings, no host names, session ids or repo owners' identities.
   Examples in templates and docs are synthetic. `POLICY.md` is the whole rule
   and `cf sources validate-registry` is the mechanical half.

## How to add a …

| | where it goes |
|---|---|
| **command** | `scripts/cfcmd/<name>.py` with `register(subparsers)` + `run(args)`; paths from `cflib.paths`, errors as `CfError(msg, hint=…)`, counts through `cflib.state`. Add the row to `SKILL.md`'s command index — `tests/test_rf_docs.py` fails without it — and a test. `CLAUDE.md` §5 |
| **adapter** (a companion convention) | a class under `scripts/adapters/`, registered with `@adapters.api.register`, imported inside `adapters.api.registry()`, and added to `vocab.IMPLEMENTED_ADAPTER_KINDS` only when it really works. Detection is that class's own `detect(cls, repo) -> (score, evidence)`. `CLAUDE.md` §6 |
| **preset** (a run shape) | `scripts/plan/presets.py` is the live table; `templates/PRESETS.md` is the record of how the five 2026-09 runs differed, and both move together. A preset with `bridge: none` is not runnable and `cf plan-run` exits 2 on it |
| **registry entry** (a new edition) | see below — it is the one file contributed by hand |
| **vocabulary value** | `scripts/cflib/vocab.py` **and** `references/vocabulary.md`, plus a mapping entry for the converted baseline |

## Adding an edition to the registry

The registry is the contribution surface: adding a book is how someone else's
work reaches this tool. The full shape is in `registry/README.md` and the
licence rule in `POLICY.md`; the short version is

```sh
cf onboard <the repo that names it>       # discovery writes a workspace overlay row
cf sources show <id>                      # check the edition label, offset and errata
cf sources set <id> confirmed=true        # you have checked it
cf sources promote <id>                   # writes registry-patch.<id>.yaml
cf sources validate-registry              # run it in the checkout you edited
```

then open a PR that pastes the patch's entry into `registry/sources.yaml`.
Nothing writes the shipped registry from a workspace, on purpose: an entry in
that file is a fact for every future workspace, so a human reviews the diff.

## Tests

```sh
python3 -m unittest discover -s tests            # the gate CI runs
python3 -m pytest -q tests/                      # same suite, with a temporary HOME
bash tests/e2e/synthetic.sh                      # the end-to-end demonstration
```

- **Every fix needs a test that fails before it.** Say how you confirmed the
  "fails before" half in the PR.
- Tests that read local baselines take their paths from `CF_BASELINE_CORPUS`,
  `CF_BASELINE_DIFF`, `CF_TML`, `CF_MT`, `CF_BOOK_TXT` and **skip cleanly**
  when those are unset: the suite must be green on a machine that has none of
  them.
- Never write outside a temporary directory, and never into a companion repo.
- The pinned regression figures live in `tests/baseline/regression-figures.json`,
  which carries each value, the assertion that pins it and why it is what it is
  (the teorth collapsed counts are 41/19 after the 2026-09-18 de-conflation).
  They do not move unless the finding is that they were wrong. If a tightened
  check rejects baseline files, **measure** how many and why, then tune or
  record a documented escape; never silently loosen and never silently drop.

## Documentation

`SKILL.md` is what a model loads instead of reading the code, so a wrong line
in it is worse than a missing one. `tests/test_rf_docs.py` enforces the parts
that can be checked mechanically: every `cf <command>` and `--flag` a document
writes must parse against the real argparse tree, every registered command
must have an index row, every cited repo path must exist, and a reference over
150 lines carries a table of contents. Keep `SKILL.md` under ~500 lines by
moving detail into `references/`.

## Pull requests

- One logical change per commit, with a message that says what changed and
  **why**; the repo's voice is plain, past-tense and specific about the failure
  a change prevents.
- Do not commit anything from a workspace directory, and do not `git add -A`.
- End a commit message with the `Co-Authored-By:` line naming the model that
  wrote the change, if one did.
- Green on `python3 -m unittest discover -s tests` before you open it.
- Reviewers: no private-companion data or book text in any commit or message
  (item 4 above); `CF_SCRUB_RANGE=origin/master..HEAD python3 -m pytest -q
  tests/test_rf_scrub.py` scans every commit the branch adds.
