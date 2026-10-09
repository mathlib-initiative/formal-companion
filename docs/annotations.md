# Annotation documents: `formal-companion/annotations`

The annotation format's specification lives in its own repository,
formal-companion-annotations
(`github.com/mathlib-initiative/formal-companion-annotations`): the
specification itself, the id rules as an algorithm and every enum value,
each a document under that repository's `docs` directory, plus its JSON
Schema and a language-neutral conformance suite. Every section number this
repository cites is the specification's.

The skill carries one release of the format's package under `vendor/` (its
manifest names the tag; at present the release candidate `v1.0.0rc2`, whose
identifier is `formal-companion/annotations`, `schema_version` `1.0`) and
imports it through `cflib.vendored`. The release candidate is not compatible
with the withdrawn `v1.0.0`: the identifier, the id rules, the vocabulary and
several fields changed (the format's changelog lists them), and documents
written against that tag must be regenerated.
