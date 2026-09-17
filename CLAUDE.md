# Contributor rules for companion-faithfulness

Read `SKILL.md` first for what the skill does, then
`docs/design/multi-source-model.md` for the data model it is built on
(workspaces, companions, sources, discovery, inference). This file is how to
change it.

## 1. What may live here

Code, templates, documentation and **source metadata**. **Nothing else.** No
book text, no page images or crops, no PDFs, no statement tables, no findings,
no ledgers, no host names, agentbox profiles, session ids or repo owners'
identities. All of that belongs to a workspace directory, which is a separate
local git repo with no remote. Examples in templates and docs are synthetic.

`registry/sources.yaml` is the one deliberate exception, and it is a narrow
one: titles, editions, shas, byte counts, page offsets, URLs, page maps and
licence notes — facts *about* an edition, never a line *of* it. See
`registry/README.md`.

Both first-target companions are private repositories. Do not paste their Lean
source or their owners' details into this repo.

## 2. Layout

```
SKILL.md README.md CLAUDE.md
docs/design/         binding specs; a package that must deviate changes the spec first
references/          prose the skill points agents at
registry/            the shipped source registry (metadata only)
scripts/cf.py        CLI entrypoint; package root for everything under scripts/
scripts/cflib/       shared library (no stage logic, no network):
                     workspace, paths, registry, discovery, inference, state,
                     items, vocab, hashing, yamlio/jsonio, adapters_api
scripts/cfcmd/       one module per command: register(subparsers) + run(args)
scripts/<stage>/     adapters, sources, pair, plan, gate, run, harvest, ledger,
                     route, detect, report
templates/  workflows/  tests/
```

Run everything by path: `python3 scripts/cf.py <command> …`. `cf.py` puts
`scripts/` on `sys.path` and discovers `cfcmd` modules with pkgutil, so a new
command is a new file — never a new entrypoint.

**`scripts/sources/` is code, `sources/` is workspace data.** The repo's own
`.gitignore` carries the workspace patterns, so `sources/` would hide the whole
stage-1 package from git; the `!scripts/sources/` negation is what stops it.
Those two lines move together or not at all.

## 3. Python

- 3.11+, standard library only. PyYAML is used when importable; `cflib.yamlio`
  has a fallback reader for the subset the skill writes — including the nested
  `sources:` / `page_map_to:` maps and the folded scalars the registry uses. Do
  not add a dependency without changing this paragraph.
- No network in library code. The only exceptions are the errata fetcher
  (curl, raw HTML kept, never a summarising fetch), an explicit
  `cf onboard --download` of a PDF the author publishes himself, and the
  run/harvest commands (ssh/rsync to an allowlisted host).
- Every module starts with a docstring naming the original it was ported from
  (path plus transcript line) or saying it is new code, and pointing at the
  spec section it implements.
- Small functions, real docstrings, type hints where they help. Prefer
  correctness over breadth; mark anything unfinished with `TODO` and say so in
  the handoff.

## 4. The rules that must not drift

- **One vocabulary.** `scripts/cflib/vocab.py` is the only definition of the
  enums; `references/vocabulary.md` documents them. A new value is a change to
  both, plus a mapping entry for the converted baseline.
- **One item grammar, one namespace rule.** `scripts/cflib/items.py`. Never
  write another item regex; never let an unparseable id become a key. Parsing
  is whitespace-tolerant and produces a *reject list* callers must report. An
  item is the pair `(source_id, item)`: the kind list is the source's, two
  sources may legitimately both have an `Exercise 1.3.19`, and a function that
  takes a bare item string takes a `source_id` too. One string spelling only:
  `qualified_item()` / `split_qualified()` around `::`.
- **Nothing about a source is typed in.** Sources, editions, offsets, kinds,
  errata URLs and scope are discovered (`cflib/discovery.py`) and inferred
  (`cflib/inference.py`). A new fact a repo can expose is a new *signal*, not a
  new flag. Overrides stay in `cf sources set` / `cf sources add`, and each one
  stamps its own `*_method: manual`.
- **Low confidence is a refusal.** Below `MIN_OFFSET_CONFIDENCE` (0.9),
  `MIN_ANCHOR_AGREEMENT` (0.80), `MIN_ANCHOR_MARGIN` (0.05) or
  `MIN_ADAPTER_CONFIDENCE` (0.5), the command stops and names the flag that
  settles it. A plausible default here is worse than no answer.
- **One state file per companion.** `companions/<name>/state.json`, written
  through `cflib.state` (atomic read-modify-write), version 2: a per-source
  `{sha, rows, items}` map and an `anchor_source`, never one top-level sha.
  Every stage writes its counts there; `cf status` prints only what it read.
- **Idempotence.** Every command may be re-run. Extraction keys on (repo
  commit, adapter key including any ledger/export sha); planning is
  deterministic given `run.yaml`; harvest diffs against the previous receipt;
  a second `cf onboard` of the same repo re-derives rather than duplicating.
- **Stamp the source.** Every artifact that quotes a source or judges an anchor
  carries `source_id` **and** `source_sha`; mismatches are refused, not warned
  about.
- **Findings are content-addressed.** `finding_id = sha256(run_id, source_id,
  item, kind, sorted unfaithful_decls)` — `cflib.hashing.finding_id`, five
  parts joined with `\n`, the item passed through `items.clean()` first. Never
  re-key on prose. The definition is inlined in four other places
  (`gate/verify_findings.py`, `sources/compat.py`, `ledger/compat.py`,
  `detect/_compat.py`) because they run where `cflib` is not importable; all
  five change in lockstep and
  `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` is the enforcement.
- **The gate runs out of tree.** It is never executed from the worktree a
  worker can edit, and pre-merge checks the changed-file set.
- **Consent and allowlist.** Anything that copies sources or source text to
  another machine goes through `Workspace.check_host` and an explicit
  `--i-have-consent` recorded in `run.yaml`.
- **Never write to a companion repo.** Discovery reads a repo's metadata,
  prose, caches and PDFs; it writes nothing back, and no stage ever does.

## 5. Adding a command

1. `scripts/cfcmd/<name>.py` with `register(subparsers)` and `run(args)`.
2. Load the workspace with `cflib.workspace.from_args(args)`; raise
   `cflib.errors.CfError(msg, hint=…)` — or `WorkspaceError` / `SourceError` /
   `DiscoveryError` — for anything the user must fix.
3. Write outputs with `cflib.jsonio` / `cflib.yamlio` (atomic) into paths from
   `cflib.paths.WorkspacePaths` — never build a path by string concatenation.
   Per-source paths take the `source_id`: `source_text(id)`, `items_json(id)`,
   `pairs_dir(id)`.
4. Record counts with `cflib.state.update` / `set_section` / `record_run` /
   `source_section`.
5. Add it to the command index in `SKILL.md` and a test in `tests/`.

## 6. Adding an adapter

Subclass `cflib.adapters_api.Adapter` in `scripts/adapters/`, set `kind`, and
implement:

- `detect(repo) -> float` — how confidently this repo uses the convention.
  Return a real score; `cf onboard` picks the highest and refuses below 0.5.
  Count a denominator that means something (files that *carry a docstring*, not
  every `.lean` file in a tree that is mostly generated stubs).
- `discover_sources(repo) -> [SourceClaim]` — the signals this convention
  exposes, each carrying the file it came from. Never invent a `sha256`; a
  truncated sha in prose is a `sha256_prefix`, which is a candidate.
- `dry_run(repo)` and `extract(repo, *, sources={id: sha})` — rows follow
  `ROW_REQUIRED_FIELDS`, which includes `source_id` and `source_sha`;
  declarations are fully qualified (track namespaces); anonymous examples get a
  synthetic `example@<file>:<line>` id so they can be attributed.
- `source_for(...)` — which source a row belongs to, from `adapter.yaml`'s
  `sources: {default, map}`. A row that matches no rule is an error naming the
  file and the tag, never a silent default.
- extend `key_inputs()` with every export sha that changes the result, and the
  shas of the sources it is bound to.

Register it in `scripts/adapters/__init__.py`'s `load()`, and add the kind to
`vocab.IMPLEMENTED_ADAPTER_KINDS` only when it really works — `ADAPTER_KINDS`
is the wider vocabulary an `adapter.yaml` may legally say, and a test pins the
narrower set against what `adapters.api.registry()` provides.

## 7. Tests

`python3 -m unittest discover -s tests`. Stdlib `unittest` only. Tests that
read local baselines read their paths from `CF_BASELINE_CORPUS`,
`CF_BASELINE_DIFF`, `CF_TML`, `CF_MT`, `CF_BOOK_TXT` and **skip cleanly** when
those are unset — the suite must pass on a machine that has none of them.
Discovery and inference tests use fixture repos and synthetic text: no network,
no PDF. Never write outside a temporary directory, and never into a companion
repo.

## 8. Git

Commit your own files with a message that says what changed and why. Do not
push, do not rewrite history, do not commit anything from a workspace
directory, and do not run `git add -A` — this repo is edited by several agents
at once; add the paths you own. End a commit message with the
`Co-Authored-By:` line for the model that wrote the change.
