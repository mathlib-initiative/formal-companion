# Rules for this text

The rules `STYLE.md` does not fix, for {{ title }}: the library
{{ library_name }} at commit `{{ library_commit }}`, and the theorem
`{{ theorem }}`. `cf informalize init` wrote this file from formal-companion's template;
the conventions task fills it in, and the editor keeps it. Every drafter, attacker, judge
and editor reads it beside `STYLE.md`.

## Notation

The macros of `notation.sty`, one row each.

| write | for |
|---|---|

## Letters

Each letter keeps one meaning in the whole text.

| letter | meaning |
|---|---|

## Standard terms

The term the text uses for each notion, the definition of the library it is, and the link
`sections/00-conventions.tex` gives for it. An item uses the term; it never spells the
definition out again.

| term | definition | where it is fixed |
|---|---|---|

## What the conventions carry

What `sections/00-conventions.tex` fixes once, so that no item repeats it: the ambient
objects and their structure, the typeclass and universe binders, the index type of a
family, the coercions, and the side conditions the library discharges inside a statement.

## Standing hypotheses

One row per key of `hypotheses.json`: the macro, the words it prints, and the Lean binder
it stands for.

| macro | prints | stands for |
|---|---|---|

## Numbers, coercions and junk values

How the text reads natural numbers against integers, the coercions it leaves silent, and
the junk values its statements rely on, each as `sections/00-conventions.tex` states it.

## The reference work

{{ reference_rules }}
