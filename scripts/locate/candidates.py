"""L2: the deterministic candidates of one web source, for one universe.

New code, implementing docs/proposals/wikipedia-locate.md section 3 (L2) in the
shape of references/locate-documents.md section 5.  Two kinds of hit, kept
apart because only one of them binds gate rule 4:

* **curated** -- a deterministic join that says "this declaration is about
  this article": a library's ``@[wikidata]`` tag on the article's own QID
  (``wikidata_tag``), Mathlib's QID-keyed ``1000.yaml`` (``mathlib_1000``) and
  its numbered ``100.yaml`` matched by normalised title (``mathlib_100``), a
  Wikipedia URL in a docstring naming the article (``docstring_url``), and a
  library's own list of main results (``library_metadata``).  Every curated
  declaration is checked in ``corpus.sqlite``'s exact table at the universe's
  commit; one that is not there is dropped and NAMED in ``dropped``, never kept
  as a phantom the gate would later refuse a worker for not locating.
* **lexical** -- full-text retrieval over the article's terms: its title, its
  aliases where the sourceset knows them, and the lead's bold terms
  (``finders.json``'s ``bold_definition`` terms).  A hint for the worker, never
  an obligation.

In the G1-blind arm (``blind``) ``curated`` is empty and the file says so, so
the gate turns rule 4 off for that source (proposal section 8.1).

The library-metadata input is a JSON or YAML file of this shape, one block per
universe library (a FormalFrontier ``formalization.yaml`` is converted into it
by whoever holds one)::

    {"libraries": {"alpha": [{"wikidata": "Q99999001", "title": "Widget lemma",
                              "declarations": ["Widget.exists_spanning_gadget"]}]}}
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from cflib import registry as registry_mod, vocab
from cflib.errors import CfError
from cflib.jsonio import write_json
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml

from . import inputs

SCHEMA = "cf-locate-candidates/1"
#: lexical hits kept per source by default
DEFAULT_LIMIT = 20


def normalise_title(text: str) -> str:
    """A title for matching only: NFKC, underscores and runs of space to one, case-folded."""
    text = unicodedata.normalize("NFKC", str(text or "")).replace("_", " ")
    text = re.sub(r"[\s]+", " ", text).strip().casefold()
    return re.sub(r"^the ", "", text)


def _wiki_title(url: str, site: str) -> str | None:
    """The page title a Wikipedia URL of ``site`` names, or None for any other URL."""
    try:
        parsed = urllib.parse.urlsplit(str(url).strip())
    except ValueError:
        return None
    host = urllib.parse.urlsplit(site).netloc if site else ""
    if parsed.scheme not in ("http", "https") or (host and parsed.netloc != host):
        return None
    if parsed.path.startswith("/wiki/"):
        return urllib.parse.unquote(parsed.path[len("/wiki/"):])
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("title"):
        return query["title"][0]
    return None


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
    """The curated inputs one ``cf locate candidates`` call was given, read once."""

    def __init__(self, *, list_1000: Path | None = None, list_100: Path | None = None,
                 library_metadata: Path | None = None, curated_library: str = "mathlib"):
        self.curated_library = curated_library
        self.by_qid_1000: dict[str, list[str]] = {}
        self.by_title_100: dict[str, list[tuple[str, list[str]]]] = {}
        self.metadata: list[tuple[str, dict]] = []
        if list_1000:
            data = _read_structured(list_1000)
            if not isinstance(data, dict):
                raise CfError(f"{list_1000}: a 1000.yaml-shaped file maps a QID to an entry")
            for qid, entry in data.items():
                if isinstance(entry, dict):
                    self.by_qid_1000[str(qid)] = _decl_names(entry)
        if list_100:
            data = _read_structured(list_100)
            if not isinstance(data, dict):
                raise CfError(f"{list_100}: a 100.yaml-shaped file maps a number to an entry")
            for number, entry in data.items():
                if isinstance(entry, dict) and entry.get("title"):
                    key = normalise_title(entry["title"])
                    self.by_title_100.setdefault(key, []).append(
                        (str(number), _decl_names(entry)))
                    if entry.get("wikidata"):
                        self.by_title_100.setdefault(str(entry["wikidata"]), []).append(
                            (str(number), _decl_names(entry)))
        if library_metadata:
            data = _read_structured(library_metadata)
            libs = data.get("libraries") if isinstance(data, dict) else None
            if not isinstance(libs, dict):
                raise CfError(f"{library_metadata}: expected {{\"libraries\": {{<id>: [...]}}}}")
            for lid, results in sorted(libs.items()):
                for result in results or []:
                    if isinstance(result, dict):
                        self.metadata.append((str(lid), result))


def _hit(library: str, declaration: str, via: str, evidence: str) -> dict:
    return {"library": library, "declaration": declaration, "via": via, "evidence": evidence}


def url_index(db: inputs.CorpusDB, site: str) -> dict[str, list[dict]]:
    """``{normalised page title: [declaration rows]}`` of every docstring Wikipedia URL, once."""
    index: dict[str, list[dict]] = {}
    for row in db.tags_of_kind("wikipedia"):
        named = _wiki_title(row["tag_value"], site)
        if named is not None:
            index.setdefault(normalise_title(named), []).append(row)
    return index


def curated_hits(entry: dict, terms: list[str], universe: inputs.Universe,
                 db: inputs.CorpusDB, lists: CuratedLists, site: str,
                 urls: dict[str, list[dict]] | None = None) -> tuple[list[dict], list[dict]]:
    """``(curated, dropped)`` for one source: every join, each checked in the exact table."""
    qid = str(entry.get("wikidata") or "")
    titles = {normalise_title(t) for t in terms if t}
    raw: list[dict] = []
    if qid:
        for row in db.tagged("wikidata", qid):
            if row["library"] in universe.libraries:
                raw.append(_hit(row["library"], row["name"], "wikidata_tag",
                                f"@[wikidata {qid}] at {row['path']}:{row['line']}"))
        for name in lists.by_qid_1000.get(qid, []):
            raw.append(_hit(lists.curated_library, name, "mathlib_1000",
                            f"1000.yaml {qid}"))
        for number, names in lists.by_title_100.get(qid, []):
            for name in names:
                raw.append(_hit(lists.curated_library, name, "mathlib_100",
                                f"100.yaml #{number} (wikidata)"))
    for title in sorted(titles):
        for number, names in lists.by_title_100.get(title, []):
            for name in names:
                raw.append(_hit(lists.curated_library, name, "mathlib_100",
                                f"100.yaml #{number} (title)"))
    urls = url_index(db, site) if urls is None else urls
    for title in sorted(titles):
        for row in urls.get(title, []):
            if row["library"] in universe.libraries:
                raw.append(_hit(row["library"], row["name"], "docstring_url",
                                f"{row['tag_value']} in the docstring at "
                                f"{row['path']}:{row['line']}"))
    for lid, result in lists.metadata:
        if (qid and str(result.get("wikidata") or "") == qid) or \
                normalise_title(result.get("title") or "") in titles - {""}:
            for name in _decl_names(result):
                raw.append(_hit(lid, name, "library_metadata",
                                f"main result of {lid}: {result.get('title') or qid}"))
    curated: list[dict] = []
    dropped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in raw:
        key = (hit["library"], hit["declaration"], hit["via"])
        if key in seen:
            continue
        seen.add(key)
        if hit["library"] not in universe.libraries:
            dropped.append({**hit, "reason": f"library {hit['library']!r} is not in universe "
                                              f"{universe.id}"})
            continue
        if db.decl(hit["library"], universe.commit(hit["library"]), hit["declaration"]) is None:
            dropped.append({**hit, "reason": "no such declaration at the universe's commit"})
            continue
        curated.append(hit)
    curated.sort(key=lambda h: (h["library"], h["declaration"], h["via"]))
    dropped.sort(key=lambda h: (h["library"], h["declaration"], h["via"]))
    return curated, dropped


def source_terms(entry: dict, view: inputs.SourceView) -> list[tuple[str, str]]:
    """``[(term, matched)]``: the title, the aliases, the lead's bold terms, deduplicated."""
    out: list[tuple[str, str]] = []
    if entry.get("title"):
        out.append((str(entry["title"]), "title"))
    for alias in entry.get("aliases") or []:
        out.append((str(alias), "alias"))
    for finder in view.finders():
        if finder.get("finder") == vocab.FINDER_BOLD_DEFINITION and finder.get("term"):
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
                 db: inputs.CorpusDB, *, limit: int, exclude: set[tuple[str, str]]
                 ) -> list[dict]:
    """Full-text hits over every term, every word first and any word only to fill up."""
    best: dict[tuple[str, str], dict] = {}
    for any_word in (False, True):
        for term, matched in terms:
            for row in db.search(inputs.fts_query(term, any_word=any_word), limit=limit):
                key = (row["library"], row["name"])
                if key in exclude or row["library"] not in universe.libraries \
                        or row["commit_sha"] != universe.commit(row["library"]):
                    continue
                score = row["score"] if not any_word else round(row["score"] / 2, 4)
                if key not in best or score > best[key]["score"]:
                    best[key] = {"library": row["library"], "declaration": row["name"],
                                 "score": score, "matched": matched}
        if len(best) >= limit:
            break
    ranked = sorted(best.values(), key=lambda h: (-h["score"], h["library"], h["declaration"]))
    return ranked[:limit]


def build(entry: dict, view: inputs.SourceView, universe: inputs.Universe,
          db: inputs.CorpusDB, lists: CuratedLists, *, site: str, blind: bool = False,
          limit: int = DEFAULT_LIMIT, urls: dict[str, list[dict]] | None = None) -> dict:
    """The candidates document of one source (references/locate-documents.md section 5)."""
    terms = source_terms(entry, view)
    curated, dropped = curated_hits(entry, [t for t, _ in terms], universe, db, lists, site,
                                    urls)
    exclude = {(h["library"], h["declaration"]) for h in curated}
    lexical = lexical_hits(terms, universe, db, limit=limit, exclude=exclude)
    doc = {
        "schema": SCHEMA,
        "source_id": entry["source_id"],
        "source_sha": entry.get("html_sha256"),
        "universe": universe.id,
        "universe_sha256": universe.sha256,
        "article_qid": entry.get("wikidata"),
        "article_title": entry.get("title"),
        "terms": [t for t, _ in terms],
        "blind": bool(blind),
        "curated": [] if blind else curated,
        "dropped": [] if blind else dropped,
        "lexical": lexical,
    }
    return doc


def run(paths: WorkspacePaths, sourceset_id: str, universe_id: str, *,
        source_ids: Iterable[str] = (), version: int | None = None,
        lists: CuratedLists | None = None, blind: bool = False,
        limit: int = DEFAULT_LIMIT, workspace=None) -> dict:
    """Write one candidates file per rendered source; return the counts."""
    manifest = inputs.load_manifest(paths, sourceset_id, version)
    universe = inputs.load_universe(paths, universe_id)
    reg = registry_mod.Registry.load(workspace)
    wanted = list(source_ids) or sorted(manifest.entries)
    unknown = [s for s in wanted if s not in manifest.entries]
    if unknown:
        raise CfError(f"not in sourceset {sourceset_id} version {manifest.version}: "
                      + ", ".join(unknown[:5]))
    lists = lists or CuratedLists()
    written: list[str] = []
    skipped: list[str] = []
    counts = {"curated": 0, "lexical": 0, "dropped": 0}
    with inputs.CorpusDB(paths.corpus_db(universe_id)) as db:
        if db.meta.get("universe_sha256") and db.meta["universe_sha256"] != universe.sha256:
            raise CfError(f"corpus.sqlite of {universe_id} was built for another universe "
                          f"({db.meta['universe_sha256'][:12]}), not {universe.sha256[:12]}",
                          hint="rebuild corpus.sqlite after the universe changed")
        url_indexes: dict[str, dict[str, list[dict]]] = {}
        for sid in wanted:
            if not manifest.rendered(sid):
                if source_ids:
                    raise CfError(f"source {sid} is not rendered in version {manifest.version}",
                                  hint="the sources stage renders it first")
                skipped.append(sid)
                continue
            entry = manifest.entries[sid]
            family = reg.family(str(entry.get("family") or ""))
            site = family.site if family is not None else ""
            view = inputs.source_view(paths, sourceset_id, sid)
            if site not in url_indexes:
                url_indexes[site] = url_index(db, site)
            doc = build(entry, view, universe, db, lists, site=site, blind=blind, limit=limit,
                        urls=url_indexes[site])
            write_json(paths.candidates_json(sourceset_id, universe_id, sid), doc,
                       sort_keys=True)
            written.append(sid)
            counts["curated"] += len(doc["curated"])
            counts["lexical"] += len(doc["lexical"])
            counts["dropped"] += len(doc["dropped"])
    return {"sourceset": manifest.pin(), "universe": universe.pin(), "blind": bool(blind),
            "written": written, "skipped_unrendered": skipped, **counts}
