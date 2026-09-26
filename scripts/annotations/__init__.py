"""The lean-companion annotation format: schema checks, id rules, canonical form.

New code.  The format is specified in docs/annotations.md and its JSON Schema is
schema/annotations-v1.schema.json (schema_version "1.0").  ``validate`` is the
checker (and a CLI) and the RFC 8785 canonicaliser, ``schemacheck`` the small
JSON Schema evaluator it runs, because this repo is standard library only
(CLAUDE.md S3), and ``ids`` the mechanical id rules of docs/annotations.md
section 5: statement-hash ids for anonymous declarations, and quote- or
page-and-title-hash ids for unnumbered passages.
"""
