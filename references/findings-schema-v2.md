# Schemas — statements, tasks, findings v2, receipts, ledger, routed files

Every example below is **synthetic**: a made-up book ("Widget Theory"), made-up companions
`alpha` / `beta`, made-up declarations. No book text, page image or crop is ever copied into this
repository; quotes live only in a campaign directory.

The machine-readable copy of §3 is `scripts/gate/schema.json`; the enums are
`scripts/cflib/vocab.py` and are documented in `references/vocabulary.md`.

---

## 1. `companions/<name>/statements.jsonl` — one declaration per line

```json
{"item_raw":"Lemma 2.3.4(ii)","item":"Lemma 2.3.4","sub":"(ii)",
 "decl":"Widget.Basic.gadget_le_of_bounded","kind":"theorem",
 "file":"Widget/Section_2_3.lean","line":412,
 "statement":"theorem gadget_le_of_bounded {d : ℕ} (hd : 1 ≤ d) (E : Set (Space d)) (hE : IsBounded E) : gadget E ≤ 1",
 "body":null,"sorry":false,"sorry_class":"none",
 "page_anchor":57,"anchor_source":"own",
 "repo":"alpha","key":true,"anonymous":false,
 "book_sha":"<64 hex>"}
```

| field | type | rule |
|---|---|---|
| `item_raw` | string | exactly as the repo wrote it (tag text, ledger label) |
| `item` | string | normalised id, strict grammar, sub-part removed (`references/vocabulary.md` §4) |
| `sub` | string\|null | `"(ii)"`, `"(3)"` or null |
| `decl` | string | **fully qualified**; namespace tracking is required, bare names are a v1 artifact |
| `kind` | string | `theorem`/`lemma`/`def`/`abbrev`/`instance`/`structure`/`class`/`inductive`/`example` |
| `file`,`line` | string,int | repo-relative, 1-based |
| `statement` | string | signature, comment-stripped, truncated at `:=` or `by` |
| `body` | string\|null | non-null for `def`/`abbrev`/`instance`/`structure` only — proxy definitions are spotted from bodies |
| `sorry` | bool | over comment-stripped source |
| `sorry_class` | enum | `exercise` \| `gap` \| `none` (from the repo's classifier where it has one) |
| `page_anchor` | int\|null | **printed** page |
| `anchor_source` | enum | `own` \| `crossref` \| `none` — a cross-reference page is not an anchor |
| `repo` | string | campaign-local companion name |
| `key` | bool | the adapter marked it a *key* declaration for the item |
| `anonymous` | bool | true for `example@<file>:<line>` synthetic ids |
| `book_sha` | string | the campaign's pinned edition |

**Validation** (`adapters/base.py`): unique `decl` per repo; item matches the grammar; `statement`
non-empty; `page_anchor` int or null; rejected item strings are reported, never dropped. Counts go
to `state.json.extract`.

A second file, `declindex.jsonl`, lists **every** declaration in the glob whether or not it is
tagged. The schema gate validates `repo:decl` against that index (see §7).

---

## 2. `runs/<run-id>/tasks/<task-id>.yaml` — one book item per task

tend's task schema; `tend-task verify tasks` must pass before a run starts.

```yaml
schema_version: 1
id: lemma-2-3-4                      # slug of the item; dashes, lowercase
title: "ATTACK: Lemma 2.3.4"         # DIFFERENTIAL: | SINGLE-SOURCE: | ATTACK: — the evidence class survives into the corpus
status: pending
priority: max                        # attack runs: max; differential: default
depends_on: []
summary: "vac probe of Lemma 2.3.4"
description: |
  <rendered from templates/tasks/<mode>.yaml.j2 with the item's rows, the peer repos,
  the printed page, and the companion's conventions summary>

  Write exactly one file `findings/lemma-2-3-4.json` and change nothing else except this
  task file. Set status to complete when the findings file passes the gate.
```

Rules:

- `id` is the **slug of the item id**, so a findings file and its task are joined by name. Both
  dash and underscore slugs exist in the baseline; the harvester accepts either.
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
  "book_sha": "<64 hex>",
  "book_located": true,
  "book_passage_quote": "<verbatim sentence(s) from the pinned edition; machine-verified>",
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
| no edition stamp | `book_sha` required | batch-1 anchors were judged against a different edition |
| kinds fixed per script | per-mode `gate.json`, plus `PROXY` | proxy findings had no way into the corpus |
| — | `withdrawn`, `mechanical` | mechanical page anchors and edition-refuted rows stay in the corpus, visibly retired |

An item with nothing to report is still a file: `matches` carrying `NO_DEFECT_FOUND` /
`SINGLE_SOURCE_OK` and an empty `disagreements`. That is how a run's denominator is known.

---

## 4. `corpus/<run-id>/RECEIPT.json` — the completeness proof

```json
{"run_id":"attack-vac-2026-09-20",
 "source":{"host":"<host alias from campaign.yaml>","path":"<remote findings dir>","listed_at":"2026-09-20T18:03:11Z"},
 "expected_count":763,"actual_count":763,
 "manifest":{"lemma-2-3-4.json":"<sha256>","…":"…"},
 "missing":[],"extra":[],
 "run_yaml_sha":"<sha256 of run.yaml>",
 "book_sha":"<64 hex>",
 "ok":true}
```

`harvest` fails when `expected_count != actual_count` or a sha differs. This gate exists because a
harvest once held 762 files where the server had 763, and the missing file was a real `CONTENT`
finding — every derived count was off by one, toward "cleaner"
(`/Users/jack/Desktop/tao-measure-lean/docs/HANDOFF.md` §4).

---

## 5. Ledger rows — `ledger/{accepted,refuted}.<name>.jsonl`

Line 1 is a `_meta` row; every later line is one adjudicated finding.

```json
{"_meta":{"repo":"alpha","written":"2026-09-20","run_ids":["attack-vac-2026-09-20"],
          "book_sha":"<64 hex>","method":"3 lenses → reconciler → 2 opposing skeptics",
          "status":"CHALLENGED"}}
{"finding_id":"<sha256>","item":"Lemma 2.3.4",
 "decls":["Widget.Basic.gadget_le_of_bounded"],
 "finding_summary":"the boundedness hypothesis excludes the case the book covers",
 "verdict":"CONFIRMED_CONTENT","severity":"medium","confidence":0.82,
 "method":"lens×3 + reconciler + 2 skeptics","contested":false,
 "dissent":[{"role":"skeptic-convention","confidence":0.6,"argument":"…"}],
 "resolution":"in_progress:fix/lemma-2-3-4",
 "reason":"self-contained justification citing the book page and file:line",
 "book_sha":"<64 hex>"}
```

Rules enforced by `ledger/validate_ledger.py`:

- every row answers a finding that exists in the corpus, by `finding_id`;
- `verdict`, `severity`, `resolution` are in the vocabulary — the legacy free-text resolution
  (`"refuted; binder dropped in PR#10"`) is split into `resolution` + `reason`;
- a `CONVENTION` verdict **must** carry a written recovery argument;
- sub-threshold skeptic dissent is recorded even when `contested` is false;
- `book_sha` matches the campaign's.

---

## 6. Routed files — `ledger/routed/findings.<name>.jsonl`

```json
{"_meta":{"status":"CHALLENGED","routing":"a finding is filed under a companion only if that companion appears in unfaithful_decls",
          "coverage":"763 of 763 items adjudicated","book_sha":"<64 hex>",
          "withdrawn":"PAGE_ANCHOR rows retired by anchor_check against the pinned edition",
          "run_ids":["attack-vac-2026-09-20","attack-hunt-2026-09-20"]}}
{"finding_id":"…","item":"Lemma 2.3.4","kind":"CONTENT","severity":"medium",
 "unfaithful_decls":["alpha:Widget.Basic.gadget_le_of_bounded"],
 "book_quote":"…","witness":{…},"reasoning":"…","suggested_fix":"…",
 "ledger":{"verdict":"CONFIRMED_CONTENT","confidence":0.82,"contested":false}}
```

- **Route only on the `unfaithful_decls` prefix.** The legacy router had a fallback branch that
  also filed rows whose `faithful` named a different repo; it inflated one companion's routed set
  from 10 to 31 rows. The fallback is gone.
- **Nothing leaves the campaign directory while `_meta.status` is `UNCHALLENGED`.**
- Page-anchor rows are routed as mechanical low-severity rows produced by `anchor_check` against
  the pinned edition, not withdrawn wholesale: in the audited campaign 32 anchors were genuinely
  wrong and were corrected. Only rows *contradicted* by the pinned edition are withdrawn.

---

## 7. The gate matrix

Mechanical unless marked. The gate runs **out of tree** — from the campaign/skill copy, never from
the worktree the worker can edit — and the pre-merge pass additionally checks
`changed files ⊆ {findings/<id>.json, tasks/<id>.yaml}`. The legacy setup ran
`python3 scripts/verify_findings.py` from inside the entrypoint the worker had write access to, so
"passed the gate twice" proved nothing about a worker that edited the gate.

| check | applies to | rule |
|---|---|---|
| schema | every file | required keys; enums from `vocab.py`; `repo:decl` validated against `declindex.jsonl`, not the item table |
| edition | every file | `book_sha` == campaign's, else refuse |
| quote (words) | `book_passage_quote`, and `book_quote` on `CONTENT`/`VACUITY`/`SUSPECTED_ERRATA` | ≥25 normalised characters; verbatim, or a ≥60%-word window present in `book.txt`. Normalisation: NFKC, ligatures `ﬁ`/`ﬂ`, curly quotes, all dashes → `-`, non-alphanumerics → space, casefold. **See §7.1 for what this does NOT catch.** |
| quote (operators) | the same quotes, when `check_operators` is on (every preset except the two `*-legacy` ones) | the quote re-normalised with `≤ ≥ < > = ≠ + - ∈ ⊆ ∪ ∩ → ¬` mapped to word tokens must match a ≥50%-token window too, so an inequality or operator flip is refused |
| witness | `CONTENT`/`VACUITY` in attack mode | `witness` present and non-empty. *Whether the reasoning actually uses it is a reviewer (LLM) check, not this gate* |
| kinds | per mode, from `gate.json` | differential/single-source: `{CONTENT, VACUITY, SUSPECTED_ERRATA, COVERAGE}`; attack: `{CONTENT, VACUITY, SUSPECTED_ERRATA}`; `PAGE_ANCHOR` only from `anchor_check`; `PROXY` only from `detect-proxies` |
| proxy evidence | `PROXY` | `bridge ∈ {proved, fiat, none}` and a closure path or bridge-theorem name; **no** book quote or witness demanded |
| coverage evidence | `COVERAGE` | item id + its page from `items.json`; no witness demanded |
| anchor evidence | `PAGE_ANCHOR` | produced mechanically: located page + `book_sha` |
| `book_located: false` | any | not a failure — emitted as a `COVERAGE` finding with the search performed. The v1 gate failed the task while the prompt told workers to set the flag |
| completeness | harvest | §4 |
| count-from-corpus | report | every published number re-derived by `tally.py` |


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
pinned edition, and (outside legacy) its operators are the book's. What it cannot prove is that a
single word in the middle was not swapped — `Finite` → `Countable` survives both passes. That is a
**reviewer** check, which is why adjudication reads the quote against the book rather than trusting
this exit code.

---

## 8. `convert_v1.py` — importing the 2,672-file baseline

Reads `~/Desktop/tao-diff-run/{findings,findings-pdf,findings-gpt,findings-vac,findings-hunt}/`
(382 + 382 + 382 + 763 + 763 files, counted 2026-09-16) and the batch-1 artifacts under
`~/Desktop/tao-differential/`, and writes v2 into `corpus/<run-id>/`. **It never modifies the
baseline.**

1. one run-id per source directory; `run.yaml` records model, book access, gate mode and that the
   run is imported;
2. `faithful` enum → list; `NO_WEAKNESS_FOUND` → `NO_DEFECT_FOUND`;
3. `witness` extracted from `reasoning` where the text names one, else null with
   `witness_extracted: false` — never fabricated;
4. `book_sha` stamped: the published edition for the five tend runs, the **2011 draft sha** for the
   batch-1 rows, whose `PAGE_ANCHOR` rows are imported `withdrawn: true`. Passing a `--book-sha`
   that is not the campaign's is refused unless `--allow-edition-mismatch` is given too, and the
   receipt records both, so no report can silently mix editions;
5. `finding_id` computed per `references/vocabulary.md` §3;
6. bare `repo:decl` names are kept and, where `statements.jsonl` supplies a unique namespace,
   `decl_qualified` is added;
7. dash and underscore slugs both accepted; the file name is not trusted for the item — `item`
   inside the JSON is;
8. batch-1 ledger rows (`accepted.adam.jsonl`, `refuted.adam.jsonl`) import into `ledger/` with
   their verdicts unchanged and free-text resolutions split (§5).

A conversion run prints a reconciliation table (files in, findings out, rows by kind, rows without
a witness) and the regression test asserts the totals against the baseline tally: **72 rows over
40 distinct items** naming the audited companion, partitioned 16 `CONTENT`/`VACUITY`,
1 `SUSPECTED_ERRATA`, 1 `COVERAGE`, 22 `PAGE_ANCHOR`
(`/Users/jack/Desktop/tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md`).
