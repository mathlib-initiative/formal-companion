# The measured method

What actually finds statement-level faithfulness defects in a Lean 4 book companion, with the
evidence for each claim. Every number here was recomputed from a file named beside it; nothing is
estimated. Read this before choosing what to run: the design of an audit is settled by §1–§3,
and §9 is the rule that stops a report quoting a number it cannot recompute.

One vocabulary note, because the shape of the work changed underneath this file: there is no
"one campaign per book" any more. A **workspace** holds N companions and M sources, a companion
is audited against a *list* of sources, and a differential is possible exactly where two
companions' source lists overlap. Everything below about evidence classes, yields and
calibration is unchanged by that — it is measured per (source, companion) pair either way.

**Contents** — §1 attack runs vs differentials · §2 the three changes that flip it ·
§3 program order for a new companion · §4 the ~40% first-pass false-positive rate ·
§5 the five recurring defect shapes · §6 safe bridge vs definitional fiat ·
§7 why the techniques find disjoint classes · §8 every bug came from a second reading ·
§9 count from the merged corpus · §10 what the numbers do not license ·
§11 where each rule lives in the code.

Primary sources, all read for this document:

None of them are in this repo — they are the campaign's own notes and the two
private companions' files — so each row says what it is, not where one machine
kept it.

| source | what it establishes |
|---|---|
| the campaign's audit-method notes | the five-run yield, the five defect shapes, the self-inflicted traps |
| the campaign's verified-figures notes | the corrected figures (411 vs 410, raw vs collapsed items, what the 6-reader pass covered) |
| the docstring-tag companion's `HANDOFF.md` §2, §4, §5, §8 | the run table, the four errors, the extension preconditions |
| the docstring-tag companion's `OPEN_FAITHFULNESS_FINDINGS.md` | the self-recomputing tally and the 72/40 partition |
| the attribution-table companion's faithfulness-audit notes | the safe-bridge rule, the disjoint-classes corollary, "every bug came from a second reading" |
| the handoff note written for that companion's owner | the same rule as written for the other companion's owner |
| the v1 differential's `{accepted,refuted}.adam.jsonl` | the ≈40% first-pass false-positive rate |
| the v1 corpus's `verify_findings.py` and its attack variant | the gate kinds that make the attack ratio partly definitional |
| the docstring-tag companion's `REVIEW_PATTERNS.md` P9–P13 | the defect shapes with detectors |
| the v1 proxy detector's README | the two proxy-detector rules and their portability |

---

## 1. Attack runs beat differentials, and the honest way to say so

Five runs over one book chapter, same corpus, same repository under test. Counting **distinct book
items whose findings name a declaration of the audited repo**, and how many of those are
substantive (`CONTENT` / `VACUITY` / `SUSPECTED_ERRATA` rather than a page anchor or a coverage
note) — `tao-measure-lean/docs/HANDOFF.md` §2, recomputed there from the harvested corpus:

| run | design | model | book access | items | substantive |
|---|---|---|---|---:|---:|
| 1 | differential | Claude | text | 21 | **4** |
| 2 | differential | Claude | page images | 16 | **2** |
| 3 | differential | GPT-5.6Sol | text | 15 | **4** |
| 4 | attack: vacuity & strength | GPT-5.6Sol | text | 11 | **11** |
| 5 | attack: defect hunt | Claude | text + images | 6 | **6** |

**Do not quote "100% signal" as precision.** The attack gate sets
`KINDS = {CONTENT, VACUITY, SUSPECTED_ERRATA}` — verified by diffing the two gate scripts, where
the attack variant carries the comment *"citation findings banned in the attack runs"* — so a page
anchor **cannot** appear in an attack run's output. The ratio is partly definitional.

**The result is the absolute column.** Each attack run surfaced more substantive items (11, 6)
than all three differentials combined (4, 2, 4), and the union of the three differentials is
smaller than either attack run alone. Attacks also reach classes a differential is structurally
blind to: a statement that is vacuous, weaker than the book, or true only by a junk value is
exactly the case where two independent repos are wrong the *same* way and therefore agree.

Cost of the cheap finding: **22 of the 40 differential-flagged items were pure pagination**
(`tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md`, tally at the foot of the file), and each consumed an
adjudication slot. Comparing two repos rewards *any* difference, and a page number is the cheapest
difference available.

## 2. The three changes that flip it

1. **Reject `PAGE_ANCHOR` and `COVERAGE` at the validation gate** for attack runs. Page anchors are
   then produced mechanically by `pair/anchor_check.py` against the pinned edition — no LLM slot,
   no adjudication slot.
2. **Reframe the task.** "Adjudicate this item" invites a diff report; "find the defect, or answer
   `NO_DEFECT_FOUND`" invites a search. Both attack task templates say explicitly that an empty
   answer is a good outcome.
3. **Require a concrete witness**: the specific dimension, set, function or index at which the Lean
   statement and the book's claim come apart. "The hypothesis looks wrong" is not a finding.

These are encoded in the skill as run modes (`differential`, `single-source`, `attack:vac`,
`attack:hunt`), a per-kind gate matrix (`references/findings-schema-v2.md` §7) and the witness
gate.

## 3. Program order for a new companion

`lint` (mechanical, no book) → `def-coverage` (item table only, no Lean) → `attack:vac` +
`attack:hunt` over every item the repo formalises → `differential` only for items ≥2 companions
share → `anchor_check` mechanically → `adjudicate` → `route`. The differential stays in the
program because it is the only stage that sees specialisation and attribution deltas *between*
repos (the two `ATTRIBUTION_GAP` verdicts in `accepted.adam.jsonl` are of that kind), not because
of its yield.

## 4. Calibration: a first-pass finding is wrong about 40% of the time

`accepted.adam.jsonl` holds 10 adjudicated rows: **4 `CONFIRMED_CONTENT`, 2 `ATTRIBUTION_GAP`,
4 `CONVENTION`** (recomputed 2026-09-16 from the file). Four of ten first-pass findings were
refuted as house convention — always in that direction, never the reverse. `refuted.adam.jsonl`
holds 5 rows (4 `CONVENTION`, 1 `REFUTED`), the refutation being a page anchor checked against the
wrong PDF edition.

Consequences the skill enforces:

- **Nothing goes upstream unchallenged.** `route` refuses to emit an upstream report while
  `_meta.status` is `UNCHALLENGED`; `publish-verdicts` refuses to write `human_confirmed`.
  The batch-1 findings routed against the third companion — **32 rows, 7 high, over 26 items**
  (recomputed from the v1 differential's `findings.teorth.jsonl`) — carry
  `_meta.status: UNCHALLENGED` and were never reported upstream; the docstring-tag
  companion's `HANDOFF.md` §6 records
  holding them back as a deliberate decision.
- **The reviewer's job is refutation, not approval.** The shipped reviewer prompt carries the 40%
  figure as its own justification, and says approving a wrong finding is a worse failure than
  requesting changes on a right one.
- **A false positive costs a human an hour**, and costs credibility if it reaches someone else's
  repository.

## 5. The five recurring defect shapes

Each recurred at least twice in one chapter of one companion. Full detectors and prior art are in
`references/review-patterns.md` (P9–P13); the tests are what matter here.

1. **Total-function junk values.** `deriv`, `derivWithin`, an `unsignedIntegralOn`, any total
   measure returns a value where the object is undefined, so `… = 0` conclusions are satisfied by
   non-differentiability and the hypothesis becomes deletable.
   **Test: delete the hypothesis. If the statement is still true, it was never doing the work.**
2. **Witness hidden behind an existential.** The book names the rationals, the singleton family,
   a specific carrier; the Lean says `∃ E`. Three instances in one chapter.
   **Test: does the book display a construction? Then does the Lean name it?** The enumeration is
   often already in the proof term.
3. **Proxy definition by definitional fiat.** A library object adopted as *the* definition with no
   proved equivalence to the book's display. **Test: §6.**
4. **Stronger hypothesis = weaker theorem.** A hypothesis quantifying over more than the book's
   does. **Test: read each binder against the book's own scope, and check any docstring that
   admits the narrowing — the reason it gives may be false (§8).**
5. **Statement contradicting the file's own documented convention.** **Test: collect the repo's
   stated conventions and look for statements quantifying over cases a convention excludes.** This
   is the cheapest class in the catalog: it needs no book, only internal consistency, and it is
   why `conventions.md` is an input to every attack prompt.

## 6. Safe bridge versus definitional fiat

> A Mathlib-first bridge is **SAFE** when it is a *proved equivalence* that recovers the book's
> text, and **UNSAFE** when the library object is adopted by *definitional fiat*.

Established jointly by the two companion sessions on 2026-09-05 (the handoff note
written for the attribution-table companion's owner, "The reusable finding", and
that campaign's faithfulness-audit notes). Evidence on both sides:

- **Unsafe:** the attribution-table companion defined its elementary measure as the library volume
  (issue #5 → PRs #6/#8) and stated its measurability definition via the library outer measure
  where the repo had its own box-cover construction with a bridge theorem (→ PR #9). The book's
  object *is* the library object by fiat, so the book's own finitary development was imported
  rather than formalised.
- **Safe:** of 36 numbered chapter-1 Definitions in the docstring-tag companion, **32 have a
  native `def`/`structure`, 0 have no declaration at all, and the 4 tagged only to theorems are
  each a proved equivalence recovering the book's text** (artifact: the v1
  differential's `def-coverage.ours.json`).

Corollary the skill implements as a first-class stage: **the cheap upper bound needs no Lean.**
From an item→declaration table alone, ask *"is there a book Definition item that no declaration
states, and only bridges consume?"* That is the content-losing case
(`adam-detector/README.md`, "Portability to another repo").

The Lean tier adds two rules, reusable verbatim across repos of the same book
(`adam-detector/README.md`): **ORDER VIOLATION** — a key definition whose definitional closure
reaches a *later*-stage library constant than the item it realises; **SAME-STAGE PRIMITIVE** — a
key definition whose direct dependencies are the library's rendition of the *same* section's
concept.

## 7. The techniques find disjoint classes

- The **differential** fires only where two repos formalise the same item, so it is structurally
  **blind to proxies**: both repos "agree" with nothing. Both proxies above were found with no
  reference formalization involved.
- The **detector** is blind to specialisation and attribution bugs — a dimension pinned, a
  degenerate case excluded, a theorem attributed to the wrong item.
- The **attack** runs are blind to nothing in principle but see one repo at a time, so they cannot
  produce an `ATTRIBUTION_GAP` verdict that names another repo's faithful rendering.
- The **mechanical lint** (`cf lint`, specified but not implemented in v1; the P-detectors and the repo's own audit script) needs
  neither book nor model and catches P12-class contradictions from inside one file.

A faithfulness program needs all four. Reporting the yield of one as the yield of the program is
the error §1 exists to prevent.

## 8. Every bug came from a second reading

> "The compiler can't see faithfulness; every bug found so far came from a second reading.
> Systematic = systematically supplying independent second readings."
> — `memory/measure-theory-faithfulness-audit.md`

That is the whole justification for the adjudication topology (three lenses → reconciler → two
opposing skeptics), for the refute-by-default challenge stage, and for the independent second
numbers vote in the report verifier. The skeletons are in `references/workflow-patterns.md`.

Two corollaries with evidence:

- **A documented restriction is not a justified one.** One exercise carried a docstring reading
  "NARROWER THAN THE BOOK" and blamed an open stub; the needed fact was already proved and the
  proof was discarding it with `obtain ⟨-, hPsum⟩` (`tao-measure-lean/docs/HANDOFF.md` §4). A plausible excuse
  survives every reading until someone checks the excuse itself.
- **A summarised source is not the source.** The local errata file was a summarising fetch and had
  silently dropped the entry that decided a high-severity finding (`tao-measure-lean/docs/HANDOFF.md` §4; P13).
  The skill therefore fetches errata with a raw HTTP client (`curl`) and keeps the raw HTML beside
  the rendered form, dated. A fetch tool that summarises is never used for a source of record.

A finding wrong in its *citation* can still point at something real — a reason to look harder, not
to close. Conversely, an erratum that *observes* a divergence and points to a blog post does not
license changing a book companion: the companion formalises the book
(`tao-measure-lean/docs/HANDOFF.md` §5, the one finding deliberately not acted on).

## 9. Count from the merged corpus, never from prose

The ledger's own closing command tallies every `unfaithful_decls` entry with the repo's prefix
over **every merged finding file** (`tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md`, "The tally that closes
this file"). As of 2026-09-07 it prints **72 rows over 40 distinct items**, partitioning as

| class | items |
|---|---:|
| `CONTENT` / `VACUITY` | 16 |
| `SUSPECTED_ERRATA` | 1 |
| `COVERAGE` only | 1 |
| `PAGE_ANCHOR` only | 22 |

The same file records the count being wrong three times first — "everything is fixed", then
"17 rows / 13 items", then 71 — **and every error ran toward "cleaner"**. Assume that bias in
anything inherited.

Four mechanical rules follow, all enforced by the skill:

1. **Re-tally from the corpus, not from the findings you read.** Triaging from read findings missed
   three items, one of them high severity (`tao-measure-lean/docs/HANDOFF.md` §4).
2. **Diff the harvest against its source.** The local harvest held 762 files where the server had
   763, and the missing file was a real `CONTENT` finding; every derived count was off by one.
   `harvest` writes a `RECEIPT.json` with the source count and a sha manifest, and refuses to
   complete on a mismatch.
3. **Reconcile the task count against the item universe.** The task generator's single-space regex
   dropped `Exercise    1.3.19` from a chapter of **411** numbered items, so the published
   denominator was 410 (`memory/tao-differential-audit-verified-figures.md`). `make_tasks`
   reconciles tasks against `items.json ∩ any-repo` and fails loudly on a gap.
4. **Publish the authoritative count, not a grep.** The repo's own classifier is the real
   exercise/in-text split (510/6/516); `grep -c sorry` gives 523 because 7 hits are prose
   (`memory/faithfulness-audit-method.md`).

"Nothing is open" ships with the one-line command that recomputes it. Every number in a handoff is
pasted from `tally.py` or `gh` output and re-derived by an independent verifier lens; the last such
pass confirmed 151 claims and corrected 19 (`memory/tao-differential-audit-verified-figures.md`),
and an earlier pass found 36 wrong claims in a first draft.

## 10. What the measured numbers do not license

- **Collapse sub-parts before quoting cross-repo counts.** Corpus item strings include sub-parts
  (`Exercise 1.3.10(ii)`). Raw counts for the third companion are 62 substantive / 25 high; the
  collapsed, quotable figures are **40 substantive / 18 high**
  (`memory/tao-differential-audit-verified-figures.md`).
- **Per-item substantive rate is ≈2%** for the two companions measured. A run that finds nothing
  on a small sample has proved very little.
- **The yields belong to the models that produced them** — a Claude bridge and GPT-5.6Sol through
  the same server, model ids unrecorded in the run metadata. Changing the worker tier changes the
  baseline a regression test compares against.
- **Inter-run agreement is not external calibration.** The hunt run re-found three already-raised
  defects; that is two runs agreeing, not an independent oracle.
- **A verdict of "faithful" covers only the clauses examined.**

## 11. How the skill encodes all of this

| rule | where it lives |
|---|---|
| attack-first program | `SKILL.md`, run-mode presets in `plan-run` |
| per-kind gate matrix, witness required in attack mode | `scripts/gate/verify_findings.py`, `gate.json` |
| page anchors mechanical | `scripts/pair/anchor_check.py` |
| conventions feed every attack prompt | `companions/<name>/conventions.md` → `templates/prompts/*` |
| challenge before report | `adjudicate`, `route` refusing `UNCHALLENGED` |
| count from corpus | `harvest/tally.py`, `report` + `verify-claims.workflow.js` |
| harvest completeness | `harvest/harvest.py` `RECEIPT.json` |
| task/item reconciliation | `plan/make_tasks.py` |
| errata fetched raw | `scripts/sources/fetch_errata.py` (curl; raw HTML kept, dated) |
