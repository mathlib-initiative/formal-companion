"""L2: the deterministic candidates of one web source, for one universe.

New code, implementing docs/proposals/wikipedia-locate.md section 3 (L2) in the
shape of references/locate-documents.md section 5.  Two kinds of hit, kept
apart because only one of them binds gate rule 4:

* **curated** -- a deterministic join that says "this declaration is about
  this article": a library's ``@[wikidata]`` tag on the article's own QID
  (``wikidata_tag``), Mathlib's QID-keyed ``1000.yaml`` (``mathlib_1000``; a
  suffixed key such as ``Q180345X`` is a further theorem under the base QID)
  and its numbered ``100.yaml`` matched by QID or by normalised title
  (``mathlib_100``), a Wikipedia URL in a docstring naming the article
  (``docstring_url``, an exact join on the canonical page URL
  ``corpus.ingest.wikipedia_urls`` writes, the mobile host folded), and a
  library's own list of main results (``library_metadata``).  A title join keys
  on the article's TITLE only -- a bold term may be another article's subject --
  and never joins an entry that names another QID than the article's.  Every
  curated declaration is checked in the database's exact table at the
  universe's commit; one that is not there, and an entry refused for its QID,
  is dropped and NAMED in ``dropped``, never kept as a phantom the gate would
  later refuse a worker for not locating.
* **lexical** -- the host tool's own ranked word search (``corpus.query.
  search``: every word first, any word only to fill up) over the article's
  terms: its title and the lead's bold terms (``finders.json``'s
  ``bold_definition`` hits in the lead section).  A hint, never an obligation.

Everything is read from ONE database, recorded in the file as ``corpus_db:
{kind, sha256}``: the full ``corpus.sqlite``; in the G1-blind arm (``blind``)
the blind ``corpus.blind.sqlite``, with no curated join computed at all (so
``curated`` and ``dropped`` are empty, ``article_qid`` is null, the lexical
hits are not filtered by any join) and ``"blind": true``, so the gate turns
rule 4 off for that source (proposal section 8.1); for the P tier a planted
database, so no hint names a declaration the ablation removed.  Each kind has
its own directory (``candidates_dir`` below), so one arm never overwrites
another's files, and the planner refuses a plan whose candidates came from
another kind of database than the one its workers search.

The library-metadata input is a JSON or YAML file of this shape, one block per
universe library (a FormalFrontier ``formalization.yaml`` is converted into it
by whoever holds one)::

    {"libraries": {"alpha": [{"wikidata": "Q99999001", "title": "Widget lemma",
                              "declarations": ["Widget.exists_spanning_gadget"]}]}}
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

from corpus import query
from corpus.ingest import wikipedia_urls
from sources import websource

from . import inputs

SCHEMA = "cf-locate-candidates/1"
#: lexical hits kept per source by default
DEFAULT_LIMIT = 20


#: apostrophe and dash look-alikes a curated list spells where Wikipedia titles use
#: U+0027 and U+2013 ("Ptolemy’s theorem", "Cauchy-Schwarz inequality")
_TITLE_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'",
                             "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                             "\u2014": "-", "\u2212": "-"})
_QID_KEY = re.compile(r"(Q[1-9][0-9]*)([A-Z]*)")


def normalise_title(text: str) -> str:
    """A title for matching only: NFKC, apostrophes and dashes folded, underscores and
    runs of space to one, case-folded, a leading "the" dropped."""
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_TITLE_FOLD)
    text = re.sub(r"[\s_]+", " ", text).strip().casefold()
    return re.sub(r"^the ", "", text)


def qid_of_key(key: Any, where: str = "1000.yaml") -> str:
    """The base QID of a ``1000.yaml`` key: ``Q180345X`` and ``Q4724004A`` are further
    theorems filed under ``Q180345`` and ``Q4724004``.  A key that is no QID is refused."""
    match = _QID_KEY.fullmatch(str(key).strip())
    if not match:
        raise CfError(f"{where}: key {key!r} is not a QID (Q<digits>, optionally a letter suffix)")
    return match.group(1)


def read_curated_1000(path: Path) -> dict[str, list[tuple[str, list[str]]]]:
    """A ``1000.yaml``-shaped file: ``{base QID: [(its key as written, declarations)]}``.

    Read as data, never fetched; an entry with no declaration is kept with ``[]``.
    The bench's gold reader keys the same file the same way.
    """
    data = _read_structured(path)
    if not isinstance(data, dict):
        raise CfError(f"{path}: a 1000.yaml-shaped file maps a QID to an entry")
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for key, entry in data.items():
        base = qid_of_key(key, str(path))
        if isinstance(entry, dict):
            out.setdefault(base, []).append((str(key).strip(), _decl_names(entry)))
    return out


def read_curated_100(path: Path) -> list[dict]:
    """A ``100.yaml``-shaped file: ``[{number, title, wikidata, declarations}]``."""
    data = _read_structured(path)
    if not isinstance(data, dict):
        raise CfError(f"{path}: a 100.yaml-shaped file maps a number to an entry")
    out = []
    for number, entry in data.items():
        if isinstance(entry, dict):
            out.append({"number": str(number), "title": str(entry.get("title") or ""),
                        "wikidata": str(entry.get("wikidata") or ""),
                        "declarations": _decl_names(entry)})
    return out


def article_url(site: str, title: str) -> str | None:
    """The article's canonical desktop page URL, in the form docstring tags are stored in."""
    if not site or not title:
        return None
    found = wikipedia_urls(f"{site.rstrip('/')}/wiki/{str(title).replace(' ', '_')}")
    return found[0] if found else None


def _read_structured(path: Path) -> Any:
    if not Path(path).is_file():
        raise CfError(f"no such file: {path}")
    if str(path).endswith(".json"):
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return read_yaml(path)


def _decl_names(entry: dict) -> list[str]:
    names = []
    for key in ("decl", "declaration"):
        if entry.get(key):
            names.append(str(entry[key]))
    for key in ("decls", "declarations"):
        value = entry.get(key)
        if isinstance(value, list):
            names.extend(str(v) for v in value if v)
    return list(dict.fromkeys(n.strip() for n in names if str(n).strip()))


class CuratedLists:
    """The curated inputs one ``cf locate candidates`` call was given, read once.

    ``curated_library`` is the universe library Mathlib's two lists name; None
    means ``corpus.universe.Universe.mathlib_library()``'s answer.
    """

    def __init__(self, *, list_1000: Path | None = None, list_100: Path | None = None,
                 library_metadata: Path | None = None, curated_library: str | None = None):
        self.curated_library = curated_library
        #: base QID -> [(the key as written, declarations)]
        self.by_qid_1000: dict[str, list[tuple[str, list[str]]]] = (
            read_curated_1000(list_1000) if list_1000 else {})
        #: 100.yaml by its own QID, and by normalised title with the entry's QID
        self.by_qid_100: dict[str, list[tuple[str, list[str]]]] = {}
        self.by_title_100: dict[str, list[tuple[str, list[str], str]]] = {}
        self.metadata: list[tuple[str, dict]] = []
        for row in read_curated_100(list_100) if list_100 else []:
            if row["wikidata"]:
                self.by_qid_100.setdefault(row["wikidata"], []).append(
                    (row["number"], row["declarations"]))
            if row["title"]:
                self.by_title_100.setdefault(normalise_title(row["title"]), []).append(
                    (row["number"], row["declarations"], row["wikidata"]))
        if library_metadata:
            data = _read_structured(library_metadata)
            libs = data.get("libraries") if isinstance(data, dict) else None
            if not isinstance(libs, dict):
                raise CfError(f"{library_metadata}: expected {{\"libraries\": {{<id>: [...]}}}}")
            for lid, results in sorted(libs.items()):
                for result in results or []:
                    if isinstance(result, dict):
                        self.metadata.append((str(lid), result))

    @property
    def names_mathlib(self) -> bool:
        return bool(self.by_qid_1000 or self.by_qid_100 or self.by_title_100)

    def resolve_library(self, universe: inputs.Universe) -> str | None:
        """The library Mathlib's lists name, or a refusal when they need one and none fits."""
        if not self.names_mathlib:
            return self.curated_library
        if self.curated_library:
            universe.library(self.curated_library)
            return self.curated_library
        lib = universe.mathlib_library()
        if lib is None:
            raise CfError(f"universe {universe.id} has no Mathlib library for the curated lists "
                          f"to name", hint="pass --curated-library <id>")
        return str(lib["id"])


def _hit(library: str, declaration: str, via: str, evidence: str) -> dict:
    return {"library": library, "declaration": declaration, "via": via, "evidence": evidence}


def url_index(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """``{canonical page URL: [tag rows]}`` of every docstring Wikipedia URL, read once."""
    index: dict[str, list[dict]] = {}
    for row in query.tags_of_kind(conn, "wikipedia"):
        index.setdefault(str(row["value"]), []).append(row)
    return index


def curated_hits(entry: dict, universe: inputs.Universe, conn: sqlite3.Connection,
                 lists: CuratedLists, site: str, urls: dict[str, list[dict]] | None = None,
                 curated_library: str | None = None) -> tuple[list[dict], list[dict]]:
    """``(curated, dropped)`` for one source: every join, each checked in the exact table.

    The joins key on the article's QID and its title; a title join whose entry
    names another QID is dropped by name, not joined.
    """
    qid = str(entry.get("wikidata") or "")
    title = normalise_title(entry.get("title") or "")
    raw: list[dict] = []
    refused: list[dict] = []
    lib_100 = curated_library
    if qid:
        for row in query.tagged(conn, "wikidata", qid):
            raw.append(_hit(row["library"], row["declaration"], "wikidata_tag",
                            f"@[wikidata {qid}] at {row['path']}:{row['line']}"))
        for key, names in lists.by_qid_1000.get(qid, []):
            for name in names:
                raw.append(_hit(lib_100, name, "mathlib_1000", f"1000.yaml {key}"))
        for number, names in lists.by_qid_100.get(qid, []):
            for name in names:
                raw.append(_hit(lib_100, name, "mathlib_100", f"100.yaml #{number} (wikidata)"))
    for number, names, own in lists.by_title_100.get(title, []) if title else []:
        for name in names:
            hit = _hit(lib_100, name, "mathlib_100", f"100.yaml #{number} (title)")
            if own and qid and own != qid:
                refused.append({**hit, "reason": f"the entry names another QID ({own})"})
            else:
                raw.append(hit)
    url = article_url(site, entry.get("title") or "")
    urls = url_index(conn) if urls is None else urls
    for row in urls.get(url or "", []):
        raw.append(_hit(row["library"], row["declaration"], "docstring_url",
                        f"{row['value']} in the docstring at {row['path']}:{row['line']}"))
    for lid, result in lists.metadata:
        own = str(result.get("wikidata") or "")
        by_qid = bool(qid) and own == qid
        by_title = bool(title) and normalise_title(result.get("title") or "") == title
        if not (by_qid or by_title):
            continue
        for name in _decl_names(result):
            hit = _hit(lid, name, "library_metadata",
                       f"main result of {lid}: {result.get('title') or qid}")
            if not by_qid and own and qid:
                refused.append({**hit, "reason": f"the entry names another QID ({own})"})
            else:
                raw.append(hit)
    curated: list[dict] = []
    dropped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in raw:
        key = (str(hit["library"]), hit["declaration"], hit["via"])
        if key in seen:
            continue
        seen.add(key)
        if hit["library"] not in universe.libraries:
            dropped.append({**hit, "reason": f"library {hit['library']!r} is not in universe "
                                              f"{universe.id}"})
            continue
        if not query.exists(conn, hit["library"], universe.commit(hit["library"]),
                            hit["declaration"]):
            dropped.append({**hit, "reason": "no such declaration at the universe's commit"})
            continue
        curated.append(hit)
    dropped.extend(refused)
    curated.sort(key=lambda h: (h["library"], h["declaration"], h["via"]))
    dropped.sort(key=lambda h: (str(h["library"]), h["declaration"], h["via"]))
    return curated, dropped


def source_terms(entry: dict, view: inputs.SourceView) -> list[tuple[str, str]]:
    """``[(term, matched)]``: the title and the lead's bold terms, deduplicated.

    Only a ``bold_definition`` finder in the lead (``section == []``) counts: a
    bolded word further down -- a theorem box's label, a term defined in passing
    -- is not what the article is about.
    """
    out: list[tuple[str, str]] = []
    if entry.get("title"):
        out.append((str(entry["title"]), "title"))
    for finder in view.finders():
        if (finder.get("finder") == vocab.FINDER_BOLD_DEFINITION and finder.get("term")
                and finder.get("section") == []):
            out.append((str(finder["term"]), "bold_term"))
    seen: set[str] = set()
    unique = []
    for term, matched in out:
        key = normalise_title(term)
        if key and key not in seen:
            seen.add(key)
            unique.append((term, matched))
    return unique


def lexical_hits(terms: list[tuple[str, str]], universe: inputs.Universe,
                 conn: sqlite3.Connection, *, limit: int, exclude: set[tuple[str, str]]
                 ) -> list[dict]:
    """The host tool's search over every term: all words first, any word only to fill up."""
    best: dict[tuple[str, str], dict] = {}
    for all_words in (True, False):
        for term, matched in terms:
            for row in query.search(conn, term, limit=limit, all_words=all_words):
                key = (row["library"], row["declaration"])
                if key in exclude or row["library"] not in universe.libraries:
                    continue
                score = float(row["score"]) if all_words else round(float(row["score"]) / 2, 4)
                if key not in best or score > best[key]["score"]:
                    best[key] = {"library": row["library"], "declaration": row["declaration"],
                                 "score": score, "matched": matched}
        if len(best) >= limit:
            break
    ranked = sorted(best.values(), key=lambda h: (-h["score"], h["library"], h["declaration"]))
    return ranked[:limit]


def build(entry: dict, view: inputs.SourceView, universe: inputs.Universe,
          conn: sqlite3.Connection, lists: CuratedLists, *, site: str, db: dict,
          blind: bool = False, limit: int = DEFAULT_LIMIT,
          urls: dict[str, list[dict]] | None = None,
          curated_library: str | None = None) -> dict:
    """The candidates document of one source (references/locate-documents.md section 5)."""
    terms = source_terms(entry, view)
    if blind:
        curated, dropped = [], []       # no join is computed: none may shape the lexical list
    else:
        curated, dropped = curated_hits(entry, universe, conn, lists, site, urls,
                                        curated_library)
    exclude = {(h["library"], h["declaration"]) for h in curated}
    lexical = lexical_hits(terms, universe, conn, limit=limit, exclude=exclude)
    return {
        "schema": SCHEMA,
        "source_id": entry["source_id"],
        "source_sha": entry.get("html_sha256"),
        "universe": universe.id,
        "universe_sha256": universe.sha256,
        "corpus_db": {"kind": db["kind"], "sha256": db["sha256"]},
        "article_qid": None if blind else entry.get("wikidata"),
        "article_title": entry.get("title"),
        "terms": [t for t, _ in terms],
        "blind": bool(blind),
        "curated": curated,
        "dropped": dropped,
        "lexical": lexical,
    }


def run(paths: WorkspacePaths, sourceset_id: str, universe_id: str, *,
        source_ids: Iterable[str] = (), version: int | None = None,
        lists: CuratedLists | None = None, blind: bool = False,
        planted: Path | None = None, limit: int = DEFAULT_LIMIT, workspace=None) -> dict:
    """Write one candidates file per rendered source; return the counts."""
    manifest = inputs.manifest(paths, sourceset_id, version)
    universe = inputs.load_universe(paths, universe_id)
    fam = websource.family(manifest.family, workspace)
    wanted = list(dict.fromkeys(source_ids)) or sorted(manifest.entries)
    unknown = [s for s in wanted if s not in manifest.entries]
    if unknown:
        raise CfError(f"not in sourceset {sourceset_id} version {manifest.version}: "
                      + ", ".join(unknown[:5]))
    lists = lists or CuratedLists()
    curated_library = None if blind else lists.resolve_library(universe)
    db = inputs.tools_database(paths, universe, blind=blind, planted=planted)
    out_dir = inputs.candidates_dir(paths, sourceset_id, universe_id, db)
    written: list[str] = []
    skipped: list[str] = []
    counts = {"curated": 0, "lexical": 0, "dropped": 0}
    conn = inputs.open_tools(db, universe)
    try:
        urls = url_index(conn)
        for sid in wanted:
            entry = manifest.entries[sid]
            if not inputs.rendered(entry):
                if source_ids:
                    raise CfError(f"source {sid} is not rendered in version {manifest.version}",
                                  hint=f"render it: `cf sourceset render {sourceset_id} "
                                       f"--source {sid}`")
                skipped.append(sid)
                continue
            inputs.check_source(paths, sourceset_id, entry)
            view = inputs.source_view(paths, sourceset_id, sid)
            doc = build(entry, view, universe, conn, lists, site=fam.site, db=db, blind=blind,
                        limit=limit, urls=urls, curated_library=curated_library)
            write_json(out_dir / f"{sid}.json", doc, sort_keys=True)
            written.append(sid)
            counts["curated"] += len(doc["curated"])
            counts["lexical"] += len(doc["lexical"])
            counts["dropped"] += len(doc["dropped"])
    finally:
        conn.close()
    return {"sourceset": manifest.pin(), "universe": universe.pin(), "blind": bool(blind),
            "corpus_db": {"kind": db["kind"], "sha256": db["sha256"]}, "dir": str(out_dir),
            "written": written, "skipped_unrendered": skipped, **counts}
