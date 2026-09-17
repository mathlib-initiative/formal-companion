"""Statement-table adapters: one per companion-repo convention.

An adapter turns a companion repository at a pinned commit into rows of the
statement table (schema v2, ``adapters.base.ROW_FIELDS``).  The design foresees
three kinds; two ship in version one:

``docstring_tags``      declarations whose docstring opens with a backticked
                        book tag (tao-measure-lean, teorth/analysis).
``attribution_table``   a machine-readable item->declaration map plus
                        statement snippets (measure_theory's overlay data).
``ledger_nodes``        a source-node ledger, usable only after an item-level
                        curation pass (``granularity: node``) -- not
                        implemented in version one.

Every adapter subclasses ``cflib.adapters_api.Adapter`` through
``adapters.api.Adapter``.  ``cflib.adapters_api.load`` delegates to the
``load`` re-exported here, so a command only ever writes:

    from cflib.adapters_api import load
    adapter = load(paths.adapter_yaml(name), workspace=source_view)
"""

from .api import Adapter, AdapterError, from_config, load, registry  # noqa: F401

__all__ = ["Adapter", "AdapterError", "load", "from_config", "registry"]
