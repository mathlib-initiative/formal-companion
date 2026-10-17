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
 "source_sha": "<html_sha256>", "layer_sha256": "<hex>", "renderer": "web-html/1",
 "universe": "u1", "universe_sha256": "<hex>"}
```

`universe` and `universe_sha256` are required in `locate` and
`locate-challenge` documents and absent from `locate-inventory` ones. One
document covers one source; it lives at `findings/<task-id>/<source-id>.json`.
`schema` is `cflib.vocab.LOCATE_DOCUMENT_SCHEMA`; `renderer` is the source's
(`web-html/1` for the first corpus).

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
  task; dropping one needs a `skips` entry naming its span.
- `item` must equal the passage id of `span` (§2.2 of the proposal), written
  `In-text #<8 hex>`; the gate recomputes every id of the document over all
  its passages, so `head_words` must be the shortest unique head.
- **The quote rule**: `quote.exact` (non-empty, at most 1,500 characters, each
  context at most 200 — `gate.json` may change both) matches the layer
  **exactly once** with its `prefix` immediately before it and its `suffix`
  immediately after, the match starts at `span[0]`, and `exact` lies inside
  the span. A passage may not start inside a formula.
- A passage whose `finder` is a deterministic one must start where that finder's
  passage of the task starts; a passage the enumerator found is `model`. A
  deterministic passage of the task is dropped only with a skip whose span
  covers its whole span.
- Every paragraph of the source with `formal_math` content — every
  `paragraphs.json` entry with `math: true` — overlaps a passage span or a
  `skips` span (the coverage floor).
- With L2 curated hits for the article (§5; gate rule 4, off in the blind arm),
  the inventory keeps at least one `formal_math` passage and no `subject`
  passage is scoped `expository` or `non_math`.

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
  {`high`, `medium`, `low`}.
- `closeMatch` requires a non-empty `note` naming the convention difference.
- Every curated candidate handed to the task (§5) must appear in `candidates`,
  accepted or rejected with a reason, or be located; the gate reads the
  candidates file itself (rule 4).
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
  three give `challenge: downgraded` on the affected records.
- The actions a target admits: `location` — `confirm`, `downgrade` (naming the
  weaker `relation`), `refute`; `absence` and `unresolved` — `uphold`,
  `overturn` (with `locations`, at least one whose relation counts); `scope` —
  `uphold`, `overturn` (with `locations`, possibly empty); `prominence` —
  `uphold`, `overturn` (naming the new `prominence`).
- A challenge task's records are its task manifest's, and each gets exactly one
  verdict (rule 1); overturn locations are checked like a locate document's.
- A record of the run not named by any challenge task is `not_required` or
  `sampled_out`, as the challenge plan said.

## 5. Candidates file (L2)

Read-only on the run host at `candidates/<source-id>.json` (in the workspace,
`sourcesets/<id>/candidates/<universe>/<source-id>.json`):

```json
{"schema": "cf-locate-candidates/1",
 "source_id": "enwiki-1001-2002", "source_sha": "<html_sha256>",
 "universe": "u1", "universe_sha256": "<hex>",
 "article_qid": "Q99999001", "article_title": "Widget lemma",
 "terms": ["widget lemma", "spanning gadget"],
 "curated": [
   {"library": "alpha", "declaration": "…",
    "via": "wikidata_tag", "evidence": "@[wikidata Q99999001] at Alpha/Widget.lean:612"}],
 "lexical": [
   {"library": "beta", "declaration": "…", "score": 12.4, "matched": "title"}]}
```

`via` ∈ `cflib.vocab.CANDIDATE_VIA`. `terms` are the title, its redirects or
aliases where known and the lead's bold terms, which lexical retrieval queried;
`matched` names which. Only `curated` hits bind gate rule 4. In the G1-blind
arm (proposal §8.1) `curated` is empty and the file says `"blind": true`.

`cf locate candidates` writes it (`scripts/locate/candidates.py`). Every curated
declaration is checked in `corpus.sqlite`'s exact table at the universe's commit,
and one that is not there, or whose library is not in the universe, is listed in
`"dropped": [{library, declaration, via, evidence, reason}]` instead of
`curated` — a phantom the gate would refuse a worker for not locating is never
kept. Its curated inputs are the operator's files, read as data: a QID-keyed
`1000.yaml` (`--curated-1000`), a numbered `100.yaml` matched by normalised title
or an entry's own `wikidata` (`--curated-100`), both naming `--curated-library`
(default `mathlib`), and a library-metadata file of main results
(`--library-metadata`):

```json
{"libraries": {"alpha": [{"wikidata": "Q99999001", "title": "Widget lemma",
                          "declarations": ["Widget.exists_spanning_gadget"]}]}}
```

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
whose files do not match. It also names the runs the set was made from
(`"runs": {"locate", "challenge", "inventory"}`), and `created` is the newest of
those runs' harvest times, so the set is a function of its inputs and a
re-export is byte-identical.

`cf export literature-links --run <locate run> --challenge-run <run> --set-id
<id>` writes it (`scripts/locate/export.py`, the records from
`scripts/locate/records.py`) under `exports/<id>/` and what it refused, by
passage, to `reports/export.<id>.json`, exiting 1 when it refused anything: a
passage whose challenge is owed, one whose outcome could not be derived, a
record the checker rejects, a quote no context can make unique, and a whole
source whose records pass `--byte-budget` (default 262,144 bytes). Only
`formal_math` passages with an outcome are exported; the subject end's `uri` and
`URL` are the family's `revision_url` (titles move), its licence the family's
SPDX id.

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
sourcesets/<id>/state.json                         counts, written through one writer
sourcesets/<id>/sources/<source-id>/               one directory per source (gitignored)
sourcesets/<id>/candidates/<universe>/<source-id>.json   L2 (§5)
```

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
 "renderer": "web-html/1"}
```

- `origin` is `enumerated` (from the PageAssessments listing) or `adopted` (a
  consumer's pin and bytes, proposal §11.1, recorded in `adopted_from`).
- A field the source does not have yet is `null` (`raw_sha256` before the fetch,
  `layer_sha256` before the render). **Append-only**: version n+1 holds every
  `source_id` of version n, and every non-null field of n unchanged — a field
  may go from `null` to a value and never back or to another value. A new
  revision of a page is a new source id beside the old one.
- The manifest sha is sha256 of the file's bytes; a run pins `(sourceset id,
  version, manifest sha256)` and the gate checks per-source shas from that pin.

**The fetch.** One parameter set, fixed: `action=parse`, `oldid=<revision>`,
`prop=text`, `formatversion=2`, `disablelimitreport=1`,
`disableeditsection=1`, `format=json`. `params_sha256` is sha256 of the
canonical JSON of that mapping without `oldid`. The response bytes are kept as
`source.raw.html` and copied, never refetched, to every other machine.

**The canonical body** is the response's `parse.text` (an adopted file that is
not such a JSON response is taken as the body itself) with every HTML comment
removed, as UTF-8 bytes in `body.html`; its sha256 is `html_sha256`, which is
the source's `source_sha` everywhere (hard rule 3).

**One source's directory.** `cf sourceset fetch` and `cf sourceset adopt` write
`source.raw.html`, `body.html` and `source.json`; the renderer,
`scripts/sources/render_html.py` (`web-html/1`, whose module docstring states the
rule in full and freezes it), writes `body.html` again and every file after it
when `cf sourceset render` runs:

| file | what |
|---|---|
| `source.raw.html` | the bytes as fetched or adopted (`raw_sha256`) |
| `source.json` | the manifest entry, as the source's own record |
| `body.html` | the canonical body (`html_sha256`) |
| `layer.txt` | the **Literature Links text layer** (`layer_sha256`): the body's DOM `textContent` — every text node in document order, MathML token text, TeX annotations and citation markers included — then the WHATWG *strip and collapse ASCII whitespace* (each run of U+0009, U+000A, U+000C, U+000D, U+0020 to one space, ends stripped); no Unicode normalisation. The only view quotes are cut from and gated against |
| `formulas.json` | `[{"span": [a, b], "alttext": "{\\displaystyle G}"}]`: each formula's half-open layer span (the whole `.mwe-math-element`, else the `math` element), sorted and disjoint; the alttext is `math@alttext`, else the `x-tex` annotation, else the fallback image's `alt`. A formula with no text in the layer (an image only) has no span and no entry, and `render.json` counts it as `formulas_without_text` |
| `head.txt` | the head text: `layer.txt` with each formula span replaced by `⟦normalized alttext⟧`. Derived; the definition is `scripts/gate/locate_text.py`'s `HeadMap`, which also maps offsets both ways |
| `text.txt` | the **reading copy** for models: math as `$<normalized alttext>$`, references (`sup.reference`), reference lists (`ol.references`, `.reflist`, `.mw-references-wrap`), edit links, style and script dropped, headings as `#`-prefixed lines (one `#` per level), `- ` before a list item, ` \| ` between table cells, a blank line between blocks; it ends with one newline |
| `layer_map.json` | `{"schema": "cf-layer-map/1", "segments": [[t0, t1, l0, l1], …]}`, sorted: `text.txt[t0:t1]` is `layer.txt[l0:l1]`; equal lengths map character by character, unequal ones (a formula) only as a whole. A character the copy adds — a heading's `#`s, a bullet, a separator, a line break — is in no segment and has no layer offset |
| `sections.json` | `[{"path": ["Theorems"], "fragment": "Theorems", "level": 2, "span": [l0, l1]}]`, the lead first with `path: []`, `fragment: null` and `level: 0`; a heading's fragment is its `id`, else its `.mw-headline`'s; spans tile the layer |
| `paragraphs.json` | `[{"span": [l0, l1], "section": ["Theorems"], "block": "p", "formulas": 2, "math": true}]`: every block (`p`, `li`, `dd`, `dt`, `blockquote`, a theorem box, a displayed formula) that holds text, outside the reference lists; blocks may nest (an `li` holding a `p` is two entries). `math` is true when the block holds a formula or a deterministic finder hit, and those are the paragraphs gate rule 8's coverage floor reads |
| `finders.json` | `[{"finder": "theorem_box", "start": l0, "span": [l0, l1], "section": […], "term": "…"}]`: the deterministic finders' passages (`cflib.vocab.DETERMINISTIC_FINDERS`), sorted by start, each starting at the first character of a word of the head text; a finder hit whose element's text is glued to the word before it is dropped and counted in `render.json`'s `finders_dropped` |
| `render.json` | `{"renderer": "web-html/1", "html_sha256", "layer_sha256", "head_sha256", "text_sha256", "counts": {…}}` |

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

`universe.yaml` is the proposal's §2.3 shape, written by `cf corpus`
(`scripts/corpus/universe.py`). Each library carries `id` (the universe-local id
a document names as `library`), `brain_lib`, `repo`, a 40-hex `commit`,
`toolchain`, `roots`, `licence`, `conventions` and, once ingested, `index: {path,
sha256, declarations}`; `excluded` lists `{id, reason}`; `canaries` lists, per
toolchain, the acceptance canary's result (`{toolchain, lean_version, passed,
checked, index_sha256, cases}`) — no library on a toolchain without a passed
canary is ingested; `corpus_db` is `{path, sha256, built, declarations,
universe_sha256}` once `corpus.sqlite` is built, and `null` again as soon as an
index changes. **`universe_sha256`** is sha256 over the lines `<id>\t<commit>\t<index
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
 "deprecated": null, "probe_proof_status": "proved", "flags": []}
```

`proof_status` is `cflib.vocab.decide_proof_status` over the probe's wrapper,
its axiom closure and its type — re-decided at ingest (`scripts/corpus/ingest.py`),
never copied from a probe, whose own answer is kept as `probe_proof_status`
because the probe as run decides in another order (an axiom by its kind first,
no `axiom_dependent`, an `isProp` failure falling through to `proved`). `axioms`
is the closure outside `propext`, `Classical.choice` and `Quot.sound` (`sorryAx`
included; an axiom declaration's closure holds itself). A declaration whose
status would turn on an `isProp` the probe could not compute is the conservative
`statement_only` with `is_prop_unknown` in `flags`; a published index's status
word, taken without a closure, carries `published_status`. `statement` is set on
a `Prop`-valued definition — derived, while the probe emits no such field, from
a `def`/`abbrev`/`opaque` whose printed type's final codomain is `Prop`. `tags`
use `cflib.vocab.TAG_KINDS`: attributes in the source span from `start_line` to
`line` (read from a checkout given to the ingest) and Wikipedia URLs in the
docstring. `manifest.json` (`schema: cf-library-index/1`) carries the library,
commit, toolchain, declaration count, `sha256` of `decls.jsonl`, the counts by
proof status and kind, the flagged and tag counts, and the probe output's shas.

**`corpus.sqlite`** (built with `journal_mode=DELETE`, opened everywhere else
with `mode=ro&immutable=1`; its sha256 is recorded in `gate.json`):

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  -- schema = cf-corpus/1, universe, universe_sha256, built
CREATE TABLE libraries (id TEXT PRIMARY KEY, repo TEXT NOT NULL, commit_sha TEXT NOT NULL,
  toolchain TEXT, brain_lib TEXT, index_sha256 TEXT NOT NULL, declarations INTEGER NOT NULL);
CREATE TABLE decls (id INTEGER PRIMARY KEY, library TEXT NOT NULL, commit_sha TEXT NOT NULL,
  name TEXT NOT NULL, module TEXT, path TEXT, line INTEGER, end_line INTEGER, kind TEXT,
  proof_status TEXT NOT NULL, statement INTEGER NOT NULL DEFAULT 0,
  printable INTEGER NOT NULL DEFAULT 1, signature TEXT, docstring TEXT,
  UNIQUE (library, commit_sha, name));                        -- the exact table
CREATE VIRTUAL TABLE decls_fts USING fts5(name_words, signature, docstring,
  tokenize = 'unicode61');                                    -- rowid = decls.id
CREATE TABLE tags (decl_id INTEGER NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX tags_by_value ON tags (tag, value);
```

`name_words` is the name split at `.`, `_` and lower-to-upper case changes,
lowercased and joined by spaces, so `Widget.exists_spanning_gadget` is found by
`spanning gadget`. A declaration present in two libraries is two rows.

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
read nothing but the database and write nothing. The query is split into words,
each quoted and the words OR-ed, so nothing a worker types reaches FTS5's own
syntax; results are ranked by `bm25` with the name weighted over the signature
over the docstring. Exit status: 0, 1 (`corpus_show.py`: no such declaration), 2
(bad arguments), 3 (the database could not be read — a host problem). The
workspace side reads the same database through `scripts/corpus/query.py`.

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
                  "layer_sha256": "<hex>", "renderer": "web-html/1",
                  "passages": [{"item": "In-text #3fa91c07", "span": [10234, 10612],
                                "head_words": 12, "prominence": "central",
                                "passage_kind": "theorem"}]}]}
  ```

  An inventory task's documents carry `"finders": [...]` (`finders.json`'s
  passages, pre-filled) instead of `passages`; a challenge task's carry
  `"records": [...]`, the records it judges: `{key, target, item, span, run_id,
  prominence, scope, passage_kind, outcome}` and, for a location, its
  `location_key`, `library`, `declaration`, `relation` and `rank`.
- `challenge-plan.json` (a challenge run only) — every record of the challenged
  runs, required or not: `{schema: "cf-locate-challenge-plan/1", run_id,
  challenged_runs, seed, sample_rate, counts, records: [{key, target, source_id,
  item, location_key, library, declaration, relation, run_id, prominence, scope,
  rule, stratum, planned, task_id}]}`. `rule` is `refute_by_default`, `hunt`,
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
   "sourceset": {"id": "wpm", "version": 3, "manifest_sha256": "<hex>",
                 "sources_dir": "<host path>/sources"},
   "sources": {"enwiki-1001-2002": {"html_sha256": "<hex>", "layer_sha256": "<hex>",
                                    "renderer": "web-html/1"}},
   "candidates_dir": "<host path>/candidates/u1", "blind": false,
   "inventory": {"run_id": "r-…", "dir": "<host path>/inventory"},
   "tasks_dir": "<gate dir>/tasks", "log_dir": "<tend root>/gate-log",
   "orchestrator_db": "<tend root>/orchestrator.sqlite",
   "max_locations": 5, "min_absence_queries": 3,
   "vendor": {"literature_links": "<manifest sha256>",
              "formal_companion_annotations": "<manifest sha256>"}}
  ```

  `inventory` is the frozen inventory a `locate` run reads (null for
  `locate-inventory` and `locate-challenge`); `blind: true` is the G1-blind arm
  (curated candidates and gate rule 4 off). The planner also writes
  `challenged_runs` (a challenge run's locate runs) and `findings_dir:
  "findings"`. The gate directory also holds `locate_text.py`,
  `verify_findings.py` (whose hardened change-set reader the locate gate reuses),
  `passage_ids.py` (`scripts/locate/passage_ids.py`: the tool an inventory worker
  runs to fill each passage's `span`, `head_words` and `item` from its quote,
  since nobody can count code points or hash a head by hand),
  `locate_records.py` (`scripts/locate/records.py`, the record builder, which
  needs no `cflib`), the two host tools of §8, and `vendor/` with both vendored
  packages and their manifests, whose shas the gate verifies at start.

  The gate is invoked as `python3 <gate>/verify_locate.py --gate-config
  <gate>/gate.json --changed-files --base <branch> [--task-id <id>]` by tend's
  validation and pre-merge commands, `--findings <file>` for one document, or
  `--root <corpus>/<run-id> --findings-dir .` over a harvested corpus. It
  validates only the documents the change set touched (or names), loads only
  the sources, candidates files and task manifests those documents name, exits
  0 / 1 (rejected) / 3 (could not run) like `verify_findings.py`, prefixes each
  refusal with its rule (`[rule 2] …`), and its last line says which ownership
  rule judged the change set: `--task-id`/`TEND_TASK_ID`, the task id recovered
  from `orchestrator.sqlite` (whose schema is assumed, not known: any table
  with a worktree-path-like and a task-id-like column, one task id or none), or
  the single-stem rule. Documents are parsed strictly: a duplicate key or a
  `NaN` is a refusal.
- `run.yaml` — the faithfulness run's keys where they apply, plus a `locate`
  block: `{mode, sourceset: {id, version, manifest_sha256}, universe: {id,
  sha256, corpus_db_sha256}, inventory_run, challenged_runs, arm, per_task,
  subject_only, seed, sample_rate, documents, passages, source_ids, skipped}`,
  and `remote` names the host directories of the sourceset's sources, the
  universe, the candidates and the inventory (`sourcesets/<id>/sources`,
  `corpora/<u>`, `sourcesets/<id>/candidates/<u>`, `inventories/<run>`);
  `repos` and `sources` are empty. `budget.max_cost` is required before
  `cf run --execute` sends a locate run anywhere (proposal §6). The run's counts
  land in the sourceset's `state.json`, not in a companion's.

**On the host, outside every worktree**, in the tend root: `usage/` (Claude's
JSON envelope per agent call, `<task-id>.<role>.<UTC stamp>.<pid>.json`, kept as
a cost cross-check), `attempts/<task-id>.json` (`{task_id, rejections, history}`:
the count of genuine reviewer rejections), `gate-log/gate.jsonl` (one JSON line
per gate call: `at`, `run_id`, `mode`, `task_id`, `ok`, `exit`, `documents`,
`by_rule`, `reasons`), and the per-user slot directory the bridge's 15-slot
`flock` semaphore uses. The bridges (`scripts/run/agent_call.py` and
`scripts/run/attempt_cap.py` beside them) read `CF_SLOT_DIR` (default
`~/.cache/cf-agent-slots`), `CF_SLOTS` (15), `CF_AGENT_TIMEOUT` (`45m`),
`CF_ATTEMPT_CAP` (unset or 0 is off; a locate run sets 3), `CF_TASK_MANIFESTS`
(the `gate/tasks` directory), `CF_EXTRA_TOOLS` (the allowlist entries for the two
host tools — and in an inventory run the passage-id tool — worker role only) and
`CF_FINDINGS_GLOB` (`findings/$TEND_TASK_ID/*.json` for the nested layout), all
exported by the generated wrappers in `bin/`. A timeout exits 124 and
an empty or 409 envelope 75, with no contribution printed, so tend retries the
same worktree; only a `request_changes` from a call that completed counts as a
rejection.

## 10. Harvested rows and passage outcomes

Harvest copies the host's `findings/` tree into `corpus/<run-id>/`, so a document
lands at `corpus/<run-id>/<task-id>/<source-id>.json` and the receipt's listing,
manifest and missing set are keyed by `<task-id>/<source-id>.json` (the listing
is `find -print0`, the checksums `shasum` over it). The landed corpus is gated
again with `verify_locate.py` and the run's `gate.local.json`, and the receipt's
`locate` block counts `documents`, `by_mode`, `tasks`, `passages`, `verdicts`,
`unresolved_documents`, `attempt_capped_documents` and `unresolved_passages` —
UNRESOLVED on its own, never as not formalized. Each
document becomes finding rows that `cflib.hashing.finding_id` keys unchanged —
the library id is the `repo` and `<library>:<declaration>@<commit>` the
declaration list:

| document row | rows | `repo` | declarations |
|---|---|---|---|
| inventory passage | one `PASSAGE` | the sourceset id | none |
| located passage | one `LOCATION` per location | its library | `[<library>:<declaration>@<commit>]` |
| absent passage | one `ABSENCE` (beside one `LOCATION` per `relatedMatch`) | the universe id | none |
| unresolved passage, or an `unresolved` document | one `UNRESOLVED` | the universe id (inventory: the sourceset id) | none |

`cf tally --locate --run <run> [--challenge-run <run>]…` computes them
(`scripts/locate/outcomes.py`) and writes `reports/locate-tally.<run-id>.json`
(the rows and the counts) and `reports/locate-outcomes.<run-id>.jsonl`, one row
per passage after challenge:

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
unresolved passage has `status: null`. A passage whose challenge is still owed
carries `challenge: null`, and the export refuses it (hard rule 5). A row also
carries `span`, `head_words`, `section`, `reason`, `unresolved_by` (`worker` or
`attempt_cap`), `refuted` (the locations a challenge removed) and `task_id`; a
passage the run planned and no document answered has `outcome: null` and
`missing: true`, and an inventory passage scoped out has `outcome: null`. The
passage's `challenge` is one value over its records: owed when any is owed, else
`downgraded` when any changed, else `survived` when any was judged, else
`sampled_out` or `not_required`.

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
sequencer: {path: …/host/sequencer.sh, sha256: <hex>, host_dir: <remote root>/campaigns/<id>}
```

`render` writes `campaigns/<id>/host/`: `sequencer.sh` (from
`templates/campaign/sequencer.sh.tmpl`, shell, no cflib) beside copies of
`scripts/run/watchdog.py` and `scripts/cost/usage.py`, and prints the rsync and
start commands. On the host the sequencer runs each shard's `tend run` in order —
shard k+1 starts when shard k's ends — with the watchdog beside it, exports the
shard's usage from `CF_TRANSCRIPT_DIRS` and then prunes the transcripts that
shard wrote, keeps its state in `<remote root>/campaigns/<id>/` (`shard-<k>.done`,
`usage-<k>.jsonl`, `sequencer.log`), and writes `STOPPED` and stops the moment a
watchdog drains a shard. The watchdog (model-free, stdlib) drains the root with
`tend-control drain` when the spend summed from `usage/` reaches the shard's cap,
or the gate-failure rate from `gate-log/gate.jsonl` (over at least 20 calls) or
the cost per merged task (over at least `min_tasks`) passes `multiple` times the
pilot's p90; every check is a line of `<root>/watchdog.jsonl` and the last is
`<root>/watchdog.json`. The operator's machine only harvests receipts:
`status` reads each shard's `RECEIPT.json` (complete, gate, the `locate` block,
the priced cost) and totals the UNRESOLVED rate.
