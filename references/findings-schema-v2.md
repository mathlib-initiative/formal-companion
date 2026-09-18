# Schemas — statements, tasks, findings v2, receipts, ledger, routed files

Every example below is **synthetic**: a made-up book ("Widget Theory", source id
`widget-theory-2e`), made-up companions `alpha` / `beta`, made-up declarations. No source text,
page image or crop is ever copied into this repository; quotes live only in a workspace directory.

**An item is the pair `(source_id, item)`.** Every row, task, finding, receipt, ledger row and
routed row carries both halves, because `Exercise 1.3.19` of one source is not the same item as
`Exercise 1.3.19` of another, and a workspace holds M sources. Where a single string is needed
the spelling is `source_id::item` (`cflib.items.qualified_item`); where a filename is needed it is
`qualified_slug` → `widget-theory-2e--lemma-2-3-4`.

The machine-readable copy of §3 is `scripts/gate/schema.json`; the enums are
`scripts/cflib/vocab.py` and are documented in `references/vocabulary.md`.

---

## 1. `companions/<name>/statements.jsonl` — one declaration per line

Line 1 is a `_meta` row naming the companion, its repo, its `sources` and its `anchor_source`;
every later line is one declaration.

```json
{"item_raw":"Lemma 2.3.4(ii)","item":"Lemma 2.3.4","sub":"(ii)","item_kind":"Lemma",
 "decl":"Widget.Basic.gadget_le_of_bounded","kind":"theorem","namespace":"Widget.Basic",
 "file":"Widget/Section_2_3.lean","line":412,
 "statement":"theorem gadget_le_of_bounded {d : ℕ} (hd : 1 ≤ d) (E : Set (Space d)) (hE : IsBounded E) : gadget E ≤ 1",
 "body":null,"sorry":false,"sorry_class":"none",
 "page_anchor":57,"page_anchor_raw":"57","anchor_source":"own",
 "repo":"alpha","key":true,"anonymous":false,"private":false,"granularity":"item",
 "source_id":"widget-theory-2e","source_sha":"<64 hex>"}
```

| field | type | rule |
|---|---|---|
| `item_raw` | string | exactly as the repo wrote it (tag text, ledger label) |
| `item` | string | normalised id, strict grammar, sub-part removed; the kind list is **this source's** (`references/vocabulary.md` §4) |
| `sub` | string\|null | `"(ii)"`, `"(3)"` or null |
| `decl` | string | **fully qualified**; namespace tracking is required, bare names are a v1 artifact |
| `kind` | string | `theorem`/`lemma`/`def`/`abbrev`/`instance`/`structure`/`class`/`inductive`/`example` |
| `file`,`line` | string,int | repo-relative, 1-based |
| `statement` | string | signature, comment-stripped, truncated at `:=` or `by` |
| `body` | string\|null | non-null for `def`/`abbrev`/`instance`/`structure` only — proxy definitions are spotted from bodies |
| `sorry` | bool | over comment-stripped source |
| `sorry_class` | enum | `exercise` \| `gap` \| `none` (from the repo's classifier where it has one) |
| `page_anchor` | int\|null | **printed** page, converted with the source's offset |
| `anchor_source` | enum | `own` \| `crossref` \| `none` — a cross-reference page is not an anchor |
| `repo` | string | workspace-local companion name |
| `key` | bool | the adapter marked it a *key* declaration for the item |
| `anonymous` | bool | true for `example@<file>:<line>` synthetic ids |
| `source_id` | string | which source this row is attributed to (`adapter.yaml` `sources.default`/`map`) |
| `source_sha` | string | sha256 of that source's PDF: the edition the row was built against |

**Validation** (`adapters/base.py`): every field of `ROW_REQUIRED_FIELDS` present and non-empty
(`source_id` and `source_sha` among them); unique `decl` per repo; item matches the grammar for
that source's kinds; `statement` non-empty; `page_anchor` int or null; rejected item strings are
reported, never dropped. Rows sort by `(source_id, …)`. Counts go to `state.json.extract` and to
`state.json.sources[<source_id>] = {sha, rows, items, at}`.

A row whose `source_id` is not in the companion's `sources` list, or whose `source_sha` is not
that source's sha in `workspace.yaml`, is refused — this is the source gate at the extraction
boundary.

A second file, `declindex.jsonl`, lists **every** declaration in the glob whether or not it is
tagged. The schema gate validates `repo:decl` against that index (see §7).

---

## 2. `runs/<run-id>/tasks/<task-id>.yaml` — one item per task

tend's task schema; `tend-task verify tasks` must pass before a run starts. A run is scoped to one
source, so `run.yaml` records `sources: [<id>]` and every task names its own.

```yaml
schema_version: 1
id: lemma-2-3-4                      # slug of the item; dashes, lowercase
source_id: widget-theory-2e          # which source this item belongs to
title: "ATTACK: Lemma 2.3.4"         # DIFFERENTIAL: | SINGLE-SOURCE: | ATTACK: — the evidence class survives into the corpus
status: pending
priority: max                        # attack runs: max; differential: default
depends_on: []
summary: "vac probe of Lemma 2.3.4"
description: |
  <rendered from templates/tasks/<mode>.yaml.tmpl with the item's rows, the peer repos,
  the source (title, edition, sha8, printed page, errata path), and the companion's
  conventions summary>

  Write exactly one file `findings/lemma-2-3-4.json` and change nothing else except this
  task file. Set status to complete when the findings file passes the gate.
```

Rules:

- `id` is the **slug of the item id**, so a findings file and its task are joined by name. Both
  dash and underscore slugs exist in the baseline; the harvester accepts either. Where one run
  spans more than one source the slug is `qualified_slug` (`<source-slug>--<item-slug>`), so two
  sources with a colliding item id cannot collide as filenames.
- The three title prefixes are load-bearing: the baseline's 382 tasks split 224 `DIFFERENTIAL:`
  and 158 `SINGLE-SOURCE:` (recomputed from `~/Desktop/tao-diff-run/tasks`), and that split is how
  the report distinguishes strong from weak evidence.
- `make_tasks` **reconciles** its output against `items.json ∩ any-repo` and fails on a gap.

---

## 3. Findings v2 — `runs/<run-id>/findings/<task-id>.json`, harvested to `corpus/<run-id>/`

```json
{
  "schema": "companion-faithfulness/findings",
  "schema_version": 2,
  "run_id": "attack-vac-2026-09-20",
  "item": "Lemma 2.3.4",
  "source_id": "widget-theory-2e",
  "source_sha": "<64 hex>",
  "book_located": true,
  "book_passage_quote": "<verbatim sentence(s) from that source's text; machine-verified>",
  "matches": [
    {"concept": "boundedness implies the gadget bound",
     "decls": [{"repo": "alpha", "decl": "Widget.Basic.gadget_le_of_bounded"}],
     "verdict": "SINGLE_SOURCE_DELTA",
     "note": "the Lean hypothesis is strictly stronger than the book's"}
  ],
  "disagreements": [
    {
      "finding_id": "<sha256, assigned at harvest>",
      "concept": "boundedness hypothesis is stronger than the book's",
      "kind": "CONTENT",
      "repos_involved": ["alpha"],
      "faithful": [],
      "unfaithful_decls": ["alpha:Widget.Basic.gadget_le_of_bounded"],
      "book_quote": "<verbatim sentence that decides it>",
      "witness": {"d": 1, "object": "E = ℕ ⊆ Space 1",
                  "why": "E is unbounded but the book's hypothesis holds for it, so the Lean statement says nothing about the case the book covers"},
      "reasoning": "…, and the witness above is the instance where the two come apart.",
      "severity": "medium",
      "suggested_fix": "weaken `IsBounded E` to the book's `IsLocallyFinite E`",
      "bridge": null,
      "withdrawn": false,
      "mechanical": false
    }
  ],
  "coverage_notes": "declarations with no counterpart; sub-parts present in one repo only"
}
```

Differences from v1, and why each exists:

| v1 | v2 | reason |
|---|---|---|
| `faithful` enum (`ours`\|`teorth`\|`adam`\|`none`\|`all_differ`\|`unsure`) | `faithful: [repo…]` list, plus optional `all_differ`/`unsure` booleans | the enum cannot name two faithful repos; agents wrote the second one into `reasoning` |
| witness in prose | `witness: {d?, object, why}` | the witness gate cannot be mechanical if the witness is a sentence |
| no id; challenge linked by `item + concept[:22]` | `finding_id` (`references/vocabulary.md` §3) | prefix joins were ambiguous |
| no edition stamp | `source_id` + `source_sha` required | batch-1 anchors were judged against a different edition, and a bare item id is ambiguous once a workspace holds two sources |
| kinds fixed per script | per-mode `gate.json`, plus `PROXY` | proxy findings had no way into the corpus |
| — | `withdrawn`, `mechanical` | mechanical page anchors and edition-refuted rows stay in the corpus, visibly retired |

An item with nothing to report is still a file: `matches` carrying `NO_DEFECT_FOUND` /
`SINGLE_SOURCE_OK` and an empty `disagreements`. That is how a run's denominator is known.

---

## 4. `corpus/<run-id>/RECEIPT.json` — the completeness proof

```json
{"run_id":"attack-vac-2026-09-20",
 "source":{"host":"<host alias from workspace.yaml>","path":"<remote findings dir>","listed_at":"2026-09-20T18:03:11Z"},
 "expected_count":763,"actual_count":763,
 "manifest":{"lemma-2-3-4.json":"<sha256>","…":"…"},
 "missing":[],"extra":[],
 "run_yaml_sha":"<sha256 of run.yaml>",
 "sources":{"widget-theory-2e":"<64 hex>"},
 "by_source":{"widget-theory-2e":763},
 "ok":true}
```

Two keys, one syllable apart, and they are not related: singular **`source`** is where the files
were rsynced *from* (a host alias and a path), and it predates the multi-source model; plural
**`sources`** is the `{source_id: sha256}` map every file in the run must agree with. A finding
whose `source_sha` differs from the sha recorded here for its `source_id` fails the receipt. The
ONE documented override is `--allow-edition-mismatch`, and the receipt records that it was used.

`harvest` fails when `expected_count != actual_count` or a sha differs. This gate exists because a
harvest once held 762 files where the server had 763, and the missing file was a real `CONTENT`
finding — every derived count was off by one, toward "cleaner"
(`/Users/jack/Desktop/tao-measure-lean/docs/HANDOFF.md` §4).

---

## 5. Ledger rows — `ledger/{accepted,refuted}.<name>.jsonl`

Line 1 is a `_meta` row; every later line is one adjudicated finding.

```json
{"_meta":{"repo":"alpha","written":"2026-09-20","run_ids":["attack-vac-2026-09-20"],
          "sources":{"widget-theory-2e":"<64 hex>"},
          "method":"3 lenses → reconciler → 2 opposing skeptics",
          "status":"CHALLENGED"}}
{"finding_id":"<sha256>","item":"Lemma 2.3.4",
 "source_id":"widget-theory-2e","source_sha":"<64 hex>",
 "decls":["Widget.Basic.gadget_le_of_bounded"],
 "finding_summary":"the boundedness hypothesis excludes the case the book covers",
 "verdict":"CONFIRMED_CONTENT","severity":"medium","confidence":0.82,
 "method":"lens×3 + reconciler + 2 skeptics","contested":false,
 "dissent":[{"role":"skeptic-convention","confidence":0.6,"argument":"…"}],
 "resolution":"in_progress:fix/lemma-2-3-4",
 "reason":"self-contained justification citing the book page and file:line"}
```

Rules enforced by `ledger/validate_ledger.py`:

- every row answers a finding that exists in the corpus, joined on
  **`(source_id, finding_id)`**;
- `verdict`, `severity`, `resolution` are in the vocabulary — the legacy free-text resolution
  (`"refuted; binder dropped in PR#10"`) is split into `resolution` + `reason`;
- a `CONVENTION` verdict **must** carry a written recovery argument;
- sub-threshold skeptic dissent is recorded even when `contested` is false;
- a row without a `source_id`, or with a `source_sha` the workspace does not know for that source,
  is an **error** — the source gate at the ledger boundary.

---

## 6. Routed files — `ledger/routed/findings.<name>.jsonl`

```json
{"_meta":{"status":"CHALLENGED","routing":"a finding is filed under a companion only if that companion appears in unfaithful_decls",
          "coverage":"763 of 763 items adjudicated","sources":{"widget-theory-2e":"<64 hex>"},
          "withdrawn":"PAGE_ANCHOR rows retired by anchor_check against the anchor edition",
          "run_ids":["attack-vac-2026-09-20","attack-hunt-2026-09-20"]}}
{"finding_id":"…","item":"Lemma 2.3.4","source_id":"widget-theory-2e","source_sha":"<64 hex>",
 "kind":"CONTENT","severity":"medium",
 "unfaithful_decls":["alpha:Widget.Basic.gadget_le_of_bounded"],
 "book_quote":"…","witness":{…},"reasoning":"…","suggested_fix":"…",
 "ledger":{"verdict":"CONFIRMED_CONTENT","confidence":0.82,"contested":false}}
```

- **Route only on the `unfaithful_decls` prefix.** The legacy router had a fallback branch that
  also filed rows whose `faithful` named a different repo; it inflated one companion's routed set
  from 10 to 31 rows. The fallback is gone.
- **Nothing leaves the workspace directory while `_meta.status` is `UNCHALLENGED`.**
- Page-anchor rows are routed as mechanical low-severity rows produced by `anchor_check` against
  the companion's **anchor** edition, not withdrawn wholesale: in the audited campaign 32 anchors
  were genuinely wrong and were corrected. Only rows *contradicted* by that edition are withdrawn.
  The owner message names the edition, because "your anchor is one page out" and "you are anchored
  to the other printing" are different messages.

---

## 7. The gate matrix

Mechanical unless marked. The gate runs **out of tree** — from the workspace/skill copy, never from
the worktree the worker can edit — and the pre-merge pass additionally checks
`changed files ⊆ {findings/<id>.json, tasks/<id>.yaml}`. The legacy setup ran
`python3 scripts/verify_findings.py` from inside the entrypoint the worker had write access to, so
"passed the gate twice" proved nothing about a worker that edited the gate.

| check | applies to | rule |
|---|---|---|
| schema | every file | required keys; enums from `vocab.py`; `repo:decl` validated against `declindex.jsonl`, not the item table |
| source | every file | `source_id` names a source in `gate.json` `sources`, and `source_sha` == that source's sha, else refuse |
| quote (words) | `book_passage_quote`, and `book_quote` on `CONTENT`/`VACUITY`/`SUSPECTED_ERRATA` | ≥25 normalised characters; verbatim, or a ≥60%-word window present in **that source's** `text.txt` — the gate picks the text by the document's `source_id`. Normalisation: NFKC, ligatures `ﬁ`/`ﬂ`, curly quotes, all dashes → `-`, non-alphanumerics → space, casefold. **See §7.1 for what this does NOT catch.** |
| quote (operators) | the same quotes, when `check_operators` is on (every preset except the two `*-legacy` ones) | the quote re-normalised with `≤ ≥ < > = ≠ + - ∈ ⊆ ∪ ∩ → ¬` mapped to word tokens must match a ≥50%-token window too, so an inequality or operator flip is refused |
| witness | `CONTENT`/`VACUITY` in attack mode | `witness` present and non-empty. *Whether the reasoning actually uses it is a reviewer (LLM) check, not this gate* |
| kinds | per mode, from `gate.json` | differential/single-source: `{CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}`; attack: `{CONTENT, VACUITY, SUSPECTED_ERRATA}`; `PAGE_ANCHOR` only from `anchor_check`; `PROXY` only from `detect-proxies` |
| proxy evidence | `PROXY` | `bridge ∈ {proved, fiat, none}` and a closure path or bridge-theorem name; **no** book quote or witness demanded |
| coverage evidence | `COVERAGE` | item id + its page from `items.json`; no witness demanded |
| anchor evidence | `PAGE_ANCHOR` | produced mechanically: located page + `source_id` + `source_sha` |
| `book_located: false` | any | not a failure — emitted as a `COVERAGE` finding with the search performed. The v1 gate failed the task while the prompt told workers to set the flag |
| completeness | harvest | §4 |
| count-from-corpus | report | every published number re-derived by `tally.py`, grouped by `(source_id, repo)` |

`runs/<run-id>/gate.json` therefore carries a **`sources` block**, not one book path:

```json
{"sources": {"widget-theory-2e": {"text": "sources/widget-theory-2e/text.txt",
                                  "sha256": "<64 hex>", "text_sha256": "<64 hex>",
                                  "page_offset": 16,
                                  "images_dir": "sources/widget-theory-2e/images"}},
 "KINDS": ["CONTENT", "VACUITY", "SUSPECTED_ERRATA"], "VERDICTS": ["…"],
 "require_witness": true, "check_operators": true}
```

`verify_findings.py` is rsynced to a host that has no `cflib` on its path, so it re-implements the
lookup and the `finding_id` byte-joining itself. Both must stay byte-identical to `cflib`; the
enforcement is `tests/test_plan_gate.py::TestFindingIdIsOneDefinition`.


### 7.1 What the quote gate does and does not prove

The word pass is **lossy by design**: `norm()` replaces every non-alphanumeric run with a space, so
`a ≤ b` and `a ≥ b` normalise identically. That tolerance exists because a `pdftotext` extraction
spells the same displayed formula a dozen ways, and without it real quotes fail. Two consequences
follow, and neither is a bug in the implementation — they are the price of the tolerance:

* **Operators.** Under the word pass alone, flipping `=` → `≠` or `+` → `-` is invisible. This is
  the defect class the audit exists to find, so a second pass, `norm_operators()`, keeps those
  operators as word tokens and requires them to match in position. It runs at a 50% window (looser
  than the word pass, because display mathematics breaks across lines): measured over the 3,019
  quotes of the baseline corpus, 2 fail at 50% and 11 at 60%. The two `*-legacy` presets — which
  exist only to validate the imported 2011-draft corpus — set `check_operators: false`, and the
  gate prints `OPERATORS NOT CHECKED (legacy preset)` when they do.
* **The unmatched remainder.** A ≥60% *window* match leaves up to 40% of the quote unverified, so a
  quote that is 70% genuine and 30% invented passes. Rejecting those would reject 25 of the 3,019
  real baseline quotes, so instead the gate **names the remainder**: `quote_ok()` returns
  `partial (…) UNVERIFIED: '<the words nobody checked>'` and `verify_findings.py` prints one `~`
  line per partially-matched quote before its `OK:` summary. It does not claim "all book quotes
  verified"; it says how many matched only in part. A reviewer reads those lines.

What the gate does prove: the quote is ≥25 characters, a long contiguous run of it is really in the
named source, and (outside legacy) its operators are the book's. What it cannot prove is that a
single word in the middle was not swapped — `Finite` → `Countable` survives both passes. That is a
**reviewer** check, which is why adjudication reads the quote against the book rather than trusting
this exit code.

---

## 8. `convert_v1.py` — importing the 2,672-file baseline

Reads `~/Desktop/tao-diff-run/{findings,findings-pdf,findings-gpt,findings-vac,findings-hunt}/`
(382 + 382 + 382 + 763 + 763 files, counted 2026-09-16) and the batch-1 artifacts under
`~/Desktop/tao-differential/`, and writes v2 into `corpus/<run-id>/`. **It never modifies the
baseline.**

1. one run-id per input directory; `run.yaml` records model, source access, gate mode and that the
   run is imported;
2. `faithful` enum → list; `NO_WEAKNESS_FOUND` → `NO_DEFECT_FOUND`;
3. `witness` extracted from `reasoning` where the text names one, else null with
   `witness_extracted: false` — never fabricated;
4. `source_id` and `source_sha` stamped on the document and on every disagreement: the published
   edition for the five tend runs, the **2011 draft** for the batch-1 rows, whose `PAGE_ANCHOR`
   rows are imported `withdrawn: true`. `--source <id>` is required when the workspace holds more
   than one; passing a `--source-sha` that is not that source's is refused unless
   `--allow-edition-mismatch` is given too, and the receipt records both, so no report can
   silently mix editions;
5. `finding_id` computed per `references/vocabulary.md` §3;
6. bare `repo:decl` names are kept and, where `statements.jsonl` supplies a unique namespace,
   `decl_qualified` is added. The `repo:` half is rewritten only when `--repo-map ours=<companion>`
   says the two names are one repository — v1 predates the workspace, and the union key
   `(source_id, repo, item, kind)` and the gate's known-repo check both read that half, so an
   unmapped import neither joins nor gates. The row keeps its v1 spelling in
   `unfaithful_decls_v1`, the document records the `repo_map` it was converted under, and an
   import without the flag warns instead of renaming anything;
7. dash and underscore slugs both accepted; the file name is not trusted for the item — `item`
   inside the JSON is;
8. batch-1 ledger rows (`accepted.adam.jsonl`, `refuted.adam.jsonl`) import into `ledger/` with
   their verdicts unchanged and free-text resolutions split (§5).

A conversion run prints a reconciliation table (files in, findings out, rows by kind, rows without
a witness) and the regression test asserts the totals against the baseline tally: **72 rows over
40 distinct items** naming the audited companion, partitioned 16 `CONTENT`/`VACUITY`,
1 `SUSPECTED_ERRATA`, 1 `COVERAGE`, 22 `PAGE_ANCHOR`
(`/Users/jack/Desktop/tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md`).
