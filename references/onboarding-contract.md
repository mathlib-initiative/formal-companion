# Onboarding contract — what a companion must provide

Stage 0 of a campaign. `cf onboard <name> --repo <path> --book <id> --adapter <kind>` writes
`companions/<name>/{adapter.yaml, conventions.md, state.json}` in the **campaign directory** (never
in the companion repo, never in this skill repo), runs the adapter in dry-run, and prints the
tag/untagged/private counts.

Onboarding is complete when `statements.jsonl` validates against the row schema **and**
`conventions.md` exists and has been confirmed by someone who can speak for the repo. An
unconfirmed convention list is the single largest contributor to the ≈40% first-pass false-positive
rate (`references/method.md` §4): every false positive measured was a house convention called a
content bug.

The seven obligations below are the contract. Nothing downstream is well-defined without them.

---

## 1. A statement-table adapter

The adapter turns a repo at a pinned commit into `statements.jsonl` (row schema in
`references/findings-schema-v2.md` §1). Four kinds:

### `docstring_tags`

Declarations whose docstring opens with a backticked book tag — `` `Exercise 1.1.3` ``,
`` `Lemma 1.1.2(i)` ``, `` `In-text proof (book p. 23)` ``. Config:

```yaml
kind: docstring_tags
glob: "TaoMeasure/Section_*.lean"        # files to scan
tag_position: opening                     # opening | anywhere
kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark, Claim, Strategy, In-text]
page_regex: '(?:book\s+)?(?:pp?\.|pages?)\s*(\d+(?:\s*[–-]\s*\d+)?)'
def_body: true                            # capture bodies for def/abbrev/instance/structure
```

Three things the legacy extractors got wrong and the shipped one must not:

- **Prefer the item's own page over a cross-reference.** A docstring reading "previewed on book
  p. 20" gave one exercise the wrong anchor. Take the first page mention that is not inside a
  cross-reference clause, and record `anchor_source: own | crossref | none`.
- **End the item regex at `)` or newline.** A companion whose tag is a docstring-start label with a
  parenthetical leaked two rows of code into the item id.
- **Give anonymous `example`s a synthetic id** `example@<file>:<line>` so they are attributable. An
  exercise realised anonymously was dispatched as `SINGLE-SOURCE` although a second repo had in
  fact formalised it.

One parser, one kind list. Three parsers in one repo disagreed on its declaration count —
1,248 vs 1,414 vs 1,413, with `Strategy` recognised by only one
(design note `stage_map.md`, Stage 0). The shipped parser handles
`private|protected|noncomputable|nonrec` and `@[...]` attribute lines the way that repo's own
`scripts/review_audit.py` does (`DECL` at lines 25-27), and tracks namespaces so `decl` is fully
qualified.

### `attribution_table`

A machine-readable item→declaration map plus snippets, joined by the adapter. Config names the
three inputs and the page offset:

```yaml
kind: attribution_table
refinement: pdf-overlay/data/target-refinement.json      # entries[] with formalization_ids + key_formalization_ids
snippets:   pdf-overlay/data/declaration-snippets.json   # snippets{decl -> {file, line, text}}
items:      pdf-overlay/data/geometry-batch-*.json       # item labels + page regions
key_field:  key_formalization_ids                        # which decls are "key" for the item
```

Two limits to state in `state.json` rather than discover later:

- **Coverage is bounded by curation.** The table covers the items someone curated. Recomputed
  2026-09-16: the adapter's export holds **178 distinct items**
  (`~/Desktop/tao-diff-run/data/adam.jsonl`) while the overlay defines **390 items** and the
  refinement table has **211 entries**. Uncurated items are invisible to the differential, not
  absent from the repo — state the denominator in `state.json` rather than discovering it later.
- **Unnameable `example`s are excluded by construction.** The adapter must therefore also scan the
  files the table maps to each item and emit `example@<file>:<line>` rows; otherwise the
  anonymous-realisation blind spot returns.

### `ledger_nodes` (deferred in v1)

A source-node → formalization ledger with prose locators. The ledger's granularity is a *source
node*, not a book item (2,884 formalization nodes and 77 source nodes in the audited export), so
the adapter must declare `granularity: node` and the campaign must run an item-level curation pass
(curator + independent verifier per node) before extraction. **v1 ships the declaration but not the
curation workflow**; `cf onboard --adapter ledger_nodes` refuses with a pointer to this paragraph,
*before* it writes anything — the kind is in `vocab.ADAPTER_KINDS` (what an `adapter.yaml` may
legally say) but not in `vocab.IMPLEMENTED_ADAPTER_KINDS` (what `adapters.api.registry()` actually
provides, which a test pins). The same holds for `custom`.

Two extra state fields are mandatory for ledger repos, because the ledger is an *export*: it goes
stale the moment a fix names a new theorem, and `extract` keyed on (repo commit, adapter sha) would
silently reproduce the pre-fix table.

```yaml
extract_key: [repo_commit, adapter_sha, ledger_sha, refinement_sha]
stale_since_commit: null   # set when the repo moves ahead of its exported ledger
```

### `custom: <script>`

Any executable emitting the row schema on stdout. The schema gate applies unchanged.

---

## 2. Book edition and page offset

- The **edition sha256** of the PDF the campaign is pinned to, in `campaign.yaml`.
- The **printed↔PDF offset** (for the audited book, printed page = PDF page − 16).
- Whether the repo's anchors are **printed** pages or PDF pages.

Two editions of one book differ by a page: ~65 numbered items sit one printed page earlier in the
published edition than in the 2011 draft, nearly all in one section
(`/Users/jack/Desktop/tao-measure-lean/CLAUDE.md` lines 48-55). A campaign that mixes them produces
a page-anchor finding for every one of them, and an adjudicator who checks against the other file
refutes them all. `book_sha` is stamped into every artifact and the gate refuses a mismatch.

## 3. Errata policy

Two fields: the **live errata URL**, and the disposition rule. The skill fetches the live page with
`curl` and keeps the raw HTML beside the rendered markdown, dated — never a summarising fetch tool,
which is what silently dropped the entry that decided a high-severity finding
(`docs/HANDOFF.md` §4, `REVIEW_PATTERNS.md` P13).

**The four dispositions**, each evidenced in the audited campaign:

| the erratum … | disposition | evidence |
|---|---|---|
| **corrects** the printed statement | formalise the corrected statement; kind `SUSPECTED_ERRATA` until confirmed, then fix | the exercise whose labels the errata swapped, and the one whose sharp constant it corrects (both fixed in PR #8) |
| **observes** a divergence and points elsewhere (e.g. a blog post) | formalise the book as printed; record the argument in the docstring; resolution `argued_correct` | the definition/remark pair deliberately not acted on (`docs/HANDOFF.md` §5): rewriting the clause would formalise the blog post and make the companion *less* faithful |
| **asks for a generalisation** nobody formalised | kind `COVERAGE`, out of scope for a fix | the exercise the errata generalises from elementary to Jordan-measurable sets; all three companions omit it — a *missing* theorem, not a wrong one |
| **deletes** an item or a part | do not formalise it; a declaration that states it is a finding | the errata list records one exercise part as wrong and deleted (`memory/measure-theory-faithfulness-audit.md`) |

An erratum is consulted **before** ruling either side wrong; both the worker and the reviewer
prompts say so.

## 4. Anchor convention

`anchor: docstring | ledger | none`.

- `docstring` — page anchors live in docstrings and are the adapter's `page_anchor`.
- `ledger` — anchors come from the ledger/overlay. **Helper ids may embed `:L<n>:` line numbers**,
  so a docstring edit must keep the line count or the whole pin cascade must be re-run.
- `none` — the companion carries item labels only; `anchor_check` skips it, and a page-anchor
  finding against it is invalid.

## 5. House conventions — `conventions.md`

The list that decides `CONVENTION_ONLY` versus `CONTENT_DELTA`. It is injected verbatim into every
worker, reviewer, attack and adjudication prompt, so it is load-bearing for the false-positive
rate. Drafted by an onboarding agent from the repo's `CLAUDE.md`, module headers and design rules;
**confirmed by the owner**. For a repo with no reachable owner, mark it
`confirmed_by: unconfirmed` — and treat every finding that rests on it as lower confidence.

Required sections:

1. **Encoding conventions** — ambient space, value types (e.g. an extended-nonnegative codomain),
   indexed families vs finite sets, bundled vs unbundled hypotheses, subtype bundling, namespace.
2. **Total functions with junk values** — *the explicit list of names* (P9). This list is what the
   `attack:vac` prompt probes: which functions in this repo return a value where the object is
   undefined.
3. **Standing conventions** — *the explicit list* (P12): degenerate dimension, a standing `1 ≤ d`,
   which cases the repo's own docstrings exclude, where the companion stops. This list is what the
   `attack:hunt` prompt checks statements against; it found the cheapest high-severity defect in
   the audited campaign with no reference to the book at all.
4. **Scope** — which chapters/sections the companion covers. "Stops at §1.4" is a scope fact, not a
   defect, and every prompt must say so or the run reports the absence.
5. **Deliberate deviations** — documented narrowings, with their justification. Flag each one for
   the P11 check: *a documented restriction is not a justified one*.

## 6. Sorry policy

```yaml
sorry:
  exercise_sorry: permanent | none
  gap_marker: "sorry -- TODO(autoform)"
  classifier: scripts/update_status_table.py   # optional: the repo's own authoritative split
```

`sorry` rows are **classified**, never reported: `sorry_class ∈ exercise | gap | none`. Where the
repo owns a classifier, the adapter calls it — the authoritative split for the audited repo is
510 exercise + 6 in-text = 516, while `grep -c sorry` gives 523 because 7 hits are prose. A
companion with `exercise_sorry: permanent` must never have an exercise solved by the fix stage;
that is a merge invariant, checked before and after.

## 7. Write policy and owner

```yaml
write_policy: read_only | branch_only | branch_and_pr
owner: session:<id> | github:<login> | external | none
validators: ["python3 scripts/update_status_table.py --check"]
post_change_cascade: "<repin command>"     # ledger/viewer repos only
consent: { heavy_runs: required, recorded_in: run.yaml }
```

- **Peers default to `read_only`.** In the audited campaign the third companion was read-only to
  the auditing session throughout, and every merge was one command on the owner's go.
- `branch_and_pr` unlocks the (post-v1) fix stage; the invariants gate runs first and opening or
  merging still waits for a human.
- Repos whose data files are pinned by sha need their **cascade command** recorded here, because a
  Lean change invalidates the ledger, the snippets, the overlay manifest, the validator constants
  and the viewer's binding at once.

---

## 8. The dry run, and what `state.json` records

```
cf onboard ours --repo <path> --book <book-id> --adapter docstring_tags
```

prints, and writes into `companions/<name>/state.json`:

- rows, distinct items, tagged vs untagged declarations, private/excluded declarations;
- `sorry` counts by class;
- items whose `page_anchor` came from a cross-reference (`anchor_source: crossref`);
- rejected item strings (never dropped silently);
- `repo`, `commit`, `adapter_sha`, `book_sha`, and for ledger repos `ledger_sha`.

A **full declaration index** is built at the same time and stored beside the table
(`declindex.jsonl`: every declaration in the glob, tagged or not). The schema gate validates
`repo:decl` strings against *that* index, not against `statements.jsonl` — workers legitimately
name helpers, private lemmas and anonymous examples that are not book items, and validating against
the item table alone would reject real findings.

---

## 9. The two concrete examples

### A. `tao-measure-lean` — `docstring_tags`

```yaml
name: ours
kind: docstring_tags
repo: <campaign-local path to the clone>
glob: "TaoMeasure/Section_*.lean"
anchor: docstring
kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark, Claim, Strategy, In-text]
sorry: { exercise_sorry: permanent, classifier: "scripts/update_status_table.py" }
write_policy: branch_and_pr
validators: ["python3 scripts/update_status_table.py --check"]
```

Measured shape at the audited commit (recomputed from `~/Desktop/tao-diff-run/data/ours.jsonl`):
**1,157 rows over 763 distinct items**, including in-text items and a second chapter. Its
`conventions.md` carries the junk-value list (its measures are total functions on sets, never on
proof terms), indexed families rather than finite sets of sets, the ambient Euclidean space,
permanent exercise stubs, and the docstring-tag rule itself — the design rules in its `CLAUDE.md`
lines 18-40. Its review catalog (`REVIEW_PATTERNS.md`) is mandatory reading before review, which is
why `cf lint` (specified in review-patterns.md, not implemented in v1) ships the same detectors.

### B. `measure_theory` — `attribution_table`

```yaml
name: adam
kind: attribution_table
repo: <campaign-local path to the clone>
refinement: pdf-overlay/data/target-refinement.json
snippets:   pdf-overlay/data/declaration-snippets.json
items:      pdf-overlay/data/geometry-batch-*.json
anchor: ledger          # helper ids embed :L<n>: — keep docstring line counts
extract_key: [repo_commit, adapter_sha, ledger_sha, refinement_sha]
sorry: { exercise_sorry: none }         # exercises are proved or realised as anonymous examples
write_policy: read_only                  # peer repo; raise only with the owner's consent
post_change_cascade: "<repin command> <root> <commit>"
viewer: measure-theory/faithfulness-verdicts@1
```

Measured shape: **562 rows over 178 distinct items** (recomputed from
`~/Desktop/tao-diff-run/data/adam.jsonl`); definition bodies are carried in `statement` on purpose,
which is how a proxy definition was spotted. Its `conventions.md` records subtype bundling, the
namespace, zero `sorry` by design, and that unnameable `example`s cannot be attributed in its
viewer. A third companion of the same book carries no page anchors at all and stops partway through
the chapter — `anchor: none`, plus a scope line in `conventions.md`; nothing in the design
precludes onboarding it.
