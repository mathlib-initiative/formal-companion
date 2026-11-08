# Writing informal mathematical accounts

Use this guide when planning, drafting, editing or reviewing an informal account
of a Lean development. It concerns the mathematical exposition, not the verdicts
of a faithfulness audit. The intended reader should understand the argument
without first opening the Lean source.

## Plan around the argument

Choose the constructions, substantial lemmas and deductions that explain the
main theorem. A dependency closure is an inventory for checking coverage, not
a table of contents. Keep implementation helpers traceable in the source
inventory without turning each helper into a numbered result. Endpoint casts,
coercions, restriction followed by inclusion and pointwise formulas rarely
need separate statements.

Give each section a mathematical job. Merge a section that has become only a
notation list into the arguments that need its notation. Place specialized
machinery near its first substantial use, rather than interrupting the main
argument with an abstract preliminary section used much later. State a result
before its proof, and use earlier results instead of proving them again.

## Introduce objects where they are used

Put variables used by one result into that result's first sentence. Put context
shared by several results into one connected opening paragraph. Do not detach
a short setup sentence immediately before a statement merely to announce a
special case. For example, introduce the finite set in the corollary that
uses it, rather than adding a separate paragraph announcing the finite case.

Make sections readable on their own, but do not repeat a catalogue of symbols.
Check neighboring section openings for repeated introductions of the same notation.
State what new notation denotes, and briefly give the action of an induced map
or functor. An explicit parameter formula can clarify what an adjective such as
"linear" leaves unclear. These explanations make the notation usable without
requiring a separate background definition.
A letter keeps its meaning within an argument; a later argument can introduce
it afresh. State a stronger assumption only where it is required. In
particular, a metric lemma must not silently make a later topological space
metric too.

Keep conventions that change the mathematics: the order of a nonstandard
composition, whether a connected subset must be nonempty, or the behavior at
a degenerate value. Do not explain familiar definitions just because Lean
represents them by a typeclass. The project's stated audience determines
what is familiar.

## Remove filler, not mathematical content

An introduction normally needs one sentence identifying the theorem and the
library. Annotation mechanics, source inventories and the drafting process
belong in the repository documentation.

Omit reminders such as that a monoid homomorphism preserves multiplication,
that a groupoid has inverses, or that a continuous map sends paths to paths
when these are assumed background. Prefer the term itself, with a definition
link where useful. If removing a sentence leaves no loss of notation,
hypothesis, construction or inference, remove it.

Name functors, homomorphisms, generated subobjects and intermediate values
that will be used again. Prefer a readable mathematical name matching the
linked Lean argument when it helps recognition. Do not print tactic syntax,
helper declaration names, typeclass binders or universe parameters. Do not
copy every temporary name from the implementation.

When proofs are requested, give actual arguments, allowing concise sketches:
state the construction, the needed equalities or closure arguments, and the
deduction. A list of intended proof ideas is not a proof. Keep substantive
steps such as subdivision, comparison through a common refinement, or descent
to a quotient; suppress routine bookkeeping. Cite a previously established
construction rather than verifying its elementary laws again.

## Let annotations follow the exposition

Link a notion at its first useful occurrence in ordinary prose or in a
statement. Do not add an explanatory sentence solely to provide a place for
an annotation. When the marker format supports it, a hypothesis inside a
numbered statement is a natural place for its definition link. Do not mark
every familiar word merely because it has a library declaration.

Link a selected proof sentence or displayed equation to the specific Lean
range that establishes it. A box around an entire proof does not provide
step correspondence. Keep linked hypotheses and notation consistent, and
verify the viewer's code highlighting and hovers for proof ranges as well as
for statement cards.

## Review the whole document before delivery

The editor must do a complete reading pass, not only apply individually
reported corrections. Review sections together for repeated introductions,
stranded setup paragraphs, empty preliminary sections, unexplained notation,
and repeated proofs of earlier results. Preserve every mathematical
hypothesis and the exact meaning of retained statements.

Render and inspect every PDF page. Check section boundaries, headings,
short isolated paragraphs, broken words, displayed equations and annotation
placement. Fix the prose or grouping that causes awkward layout before using
manual spacing or page breaks. A successful compiler or marker check does
not establish readable typesetting.

The final review should answer:

- Can the intended reader follow the mathematical argument without opening Lean?
- Does each numbered result contribute to that argument?
- Are one-use variables introduced inside the result that uses them?
- Does every remaining explanatory sentence serve the intended reader?
- Are the chosen mathematical names recognizable in their linked Lean ranges?
- Do proof sketches contain deductions, rather than promises of deductions?
- Do the rendered pages read as a connected mathematical text?

Treat failures of these checks as editorial work to complete before asking
for human critique. A missing hypothesis is a mathematical defect; an
unnecessary sentence is a style defect. Neither should be disguised as the
other.
