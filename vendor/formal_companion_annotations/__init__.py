"""The formal-companion annotation format: schema checks, id rules, canonical form.

The format is specified in docs/annotations.md and its JSON Schema is
schema/annotations-v1.0.schema.json (one file per format minor; schema_version
"1.0"), shipped inside this package as ``schema/annotations-v1.0.schema.json``.
``validate`` is the checker (and a CLI) and the RFC 8785 canonicaliser,
``schemacheck`` the small JSON Schema evaluator it runs, because this package
is standard library only, ``ids`` the mechanical id rules of
docs/id-algorithm.md (statement-hash ids for anonymous declarations, and
quote- or page-and-title-hash ids for unnumbered passages), and ``vocab`` the
format's enums and id grammar.

The submodules are not imported here, so ``python3 -m
formal_companion_annotations.validate`` runs without a double import.
"""
from __future__ import annotations

#: The stable public API: the version and the four modules, each of which
#: states its own names in its ``__all__``.  Listing the modules does not
#: import them; ``from formal_companion_annotations import *`` does.
__all__ = ["__version__", "ids", "schemacheck", "validate", "vocab"]

__version__ = "1.0.0rc2"
