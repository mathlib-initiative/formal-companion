# Vocabulary — one enum set, and how the legacy ones map onto it

Five vocabularies drifted across the stages of the 2026-09 audit: the batch-1 Workflow schema, the
tend corpus gate, the challenge workflow, the adjudication ledger, and the viewer data file. Rows
could not be joined without a human in the loop. This file defines **one** set, and gives the
mapping tables that let `scripts/gate/convert_v1.py` import the 2,672-file baseline without
touching it.

The enums here are the single source of truth in code: `scripts/cflib/vocab.py`. Anything not
listed is invalid and the schema gate rejects it.

**Contents** — §1 the canonical vocabulary (match verdicts, finding kinds, severity, ledger
verdicts, resolutions, source roles and licence classes, re-check statuses, statement-row
provenance, annotation documents, locate) · §2 mapping tables from the five legacy vocabularies ·
§3 `finding_id`, the frozen rule · §4 item ids.

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
| `LICENCE_CLASSES` | `closed` \| `open_attribution` \| `open_share_alike` \| `public_domain` | what an **exported** record may quote of the source (`POLICY.md` §4.1). `closed` is the default (`DEFAULT_LICENCE_CLASS`) for every entry that says nothing, so every entry written before the field existed is `closed`: all rights reserved, quotes redacted on export. `open_attribution` (CC BY and the like) and `open_share_alike` (CC BY-SA; Wikipedia's text) may be quoted in capped form with a citation and the licence named (`ATTRIBUTION_LICENCE_CLASSES`); `public_domain` needs no attribution and is capped all the same. `OPEN_LICENCE_CLASSES` is the three open ones. Orthogonal to `LICENCE_FETCH`: a class says what may be *quoted*, a fetch value what may be *downloaded*, and neither permits redistribution or source text in the skill repo. A registry entry carries it as `licence_class`, a web-source family as its own `licence_class` (`registry/README.md`), and `Registry.licence_class(source_id)` answers for any id — an unknown one is `closed` |
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

### 1.10 Annotation documents (`formal-companion/annotations`, schema_version `1.0`)

The annotation enums are defined in the vendored `formal_companion_annotations.vocab`
(`vendor/formal_companion_annotations/vocab.py`, one release of formal-companion-annotations)
and documented, value by value, in `formal-companion-annotations/docs/vocabulary.md`. The format
owns its vocabulary and names it its own way (`ANNOTATION_VERDICTS`, `ANNOTATION_SEVERITIES`,
`ANNOTATION_VERDICT_STATUSES`, `ANNOTATION_RESOLUTION_STATES`, `ANNOTATION_SOURCE_ROLES`,
`ANNOTATION_SORRY_CLASSES`, ...); the skill keeps its own sets here for its stages and maps onto
the format's values where it writes a document: a verdict's `verdict`, `severity` and `status`
are the viewer layer's (section 1.7), a source's `role` section 1.5's, a declaration's `sorry`
`SORRY_CLASSES`, a resolution's `state` section 1.4's. `tests/test_vendored_format.py` checks that
every value the skill writes is one the format admits. The ledger verdicts and finding kinds
(sections 1.2 and 1.4) are not in the format at all: a document carries them only under `x_`
keys (`x_ledger_verdict`).

### 1.11 Locate — where a source is formalized, in any library

The vocabulary of `docs/proposals/wikipedia-locate.md`, in `cflib.vocab`'s locate block, with
`vocab.derive_status`, `vocab.decide_proof_status`, the `In-text #<hex>` item of §4 and the head
hash in `scripts/cflib/passages.py`. The stages that use it are `scripts/sources/` (the renderer
and sourcesets), `scripts/corpus/` (universes and `corpus.sqlite`), `scripts/locate/` (candidates,
the planner, outcomes and the export), `scripts/gate/verify_locate.py` and `scripts/bench/`; the
host-side copies of these values (the gate, the bridge helpers) are pinned to this block by
tests. The extension
namespace of every locate record is `org.mathlib-initiative.formal-companion.locate`
(`LOCATE_EXTENSION`).

**Run modes and kinds.** Three modes join `RUN_MODES`, one per locate preset, each with the
kinds its gate admits (`KINDS_BY_MODE`, `kinds_for_mode`):

| mode | stage | kinds |
|---|---|---|
| `locate-inventory` | L3a: one task per source, the passage table | `PASSAGE`, `UNRESOLVED` |
| `locate` | L3b: each `formal_math` passage ends located, absent or unresolved | `LOCATION`, `ABSENCE`, `UNRESOLVED` |
| `locate-challenge` | L5: per-record verdicts over those records | `LOCATION`, `ABSENCE`, `UNRESOLVED` |

| kind | meaning |
|---|---|
| `PASSAGE` | one passage of an inventory, keyed by its `In-text #<hex>` item |
| `LOCATION` | a declaration at a commit states the passage, wholly or in part (a relation below) |
| `ABSENCE` | a checked statement that the passage was searched for in a universe and nothing was found |
| `UNRESOLVED` | the worker, or the attempt cap, could not decide. Counted on its own, **never** as not formalized |

These are `LOCATE_FINDING_KINDS`; `ALL_FINDING_KINDS` is them plus §1.2's. `FINDING_KINDS` itself
stays the six faithfulness kinds, because the ledger validator, the answer key and the harvest's
substantive kinds read it and a locate outcome is not a defect. The tally hashes a document row's
`kind` into `finding_id` (§3), so **in a locate document `kind` is the finding kind**, and a
passage's own kind travels as `passage_kind`; only an exported Literature Links record carries the
passage kind as its `kind`.

**Passages** (the proposal's §2.2):

| enum | values |
|---|---|
| `PASSAGE_KINDS` | `definition` `theorem` `conjecture` `example` `construction` `notation` `other` |
| `PROMINENCE` | `subject` — what the article is about · `central` — a named result or definition it states · `supporting` — anything else formal. Not "role", which Literature Links uses for an end. `REFUTE_BY_DEFAULT_PROMINENCE` is `subject` and `central`: their locations are refuted by default and their absences and unresolved outcomes are hunted (the proposal's §5) |
| `SCOPES` | `formal_math` `expository` `non_math`. Only `formal_math` passages are located (`LOCATED_SCOPES`); a passage may not be scoped out while the deterministic candidates hold a tag or curated hit for it |

**Proof status** of a declaration (`PROOF_STATUSES`, the proposal's §2.4), decided in this order, the first rule
that applies winning, whatever the declaration's kind — mechanically from the axiom closure and
the type (`vocab.decide_proof_status`), never by a model and never by a worker:

| order | value | when |
|---|---|---|
| 1 | `wanted` | the type is a `ProofWanted`/`DefWanted`-style wrapper |
| 2 | `statement_only` | the axiom closure contains `sorryAx` (`SORRY_AXIOM`) |
| 3 | `axiom_dependent` | the closure contains an axiom outside `propext`, `Classical.choice`, `Quot.sound` (`STANDARD_AXIOMS`) |
| 4 | `proved` | the type is a `Prop` |
| 5 | `definition` | data: types, functions, structures, classes, instances |

A `Prop`-valued definition (`def RiemannHypothesis : Prop`) is a `definition` whose `statement`
flag the index sets beside it; it never formalizes a theorem passage.

**Relations** of a location (`LOCATE_RELATIONS`, the proposal's §2.6): Literature Links relation words with SKOS
direction, subject the passage, object the declaration.

| value | meaning |
|---|---|
| `exactMatch` | the declaration states the passage |
| `closeMatch` | the same up to a convention difference (0 ∈ ℕ, junk values, normalization), named in `note` |
| `broadMatch` | the declaration is more general; the passage is an instance |
| `narrowMatch` | the declaration is a special case of the passage |
| `hasPart` | the declaration states part of a passage that says several things |
| `relatedMatch` | relevant, but it states something else. Never counts towards a status |

`noMatch` (`NO_MATCH`) is the relation of an **absence** record only — one `lean` end per universe
library, no `location` — never of a location. At most `MAX_LOCATIONS_PER_PASSAGE` (5) locations per
passage, ranked. `WEAK_RELATIONS` (`relatedMatch`, `narrowMatch`, `hasPart`) are the ones the
challenge sample re-judges. `RELATION_STRENGTH` orders the relations for a challenge's
`downgrade`, which must name a strictly weaker one: `exactMatch` > `closeMatch` > `broadMatch` >
`narrowMatch` = `hasPart` > `relatedMatch`.

**The derived status** (`DERIVED_STATUSES`, best first): the Literature Links `status` of a
passage's records, computed once after challenge by `vocab.derive_status(passage_kind, locations,
absent, coverage=None)`, each location giving its `relation`, `proof_status`, optional `statement`
flag and, when a coverage claim names it, its `location_key` (distinct: one key on two locations is
refused); `coverage` is the passage's coverage claim exactly as a record carries it (below:
`COVERAGE_RECORD_KEYS`, `complete` · `parts` · `challenge`, the challenge a `CHALLENGE_VERDICTS`
value) or `None`, and the three-argument call is the one it always was:

| status | when |
|---|---|
| `formalized` | theorem/conjecture passage: `exactMatch`, `closeMatch` or `broadMatch` to a `proved` declaration; `exactMatch` or `closeMatch` to **data** — a `definition` without the `statement` flag (an equivalence, isomorphism, bijection, structure-valued construction: the Chinese remainder theorem as a `RingEquiv`); a **survived coverage claim** whose every part is `proved`. Definition passage: `exactMatch` or `closeMatch` to a `definition`; a survived coverage claim whose every part is a `definition` or `proved` |
| `statement_only` | the best such location is `statement_only`, `wanted` or `axiom_dependent`, or is a `Prop`-valued definition stating a theorem passage; or a theorem passage's survived coverage claim whose every part is `proved`, `statement_only`, `wanted` or `axiom_dependent` and not every one `proved` |
| `partial` | only `hasPart` or `narrowMatch`, or `broadMatch` to a definition, with no coverage claim that upgrades them |
| `not_formalized` | a checked absence, possibly beside `relatedMatch` locations |
| none (`None`) | unresolved: no location that counts and no absence — including a passage whose only locations are `relatedMatch` |

The best status over all the evidence wins (`formalized` > `statement_only` > `partial` >
`not_formalized`). A coverage claim whose challenge is anything but `survived` —
`challenge_pending`, `not_required`, `sampled_out`, `downgraded` — upgrades nothing; a refuted
claim is removed by the outcomes before the status is derived, and an owed claim (no challenge has
judged it yet, so it has no challenge value) is passed as no claim, which gives the same status.

**The maintainer's decision D1** (2026-10-18), verbatim:

> D1: yes. Formalized when the hasPart locations together cover every clause (challenger confirms
> coverage), and when an exact/closeMatch is to an equivalence or data declaration that states the
> theorem (e.g. CRT's RingEquiv). Update vocab and WikiLean's parity table.

It replaces two of the decisions this section used to make the conservative way: a theorem passage
matched exactly or closely to data was `partial` (as its `broadMatch` still is), and a passage that
says several things — a conjunction of clauses — stated clause by clause by `hasPart` locations was
`partial` however completely they covered it. The table above is the decision; the shared parity
table `tests/data/locate/status-parity.json` (`cf-locate-status-parity/1`) holds its cases with
their expected status, written by hand, and a consumer's own implementation runs the same cases.

**The coverage claim.** A located passage may carry `coverage: {"complete": true, "parts": [<rank>,
…], "note": "<one sentence>"}` in its locate document (references/locate-documents.md §3): its
`hasPart` locations, named by rank, together state every clause of the passage, and none adds a
hypothesis stronger than the passage's. At least `MIN_COVERAGE_PARTS` (2) distinct parts, each a
`hasPart` location of the passage; the `note` says how, in the worker's own words, within the
record note cap (no more than `EXPORT_QUOTE_WORDS` words of the source verbatim, at most 2,000
characters). The gate refuses a claim on an absent or unresolved passage. Every claim is challenged
whatever the passage's prominence — refuted by default, target `coverage` (`COVERAGE_TARGET`),
actions `confirm` or `refute` (`COVERAGE_ACTIONS`) — because it upgrades a status: `confirm` makes
it `survived`, `refute` removes the claim and leaves its parts `hasPart`. An exported record
carries it as `extensions[‹locate›].coverage = {"complete": true, "parts": [<location_key>, …],
"challenge": <value>}` — those three keys (`COVERAGE_RECORD_KEYS`) and no others, on every record
of the passage, the note left out — and `outcomes` derives the status from that same projection
(`records.coverage_ext`), so a record's status is the one its claim gives.

**Parity, round 2** (2026-10-18). Run against the consumer's own implementation, the shared table
agreed on every status an exported record set can carry, and `derive_status` differed on three
inputs no export produces; each is now refused here as the consumer refuses it, by the record
shape of part A of the agreed specification:

- a claim whose challenge is `null` (owed; case C14, which round 1 expected `partial`): part A's
  record carries "<challenge value>", and part B.1's list of claims that upgrade nothing ("pending,
  not_required, sampled_out, refuted, absent") is read with A — a refuted claim is removed and an
  absent one is no claim, so neither reaches the function as a claim, and `refuted` is no
  challenge value (X11);
- a claim with a key beside `complete`, `parts` and `challenge` (a `note`: X12; a claim missing
  `challenge`: X13): the outcomes' row keeps the note, and they pass the record's projection;
- one `location_key` on two locations, whichever relation comes last (X14, X15) and with no claim
  at all (X16): a key is the sha-256 of source, item, library, commit and declaration, and gate
  rule 6 admits one location per declaration, so no run produces one.

Read literally until the maintainer says otherwise (each is a parity case marked with its
question):

- **Q1** — part B.2 of the agreed specification names definition passages and construction and
  notation, not the definition row of an `example` or `other` passage (part B.1 names the theorem
  row of both): a survived claim on an `example` or `other` passage upgrades only by the theorem
  row, so `definition` parts upgrade nothing there;
- **Q2** — "proved-or-statement-bearing" is read as the proof statuses `proved`, `statement_only`,
  `wanted` and `axiom_dependent`: a theorem passage's part that is data, or a `Prop`-valued
  definition whose proof status is `definition`, upgrades nothing;
- **Q3** — a definition passage's claim with a `statement_only`, `wanted` or `axiom_dependent` part
  upgrades nothing (part B.2 gives no `statement_only` fallback, as B.1 does for a theorem).

The proposal's table names theorem/conjecture and definition passages only. Decided here, each
the conservative way, until the maintainer says otherwise:

- a `construction` or `notation` passage introduces an object or a symbol, so it is judged as a
  definition passage (`DEFINITION_PASSAGE_KINDS`); an `example` or `other` passage is judged by
  **both** rows and takes the better (`EITHER_PASSAGE_KINDS`);
- a definition passage matched exactly or closely to a `proved` theorem is `partial`;
- the relation decides before the proof status or the `statement` flag: `hasPart` and
  `narrowMatch` (and, for a definition passage, `broadMatch`) are `partial` whatever the
  declaration is, so a sorry'd lemma stating one part of a theorem is never `statement_only` —
  unless a survived coverage claim names it, when decision D1 above judges the parts together;
- a passage whose only locations are `relatedMatch`, with no checked absence, has **no status**
  (`None`). It is what a challenge leaves when it downgrades a passage's one location to
  `relatedMatch`: nothing says the passage is formalized and nobody checked that it is absent,
  so it counts as unresolved — a miss for the safety metric, never `not_formalized`. A worker
  that searched and found only related declarations records the absence beside them, and the
  passage is then `not_formalized`.

Refused (`CfError`), because no consistent record says them: a checked absence beside a location
that counts; the `statement` flag on a `proved` declaration (`decide_proof_status` gives a
`Prop`-valued definition every status but `proved`, which is a theorem's); a `statement` that is
not a bool (absent or `null` is `false`); one `location_key` on two locations; a coverage claim
that is not `complete: true`, names fewer than two distinct parts, or names a part that is not one
of the passage's `hasPart` locations (a part a challenge refuted or downgraded: the claim is then
challenged again), whatever its challenge; a coverage claim with no challenge value (owed) or with
a key outside `COVERAGE_RECORD_KEYS`; and any value outside these sets — a `refuted` challenge
among them.

**Challenge verdicts** (`CHALLENGE_VERDICTS`, the proposal's §5): every record leaves a workspace with one.

| value | meaning |
|---|---|
| `survived` | challenged and confirmed, with a quoted witness |
| `downgraded` | challenged, and the relation or the outcome was weakened |
| `not_required` | outside every challenge rule |
| `sampled_out` | inside the stratified sample's frame, and not drawn |
| `challenge_pending` | a rule now requires it and no challenge judged it: the passage's prominence was overturned to `subject` or `central` after its records were planned under `supporting`, so its unjudged locations, absence or unresolved outcome are refuted-by-default or hunted records nobody refuted or hunted. Exported as it is; a consumer reads it as unchallenged |

`CHALLENGE_UNJUDGED` is `not_required` · `sampled_out` · `challenge_pending`: the values of a
record no verdict judged.

**The values the locate documents carry** (`references/locate-documents.md`, every one in
`cflib.vocab`'s locate block):

| enum | values |
|---|---|
| `LOCATE_DOCUMENT_SCHEMA` | `cf-locate/1`: the `schema` of every inventory, locate and challenge document |
| `RENDERER_WEB_HTML` | `web-html/3`: the renderer of the first corpus's web sources, pinned by every run, gate and score document (`web-html/1` kept style and script text in the layer and collapsed ASCII whitespace only; `web-html/2` took a named concept's bold for a paragraph label and recorded no view shas in `render.json`; no layer of either is read any more) |
| `PAGE_FLAGS` | `redirect` · `disambiguation` · `list` · `biography` — what a sourceset entry's `flags` say of its page, each `true`, `false` or `null` (not determined): the first three set at enumeration from the page info and the assessment, `biography` (Wikidata P31 = Q5) by `cf sourceset flag-biographies`; a page with one of them true is no concept article |
| `SOURCE_ORIGINS` | `enumerated` (the PageAssessments listing, at a page's latest revision) · `adopted` (a consumer's pinned revision and bytes, recorded in `adopted_from`; a run plans it instead of an enumerated revision of the same page) — a sourceset entry's `origin` |
| `LOCATE_OUTCOMES` | `located` · `absent` · `unresolved` — what one `formal_math` passage of a locate document ends with; `OUTCOME_KIND` tallies them as `LOCATION`, `ABSENCE` and `UNRESOLVED` |
| `FINDERS` | `theorem_box` · `bold_definition` · `lead` (the `DETERMINISTIC_FINDERS`, whose passages are pre-filled in an inventory task and may be dropped only with a skip naming their span) · `model` (the enumerator's own) |
| `BOLD_LABELS` | `proof` · `proofs` · `proof sketch` · `sketch of proof` · `proof idea` · `example` · `examples` · `counterexample` · `counterexamples` · `non-example` · `definition` · `definitions` · `theorem` · `theorems` · `lemma` · `lemmas` · `corollary` · `corollaries` · `proposition` · `propositions` · `remark` · `remarks` · `notation` · `exercise` · `exercises` · `claim` · `conjecture` · `case` · `cases` · `step` · `base case` · `inductive step` · `induction step` · `hint` · `note` · `notes` · `warning` · `caution` — a bold run made only of one of these (case-folded, punctuation, an enumerator or a parenthesised name stripped: "Proof.", "Case 2:", "Theorem (Widget lemma).") only labels its paragraph: `is_bold_label` is true, so it makes no `bold_definition` passage and is no L2 lexical term |
| `BOLD_LABELS_PUNCTUATED` | `solution` · `answer` · `problem` · `question` · `input` · `output` · `statement` · `observation` · `axiom` · `axioms` · `fact` · `result` · `construction` · `algorithm` · `property` · `properties` · `assumption` · `assumptions` · `special case` · `idea` — labels only when written as one ("Solution:", "Problem 3."), since bare they may be the term a sentence defines |
| `CONFIDENCES` | `high` · `medium` · `low` — a location's confidence, recorded and never an input to a status |
| `CANDIDATE_VERDICTS` | `accepted` · `rejected` — what a receipt says of each candidate it examined; a rejection carries a reason |
| `CANDIDATE_VIA` | `wikidata_tag` · `mathlib_1000` · `mathlib_100` · `docstring_url` · `library_metadata` — how an L2 **curated** candidate was found; lexical retrieval is not curated, and only curated hits bind gate rule 4 |
| `TAG_KINDS` | `wikidata` · `stacks` · `kerodon` · `wikipedia` — the tags the library index parses from a declaration's source span and docstring |
| `CHALLENGE_TARGETS` | `location` · `absence` · `unresolved` · `scope` · `prominence` · `coverage` (`COVERAGE_TARGET`: a passage's coverage claim, decision D1; its actions are `COVERAGE_ACTIONS`, `confirm` · `refute`) |
| `CHALLENGE_ACTIONS` | `confirm` and `uphold` (`CHALLENGE_KEEP`: the record survives) · `downgrade`, `refute` and `overturn` (`CHALLENGE_CHANGE`: the affected records are `downgraded`) |
| `UNRESOLVED_ATTEMPT_CAP` | `attempt_cap`: the reason on an `UNRESOLVED` document the bridge writes after a task's third genuine reviewer rejection; never a worker's |
| `UNRESOLVED_BY` | `worker` (`UNRESOLVED_BY_WORKER`: the worker could not decide) · `attempt_cap` (the bridge, at the cap) — an `UNRESOLVED` document's `unresolved.by` |
| `CHALLENGE_RULES` | `refute_by_default` (a location on a `subject` or `central` passage, and every coverage claim) · `hunt` (an absence or unresolved outcome on one) · `sample` (the stratified sample's frame: scope demotions, `supporting` demotions, weak relations) · `none` — why the challenge plan put a record where it did |
| `CHALLENGE_PLANNED` | `required` (`CHALLENGE_REQUIRED`: the record is in a challenge task and its verdict is owed) · `sampled_out` · `not_required` — what the challenge plan says a record is owed, before any verdict |
| `INDEX_FLAGS` | `is_prop_unknown` (the probe's `isProp` failed, so the status is the conservative `statement_only`) · `published_status` (a published index's own status word, accepted with `cf corpus ingest --accept-published`) — a normalised declaration's `flags` |

Bounds, also in `cflib.vocab`: `MIN_ABSENCE_QUERIES` (3 distinct queries in an absence's receipt),
`MIN_COVERAGE_PARTS` (2 distinct `hasPart` parts in a coverage claim), `COVERAGE_RECORD_KEYS`
(`complete` · `parts` · `challenge`, the keys of a record's claim and of `derive_status`'s),
`MAX_PASSAGES_PER_LOCATE_TASK` (8), `CHALLENGE_BATCH_RECORDS` (about 20 records per challenge
task), the export's quote caps `EXPORT_QUOTE_WORDS` (12 words of `exact`) and
`EXPORT_CONTEXT_CHARS` (32 characters of each of `prefix` and `suffix`), the record set's
token grammars (`references/locate-documents.md` §6, the consumer's own): `RECORDSET_TOKEN`
(letters, digits and `._:-`, at most 128, a letter or digit first: a seal's `set_id`, its
`sourceset.id` and a campaign id, which never starts `RUN_TOKEN_PREFIX`, `run:`) and
`RUN_TOKEN_ID` (letters, digits and `._-`, at most 64: a run id a run token
`run:<locate run>[+<challenge run>]` can spell, which `cf plan-locate` checks), and the run
machinery's: `MAX_TASKS_PER_ROOT` (500 tasks in one tend root; a larger plan is campaign
shards), `LOCATE_ATTEMPT_CAP` (3: the genuine reviewer rejection that reaches it writes the
`UNRESOLVED` document), `SESSION_SLOTS` (15 `flock` slots, one below the account's 16 sessions)
and `AGENT_TIMEOUT` (`45m` per agent call). The host-side scripts that cannot import `cflib`
keep copies of these, which tests pin.

**The benchmark's values** (the proposal's §8 and §9):

| enum | values |
|---|---|
| `BENCH_TIERS` | `G1` · `G3` · `GX` · `G4` · `P` — never pooled; `BENCH_TIER_ORDER` is the same tiers in the order a score and a comparison report them, `BENCH_LABELLED_TIERS` the four whose gold is labelled (`G1` · `G3` · `GX` · `G4`; P's gold is the planted ablation) and `BENCH_CLAIM_TIERS` the ones a pre-registered claim may come from (`G1` blind-arm only · `G3` · `P`) |
| `TOOLS_DB_KINDS` | `full` (`corpus.sqlite`) · `blind` (the G1-blind `corpus.blind.sqlite`) · `planted` (a module-ablated database, the P tier) — the database a locate run's workers search: a candidates file's `corpus_db.kind`, `gate.json`'s and `run.yaml`'s `tools_corpus_db.kind`, a score's pin (`references/locate-documents.md` §9) |
| `BENCH_ARMS` | `assisted` (production) · `blind` (L2's curated joins, gate rule 4 and the tag table off for the scored items); never pooled |
| `BENCH_SPLITS` | `dev` · `eval` · `test` |
| `FNF_CAUSES` | `inventory_miss` · `scope_demotion` · `absence` · `weak_relation` · `unresolved` · `gate_failed` — why a gold-formalized passage did not end `formalized`; unresolved and gate-failed tasks are misses, never exclusions |
| `ERROR_CODES` | `E-INVENTORY-MISS` · `E-RETRIEVAL-MISS` · `E-REJECTED-CORRECT` · `E-WRONG-RELATION` · `E-FALSE-ABSENCE` · `E-WRONG-SCOPE` · `E-UNRESOLVED` · `E-MEMORY` — the mechanical code of every miss in the improvement loop |
| `BENCH_LABEL_MODES` | `anchor` (a G1 entry to a passage, its relation adjudicated once) · `label` (a G3 span, blind) · `calibrate` (the hunters' recall *h*, tag-blind, on anchored G1 passages) · `inventory` (an article exhaustively, GX) — what one call of the frozen labeler does (`references/locate-bench.md`) |

Licence classes are §1.6's `LICENCE_CLASSES`.

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

A source with no numbering **and** no printed page — a web page — keys its passages as
`In-text #<hex>`, 8 to 16 lowercase hex digits of the hash of the passage's head
(`scripts/cflib/passages.py`: the vendored `passage_quote_id` over its first twelve words, extended
up to forty until no other passage of the source starts the same way). It is one production of
the in-text grammar:

- tried **last**: a printed page wins, then a named numbered item, and only a body both refuse
  can be a passage — so `In-text #3, book p. 71` is still the p.71 item and `In-text #1,
  Definition 1.4.35` still the anchored Definition 1.4.35, as they were before the production
  existed (a well-formed hash holds no `p` and no space, so it never reaches the first two);
- parsed whitespace-tolerantly like the others — `In-text#3fa91c07`, `in text  # 3fa91c07` and
  `In-text #3fa91c07` are one item, and `join_key` gives all three as `In-text #3fa91c07`;
- a hash that is uppercase, too short, too long, not hex, or followed by anything is a reject
  with reason `bad_passage_id` and the problem named in its context, never a guess;
- `clean()` and `finding_id` did not change to admit it, so as for every label the spellings join
  as one item and hash as two: a stage that writes a passage label writes
  `items.passage_item(hex)`'s spelling;
- one conversion pair, `items.passage_item(hex)` → `In-text #<hex>` and `items.passage_hex(item)`
  → the hex, and one to and from the vendored format's quote-hash id:
  `items.to_vendored_passage_id(item)` → `passage-<8 hex>` (refused for a longer hash, which the
  format's `PASSAGE_QUOTE_ID_PATTERN` does not admit) and `items.from_vendored_passage_id(id)`
  (refused for a `passage-p<page>-<hex>` id, which hashes a title, not a head).

**The head rule is frozen**, like `clean()`: the id is content-addressed, a consumer and the
out-of-tree gate must reproduce it, and changing any step re-keys every passage. Over a source's
*head text* (`scripts/cflib/passages.py`):

1. the head text is the Literature Links text layer with each formula replaced by
   `passages.formula_token(alttext)` = `⟦` + `normalize_alttext(alttext)` + `⟧` (U+27E6, U+27E7).
   `normalize_alttext` is the vendored `ids.normalize_text` (NFKC, each run of Unicode
   `White_Space` to one space, ends stripped), every `\displaystyle` control word dropped, and the
   outer braces stripped while the whole string is one balanced group, to a fixed point. The
   brackets are hashed: `⟦G⟧` and the word `G` are different heads;
2. a passage starts at the first character of a word — offset 0, or just after whitespace;
   a start inside a word, on whitespace or outside the text is refused (`PassageIdError`);
3. the words from a start are the vendored `normalize_text` of the text from there, split on
   single spaces (a formula with spaces inside it is several words; no bracket is parsed);
4. the head is the first `n` words joined by one space, for the smallest `n` in 12..40 at which
   no other passage of the source shares those `n` words (`n` is each passage's own, and it is
   stored as `head_words`; fewer than 12 only when the text runs out);
5. the hex is the vendored `passage_quote_id(head)` without its `passage-` prefix: 8 digits;
6. two passages that share forty words, an offset given twice, or two different heads with one
   hash are refused, naming both offsets — never dropped and never re-keyed.

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
