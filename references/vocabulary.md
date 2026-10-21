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
| `SCOPES` | `formal_math` (mathematics a library could state) `expository` (history, motivation, applications told about mathematics, and proof narration and commentary, PD5) `non_math` (anything else), as the inventory prompt and the bench's labeler both give them. Only `formal_math` passages are located (`LOCATED_SCOPES`); a passage may not be scoped out while the deterministic candidates hold a tag or curated hit for it |

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
| `closeMatch` | the same up to a convention (R1 below): named in the location's `convention`, from a closed list, and described in its `note` |
| `broadMatch` | the passage follows from the declaration by instantiation or weakening (R2) |
| `narrowMatch` | the declaration is the passage at special objects (R3) |
| `hasPart` | the declaration states one clause of a passage that says several things (R4) |
| `relatedMatch` | relevant, but it states something else (R5). Never counts towards a status |

Rules R1–R7 below decide between them; the table only names them.

`noMatch` (`NO_MATCH`) is the relation of an **absence** record only — one `lean` end per universe
library, no `location` — never of a location. At most `MAX_LOCATIONS_PER_PASSAGE` (5) locations per
passage, ranked. `STRONG_RELATIONS` (`exactMatch`, `closeMatch`, `broadMatch`) are the three that
formalize a theorem passage against a proved declaration; `WEAK_RELATIONS` (`relatedMatch`,
`narrowMatch`, `hasPart`) the rest. On a `supporting` passage the challenge plan re-judges both:
every location that sets the passage's derived status on its own (its **bases**: each location
whose status alone is the passage's — the passage keeps the status while any one of them
stands) is required whenever its relation is strong, the passage's other strong locations are
sampled per relation at the plan's `strong_sample_rate`, and its weak ones at its `sample_rate`
(the smoke-2 audit of 2026-10-19 found the strong relations of supporting passages, then never
challenged, right about 45% of the time, and every formalized supporting passage resting on
one). `RELATION_STRENGTH` orders the relations for a challenge's
`downgrade`, which must name a strictly weaker one: `exactMatch` > `closeMatch` > `broadMatch` >
`narrowMatch` = `hasPart` > `relatedMatch`.

**How a relation is judged: rules R1–R7** (the smoke-2 audit of 2026-10-19, its resolver's
rules, adopted for every run planned since; R3 and R5 are worded so the special case, the
weakening and the mixed case each have one answer; R1's junk values gave way to PD7's three-way
test on 2026-10-20). A worker, a challenger and the bench's labeler judge each record by them,
pairwise, the passage against the one declaration:

- **R1** — `closeMatch` only for a convention: notation, encoding, normalisation, renaming,
  unfolding, bundling (the location's `convention`). A strengthening, a generalisation or a
  weakening is never one, and neither is a junk value (PD7). Where the declaration's map
  f′ : X′ → Y extends the passage's f : X → Y (X ⊂ X′): if f′ agrees with f on X and every case
  outside X is mathematically trivial — a junk or default value, given where the passage leaves f
  undefined, that the passage's mathematics does not depend on (x/0 = 0, `riemannZeta` at its
  pole s = 1), or an extra case that reduces to a tautology (p = q, where the statement becomes
  IsSquare 0 ↔ IsSquare 0) — it is `exactMatch`; if it agrees on X and some case outside X has
  real mathematical content, it is `broadMatch` (R2), a generalisation; if f′(x) ≠ f(x) at some
  x where both are defined, it is `closeMatch` (a convention) or weaker.
- **R2** — `broadMatch` when the passage follows from the declaration by instantiation or
  weakening (a general codomain, a strict derivative for a plain one, an iff for an if, all
  primes for a finite table), with no hypothesis stronger than the passage's; a stronger
  hypothesis makes it at most `narrowMatch` (R3).
- **R3** — `narrowMatch` when the declaration is the passage at special objects: the passage,
  instantiated at the declaration's objects, says what the declaration says (a natural-number
  statement for an integer one, a field for a commutative ring, the n = 1 case). It is also
  `narrowMatch` when the declaration proves the passage's conclusion, or a stronger one, under
  stronger hypotheses that some of the passage's own cases meet (global continuity where the
  passage has local): a special case (PD4).
- **R4** — `hasPart` when the declaration states one clause of a passage that says several
  things; `exactMatch`, `closeMatch` and `broadMatch` only when it states every clause.
- **R5** — `relatedMatch` when the connection needs another theorem, or the two statements are
  incomparable: a setting that neither contains nor is contained in the passage's (a
  finite-dimensional lemma where the passage's spaces are infinite-dimensional), or a
  declaration with stronger hypotheses whose conclusion does not give the passage's (one whose
  conclusion does is R3's special case). A declaration that follows from the passage by
  weakening states part of it at most: `hasPart` when what it states is one of the passage's
  clauses, else `relatedMatch`. So `hasPart` needs the declaration alone, instantiated at the
  passage's objects, to entail a clause the passage asserts, and `narrowMatch` needs the
  passage, instantiated at the declaration's objects, to say what the declaration says.
- **R6** — relations are pairwise: what else the universe holds never changes a record's
  relation.
- **R7** — a definition passage is judged on the data it defines; bundled proof fields are
  packaging (R1's bundling).

The **policy defaults**, the maintainer's (PD1–PD4 of 2026-10-19, PD5–PD10 of 2026-10-20),
numbered PD1–PD10 apart from the review patterns' P1–P13 (`references/review-patterns.md`). Each
is a default, not a law of the format: it can be overridden, and an override is a change to this
paragraph, to both prompts (`templates/prompts/locate.md.tmpl` and `locate-challenge.md.tmpl`)
and to the bench's labeler (`workflows/locate-label.workflow.js`, whose `LABELER_VERSION` it
bumps), in one commit — all three carry these lines word for word, and
`tests/test_locate_judgment.py` holds them together; PD5 is also the inventory prompt's
(`templates/prompts/locate-inventory.md.tmpl`) — followed by a relabel of the bench's gold,
which lives on the operator's machine (`references/locate-bench.md` §1) and so is never in that
commit. From PD5–PD9 on, the bench's score refuses gold labelled under other judgment rules than
the runs it scores (`references/locate-bench.md` §8), so a run planned after an override waits
for that relabel:

- **PD1** — a definition stated over a general type parameter where the passage has a specific
  one (any ring where the passage has ℂ) is `broadMatch` (R2).
- **PD2** — a statement over ℕ where the passage has ℤ is `narrowMatch` (R3).
- **PD3** — a trivial or degenerate special case (the n = 1 case of a matrix identity) is
  `narrowMatch`, never vacuous: it is not refuted for being trivial.
- **PD4** — a declaration that proves the passage's conclusion, or a stronger one, under
  stronger hypotheses that some of the passage's own cases meet (global instead of local
  continuity) is `narrowMatch` (R3), not `relatedMatch` and not `broadMatch`.
- **PD5** — only definitions, theorems, equations and declarative statements about mathematical
  objects are `formal_math` passages. Proof narration and commentary (what a proof uses, how it
  goes) is none: the inventory scopes it `expository` or leaves it out, so it is never located and
  has no status. Inside a proof, each definition, theorem, equation or declarative statement is a
  passage of its own. Linking a single word to the Lean definition it names is a separate feature,
  not a passage.
- **PD6** — a part of a coverage claim that alone states every clause is no part: it is one
  location with its own relation (a centralizer defined in any ring, where the passage has a
  matrix's centralizer, is `broadMatch`, PD1), and a coverage claim stands only on parts that
  each state some of the clauses.
- **PD7** — a value the declaration gives where the passage leaves one undefined (a junk or
  default value, R1) is trivial when the passage's mathematics does not depend on it — in the
  maintainer's words, "it shouldn't matter what the value of riemannZeta 1 is" — so
  `riemannZeta` at s = 1 is a junk value against a passage that defines the zeta function away
  from s = 1, though Mathlib proves a closed form for it (`riemannZeta_one`). That test is for
  such values: an extra case of the statement itself (a ring that is not a field, where the
  passage has a field) is trivial when it reduces to a tautology, and one with real mathematical
  content makes a generalisation, never a junk value. Whether a case is trivial is still a
  judgment: an `exactMatch` or `broadMatch` that rests on it says why in its `note`. (PD3's
  trivial case is the other way round: a declaration that states only a degenerate case of the
  passage.)
- **PD8** — a passage follows from a declaration by instantiation (R2) only when, after
  instantiating, what is left closes by decidable arithmetic (`omega`, `norm_num`, `decide`); a
  bridge that needs a named lemma (`Nat.mul_mod`, a parity lemma for (−1)^n) makes it
  `relatedMatch` (R5). R2's weakenings (a strict derivative for a plain one, an iff for an if)
  are no instantiation: PD8 leaves them to R2.
- **PD9** — a stated reason ("it holds because X") is a passage of its own only when X is a
  declarative mathematical statement that no neighbouring passage already states; otherwise X is
  no passage and no clause.
- **PD10** — one notion in two encodings is no bridge: a declaration that uses Mathlib's
  standard notion where the passage has its own definition of the same thing (`IsCoprime m n`
  where the passage says m and n have no common factor other than 1, linked by
  `Int.isCoprime_iff_gcd_eq_one`) differs from the passage by an `encoding` (R1: a `closeMatch`
  when nothing else differs), and the library lemma that links the two definitions is no PD8
  bridge. It sharpens R1's encoding and limits PD8, which governs bridges between statements,
  not between two definitions of one notion. Its exception is a link that is itself what the
  passage asserts: when the lemma that links the two notions is the passage's own theorem, a
  declaration stated with the notion whose definition already contains that theorem's claim
  presupposes the passage, and it is `relatedMatch` (R5).

PD1 makes a definition passage stated over a specific object `partial` against a definition over
a general one (`derive_status` counts a definition passage's `broadMatch` as `partial`); PD2 and
PD3 make a theorem passage `partial` against such a declaration, never `formalized` or unresolved.

What PD5–PD10 replace or sharpen (the smoke-3 audit's open policy calls, its FM8; PD10 and
PD7's test for "trivial" are the maintainer's later answers that day):

- PD5 sharpens the inventory's `expository` scope (FM9: a set-up passage scoped formal) and
  settles the audit's question about proof passages: a proof is not one passage whose displayed
  steps are its clauses, nor one the theorem it proves formalizes — each statement in it is
  located on its own, with the kind of what it states (an equation or a declarative statement
  that asserts a fact is a `theorem`, one that defines an object a `definition`). The `step` "on
  a proof" (FM3) stays for inventories planned before it. PD5 is the inventory's rule: a locate
  worker cannot re-scope a passage, so one the inventory scoped `formal_math` that is only
  narration ends `unresolved`, its `reason` naming PD5 — exported as one `UNRESOLVED` record
  with no status, counted in the unresolved rate, and hunted by the challenge when `subject` or
  `central` — until the source is inventoried again. No challenge re-scopes it: a challenge
  re-reads only a scope that was demoted. The unresolved rate is where such an inventory defect
  shows.
- PD6 sharpens R4 and the coverage claim of decision D1: the maintainer's example is
  `Subring.centralizer` for a matrix's centralizer, one `broadMatch` card ("more general"), not
  a part. Gate rule 9 refuses such a part under judgment rules 2. A challenge reaches less than
  the rule: it refutes a claim whose part states every clause of its own claim list, and since
  it never raises a relation that part stays a `hasPart`, so the passage is `partial` where the
  one location PD6 makes of it could derive `formalized` (a `broadMatch` to a proved theorem, on
  a theorem passage). A challenge planned under rules 2 of a locate run planned under rules 1
  can so lower a passage's status that the run's coverage claim had raised; locating the
  passage again under rules 2 gives it PD6's one location.
- PD7 replaces R1's "junk values", which made a junk value a `closeMatch` convention:
  `junk_value` leaves `CLOSE_MATCH_CONVENTIONS` (gate rule 9 refuses it under judgment rules 2),
  and the prompts' example closeMatch note, Lean returning 0 at n = 0, is now an exactMatch's.
  Its test for a value outside the passage's domain, the maintainer's, given later the same day,
  is whether the passage's mathematics depends on that value, not whether the value has a theory
  of its own: `riemannZeta` at s = 1 is R1's junk-value example again (an earlier text of the
  rule left it to the record's note, since Mathlib proves a closed form there). The test is of
  values, not of the extra cases of a statement, which are trivial when they reduce to a
  tautology (the paragraph after the maintainer's words says why).
- PD8 sharpens R2's "instantiation" and R5's "the connection needs another theorem". It is
  read as the maintainer recorded it, of what is left "after instantiating": R2's other route, a
  weakening (a strict derivative for a plain one, which needs `HasStrictDerivAt.hasDerivAt`), is
  no instantiation and stays `broadMatch` by R2.
- PD9 sharpens the claim list (R4, FM1), beside "history, motivation and attribution are not
  claims". The maintainer's answer of 2026-10-21 to the statement cut's question 5 rewords it
  with the same test: a stated reason X becomes a passage of its own, cut by the inventory,
  rather than a clause of the passage it supports (the words above, which the inventory, locate
  and challenge prompts and the bench's labeler carry). A run of judgment rules 2 was planned
  under the earlier words, which made X a claim of that passage's list; the gate checks neither
  wording.
- PD10 sharpens R1's `encoding` and limits PD8: a library lemma that links Mathlib's standard
  notion to the passage's notion of the same thing (`IsCoprime` and "no common factor other than
  1", through `Int.isCoprime_iff_gcd_eq_one`) is an encoding difference, not a PD8 bridge, since
  PD8 governs bridges between statements, not between two definitions of one notion. PD8 is
  otherwise as recorded, and a weakening stays R2's. The maintainer named the difference, an
  encoding (R1); that it is a `closeMatch` when nothing else differs is the relation table's
  definition of `closeMatch`, the same up to a convention, not a further ruling of his.
  Neither is PD10's exception, a link that is itself what the passage asserts. His example
  links two definitions the passage takes as given (the passage is about something else that
  holds of coprime numbers); when the link is the passage's own theorem, a declaration whose
  notion contains that theorem's claim by definition states nothing of the passage that the
  linking lemma does not, so reading PD10 there would make it a formalization of the theorem
  it presupposes. That is R5's "the connection needs another theorem", which before PD10
  PD8's named-lemma bridge gave as well; it is recorded as the exception so that PD10 does
  not reverse it, and it awaits the maintainer's confirmation.

**PD10 and PD7's test kept the labeler at `locate-label/5`**, and so did PD10's exception, which
joined them later the same day. An override bumps the labeler's version (the paragraph above);
these did not, because what a version decides in the bench is the judgment rules
`bench.gold.LABELER_RULES` maps it to, which the score holds gold and runs to, and they add no
gate check: `JUDGMENT_RULES` stayed 2, so a `/6` would have mapped to 2 too and the score would
have refused nothing it did not refuse then. `/5` had labelled no gold when they joined it, and the labeler's
file sha moved with them, so the transcriber refuses a result of args emitted from the earlier
text and gold of `/5` is labelled by this one. Once gold has been labelled under a version, a
change to the rules it carries bumps it. What neither version nor rules can hold is a run of
judgment rules 2 planned from the earlier text, whose prompts lack PD10 and PD7's test (or PD10's
exception): the score holds a run to its `judgment_rules`, and pins its prompts' shas
(`config_shas.prompts`) in the measurement without comparing them with the gold's labeler, so it
would score such a run against gold of this text, and a `/6` would not refuse it either. None had
been planned when these rules joined; one planned from a checkout of the earlier text is planned
again from this one before it is scored. The statement cut's flip, which adds gate checks
(judgment rules 3), is the bump: the labeler is `locate-label/6`, which
`bench.gold.LABELER_RULES` maps to 3, so gold of `/5` scores no run planned from the flip on.

**PD7 in the maintainer's words** (2026-10-20), verbatim:

> A junk value means that X is exactly the same as Y, but the Lean case is just defined over
> certain trivial values that do not effect the proof because they are usually left undefined.
> Eg x/0. The Lean `f' : X' -> Y` extends the informal map `f : X -> Y` with X ⊂ X'. A non junk
> value (e.g. closematch) would be when f(x) ≠ f'(x) for some x where both are well-defined.

and, the same day:

> this is slightly subjective regarding what "trivial" exactly means. If there are cases outside
> of the passage's domain which are non-trivial mathematically then this should be marked as a
> generalization not a junk value.

and, later the same day, of `riemannZeta` at s = 1:

> RiemannZeta is hard but I'd still classify it as a junk value personally since the mathematics
> here doesn't actually depend on the value of riemannZeta 1. The point is that it shouldn't
> matter what the value of riemannZeta 1 is, and that is still the case.

So agreement on the passage's domain with only junk outside it is `exactMatch`: a division with
x/0 = 0 states a passage that leaves x/0 undefined, and Mathlib's `riemannZeta`, which takes a
value at its pole s = 1, states a passage that defines the zeta function away from it. An extra
case that is only a tautology (quadratic reciprocity's p = q, where both sides become
IsSquare 0 ↔ IsSquare 0) is junk too. A case outside the domain that says something — a
statement proved for every ring where the passage has a field, whose other rings carry content
of their own — is a generalisation, `broadMatch`. Either way the record's note says which. The
test for a value is the maintainer's last word: whether the passage's mathematics depends on the
value the declaration gives where the passage gives none. A value with a theory of its own can
still be junk to the passage: Mathlib's documentation calls ζ(s) undefined at s = 1 and proves a
closed form for the value it assigns there (`riemannZeta_one`), and a passage that defines the
zeta function away from s = 1 depends on neither. The test is of values, which his first and
last words speak of ("trivial values … usually left undefined", "the value of riemannZeta 1"),
and not of an extra case of the statement, which his second words settle: the passage's
mathematics does not depend on the rings that are not fields either, yet a statement proved for
them says something of its own and so makes a generalisation, where p = q says nothing. A record
of rules 1 whose `junk_value` sits on a declaration that differs from the passage where both are
defined (a count that is 0 for an infinite group, where the passage counts with cardinals) was
never a junk value: it is a `closeMatch` by a real convention, or weaker.

**The challenger's rule** (the audit's FM7: 8 of 25 challenge results were wrong, each a reasoning
slip, not a search miss), in the challenge prompt word for word:

- **FM7** — before you downgrade or refute, check the record against the passage's clauses,
  its conjuncts or the one clause of a simple statement. Apply D1: an exact or close match to an
  equivalence or other data that states the theorem formalizes it, so a bijection is the data
  form of a cardinality equation and stays `closeMatch`. Keep definitionally linked declarations
  consistent: two declarations on one passage defined one from the other, or one object in two
  encodings, get the same relation unless their statements differ — your task holds every
  required location of a passage, and each location record shows the passage's other locations
  (`passage_locations`), read-only. A trivial special case is `narrowMatch` (PD3), never a
  refute.

Until the statement cut (judgment rules 3) the challenger wrote a claim list of its own and the
rule read "check the record against your claim list of the passage"; under rules 3 the
inventory owns the one list, the passage's conjuncts (`docs/proposals/statement-cut.md` §1.4), so
a challenge prompt planned under rules 3 says the words above. The locate and challenge prompts
render the statement cut's text only for a plan whose `judgment_rules` is 3 (the slot
`judgment_rules_statement_cut` of `locate.prompts`, read from `JUDGMENT_RULES`, which the
planner writes into the run's gate.json): a plan of rules 2, before the proposal's flip,
renders the text it rendered before — the claim lists, this rule's earlier words and PD9's
("a stated reason … makes X a claim only when …") — byte for byte, so its prompts never
contradict its gate.

**The statement cut's prompt rules** (the smoke-3 audit's §5 fixes 2a, 2e, 3a, 4b and 5a, as
`docs/proposals/statement-cut.md` §5 names them), in both locate prompts and the bench's
labeler word for word. They are judgment, which the challenge re-checks; the gate holds only
their mechanical halves (a strong location's `tests` and `instantiation`, a `hasPart`'s by its
`conjunct_relation`, an absence's `special_case`):

- **Fix 2a** — every `exactMatch`, `closeMatch` or `broadMatch` writes both instantiation tests,
  one line each, as its `tests`: `r2`, how the passage follows from the declaration (by
  instantiation, or by one of R2's weakenings), and `r3`, why the declaration, at the passage's
  own objects, needs nothing the passage does not give (no stronger hypothesis, no extra instance
  argument). A bundle takes its weakest part's relation, so a `hasPart` whose
  `conjunct_relation` is one of the three writes both too, of the conjuncts it states in place
  of the passage. A `closeMatch` stands only when both hold with a named convention and nothing
  else differs; when either fails, the relation is weaker.
- **Fix 2e** — when the declaration is defined beyond the passage's domain, its `instantiation`
  line states PD7's three-way test: whether it agrees with the passage on the passage's domain,
  and whether each case outside that domain is trivial (a junk or default value the passage's
  mathematics does not depend on, or an extra case that reduces to a tautology) or has real
  mathematical content.
- **Fix 3a** — a lemma that only unfolds a definition the passage glosses (a membership,
  application or definitional lemma, `mem_*_iff`, `*_apply`, `*_def`, restating what the
  passage's term means) is `relatedMatch`, never a part; a name is no statement, so read what
  the lemma states before you credit it.
- **Fix 4b** — on a definition, construction or notation passage, a declaration that is the
  defined object is an `exactMatch` or `closeMatch` (R7), never a part; and before you call a
  declaration's hypothesis stronger than the passage's, read it with its roles swapped (a
  symmetric hypothesis, the two objects exchanged), and call it stronger only when no such
  reading is the passage's.
- **Fix 5a** — search each clause's special case too: for every clause, one query for its
  conclusion under a stronger hypothesis (PD4's `narrowMatch`), and search the universe's
  statement-only conjecture libraries, where a `narrowMatch` to a declaration stated without a
  proof still makes a theorem passage `partial`.

**The failure modes** the rules answer, as the audit numbered them (the labels code, tests and
the changelog cite):

| label | failure mode | what answers it |
|---|---|---|
| FM1 | a whole-passage relation for a declaration that states only some clauses | the claim list, `clauses`, R4, the gap phrases |
| FM2 | the challenge plan never re-checked strong relations or absences on supporting passages | the plan's `basis` rule and its strong and absence samples |
| FM3 | a proof ingredient, a bridge lemma or a neighbouring fact credited as a part | R5, a `hasPart`'s or `narrowMatch`'s `step` |
| FM4 | generality in the wrong direction, or `closeMatch` for what is no convention | R1–R3, `convention`, the generality phrases |
| FM5 | an incomplete hunt: a stating declaration missed, or a false absence | a query of its own per claim (`query_clauses`), the rejected-candidate check, the absence sample |
| FM6 | a setting mismatch: the declaration's hypotheses exclude the passage's objects | `instantiation`, R3 and R5 |
| FM7 | a challenger's reasoning slip (D1 ignored, a special case refuted, two encodings split) | the challenger's rule above, its own claim list on every verdict |
| FM8 | policy the rules did not settle | R1–R7 and PD1–PD10 |
| FM9 | data and inventory defects (a statement flag, a cut span, a set-up passage scoped formal) | not the judgment rules: ingest and inventory work |

What the gate holds mechanically, under the judgment rules (gate rule 9,
`references/locate-documents.md` §3–§4): R1's closed list, a `convention` on a `closeMatch` only,
and the words an `exactMatch` or `closeMatch` note may not say; R4 through the claim list and each
record's `clauses`, and a strong record whose note or instantiation admits a gap; R5 through a
declaration's `instantiation` and a `hasPart`'s or `narrowMatch`'s `step`; an absence's queries,
a query of its own per claim, and its rejected candidates; and, in a challenge, the
challenger's own claim list under every verdict on a passage's records. Under judgment rules 2
(`JUDGMENT_RULES`) two more: PD6 — a coverage claim one of whose parts names every claim alone
is refused, and so is a challenger's confirm whose `part_clauses` give one part every claim of
its own list — and PD7 — `junk_value` is no convention, save on a challenger's confirm that keeps
a record's own `junk_value` by naming no convention or naming it again (written under rules 1: a
challenge never raises a relation, so it cannot make that record the `exactMatch` PD7 would); a
confirm that names another convention relabels the record and is held to the rules-2 list. It
rests on the claim list a worker writes:
a list that leaves a clause out, with an `exactMatch` naming every claim on it, passes, and only
the challenge re-checks it — the phrase lists are tripwires that catch an honest note, not a
parser. The rest is judgment, which the challenge re-checks: PD5 (what the inventory makes a
passage), PD7's "trivial", PD8's bridge, PD9's stated reasons and PD10's encodings are not
checked mechanically.
A bridge lemma named in a `broadMatch`'s `instantiation` cannot be told from the instances and
hypotheses the line names by right (`Fact p.Prime`, a ring instance, the declaration's own
name), and a `hasPart` naming every claim outside a coverage claim may be a weakening credited
with its clause or a claim list too coarse to show the part, so neither is refused.
Under judgment rules 3 (`RULES_STATEMENT_CUT`, the statement cut below; bound by a gate.json
that names it, which every run planned from the proposal's flip on does) the claim list is the
inventory's: R4 is checked against the frozen passage's conjunct ids (or `c1`), and no locate
or challenge document writes `claims`. Mechanically, then: a `hasPart` of a compound statement
gives its `conjunct_relation` (a `closeMatch` part its convention) and nothing else does; a
coverage claim is a bundle — on a compound statement only, its parts' clauses partitioning the
conjuncts, none naming them all, each part's `conjunct_relation` strong — and a compound
statement whose strong parts partition its conjuncts without one is refused (the audit's fix
4a); every strong location writes its two `tests` and an `instantiation`, and is refused when
the passage speaks of integers (`NAT_FOR_INT_INTEGER_WORDS`: "integer", "integers", `ℤ`) and
every number-typed binder of the declaration is ℕ, unless its note names ℕ or the naturals
(`NAT_FOR_INT_NATURAL_WORDS`: `ℕ`, "natural*"; a word whole, as the phrase lists read it, a
symbol anywhere); the generality words are `CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT`; a
`hasPart` part is held to all of these by its `conjunct_relation`, so a part whose
`conjunct_relation` is strong is a strong location (the bundle takes its weakest part's
relation); an absence, an uphold and an absent fill name their nearest `special_case`, and an
absent fill accepts no candidate; a `cut_flag` sits on an unresolved passage only; a
challenger's bundle confirm gives `part_relations` no stronger than each part's own, and stands
beside no verdict of its document whose `clauses` give one part every conjunct (PD6); a bundle
refute gives `unstated` and a reason for every part, a verdict that keeps or leaves a strong
relation — a part's by the `conjunct_relation` it leaves — its own `tests`, a document that
leaves a located passage with no counting location its `fills`, and a prominence sample's
conditional records are judged exactly beside an overturn of their passage to subject or
central (`REFUTE_BY_DEFAULT_PROMINENCE`). Gate rule 10 (`statement_cut_rule`, with
`span_end_rule` 2) holds the
inventory's cut: disjoint spans, one text one passage; no run from one block into the next but
inside a theorem box, into the displays a sentence introduces, or over the items of one list of
a compound statement; no end inside a formula; conjuncts none or 2–5 of a closed shape, on a
`formal_math` passage; a passage scoped out `supporting`. What one statement is, where a
sentence is cut, chains, proofs and PD9's stated reasons stay judgment: rule 10 counts each
formal passage's sentences and displays into the harvest receipt and refuses nothing on them.

**The derived status** (`DERIVED_STATUSES`, best first): the Literature Links `status` of a
passage's records, computed once after challenge by `vocab.derive_status(passage_kind, locations,
absent, coverage=None)`, each location giving its `relation`, `proof_status`, optional `statement`
flag and, when a coverage claim names it, its `location_key` (distinct: one key on two locations is
refused); `coverage` is the passage's coverage claim exactly as a record carries it (below:
`COVERAGE_RECORD_KEYS`, `complete` · `parts` · `challenge`, the challenge a `CHALLENGE_VERDICTS`
value) or `None`, and the three-argument call is the one it always was:

| status | when |
|---|---|
| `formalized` | theorem/conjecture passage: `exactMatch`, `closeMatch` or `broadMatch` to a `proved` declaration; `exactMatch` or `closeMatch` to **data** — a `definition` without the `statement` flag (an equivalence, isomorphism, bijection, structure-valued construction: a theorem that two quotient rings are isomorphic, as a `RingEquiv`). Definition passage: `exactMatch` or `closeMatch` to a `definition`. Any passage: a **survived coverage claim** whose every part is finished — `proved`, or data |
| `statement_only` | the best such location is `statement_only`, `wanted` or `axiom_dependent`, or is a `Prop`-valued definition stating a theorem passage; or any passage's survived coverage claim with an unfinished part — `statement_only`, `wanted`, `axiom_dependent`, or a `Prop`-valued definition |
| `partial` | only `hasPart` or `narrowMatch`, or `broadMatch` to a definition, with no coverage claim that upgrades them |
| `not_formalized` | a checked absence, possibly beside `relatedMatch` locations |
| none (`None`) | unresolved: no location that counts and no absence — including a passage whose only locations are `relatedMatch` |

A survived coverage claim acts, for the status, exactly like **one more `exactMatch` location**
(the reading of D1 that parity round 3 settled, below): a `proved` declaration to the theorem row
and a `definition` to the definition row when every part is finished, so it formalizes the passage
on either row, and a `statement_only` declaration, `statement_only` on both rows, when any part is
unfinished. An `example` or `other` passage judges it on both rows, the better winning, as it
judges every location. The best status over all the evidence wins (`formalized` >
`statement_only` > `partial` > `not_formalized`). A coverage claim whose challenge is anything but
`survived` — `challenge_pending`, `not_required`, `sampled_out`, `downgraded` — adds nothing; a
refuted claim is removed by the outcomes before the status is derived, and an owed claim (no
challenge has judged it yet, so it has no challenge value) is passed as no claim, which gives the
same status.

**The maintainer's decision D1** (2026-10-18), verbatim:

> D1: yes. Formalized when the hasPart locations together cover every clause (challenger confirms
> coverage), and when an exact/closeMatch is to an equivalence or data declaration that states the
> theorem (e.g. CRT's RingEquiv). Update vocab and WikiLean's parity table.

It replaces two of the decisions this section used to make the conservative way: a theorem passage
matched exactly or closely to data was `partial` (as its `broadMatch` still is), and a passage that
says several things — a conjunction of clauses — stated clause by clause by `hasPart` locations was
`partial` however completely they covered it. The table above is the decision; the shared parity
table `tests/data/locate/status-parity.json` (`cf-locate-status-parity/1`) holds its cases with
their expected status, written by hand — and, on the cases that carry `expected_challenges`, the
challenge each location's exported record carries once the claim's verdict is applied — and a
consumer's own implementation runs the same cases.

**The coverage claim.** A located passage may carry `coverage: {"complete": true, "parts": [<rank>,
…], "note": "<one sentence>"}` in its locate document (references/locate-documents.md §3): its
`hasPart` locations, named by rank, together state every clause of the passage, and none adds a
hypothesis stronger than the passage's. At least `MIN_COVERAGE_PARTS` (2) distinct parts, each a
`hasPart` location of the passage; the `note` says how, in the worker's own words, within the
record note cap (no more than `EXPORT_QUOTE_WORDS` words of the source verbatim, at most 2,000
characters). The gate refuses a claim on an absent or unresolved passage. Every claim is challenged
whatever the passage's prominence — refuted by default, target `coverage` (`COVERAGE_TARGET`),
actions `confirm` or `refute` (`COVERAGE_ACTIONS`) — because it upgrades a status: `confirm` makes
it `survived` and **confirms its parts** — each part whose own record no verdict judged
(`not_required`, `sampled_out`) is `survived` too, a part a verdict judged keeps that verdict
(`outcomes.confirm_parts`) — and `refute` removes the claim and leaves its parts `hasPart` with
the values they had. The confirmation stands in only for parts no rule required: a prominence
overturn to `subject` or `central` in the same run reopens the passage first, and a part it made
`challenge_pending` stays pending, owed its own verdict under the stricter rule, while the claim
stays `survived` and the status is unchanged (parity case P05). The challenge planner puts a
claim and its parts in one task, so one challenger judges them together. A part refuted, or downgraded out of `hasPart`, by another
verdict lapses the claim: it upgrades nothing, confirms no part, and the export refuses the
passage until the claim is challenged again. An exported record
carries it as `extensions[‹locate›].coverage = {"complete": true, "parts": [<location_key>, …],
"challenge": <value>}` — those three keys (`COVERAGE_RECORD_KEYS`) and no others, on every record
of the passage, the note left out — and `outcomes` derives the status from that same projection
(`records.coverage_ext`), so a record's status is the one its claim gives.

**The bundle** (judgment rules 3 and status rule 2, `docs/proposals/statement-cut.md` §1.3,
§1.4 and §2): under the statement cut a coverage claim is a bundle or nothing. It stands only
on a compound statement — a passage the inventory gave `conjuncts` and a `shape` — and its
parts are `hasPart` locations whose `clauses` name the inventory's conjunct ids, pairwise
disjoint, together every conjunct, none all of them, each with a `conjunct_relation`
(`CONJUNCT_RELATIONS`) to the conjuncts it states that is one of `BUNDLE_RELATIONS`. A
challenger's **confirm** gives `part_relations`, one per part, none stronger than the part's
own (a challenge never raises a relation), and its **refute** names the conjuncts no part
states (`unstated`) and, for every part, why no R1 convention reaches its conjunct
(`part_reasons`); "the parts state it after unfolding or encoding" is a confirm. The bundle's
relation is its weakest part's (status rule 2 above), and a record of `cf-locate-recordset/2`
carries it as `{"complete": true, "parts": [<location_key>, …], "challenge", "relation",
"conjuncts": [{"id", "text", "part": <location_key>}], "bundle_key"}`
(`COVERAGE_RECORD_KEYS_BUNDLE`): the conjuncts in id order, each beside the part that states it,
held to the export's free-text cap, and `bundle_key` the `cflib.hashing.bundle_key` of the
passage key and the parts' pair keys, so a consumer shows a bundle's declarations each beside
its conjunct, and keys a vote on it, from the record set alone.

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

**Parity, round 3** (2026-10-18): the reading of D1. Rounds 1 and 2 read three points of part B
of the agreed specification literally and left them open as Q1–Q3; round 3 resolved all three,
from D1, with one rule, and the parity cases that turn on them are marked with their question
(and say what round 2 expected). The rule: a survived coverage claim acts, for the derived
status, exactly like one more `exactMatch` location, and that location is a **fixed stand-in**,
not any one of the parts. Each part is **finished** (`proved`; a `definition` without the flag,
data) or **unfinished** (`statement_only`, `wanted`, `axiom_dependent`; a `definition` with the
flag, a `Prop`-valued definition). Every part finished: the stand-in is a `proved` declaration on
the theorem row and a `definition` (no flag) on the definition row, so the claim formalizes the
passage on either. Any part unfinished: it is a `statement_only` declaration on both rows. It is
not an `exactMatch` to any one part's own proof status and flag, the least finished one's
included: a definition passage's claim of `proved` parts formalizes it, where an `exactMatch` to
a `proved` theorem is `partial`, and one with a `Prop`-valued definition part is
`statement_only`, where an `exactMatch` to that definition formalizes it (O07). Everything else about the status is unchanged, a claim that is
not survived adds nothing, and confirming a claim confirms its parts (above) — except a part a
prominence overturn in the same run reopened, which stays `challenge_pending` (the reopen wins).

- **Q1** (resolved) — an `example` or `other` passage judges a survived claim on **both** rows,
  the better winning, exactly as it judges an ordinary location: `definition` parts formalize it
  (E03, E04, R08–R11). Rounds 1 and 2 applied part B.2 to definition, construction and notation
  passages only, and left these `partial`.
- **Q2** (resolved) — a data part is finished, as data that states a theorem is (part B.3): a
  theorem passage's claim with `proved` and data parts is `formalized` (C08, R01, R02), and a
  `Prop`-valued definition part is unfinished, so its claim is `statement_only` (C09, R03).
  Rounds 1 and 2 counted only the proof statuses `proved`, `statement_only`, `wanted` and
  `axiom_dependent`, and left both `partial`.
- **Q3** (resolved) — a definition passage's claim with an unfinished part gets the
  `statement_only` an `exactMatch` to an unfinished declaration gets (D07, D08, R05, R06).
  Rounds 1 and 2 gave part B.2 no such fallback, and left them `partial`.

One case outside the three moved with them: a definition passage's claim whose part is a
`Prop`-valued definition (D04, R07) is `statement_only` — the rule's order makes such a part
unfinished on every row — where round 2 said `formalized`. An `exactMatch` to a `Prop`-valued
definition alone still formalizes a definition passage (O07): the claim's rule, not the
definition row, decides that its parts are finished.

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

**Status rule 2: a bundle takes its weakest part's relation** (`STATUS_RULE_BUNDLE`; the
maintainer's answer of 2026-10-21 to the statement cut's question 9, accepting its
recommendation: a bundle of broader parts is `broadMatch`, so a definition passage with only
broader parts is `partial`, as with a single broader card; `docs/proposals/statement-cut.md` §2). Each
version of the derivation binds only the locate runs whose `run.yaml` names it as
`locate.status_rule`: one without the key is status rule 1, everything above, and its tallies
and record sets are what they always were. `derive_status(…, status_rule=2)` reads a coverage
claim as a **bundle**, the claim a record of `cf-locate-recordset/2` carries: keys within
`COVERAGE_RECORD_KEYS_BUNDLE` — `complete` · `parts` · `challenge` · `relation` · `conjuncts` ·
`bundle_key` — its `relation` required and one of `BUNDLE_RELATIONS`, its `conjuncts` (when
given) `c1` … `cN` in order, each `{id, text, part}` beside the one part that states it and
every part stating at least one, and its `bundle_key` (when given) 64 lowercase hex. A survived
bundle acts as one more location of **its own relation**, where status rule 1 makes it one more
`exactMatch`, with the same fixed stand-in for its proof status (finished when every part is
`proved` or data). Only the definition row's `broadMatch` changes:

| passage | bundle relation | parts | status rule 1 | status rule 2 |
|---|---|---|---|---|
| theorem, conjecture | `exactMatch` / `closeMatch` / `broadMatch` | finished | `formalized` | `formalized` |
| theorem, conjecture | any of the three | unfinished | `statement_only` | `statement_only` |
| definition, construction, notation | `exactMatch` / `closeMatch` | finished | `formalized` | `formalized` |
| definition, construction, notation | `broadMatch` | finished | `formalized` | **`partial`** |
| definition, construction, notation | `broadMatch` | unfinished | `statement_only` | **`partial`** |
| example, other | `broadMatch` | finished | `formalized` | `formalized` (the theorem row) |

The bundle's relation is computed, never written (`outcomes.bundle_relation`): each part's
`conjunct_relation` as its locate document wrote it (or a verdict on the part weakened it),
and, once a challenger confirms the claim, that confirm's `part_relations` entry for the part
where it is weaker: a challenge never raises a relation, so an entry stronger than the part's
own (which the gate refuses) changes nothing; then the weakest over the parts (the greatest
`RELATION_STRENGTH`). A bundle of an `exactMatch` and a `broadMatch` part is a `broadMatch`
bundle. A bundle whose relation is no bundle's — a part with no conjunct relation, or a part
that states only a special case of its conjuncts (`narrowMatch`) — or one on a passage the
inventory gave no conjuncts no longer holds: it upgrades nothing and the export refuses the
passage until it is challenged again. Status rule 2 adds one derivation outside
`derive_status`: a `located` passage the challenge leaves with no location that counts and no
absence is **owed a hunt** — its challenge is `challenge_pending`, its outcome row carries
`owed: "hunt"` (`CHALLENGE_RULE_HUNT`) and a `problem`, so the export refuses it, and the tally
counts it as `owed_hunt` (the smoke-3 audit's anomalies 1 and 2); under status rule 1 such a
passage has no status and is exported. The hand-written cases are
`tests/data/locate/status-parity-2.json` (`cf-locate-status-parity/2`, every case with
`status_rule: 2` and, where status rule 1 reads the claim's three D1 keys, what it derives
there), run beside `tests/data/locate/status-parity.json`, which stays the test of status rule
1 and of every `cf-locate-recordset/1` set.

**The statement cut** (`docs/proposals/statement-cut.md`, the maintainer's answers of
2026-10-21): the inventory cuts an article into statements, one passage per statement; a
compound statement's conjuncts are the inventory's, cited by locate and the challenge; a
coverage claim is a **bundle**, allowed only on a compound statement, whose relation is its
weakest part's; and a re-cut is a routine, versioned operation with a carry-over map. Its names
are defined in `cflib.vocab`'s statement-cut block and `cflib.hashing`, and **from the
proposal's flip** (its §7) `cf plan-locate` writes its versions into every run it plans —
`judgment_rules` 3, `span_end_rule` 2 and `statement_cut_rule` 1 in gate.json, and a locate
run's `locate.status_rule` 2 in `run.yaml` — and refuses a run of the earlier flow (a locate run
over an inventory without the cut, a challenge of a locate run of earlier rules). Each rule
binds only a run whose gate.json or `run.yaml` names its version, so every run planned before
the flip is gated, tallied and exported as before. The proposal's §1–§5 are normative for the
packages that build it; each package extends this paragraph in its own section (§6 of the
proposal says which).

| name | values |
|---|---|
| versions | `RULES_STATEMENT_CUT` (3: `judgment_rules` of the cut), `SPAN_END_STATEMENT_CUT` (2: `span_end_rule` of the cut), `STATEMENT_CUT_RULE` (1: `statement_cut_rule`, gate rule 10), `STATUS_RULE` (`status_rule` in a locate run's `run.yaml` and in its record set's seal: 1 is `STATUS_RULE_D1`, today's derivation and the default; 2 is `STATUS_RULE_BUNDLE`) |
| `COMPOUND_SHAPES` | `existence_uniqueness` · `iff` · `cases` · `conclusions` · `equations` (the closed list; `COMPOUND_SHAPE_ORDER` is the maintainer's order). A compound statement has `MIN_CONJUNCTS`–`MAX_CONJUNCTS` (2–5) conjuncts `c1` … `cN`, `existence_uniqueness` exactly 2 (`SHAPE_CONJUNCT_COUNTS`); a simple statement has none, and its one clause id is `SIMPLE_STATEMENT_CLAUSE` (`c1`) |
| an inventory passage's `conjuncts` and `shape` | written by the inventory on a compound `formal_math` passage only, both or neither: `conjuncts` a list of `{id, text}` (ids `c1` … `cN` in order, each text one line of at most `MAX_CLAIM_CHARS` characters in the worker's own words, never more than `EXPORT_QUOTE_WORDS` consecutive words of the source, alone or read one after the other, as `verify_locate.check_claims` holds a claim list), `shape` one of `COMPOUND_SHAPES`; a simple statement has neither key, and its `statement` (required, as before) carries the hypotheses and objects the tight span leaves to its neighbours (`references/locate-documents.md` §2). `passage_ids.py` writes both back as it found them |
| `CUT_CONNECTIVES` | `;` · `since` · `because` · `so` · `hence` · `then` · `moreover` · `, and` (with a new subject after it): where the inventory cuts inside a sentence, never between a hypothesis and its conclusion, inside an iff, or between conjuncts about one subject. A prompt rule (the cut rules below); no gate parses a sentence. The end rule's version 2 restates the list (`locate_text.CUT_CONNECTIVES`) to leave the one before the next passage out of both passages |
| the end, `span_end_rule` 2 | an inventory passage with no `until` ends at the earlier of its **extent** and the next passage's start, never before its quote's end (`locate_text.passage_end(…, rule=2, next_start=…, formulas=…)`; version 1 ends it at its paragraph's end). When the next start is the earlier, the **separator** before it — whitespace, one connective of `CUT_CONNECTIVES` and a comma or semicolon, outside every formula (`locate_text.Blocks.separator_start`) — is in neither passage, so a statement's span, and its `passage_key`, is the one an `until` at its last word gives, whether or not its neighbour is a passage. The extent (`locate_text.Blocks.extent`, the proposal's §3.1, over `paragraphs.json` and `formulas.json`) is the end of the start's outermost block when that is a theorem box (or shares its span with one), else of that block followed by the **display blocks** its last sentence introduces (a block whose last character — a trailing formula read as its alttext's last character after TeX spacing, a `\right.` null delimiter, a `\\` line break, an `\end{…}` closer and closing braces, and closing brackets and quotes after a full stop read through to it — is no `.`, `!` or `?`, a formula's `!` being a factorial and no stop (`locate_text.FORMULA_STOPS`); a display block holds a formula and no letter outside its formulas, and a list item `li` is never one a sentence introduces) — with, for a compound statement, the rest of its run of `li` or `dd` items first. So two passages of one paragraph never nest, and a passage leaves its block only inside a theorem box, into the displays its sentence introduces, or over the items of one list (gate rule 10's bound). The outermost block is the bound: inside a `blockquote`, a `dd` or an `li` that holds other blocks, a passage no later start ends runs to the container's end unless its `until` ends it sooner (the inventory prompt says to give one there), a compound statement that starts in a nested item runs over its parent's run of items, and two lists with nothing between them are one run (`paragraphs.json` does not say where a list ends) |
| a passage's units | `locate_text.units`: its sentences (a `.`, `!` or `?` — or a formula whose alttext ends with a `.` or `?`, a formula's `!` being a factorial — before whitespace or a block's start and an upper-case letter or a formula, not after one of `ABBREVIATIONS`, `locate_records._ABBREVIATIONS`' list) and its displays (the `math` blocks inside it); the harvest receipt counts both, and refuses nothing on them |
| a part's `conjunct_relation` | `CONJUNCT_RELATIONS`: `exactMatch` · `closeMatch` · `broadMatch` · `narrowMatch`, judged against the conjunct; a bundle's relation is one of `BUNDLE_RELATIONS` (the strong ones), its weakest part's |
| a strong location's `tests` | `TWO_WAY_TEST_KEYS`: `r2` (how the passage follows from the declaration) · `r3` (why the declaration needs nothing more at the passage's objects) |
| `CUT_FLAGS` | `not_a_statement` · `several_statements`: why a locate worker ends a passage unresolved because of its cut; `not_a_statement` is also the consumer's ballot flag, which no stage here writes |
| `CARRY_FATES` | what a re-cut does to a passage of the old cut, by the carry-over map (`cf locate carry`, `references/locate-documents.md` §12), from spans and span text only: `unchanged` (a new passage has its passage key: the same text on the same page) · `changed` (not unchanged, and the new layer does not hold its text exactly once, so it is never placed) · placed on the new layer and overlapped by no new passage, `removed` · by two or more, `split` · by one starting where it starts, `end_moved` · by one starting before it and holding the whole of it, `merged` · by one otherwise, `reshaped`; and `new` (`CARRY_NEW`) for a new passage no old one went into. Only `unchanged` carries: a pair verdict, a label or a known answer on a passage whose new passage is `formal_math`, with the same conjuncts when it read them (`locate.carry.carries`), and a gold row whose passages, every one over its span, are unchanged and still the passages there, while the labeler version is the same (`GOLD_CARRY_STATES`); everything else is asked again, and a status is derived again, never carried |
| `GOLD_CARRY_STATES` | what a re-cut does to a frozen gold row (`cf bench carry`, `references/locate-bench.md` §7), the old cut on the layer the gold is labelled on: `carried` (the new cut is on that layer too, and the passages over the row's span are its old ones, unchanged, at the same spans: its labels stand) · `reanchor` (the same on another layer, a new revision or rendering whose text over the span is unchanged: its labels stand and its span moves to the report's `anchor`) · `relabel` (anything else, and every row across a labeler bump); a row without a span is counted apart |

The keys (`cflib.hashing`, each one definition): `span_sha256` is the sha256 of the passage's
layer text; `passage_key` is sha256 of `<pageid>\n<span_sha256>`; `pair_key` of
`<passage_key>\n<repository>\n<declaration>`, the repository the lean end's `uri`; `bundle_key`
of the passage key and its parts' pair keys, sorted, one per line; `cut_id` of the canonical JSON
of a page's pageid, layer sha and sorted spans. None holds a revision, a commit, a run or an
item, so a key changes only when what it names does. The carry-over map keys every passage of
both cuts by `passage_key` and names each cut by its `cut_id`; two passages of one cut with one
key refuse their page there. A `pair_key` is unchanged exactly when its passage key and its
declaration are, and a `bundle_key` exactly when its passage key and every part's pair key are;
neither says anything of the conjuncts, so an unchanged key carries a verdict on its own only when
the verdict read no conjunct, and one that read them (a bundle's, a bundle part's) also needs the
map's `conjuncts_changed` false.

**How the inventory cuts** (the proposal's §3.4; `templates/prompts/locate-inventory.md.tmpl`
and the inventory mode of the bench's labeler, `workflows/locate-label.workflow.js`, carry these
lines word for word, and `tests/test_locate_statement_cut.py` holds the three together). They are
prompt rules: what the gate can check of them is gate rule 10 (the proposal's §3.3), and the rest
— what one statement is, where a sentence is cut, lists, chains, proofs, the context — only the
receipt counts.

- **The test.** A passage is one statement when one Lean declaration could state it whole, with nothing left that a reader would call a second result, even when a library states it in two lemmas. Otherwise cut it: a definition and a theorem about it, a fact and the reason the text gives for it, or two cases in two sentences are two passages, not one.
- **Units.** A passage is one or more whole units: a sentence, or one of its independent clauses, with the displays that sentence introduces; or a list item. It never runs from one paragraph into the next, except inside a theorem box, into the displays its last sentence introduces, or, for a compound statement, over the items of one list.
- **Displays.** A displayed formula is never cut and belongs to the sentence that introduces it. An equation chain (a = b = c) is one statement, its end-to-end equation; its middle terms are steps, not conjuncts. A display of several separate equations is one compound statement, shape `equations`.
- **Inside a sentence.** Cut where an independent clause asserts a fact of its own, at one of ";", "since", "because", "so", "hence", "then", "moreover", ", and" (", and" only when a new subject follows). Never cut between a hypothesis and its conclusion, inside an iff, or between conjuncts about one subject. Each piece starts at a word, and the connective starts no piece.
- **Lists.** A list whose items each state a fact is one passage per item. A list of the cases of one definition or law, or of the conditions of one equivalence, is one compound statement (shape `cases` or `iff`) that starts at its first item. Its lead-in sentence ("the following are equivalent:") is expository, unless it states a reason no neighbour states, which is a statement of its own.
- **Proofs.** Proof narration and commentary is expository (PD5); each definition, theorem, equation or declarative statement inside a proof is a passage of its own, and the proof as a whole is none.
- **Context.** A sentence that only sets up objects or hypotheses ("Let W be a finite widget.") states nothing of its own: it opens the first statement that uses it, since a statement is never cut from its hypothesis. Context from further away stays out of the span: `statement` carries the hypotheses and objects ("for a finite widget W, …"), so it reads on its own and the span stays tight.
- **Conjuncts.** A compound statement is one of a closed list of shapes: `existence_uniqueness` (existence and uniqueness), `iff` (an equivalence, or the conditions of one), `cases` (the cases of one law or one definition, in one sentence, one display or one list), `conclusions` (several conclusions about one subject under one hypothesis) and `equations` (one display holding several equations). Write its `conjuncts`, 2 to 5 of them, and its `shape`; `existence_uniqueness` has exactly two. A simple statement has neither. Anything else that holds two results is two statements: cut it.
- **The subject.** A lead that describes the subject rather than states it is expository; the subject marker, `prominence: subject`, goes on the passage that states the theorem or definition, which may be its theorem box. A passage not scoped `formal_math` is always `supporting`.

A stated reason is cut by PD9 in the words the proposal's §3.4 gives it, which the inventory
prompt and the labeler's inventory mode carry from this package on and the judgment rules' own
PD9 line takes at the proposal's flip: "a stated reason ("it holds because X") is a passage of
its own only when X is a declarative mathematical statement that no neighbouring passage already
states; otherwise X is no passage and no clause."

**Challenge verdicts** (`CHALLENGE_VERDICTS`, the proposal's §5): every record leaves a workspace with one.

| value | meaning |
|---|---|
| `survived` | challenged and confirmed, with a quoted witness |
| `downgraded` | challenged, and the relation or the outcome was weakened |
| `not_required` | outside every challenge rule |
| `sampled_out` | inside the stratified sample's frame, and not drawn |
| `challenge_pending` | a rule now requires it and no challenge judged it: the passage's prominence was overturned to `subject` or `central` after its records were planned under `supporting`, so its unjudged locations (a part of a coverage claim confirmed in the same run among them), absence or unresolved outcome are refuted-by-default or hunted records nobody refuted or hunted. Exported as it is, with no QID on its subject end; a consumer reads it as unchallenged. Under status rule 2 also the challenge of a passage **owed a hunt** (`owed: "hunt"`): a located passage the challenge left with no location that counts and no absence, which the export refuses |

`CHALLENGE_UNJUDGED` is `not_required` · `sampled_out` · `challenge_pending`: the values of a
record no verdict judged.

Two challenge rules of the statement cut (the smoke-3 audit's three status anomalies,
`docs/proposals/statement-cut.md` §5, fix 6), each binding only what names it. **Conditional
records** (anomaly 3): a challenge plan made under judgment rules 3 puts every location,
absence and unresolved record of a `supporting` passage whose prominence record it requires,
and which it does not require itself, in that passage's task, marked `conditional:
"prominence"` with its planned value unchanged (`challenge_plan.mark_conditional`); the
challenger judges them only when it overturns the prominence to `subject` or `central`, and
then judges every one, each as a refuted-by-default or hunted record, and the outcomes apply such
a verdict only beside that overturn in the same challenge run (a plan made before has no such
record, so nothing changes for it). **Fills** (anomalies 1 and 2): a challenge document of
judgment rules 3 whose verdicts leave a `located` passage with no location that counts — its
judged results and the unjudged `passage_locations` together — gives `fills[<item>]`,
`{"outcome": "absent", "receipt"}` held to an absence's checks or `{"outcome": "located",
"locations"}` held to an overturn's, and the outcomes of a run of status rule 2 apply it: the
passage becomes absent beside its `relatedMatch` locations, the absence the challenger's own
(`survived`), or takes the challenger's locations first (`survived`), as an overturn's.

**The values the locate documents carry** (`references/locate-documents.md`, every one in
`cflib.vocab`'s locate block):

| enum | values |
|---|---|
| `LOCATE_DOCUMENT_SCHEMA` | `cf-locate/1`: the `schema` of every inventory, locate and challenge document |
| `RENDERER_WEB_HTML` | `web-html/3`: the renderer of the first corpus's web sources, pinned by every run, gate and score document (`web-html/1` kept style and script text in the layer and collapsed ASCII whitespace only; `web-html/2` took a named concept's bold for a paragraph label and recorded no view shas in `render.json`; no layer of either is read any more) |
| `PAGE_FLAGS` | `redirect` · `disambiguation` · `list` · `biography` — what a sourceset entry's `flags` say of its page, each `true`, `false` or `null` (not determined): the first three set at enumeration from the page info and the assessment, `biography` (Wikidata P31 = Q5) by `cf sourceset flag-biographies`; a page with one of them true is no concept article |
| `LOCATE_SKIP_REASONS` | `not rendered` (no layer yet: a render settles it) · `parity refused` (its render stands refused because its layer is not the producer's: no render of the sourceset settles it, only the producer's corrected delivery adopted into a new sourceset) · `no concept article` (followed by the page's true `PAGE_FLAGS` in parentheses, "no concept article (redirect)") — why an inventory plan left a source out (`run.yaml` `locate.skipped`); a bench draw's excluded article gives the first two as its reason, with the sourceset version |
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
| `CHALLENGE_RULES` | `refute_by_default` (a location on a `subject` or `central` passage, every coverage claim, a strong location whose own note admits a clause it does not state, `admits_missing_clause`, and every `subject` passage's prominence, stratum `prominence:subject`, since its records carry the article's QID) · `hunt` (an absence or unresolved outcome on one) · `basis` (`CHALLENGE_RULE_BASIS`: a location that sets a `supporting` passage's derived status on its own — every such one, not only the best-ranked — when its relation is one of `STRONG_RELATIONS`; required) · `sample` (the stratified sample's frame: scope demotions and `supporting` demotions and weak relations, at `sample_rate`; a `supporting` passage's other strong locations, one stratum per relation, at `strong_sample_rate`; a `supporting` passage's absence, stratum `outcome:absent`, at `absence_sample_rate`, hunted when drawn) · `none` (a `supporting` passage's unresolved outcome) — why the challenge plan put a record where it did |
| `CHALLENGE_PLANNED` | `required` (`CHALLENGE_REQUIRED`: the record is in a challenge task and its verdict is owed) · `sampled_out` · `not_required` — what the challenge plan says a record is owed, before any verdict |
| `JUDGMENT_RULES` | `3` (`RULES_STATEMENT_CUT`, from the statement cut's flip; `2` before it): the version of the judgment rules (gate rule 9, R1–R7 and the policy defaults above) a run's `gate.json` names as `judgment_rules`; `cf plan-locate` writes it into every run it plans, and the gate holds a run to the version it names: `1` R1–R7, `2` (`RULES_PD5_PD9`) PD5–PD9 too (PD6's whole part and PD7's junk value refused), `3` the statement cut's (the inventory's conjunct ids for clause ids, a coverage claim only as a bundle, the smoke-3 audit's failure-mode rules; the statement-cut paragraph below). A `gate.json` without it (a run planned before the rules) is gated without rule 9 |
| `SPAN_END_RULE` | `2` (`SPAN_END_STATEMENT_CUT`, from the statement cut's flip; `1` before it): the version of the span-end rule (gate rule 2's end check: an inventory passage ends where `passage_ids.py` ends it, `locate_text.passage_end`; `1` its `until`, else its paragraph, `2` its `until`, else the earlier of its extent and the next passage's start) a run's `gate.json` names as `span_end_rule`; `cf plan-locate` writes it into every run it plans, and a `gate.json` without it (a run planned before the rule, whose `passage_ids.py` dropped `until`) is gated with the end held inside the layer and after the quote only. A locate or challenge document carries no span of its own, so there the key binds nothing (references/locate-documents.md §2 and §9) |
| `CLAIM_ID` | `c1`, `c2`, … (`c[1-9][0-9]?`): the id of a passage's claim, one formal clause of its claim list (`claims` of a locate or challenge document, references/locate-documents.md §3–§4), which a location's `clauses` name |
| `CLAUSE_GAP_PHRASES` | `does not state` · `do not state` · `not stated` · `not part of` · `not a part of` · `rather than asserting` · `rather than stating` · `rather than proving` · `only the existence` · `only existence` · `only proves` · `only states` · `proves only` · `states only` · `only one direction` · `one direction only` · `not the converse` · `converse is not` · `without the converse` · `without uniqueness` · `uniqueness is not` · `does not cover` · `do not cover` · `not covered` · `not asserted` · `not included` · `omits` · `omitted` · `leaves out` · `left out` · `does not prove` · `does not assert` · `does not establish` — the closed list by which a strong record's note or instantiation admits a clause its declaration does not state (`admits_missing_clause`: whole words, case-folded, the apostrophe folded and a contraction spelled out, so "doesn't state" is "does not state"; a hit is set aside when negated — "not only the existence" —, when the passage is its subject — "the passage does not cover n = 0", "not part of the passage" — or in a relative clause on what an "unlike …" clause names); the gate refuses such a record under the judgment rules, and the challenge plan refutes it by default. A tripwire, not a parser: a note it misreads is reworded to say what the declaration does |
| `GENERAL_QUERY_TAG` | `general`: the tag, in a receipt's `query_clauses`, of a query for the general theorem a worked example is an instance of; an `example` passage's absence carries one of its own under the judgment rules (each claim, and `general`, matched to a distinct query) |
| `CLOSE_MATCH_CONVENTIONS` | `notation` · `encoding` · `normalisation` · `renaming` · `unfolding` · `bundling` (`CONVENTION_*`) — the conventions a `closeMatch` differs from its passage by (R1), named as its `convention` under the judgment rules. `LEGACY_CLOSE_MATCH_CONVENTIONS` is the list under judgment rules 1, with `junk_value` (`CONVENTION_JUNK_VALUE`), which PD7 took off it: a run planned under rules 1 is gated by it, and its records keep the value. `encoding` covers one notion under two definitions that a library lemma links (PD10) |
| `CLOSE_MATCH_GENERALITY_PHRASES` | `stronger` · `strengthen*` · `weaker` · `weaken*` · `more general*` · `generali*` · `special case` · `particular case` · `broader` · `narrower` · `strict derivative*` · `strict differentiab*` · `strictly differentiab*` — words an `exactMatch` or `closeMatch` note may not say (`says_generality`: whole words, case-folded, a trailing `*` any ending of the word, so "more generally" and "generalisation" are hits); each describes a `broadMatch`, a `narrowMatch` or a weakening. "strict" and "strictly" alone are not on it: a strict inequality or a strictly increasing list is no strengthening. The prompts render the list from this tuple |
| `JUDGMENT_LOCATION_FIELDS` | `clauses` · `instantiation` · `step` · `convention` · `conjunct_relation` · `tests` — the fields the judgment rules add to a location, which the outcomes keep and a challenge manifest shows; none is exported. A `convention` belongs to a `closeMatch` and a `step` to a `STEP_RELATIONS` location only; the last two are judgment rules 3's, which the outcomes keep only on a run of status rule 2: a `conjunct_relation` a `hasPart`'s of a compound statement and `tests` a strong location's (and a `hasPart`'s whose `conjunct_relation` is strong), and a verdict that gives them replaces the location's (a `conjunct_relation` never with a stronger one), the outcomes dropping each from a location whose relation no longer takes it |
| `STEP_RELATIONS` | `hasPart` · `narrowMatch` — the relations whose location says, in its `step`, what the declaration alone entails of the passage (on a proof the displayed step, on any other passage the clause) |
| `CANDIDATE_PART_PHRASES` | `only part` · `part of the passage` · `one clause` · `one of the clauses` · `not the whole` · `not the full` · `special case` · `not the worked example` · `not the example` · `partially` · `more general*` · `generali*` — what an absence's rejected candidate may not be rejected as (`rejects_a_part`, matched as `CLOSE_MATCH_GENERALITY_PHRASES` are): a declaration that states a clause, a case or a generalisation of the passage is a location, and the absence is refused under the judgment rules |
| `INDEX_FLAGS` | `is_prop_unknown` (the probe's `isProp` failed, so the status is the conservative `statement_only`) · `published_status` (a published index's own status word, accepted with `cf corpus ingest --accept-published`) — a normalised declaration's `flags` |

Bounds, also in `cflib.vocab`: `MIN_ABSENCE_QUERIES` (3 distinct queries in an absence's receipt),
`MAX_PASSAGE_CLAIMS` (12 claims in a passage's claim list), `MAX_CLAIM_CHARS` (300 characters,
one line, of a claim's text) and `MAX_JUDGMENT_LINE_CHARS` (300 characters, one line, of a
location's `instantiation` or `step`, with at least `MIN_JUDGMENT_LINE_WORDS`, 3, words that
carry a letter),
`MIN_COVERAGE_PARTS` (2 distinct `hasPart` parts in a coverage claim), `COVERAGE_RECORD_KEYS`
(`complete` · `parts` · `challenge`, the keys of a record's claim and of `derive_status`'s),
`MAX_PASSAGES_PER_LOCATE_TASK` (8), `CHALLENGE_BATCH_RECORDS` (about 20 records per challenge
task), the challenge plan's default sampling rates `CHALLENGE_SAMPLE_RATE` (0.2: scope and
`supporting` demotions, weak relations), `CHALLENGE_STRONG_SAMPLE_RATE` (0.5: a `supporting`
passage's strong locations beside its basis) and `CHALLENGE_ABSENCE_SAMPLE_RATE` (0.5 from the
statement cut's flip, the smoke-3 audit's fix 5c, 0.25 before: a `supporting` passage's
absence; `cf plan-locate --sample-rate`, `--strong-sample-rate`, `--absence-sample-rate`, each
recorded in `run.yaml`, so a plan made before the flip records the 0.25 it drew at), the export's quote caps `EXPORT_QUOTE_WORDS` (12 words of `exact`) and
`EXPORT_CONTEXT_CHARS` (32 characters of each of `prefix` and `suffix`), the record set's
token grammars (`references/locate-documents.md` §6, the consumer's own): `RECORDSET_TOKEN`
(letters, digits and `._:-`, at most 128, a letter or digit first: a seal's `set_id`, its
`sourceset.id` and a campaign id, which never starts `RUN_TOKEN_PREFIX`, `run:`) and
`RUN_TOKEN_ID` (letters, digits and `._-`, at most 64: a run id a run token
`run:<locate run>[+<challenge run>]` can spell, which `cf plan-locate` checks), and the run
machinery's: `MAX_TASKS_PER_ROOT` (500 tasks in one tend root; a larger plan is campaign
shards), `LOCATE_ATTEMPT_CAP` (3: the genuine reviewer rejection that reaches it writes the
`UNRESOLVED` document), `SESSION_SLOTS` (15 `flock` slots, one below the account's 16 sessions)
and `AGENT_TIMEOUT` (`45m` per agent call), and `GATE_CALLERS`, what a locate gate log line's
`caller` says the call was (`references/locate-documents.md` §9): `validation` (tend's
`--changed-files` call, the only one the log's readers count as an attempt) · `pre_merge`
(`--changed-files --pre-merge`) · `findings_check` (an agent's or the bridge's own
`--findings` check) · `directory` (a directory of documents: a harvest, a hand check). The
host-side scripts that cannot import `cflib` keep copies of these, which tests pin.

**The benchmark's values** (the proposal's §8 and §9):

| enum | values |
|---|---|
| `BENCH_TIERS` | `G1` · `G3` · `GX` · `G4` · `P` — never pooled; `BENCH_TIER_ORDER` is the same tiers in the order a score and a comparison report them, `BENCH_LABELLED_TIERS` the four whose gold is labelled (`G1` · `G3` · `GX` · `G4`; P's gold is the planted ablation) and `BENCH_CLAIM_TIERS` the ones a pre-registered claim may come from (`G1` blind-arm only · `G3` · `P`) |
| `TOOLS_DB_KINDS` | `full` (`corpus.sqlite`) · `blind` (the G1-blind `corpus.blind.sqlite`) · `planted` (a module-ablated database, the P tier) — the database a locate run's workers search: a candidates file's `corpus_db.kind`, `gate.json`'s and `run.yaml`'s `tools_corpus_db.kind`, a score's pin (`references/locate-documents.md` §9) |
| `BENCH_ARMS` | `assisted` (production) · `blind` (L2's curated joins, gate rule 4 and the tag table off for the scored items); never pooled |
| `BENCH_SPLITS` | `dev` · `eval` · `test` |
| `BENCH_AA_VERDICTS` | `differs` (the whole paired interval of replicate − baseline lies past the tolerance: the runs disagree) · `within` (the whole interval lies inside it: agreement shown) · `unresolved` (it reaches past the tolerance without lying wholly beyond it: no difference shown, and no agreement either) — what the A/A's agreement says of one metric, or of the cost per article (`references/locate-bench.md` §9, rule 5); only `differs` fails it |
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
