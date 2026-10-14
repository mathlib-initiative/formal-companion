# Legacy annotation documents: `formal-companion/annotations`

This is a legacy intermediate format. `cf export` writes it, and
`cf overlay export` converts it to
[Literature Links](https://github.com/mathlib-initiative/literature-links), the
format of a companion's `annotations/` and of the PDF overlay
(`docs/overlay.md`).

Its specification is tag `legacy-v1.0.0rc2` of the Literature Links
repository, which was formal-companion-annotations before the rename: the
specification, the id rules as an algorithm and every enum value, each a
document under that tag's `docs` directory, plus its JSON Schema and a
language-neutral conformance suite. Every section number this repository cites
for the legacy format is that specification's.

The skill carries that release's package under
`vendor/formal_companion_annotations/` (identifier
`formal-companion/annotations`, `schema_version` `1.0`; its manifest records
the tag under its original name, `v1.0.0rc2`) and imports it through
`cflib.vendored`. It is not compatible with Literature Links 1.0.0.
