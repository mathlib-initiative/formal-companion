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
- the **curated lists** (`--curated-qids`, repeatable; `scripts/bench/curated.py`, which
  `cf bench anchor` reads its lists with too): Mathlib's `$MATHLIB/docs/1000.yaml` (QID →
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
  exactly the listed sources (`cf sourceset fetch`, then `cf sourceset render --source <id>`)
  and build again with the newer manifest; the draw depends on the frame and the seed alone,
  and source ids order by page id, so the same pages are drawn whichever revision each page
  resolves to; or
- with `--exclude-unrendered`, **benches the rest**: `articles` loses the excluded rows, which
  move, with their `pi_uniform`, `pi_curated`, `pi`, `in_uniform`, `in_curated` and a
  `reason`, to `excluded` (a key the file carries only then); `uniform.drawn` and
  `curated.drawn` stay as drawn, and the gold manifest carries `excluded` too, so a score of
  the rest says what was drawn and not benched, and with what probability.

The list is removed by a build that finds every drawn article rendered.

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
id>")`, among the blocks inside the layer that hold at least four words (so a span can join);
each carries its section path from `paragraphs.json` (or `sections.json`), its fragment,
`pi_span = k/M` and the article's `pi`. Its id is `g3-` + 12 hex of sha256 over `source id \n
start \n end`. It reads no run.

**The host manifest** (`host/bench-host.json`, `cf-bench-host/1`): `{bench, sourceset, articles:
{dev, eval, test}}` — the article ids a run must cover, and nothing else.

Re-running `cf bench build` with the same inputs rewrites the same files; once the bench holds
gold, a build that would change the draw is refused (a new draw is a new bench id).

## 4. G1

`cf bench anchor <id> --universe U --curated-1000 <file> [--curated-100 <file>] [--rename-table
<file>] [--library L]` resolves, mechanically:

- the lists are read as §2 reads them: a `1000.yaml` entry (QID → `{title, decl | decls}`,
  a suffixed key naming its base QID) matches the drawn articles with that QID; a `100.yaml`
  entry (number → `{title, decl | decls}`, no QID) matches the drawn articles its resolved
  QIDs name and the one whose folded title is its own, and its `evidence` says which
  (`mathlib_100 57 (by title)`, `mathlib_100 7 (QID from a declaration shared with
  1000.yaml)`); an entry with no declaration is counted, not used;
- every drawn article's QID is looked up in `corpus.sqlite`'s tag table (`wikidata`) for the
  named libraries (default: the universe's library whose `brain_lib` is `Mathlib`);
- each declaration is resolved in the exact table at the universe's commit, following the
  rename table (`old new` or `old -> new` lines, or a mapping) up to ten hops; one that does not
  exist at the pin is **dropped with the reason**, and an entry left with nothing is dropped;
- the **accept set** is widened with the names `to_additive` would give or take (`mul`/`add`,
  `one`/`zero`, `prod`/`sum`, `inv`/`neg`, `div`/`sub`, `Monoid`/`AddMonoid`, … one
  substitution at a time, `scripts/bench/gold.py` `twins`), each kept only when it exists at
  the pin and marked `why: to_additive`;
- claims on one article whose accept sets meet are one entry, `g1-` + 12 hex.

`gold/g1-resolved.json`: `{schema: cf-bench-g1-resolved/1, bench, universe: {id, sha256},
library, commit, entries: [{entry_id, source_id, qid, title, via: [...], evidence: [...],
declarations, accept: [{library, declaration, commit, why}], dropped: [{library, declaration,
reason}]}], dropped_entries, unmatched, listed_without_declarations}`. `via` values are
`cflib.vocab.CANDIDATE_VIA`'s.

Anchoring is the labeler's (§6, mode `anchor`): it quotes the passage the accept set formalizes
and adjudicates the relation **once**. `gold/g1.jsonl` rows: `{gold_id, source_id, qid, via,
level: passage | article, span, quote, anchor_problem, passage_kind, relation, status, related,
scope, declarations, accept, evidence, canary, tier}`. The anchor is kept only when the quote
matches the layer **exactly once** (`scripts/gate/locate_text.py` `find_quote`); otherwise the
row is `level: article` and scores at article level only. `status` is
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
deterministic finders (F), the union adjudicated. A GX transcription needs `--signed-by`, and
freeze refuses unsigned inventories.

**G4** (`gold/g4.jsonl`), the maintainer's spot checks, from `cf bench anchor <id> --g4
<file.jsonl>`: rows `{source_id, quote, passage_kind, declarations, checked_by}`, each quote
exactly once on the layer, each declaration at the pin; at most 30 negatives and 20 positives
per release.

***h*** (`gold/calibration.json`, `cf-bench-calibration/1`): the labeler's `calibrate` mode runs
the G3 hunters **tag-blind** (no `@[wikidata]`/`@[stacks]`/`@[kerodon]`, no docstring URLs, no
curated lists) on every anchored G1 passage; `h = found / total`, where *found* counts the
passages whose re-checked declarations meet the entry's accept set. G3's negative purity is
reported as at least *h* (§8.1).

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

**Frozen.** The args stamp `labeler: {version, workflow_sha256}` and a `digest` over themselves;
the result echoes `args_digest` and the workflow's own `LABELER_VERSION`. The transcriber refuses
a result for other args, from another labeler version, or when the workflow file's sha changed
since emission, and refuses a label for a task the args never named. An unlabelled task stays
unlabelled, and a transcription merges into the tier by gold id: a resumed or partial run keeps
the earlier labels of the tasks it did not answer. `--transcripts <dir>` prices the labeler's sessions into `spend.json`
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
 "labeler": {"version": "locate-label/1", "skill_commit": "<40 hex>",
             "workflow_sha256": {"label": "<hex>"}, "prompt_sha256": {"label": "<hex>"}},
 "canary_sha256": "<hex>", "created": "…", "manifest_sha256": "<hex>"}
```

- `manifest_sha256` is `cflib.hashing.sha256_json` of the manifest without that key,
  `created`, `version` and `previous_sha256`.
- `seen` lists the eval and test gold rows whose accept set (or counting declarations) meets a
  dev gold row's: the seen-declaration stratum.
- Every label row must carry the bench's canary, or freeze refuses.
- **Irreversible.** Re-freezing unchanged gold reports the existing version; changed gold is
  refused unless `--supersede`, which writes version n+1 chained by `previous_sha256`. Once
  frozen, `cf bench anchor` refuses to change a frozen tier without `--supersede`, and
  `cf bench score` refuses when any frozen file's sha no longer matches. The loop never edits
  gold.
- `--plant` writes **P**: a copy of the universe's `corpus.sqlite` with every declaration (and
  its tags and FTS row) of the modules holding an eval G1 accept declaration removed, and
  `gold/planted/ablation.json` (`cf-bench-ablation/1`: modules, removed names, both shas). A run
  planned against that database is a P run; any location it reports at a removed declaration is
  `E-MEMORY`. Hypothesis mutations as synthetic sources are *not implemented*.

## 8. The score document

`cf bench score <id> --run <locate run> --split {dev,eval,test} [--arm A] [--gate-log <dir>]
[--transcripts <dir> | --canary-report <file> | --no-canary-check] [--article-costs <file>]
[--audit-labels <file>] [--pin KEY=VALUE] [--bootstrap N] [--seed N] [--checkpoint]` writes
`scores/<split>-<first 16 hex of measurement_id>.json` (`cf-bench-score/1`); re-scoring the same
run on the same split rewrites the same file. Top-level keys:

`schema, bench, split, arm, checkpoint, run, runs, measurement_id, pins, gold_manifest,
coverage, canary_check, tiers, strata, cost, gate_rejections, misses, audit, bootstrap,
per_cluster, created`.

**Run passages.** The run's inventory run is its `run.yaml` `locate.inventory_run` (and any
the outcome rows' `runs.inventory` name; a run naming none is refused). Its frozen inventory
documents supply each
passage's span, scope and kind; the outcome rows supply outcome, status and locations; the
locate documents supply each passage's receipt. A passage with no outcome row ended with no
locate result. An article whose inventory document is `unresolved` is *unresolved*; one the
task manifests planned and nothing landed for is *gate-failed*.

**The join** (§8.2, last paragraph): a gold passage and a run passage of one source join when
at least **four whole layer words** lie inside both spans; one to one, greedily by overlap,
ties broken by gold id then item. Never by id.

**Metrics**, per tier, each kept as a numerator and denominator per article cluster
(`per_cluster`) with a 95% percentile interval over `--bootstrap` (2000) cluster resamples,
seeded per metric:

| metric | over | counts |
|---|---|---|
| `fnf_rate` (the safety metric) | gold passages with status `formalized` | those whose run status is not `formalized`, for any reason; `causes` breaks them down by `cflib.vocab.FNF_CAUSES`, first applicable: unresolved and gate-failed articles, then no joined passage (`inventory_miss`), a run scope other than `formal_math` (`scope_demotion`), no locate result (`gate_failed`), an unresolved outcome or no status (`unresolved`), an absence (`absence`), else `weak_relation` (partial or statement-only) |
| `article_fnf_rate` | G1 entries that could not be anchored | articles with no run passage `formalized` at an accept-set declaration |
| `overclaim_rate` | joined run passages derived `formalized` whose gold has a status | gold not `formalized` |
| `scope_error_rate` | joined passages whose gold has a scope | run scope ≠ gold scope |
| `unresolved_rate` | joined `formal_math` run passages | unresolved outcome, or an outcome with no status |
| `inventory_recall` | GX passages; G3 `formal_math` spans weighted by `1/(pi_span·pi_article)` | joined |
| `recall_at_1/3/5` | gold passages with an accept set (G1's, else the counting gold declarations) | a run location of rank ≤ k in it |
| `precision_at_1` | joined passages with an accept set and a rank-1 location | rank 1 in it |
| `library_recall` (G3) | (gold passage, library of a counting gold declaration) pairs | a run location in that library |
| `memory_rate` (P) | eval G1 positives whose accept set the planted index removed | a run location at a removed declaration |

`relation_confusion` counts, per tier, gold relation × run relation over the six relations,
for every run location at a gold declaration. `strata` repeats `fnf_rate` and `recall_at_1` per
article importance and class, gold passage kind and, outside dev, `seen:yes`/`seen:no`.
`misses` lists every miss with its mechanical **error code** (`cflib.vocab.ERROR_CODES`):
inventory miss `E-INVENTORY-MISS`, scope demotion `E-WRONG-SCOPE`, unresolved and gate-failed
`E-UNRESOLVED`, an absence `E-FALSE-ABSENCE` (or `E-REJECTED-CORRECT` when the receipt rejected
an accept declaration), a weak relation `E-WRONG-RELATION` when a run location is in the accept
set, else `E-REJECTED-CORRECT` or `E-RETRIEVAL-MISS`, an overclaim `E-WRONG-RELATION`, and a
planted citation `E-MEMORY`.

`cost`: the runs' receipt dollars per article, per cluster, from `--article-costs` (`{source id:
dollars}` over every session of each article's task) or else the total split evenly (`basis`
says which; the even split has no spread, so its interval is a point). `gate_rejections`: from
the gate log, attempts, rejections, and the rejection rate per reason class (phantom
declaration, outside the universe, quote, passage id, schema, ownership, absence receipt,
curated, coverage, or the rule number). `audit`: about 30 exact/broad run locations outside
every gold passage, seeded, written to `audits/<split>-<id16>.jsonl` with `verdict: null` for labelling;
`--audit-labels` counts the labelled ones (`correct` | `overclaim`).

**Pins and `measurement_id`** (§8.3): `pins` holds `skill_commit` (this checkout, through
`cflib.gitsafe`), `prompts` (each run's `run.yaml` prompt shas), `gate` (each run's `gate.json`
sha), `observed_models` (the receipts' `cost.tokens` models, counted by `messages_by_model`
when present, else by tokens), `agent_cli`, `tend`, `agentbox` (from `run.yaml` `versions`, or
`--pin`; `unrecorded` otherwise), `universe_sha256`, `corpus_db_sha256`,
`sourceset_manifest_sha256` (from the locate run's `run.yaml` `locate` block), `renderer`,
`gold_manifest_sha256`, `receipts` (each `RECEIPT.json`'s sha) and `price_table_sha256`.
`measurement_id` is `cflib.hashing.sha256_json(pins)`.

**Refusals**: a receipt that is not `complete` or has no `cost` block; gold changed after
freeze; a run whose task manifests did not plan every article of the split, or whose inventory
read another rendering than the bench pins; `--arm` other than the run's `run.yaml` `locate.arm`;
the test split without `--checkpoint`; no canary check at all; and any transcript (or host
scan) holding the canary, which fails the run (§8.4).

## 9. The acceptance rule and the loop ledger

`cf bench compare <id> <baseline> <candidate> --claim <tier.metric> [--aa <replicate>]
[--replication] [--alpha 0.05]` refuses two scores of different benches, splits, arms or gold
manifests, and — **a model change is a new baseline, never an improvement** — two whose
`observed_models` sets or `renderer`s differ. Only G1 (blind arm), G3 and P metrics may be the
claim. It pairs the two runs cluster by cluster (the same resampled clusters for both) and
accepts only when every condition holds:

1. `claim`: the claim metric's improvement (lower is better for the rates and cost, higher for
   recalls and precision) has its lower bound above zero at the comparison's alpha, and so does
   the unseen-declaration stratum's (`claim_unseen`) where it exists;
2. `safety`: in every tier both hold, the 95% upper bound of the increase of `fnf_rate` and of
   `overclaim_rate` is at most 0.01;
3. `strata`: no stratum with n ≥ 30 in both drops by more than three points (its 2.5% bound of
   improvement is at least −0.03);
4. `cost`: the 95% upper bound of the cost per article's increase is at most 20% of the
   baseline's;
5. `aa`: the same rule applied to the baseline and its A/A replicate does **not** accept (no
   `--aa` fails the condition);
6. multiplicity: the j-th claimed, non-replication entry of the ledger uses `alpha · 2^-j`
   (alpha-spending); `--replication` (a fresh draw) uses alpha.

Below 150 eval articles and 400 gold positives the decision is **recorded, not claimed**
(`claimed: false`). Each comparison appends one row to `ledger.jsonl` (`cf-bench-ledger/1`):
`{entry_id, bench, at, claim, split, arm, baseline, candidate, gold_manifest_sha256, alpha,
alpha_j, replication, conditions, decision, claimed, claim_basis, differences, failed, note}`;
`differences` names the pins that differ without making the scores incomparable (universe,
sourceset manifest, tool versions) and says when the baseline's measurement is one an earlier
entry already used: an unchanged baseline arm is reused, never re-run (§9, step 6).

## 10. What the bench reads

From other packages, by `references/locate-documents.md`, through `scripts/bench/inputs.py`:

| file | fields |
|---|---|
| `sourcesets/<id>/manifest-<n>.jsonl` (§7) | header `schema`; entries `source_id, pageid, revision, title, wikidata, assessment, origin, adopted_from, layer_sha256, renderer`; which source of a page is read is `Manifest.preferred()` of `scripts/sources/sourceset.py`, called, not copied |
| `sourcesets/<id>/sources/<source>/layer.txt`, `paragraphs.json`, `sections.json`, `finders.json` (§7) | the layer (sha checked), block `span`/`section`/`block`, section `path`/`fragment`/`span`, finder `finder`/`span` |
| `corpora/<u>/universe.yaml` (§8) | `libraries[].id, repo, commit, brain_lib`, `universe_sha256` |
| `corpora/<u>/corpus.sqlite` (§8) | `decls(library, commit_sha, name, module, path, line, proof_status, statement, signature)`, `tags(decl_id, tag, value)`, `libraries(id, commit_sha)`, `decls_fts` (P only) |
| `reports/locate-outcomes.<run>.jsonl` (§10) | `source_id, item, scope, passage_kind, prominence, outcome, status, locations[].{library, declaration, relation, rank}, runs.{inventory, locate, challenge}`, and `span` when the row carries it |
| `corpus/<run>/**/*.json` (§1–§3, §10) | inventory documents' `layer_sha256`, `unresolved`, `passages[].{item, span, scope, passage_kind}`; locate documents' `passages[].receipt.candidates[].{library, declaration, verdict}` |
| `runs/<run>/tend-root/gate/tasks/*.json` (§9) | `documents[].source_id` (what was planned) |
| `runs/<run>/run.yaml`, `runs/<run>/gate.json` (§9) | `prompts`, `versions`, `locate.{arm, inventory_run, universe, sourceset}`; the gate's sha |
| `corpus/<run>/RECEIPT.json` | `complete`, `cost.{usd, tokens, messages_by_model, price_table_sha256}` |
| a harvested `gate-log/` (§9) | one line per call: `task, mode, ok, reasons` |
