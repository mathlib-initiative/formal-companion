# Locate: where is a source formalized, in any library

**Status: proposal v2, 2026-10-17; milestones L-M0 to L-M3 built on synthetic fixtures 2026-10-18 (§1). Nothing has run on `$HOST` yet: §12's L-M2 smoke run is the next step.**
v1 of this document was reviewed by four adversarial readers (scale and
operations, measurement, contract and model, consumer); every blocker and
major issue they raised is either resolved here or listed in §12.

This document extends the skill from its forward question (is this companion
faithful to its source?) to the inverse one: **given a source, which of its
passages are formalized in Lean, in which library, at which commit, and how
closely?** The first corpus is English Wikipedia's WikiProject Mathematics;
the long-term corpus is research papers. Every stage is therefore built to be
measured, gated and improved, not run once.

Commands written `cf ...` exist unless marked *not implemented*; so do paths, unless written in italics.

**Contents**: 0 conventions · 1 what exists · 2 the model · 3 the stages ·
4 gates · 5 challenge · 6 the host · 7 budget and scale · 8 the benchmark ·
9 the improvement loop · 10 policy · 11 consumers · 12 milestones ·
13 open questions for the maintainer

## 0. Conventions

Placeholders: `$WIKILEAN` (the first consumer), `$MATHLIB` (a Mathlib checkout at the universe's commit), `$AUTOFORM` (the Autoform
plugin, MIT, whose library-index probe §2.5 adapts), `$HOST` (the allowlisted
run host, named only in a workspace's `workspace.yaml`, never here —
`CLAUDE.md` §1). "The maintainer" signs the human gates. Figures marked
*estimate* were not measured; every other figure names its source.

| term | meaning |
|---|---|
| library | a Lean package at a 40-hex commit |
| universe | a named, frozen set of libraries |
| web source | one canonical rendering of one page revision |
| passage | a span of a source that states something, keyed by `In-text #<hex>` |
| location | (passage, declaration@commit, relation) |
| absence | a checked statement that a passage was searched for in a universe and nothing was found |
| outcome | what a passage ends with: located, absent, or unresolved |

## 1. What exists

Built and tested on synthetic fixtures (`tests/test_locate_*.py`, the end-to-end
`tests/e2e/locate_synthetic/`). Of the real-data runs, L0's probe and host drivers
built a universe on `$HOST` (the L0 row), and L1, L2, the planner and the staged gate
have run against real pages and that universe on a copy of a workspace; nothing has
run with a model, and no task has yet gone through worker, gate, reviewer and merge on
`$HOST` (§12).

| part | path | state |
|---|---|---|
| companion/source/item/finding model; frozen `clean()` and `finding_id` | `scripts/cflib/items.py`, `cflib/hashing.py` | keys numbered items, `In-text p.N` and the `In-text #<hex>` passage of §2.2, tried after the page and the cross-reference so no existing label moved; `hashing.location_key` is §2.7's run-free key |
| the passage head id of §2.2 | `scripts/cflib/passages.py`, `scripts/gate/locate_text.py` | the frozen head rule, restated stdlib-only for the gate in lockstep (a test pins the two) with the layer/head offset map and the exact-once quote match |
| the locate vocabulary of §2.2, §2.4, §2.6, §5, §8 and §9 | `scripts/cflib/vocab.py`, `references/vocabulary.md` §1.11 | values, bounds, `decide_proof_status`, `derive_status` |
| licence classes and web-source families of §10 | `scripts/cflib/registry.py`, `registry/sources.yaml`, `POLICY.md` §4.1 | ships `enwiki`; `cf export literature-links` exports quotes only from an open class |
| L1: web sources, the renderer, sourcesets | `scripts/sources/render_html.py`, `websource.py`, `wikinet.py`, `sourceset.py`; `cf sourceset` | renderer `web-html/3` (layer, formulas, head text, reading copy and its map, sections, blocks, deterministic finders, every view's sha in `render.json`); append-only manifests with page flags and `create --from` to a new renderer; the one Wikipedia client, under `--execute` only, which also asks Wikidata for `flag-biographies`. Parity with `$WIKILEAN`'s text layer is pinned by a fixture its own `text_layer` generated (`tests/data/locate/text-layer.json`, synthetic bodies with recorded divergences) and checked per page at render against a producer's `layer_sha256`: **unverified on real pages** |
| L0: universes, ingest, `corpus.sqlite`, the host tools | `scripts/corpus/`, `templates/corpus/`; `cf corpus` | §2.4's proof status re-decided at ingest; the canary gate per toolchain; the exact table, FTS5 and tags; a blind build for G1-blind; the probe and its host drivers as run on `$HOST` (44 of 45 libraries, before two driver fixes, §12) |
| L2: candidates | `scripts/locate/candidates.py`; `cf locate candidates` | curated joins and lexical retrieval over `corpus.sqlite` |
| L3a/L3b/L5: the locate planner and presets | `scripts/locate/`, `scripts/cfcmd/plan_locate.py`, `templates/prompts/locate-*.md.tmpl`; `cf plan-locate` | sourceset-scoped runs, task manifests, the tend root with the out-of-tree gate directory |
| L4: the locate gate | `scripts/gate/verify_locate.py` | rules 1–8 over only the task's own documents; the ownership rule names itself; the task id read from `orchestrator.sqlite` assumes a schema nobody has checked against tend |
| the bridge and the host | `scripts/run/agent_call.py`, `attempt_cap.py`, `watchdog.py`, the bridges | 15 flock slots, `timeout 45m`, an empty or 409 envelope exits non-zero, envelopes kept under `usage/` (a call that left none, a timeout included, leaves a record the watchdog estimates), the attempt cap that writes `UNRESOLVED` |
| L6: harvest, tally, export | `scripts/harvest/` (recursive), `scripts/locate/outcomes.py`, `records.py`, `export.py`; `cf harvest`, `cf tally --locate`, `cf export literature-links` | sealed record sets every record of which the vendored checker accepts |
| campaigns | `scripts/run/campaign.py`, `templates/campaign/`; `cf campaign` | the host sequencer and the spend watchdog are rendered, never run here |
| the benchmark | `scripts/bench/`, `workflows/locate-label.workflow.js`, `references/locate-bench.md`; `cf bench` | §8's gold tiers, metrics with clustered intervals and `measurement_id`, §9's acceptance rule; no gold exists yet |
| text manuscripts located by heading path + line | `scripts/sources/locator.py`, `prepare_source.py` | the faithfulness stages' own: an `.html` source there is copied raw into `text.txt`; locate does not use it |
| S0 ingest, S1 inventory (deterministic halves) | `scripts/pipeline/` | PDF-only; the passage enumerator is deferred; measured passage recall 19/222 |
| scoring with a recall denominator | `scripts/score` | defect kinds only; `cf score` is unchanged by locate |
| Literature Links 2.0 (vendored, `format-2.0` @ `2bb328c`), overlay viewer | `vendor/literature_links`, `templates/overlay/runtime` | the viewer reads `pdf` ends only |

Mathlib scores 0 on every adapter and should not be onboarded: a library is not a
companion of Wikipedia, which is why locate plans from a sourceset and a universe.

## 2. The model

### 2.1 Web sources

A web source is identified by **page id and revision**, never by title
(titles contain `/` and move: 1,722 WikiProject titles changed between May and
October 2026):

```yaml
id: enwiki-28767-1371234567          # family-pageid-revision; safe for cflib.paths
family: enwiki
pageid: 28767
revision: 1371234567                 # MediaWiki oldid
title: Sylow theorems                # metadata only
wikidata: Q207039                    # the article's own QID (pageprops); its subject passages' records carry it (§2.2)
fetch: {api: action=parse, params_sha256: <sha of the fixed parameter set>}
raw_sha256: <sha of the bytes as fetched>
html_sha256: <sha of the canonical body>     # = source_sha
layer_sha256: <sha of the Literature Links text layer>
renderer: web-html/3
licence_class: open_share_alike
assessment: {class: B, importance: high}
```

- **Fetch once, one place.** `action=parse&oldid=N` with a fixed parameter
  set (`disablelimitreport`, `disableeditsection`, a pinned `formatversion`,
  `prop=text`). The *canonical body* is the parse output with HTML comments
  stripped; its sha is the source sha (hard rule 3). The raw bytes are kept as
  `source.raw.html` and copied, never refetched, to every other machine.
- **Adopt the consumer's bytes.** When a consumer (WikiLean) already pins a
  revision of the page and holds the cached parse bytes, the sourceset adopts
  that revision and those bytes instead of choosing its own (§11.1). The pins
  row is the import contract of `references/locate-documents.md` §7: a row may
  carry the fixed `fetch` and the producer's `layer_sha256`, and `render` refuses
  a page whose layer is not that sha.
- **Two text views of one document.** `scripts/sources/render_html.py`
  (stdlib) computes from the canonical body:
  1. the **Literature Links text layer** — the HTML `textContent`
     whitespace-collapsed exactly as the Literature Links spec defines it
     (MathML token text and its TeX annotation included, citation markers
     included, no Unicode normalization). This is the **only** layer quotes are
     cut from and gated against. It is a port of `$WIKILEAN`'s
     `site/anchor_resolve.text_layer`, and a parity test pins both;
  2. `text.txt`, the **reading copy** for models: math recovered from
     `alttext` as `$TeX$`, references and edit links dropped, headings as `#`
     lines;
  3. `layer_map.json`, a character-offset map from `text.txt` to the layer,
     and `sections.json` (heading path, fragment id, layer span).
- **Sourcesets.** Twenty thousand sources do not fit `workspace.yaml`, which
  every command validates. Web sources live in a **sourceset** (`cf sourceset`):
  `sourcesets/<id>/manifest-<n>.jsonl`, append-only and versioned, plus one
  directory per source, validated lazily. A run pins `(sourceset id, manifest
  sha)`; the gate checks per-source shas from that pin.

### 2.2 Passages

`cflib/items.py` gains one production, `In-text #<hex>` (8–16 hex digits),
with one conversion function to and from the vendored `passage-<hex>` id.
`clean()` is untouched.

The hex is the vendored passage hash over a **renderer-independent head**: the
passage's text on the Literature Links layer with each formula replaced by
`⟦normalized alttext⟧` (`cflib.passages.formula_token`: the brackets are part
of the hashed text, so a formula never hashes as the word it spells), taken
from the passage start for twelve words and extended word by word until the
head is unique within the source (cap: forty words; the length used is
stored). Words are the vendored `normalize_text` split on single spaces, and a
passage starts at the first character of a word. Two passages that still
collide are a gate refusal, never a silent drop. The rule is frozen: the id is
content-addressed, and the out-of-tree gate re-computes it
(`references/vocabulary.md` §4 states it in full).

A passage row of an inventory document carries **two kinds**: `kind` is the
finding kind `PASSAGE`, because harvest hashes a row's `kind` into
`finding_id` and the gate checks it against the mode's kinds, and the
passage's own kind travels as `passage_kind`. Only the exported Literature
Links record (§2.7) carries the passage kind as its `kind`.

```json
{"source_id": "enwiki-28767-1371234567", "source_sha": "<html_sha256>",
 "layer_sha256": "<hex>", "renderer": "web-html/3",
 "item": "In-text #3fa91c07", "head_words": 12,
 "span": [10234, 10612],
 "quote": {"exact": "…", "prefix": "…", "suffix": "…"},
 "section": ["Theorems"], "fragment": "Theorems",
 "kind": "PASSAGE", "passage_kind": "theorem",
 "prominence": "central", "scope": "formal_math",
 "statement": "<informal restatement>", "concept": null}
```

- `passage_kind`: `definition`, `theorem`, `conjecture`, `example`,
  `construction`, `notation`, `other`.
- `prominence` (not "role", which Literature Links uses): `subject` — what the
  article is about; `central` — a named result or definition the article
  states; `supporting` — anything else formal.
- `scope`: `formal_math`, `expository`, `non_math`. Only `formal_math`
  passages are located. A passage may not be scoped `non_math` or `expository`
  when L2 holds a tag or curated hit for it (§4).
- `concept`: a passage's own Wikidata QID, set only when a validated method
  assigned it (§11.3); a document's `concept` is never the article's QID by
  default, and no method assigns a passage a QID of its own yet.
- **The article's QID on its subject passages** is the one method that gives a
  record a QID, the maintainer's decision B5 of 2026-10-19 ("write the
  article's QID as identifiers.wikidata on records of subject-prominence
  passages only"): the export writes the source's `wikidata` (its pageprops at
  the pinned revision, or the producer's pin) as the subject end's
  `identifiers.wikidata` on every record of a passage whose prominence after
  the challenge is `subject`, and on no other passage's, since the article's
  QID on every passage welds the wrong concepts (a theorem the article only
  mentions would join the article's concept). A source with no QID gives its
  records none, and one whose `wikidata` is no QID is refused. The skill does
  not check that the QID's enwiki sitelink is this article, since library code
  has no network (`CLAUDE.md` §3): the consumer does, before any concept-level
  edge (§11.3).

### 2.3 Universes

```yaml
# corpora/u1/universe.yaml
id: u1
frozen: 2026-10-17
libraries:
  - id: mathlib                         # universe-local id, used as `repo`
    brain_lib: Mathlib                  # the consumer's library token (§11.3)
    repo: https://github.com/leanprover-community/mathlib4
    commit: <40 hex>
    toolchain: leanprover/lean4:vX
    roots: [Mathlib]
    licence: {spdx: Apache-2.0}
    conventions: corpora/u1/conventions/mathlib.md   # confirmed_by stamp required
    index: {path: index/mathlib/, sha256: <hex>, declarations: 0}
  - id: lean-core                       # Init and Std at mathlib's toolchain
  - id: batteries                       # at mathlib's manifest rev
  - ...
excluded:
  - {id: atlas-v1, reason: "licence forbids ML evaluation use"}
universe_sha256: <sha over sorted (id, commit, index sha)>
```

- Core packages (`Init`, `Std`, `Batteries`) are libraries of their own,
  pinned from Mathlib's toolchain and lake manifest, so true locations in core
  are not reported absent. The host driver indexes them as the kinds `core`
  (the toolchain's `Lean.githash`, paths in the lean4 repository) and
  `package-in-workspace` (the manifest's rev), both probed inside Mathlib's
  built workspace (`scripts/corpus/universe_host.py`).
- **Scope is chosen per library, by its roots, and the choice is stated** (a
  row's `roots`, and this list for the default universe). A root left out is
  out of the universe: its declarations are absences, not misses.
  - *mathlib*: `Mathlib` and `Wanted`. `Wanted` is Mathlib's own `lean_lib`
    of `proof_wanted` statements (`Wanted/X/Y.lean` beside `Mathlib/X/Y.lean`);
    a `proof_wanted` elaborates to a private placeholder whose type is
    `ProofWanted T`, which the probe keeps although private and classifies
    `wanted`, so without this root no wanted statement of Mathlib is
    locatable. `Archive`, `Counterexamples`, `MathlibTest`, `Cache` and `docs`
    are out. The build targets follow the roots, so `Wanted` is built too.
  - *lean-core*: `Init.*` and `Std.*`. The `Lean` namespace — the compiler,
    elaborator and metaprogramming API — is out by choice: no passage of a
    mathematical source states one of its tens of thousands of declarations.
  - *batteries*: `Batteries.*`.
  - *formal-conjectures*: `FormalConjectures.+` and
    `FormalConjecturesForMathlib`, the separate `lean_lib` that holds the
    project's `proof_wanted` statements and the definitions its problems
    share; `FormalConjecturesUtil` (tooling) and `FormalConjecturesTest`
    (tests) are out.
- `brain_lib` is one token per library: a letter, then letters, digits and `_`,
  at most 64 (`FF_AdicModules`; the consumer's Brain refuses `FF.AdicModules`),
  which `cf corpus add` enforces on every library it adds. A library whose
  roots are several of a consumer's libraries (lean-core's `Init` and `Std`)
  carries one token; a consumer that needs one per root splits it into two
  universe libraries at the same commit, one root each — a token may name only
  one repository, and a repository may carry several tokens.
- **One universe per campaign**, frozen into every shard's `gate.json`. A
  library that fails to build or probe is `excluded` with its reason, and
  absences honestly name the smaller universe.
- A name present in two libraries is two declarations with two locations.
- Licence is per library and, where needed, per root or path.

### 2.4 Library indexes and `corpus.sqlite`

The probe writes sharded JSONL in `autoform-library-index/v0` shape (the format
`$AUTOFORM` and ATLAS publish), so a probe rerun skips finished shards; ingest
normalises it into one `decls.jsonl` plus `manifest.json` per library
(`cf-library-index/1`, `references/locate-documents.md` §8). Declaration records add:

| field | meaning | computed |
|---|---|---|
| `path`, `line`, `end_line` | source file and span | declaration ranges |
| `axioms` | the axiom closure as a small set | **one memoised DFS over all constants** in one process (an axiom bitset per constant, explicit worklist); output only for the library's own roots, traversal over everything |
| `proof_status` | see below | mechanically, from `axioms` and the type, **never by a model and never by the worker** |
| `tags` | `wikidata`, `stacks`, `kerodon` tags; Wikipedia URLs in the docstring | parsed from the declaration's source span and docstring |
| `printable` | false when `ppExpr` hit its heartbeat or size limit | per-declaration limit, try/catch |

`proof_status`, decided in this order, independent of declaration kind:

1. `wanted` — the type is a `ProofWanted`/`DefWanted`-style wrapper (checked
   first; on an older toolchain whose `proof_wanted` adds no constant, a source
   scan records a `{module, lines}` end with this status);
2. `statement_only` — the closure contains `sorryAx`;
3. `axiom_dependent` — the closure contains an axiom outside {`propext`,
   `Classical.choice`, `Quot.sound`};
4. `proved` — a declaration whose type is a `Prop`;
5. `definition` — data (types, functions, structures, classes, instances).

A `Prop`-valued *definition* (`def RiemannHypothesis : Prop`) is a
`definition` whose `statement` flag is set; it never formalizes a theorem
passage (§2.6).

For module-system libraries (`module`, `public section`) the probe loads the
private/server olean level, so a `sorry` inside a private proof body is seen.
**Acceptance canary per toolchain**: a scratch library reproducing a `sorry`
hidden in a private proof body of a `module` file, a `theorem_wanted`, a
`Prop`-valued def, and an axiom-dependent theorem; the probe must classify all
four before any library on that toolchain is ingested.

`corpus.sqlite` (derived, rebuildable) holds every declaration of the
universe: an exact table `(library, commit, name)` for existence checks, an
FTS5 index over name segments, signature and docstring, and a tag table. It is
built with `journal_mode=DELETE`, lives in a read-only host directory outside
every entrypoint, is opened with `mode=ro&immutable=1` everywhere, and its sha
is verified once at `cf run`'s verify stage and recorded in `gate.json`.

**`cf` never builds a library**, the rule `detect-proxies --lean` already
follows: `cf corpus probe-command` prints a per-library pipeline (disk
floor check → `ulimit -n 65536` → cache fetch → build → probe → `cf corpus
ingest` → remove `.lake`, keeping the index); the operator runs it on `$HOST`.

### 2.5 The probe

`templates/corpus/probe.lean.tmpl`, adapted from `$AUTOFORM`'s
library probe at a pinned commit (MIT; the header says so). It uses the
long-stable `Environment` API so one template serves every toolchain in the
universe, and falls back where a newer API (the module system's olean levels)
is absent. Sharded by a stable hash of the name; per-declaration `ppExpr`
heartbeat and character limits; per-module output files; no total size cap.

### 2.6 Relations, outcomes and the derived status

A location's **relation** (model-judged, challenged) is a Literature Links
relation word with SKOS direction, subject = passage, object = declaration:

| relation | meaning |
|---|---|
| `exactMatch` | the declaration states the passage |
| `closeMatch` | the same up to a convention difference (0 ∈ ℕ, junk values, normalization), named in `note` |
| `broadMatch` | the declaration is more general; the passage is an instance |
| `narrowMatch` | the declaration is a special case of the passage |
| `hasPart` | the declaration states part of a passage that says several things |
| `relatedMatch` | relevant but states something else |

A passage's **outcome** is one of: *located* (≥1 location), *absent* (a
checked absence; §2.7), or *unresolved* (the worker or the attempt cap could
not decide; exported as a record with `object: []` and no status, and counted
as unresolved, never as not formalized). An absence may sit beside
`relatedMatch` locations.

The **derived status** of a passage (the Literature Links `status`, computed
once per passage after challenge and stamped on all its records):

| status | when |
|---|---|
| `formalized` | theorem/conjecture passage: `exactMatch`, `closeMatch` or `broadMatch` to a `proved` theorem; `exactMatch` or `closeMatch` to a data declaration that states the theorem (a `definition` without the `statement` flag: an equivalence, an isomorphism, a structure-valued construction — the Chinese remainder theorem as a `RingEquiv`). definition passage: `exactMatch` or `closeMatch` to a `definition`. any passage: a survived coverage claim whose every part is finished — `proved`, or data |
| `statement_only` | the best such location is `statement_only`, `wanted` or `axiom_dependent`, or is a `Prop`-valued definition stating a theorem passage; or any passage's survived coverage claim with an unfinished part (`statement_only`, `wanted`, `axiom_dependent`, or a `Prop`-valued definition) |
| `partial` | only `hasPart` or `narrowMatch` (or `broadMatch` to a definition), with no coverage claim that upgrades them |
| `not_formalized` | a checked absence, possibly with `relatedMatch` locations |
| *(none)* | unresolved; also only `relatedMatch` locations and no checked absence (what a challenge that downgrades a passage's one location leaves) |

A **coverage claim** is a located passage's statement that its `hasPart`
locations, at least two, together state every clause with no hypothesis
stronger than the passage's (`references/locate-documents.md` §3). Every
claim is challenged, whatever the passage's prominence (§5), and only one
whose challenge is `survived` counts; a refute removes it and leaves its parts
`hasPart`, and a confirm confirms its parts too (each part whose own record no
verdict judged, `not_required` or `sampled_out`, is `survived`) — unless a
prominence overturn to `subject` or `central` in the same run reopened the
part: the reopen wins, and the part stays `challenge_pending`, owed its own
verdict (§5). The best status over all the evidence wins. The data row and the
coverage rows are the maintainer's decision D1 of 2026-10-18 ("D1: yes. Formalized when the hasPart
locations together cover every clause (challenger confirms coverage), and when
an exact/closeMatch is to an equivalence or data declaration that states the
theorem (e.g. CRT's RingEquiv). Update vocab and WikiLean's parity table.");
before it, both were `partial`.

**The reading of D1** (parity round 3, which resolved the questions Q1–Q3 of
`references/vocabulary.md` §1.11): a survived coverage claim acts, for the
derived status, exactly like one more `exactMatch` location, a fixed stand-in
rather than any one part. Each part is finished (`proved`; data) or unfinished
(`statement_only`, `wanted`, `axiom_dependent`; a `Prop`-valued definition).
Every part finished, the stand-in is a `proved` declaration to the theorem row
and a `definition` to the definition row, so it formalizes the passage on
either; any part unfinished, it is a `statement_only` declaration on both.
An example or `other` passage judges it on both rows, the better winning, as
for every location (Q1); a data part is finished and a `Prop`-valued
definition part is not (Q2); and a definition passage's claim with an
unfinished part is `statement_only`, as an `exactMatch` to a `statement_only`,
`wanted` or `axiom_dependent` declaration is (Q3; an `exactMatch` to a
`Prop`-valued definition alone still formalizes a definition passage, while
as a claim's part it is unfinished). Nothing else in the table changes, and a claim that is not
survived adds nothing.

Built as `cflib.vocab.derive_status`, which refuses a contradiction (an
absence beside a location that counts; the `statement` flag on a `proved`
declaration; one `location_key` on two locations; a coverage claim whose parts
are not the passage's `hasPart` locations, or that is not the record's
`{complete, parts, challenge}` with a challenge value) rather than mapping it;
the cases this table does not name are decided in `references/vocabulary.md`
§1.11, and the shared parity table
`tests/data/locate/status-parity.json` holds every row's cases for a
consumer's own implementation.

### 2.7 The records

Literature Links 2.0 records, validated by the vendored checker. One
extension namespace root: `org.mathlib-initiative.formal-companion.locate`
(written `‹locate›` below).

A location:

```json
{"id": "enwiki-28767-1371234567::In-text #3fa91c07",
 "subject": [{"type": "html",
              "uri": "https://en.wikipedia.org/w/index.php?title=Sylow_theorems&oldid=1371234567",
              "version": "1371234567",
              "location": {"quote": {"exact": "…", "prefix": "…", "suffix": "…"}},
              "citation": {"type": "webpage", "title": "Sylow theorems", "container-title": "Wikipedia",
                           "URL": "https://en.wikipedia.org/w/index.php?title=Sylow_theorems&oldid=1371234567"},
              "extensions": {"‹locate›": {"pageid": 28767, "html_sha256": "<hex>", "layer_sha256": "<hex>",
                                          "renderer": "web-html/3", "span": [10234, 10612],
                                          "span_sha256": "<hex>", "licence": "CC-BY-SA-4.0"}}}],
 "object": [{"type": "lean", "uri": "https://github.com/leanprover-community/mathlib4",
             "version": "<40-hex commit>", "role": "key",
             "location": {"declaration": "Sylow.exists_subgroup_card_pow_prime",
                          "module": "Mathlib.GroupTheory.Sylow"},
             "extensions": {"‹locate›": {"library": "mathlib", "path": "Mathlib/GroupTheory/Sylow.lean",
                                         "line": 612, "end_line": 640, "proof_status": "proved"}}}],
 "relation": "exactMatch", "status": "formalized", "kind": "theorem",
 "extensions": {"‹locate›": {"item": "In-text #3fa91c07", "location_key": "<run-free sha>",
                             "rank": 1, "confidence": "high", "universe": "u1",
                             "universe_sha256": "<hex>", "challenge": "survived",
                             "section": ["Theorems"], "prominence": "subject"}}}
```

- `id` is the qualified passage id, shared by every record about that passage.
- `lines` is omitted on whole-declaration ends; path and span ride in the
  extension so a reader can build a source link at the commit. `end_line` is
  there only when the library's index has it (a published index may record
  `line` alone); a reader treats it as optional.
- `section` (the passage's heading path) and `prominence` ride on every record
  of the passage, so a reader can single out the subject passage.
- The subject end carries the passage's whole `span` (half-open offsets on the
  layer, never text) and the `layer_sha256` it indexes, so a reader holding
  that layer highlights the passage, not only the capped quote.
- A `subject` passage's subject end carries the article's QID as
  `identifiers.wikidata` (`"identifiers": {"wikidata": "Q207039"}`, beside
  `citation`), and no other passage's does (§2.2).
- A `closeMatch` always names its convention difference in `note`, one a
  challenge downgraded to `closeMatch` too (the challenger's note, else the
  first sentence of its witness that quotes the source within the quote cap).
- `proof_status` is joined from the index at export, never written by a worker.
- `location_key` = sha256(source_id, clean(item), library, commit,
  declaration) — run-free, for idempotent upserts by consumers. The run-scoped
  `finding_id` stays in the ledger.
- `challenge` ∈ {`survived`, `downgraded`, `not_required`, `sampled_out`,
  `challenge_pending`}; the last is a record a prominence overturn put under
  a rule (§5) after its challenge ran, read as unchallenged.
- At most **K = 5** locations per passage, ranked.

An absence is one record with `relation: "noMatch"`, `status:
"not_formalized"`, and **one `lean` end per universe library** (`type`, repo
`uri` and 40-hex `version`, nothing else: no `location`, no `role`, no
extension) — the spec's own checked-absence form, readable without fetching
anything. The universe id and sha ride in the
extension; the search receipt (queries, candidates examined, reasons) is a
sidecar file in the sealed record set, referenced by sha256 and count, never
inline.

Exported quotes are capped (twelve words of `exact`, at most 32 characters of
prefix and suffix) and carry the CSL citation and licence pointer above.

## 3. The stages

| stage | what | runs | model |
|---|---|---|---|
| L0 universe | per library: clone at commit, fetch caches, build, probe, ingest, prune | `$HOST`, sequential nice'd jobs | no |
| L1 sources | enumerate the corpus (PageAssessments API: titles, class, importance, page ids, QIDs, latest revisions), adopt consumer pins and bytes, fetch the rest once, render | one machine, then copied | no |
| L2 candidates | deterministic joins (tags, curated lists such as Mathlib's `$MATHLIB/docs/1000.yaml` and `$MATHLIB/docs/100.yaml`, docstring URLs, a library's own metadata such as FormalFrontier `formalization.yaml` main results) and lexical retrieval on title, aliases and bold terms; written to a read-only host directory keyed by source id | anywhere | no |
| L3a inventory | one task per source: deterministic finders (theorem boxes, bold definitions, lead sentence) then the enumerator; outputs the passage table, frozen by the gate | fleet | yes |
| L3b locate | tasks of **≤ 8 `formal_math` passages** from frozen inventories; each passage ends located, absent or unresolved | fleet | yes |
| L4 gate | §4 | out of tree | no |
| L5 challenge | separate tasks over batches of records; per-record verdicts (§5) | fleet | yes |
| L6 harvest, export | receipts, tally, derived status, `cf export literature-links` with a per-source byte budget | local | no |
| L7 consume | consumers import sealed record sets with their own credentials (§11) | consumer | no |

L3a/L3b/L5 are three presets (`locate-inventory`, `locate`,
`locate-challenge`) on a **new locate planner** (sourceset-scoped, not
companion-scoped; `scripts/cfcmd/plan_locate.py`) that renders tend roots on the same
gate/bridge/harvest machinery. Locate documents use the library id as `repo`
and `library:declaration@commit` as the declaration list, so `finding_id`,
the tally key and receipts work unchanged; a sourceset-level state file
replaces `companions/<name>/state.json` for locate.

Findings layout: one document per source under
`findings/<task-id>/<source-id>.json`, harvested to
`corpus/<run-id>/<task-id>/<source-id>.json`; the locate gate's changed-file
check, harvest's listing and its checksums are recursive (`find -print0`).

## 4. Gates (locate mode)

All mechanical, run from outside the worker's tree, over **only the task's own
documents** (`--findings` from the bridge), loading only the sources they name:

1. schema of the locate document; every exported record passes the vendored
   Literature Links checker (the vendored packages are copied into the gate
   directory with their manifest shas verified at gate start);
2. every quote matches **exactly once** on the source's Literature Links layer
   with its prefix/suffix, and its `In-text #<hex>` equals the passage hash of
   that quote's head; `source_sha`, `layer_sha256` and `renderer` match the
   pinned sourceset;
3. every lean end exists in `corpus.sqlite`'s exact table at that library and
   commit; the worker may not write `proof_status` (the field is refused);
4. a passage may not be scoped `non_math`/`expository`, and may not end absent,
   while L2 holds a tag or curated hit for the article's own QID that the
   document neither locates nor rejects with a reason; the gate computes those
   hits itself from L2, not from anything the worker wrote;
5. an absence lists at least three distinct queries in its receipt and names
   the universe frozen in `gate.json`;
6. at most K locations per passage, ranks distinct;
7. ownership: the changed files are that task's own directory, using the task
   id recovered from `<tend-root>/orchestrator.sqlite` (opened `mode=ro`, so
   the rows tend's WAL holds are read; `immutable=1` only when no `-wal` lies
   beside it or its directory cannot be written, and the last line then says
   so) when tend does not export `TEND_TASK_ID` to validation commands;
   otherwise the single-stem rule, and the gate's last line says which rule
   ran and, for the single-stem rule, why no task id was found;
8. an inventory document (L3a) may not drop a passage the deterministic
   finders found without a reason, and every `formal_math` paragraph of the
   source must be covered by a passage or a recorded skip (a paragraph-coverage
   floor).

As built (`scripts/gate/verify_locate.py`; `references/locate-documents.md`
§3): rule 4 has two sides. An inventory of an article with curated hits keeps a
`subject` passage scoped `formal_math` and scopes no `subject` passage out. A
locate document answers each hit the planner handed its task (the manifest's
`curated` list, intersected with the candidates file the gate reads itself)
with a location whose relation counts, or with a rejection that gives a reason.
A `relatedMatch` discharges nothing, and an `accepted` candidate must be one of
its passage's counting locations. Rule 4 is off in the blind arm. Rule 6 also
allows only one location per declaration. Rule 1 also holds a worker's free text
(notes, absence queries, candidate reasons) to the export's verbatim-source cap.
Rule 7 reads the task file: `tasks/<task-id>.yaml`, never added, changed in its
`status:` line only, and one task per change set unless `--pre-merge`. Rule 8
refuses a skip that holds a kept passage's whole span. A `by: attempt_cap`
document is the bridge's: its reason at a count of at least
`LOCATE_ATTEMPT_CAP`, and under `--changed-files` the counter in
`<tend root>/attempts/` at the cap.

## 5. Challenge

The tend reviewer in L3a/L3b checks form and gate output only. Judgment lives
in **L5**, separate tasks over batches of about 20 records, which write
per-record verdicts into their own documents, so one disputed record never
re-runs forty-nine others:

- every location on a `subject` or `central` passage is refuted by default:
  the challenger restates the passage, reads the declaration's signature and
  either confirms with a quoted witness or downgrades the relation;
- every absence and every unresolved outcome on a `subject` or `central`
  passage is hunted: the challenger searches first, with tools the worker did
  not have (ripgrep over the pinned checkouts, a type-search at the pin);
- every coverage claim (§2.6), whatever the passage's prominence, is refuted
  by default: the challenger matches each clause of the passage to a part and
  checks that no part adds a hypothesis, then confirms the claim or refutes it
  (the parts stay `hasPart`);
- a stratified sample of `non_math`/`expository` scopes, `supporting`
  demotions and weak relations (`relatedMatch`, `narrowMatch`, `hasPart`) is
  re-judged;
- everything else is `not_required` or `sampled_out`, and says so — except a
  part of a coverage claim the challenge confirmed, which is `survived` with
  the claim (§2.6); a passage whose prominence the challenge overturned to
  `subject` or `central` has its unjudged records marked `challenge_pending`,
  never left `not_required`, and that wins over the claim: a part it reopened
  stays `challenge_pending` beside a confirmed claim.

Hard rule 5 applies per record: nothing leaves the workspace without a
`challenge` value.

As built (`scripts/locate/outcomes.py`): a challenge applies only to the runs
its plan names, and only through the records it planned. A `downgrade` names a
strictly weaker relation, in the order `exactMatch` > `closeMatch` >
`broadMatch` > `narrowMatch` = `hasPart` > `relatedMatch`
(`cflib.vocab.RELATION_STRENGTH`; the gate checks the same order).

## 6. The host

- **Tools on the host.** `cf` and `cflib` are not on the host. The locate
  worker and challenger get two stdlib scripts, `corpus_search.py` and
  `corpus_show.py`, shipped in the read-only gate directory the way
  `verify_findings.py` is, allowlisted as `Bash(<absolute path>:*)`, with the
  exact invocation rendered into the prompts.
- **The session cap.** The account cap is 16 concurrent agent sessions per
  Unix user, across every root. The bridge takes one of 15 `flock` slot files
  before calling `agentbox` and waits rather than failing; an empty or 409
  envelope exits non-zero so tend retries the same worktree; every agent call
  is wrapped in `timeout` (45 minutes), and expiry is an infrastructure
  failure, not a verdict.
- **Convergence.** An attempt counter keyed on the task id, outside any
  worktree, counts only genuine reviewer verdicts; the third rejection writes
  a schema-valid `UNRESOLVED` document, which the reviewer bridge approves
  mechanically. Receipts count `UNRESOLVED` separately.
- **Roots** hold at most 500 tasks, fed in waves (`--max-open-tasks` about
  twice the session count); task YAML stays small; candidates and sources live
  in read-only host directories keyed by source id.
- **Cost and spend.** The bridge keeps Claude's JSON envelope outside
  the worktree as a cross-check; primary cost comes from transcripts priced by
  `scripts/cost`, exported within days of each run (transcripts age out after
  about thirty days). A host-side, model-free **watchdog** per shard drains the
  root when its spend passes the cap recorded in `run.yaml`, or when the
  gate-failure rate or cost per merged task exceeds a multiple of the pilot's
  p90. A recorded cap is a precondition of `cf run --execute` for locate.
- **`cf campaign`** renders a host-side sequencer (shell, no `cflib`)
  that starts shard k+1 when shard k ends, runs the watchdog, exports usage and
  prunes transcripts per shard; the operator's machine only harvests receipts.
- **Builds** run as the user, with a clean environment and nice/ionice. The
  user can still reach the agent daemon from a build, so building a
  third-party library is a trust decision the box owner signs (§13).
- **Labels never go to the host** (§8.4).

As built (`scripts/run/campaign.py`, `templates/campaign/sequencer.sh.tmpl`;
`references/runbook-server.md` §9): `cf campaign render` needs a pilot whose
smoke test is recorded, which then stands for every shard's. The sequencer
holds a lock, so one runs per campaign. It keys its state by run id, refuses a
root with a live `tend run`, and scans each shard's transcripts for the bench's
label canaries before it prunes them. A restarted shard keeps its first
transcript baseline and settles what its earlier attempt left first; a stopped
shard is never relaunched as it stands (only `CF_ACCEPT` passes a canary hit),
and a killed sequencer's watchdog never outlives its tend run or watches the
relaunch.

## 7. Budget and scale

Anchors (`$HOST`, 2026-09/10): single-item tasks cost $3.4–12 at Opus list
prices with 2.6–3.7 merged tasks per agent-hour and mean concurrency under 9.
Locate is budgeted **per passage**, and the pilot (§12, L-M4) measures dollars
and slot-minutes per passage before any tier is planned; a tier whose
projection exceeds the signed budget is refused.

*Estimate*, before the pilot: WikiProject Mathematics has about 19.5k concept
articles; Top+High is about 950, and Tier A (Top, High and Vital) about 1,500
articles and 54k passages. At one-fifth of a light single item per passage,
Tier A is roughly $38k and 5–6 days on one user's cap; every passage of every
concept article is roughly $250k and 40 days. Hence:

1. L0–L2 run over everything and cost nothing;
2. an **article-level tier** first: one subject passage per concept article
   (about 19.5k passages, bundled up to eight per task), which answers "is this
   article's subject formalized, and where" for the whole project;
3. passage-level work by tier (Tier A, then Mid), ordered within a tier by L2
   candidate yield;
4. anything beyond Tier A requires a cheaper worker: a direct-API tier, or a
   newer agent CLI on the host that admits a cheaper model, after a 1/1 smoke.

## 8. The benchmark

`cf bench` (`build | anchor | freeze | score | compare`) sits beside
`cf score`, which stays as it is.

### 8.1 Gold

| tier | what | how |
|---|---|---|
| G1 | maintainer-curated positives: Mathlib `@[wikidata]` tags, `$MATHLIB/docs/1000.yaml`, `$MATHLIB/docs/100.yaml` | `cf bench anchor`: resolve each declaration in the universe through the rename table (else drop with a reason); widen the accept set (`to_additive` twins, renames); anchor to a passage by an exact selector check; adjudicate the relation once with the frozen labeler; unanchorable entries score at article level only |
| G3 | **blind labels** of an independent sample: spans drawn from `sections.json`, not from any run | a frozen labeling workflow (its commit and prompt shas in the gold manifest) that never sees run records, with hunters on tools the system does not use (ripgrep over pinned checkouts, a type search at the pin, LeanSearch-class search with every hit re-checked at the pin); hunter recall *h* is calibrated tag-blind on G1, and G3 negative purity is reported as at least *h* |
| GX | exhaustive inventories of 8–10 articles | two independent enumerators plus the deterministic finders; the union adjudicated by the labeler and signed by the maintainer |
| G4 | the maintainer's spot checks | up to 30 negatives and 20 positives per release |
| P | planted | a module-ablated index removing the modules of the eval G1 declarations (any citation of a removed declaration is `E-MEMORY`); hypothesis mutations as synthetic sources |

G1 is scored in two arms that are never pooled: **G1-assisted** (production)
and **G1-blind** (L2's curated joins, gate rule 4 and the tag table turned off
for those items). Only G1-blind, G3 and P may carry a pre-registered metric.
Consumer-side human annotations are not a tier: they become G4 seeds, frozen
to those whose human provenance predates the first import.

### 8.2 Metrics

All per tier, never pooled, with article-clustered intervals:

- **false-not-formalized rate** (the safety metric): gold-formalized passages
  whose derived status is not `formalized`, for *any* reason, with the cause
  breakdown — inventory miss, scope demotion, absence, weak relation only,
  unresolved, gate-failed; unresolved and gate-failed tasks are misses, not
  exclusions;
- **overclaim rate**: derived `formalized` where gold is partial, related or
  absent (G3), plus a fixed audit of about 30 random exact/broad claims outside
  gold per arm;
- scope-error rate and unresolved rate (non-regression bounds);
- inventory recall (GX; G3 for an unbiased estimate);
- locate recall@k and precision@1, library recall (G3 only), relation
  confusion over six relations (consumer `special_case` labels are excluded:
  their direction is unknown);
- pre-gate rejection rates by reason (phantom declaration, outside the
  universe) from the gate log over every worker attempt (an agent's own
  `--findings` checks counted apart);
- cost per article over every session of its task, from transcripts;
  benchmark-construction spend in its own receipt.

Runs join to gold by **span overlap on the layer** (one-to-one, at least a
four-word overlap), never by id.

The definitions the bench settled are in `references/locate-bench.md` §8: the
join, the cause order of a miss, the interval method, the pins the
`measurement_id` hashes and the refusals. Where they are more precise than this
section, they are what is built.

### 8.3 Splits and pins

Articles are clustered (union-find over shared article QIDs and shared gold
accept-set declarations) and the cluster hash picks the split. Val/test
passages whose gold declaration also appears in dev form a separate
*seen-declaration* stratum, and a gain must hold on the unseen stratum.

A score document's `measurement_id` hashes every pin: skill commit, prompt and
gate shas, observed model ids (from transcripts, with counts), agent CLI,
tend and agentbox versions, universe sha, sourceset manifest sha, renderer
version, gold manifest sha, receipts, price table. An A/A replicate is matched
on the configuration shas a plan records (`run.yaml` `locate.config_shas`: the
prompt templates and the gate code, without run-specific bytes, and what the
agent wrappers run with -- agent CLI, model, sandbox bypass, agentbox profile,
attempt cap, merge target), not on the rendered prompts. `cf bench compare` refuses
arms whose observed model sets or renderers differ, and `cf bench score`
refuses an incomplete receipt.

### 8.4 Labels stay off the host

The fleet runs as the same Unix user that could read any file the host holds.
Val and test labels stay on the operator's machine; the host gets label-free
manifests; scoring runs locally on harvested records. Each label file carries
a canary string, and a transcript containing one fails the run.

### 8.5 Bench v0 (the pilot's benchmark)

1. Frame: the live Tier A list. Draw 40 articles uniformly and 40 from Tier A
   ∩ curated QIDs, recording inclusion probabilities. One source per task.
2. Split 40 dev / 40 eval by cluster; no test split in the pilot.
3. Eval labels: G1 anchored (about 20 positives); G3-blind, 8 spans per
   article (about 320 spans); GX for 8 articles; *h* measured tag-blind on G1;
   G4 checks.
4. Planted: one module-ablated index; a G1-blind arm.
5. Runs: the baseline twice on eval (A/A, interleaved, fresh roots) plus each
   candidate; the consumer's existing annotations on overlapping articles are
   scored as run 0 at no cost.
6. Expected precision: about ±0.1 on the false-not-formalized rate — enough
   to catch gross failure, not three-point changes. The pilot records
   decisions; it does not claim gains.

*Estimate*: $1.5–3.6k list-equivalent for v0.

## 9. The improvement loop

1. Run on dev; `cf bench score`; give every miss a mechanical error code
   (inventory miss, retrieval miss, rejected-correct, wrong relation, false
   absence, wrong scope, unresolved, `E-MEMORY`).
2. A proposer that sees only dev opens a change (prompts, finders, retrieval,
   gates) with a pre-registered claim, logged in the loop ledger.
3. A referee in a separate checkout and workspace re-runs dev and eval.
   Acceptance (applied only once eval holds about 150 articles or 400 gold
   positives; below that, decisions are recorded, not claimed):
   the pre-registered metric improves with its interval's lower bound above
   zero; the safety metrics do not regress (upper-bound increase ≤ 0.01);
   strata with n ≥ 30 do not drop by more than three points on the interval;
   Δcost per article stays within 20% on its paired interval; the A/A run
   rejects; multiplicity is controlled by alpha-spending across the ledger or
   replication on a fresh draw.
4. Test is scored only at checkpoints. A model change is a new baseline, never
   an improvement. The loop never edits gold; a gold change is its own change
   with a new manifest sha.
5. *references/locate-patterns.md* (*not implemented*) is fed from dev errors only and
   linted to fail if it names a val/test gold declaration or QID.
6. Each iteration has a spend cap and a reserved session quota (4 of 15), or
   runs between campaign shards; an unchanged baseline arm is reused when every
   pin matches.

## 10. Policy

- **Licence classes.** Sources gain `licence_class` (`closed` default,
  `open_attribution`, `open_share_alike`, `public_domain`) in
  `cflib/vocab.py`, `POLICY.md`, hard rule 6 and the registry validator, in one
  change. Wikipedia text is CC BY-SA 4.0: exported quotes stay capped (§2.7) as
  quotation, carry a citation and licence pointer, and a consumer that
  publishes its mapping under CC0 must state that quote fields are CC BY-SA.
  The skill repo still holds no source text.
- **Libraries are read, never trusted** (§6, Builds).
- **No production credentials on the host.** The fleet produces sealed,
  content-addressed record sets.
- **Network.** The Wikipedia fetcher (curl, raw bytes kept, fixed parameters)
  is a named network exception added to `CLAUDE.md` §3 in the same commit.

## 11. Consumers

### 11.1 Contract for any consumer

A consumer receives a **sealed record set**: `records.jsonl` per source,
receipt sidecars, the universe manifest (§2.3's shape: `universe_sha256`,
`frozen`, a list of libraries), the sourceset manifest slice and a `SEAL.json`
of shas that names the set, a campaign token (the campaign id, or
`run:<run>[+<challenge run>]` outside one), the universe, the sourceset, the
skill commit and the byte budget applied (none by default;
`references/locate-documents.md` §6). A consumer must not refetch the source; it gets the
canonical body's sha and, when it supplied the bytes (§2.1), matches them.

### 11.2 WikiLean

WikiLean's per-article D1 annotations are its human-moderated layer; its
native Literature Links storage is not yet live in production (migration 0014
unapplied, legacy schema 3 everywhere). The locate output therefore enters
WikiLean as a **separate, read-only atlas data plane** first, and the D1
import is a later, optional step:

1. **Atlas plane (first):** sealed record sets are published to an atlas store
   (`$WIKILEAN` decides R2, KV or static shards) keyed by page id and
   revision; article pages render an atlas panel from it; humans confirm or
   flag individual links into a small D1 table, which becomes G4 seed data.
   No D1 annotation write, no item identity matching, no 256 KB cap, no write
   limiter, no interaction with the nightly moderation pipeline.
2. **D1 import (later, gated):** once native storage is live, an importer in a
   release-neutral directory reads each article with the bearer GET, merges
   deterministically, POSTs with `base_version`, rebases once on 409, paces
   itself under the write limiter, writes per-article receipts keyed by the
   record-set sha, and owns identity: WikiLean item ids carry a passage
   lineage matched by span overlap on the layer, link ids are deterministic,
   supersession replaces older-universe locate links on AI items, never sets
   legacy items `rejected`, sends findings for human-owned items as proposals,
   and drops a passage overlapping a human tombstone. Its acceptance test is an
   import rehearsal on a copied D1 with zero 400/413/422 and an idempotent
   second import.

### 11.3 The Brain

WikiLean's Brain release gate accepts only provenance changes, its reducer
inputs are sealed, and non-Mathlib libraries are mentions-only by policy.
Locate output therefore reaches the Brain only (a) through
`brain/proposals/*.jsonl` and the offline fold, or (b) after the maintainer
chooses a gate mode for content changes. Concept-level edges come only from
`subject` passages, through the article's QID their records carry as
`identifiers.wikidata` (§2.2). That QID is the producer's claim, not yet a
validated one: the consumer validates it by checking its enwiki sitelink
against the article in a sealed Wikidata bundle (WikiLean's sealed entity
bundle, or the offline fold's QID and label checks), and a record whose QID
fails the check gives no concept-level edge. A record of any other passage
carries no QID and gives none. Each library maps to a `brain_lib` token unique
per repository.

## 12. Milestones

| | scope | done when |
|---|---|---|
| L-M0 | vocabulary, item production, web source, renderer (both views + map), sourceset, licence classes | unit tests; a synthetic Wikipedia page round-trips prepare → layer quote → gate; renderer parity with `$WIKILEAN`'s text layer |
| L-M1 | universe, probe, ingest, `corpus.sqlite`, host search/show scripts | the per-toolchain canary passes; counts reconcile with published indexes where they exist |
| L-M2 | L2 candidates; locate planner, presets, gate, bridge hardening (slots, timeout, envelope, attempt cap), export | a 10-article smoke run on `$HOST` merges, gates and exports valid records |
| L-M3 | `cf bench` and bench v0 gold | anchored G1, G3-blind eval labels, GX, *h* |
| L-M4 | pilot: baseline A/A plus one pre-registered comparison | a baseline with intervals and one recorded accept/reject decision |
| L-M5 | article-level tier over all concept articles | every shard receipt complete with UNRESOLVED ≤ 5%, residual queued |
| L-M6 | passage-level Tier A | as L-M5, within the signed budget |

**State, 2026-10-18.** The code of L-M0 to L-M3 is built and passes its unit tests
and the synthetic end-to-end test (`tests/e2e/locate_synthetic/`). Every "done when"
that needs something this repository cannot hold is open: renderer parity with
`$WIKILEAN`'s text layer, the per-toolchain canary on `$HOST`, the 10-article
smoke run, and bench v0's gold. The universe built on `$HOST` (44 of 45 libraries)
predates two fixes to the host drivers — the probe now receives a library's
`SRC_DIR`, and the disk floor measures the volume the work is written to — so a
library whose sources sit under a `SRC_DIR` other than `.` must be re-probed, and
its index re-ingested, before it counts.

## 13. Open questions for the maintainer

1. Locate in this skill (assumed) or a sibling skill?
2. May an open-licence benchmark manifest (revisions, shas, passage ids, gold
   declarations; no text, no labels for val/test) live in this repository?
3. Who signs universe membership and the trust decision to build third-party
   libraries on the shared host, and at what cadence are commits bumped?
4. The spend cap and who holds it; whether the per-user session cap can be
   raised; whether a direct-API route exists on the host.
