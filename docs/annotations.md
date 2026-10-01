# Annotation documents: the vendored format

The annotation format's specification moved to its own repository,
formal-companion-annotations
(`github.com/mathlib-initiative/formal-companion-annotations`), as
`docs/annotations.md` there. Every section number this repository cites is
that document's; the numbers are unchanged.

The skill carries one tagged release of the format's package under `vendor/`
and imports it through `cflib.vendored`.
`vendor/formal_companion_annotations.manifest.json` names that release — its
upstream, tag, commit, version and the sha256 of every file — and
`scripts/annotations/sync_format.py` is what replaces it. That manifest is the
only place the release is named, and the package's own
`formal_companion_annotations.vocab` is the only place its `schema` value and
major version are written down: the format repository may move ahead of this
one, including by renaming the schema identity, so nothing here states the
identity itself. `docs/overlay.md`, "Upgrading the format", is how a bump
reaches a generated overlay.
