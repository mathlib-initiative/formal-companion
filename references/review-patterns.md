# Review patterns — a portable catalog

Recurring faithfulness defects, each with a **detector** and a **standard fix**, written so they
apply to any Lean book companion. Placeholders in `{{…}}` are filled from the campaign:

| placeholder | filled from |
|---|---|
| `{{SRC}}` | `adapter.yaml.glob` — the companion's source glob (e.g. `Lib/Section_*.lean`) |
| `{{BOOK}}` | `book/<id>/book.txt`, with `book.json.offset` for page arithmetic |
| `{{TOTAL_FUNCS}}` / `{{TOTAL_FUNCS_ALT}}` | `conventions.md` §2 — the repo's list of total functions with junk values, as a list and as a regex alternation |
| `{{CONVENTIONS}}` | `conventions.md` §3 — the repo's standing conventions and excluded cases |
| `{{ERRATA_URL}}` | `campaign.yaml.errata_url` — the **live** page, never a local summary |
| `{{CLASSIFIER}}` | `adapter.yaml.sorry.classifier` — the repo's authoritative `sorry` split |

Provenance: P1–P6 are distilled from a 32-finding audit of a second companion of the same book;
P7–P8 come from another project's published catalog; **P9–P13 are the five shapes the 2026-09
five-run audit found, each recurring at least twice in one chapter**
(`/Users/jack/Desktop/tao-measure-lean/REVIEW_PATTERNS.md`, the source this file generalises).

P9–P13 are *statement-level*: invisible to the compiler **and** to a differential, because two
independent repos can be wrong the same way and agree. They are found by adversarial probing with a
concrete witness.

**Mechanical first.** P1, P2, P6, P7, P9, P10 and P12 have greppable detectors; `cf lint <name>`
runs them, together with the companion's own mechanical checks where it has them (the audited repo
ships six: anchor hygiene, exercise-`sorry` preservation, item-numbering gaps, sibling
dimension-hypothesis disagreement, TFAE candidates, and comment lines that begin with a declaration
keyword and confuse line-anchored regexes). Run the catalog **before** spending model budget, and
again as a fix-PR invariant.

---

## P1 — Definition attached to a proof term instead of the object

- **Symptom:** `def Foo.measure (hE : IsFoo E) : ℝ := …` where the book types the object as a
  function on sets; consumers then need `∃ hE, hE.measure = c` instead of `measure E = c`, plus
  irrelevance plumbing.
- **Detect:** `rg -n "\(h[A-Z][a-zA-Z]* *: *Is" {{SRC}} | rg "def .*(measure|volume|length|integral)"`
- **Fix:** a function on the object with a junk value off the class
  (`if h : IsFoo E then … else 0`), book-shaped statements, one bridging lemma.
- **Prior art:** a peer companion needed 20 plumbing call-sites, and its own later section already
  discards the proof argument.

## P2 — A finite *set* of sets where the book has a finite indexed family

- **Symptom:** k-fold union/sum laws over `S : Finset (Set _)`; repeats (`E₁ = E₂`) cannot be
  expressed, silently weakening subadditivity-shaped statements.
- **Detect:** `rg -n "Finset \(Set" {{SRC}}`
- **Fix:** `{E : Fin k → Set _}` (or `ι` + `Fintype ι`) and `∑ i, …` over the index.
- **Prior art:** the peer companion hand-rolls the indexed version elsewhere and does an
  image-dedup dance to consume the `Finset` one.

## P3 — Silent endpoint or normalisation deviation

- **Symptom:** "equivalent-ish" but not the book's: half-open vs closed intervals, a changed
  normalisation, a dropped dimension or nonemptiness hypothesis.
- **Detect:** not fully greppable. `rg -n "Ioc|Ico|Icc|Ioo" <file>` and compare every interval
  constructor and every numeric hypothesis against `{{BOOK}}`.
- **Fix:** match the book exactly; when a deviation is deliberate, say so in the docstring **at the
  definition site** — and see P11 about the reason you give.

## P4 — Book item missing from the file (especially Remarks)

- **Symptom:** a numbered item with no counterpart and no documented skip decision.
- **Detect:** the coverage map (book item → declaration) per section; any row with no declaration
  and no skip note is a hit. `cf pair` emits it as `uncovered.<name>.json`. Remarks count — they
  often carry counterexamples and non-invariance results.
- **Fix:** state it (`sorry`'d if it is an exercise), or record an explicit skip note in the section
  header. Report as kind `COVERAGE`.

## P5 — Hand-rolled library facts / duplicate lemmas

- **Symptom:** a helper that is (or trivially wraps) a stock library lemma; two byte-identical local
  lemmas.
- **Detect:** for each new helper, search the library **at the project's pinned revision** before
  keeping it; `rg` the project for duplicate statement text.
- **Fix:** delete and use the library name, or keep a one-line wrapper naming the original.

## P6 — Model scaffolding left in committed proofs

- **Symptom:** "Strategy:", "Step 1:", "-- Both sides should now match" narration; dead `#check`s;
  unused helper `def`s proving `Prop`s.
- **Detect:** `rg -n "Strategy:|Step [0-9]:|#check" {{SRC}}`
- **Fix:** delete. A comment survives only if it explains mathematics.

## P7 — Statement over-specialised below the book's generality

- **Symptom:** a dimension, field or space pinned concrete where the book states the result
  generally.
- **Detect:** `rg -n "Fin [0-9]" {{SRC}}` inside statements; read against the book.
- **Fix:** quantify at the book's generality.

## P8 — Ad-hoc predicate instead of the library idiom

- **Symptom:** a hand-rolled predicate where the library has the canonical one.
- **Detect:** review each definition-shaped statement against the library's vocabulary.
- **Fix:** restate with the library predicate; convert at the boundary if a book-shaped form is
  needed downstream.

---

## P9 — Junk-value conclusion from a total function

- **Symptom:** the conclusion is an equation on a function the system makes **total** — anything in
  `{{TOTAL_FUNCS}}` — so a point where the object is mathematically undefined satisfies it by the
  junk value, and the hypothesis that was supposed to make the statement meaningful does no work.
- **Detect:** `rg -n '({{TOTAL_FUNCS_ALT}}).*= 0' {{SRC}}` (the list as a regex alternation), then the decisive test:
  **delete the hypothesis and ask whether the statement is still true.** If it is, the hypothesis
  was never load-bearing. For `∃ x, f x = 0` goals, ask whether a point of non-definedness would
  satisfy it.
- **Fix:** conclude with the bundled predicate that cannot be satisfied by a junk value
  (`HasDerivAt f 0 x` rather than `deriv f x = 0`); or add the definedness/measurability clause
  rather than asserting only a value.
- **Two sub-cases, worth keeping apart:** the *hypothesis* stops doing work, or the *conclusion* is
  too weak because it asserts a value without asserting the object is defined.
- **Prior art:** a mean-value-theorem-shaped pair (witness: a function with a corner in the interior
  — continuous, equal at the endpoints, not differentiable at the corner, yet the total derivative
  returns the junk value there), plus two in-text results of the same shape. Note that the negative
  results in the same file already used the bundled predicate — the file contradicted its own
  convention.

## P10 — The book's witness hidden behind an existential

- **Symptom:** the book displays a specific counterexample or construction and the Lean says
  `∃ E, …`, exposing none of the identities the book states about it.
- **Detect:** for any item whose text contains "a counterexample is", "take", "consider the", or a
  displayed construction, check whether the Lean names it.
  `rg -n '∃ \(?[A-Za-z]+ : Type' {{SRC}}` finds the worst cases (existentially quantified carrier
  types).
- **Fix:** state the named family as its own theorem returning the book's identities, and derive the
  existential from it as a corollary so downstream users are unaffected. **Check the proof term
  first** — the construction is often already there, which turns an "expensive" fix into two lines.
- **Prior art:** three instances in one chapter; in one, the enumeration the statement was hiding
  was the first line of its own proof.

## P11 — Hypothesis stronger than the book's, hence a weaker theorem

- **Symptom:** a hypothesis quantifying over more than the book's does — an a.e. condition on the
  whole space where the book's object lives on a subset, convexity where the book says only
  "polytope", a dimension bound the book does not impose.
- **Detect:** read each binder against the book's own scope. Watch for a docstring admitting the
  narrowing — and **check the reason it gives**, which may be false.
- **Fix:** weaken to the book's hypothesis. If a lemma is claimed to be missing, verify that claim:
  the needed fact is often already proved and merely discarded by the proof
  (`obtain ⟨-, h⟩`-shaped discards are the tell — though the same pattern is *correct* wherever the
  conclusion genuinely does not need the discarded component, so the smell is the mismatch with the
  docstring's excuse, not the pattern).

## P12 — Statement contradicting the file's own documented convention

- **Symptom:** a theorem false, or vacuous, at a boundary case that another docstring in the same
  repo explicitly rules out.
- **Detect:** **the cheapest class in this catalog — needs no book, only internal consistency.**
  Take `{{CONVENTIONS}}` and grep for statements quantifying over the cases a convention excludes
  (a degenerate dimension, an empty family, an unbounded object the convention gives a junk value).
- **Fix:** add the hypothesis the convention implies, or restate.
- **Prior art:** an exercise false at the degenerate dimension by the companion's own documented
  convention, found from inside the repo alone.

## P13 — Docstring asserting something false about the book

- **Symptom:** prose making a checkable claim — that the book prints X, that an erratum says Y, that
  a restriction is forced — and the claim is wrong. Proofs are machine-checked; docstrings are not,
  so this is where unfaithfulness accumulates silently.
- **Detect:** treat every factual sentence in a docstring as a claim to verify. Check errata claims
  against `{{ERRATA_URL}}` **live**, not a local summary: a summarising fetch had silently dropped
  the entry that decided a high-severity finding.
- **Fix:** correct the claim, and prefer describing content over citing labels the errata disputes.
- **Prior art:** a docstring asserting the exact opposite of the published erratum; and P11's false
  justification.

---

## Using the catalog

1. `cf lint <name>` runs every greppable detector plus the repo's own mechanical checks, before any
   model budget is spent, and again before a fix PR is opened.
2. The attack prompts are built from the same material: `{{TOTAL_FUNCS}}` drives the vacuity probe
   (P9) and `{{CONVENTIONS}}` drives the internal-consistency probe (P12). A companion whose
   `conventions.md` lists neither gets a weaker run — which is why onboarding treats both lists as
   required (`references/onboarding-contract.md` §5).
3. Extend the catalog per campaign: a pattern earns a number when it has recurred **twice** with a
   detector someone can run. Record the prior art as a finding id in the campaign ledger, not as
   book text.
