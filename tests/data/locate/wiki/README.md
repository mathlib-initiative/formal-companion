# Recorded Wikipedia API responses (synthetic)

Every page here is made up -- "Widget lemma" (page 1001, revision 2002, Q99999001),
"Gadget theorem" (1002, 2003) and "Sprocket" (1003, 2004) -- in the shape the
MediaWiki API answers `action=parse` (formatversion 2), `list=projectpages` and
`prop=revisions|pageprops`. `sources.wikinet.FixtureTransport` files each one under
the sha256 of its `query`'s canonical form; `tests/test_locate_sources.py` replays them.
No text of any real article is here.
