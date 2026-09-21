# Changelog

`cf --version` prints the number in `scripts/cflib/__init__.py`. This file
says what a change to it promises.

## What a version number means here

The thing that must not break is **a corpus**. `corpus/<run>/` is committed,
content-addressed and used as a calibration baseline, so the compatibility
promise is about the keys a corpus and a ledger are joined on, not about the
Python API.

- **Major** — a corpus or ledger written by an older version no longer joins.
  The two frozen definitions are `cflib.hashing.finding_id` (five parts,
  `\n`-joined, the item through `items.clean()` first) and
  `cflib.items.clean()` itself. Changing either re-keys every finding ever
  recorded, so it is a major version and a documented migration, never a
  bugfix. `finding_id` is defined twice — `cflib.hashing.finding_id` and the
  copy `gate/verify_findings.py` inlines because `cf run` transfers it to a
  host with no cflib; the three compat shims delegate.
  `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` pins all of it.
- **Minor** — a new command, a new stage artifact, a new optional row key, a
  new adapter kind, a new registry field. A reader of an older artifact keeps
  working; a reader of a *newer* artifact may need the newer version.
- **Patch** — a fix that changes no artifact shape.

Two things that are deliberately *not* covered: the pinned regression figures
(they are measurements of one corpus, and a change to them is a finding, not a
release), and `docs/design/archive/`, which is history and does not move.

## Unreleased

Nothing is released yet: there is no tag, and the version has been `0.2.0`
since the multi-source rewrite. The entries below exist so that the first
release has something to say.

### Added
- `docs/design/stage_map.md`: the current data model, workspace layout and
  stage map, replacing the archived design record as the spec.
- `CONTRIBUTING.md`, `POLICY.md`, this file, and `evals/` (trigger evals as
  data).
- `cf sources validate-registry` and `cf sources promote <id>`: the registry
  is the contribution surface, and it now has a check a contributor can run
  and a command that writes their entry as a reviewable patch.
- `tests/test_rf_docs.py`: the docs-to-CLI contract — every `cf` command and
  flag a document names must parse, every command must be in SKILL.md's index,
  and every cited repo path must exist.
- `tests/test_rf_scrub.py`: the whole-tracked-tree check for machine-local
  content — absolute home paths, one person's home layout, host names,
  addresses, session and Workflow ids, and citations into a transcript
  directory this repo has never held — and for citations of files that are
  not here. The rules and their allowlist are spelled out in that file.

### Changed
- `cf harvest` and `cf import-corpus` run the findings gate **by default**;
  `--no-gate` is the escape and records `gate: null`, which `cf tally` then
  names. Previously the gate was opt-in and `SKILL.md` did not mention it.
- the 1,941-line design record moved to
  `docs/design/archive/multi-source-model.md` with a banner; four statements in
  it are marked superseded in place.
- a multi-line warning goes to stderr as one block (`cflib.log.warn_block`).
  The continuation lines of fourteen warnings — including `cf status`'s
  unconfirmed-sources list, whose per-source rows carry the sha and the origin
  — used to print on stdout, so `cf … 1>out 2>err` split each warning from its
  own evidence.
- `tests/e2e/multisource.sh` takes `CF_TML` and `CF_MT` from the environment
  and names them when they are unset, instead of defaulting to two checkouts
  that exist on one machine.

### Fixed
- 44 docstring citations pointed at four design documents that were never
  written, and the reference docs sent a stuck reader to `cf ingest`, a command
  that never existed; `book/fetch_errata.py` and several other stale paths went
  the same way.
- seven of those citations were rewritten into quotations of
  `docs/design/stage_map.md`, which did not carry the sentences: the three stage
  gates they lean on (the Stage 7 ledger gate, Stage 8's corpus join, Stage 9's
  routing rule) are written down there now, `scripts/adapters/base.py` points at
  the document that really holds the statement-row field list, and the contract
  test checks a quotation as well as a path.
- `cf sources validate-registry` refused an ordinary edition contribution: the
  second-sentence check read an author's middle initial (`John B. Conway`), a
  volume numeral (`Analysis I. Fourth edition`) and an abbreviation
  (`2nd ed. Revised`) as prose. It now requires a real word before the sentence
  break, and with `--overlay` it reports the count it actually checked.
- a docstring told an operator recovering a killed run to look at
  `cf challenge`; the spelling is `cf adjudicate --challenge-only`, and the
  contract test now reads the `cf <word>` literals under `scripts/`,
  `workflows/` and `templates/` too.
- two line-range citations into files this repo rewrites (`references/traps.md`
  G5 into `CLAUDE.md`, `references/vocabulary.md` §2.2 into the gate script);
  both now cite a section, and a test refuses a new one.
- the findings gate ran `git` inside a worktree a worker can configure, so a
  content filter driver named from that worktree's `.git/info/attributes` made
  the gate run the worker's own program — and the gate still reported the
  contribution clean. Every filter driver the repo names is now blanked before
  the gate compares anything.
- `cf share export` and `cf route --github` redact prose with one redactor over
  every free-text field of a row, not the seven field names v1 happened to use.
  Book text used to survive in `summary`, `recovery_argument`, a dissent's
  argument and the witness — that is, in a bundle handed to somebody else and
  in the issue body drafted for a public tracker.
- a manuscript companion (`blueprint_tex`) has no item table by design, so the
  handoff gave it a coverage denominator of zero and a `TODO(coverage)` naming
  commands that could not fill it, while the same file said the run had
  hundreds of tasks. Its universe now comes from the pairing index, so the two
  halves of the report agree.
- a differential `cf plan-run` staged every companion in the workspace,
  including companions of other sources; it now stages only the companions
  bound to the source the run audits.
- `cf sources add` bound the source to the companion and saved the workspace
  before preparing it, so a source it then refused with exit 2 stayed in the
  audited scope. It prepares first, and a refusal leaves no binding, no overlay
  row and no half-prepared directory behind.

## 0.2.0 — the multi-source model

The rewrite from "one campaign per book" to **N companions × M sources**:
`workspace.yaml` (schema 2), sources identified by the sha256 of their bytes
and remembered in a shipped registry, `cf onboard` discovering and inferring
everything that used to be typed in, `(source_id, item)` as the item key, and
`source_sha` on every artifact. `cf book` was deleted in favour of
`cf sources refresh`. See `docs/design/archive/multi-source-model.md` for the
design record.

## 0.1.0 — the port

The first version: one campaign, one book, the stages that produced the
baseline corpus.
