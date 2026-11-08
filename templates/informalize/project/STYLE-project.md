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

Keep a letter's meaning fixed within an argument; introduce it afresh in a later one when needed.

| letter | meaning |
|---|---|

## Standard terms

The assumed background and useful definition links. Mark a retained notion at its first
natural occurrence, including inside a statement; never add prose solely to host a link.

| term | constant | first useful occurrence |
|---|---|---|

## Local introductions and material conventions

Put one-use variables in their result and shared context in a connected section opening.
Record only conventions that change the mathematics, such as nonemptiness or composition
order. The optional conventions file may be empty. List the audience's assumed background
and the mathematical job of each section. Do not create a preliminary section that only
collects notation used later.

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
