"""The checkpointed annotation document of a pipeline: build, cap, write, check.

New code for milestone M1 of docs/proposals/pdf-to-companion.md (sections 4.1,
4.2 and 4.6).  Every stage writes ``pipelines/<id>/doc.vN.json`` once, and
every checkpoint must pass ``formal_companion_annotations.validate --canonical``
with no warnings (section 3.3); :func:`write_checkpoint` is the one place that
serialises a checkpoint, so a stage cannot write one that fails the checker.

Section 4.6 caps a selector's ``exact`` text at a fixed number of words before
any id is minted: the document ships with the companion, and full statement
text stays in the workspace.  :data:`EXACT_WORD_CAP` is that number and
:func:`cap_words` applies it; :func:`over_cap` is the re-check S8 will run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Iterator

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids
from formal_companion_annotations import validate as annotations_validate
from formal_companion_annotations import vocab
from annotations.export.common import declare_profiles
from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import atomic_write

#: the most words a TextQuoteSelector's ``exact`` (or an item's ``quote.exact``)
#: may carry in a pipeline document (proposal section 4.6)
EXACT_WORD_CAP = 12
#: characters of context kept on each side of an ``exact`` (prefix, suffix)
CONTEXT_CHARS = 32

PDF_FRAGMENT = "http://tools.ietf.org/rfc/rfc8118"

#: how quotes on a PDF source are matched: NFKC, whitespace collapsed, case kept
#: (docs/annotations.md section 4.6; ``ids.normalize_text`` is the normaliser)
TEXT_LAYER_MATCH = {"unicode": "nfkc", "whitespace": "collapse", "case": "sensitive"}


def normalize(text: str) -> str:
    """The text-layer normalisation: NFKC, whitespace runs to one space, ends stripped."""
    return ids.normalize_text(text or "")


def cap_words(text: str, cap: int = EXACT_WORD_CAP) -> str:
    """The first ``cap`` words of the normalised ``text``."""
    return " ".join(normalize(text).split(" ")[:cap]) if normalize(text) else ""


def word_count(text: str) -> int:
    norm = normalize(text)
    return len(norm.split(" ")) if norm else 0


def new_document(companion: dict, sources: list[dict], provenance: list[dict]) -> dict:
    """An empty 1.0 document with its header, sources and provenance.

    The root ``profiles`` is written by :func:`write_checkpoint`, from the keys
    the finished document holds.  Nothing the stages write is profile-owned
    (a source's ``role`` is core), so a checkpoint is core only and carries
    no ``profiles``.
    """
    return {
        "schema": vocab.ANNOTATION_SCHEMA,
        "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
        "companion": dict(companion),
        "sources": list(sources),
        "items": [],
        "declarations": [],
        "links": [],
        "provenance": list(provenance),
    }


def page_quote_location(item_id: str, source_id: str, page: int, exact: str,
                        prefix: str, suffix: str, provenance: str) -> dict:
    """``<item>@pNNN``: a physical page refined by a TextQuoteSelector."""
    quote: dict[str, Any] = {"type": "TextQuoteSelector", "exact": exact}
    if prefix:
        quote["prefix"] = prefix
    if suffix:
        quote["suffix"] = suffix
    return {
        "id": f"{item_id}@p{page:03d}",
        "items": [item_id],
        "pages": {"first": page, "basis": "physical"},
        "provenance": provenance,
        "target": {"source": source_id, "selector": [
            {"type": "FragmentSelector", "conformsTo": PDF_FRAGMENT, "value": f"page={page}",
             "refinedBy": quote}]},
    }


def quote_in_context(page_text: str, exact: str, nth: int = 0) -> tuple[str, str] | None:
    """``(prefix, suffix)`` around the ``nth`` occurrence of ``exact`` in the normalised page."""
    norm = normalize(page_text)
    start = -1
    for _ in range(nth + 1):
        start = norm.find(exact, start + 1)
        if start < 0:
            return None
    end = start + len(exact)
    return norm[max(0, start - CONTEXT_CHARS):start], norm[end:end + CONTEXT_CHARS]


def page_quotes(doc: dict) -> Iterator[tuple[dict, str, int, dict]]:
    """``(location, source id, page, TextQuoteSelector)`` for every page-bound quote.

    A quote is page-bound when it refines a ``page=N`` fragment, directly or
    further down the ``refinedBy`` chain; top-level quotes have no page.
    """
    for loc in doc.get("locations") or []:
        source = (loc.get("target") or {}).get("source")
        for alt in (loc.get("target") or {}).get("selector") or []:
            page = None
            node = alt
            while isinstance(node, dict):
                if node.get("type") == "FragmentSelector":
                    value = str(node.get("value") or "")
                    if value.startswith("page="):
                        try:
                            page = int(value.split("&", 1)[0][len("page="):])
                        except ValueError:
                            page = None
                elif node.get("type") == "TextQuoteSelector" and page is not None:
                    yield loc, source, page, node
                node = node.get("refinedBy")


def over_cap(doc: dict, cap: int = EXACT_WORD_CAP) -> list[str]:
    """Every ``exact`` (selector or item quote) longer than ``cap`` words."""
    problems = []
    for loc, _source, _page, quote in page_quotes(doc):
        if word_count(quote["exact"]) > cap:
            problems.append(f"location {loc['id']}: exact is {word_count(quote['exact'])} "
                            f"words (cap {cap})")
    for item in doc.get("items") or []:
        exact = (item.get("quote") or {}).get("exact")
        if exact and word_count(exact) > cap:
            problems.append(f"item {item['id']}: quote.exact is {word_count(exact)} words "
                            f"(cap {cap})")
    return problems


#: The lints a checkpoint must pass besides the schema and invariants.
LINTS = ("external-urls",)


def check(doc: dict) -> tuple[str, list[str], list[str]]:
    """``(canonical text, problems, warnings)`` for a document about to be written.

    Problems and warnings are the checker's lines, ``<path>: <message> [<code>]``.
    The document's ``profiles`` are declared first, from the keys it holds.
    """
    declare_profiles(doc)
    canonical_doc = annotations_validate.canonicalize(doc)
    text = annotations_validate.canonical_dumps(canonical_doc)
    report = annotations_validate.check(canonical_doc, text=text, canonical=True, lints=LINTS)
    return (text, [f"{p} [{p.code}]" for p in report.problems],
            [f"{w} [{w.code}]" for w in report.warnings])


def write_checkpoint(path: str | Path, doc: dict) -> str:
    """Write ``doc`` canonically and return the file's sha256.

    Refuses (and writes nothing) when the checker finds a problem or a warning:
    a checkpoint that ``formal_companion_annotations.validate --canonical
    --lint external-urls`` would reject is not a checkpoint.
    """
    text, problems, warnings = check(doc)
    if problems or warnings:
        shown = problems + [f"warning: {w}" for w in warnings]
        raise CfError(f"refusing to write {Path(path).name}: the document does not pass "
                      f"formal_companion_annotations.validate --canonical --lint external-urls: "
                      + "; ".join(shown[:10])
                      + (f" (and {len(shown) - 10} more)" if len(shown) > 10 else ""),
                      hint="this is a defect in the stage that built it; nothing was written")
    atomic_write(path, text)
    return sha256_file(path)


def sorted_ids(values: Iterable[str]) -> list[str]:
    """Id lists in the document's order (UTF-16 code units, docs/annotations.md section 7)."""
    return sorted(set(values), key=annotations_validate.order_key)
