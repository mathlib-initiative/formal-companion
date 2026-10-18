# The locate benchmark

The files `cf bench` writes and reads (`docs/proposals/wikipedia-locate.md` §8 and §9). The
code is `scripts/bench/` behind `scripts/cfcmd/bench.py`; the labeler is
`workflows/locate-label.workflow.js`. Every example is synthetic. `cf score` is a different
measurement (faithfulness defects against an answer key) and shares nothing with this.

**Contents**: 1 layout · 2 the frame and the draws · 3 clusters, splits, spans and the host ·
4 G1 · 5 G3, GX, G4 and *h* · 6 the labeler · 7 the gold manifest and P · 8 the score
document · 9 the acceptance rule and the loop ledger · 10 what the bench reads

## 1. Layout

```
benches/<id>/bench.yaml               the bench's config and state (holds the canary)
benches/<id>/frame.json               the frame, resolved against the pinned sourceset
benches/<id>/draw.json                the two draws with inclusion probabilities
benches/<id>/clusters.json            article clusters and the split of each
benches/<id>/unrendered.txt           drawn articles not yet rendered (only while there are any)
benches/<id>/spans.json               the G3 span sample
benches/<id>/host/bench-host.json     LABEL-FREE: article ids per split; the only thing a host gets
benches/<id>/host/canaries.txt        the canary string, for a host-side transcript scan
benches/<id>/gold/g1-resolved.json    G1 before anchoring
benches/<id>/gold/g1.jsonl g3.jsonl gx.jsonl g4.jsonl    label files, a canary in every row
benches/<id>/gold/calibration.json    h, the hunters' recall
benches/<id>/gold/planted/            P: ablation.json and the module-ablated corpus.sqlite
benches/<id>/labeler/args-<mode>.json, results/   the labeler's args and copied results
benches/<id>/gold-manifest-<n>.json   gold version n, never rewritten
benches/<id>/spend.json               benchmark-construction spend, its own receipt
benches/<id>/scores/<split>-<id16>.json   score documents, named by split and measurement_id
benches/<id>/audits/<split>-<id16>.jsonl  the overclaim audit sample of one score
benches/<id>/ledger.jsonl             the loop ledger
```

`scripts/bench/layout.py` holds every one of these names. **Labels never go to the host**
(§8.4): `gold/`, `labeler/`, `spans.json`, `scores/` and `ledger.jsonl` stay on the operator's
machine; `host/` is the part that may be copied. The labeler args hold source excerpts and
stay local too.

## 2. The frame and the draws

`cf bench build <id> --sourceset S --frame <file> [--curated-qids <file>]… [--seed N]` reads:

- the **frame** file: page ids or source ids, one per line (`#` comments), or a JSON list of
  them or of `{"pageid"}`/`{"source_id"}` objects. Each row resolves to one source of the
  pinned sourceset manifest: a source id is taken as given, and a page id resolves to the
  source a run reads — the sourceset's own rule (`Manifest.preferred()`,
  `references/locate-documents.md` §7): the page's adopted revision when a consumer pinned
  it, else its highest enumerated revision — never the highest *rendered* one, which would
  bench a newer revision than the pin as soon as both are rendered. The article's QID and
  assessment are the page's: when the resolved entry has none, the one value the page's
  other entries agree on is used, and `page_facts_from` names the entry it came from. A row
  the manifest does not hold is **refused by name**, because a frame that quietly lost an
  article biases every inclusion probability; a row it holds **unrendered stays in the frame**
  (`rendered: false`, listed in `unrendered`) — the draw reads no layer, so the inclusion
  probabilities are the whole frame's and only the drawn articles need a render; a second row
  for a page already in the frame is a duplicate;
- the **curated lists** of the curated stratum (`--curated-qids`, repeatable;
  `scripts/bench/curated.py`; G1 in §4 reads the same files through L2's own join, which is
  narrower, so a stratum article may carry no G1 entry): Mathlib's `$MATHLIB/docs/1000.yaml` (QID →
  `{title, decl | decls}`; a key with one capital-letter suffix, `Q180345X` or `Q4724004A`,
  names its base QID and keeps its own name), its `$MATHLIB/docs/100.yaml` (theorem number →
  `{title, decl | decls, authors, links, note}`, no QID), or a plain list of QIDs (one per
  line, or a JSON list). A 100.yaml entry resolves to QIDs, in order, through its own
  `wikidata` field, a declaration it shares with a 1000.yaml entry given in the same call, or
  its folded title equal to a 1000.yaml entry's; resolved or not, it also names the article
  whose folded title is its own (NFKD without combining marks, curly quotes straightened,
  dashes hyphens, case folded, a leading "the " dropped). Nothing is fetched: a title only
  Wikipedia's redirects could resolve is the operator's to supply, as a QID in a plain list.
  A mapping key that is neither a QID nor a number is refused; a plain-list value that is not
  a QID is reported in the file's `rejected`, never read; the 100.yaml entries that name no
  frame article are listed in `curated_lists.unmatched_100` and counted by the build, so a
  list that matched nothing says so.

`frame.json`: `{schema: cf-bench-frame/1, bench, sourceset: {id, version, manifest_sha256},
frame_file_sha256, articles: [{source_id, pageid, revision, title, wikidata, assessment,
layer_sha256, renderer, rendered, revision_basis, page_facts_from, curated, curated_via}], N,
N_curated, N_adopted, N_rendered, unrendered, duplicates, curated_lists: {files: [{name, sha256,
kind, entries, qids, rejected,
resolved, title_only}], qids, unmatched_100: [{list, key, title, qids}]}}`. `revision_basis` is
`adopted`, `enumerated` or `source_id` (the frame file named the source); `curated_via` names
the entries that make an article curated (`mathlib_1000 Q180345X`, `mathlib_100 #57`,
`qid_list Q42`); a file's `kind` is `mathlib_1000`, `mathlib_100` or `qid_list`, and
`resolved` (100.yaml only) counts its entries by how they got a QID.

`draw.json`: two simple random samples without replacement, each from its own seeded
generator (`random.Random("<seed>:uniform")`, `"<seed>:curated"`): `uniform` (n of the whole
frame, default 40) and `curated` (n of the frame's curated articles, default 40). They are
independent, so an article can be in both, and each row records `pi_uniform = n/N`,
`pi_curated = n_c/N_c` (0 off the curated stratum) and
`pi = 1 - (1 - pi_uniform)(1 - pi_curated)`. A draw larger than its stratum takes all of it and
says `short: true`.

**A drawn article must be rendered** (its spans are drawn from its layer). When the pinned
manifest holds one unrendered, the build writes the drawn unrendered source ids to
`benches/<id>/unrendered.txt` and either:

- **refuses** (the default), naming them and the step that settles it: fetch and render
  exactly the listed sources (`cf sourceset fetch <set> --select benches/<id>/unrendered.txt
  --execute`, then `cf sourceset render <set> --source <id>`)
  and build again with the newer manifest; the draw depends on the frame and the seed alone,
  and source ids order by page id, so the same pages are drawn whichever revision each page
  resolves to; or
- with `--exclude-unrendered`, **benches the rest**: `articles` loses the excluded rows, which
  move, with their `pi_uniform`, `pi_curated`, `pi`, `in_uniform`, `in_curated` and a
  `reason`, to `excluded` (a key the file carries only then); `uniform.drawn` and
  `curated.drawn` stay as drawn, and the gold manifest carries `excluded` too, so a score of
  the rest says what was drawn and not benched, and with what probability.

The list is removed by a build that finds every drawn article rendered.

**A drawn article must be rendered by the current renderer** (`sources.render_html.RENDERER`).
A run is planned only on the current renderer's layers (`locate.inputs.check_source`), and
`cf bench score` refuses a run that read another rendering than the bench pins, so a bench
drawn on an older renderer's layers could score nothing. The build refuses one, naming each
drawn source and its renderer, before anything is written, with the step that settles it:
`cf sourceset create <new id> --from <set>`, `cf sourceset render <new id>`, and the bench
built on `<new id>` — whose draw is the same, since `--from` keeps every source id. An
undrawn frame article of another renderer is only recorded (`renderer` in `frame.json`).

## 3. Clusters, splits, spans and the host

**Clusters** are a union-find over the drawn articles: two articles are one cluster when they
share an article QID or a G1 accept-set declaration. Only G1's accept sets join: a G3 or G4
label is drawn after the split and names whatever the hunters found, and one widely used lemma
would otherwise fold the bench into one cluster; a gold declaration such a label shares with dev
puts its passage in the **seen-declaration stratum** instead (§8.3). A cluster's id is
`c-` + the first 16 hex of sha256 over its sorted member ids. Clustering runs again after G1 is
resolved, after every transcription and at freeze.

**The split** is picked by each cluster's own hash: `u = sha256("<bench>:<seed>:<cluster id>") /
2^256` against the cumulative shares `--dev`, `--eval`, `--test` (default 0.5/0.5/0: bench v0
has no test split). A cluster never spans two splits, and its split depends on its members
alone; the shares hold in expectation, and `cf bench build` prints what it got.

`clusters.json`: `{schema: cf-bench-clusters/1, bench, clusters: {cid: [source ids]}, splits:
{cid: split}, articles: {source id: split}, basis}`.

**The G3 span sample** (`spans.json`, `cf-bench-spans/1`) draws `--spans-per-article` (8)
blocks of each drawn article's `paragraphs.json` uniformly with `random.Random("<seed>:spans:<source
id>")`, among the blocks inside the layer that hold at least four words (so a span can join)
and that no other such block contains: blocks nest (an `li` holding a `p`, a theorem box
holding one, a displayed formula inside a paragraph), so the units are the outermost blocks,
disjoint and covering every block's text, `math` when any block of the unit is, so `M` counts
distinct units; two blocks with one span are one unit, and two that overlap without nesting are
refused; each carries its section path from `paragraphs.json` (or `sections.json`), its fragment,
`pi_span = k/M` and the article's `pi`. Its id is `g3-` + 12 hex of sha256 over `source id \n
start \n end`. It reads no run.

**The host manifest** (`host/bench-host.json`, `cf-bench-host/1`): `{bench, sourceset, articles:
{dev, eval, test}}` — the article ids a run must cover, and nothing else.

Re-running `cf bench build` with the same inputs rewrites the same files and keeps what anchoring
recorded in `bench.yaml` (`library`, `gx_articles`, `state`); once the bench holds gold, a build
that would change the frame, the draw, the span sample, the split fractions, the seed or the
sourceset pin is refused before anything is written (a new draw is a new bench id).

## 4. G1

`cf bench anchor <id> --universe U --curated-1000 <file> [--curated-100 <file>] [--rename-table
<file>] [--library L]` first opens the universe's `corpus.sqlite` through `corpus.query.open_db`
(read-only, immutable) and refuses it unless `universe.yaml` (read by
`corpus.universe.Universe.load`) records a built database and `corpus.query.check_universe`
passes: the database's `meta.universe_sha256` is the universe's recomputed sha. Gold stamps that
recomputed sha as `bench.yaml` `universe: {id, sha256}`; once anything is resolved or labelled,
a new state of the universe (an index re-ingested and rebuilt) is refused unless `--supersede`
starts the gold over at it. Then it resolves, mechanically:

- a `1000.yaml` entry (QID → `{title, decl | decls}`) matches the drawn articles with that QID
  (a lettered key, `Q4724004A`, by its base QID, keeping the key as written);
  a `100.yaml` entry (number → `{title, decl | decls, wikidata}`) matches by the QID it
  carries and by its title under `locate.candidates.normalise_title` (NFKC, `_` to a space,
  case-folded, a leading "the " dropped) -- the join `cf locate candidates` makes, over the
  article's title only (L2 also reads its aliases and bold terms); an entry with no declaration
  is counted, not used;
- every drawn article's QID is looked up in `corpus.sqlite`'s tag table (`wikidata`,
  `corpus.query.tagged`) for the named libraries (default: the universe's Mathlib,
  `corpus.universe.Universe.mathlib_library` -- the one library whose `brain_lib` is
  `Mathlib`, else the one whose id is `mathlib` -- the rule `cf locate candidates` uses); its
  evidence is the declaration's `path:line`;
- each declaration is resolved in the exact table at the universe's commit, following the
  rename table (`old new` or `old -> new` lines, or a mapping) up to ten hops; one that does not
  exist at the pin is **dropped with the reason**, and an entry left with nothing is dropped;
  when the listed name does exist (a deprecated alias), every later name of its rename chain
  that exists at the pin is accepted too, `why: renamed`;
- a resolved declaration that is a deprecated alias (`corpus.sqlite` `decls.deprecated_to`,
  the probe's `deprecated.new_name`) also accepts the new name it points at, when that exists at
  the pin, marked `why: renamed`;
- the **accept set** is widened with the name `to_additive` would give or take, over every
  accepted name: the whole image in one direction, every token translated at once (`mul`/`add`,
  `one`/`zero`, `prod`/`sum`, `inv`/`neg`, `div`/`sub`, `pow`/`nsmul`, `Monoid`/`AddMonoid`,
  `MulAction`/`AddAction`, …; `mul_one` → `add_zero`, never `mul_zero`), and none for a name
  that mixes the two kinds (`scripts/bench/gold.py` `twins`); it is kept only when it exists at
  the pin and marked `why: to_additive`;
- claims on one article whose accept sets meet are one entry, `g1-` + 12 hex.

Re-resolving prunes `gold/g1.jsonl` to the entries still resolved; freeze refuses a G1 tier
that does not label exactly the resolved entries.

`gold/g1-resolved.json`: `{schema: cf-bench-g1-resolved/1, bench, universe: {id, sha256},
library, commit, entries: [{entry_id, source_id, qid, title, via: [...], evidence: [...],
declarations, accept: [{library, declaration, commit, why}], dropped: [{library, declaration,
reason}]}], dropped_entries, unmatched, listed_without_declarations}`. `via` values are
`cflib.vocab.CANDIDATE_VIA`'s.

Anchoring is the labeler's (§6, mode `anchor`): it quotes the passage the accept set formalizes
and adjudicates the relation **once**. `gold/g1.jsonl` rows: `{gold_id, source_id, qid, via,
level: passage | article, span, quote, anchor_problem, passage_kind, relation, status, related,
scope, declarations, accept, evidence, labeler_sha256, args_digest, labeler_commit, canary,
tier}`. The anchor is kept only when the quote matches the layer **exactly once**
(`scripts/gate/locate_text.py` `find_quote`) and its span holds at least **four whole layer
words** (the join's floor: a shorter span could never join a run passage); otherwise the row is
`level: article`, with the reason in `anchor_problem`, and scores at article level only. The same
floor holds for GX and G4 quotes. `status` is
`cflib.vocab.derive_status` over the accept set at the labeler's relation with each
declaration's `proof_status` and `statement` joined from `corpus.sqlite`.

## 5. G3, GX, G4 and *h*

**G3** (`gold/g3.jsonl`), the blind labels of the span sample: `{gold_id (the span id), source_id,
span, section, scope, passage_kind, status, related, declarations: [{library, declaration,
commit, relation, proof_status, statement, evidence}], dropped, pi_span, pi_article, evidence,
hunters}`. Every declaration the labeler returns is **re-checked at the pin** by the
transcriber (exact table, universe commit, a known relation) and dropped with the reason
otherwise. `status`, for a `formal_math` span: `derive_status` over the declarations whose
relation counts, else `not_formalized` (the hunters searched, so an empty list is a checked
absence); `related` is true when a `relatedMatch` declaration stands beside it. A non-math span
has no status.

**GX** (`gold/gx.jsonl`), the exhaustive inventories of `--gx` (8) eval articles, drawn with
`random.Random("<seed>:gx")`: `{gold_id (gx- + 12 hex of the span), source_id, span, quote,
passage_kind, scope, from: [A, B, F], signed_by}` — two independent enumerators (A, B) plus the
deterministic finders (F), the union adjudicated. The labeler leaves an article unlabelled when an
enumerator failed, and the transcriber refuses an inventory that does not list both
(`enumerators: [A, B]`). A GX transcription needs `--signed-by`, and freeze refuses unsigned
inventories.

**G4** (`gold/g4.jsonl`), the maintainer's spot checks, from `cf bench anchor <id> --g4
<file.jsonl>`: rows `{source_id, quote, passage_kind, declarations, checked_by}`, each quote
exactly once on the layer, each declaration at the pin; at most 30 negatives and 20 positives
per release.

***h*** (`gold/calibration.json`, `cf-bench-calibration/1`): the labeler's `calibrate` mode runs
the G3 hunters **tag-blind** (no `@[wikidata]`/`@[stacks]`/`@[kerodon]`, no docstring URLs, no
curated lists, and no prompt names the article's QID) on every anchored G1 passage, and its
labeler judges only what they found, adding nothing; `h = found / total`, where *found* counts
the passages for which a declaration a **hunter** returned, kept at the pin, meets the entry's
accept set, and *total* is every anchored G1 passage. Transcriptions merge by gold id, so a
partial run keeps the earlier rows; `unanswered` lists the anchored passages with no row, and
`h` is null until it is empty (freeze refuses it then). G3's negative purity is reported as at
least *h* (§8.1).

## 6. The labeler

`workflows/locate-label.workflow.js`, run in a session. `cf bench anchor <id> --emit-args
<mode>` writes `labeler/args-<mode>.json` (`cf-locate-label-args-v1`) and prints the
`Workflow(...)` invocation; `cf bench anchor <id> --transcribe <result.json>` turns the result
(`cf-locate-label-v1`) into label rows. The modes are `cflib.vocab.BENCH_LABEL_MODES`:

| mode | tasks | agents per task | result row |
|---|---|---|---|
| `anchor` | resolved G1 entries, with each accept declaration's signature and path | one labeler | `{task_id, quote, relation, passage_kind, evidence}` |
| `label` | G3 spans, the span's text inline | `--hunters` (2) independent hunters, then one labeler | `{task_id, scope, passage_kind, declarations, evidence, hunters}` |
| `calibrate` | anchored G1 passages, **no accept set** | the same, tag-blind | the same |
| `inventory` | GX articles, with their finder passages | two independent enumerators, then one labeler | `{task_id, source_id, passages: [{quote, passage_kind, scope, from}]}` |

The args carry the bench's tasks, the sources' paths (reading copy and layer), the universe's
libraries at their commits with any `--checkout LIB=DIR`, `--type-search` and `--search-url`
hints — **nothing a run wrote**, and every prompt forbids looking for one. The hunters search
with ripgrep over the pinned checkouts, a type search at the pin and a LeanSearch-class
service, and every hit counts only once found in the pinned checkout.

**Frozen.** The args stamp `labeler: {version, workflow_sha256, skill_commit}` (the checkout
they were emitted from) and a `digest` over themselves;
the result echoes `args_digest` and the workflow's own `LABELER_VERSION` (`locate-label/2`). The
transcriber refuses a result for other args, from another labeler version, or when the workflow
file's sha changed since emission, and refuses a label for a task the args never named or
labelled twice (the workflow also forces each answer's `task_id` to its task's). An unlabelled
task stays unlabelled -- it writes no row -- and a transcription merges into the tier by gold
id: a resumed or partial run keeps the earlier labels of the tasks it did not answer. Every row
carries the labeler that wrote it (`labeler_sha256`, `args_digest`, `labeler_commit`), and a
tier never mixes two: a partial run of another labeler is refused, so it is re-run on every task. `--transcripts <dir>` prices the labeler's sessions into `spend.json`
(`cf-bench-spend/1`: the runs, their `cost` blocks, the total), the bench's own receipt, never
a run's.

## 7. The gold manifest and P

`cf bench freeze <id> [--plant] [--supersede]` writes `gold-manifest-<n>.json` (`cf-bench-gold/1`):

```json
{"schema": "cf-bench-gold/1", "bench": "b1", "version": 1, "previous_sha256": null,
 "sourceset": {"id": "wpm", "version": 3, "manifest_sha256": "<hex>"},
 "universe": {"id": "u1", "sha256": "<hex>"}, "library": "mathlib",
 "frame_sha256": "<hex>", "draw_sha256": "<hex>", "spans_sha256": "<hex>",
 "excluded": [{"source_id": "enwiki-1009-2010", "reason": "not rendered in sourceset wpm version 3",
               "pi": 0.04}],
 "clusters_sha256": "<hex>", "clusters": {"c-…": ["enwiki-1001-2002"]},
 "splits": {"dev": ["…"], "eval": ["…"], "test": []},
 "tiers": {"G1": {"file": "gold/g1.jsonl", "sha256": "<hex>", "rows": 20, "positives": 18,
                  "anchored": 17, "article_level": 3},
           "G3": {"file": "gold/g3.jsonl", "sha256": "<hex>", "rows": 320, "positives": 41,
                  "formal_math": 150},
           "GX": {"…": "…", "articles": 8, "signed_by": ["the maintainer"]},
           "P": {"ablation_sha256": "<hex>", "corpus_sha256": "<hex>",
                 "removed_declarations": 812, "modules": 14}},
 "seen": ["g3-…"], "h": {"found": 15, "total": 17, "h": 0.88, "tag_blind": true},
 "labeler": {"version": "locate-label/2", "skill_commit": {"label": "<40 hex>"},
             "workflow_sha256": {"label": "<hex>"}, "prompt_sha256": {"label": "<hex>"}},
 "canary_sha256": "<hex>", "created": "…", "manifest_sha256": "<hex>"}
```

- `manifest_sha256` is `cflib.hashing.sha256_json` of the manifest without that key,
  `created`, `version` and `previous_sha256`.
- `labeler` is read from the rows each mode wrote (G1 for `anchor`, G3 for `label`, GX for
  `inventory`, `calibration.json` for `calibrate`), never from args on disk, so re-freezing
  unchanged gold from a later checkout changes nothing; a tier whose rows name two labelers, or
  none, is refused.
- Freeze also refuses a G1 tier that does not label exactly the resolved entries, and an *h*
  that is not over every anchored G1 passage.
- `seen` lists the eval and test gold rows whose accept set (or counting declarations) meets a
  dev gold row's: the seen-declaration stratum.
- Every label row must carry the bench's canary, or freeze refuses.
- **Irreversible.** Re-freezing unchanged gold reports the existing version; changed gold is
  refused unless `--supersede`, which writes version n+1 chained by `previous_sha256`. Once
  frozen, `cf bench anchor` refuses to change a frozen tier without `--supersede`, and
  `cf bench score` refuses when any frozen file's sha no longer matches -- the label files, the
  frame, draw, spans and clusters, P's `ablation.json` and database, and the canary
  (`canary_sha256`). The loop never edits gold.
- `--plant` writes **P**: a copy of the universe's `corpus.sqlite` (opened and checked as in
  §4; refused when the universe is no longer at the state gold is labelled at) with every
  declaration (and its tags and FTS row) of the modules holding an eval G1 accept declaration
  removed. The copy keeps the `meta` table, so it passes `corpus.query.check_universe` for the
  same universe, plus `meta.planted = <bench id>`; `gold/planted/ablation.json`
  (`cf-bench-ablation/1`: `universe {id, sha256}`, modules, removed names, `source_db_sha256`,
  `corpus_sha256`). It is planted into `gold/planted.staging/` and moved into `gold/planted/`
  only with the manifest that records it, so a refused freeze leaves a frozen P as it was; a
  freeze without `--plant` carries the last manifest's P over, verified.
- **A P run** (proposal §8.1; the tools database of references/locate-documents.md §9): plan an
  eval locate run whose workers search the planted file while the gate and the outcomes check the
  full `corpus.sqlite` -- `cf plan-locate --preset locate --tools-corpus-db <file>`, naming
  `benches/<id>/gold/planted/corpus.sqlite`, which records `run.yaml`
  `locate.universe.tools_corpus_db: {kind: planted, sha256}`. A citation of a removed declaration
  then passes gate rule 3 (the full database holds it) and lands in the outcome rows as a
  location, which the score counts as `E-MEMORY`. Hypothesis mutations as synthetic sources are
  *not implemented*.

## 8. The score document

`cf bench score <id> --run <locate run> --split {dev,eval,test} [--arm A] [--gate-log <path>]
[--transcripts <dir> | --canary-report <file> | --no-canary-check] [--article-costs <file>]
[--audit-labels <file>] [--pin KEY=VALUE] [--bootstrap N] [--seed N] [--checkpoint]
[--subset <ids>|<file>]` writes
`scores/<split>-<first 16 hex of measurement_id>.json` (`cf-bench-score/1`); re-scoring the same
run on the same split rewrites the same file, whatever the checkout committed in between. Top-level
keys:

`schema, bench, split, arm, checkpoint, run, runs, subset, subset_articles, subset_sha256,
measurement_id, pins, scorer, gold_manifest, coverage, canary_check, tiers, strata, cost,
gate_rejections, misses, audit, bootstrap, per_cluster, created`.

**A pre-registered subset.** `--subset` (source ids, comma-separated and repeatable, or a file of
them: one per line with `#` comments, or a JSON list) scores only the articles it names, each of
which must be an article of the split, for a run that covers fewer than the whole split (a smoke
run, a pilot). Every coverage rule below applies to those articles alone. The document says
`subset: true` with the sorted `subset_articles` and their `subset_sha256`; `pins.subset:
{articles, sha256, of_split}` puts the list in `measurement_id`; and the file is
`scores/<split>-subset-<id16>.json`. A whole-split score has `subset: false`, nulls beside it
and no `pins.subset`, so its `measurement_id` is what it always was. `cf bench compare` refuses a
subset score in any role (§9). Gold must be frozen for a subset score too: freezing stays its own
explicit step (`cf bench freeze`).

**The runs** (`runs: {inventory, locate, challenge}`): the inventory run is the locate run's
`run.yaml` `locate.inventory_run` (with any the outcome rows' `runs.inventory` name; a run
naming none is refused), and the challenge runs are the ones the locate tally built the outcome
rows with (`reports/locate-tally.<run>.json` `challenge_runs`), so they are pinned even when no
verdict touched a row. Every one of them must have a receipt that is complete, **gated**
(`locate.inputs.require_gated`: `gate.ok`) and priced (a `cost` block); a run read through
`run.yaml` without a locate block is refused (`locate.outcomes.run_yaml`); and every run's
`locate.arm` must be the locate run's -- a G1-blind score rests on a blind inventory and blind
challenges.

**Run passages.** The frozen inventory (`locate.outcomes.inventory`, keyed by
`cflib.items.join_key`) supplies each passage's span, scope and kind; the outcome rows
(`locate.outcomes.outcome_row`) supply outcome, status and locations; the locate documents
(`locate.inputs.harvested_documents`) supply each passage's receipt. **Coverage** is two
checks. The inventory run's task manifests set inventory coverage: an article of the split they
did not plan is refused, one whose document is `unresolved` is *unresolved*, and one planned
with nothing landed is *gate-failed*. The outcome rows set locate coverage: the planner writes
a row for every passage the locate run planned -- landed, or `missing: true` when no document
landed, which ends as gate-failed -- so a `formal_math` passage of a covered article with no row
was never planned (a `--source` or shard run, see its `locate.source_ids`), and the score is
refused rather than counting it as a gate failure. An article-tier run (`locate.subject_only`)
is refused outright: it located one subject passage per article and is not scored passage by
passage.

`coverage`: `articles, clusters, gold_positives, total_positives, articles_unresolved,
articles_gate_failed, run_passages, basis, inventory_planned, outcome_rows, missing_rows,
challenge_owed: {located, absent, unresolved, total}` -- `challenge_owed` counts the rows with an
outcome whose `challenge` is still null (owed a verdict).

**The join** (§8.2, last paragraph): a gold passage and a run passage of one source join when
at least **four whole layer words** lie inside both spans; one to one across distinct passages,
greedily by overlap, ties broken by gold id then item. Gold rows that are one passage of the
text -- a run passage already holding a gold span joins another only when the two gold spans
share four words (two G1 entries anchored to one theorem) -- may all join the run passage that
holds them; two disjoint gold passages never share one. Never by id.

**Metrics**, per tier, each kept as a numerator and denominator per article cluster
(`per_cluster`) with a 95% percentile interval over `--bootstrap` (2000) cluster resamples,
seeded per metric:

| metric | over | counts |
|---|---|---|
| `fnf_rate` (the safety metric) | gold passages with status `formalized` | those whose run status is not `formalized`, for any reason; `causes` breaks them down by `cflib.vocab.FNF_CAUSES`, first applicable: unresolved and gate-failed articles, then no joined passage (`inventory_miss`), a run scope other than `formal_math` (`scope_demotion`), no locate result (`gate_failed`), an unresolved outcome or no status (`unresolved`), an absence (`absence`), else `weak_relation` (partial or statement-only) |
| `article_fnf_rate` | G1 entries that could not be anchored, with gold status `formalized` | articles with no run passage `formalized` at an accept-set declaration (`E-WRONG-RELATION` when one locates it at a weaker status, else `E-RETRIEVAL-MISS`) |
| `overclaim_rate` | joined run passages derived `formalized` whose gold has a status | gold not `formalized` |
| `scope_error_rate` | joined passages whose gold has a scope | run scope ≠ gold scope |
| `unresolved_rate` | joined `formal_math` run passages, and the `formal_math` gold passages of unresolved or gate-failed articles | an unresolved outcome, no locate result (gate-failed, `missing`), no status; every passage of such an article |
| `inventory_recall` | GX passages; G3 `formal_math` spans weighted by `1/(pi_span·pi_article)` | joined |
| `recall_at_1/3/5` | gold passages with an accept set (G1's, else the counting gold declarations) | a run location of rank ≤ k in it |
| `precision_at_1` | joined passages with an accept set and a rank-1 location | rank 1 in it |
| `library_recall` (G3) | (gold passage, library of a counting gold declaration) pairs | a run location in that library |
| `memory_rate` (P) | eval G1 entries (passage or article level) whose accept set the planted index removed | its joined passage cites any removed declaration, or any passage of its article cites one of its accept set; every citation of a removed declaration anywhere is an `E-MEMORY` miss |

`relation_confusion` counts, per tier, gold relation × run relation over the six relations,
for every run location at a gold declaration. `strata` repeats **every** metric of the tier
(P included) per article importance and class, gold passage kind and, outside dev,
`seen:yes`/`seen:no`.
`misses` lists every miss with its mechanical **error code** (`cflib.vocab.ERROR_CODES`):
inventory miss `E-INVENTORY-MISS`, scope demotion `E-WRONG-SCOPE`, unresolved and gate-failed
`E-UNRESOLVED`, an absence `E-FALSE-ABSENCE` (or `E-REJECTED-CORRECT` when the receipt rejected
an accept declaration), a weak relation `E-WRONG-RELATION` when a run location is in the accept
set, else `E-REJECTED-CORRECT` or `E-RETRIEVAL-MISS`, an overclaim `E-WRONG-RELATION`, and a
planted citation `E-MEMORY`.

**P.** A run whose `run.yaml` `locate.universe.tools_corpus_db.kind` is `planted` is scored on
tier P alone: `tiers.P: {gold, joined, metrics: {memory_rate}, memory_citations,
ablation_sha256, note}`, where the gold is the eval G1 passages whose accept set meets the
ablation's removed declarations and `memory_citations` counts the run's locations at a removed
declaration. It is refused when the bench has no planted tier, when its tools database's sha is
not the ablation's `corpus_sha256`, and off eval.

`cost`: dollars per article, per cluster, two ways -- every stage (`value`, and
`per_cluster.cost.usd_per_article`) and the locate and challenge spend alone
(`locate_and_challenge`, `per_cluster.cost.usd_per_article_locate`). Each run's receipt prices
the whole run and each run covers its own sources, so a run's dollars are spread over its
`run.yaml` `locate.source_ids` (else the sources its task manifests planned) and an article is
charged the share of every run that covered it; `runs: {run: {usd, sources, share}}` and
`by_stage: {inventory, locate, challenge}` (dollars per article of the split) say how.
`--article-costs` (`{source id: dollars}` over every session of each article's task) replaces
the spread for both figures (`by_stage: null`); `basis` says which. Every figure -- a receipt's
`cost.usd`, an `--article-costs` value -- must be a finite, non-negative number, or the score is
refused: a cost it cannot read is never read as $0. Nor is a lower bound read as a cost: a receipt
whose `cost.usd` is one (`cost.fleet.lower_bound`: `unpriced_calls` > 0, a call that left no priced
envelope such as an agent timeout, or under the transcripts basis a model in no price table) is
refused unless `--article-costs` prices every article, and the score then says
`usd_total_lower_bound: true` and names the runs under `lower_bound_runs`.

`gate_rejections`: per run of the score, its harvested `runs/<run>/host/gate.jsonl` (or the
`--gate-log` file or directory, filtered to the run), else `null`: `{attempts, rejected,
rejection_rate, broken, dropped_lines, by_rule, by_reason}`. Only worker calls of that run
count: a line with an empty `task_id` (the harvest's own re-gate) or another `run_id` is
dropped, and a call that exited 3 (the gate could not run) is `broken`, never a rejection. A
rule is the line's `by_rule` key, else the reason's `[rule N]` prefix; `by_reason` classes are
`rule_<N>` plus, when the message says which, `:<sub-class>` (`phantom_declaration`,
`outside_universe`, `universe_pin`, `proof_status_written`, `curated`, `absence_receipt`,
`ranks`, `note`, `pin`, `quote`, `passage_id`, `coverage`, `ownership`, `schema`), matched on
the message after the `<document>: <where>:` prefix so a passage's item id never classifies a
reason. `audit`: about 30 exact/broad run locations outside every gold passage, seeded, written
to `audits/<split>-<id16>.jsonl` with `verdict: null` for labelling; `--audit-labels` counts the
labelled ones (`correct` | `overclaim`).

`canary_check`: `--transcripts <dir>` scans the transcripts here (`basis: transcripts`);
`--canary-report <file>` reads one host scan (`basis: host report`); with neither, every run's
harvested `runs/<run>/host/canary.json` is read (`basis: host reports`), and a run without one
leaves the check undone, which is refused unless `--no-canary-check` (`ran: false`). A scan is
`{"scanned": <transcripts scanned>, "hits": [<file names holding a canary>]}`; any hit fails the
run. A check that read nothing is refused, never passed: a `--transcripts` path that does not
exist or holds no file, a transcript that cannot be read (a bad `.gz` included), and a host scan
whose `scanned` is not a positive count.

**Pins and `measurement_id`** (§8.3): `pins` holds, per run of the score, `skill_commit` (its
`run.yaml` `locate.skill_commit`, the checkout it was planned from, else `unrecorded`),
`gate_files` (the sha of its `locate.gate_files` map), `prompts` and `gate` (its `gate.json`
sha); then `observed_models` (the receipts' `cost.tokens` models, counted by
`messages_by_model` when present, else by tokens), `agent_cli`, `tend`, `agentbox` (from
`run.yaml` `versions`, or `--pin`; `unrecorded` otherwise), `universe_sha256`,
`gold_universe_sha256`, `corpus_db_sha256`, `tools_corpus_db: {kind, sha256}` (`full` when
unrecorded), `sourceset_manifest_sha256` (from the locate run's `locate` block), `renderer` (the
renderers the inventory documents of the split declare, each refused unless it is the bench's
for that article, as is a document naming no `layer_sha256` or `renderer`),
`gold_manifest_sha256`, `receipts` (each `RECEIPT.json`'s sha), `price_table_sha256` and
`challenge_runs`. `--pin` fills a version no `run.yaml` records, and is refused when it
contradicts one. `measurement_id` is `cflib.hashing.sha256_json(pins)`. The scorer's own code
is **not** a pin: `scorer: {sha256, files}` (a sha over `scripts/bench/*.py`, names and bytes)
sits beside it, so re-scoring an unchanged run with any checkout writes the same file, and
`cf bench compare` refuses two scores of different scorers.

**Refusals**: a receipt that is not `complete`, not gated or has no `cost` block; a run without
a locate tally; gold changed after freeze; a run planned on another universe, or another state
of it, than gold is labelled at (re-anchor with `--supersede`); runs of mixed arms, or `--arm`
other than the run's; a subject-only run; an inventory whose task manifests did not plan every
article of the split, or that read another rendering than the bench pins; a `formal_math`
passage of a covered article with no outcome row; an inventory document naming no layer or
renderer, or another renderer than the bench's; a cost that is not a finite non-negative number;
a `--pin` contradicting a recorded version; a planted run that is not this bench's frozen
ablation, or off eval, or a run that searched the planted database but records another kind;
the test split without `--checkpoint`; no canary check at all, or one that read nothing; and any
transcript (or host scan) holding the canary, which fails the run (§8.4).

## 9. The acceptance rule and the loop ledger

`cf bench compare <id> <baseline> <candidate> --claim <tier.metric> [--aa <replicate>]
[--replication BENCH:ENTRY] [--alpha 0.05] [--bootstrap N]` refuses a subset score (§8: a
pre-registered subset is a measurement, never a claim) as the baseline, the candidate or the
A/A replicate, before it reads anything else; an alpha outside
`0 < alpha < 0.5`; two scores of different benches, splits, arms, gold manifests or scorers
(`scorer.sha256`); two whose workers searched different kinds of tools database
(`pins.tools_corpus_db.kind`), or one planted and one not; one scored after a challenge run
(`pins.challenge_runs`)
and one not, or one owing challenge verdicts (`coverage.challenge_owed.total`) and one not; and
— **a model change is a new baseline, never an improvement** — two whose `observed_models` sets
or `renderer`s differ. Only G1 (blind arm), G3 and P metrics may be the claim. It pairs the two runs cluster by cluster (the same resampled clusters for both) and
accepts only when every condition holds:

1. `claim`: the claim metric's improvement (lower is better for the rates and cost, higher for
   recalls and precision) has its lower bound above zero at the comparison's alpha, and so does
   the unseen-declaration stratum's (`claim_unseen`), which outside dev must exist for the claim
   metric in both scores (a missing one fails); the bootstrap must put at least ten draws below
   that bound (`len(draws) · alpha_j ≥ 10`), else the comparison is refused with the
   `--bootstrap` it needs;
2. `safety`: in every tier both hold, the 95% upper bound of the increase of `fnf_rate` and of
   `overclaim_rate` is at most 0.01; a baseline with no such rate (or only zero denominators) is a
   rate of zero, so the bound is the candidate's own rate's, and a candidate with no such errors
   passes;
3. `strata`: no stratum with n ≥ 30 in both drops by more than three points (its 2.5% bound of
   improvement is at least −0.03), for every metric both scores stratify
   (`conditions.strata.checked` keys `<stratum>.<metric>`);
4. `cost`: the 95% upper bound of the cost per article's increase is at most 20% of the
   baseline's -- on the locate and challenge spend (`usd_per_article_locate`) when the two
   scores share their inventory runs, whose cost would otherwise dilute the rise, else on every
   stage (`usd_per_article`); `conditions.cost.cells` says which. Two price tables
   (`pins.price_table_sha256`) fail it, and so does any rise over a zero-cost baseline (20% of
   nothing is nothing);
5. `aa`: the same rule applied to the baseline and its A/A replicate does **not** accept (no
   `--aa` fails the condition). The replicate must be a distinct run -- not the baseline's
   measurement, run or locate receipt -- of the baseline's configuration: the comparison is
   refused when any of `universe_sha256`, `sourceset_manifest_sha256`, the tool versions, the
   runs' `skill_commit`, `gate_files` and `prompts`, or `price_table_sha256` differ;
6. multiplicity: the j-th claimed, non-replication entry of the bench's ledger uses
   `alpha · 2^-j` (alpha-spending). `--replication <bench>:<entry id>` replicates an accepted,
   claimed entry of ANOTHER bench's ledger (a fresh draw is a new bench) for the same claim, and
   uses alpha; it is refused within one bench, for an entry that is no accepted claim, and a
   second time for one entry.

The ledger is always `benches/<id>/ledger.jsonl`; a comparison holds an exclusive lock on
`ledger.jsonl.lock` from reading it to appending, so two comparisons never spend one alpha.
Below 150 eval articles and 400 gold positives, or when the candidate was scored without a
canary check, the decision is **recorded, not claimed** (`claimed: false`). Each comparison
appends one row to `ledger.jsonl` (`cf-bench-ledger/1`): `{entry_id, bench, at, claim, split,
arm, baseline, candidate, gold_manifest_sha256, alpha, alpha_j, replication, replicates,
bootstrap, conditions, decision, claimed, claim_basis, differences, failed, note}`;
`differences` names the pins that differ without making the scores incomparable (universe,
corpus database, sourceset manifest, tool versions, the runs' commits, gate files and prompts),
notes when both scores still owe challenge verdicts, and says when the baseline's measurement
is one an earlier entry already used: an unchanged baseline arm is reused, never re-run (§9,
step 6).

## 10. What the bench reads

From other packages, by `references/locate-documents.md`, each through the package that owns
its reader (`scripts/bench/inputs.py` re-implements none):

| file | read by | fields |
|---|---|---|
| `sourcesets/<id>/manifest-<n>.jsonl` (§7) | `sources.sourceset.Sourceset` (`latest`, `load`, `pinned`; `Manifest.pin()`, `preferred()` -- which source of a page is read, called, not copied) | entries `source_id, pageid, revision, title, wikidata, assessment, origin, adopted_from, layer_sha256, renderer` |
| `sourcesets/<id>/sources/<source>/layer.txt`, `paragraphs.json`, `sections.json`, `finders.json` (§7) | the bench (the layer's sha checked against the manifest) | block `span`/`section`/`block`, section `path`/`fragment`/`span`, finder `finder`/`span` |
| `corpora/<u>/universe.yaml` (§8) | `corpus.universe.Universe.load` (validating; `sha256()` recomputed, `mathlib_library()`, `corpus_db`) | `id`, `libraries[].{id, repo, commit, brain_lib}` |
| `corpora/<u>/corpus.sqlite` (§8) | `corpus.query`: `open_db`, `check_universe`, `exists`, `declaration` (with `deprecated_to`), `tagged` (with `path`, `line`), `libraries` | `meta.universe_sha256`; `decls`; `tags`; the planted copy is the bench's own (§7) |
| `reports/locate-outcomes.<run>.jsonl` (§10) | the bench (the rows of `locate.outcomes.outcome_row`) | `source_id, item, scope, passage_kind, prominence, outcome, status, locations[].{library, declaration, relation, rank}, runs.{inventory, locate, challenge}, challenge, missing, span` |
| `reports/locate-tally.<run>.json` (§10) | the bench | `challenge_runs` |
| `corpus/<run>/<task-id>/<source-id>.json` (§1–§3, §10) | `locate.outcomes.inventory` (inventory runs), `locate.inputs.harvested_documents` (any run) | inventory documents' `layer_sha256`, `unresolved`, `passages[].{item, span, scope, passage_kind}`; locate documents' `passages[].receipt.candidates[].{library, declaration, verdict}` |
| `runs/<run>/tend-root/gate/tasks/*.json` (§9) | `locate.outcomes.task_manifests` | the inventory run's `documents[].source_id` (inventory coverage); any run's, for its cost share when `run.yaml` names no `source_ids` |
| `runs/<run>/run.yaml`, `runs/<run>/gate.json` (§9) | `locate.outcomes.run_yaml` (a locate block required) | `prompts`, `versions`, `locate.{mode, arm, inventory_run, source_ids, subject_only, skill_commit, gate_files, sourceset, universe.{id, sha256, corpus_db_sha256, tools_corpus_db.{kind, sha256}}}`; the gate's sha |
| `corpus/<run>/RECEIPT.json` | `locate.inputs.require_gated` | `complete`, `gate.ok`, `cost.{usd, tokens, messages_by_model, price_table_sha256}` |
| `runs/<run>/host/gate.jsonl` (brought home by `cf harvest`) | the bench | one line per gate call: `run_id, task_id, task_id_from, changed_files, ownership, mode, ok, exit, documents, by_rule, reasons` (each reason `[rule N] <document>: <where>: <message>`) |
| `runs/<run>/host/canary.json` (brought home by `cf harvest`) | the bench | `{scanned, hits}` |
