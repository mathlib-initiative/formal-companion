# Vocabulary — one enum set, and how the legacy ones map onto it

Five vocabularies drifted across the stages of the 2026-09 audit: the batch-1 Workflow schema, the
tend corpus gate, the challenge workflow, the adjudication ledger, and the viewer data file. Rows
could not be joined without a human in the loop. This file defines **one** set, and gives the
mapping tables that let `scripts/gate/convert_v1.py` import the 2,672-file baseline without
touching it.

The enums here are the single source of truth in code: `scripts/cflib/vocab.py`. Anything not
listed is invalid and the schema gate rejects it.

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
| `PAGE_ANCHOR` | **mechanical only** — `pair/anchor_check.py` | `source: "anchor_check"`, `page: {claimed, actual}` and the pinned edition's `book_sha` |

`PAGE_ANCHOR` is never an LLM kind. The attack gate rejects `PAGE_ANCHOR` and `COVERAGE` outright,
which is why an attack run's 100% substantive ratio is partly definitional
(`references/method.md` §1).

**Provenance has ONE key: `source`.** `pair/anchor_check.py` writes it, the gate's
mechanical-only rule reads it, and `route.provenance()` reads it (accepting the legacy
`provenance` spelling for an older corpus). `cf pair` publishes the rows into
`corpus/<run-id>/` through the `mechanical` gate, so they are routed like any other
finding rather than living only in `pairs/anchor-check.json`.

**Withdrawal.** A PAGE_ANCHOR row that did NOT come from `anchor_check` is judged against
the pinned edition, three ways: the mechanical check found the same mismatch → routed; it
examined the item and the repo anchor is correct → withdrawn as *contradicted by the pinned
edition*; it never examined the item → withdrawn with the re-derive instruction. Only the
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

### 1.6 Viewer layer (only for companions with a viewer contract)

`verdict` ∈ `faithful` | `convention` | `attribution_gap` | `proxy` | `deviation`;
`severity` ∈ `none` | `low` | `medium` | `high`;
`status` ∈ `unconfirmed` | `contested` | `human_confirmed` | `fixed`.

`human_confirmed` is never written by a machine pass.

---

## 2. Mapping tables from the five legacy vocabularies

### 2.1 Batch-1 Workflow findings (`~/Desktop/tao-differential/harvest.partial.json`, `findings.*.jsonl`)

Source: the batch-1 fleet schema (recovered transcript `b174aa13` L5600) and the routed files.

| legacy field | legacy values | canonical |
|---|---|---|
| `matches[].verdict` | `AGREE`, `CONVENTION_ONLY`, `CONTENT_DELTA` | same |
| `disagreements[].kind` | `CONTENT`, `PAGE_ANCHOR`, `VACUITY`, `SUSPECTED_ERRATA` | same (no `COVERAGE`, no `PROXY` in batch 1) |
| `faithful` | enum `ours` \| `teorth` \| `adam` \| `none` \| `all_differ` \| `unsure` | **list** `faithful: [repo…]`; `none` → `[]`; `all_differ` → `[]` plus `all_differ: true`; `unsure` → `[]` plus `unsure: true` |
| `severity` | `high`, `medium`, `low` | same |
| `_meta.status` | free text beginning `UNCHALLENGED` | `_meta.status: "UNCHALLENGED"` plus the prose in `_meta.note` |

Batch-1 rows were produced against the **2011 draft** PDF, so the converter stamps them with the
draft's `book_sha`, not the campaign's, and marks their `PAGE_ANCHOR` rows `withdrawn: true`
(the wholesale withdrawal is recorded in `findings.adam.jsonl`'s `_meta.withdrawn`).

### 2.2 Corpus v1 (the five tend runs; `verify_findings.py` L18-20 and its attack variant)

| legacy | canonical |
|---|---|
| `VERDICTS = {AGREE, CONVENTION_ONLY, CONTENT_DELTA, SINGLE_SOURCE_OK, SINGLE_SOURCE_DELTA}` | same five |
| attack additions `NO_DEFECT_FOUND`, `NO_WEAKNESS_FOUND` | both → `NO_DEFECT_FOUND` |
| differential `KINDS = {CONTENT, PAGE_ANCHOR, VACUITY, SUSPECTED_ERRATA, COVERAGE}` | same, minus `PAGE_ANCHOR` (converted rows keep the kind and gain `mechanical: false`, `withdrawn` decided by `anchor_check`) |
| attack `KINDS = {CONTENT, VACUITY, SUSPECTED_ERRATA}` | same |
| `book_passage_quote`, `book_quote` | unchanged; `book_sha` added |
| `faithful` (enum) | list, as in §2.1 |
| witness in prose inside `reasoning` | `witness: {d?, object, why}` — extracted where the text names one, else `witness: null` and `witness_extracted: false` |
| `unfaithful_decls` bare names `repo:decl` | unchanged, plus `decl_qualified` where the statement table can supply the namespace |
| task-id file name (`exercise-1_1_1.json` / `exercise-1-1-17.json`) | `run_id` + `item`; the converter accepts dash **and** underscore slugs |

### 2.3 Challenge workflow (`challenge-batch1-findings-wf_c3f0e5d0-d65.js`)

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

### 2.4 Adjudication ledger (`adam-faithfulness-adjudication-wf_60a87e92-dd5.js`, `accepted/refuted.adam.jsonl`)

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

Ledger → viewer, as implemented by both generators
(`~/Desktop/notes/camera-ready-2026-09-08-tools/make_verdicts.py`,
`~/Desktop/tao-differential/adam-detector/make_verdicts.py`):

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

```
finding_id = sha256(
    run_id + "\x1f" +
    item    + "\x1f" +          # normalised item id, sub-part included
    kind    + "\x1f" +
    "\x1e".join(sorted(unfaithful_decls))
).hexdigest()
```

Rules and reasons:

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

**Workflow replay is not keyed on ids.** The Workflow tool replays a cached `agent()` result when
the prompt bytes and the preceding call order are unchanged, so "re-running adjudication on a
superset replays the cached rows" holds only if the inputs builder is deterministic and stably
ordered. `adjudicate` therefore sorts its inputs by `finding_id` and writes the input file before
launching.

---

## 4. Item ids

Grammar: `"<Kind> <c>.<s>.<n>"` with an optional sub-part suffix `(iv)` / `(3)`, where `<Kind>`
comes from `campaign.yaml`'s kind list (`Definition`, `Lemma`, `Theorem`, `Proposition`,
`Corollary`, `Exercise`, `Example`, `Remark`, plus per-book extras such as `Claim`, `Strategy`).
Non-numbered items use `In-text p.<printed page>`.

- Parsing is **whitespace-tolerant**: `Exercise    1.3.19` is the same item as `Exercise 1.3.19`.
  A single-space regex in the legacy task generator dropped exactly that item and published a
  denominator of 410 for a chapter of 411.
- Normalisation strips a trailing period and lifts the sub-part into its own field; the item key
  never carries it, so `Exercise 1.3.10(ii)` and `Exercise 1.3.10(iv)` share one key and collapse
  correctly when items are counted.
- Junk keys are rejected, not normalised: the legacy `norm()` passed strings like
  `Corollary (Lemma 1.2.5 + Exercise 1.2.4)` through as item ids. The strict grammar plus a reject
  list is in `scripts/cflib/` and every rejected string is reported, never dropped silently.
