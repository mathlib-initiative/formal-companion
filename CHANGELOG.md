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
- the errata converter deleted every mathematical symbol. On the pages these
  come from a formula is an `<img>` (`latex.php?latex=<url-encoded TeX>`, the
  same TeX in `alt`), so stripping tags left the prose and dropped the content:
  entries of the shape `In Corollary 3.2.4(b), should be , and the same` — a
  grammatical sentence that says nothing, shipped to workers as authoritative
  errata, while the symbols sat in the kept `errata.raw.html` the whole time.
  The formulas are now rendered as
  `$TeX$`, keyed on the `latex.php?latex=` pattern and `class="latex"` rather
  than on a host; `errata.json` records how many were recovered and how many
  were not. The renderer carries a version, so `cf sources refresh` re-derives
  an already-ingested `errata.md` from the bytes on disk with no network (read
  back as the UTF-8 the fetch wrote, whatever charset the page declared), and
  `cf status` names a source still holding an older render. A render by a
  NEWER converter is left alone. No extract key
  moves: `key_files` are paths inside the companion repo, and no adapter reads
  `errata.md` (`tests/test_sources_pair.py` pins both directions).
- an errata entry that still looks emptied — `, ,`, `should be ,`, an empty
  quotation, a formula the page carries only as image bytes, or TeX the page
  carries in a `<script type="math/…">` that the render does not emit (the
  entry keeps under 90% of the characters the renderer saw) — is now listed
  in `errata.json` and warned about once per source by `cf onboard` and
  `cf sources refresh`, naming the entries; `cf status` gives the count. It
  used to be emitted silently, which is the whole of the defect above. The
  guard is not exhaustive: maths in an image the page does not mark as a
  formula, or in an `<svg>`, is caught only when the sentence's shape gives it
  away.
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
  audited scope; `cf onboard <repo> --source <file>` reached the same refusals
  and left a source row, a registry overlay row and a prepared directory behind,
  recoverable only with a `cf sources remove` nothing named. Both prepare
  first now: a refusal leaves no binding and no overlay row, and removes a
  directory it prepared for a source the workspace did not already hold. A
  source the workspace did already hold stays; `cf sources add` says so and names
  the command that drops it, and `cf onboard` does the same for one the operator
  named with `--source`.
- `cf status` exited 2 on a companion directory that has an adapter but no
  `workspace.yaml` entry — the state a half-finished onboard or a copied
  directory leaves — one line after printing it as `not onboarded`. It prints it
  and exits 0.
- the printed `cf adjudicate` and `cd` next steps interpolated the workspace
  path unquoted, so in a workspace whose directory name contains a space they
  could not be pasted. `cf route` writes one of them into the routed ledger and
  into the owner-message draft, so the broken command travelled upstream.
- `cf share export` told the operator that `cf share import` would refuse a
  bundle with no ledger rows. The import accepts that bundle and merges nothing;
  the export says what it does instead. Its summary also named three field names
  for the prose scan, which has covered every free-text field of a row, at any
  depth, since that scan was widened.
- the findings gate blanked the git filter drivers a companion's own config
  names by passing `git -c <driver>=`, which cannot express a driver whose name
  contains `=`; such a driver still ran during the gate, and the gate still
  reported the contribution clean. The blanks travel in the environment now.
- the batched pre-merge check refused every merge of two or more tasks: each
  task's own task file is modified rather than added, and the relaxation only
  fired when every changed path was new.
- a source text that exists but cannot be read — a directory left by a partial
  copy, or a file the run host's user may not open — ended the gate in a
  traceback instead of the exit code reserved for infrastructure faults.
- a quote spread over three or more pages of the source matched no page at all,
  which turned the item-page check off rather than refusing the finding; one
  folio-only page at the seam was enough to reach it.
- a quote whose edge carries a sign written apart from its operand (`- 1 ≤ …`
  where the source has `1 ≤ …`) was certified verbatim.
- `cf report` dropped a corpus document `cf tally` refuses to read, publishing
  coverage figures that were a lower bound with nothing in the report saying so;
  it also dropped every examined item outside the audited chapters from those
  figures, including the count of items with findings, while the tally counted
  them.
- `cf report` counted a manuscript companion's items with the default numbering
  depth rather than the source's own, so a deeper label was dropped and the
  coverage line could read "0 of 0 item(s) in scope examined" for a manuscript
  that was examined in full.
- the remedy printed for an ungated run named a `cf import-corpus` invocation
  that exits 2 in a workspace with more than one source, because `--source` was
  missing from it.
- the per-chapter "in task universe" column printed 0 where the total row
  printed a dash: nothing had computed the figure.
- on a host whose git predates 2.31 the blanking of a content filter driver
  whose name contains `=` reached it through neither channel, so such a driver
  still ran on a read. The gate now refuses to read that worktree at all (exit
  3, naming the driver and the git version) and `cflib.gitsafe` returns a
  refusal instead of a read; on git 2.31 and later nothing changes.
- a boundary dash was stripped as punctuation unless a DIGIT stood beside it, so
  `- m*(F) ≤ m*(E)`, `- (1 ≤ m(F))` and `- x ≤ y` were still stripped back to the
  book's own words and certified verbatim while the same flips written without
  the space were refused. A dash at a quote's edge is now punctuation only when
  the text beside it is an alphabetic word.
- an alphabetic word was not enough either: the words a display opens with
  (`lim`, `sup`, `inf`, `dist`, …) and the letter run inside a trailing LaTeX
  macro read as prose, so `- lim sup En …` and `… \alpha -` were stripped back
  to the book's own words and certified verbatim while the glued spellings were
  refused. Those are operands now, at both edges.
- `cf quotes hydrate --out` read a git read the safety boundary REFUSED as "no
  repository encloses this path", so a refusal became permission to write
  recovered book text into a tracked file — the one thing that check exists to
  stop. A refused read means "do not write" now, as a hung git already did, and
  `cf recheck`'s pin records the refusal's own reason instead of calling a
  checkout "not a git checkout".
- `cf doctor` reports `git --version` beside the presence check and warns below
  2.31, the floor the gate's content-filter blanking rests on;
  `references/runbook-server.md` §2 records it as a precondition. The
  requirement used to be met for the first time as a blocked run.
- `cf report` dropped a whole corpus run from section 0 when its documents named
  a source the workspace no longer holds — a `cf sources remove` after a
  harvest, or a corpus imported under another workspace's id. The run's items,
  findings, unreadable documents and ungated state left every section-0 figure
  and `reports/coverage.json` without a word, while `cf tally` went on counting
  the same rows. They are published as `unknown_source_documents` and named in
  the note, and `cf sources remove` says which corpus runs were judged against
  the id it is dropping, whether the receipt or the run's plan names it.
- the handoff said only one of its five caveats on the three shapes whose
  per-source note ends early (a source no companion is bound to, an unpaired
  manuscript, a source with no `items.json`): an ungated or gate-FAILED run, an
  examined item outside the audited chapters and an unstamped document went
  unmentioned. All five are said on every shape now, for a run that names no
  single source as well, and `cf report` echoes the same set on the terminal.
- `cf share export` tells you which of the two zero-ledger bundles you have
  written: one `cf share import` merges zero rows from, or a findings-only
  bundle the import refuses, which is read with `cf quotes hydrate --bundle
  <bundle>`. `cf share export --help` describes the redaction the command
  performs: every free-text field of a row at any depth, and a record of (page,
  normalised length, sha256, and at most a quarter of the quote's words from each
  end, none at all below fourteen words).

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
