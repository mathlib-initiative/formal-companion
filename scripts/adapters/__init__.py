"""Statement-table adapters: one per companion-repo convention.

An adapter turns a companion repository at a pinned commit into rows of the
statement table (schema v2, ``adapters.base.ROW_FIELDS``).  The design foresees
three kinds; two ship in version one:

``docstring_tags``      declarations whose docstring opens with a book tag,
                        backticked or bare (tao-measure-lean, teorth/analysis).
``attribution_table``   a machine-readable item->declaration map, its
                        statements cut from the Lean (measure_theory's overlay data).
``blueprint_tex``       a leanblueprint manuscript: ``\\lean{}`` / ``\\leanok``
                        inside the statement environments of blueprint/**/*.tex.
``ledger_nodes``        a source-node ledger, usable only after an item-level
                        curation pass (``granularity: node``) -- not
                        implemented in version one.

Every adapter subclasses ``cflib.adapters_api.Adapter`` through
``adapters.api.Adapter``.  ``cflib.adapters_api.load`` delegates to the
``load`` re-exported here, so a command only ever writes:

    from cflib.adapters_api import load
    adapter = load(paths.adapter_yaml(name), workspace=source_view)
"""

from .api import (Adapter, AdapterError, detect_all, explain_all,  # noqa: F401
                  from_config, load, registry)

__all__ = ["Adapter", "AdapterError", "load", "from_config", "registry",
           "detect_all", "explain_all"]
