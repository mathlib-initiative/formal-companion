# Locate documents

The file formats the locate stages exchange (`docs/proposals/wikipedia-locate.md`
§3–§5). Every package that writes or reads one of these follows this file; a
change to a shape is a change here in the same commit. **Status**: the formats
are fixed and built: `scripts/gate/locate_text.py` (the head text and the layer
quote of §7), the paths of `cflib.paths`, the sourcesets and renderer of §7
(`cf sourceset`), the universes and `corpus.sqlite` of §8 (`cf corpus`), and the
planner side (`scripts/locate/`: §5's candidates, §6's sealed set, §9's run,
§10's outcomes and §12's carry-over map).

**Contents**: 1 the common header · 2 inventory document · 3 locate document ·
4 challenge document · 5 candidates file · 6 sealed record set · 7 sourcesets and
rendered web sources · 8 universes, library indexes and `corpus.sqlite` · 9 a
locate run on disk · 10 harvested rows and passage outcomes · 11 campaigns · 12 carry-over
maps · 13 inventory audit records (13.9 the turn machine, 13.10 the harvest's `audit`
block)

Every example is synthetic: a made-up article ("Widget lemma", page id 1001,
revision 2002), made-up libraries `alpha` and `beta`, made-up declarations.

Common rules:

- JSON, UTF-8, sorted keys when written by `cf`; workers may write any key
  order.
- Offsets (`span`) are code-point offsets into the source's Literature Links
  text layer (`layer.txt`), half-open.
- A library is named by its universe-local id (`alpha`, `beta`, …); a declaration by its full Lean name. The commit is
  never written by a worker: it is the universe's.
- No document carries `proof_status`; it is joined from the index at export.
- A passage's kind is `passage_kind` (`cflib.vocab.PASSAGE_KINDS`). The word
  `kind` is reserved for the finding kind (`PASSAGE`, `LOCATION`, `ABSENCE`,
  `UNRESOLVED`) that harvest hashes into `finding_id`.
- `location_key` is `cflib.hashing.location_key(source_id, item, library,
  commit, declaration)`: sha256 over the five parts joined with a newline, the
  item through `items.clean()` first, the same shape as `finding_id`.

## 1. The common header

Every locate document starts with:

```json
{"schema": "cf-locate/1",
 "mode": "locate-inventory",
 "run_id": "r-…", "task_id": "t-…",
 "source_id": "enwiki-1001-2002",
 "source_sha": "<html_sha256>", "layer_sha256": "<hex>", "renderer": "web-html/3",
 "universe": "u1", "universe_sha256": "<hex>"}
```

`universe` and `universe_sha256` are required in `locate` and
`locate-challenge` documents and absent from `locate-inventory` ones. One
document covers one source; it lives at `findings/<task-id>/<source-id>.json`
(a file whose name is not its `source_id` is refused, rule 1), and a task that
writes documents writes one for every source its task manifest names (rule 1),
each a regular file (a symlink is refused).
`schema` is `cflib.vocab.LOCATE_DOCUMENT_SCHEMA`; `renderer` is the source's
(`web-html/3` for the first corpus).

A task that could not be finished writes one document per source with a
document-level `"unresolved": {"reason": "…", "by": "worker" | "attempt_cap"}`:
an inventory document then carries `"passages": []`, a locate document carries
every task passage with `outcome: "unresolved"`, and a challenge document
carries `"verdicts": []`. `by: "attempt_cap"` (reason
`cflib.vocab.UNRESOLVED_ATTEMPT_CAP`) is written by the bridge after a task's
third genuine reviewer rejection (proposal §6), never by a worker: the gate
refuses it (rule 1) unless its reason is the bridge's own text
(`attempt_cap.cap_reason`, `attempt_cap: <n> genuine reviewer rejections; …`
with `n` at least `cflib.vocab.LOCATE_ATTEMPT_CAP`) and, in tend's validation
(`--changed-files`), the task's counter `<tend-root>/attempts/<task-id>.json` is
at the cap. The gate finds the tend root as the bridge does: it is the directory
of `gate.json`'s `orchestrator_db` (§9), on a run host the sibling of the gate
directory, never its parent. A harvested corpus has no counter; there the text
is enough.

## 2. Inventory document (`mode: locate-inventory`)

```json
{"…header…": "…",
 "passages": [
   {"item": "In-text #3fa91c07", "head_words": 12,
    "span": [10234, 10612],
    "quote": {"exact": "…", "prefix": "…", "suffix": "…"},
    "section": ["Theorems"], "fragment": "Theorems",
    "passage_kind": "theorem", "prominence": "central", "scope": "formal_math",
    "statement": "<informal restatement, one or two sentences>",
    "concept": null,
    "finder": "theorem_box"},
   {"item": "In-text #0000c0de", "head_words": 12, "span": [10613, 10734],
    "quote": {"exact": "Every finite widget has a spanning gadget", "prefix": "", "suffix": ""},
    "section": ["Theorems"], "fragment": "Theorems",
    "passage_kind": "theorem", "prominence": "supporting", "scope": "formal_math",
    "statement": "For a finite widget W: W has a spanning gadget, and any two spanning gadgets of W are equal.",
    "conjuncts": [{"id": "c1", "text": "a finite widget has a spanning gadget"},
                  {"id": "c2", "text": "two spanning gadgets of one finite widget are equal"}],
    "shape": "existence_uniqueness",
    "concept": null, "finder": "model"}],
 "skips": [{"span": [200, 900], "reason": "infobox"}],
 "notes": "free text, at most 2,000 characters"}
```

- `finder` ∈ {`theorem_box`, `bold_definition`, `lead`, `model`}: which finder
  proposed the passage. Deterministic finders' passages are pre-filled in the
  task, one row per (finder, start); two finders may share a start (a lead
  paragraph with a bold term, a theorem box and its first paragraph), and the
  worker writes ONE passage per start, its `finder` any one of that start's
  finders. Dropping a start needs a `skips` entry covering each of its
  finders' spans.
- `item` must equal the passage id of `span` (§2.2 of the proposal), written
  `In-text #<8 hex>`; the gate recomputes every id of the document over all
  its passages, so `head_words` must be the shortest unique head.
- **The quote rule**: `quote.exact` (non-empty, at most 1,500 characters, each
  context at most 200 — `gate.json` may change both) matches the layer
  **exactly once** with its `prefix` immediately before it and its `suffix`
  immediately after, the match starts at `span[0]`, and `exact` lies inside
  the span. A passage may not start inside a formula. And the EXPORT must be
  able to quote it: `locate_records.cap_quote` (`scripts/locate/records.py`:
  twelve words, at most 32 characters of context) must find it exactly once,
  or the passage is refused under rule 1 — a worker's longer quote may be
  unique where the exported one is not, and the refusal comes while the start
  can still move, not at `cf export literature-links`.
- **The end**: `span[1]` is where `passage_ids.py` ends the passage
  (`locate_text.passage_end`), and gate rule 2 recomputes it, by the span-end
  rule the run's `gate.json` names (`span_end_rule`, §9; `passage_ids.py` reads
  it from the `gate.json` beside it in the gate directory): the end of the
  first occurrence of the passage's `until` (its last words, as the layer has
  them) at or after its start when it gives one; else, under **version 1**, the
  end of the outermost `paragraphs.json` block it starts in, else the end of
  its quote; else, under **version 2** (the statement cut's,
  `docs/proposals/statement-cut.md` §3.2), the earlier of its **extent** and the
  start of the document's next passage (the quote's end when no block holds the
  start), the **separator** before that start left out of both passages when
  it is the earlier (whitespace, one connective of `CUT_CONNECTIVES` as a whole
  word, `, and` only after its comma, and a comma or semicolon, read back from
  the next start and never inside a formula:
  `locate_text.Blocks.separator_start`), so a statement ends where an `until` at its last word
  would end it and keeps one `passage_key` whether or not its neighbour is a
  passage; under **version 3** (the continued cut's, the proposal's §3.1–§3.2)
  the same with the **continued extent**, which also takes in the paragraph
  that finishes a sentence after its display (a plain `p` whose first word goes
  on with the sentence, after a block that ends with a display and no full stop:
  `locate_text.Blocks.continuation`), so "…such that [display] for all x." ends
  after "x."; and never before the end of its quote. The extent
  (`locate_text.Blocks.extent`, the proposal's §3.1) is the end of the outermost
  block the passage starts in when that block is a theorem box (or shares its
  span with one, as a box holding one paragraph does), and otherwise the end of
  that block followed by the display blocks its last sentence introduces: while
  the last block's last character — a trailing formula read as the last
  character of its normalised alttext after TeX spacing (`\,` `\;` `\:` `\!`
  `\quad` `\qquad` `~`), a `\right.` null delimiter, a `\\` line break, an
  `\end{…}` closer and closing braces (`locate_text.formula_stop`), and closing
  brackets and quotes after a full stop read through to it — is none of `.`,
  `!`, `?` (a formula's `!` is a factorial and no stop:
  `locate_text.FORMULA_STOPS`), and the next block (the first one no other block holds, after only
  whitespace) holds a formula and no letter outside its formulas and is no
  `li` (a list item is a unit of its own), that block joins. The outermost
  block is the bound, as version 1's is: inside a `blockquote`, a `dd` or an
  `li` that holds other blocks, a passage that no later start ends runs to the
  container's end unless its `until` ends it sooner (the inventory prompt
  says to give one there); a compound statement that starts in a nested item
  runs over its parent's run of items; and `paragraphs.json` does not say
  where a list ends, so two lists with nothing between them are one run of
  items. Version 2 places every passage before it ends any, so no passage needs
  an `until` to stop where the next one begins, and two passages of one
  paragraph never nest. The export carries the span, so the end is the
  extent a reader highlights, and any other end is refused under rule 2: an
  offset edited by hand, an end past the paragraph or into the next kept
  passage with no `until` that puts it there, or an end counted in UTF-16 units
  (every offset counts code points of the layer, and on a layer with
  astral-plane characters a UTF-16 count runs further). `until` stays in the
  document, so the end can be recomputed, and a second run of the tool places
  the same spans; an `until` that is not a string or is not on the layer after
  the start is refused. Under version 1 two passages of one paragraph may
  overlap: each runs to the paragraph's end unless its `until` ends it sooner.
  The end check binds a
  run whose `gate.json` names `span_end_rule` (§9) — every run `cf
  plan-locate` plans now — and no other: the `passage_ids.py` of a run planned
  before it placed an end by its `until` and then dropped the `until`, so the
  end cannot be recomputed, and such an inventory is gated, also at a harvest's
  re-gate by either gate, with its end held inside the layer and after its
  quote, as it was when it ran. A run planned before the statement cut's flip
  names version 1 or no version, so a re-gate recomputes its ends by version 1,
  or holds them only to the layer and the quote, exactly as before.
- **The statement cut** (`statement_cut_rule` 1 in `gate.json`, and 2, the
  continued cut, from the flip of inv-pilot-1's fixes on; the
  proposal's §1.1 and §3; every run planned from the proposal's flip on names
  it, and one whose `gate.json` names no `statement_cut_rule` — every run
  planned before the flip — is gated as before). A passage is one statement, cut by the rules of
  `references/vocabulary.md` §1.11 ("How the inventory cuts"), which the
  inventory prompt gives word for word. Two fields join the row, on a compound
  statement only, and there is no new scope and no new passage kind:
  `conjuncts`, absent on a simple statement (never `[]` and never `null`), else
  2 to 5 objects with exactly the keys `id` and `text`, the ids `c1`, `c2`, … in
  order with no gap, each `text` one line of at most 300 characters in the
  worker's own words with no more than twelve consecutive words of the source,
  alone or read one after the other, the claim rules
  `verify_locate.check_claims` holds (a bundle's conjuncts are exported); and `shape`, present exactly when
  `conjuncts` is, one of `cflib.vocab.COMPOUND_SHAPES` (`existence_uniqueness`
  with exactly two conjuncts, `iff`, `cases`, `conclusions`, `equations`). Only
  a `formal_math` passage carries them. `statement` stays required and is the
  passage's context: it reads on its own, naming the hypotheses and objects the
  tight span leaves to its neighbours. A stated reason is a passage of its own
  when it is a declarative statement no neighbouring passage states (PD9), and
  no passage otherwise; no field links it to the statement it supports. A lead
  that describes the subject rather than states it is `expository`, and
  `prominence: subject` — with it the article's QID on the passage's records —
  goes on at most one passage, the first that states the theorem or definition,
  which may be its theorem box; a later restatement is `central`.
  Gate rule 10 (the proposal's §3.3; §9 below) binds only a run whose
  `gate.json` names `statement_cut_rule`, which the planner writes from the
  flip on (version 3, the wide bound, from the flip of the wide bound), and
  the inventory prompt describes the cut's end of end rule 3,
  which every run planned since the flip of inv-pilot-1's fixes names (end
  rule 2 from the statement cut's flip until then; a run planned before keeps
  the prompt it rendered, version 1's paragraph before the statement cut's
  flip). It refuses,
  over the document: two passages whose spans share
  a character; a span that ends past its extent (`locate_text.extent`, with
  `compound` when the passage has `conjuncts`, so a compound statement may run
  over the items of one list, its run of `li` or `dd` items); a span that ends
  strictly inside a formula;
  `conjuncts` and `shape` that break the rules above; `conjuncts` on a passage
  not scoped `formal_math`; a passage not scoped `formal_math` whose prominence
  is not `supporting`; and two passages with the same span text. It refuses
  nothing on a passage's size: the harvest receipt counts each passage's
  sentences and displays (`locate_text.units`) and the compound statements'
  conjuncts (its `cut` block, §9). `passage_ids.py` writes
  `conjuncts` and `shape` back as it found them. Under `statement_cut_rule` 2
  (§9; it reads `span_end_rule` 3) the bound of a span's end is the loose
  continued one — a sentence that runs on may keep the paragraph that finishes
  it, whether or not the gate sees the display between — and rule 10 also
  refuses two or more `formal_math` passages of the source marked
  `prominence: subject`, naming them all: the marker goes on the one that
  states the theorem or definition, and a lead that only describes it is
  `expository`. Under `statement_cut_rule` 3 (§9; it reads `span_end_rule` 3
  too) the bound is the wide one — the loose bound read across a footnote mark,
  and into an indented line or a paragraph that opens with a formula when it
  finishes the sentence — and the rest is rule 2's.
- A passage whose `finder` is a deterministic one must start where that finder's
  passage of the task starts; a passage the enumerator found is `model`. A
  deterministic passage of the task is dropped only with a skip whose span
  covers its whole span.
- Every paragraph of the source with `formal_math` content — every
  `paragraphs.json` entry with `math: true` — overlaps a passage span or a
  `skips` span (the coverage floor). No skip holds the whole span of a passage
  the inventory keeps (rule 8: a skip drops text no passage keeps); a skip
  inside a kept passage is how a finder nested in it, a bolded definition in a
  theorem box, is dropped. What the skips
  removed is counted at harvest (§10): the receipt's gate block carries
  `skipped: {skips, chars, math_paragraphs, finder_passages, by_source}`, the
  math paragraphs and deterministic finders' passages a skip covers and no
  passage keeps.
- With L2 curated hits for the article (§5; gate rule 4, off in the blind arm),
  the inventory keeps a passage of prominence `subject` scoped `formal_math`
  (the hits are for the article's own subject, so relabelling it `central`
  does not turn the rule off) and no `subject` passage is scoped `expository`
  or `non_math`.

## 3. Locate document (`mode: locate`)

A locate task carries at most eight passages of one frozen inventory.

```json
{"…header…": "…",
 "passages": [
   {"item": "In-text #3fa91c07", "outcome": "located",
    "claims": [{"id": "c1", "text": "every finite widget has a spanning gadget"}],
    "locations": [
      {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
       "relation": "exactMatch", "rank": 1, "confidence": "high", "clauses": ["c1"],
       "note": "", "witness": "<why this declaration states the passage>"}],
    "receipt": {
      "queries": ["widget spanning gadget exists", "Widget.exists"],
      "candidates": [
        {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
         "verdict": "accepted", "reason": ""},
        {"library": "beta", "declaration": "Beta.Widget.gadget_of_finite",
         "verdict": "rejected", "reason": "states the finite case only"}]}},
   {"item": "In-text #91aa02de", "outcome": "absent", "locations": [],
    "claims": [{"id": "c1", "text": "…"}],
    "receipt": {"queries": ["…", "…", "…"], "candidates": [],
                "query_clauses": {"…": ["c1"]}}},
   {"item": "In-text #0c4d77b1", "outcome": "unresolved", "locations": [],
    "reason": "the passage's quantifier is ambiguous", "receipt": {"queries": [], "candidates": []}}]}
```

- `outcome` ∈ {`located`, `absent`, `unresolved`}. `located` needs a location
  whose relation is not `relatedMatch` (with only related ones the passage is
  absent, beside them); `absent` may carry `relatedMatch` locations only;
  `unresolved` has no locations and says why in `reason`.
- The document's passages are exactly the task's (the task manifest, §9), each
  once.
- `relation` ∈ the six locate relations; `rank` 1..5, distinct; `confidence` ∈
  {`high`, `medium`, `low`}. One location per declaration: a `(library,
  declaration)` pair appears at most once among a passage's locations (rule 6),
  since both would carry one `location_key`.
- `closeMatch` requires a non-empty `note` naming the convention difference.
- Every curated candidate handed to the task (§5) is located — by a location
  whose relation counts — or rejected with a reason in a `candidates` entry;
  the gate reads the candidates file itself (rule 4). A `relatedMatch`
  location does not discharge a curated hit: "relevant but states something
  else" is the worker's reason, written as a rejection beside it. An
  `accepted` candidate is one of its passage's locations with a relation that
  counts; accepted and not located is refused (rule 1). "Handed to the task" is the task manifest
  document's `curated: [{library, declaration}]` list intersected with the
  candidates file (a manifest cannot add a hit): the planner gives each of a
  source's curated hits to exactly one of its tasks. A manifest without the key
  hands the task every curated hit of the file.
- Every declaration a location or an `accepted` candidate names exists in
  `corpus.sqlite` at that library and the universe's commit (rule 3). No
  document carries `proof_status`, `commit` or `version` anywhere.
- An absence's receipt lists at least three distinct queries (whitespace and
  case folded) (rule 5).
- No location's `note`, and no query or candidate `reason` of an absence's
  receipt, carries more than `EXPORT_QUOTE_WORDS` consecutive words of the
  layer verbatim (rule 1): the export refuses such a passage (§6), so the gate
  applies the export's own check (`locate_records.check_note` and
  `receipt_document`) while the worker can still rewrite it. A located
  passage's receipt is not exported and is not capped.
- A located passage that says several things may carry a **coverage claim**
  (the maintainer's decision D1, `references/vocabulary.md` §1.11): its
  `hasPart` locations together state every clause of the passage, and no part
  adds a hypothesis stronger than the passage's:

  ```json
  {"item": "In-text #3fa91c07", "outcome": "located",
   "claims": [{"id": "c1", "text": "a spanning gadget exists"},
              {"id": "c2", "text": "its size divides the order"}],
   "locations": [{"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
                  "relation": "hasPart", "rank": 1, "clauses": ["c1"],
                  "step": "the existence of a spanning gadget", "…": "…"},
                 {"library": "alpha", "declaration": "Widget.card_dvd",
                  "relation": "hasPart", "rank": 2, "clauses": ["c2"],
                  "step": "the divisibility of the gadget's size", "…": "…"}],
   "coverage": {"complete": true, "parts": [1, 2],
                "note": "existence and the divisibility of its size together state it"},
   "receipt": {"…": "…"}}
  ```

  `coverage` is an object with exactly these keys (rule 1): `complete` is
  `true`; `parts` lists at least two distinct ranks (`MIN_COVERAGE_PARTS`), each
  the rank of one of this passage's `hasPart` locations; `note` is one sentence,
  non-empty, at most 2,000 characters, saying how the parts together state
  every clause, in the worker's own words — no more of the source verbatim than
  the export's quote cap (`EXPORT_QUOTE_WORDS`). A claim on an absent or
  unresolved passage is refused. The claim upgrades the passage's derived
  status only once a challenger confirms it (§4), so a passage without one is
  `partial` however many parts it has.

**The judgment rules** (gate rule 9; `references/vocabulary.md` §1.11, R1–R7
and the policy defaults PD1–PD10) bind a run whose `gate.json` names
`judgment_rules` (§9) — every run `cf plan-locate` plans now — and no other: a
document of a run planned before them is gated, harvested, tallied and exported
as it always was. A run is held to the version its `gate.json` names: version 1
is everything below but the two checks marked **(rules 2)**, which version 2
(PD5–PD9, 2026-10-20) adds. Under them:

- **The claim list.** A `located` or `absent` passage carries `claims` (optional
  on an `unresolved` one): `[{"id": "c1", "text": "<one clause>"}, …]`, every
  formal clause the passage asserts — each conclusion, each direction of an iff,
  existence and uniqueness apart, each computation of a worked example, each
  condition of a definition — written before any declaration is judged. Each
  claim is exactly `{id, text}`; ids are `c1`, `c2`, … (`cflib.vocab.CLAIM_ID`),
  distinct, at most `MAX_PASSAGE_CLAIMS` (12); `text` is one line (no line break
  of any kind, `\r` and U+2028 included) of at most `MAX_CLAIM_CHARS` (300)
  characters, a paraphrase in the worker's own words: more than
  `EXPORT_QUOTE_WORDS` consecutive words of the source is a rule 1 refusal,
  like a note's — in a claim, and in the claims read one after the other (a
  sentence split over two claims is still the source).
- **The source in every spelling.** Rule 1's cap holds a worker's free text to
  the layer as it is, to the layer without its format characters (Unicode
  category Cf: a zero-width space after every word is still the source's words)
  and to the reading copy's spelling of it: `text.txt` writes a formula as
  `$<normalized alttext>$` (§7) where the layer carries `{\displaystyle …}`, so
  a sentence copied from the reading copy with a formula in it never matches
  the layer. The gate builds that spelling from `layer.txt` and
  `formulas.json`, which it pins already. Under the judgment rules this holds
  every free text a document writes — claims, `instantiation`, `step`, notes,
  queries, reasons, a coverage note; a run planned before them is held to the
  layer alone, as the export is.
- **Clauses.** Every location whose relation counts carries `clauses`, the
  non-empty, distinct ids of the claims the declaration states; an `exactMatch`,
  `closeMatch` or `broadMatch` (`cflib.vocab.STRONG_RELATIONS`) names **every**
  claim (R4: a declaration that states some clauses is `hasPart`, with a coverage
  claim when its parts together state them all), and a `relatedMatch` names none
  (an absent or empty `clauses`). A coverage claim's parts' `clauses` together
  name every claim. **(rules 2)** No part of a coverage claim names every claim
  alone: such a part is one location with its own relation (PD6: `exactMatch`,
  `closeMatch` or `broadMatch` by R1–R2, `narrowMatch` by R3), and the claim is
  refused until it names only parts that each state some of the clauses. A
  `hasPart` that names every claim outside a coverage claim is not refused: it
  may be a weakening credited with its clause or a claim list too coarse to show
  the part, which the challenge judges.
- **Queries per clause.** A receipt may carry `query_clauses`, `{"<query>":
  ["c1", …]}`: each key one of its `queries` exactly as written, each value the
  non-empty, distinct ids of the claims that query targets, or `general`
  (`cflib.vocab.GENERAL_QUERY_TAG`) for the general theorem a worked example is
  an instance of. An **absence** carries it and tags **every** query it lists,
  and each claim — with `general`, for an `example` passage — has a **query of
  its own** among the tags: a matching of distinct queries (queries that differ
  only in case or spacing are one) to the claims, so one query tagged with
  every claim, beside untagged others, searched for one of them, and an absence
  lists at least as many distinct queries as it has claims (and `general`). The
  smoke-2 audit's false absences searched the method, the complexity or the
  theorem's name and never the formal clause. On a located passage it is
  optional and checked when given. The export's receipt sidecar (§6) is built
  from `queries` and `candidates` alone, so the tags are not exported.
- **A rejection is not a part.** An absent passage's receipt rejects no candidate
  for stating part of the passage, a case of it or a generalisation of it — a
  reason that says one of `cflib.vocab.CANDIDATE_PART_PHRASES` ("only part",
  "one clause", "not the whole", "special case", "not the worked example",
  "more general…", …) — since such a declaration is a `hasPart`, `narrowMatch` or
  `broadMatch` location (R2–R4) and the passage is located.
- **Instantiation.** A location whose relation counts, on a declaration whose
  signature takes an **instance-implicit argument at a binder position**, carries
  `instantiation`: one line (at most `MAX_JUDGMENT_LINE_CHARS`, 300 characters,
  and at least `MIN_JUDGMENT_LINE_WORDS`, 3, words with a letter: "n/a" is none;
  in the worker's own words) saying at which of the passage's objects the
  declaration applies and which instance or hypothesis makes it apply there. The
  gate sees the signature `corpus.sqlite` recorded for the declaration at the
  universe's commit (`decls.signature`, the probe's printed type, §8) and reads
  it as a telescope (`verify_locate.binder_groups`): `∀ <binders>, <rest>` gives
  each balanced group of its binder list in `()`, `{}`, `[]` or `⦃⦄`, and
  `<segment> → <rest>` gives the segment when it is one balanced group; a segment
  that is no group — an anonymous explicit argument (`Set M →`), a
  non-dependent hypothesis (`p.Prime →`) — is passed over and the scan goes on,
  so a binder after one is found; it stops at the conclusion (no arrow after a
  segment, or a `,` outside every bracket before the next arrow: an `∃`'s body).
  An instance-implicit argument is a `[…]` group (`[inst : Group G]`, `[Finite
  G]`). A bracket in the statement (`a ≡ b [MOD n]`) is at no binder position,
  and a declaration whose signature the index did not record is not held to the
  rule (the gate cannot see its binders). A setting that neither contains nor is
  contained in the passage's makes the relation `relatedMatch` (R5), and a
  special case of it `narrowMatch` (R3): that is the worker's judgment, which a
  challenger checks. `instantiation` may sit on any relation, and is checked
  wherever it does.
- **A closeMatch names its convention, and only a closeMatch.** A `closeMatch`
  location carries `convention`, one of `cflib.vocab.CLOSE_MATCH_CONVENTIONS` —
  `notation`, `encoding`, `normalisation`, `renaming`, `unfolding`, `bundling`
  (R1) — beside the `note` that says what the declaration does by it; a
  `convention` on any other relation is refused (it contradicts the relation).
  Under version 1 the list is `LEGACY_CLOSE_MATCH_CONVENTIONS`, which holds
  `junk_value` too; **(rules 2)** a junk value is no convention (PD7: a
  declaration that agrees with the passage on the passage's domain is an
  `exactMatch` when every case outside it is trivial, its note saying why, and a
  `broadMatch` when one has real mathematical content), and `junk_value` is
  refused with that rule in the message. An `exactMatch` or `closeMatch` note says none of
  `CLOSE_MATCH_GENERALITY_PHRASES` ("stronger", "strengthen…", "weaker",
  "weaken…", "more general…", "generali…", "special case", "particular case",
  "broader", "narrower", "strict derivative…", "strict differentiab…", "strictly
  differentiab…"; whole
  words, a trailing `…` any ending, so `HasStrictDerivAt` and "a strict
  inequality" are no such words): a strengthening or a generalisation is
  `broadMatch` (R2), a special case `narrowMatch` (R3), a weakening `hasPart` or
  `relatedMatch` (R5). The prompts render the list from that tuple. A challenge
  verdict that leaves a `closeMatch` names its convention too — a downgrade's
  own, a confirm's when the record names none, held to its run's list; a confirm
  that keeps the record's own convention, by naming none or naming it again,
  keeps a `junk_value` a run of version 1 wrote, since a challenge never raises
  a relation to the `exactMatch` PD7 would make it, and a confirm that names
  another convention replaces the record's and is held to the list — and the
  challenger's own note
  (the one `locate_records.downgrade_note` takes from the verdict when the
  record has none) is held to the same words; the record's own note, the
  worker's, is not.
- **A part names what the declaration alone entails.** `hasPart` and
  `narrowMatch` (`cflib.vocab.STEP_RELATIONS`) need the declaration alone to
  entail what they credit it with (R5; a lemma the proof uses, one that needs a
  second declaration, a neighbouring passage's fact is `relatedMatch`), and
  every such location carries `step`, one line (as `instantiation`) saying what
  the declaration alone entails of this passage: on a proof the displayed step,
  on any other passage — whatever its `passage_kind` — the clause, as the
  declaration states it. A `step` on any other relation is refused. A challenge
  verdict that leaves a `hasPart` or `narrowMatch` names it too: a downgrade's
  own `step`, a confirm's when the record has none.
- **A note that admits a gap.** A strong location whose `note` or
  `instantiation` says it leaves a clause unstated — a phrase of
  `cflib.vocab.CLAUSE_GAP_PHRASES` ("does not state", "not stated", "not part
  of", "rather than asserting", "only proves", "only the existence", "one
  direction only", "converse is not", "without uniqueness", "does not cover",
  "omits", "leaves out", …; whole words, case-folded, a contraction spelled out)
  — is refused: its own words make it `hasPart`. A phrase that is negated ("not
  only the existence"), whose subject is the passage ("the passage does not
  cover n = 0", "not part of the passage": a junk-value note says what the
  passage leaves out) or that sits in a relative clause on an "unlike …"
  clause's object is set aside; the message says to reword a note it misreads,
  describing what the declaration does. The challenge plan refutes such a record
  by default in a run planned before the rules too (§9).

None of these fields is exported: a record carries what it always did, and the
record set's format is unchanged (§6). Every structural check rests on the claim
list the worker writes: a list that drops a clause, with an `exactMatch` naming
every claim on it, passes the gate, and only the challenge, which writes its own
list, re-checks it.

**Under judgment rules 3** (`RULES_STATEMENT_CUT`, the statement cut:
`docs/proposals/statement-cut.md` §1.3 and §5), which bind a run whose `gate.json`
names `judgment_rules: 3` — every run planned from the proposal's flip on (§9) — the
inventory owns the clause list, and everything above is checked against it:

```json
{"item": "In-text #0000c0de", "outcome": "located",
 "locations": [
   {"library": "alpha", "declaration": "Widget.exists_spanning_gadget", "relation": "hasPart",
    "rank": 1, "confidence": "high", "clauses": ["c1"], "conjunct_relation": "broadMatch",
    "step": "a spanning gadget for every widget, finite or not",
    "instantiation": "…", "tests": {"r2": "…", "r3": "…"}, "note": "", "witness": "…"},
   {"library": "alpha", "declaration": "Widget.spanning_gadget_unique", "relation": "hasPart",
    "rank": 2, "confidence": "high", "clauses": ["c2"], "conjunct_relation": "exactMatch",
    "step": "two spanning gadgets of a finite widget coincide",
    "instantiation": "…", "tests": {"r2": "…", "r3": "…"}, "note": "", "witness": "…"}],
 "coverage": {"complete": true, "parts": [1, 2],
              "note": "existence holds for every widget and uniqueness for finite ones"},
 "receipt": {"queries": ["…"], "candidates": [], "query_clauses": {"…": ["c1"]}}}
```

- **No claim list (J1).** `claims` is refused on every passage. The clause ids are
  the task manifest passage's conjunct ids (`conjuncts`, which the planner copies
  from the frozen inventory with its `statement` and `shape`, §9), or `["c1"]`
  (`SIMPLE_STATEMENT_CLAUSE`) for a simple statement; `clauses` and
  `query_clauses` name them as they name claims above — a strong relation every
  id, a `relatedMatch` none. A manifest whose conjuncts are not `c1` … `cN` is an
  inconsistent frozen inventory (exit 3).
- **A part's relation (J2).** A `hasPart` of a compound statement names the
  conjuncts it states and gives `conjunct_relation`, one of `CONJUNCT_RELATIONS`
  (`exactMatch`, `closeMatch`, `broadMatch`, `narrowMatch`): its relation to the
  conjunction of those conjuncts by R1–R3 and PD1–PD10. A `closeMatch` part names
  its `convention` from the rules-2 list (no `junk_value`), and any other part
  names none; `step` stays required. A `hasPart` of a simple statement names `c1`
  and carries no `conjunct_relation`; nothing else carries one.
- **A bundle (J3).** `coverage` keeps its object and rule 1's checks, and is
  refused on a simple statement. Its parts' `clauses` are pairwise disjoint,
  together name every conjunct, and none names them all (PD6); every part's
  `conjunct_relation` is one of `BUNDLE_RELATIONS` (`exactMatch`, `closeMatch`,
  `broadMatch`): a part that states only a special case of its conjuncts leaves
  them unstated. The bundle's relation is its weakest part's, computed by the
  outcomes and never written. **Fix 4a**: a compound statement with no coverage
  claim whose `hasPart` locations of a strong `conjunct_relation` partition the
  conjuncts (at least two, pairwise disjoint, every conjunct, none naming all) is
  refused — write the bundle, or give the part that falls short the relation it
  bears.
- **A strong location (fixes 2a–2c).** An `exactMatch`, `closeMatch` or
  `broadMatch` writes both instantiation tests, `tests: {"r2": <how the passage
  follows from the declaration>, "r3": <why the declaration, at the passage's
  objects, needs nothing more>}` (`TWO_WAY_TEST_KEYS`, each a one-line judgment
  field), and an `instantiation` whatever its binders. **The ℕ-for-ℤ tripwire**:
  when the passage's `statement` or a conjunct says "integer", "integers" or `ℤ`
  (`NAT_FOR_INT_INTEGER_WORDS`; the symbol anywhere, as in "nℤ")
  and every number-typed binder group of the declaration's signature
  (`verify_locate.numeric_binders`: `(n : ℕ)`, `{m k : ℤ}` …, as `binder_groups`
  reads the telescope) is `ℕ`, the strong relation is refused unless its `note`
  names `ℕ` or the naturals (`NAT_FOR_INT_NATURAL_WORDS`; a statement over ℕ for
  one over ℤ is `narrowMatch`, R3); a declaration with no recorded signature, or
  no number-typed binder, is not held to it. An exact or close note is held to
  `CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT`, the list above with "table",
  "for all primes", "every point" and "differentiable at". `tests` on another
  relation is checked when given. **A part is held by its `conjunct_relation`**:
  a `hasPart` of a compound statement whose `conjunct_relation` is `exactMatch`,
  `closeMatch` or `broadMatch` is a strong location here — its `tests`, its
  `instantiation`, the tripwire, and the generality words when it is exact or
  close — since a bundle takes its weakest part's relation; a `narrowMatch` part
  owes none of them. The gap check stays the location's own: a part states some
  conjuncts and not the rest by definition.
- **An absence's special case (fix 5b).** An absence's receipt names the nearest
  special-case candidate it found and why it fails R3 or PD4:
  `receipt.special_case: {library, declaration, reason}` — the declaration in
  `corpus.sqlite` at that library (rule 3) — or `{reason}` when there is none,
  `reason` a one-line judgment field.
- **A cut flag (J4).** An `unresolved` passage may carry `cut_flag`, one of
  `CUT_FLAGS` (`not_a_statement`, `several_statements`): what a worker who cannot
  re-cut a passage says of its cut. It is refused on any other outcome.

## 4. Challenge document (`mode: locate-challenge`)

```json
{"…header…": "…",
 "verdicts": [
   {"item": "In-text #3fa91c07", "target": "location",
    "location_key": "<run-free sha>", "action": "confirm", "clauses": ["c1"],
    "witness": "<quoted signature fragment and why it states the passage>"},
   {"item": "In-text #3fa91c07", "target": "location",
    "location_key": "<sha>", "action": "downgrade", "relation": "closeMatch",
    "clauses": ["c1"], "convention": "encoding",
    "note": "the declaration writes the gadget as a finite set", "witness": "…"},
   {"item": "In-text #3fa91c07", "target": "location",
    "location_key": "<sha>", "action": "downgrade", "relation": "hasPart",
    "clauses": ["c1"], "step": "the existence of a spanning gadget for each widget",
    "witness": "…"},
   {"item": "In-text #91aa02de", "target": "absence", "action": "overturn",
    "locations": [{"library": "beta", "declaration": "…", "relation": "exactMatch",
                   "rank": 1, "confidence": "high", "clauses": ["c1"], "note": "",
                   "witness": "…"}],
    "witness": "found with a type search the worker did not run"},
   {"item": "In-text #77e01b2c", "target": "absence", "action": "uphold",
    "queries": ["gadget cover finite", "spanning gadget card", "Widget.cover"],
    "query_clauses": {"gadget cover finite": ["c1"], "spanning gadget card": ["c2"],
                      "Widget.cover": ["c1", "c2"]},
    "witness": "…"},
   {"item": "In-text #5be3a910", "target": "coverage", "action": "confirm",
    "part_clauses": {"<part 1's location_key>": ["c1"], "<part 2's>": ["c2"]},
    "witness": "…"},
   {"item": "In-text #0c4d77b1", "target": "scope", "action": "uphold", "witness": "…"}],
 "claims": {"In-text #3fa91c07": [{"id": "c1", "text": "…"}],
            "In-text #91aa02de": [{"id": "c1", "text": "…"}],
            "In-text #77e01b2c": [{"id": "c1", "text": "…"}, {"id": "c2", "text": "…"}],
            "In-text #5be3a910": [{"id": "c1", "text": "…"}, {"id": "c2", "text": "…"}]}}
```

Under the judgment rules (§3) a challenge document carries `claims`, `{<item>:
[{id, text}, …]}`: the challenger's own claim list of each passage it judges a
location, a coverage claim, an absence or an unresolved outcome of — **whatever
the verdict**, a refute, a downgrade to `relatedMatch` and an uphold as much as
a confirm — written before it judges (the FM7 rule, `references/vocabulary.md`
§1.11), each list checked as a locate document's. Then:

- A `confirm` of a location whose relation counts, and a `downgrade` to one,
  names the ids it states in `clauses`, every one for a strong relation and none
  for a `downgrade` to `relatedMatch`; an overturn's locations carry them as a
  locate document's do, against the same list. A verdict that keeps or leaves a
  strong relation rests on a claim list at least as long as the worker's
  `claims` for the passage (the task manifest's): a challenger splits a
  worker's clause, never drops one.
- A verdict that leaves a `hasPart` or `narrowMatch` names its `step`, and one
  that leaves a `closeMatch` its `convention`, as §3 has them (a downgrade's
  own, a confirm's when the record names none); a `step` or `convention` on a
  verdict that leaves any other relation is refused, and a blank one is none.
- A verdict that keeps a counting relation on a declaration whose signature
  takes an instance argument, when the record names no `instantiation` (every
  record of a run planned before the rules), carries the challenger's own.
- A `downgrade` whose own note — the verdict's `note`, or for a `closeMatch`
  with no note of its own the note the outcomes will take from the verdict —
  or own `instantiation` admits a gap on a strong relation is refused, and an
  exact or close one's note is held to the generality words. The rule reads the
  challenger's words only: a record's own `note` (the task manifest's, §9) is
  its worker's, and when it admits a gap the challenge plan refutes the record
  by default, for the challenger to judge, but never forces its verdict.
- A coverage `confirm` names, per part, the challenger's claim ids it states:
  `part_clauses`, `{<part's location_key>: [c1, …]}`, one entry for each part of
  the record and no other, each a non-empty list of distinct ids, together
  naming every claim (decision D1's verdict, which raises the passage's status,
  is checked against the challenger's own clauses as a location's is).
  **(rules 2)** No part's list names every claim of the challenger's own: by
  its own clauses such a part is one location, not a part (PD6), and the claim
  is refuted, its part's record judged on its own. A challenge never raises
  that part's `hasPart`, so the passage is left `partial` until it is located
  again; of a locate run planned under version 1, whose coverage claim raised
  it, the challenge so lowers the passage's status
  (`references/vocabulary.md` §1.11, PD6).
- An `uphold` of an absence or an unresolved outcome lists the challenger's own
  search: `queries` (at least `min_absence_queries` distinct, in its own words)
  and `query_clauses`, every query tagged and each claim — with `general` for an
  `example` passage — with a query of its own, as an absence's receipt (§3).

A `claims` entry for a passage the task does not judge is refused.

**Under judgment rules 3** (the statement cut, `docs/proposals/statement-cut.md`
§1.4 and §5) the challenger writes no claim list either: `claims` is refused, and
every verdict's `clauses`, `query_clauses` and `unstated` name the record's clause
ids — its passage's conjunct ids, which the task manifest's record carries
(`conjuncts`, with `statement` and `shape`, §9), or `c1`. The rule that a strong
relation rests on a list at least as long as the worker's is void: there is one
list. Then:

```json
{"…header…": "…",
 "verdicts": [
   {"item": "In-text #0000c0de", "target": "coverage", "action": "confirm",
    "part_relations": {"<part 1's location_key>": "broadMatch",
                       "<part 2's location_key>": "exactMatch"}, "witness": "…"},
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
    "action": "refute", "witness": "…"}],
 "fills": {"In-text #0000d00d": {"outcome": "absent",
                                 "receipt": {"queries": ["…", "…", "…"], "candidates": [],
                                             "query_clauses": {"…": ["c1"]},
                                             "special_case": {"reason": "…"}}}}}
```

- A coverage **confirm** gives `part_relations`, one entry for each part of the
  record and no other, each one of `BUNDLE_RELATIONS` and no stronger
  (`RELATION_STRENGTH`) than that part's own `conjunct_relation` (the record's
  `parts` carry each part's `clauses` and `conjunct_relation`), nor than a
  `conjunct_relation` the same document gives the part's own record: a challenge
  never raises a relation. `part_clauses` is refused. A part with no
  `conjunct_relation` in the manifest cannot be checked (exit 3). PD6, which
  rules 2 held through `part_clauses`, is held through the same document's
  verdicts on the parts: a confirm beside a verdict whose `clauses` name every
  conjunct of the passage is refused, since that part is one location, no part.
- A coverage **refute** names in `unstated` the conjunct ids, non-empty and
  distinct, that no part states, and gives `part_reasons`, one line for every
  part and no other, saying why no R1 convention reaches its conjunct (fix 1a;
  "the parts state it after unfolding" is a confirm). A refute that names a part's
  own conjunct thus says why that part fails (fix 1b).
- A verdict that keeps or leaves a strong relation runs the challenger's own two
  tests, `tests: {r2, r3}` (fix 2d), and passes the ℕ-for-ℤ tripwire (§3),
  answered by its note or the record's; the generality words are the longer list.
  A verdict that leaves a `hasPart` of a compound statement may give a
  `conjunct_relation`, never stronger than the record's; no other verdict gives
  one. Such a verdict is held by the `conjunct_relation` it leaves the part (its
  own, else the record's on a confirm): a strong one is a strong relation kept,
  with its `tests`, the tripwire and the generality words.
- An `uphold` of an absence or an unresolved outcome gives `special_case` beside
  its queries (fix 5b), as an absence's receipt does.
- **Fills (anomalies A1, A2).** A document whose verdicts leave a `located`
  passage with no location whose relation counts — its judged location records (a
  confirm keeps the relation, a downgrade leaves the one it names, a refute
  removes it) and the passage's other locations of the run (`passage_locations`,
  which no task judges) together — gives `fills[<item>]`: `{outcome: absent,
  receipt}`, held to an absence's checks (at least `min_absence_queries` distinct
  queries, a query of its own per clause, no rejection of a part, the special
  case, no `accepted` candidate — an accepted candidate is one of the passage's
  counting locations, and an absent fill leaves it none, as on a locate absence
  (rule 1) — the export's receipt), or `{outcome: located, locations}`, held to an
  overturn's. `fills` on any other item is refused.
- **Conditional records (anomaly A3).** A record the challenge plan marks
  `conditional: "prominence"` — a sampled `supporting` passage's unrequired
  location, absence or unresolved record, in the task of its prominence record —
  is judged exactly when the same document overturns that passage's prominence to
  `subject` or `central`: then every one is owed (a refuted-by-default or hunted
  record); without such an overturn none is owed, and a verdict on one is refused
  (rule 1).

- `target` ∈ {`location`, `absence`, `unresolved`, `scope`, `prominence`,
  `coverage`}.
- `action` ∈ {`confirm`, `downgrade`, `refute`, `overturn`, `uphold`}:
  `confirm`/`uphold` keep the record (`challenge: survived`); `downgrade`
  changes a relation, `refute` removes a location, `overturn` replaces an
  absence or unresolved outcome (or a scope) with the given locations — all
  three give `challenge: downgraded` on the affected records, and the passage's
  challenge is `downgraded`. The locations an overturn adds are the
  challenger's own witnessed, gated findings: each carries `survived`. The
  locations a refute leaves are ranked 1..n again, in their order.
- The actions a target admits: `location` — `confirm`, `downgrade` (naming a
  `relation` weaker than the record's own, in the order `exactMatch` >
  `closeMatch` > `broadMatch` > `narrowMatch` = `hasPart` > `relatedMatch`;
  the task manifest's record carries that relation), `refute`; `absence` and `unresolved` — `uphold`,
  `overturn` (with `locations`, at least one whose relation counts); `scope` —
  `uphold`, `overturn` (with `locations`, possibly empty); `prominence` —
  `uphold`, `overturn` (naming the new `prominence`, which differs from the
  record's own; the task manifest's record carries that prominence, and an
  overturn naming it is refused, rule 1, since it would change nothing yet mark
  the passage `downgraded`);
  `coverage` — `confirm`
  (the claim `survived`, and with it each part whose own record no verdict
  judged: a `not_required` or `sampled_out` part is `survived`, a judged one
  keeps its verdict, and a part a prominence overturn in the same run reopened
  stays `challenge_pending`, §10) or `refute` (the claim is removed and its
  parts stay `hasPart` locations with the values they had; the passage's
  challenge is `downgraded`). A coverage
  verdict names its record by `item` alone, like every passage-level one.
- Every coverage claim is a record of the challenge, whatever the passage's
  prominence, refuted by default: it upgrades a status. Its record in the task
  manifest carries `parts` — `{location_key, library, declaration, relation,
  rank}` for each — and the claim's `note`. A document that confirms a claim
  and refutes or downgrades one of the parts it names is refused (rule 1): the
  claim would name a location that is no longer a `hasPart` one.
- Every `subject` passage's prominence is a record of the challenge, refuted by
  default, since its records carry the article's QID (§6): `uphold` keeps the
  label, and `overturn` to `central` or `supporting` takes the QID off every
  record of the passage. A `supporting` passage's prominence is sampled, and an
  overturn of it to `subject` gives the QID to the passage's judged records.
- A `downgrade` to `closeMatch` may give `note`, the convention difference the
  record will carry; without one, a sentence of its `witness` (at most 300
  characters) becomes the note, since an exported `closeMatch` always names one
  (§6): the first sentence that carries no more of the source verbatim than the
  export's quote cap (`EXPORT_QUOTE_WORDS`), checked against the source's layer
  — a witness that opens by quoting the passage lends its next sentence — where
  a sentence ends at `.`, `!` or `?` before a space but not after an
  abbreviation (`i.e.`, `e.g.`, `cf.` …). When every sentence quotes past the
  cap the first is taken, and the export would refuse the passage by name, so
  the gate refuses the verdict (rule 1): it computes the same note with the
  export's own `locate_records.downgrade_note` and checks it with
  `check_note`. A downgrade to another relation exports no note, and its
  `note` is not capped.
- A challenge task's records are its task manifest's, and each gets exactly one
  verdict (rule 1); overturn locations are checked like a locate document's.
  A verdict names its record by string `item` (and string `location_key` for a
  location).
- A record of the run not named by any challenge task is `not_required` or
  `sampled_out`, as the challenge plan said — `challenge_pending` when a
  prominence overturn to `subject` or `central` put it under a rule that
  requires it (§10).

## 5. Candidates file (L2)

Read-only on the run host at `candidates/<source-id>.json` (in the workspace,
`sourcesets/<id>/candidates/<universe>/<source-id>.json` for candidates retrieved
from the full database, and `…/candidates/<universe>/blind/` or
`…/candidates/<universe>/planted-<sha16>/` for the G1-blind and a planted tools
database, §9 — one directory per database kind, so one arm never overwrites
another's files):

```json
{"schema": "cf-locate-candidates/1",
 "source_id": "enwiki-1001-2002", "source_sha": "<html_sha256>",
 "universe": "u1", "universe_sha256": "<hex>",
 "corpus_db": {"kind": "full", "sha256": "<hex>"},
 "article_qid": "Q99999001", "article_title": "Widget lemma",
 "terms": ["Widget lemma", "widget lemma theorem"], "blind": false,
 "curated": [
   {"library": "alpha", "declaration": "…",
    "via": "wikidata_tag", "evidence": "@[wikidata Q99999001] at Alpha/Widget.lean:612"}],
 "lexical": [
   {"library": "beta", "declaration": "…", "score": 12.4, "matched": "title"}]}
```

An inventory or locate plan pins each candidates file it read by the sha256 of
its bytes, in `gate.json` `candidates_sha256` (§9), and the gate refuses
(exit 3) one that is not those bytes: rule 4 reads the curated hits from this
file, so a file swapped or edited after planning would change what it asks.

`via` ∈ `cflib.vocab.CANDIDATE_VIA`. `terms` are the title and the lead's bold
terms (`bold_definition` finders whose `section` is the lead's `[]`), which
lexical retrieval — the host tool's own search, `corpus.query.search`, every
word first and any word only to fill up — queried; `matched` (`title` or
`bold_term`) names which. `lexical` is in the search's own order: a declaration
a term names as an identifier first (§8, the host tools), then by `score`. A bold term is a term only when it has at least three
letters, is not only a paragraph label (`cflib.vocab.is_bold_label`: "Proof.",
"Example:") and is not only stop words (`candidates.is_lexical_term`); a bold
"R" or "1'" is never queried. `corpus_db` is the database the lexical hits were
retrieved from (§9's tools database): `full`, `blind` or `planted`, with its sha. Only
`curated` hits bind gate rule 4. In the G1-blind arm (proposal §8.1) the file is
retrieved from `corpus.blind.sqlite`, no curated join is computed (`curated`
and `dropped` are empty, `article_qid` is null, nothing filters the lexical
hits) and it says `"blind": true`.

`cf locate candidates` writes it (`scripts/locate/candidates.py`). Every curated
declaration is checked in `corpus.sqlite`'s exact table at the universe's commit,
and one that is not there, or whose library is not in the universe, is listed in
`"dropped": [{library, declaration, via, evidence, reason}]` instead of
`curated` — a phantom the gate would refuse a worker for not locating is never
kept. Its curated inputs are the operator's files, read as data: a QID-keyed
`1000.yaml` (`--curated-1000`; a suffixed key such as `Q180345X` is a further
theorem under the base QID, and a key that is no QID is refused), a numbered
`100.yaml` matched by an entry's own `wikidata` or by normalised title
(`--curated-100`), both naming `--curated-library` (default: the universe's
Mathlib library, `corpus.universe.Universe.mathlib_library`), and a
library-metadata file of main results (`--library-metadata`). A title join keys
on the article's TITLE only (a bold term may be another article's subject),
with apostrophe and dash look-alikes folded, and an entry that names another
QID than the article's is dropped with the reason `the entry names another QID
(<qid>)` rather than joined. A docstring URL joins on the canonical page URL
`corpus.ingest.wikipedia_urls` writes, so a mobile link joins too; the article's
side is the same reader over the ways its URL is written: the exact title,
percent-encoded and decoded back, so a title with a `?`, a `#` or a quote mark
meets the docstring's `%3F`, `%23` or `%22`, and MediaWiki's own page URL, which
leaves `!`, `*`, `,`, `;` and `:` literal, so a title ending in one meets a
link copied from the page, whose last mark the reader strips as punctuation.
A title ending in `?` is also read without it, since the reader ends a link
typed with that `?` raw at the `?`, as a browser does; either trade-off also
joins the article named by the same words less the mark. A raw `?` inside a
title is not read that way: what it leaves (`Widget` of `Widget? (film)`) is
another article's whole name, so such a link joins only that article.
`--blind` reads `corpus.blind.sqlite` and `--tools-corpus-db <file>` a planted
database. The curated joins and their checks are always the full
`corpus.sqlite`'s, so a `dropped` reason is true of the universe; a planted file
then withholds every hit its own database lacks — from `curated`, `dropped` and
the QID refusals alike, by name and without a trace in the file — because the
file reaches the P run's workers and a removed declaration is what the P tier
measures citations of from memory. Only their count is reported, on the console
and as `withheld` in the sourceset's `state.json`, which records each run under
`candidates.<universe>` (or `candidates.<universe>/<kind dir>`), with `partial`
and `written` when `--source` narrowed it:

```json
{"libraries": {"alpha": [{"wikidata": "Q99999001", "title": "Widget lemma",
                          "declarations": ["Widget.exists_spanning_gadget"]}]}}
```

## 6. Sealed record set (export)

```
<set>/SEAL.json        {"schema": "cf-locate-recordset/1", "set_id", "campaign",
                        "universe": {"id", "universe_sha256"},
                        "sourceset": {"id", "manifest_sha256"}, "skill_commit",
                        "created", "byte_budget", "files": {"<relative path>": "<sha256>"}}
<set>/universe.json    {"id", "universe_sha256", "frozen", "libraries": [...], "excluded": [...]}
<set>/sources.jsonl    per source: source_id, pageid, revision, title, wikidata, html_sha256,
                       layer_sha256, renderer, assessment
<set>/records/<source-id>.jsonl   Literature Links 2.0 records (proposal §2.7)
<set>/receipts/<sha256>.json      search receipts referenced from absence records
```

**The format is unchanged by the judgment rules** (§3, §4): their fields —
a passage's `claims`, a location's `clauses` and the rest — live in the locate
and challenge documents and in the outcomes (§10) only. No record, receipt or
seal carries one, since the consumer's strict builder refuses a key it does not
know, and a run planned under the rules exports the same shapes as one planned
before them.

**The record set of status rule 2** (`cf-locate-recordset/2`;
`docs/proposals/statement-cut.md` §2.4). A locate run whose `run.yaml` names
`locate.status_rule: 2` — from the statement cut's flip on, exactly the runs of
judgment rules 3 — exports `/2`; every other run exports `cf-locate-recordset/1`
byte for byte as below. `/2` is `/1` with these differences, and no other:

- `SEAL.json` carries `/1`'s keys and one more, `status_rule` (`2`), the version of
  the derivation its records' statuses were derived by (`export.SEAL_KEYS_BUNDLE`).
- Every record's locate extension carries `passage_key` — `sha256("<pageid>\n<span_sha256>")`,
  the subject end's `pageid` and `span_sha256` — and a location record's also its
  `pair_key`, `sha256("<passage_key>\n<repository>\n<declaration>")`, the
  repository the lean end's `uri`, beside `location_key` (`cflib.hashing`, restated
  in `records.passage_key` and `records.pair_key`). Neither holds a revision, a
  commit, a run or an item, so a verdict keyed by `pair_key` survives a new
  revision, a library commit and every re-cut that leaves its passage's text alone.
- A coverage claim is a **bundle** and rides on every record of its passage as
  `{"complete": true, "parts": [<location_key>, …], "challenge", "relation",
  "conjuncts": [{"id", "text", "part": <location_key>}], "bundle_key"}`
  (`cflib.vocab.COVERAGE_RECORD_KEYS_BUNDLE`, `records.bundle_ext`): `relation` the
  bundle's, its weakest part's (one of `exactMatch`, `closeMatch`, `broadMatch`);
  the conjuncts the frozen inventory's, in id order, each beside the one part whose
  `clauses` name it, every part stating at least one, each text held to the
  export's free-text cap (`locate_records.check_note`'s rule); and `bundle_key` the
  sha256 of the passage key and the parts' pair keys, sorted, one per line. A
  consumer shows a bundle's declarations each beside its conjunct, and keys a vote
  on it, from the record set alone. A passage whose bundle its records cannot
  carry — no conjuncts, a conjunct no part or two parts state, a part stating none,
  a relation outside the three — is refused by name.
- The status every record carries is `derive_status(…, status_rule=2)` over that
  same claim: a survived bundle is one more location of its own relation, not one
  more `exactMatch`, so a definition, construction or notation passage whose bundle
  is `broadMatch` is `partial` (`references/vocabulary.md` §1.11).

A target directory may hold a `/1` or a `/2` set: a re-export replaces either. The
self-evidencing format the decision log once planned as `/2` takes the next number,
`/3`, when it is built.

`SEAL.json` lists every other file with its sha256; a consumer refuses a set
whose files do not match. Its keys are exactly these, every one always present,
and no other (`export.SEAL_KEYS`; a consumer refuses a seal with any other key,
so the runs and the manifest version are the export report's, not the seal's):

- `set_id` — the `--set-id` the set was exported under: a **token**, letters,
  digits and `._:-`, at most 128, starting with a letter or digit
  (`cflib.vocab.RECORDSET_TOKEN`, the consumer's own token grammar). Any other is
  refused before a file is written.
- `campaign` — **never null**, and a token of one grammar: the locate run's
  `run.yaml` `locate.campaign` (which `cf campaign plan` stamps into a shard), a
  campaign id of letters, digits and `._:-` (at most 128, never starting `run:`);
  or, for a run in no campaign, `run:<locate run>`, followed by `+<challenge
  run>` when one challenge run applies (`run:loc-1+chl-1`), each run id letters,
  digits and `._-` (at most 64; `cflib.vocab.RUN_TOKEN_ID`). A run token names
  **at most one** challenge run: a run in no campaign exported with two is
  refused, as is any id the grammar does not admit, before a file is written.
  `cf plan-locate` refuses a `--run-id` or `--challenge-of` id outside the
  run-id grammar, and `cf campaign plan` a campaign id longer than 128, so the
  refusal comes before the run, which cannot be renamed once it has run.
- `universe` — `{"id", "universe_sha256"}`, the key `universe.json` and every
  record use.
- `sourceset` — `{"id", "manifest_sha256"}`, the manifest the run pinned; the
  id is a token of `set_id`'s grammar. `cf sourceset create` refuses a new id
  outside it, where the id is chosen, and the export refuses a run pinned to an
  older one, before a file is written.
- `skill_commit` — the exporting checkout's 40-hex HEAD; the export refuses to
  seal from a checkout with none.
- `created` — the newest of those runs' harvest times (ISO 8601 UTC, `…Z`), so
  the set is a function of its inputs and a re-export is byte-identical.
- `byte_budget` — the per-source budget that applied, `0` for none (below).

A receipt's name, the absence record's `receipt.sha256` and the seal's entry are
one value: the sha256 of the receipt file's exact bytes. The record's
`receipt.queries` and `receipt.candidates` are the lengths of that file's
`queries` and `candidates` lists, which a consumer checks; a query a document
repeats exactly (the gate counts distinct queries, so it admits one) is listed
once, in its first place.

`universe.json` is the universe as proposal §2.3 writes it: `id`,
`universe_sha256`, `frozen` (the `YYYY-MM-DD` it was frozen; the export refuses a
universe without one), `libraries` — a **list** sorted by id, each `{"id",
"brain_lib", "repo"` (§8's repository grammar)`, "commit"` (40 hex)`, "toolchain", "roots": [...], "licence"`
(`{"spdx"}`, never null: the export refuses a universe with a library that records
no licence, naming it and the `cf corpus add … --licence <SPDX id>` that records
it)`, "display_name"}`, where `display_name` is the library's
`brain_lib`, the one name the universe gives it, when that is a display name the
consumer shows (printable ASCII letters, digits, spaces and `,.()/+:'&_-` or `·`,
at most 100: `export.DISPLAY_NAME_RE`), and null otherwise (a universe written
before the token grammar of §8 may carry a `brain_lib` outside it, and a consumer
refuses a set whose display name could read as another library's) — and
`excluded`, `[{"id",
"reason"}]`. `brain_lib`, `toolchain` and each root are at most 300 characters,
a reason at most 1000 (§8, where they are refused). Nothing the universe does
not define (no index paths, no host instructions).

What every record carries beyond §2.7's example:

- The subject end's `extensions[‹locate›]` carries the passage's whole `span`
  — `[start, end]`, its half-open code-point offsets on the `web-html/3` layer
  (two integers, `0 <= start < end`), never its text — and the `layer_sha256`
  of the layer they index (64 lowercase hex, the source's own in
  `sources.jsonl`), beside `span_sha256`, the sha256 of those characters. A
  consumer holding that layer (the producer's, when its `layer_sha256` is this
  one) highlights the whole passage, not only the capped quote, whose `exact`
  is the span's head. The export writes both on every record; a consumer
  treats the pair as optional (a set exported before them still builds) but
  never one without the other. The gate's rule-1 preview builds the same pair
  from `gate.json`'s pin. `records.passage_span` refuses a span that is not two
  integers with `0 <= start < end`, and the export one that leaves the layer.

  ```json
  {"pageid": 1001, "html_sha256": "<hex>", "layer_sha256": "<hex>",
   "renderer": "web-html/3", "span": [10234, 10612], "span_sha256": "<hex>",
   "licence": "CC-BY-SA-4.0"}
  ```
- A `subject` passage's records carry the article's QID — the source's
  `wikidata` in `sources.jsonl` — as the subject end's `identifiers.wikidata`
  (`"identifiers": {"wikidata": "Q99999001"}`, beside `citation`, outside the
  locate extension), and no other passage's records do: the prominence is the
  one the record's extension carries, after the challenge, so a prominence
  overturn to `subject` adds it and one away removes it (the maintainer's
  decision B5, proposal §2.2; `records.article_qid`). The challenge plan
  refutes every `subject` prominence by default (§9), so under a plan written
  since that rule each QID rests on a label a challenger judged. An inventory
  marks at most one subject passage (the prompt's subject rule; gate rule 10
  (h) under `statement_cut_rule` 2 or 3), but an older run or a prominence overturn
  to `subject` may give an article several, each judged on its own. A record whose
  `challenge` is `challenge_pending` carries none: an overturn to `subject`
  reopened it and no challenger judged it under that rule (§10), while the
  passage's judged records carry the QID. The QID
  names the subject end's article, not the record's declaration: the record
  says only that its declaration stands in its `relation` to a passage about
  that article's subject (the second smoke run's 31 QID records spanned seven
  relations, `hasPart`, `relatedMatch` and `noMatch` among them), so a consumer
  that folds a QID into a concept-level edge reads the relation first. A source
  with no QID gives none, and one whose `wikidata` is no QID (`Q[1-9][0-9]*`)
  is refused per passage. The QID is the producer's claim: the consumer checks
  its enwiki sitelink against the article before it draws a concept-level edge
  (proposal §11.3). The gate's rule-1 preview writes none (its passages carry
  no prominence after a challenge, and the checker admits either form).
- `extensions[‹locate›].section` — the passage's heading path (a list of
  non-empty strings; `[]` is the lead) — and `.prominence` (`subject`,
  `central`, `supporting`), after the challenge (a prominence overturn moves
  it), so a consumer can single out the subject passage without a passage
  table. A passage the outcomes give neither is refused.
- `extensions[‹locate›].coverage` — on every record of a passage whose
  coverage claim stands after the challenge (decision D1): `{"complete": true,
  "parts": [<location_key>, …], "challenge": <value>}`, each part the
  `location_key` of one of the passage's `hasPart` records; a refuted claim is
  not exported, and a claim whose parts are not the passage's `hasPart`
  locations, or whose challenge is none of the record challenge values, is
  refused (`records.coverage_problem`). The claim carries those three keys
  and no others (`records.coverage_ext`; the note is not exported), and the
  outcomes derive the passage's `status` from that same projection.
- A location's lean end names its declaration's `module` (in `location`) and
  carries `library`, `path` (the repository-relative `.lean` file) and `line`
  (>= 1) in its extension, **always**: a location whose index row lacks any of
  them (the index admits a module with no path) is refused, by name, per
  passage. Only `end_line` is optional, carried **when the index has one**: a
  library's own published index may record `line` only, and its ends then carry
  no `end_line` (never a null or a guessed one). A consumer treats `end_line` as
  optional and renders `line N`.
- A `closeMatch` always carries a non-empty `note`, a downgraded one too: a
  challenge downgrade to `closeMatch` takes the verdict's `note`, else the first
  sentence of its witness the quote cap admits (§4); a `closeMatch` with no note
  is refused.
- An absence keeps one lean end per repository the universe searched, at its
  commit, each **minimal**: `{"type": "lean", "uri": <repo>, "version": <40-hex
  commit>}` and nothing else (no `role`, no extension; `universe.json` says which
  libraries a repository is), about half the bytes of the earlier form at 48
  libraries. Two libraries split from one repository at one commit (lean-core's
  `Init` and `Std`, proposal §2.3) are one search target and one end, so a
  consumer checks the ends against the universe's distinct `(repo, commit)`
  pairs, each once (`records.absence_ends`; the first library by id places it).

`cf export literature-links --run <locate run> --challenge-run <run> --set-id
<id>` writes it (`scripts/locate/export.py`, the records from
`scripts/locate/records.py`) under `exports/<id>/` (or `--out DIR`) and what it
refused, by passage, to `reports/export.<id>.json` (which also names the runs,
`run_id`, `challenge_runs` and `inventory_run`, the `campaign` token and the
`sourceset` with its manifest `version`, and lists as `brain_lib_not_token`,
`[{"library", "brain_lib", "from_id"}]` by library id, every library whose
`brain_lib` as `universe.json` writes it — its own, else its id, `from_id` true, as
for `lean-core` — is no library token of §8: those are exported, not refused, and
the universe that replaces a frozen one carries tokens), exiting 1 when it refused
anything: a passage whose challenge is owed, one whose outcome row carries a
`problem` (§10: an outcome that could not be derived, a location that could not
be joined, a document off the pinned rendering, a challenge that no longer fits
its record), a `formal_math` passage with no outcome, a record the checker
rejects, a quote no context can make unique, a receipt query or reason or a
location note carrying more than `EXPORT_QUOTE_WORDS` words of the source
verbatim, a passage with no section or prominence or a `closeMatch` with no
note, a location the index gives no module, path or line, and a whole source whose records and receipts pass `--byte-budget` or
whose licence does not allow it. No budget applies unless one is given (the
default is 0, off): a consumer with a cap — an import into a store with a
per-row limit — passes its own, and the seal records it. A source's manifest
entry's `licence_class` must be the registry's for the source, one of
`cflib.vocab.OPEN_LICENCE_CLASSES`, and an attribution class needs the family's
SPDX id. A receipt keeps each candidate's documented keys only. Only
`formal_math` passages with an outcome are exported; the subject end's `uri` and
`URL` are `sources.websource.revision_url` (titles move), its container title
and licence the family's. The target is never the skill checkout or a companion
repository, never a symlink, and never a non-empty directory without a
`cf-locate-recordset/1` seal or holding a file its seal does not name; a
re-export removes only what the previous seal named and the new set does not
carry.

Records are the proposal's §2.7 shape with one addition the vendored checker
requires: the subject end's CSL `citation` carries `"id"` (the source id) beside
its `type`. Quotes are capped at export (`cflib.vocab.EXPORT_QUOTE_WORDS` words of
`exact`, `EXPORT_CONTEXT_CHARS` characters of `prefix` and of `suffix`, cut at the
layer, so a capped quote still matches the layer exactly once or is refused),
every record carries a `challenge` value, and every record passes
`literature_links.check` before the set is sealed.

## 7. Sourcesets and rendered web sources

A web source (proposal §2.1) is identified by family, page id and revision —
`cflib.registry.web_source_id` gives `enwiki-1001-2002` — and lives in a
**sourceset**, never in `workspace.yaml`. The workspace paths are
`cflib.paths.WorkspacePaths`' `sourceset_*` accessors:

```
sourcesets/<id>/manifest-<n>.jsonl                 version n, never rewritten
sourcesets/<id>/state.json                         counts and sections, one writer (below)
sourcesets/<id>/sources/<source-id>/               one directory per source (gitignored)
sourcesets/<id>/candidates/<universe>/<source-id>.json   L2 (§5)
```

**The state file.** `state.json` is written by ONE writer,
`scripts/sources/sourceset.py`: `Sourceset.refresh_state(command=…)` stamps the
latest version's pin and counts (`manifest`, `counts`, `commands`), and
`Sourceset.record_state(section, payload, key=None)` writes every other section —
the planner's, the outcomes', the export's, the render's standing
`parity_refusals` (below): with no `key` the section becomes
`payload`, with one `state[section][key]` becomes `{…what it was, …payload, "at":
<now>}`, its sibling keys kept. The file always carries `schema:
cf-sourceset-state/1` and `id`; those, `family`, `manifest`, `counts`,
`commands`, `history` and `updated_at` are no section. `counts` counts sources
(enumerated, adopted, fetched, rendered) and pages: `by_class` and
`by_importance` count each page once, by the assessment of the source a run
plans for it. `commands` holds the last run of each command and `history` every
run in order (the newest 1,000; `history_dropped` counts the rest), each row
saying whether it wrote a version (`wrote_version`), so a no-op re-run never
hides the run that wrote one. `cf sourceset status` writes the file only with
`--verify`, and `cf status` prints every sourceset's row from it.

**Manifests.** Line 1 of `manifest-<n>.jsonl` is a header, every later line one
source, sorted by `source_id`, each a canonical JSON object (sorted keys, no
spaces, UTF-8):

```json
{"schema": "cf-sourceset/1", "id": "wpm", "version": 3, "family": "enwiki",
 "previous_sha256": "<sha256 of manifest-2.jsonl, or null for version 1>",
 "created": "2026-10-17T12:00:00Z", "sources": 2}
{"source_id": "enwiki-1001-2002", "family": "enwiki", "pageid": 1001, "revision": 2002,
 "title": "Widget lemma", "wikidata": "Q99999001",
 "assessment": {"class": "B", "importance": "high"},
 "origin": "enumerated", "licence_class": "open_share_alike",
 "fetch": {"api": "action=parse", "params_sha256": "<hex>"},
 "raw_sha256": "<hex>", "html_sha256": "<hex>", "layer_sha256": "<hex>",
 "renderer": "web-html/3"}
```

- `origin` is `enumerated` (from the PageAssessments listing) or `adopted` (a
  consumer's pin, with its bytes when it holds them, proposal §11.1, recorded in
  `adopted_from`), the values of `cflib.vocab.SOURCE_ORIGINS`. A consumer's pin
  of an enumerated entry's own revision keeps its `origin` and adds
  `adopted_from: {consumer, pins_sha256}` (plus the producer's `layer_sha256`
  when its pin carried one; below, "Adopting a producer's pins"). A pin is
  `{revision, pageid | title, bytes?, wikidata?, assessment?, fetch?,
  layer_sha256?}`: one without bytes is adopted with null shas
  and fetched at exactly that revision by `cf sourceset fetch`; one with a title
  and no page id has its page resolved from its revision, never from the title,
  which moves: from the sourceset when it already holds that revision (a
  revision id names one page, so re-running the same pins asks nothing), else
  by `action=query&revids=` under `--execute`. A new adopted revision
  takes the `assessment`, `wikidata` and `title` its pin does not carry from its
  page's other sources (an enumerated one first), and an enumeration fills them
  on an adopted entry whose page it lists, so a pinned page stays in every frame
  its page is in.
- **One source per page is planned** (`Manifest.preferred()` in
  `scripts/sources/sourceset.py`): a page with an adopted source (either kind
  above) plans it, the highest such revision when there are several, because the
  proposal's §2.1 adopts the consumer's revision instead of choosing one; any
  other page plans its highest enumerated revision. So `cf sourceset
  enumerate-wikiproject` adds no revision of a page a consumer pinned (unless
  `--include-adopted`), and `cf sourceset fetch` does not fetch an enumerated
  revision of a page whose adopted bytes are in.
- **Page flags.** An entry may carry `flags: {redirect, disambiguation, list,
  biography}` (`cflib.vocab.PAGE_FLAGS`), each `true`, `false` or `null` (not
  determined), describing the page as the listing found it.
  `enumerate-wikiproject` sets the first three from the page info (`info`
  `redirect`, `pageprops` `disambiguation`) and the assessment class (Redirect,
  Disambig, List or FL; a "List of" title), on every entry of a listed page
  that lacks them; the optional `cf sourceset flag-biographies` sets `biography`
  from Wikidata (P31 = Q5) for every entry with a QID. A page with a flag true
  is no concept article (`websource.concept_page`): a plain `fetch` leaves the
  redirects and disambiguation pages out (`--include-flagged` fetches them),
  and `cf plan-locate --preset locate-inventory` plans no such page unless
  `--source` names it or `--include-flagged` is given (each one left out is
  named in `run.yaml` `locate.skipped`, and a plan warns of the planned pages
  with no `biography` flag yet). Both read the **effective** flags
  (`websource.effective_flags`): a flag the entry did not determine — every
  flag of an entry enumerated before the flags existed, or seeded by `create
  --from` from such a sourceset, is null — is read off the entry's own
  assessment class and title by the enumeration's rule, so a Redirect or
  Disambig class is no article even before `enumerate-wikiproject --execute`
  fills the flags from the page info (a fetch of unflagged entries says so).
  Each flag is append-only on its own: a determined flag never changes, an
  undetermined one may be set later.
- **The enumeration** records, in `state.json`, the listing's rows of other
  namespaces it skipped (`non_article_rows`, by namespace), the flagged page
  counts and whether it resumed. Its network answers are checkpointed in
  `sourcesets/<id>/enumerate.checkpoint.json` every 20 answers and when a request
  fails; a re-run of the same project resumes from it (the listing from its
  continuation, the page infos not asked twice) and deletes it once the version
  is written; `--restart` discards it. A page-info answer is followed through
  its `continue`.
- A field the source does not have yet is `null` (`raw_sha256` before the fetch,
  `layer_sha256` before the render). **Append-only**: version n+1 holds every
  `source_id` of version n, and every non-null field of n unchanged — a field
  may go from `null` to a value and never back or to another value. A new
  revision of a page is a new source id beside the old one.
- The manifest sha is sha256 of the file's bytes; a run pins `(sourceset id,
  version, manifest sha256)` and the gate checks per-source shas from that pin.
- **Snapshots and deltas.** A version is a *snapshot* — the header above, then
  every entry — or a *delta*: a header with `"schema": "cf-sourceset-delta/1"`,
  the same keys (`sources` still counts every source) and `"changed": k`, then
  only the k entries that are new or changed since version n-1. Version n is
  version n-1 with those lines laid over it; a delta line repeats no unchanged
  entry. `Sourceset.write_version` writes a delta unless it changes half the
  sources or follows `MAX_DELTA_CHAIN` (64) deltas in a row, so a fetch that
  checkpoints every 200 of thirty thousand sources writes some tens of
  megabytes of manifests, not one and a half gigabytes. A reader replays the
  deltas onto the snapshot before them, checking each `previous_sha256` and the
  append-only rule at every link; the pinned sha is the version file's own,
  and the chain makes it fix every entry. `sources.sourceset.Sourceset.load`
  is that reader, and the planner's and the bench's own readers hand it a
  delta version.

**The fetch.** One parameter set, fixed: `action=parse`, `oldid=<revision>`,
`prop=text`, `formatversion=2`, `disablelimitreport=1`,
`disableeditsection=1`, `format=json`. `params_sha256` is sha256 of the
canonical JSON of that mapping without `oldid`. The response bytes are kept as
`source.raw.html` and copied, never refetched, to every other machine.

**Adopting a producer's pins.** `cf sourceset adopt <id> --from <pins.jsonl>
--consumer <name>` is the import contract a producer — the system that chooses
the revisions and holds their bytes, `$WIKILEAN` — delivers through. Each line
of the pins file is one JSON object (`sources.websource.PIN_FIELDS`, checked by
`pin_problems`):

| field | |
|---|---|
| `revision` | a positive integer; required |
| `pageid`, `title` | one of them required: a positive integer; a string. A pin with no `pageid` has its page resolved from its revision (above), never from its title |
| `bytes` | optional: the path of the revision's bytes, relative to the pins file's directory and inside it — never absolute, no `..` component, no leading `~` (nothing expands it) — and, symlinks followed, it must resolve inside that directory: a delivery is one self-contained directory, so `pins_sha256` binds rows that name files in it |
| `wikidata` | a QID or null |
| `assessment` | null or `{class, importance}`, strings or null; the importance is lower-cased on the way in (`High` becomes `high`), the class kept as written |
| `fetch` | optional, only with `bytes`: exactly `{"api": "action=parse", "params_sha256": "<hex>"}`, `sources.websource.fetch_block()`, the fixed fetch above |
| `layer_sha256` | optional, only with `bytes`: the producer's sha256 of the UTF-8 of its own text layer of those bytes' HTML (the response's `parse.text`), which the render below holds `layer.txt` to |

A field outside these is refused, not ignored (a misspelt `layer_sha256` would
otherwise switch the render's check off), and so is a `fetch` or `layer_sha256`
in a row with no `bytes`: each speaks of the producer's bytes, and what `cf
sourceset fetch` would get for that revision instead is a new parse of it (an old
revision's templates expanded as they are now), possibly other bytes, which a
recorded `fetch` or producer sha would misdescribe. A row that carries `fetch` says its
bytes are that fetch's response, so they must be its exact JSON: `parse.text` a
string (`formatversion=2`), integer `parse.revid` and `parse.pageid` equal to the
pin's — never a bare body. JSON bytes of any row must be an `action=parse`
response with a `parse.text` (an API error is refused) whose `revid` and
`pageid`, when it carries them, are the pin's; a row without `fetch` may still
hand over the HTML body itself.

**One bad row refuses the whole file**, naming its line (the file's own,
blank lines counted): nothing is adopted and the command exits 2. The rows are
read from the bytes `pins_sha256` is taken of. Once every title pin's page is
resolved, **a source is pinned by one row**: rows naming one source id refuse
the file unless every field says the same (a repeated line, taken once; an
absent field and a `null` one say the same), since rows that disagree — on the
bytes, the `layer_sha256`, anything — leave no way to tell which one the
producer meant.

An adopted entry records `adopted_from: {consumer, pins_sha256, layer_sha256?}`
— the `--consumer` name, the sha256 of the pins file's bytes, and the row's
`layer_sha256` when it carried one (`sources.websource.adopted_from_problems`
is the shape) — and a row's `fetch` as the entry's `fetch`. Under a source id the
sourceset already holds, other bytes are refused; the same bytes are already
held, refused when the row's `layer_sha256` contradicts the producer's sha
recorded for them or the layer already rendered from them (a layer another
renderer recorded is no measure of this one's parity, so that refusal names
the `create --from` migration below, after which the delivery is adopted into
the seeded sourceset — which records no producer sha for a source the old one
adopted with none, so a kept run's sourceset is instead created new and filled
by `adopt` alone, `references/runbook-server.md` §9); and bytes the
sourceset fetched itself become the consumer's pin (`adopted_from` added, so
`Manifest.preferred()` plans them). A recorded `adopted_from`, the producer's
`layer_sha256` in it included, is never replaced (append-only): a source first
adopted without one (a pin with no bytes) keeps none, which adopt names and
counts (`commands.adopt.layer_sha256_not_recorded`), and a producer sha that
was wrong stays this sourceset's, its source refused by every render until
its layer is that sha. A producer's corrected delivery therefore goes into a
new sourceset (`cf sourceset create`, then `adopt`), as adopt's refusal says;
`create --from` carries `adopted_from` over, so a seeded sourceset is held to
the same producer sha under the next renderer.

**The render is held to the producer's layer.** `cf sourceset render` refuses,
by name, a source whose `layer.txt` sha would not be its
`adopted_from.layer_sha256`: none of its views is written, its entry keeps
`layer_sha256: null`, the summary line counts and names it ("… N refused, M of
them because the layer is not the producer's: …"), `state.json`'s
`commands.render.not_the_producers_layer` lists this run's, and the command
exits 1. The refusal **stands** until a render writes that source's layer,
whichever later runs visit it (a `--source` run visits only some):
`state.json`'s `parity_refusals` section holds `{<source id>: {layer_sha256,
producer_layer_sha256, renderer, at}}` — this renderer's layer sha, the
producer's, and the renderer that measured them — merged by every render
(`Sourceset.record_parity_refusals`; a sourceset that never had one has no
such section) and read through `Sourceset.parity_refusals`, which keeps only
sources the manifest still holds unrendered under that producer sha. `cf
sourceset status` reports `producer_layer: {pinned, rendered, refused,
waiting}` — sources whose pin carried a `layer_sha256`, those rendered to it, the
standing refusals by id, and those not rendered yet — and `status --verify`
counts each standing refusal as a problem and exits 1. No manifest version can
record such a layer either (`entry_problems`). So every delivery is a
text-layer parity test on real pages that fails closed; a source whose pin
carried no `layer_sha256` is rendered unchecked.

**The canonical body** is the response's `parse.text` (an adopted file that is
not such a JSON response is taken as the body itself; adopt refuses JSON that is
no parse response with a `parse.text`, an API error included) decoded as UTF-8
with one leading byte-order mark dropped, with every HTML comment removed as the
HTML tokenizer delimits one in the data state — `<!--` to the first `-->` or
`--!>`, `<!-->` and `<!--->` as whole empty comments, an unterminated one to the
end, while a `<!--` inside a tag or inside the raw text of `script`, `style`,
`xmp`, `iframe`, `noembed`, `noframes`, `textarea` or `title` is text — as the
exact UTF-8 bytes in `body.html` (a CR stays a CR); its sha256 is `html_sha256`,
which is the source's `source_sha` everywhere (hard rule 3). A body holding a
lone surrogate, or nesting elements more than 256 deep, is refused.

**One source's directory.** `cf sourceset fetch` and `cf sourceset adopt` write
`source.raw.html`, `body.html` and `source.json`; the renderer,
`scripts/sources/render_html.py` (`web-html/3`, whose module docstring states the
rule in full and freezes it), writes `body.html` again and every file after it
when `cf sourceset render` runs. A source whose layer is recorded is only ever
completed, never rewritten: `render` restores a missing view, and refuses by name
a view that exists with other bytes than the current renderer writes from the
body (the rule changed under its id, or the file was edited):

| file | what |
|---|---|
| `source.raw.html` | the bytes as fetched or adopted (`raw_sha256`) |
| `source.json` | the manifest entry, as the source's own record |
| `body.html` | the canonical body (`html_sha256`) |
| `layer.txt` | the **Literature Links text layer** (`layer_sha256`): the body's DOM `textContent` without the `script`, `style` and `template` elements — every other text node in document order, MathML token text, TeX annotations and citation markers included; the one newline right after a `pre`, `listing` or `textarea` start tag is not text, as the HTML5 tree builder drops it — with each run of `White_Space` (the 25 code points of the Unicode property, U+00A0 and U+2009 among them) made one space and the ends stripped; no space is added at element boundaries and there is no other normalisation. It is the rule the consumer's own text layer implements (proposal §2.1), and the only view quotes are cut from and gated against |
| `formulas.json` | `[{"span": [a, b], "alttext": "{\\displaystyle G}"}]`: each formula's half-open layer span (the whole `.mwe-math-element`, else the `math` element), sorted and disjoint; the alttext is `math@alttext`, else the `x-tex` annotation, else the fallback image's `alt`. A formula with no text in the layer (an image only) has no span and no entry, `render.json` counts it as `formulas_without_text`, and the reading copy still writes it as `$<normalized alttext>$`, in no segment |
| `head.txt` | the head text: `layer.txt` with each formula span replaced by `⟦normalized alttext⟧`. Derived; the definition is `scripts/gate/locate_text.py`'s `HeadMap`, which also maps offsets both ways |
| `text.txt` | the **reading copy** for models: math as `$<normalized alttext>$`, references (`sup.reference`), reference lists (`ol.references`, `.reflist`, `.mw-references-wrap`), edit links, style, script and template dropped, headings as `#`-prefixed lines (one `#` per level), `- ` before a list item, ` \| ` between table cells (one space each side), a blank line between blocks (inside a table cell a block, a display formula included, is only a space: a row is one line); it ends with one newline |
| `layer_map.json` | `{"schema": "cf-layer-map/2", "renderer": "web-html/3", "segments": [[t0, t1, l0, l1, kind], …]}`, sorted: `text.txt[t0:t1]` is `layer.txt[l0:l1]`, character by character when `kind` is `0` (text) and only as a whole when it is `1` (a formula: only `t0` and `l0` correspond, whatever the two lengths are). A character the copy adds — a heading's `#`s, a bullet, a separator, a line break, an image-only formula — is in no segment and has no layer offset. The map names the renderer that wrote it: its offsets point into that renderer's views only, so `LayerMap.read` refuses a map, or a directory whose `render.json`, names another, and `cf sourceset status --verify` reports such a view |
| `sections.json` | `[{"path": ["Theorems"], "fragment": "Theorems", "level": 2, "span": [l0, l1]}]`, the lead first with `path: []`, `fragment: null` and `level: 0`; a heading's title is its text outside the reading copy's dropped regions (an edit link, a reference marker, an inline `style`), its fragment its `id`, else its `.mw-headline`'s; spans tile the layer |
| `paragraphs.json` | `[{"span": [l0, l1], "section": ["Theorems"], "block": "p", "formulas": 2, "texhtml": 0, "math": true}]`: every block (`p`, `li`, `dd`, `dt`, `blockquote`, a theorem box, a displayed formula) that holds text outside the reading copy's dropped regions, its span measured over that kept text alone (an inline TemplateStyles `style` opening a paragraph is outside its span; a paragraph holding only such text is no block), outside the reference lists; blocks may nest (an `li` holding a `p` is two entries). `formulas` counts the `formulas.json` formulas starting inside it and `texhtml` the formulas written as HTML (each outermost element of class `texhtml`, MediaWiki's `{{math}}` and `{{mvar}}`, which carry no TeX and so are no `formulas.json` formula); `math` is true when the block holds either or a deterministic finder hit, and those are the paragraphs gate rule 8's coverage floor reads |
| `finders.json` | `[{"finder": "theorem_box", "start": l0, "span": [l0, l1], "section": […], "term": "…"}]`: the deterministic finders' passages (`cflib.vocab.DETERMINISTIC_FINDERS`), sorted by start, each spanning its element's kept text (as for blocks) and starting at the first character of a word of the head text; a finder hit whose kept text is glued to the word before it (a citation marker with no space after it, say) is dropped and counted in `render.json`'s `finders_dropped`. A `bold_definition` is a paragraph's first bold that names a term: at least two letters, and not only a paragraph label (`cflib.vocab.is_bold_label` over `BOLD_LABELS`: "Proof.", "Example 2:", "Definition:"; a word of `BOLD_LABELS_PUNCTUATED` only with a label's punctuation, "Solution:", never for an enumerator alone, so "Axiom A" and "property (T)" are terms; a label word with a parenthesised name, "Theorem (Widget lemma).", names a statement and is a term too); a paragraph whose every bold is a label or a symbol makes none, and `render.json` counts the paragraphs a label alone kept from a passage (`bold_labels_dropped`). Finders of different kinds may share a start (a lead paragraph that opens on a bold term); one passage at that start keeps them all (gate rule 8) |
| `render.json` | `{"renderer": "web-html/3", "html_sha256", "layer_sha256", "head_sha256", "text_sha256", "counts": {…}, "views": {"layer.txt": "<sha256>", …}}`: `views` holds the sha256 of the bytes of every other file a rendering writes (`body.html`, whose sha is `html_sha256`, and `render.json` itself excepted), which `cf sourceset status --verify` and the planner's check of a source (`locate.inputs.check_source`) compare with the files; a current rendering without it is refused |

The `web-html/1` rule was corrected once, before any rendering under it was
pinned anywhere, after an adversarial review of the renderer. The corrections:
comments removed as the tokenizer delimits them (not by a `<!--.*?-->` pattern),
a byte-order mark dropped, the newline after `pre`/`listing`/`textarea` (this
one moves `layer.txt`), blocks and finder passages measured over kept text, an
image-only formula written into the reading copy, one space each side of a cell
separator, a display formula inside a cell kept on its row, and `layer_map.json`
moved to `cf-layer-map/2`.

**`web-html/2`** replaced it after the first real pages were rendered: under
`/1` not one layer equalled the consumer's text layer, because `/1` kept
`style` and `script` text (a fifth of a real page's layer was TemplateStyles
CSS, and a passage could open on it) and collapsed only ASCII whitespace. `/2`
is the Literature Links rule above. It also honours a self-closing flag on a
MathML or SVG element (`<mspace/>` holds nothing, as in the HTML5 tree builder;
the flag on an HTML element is still ignored), and a block holding only a
formula written as HTML (`texhtml`, MediaWiki's `{{math}}`) is a `math` block
that the coverage floor reads; a bold that only labels its paragraph ("Proof.")
or is a symbol (a bold R) makes no `bold_definition`.

**`web-html/3`** keeps the layer, the head text, the reading copy and the
sections of `/2` character for character, so no passage id moves (a block's
`math` flag follows the finders, so it changes where a finder now starts, and
`layer_map.json` names the new id); it changes two things. The paragraph-label rule no longer takes a named concept
or statement for a label: `/2` dropped a trailing single letter, roman numeral
or parenthesised token as an enumerator even after a word of
`BOLD_LABELS_PUNCTUATED`, and a parenthesised name after any label word, so
"property (T)", "Axiom A", "Problem P", "Statement A" and "Theorem (Widget
lemma)." made no `bold_definition` passage — no inventory pre-fill, no rule 8
obligation, no L2 query — with nothing counted; `/3` counts what the rule still
drops (`bold_labels_dropped`). And `render.json` records every view's sha
(`views`): under `/2` a later edit of the label sets (which live in
`cflib.vocab`) changed `finders.json` of a recorded source in place under the
same id, which `render` counted unchanged and `status --verify` could not see.
The label rule is part of the frozen renderer: `tests/test_locate_sources.py`
pins its fingerprint (the two label sets, its patterns and its answers on a
fixed list of bolds) to the renderer id beside the golden views.

**Parity with the consumer's text layer** is pinned by a conformance fixture,
`tests/data/locate/text-layer.json`: synthetic MediaWiki-shaped bodies, each with
the layer `$WIKILEAN`'s own `text_layer` gave for it (stamped with that commit
and the module's sha256) and the renderer id it holds for, which
`tests/test_locate_text_layer.py` replays through this renderer.
`tests/gen_text_layer.py` regenerates it from a read-only checkout, running the
function from the module's bytes in a separate isolated process, and refuses an
unmarked difference or a stale mark. Where the two layers differ the case is a
`known_divergence` that names the side that is not the HTML5 parser there
(`departs`) and records this renderer's layer beside the consumer's. Most are the
consumer's regular-expression scanner: the newline after `pre`, a reference with
no `;`, a `>` inside a quoted attribute, a self-closing `<style/>` or `<script/>`
in SVG or MathML (which it reads as the start of a skipped element that runs to
the end of the page). Four are this renderer's: in `web-html/3`'s tree builder a
`template` is no scope boundary, so an `li`, `dd`, `td` or `tr` start tag or an
end tag inside one closes an element outside it, and the text after it leaks into
the layer; MediaWiki's parser output carries no `<template>` element, and
following HTML5 there moves layers, so it is a new renderer id. On real pages
the same parity is checked source by source: a producer's `layer_sha256`, held
against the render (above, "Adopting a producer's pins").

A `/1` or `/2` rendering cannot be re-rendered in
place (a recorded `layer_sha256` never changes): `cf sourceset create <new>
--from <old>` seeds a new sourceset with the old one's identities and bytes and
no layers, and `cf sourceset render <new>` renders it under the current id. The
seeded sourceset appears whole or not at all — it is built in
`sourcesets/.seeding-<new>/` and renamed into place once its version is written
— so an interrupted seeding is re-run as the same command. Any
change to any view, the layout of a view's file included, is a new renderer
id: `tests/test_locate_sources.py` pins every view of a synthetic page to it.

**Another renderer's source is refused wherever locate would read it.** A
manifest entry whose layer sha is recorded under any renderer id but
`web-html/3` — `/1`, `/2`, an id this checkout does not know, or none — carries
another rule's layer, offsets and passage ids, however consistent its own files
are (`locate.inputs.check_renderer`, which `locate.inputs.check_source` runs
first). `cf locate candidates` refuses it by name before it writes any source's
file, and `cf plan-locate` before it writes anything, so no task document and no
`gate.json` pins it; the
export refuses it per source in its report. Each refusal names the source, its
renderer and the `create --from` step above. `cf bench build` refuses a drawn
article of another renderer (an undrawn frame article of one is only recorded),
and `cf bench score` refuses a run that read another rendering than the one its
bench pinned. The gate is not one of them: it holds a run's
documents to that run's own pins, which only the planner writes.

Offsets are code-point offsets. A passage's `span` is a layer span; its id is
computed on the head text at `HeadMap.to_head(span[0])` (§2 of the proposal,
`references/vocabulary.md` §4), and a passage may not start inside a formula.

## 8. Universes, library indexes and `corpus.sqlite`

```
corpora/<universe>/universe.yaml
corpora/<universe>/conventions/<library>.md       confirmed_by stamp required
corpora/<universe>/index/<library>/manifest.json  one normalised index per library
corpora/<universe>/index/<library>/decls.jsonl
corpora/<universe>/corpus.sqlite                  derived, rebuildable
```

The same layout may live under any directory instead of a workspace's
`corpora/`: `cf corpus <subcommand> --base <dir>` reads and writes
`<dir>/<universe>/` and reads no workspace (`cflib.paths.CorporaPaths`, which a
workspace's paths delegate to) — how a universe kept on the run host beside the
drivers' indexes is made, ingested, built and searched.

`universe.yaml` is the proposal's §2.3 shape, written by `cf corpus`
(`scripts/corpus/universe.py`). Each library carries `id` (the universe-local id
a document names as `library`: a lowercase letter, then lowercase letters, digits
and `._-`, at most 64, and none of YAML's bool and null words `yes`, `no`, `true`,
`false`, `on`, `off` and `null` — `corpus.universe.LIBRARY_ID`, which a universe's
own id and an exclusion's id follow too, on load as at `cf corpus add`; the letter
comes first and the words are left out so that no id reads as anything but itself
in YAML, quoted or bare: not a number or date, `1.5e-3` or `2026-10-17`, and not a
bool or null, `no` or `null`), `brain_lib`, `repo` (an https repository URL a
record set's consumer can link: a dotted host and one to four path segments of
letters, digits and `._~-`, at most 100 each, none of them `.` or `..`, so no
empty segment and no trailing slash — `corpus.universe.REPO`, refused at `cf corpus
add` and on load; the repo is outside `universe_sha256`; and **one spelling per
repository**: libraries that share a repository spell it alike, and one whose repo
is another library's repository spelled differently — the host's case, a `www.`, a
trailing `.git` in any case, and on github.com the owner's or name's case
(`corpus.universe.repo_key`) — is refused at `cf corpus add` and on load, naming
the spelling the universe uses, because everything downstream compares a repo
exactly as written: an absence's one end per `(repo, commit)` (§6) and the
consumer's `(repo, root)` rule), a 40-hex `commit`,
`toolchain`, `roots`, `licence` (`{spdx}`: an SPDX id or expression; null while a
library pinned without `--licence` has none, and a re-add with `--licence` records
it, since the licence is outside `universe_sha256`; a re-add naming another
licence or another `brain_lib` than the row records is refused), `conventions`
and, once ingested, `index: {path,
sha256, declarations}` — and `published_index: {schema, reason, accepted}` when that
index is one the library publishes itself, accepted by name with `cf corpus ingest
--accept-published <reason>` because neither the probe nor its canary checked it —
and `host: {kind, ref, options}` when the host driver is to index it other than as a
`build` of its commit (`cf corpus add --host-kind --host-ref --host-option`): a
`published-index` from its tag, Lean `core` or a `package-in-workspace` probed inside
another library's built `WORKSPACE`, and the driver options of its universe-list line,
checked by the driver's own parser (`scripts/corpus/universe_host.py`). `host` is how
a pin is built, not what is pinned: it is outside `universe_sha256`, and `cf corpus
probe-command` prints from it the universe's whole driver list (every library, those
probed inside a workspace last) and the steps of that library's kind;
`brain_lib`, `toolchain` and each root are at most 300 characters and an
exclusion's reason at most 1000 (`corpus.universe.MAX_NAME_CHARS`,
`MAX_REASON_CHARS`: the record set's consumer refuses a whole set over a longer
one, so `cf corpus add` refuses it where it is written, as does a load); a
library `cf corpus add` adds carries a `brain_lib` that is a **library token** —
a letter, then letters, digits and `_`, at most 64 (`corpus.universe.BRAIN_LIB`,
the grammar the consumer's Brain admits as a library's name: `Mathlib`,
`FF_AdicModules`, never `FF.AdicModules`) — the `--brain-lib` given, else the id
when it is one; any other is refused, naming `--brain-lib` and a spelling it
admits. Only a new row, and a re-add's `--brain-lib`, is held to it: a universe
written before (u1's `FF.<Root>` tokens) loads, re-adds without `--brain-lib` and
exports as it stands, since a frozen universe is never rewritten — the export
report names each such name (§6) — and the next universe carries tokens. A token
names one repository and is **compared without case** (`Mathlib` and `MATHLIB` are
one token): a library whose token, its own or else its id, is another repository's
is refused at `cf corpus add` and on load, while one repository may carry one
token over several libraries, or several tokens;
`excluded` lists `{id, reason}` (what a universe leaves out WITHIN a library is a
choice of its `roots`, stated in the proposal's §2.3 for the default universe: Mathlib
with its `Wanted` library of `proof_wanted` statements, Lean core without the `Lean`
namespace); `canaries` lists, per
toolchain, the acceptance canary's result (`{toolchain, lean_version, passed,
checked, index_sha256, cases}`) — no library on a toolchain without a passed
canary is ingested; `corpus_db` is `{path, sha256, built, declarations,
universe_sha256}` once `corpus.sqlite` is built, `corpus_db_blind` the same for
`corpus.blind.sqlite` (`cf corpus build-db <u> --blind`), and both are `null` again
as soon as an index changes. **`universe_sha256`** is sha256 over the lines `<id>\t<commit>\t<index
sha256>`, one per library sorted by id (an empty index sha before ingest), joined
with `\n` — so a re-probe that changes one index changes the universe.

**A normalised index** (`decls.jsonl`, sorted by name; its sha256 is
`index.sha256`) holds one declaration per line:

```json
{"name": "Widget.exists_spanning_gadget", "module": "Alpha.Widget",
 "path": "Alpha/Widget.lean", "line": 612, "start_line": 610, "end_line": 640,
 "kind": "theorem", "proof_status": "proved", "statement": false, "printable": true,
 "signature": "∀ (w : Widget), ∃ g, g.Spans w", "docstring": "…",
 "axioms": [], "tags": [{"tag": "wikidata", "value": "Q99999001"}],
 "deprecated": null, "private": false, "probe_proof_status": "proved", "flags": []}
```

`module` is the module's name as Lean prints it, a component that is no
identifier escaped (`Alpha.Problems.«1»`, `Alpha.Arxiv.«0805.3777».Rank`), and
`path` the file Lake reads it from, its components unescaped
(`Alpha/Problems/1.lean`). The host driver expands a root glob into such names
(`scripts/corpus/probe_merge.py` `module_of_file`), compares names by their
components, and stops — never skips — at a file no module name can express.

**Ingest refuses an index that is not the pinned one, whole**
(`scripts/corpus/ingest.py`): the library, commit and toolchain are read from the
index's own header and its `manifest.json`, which must both name the pin and may
not disagree; the manifest's `index_sha256` and `index_bytes` must be the bytes
read and the header's `declaration_count` and the manifest's `records` the
records read; a probed checkout recorded `dirty` (or not known to be clean), a
probe index on a toolchain without a passed canary, a record without its
`nonstandard_axioms` or whose own status contradicts its fields, and an index in
which no module has a source path (a probe that never saw the sources) are
refused. Two private declarations sharing a user name — the probe names a private
declaration by its user name — are dropped and counted (`private_collisions_dropped`);
two public ones are refused.

`proof_status` is `cflib.vocab.decide_proof_status` over the probe's wrapper,
its axiom closure and its type — re-decided at ingest,
never copied from a probe, whose own answer is kept as `probe_proof_status`
because the probe as run decides in another order (an axiom by its kind first,
no `axiom_dependent`, an `isProp` failure falling through to `proved`). `axioms`
is the closure outside `propext`, `Classical.choice` and `Quot.sound` (`sorryAx`
included; an axiom declaration's closure holds itself). A declaration whose
status would turn on an `isProp` the probe could not compute is the conservative
`statement_only` with `is_prop_unknown` in `flags`; a published index's status
word, taken without a closure, carries `published_status` — Autoform's
`complete` (no `sorry`, no non-standard axiom) is `proved` for a theorem and
`definition` for every data kind (`def`, `abbrev`, `opaque`, `instance`,
`inductive`, `structure`, `class`), its `wanted` is `wanted`, its kind `lemma` is
written `theorem`, and a word or kind outside that is refused. `statement` is set on
a `Prop`-valued definition — derived, while the probe emits no such field, from
a `def`/`abbrev`/`opaque` whose printed type's final codomain is `Prop`. The
source scan for a `proof_wanted` that adds no constant is not implemented (the
proposal's §2.4, rule 1): such a result is not in the index, and every manifest
says `proof_wanted_scan: "not implemented"`. `tags` use `cflib.vocab.TAG_KINDS`:
attributes read from a checkout given to the ingest — which must be at the pinned
commit (its HEAD read from its files) and clean — and Wikipedia URLs in the
docstring. An attribute list `@[...]` is read whole, its brackets balanced (`@[ext,
aesop safe constructors (rule_sets := [G]), wikidata Q1]` is one list, however many
lines it spans), after comments and string and character literals are blanked; a
declaration's tags are those of every list that starts in its span (from
`start_line` to `line`), except that a declaration whose span lies inside a list —
a `to_additive` twin, which the probe reports at its attribute — takes only that
list's `(attr := …)` tags, which apply to both twins. An `attribute [wikidata Q1]
Name` command anywhere in the library tags the declaration `Name` resolves to
through the enclosing namespaces, innermost first (no `open` namespace is tried); a
name it cannot resolve in the library is counted in the manifest's
`attribute_commands`, never guessed. A twin the command generates lies inside its
list and takes its `(attr := …)` tags as above; an existing twin it pairs its target
with (`attribute [to_additive (attr := wikidata Q1)] MulWidget` beside a hand-written
`AddWidget`) is not tagged, since the source never writes that name. `-attr` and
`local attr` tag nothing. A Wikipedia tag's value is
one canonical page URL, the DESKTOP form `https://<lang>.wikipedia.org/wiki/<title>`:
the mobile `<lang>.m.` host folded, the title percent-decoded, spaces and runs of underscores one
underscore, its first letter upper-case, no fragment or query; an apostrophe
and a balanced pair of parentheses are part of the title, an unmatched `)` and
trailing sentence punctuation are not. A consumer joining on an article URL
puts it in the same form first. `manifest.json` (`schema: cf-library-index/1`) carries the library,
commit, toolchain, declaration count, `sha256` of `decls.jsonl`, the counts by
proof status and kind, the flagged and tag counts, and the probe output's shas.

**`corpus.sqlite`** (built with `journal_mode=DELETE`, opened everywhere else
with `mode=ro&immutable=1`; its sha256 is recorded in `gate.json`):

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  -- schema = cf-corpus/1, universe, universe_sha256, built, blind (1 or 0)
CREATE TABLE libraries (id TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL,
  toolchain TEXT, brain_lib TEXT, index_sha256 TEXT NOT NULL, declarations INTEGER NOT NULL);
CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL,
  name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT,
  proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0,
  printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT, deprecated_to TEXT,
  UNIQUE (library, commit_sha, name));                        -- the exact table
CREATE VIRTUAL TABLE decls_fts USING fts5(name_words, signature, docstring,
  tokenize = 'unicode61');                                    -- rowid = decls.id
CREATE TABLE tags (decl_id INTEGER NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX tags_by_value ON tags (tag, value);
```

`name_words` is the name's words joined by spaces, by one rule: a word is a run
of letters and digits (every other character, `.` and `_` included, separates),
a run is split again where a lower-case letter or a digit is followed by an
upper-case one, and words are lower-cased — `Widget.exists_spanning_gadget` is
`widget exists spanning gadget`, `Polynomial.natDegree` `polynomial nat degree`,
`T2Space` `t2 space`. Search queries are split by the same rule
(`scripts/corpus/corpus_search.py` `iter_words`), so `natDegree`, `nat_degree`
and `nat degree` are one query. A declaration present in two libraries is two
rows. `deprecated_to` is the new name a deprecated alias points at (the probe's
`deprecated.new_name`), null otherwise.

**The G1-blind database** (`corpus.blind.sqlite`, `cf corpus build-db <u>
--blind`, `cflib.paths.WorkspacePaths.corpus_db_blind`) has the same declarations
with an empty `tags` table, every Wikipedia link stripped from `decls.docstring`
and from the FTS docstring column, and `meta.blind` = `1`: the proposal's §8.1
arm with "the tag table turned off", which a run of that arm pins in place of
`corpus.sqlite`, so neither a curated join nor a host tool can reach an
article's tag or URL.

**The host tools** (`scripts/corpus/corpus_search.py` and
`scripts/corpus/corpus_show.py`) are standard-library scripts copied into the
gate directory — both together: `corpus_show.py` imports its helpers from its
sibling — which the prompts name by absolute path:

```
python3 <gate>/corpus_search.py --db <corpus.sqlite> [--library L] [--limit N] <query words…>
python3 <gate>/corpus_show.py --db <corpus.sqlite> <library> <declaration>
```

Both print JSON lines (`library`, `declaration`, `module`, `path`, `line`,
`end_line`, `kind`, `signature`; `corpus_search.py` adds its `score`,
`corpus_show.py` adds `docstring` and `tags`, and neither shows a proof status),
read nothing but the database and write nothing. In a gate directory they read
only the database the `gate.json` beside them pins (`tools_corpus_db.path`): a
`--db` naming another file, a `--db` given twice (argparse would keep the last)
and an abbreviated `--d` are bad arguments, and a `gate.json` there that pins no
tools database is exit 3; outside one, `--db` is the caller's. The query is split into words
by the rule above (at most 32), each quoted and the words OR-ed, so nothing a
worker types reaches FTS5's own syntax; results are ranked by `bm25` with the name weighted over the signature
over the docstring — after the declarations whose name matches the query as an
identifier (`corpus_search.identifier_tier`), which come first in three tiers:
the name is the query; the name's words are exactly the query's (`is_compact` and
`IsCompact`, `compact space` and `CompactSpace`); the words of the name's last
components, cut at a `.`, are (`Prime` and `Nat.Prime`). Within a tier the name
with fewer words comes first, then `bm25`; `score` is `-bm25` of the match that
found the row (the name phrase for an identifier), so it orders rows within a
tier. `bm25` alone rewards a name that repeats the query's words, which on a
real library put an identifier hundreds of rows under its own lemmas. Every
search reads the FTS5 index first and joins its matches to `decls` by rowid
(`CROSS JOIN`, the library filter written `+d.library = ?` so no index serves
it): a `--library` search costs what the unfiltered one does, never a walk of
the library's rows with the match re-run per row (about 90 s a call on the field
universe under SQLite 3.43's planner). The schema is unchanged, so a database
built before this is read as it is. Exit status: 0, 1 (`corpus_show.py`: no such declaration), 2
(bad arguments, one that is not valid UTF-8 included), 3 (the database could not
be read — a host problem); never a traceback.

**The workspace side** reads the database through `scripts/corpus/query.py` and
nowhere else: `open_db(path)`, `meta(conn)`, `check_universe(conn, universe, *,
blind=None)` (refuses a database that is not `cf-corpus/1` or whose
`universe_sha256` is not the universe's — call it before trusting one),
`is_blind(conn)`, `libraries(conn)`, `exists(conn, library, commit, name)` (the
exact table), `declaration(conn, library, name, *, commit=None)` (every column
with `tags`), `search(conn, words, *, library=None, limit=20, all_words=False)`
(the host tool's search; `all_words` keeps only declarations matching every
word), `show(conn, library, name)`, and the tag joins `tagged(conn, tag, value)`
and `tags_of_kind(conn, tag)`, whose rows are `{library, commit, declaration,
path, line, tag, value}`.

## 9. A locate run on disk

A locate run is planned from a sourceset and a universe by `cf plan-locate`
(`scripts/cfcmd/plan_locate.py`, `scripts/locate/plan.py`) into `runs/<run-id>/`,
like a faithfulness run:

- `tasks/<task-id>.yaml` — tend's task shape (`schema_version, id, title,
  status, priority, depends_on, summary, description`), titled `inventory:`,
  `locate:` or `challenge:`; the description names the task's documents, the
  paths to read and the gate command. Task ids: `inv-<source-id>`,
  `loc-<source-id>-<k>` (one source per task), `loc-subject-<kkkk>` (the
  article tier, one subject passage per source), `chl-<kkkk>`.
- `gate/tasks/<task-id>.json` in the tend root — the **task manifest**, outside
  every worktree, read by the gate (only these documents, only these passages)
  and by the bridge (the `UNRESOLVED` document it writes at the attempt cap):

  ```json
  {"task_id": "t-…", "mode": "locate", "run_id": "r-…",
   "documents": [{"source_id": "enwiki-1001-2002", "source_sha": "<hex>",
                  "layer_sha256": "<hex>", "renderer": "web-html/3",
                  "passages": [{"item": "In-text #3fa91c07", "span": [10234, 10612],
                                "head_words": 12, "prominence": "central",
                                "passage_kind": "theorem", "text_span": [9871, 10190],
                                "section": ["Statement"],
                                "quote": {"exact": "…", "prefix": "", "suffix": ""}}],
                  "curated": [{"library": "alpha",
                               "declaration": "Widget.exists_spanning_gadget"}]}]}
  ```

  `span` is a `layer.txt` offset; `text_span` is the same passage in `text.txt`
  (through `layer_map.json`), and `section` and the inventory's `quote` say where
  it is, so a worker can find what it reads. `curated` is the curated hits of the
  source's candidates file this task answers for (gate rule 4): all of them go to
  the one task of the source holding its subject passage, else its first task,
  and every other task of the source gets `[]`. An inventory task's documents
  carry `"finders": [...]` (`finders.json`'s passages, pre-filled, one row per
  finder and start) instead of `passages` and `curated`; a challenge task's carry
  `"records": [...]`, the records it judges: `{key, target, item, span,
  text_span, section, quote, run_id, prominence, scope, passage_kind, outcome}`
  and, for a location, its `location_key`, `library`, `declaration`, `relation`,
  `rank` and `note` (when it has one); for a coverage claim, its `parts`
  (`[{location_key, library, declaration, relation, rank}]`) and `note`; and,
  where the locate document wrote them (§3, the judgment rules), the passage's
  `claims` and a location's `clauses`, `instantiation`, `step` and `convention`
  — for the challenger to check, never evidence. A location record also carries
  `passage_locations`, the passage's other locations of the same run
  (`[{location_key, library, declaration, relation, rank, planned}]`, best rank
  first, `planned` the plan's value for each): read-only context for the FM7
  rule, never records the task gives a verdict for. A passage's records are
  never split over two challenge tasks — a coverage claim with the parts it
  names, and every required location of one passage with the others. Every task manifest of the plan is also kept beside `run.yaml`, in
  `runs/<run-id>/planned.json` (`{schema: "cf-locate-planned/1", run_id,
  tasks}`), which the outcomes read the planned passages from — the tend root is
  not something a workspace commits.
- `challenge-plan.json` (a challenge run only) — every record of the challenged
  runs, required or not: `{schema: "cf-locate-challenge-plan/1", run_id,
  challenged_runs, seed, sample_rate, strong_sample_rate, absence_sample_rate,
  counts, records: [{key, target, source_id,
  item, location_key, library, declaration, relation, run_id, prominence, scope,
  outcome, rule, stratum, planned, task_id}]}` (a coverage record adds `parts`, its
  parts' location keys). `rule` is `refute_by_default`, `hunt`, `basis`,
  `sample` or `none` — every coverage claim is `refute_by_default`, and so is
  every `subject` passage's prominence (stratum `prominence:subject`, a name
  only: it is required whatever the rates, as its records carry the article's
  QID, §6), and each of a
  `supporting` passage's bases (every location whose own derived status is the
  passage's: the passage keeps that status while any one of them stands) is
  `basis` when its relation is `exactMatch`, `closeMatch` or `broadMatch`; a
  sampled record's `stratum` is `relation:<relation>`, `scope:<scope>`,
  `prominence:supporting` or `outcome:absent` (a `supporting` passage's absence),
  each drawn at its rate — a strong relation's at `strong_sample_rate`, the
  absences' at `absence_sample_rate`, every other at `sample_rate` — at least one
  record of a non-empty stratum whose rate is above zero; `planned` is
  `required`, `sampled_out` or `not_required`. A plan written before the two later
  rates carries `sample_rate` alone and no `basis` rule: it drew strong
  relations and supporting absences at none. A plan written before the subject
  prominence rule has no `prominence:subject` record: its subject passages keep
  their prominence unchallenged, and their records the QID.
  A plan made under judgment rules 3 (the statement cut's anomaly A3,
  `docs/proposals/statement-cut.md` §5) adds `conditional: "prominence"` to each
  location, absence and unresolved record of a `supporting` passage whose
  prominence record is `required` and which it did not require itself: its
  `planned` value is unchanged, its `task_id` is that prominence record's task, and
  a verdict on it counts only beside an overturn of that prominence to `subject`
  or `central` in the same challenge run (§10; the gate holds the two to one
  document, §4, and a conditional record sits in its prominence record's task, so
  they agree). A plan made before has no such key,
  and the outcomes read it as they always did.
  A record's `key` is its `location_key`, or sha256 of `<target>\n<source_id>\n<clean(item)>`
  for an absence, unresolved outcome, scope, prominence or coverage claim. The outcomes read it
  back: a required record no verdict judged is owed (`challenge: null`). The gate
  reads a record's `item`, `target`, `location_key` (location targets only), `span`
  and `passage_kind`.
- `findings/<task-id>/<source-id>.json` — the documents (§1–§4); a contribution
  may change only its own task's directory and `tasks/<task-id>.yaml`, and that
  file only in its `status:` line (rule 7: every other line reads as at the
  merge base, there is one status line and it holds one status word,
  `status: [a-z][a-z_-]*` — no flow mapping, list or YAML tag rides on it —
  the file is already on the merge target — an added task file is
  refused — and a `tasks/<task-id>.yml` is another path, refused too).
- `gate.json` — read by the out-of-tree locate gate,
  `scripts/gate/verify_locate.py`:

  ```json
  {"schema": "cf-gate-locate/1", "mode": "locate", "preset": "locate", "run_id": "r-…",
   "kinds": ["ABSENCE", "LOCATION", "UNRESOLVED"],
   "universe": {"id": "u1", "sha256": "<hex>",
                "libraries": {"alpha": {"repo": "https://…", "commit": "<40 hex>"}}},
   "corpus_db": {"path": "<host path>/corpus.sqlite", "sha256": "<hex>"},
   "tools_corpus_db": {"path": "<host path>/corpus.sqlite", "sha256": "<hex>",
                       "kind": "full"},
   "sourceset": {"id": "wpm", "version": 3, "manifest_sha256": "<hex>",
                 "sources_dir": "<host path>/sources"},
   "sources": {"enwiki-1001-2002": {"html_sha256": "<hex>", "layer_sha256": "<hex>",
                                    "renderer": "web-html/3",
                                    "views": {"formulas.json": "<hex>",
                                              "paragraphs.json": "<hex>"}}},
   "candidates_dir": "<host path>/candidates/u1", "blind": false,
   "candidates_sha256": {"enwiki-1001-2002": "<hex>"},
   "inventory": {"run_id": "r-…"}, "challenged_runs": [], "findings_dir": "findings",
   "tasks_dir": "<gate dir>/tasks", "log_dir": "<tend root>/gate-log",
   "orchestrator_db": "<tend root>/orchestrator.sqlite",
   "max_locations": 5, "min_absence_queries": 3, "judgment_rules": 2,
   "span_end_rule": 1,
   "vendor": {"literature_links": "<manifest sha256>",
              "formal_companion_annotations": "<manifest sha256>"}}
  ```

  `judgment_rules` is the version of the judgment rules (rule 9, §3 and §4;
  `cflib.vocab.JUDGMENT_RULES`) the run was planned under: the planner writes it
  into every gate.json, and the gate applies rule 9 when it is there and not
  otherwise, so a run planned before the rules — its gate.json has no such key —
  is gated, also at harvest, by the rules it ran under. Version 1 is R1–R7;
  version 2, written since the maintainer's PD5–PD9 (2026-10-20), adds PD6's
  whole part and PD7's junk value (§3, §4), so a run that names 1 is gated, also
  by the checkout's gate, without them; version 3, written from the statement
  cut's flip on, is that proposal's (below). The bench holds its gold to the same
  version (`references/locate-bench.md` §8). A version above the gate's own, or a
  value that is not a non-negative integer, is exit 3.

  `span_end_rule` is, in the same way, the version of the span-end rule (rule
  2's end check, §2; `cflib.vocab.SPAN_END_RULE`) the run was planned under. The
  planner writes it into every gate.json, and the gate holds an inventory
  passage's end to `locate_text.passage_end` when it is there and not otherwise:
  an inventory run planned before it — written by the `passage_ids.py` that
  dropped `until` — keeps the bound it was gated under (the end inside the
  layer, after the quote), at `cf harvest`, at `cf harvest --checkout-gate` and
  at any other re-gate of its corpus. What it binds is the documents of the run
  whose gate.json names it. A `locate` or `locate-challenge` document carries no
  span of its own — the gate reads each passage's span from the task manifest
  (`passages`, `records`), and the planner copies those from the frozen
  inventory or the challenged run — so in those modes the key binds nothing: a
  locate run planned now over an inventory planned before the rule (`cf
  plan-locate --inventory-run`, which reads the inventory's harvest receipt and
  never re-gates it) plans and gates as before, its frozen passages keeping the
  ends their own run's gate accepted. Exit 3 for a bad value, as for
  `judgment_rules`.

  **The statement cut's versions** (`docs/proposals/statement-cut.md` §2.2) are
  written from the proposal's flip on, and bind only a gate.json that names them,
  so every run planned before it is re-gated, tallied and exported as it was. The
  gate knew them before the planner wrote them: `judgment_rules` 3 (§3 and §4,
  "Under judgment rules 3"), `span_end_rule` 2 and `statement_cut_rule` 1, its own
  key, written in every mode until the flip of inv-pilot-1's fixes, which moved
  the last two to 3 and 2 (below). `span_end_rule` 2 ends an inventory passage that
  gives no `until` at the earlier of its **extent** — its block, the whole of a
  theorem box, and the displays its last sentence introduces
  (`locate_text.extent`, the proposal's §3.1) — and the start of the document's
  next passage, less the separator before that start when it is the earlier
  (§2), never before its quote's end (`locate_text.passage_end(...,
  rule=2)`); `until` ends it as before. `statement_cut_rule` 1 is **rule 10**, over
  an inventory document after rules 2 and 8 (§3.3), refusing: (a) two passages
  whose spans share a character; (b) a span that ends past its extent, read with
  `compound` when the passage has `conjuncts` (a passage runs from one block into
  the next only inside a theorem box, into the displays its last sentence
  introduces, or — a compound statement — over the items of one list; one that
  starts in no block may not run into one); (c) a span that ends strictly inside a
  formula; (d) `conjuncts` that are not absent (never `[]` or `null`) or 2–5
  entries of exactly `{id, text}`, ids `c1` … `cN` in order, each text one line of
  at most `MAX_CLAIM_CHARS` characters in the worker's own words alone and read
  one after the other (a bundle's conjuncts are exported), with a `shape` of
  `COMPOUND_SHAPES` present exactly when they are, `existence_uniqueness` with
  exactly two; (e) `conjuncts` on a passage not scoped `formal_math`; (f) a passage
  not scoped `formal_math` whose prominence is not `supporting`; (g) two passages
  with one span text, which would share a `passage_key`. A gate.json naming
  `statement_cut_rule` with a `span_end_rule` below 2 is exit 3 (rule 10's
  disjointness rests on the new end), as is a version above the gate's own. Rule
  10 refuses nothing on a passage's size: the harvest receipt's gate block gains
  `cut: {passages, formal, compound, conjuncts, by_shape, sentences, displays,
  multi_sentence, multi_display, by_source}` — every passage, the `formal_math`
  ones, the compound ones, their conjuncts and their shapes (the cut's clauses
  are `conjuncts + formal − compound`, one for each simple statement), and, over
  the formal ones, their
  sentences (`locate_text.units`: a `.`, `!` or `?` before whitespace and an
  upper-case letter or a formula, not after an abbreviation), their displays
  (`math` blocks inside the span) and how many hold more than one of each — only
  for a run whose gate.json names the rule.

  The **continued cut** (inv-pilot-1's fixes; the proposal's §2.2) is what the
  planner wrote from the flip of those fixes to the flip of the wide bound (the
  proposal's §7), in every mode, and what the gate knew before it did: `span_end_rule` 3 ends a passage as version 2
  does with the *continued* extent (§2), and `statement_cut_rule` 2 is rule 10
  with (b) read against the loose continued bound (`locate_text.Blocks.runs_on`:
  a block's last sentence with no full stop, never a list item's or a theorem
  box's, runs on over any `dd` or display blocks that leave it open into a plain
  paragraph that continues it; it admits only a longer `until`), its refusal
  naming "the paragraph that finishes that sentence after them", and (h): two or
  more `formal_math` passages of the source marked `prominence: subject`, all
  named, none chosen. Each statement-cut rule reads exactly one span-end rule
  (`STATEMENT_CUT_SPAN_END`: 1 reads 2, 2 reads 3), and `span_end_rule` 3 binds
  only beside `statement_cut_rule` 2: a gate.json naming any other pair is exit
  3, while no key, `span_end_rule` 1 or 2 alone and (2, 1) stay admitted. Under
  statement-cut rule 2 the receipt's `cut` block also counts `subjects` (formal
  passages marked subject), `continued` (formal passages ending past their
  extent without the continuation), `continuation_starts` (passages starting at
  a continuation's first word: the words after a display cut from their
  sentence), `continuations_unheld` (continuations after a block a formal
  passage shares a character with, that no passage holds), `loose_starts` and
  `loose_unheld` (the same over the paragraphs only the loose bound reaches,
  `locate_text.Blocks.loose_tails`: after a display the gate cannot see, a
  paragraph with no full stop or a container's sentence, where the default end
  stops before them and only an `until` keeps them), `connective_points`,
  `formal_with_connective` and `by_connective` (the connectives of
  `CUT_CONNECTIVES` inside formal passages, `locate_text.Blocks.cut_points`, an
  upper bound on the cuts there) and `connective_starts` (formal passages whose
  first word is a connective and that start inside a sentence: one that starts
  "Then…" after a full stop is a correct passage and is not counted). A locate
  run planned now over an inventory of (2, 1) — inv-pilot-1's, say — is
  admitted, since it reads only the inventory's conjuncts, and names the pair
  the planner writes now itself, which binds nothing in a locate document.

  The **wide cut** (`statement_cut_rule` 3, `STATEMENT_CUT_WIDE`; inv-pilot-2's
  repairs, the proposal's §3.3) is what the planner writes from the flip of the
  wide bound on (the proposal's §7), in every mode, beside `span_end_rule` 3, and
  what the gate knew before it did; a locate run planned now over an inventory of
  (3, 2) — inv-pilot-2's, say — is admitted, as one over (2, 1) is. It reads
  `span_end_rule` 3, as rule 2 does, so no default end, span or `passage_key`
  moves: it is rule 10 with (b) read against the **wide** bound
  (`locate_text.Blocks.runs_on(…, wide=True)`: the loose bound read across a
  footnote mark between two blocks, `[12]`, `[a]`, `[nb 3]` or `[note 3]`, which the
  renderer leaves outside every block span, and ended by a `dd` or a paragraph
  that finishes the sentence, by its first word or by a formula that opens it
  after a block ending with a word), its refusal naming "the paragraph or
  indented line that finishes that sentence after them (a footnote mark between
  them, or a formula opening that paragraph, does not end it)". Rule 1's and rule
  2's words are unchanged. Under it the receipt's `cut` block also counts
  `widened` (formal passages ending past their loose extent: the merges only the
  wide bound admits, each for a reader to judge), `wide_starts` and `wide_unheld`
  (the same as `loose_starts` and `loose_unheld` over the tails only the wide
  bound reaches, `locate_text.Blocks.wide_tails`; `wide_starts` is an upper bound,
  since a tail can be a correct start of its own), on a receipt line of their
  own. The text-only `dd` with a capital and a capitalised "Then" line between two
  displays stay refused, by design.

  From the flip, too, the planner copies into a locate task manifest's passage the
  frozen inventory's `statement`, and a compound statement's `conjuncts` and
  `shape`; into a challenge task's record the same three and `conditional`
  (anomaly A3: a prominence sample's other records, judged only beside an overturn
  of their passage); writes `locate.status_rule` (`STATUS_RULE`, 2) into a locate
  run's `run.yaml`, exactly when its `judgment_rules` is 3; and refuses `--preset
  locate` over an inventory run whose gate.json names no `statement_cut_rule`
  (inventory the sources again) and `--preset locate-challenge` of a locate run
  judged under `judgment_rules` below 3, whose documents carry their own claim
  lists. A planned run is never touched by either refusal. Before the flip
  (`locate.plan.statement_cut_planned`) every manifest and run.yaml was what it had
  always been.

  Three pins hold what the gate reads to the bytes the plan read; each is exit 3
  on a mismatch. `corpus_db.sha256`, the sha256 of the CHECK database when the
  run was planned, is compared when the gate first opens the database (rule 3:
  once per call, in the modes that read it), so a doctored copy never passes a
  phantom declaration — on the run host, on a contributor's machine or at a
  harvest's re-gate — and a gate.json naming a database with no pin is exit 3
  too; two builds of one universe are not byte identical, so a rebuild after
  planning is refused until a re-plan pins it. `candidates_sha256`
  (`{source_id: sha256}`, written by an inventory or locate plan, the modes whose
  gate reads the files) pins each candidates file the plan read (§5); with the
  key, a source whose file the gate reads and the map leaves out is exit 3. Each
  source pin's `views` (`{"formulas.json": <sha256>, "paragraphs.json":
  <sha256>}`, copied by the planner from the source's `render.json` once each
  file is checked against it, `locate.plan.source_views`) holds the two tables
  the gate opens beside `layer.txt` as `layer_sha256` holds the layer. A
  gate.json planned before the pins has no `candidates_sha256` and no `views`,
  and its candidates and tables are read as found, so the additions keep the
  schema `cf-gate-locate/1`; `corpus_db.sha256` was always written and is now
  compared. `--self-check` compares the database and every pinned candidates
  file.

  `orchestrator_db` lies in the tend root, so its directory is also where the
  gate reads the bridge's `attempts/<task-id>.json` (§1); on a run host the
  gate directory is `<root>/<run>-gate/`, beside the tend root
  `<root>/<run>-tend/`, not inside it.

  `inventory` names the frozen inventory a `locate` run reads (null for
  `locate-inventory` and `locate-challenge`); it reaches the host only through
  the task manifests, which the planner derives from it. `blind: true` is the
  G1-blind arm (curated candidates and gate rule 4 off). `challenged_runs` are a
  challenge run's locate runs. Two databases are named: `corpus_db`, the CHECK
  database — always the universe's full `corpus.sqlite`, which rule 3, the
  outcomes' join, the export and the bench gold read — and `tools_corpus_db`,
  the one the worker's two host tools search, `kind` `full` (the same file),
  `blind` (`corpora/<u>/corpus.blind.sqlite`, `cf plan-locate --blind`, refused
  when it was never built) or `planted` (`cf plan-locate --tools-corpus-db
  <file>`, a full build for the same universe, for the P tier; on a remote host
  it lands at `corpora/<u>/planted-<sha16>.sqlite`). Each is accepted by the
  universe sha it was built for (`corpus.query.check_universe`), never by its
  bytes; its `sha256` is a recorded pin. The prompts and the worker's allowlist
  put `--db <tools path>` inside the allowed prefix, and the tools themselves
  refuse any other, a repeated or an abbreviated `--db` (§8), so a worker cannot
  point them elsewhere; an inventory or locate plan refuses a source whose
  candidates file is missing or was retrieved from another kind of database
  (§5). `gate.local.json` is the same document with this machine's paths, and
  its `log_dir` is `runs/<run-id>/gate-local-log/`, so a gate run here never
  writes into the workers' log.

  The gate directory holds, every file required (a missing one is a planning
  error): `verify_locate.py`, `verify_findings.py` (which it imports; its
  hardened change-set reader), `locate_text.py`, `passage_ids.py`
  (`scripts/locate/passage_ids.py`: the tool an inventory worker runs to fill
  each passage's `span`, `head_words` and `item` from its quote, since nobody can
  count code points or hash a head by hand), `locate_records.py`
  (`scripts/locate/records.py`, the record builder, which needs no `cflib`), the
  two host tools of §8 (`corpus_show.py` imports `corpus_search.py`), `vendor/`
  with both vendored packages and their manifests, whose shas the gate verifies
  at start, `tasks/` and `gate.json`; an audited inventory plan adds the audit's three
  host tools (§13), and no other plan stages them. The tend root's `bin/` holds the bridge with
  `normalize_result.py`, `extract_final_json.py`, `agent_call.py` and
  `attempt_cap.py` — for every run, a faithfulness one too — and, for a locate
  run, `watchdog.py`. No library checkout is named to the challenger: nothing
  creates, transfers or verifies one on a run host. Rule 1
  builds every implied record with `locate_records` itself (`location_record`,
  `absence_record` over the absence's real receipt through `receipt_document`,
  `unresolved_record`, `cap_quote` and `check_note`; the challenge value,
  the location key and the joined proof status are placeholders, the source's
  citation fields too unless the `sources` entry carries `uri`, `title`,
  `container_title` and `licence`), from the gate directory — also when the
  harvest runs the run's own staged copy in-process (§10) — or from the
  checkout's `scripts/locate/records.py` when the checkout's gate runs
  in-process (`cf harvest --checkout-gate`). A sibling that is missing or does not import is exit 3 with one
  `FAIL` line naming it, never a traceback. Every SQLite database is opened
  through a percent-quoted `file:` URI with `mode=ro&immutable=1`.

  The directory is self-contained: every module these files import is the
  standard library or another file of the directory, so the gate runs as
  `python3 -I <gate>/verify_locate.py` with nothing else on the path, and a
  checkout missing one of them refuses to plan.

  The gate is invoked as `python3 <gate>/verify_locate.py --gate-config
  <gate>/gate.json --changed-files --base <branch> [--task-id <id>]` by tend's
  validation command and, with `--pre-merge` added, its pre-merge command,
  `--findings <file>` for one document,
  `--root <corpus>/<run-id> --findings-dir .` over a harvested corpus, or
  `--self-check`, which validates nothing and exits 0 or 3 (its modules, the
  vendored pins, the task manifests, the sources and `corpus.sqlite`) and is
  what `cf run`'s verify stage runs on the host, under `python3 -I`. **The
  command line is the log line's label**, so the gate refuses, with exit 3 and
  one `FAIL` line before it reads or logs anything: `--findings` with
  `--changed-files` (a mutually exclusive pair), `--pre-merge` without
  `--changed-files`, an abbreviated flag (`allow_abbrev=False`), a repeated
  `--gate-config` (argparse would keep the last), and, when a `gate.json` sits
  beside the script — in every staged gate directory — a `--gate-config` that
  resolves to any other file, since the directory's own config names the log the
  watchdog and the bench read. The checkout's `scripts/gate/` holds no
  `gate.json`, so a hand check there takes any config; the staged copy runs with
  its own only. It
  validates only the documents the change set touched (or names), loads only
  the sources, candidates files and task manifests those documents name, exits
  0 / 1 (rejected) / 3 (could not run) like `verify_findings.py`, prefixes each
  refusal with its rule (`[rule 2] …`), and its last line says which ownership
  rule judged the change set: `--task-id`/`TEND_TASK_ID`, the task id recovered
  from `orchestrator.sqlite` (whose schema is assumed, not known: any table
  with a worktree-path-like and a task-id-like column, one task id or none; a
  recorded path counts when it resolves to the worktree's directory; the
  database is opened `mode=ro`, because tend writes it in WAL mode and a live
  run's rows sit in the WAL until a checkpoint; `immutable=1` is the fallback
  only when no `-wal` lies beside it, where the main file is complete, or its
  directory cannot be written, where the line adds "read immutably: rows in its
  -wal may be missing" when a `-wal` is there — any other failed read is no
  task id, never an immutable read), or the single-stem rule, followed by why
  no task id was found. Without a task id a change set is one task's, and
  only `--pre-merge` (a batch of several workers' contributions) admits several
  tasks, each of whose documents must be an addition. A problem list is capped
  at 200 lines, the rest counted by rule, and a passage over the location cap
  is refused without its locations being read. Documents are parsed strictly:
  a duplicate key, a
  `NaN`/`Infinity` or a number that overflows to one, a lone surrogate, or
  nesting deeper than 64 levels is a rule 1 refusal, and so is a value of the
  wrong type where a word or an id belongs; a document is read only from a
  regular file, at most `max_document_bytes` of it (a symlink, device or fifo
  is refused under rule 1, and a change-set path that is not a regular file in
  the worktree under rule 7). Over a directory (a harvested corpus), JSON
  anywhere but `<task-id>/<source-id>.json` is a rule 7 refusal, never skipped.
  A `gate.json` value of the wrong shape (a non-positive integer, `kinds` that
  is not a list of strings, a path that is not a string), and a `formulas.json`
  or `paragraphs.json` that is not a list of entries, is exit 3.
- `run.yaml` — the faithfulness run's keys where they apply, plus a `locate`
  block: `{mode, sourceset: {id, version, manifest_sha256}, universe: {id,
  sha256, corpus_db_sha256, tools_corpus_db: {kind, sha256}}, inventory_run,
  challenged_runs, arm, agent_timeout, skill_commit, gate_files, config_shas, per_task,
  subject_only, seed, sample_rate, strong_sample_rate, absence_sample_rate,
  documents, passages, source_ids, skipped}` (the three rates null outside a
  challenge run; a run planned before the two later rates records neither),
  `status_rule` on a locate run planned under the statement cut (above; absent
  is status rule 1), and `audit: {rule, review_rounds}` on an inventory planned
  with `--audit-rounds 2|3` (§13; absent, the run is unaudited and its plan is the
  one an unaudited plan always was).
  `audit` is the decision table's version (`cflib.vocab.INVENTORY_AUDIT_RULE`) and R, the
  review rounds a document takes at most (`AUDIT_REVIEW_ROUNDS`); the flag belongs to
  the locate-inventory preset and the `agentbox-claude` bridge, and a re-plan keeps a
  run's audit, as it keeps its preset. An audited plan renders seven prompts (§13.9),
  stages the audit's three host tools in the gate directory, and exports
  `CF_AUDIT_RULE` and `CF_AUDIT_ROUNDS` in both wrappers with the audit's allowlists
  (below).
  `agent_timeout` is the `CF_AGENT_TIMEOUT` the plan staged into both wrappers
  (`--agent-timeout`, a duration the bridge reads, `100m`, `90s`, `2h` or seconds,
  within `cflib.vocab.AGENT_TIMEOUT_MIN` and `AGENT_TIMEOUT_MAX`, refused otherwise
  before anything is written), or null: the wrappers export none, and every call
  runs under the environment that launches tend, else the bridge's `45m`
  (`AGENT_TIMEOUT`); a run planned before the option records no key, which means
  the same.
  `arm` is `blind` with `--blind`, else `assisted`, and a locate plan whose
  inventory run, or a challenge plan whose challenged runs, are of another arm
  is refused; so is a challenge plan whose tools database is not the one its
  challenged runs' workers searched (their `tools_corpus_db`: the same kind,
  and for a planted one the same sha), named with the `--tools-corpus-db` or
  `--blind` that settles it, since a challenge of a P run that hunted in the
  full database would overturn absences with the declarations the ablation
  removed;
  `skill_commit` is the planning checkout's HEAD; `gate_files` is `{path under
  the tend root: sha256}` of every file staged into `gate/` and `bin/` (the task
  manifests aside). `config_shas` is the configuration behind those bytes, free
  of the run's own id and paths: `{prompts: {<templates/prompts/ file the plan
  rendered>: sha256 of its bytes}, gate_files: {<path as in gate_files>:
  sha256}, agents: {agent_host, model, allow_sandbox_bypass, agentbox_profile,
  attempt_cap, merge_target[, agent_timeout][, audit]}}`, whose `gate_files` leaves out `gate/gate.json`, the task manifests,
  the `bin/*-agent.sh` wrappers and any staged file whose bytes name one of the
  run's paths, and whose `agents` is what those wrappers run with that names no
  run, as the planner decided it (never read back from the wrappers): the
  bridge's agent CLI (`CF_AGENT_HOST`, null for a bridge that takes none), the
  model (`CF_MODEL`), whether the agents run with approvals and the sandbox off
  (`CF_ALLOW_SANDBOX_BYPASS`, `--allow-sandbox-bypass`), the agentbox profile
  they run under (`CF_AGENTBOX_PROFILE`, the workspace's `hosts.agentbox_profile`;
  null when the plan exports none and the bridge takes the host user's), the
  attempt cap (`CF_ATTEMPT_CAP`), the merge target (`CF_MERGE_TARGET`), and the
  agent timeout (`CF_AGENT_TIMEOUT`) only when `--agent-timeout` staged one, so a
  plan without it pins the map it pinned before the option, and the audit
  (`{rule, review_rounds}`, `CF_AUDIT_RULE` and `CF_AUDIT_ROUNDS`) only in an audited
  plan, whose `prompts` hash the audit's templates in place of the form reviewer's. Two plans of one
  configuration that differ only in run id
  record equal `config_shas` — what the bench's A/A check compares, so a run whose
  agents ran with the bypass is no replicate of one whose agents did not, while the
  rendered shas (`gate_files` and the top-level `prompts`) stay recorded pins.
  The planner never stamps `locate.campaign` (`cf campaign plan` does), and a
  re-plan of a stamped shard carries its stamp forward. `passages` counts what
  the tasks owe: an inventory task's distinct finder starts, a locate task's
  passages, a challenge task's records;
  a source the inventory covered with every passage scoped out is planned as
  `skipped` and still in `source_ids`. An inventory plan's `skipped` names each
  source it left out and why: a page that is no concept article (its flags),
  `not rendered`, or `parity refused` — a source whose render stands refused
  because its layer is not the producer's (§7, `Sourceset.parity_refusals`),
  which no render of the sourceset settles; a `--source` naming one is refused
  with the step that does (the producer's corrected delivery adopted into a new
  sourceset), as `cf locate candidates --source` refuses it (a candidates run
  over every source writes it no file and warns of it apart from the sources not
  rendered yet). `remote` names the host directories of
  the sourceset's sources, the universe and the candidates
  (`sourcesets/<id>/sources`, `corpora/<u>`, `sourcesets/<id>/candidates/<u>`);
  `repos` and `sources` are empty. `budget.max_cost` comes only from
  `--max-cost` (the workspace default is recorded as
  `budget.workspace_max_cost_usd`, which nothing reads as a cap), and is
  required before `cf run --execute` sends a locate run anywhere (proposal §6).
  A challenge whose plan requires no record (every one `not_required` or
  `sampled_out`) is still planned, with no task: its `challenge-plan.json` is the
  result, and it needs no harvest. A re-plan checks everything before it writes
  anything, and refuses a run id that names a run it reads or a run of another
  preset, and a harvested challenge run (one with a `RECEIPT.json`) whose
  `challenge-plan.json` it would change: a record's planned value that differs,
  or a record either plan lacks. Its verdicts answer the plan they were planned
  under, so a record the new plan requires would be owed and its passage refused
  at export, and a verdict on a record it no longer requires would be ignored. A
  planner change that requires more (every `subject` prominence, §4) is planned
  as a new challenge run. The run's counts land in the sourceset's `state.json`,
  through `Sourceset.record_state`, not in a companion's.

  `cf run` refuses a locate run planned for `local` (or given `--local`) before
  it writes anything: a locate run runs on an allowlisted host through tend. It
  refuses a `--max-cost` that differs from a recorded `budget.max_cost`, and
  records a given one there when none is, since the campaign, the sequencer and
  the watchdog read that key. It sends the run's source layers (only with
  `--include-source-text`, and then `run.sources_transferred` names them;
  without it the plan names the sources it expects to find on the host
  already), `corpus.sqlite`, the tools database when gate.json's
  `tools_corpus_db` names another file, and this run's sources' candidates
  files from its own kind directory (never the sourceset's whole directory; in
  the blind arm the blind ones, which its prompts name and which hold only
  lexical hints). Each candidates directory is left read-only, and a kind
  directory's transfer opens, creates if need be and closes again the
  per-universe directory around it, so the arms go to one host in any order.
  The verify stage checks each database against its pinned sha256 once, and
  every source layer the run reads against gate.json's `layer_sha256`, read
  from the host's own gate.json once that file is shown to be the staged one,
  and the `formulas.json` and `paragraphs.json` the gate opens beside it as JSON
  lists of objects and, when the source's pin carries `views`, as those shas (each failure named, as
  `sha256sum -c` names it), so a run whose layers were neither sent nor
  already there stops at verify instead of failing every gate call. No
  command sent to the host grows with the number of sources: the transfer
  creates the sources directory, and each layer's rsync its own. The
  frozen inventory is not sent: nothing on the host reads it, and its passages
  reach the workers and the gate in the task manifests. The preflight checks
  tend against `hosts.<host>.tend_version` when one is recorded, and prints
  `tend-version:`, `agentbox-version:` and `agent-cli-version:` lines (the CLI
  the run's `bridge` launches; `unknown` when a probe finds none), and an
  executed preflight records them at the top
  level as `versions: {tend, agentbox, agent_cli}`. `cf campaign plan` adds
  `campaign: <id>` to a shard's `locate` block (§11).

**On the host, outside every worktree**, in the tend root: `usage/` (Claude's
JSON envelope per agent call, `<task-id>.<role>.<UTC stamp>.<pid>.json`, kept as
a cost cross-check; a call that left none — a timeout, a signal, an empty or
non-JSON stdout — leaves a record of its own under that name, `{type: result,
subtype: error_<outcome>, is_error: true, total_cost_usd: null, duration_ms,
cf_agent_call: {outcome, role, task_id, slot, elapsed_s, timeout_s, exit,
signal, stdout_bytes, cost: unknown}}`, and prose stdout is kept beside it as
`.out`; an audited run's call names its step after the role,
`<task-id>.<role>.<step>.<UTC stamp>.<pid>.json`, and its record carries `step`, §13.9),
`attempts/<task-id>.json` (`{task_id, rejections, history}`:
the count of genuine reviewer rejections), `audit/<task-id>/` (an audited run only: the turn
machine's `state.json` and the round files, §13.9), `gate-log/gate.jsonl` (one JSON line
per gate call: `at`, `run_id`, `mode`, `task_id`, `task_id_from` (`--task-id`,
`$TEND_TASK_ID`, `orchestrator.sqlite…`, or null with no id), `caller` (which call
it was: `validation` for `--changed-files`, tend's validation command; `pre_merge` for
`--changed-files --pre-merge`; `findings_check` for `--findings`, an agent's or the
bridge's own check; `directory` for a directory of documents; a line from a gate
older than the field has none), `changed_files`
(whether the call judged a change set), `pre_merge` (whether it was tend's
pre-merge call, `--pre-merge`; a line with no `pre_merge`, from a gate older than
the field, reads as false), `ownership` (the call's ownership line, null without
`--changed-files`), `ownership_note` (why no task id was found), `ok`, `exit`,
`documents`, `by_rule`, `reasons`; the watchdog's gate-failure rate and the
bench's `gate_rejections` count only the validation lines (`changed_files: true`,
`pre_merge` not true), tend's validation calls, whatever their `task_id`, and
never a worker's or a reviewer's `--findings` check; tend's pre-merge re-check of
a batch it already validated is no second attempt, and both count it apart (the
watchdog's `pre_merge_calls` and `pre_merge_failed`, the bench's
`gate_rejections.pre_merge`), since a batch conflict is a real event; a line with
no `changed_files`, from a gate older than the field, counts for neither), and
the per-user slot directory the bridge's 15-slot
`flock` semaphore uses. The bridges (`scripts/run/agent_call.py` and
`scripts/run/attempt_cap.py` beside them) read `CF_SLOT_DIR` (default
`~/.cache/cf-agent-slots`), `CF_SLOTS` (15), `CF_AGENT_TIMEOUT` (`45m`; exported by the
wrappers only when the plan set `--agent-timeout`, which then overrides the launching
environment's),
`CF_ATTEMPT_CAP` (unset or 0 is off; a locate run sets 3, and the bridge refuses 1 or 2
with exit 78 before any agent call: the gate accepts a `by: attempt_cap` document at no
count under `cflib.vocab.LOCATE_ATTEMPT_CAP`), `CF_TASK_MANIFESTS`
(the `gate/tasks` directory), `CF_MERGE_TARGET` (the branch a capped task's
change set is read against, as the gate reads it; default `main`), `CF_EXTRA_TOOLS` (the allowlist entries for the two
host tools, each `Bash(python3 <gate>/corpus_search.py --db <tools path>:*)` —
and in an inventory run the passage-id tool — worker role only),
`CF_REVIEWER_TOOLS` (the reviewer's read-only default plus the gate's `--findings`
check, which its prompt has it run, as the whole gate command with `--findings`;
reviewer role only) and `CF_FINDINGS_GLOB`
(`findings/$TEND_TASK_ID/*.json` for the nested layout), all exported by the
generated wrappers in `bin/`; in an audited inventory plan (§13.9) both wrappers also
export `CF_AUDIT_RULE` and `CF_AUDIT_ROUNDS`, the worker's `CF_EXTRA_TOOLS` adds
`Bash(python3 <gate>/inventory_edit.py:*)`, the rendering's one-passage view
`Bash(python3 <gate>/inventory_render.py --gate-config <gate>/gate.json --show:*)` and
the validator `Bash(python3 <gate>/inventory_audit.py validate:*)`, and the reviewer's
`CF_REVIEWER_TOOLS` is `Read,Glob,Grep`, `git diff`, `git show`, `git log` and `git
status` (never `Bash(git:*)`), the gate's `--findings` check, the same view and the
validator: `strip`, `collateral` and `merge` are the bridge's, and no agent's list
admits them; the planner stages every helper the bridge runs
(`agent_call.py`, `attempt_cap.py`, `normalize_result.py`,
`extract_final_json.py`, and for a locate run `watchdog.py`) beside it in
`bin/`. Both roles' default allowlists in the bridge also carry the gate,
derived from `CF_GATE_CMD` and depending on the gate script: for the locate gate
the whole command with `--findings`, `Bash(python3 <gate>/verify_locate.py
--gate-config <gate>/gate.json --findings:*)`, the prefix of every gate
invocation the locate prompts render, so an agent runs its own check and never
tend's `--changed-files` call — the only one the log's readers count as an
attempt — and whatever it appends to that prefix to fake one is a command line
the gate refuses; for the findings gate of the faithfulness pipeline, whose
prompts render the bare `{{gate_command}}`, the program alone,
`Bash(python3 <gate>/verify_findings.py:*)`. (A codex worker has no allowlist and
could still call `--changed-files` on its own worktree: that skews a watchdog
rate, never a record.) A timeout exits 124 and
an empty or 409 envelope 75, with no contribution printed, so tend retries the
same worktree; only a `request_changes` from a call that completed, which the
reviewer stated — in its result file, or with none an explicit `verdict:` in its
prose (`normalize_result.explicit_verdict`) — counts as a rejection; a verdict
the normaliser fell back to for want of one does not. At the cap the bridge
puts every other path the rejected attempts changed since the merge base back
as it is there (a neighbour's task file, a stray file, a deletion; `.tend/`
aside) and keeps the task file's lines with `status: complete`, and the reviewer
side approves mechanically only a change set of exactly those documents and that
status. Both roles' allowlists carry that gate entry by absolute path: the
worker runs the check before committing and the reviewer to confirm the gate
passes. When an agent writes no result file,
the bridge's fallback runs that command with `--findings <file>` for each of the
task's own documents (the `CF_FINDINGS_GLOB` matches) and reports the gate's
refusals from stderr.

## 10. Harvested rows and passage outcomes

Harvest copies the host's `findings/` tree into `corpus/<run-id>/`, so a document
lands at `corpus/<run-id>/<task-id>/<source-id>.json` and the receipt's listing,
manifest and missing set are keyed by `<task-id>/<source-id>.json` (the listing
is `find -type f -print0`, the checksums `shasum` over it; a symlink is not
listed, not landed (the rsync runs `--no-links`) and not counted, and with
`--verify-sha` a landed file the source gave no checksum for is named in
`sha_unverified` and makes the receipt incomplete). Every file the receipt
counts is one the re-gate validated or names. The landed corpus is gated
again with `gate.local.json` by **the run's own gate**: the copy of
`verify_locate.py`, its siblings and `vendor/` that the plan staged in
`runs/<run-id>/tend-root/gate/`, each file first checked against `run.yaml`
`locate.gate_files` and then loaded in-process apart from the checkout's
modules of the same names, so a gate edited or upgraded since the plan never
judges the corpus its workers were judged by. A copy whose file is missing or
not the planned bytes, one that holds a Python module its plan did not stage
(the directory comes first on the import path, as on the host), or a run whose
`run.yaml` records no `gate_files`, fails the gate without running (the
receipt is not complete). The copy runs from its pinned sources only: the
import writes no bytecode into the run's directory and reads none from it
(bytecode is looked up under an empty directory of its own), so a `.pyc`
planted in its `__pycache__/` never runs in place of the source it claims to
be. The modules set aside while it runs are read off the directory (each
`<name>.py` beside the gate and each package under `vendor/`), not kept in a
second list. Nor does the re-gate take the plan's pins from the config alone:
`gate.local.json` must be the `gate/gate.json` the plan staged (itself the
bytes `gate_files` pins) in every field but the ones that name the layout's
paths (`corpus_db.path`, `tools_corpus_db.path`, `sourceset.sources_dir`,
`candidates_dir`, `tasks_dir`, `log_dir`, `orchestrator_db`), so its
`corpus_db.sha256`, `candidates_sha256` and source pins are the plan's; its
`corpus_db.sha256` must be `run.yaml` `locate.universe.corpus_db_sha256`; and
every task manifest in its `tasks_dir` must be the one `runs/<run-id>/planned.json`
records, with none missing and none added, so an emptied `curated` list (which
would hand a task no curated hit and turn rule 4 off) or a manifest for a task
the plan never had is refused. Any of these fails the gate without running,
and the refusal says what settles it: `cf harvest --checkout-gate`, which
re-gates with this checkout's gate instead, for a gate copy that cannot be told
from an edited one; putting back the plan's file, or a re-plan, for a config or
manifest that is not the plan's. With `--checkout-gate` the run's own copy and
its staged `gate.json` are not consulted, the `run.yaml` corpus pin still is,
and the manifests are held to `planned.json` when the run has one (a run
planned before `planned.json` reads them as found). `cf harvest` then says the
gate could not judge the findings, never that they failed it. The receipt's
`gate` block records which gate ran as `gate_code`: `{from: "run", dir, sha256,
files, files_sha256}` — the gate script's sha256 and the sha256 of the sorted
`<path>\t<sha256>` lines of the files checked — or `{from: "checkout", dir,
sha256}`. The receipt's
`locate` block counts `documents`, `by_mode`, `tasks`, `passages`, `verdicts`,
`unresolved_documents`, `attempt_capped_documents` and `unresolved_passages` —
UNRESOLVED on its own, never as not formalized. That re-gate writes no line to
any gate log: it is not a worker's attempt. For an inventory run the receipt's
`gate` block also carries `skipped` (§2: what the skips removed, in total and
`by_source`), and `cf harvest` prints it beside the gate's verdict.

A remote `cf harvest <run> --execute` also brings home, outside the findings
rsync (which carries `*.json` documents only) and outside `corpus/<run-id>/`
(whose tree is the receipt's manifest), into `runs/<run-id>/host/`
(`WorkspacePaths.run_host_dir`, `scripts/harvest/host_artifacts.py`):

| local file | from the host |
|---|---|
| `gate.jsonl` | `<log_dir>/gate.jsonl`, `log_dir` being the host `gate.json`'s (the tend root's `gate-log/`) |
| `usage.jsonl` | a campaign shard only: the sequencer's `<campaign dir>/usage-<run-id>.jsonl` (§11) |
| `canary.json` | a campaign shard only: the sequencer's `<campaign dir>/canary-<run-id>.json` (§11), `scripts/run/canary_scan.py`'s `{scanned, hits, canaries, canary_sha256}`, the last the sha256 of each canary's UTF-8 bytes it looked for; `cf bench score` refuses a scan that does not list its bench's canary (references/locate-bench.md §8) |
| `envelopes/` | the tend root's `usage/` `*.json`: the bridge's kept envelope of each agent call, and the record of each call that left none (§9) |
| `audit/` | an audited inventory run only (`run.yaml` `locate.audit`): the tend root's `audit/` `*.json`, the turn machine's state and round files (§13.9), which the receipt's `audit` block is read from (§13.10) |

A shard is a run whose `run.yaml` carries `locate.campaign` and which that
campaign's `campaign.yaml` lists; the campaign directory is the rendered
sequencer's `host_dir`. Each file is two steps: the host is asked whether it
is there (`ssh <host> test -f <path>`: 0 there, 1 absent, anything else a
failure to ask, said in the reason), and a file that is there is copied by a
plain `rsync -a` — no `--ignore-missing-args`, which macOS's rsync 2.6.9
rejects — and lands only once it arrived whole; the dry run prints those
commands with the findings ones. The receipt records `host_artifacts: {<local
name>: {remote, expected, fetched, path, sha256, bytes} | {remote, expected,
fetched: false, reason, kept_previous?}}` (a shard's rows also carry
`campaign`) and `host_artifacts_missing`, the EXPECTED files that did not come
home. `gate.jsonl` is expected — every merged task ran the gate, which logs
each call — so a harvest without it is **INCOMPLETE** and exits 1, a previous
harvest's copy notwithstanding, and so is an audited run's `audit/`, since every
task it merged was reviewed by the turn machine, which keeps its state there; `usage.jsonl` and `canary.json` are not, and
one the host lacks is `absent on the host`, never a failed harvest
(`envelopes/` and `audit/` are directories: `test -d`, then their `*.json` files only, each row
carrying `files` and the sha256 over its sorted `path<TAB>sha256` lines). The
receipt's `cost` block is priced from the first of: `--transcripts`, a landed
`usage.jsonl`, the landed `envelopes/`. From envelopes (`basis: envelopes`,
`cost.fleet.envelope_cost_block`) the dollars are the envelopes' own
`total_cost_usd`, the tokens are read per model from `modelUsage` (else `usage`
under the envelope's `model`, else the run's), and the block carries
`envelope_count`, `messages_by_model`, `usd_by_model`, `usd_from_price_table`
(the same tokens priced with `budget.prices`, a cross-check) and
`unpriced_calls`/`unpriced_seconds` (records of calls that left no priced
envelope, which make `usd` a lower bound), and, when any envelope's name carries an
audited call's step (§13.9), `by_step`: `{<step, or the role of a call that names
none>: {envelopes, usd, unpriced_calls}}`, which a run with no stepped envelope does
not get. Of a lower bound `cf harvest` warns `LOWER BOUND`, and `NO RESPONSES
priced` when no envelope was priced, on whichever block the receipt carries, and
`cf bench score` refuses such a receipt unless `--article-costs` prices every
article. `cf tally --locate` prints every
read run's cost line — its source and envelope count — and records it under
`cost` in the tally report. The harvest then merges `runs.<run-id>` of the sourceset's
`state.json` through `Sourceset.record_state` — `{status: harvested,
harvested_at, complete, gate_ok, locate: <the receipt's locate block>}` over the
row `cf plan-locate` wrote, whose fields stay. The faithfulness `cf tally`
leaves a locate corpus out (one whose `run.yaml` copy carries a `locate` block
or a locate mode, or whose receipt has a `locate` block), names it under
`locate_runs`, and `cf status` and `cf run --status` count a nested corpus by
the receipt's own listing.

Each document becomes finding rows that `cflib.hashing.finding_id` keys unchanged —
the library id is the `repo` and `<library>:<declaration>@<commit>` the
declaration list:

| document row | rows | `repo` | declarations |
|---|---|---|---|
| inventory passage | one `PASSAGE` | the sourceset id | none |
| located passage | one `LOCATION` per location | its library | `[<library>:<declaration>@<commit>]` |
| absent passage | one `ABSENCE` (beside one `LOCATION` per `relatedMatch`) | the universe id | none |
| unresolved passage, or an `unresolved` document | one `UNRESOLVED` | the universe id (inventory: the sourceset id) | none |

Every row also carries the `source_sha` and `layer_sha256` of its document.

`cf tally --locate --run <run> [--challenge-run <run>]…` computes them
(`scripts/locate/outcomes.py`) and writes `reports/locate-tally.<run-id>.json`
(the rows, the counts, and `gates`: `{run id: {role, receipt, complete,
gate_ok, harvested_at, receipt_sha256}}` for the locate run, its inventory and
each challenge run -- `receipt_sha256` the sha of the `RECEIPT.json` it read, so
a reader can tell outcomes of this harvest from those of an earlier one
(`cf bench score` refuses rows tallied before the last harvest, and so refuses
any tally that records no `receipt_sha256` -- every tally written before the
field existed, even one written after the last harvest: re-run
`cf tally --locate` on it); a challenge
run's `required`, the records its plan required of the run; and an inventory's
`skipped`, its receipt's `gate.skipped`, what its skips left unkept, which the
tally also prints -- each one not a complete gated harvest named in a warning) and
`reports/locate-outcomes.<run-id>.jsonl`, one row per passage after challenge:

```json
{"source_id": "enwiki-1001-2002", "item": "In-text #3fa91c07", "passage_kind": "theorem",
 "prominence": "central", "scope": "formal_math", "outcome": "located",
 "locations": [{"library": "alpha", "commit": "<40 hex>",
                "declaration": "Widget.exists_spanning_gadget", "relation": "exactMatch",
                "rank": 1, "confidence": "high", "proof_status": "proved",
                "statement": false, "location_key": "<hex>", "challenge": "survived"}],
 "coverage": null, "coverage_refuted": null,
 "absence": null, "challenge": "survived", "status": "formalized",
 "runs": {"inventory": "r-…", "locate": "r-…", "challenge": "r-…"}}
```

`proof_status` and `statement` are joined from `corpus.sqlite` at harvest (with
the declaration's `module`, `path`, `line`, `end_line` and `kind`), and a
`proof_status` a document wrote is ignored and named; `status` is
`cflib.vocab.derive_status` over the surviving locations, the absence and the
coverage claim as its records carry it (`records.coverage_ext`: no note, and
no claim at all while it is owed); an unresolved passage has `status: null`
(one that carries locations anyway is named as a problem). A passage whose challenge is still owed carries
`challenge: null`, and the export refuses it (hard rule 5). A row also carries
`span`, `head_words`, `section`, `source_sha`, `layer_sha256`, `reason`,
`unresolved_by` (`worker` — the default — or `attempt_cap`), `refuted` (the
locations a challenge removed), `coverage` (the passage's coverage claim:
`{complete, parts: [location_key, …], note, challenge}`, its ranks turned into
the joined locations' keys, or null) and `coverage_refuted` (a claim a challenge
refuted, with the verdict's `witness`, or null), `task_id`, and `problem` when something about
the passage is wrong. Under the judgment rules (§3, §4) a row also carries
`claims` — the claim list its locations' `clauses` name: the worker's, or the
challenger's once an overturn replaced the locations — and `challenge_claims`,
the challenger's own list when a verdict document gave one; `challenge_receipt`,
`{queries, query_clauses}`, the search an uphold of the passage's absence or
unresolved outcome lists; and `coverage_challenge_clauses`, a coverage confirm's
`part_clauses` (beside the claim, whose keys stay `COVERAGE_RECORD_KEYS`). A
location keeps the fields its document wrote (`clauses`, `instantiation`, `step`,
`convention`) and, when a confirm or an applied downgrade named them, the
verdict's `clauses` as `challenge_clauses` and its `step`, `convention` and
`instantiation` in place of the worker's; then a field the relation it is left
with does not take is dropped — a `convention` off a `closeMatch`, a `step` off a
`hasPart` or `narrowMatch` — so a downgrade from `closeMatch` to `broadMatch`
leaves no convention behind. Each is written only where a document wrote it,
so a run planned before the rules yields the rows it always did, and none is
exported (§6). What else can be wrong about a passage: a location that could not be joined, a document whose
universe or `source_sha`/`layer_sha256` is not the run's pin, an outcome that is
not one of the three, a challenge that no longer fits its record. A passage the
run planned (`planned.json`, §9; its count must be `run.yaml`'s) and no document
answered has `outcome: null` and `missing: true`, and an inventory passage scoped
out has `outcome: null`. A passage answered in another task's document than its
own is ignored there, and a declaration listed twice keeps its best rank. The
passage's `challenge` is one value over its records — its locations, the
refuted ones included, its coverage claim (a refuted one included), and its
passage-level and prominence records: owed when
any is owed, else `challenge_pending` when any is, else `downgraded` when any
changed, else `survived` when any was judged, else `sampled_out` or
`not_required`. A prominence overturn to `subject` or `central` reopens the
passage: §5 refutes such a passage's locations and hunts its absence, so each
of its records no verdict judged (planned `not_required` or `sampled_out` under
`supporting`) becomes `challenge_pending` — exported as it is, read by a
consumer as unchallenged, never as "no rule applied", and with no QID on its
subject end (§6). The reopen wins over a
coverage claim confirmed in the same run: it is applied before the claim
confirms its parts, so a part it reopened stays `challenge_pending`, owed its
own verdict under the stricter rule, while the claim stays `survived`.

A challenge run is applied only to a locate run its plan's `challenged_runs`
names, only through the plan records whose `run_id` is that run, and a verdict
counts only on a record the plan REQUIRED (one on any other is named and
ignored). A location's verdict applies only while its relation is the one the
plan saw, a passage-level one only while the outcome is, a coverage verdict only
while the claim names the parts the plan saw and only as `confirm` or `refute`
(otherwise the record is owed again); a claim one of whose parts a location
verdict refuted or downgraded no longer holds: it upgrades nothing and the
passage carries a `problem` until the claim is challenged again; a `downgrade` must name a strictly weaker relation (exact,
then close, then broad, then narrow/hasPart, then related: §4's order,
`cflib.vocab.RELATION_STRENGTH`); a prominence changes only
by an `overturn` to another prominence (one naming the passage's own is
ignored, and the record is owed); an `overturn` puts the challenger's
locations first in their own order, then the kept `relatedMatch` ones,
renumbered and capped at five; and a scope overturned with no location makes
the passage `formal_math` with no outcome and a `problem`, which the export
refuses. The counts' `unresolved`
is every answered passage with no status — the worker's and those a challenge
left without a location that counts.

**Under judgment rules 3** (the statement cut, `docs/proposals/statement-cut.md`
§1.3–§1.5 and §5), on a run of status rule 2 only — whose challenges are judged
under rules 3 — and only where a document wrote them; a run of status rule 1
yields the rows it always did, whatever keys its documents carry: a location keeps
its `conjunct_relation` and `tests` (`outcomes.CUT_LOCATION_FIELDS`, the last two of
`JUDGMENT_LOCATION_FIELDS`); a confirm's or applied downgrade's own `tests` replace
the worker's, and its `conjunct_relation` the location's when it is one of
`CONJUNCT_RELATIONS` and not stronger (another is a `problem` and the location's is
kept); a location left with a relation that does not take a field loses it
(`tests` off a location no longer held to a strong relation — its own, or a
`hasPart`'s `conjunct_relation`, `outcomes.held_relation`, so a strong part keeps
them — `conjunct_relation` off one no longer `hasPart`; a `convention` stays on a
`hasPart` whose `conjunct_relation` is `closeMatch`); a coverage confirm's
`part_relations` sit beside the claim as `coverage_part_relations`; a refute's
`unstated` and `part_reasons` on `coverage_refuted`; and an uphold's `special_case`
in `challenge_receipt`. A verdict on a record its challenge plan marked
`conditional: "prominence"` (its `challenge-plan.json` row; `references/vocabulary.md`
§1.11) counts only when the same challenge run overturned
that passage's prominence to `subject` or `central`; otherwise it is named in the
problems and ignored, as a verdict on any record the plan did not require is.

**Status rule 2** (`run.yaml` `locate.status_rule: 2`, which `cf plan-locate`
writes from the statement cut's flip on; a run.yaml without it is status rule 1,
and one naming any other value is refused): every row also carries the passage's
`statement`, `conjuncts` and `shape` from the frozen inventory (where it has them);
its `coverage` carries `relation`, the bundle's (its weakest part's: the weaker of
each part's `conjunct_relation` and a confirm's `part_relations` entry for it, a
challenge never raising a relation), and `status` is `derive_status(…, status_rule=2)` over the claim's
projection `{complete, parts, challenge, relation}`; a claim on a passage with no
`conjuncts`, or whose weakest part is no bundle's relation (no `conjunct_relation`,
or a `narrowMatch` of its conjuncts), no longer holds — it upgrades nothing and the
row carries a `problem`; an `unresolved` passage keeps its `cut_flag`
(`CUT_FLAGS`; another value is a `problem`); a challenge document's `fills` are
applied — `{"outcome": "absent", "receipt"}` makes a passage its verdicts left with
no location that counts `absent`, beside its `relatedMatch` locations, the absence
the challenger's (`survived`), and `{"outcome": "located", "locations"}` puts the
challenger's locations first (`survived`), as an overturn's; the row records `fill:
{outcome, challenge_run}`, and a fill on any other passage is a `problem` and
ignored — and a `located` passage the challenge still leaves with no location that
counts and no absence is **owed a hunt**: `challenge: "challenge_pending"` (unless a
record is owed outright), `owed: "hunt"` and a `problem`, so the export refuses it
by name. The counts gain three keys, and a run of status rule 1 has none of them:
`owed_hunt`, `by_cut_flag` (`{flag: n}` over the unresolved passages) and
`bundles` (`{written, survived, by_relation}`: the claims locate wrote, standing or
refuted; those `survived` that hold; and those by relation). Under status rule 1 a
passage a challenge left with nothing that counts has no status and is exported,
and a fill is named in the problems and not applied.
The bench (`references/locate-bench.md` §8) joins runs to gold by the row's
layer `span`, and reads it from the inventory documents when a row lacks it.

## 11. Campaigns

A campaign (`scripts/cfcmd/campaign.py`, logic in `scripts/run/campaign.py`) is a
sequence of locate runs, called shards, fed in waves. It plans nothing: each
shard is a locate run already planned, of at most 500 tasks, with a spend cap in
its own `run.yaml`, for the campaign's one allowlisted host.

`campaigns/<id>/campaign.yaml`:

```yaml
version: 1
id: wpm-tier-a
host: examplebox
shards:                       # in the order they run
  - {index: 1, run_id: wpm-a-1, mode: locate, tasks: 480, max_cost: 120.0, workers: 6, reviewers: 4}
tasks: 480
max_cost: 120.0               # the sum of the shards' caps
watchdog: {p90_cost_per_task: 4.1, p90_gate_failure_rate: 0.12, multiple: 2.0, min_tasks: 5,
           unpriced_usd_per_hour: 36.0,         # what an hour of a call with no priced envelope costs
           unpriced_usd_per_hour_pilot: wpm-pilot}  # the pilot it was measured on; null when given
pilot: wpm-pilot              # a run whose run.yaml records run.smoke_verified: true
transcript_dirs: <host dir>   # colon-separated, each absolute or ~/…; else hosts.<host>.transcript_dirs
canaries: <host path>         # the bench's canaries.txt on the host (absolute or ~/…), or null
sequencer: {path: …/host/sequencer.sh, sha256: <hex>, host_dir: <remote root>/campaigns/<id>}
```

A campaign id is letters, digits, `.`, `_` and `-`, starting with a letter or
digit. `plan` stamps `locate.campaign: <id>` into each shard's `run.yaml` (the
export seals it) and refuses a shard stamped with another campaign; once the
campaign is rendered a re-plan may only append shards. A re-plan reads every
shard's `run.yaml` again and keeps what it is not given: the watchdog's
thresholds (`multiple` 2 and `min_tasks` 5 the first time), the pilot, the
transcript directories and the canaries. `watchdog.unpriced_usd_per_hour` is
the rate the watchdog estimates a call that left no priced envelope at until
its shard has a priced envelope with a duration of its own: `--unpriced-usd-per-hour`
(a positive, finite dollars-per-hour), kept on a re-plan until the flag gives
another, else the pilot's highest priced dollars per hour among its envelopes
that carry `duration_ms` (`total_cost_usd` over `duration_ms`, both positive,
read from `runs/<pilot>/host/envelopes/`, where `cf harvest` lands the pilot's
`usage/`, by the watchdog's own `ledger`, which takes the hour from the
unrounded rate). A measured rate is measured again at every plan, so it
follows the pilot; `unpriced_usd_per_hour_pilot` names the pilot it came from
(null for the flag's). When the pilot has no such envelope, a rate measured on
that same pilot before is kept (its envelopes were pruned, say), and a pilot
the rate was not measured on leaves it null, never the last pilot's. A kept
rate is checked at every plan as the flag is: one that is not a positive,
finite number (a hand edit) is refused by name before anything is written,
and the flag replaces it. A transcript directory or canaries
path is absolute or `~/…` (the host's home), since render quotes every path
into the sequencer; anything else is refused, at `plan` for the flags and at
`render` for `hosts.<host>.transcript_dirs`. `render` refuses a campaign whose
`pilot` is missing or records no passed smoke test, since the sequencer
launches each shard at full concurrency with no smoke test of its own; one
with no `watchdog.unpriced_usd_per_hour` (or one that is not a positive,
finite number), since its watchdog would read every call that left no priced
envelope as $0 until a shard's first priced envelope; and a
shard whose `run.yaml` no longer matches its row (`max_cost`, `tasks`,
`workers`, `reviewers`) or its `locate.campaign` stamp: a shard re-planned
since is planned into the campaign again with `cf campaign plan`. Each shard
goes through `cf run <shard> --stage verify` first — transfer and verify,
nothing launched; a shard smoke-tested with `cf run` keeps its own tend run
going.

`render` writes `campaigns/<id>/host/`: `sequencer.sh` (from
`templates/campaign/sequencer.sh.tmpl`, shell, no cflib) beside copies of
`scripts/run/watchdog.py`, `scripts/cost/usage.py` and
`scripts/run/canary_scan.py`, and prints the transfer (`ssh <host> mkdir -p
<host_dir> && rsync …`) and start commands (every path in them shell-quoted).
On the host one sequencer at a time holds the campaign (`flock` on
`sequencer.lock`, which nothing it launches inherits, so a killed sequencer can
be started again); it reads a leading `~/` of a transcript directory or of the
canaries path against the host's `$HOME`, and stops before any shard when one
of them is not there. It runs each shard's `tend run` in order — shard k+1
starts when shard k's ends — refusing a root that already has a live tend run,
with the watchdog beside it (`--parent`: a killed sequencer's watchdog watches
its tend run to the end and exits within seconds of it; before a shard is
launched, `watchdog.py --stop-others` stops any watchdog still on its root, so a
relaunch is never watched at a killed sequencer's cap). A shard's transcripts
are the files that appeared under `CF_TRANSCRIPT_DIRS` (default:
campaign.yaml's) since the shard first started — the directories' listing when it first started (`.before`,
taken once: a restart keeps it) and after, never a file that was already there
or that an earlier attempt already exported — and that name its tend root,
entrypoint or gate directory; a new file naming none of them is another
session's and is left alone. After each shard, and before a resumed shard is
relaunched, the sequencer scans them for `CF_CANARIES` (default:
campaign.yaml's) into `canary-<run-id>.json` (`{scanned, hits, canaries}`, the
report `cf bench score --canary-report` reads), appends their usage to
`usage-<run-id>.jsonl` (a resumed shard keeps its earlier attempt's rows), and
deletes exactly those files, keeping them all when the scan found a canary or
the export failed, and keeping them unexported for the next attempt when the
scan could not run. A later clean scan is merged into a clean report (scanned
summed; `canary_sha256`, when present, the canaries both scans used); a report
with a hit, or one that cannot be read, is never replaced, and a later scan is
kept beside it in `canary-<run-id>.attempts/`. Its state in
`<remote root>/campaigns/<id>/` is keyed by run id (`shard-<run-id>.done`,
`.started`, `.before`, `.stopped`, `usage-<run-id>.jsonl`,
`canary-<run-id>.json`, `canary-<run-id>.attempts/`, `sequencer.log`). It
writes `STOPPED` (and the reason into `shard-<run-id>.stopped`) and stops when
a shard's last watchdog record shows a drain, or a reason to drain that no
drain acted on (a failed `tend-control drain`, or a cap crossed after the last
periodic check), or cannot be read, when the watchdog died during the shard,
when a canary was found anywhere in the run's reports, and when the scan could
not run. A stopped shard is not relaunched as it stands: once `STOPPED` is
removed, a shard whose canary reports hold a hit, or which the watchdog's dry
run already finds a reason to drain, stops again before its `tend run` starts.
`CF_ACCEPT=<run-id>[,…]` accepts such a shard as it stands: the sequencer
settles what it left, writes why it had stopped into `shard-<run-id>.done`, and
moves on to the next shard; while the canary scan of what it left cannot run,
the accept is refused and the campaign stops. `CF_ACCEPT` is the only way past
a canary hit: a higher cap (the shard re-planned, then planned into the
campaign and rendered again) passes only a stop at the cap. Only a shard that
has started (stopped, or interrupted) is accepted; an id naming a shard that never started, or no shard
of the campaign, is ignored with a log line, and that shard runs. The watchdog (model-free, stdlib) drains the root with
`tend-control drain` when the spend from `usage/` reaches the shard's cap — the
priced envelopes' `total_cost_usd` plus, for each call that left no priced
envelope, its elapsed seconds times the root's highest dollars-per-second
(before any priced envelope has a duration, `--unpriced-usd-per-hour`, which
the sequencer passes from `watchdog.unpriced_usd_per_hour`; the record says
`estimated_unpriced_usd: null` when neither exists) —
or the gate-failure rate of tend's validation calls in `gate-log/gate.jsonl` (its
`changed_files: true` lines without `pre_merge: true`, over at least 20 of them; the
pre-merge calls are recorded apart, as `pre_merge_calls` and `pre_merge_failed`) or
the cost per merged task (over at least `min_tasks`) passes `multiple` times the
pilot's p90; every check is a line of `<root>/watchdog.jsonl` and the last is
`<root>/watchdog.json` (a progress line it cannot print, on a full disk, is
lost, never the watchdog); `--min-gate-calls` and `--min-tasks` are at least 1. The
operator's machine only harvests receipts: `status` reads each shard's
`RECEIPT.json` (complete, the gate as ok, FAILED or UNGATED — a receipt with no
gate block was never gated — the `locate` block, the priced cost) and totals
the UNRESOLVED rate.

## 12. Carry-over maps

A re-cut is routine (the maintainer's answer of 2026-10-21): an inventory may divide a page
again, at the same revision or at a new one, and what was judged on the old cut carries to the
new one wherever its passage's text is unchanged, while only the changed passages are asked
again (`docs/proposals/statement-cut.md` §4). The map that says which is which is
`cf locate carry`, the code `scripts/locate/carry.py`:

```
cf locate carry --from <old inventory run> --to <new inventory run> [--source ID]…
                [--rows FILE --rows-out FILE]
```

It reads the two runs' gated inventory documents (§2; a run that is not a `locate-inventory`
run, or whose receipt is not complete and gated, is refused), the sourceset each run pinned and
each source's `layer.txt`, and writes one file, `reports/locate-carry/<from>/<to>.json`
(`cflib.paths.WorkspacePaths.locate_carry_json`), for every page both runs hold a document for,
or only the pages of the named `--source`s. The document holds no time and no path, so a re-run
over the same runs writes the same bytes.

**The keys** are `cflib.hashing`'s, one definition each (`references/vocabulary.md` §1.11):
`span_sha256` is the sha256 of the passage's layer text, `passage_key` the sha256 of
`<pageid>\n<span_sha256>`, and `cut_id` the sha256 of the canonical JSON of the page id, the layer
sha and the cut's spans, sorted. None holds a revision, a run or an item, so the same text on the
same page is one passage key under any cut and in any revision; an item may survive a re-cut that
a key does not (an end that moved), and the reverse.

**The map**, for one page, from spans and span text only:

1. Key every passage of both cuts, whatever its scope. Two passages of one cut with one key —
   the same text — refuse the page, since nothing could tell them apart (gate rule 10 (g) keeps
   that out of every cut made under `statement_cut_rule` 1; an older cut is checked here).
2. An old passage whose key a new passage has is **`unchanged`**.
3. Any other is placed on the new layer: at its own span when both documents were made on one
   layer (the same `layer_sha256`), else at the one occurrence of its text in the new layer
   (overlapping occurrences counted). A text the new layer does not hold exactly once is
   **`changed`**: it is never guessed onto a place.
4. With M the new passages that share a character with the placed span: none is **`removed`**;
   two or more is **`split`**; one that starts where it starts is **`end_moved`**; one that
   starts before it and holds the whole of it is **`merged`**; one otherwise is **`reshaped`**.
5. A new passage no old passage went into — no old passage has its key, and it shares no
   character with a placed old one — is **`new`**.

The fates are `cflib.vocab.CARRY_FATES` and `CARRY_NEW`. A page is also refused when a
document's `layer_sha256` is not the sha of the layer the sourceset holds for its source (its
spans are offsets into one layer), when the sourceset is missing a rendered view of the source or
holds one that cannot be read (named by the exception's type, never by a path), when a document
is unresolved, when a run holds two sources of the page, or when a span is not inside its layer;
each refusal names its reason.

```json
{"schema": "cf-locate-carry/1",
 "old": {"inventory_run": "inv-a", "span_end_rule": 0, "statement_cut_rule": 0},
 "new": {"inventory_run": "inv-b", "span_end_rule": 2, "statement_cut_rule": 1},
 "sources": null,
 "pages": [
   {"pageid": 1001,
    "old": {"source_id": "enwiki-1001-2002", "layer_sha256": "<hex>", "cut_id": "<hex>"},
    "new": {"source_id": "enwiki-1001-2002", "layer_sha256": "<hex>", "cut_id": "<hex>"},
    "same_layer": true,
    "passages": [
      {"passage_key": "<old key>", "item": "In-text #0000c0de", "span": [120, 380],
       "scope": "formal_math", "fate": "split",
       "into": [{"passage_key": "<new key>", "item": "In-text #0000c0de", "span": [120, 241],
                 "scope": "formal_math"},
                {"passage_key": "<new key>", "item": "In-text #0000a11d", "span": [243, 380],
                 "scope": "formal_math"}],
       "scope_changed": false, "conjuncts_changed": true},
      {"passage_key": "<key>", "item": "In-text #0000f00d", "span": [400, 466],
       "scope": "formal_math", "fate": "unchanged",
       "into": [{"passage_key": "<the same key>", "item": "In-text #0000f00d", "span": [400, 466],
                 "scope": "formal_math"}],
       "scope_changed": false, "conjuncts_changed": false},
      {"passage_key": "<key>", "item": "In-text #0000d00d", "span": [470, 498],
       "scope": "expository", "fate": "removed", "into": [],
       "scope_changed": null, "conjuncts_changed": null}],
    "new_passages": [{"passage_key": "<key>", "item": "In-text #0000b0b0", "span": [500, 540],
                      "scope": "formal_math", "fate": "new"}],
    "counts": {"new": 1, "removed": 1, "split": 1, "unchanged": 1}}],
 "refused": [{"pageid": 1002, "reason": "two passages of the old cut share one passage_key (…)"}],
 "unpaired": {"old": [1003], "new": []},
 "counts": {"pages": 1, "new": 1, "removed": 1, "split": 1, "unchanged": 1}}
```

| field | |
|---|---|
| `old`, `new` | each run, with the `span_end_rule` and `statement_cut_rule` its `gate.json` names (0: it names none, as a run planned before the rule) — what the cut was made under |
| `sources` | the `--source` ids, sorted, or null for every page |
| `pages[].old`, `.new` | the source each cut read, its layer sha and the cut's `cut_id` |
| `pages[].passages` | every passage of the old cut, by span: its key, item, span and scope as the old document has them, its `fate`, and `into` the new passages it went into, by span, each with its key, item, span and the scope the new cut gave it (one for `unchanged`, the same key; none for `removed` and `changed`) |
| `scope_changed`, `conjuncts_changed` | whether a passage of `into` has another `scope`, or another conjunct list (the sha256 of the canonical JSON of its `conjuncts`, `[]` for a simple statement, differs); null when `into` is empty |
| `pages[].new_passages` | the new passages of fate `new`, by span, with their scope |
| `counts` | each fate (and `new`) that occurs, per page and summed, and `pages`, the pages mapped |
| `refused` | the pages the map could not be computed for, by page id, with the reason |
| `unpaired` | the page ids only the old run (`old`) or only the new run (`new`) holds a document for |

**Rows.** `--rows FILE --rows-out FILE` (always together) copy a JSON-lines file of objects,
each with a `passage_key` of the old cut — known answers, a verdict export, anything keyed by a
passage — in its order to `--rows-out`, one canonical JSON object a line, each row gaining
`carry` (`{fate, into, scope_changed, conjuncts_changed}` of its old passage, or null when no
mapped old passage has the key) and `carried`. The map's own entries give each old item's key. A
line ends at `\n` only, so a U+2028, U+2029 or U+0085 that JSON holds unescaped inside a string
is read as written and written back so. A row may say `"reads_conjuncts": false` — it read no
conjunct — and the conjunct test below is then left out for it; any other value than a boolean
is refused, and a row without the key is held to the test. A row without a 64-hex `passage_key`,
a file that is not UTF-8, and a `--rows-out` inside the skill repo, naming the `--rows` file
itself, naming a directory or under a path that is a file, are refused before anything is
written; a row's other keys are kept as written.

**What carries** (`docs/proposals/statement-cut.md` §4.4; `locate.carry.carries(entry, *,
reads_conjuncts=True)`): a pair verdict, label or known answer on an old passage carries when the
passage is `unchanged`, the new passage (its one `into`) is `formal_math`, and — for anything
that read the conjuncts — `conjuncts_changed` is false. The old scope is no test: the same text,
scoped `expository` by the old cut and `formal_math` by the new one, carries what was said of it.
A bundle verdict read the conjuncts, and so did each of its parts' pair verdicts; a row that
cannot say is held to the conjunct test. A key built on the passage key is necessary for a carry
and not enough: `pair_key` is unchanged exactly when the passage key and the declaration are,
which says nothing of the conjuncts, and `bundle_key` is unchanged exactly when the passage key
and every part's pair key are. So a consumer that keys a verdict by `pair_key` or `bundle_key`
carries it on that key alone only when the verdict read no conjunct; one that read them (every
bundle verdict, every bundle part's pair verdict) also needs `conjuncts_changed` false from this
map, or the conjuncts of the two cuts compared. Everything else is asked again, the old verdict
shown as one on an earlier cut; a status is never carried, only derived again. Bench gold is
carried by `cf bench carry` over the same map (`references/locate-bench.md` §7).

**The parity table.** `tests/data/locate/carry-parity.json` (`cf-locate-carry-parity/1`) holds
hand-written cases — two layers, the old and the new passages, the expected fate of each old
passage, the items it went into and the new passages, and where a case pins them `carries` (a
row that read the conjuncts) and `carries_unread` (one that read none) — covering every fate, a
new revision, scope changes both ways, a conjunct change and a refused page, with a passage-key
test vector. A consumer that
ports the map (`docs/proposals/statement-cut.md` §8) runs the same cases; `tests/test_locate_carry.py`
runs them against `locate.carry.map_page`.

## 13. Inventory audit records (`cf-inventory-audit/1`, `cf-inventory-audit/2`)

An **audited** inventory run reviews each inventory document in rounds, after the worker has
committed it and the gate has passed it: an auditor (round 1) or a checker (round 2 on) judges
the cut against the inventory prompt's rules, a skeptic judges the flags it raised, a merge that
no agent runs turns the two into the round's verdicts and decides by a fixed table, and on a
rejection the worker repairs the document with recorded edit operations, each citing the verdict
it applies. `cf plan-locate --preset locate-inventory --audit-rounds 2|3` plans one
(`run.yaml` `locate.audit`, §9); a run planned without it stages, renders and gates
exactly as before. This section is the format the stage
is built on and the three host tools that read and write it, under **audit rule 1**, which
audits the passages; **audit rule 2** (§13.11, what a plan writes from its flip on) audits the
skips too, and §13.1-13.10 hold under it but where §13.11 says otherwise.

**The tools.** `scripts/gate/inventory_render.py`, `scripts/gate/inventory_edit.py` and
`scripts/gate/inventory_audit.py` are standard library only and import no `cflib`; the planner
copies them into the gate directory (§9) only for an audited plan
(`locate.tend_root.AUDIT_GATE_FILES`, pinned in `locate.gate_files` with the rest), and they run
there as `python3 -I <gate>/<tool>`, importing `verify_locate.py` and each other from the
directory. Each reads a source only through the gate's pinned view (`verify_locate.SourceView`):
a `layer.txt`, `formulas.json` or `paragraphs.json` that is not the pinned bytes is exit 3 in the
gate's own words. Exit 0 is success, 1 an invalid input (`inventory_audit.py`'s `INVALID:` lines)
or a refused operation (`inventory_edit.py`'s `REFUSED:` lines, nothing written), 2 an input that
cannot be read or a path refused, 3 a `gate.json` or a pinned file that cannot be read. The
vocabulary is `cflib.vocab`'s audit block, which `inventory_audit.py` copies and a test pins
(`references/vocabulary.md` §1.11).

**Where the files are.** In the task's worktree, under `.tend/audit/` (never committed: gate
rule 7 refuses any committed `.tend/` path). `inventory_render.py` and `inventory_audit.py` write
only into `<cwd>/.tend/audit/` and to `<cwd>/.tend/agent_result.json`, through no symlink;
`inventory_edit.py` writes only the document it is named, `findings/<task>/<source>.json` of the
working directory. Round `k`'s files are `r<k>.<name>.json` (and `.md`):

| file | written by | kind |
|---|---|---|
| `r<k>.render.part<i>.md`, `r<k>.render.json`, `r<k>.diff.md` | `inventory_render.py` | the rendering, its index, the diff since the last snapshot |
| `r<k>.document.json` | the bridge | the snapshot round `k` reviewed |
| `r1.audit.json` (or `r1.audit.part<i>.json`) | the auditor | `audit` |
| `r<k>.audit.json` (or parts), `k` ≥ 2 | the checker | `check` |
| `r<k>.check.part<i>.expect.json`, `k` ≥ 2 | the bridge | a check part's verdict and collateral ids |
| `r<k>.collateral.json`, `k` ≥ 2 | `inventory_audit.py collateral` | `collateral` |
| `r<k>.flags.json` | `inventory_audit.py strip` | `flags` |
| `r<k>.flags.part<i>.json` | the bridge | `flags`: the part's flags and disputes |
| `r<k>.skeptic.json` (from `r<k>.skeptic.part<i>.json`) | the skeptic (the bridge combines the parts) | `skeptic` |
| `r<k>.verdicts.json` | `inventory_audit.py merge` | `verdicts` |
| `r<k>.result.json` | the bridge | the contract the round's decision printed |
| `r<k>.ops.json`, `r<k>.repair.json` | the worker | `ops`, `repair` |

**Common rules.** Every file is a JSON object with `schema` (`cf-inventory-audit/1`; a rule-2
run's `cf-inventory-audit/2`, §13.11), `kind`,
`round` and `source_id`, the document's. A passage is named by its item, `In-text #<8 hex>`, of
the document the round reviewed; a verdict by `r<k>-v<nnn>`, a flag by `r<k>-f<nnn>`, a
collateral entry by `r<k>-x<nnn>`. A key a record does not take is refused, and so is a
`reason` of more than 30 words, an `action` or a `why` of more than 40, an `evidence` of more
than 25 and a `summary` of more than 80 (words are runs of non-space). `rule` is a rule of the
statement cut's prompt (`docs/proposals/statement-cut.md` §3.4), 1 to 10, and 0 only on a
`clean` verdict.

### 13.1 The rendering

`inventory_render.py --gate-config <gate>/gate.json --document <doc>` prints every passage in
span order: `[n] item · span · scope · kind · prominence · section`, then `TEXT` (the span, a
line break shown as `⏎`), `STATEMENT`, `SHAPE` with its conjuncts on a compound statement, and
`HINT` (`kind-other`: a formal passage of kind `other`; `kind-rule`: one the kind rule of §13.7
judges; `in-proof` and `in-example`: its section path names a proof or an example, `\bproofs?\b`
or `\bexamples?\b` case-folded; `connective-start`: its first word is one of
`AUDIT_CONNECTIVE_HINTS`; `widened`: it ends past its loose extent, so only statement-cut rule
3's wide bound lets it run, a merge the checker judges); before each passage a `GAP` line, the
layer text no passage holds (past 400 characters, its first and last 200); at the end the gap to
the end and the skips. `--out-dir .tend/audit --label r<k>` writes it as parts of at most about
`--part-bytes` (default `AUDIT_PART_BYTES`, 50,000), cut only between two passages (a passage
longer than that is a part of its own), each opening with the rendering's header and `Part i of
n: passages [a] to [b] of N.`, and `r<k>.render.json`, `{schema, kind: "render", label,
source_id, document_sha256, layer_sha256, passages, formal, part_bytes, parts: [{file, bytes,
first, last, items, formal}], diff}` — the coverage a chunked call is held to. With `--previous
<snapshot>` it also writes `r<k>.diff.md`: each passage changed (with the fields that changed),
added (with the earlier passages its span overlaps) or removed (with the passages now holding
its text), before and after, passages matched by item and then by start. `--show <item>
[--context N]` prints one passage with N characters (default 300) of layer on either side.

### 13.2 The auditor's file (`kind: audit`)

```json
{"schema": "cf-inventory-audit/1", "kind": "audit", "round": 1, "source_id": "enwiki-1001-2002",
 "passages": [{"item": "In-text #0000c0de", "origin": "statement_section", "verdict": "over_cut",
               "rule": 1, "merge_with": "In-text #3fa91c07",
               "reason": "one statement with the definition before it",
               "action": "merge into #3fa91c07; keep its start; end after 'spanning gadget.'"}],
 "expository_flags": [{"item": "In-text #00c0ffee", "rule": 7, "reason": "…", "action": "…"}],
 "per_spec_formal_estimate": 12, "patterns": ["…"], "subject_check": "…"}
```

Every `formal_math` passage the call judges (all of them, or a part's `formal` items:
`inventory_audit.py validate --kind audit --render-index r1.render.json --part <i>`, which
refuses an index of another document) has exactly one row, and no other passage has one. `origin` is one of `lead`, `theorem_box`,
`statement_section`, `proof`, `example`, `list_item`, `application_or_extension`, `history`,
`other`. `verdict` is one of `clean`, `over_cut` (on the passage that merges into `merge_with`,
which it must name), `under_cut`, `should_be_expository`, `should_be_non_math`, `redundant`,
`wrong_kind`, `wrong_prominence`, `statement_misstates`; a non-clean row gives its `reason` and
an `action` a worker can apply without judging again. `kind_other_ok: true`, on a passage of kind
`other` only and with a reason, says `other` is right for it, which exempts it from the kind
rule. `expository_flags` names passages not scoped `formal_math` that should be
(`should_be_formal` once flagged); `patterns` holds at most five entries. A row may carry `n`, the
rendering's number.

### 13.3 The flags file (`kind: flags`)

What the skeptic sees, and nothing else: `inventory_audit.py strip` writes `{flags: [{flag_id,
item, verdict, rule, merge_with, reason}], disputes: [{verdict_id, item, verdict, rule, action,
worker_reason}]}`, in reading order. In round 1 the flags are the auditor's non-clean rows and
its expository flags; the auditor's actions, patterns, estimate and clean judgments are withheld.
From round 2 they are the checker's `new` entries, and `disputes` are the verdicts the worker
reported `disputed`.

### 13.4 The skeptic's file (`kind: skeptic`)

`{verdicts: [{flag_id, upheld, verdict, rule, evidence, reason}], missed: [{item, verdict, rule,
merge_with, evidence, reason, action}], disputes: [{verdict_id, worker_right, reason}], examined:
[items], per_spec_formal_estimate, summary}`. Every flag and every dispute is judged exactly once.
An upheld flag names a defect (never `clean`) and its rule, and may correct the verdict; its
`evidence` is a quote of the layer the rule turns on, which the merge looks for on the layer
(whitespace and the rendering's `⏎` folded): an upheld flag without it counts as refuted.
`examined` names the formal passages no flag names that the skeptic judged; in round 1 it holds
at least `min(clean, max(AUDIT_SKEPTIC_FLOOR, ⌈AUDIT_SKEPTIC_SHARE · clean⌉))` of them (10, or a
tenth), and `missed` holds what it found wrong among any passages, each with its evidence. A
part file (`validate --kind skeptic --flags r<k>.flags.part<i>.json --render-index r<k>.render.json
--part <i>`) judges its part's flags and disputes, and its hunt is over its part's unflagged
formal passages, the floor theirs: the floor is superadditive, so parts that each meet theirs,
combined, meet the document's.

### 13.5 The checker's file (`kind: check`, round 2 on)

`{checked: [{verdict_id, status, reason}], collateral: [{id, harmful, reason}], new: [{item,
verdict, rule, merge_with, reason, action}], regressions: [{verdict_id, reason}]}`. Every verdict
of the last round's verdicts file is checked exactly once, with a status: `applied`, `partial`,
`missing`, `wrong` (applied in a way the spec makes wrong), `gate_refused_ok` (the worker quotes a
gate refusal the gate's bound really makes), `gate_refused_doubtful` or `disputed` (left to the
skeptic). Every collateral entry is judged harmful or not; `new` holds defects of changed passages
and their neighbours only, and `regressions` verdicts of an earlier round that the latest change
undid. A part file judges the verdict ids and the collateral ids (`r<k>-x<nnn>`) its `--expect`
list names, each exactly once; the parts together judge every one once.

### 13.6 The collateral file (`kind: collateral`)

`inventory_audit.py collateral --snapshot <r(k-1) document> --document <doc> --ops <ops>
--verdicts <r(k-1) verdicts>` re-applies the worker's operations to the snapshot
(`inventory_edit.apply`) and compares the result with the document on every field but `item`,
`span` and `head_words`: `{collateral: [{id, change, item, fields, before, after, …}]}`, where
`change` is `hand_edit` (a passage the operations do not produce, or one they produce that the
document lacks), `moved` (a passage no operation names whose span still moved: a neighbour's
merge or drop moved its default end), `uncited_op` (an operation citing a verdict the round did
not ask for) or `ops_refused` (operations the edit tool refuses on the snapshot; every change is
then a hand edit). This is how an edit no verdict names is found without a gate rule.

### 13.7 The verdicts file and the decision table (`kind: verdicts`)

`inventory_audit.py merge` validates every input against this section, then writes `{round,
review_rounds, final, decision_rule: "audit/1", run_id, task_id, source_id, document_sha256,
verdicts: [{id, item, start, verdict, rule, merge_with, action, origin, …}], refuted, set_aside,
history, kind_other_ok, kind_seen, counts, harm, estimates, rounds: [{round, actionable, harm,
document_sha256}], T, decision, revert_to, audit_failed, why}` and, with `--result`, the
reviewer's `.tend/agent_result.json` (`verdict`, `notes`, and on a rejection a `feedback_text` of
at most 1,500 characters naming the verdicts file). `verdicts` are the **actionable** ones, the
next repair's work, each with its `origin`:

- `auditor`: an auditor's flag the skeptic upheld with evidence (the skeptic's verdict, the
  auditor's action); `skeptic_missed`: a skeptic's missed entry with evidence, not `clean` and
  not already a verdict;
- `kind_rule`: the mechanical `wrong_kind` (rule 6) on every `formal_math` passage of kind
  `other` whose section path names a proof or an example, unless the auditor gave
  `kind_other_ok` with a reason or another verdict already settles its kind (`wrong_kind`,
  `should_be_expository`, `should_be_non_math`, `redundant`, `over_cut`); from round 2 only on
  passages no earlier `wrong_kind` judged (`kind_seen`, by start);
- from round 2, for each verdict the last round asked for: `applied` goes to `history`;
  `gate_refused_ok` to `set_aside`; `partial`, `missing`, `wrong` and `gate_refused_doubtful` are
  `carried`; a verdict the worker disputed follows the skeptic: `worker_right` sets it aside,
  otherwise it is `dispute_refuted`; a harmful collateral entry is a `revert` verdict
  (`collateral`); a regression re-issues the regressed verdict (`regression`); a checker's `new`
  entry the skeptic upheld with evidence is `check_new`.

`harm` is true when the round's check found a `wrong` application, harmful collateral or a
regression. With T = max(`AUDIT_THRESHOLD_MIN`, ⌈`AUDIT_THRESHOLD_SHARE` · formal⌉) (5, or 3% of
the formal passages), the decision (`approve`, `request_changes` or `revert`) is:

| round | decision |
|---|---|
| 1 | `request_changes` when any verdict is actionable, else `approve` |
| 1 < k < R | `request_changes` when at least T are actionable and fewer than round k−1's; otherwise as an approval below |
| an approval from round 2 on (the final round's always is one) | `approve` when `harm` is false, else `revert` to `revert_to`, the best reviewed snapshot: of the rounds without harm the one with the fewest actionable verdicts, the later on a tie (round 1 always qualifies) |

R, the plan's review rounds, is one of `AUDIT_REVIEW_ROUNDS` (2 or 3); `audit/2` adds one
clause for a middle round (§13.11). `merge --audit-failed
<reason>` records an audit whose output stayed invalid: no verdicts, `decision: approve`,
`audit_failed: true`, counted. The counts include the formal passages of kind `other`
(`kind_other_formal`) and those the kind rule judges (`kind_other_in_proof_or_example`).

### 13.8 The worker's ops and repair files (`kind: ops`, `kind: repair`)

`{ops: [{op, verdict | neighbour_of, …}]}`: each operation cites exactly one verdict of the
round, `verdict` (the verdict it applies) or `neighbour_of` (a neighbour's end or start that
verdict forces); one that cites none, or a verdict the round did not ask for, is refused.
`inventory_edit.py --document findings/<task>/<source>.json --ops <ops> [--base <snapshot>]
[--verdicts <verdicts>] [--write]` applies them in order, items naming the passages of `--base`
(the snapshot the round reviewed; without it the document), so the whole file can be applied
again after a gate refusal; without `--write` it is a dry run. The operations are `merge`,
`split`, `set`, `add` and `drop`, each field one of
`scope`, `passage_kind`, `prominence`, `statement`, `shape`, `conjuncts`, `until`, `concept`,
`quote` and `quote_end`:

- `merge {keep, drop, fields?}`: `drop` (an item or a list, never `keep` and no item twice)
  joins `keep`, which starts at the earlier start (with that passage's quote and finder), ends where the one ending later ended
  (its `until`, or none), and keeps its other fields, then `fields`;
- `split {item, at, new, earlier?}`: a new passage starts at the quote `at` inside `item`, ends
  where `item` ended, and takes `new` (`statement`, `passage_kind`, `scope` and `prominence`
  required, `section` and `fragment` allowed); `item` loses its `until` unless `earlier` gives
  one;
- `set {item, fields}`: `null` removes `until`, `shape` and `conjuncts` and empties `concept`;
  `quote` moves the start; `quote_end` cuts the quote's `exact` after its first occurrence of those
  words, the start kept and the `suffix` dropped;
- `add {after, quote, fields}`: a passage from text no passage holds, after `after` (null: first);
- `drop {item}`.

A `revert` verdict (rule 0, origin `collateral`) names an entry of `r<k>.collateral.json`, which
the revision stages beside the verdicts: its operations put the entry's `before` back (a `set` of
the changed fields, an `add` of a lost passage, a `drop` of an added one, a `set` of `until` for
a moved one), each citing the verdict.
A passage an operation scopes out of `formal_math` becomes `supporting` and loses `shape` and
`conjuncts` unless the operation says otherwise. The tool computes no offsets: `passage_ids.py
--write` places and re-keys the passages afterwards, and the gate judges the result.
`r<k>.repair.json` is `{entries: [{verdict_id, status, why, gate_line?}]}`, one per verdict the
round asked for: `applied`, `not_applied`, `gate_refused` (with the gate's refusal line,
verbatim, as `gate_line`) or `disputed` (the spec as written does not support the verdict; `why`
says so). `inventory_audit.py validate --kind audit|skeptic|check|repair|ops` holds one file to
this section and is the call an agent makes before it finishes.

### 13.9 The turn machine (`scripts/run/audit_turn.py`)

An audited run's wrappers export `CF_AUDIT_RULE` (the audit rule, 1 or 2) and `CF_AUDIT_ROUNDS`
(R, one of `AUDIT_REVIEW_ROUNDS`). With `CF_AUDIT_RULE` set, `bridge-agentbox.sh` hands every turn
of a task, after the attempt cap's check, to `bin/audit_turn.py` with its own allowlist, agentbox
profile, host and bypass; the turn machine runs as `python3 -I`, imports no `cflib`, calls every
agent through `agent_call.py --step` and prints tend's contract on stdout and nothing else. Only
a plan made with `--audit-rounds` exports the variable, and without it the bridge is what it was. It refuses (exit 78) a
host other than `claude`, an unknown rule, an R outside `AUDIT_REVIEW_ROUNDS`, a `CF_GATE_CMD`
that is not the locate gate with its `--gate-config`, a gate directory without the three host
tools, and a task of more than one document; a prompt file the run did not stage is exit 66.
The reviewer reads `reviewer-system.md` with `reviewer.md` (the auditor), `reviewer-check.md`
(the checker) or `reviewer-skeptic.md`; the worker `worker-system.md` with `worker.md` and, in a
revision, `worker-revision.md`. The planner renders the four reviewer prompts and the revision
from `templates/prompts/locate-inventory-{audit-system,audit,check,skeptic,revision}.md.tmpl`
(`locate.prompts.AUDIT_TEMPLATES`) and the worker's two as an unaudited inventory's:
`reviewer-system.md` is the rule card, the statement cut's rules 1-10 of
`docs/proposals/statement-cut.md` §3.4 as C1 to C10 (the kind rule under C6), what the gate's
bound allows under the statement-cut rule the plan writes (`locate.prompts.gate_bound`), the
material-is-data rule and the tools; the auditor judges every formal passage of its part, the
kind-hinted ones explicitly, the skeptic refutes by default with evidence, the checker judges
every verdict and collateral entry its part owes and every `widened` passage, and the revision
applies its session's verdicts through `inventory_edit.py --base r<k>.document.json --verdicts
r<k>.verdicts.json --write`, `passage_ids.py --write` and the gate, recording each verdict in
the repair file. Each has the bridge's `$TEND_*` slots and `$CF_AUDIT_ROUND`,
`$CF_AUDIT_ROUNDS`, `$CF_AUDIT_SOURCE` and `$CF_AUDIT_DOCUMENT` filled, and the turn appends a
`## This call` block (a worker's: `## This session`) naming what is staged, the one file the call
writes and its `inventory_audit.py validate` line.

**The state** is `<tend-root>/audit/<task-id>/state.json` (`AUDIT_TURN_SCHEMA`, `cf-audit-turn/1`):
`{round, rounds: {<k>: {document_sha256, head, worktree_id, steps, raw, decision, actionable,
harm, audit_failed, revert_to}}, revision, pending, approved, best: {round, sha256, snapshot},
last, rejections, counts: {reviewer_tampered, gate_resets, audit_failed, precheck_refused,
reverts, retries, unaudited, repair_invalid, no_progress}, events}`. The round files of §13's table are
beside it in `<source-id>/`, with `raw/` (each call's envelope) and `discarded/` (a round's files
whose document changed under it). The worktree's `.tend/audit/` is their mirror: before every
call it is cleared and holds exactly what that call may read, and the call's output is copied
back after, so a wiped `.tend/` loses nothing; a `.tend` that is a symlink is refused.

**A reviewer turn of round k:**

1. A revert pending: approve mechanically when the document's sha256 is the snapshot's, else ask
   for it again (unrecorded; after `AUDIT_REVERT_ATTEMPTS` the turn stops, exit 1). A document
   approved before, or a rejection recorded for this very document, is answered again as it was.
2. The precheck, the gate's `--findings` check: exit 1 is a `request_changes` the bridge writes
   and does not record, and the round does not move; any other failure is infrastructure (75).
   A document whose sha256 is the one round k − 1 reviewed and rejected (a gate-loop reset, or a
   revision that changed nothing) is approved mechanically, with no agent call and counted
   (`no_progress`): there is nothing to check, and the last round's ops file is never re-applied
   to it as collateral.
3. The snapshot `r<k>.document.json`. An `unresolved` document, or one with no passage, is
   approved unaudited (counted).
4. The rendering in parts (`inventory_render.py --out-dir .tend/audit --label r<k>`, from round 2
   with `--previous r<k-1>.document.json`; `CF_AUDIT_PART_BYTES` overrides the part size).
5. Round 1: one auditor call per part, writing `r1.audit.part<i>.json`, validated against its
   part (`--render-index r1.render.json --part <i>`). From round 2: `collateral` (with the
   worker's ops when they parse as JSON), then one checker call per part that owes a verdict of
   the last round (placed by its start) or a collateral entry, writing `r<k>.audit.part<i>.json`
   against `--expect r<k>.check.part<i>.expect.json`; the worker's repair file goes on only when
   it validates, else it is counted (`repair_invalid`).
6. `strip`, then one skeptic call per part with flags or disputes (round 1: also any part with an
   unflagged formal passage, the hunt's), each seeing the part's rendering and
   `r<k>.flags.part<i>.json` and never the auditor's or the checker's file, validated against
   its part; the parts are combined into `r<k>.skeptic.json` (a missed entry once per item and
   verdict, an examined passage once, the estimates summed, the summaries joined within
   `AUDIT_SUMMARY_WORDS`).
7. `merge --gate-config`: `r<k>.verdicts.json` and the reviewer's result.

An agent step whose output is missing or invalid is called again `AUDIT_OUTPUT_RETRIES` time, with
the validator's lines and its last output staged; still invalid, or files the strip or the merge
refuse, or a rendering or collateral that exits other than 0 or 3 (a failure on its input, which
a retry would only repeat), and the round is `merge --audit-failed`: approve, counted. A step that validated for the
round's document is not called again: a call that fails as infrastructure exits 124, 75, 64 or
128 + N, tend retries the turn, and only what is left is paid for. Around every reviewer call the
tamper guard compares HEAD, the tracked changes and the untracked files outside `.tend/`; a
change is undone (`git reset --hard`, then `git clean -fd -e .tend`) and counted.

**The decision.** `approve` ends the task's audit. `request_changes` moves the round to k + 1,
opens the revision of round k's verdicts, and is recorded as a genuine rejection
(`attempt_cap.record`, with the round's last agent envelope); the merge never rejects in the
final round, so a task takes at most R − 1, under `LOCATE_ATTEMPT_CAP`. `revert` is a request the
bridge writes and does not record: the next worker turn writes `r<j>.document.json`'s bytes to
the document and commits them with no agent call, and the next reviewer turn approves when the
sha256 matches. After every merge `best` names the best reviewed snapshot: of the rounds whose
check found no harm, the one with the fewest actionable verdicts, the later on a tie.

**A worker turn.** The first call, and every re-run of it before a review, is `worker.md` alone,
with no step; a re-run with a discussion adds `## THIS IS A RE-RUN — the validation gate refused
your last commit` and the last `AUDIT_DISCUSSION_TAIL` bytes of `.tend/discussion.md` (never "a
reviewer rejected", since no reviewer did). A revision runs sessions of at most
`AUDIT_REPAIR_VERDICTS_PER_SESSION` verdicts, each a fresh `revision` call of `worker.md`,
`worker-revision.md` and its block (its verdict ids; `r<k>.verdicts.json`, `r<k>.document.json`,
`r<k>.collateral.json` from round 2, and the ops and repair files so far staged), the worker's `r<k>.ops.json` and `r<k>.repair.json`
copied back after each; an interrupted revision resumes at its next session. A committed revision
that comes back without a review was refused by tend's pre-review gate or by the precheck: it is
re-run on the gate's words, and at the `AUDIT_GATE_RESETS`th refusal the worktree is reset to
round k's reviewed commit with no agent call, so the next review finds no progress and
approves.

**The cap.** In an audited run `attempt_cap.py` never writes UNRESOLVED when the state names a
best snapshot: the worker side writes its bytes (checked against `best.sha256`), puts every other
path back as the unaudited cap does and commits, and the reviewer side approves exactly that
change set. `attempt_cap.py count` prints a task's count.

**The envelopes** of an audited run's calls name their step (`AUDIT_STEPS`: `audit`, `skeptic`,
`check`, `revision`); the watchdog reads both names, counts every envelope towards the spend,
and records `spend_by_step` for a root that has stepped envelopes.

### 13.10 The harvest: the receipt's `audit` block

A remote `cf harvest` of an audited run also fetches the tend root's `audit/` (its `*.json`:
each task's `state.json` and round files, §10) to `runs/<run-id>/host/audit/`, an expected
artifact, and `scripts/harvest/audit_summary.py` reads it beside the corpus that landed. A harvest
that fetched nothing (a local `--from`, a failed transfer) reads the copy a previous harvest left
there (`source: kept`). The receipt then carries, for a run whose `run.yaml` names
`locate.audit` and for no other (an unaudited receipt is what it was):

```
audit: {rule, review_rounds, source: fetched | kept | null, documents, audited,
  outcomes: {approved, reverted, audit_failed, not_audited, capped, stale, unaudited},
  stale: [source ids], unaudited: [source ids], not_harvested: [task ids], unreadable: [paths],
  rounds: {"<k>": {documents, decisions: {approve, request_changes, revert}, rejected,
                   audit_failed, harm, counts: {<the verdicts files' counts, summed>, by_verdict}}},
  residual: {documents, actionable, by_verdict, formal, per_formal,
             kind_other_formal_before, kind_other_formal_after,
             kind_other_in_proof_or_example_before, kind_other_in_proof_or_example_after},
  turns: {tasks, rejections, <the states' counts, summed>},
  per_source: {<source id>: {task_id, outcome, document_sha256, rounds, final_round, decision,
                             reviewed_round, actionable_by_round, residual?, formal?}},
  cost_usd: {worker_initial, worker_revision, audit, skeptic, check} | null, cost_calls,
  cost_unpriced_calls}
```

- **The outcome** (`AUDIT_OUTCOMES`) of a harvested document is judged by its sha256: the
  state's `approved` sha256 makes it `approved`, or `reverted`, `audit_failed` or `not_audited`
  when that approval was a revert, an audit whose output stayed invalid, or an unresolved or empty
  document; the state's `best` sha256 makes it `capped` (the audited cap's restore); a task the
  audit saw whose bytes match neither is `stale`, and one the audit directory holds nothing of
  `unaudited`. `cf harvest` warns of each, and of an audited run with no `audit/` here at all;
  neither changes `complete`, which the expected artifact's absence already decides.
- **The rounds** sum each round's verdicts files (§13.7): the decisions, the files' `counts`
  (round 1's `flagged`, `upheld`, `refuted`, `missed_added`, `kind_rule`, …; a later round's
  `status_<s>` for each check status, `collateral`, `collateral_harmful`, `regressions`, `new`,
  …), their `by_verdict`, and how many found harm.
- **The residual** is the verdicts of the round that reviewed the harvested bytes, the latest
  whose `document_sha256` is the document's (the last round's, or the one a revert or the cap
  restored), per formal passage; the kind rule's counts are taken before (round 1) and after
  over the same documents.
- **The cost** is the fetched envelopes' dollars by step (`cost.fleet.envelope_by_step`,
  `AUDIT_COST_STEPS`): a worker envelope named with no step is the first call
  (`worker_initial`), `revision` its revisions; a reviewer envelope with no step would be
  named under `reviewer`. The receipt's own `cost` block, priced from those envelopes, carries
  the same split as `by_step` (§10).

### 13.11 Skips under rule 2 (`cf-inventory-audit/2`)

Rule 1 audits passages only, so a skip a worker should not have made — a proof or a worked
example skipped whole, a display skipped from the sentence that introduces it — passes every
round unseen. **Audit rule 2** (`INVENTORY_AUDIT_RULES`, the `--rule 2` of every host tool,
`CF_AUDIT_RULE=2`, `run.yaml` `locate.audit.rule: 2`) audits the skips too: each is judged once,
a skip that hides statements becomes a verdict, and the repair turns it into passages. The
files of a rule-2 run carry `schema: cf-inventory-audit/2`; every rule-1 behaviour above is
unchanged, and a tool called without `--rule` is rule 1, byte for byte (a rule-1 file with a
rule-2 key is refused as any unknown key is, and a `/2` file under rule 1 by its schema).

**Terms.** A *skip* is a `skips` entry, `{span, reason}` (§2). A math paragraph (a
`paragraphs.json` entry with `math: true`) is *held* when a passage's span overlaps it; one a
skip overlaps and no passage holds is *under a skip*, what the gate's `skipped.math_paragraphs`
counts. The *furniture* is the lead (an empty section path) and the back matter (a top heading
of `AUDIT_BACK_MATTER`, case-folded). A section, not furniture, is *mostly skipped* when at least
`AUDIT_MOSTLY_SKIPPED_MIN` (2) of its math paragraphs are under a skip and more than
`AUDIT_MOSTLY_SKIPPED_SHARE` (half) of them are: the soft threshold, reported and never
refusing. A skip is named `r<k>-s<nnn>`, round k's numbering of the snapshot's skips in span
order (ties by their place in `skips`); the name holds for that round only.
`inventory_audit.skip_facts` computes what everything below reads of a skip: its outermost
blocks (each math when an entry inside it is, and held or not), its math paragraphs and those
under it and no passage (`unheld`, and `unheld_outside` the furniture), its sections, and
whether it is furniture (every block in the lead or the back matter), in a proof or an example
section, in a mostly skipped one, *partial* (it starts or ends inside a block), *inside* a kept
passage (a finder nested in one), and whether the **skip rule** applies: it overlaps a math
paragraph no passage holds, outside the furniture, whose section path names a proof or an
example (the kind rule's `\bproofs?\b|\bexamples?\b`) or is mostly skipped.

**The rendering** (`inventory_render.py --rule 2`). Each skip that does not lie inside kept
passages is a SKIP block in place, in the gap before the next passage (the gap's text split
around it):

```
SKIP r1-s002 · [5200, 6100] · 900 chars · 4 block(s), 3 math paragraph(s), 3 held by no passage · Proof
  REASON: proof narration; it argues the theorem above
  ¶ [5200, 5310] p · math · held by no passage: Suppose W is a finite widget with n nodes.
  ¶ [5311, 5420] p · math · held by no passage: Then every gadget of W has at most n edges.
  ¶ [5421, 5600] dd · math · held by no passage: e(G) ≤ n
  ¶ [5601, 6100] p: This completes the proof.
  HINT: skip-rule in-proof mostly-skipped
```

One `¶` line per outermost block, its span clipped to the skip (`(block [a, b])` when the block
runs past it), so a repair copies spans into `unskip`: a math block in full to 1,500 characters
(its first and last 700 past that), any other block as a `GAP` line is clipped, a run of three or
more list items with no math one `… [a, b] N list items, no math: <first> … <last>` line, and
text no block holds (a heading) a `⋯` line. The hints are `AUDIT_SKIP_HINTS`: `skip-rule`,
`in-proof`, `in-example`, `mostly-skipped`, `partial` and `furniture`. Past
`AUDIT_SKIP_BLOCK_BYTES` (20,000) every further block is one line of its first and last 100
characters and the block ends `… the rest in full: --show <id>`. A skip whose text lies inside
kept passages is one entry of a closing `SKIPS inside kept passages: <id> [a, b] <reason>; …`
line, judged like any other. The header gains `Skips: N (r1-s001 to r1-s0NN); M math
paragraph(s) lie under them and no passage, O of them outside the lead and the back matter.`
(`Skips: none.` without one) and, when a section is mostly skipped, `ATTENTION: mostly skipped
sections: <section> <under> of <math>; …`. `r<k>.render.json` gains `rule: 2` and `skips`, one
row per skip `{id, span, reason, part, blocks, math, unheld, unheld_outside, furniture, rule,
sections, inside}`, and each part's row its `skips`: the part that shows a skip owns it (a skip
just before a part's first passage is that part's; the tail's and the closing line's are the last
part's). `--rule 2 --show r<k>-s<nnn>` prints one skip whole, with `--context` characters of
layer before and after it, through the same command prefix as `--show <item>`. With `--previous`
and `--previous-verdicts r<k-1>.verdicts.json`, the diff gains a `SKIPS` section — every skip of
the snapshot removed (`REMOVED SKIP`), changed (`CHANGED SKIP`, before and after) or added
(`ADDED SKIP`) — and one `SKIP VERDICT <id> on <skip> [a, b]: <n> math paragraph(s) unheld
before, <m> now; <h> of its <s> statement(s) now start inside a passage; the skip is gone.` (or
the skips that remain in its span) line per skip verdict of the last round.

**The auditor's file** gains the required `skips`: one row for every skip its part owns (all of
them in a whole-document file), each judged exactly once:

```json
"skips": [
  {"skip": "r1-s002", "verdict": "skipped_statements", "rule": 6, "merge_with": "",
   "reason": "a proof skipped whole; two steps assert facts",
   "action": "unskip r1-s002; add the two statements; the narration paragraphs become expository",
   "statements": [{"at": "Then every gadget of W has", "until": "at most n edges.", "passage_kind": "theorem"},
                  {"at": "e(G) ≤ n", "until": "", "passage_kind": "theorem"}]},
  {"skip": "r1-s001", "verdict": "clean", "rule": 0, "merge_with": "", "reason": "", "action": "",
   "statements": []}]
```

`verdict` is `clean` or `skipped_statements` (`AUDIT_SKIP_VERDICTS`) and nothing else, and no
passage row takes `skipped_statements`. A `skipped_statements` row names its rule (6 for a proof
or an example, 3 for a display, 1 or 2 elsewhere), its reason and action, and at least one
statement or a `merge_with`: the kept passage whose sentence the skipped text finishes (the
partial case, a display skipped from its sentence); `statements` are what it hides, as the
auditor proposes to cut them, at most `AUDIT_SKIP_STATEMENTS_MAX` (40), each `at` 3 to 12 words
verbatim from the layer inside the skip where it starts, `until` at most 12 (its last words, or
""), `passage_kind` one of the inventory's kinds. A clean row names rule 0, no statement and no
`merge_with`; `skip_ok: true`, on a clean row only and with a reason, exempts a skip the skip
rule judges (HINT `skip-rule`) from it, as `kind_other_ok` does a passage from the kind rule.
`validate --kind audit --rule 2 --render-index r1.render.json --part <i>` holds a part to the
skips its index row names; without an index, `--gate-config` reads the facts from the source.

**The flags and the skeptic.** `strip --rule 2` adds one flag per `skipped_statements` row,
`{flag_id, item: "", skip, span, verdict, rule, merge_with, reason}`, placed among the passage
flags by where it starts; its statements and action are withheld. The skeptic upholds a skip
flag only as `skipped_statements` (and a passage flag never so), with `evidence` (at most 25
words, verbatim) that states something **inside the skip**: the merge looks for it in the skip's
own text, the layer between its span's ends, and evidence elsewhere refutes the flag
(`no_evidence`). It refutes a skip of narration only (PD5), of furniture, or of a statement a kept
passage already states (C10). Its file gains `examined_skips` and `missed_skips`: in round 1,
`examined_skips` names **every** skip of its part no flag names that holds a math paragraph no
passage holds outside the furniture (all of them, not a floor), and `missed_skips` those it finds
hiding statements, in the auditor's row shape with `evidence`. A part with such a skip gets a
skeptic call even with no flag and no formal passage. A dispute of a skip verdict carries its
`span`.

**The merge** (`merge --rule 2`; round 1 and later). A skip verdict's record is `{id, item: "",
skip, skips, span, start, verdict: "skipped_statements", rule, merge_with, action, origin,
statements, unheld, …}`: its target is the span, `skips` the skips of the round's snapshot that
overlap it (`skip` the one when there is one), `unheld` the math paragraphs under them no passage
holds; verdict ids number every verdict by where its target starts. An upheld skip flag is an
`auditor` verdict with the auditor's action and the statements whose `at` starts inside the skip
(the others dropped and counted, `statements_dropped`; the verdict stands on its evidence); an
evidenced missed skip is `skeptic_missed`; then **the skip rule** adds `skipped_statements`
(origin `skip_rule`, rule `AUDIT_SKIP_RULE_RULE`, no statements, `AUDIT_SKIP_RULE_ACTION`) on every
skip it applies to that no skip verdict of the round already settles, the auditor did not exempt
with `skip_ok`, and that lies inside no span of `skip_seen` (the spans an earlier round exempted
or set aside). A refuted skip flag does not stop it. From round 2, a skip verdict the check
leaves `partial`, `missing`, `wrong` or `gate_refused_doubtful` (or a dispute refuted, or a
regression) is re-issued by its original span, naming the new snapshot's skips there (possibly
none); one `applied` goes to `history` with its span, where a narrowed remnant that still holds
math in a proof section meets the skip rule again; one set aside (`gate_refused_ok`, a dispute
upheld) adds its span to `skip_seen`. The verdicts file gains `skip_ok` (`[{skip, span,
reason}]`), `skip_seen` (`[[a, b]]`) and `skip_report`, and its counts `skips`, `skips_judged`,
`skips_flagged`, `skips_upheld`, `skips_refuted`, `skip_missed_added`, `skip_missed_dropped`,
`skip_rule`, `skip_ok`, `hidden_statements` (the statements of the round's upheld skip
verdicts), `statements_dropped` and, from round 2, `skip_carried`; `flagged`, `upheld` and
`refuted` stay the passages'. `skip_report` is `{skips, chars, math_paragraphs,
furniture_math_paragraphs, by_section: {<section>: {skipped, math}}, mostly_skipped: [<section>]}`
over the round's document (`math_paragraphs` the gate's count, `by_section` the sections with a
math paragraph under a skip, the lead named `(lead)`); an `audit_failed` merge gives one when
`--gate-config` still reads the source.

**The decision table, `audit/2`**, is `audit/1` with one clause more: a middle round also
requests changes when any skip verdict is actionable, below T as well, provided the round made
progress — fewer actionable verdicts than the round before, or fewer math paragraphs under skips
outside the furniture (`skip_report`'s `math_paragraphs` less `furniture_math_paragraphs`).
Round 1 and the final round are as before, so at most R − 1 rejections still hold.

**The repair.** `inventory_edit.py --rule 2` adds `unskip {skip, keep?, verdict | neighbour_of}`
(`AUDIT_SKIP_OPS`): the skip — an id of the base snapshot's numbering, `r<k>-s<nnn>` of the
round the ops file names — gives up its text but the pieces `keep` names, `[{span: [a, b],
reason?}]`, each inside its span, non-empty, disjoint and in order (a piece without a reason keeps
the skip's); no `keep` (or `[]`) drops it, one piece shrinks it, two split it. It refuses an
unknown id, another round's id, a skip named twice, a piece outside the skip or overlapping
another, and no citation. **No operation adds or widens a skip.** The statements are `add`
operations as any (`scope: formal_math`, the kind the verdict proposes, `prominence:
supporting`), each math paragraph of the freed text no statement holds an `add` of an
expository passage (narration inside a proof is expository, never a skip), and the partial case a
`set` of `until` on the passage `merge_with` names; every operation cites the skip verdict.
`named_skips` gives the skips the operations name. The gate, the run's own, validates the result
unchanged: rule 8 refuses a skip that still holds a whole added passage (an `unskip` forgotten),
the coverage floor a freed math paragraph no passage holds (narration forgotten). `collateral
--rule 2` also compares the skips: each difference between the document's and what the cited
operations make of the snapshot's is a `skip_edit` entry (`AUDIT_SKIP_COLLATERAL`), `{id,
change: "skip_edit", skip, before: {span, reason} | null, after: {span, reason} | null}` — a skip
changed (both), removed (no `after`) or added (no `before`) by hand, `skip` the snapshot's skip
that holds it — which the checker judges harmful or not like any collateral; no operation puts
back a skip removed by hand (the final round's revert to the best snapshot does). The checker
gives each skip verdict a status like any verdict (`applied` when no statement is left under a
skip in its span and the statements it named are held), reading the diff's SKIP VERDICT lines.

**The turn machine** (`CF_AUDIT_RULE=2`) passes `--rule 2` to every host tool call and into every
validator line it gives an agent, records `rule: 2` in `state.json` (a state of another rule is
refused), stages the last round's verdicts for the rendering's diff, names each part's skips in
the call's block, places a skip flag in the part that shows its skip, runs the skip hunt's
skeptic calls, combines the parts' `examined_skips` and `missed_skips`, gives the `audit_failed`
merge `--gate-config`, and **weighs** the revision's sessions: a skip verdict weighs max(1, its
statements, its `unheld`) and any other verdict 1, a session holds up to
`AUDIT_REPAIR_VERDICTS_PER_SESSION` (40) of weight, and a heavier verdict has a session of its own.
