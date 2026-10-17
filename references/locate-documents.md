# Locate documents

The file formats the locate stages exchange (`docs/proposals/wikipedia-locate.md`
§3–§5). Every package that writes or reads one of these follows this file; a
change to a shape is a change here in the same commit. **Status: not
implemented** — nothing reads or writes these yet.

**Contents**: 1 the common header · 2 inventory document · 3 locate document ·
4 challenge document · 5 candidates file · 6 sealed record set

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
 "source_sha": "<html_sha256>", "layer_sha256": "<hex>", "renderer": "web-html/1",
 "universe": "u1", "universe_sha256": "<hex>"}
```

`universe` and `universe_sha256` are required in `locate` and
`locate-challenge` documents and absent from `locate-inventory` ones. One
document covers one source; it lives at `findings/<task-id>/<source-id>.json`.

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
    "finder": "theorem_box"}],
 "skips": [{"span": [200, 900], "reason": "infobox"}],
 "notes": "free text, at most 2,000 characters"}
```

- `finder` ∈ {`theorem_box`, `bold_definition`, `lead`, `model`}: which finder
  proposed the passage. Deterministic finders' passages are pre-filled in the
  task; dropping one needs a `skips` entry naming its span.
- `item` must equal the passage id of `span` (§2.2 of the proposal); the gate
  recomputes it.
- Every paragraph of the source with `formal_math` content is covered by a
  passage span or a `skips` span (the coverage floor).

## 3. Locate document (`mode: locate`)

A locate task carries at most eight passages of one frozen inventory.

```json
{"…header…": "…",
 "passages": [
   {"item": "In-text #3fa91c07", "outcome": "located",
    "locations": [
      {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
       "relation": "exactMatch", "rank": 1, "confidence": "high",
       "note": "", "witness": "<why this declaration states the passage>"}],
    "receipt": {
      "queries": ["widget spanning gadget exists", "Widget.exists"],
      "candidates": [
        {"library": "alpha", "declaration": "Widget.exists_spanning_gadget",
         "verdict": "accepted", "reason": ""},
        {"library": "beta", "declaration": "Beta.Widget.gadget_of_finite",
         "verdict": "rejected", "reason": "states the finite case only"}]}},
   {"item": "In-text #91aa02de", "outcome": "absent", "locations": [],
    "receipt": {"queries": ["…", "…", "…"], "candidates": []}},
   {"item": "In-text #0c4d77b1", "outcome": "unresolved", "locations": [],
    "reason": "the passage's quantifier is ambiguous", "receipt": {"queries": [], "candidates": []}}]}
```

- `outcome` ∈ {`located`, `absent`, `unresolved`}. `absent` may carry
  `relatedMatch` locations only.
- `relation` ∈ the six locate relations; `rank` 1..5, distinct; `confidence` ∈
  {`high`, `medium`, `low`}.
- `closeMatch` requires a non-empty `note` naming the convention difference.
- Every curated candidate handed to the task (§5) must appear in `candidates`,
  accepted or rejected with a reason.

## 4. Challenge document (`mode: locate-challenge`)

```json
{"…header…": "…",
 "verdicts": [
   {"item": "In-text #3fa91c07", "target": "location",
    "location_key": "<run-free sha>", "action": "confirm",
    "witness": "<quoted signature fragment and why it states the passage>"},
   {"item": "In-text #3fa91c07", "target": "location",
    "location_key": "<sha>", "action": "downgrade", "relation": "broadMatch",
    "witness": "…"},
   {"item": "In-text #91aa02de", "target": "absence", "action": "overturn",
    "locations": [{"library": "beta", "declaration": "…", "relation": "exactMatch",
                   "rank": 1, "confidence": "high", "note": "", "witness": "…"}],
    "witness": "found with a type search the worker did not run"},
   {"item": "In-text #0c4d77b1", "target": "scope", "action": "uphold", "witness": "…"}]}
```

- `target` ∈ {`location`, `absence`, `unresolved`, `scope`, `prominence`}.
- `action` ∈ {`confirm`, `downgrade`, `refute`, `overturn`, `uphold`}:
  `confirm`/`uphold` keep the record (`challenge: survived`); `downgrade`
  changes a relation, `refute` removes a location, `overturn` replaces an
  absence or unresolved outcome (or a scope) with the given locations — all
  three give `challenge: downgraded` on the affected records.
- A record not named by any verdict is `not_required` or `sampled_out`, as the
  challenge plan said.

## 5. Candidates file (L2)

Read-only on the run host at `candidates/<source-id>.json`:

```json
{"source_id": "enwiki-1001-2002", "article_qid": "Q99999001",
 "curated": [
   {"library": "alpha", "declaration": "…",
    "via": "wikidata_tag", "evidence": "@[wikidata Q99999001] at Alpha/Widget.lean:612"}],
 "lexical": [
   {"library": "beta", "declaration": "…", "score": 12.4, "matched": "title"}]}
```

`via` ∈ {`wikidata_tag`, `mathlib_1000`, `mathlib_100`, `docstring_url`,
`library_metadata`}. In the G1-blind arm (proposal §8.1) `curated` is empty.

## 6. Sealed record set (export)

```
<set>/SEAL.json        {"schema": "cf-locate-recordset/1", "campaign", "universe": {"id", "sha256"},
                        "sourceset": {"id", "manifest_sha256"}, "skill_commit", "created",
                        "files": {"<relative path>": "<sha256>"}}
<set>/universe.json    the universe: per library id, repo, commit, toolchain, brain_lib, display_name, licence
<set>/sources.jsonl    per source: source_id, pageid, revision, title, wikidata, html_sha256,
                       layer_sha256, renderer, assessment
<set>/records/<source-id>.jsonl   Literature Links 2.0 records (proposal §2.7)
<set>/receipts/<sha256>.json      search receipts referenced from absence records
```

`SEAL.json` lists every other file with its sha256; a consumer refuses a set
whose files do not match.
