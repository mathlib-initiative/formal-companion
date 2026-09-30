# Contributor rules for companion-faithfulness

Read `SKILL.md` first for what the skill does, then
`docs/design/stage_map.md` for the data model it is built on (workspaces,
companions, sources, the layout, the eleven stages). This file is how to change
it, and `CONTRIBUTING.md` is the same ground for a human sending a patch.

`docs/design/stage_map.md` is the **current** spec: a normative statement there
was checked against the code, and a package that must deviate changes that file
in the same commit. `docs/design/archive/` holds the design record the
implementation was written from; module docstrings cite its sections for
provenance, but nothing is bound by it and four of its statements are marked
superseded in place.

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
SKILL.md README.md CLAUDE.md CONTRIBUTING.md CHANGELOG.md POLICY.md LICENSE
docs/design/stage_map.md   the current spec; change it in the same commit as the code
docs/design/archive/       the design record it came from (historical)
docs/proposals/            designs not yet built (pdf-to-companion.md)
references/          prose the skill points agents at
registry/            the shipped source registry (metadata only)
vendor/              formal_companion_annotations/: one release of the annotation
                     format (formal-companion-annotations), byte for byte, sealed
                     by its manifest; never edited here
evals/               trigger evals: prompts as data, run by hand
bin/cf               the PATH shim; it resolves back to this checkout
scripts/cf.py        CLI entrypoint; package root for everything under scripts/
scripts/cflib/       shared library (no stage logic, no network):
                     workspace, paths, registry, discovery, inference, state,
                     items, vocab, hashing, yamlio/jsonio, adapters_api, vendored
scripts/cfcmd/       one module per command: register(subparsers) + run(args)
scripts/<stage>/     adapters, sources, pair, plan, gate, run, harvest, ledger,
                     route, detect, report, recheck, score, share, cost, pipeline
scripts/annotations/ the skill's tooling around the format, not a stage: export/
                     (the companion exporters behind `cf export`), review_tags.py
                     (`cf review-tags`), see docs/exporters.md, and sync_format.py,
                     which vendors a release; the format itself (schema, checker,
                     id rules) is vendor/formal_companion_annotations/
templates/  workflows/  tests/  .github/workflows/
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
  not add a dependency without changing this paragraph. `vendor/` is not one:
  it holds one release of formal-companion-annotations, itself standard library
  only, copied into the tree (section 2), so a clone still runs with nothing
  installed.
- No network in library code. The only exceptions are the errata fetcher
  (curl, raw HTML kept, never a summarising fetch), an explicit
  `cf onboard --download` of a PDF the author publishes himself, and the
  run/harvest commands (ssh/rsync to an allowlisted host).
- Every module starts with a docstring saying **what** it was ported from — the
  v1 campaign script, cell or workflow, named for what it did — or saying it is
  new code, and pointing at the section it implements:
  `docs/design/stage_map.md` for current behaviour, or a section of
  `docs/design/archive/` for provenance. Never a path into a transcript or a
  machine: that is unresolvable for every reader but one, and 30 docstrings once
  cited a `recovered/` directory this repo has never held. Cite a document that
  is in the tree — 44 docstrings once cited four design documents nobody had
  written — and `tests/test_rf_docs.py` and `tests/test_rf_scrub.py` fail on a
  citation that resolves nowhere.
- Small functions, real docstrings, type hints where they help. Prefer
  correctness over breadth; mark anything unfinished with `TODO` and say so in
  the handoff.

## 4. The rules that must not drift

- **One vocabulary.** `scripts/cflib/vocab.py` is the only definition of the
  enums; `references/vocabulary.md` documents them. A new value is a change to
  both, plus a mapping entry for the converted baseline. The exception is the
  annotation format: its enums, and nine sets it shares with the skill, are
  defined by the vendored `formal_companion_annotations.vocab`; `cflib.vocab`
  keeps copies of the nine for the skill's stages, and
  `tests/test_vendored_format.py` checks that they equal the format's.
- **One item grammar, one namespace rule.** `scripts/cflib/items.py`. Never
  write another item regex; never let an unparseable id become a key. Parsing
  is whitespace-tolerant and produces a *reject list* callers must report. An
  item is the pair `(source_id, item)`: the kind list is the source's, two
  sources may legitimately both have an `Exercise 1.3.19`, and a function that
  takes a bare item string takes a `source_id` too. One string spelling only:
  `qualified_item()` / `split_qualified()` around `::`.
- **Two normalisers, and they are not interchangeable.** `items.clean()` is the
  ID transformation (whitespace only, hashed by `finding_id`, frozen — changing
  it re-keys the whole corpus). `items.join_key()` is the KEY two corpus
  generations join on: for a numbered label it *is* `clean()`, and for an
  unnumbered one it is the strict grammar, so the v1 corpus's
  `"In-text, book p. 71"` and the extraction's `"In-text p.71"` are one item.
  Anything that MATCHES on an item matches on `join_key` (`tally.
  collapse_to_book_item`, `ledger.compat.item_join_key`); anything that WRITES
  or HASHES a label leaves it as written. An in-text label the grammar refuses
  keeps itself as its own key and is reported, never guessed onto an item.
- **Nothing about a source is typed in.** Sources, editions, offsets, kinds,
  errata URLs and scope are discovered (`cflib/discovery.py`) and inferred
  (`cflib/inference.py`). A new fact a repo can expose is a new *signal*, not a
  new flag. Overrides stay in `cf sources set` / `cf sources add`, and each one
  stamps its own `*_method: manual`.
- **Low confidence is a refusal.** Below `MIN_OFFSET_CONFIDENCE` (0.9),
  `MIN_ANCHOR_AGREEMENT` (0.80), `MIN_ANCHOR_MARGIN` (0.05),
  `MIN_ADAPTER_CONFIDENCE` (0.5) or `MIN_ITEM_COMPLETENESS` (0.6), the command
  stops and names the flag that settles it. A plausible default here is worse
  than no answer.
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
  re-key on prose. The definition is inlined in exactly one other place,
  `gate/verify_findings.py`, because the gate is transferred to a run host where
  `cflib` is not importable; the two change in lockstep and
  `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` is the enforcement —
  it also pins the compat shims' `finding_id`, which now delegate rather than
  inline.
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

Subclass `adapters.api.Adapter` (which is `cflib.adapters_api.Adapter` plus the
repo/glob/config helpers every concrete adapter needs) in a new file under
`scripts/adapters/`, set `kind`, and implement:

- `detect(cls, repo) -> (score, evidence)` — a **classmethod**: how confidently
  this repo uses the convention, and the measurement behind the number.
  `cflib.discovery.detect_scores` calls it on every class in
  `adapters.api.registry()` and `cf onboard` picks the highest, refusing below
  0.5, so detection is a loop over the adapters and a new convention adds no
  line to the core. Count a denominator that means something (files that *carry
  a docstring*, not every `.lean` file in a tree that is mostly generated
  stubs), keep this convention's globs and score constants in this file, and put
  any per-repo spelling the measurement found into `evidence` — `cf onboard`
  writes those keys into the generated `adapter.yaml`, which is how
  `tag.require_backticks` stopped being a hard-coded default.
- `explain(cls, repo) -> [str]` — one "looked for / found" line per signal this
  convention keys on, for a repo it did *not* claim, naming the override that
  would settle it. These lines are the whole content of the refusal a user sees
  when nothing matched.
- `discover_sources(repo) -> [SourceClaim]` — the signals this convention
  exposes, each carrying the file it came from. Never invent a `sha256`; a
  truncated sha in prose is a `sha256_prefix`, which is a candidate. `cf onboard`
  merges what this ADDS to the seven repo signals, and a convention that IS its
  own manuscript says so with a claim whose `files` name it — absolute paths in
  reading order, which onboarding concatenates in that order and fingerprints
  (`blueprint_tex`, `references/onboarding-contract.md` §0.1).
- `dry_run(repo)` and `extract(repo, *, sources={id: sha})` — rows follow
  `ROW_REQUIRED_FIELDS`, which includes `source_id` and `source_sha`;
  declarations are fully qualified (track namespaces); anonymous examples get a
  synthetic `example@<file>:<line>` id so they can be attributed.
- `source_for(...)` — which source a row belongs to, from `adapter.yaml`'s
  `sources: {default, map}`. A row that matches no rule is an error naming the
  file and the tag, never a silent default.
- extend `key_inputs()` with every export sha that changes the result, and the
  shas of the sources it is bound to.

Decorate the class with `@adapters.api.register` and add its module to the
import inside `adapters.api.registry()` (that import is what makes the kind
exist before anything asks for it), then add the kind to
`vocab.IMPLEMENTED_ADAPTER_KINDS` only when it really works — `ADAPTER_KINDS`
is the wider vocabulary an `adapter.yaml` may legally say, and a test pins the
narrower set against what `adapters.api.registry()` provides. `cf onboard` also
needs a row in `cfcmd.onboard.DEFAULTS` (glob, anchor, granularity) and a
default block in `templates/adapter.yaml.example` for anything it configures.

`docs/adding-an-adapter.md` is the same list as a walkthrough against the
`notes-a` fixture, and it says what a new **book** needs instead — which is
nothing from here, except the hand-written
`scripts/detect/mathlib_stages/<source_id>.json` that `detect-proxies --lean`
reads.

## 7. Tests

`python3 -m unittest discover -s tests`. Stdlib `unittest` only. Tests that
read local baselines read their paths from `CF_BASELINE_CORPUS`,
`CF_BASELINE_DIFF`, `CF_TML`, `CF_MT`, `CF_BOOK_TXT` (and the exporter
reproductions from `CF_TAO_PDF`, `CF_TML_SITE`, `CF_MT_BUILD`,
`CF_M3_REFERENCE_DIR`) and **skip cleanly** when those are unset — the suite
must pass on a machine that has none of them.
The M1 tests read five more: `CF_TAO_PDF` (the published Tao PDF for
`tests/test_m1_calibration.py`, which skips without it), `CF_M1_DOWNLOAD=1`
(opt in to downloading that PDF; nothing is fetched by default),
`CF_M1_ENFORCE_BARS=1` (assert the proposal's recall bars instead of only
reporting them), `CF_M1_REPORT=<file>` (write the calibration figures as JSON)
and `CF_TEST_TMP` (the parent of the M1 tests' temporary directories).
Discovery and inference tests use fixture repos and synthetic text: no network,
and the only PDFs read are the fixture PDFs the suite generates in a temporary
directory. `tests/_hermetic.py` is what makes that true: importing it pins HOME
at a temporary directory, so `library_paths`' `~/Downloads` entry cannot reach
the developer's own files. Every test module that builds a workspace imports it
as its first import — `tests/conftest.py` is a pytest hook, and the command
above loads no such hook. Never write outside a temporary directory, and never
into a companion repo.

## 8. Git

Commit your own files with a message that says what changed and why. Do not
push, do not rewrite history, do not commit anything from a workspace
directory, and do not run `git add -A` — this repo is edited by several agents
at once; add the paths you own. End a commit message with the
`Co-Authored-By:` line for the model that wrote the change.

## End-to-end demonstration

`tests/e2e/multisource.sh` rebuilds a workspace from nothing: it onboards the two
Tao companions with no flags, generates a public-domain booklet and two
synthetic companions of it, and checks about seventy artifacts (never log
lines) against pinned figures. Run it with `bash tests/e2e/multisource.sh
[--offline] [<workspace dir>]`; with no workspace argument it makes one under
`$TMPDIR`, never inside the checkout — `cf init` refuses a workspace under the
skill repo. It needs poppler's `pdftotext`, the two companion checkouts named
by `CF_TML` and `CF_MT` (plus the optional `CF_BASELINE_CORPUS`) — it defaults to
no path of its own and says what to set — and, unless `--offline`, network access
for the errata fetch. Its generated
fixtures are gitignored.
