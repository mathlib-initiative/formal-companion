"""Stage 3: pairing, coverage, queue and the mechanical page-anchor check.

Everything here keys on the BOOK item id -- the only key N independent
codebases share -- through the strict grammar in ``cflib`` (fallback:
``book/compat.py``).  Nothing here calls a model: the LLM stages start at
``cf plan-run``.
"""
