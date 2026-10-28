"""The locate corpus: universes of Lean libraries, their indexes, and ``corpus.sqlite``.

New code for docs/proposals/wikipedia-locate.md sections 2.3-2.5 and 6, in the
shapes references/locate-documents.md section 8 fixes.  Two halves:

* host side, standard library only and importing no ``cflib``, because the run
  host has none: the probe drivers (``build_universe_host.sh``,
  ``probe_host.sh``, ``probe_merge.py``, ``universe_host.py``) and the two
  tools a locate worker runs (``corpus_search.py``, ``corpus_show.py``);
* workspace side: ``universe.py`` (``universe.yaml``), ``ingest.py`` (a probe's
  index into a normalised one, with every proof status re-decided),
  ``build_db.py`` (``corpus.sqlite``) and ``query.py`` (the read API the other
  locate stages call).

``cf`` never builds a library: ``cf corpus probe-command`` prints the host
pipeline and the operator runs it.
"""
