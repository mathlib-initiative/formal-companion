# Contributor rules for companion-faithfulness

Read `SKILL.md` first for what the skill does; this file is how to change it.

## 1. What may live here

Code, templates and documentation. **Nothing else.** No book text, no page
images or crops, no PDFs, no statement tables, no findings, no ledgers, no
host names, agentbox profiles, session ids or repo owners' identities. All of
that belongs to a campaign directory, which is a separate local git repo with
no remote. Examples in templates and docs are synthetic.

Both first-target companions are private repositories. Do not paste their Lean
source or their owners' details into this repo.

## 2. Layout

```
SKILL.md README.md CLAUDE.md
references/          prose the skill points agents at
scripts/cf.py        CLI entrypoint; package root for everything under scripts/
scripts/cflib/       shared library (no stage logic, no network)
scripts/cfcmd/       one module per command: register(subparsers) + run(args)
scripts/<stage>/     adapters, book, pair, plan, gate, run, harvest, ledger,
                     route, detect, report
templates/  workflows/  tests/
```

Run everything by path: `python3 scripts/cf.py <command> …`. `cf.py` puts
`scripts/` on `sys.path` and discovers `cfcmd` modules with pkgutil, so a new
command is a new file — never a new entrypoint.

## 3. Python

- 3.11+, standard library only. PyYAML is used when importable; `cflib.yamlio`
  has a fallback reader for the subset the skill writes. Do not add a
  dependency without changing this paragraph.
- No network in library code. The only exceptions are the errata fetcher
  (curl, raw HTML kept, never a summarising fetch) and the run/harvest commands
  (ssh/rsync to an allowlisted host).
- Every module starts with a docstring naming the original it was ported from
  (path plus transcript line) or saying it is new code.
- Small functions, real docstrings, type hints where they help. Prefer
  correctness over breadth; mark anything unfinished with `TODO` and say so in
  the handoff.

## 4. The rules that must not drift

- **One vocabulary.** `scripts/cflib/vocab.py` is the only definition of the
  enums; `references/vocabulary.md` documents them. A new value is a change to
  both, plus a mapping entry for the converted baseline.
- **One item grammar.** `scripts/cflib/items.py`. Never write another item
  regex; never let an unparseable id become a key. Parsing is
  whitespace-tolerant and produces a *reject list* callers must report.
- **One state file per companion.** `companions/<name>/state.json`, written
  through `cflib.state` (atomic read-modify-write). Every stage writes its
  counts there; `cf status` prints only what it read.
- **Idempotence.** Every command may be re-run. Extraction keys on (repo
  commit, adapter key including any ledger/export sha); planning is
  deterministic given `run.yaml`; harvest diffs against the previous receipt.
- **Stamp the edition.** Every artifact that quotes the book or judges an
  anchor carries `book_sha`; mismatches are refused, not warned about.
- **Findings are content-addressed.** `finding_id = sha256(run_id, item, kind,
  sorted unfaithful_decls)` — `cflib.hashing.finding_id`. Never re-key on
  prose.
- **The gate runs out of tree.** It is never executed from the worktree a
  worker can edit, and pre-merge checks the changed-file set.
- **Consent and allowlist.** Anything that copies sources or book text to
  another machine goes through `Campaign.check_host` and an explicit
  `--i-have-consent` recorded in `run.yaml`.

## 5. Adding a command

1. `scripts/cfcmd/<name>.py` with `register(subparsers)` and `run(args)`.
2. Load the campaign with `cflib.campaign.from_args(args)`; raise
   `cflib.errors.CfError(msg, hint=…)` for anything the user must fix.
3. Write outputs with `cflib.jsonio` / `cflib.yamlio` (atomic) into paths from
   `cflib.paths.CampaignPaths` — never build a path by string concatenation.
4. Record counts with `cflib.state.update` / `set_section` / `record_run`.
5. Add it to the command index in `SKILL.md` and a test in `tests/`.

## 6. Adding an adapter

Subclass `cflib.adapters_api.Adapter` in `scripts/adapters/`, set `kind`,
implement `dry_run(repo)`, `extract(repo, book_sha)` and extend `key_inputs()`
with every export sha that changes the result. Register it in
`scripts/adapters/__init__.py`'s `load(adapter_yaml)`. Rows follow
`ROW_REQUIRED_FIELDS`; declarations are fully qualified (track namespaces);
anonymous examples get a synthetic `example@<file>:<line>` id so they can be
attributed.

## 7. Tests

`python3 -m unittest discover -s tests`. Stdlib `unittest` only. Tests that
read local baselines read their paths from `CF_BASELINE_CORPUS`,
`CF_BASELINE_DIFF`, `CF_TML`, `CF_MT`, `CF_BOOK_TXT` and **skip cleanly** when
those are unset — the suite must pass on a machine that has none of them.
Never write outside a temporary directory.

## 8. Git

Commit your own files with a message that says what changed and why. Do not
push, do not rewrite history, do not commit anything from a campaign
directory, and do not run `git add -A` — this repo is edited by several agents
at once; add the paths you own. End a commit message with the
`Co-Authored-By:` line for the model that wrote the change.
