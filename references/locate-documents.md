# Locate documents

The file formats the locate stages exchange (`docs/proposals/wikipedia-locate.md`
§3–§5). Every package that writes or reads one of these follows this file; a
change to a shape is a change here in the same commit. **Status**: the formats
are fixed and built: `scripts/gate/locate_text.py` (the head text and the layer
quote of §7), the paths of `cflib.paths`, the sourcesets and renderer of §7
(`cf sourceset`), the universes and `corpus.sqlite` of §8 (`cf corpus`), and the
planner side (`scripts/locate/`: §5's candidates, §6's sealed set, §9's run and
§10's outcomes).

**Contents**: 1 the common header · 2 inventory document · 3 locate document ·
4 challenge document · 5 candidates file · 6 sealed record set · 7 sourcesets and
rendered web sources · 8 universes, library indexes and `corpus.sqlite` · 9 a
locate run on disk · 10 harvested rows and passage outcomes · 11 campaigns

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
third genuine reviewer rejection (proposal §6), never by a worker.

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
- A passage whose `finder` is a deterministic one must start where that finder's
  passage of the task starts; a passage the enumerator found is `model`. A
  deterministic passage of the task is dropped only with a skip whose span
  covers its whole span.
- Every paragraph of the source with `formal_math` content — every
  `paragraphs.json` entry with `math: true` — overlaps a passage span or a
  `skips` span (the coverage floor).
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
  `uphold`, `overturn` (naming the new `prominence`).
- A `downgrade` to `closeMatch` may give `note`, the convention difference the
  record will carry; without one, a sentence of its `witness` (at most 300
  characters) becomes the note, since an exported `closeMatch` always names one
  (§6): the first sentence that carries no more of the source verbatim than the
  export's quote cap (`EXPORT_QUOTE_WORDS`), checked against the source's layer
  — a witness that opens by quoting the passage lends its next sentence — where
  a sentence ends at `.`, `!` or `?` before a space but not after an
  abbreviation (`i.e.`, `e.g.`, `cf.` …). When every sentence quotes past the
  cap the first is taken, and the export refuses the passage by name.
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

`via` ∈ `cflib.vocab.CANDIDATE_VIA`. `terms` are the title and the lead's bold
terms (`bold_definition` finders whose `section` is the lead's `[]`), which
lexical retrieval — the host tool's own search, `corpus.query.search`, every
word first and any word only to fill up — queried; `matched` (`title` or
`bold_term`) names which. `lexical` is in the search's own order: a declaration
a term names as an identifier first (§8, the host tools), then by `score`. A bold term is a term only when it has at least three
letters, is not only a paragraph label (`cflib.vocab.is_bold_label`: "Proof.",
"Example:") and is not only stop words (`candidates.is_lexical_term`); a bold
"R" or "1'" is never queried. `corpus_db` is the database the file was retrieved
from (§9's tools database): `full`, `blind` or `planted`, with its sha. Only
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
`corpus.ingest.wikipedia_urls` writes, so a mobile link joins too.
`--blind` reads `corpus.blind.sqlite` and `--tools-corpus-db <file>` a planted
database; the sourceset's `state.json` records each run under
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
one value: the sha256 of the receipt file's exact bytes.

`universe.json` is the universe as proposal §2.3 writes it: `id`,
`universe_sha256`, `frozen` (the `YYYY-MM-DD` it was frozen; the export refuses a
universe without one), `libraries` — a **list** sorted by id, each `{"id",
"brain_lib", "repo", "commit"` (40 hex)`, "toolchain", "roots": [...], "licence"`
(`{"spdx"}`, never null: the export refuses a universe with a library that records
no licence, naming it and the `cf corpus add … --licence <SPDX id>` that records
it)`, "display_name"}`, where `display_name` is the library's
`brain_lib`, the one name the universe gives it — and `excluded`, `[{"id",
"reason"}]`. Nothing the universe does not define (no index paths, no host
instructions).

What every record carries beyond §2.7's example:

- `extensions[‹locate›].section` — the passage's heading path (a list of
  non-empty strings; `[]` is the lead) — and `.prominence` (`subject`,
  `central`, `supporting`), after the challenge (a prominence overturn moves
  it), so a consumer can single out the subject passage without a passage
  table. A passage the outcomes give neither is refused.
- A location's lean end carries `library`, `path`, `line` and `end_line` in its
  extension **when the index has them**: a library's own published index may
  record `line` only, and its ends then carry no `end_line` (never a null or a
  guessed one). A consumer treats `end_line` as optional and renders `line N`.
- A `closeMatch` always carries a non-empty `note`, a downgraded one too: a
  challenge downgrade to `closeMatch` takes the verdict's `note`, else the first
  sentence of its witness the quote cap admits (§4); a `closeMatch` with no note
  is refused.
- An absence keeps one lean end per universe library, each **minimal**:
  `{"type": "lean", "uri": <repo>, "version": <40-hex commit>}` and nothing else
  (no `role`, no extension; `universe.json` says which library a repository is),
  about half the bytes of the earlier form at 48 libraries.

`cf export literature-links --run <locate run> --challenge-run <run> --set-id
<id>` writes it (`scripts/locate/export.py`, the records from
`scripts/locate/records.py`) under `exports/<id>/` (or `--out DIR`) and what it
refused, by passage, to `reports/export.<id>.json` (which also names the runs,
`run_id`, `challenge_runs` and `inventory_run`, the `campaign` token and the
`sourceset` with its manifest `version`), exiting 1 when it refused
anything: a passage whose challenge is owed, one whose outcome row carries a
`problem` (§10: an outcome that could not be derived, a location that could not
be joined, a document off the pinned rendering, a challenge that no longer fits
its record), a `formal_math` passage with no outcome, a record the checker
rejects, a quote no context can make unique, a receipt query or reason or a
location note carrying more than `EXPORT_QUOTE_WORDS` words of the source
verbatim, a passage with no section or prominence or a `closeMatch` with no
note, and a whole source whose records and receipts pass `--byte-budget` or
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
the planner's, the outcomes', the export's: with no `key` the section becomes
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
  `adopted_from: {consumer, pins_sha256}`. A pin is `{revision, pageid | title,
  bytes?, wikidata?, assessment?}`: one without bytes is adopted with null shas
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

A `/1` or `/2` rendering cannot be re-rendered in
place (a recorded `layer_sha256` never changes): `cf sourceset create <new>
--from <old>` seeds a new sourceset with the old one's identities and bytes and
no layers, and `cf sourceset render <new>` renders it under the current id. The
seeded sourceset appears whole or not at all — it is built in
`sourcesets/.seeding-<new>/` and renamed into place once its version is written
— so an interrupted seeding is re-run as the same command. Any
change to any view, the layout of a view's file included, is a new renderer
id: `tests/test_locate_sources.py` pins every view of a synthetic page to it.

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
a document names as `library`), `brain_lib`, `repo`, a 40-hex `commit`,
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
read nothing but the database and write nothing. The query is split into words
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
  and, for a location, its `location_key`, `library`, `declaration`, `relation`
  and `rank`. Every task manifest of the plan is also kept beside `run.yaml`, in
  `runs/<run-id>/planned.json` (`{schema: "cf-locate-planned/1", run_id,
  tasks}`), which the outcomes read the planned passages from — the tend root is
  not something a workspace commits.
- `challenge-plan.json` (a challenge run only) — every record of the challenged
  runs, required or not: `{schema: "cf-locate-challenge-plan/1", run_id,
  challenged_runs, seed, sample_rate, counts, records: [{key, target, source_id,
  item, location_key, library, declaration, relation, run_id, prominence, scope,
  outcome, rule, stratum, planned, task_id}]}`. `rule` is `refute_by_default`, `hunt`,
  `sample` or `none`; `planned` is `required`, `sampled_out` or `not_required`.
  A record's `key` is its `location_key`, or sha256 of `<target>\n<source_id>\n<clean(item)>`
  for an absence, unresolved outcome, scope or prominence. The outcomes read it
  back: a required record no verdict judged is owed (`challenge: null`). The gate
  reads a record's `item`, `target`, `location_key` (location targets only), `span`
  and `passage_kind`.
- `findings/<task-id>/<source-id>.json` — the documents (§1–§4); a contribution
  may change only its own task's directory and `tasks/<task-id>.yaml`.
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
                                    "renderer": "web-html/3"}},
   "candidates_dir": "<host path>/candidates/u1", "blind": false,
   "inventory": {"run_id": "r-…"}, "challenged_runs": [], "findings_dir": "findings",
   "tasks_dir": "<gate dir>/tasks", "log_dir": "<tend root>/gate-log",
   "orchestrator_db": "<tend root>/orchestrator.sqlite",
   "max_locations": 5, "min_absence_queries": 3,
   "vendor": {"literature_links": "<manifest sha256>",
              "formal_companion_annotations": "<manifest sha256>"}}
  ```

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
  put `--db <tools path>` inside the allowed prefix, so a worker cannot point
  the tools elsewhere, and an inventory or locate plan refuses a source whose
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
  at start, `tasks/` and `gate.json`. The tend root's `bin/` holds the bridge with
  `normalize_result.py`, `extract_final_json.py`, `agent_call.py` and
  `attempt_cap.py` — for every run, a faithfulness one too — and, for a locate
  run, `watchdog.py`. No library checkout is named to the challenger: nothing
  creates, transfers or verifies one on a run host. Rule 1
  builds every implied record with `locate_records` itself (`location_record`,
  `absence_record`, `unresolved_record` and `cap_quote`; the challenge value,
  the location key and the joined proof status are placeholders, the source's
  citation fields too unless the `sources` entry carries `uri`, `title`,
  `container_title` and `licence`), from the gate directory, or from the
  checkout's `scripts/locate/records.py` when the harvest runs the gate
  in-process. A sibling that is missing or does not import is exit 3 with one
  `FAIL` line naming it, never a traceback. Every SQLite database is opened
  through a percent-quoted `file:` URI with `mode=ro&immutable=1`.

  The directory is self-contained: every module these files import is the
  standard library or another file of the directory, so the gate runs as
  `python3 -I <gate>/verify_locate.py` with nothing else on the path, and a
  checkout missing one of them refuses to plan.

  The gate is invoked as `python3 <gate>/verify_locate.py --gate-config
  <gate>/gate.json --changed-files --base <branch> [--task-id <id>]` by tend's
  validation and pre-merge commands, `--findings <file>` for one document,
  `--root <corpus>/<run-id> --findings-dir .` over a harvested corpus, or
  `--self-check`, which validates nothing and exits 0 or 3 (its modules, the
  vendored pins, the task manifests, the sources and `corpus.sqlite`) and is
  what `cf run`'s verify stage runs on the host, under `python3 -I`. It
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
  no task id was found. Documents are parsed strictly: a duplicate key, a
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
  challenged_runs, arm, skill_commit, gate_files, per_task, subject_only, seed,
  sample_rate, documents, passages, source_ids, skipped}`. `arm` is `blind` with
  `--blind`, else `assisted`, and a locate plan whose inventory run, or a
  challenge plan whose challenged runs, are of another arm is refused;
  `skill_commit` is the planning checkout's HEAD; `gate_files` is `{path under
  the tend root: sha256}` of every file staged into `gate/` and `bin/` (the task
  manifests aside). The planner never writes `locate.campaign` (`cf campaign
  plan` stamps it). `passages` counts what the tasks owe: an inventory task's
  distinct finder starts, a locate task's passages, a challenge task's records;
  a source the inventory covered with every passage scoped out is planned as
  `skipped` and still in `source_ids`. `remote` names the host directories of
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
  preset. The run's counts land in the sourceset's `state.json`, through
  `Sourceset.record_state`, not in a companion's.

  `cf run` refuses a locate run planned for `local` (or given `--local`) before
  it writes anything: a locate run runs on an allowlisted host through tend. It
  refuses a `--max-cost` that differs from a recorded `budget.max_cost`, and
  records a given one there when none is, since the campaign, the sequencer and
  the watchdog read that key. It sends the run's source layers (only with
  `--include-source-text`, and then `run.sources_transferred` names them),
  `corpus.sqlite`, the tools database when gate.json's `tools_corpus_db` names
  another file, and this run's sources' candidates files (never the
  sourceset's whole directory, and none in the blind arm); the verify stage
  checks each database against its pinned sha256 once. The frozen inventory is not sent: nothing on
  the host reads it, and its passages reach the workers and the gate in the task
  manifests. The preflight prints `tend-version:`, `agentbox-version:` and
  `agent-cli-version:` lines (the CLI the run's `bridge` launches; `unknown`
  when a probe finds none), and an executed preflight records them at the top
  level as `versions: {tend, agentbox, agent_cli}`. `cf campaign plan` adds
  `campaign: <id>` to a shard's `locate` block (§11).

**On the host, outside every worktree**, in the tend root: `usage/` (Claude's
JSON envelope per agent call, `<task-id>.<role>.<UTC stamp>.<pid>.json`, kept as
a cost cross-check; a call that left none — a timeout, a signal, an empty or
non-JSON stdout — leaves a record of its own under that name, `{type: result,
subtype: error_<outcome>, is_error: true, total_cost_usd: null, duration_ms,
cf_agent_call: {outcome, role, task_id, slot, elapsed_s, timeout_s, exit,
signal, stdout_bytes, cost: unknown}}`, and prose stdout is kept beside it as
`.out`), `attempts/<task-id>.json` (`{task_id, rejections, history}`:
the count of genuine reviewer rejections), `gate-log/gate.jsonl` (one JSON line
per gate call: `at`, `run_id`, `mode`, `task_id`, `task_id_from` (`--task-id`,
`$TEND_TASK_ID`, `orchestrator.sqlite…`, or null with no id), `changed_files`
(whether the call judged a change set), `ownership` (the call's ownership line,
null without `--changed-files`), `ownership_note` (why no task id was found),
`ok`, `exit`, `documents`, `by_rule`, `reasons`), and the per-user slot directory the bridge's 15-slot
`flock` semaphore uses. The bridges (`scripts/run/agent_call.py` and
`scripts/run/attempt_cap.py` beside them) read `CF_SLOT_DIR` (default
`~/.cache/cf-agent-slots`), `CF_SLOTS` (15), `CF_AGENT_TIMEOUT` (`45m`),
`CF_ATTEMPT_CAP` (unset or 0 is off; a locate run sets 3), `CF_TASK_MANIFESTS`
(the `gate/tasks` directory), `CF_EXTRA_TOOLS` (the allowlist entries for the two
host tools, each `Bash(python3 <gate>/corpus_search.py --db <tools path>:*)` —
and in an inventory run the passage-id tool — worker role only),
`CF_REVIEWER_TOOLS` (the reviewer's read-only default plus the gate, which its
prompt has it run; reviewer role only) and `CF_FINDINGS_GLOB`
(`findings/$TEND_TASK_ID/*.json` for the nested layout), all exported by the
generated wrappers in `bin/`; the planner stages every helper the bridge runs
(`agent_call.py`, `attempt_cap.py`, `normalize_result.py`,
`extract_final_json.py`, and for a locate run `watchdog.py`) beside it in
`bin/`. Both roles' default allowlists in the bridge also carry the gate
program, `Bash(python3 <gate>/verify_locate.py:*)`, derived from `CF_GATE_CMD`:
the prefix of every gate invocation both prompts render. A timeout exits 124 and
an empty or 409 envelope 75, with no contribution printed, so tend retries the
same worktree; only a `request_changes` from a call that completed counts as a
rejection. Both roles' allowlists carry the gate command's program by absolute
path (`CF_GATE_CMD` without its flags): the worker runs it before committing and
the reviewer to confirm the gate passes. When an agent writes no result file,
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
again with `verify_locate.py` and the run's `gate.local.json`, and the receipt's
`locate` block counts `documents`, `by_mode`, `tasks`, `passages`, `verdicts`,
`unresolved_documents`, `attempt_capped_documents` and `unresolved_passages` —
UNRESOLVED on its own, never as not formalized. That re-gate writes no line to
any gate log: it is not a worker's attempt.

A remote `cf harvest <run> --execute` also brings home, outside the findings
rsync (which carries `*.json` documents only) and outside `corpus/<run-id>/`
(whose tree is the receipt's manifest), into `runs/<run-id>/host/`
(`WorkspacePaths.run_host_dir`, `scripts/harvest/host_artifacts.py`):

| local file | from the host |
|---|---|
| `gate.jsonl` | `<log_dir>/gate.jsonl`, `log_dir` being the host `gate.json`'s (the tend root's `gate-log/`) |
| `usage.jsonl` | a campaign shard only: the sequencer's `<campaign dir>/usage-<run-id>.jsonl` (§11) |
| `canary.json` | a campaign shard only: the sequencer's `<campaign dir>/canary-<run-id>.json` (§11) |
| `envelopes/` | the tend root's `usage/` `*.json`: the bridge's kept envelope of each agent call, and the record of each call that left none (§9) |

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
harvest's copy notwithstanding; `usage.jsonl` and `canary.json` are not, and
one the host lacks is `absent on the host`, never a failed harvest
(`envelopes/` is a directory: `test -d`, then its `*.json` files only, its row
carrying `files` and the sha256 over its sorted `path<TAB>sha256` lines). The
receipt's `cost` block is priced from the first of: `--transcripts`, a landed
`usage.jsonl`, the landed `envelopes/`. From envelopes (`basis: envelopes`,
`cost.fleet.envelope_cost_block`) the dollars are the envelopes' own
`total_cost_usd`, the tokens are read per model from `modelUsage` (else `usage`
under the envelope's `model`, else the run's), and the block carries
`envelope_count`, `messages_by_model`, `usd_by_model`, `usd_from_price_table`
(the same tokens priced with `budget.prices`, a cross-check) and
`unpriced_calls`/`unpriced_seconds` (records of calls that left no priced
envelope, which make `usd` a lower bound). `cf tally --locate` prints every
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
gate_ok}}` for the locate run, its inventory and each challenge run, each one
not a complete gated harvest named in a warning) and
`reports/locate-outcomes.<run-id>.jsonl`, one row per passage after challenge:

```json
{"source_id": "enwiki-1001-2002", "item": "In-text #3fa91c07", "passage_kind": "theorem",
 "prominence": "central", "scope": "formal_math", "outcome": "located",
 "locations": [{"library": "alpha", "commit": "<40 hex>",
                "declaration": "Widget.exists_spanning_gadget", "relation": "exactMatch",
                "rank": 1, "confidence": "high", "proof_status": "proved",
                "statement": false, "location_key": "<hex>", "challenge": "survived"}],
 "absence": null, "challenge": "survived", "status": "formalized",
 "runs": {"inventory": "r-…", "locate": "r-…", "challenge": "r-…"}}
```

`proof_status` and `statement` are joined from `corpus.sqlite` at harvest (with
the declaration's `module`, `path`, `line`, `end_line` and `kind`), and a
`proof_status` a document wrote is ignored and named; `status` is
`cflib.vocab.derive_status` over the surviving locations and the absence; an
unresolved passage has `status: null` (one that carries locations anyway is
named as a problem). A passage whose challenge is still owed carries
`challenge: null`, and the export refuses it (hard rule 5). A row also carries
`span`, `head_words`, `section`, `source_sha`, `layer_sha256`, `reason`,
`unresolved_by` (`worker` — the default — or `attempt_cap`), `refuted` (the
locations a challenge removed), `task_id`, and `problem` when something about
the passage is wrong: a location that could not be joined, a document whose
universe or `source_sha`/`layer_sha256` is not the run's pin, an outcome that is
not one of the three, a challenge that no longer fits its record. A passage the
run planned (`planned.json`, §9; its count must be `run.yaml`'s) and no document
answered has `outcome: null` and `missing: true`, and an inventory passage scoped
out has `outcome: null`. A passage answered in another task's document than its
own is ignored there, and a declaration listed twice keeps its best rank. The
passage's `challenge` is one value over its records — its locations, the
refuted ones included, and its passage-level and prominence records: owed when
any is owed, else `challenge_pending` when any is, else `downgraded` when any
changed, else `survived` when any was judged, else `sampled_out` or
`not_required`. A prominence overturn to `subject` or `central` reopens the
passage: §5 refutes such a passage's locations and hunts its absence, so each
of its records no verdict judged (planned `not_required` or `sampled_out` under
`supporting`) becomes `challenge_pending` — exported as it is, read by a
consumer as unchallenged, never as "no rule applied".

A challenge run is applied only to a locate run its plan's `challenged_runs`
names, only through the plan records whose `run_id` is that run, and a verdict
counts only on a record the plan REQUIRED (one on any other is named and
ignored). A location's verdict applies only while its relation is the one the
plan saw, a passage-level one only while the outcome is (otherwise the record
is owed again); a `downgrade` must name a strictly weaker relation (exact,
then close, then broad, then narrow/hasPart, then related: §4's order,
`cflib.vocab.RELATION_STRENGTH`); a prominence changes only
by `overturn`; an `overturn` puts the challenger's locations first in their own
order, then the kept `relatedMatch` ones, renumbered and capped at five; and a
scope overturned with no location makes the passage `formal_math` with no
outcome and a `problem`, which the export refuses. The counts' `unresolved`
is every answered passage with no status — the worker's and those a challenge
left without a location that counts.
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
watchdog: {p90_cost_per_task: 4.1, p90_gate_failure_rate: 0.12, multiple: 2.0, min_tasks: 5}
pilot: wpm-pilot              # a run whose run.yaml records run.smoke_verified: true
transcript_dirs: <host dir>   # colon-separated; else hosts.<host>.transcript_dirs
canaries: <host path>         # the bench's canaries.txt on the host, or null
sequencer: {path: …/host/sequencer.sh, sha256: <hex>, host_dir: <remote root>/campaigns/<id>}
```

A campaign id is letters, digits, `.`, `_` and `-`, starting with a letter or
digit. `plan` stamps `locate.campaign: <id>` into each shard's `run.yaml` (the
export seals it) and refuses a shard stamped with another campaign; once the
campaign is rendered a re-plan may only append shards. `render` refuses a
campaign whose `pilot` is missing or records no passed smoke test, since the
sequencer launches each shard at full concurrency with no smoke test of its
own. Each shard goes through `cf run <shard> --stage verify` first — transfer
and verify, nothing launched; a shard smoke-tested with `cf run` keeps its own
tend run going.

`render` writes `campaigns/<id>/host/`: `sequencer.sh` (from
`templates/campaign/sequencer.sh.tmpl`, shell, no cflib) beside copies of
`scripts/run/watchdog.py`, `scripts/cost/usage.py` and
`scripts/run/canary_scan.py`, and prints the rsync and start commands (every
path in them shell-quoted). On the host one sequencer at a time holds the
campaign (`flock` on `sequencer.lock`); it runs each shard's `tend run` in order
— shard k+1 starts when shard k's ends — refusing a root that already has a live
tend run, with the watchdog beside it. After each shard it takes the
transcripts that appeared under `CF_TRANSCRIPT_DIRS` (default: campaign.yaml's)
since the shard started — the directories' listing before and after, never a
file that was already there — scans them for `CF_CANARIES` (default:
campaign.yaml's) into `canary-<run-id>.json` (`{scanned, hits, canaries}`, the
report `cf bench score --canary-report` reads), appends their usage to
`usage-<run-id>.jsonl` (a resumed shard keeps its earlier attempt's rows), and
deletes exactly those files, keeping them all when the scan found a canary or
could not run, or the export failed. Its state in `<remote root>/campaigns/<id>/`
is keyed by run id (`shard-<run-id>.done`, `.started`, `.before`, `usage-<run-id>.jsonl`,
`canary-<run-id>.json`, `sequencer.log`). It writes `STOPPED` and stops when a
shard's last watchdog record shows a drain, or a reason to drain that no drain
acted on (a failed `tend-control drain`, or a cap crossed after the last
periodic check), or cannot be read, and when a canary was found. The watchdog (model-free, stdlib) drains the root with
`tend-control drain` when the spend from `usage/` reaches the shard's cap — the
priced envelopes' `total_cost_usd` plus, for each call that left no priced
envelope, its elapsed seconds times the root's highest dollars-per-second
(`--unpriced-usd-per-hour` before any priced envelope has a duration; the
record says `estimated_unpriced_usd: null` when neither exists) —
or the gate-failure rate from `gate-log/gate.jsonl` (over at least 20 calls) or
the cost per merged task (over at least `min_tasks`) passes `multiple` times the
pilot's p90; every check is a line of `<root>/watchdog.jsonl` and the last is
`<root>/watchdog.json`; `--min-gate-calls` and `--min-tasks` are at least 1. The
operator's machine only harvests receipts: `status` reads each shard's
`RECEIPT.json` (complete, the gate as ok, FAILED or UNGATED — a receipt with no
gate block was never gated — the `locate` block, the priced cost) and totals
the UNRESOLVED rate.
