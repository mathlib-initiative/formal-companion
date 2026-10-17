"""The locate stage: where a source is formalized, in any library.

New code, implementing docs/proposals/wikipedia-locate.md sections 2.6, 2.7,
3 (L2, L3a, L3b, L5, L6), 5, 7 and 11.1 in the formats of
references/locate-documents.md: the L2 candidates (``candidates``), the
sourceset-scoped planner and its three presets (``presets``, ``plan``,
``challenge_plan``, ``prompts``, ``tend_root``), the harvest of locate
documents into passage outcomes with a derived status (``outcomes``), and the
Literature Links export with its sealed record set (``records``, ``export``).

``inputs`` holds the small readers of the formats other packages write (the
sourceset of section 7, the universe and ``corpus.sqlite`` of section 8);
``records`` imports nothing from ``cflib`` so the gate can carry it to a run
host.
"""
