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
