"""Annotations that relate a subject and an object, lists of ends, with offline validation.

``companion`` defines the format and its strict JSON codec.
``discovery`` reads annotations from selected files and directories.
``cli`` runs the ``validate`` and ``scan`` commands.
``schemacheck`` is the small JSON Schema evaluator that ``companion`` uses;
it is not a general-purpose JSON Schema implementation.

Submodules are not imported eagerly. Each declares its public names in __all__.
"""
from __future__ import annotations

__all__ = ["__version__", "schemacheck", "companion", "cli", "discovery"]

__version__ = "1.0.1"
