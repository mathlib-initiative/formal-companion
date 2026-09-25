# Vocabulary — one enum set, and how the legacy ones map onto it

Five vocabularies drifted across the stages of the 2026-09 audit: the batch-1 Workflow schema, the
tend corpus gate, the challenge workflow, the adjudication ledger, and the viewer data file. Rows
could not be joined without a human in the loop. This file defines **one** set, and gives the
mapping tables that let `scripts/gate/convert_v1.py` import the 2,672-file baseline without
touching it.

The enums here are the single source of truth in code: `scripts/cflib/vocab.py`. Anything not
listed is invalid and the schema gate rejects it.

**Contents** — §1 the canonical vocabulary (match verdicts, finding kinds, severity, ledger
verdicts, resolutions, source roles, re-check statuses, statement-row provenance) · §2 mapping
tables from the five legacy vocabularies · §3 `finding_id`, the frozen rule · §4 item ids.

---

## 1. The canonical vocabulary

### 1.1 Match verdicts — per concept, per item (`matches[].verdict`)

| value | meaning |
|---|---|
| `AGREE` | two or more companions render the concept the same way, and it is the book's |
| `CONVENTION_ONLY` | they differ, but the book's claim is recoverable by routine unfolding of a documented house convention (write the recovery argument) |
| `CONTENT_DELTA` | they differ and at least one is not the book's claim |
| `SINGLE_SOURCE_OK` | only one companion formalises it and it checks out against the book |
| `SINGLE_SOURCE_DELTA` | only one companion formalises it and it does not |
| `NO_DEFECT_FOUND` | attack mode: the probe ran and found nothing. A correct and valuable answer |

### 1.2 Finding kinds (`disagreements[].kind`)

| value | who may emit it | evidence the gate demands |
|---|---|---|
| `CONTENT` | worker (any mode) | `book_quote` + `witness` (witness required in attack mode) |
| `VACUITY` | worker (any mode) | as `CONTENT`; the witness is the instance that makes it vacuous or trivially true |
| `PROXY` | `detect-proxies` only | `bridge: proved \| fiat \| none` + the closure path; no book quote, no witness |
| `SUSPECTED_ERRATA` | worker (any mode) | `book_quote` + the errata entry, cited by date and URL |
| `COVERAGE` | worker (differential / single-source modes) or `def-coverage` | the item id and its page in `items.json`; no witness |
| `PAGE_ANCHOR` | **mechanical only** — `pair/anchor_check.py` | `source: "anchor_check"`, `page: {claimed, actual}` and the anchor edition's `source_id` + `source_sha` |

`PAGE_ANCHOR` is never an LLM kind. The attack gate rejects `PAGE_ANCHOR` and `COVERAGE` outright,
which is why an attack run's 100% substantive ratio is partly definitional
(`references/method.md` §1).

**Provenance has ONE key: `source`.** `pair/anchor_check.py` writes it, the gate's
mechanical-only rule reads it, and `route.provenance()` reads it (accepting the legacy
`provenance` spelling for an older corpus). Note the collision of words and not of keys: this
`source` is *who produced the row*, while `source_id` is *which book it is about*. `cf pair`
publishes the rows into `corpus/<run-id>/` through the `mechanical` gate, so they are routed like
any other finding rather than living only in `pairs/<source_id>/anchor-check.json`.

**Withdrawal.** A PAGE_ANCHOR row that did NOT come from `anchor_check` is judged against
the companion's **anchor edition**, three ways: the mechanical check found the same mismatch →
routed; it examined the item and the repo anchor is correct → withdrawn as *contradicted by the
anchor edition*; it never examined the item → withdrawn with the re-derive instruction. Only the
first two were ever meant to exist as one branch, and before this the code tested a key
`anchor_check` never writes, so every non-mechanical row was withdrawn wholesale.

### 1.3 Severity

`low` | `medium` | `high`. Ledger rows may additionally carry `none` for a refuted row (the
adjudication schema used `none`, and `refuted.adam.jsonl` row 1 records it).

Reviewer/camera-ready lenses have their own fourth level `note`, meaning "not a finding, record
it": it lives only inside those workflows' results and never enters the corpus.

### 1.4 Ledger verdicts (adjudication outcome, one per finding)

| value | meaning |
|---|---|
| `CONFIRMED_CONTENT` | the Lean asserts something materially different from the book, or a main clause is missing |
| `ATTRIBUTION_GAP` | a faithful statement exists in the repo, but the declaration attributed to the item does not state it — the fix is naming, not mathematics |
| `CONVENTION` | an encoding or degenerate-case choice; the book's claim is recoverable, **and the recovery argument is written down** |
| `ERRATA` | the printed text is what diverges; the errata entry decides it |
| `REFUTED` | the finding's factual premise is wrong (misread Lean, misread book, wrong edition) |
| `UNSURE` | adjudicated and undecided. Must carry what would decide it |

Companion fields: `confidence` (0–1), `method`, `contested: bool`, `dissent[]` (skeptic votes
below the contest threshold are still recorded — a 0.6-confidence dissent was dropped once and had
to be re-surfaced by hand).

### 1.5 Resolutions

`fixed:PR#N` | `in_progress:<branch>` | `refuted` | `argued_correct` | `out_of_scope`.

`argued_correct` is the disposition for a finding the owner answers rather than fixes (the
"formalise the book, not the blog post" case). The legacy ledger used free text here — one row
reads `refuted; binder dropped in PR#10` — which is exactly the drift this enum removes; the
converter maps such a row to `refuted` and moves the prose into `reason`.

### 1.6 The source model

These came with the multi-source model (`docs/design/archive/multi-source-model.md`); they describe
*sources*, not findings, but they are enums and they live in the same file.

| enum | values | what it decides |
|---|---|---|
| `SOURCE_ROLES` | `audited` \| `reference` | `audited`: at least one companion is checked against it. `reference`: prepared only so the page map to a sibling edition can be checked, and audited by nobody — the disposition for an edition a repo bundles but does not anchor to |
| `LICENCE_FETCH` | `none` \| `author_site` \| `open` | what a registry licence note permits. `none` refuses a download even with `--download`. **Nothing is ever redistributed, whatever this says** |
| `DISCOVERY_SIGNALS` | `formalization_yaml` `ledger_source` `official_source_json` `graph_json` `prose` `filesystem` `citation` | S1–S7: which repo signal produced a claim. All seven run for every adapter kind |
| `PDF_FOUND_IN` | `repo_declared` `repo_cache` `workspace_store` `library_path` `download` `none` | which step of the search order located the bytes — in search order, so it doubles as a provenance ranking |
| `OFFSET_METHODS` | `running_head_mode` `declared` `manual` `registry` `unknown` | how a page offset was arrived at. `manual` means a human typed it through `cf sources set`, and the confidence beside it is not an inference |
| `ADAPTER_KINDS` | `docstring_tags` `attribution_table` `blueprint_tex` `ledger_nodes` `custom` | what an `adapter.yaml` may legally say |
| `IMPLEMENTED_ADAPTER_KINDS` | `docstring_tags` `attribution_table` `blueprint_tex` | what v1 can actually parse. `cf onboard` refuses the difference **before** writing anything |
| `WRITE_POLICIES` | `read_only` `branch_only` `branch_and_pr` | v1 is findings-only; peers stay `read_only` either way |
| `ANCHOR_CONVENTIONS` | `docstring` `ledger` `none` | where a page anchor comes from, and whether anchor-agreement can decide an edition at all |

Also here, and load-bearing: `DEFINITIONAL_KEYWORDS` — `def`, `abbrev`, `structure`, `class`,
`inductive`, `instance`. The definition-coverage split (stated / bridge-only / missing) turns on
that set and nothing else, and it is written down once because two stages both write
`pairs/<source_id>/def-coverage.<name>.json` and used to disagree about `instance`.

### 1.7 Viewer layer (only for companions with a viewer contract)

`verdict` ∈ `faithful` | `convention` | `attribution_gap` | `proxy` | `deviation`;
`severity` ∈ `none` | `low` | `medium` | `high`;
`status` ∈ `unconfirmed` | `contested` | `human_confirmed` | `fixed`.

`human_confirmed` is never written by a machine pass.

### 1.8 Re-check statuses (`cf recheck`, `recheck.status` on a ledger row)

`unchanged` | `changed` | `gone` | `unpinned`. `cflib.vocab.RECHECK_STATUSES`.

These are facts about the **code**, not verdicts. A finding is a claim about a specific Lean
statement at a specific revision, so `cf recheck` re-extracts the companion at its current commit
and compares each pinned `decl_fingerprint` (§3.2) with what is there now:

| value | what it means | what happens to the row |
|---|---|---|
| `unchanged` | every pinned declaration still normalises to the same fingerprint | nothing; the claim is still about this code |
| `changed` | a fingerprint differs — the statement moved | `needs_rereview`: it may have been fixed or made worse, and only a reader can say which |
| `gone` | a pinned declaration is not in the table any more | `needs_rereview` |
| `unpinned` | the row carries no fingerprint and its run recorded none | reported, never guessed at |

`unpinned` is the honest answer for every imported legacy corpus and every row written before the
pin existed, and it is why re-checking a legacy ledger cannot move an adjudicated number: a
fingerprint taken at today's HEAD would claim the finding was judged against code it never saw.
A `COVERAGE` finding names no declaration, so it is `unpinned` too — there is nothing to fingerprint,
and reporting it as `unchanged` would read as "still verified".

**`cf recheck` writes no verdict and no resolution.** `validate_ledger` rejects a `recheck` block
that carries either: stating what the code did and deciding what that means are two different jobs,
and only the second one is an adjudication.

### 1.9 Statement-row provenance (`statements.jsonl`, `provenance`)

`table` | `lean_scan` | `docstring` | `blueprint`, or null. `cflib.vocab.ROW_PROVENANCE`.

Which artifact **of the companion** a statement row was read from — not which source it is about,
which is `source_id`. Most conventions have one artifact and the value is a constant per adapter
(`docstring` for `docstring_tags`, `blueprint` for `blueprint_tex`). `attribution_table` has two
and they disagree:

| value | meaning |
|---|---|
| `table` | the companion's exported item → declaration attribution table said so |
| `lean_scan` | the Lean sources claim it for a book item and the export **omits** it (`docs/design/stage_map.md` §4, Stage 2) |
| null | the adapter does not distinguish — the honest value for a `custom` extractor reading something else |

An omission whose docstring names no item gets no row at all — there is nothing to key it on —
and goes to `companions/<name>/unattributed.jsonl` instead, which `cf plan-run` stages beside the
tables. Typically that is most of the omissions: they sit in a `section` or `namespace` region under
a block header that names its item only in prose.

A `COVERAGE` finding is the reason this exists. "No declaration of this companion states X" read
off a `table`-only corpus is a claim about the export, not about the repository, and a public,
sorry-free theorem the export left out makes it false.
`lean_scan.policy` decides what an omission does (`cflib.vocab.CROSS_CHECK_POLICIES`) — `union`
publishes it as a row, `report` does not (under both, the omissions that name no item go to
`unattributed.jsonl` and `cf extract` names the omitted declarations in a warning), and `off` is a
deviation a companion states in its own `adapter.yaml`: no scan runs, so nothing is named, and
`cf extract` warns on every run that the table was not cross-checked. A bare `off` is YAML false
and is read as `off`.

---

## 2. Mapping tables from the five legacy vocabularies

### 2.1 Batch-1 Workflow findings (the v1 differential's `harvest.partial.json`, `findings.*.jsonl`)

Source: the batch-1 fleet schema and the routed files of the v1 campaign.

| legacy field | legacy values | canonical |
|---|---|---|
| `matches[].verdict` | `AGREE`, `CONVENTION_ONLY`, `CONTENT_DELTA` | same |
| `disagreements[].kind` | `CONTENT`, `PAGE_ANCHOR`, `VACUITY`, `SUSPECTED_ERRATA` | same (no `COVERAGE`, no `PROXY` in batch 1) |
| `faithful` | enum `ours` \| `teorth` \| `adam` \| `none` \| `all_differ` \| `unsure` | **list** `faithful: [repo…]`; `none` → `[]`; `all_differ` → `[]` plus `all_differ: true`; `unsure` → `[]` plus `unsure: true` |
| `severity` | `high`, `medium`, `low` | same |
| `_meta.status` | free text beginning `UNCHALLENGED` | `_meta.status: "UNCHALLENGED"` plus the prose in `_meta.note` |

Batch-1 rows were produced against the **2011 draft** PDF, so the converter stamps them with that
edition's `source_id` and `source_sha` — `tao-measure-2011-draft`, `62d95658…` — not the audited
edition's, and marks their `PAGE_ANCHOR` rows `withdrawn: true` (the wholesale withdrawal is
recorded in `findings.adam.jsonl`'s `_meta.withdrawn`). That is the whole point of stamping the
source rather than assuming one: the rows stay importable and stay honest about what they were
judged against.

### 2.2 Corpus v1 (the five tend runs; the v1 gate's verdict set and its attack variant, now `plan/presets.py`'s `LEGACY_*_VERDICTS`)

| legacy | canonical |
|---|---|
| `VERDICTS = {AGREE, CONVENTION_ONLY, CONTENT_DELTA, SINGLE_SOURCE_OK, SINGLE_SOURCE_DELTA}` | same five |
| attack additions `NO_DEFECT_FOUND`, `NO_WEAKNESS_FOUND` | both → `NO_DEFECT_FOUND` |
| differential `KINDS = {CONTENT, PAGE_ANCHOR, VACUITY, SUSPECTED_ERRATA, COVERAGE}` | same, minus `PAGE_ANCHOR` (converted rows keep the kind and gain `mechanical: false`, `withdrawn` decided by `anchor_check`) |
| attack `KINDS = {CONTENT, VACUITY, SUSPECTED_ERRATA}` | same |
| `book_passage_quote`, `book_quote` | unchanged — still a quote from a book; `source_id` + `source_sha` added, and they decide *which* text the quote gate checks it against |
| `faithful` (enum) | list, as in §2.1 |
| witness in prose inside `reasoning` | `witness: {d?, object, why}` — extracted where the text names one, else `witness: null` and `witness_extracted: false` |
| `unfaithful_decls` bare names `repo:decl` | unchanged, plus `decl_qualified` where the statement table can supply the namespace. The `repo:` half is this campaign's vocabulary (`ours`, `teorth`, `adam`); `convert_v1 --repo-map ours=<companion id>` maps it onto the importing workspace's companions, because the union key holds the repo — see `docs/design/archive/multi-source-model.md` §6.1 |
| task-id file name (`exercise-1_1_1.json` / `exercise-1-1-17.json`) | `run_id` + `item`; the converter accepts dash **and** underscore slugs |

### 2.3 Challenge workflow (the v1 campaign's challenge-batch1-findings run)

| legacy | canonical |
|---|---|
| `ruling_survives: false` | ledger verdict `REFUTED` unless `classification` says otherwise |
| `classification: CONTENT` | `CONFIRMED_CONTENT` |
| `classification: CONVENTION` | `CONVENTION` |
| `classification: ERRATA` | `ERRATA` |
| `classification: ATTRIBUTION` | `ATTRIBUTION_GAP` |
| `classification: UNSURE` | `UNSURE` |
| `survives_all` (all refuters agree) | `contested: false`; any dissent → `contested: true` with the votes in `dissent[]` |
| linkage by `item + concept.slice(0,40)` | `finding_id` (§3) |

### 2.4 Adjudication ledger (the v1 adam-faithfulness-adjudication run, `accepted/refuted.adam.jsonl`)

Finding verdicts already match §1.4. The workflow's two other schemas map into finding kinds:

| definition-reader verdict | canonical |
|---|---|
| `NATIVE_FAITHFUL` | no finding |
| `NATIVE_DEVIATION` | `CONTENT` |
| `PROXY_WITH_BRIDGE` | `PROXY` with `bridge: proved` (name the bridge theorem) |
| `PROXY_NO_BRIDGE` | `PROXY` with `bridge: fiat` |
| `NOT_THIS_DEFINITION` | `COVERAGE` (plus `ATTRIBUTION_GAP` at adjudication if a faithful declaration exists elsewhere) |

| uncovered-definition (B3) classification | canonical |
|---|---|
| `NATIVE_DEF_EXISTS_UNCURATED` | `ATTRIBUTION_GAP`, severity low |
| `BRIDGE_THEOREM_ONLY` | `PROXY`, `bridge: proved`, severity low |
| `MATHLIB_STANDIN_ONLY` | `PROXY`, `bridge: none`, severity medium |
| `ABSENT` | `COVERAGE`, severity medium |

`fix_effort` ∈ `none` | `trivial` | `small` | `medium` | `large` survives as an optional ledger
field; it is advisory and no gate reads it.

### 2.5 Viewer file (`measure-theory/faithfulness-verdicts` v1)

Ledger → viewer, as implemented by both of the v1 campaign's generators (the
camera-ready tools' `make_verdicts.py` and the proxy detector's copy of it):

| ledger verdict | viewer verdict |
|---|---|
| `CONFIRMED_CONTENT` | `deviation` |
| `ATTRIBUTION_GAP` | `attribution_gap` |
| `CONVENTION` | `convention` |
| `REFUTED` | `faithful` |
| `ERRATA` | `convention` (with the errata entry in `evidence`) |
| `UNSURE` | not exported |
| kind `PROXY` (any bridge) | `proxy` |

| resolution / adjudication state | viewer status |
|---|---|
| `fixed:PR#N` or `in_progress:<branch>` | `fixed` |
| `contested: true` | `contested` |
| anything else | `unconfirmed` |
| (never machine-written) | `human_confirmed` |

The viewer file is bound to the ledger sha; a stale sha is refused by the viewer, so a re-bind is
part of the publish cascade.

---

## 3. `finding_id` — the rule

```python
finding_id(run_id, source_id, item, kind, unfaithful_decls) = sha256(
    "\n".join([run_id, source_id, items.clean(item), kind, *sorted(unfaithful_decls)])
).hexdigest()
```

Five parts, joined with a newline, and the declarations go in as **separate parts** rather than
pre-joined. `cflib.hashing.finding_id` is the definition; `gate/verify_findings.py` inlines it
byte-for-byte because the gate is transferred to a run host where `cflib` is not importable. The
compat shims (`sources/compat.py`, `ledger/compat.py`, `detect/_compat.py`) delegate to `cflib`, and
`tests/test_plan_gate.py::TestFindingIdIsOneDefinition` asserts every spelling agrees on every digest.

Rules and reasons:

- **`source_id` is a part.** An item is the pair `(source_id, item)`: without it, `Exercise 1.3.19`
  of one source and `Exercise 1.3.19` of another produce the same id, and a workspace auditing two
  books would silently merge their findings. This is why v2 digests differ from the four-part ones
  the earlier corpus carried — correctly, because those ids collide.
- **The item is passed through `items.clean()` first**, so `Exercise    1.3.19` and
  `Exercise 1.3.19` — the whitespace variant the baseline lost — hash the same.
- **`concept` is not in the hash.** It is LLM-written prose, so the same defect in two runs would
  hash differently and cross-run linking (`union`, `agreed_high`, re-adjudication) would fail.
- **`unfaithful_decls` is sorted** and compared as `repo:decl` strings exactly as the gate accepts
  them, so the id does not depend on the order the agent happened to list them in.
- **The id is assigned at harvest**, by `harvest/harvest.py`, not by the agent: a worker cannot be
  trusted to compute it and an agent-written id cannot be verified cheaply.
- **A row with no `unfaithful_decls` still gets an id** (the joined field is empty). Such rows are
  never routed — routing is on the `unfaithful_decls` prefix only.
- Challenge and adjudication results link by `finding_id`, never by a label prefix. The legacy
  22-/30-character prefix join is what made batch-1 challenge results ambiguous.

### 3.1 `defect_key` — the run-independent key

```python
defect_key(source_id, item, kind, unfaithful_decls) = finding_id(
    "", source_id, items.join_key(item), kind, unfaithful_decls)
```

`cflib.hashing.defect_key`, beside the id so the two cannot drift. `run_id` is `finding_id`'s
**first** part, so the same defect found by two runs gets two ids: an id addresses one
*observation*, and this key is the *identity* two observations share. `union --group-by defect-key`
groups on it; `agreed` groups on `(source_id, repo, collapsed item, kind)` plus a shared
declaration, and adjudication, transcription and `cf recheck` address one observation, so they join
on `finding_id`. `--new-only` reported every re-found defect as new while this key did not exist.

Two details are load-bearing:

- **the item goes in as the JOIN key**, not as the label a worker wrote. `harvest.tally` passes
  `collapse_to_book_item()` (`items.join_key` for an in-text label, the book item for a numbered
  one), because `Exercise 1.6.27` and `Exercise 1.6.27(iii)` are one defect at one book item. Keyed
  on the raw spelling they landed in two groups, so the "exact" grouping was not even a refinement
  of the default one — which is the shape of the duplicate `union.adam.json` recorded for
  `Exercise 1.6.39`. §4's rule: anything that MATCHES on an item matches on `join_key`.
- **the companion is not hashed in.** It is a field on every row and part of every grouping tuple
  that uses this key (`(source_id, repo, defect_key)` in `union`), so folding it in would change no
  grouping and re-key what is already written.

Neither key is ever stored on a finding: both are recomputed from the content every time it is read.

### 3.2 The revision pin — `repo_commit` and `decl_fingerprints`

A finding is a claim about a Lean statement **at a revision**. Two fields carry that:

| field | written by | how |
|---|---|---|
| `repo_commit` | `cf plan-run` → `run.yaml` `companion_pins`, stamped by `cf harvest` | the companion's HEAD at plan time, read through `cflib.gitsafe`, with a `dirty` flag beside it: a working tree that does not match its commit is not pinned by it |
| `decl_fingerprints` | `cf harvest` → `reports/pin.<run-id>.json`, copied onto the ledger row | `{repo:decl → sha256}` over the declaration's statement text in `companions/<name>/statements.jsonl` **at that commit**, with Lean block comments (so docstrings) dropped and whitespace collapsed |

Neither is ever taken from a worker, for exactly the reason `finding_id` is recomputed at harvest: a
worker cannot be trusted to state what it was judged against. A declaration is fingerprinted **only**
when the statement table was extracted at the planned commit and the checkout was clean; otherwise
it is listed as `unpinned` rather than fingerprinted against a revision it never saw. Whitespace and
docstring changes are deliberately invisible to the fingerprint: a re-check that shouts at
reformatting gets run once.

**Workflow replay is not keyed on ids.** The Workflow tool replays a cached `agent()` result when
the prompt bytes and the preceding call order are unchanged, so "re-running adjudication on a
superset replays the cached rows" holds only if the inputs builder is deterministic and stably
ordered. `adjudicate` therefore sorts its inputs by `finding_id` and writes the input file before
launching.

---

## 4. Item ids

**An item is the pair `(source_id, item)`.** Two sources may legitimately both have an
`Exercise 1.3.19`, and they are different items; nothing keys on the item half alone.

| context | representation |
|---|---|
| a JSON/JSONL row | two fields: `"source_id": "<id>"`, `"item": "Exercise 1.3.19"` |
| an in-memory dict key | the tuple `(source_id, item)` |
| one string (task id, log line, finding payload) | `qualified_item(source_id, item)` → `<source_id>::<item>` |
| a filename or slug | `qualified_slug(source_id, item)` → `<source-slug>--exercise-1-3-19` |

`::` is the one separator (`cflib.items.QUALIFIED_SEP`); `split_qualified()` is its inverse and
raises on a string without it. A function that takes a bare item string takes a `source_id` too.

Grammar for the item half: `"<Kind> <n>[.<n>…]"` with an optional sub-part suffix `(iv)` / `(3)` /
`(a)`, where `<Kind>` comes from **that source's** kind list — inferred from the source's own
headings and stored in `workspace.yaml` `sources.<id>.kinds` (typically `Definition`, `Lemma`,
`Theorem`, `Proposition`, `Corollary`, `Exercise`, `Example`, `Remark`, plus per-source extras such
as `Claim`, `Strategy`). Kind inference folds case, so an AMS-style `THEOREM 1.2` head counts as
`Theorem`. Structural words (`Chapter`, `Section`, `Figure`, `Table`, …) are counted and reported
but are never item kinds. Non-numbered items use `In-text p.<printed page>`.

- **How many numbers is the source's numbering, not a constant.** `Theorem 7`, `Theorem 3.4`,
  `Definition 1.2.3` and `Lemma 2.3.4.1` are four real book conventions; `cflib.inference.
  infer_numbering` reads the depth off the source's own headings and it is stored as
  `numbering: {min_segments, max_segments, …}` in `sources/<id>/items.json` and `meta.json`, so
  every stage reads one definition. A caller that does not know the source gets
  `items.DEFAULT_NUMBERING` — two or three segments, the grammar the existing corpus was keyed
  with, so a re-parse of the baseline is byte-identical, and an empty stored mapping (a host
  without `cflib` inferred nothing) reads back as that default rather than as `(2, 2)`.
  `cf sources set <id> numbering=<min>,<max>` is the override, stamping `numbering_method:
  manual`: it is the one inference a re-run cannot settle, because re-running reads the same
  headings and reaches the same depth.
- **A label deeper than the numbering is a reject, never a truncation.** `Theorem 2.3.4.5` under a
  three-segment numbering is `too_many_segments`, not `Theorem 2.3.4` with a stray `.5`: the old
  behaviour merged every item of a section onto one audit key with nothing on any reject list,
  which is the one failure shape that produces wrong results rather than missing ones.

- Parsing is **whitespace-tolerant**: `Exercise    1.3.19` is the same item as `Exercise 1.3.19`.
  A single-space regex in the legacy task generator dropped exactly that item and published a
  denominator of 410 for a chapter of 411.
- Normalisation strips a trailing period and lifts the sub-part into its own field; the item key
  never carries it, so `Exercise 1.3.10(ii)` and `Exercise 1.3.10(iv)` share one key and collapse
  correctly when items are counted.
- Junk keys are rejected, not normalised: the legacy `norm()` passed strings like
  `Corollary (Lemma 1.2.5 + Exercise 1.2.4)` through as item ids. The strict grammar plus a reject
  list is in `scripts/cflib/items.py` and every rejected string is reported with its reason, its
  declaration and its file:line, never dropped silently. That exact string is still the one reject
  in `tao-measure-lean` (reason `no_number`, 2026-09-16), and an onboarding that reports one
  reject is telling you something true about the repo.
