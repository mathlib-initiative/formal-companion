# Statement cut: one passage per statement, bundles, and a versioned cut

**Status: specification, 2026-10-21, built but for the flip. Built: the shared names of §6.1
(constants, keys, prompt slots and the tests that pin them) and the four packages of §6.2 (cut,
gate, fixes and carry), merged, the carry package's `scripts/bench/gold.py` share after the
fixes package, but for what §9 lists as not built. The flip (§7) is not made, so no planner writes the new versions
and every earlier run is gated, tallied and exported as before; an inventory planned now would
render the cut rules its gate does not hold it to, so none is planned before the flip. Commands
and paths written in *italics* are not implemented.**

This document turns the maintainer's answers of 2026-10-21 on statement-level extraction into
rules precise enough that four builders working in parallel agree on every name. It extends
`docs/proposals/wikipedia-locate.md` (the locate pipeline) and changes the formats of
`references/locate-documents.md`; `docs/design/stage_map.md` §4.0 says which rules are versioned,
and changes when the flip (§7) lands.

Its sources are the maintainer's notes, which this tree does not hold: the statement-level
extraction design and its §10 answers, the decision log's answers of 2026-10-20 (PD5–PD10, the
council test run) and 2026-10-21 (statement-level extraction), and the smoke-3 audit's §5
(failure modes) with its "three status anomalies". Every example below is synthetic: no
article's text, no real page id, no real passage id, no QID.

**Contents**: 0 terms · 1 the data model · 2 the bundle and the version keys · 3 the cut rules ·
4 keys and the carry-over map · 5 the failure-mode fixes · 6 the work split · 7 the flip, and what
stays as it was · 8 what WikiLean must do · 9 readings taken, and what is not built

## 0. Terms

| term | meaning |
|---|---|
| statement | a definition, theorem, equation or declarative statement about mathematical objects (PD5) that one Lean declaration could state whole, with nothing left a reader would call a second result |
| compound statement | one statement whose conclusion is a conjunction the text does not separate, of one of the closed list of shapes (`COMPOUND_SHAPES`, §1.1) |
| conjunct | one part of a compound statement, written by the inventory as `{id, text}`, ids `c1` … `cN` |
| simple statement | a statement with no conjuncts; its one clause id is `c1` (`SIMPLE_STATEMENT_CLAUSE`) |
| clause ids | what a location's `clauses` and a receipt's `query_clauses` name: a compound statement's conjunct ids, or `["c1"]` for a simple one. Under judgment rules 3 no locate or challenge document writes its own claim list |
| bundle | a coverage claim on a compound statement: its `hasPart` parts together state every conjunct, each conjunct stated by exactly one part, no part stating them all. Shown and voted on as one card |
| cut | how an inventory divides one page into passages. A re-cut is a new cut of the same page, at the same revision or a new one |
| unit | a sentence, an independent clause of one (§3.4), a list item, or a display |
| the flip | the one integration commit (§7) after which the planner writes the new versions |
| rules 3 | `judgment_rules` 3 (`RULES_STATEMENT_CUT`) |

The audit's labels are kept: FM1–FM9 for its failure modes, and A1–A3 here for its three status
anomalies (a downgrade that leaves no counting location and no absence, twice; a prominence
overturn that reopens records no task held).

## 1. The data model

### 1.1 The inventory passage (`statement_cut_rule` 1)

Two fields join an inventory passage (`references/locate-documents.md` §2); nothing else in the
row changes, and there is **no new scope and no new passage kind**:

```json
{"item": "In-text #0000c0de", "head_words": 12, "span": [120, 241],
 "quote": {"exact": "Every finite widget has a spanning gadget", "prefix": "", "suffix": ""},
 "section": ["Gadgets"], "fragment": "Gadgets",
 "passage_kind": "theorem", "prominence": "central", "scope": "formal_math",
 "statement": "For a finite widget W: W has a spanning gadget, and any two spanning gadgets of W are equal.",
 "conjuncts": [{"id": "c1", "text": "a finite widget has a spanning gadget"},
               {"id": "c2", "text": "two spanning gadgets of one finite widget are equal"}],
 "shape": "existence_uniqueness",
 "concept": null, "finder": "model"}
```

- `conjuncts` — absent on a simple statement (never `[]` and never `null`); on a compound one a
  list of `MIN_CONJUNCTS`–`MAX_CONJUNCTS` (2–5) objects with exactly the keys `id` and `text`,
  the ids `c1`, `c2`, … in order with no gap. Each `text` is held to the claim rules a locate
  document's claims are held to today (`verify_locate.check_claims`): one line, at most
  `MAX_CLAIM_CHARS` (300) characters, in the worker's own words — no more than
  `EXPORT_QUOTE_WORDS` (12) consecutive words of the source in any spelling, alone or read one
  after the other — since a bundle's conjuncts are exported (§2.4).
- `shape` — present exactly when `conjuncts` is, one of `COMPOUND_SHAPES` (`existence_uniqueness`,
  `iff`, `cases`, `conclusions`, `equations`); `existence_uniqueness` has exactly two conjuncts
  (`SHAPE_CONJUNCT_COUNTS`).
- Only a `formal_math` passage carries `conjuncts`.
- `statement` stays required and becomes the passage's context: it reads on its own, naming the
  hypotheses and objects the tight span leaves to its neighbours ("for a finite widget W, …").
  The gate cannot check that; the prompt says it (§3.4, rule 7).
- **A stated reason** (PD9 reworded, the maintainer's answer to question 5) is a passage of its own
  when it is a declarative statement that no neighbouring passage states, and no passage
  otherwise. No field links it to the statement it supports: the link is prose, in both
  passages' `statement`, and nothing downstream reads it.
- **A descriptive subject lead** (question 6) is cut like any other text: what describes rather
  than states is `expository`, and the subject marker — `prominence: subject`, and with it the
  article's QID on the passage's records (`locate.records.article_qid`, unchanged) — goes on the
  passage that states the theorem, which may be the theorem box. Rule 10 (§3.3) refuses
  `subject` on a passage not scoped `formal_math`, and holds every passage not scoped
  `formal_math` to `prominence: supporting`, the one value nothing reads there.

### 1.2 What the task manifests carry

The planner (`scripts/locate/plan.py`) copies from the frozen inventory into each locate task
manifest's passage (`_passage_row`) its `statement`, and its `conjuncts` and `shape` when it has
them. A challenge task's record (`challenge_tasks`' `keep`) carries the same three from the
outcome row, every location field of `JUDGMENT_LOCATION_FIELDS` (which now ends with
`conjunct_relation` and `tests`), and `conditional` (§5, A3). A coverage record's `parts` carry
each part's `clauses` and `conjunct_relation` beside the keys they carry today.

### 1.3 The locate document under rules 3

Rule 9 changes in four ways; everything rule 9 checks today keeps being checked, against the
manifest's clause ids instead of a written claim list.

```json
{"item": "In-text #0000c0de", "outcome": "located",
 "locations": [
   {"library": "alpha", "declaration": "Widget.exists_spanning_gadget", "relation": "hasPart",
    "rank": 1, "confidence": "high", "clauses": ["c1"], "conjunct_relation": "broadMatch",
    "step": "a spanning gadget for every widget, finite or not",
    "instantiation": "W as a widget; no instance argument",
    "tests": {"r2": "<how c1 follows>", "r3": "<why it needs nothing more at W>"}, "note": "",
    "witness": "<why it states c1>"},
   {"library": "alpha", "declaration": "Widget.spanning_gadget_unique", "relation": "hasPart",
    "rank": 2, "confidence": "high", "clauses": ["c2"], "conjunct_relation": "exactMatch",
    "step": "two spanning gadgets of a finite widget coincide",
    "instantiation": "W as a finite widget; no instance argument",
    "tests": {"r2": "<how c2 follows>", "r3": "<why it needs nothing more at W>"}, "note": "",
    "witness": "<why it states c2>"}],
 "coverage": {"complete": true, "parts": [1, 2],
              "note": "existence holds for every widget and uniqueness for finite ones"},
 "receipt": {"queries": ["…"], "candidates": [], "query_clauses": {"…": ["c1"]}}}
```

- **J1, no claim list.** `claims` is refused on every passage (the inventory owns the list), and
  the clause ids are the manifest passage's conjunct ids, or `["c1"]`. `clauses` and
  `query_clauses` name them exactly as they name claims today (`check_clauses`,
  `check_query_clauses`): a strong relation names every id, a `relatedMatch` none.
- **J2, a part's relation.** A `hasPart` location of a compound statement names in `clauses` the
  conjuncts it states and gives `conjunct_relation`, one of `CONJUNCT_RELATIONS` (`exactMatch`,
  `closeMatch`, `broadMatch`, `narrowMatch`), its relation to the conjunction of those conjuncts
  by R1–R3 and PD1–PD10; a `conjunct_relation` of `closeMatch` names its `convention` (from the
  rules-2 list, so no `junk_value`), and `step` stays required. A `hasPart` of a simple statement
  names `["c1"]` and carries no `conjunct_relation`. Nothing else carries one.
- **J3, a bundle.** `coverage` keeps today's object and checks (rule 1), and is refused on a
  simple statement. Its parts' `clauses` are pairwise disjoint, together name every conjunct, and
  none names them all (PD6, so the rules-2 whole-part check is implied); every part's
  `conjunct_relation` is one of `BUNDLE_RELATIONS` (a part that states only a special case of its
  conjuncts leaves them unstated, and the statement is partial). The bundle's relation is computed
  (§2.1), never written.
- **J4, the failure-mode rules** of §5: fixes 2a–2c (`tests` and `instantiation` on every strong
  location, the ℕ-for-ℤ tripwire, the longer generality list), each holding a `hasPart` part by
  its `conjunct_relation` (a part whose `conjunct_relation` is strong is a strong location: the
  bundle takes the weakest part's relation, §2.1), fix 4a's under-claim check, fix
  5b's `special_case` on an absence's receipt, and an optional `cut_flag` on an unresolved passage,
  `not_a_statement` or `several_statements` (`CUT_FLAGS`): what a worker who cannot re-cut a
  passage says of its cut, counted in the tally for the inventory audit.

### 1.4 The challenge document under rules 3

```json
{"…header…": "…",
 "verdicts": [
   {"item": "In-text #0000c0de", "target": "coverage", "action": "confirm",
    "part_relations": {"<part 1's location_key>": "broadMatch",
                       "<part 2's location_key>": "exactMatch"},
    "witness": "…"},
   {"item": "In-text #0000beef", "target": "coverage", "action": "refute",
    "unstated": ["c2"],
    "part_reasons": {"<part 1's location_key>": "states existence only, for any widget",
                     "<part 2's location_key>": "bounds the size of a gadget, not its uniqueness"},
    "witness": "…"},
   {"item": "In-text #0000f00d", "target": "location", "location_key": "<sha>",
    "action": "confirm", "clauses": ["c1"],
    "tests": {"r2": "the passage is the declaration at W, by instantiation",
              "r3": "at a finite widget the declaration needs no extra hypothesis"},
    "witness": "…"},
   {"item": "In-text #0000d00d", "target": "location", "location_key": "<sha>",
    "action": "downgrade", "relation": "relatedMatch", "witness": "…"}],
 "fills": {"In-text #0000d00d": {"outcome": "absent",
                                 "receipt": {"queries": ["…", "…", "…"], "candidates": [],
                                             "query_clauses": {"…": ["c1"]},
                                             "special_case": {"reason": "no library states a gadget bound under any hypothesis"}}}}}
```

- No `claims` (refused); every verdict's `clauses`, `query_clauses` and `unstated` name the
  record's clause ids from the manifest. The FM7 rule's "a claim list at least as long as the
  worker's" is void: there is one list.
- A coverage **confirm** gives `part_relations`, one entry for each part and no other, each one of
  `BUNDLE_RELATIONS` and no stronger (`RELATION_STRENGTH`) than that part's own
  `conjunct_relation`: a challenge never raises a relation. `part_clauses` is refused under
  rules 3 (the parts' conjuncts are the locate document's), so PD6 is held through the same
  document's verdicts on the parts: a confirm beside one whose `clauses` name every conjunct is
  refused (that part is one location, no part).
- A coverage **refute** names in `unstated` the conjunct ids (non-empty, distinct) that no part
  states, and gives `part_reasons`, one line for every part, saying why no R1 convention reaches
  its conjunct (fix 1a).
- A verdict that keeps or leaves a strong relation gives its own `tests` (fix 2d). A verdict on a
  `hasPart` part may give a `conjunct_relation`, never stronger than the record's, and is held by
  the `conjunct_relation` it leaves the part (its own, else the record's): a strong one is a
  strong relation kept, with its `tests`, the ℕ-for-ℤ tripwire and the generality words.
- An uphold of an absence or an unresolved outcome gives `special_case` (fix 5b) beside its
  queries.
- `fills` (A1, A2): see §5, fix 6.

### 1.5 The outcome row

`locate.outcomes` rows (`references/locate-documents.md` §10) of a run of status rule 2 carry the
passage's `statement`, `conjuncts` and `shape` from the frozen inventory; each location keeps the
`conjunct_relation` and `tests` its document wrote (a verdict's own replacing them, and `tests`
kept while the location is held to a strong relation, a part by its `conjunct_relation`; §9);
`coverage` carries `relation` (the bundle's, §2.1); a passage owed a hunt carries `owed: "hunt"`
(§2.5); an unresolved passage keeps its `cut_flag`. A run of status rule 1 writes exactly the rows
it writes today, whatever keys its documents carry.

## 2. The bundle and the version keys

### 2.1 How a coverage claim becomes a bundle, and its relation

Under rules 3 a coverage claim is a bundle or nothing: J3 admits it only on a compound statement,
with every conjunct stated by exactly one part. Its relation is the **weakest part's** (the
maintainer's answer to question 9): the part relation of greatest `RELATION_STRENGTH` among

- each part's `conjunct_relation`, as the locate document wrote it (or a verdict on the part
  weakened it); then, once a challenger confirms the claim,
- each part's `part_relations` entry of that confirm, where it is weaker: a challenge never
  raises a relation, so the weaker of the two stands (§9). The gate refuses an entry stronger
  than the part's own, so on a document it admits the entry is the part's relation.

So a bundle of an `exactMatch` and a `broadMatch` part is a `broadMatch` bundle. The outcomes
compute it and put it on the claim as `relation`; no document writes it.

### 2.2 The version keys: the minimal set

| key | where it is written | what it binds | versions |
|---|---|---|---|
| `judgment_rules` | gate.json, every mode (as today) | rule 9 of locate and challenge documents; the bench holds gold to it | 1 R1–R7; 2 PD5–PD9; **3** the cut: J1–J4, the challenge rules of §1.4, A1–A3's gate rules |
| `span_end_rule` | gate.json, every mode (as today) | rule 2's end check of an inventory passage | 1 the end of `until`, else the paragraph; **2** the end of `until`, else the earlier of the extent and the next passage's start (§3.2) |
| `statement_cut_rule` | gate.json, every mode (new) | rule 10 of an inventory document (§3.3) | **1** |
| `status_rule` | a locate run's `run.yaml` (`locate.status_rule`); its record set's seal (new) | `derive_status`, the outcomes' owed hunt, the record-set format | 1 decision D1 (absent means 1); **2** the bundle's relation, the owed hunt, `cf-locate-recordset/2` |

`cf plan-locate` writes all four from the flip on (`vocab.JUDGMENT_RULES` and
`vocab.SPAN_END_RULE` take the values of `RULES_STATEMENT_CUT` and `SPAN_END_STATEMENT_CUT`;
`STATEMENT_CUT_RULE` and `STATUS_RULE` are written beside them). Each binds a run only when its
own gate.json or run.yaml names it, so a re-gate, a re-tally and a re-export of a run planned
before the flip are what they were.

Why no other key:

- **No cut version.** A cut's identity is its content (`cut_id`, §4.1) and the inventory run
  that made it; the rules it was made under are that run's `span_end_rule` and
  `statement_cut_rule`. The carry-over map (§4) records all three.
- **No key for the challenge plan's conditional records** (A3): `challenge-plan.json` itself
  carries `conditional` on each, and a plan made before has none, so nothing changes for it.
- **No key for the absence sample rate** (fix 5c): `run.yaml` and `challenge-plan.json` already
  record the rates a plan drew at.
- **No key for the record-set format**: the seal's `schema` is the format, and `status_rule` in
  it says how the statuses inside were derived.

The keys depend on each other, and the planner and the gate enforce it:

- A gate.json naming `statement_cut_rule` ≥ 1 with `span_end_rule` < 2 is a broken gate.json
  (exit 3): rule 10's disjointness rests on the new end rule.
- From the flip, `cf plan-locate --preset locate` refuses an inventory run whose gate.json names
  no `statement_cut_rule` (the hint: inventory the sources again), since judgment rules 3 reads
  the inventory's conjuncts, and `--preset locate-challenge` refuses a locate run planned under
  `judgment_rules` < 3, since its documents carry their own claim lists. Neither touches a
  planned run: old runs are re-gated, tallied and exported by their own keys, and a new run of
  the old flow is not offered (§9). Before the flip the planner plans as it does today.
- A locate run's `status_rule` is 2 exactly when its `judgment_rules` is 3: only a run of rules 3
  writes conjunct relations, so only its bundles have a relation to take.

### 2.3 `derive_status` under status rule 2

`vocab.derive_status(passage_kind, locations, absent, coverage=None, *, status_rule=1)`.

- **Status rule 1** is today's function, unchanged in every case of
  `tests/data/locate/status-parity.json`, and still refuses a claim with any key outside
  `COVERAGE_RECORD_KEYS` — a `relation` among them.
- **Status rule 2**: `coverage` is the claim as a record of status rule 2 carries it, keys within
  `COVERAGE_RECORD_KEYS_BUNDLE`, its `relation` required and one of `BUNDLE_RELATIONS`. A survived
  bundle acts as one more location of **that relation** — where status rule 1 makes it one more
  `exactMatch` — with the same fixed stand-in for its proof status (finished when every part is
  `proved` or data, else `statement_only`; `_coverage_location`, unchanged). Everything else is
  status rule 1.

What changes is the definition row's `broadMatch`: a bundle of broader parts on a definition,
construction or notation passage is `partial`, as one broader card is (PD1). On the theorem row
the three strong relations already give one status.

| passage | bundle relation | parts | status rule 1 | status rule 2 |
|---|---|---|---|---|
| theorem | `exactMatch` / `closeMatch` / `broadMatch` | finished | `formalized` | `formalized` |
| theorem | any strong | unfinished | `statement_only` | `statement_only` |
| definition | `exactMatch` / `closeMatch` | finished | `formalized` | `formalized` |
| definition | `broadMatch` | finished | `formalized` | **`partial`** |
| definition | `broadMatch` | unfinished | `statement_only` | **`partial`** |
| example, other | `broadMatch` | finished | `formalized` | `formalized` (the theorem row) |

The cases are written by hand into `tests/data/locate/status-parity-2.json`
(`cf-locate-status-parity/2`, every case with `status_rule: 2`), the table WikiLean ports (§8);
the rule-1 table is not touched.

### 2.4 The record set of status rule 2 (`cf-locate-recordset/2`)

A run of status rule 1 exports `cf-locate-recordset/1` byte for byte as today. A run of status
rule 2 exports `cf-locate-recordset/2`:

- `SEAL.json` has `/1`'s keys and one more, `status_rule` (2).
- Every record's locate extension carries `passage_key`, and a location record's also its
  `pair_key` (§4.1), beside `location_key`.
- A coverage claim rides on every record of its passage as
  `{"complete": true, "parts": [<location_key>, …], "challenge", "relation", "conjuncts":
  [{"id", "text", "part": <location_key>}], "bundle_key"}` — `COVERAGE_RECORD_KEYS_BUNDLE`, the
  conjuncts in id order, each beside the part that states it, so a consumer can show a bundle's
  declarations each beside its conjunct with the record set alone. The conjunct texts are held
  to the export's free-text cap (`locate_records.check_note`).

`/2` takes the next number of the record-set schema; the self-evidencing format the decision log
planned as `/2` becomes `/3` when it is built (§9).

### 2.5 The owed hunt (A1, A2)

Under status rule 2, a `located` passage that the challenge leaves with no counting location and
no absence is **owed a hunt**: the outcomes give it challenge `challenge_pending` and `owed:
"hunt"`, put a problem on it — so `cf export literature-links` refuses it by name — and count it
as `owed_hunt` in the tally. Under status rule 1 it stays what it is today: status none, exported.
Rules 3's `fills` (§5, fix 6) makes the case unreachable for a challenge gated under it; the derivation
is the net under it. No top-up wave plans the hunt yet (§9).

## 3. The cut rules

### 3.1 Definitions, on the layer and `paragraphs.json`

All offsets are layer code points (`references/locate-documents.md` §2). The blocks are
`paragraphs.json`'s entries; `block` is one of `p`, `li`, `dd`, `dt`, `blockquote`,
`theorem_box`, `math` (a display formula).

- **block(x)** — the outermost block holding the offset x (`locate_text.paragraph_end`'s choice;
  the table lists an outer block before the ones it holds). None when no block holds x. Entries
  with one span are one block of every kind they name: a theorem box that holds one paragraph has
  that paragraph's span, and the renderer lists the `p` first (`locate_text.Blocks`).
- **next(B)** — the first block not held by another that starts at or after B's end with only
  whitespace between; none otherwise.
- **display block** — a block holding at least one formula of `formulas.json` whose head text,
  every formula token removed, holds no letter: a display, or a `dd` holding only one.
- **introduces(B)** — B's last non-space character, reading a trailing formula token as the last
  character of its normalised alttext after TeX spacing (`\,` `\;` `\:` `\!` `\quad` `\qquad`
  `~`), a `\right.` null delimiter, a `\\` line break, an `\end{…}` closer and a closing brace
  that is no escaped `\}` (`locate_text.formula_stop`: `\begin{aligned}a&=b.\end{aligned}` and
  `a=b{\text{.}}` end with a full stop), and reading closing brackets and quotes after a full stop
  through to it (`(See the notes.)` ends with one), is none of `.`, `!`, `?` (of `.` and `?` when
  it is a formula's: TeX writes a factorial with `!`, so `x^{n!}` ends with no stop,
  `locate_text.FORMULA_STOPS`): B's last sentence runs on into what follows.
- **chain(B)** — B, then while the last block introduces and its next() is a display block that is
  no `li`, that block: the displays B's last sentence introduces, which belong to it. A list item
  is a unit of its own (§3.4, rule 2), so a list of display items is one passage per item, or a
  compound statement over them (list run); a `dd` is how an article indents a display, and joins.
- **list run(B)** — when B is an `li` or a `dd`: B and the next() blocks of the same `block` after
  it.
- **extent(s, compound)** — with B = block(s): none when B is none; B's end when B is a theorem
  box; else the end of the last block of chain(L), where L is the last block of list run(B) when
  `compound` and B is an `li` or `dd`, and B otherwise.

block(x) is the outermost block, as version 1's paragraph is, so the extent is an upper bound and
not a unit: inside a `blockquote`, a `dd` or an `li` that holds other blocks (a nested list's
parent item), a passage that no later start ends runs to the container's end, and rule 10 (b)
does not refuse an `until` that ends it there; the prompt's units rule is what keeps such a
passage to its paragraph, by its `until` (the prompt's end paragraph says so). A compound
statement that starts in a nested item runs over its parent's list run, and `paragraphs.json`
does not say where one list ends and the next begins (the renderer is frozen), so two lists with
nothing between them are one list run; both only widen a compound statement's bound. A false
stop would narrow a bound, and rule 10 (b) would then refuse a right `until`, which is why a
formula's `!` is read as no stop.

### 3.2 The end rule (`span_end_rule` 2)

`locate_text.passage_end(layer, paragraphs, start, exact, until=None, *, rule=1,
next_start=None, formulas=None)`. Rule 1 is today's function. Rule 2: with an `until`, the end of
its first occurrence at or after the start (as today); without one, the earlier of
extent(start, compound=False) (the quote's end when it is none) and `next_start`, the start of
the document's next passage; never before the quote's end. When `next_start` is the earlier, the
**separator** before it is in neither passage (`Blocks.separator_start`): read back from
`next_start`, and again while anything is set aside, whitespace, one connective of
`CUT_CONNECTIVES` as a whole word (`, and` only after its comma) and a comma or semicolon, never a
character inside a formula. So "…sprockets; every…" ends its first piece after "sprockets", and a
statement ends where an `until` at its last word ends it whether or not its neighbour — a stated
reason PD9 makes a passage, say — is cut as a passage: one span, one `passage_key` (§4.1), across
the re-cuts the carry-over map (§4) is for. `passage_ids.py` places every passage
first, then ends each by the rule its run names — `span_end_rule` from the gate.json beside it (a
staged gate directory always holds one), else its *--span-end-rule* (default 1) — so before the
flip it writes the ends it writes today, and after it no passage needs an `until` to stop where
the next one begins; the gate recomputes the same end from the document. A compound statement
over a list gives `until` to run past its first item.

This alone ends the nesting the smoke-3 audit counted (FM9): a passage no longer runs to its
paragraph's end over a later passage of the same paragraph.

### 3.3 Gate rule 10, the statement cut (`statement_cut_rule` 1)

Over an inventory document, after rules 2 and 8, refusing:

- **(a)** two passages of the source whose spans share a character (overlap, and containment);
- **(b)** a span that ends past extent(start, compound=the passage has `conjuncts`): a passage
  runs from one block into the next only inside a theorem box, into the displays its last
  sentence introduces, or — a compound statement — over the items of one list;
- **(c)** a span that ends strictly inside a formula's layer span (rule 2 already refuses one
  that starts there): a display is never cut;
- **(d)** `conjuncts` that are not absent or 2–5 well-formed entries (§1.1), a `shape` outside
  `COMPOUND_SHAPES`, a shape without conjuncts or conjuncts without a shape, and an
  `existence_uniqueness` without exactly two;
- **(e)** `conjuncts` on a passage not scoped `formal_math`;
- **(f)** a passage not scoped `formal_math` whose prominence is not `supporting` (so never
  `subject`);
- **(g)** two passages of the source with the same span text — one `passage_key` for two
  passages, which no carry-over could tell apart.

It refuses nothing on a passage's size. The harvest receipt's gate block gains `cut: {passages,
formal, compound, conjuncts, by_shape, sentences, displays, multi_sentence, multi_display,
by_source}`: `conjuncts` is the compound statements' conjuncts, so a cut's clauses are
`conjuncts + formal − compound` (a simple statement's one clause each); sentences are counted
per passage (`locate_text.units`: a `.`, `!` or `?` before whitespace and an upper-case letter
or a formula token, not after an abbreviation of `locate_records._ABBREVIATIONS`; a formula
whose alttext ends with `.` or `?`, by `formula_stop`, is one too, a formula's `!` being a
factorial, and a block that starts right after one stands in for the whitespace), displays as
the `math` blocks inside it. It reports them.

### 3.4 The prompt's rules (the inventory prompt and the labeler's inventory mode)

What the gate cannot check is said in the prompt, `templates/prompts/locate-inventory.md.tmpl`,
and in the labeler's inventory mode, `workflows/locate-label.workflow.js`, in the same words
(a test holds them together, as it holds the judgment rules):

1. **The test.** A passage is one statement when one Lean declaration could state it whole with
   nothing left a reader would call a second result, even when a library states it in two
   lemmas. Otherwise cut it: a definition and a theorem about it; a fact and the reason the text
   gives for it; two cases in two sentences.
2. **Units.** A passage is one or more whole units: a sentence or one of its independent
   clauses, with the displays that sentence introduces; or a list item.
3. **Displays** are never cut and belong to the sentence that introduces them. An equation chain
   (a = b = c) is one statement, its end-to-end equation, and its middle terms are steps. A
   display of several separate equations is one compound statement, shape `equations`.
4. **Inside a sentence**, cut where an independent clause asserts its own fact: after a
   semicolon, or at "since", "because", "so", "hence", "then", "moreover", or ", and" followed
   by a new subject (`CUT_CONNECTIVES`). Never between a hypothesis and its conclusion, inside an
   iff, or between conjuncts about one subject. Each piece starts at a word; the connective
   starts no piece.
5. **Lists.** A list whose items each state a fact is one passage per item. A list of the cases
   of one definition or law, or of the conditions of one equivalence, is one compound statement
   (`cases`, `iff`). Its lead-in sentence is expository, unless it states a reason no neighbour
   states, which is a statement of its own.
6. **Proofs** (PD5, unchanged): narration is expository; each statement inside is its own
   passage.
7. **Context.** `statement` carries the hypotheses and objects, so it reads on its own and the
   span stays tight.
8. **Conjuncts.** Write the conjuncts of a compound statement, 2 to 5, with one of the five
   shapes; a simple statement has none. Anything else holding two results is two statements.
9. **The subject.** A lead that describes rather than states is expository; the subject marker
   goes on the passage that states the theorem.
10. **PD9**, in the words all five rule files carry from the flip on (§6.2):

> **PD9** — a stated reason ("it holds because X") is a passage of its own only when X is a
> declarative mathematical statement that no neighbouring passage already states; otherwise X is
> no passage and no clause.

### 3.5 Which rule is mechanical

| rule | the gate | the prompt |
|---|---|---|
| no nesting, no overlap | rule 10 (a), and the end rule (§3.2) | the default end; give `until` to stop sooner |
| no run into another block (theorem box, introduced displays and a compound list excepted) | rule 10 (b) | units, displays |
| a display is never cut | rule 2 (start), rule 10 (c) (end) | displays |
| conjunct count and shape | rule 10 (d), (e) | conjuncts, the closed list |
| the subject on the stating passage | rule 10 (f) | the subject |
| one key per passage | rule 10 (g) | — |
| what one statement is; where a sentence is cut; lists; chains; proofs; context; PD9 | — (counted in the receipt only) | 1–10 above |

## 4. Keys and the carry-over map

### 4.1 The keys

Defined once in `cflib.hashing` (built, §6.1); `locate_records`, which imports no cflib, restates
them in lockstep, as `verify_findings` restates `finding_id`:

- `span_sha256(layer, span)` — sha256 of the layer's code points `[start, end)`, UTF-8: the
  `span_sha256` every record's subject end already carries.
- `passage_key(pageid, span_sha256)` — sha256 of `<pageid>\n<span_sha256>`.
- `pair_key(passage_key, repository, declaration)` — sha256 of
  `<passage_key>\n<repository>\n<declaration>`, the repository the lean end's `uri`.
- `bundle_key(passage_key, pair_keys)` — sha256 of the passage key and the parts' pair keys,
  sorted, one per line; at least two distinct parts.
- `cut_id(pageid, layer_sha256, spans)` — `sha256_json` of `{pageid, layer_sha256, spans}`, the
  spans sorted.

No key holds a revision, a commit, a run or an item. The same text on the same page is one
passage key in any revision and under any cut; an item (`In-text #<hex>`) may survive a re-cut
that a passage key does not (an end that moved), and the reverse (a new head).

### 4.2 The map

For one page, between an **old** cut and a **new** cut (two inventory runs' passages of that
page, at one revision or two), computed from spans and span text only:

1. Key every passage of both cuts. Two passages of one cut with one key refuse the page
   (rule 10 (g) keeps that out of every new cut; an old cut is checked here).
2. An old passage whose key a new passage has is **`unchanged`**.
3. Otherwise place it on the new layer: its own span when both cuts share the layer sha;
   else the one occurrence of its text in the new layer. Its text not there exactly once:
   **`changed`**.
4. With M the new passages that share a character with the placed span: none — **`removed`**;
   two or more — **`split`**, into them; one, starting where it starts — **`end_moved`**; one,
   starting before it and holding the whole of it — **`merged`**; one otherwise —
   **`reshaped`**.
5. A new passage that shares a character with no placed old passage, and whose key no old
   passage has, is **`new`**.

Every entry names the passages it went into by passage key and item, and says whether the scope
or the conjuncts changed (`scope_changed`, `conjuncts_changed`: the sha256 of the canonical
conjunct list differs), which a consumer reads before it carries anything.

### 4.3 The document (`cf-locate-carry/1`)

```json
{"schema": "cf-locate-carry/1",
 "old": {"inventory_run": "inv-a", "span_end_rule": 1, "statement_cut_rule": 0},
 "new": {"inventory_run": "inv-b", "span_end_rule": 2, "statement_cut_rule": 1},
 "pages": [
   {"pageid": 1001,
    "old": {"source_id": "enwiki-1001-2002", "layer_sha256": "<hex>", "cut_id": "<hex>"},
    "new": {"source_id": "enwiki-1001-2002", "layer_sha256": "<hex>", "cut_id": "<hex>"},
    "same_layer": true,
    "passages": [
      {"passage_key": "<old key>", "item": "In-text #0000c0de", "span": [120, 380],
       "scope": "formal_math", "fate": "split",
       "into": [{"passage_key": "<new key>", "item": "In-text #0000c0de", "span": [120, 241]},
                {"passage_key": "<new key>", "item": "In-text #0000a11d", "span": [243, 380]}],
       "scope_changed": false, "conjuncts_changed": true},
      {"passage_key": "<key>", "item": "In-text #0000f00d", "span": [400, 466],
       "scope": "formal_math", "fate": "unchanged",
       "into": [{"passage_key": "<the same key>", "item": "In-text #0000f00d", "span": [400, 466]}],
       "scope_changed": false, "conjuncts_changed": false}],
    "new_passages": [{"passage_key": "<key>", "item": "In-text #0000b0b0", "span": [500, 540],
                      "scope": "formal_math", "fate": "new"}],
    "counts": {"unchanged": 1, "split": 1, "new": 1}}],
 "refused": [{"pageid": 1002, "reason": "two passages of the old cut share one passage_key"}],
 "counts": {"pages": 1, "unchanged": 1, "split": 1, "new": 1}}
```

`cf locate carry --from <run> --to <run> [--source ID]… [--rows FILE --rows-out FILE]`, over
two inventory runs (a new subcommand of `cf locate`, the deterministic steps that call no model),
writes it to `reports/locate-carry/<from>/<to>.json` for every page both runs inventoried (or the
named sources' pages), and with `--rows` copies a JSONL file of rows keyed by `passage_key`
(known answers, a verdict export) adding each row's `carry: {fate, into}` and `carried`. It reads
the two runs' gated inventory documents and their layers, writes nothing else, and is
deterministic. The code is `scripts/locate/carry.py`; the shared cases a consumer's port runs are
`tests/data/locate/carry-parity.json` (old spans, new spans, layers, expected fates), written by
hand like the status parity table. As built (`references/locate-documents.md` §12), the document
also names the `--source` ids (`sources`) and the pages one run alone holds (`unpaired`), each
`into` entry names the scope the new cut gave its passage (which §4.4 reads), and a row's `carry`
also holds `scope_changed` and `conjuncts_changed`.

### 4.4 What carries, and what is asked again

- A **pair** verdict, label or known answer keyed by passage key (and repository and
  declaration) carries when its passage is `unchanged`, the new passage is `formal_math`, and —
  for anything that read the conjuncts — `conjuncts_changed` is false. An unchanged text keeps its
  pair keys, so with `pair_key` as the key the first test is automatic; no key holds the
  conjuncts, so the last is not.
- A **bundle** verdict carries when its passage is `unchanged` with its conjuncts, and each part's
  pair carries.
- Everything else is asked again; the old verdict is shown as one on an earlier cut. A status is
  never carried: it is derived again.
- **Bench gold** (`scripts/bench/`): `cf bench carry BENCH --from <run> --to <run>`, over two
  inventory runs, writes the report `gold/carry-<n>.json` (`cf-bench-carry/1`), one row per gold
  row of every tier whose `span` lies in one passage of the old cut: `carried` when that passage
  is `unchanged`, else `relabel` with its fate. It edits no label file (a gold change is a new
  manifest version), and a carried row keeps its labels only while the labeler version is the
  same: across a labeler bump every row is `relabel`, and the report says so. The flip's bump to
  `locate-label/6` is one, so this round relabels all gold; the tool is for the re-cuts after it.
  As built (`references/locate-bench.md` §7), each row's `state` is one of `GOLD_CARRY_STATES`:
  `carried`, `reanchor` (its text unchanged on a new layer: its labels stand and its span moves to
  the row's `anchor`) or `relabel`; a span, in one passage, in several or in none, carries only
  while every old passage over it is `unchanged` and the new passages over it are those, at their
  spans. §9 gives both readings.
- **Known answers** are the operator's list, outside this tree: `--rows` gives each its fate.

## 5. The failure-mode fixes

Every fix of the smoke-3 audit's §5 and of its status anomalies, with what it becomes. A fix is
named by the audit's §5 item and a letter (fix 2a is item 2's first); the audit's FM label for
the item is beside it. "Rules 3" means it binds only a run whose gate.json names
`judgment_rules` 3; "status 2" a run of status rule 2; a plan rule binds the plans made after the
flip, each plan recording what it drew.

| fix | FM | the audit's proposal | becomes | file | package |
|---|---|---|---|---|---|
| 1a | FM7 on claims | a refute names the clause no part states, and why no R1 convention reaches each part; "they state it after unfolding" is a confirm | prompt rule + gate (rules 3): a coverage refute gives `unstated` and `part_reasons` (§1.4) | `templates/prompts/locate-challenge.md.tmpl`, `scripts/gate/verify_locate.py` | fixes, gate |
| 1b | FM7 on claims | refuse a refute whose named clause is in a part's `clauses` unless it says why that part fails | gate (rules 3): implied by 1a, since `part_reasons` names every part | `scripts/gate/verify_locate.py` | gate |
| 1c | FM7 on claims | refuse a claim one of whose parts carries every clause | built (rules 2, PD6); under rules 3 implied by J3 | — | — |
| 1d | FM7 on claims | a second challenger for 25% of confirmed claims | **not built** (§9) | — | — |
| 2a | FM1, FM4 | every strong record writes both instantiation tests | prompt rule + gate (rules 3): `tests` `{r2, r3}` on every strong location, a part by its `conjunct_relation` | both locate prompts; `scripts/gate/verify_locate.py` | fixes, gate |
| 2b | FM1, FM4 | require `instantiation` on every strong record; refuse a strong relation when the passage says integer and every numeric binder is ℕ, unless the note answers it | gate (rules 3): `instantiation` required on every strong location whatever its binders, a part by its `conjunct_relation`; the ℕ tripwire on the passage's `statement` and conjunct texts (`NAT_FOR_INT_INTEGER_WORDS`: "integer", "integers", "ℤ") against the signature's binder groups (`binder_groups`), answered by a note naming "ℕ" or "natural" (`NAT_FOR_INT_NATURAL_WORDS`) | `scripts/gate/verify_locate.py` | gate |
| 2c | FM1, FM4 | "table", "for all primes", "every point", "differentiable at" join the closeMatch tripwire | gate (rules 3): `CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT`, on an exact or close part's note too; the prompts render `generality_phrases_statement_cut` | `scripts/gate/verify_locate.py`; both locate prompts | gate, fixes |
| 2d | FM1, FM4 | a confirm of a strong basis carries the challenger's own two-way test | gate (rules 3): `tests` on every verdict that keeps or leaves a strong relation, a part's by the `conjunct_relation` it leaves | `scripts/gate/verify_locate.py` | gate |
| 2e | FM1, FM4 | the instantiation note states PD7's three-way test | prompt rule | both locate prompts, the labeler | fixes |
| 3a | FM3, FM6 | a lemma that only unfolds a definition the passage glosses (`mem_*_iff`, `*_apply`, `*_def`) is `relatedMatch` | prompt rule (a name pattern is no statement; §9) | both locate prompts, the labeler | fixes |
| 3b | FM3, FM6 | a `hasPart`/`narrowMatch` on an abstract structure names the instance at the passage's objects, and the gate checks it in the corpus | **not built as a corpus check** (§9); the binder-triggered `instantiation` of rules 1 stands | — | — |
| 3c | FM9 | the inventory rules of item 8 | the cut (§3) | — | cut |
| 4a | — (over-correction) | refuse a passage whose `hasPart` clauses together name every clause when no coverage claim is written | gate (rules 3): refuse a compound statement on which some of its `hasPart` locations with a strong `conjunct_relation` partition the conjuncts (pairwise disjoint, every conjunct, at least two, none naming all) while it carries no coverage claim | `scripts/gate/verify_locate.py` | gate |
| 4b | — | on a definition passage the defined object is exact or close, not a part; try a hypothesis with roles swapped before calling it stronger | prompt rule | both locate prompts, the labeler | fixes |
| 4c | — | extend the under-claim guard to central passages | the next audit's frame; no code | — | — |
| 5a | FM5 | one query per clause for its special case, and search the conjecture libraries | prompt rule | `templates/prompts/locate.md.tmpl`, the challenge prompt's uphold | fixes |
| 5b | FM5 | an absence receipt names the nearest special-case candidate and why it fails R3/PD4 | gate (rules 3): `receipt.special_case` on every absence, every uphold and every absent fill: `{library, declaration, reason}` (the declaration in `corpus.sqlite`, rule 3) or `{reason}` when there is none; `reason` a judgment line | `scripts/gate/verify_locate.py` | gate |
| 5c | FM5 | the supporting-absence sample from 25% to 50% | plan rule: `CHALLENGE_ABSENCE_SAMPLE_RATE` 0.5 at the flip | `scripts/cflib/vocab.py` | fixes |
| 6, gate | A1, A2 | a verdict that leaves a formal passage with no counting location and no absence is refused unless it brings a replacement or an absence with a hunt | gate (rules 3): a document whose verdicts leave a `located` passage with no counting location — its judged results and the unjudged `passage_locations` together — gives `fills[item]`: `{outcome: absent, receipt}` held to an absence's checks (an accepted candidate among them, refused as on a locate absence), or `{outcome: located, locations}` held to an overturn's; `fills` on any other item is refused | `scripts/gate/verify_locate.py` | gate |
| 6, derivation | A1, A2 | such a passage is `challenge_pending`, owed a hunt, not none | derivation (status 2): §2.5; the outcomes apply `fills` | `scripts/locate/outcomes.py` | fixes |
| 6, plan | A3 | a prominence sample goes in the same task as all of its passage's locations | plan rule: for a `supporting` passage whose prominence record is required, its unrequired location, absence and unresolved records join the same task with `conditional: "prominence"`, their planned value unchanged | `scripts/locate/challenge_plan.py`, `scripts/locate/plan.py` | fixes, gate |
| 6, gate | A3 | an overturn to subject or central comes with verdicts on those locations | gate (rules 3): a prominence overturn to `subject` or `central` judges every conditional record of its passage in the same document, each as a refuted-by-default or hunted record; a verdict on a conditional record with no such overturn is refused, and without one no conditional record is owed | `scripts/gate/verify_locate.py` | gate |
| 6, outcomes | A3 | — | a conditional record's verdict applies when the same challenge run overturned its passage's prominence to `subject` or `central` | `scripts/locate/outcomes.py` | fixes |
| 7 | FM8 | PD5–PD9 (and the maintainer's PD10) | built (rules 2); carried into rules 3 unchanged but PD9's words (§3.4) | — | — |
| 8 | FM9 | spans end at the next passage's start; a span runs through the display its sentence introduces; a passage with no formal clause is expository | the end rule, rule 10 and the inventory prompt (§3) | §3 | cut, gate |

The new prompt rules (fixes 2a, 2e, 3a, 4b and 5a) are written word for word in
`references/vocabulary.md` §1.11 (a list headed "The statement cut's prompt rules"), both locate
prompts and the labeler, and `tests/test_locate_judgment.py` holds them together as it holds
R1–R7 and PD1–PD10.

## 6. The work split

Four packages, each on its own branch from this one, with disjoint files where that is possible.
Each keeps the suite and `tests/e2e/locate_synthetic/driver.py` green **before** the flip: its
new behaviour is reached only through the new versions, which its own tests set by hand
(gate.json keys, `run.yaml` `locate.status_rule`, `derive_status(status_rule=2)`). Merge order:
cut, then gate (it calls cut's `locate_text` functions), then fixes and carry in either order,
then the flip (§7). No run is planned between a merge and the flip (this round spends nothing),
and the flip is what a run of the new flow needs. A prompt that describes the new flow renders
it only for a plan of the new versions: the locate and challenge prompts by the slot
`judgment_rules_statement_cut` (`vocab.JUDGMENT_RULES`, which the planner writes into gate.json),
so a plan made before the flip renders the prompts it rendered before.

### 6.1 Built here

`cflib.vocab`'s statement-cut block (every name of §1–§4: `RULES_STATEMENT_CUT`,
`SPAN_END_STATEMENT_CUT`, `STATEMENT_CUT_RULE`, `STATUS_RULE_D1`, `STATUS_RULE_BUNDLE`,
`STATUS_RULE`, the shapes, `MIN_CONJUNCTS`, `MAX_CONJUNCTS`, `SHAPE_CONJUNCT_COUNTS`,
`SIMPLE_STATEMENT_CLAUSE`, `CONJUNCT_RELATIONS`, `BUNDLE_RELATIONS`, `TWO_WAY_TEST_KEYS`,
`CUT_CONNECTIVES`, `CUT_FLAGS`, `COVERAGE_RECORD_KEYS_BUNDLE`,
`CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT`, the carry fates), `conjunct_relation` and `tests`
at the end of `JUDGMENT_LOCATION_FIELDS`, the keys of §4.1 in `cflib.hashing`, the prompt slots
in `locate.prompts.build_context` (`compound_shapes`, `min_conjuncts`, `max_conjuncts`,
`cut_connectives`, `cut_flags`, `simple_statement_clause`, `generality_phrases_statement_cut`), a
paragraph in `references/vocabulary.md` §1.11, and `tests/test_locate_statement_cut_spec.py`. No
stage reads any of it.

### 6.2 The packages

**cut** — the inventory side.

- `templates/prompts/locate-inventory.md.tmpl`: §3.4, the conjuncts and shapes (from the
  slots), `until` under the new end rule, PD9's line. Where a passage ends, and what rule 10
  refuses, is said by the end rule the run's tool applies: a `span_end_statement_cut` slot
  (`vocab.SPAN_END_RULE == SPAN_END_STATEMENT_CUT`) keeps version 1's paragraph until the flip.
- `scripts/gate/locate_text.py`: `passage_end(…, rule=2, next_start=…, formulas=…)`,
  `extent(…)` and its helpers (§3.1), `units(…)` (§3.3); the rule-1 behaviour unchanged.
- `scripts/locate/passage_ids.py`: the end by the run's `span_end_rule` (§3.2; rule 2 in two
  passes), the conjuncts left as written.
- `workflows/locate-label.workflow.js`: the inventory mode's cut rules (§3.4, word for word) and
  conjuncts in its passages.
- docs: `references/locate-documents.md` §2; the statement-cut paragraph of
  `references/vocabulary.md` §1.11 (the cut rows).
- tests: a new `tests/test_locate_statement_cut.py` (the end rule on synthetic layers, every
  definition of §3.1, the prompt and labeler lines held together).

**gate** — the gate, the planner's gate config and manifests, the receipt counts, and the flip.

- `scripts/gate/verify_locate.py`: rule 10 (§3.3); rule 2 under `span_end_rule` 2; rule 9 under
  rules 3 (J1–J4, §1.4, every gate row of §5); the inlined copies of the new vocabulary, pinned to
  `cflib.vocab` by `tests/test_verify_locate.py`; the stats rule 10 counts.
- `scripts/harvest/gate_corpus.py`: the receipt's `cut` block (as it carries `skipped`).
- `scripts/locate/plan.py`, `scripts/cfcmd/plan_locate.py`: the gate.json keys and `run.yaml`
  `status_rule` (written at the flip), the manifests' new fields (§1.2), the two refusals of §2.2.
- docs: `references/locate-documents.md` §3, §4 and §9; the "what the gate holds mechanically"
  paragraph of `references/vocabulary.md` §1.11; `docs/design/stage_map.md` §4.0's sentence on
  versioned rules (at the flip).
- tests: a new `tests/test_locate_gate_statement_cut.py`; the version pins in
  `tests/test_verify_locate.py` and `tests/test_locate_judgment.py` (at the flip);
  `tests/_locate_fixtures.py` (only this package edits it); the e2e driver (at the flip).

**fixes** — the judgment prompts, the challenge plan, the outcomes, the status rule and the
record set.

- `templates/prompts/locate.md.tmpl`, `templates/prompts/locate-challenge.md.tmpl`: the rules-3
  flow (no claim list; conjunct ids; `conjunct_relation`; the bundle; `part_relations`,
  `unstated`, `part_reasons`, `tests`, `special_case`, `fills`, `cut_flag`; conditional records)
  and the prompt rules of §5.
- `templates/prompts/locate-worker-system.md.tmpl`: the judgment rules' line of the list of what
  the gate refuses (its locate and challenge part, `{{#unless is_inventory}}`), in the rules-3
  words; no other package edits the file.
- `workflows/locate-label.workflow.js`: the shared rules block (PD9's line, §5's prompt rules) and
  the label mode's `conjunct_relation` per part — not the inventory mode, which is cut's.
- `scripts/cflib/vocab.py`: `derive_status(…, status_rule=…)` (§2.3), and at the flip
  `CHALLENGE_ABSENCE_SAMPLE_RATE`.
- `scripts/locate/challenge_plan.py` (A3, the records' new fields), `scripts/locate/outcomes.py`
  (§1.5, §2.1, §2.5, `fills`, conditional verdicts, `cut_flag` counts — every new count key only
  under status rule 2), `scripts/locate/records.py` (the keys restated, `coverage_ext` of status
  rule 2), `scripts/locate/export.py` (`/2`, §2.4).
- `tests/data/locate/status-parity-2.json`.
- docs: `references/locate-documents.md` §6 and §10; `references/vocabulary.md` §1.11's derived
  status, coverage claim, challenge verdicts and judgment-rule lines (PD9, the prompt rules).
- tests: new `tests/test_locate_bundle_status.py` and `tests/test_locate_fm_fixes.py`, and the
  word-for-word checks in `tests/test_locate_judgment.py` for PD9 and the new lines.

**carry** — the keys' consumers, the carry-over map and the bench.

- `scripts/locate/carry.py` and its subcommand in `scripts/cfcmd/locate.py`; the report path in
  `scripts/cflib/paths.py` (`WorkspacePaths`).
- `scripts/bench/` (`scripts/bench/carry.py`, `scripts/cfcmd/bench.py`'s `carry` subcommand,
  and `scripts/bench/gold.py`: the label rows' `conjunct_relation`, gold bundles derived under
  status rule 2). The `gold.py` share calls the fixes package's `derive_status(…,
  status_rule=2)`, so it landed after fixes merged, before the flip; the labeler's label mode
  names the span's bundle (`bundle`, `references/locate-bench.md` §5 and §6).
- `tests/data/locate/carry-parity.json`.
- docs: a new §12 "Carry-over maps" in `references/locate-documents.md`; the keys and fates rows
  of the statement-cut paragraph in `references/vocabulary.md` §1.11; `references/locate-bench.md`
  (gold carry); the command index in `SKILL.md`; the L6 row of `docs/design/stage_map.md` §4.0.
- tests: a new `tests/test_locate_carry.py`.

### 6.3 Shared files, by section

| file | cut | gate | fixes | carry |
|---|---|---|---|---|
| `scripts/cflib/vocab.py` | — (names are built) | the flip's two values | `derive_status`; the absence rate at the flip | — |
| `references/vocabulary.md` §1.11 | the statement-cut paragraph's cut rows | "what the gate holds mechanically" | derived status, coverage claim, challenge verdicts, the rule lines | the statement-cut paragraph's keys and fates |
| `references/locate-documents.md` | §2 | §3, §4, §9 | §6, §10 | a new §12 |
| `docs/design/stage_map.md` §4.0 | — | the versioned-rules sentence (at the flip) | — | the L6 row |
| `workflows/locate-label.workflow.js` | the inventory mode | — | the shared rules block, the label mode | — |
| `templates/prompts/locate-worker-system.md.tmpl` | — | — | the judgment rules' line of the gate list | — |
| `tests/test_locate_judgment.py` | — | the version pins (at the flip) | the word-for-word checks | — |
| `tests/e2e/locate_synthetic/driver.py` | — | the documents' new shapes, and a carry step between two inventories (at the flip) | — | — |
| `CHANGELOG.md` | one bullet | one bullet, and the flip's | one bullet | one bullet |

Each package adds its bullets at the top of `CHANGELOG.md`'s "### Changed" (or "### Added" for a
new command), and a merge keeps every bullet.

## 7. The flip, and what stays as it was

**The flip** is one commit by the gate package after the four have merged: `vocab.JUDGMENT_RULES
= RULES_STATEMENT_CUT`, `vocab.SPAN_END_RULE = SPAN_END_STATEMENT_CUT`; the planner writes
`statement_cut_rule` and `status_rule`; `CHALLENGE_ABSENCE_SAMPLE_RATE` is 0.5; the labeler is
`locate-label/6` and `bench.gold.LABELER_RULES` maps it to 3; the e2e driver's documents take the
new shapes, and it runs `cf locate carry` between two of its inventories; the version pins in the tests move; `docs/design/stage_map.md` §4.0 names the four
keys; `CHANGELOG.md` says what changed for a newly planned run. From then on the bench's score
refuses gold of `locate-label/5` against a run of rules 3, as it refuses any gold of other rules,
until the relabel.

**What stays as it was**, and how each package proves it before it merges (and the flip once
more):

1. Re-gate every run of the operator's workspace, `$WS/runs/*/`: copy each run's
   `gate.local.json` to a scratch directory with `log_dir` pointing there, and run the checkout's
   `scripts/gate/verify_locate.py --gate-config <copy> --root $WS/corpus/<run> --findings-dir .`
   (`--self-check` for a run with no harvested corpus); record the exit and every problem line.
2. Re-tally the smoke runs read-only: `locate.outcomes.build` for the inventory and for each locate
   run with its challenge run, and the export into a scratch directory with its report captured
   rather than written (the seal's `skill_commit` aside, every file's sha).
3. Do both on the base commit and on the package's head; the two must be identical. Nothing is
   written under `$WS`.

Every new field, count key and record key appears only under the versions that name it; a
document of an earlier version reads, gates, derives and exports byte for byte as before.

## 8. What WikiLean must do

The consumer is outside this tree (the maintainer's scope rule: what is specific to WikiLean goes
in WikiLean). These are the follow-ups, each needed only once a set of status rule 2 is imported:

1. **The record set.** Accept `cf-locate-recordset/2`: the seal's one extra key `status_rule`;
   `passage_key` in every record's locate extension and `pair_key` in a location record's; a
   coverage claim with `relation`, `conjuncts` and `bundle_key` (§2.4). Keep `/1` as it is.
2. **The status.** In the derivation the atlas builder checks parity with, read a status-2
   claim's `relation` as the stand-in's relation (§2.3), and run
   `tests/data/locate/status-parity-2.json` beside the round-3 table, which stays the test of
   `/1` sets.
3. **Verdicts** (the revised 0015): key a verdict by `pair_key` beside `location_key`; it then
   survives every re-cut and revision that leaves its passage's text alone, and one that read the
   conjuncts (a bundle's, a bundle part's) only while they are the same too (§4.4: the map's
   `conjuncts_changed`). Show one whose `pair_key` no longer appears as a verdict on an earlier
   cut, with the fate from the carry map (or from the port of §4.2, held to
   `tests/data/locate/carry-parity.json`), and ask again.
4. **The ballot** (the council harness): a bundle is one option, keyed by `bundle_key`, its parts
   shown each beside its conjunct from the claim's `conjuncts`; the "not a statement" flag is the
   token `not_a_statement`, kept beside the ranking and keyed by `passage_key`.
5. **Nothing else changes**: the subject's QID still rides on the subject passage's records, now
   the stating one; a passage owed a hunt is not exported.

## 9. Readings taken, and what is not built

Readings the maintainer may overrule (each is one change here and in the package that built it):

- **"One part per conjunct"** is read as *every conjunct stated by exactly one part* (the parts
  partition the conjuncts), so a part may state two of three conjuncts. For two conjuncts — an
  iff, existence and uniqueness, nearly every bundle — the reading is the literal one. The literal
  reading for more (each part exactly one conjunct) is a one-line change to J3.
- **A `hasPart` on a simple statement** stays allowed, naming `c1` (the design keeps "a simple
  statement that has only narrowMatch or hasPart cards" partial); only a compound one's parts carry
  a conjunct relation and may form a bundle.
- **A part is held by its `conjunct_relation`**: one whose `conjunct_relation` is strong owes
  everything a strong location owes (fixes 2a–2c and R1's words: `tests`, `instantiation`, the
  ℕ-for-ℤ tripwire, the generality list), and so does a verdict that keeps a strong one (fix 2d),
  since a bundle takes its weakest part's relation and a strong part is a strong claim. The gap
  check (a note admitting a clause it does not state) stays the location's own, since a part
  states some conjuncts and not the rest by definition. Exempting parts from `tests` alone (the
  gate builder's first reading) is a small change to `verify_locate.check_strong_location`.
- **A passage not scoped `formal_math` is `supporting`** (rule 10 (f)): nothing reads its
  prominence, and one value keeps the subject marker where it belongs.
- **A bundle's conjuncts are exported** (§2.4), so a ballot shows each declaration beside its
  conjunct from the record set alone. They are the inventory worker's paraphrases, held to the
  export's cap.
- **A confirm's `part_relations` entry counts only where it is weaker** than the part's own
  `conjunct_relation` (§2.1). A challenge never raises a relation, so the outcomes keep the
  weaker of the two; "replaces", read literally, would let a confirm the gate did not admit
  raise a part. On every document the gate admits the two readings agree, and the literal one
  is a one-line change to `outcomes.bundle_relation`.
- **A challenger's `tests` replace the worker's** on the location its confirm or downgrade
  leaves (fix 2d), as its `step`, `convention` and `instantiation` do: the outcome row keeps one
  pair, the verdict's, and the worker's stay in its locate document. A location keeps `tests`
  while it is held to a strong relation — its own, or a part's `conjunct_relation` — and loses
  them when a verdict leaves it weaker. Keeping both pairs would be a new key on the row.
- **`cf-locate-recordset/2`** is the status rule's format; the self-evidencing format planned under
  that number becomes `/3`.
- **No new run of the old flow**: after the flip a locate run needs an inventory of the cut, and a
  challenge a locate run of rules 3 (§2.2). Old runs stay readable, gated, tallied and exported.
- **Gold in one passage carries more strictly than §4.4's letter** (`cf bench carry`): only while
  the new passages over its span are that passage at its span, not whenever the passage is
  `unchanged`. A new passage nested in the unchanged one, or the unchanged text's twin cut
  elsewhere on the page, changes the run passages the score joins the label against
  (`bench.metrics.join`, greedy by overlap), so the row is relabelled. The letter is a small
  change to `bench.carry`.
- **Gold on a new revision whose text is unchanged is `reanchor`, not `carried`**: the
  maintainer's answer carries gold wherever its text is unchanged, so its labels stand and the
  report gives its span on the new layer; the bench scores only runs on the layers its frame
  pins, so the row reaches a score only through a new gold version on a frame of the new layer,
  which no tool writes yet (below).
- **A `--rows` row that does not say whether it read the conjuncts is held to the conjunct test**
  (§4.4's "for anything that read the conjuncts"); `"reads_conjuncts": false` leaves the test out.
- **A gold bundle is the labeler's word** (`bench.gold.gold_bundle`): the label mode names the
  span's bundle by its parts (`bundle`), and the transcriber holds it to what it can check —
  at least two distinct parts, each a `hasPart` kept at the pin whose `conjunct_relation` is
  strong — but not to J3's partition, since a G3 span has no inventory conjuncts and the
  labeler lists its claims to itself only; a bundle that does not stand leaves its parts the
  `hasPart`s they are. Gold statuses of a labeler of rules 3 are derived under status rule 2,
  as a run of rules 3 is. Asking the labeler for each part's claim ids would let the
  transcriber check the partition too: a change to the label schema and `gold_bundle`.
- **`cf locate carry` records nothing in a `state.json`**: §4.3 has it write nothing but the map
  (and `--rows-out`), against CLAUDE.md §5's step that records a command's counts. The two runs
  each pin their own sourceset, so no one `state.json` is the map's; its `counts` are its record.

Not built in this round, and why:

- **Fix 1d, a second challenger for confirmed claims.** Two verdicts on one record are refused
  everywhere a challenge plan is read (`outcomes.challenge_values`), and combining them is a new
  rule of its own (a refute by either removes the claim). The cut should bring claims from 16% of
  formal passages to at most 5%; the inventory pilot measures that first.
- **Fix 3b's corpus check of an instance.** An instance argument is usually found by type-class
  search through a chain of instances, or is an automatically named one, so "the instance" is
  often no single declaration the corpus can confirm: a gate that refused a missing name would
  refuse correct records. The binder-triggered `instantiation` of rules 1 stays, and fix 3a is a
  prompt rule because a declaration's name (`mem_*_iff`, `*_def`) is a convention, not a
  statement.
- **A top-up wave for a passage owed a hunt** (§2.5). Under rules 3 `fills` makes the case
  unreachable; the derivation blocks any that remains from export. A wave that plans only owed
  hunts needs a challenge plan built over earlier challenge runs, which none is.
- **The council's bundle option and its "not a statement" flag** (§8): the harness is not in this
  tree.
- **A statement-level bench frame.** The G3 span sample draws blocks; relabelling it at statement
  level, with the labeler cutting by §3.4, is the bench relabel step that follows smoke 4. The
  flip's labeler bump already stops the score from mixing the two.
- **A writer of the re-anchored frame**: `cf bench carry` gives a `reanchor` row's span on the new
  layer and edits no gold file; no tool writes a frame of the new layer, or the gold version on
  it, from those anchors.
