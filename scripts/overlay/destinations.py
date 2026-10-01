#!/usr/bin/env python3
"""Declaration destinations from a built Verso literate site.

New code, implementing section 7 of the overlay contract
(`docs/overlay.md`, seeded as the overlay template's SPEC.md) and the format's
``destinations[]`` records (docs/annotations.md section 8.2).  The overlay
viewer puts a "where is this rendered" link on every declaration card; this is
what writes those links, and it writes them from the site that was actually
built rather than from a table somebody keeps by hand.

The literate genre renders one page per Lean module and gives a declaration an
anchor whose id is its fully qualified name with each ``.`` replaced by
``___``.  So for every declaration of the document:

* the module page is ``<Module/Path>/index.html`` -- the declaration's own
  ``module``, never a path parsed out of prose;
* the anchor is the declaration's ``name`` with ``.`` replaced by ``___``;
* the destination is ``exact`` (``href`` plus ``fragment``) only when that
  anchor id really occurs, once, in that page's bytes;
* otherwise it is a ``fallback`` to the module page, or to the site index when
  the module has no page at all, carrying a ``fallback_label`` the viewer shows
  the reader -- so a link that lands somewhere less precise than the
  declaration says so on the card instead of looking exact.

An ``example`` or an anonymous ``instance`` has no name, so it has no anchor and
takes the module-page fallback.  Nothing here writes to the site: the pages are
read, and a page whose derived path leaves the site root is refused.

Run it by path::

    python3 scripts/overlay/destinations.py --site .lake/build/literate-html \\
        --doc pdf-overlay/annotations.json --out fragment.json
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V              # noqa: E402
from formal_companion_annotations import vocab                      # noqa: E402
from cflib.errors import CfError                                    # noqa: E402
from cflib.jsonio import atomic_write                               # noqa: E402

#: The separator the literate genre writes in place of a dot in an anchor id.
ANCHOR_SEP = "___"
#: The site kind these destinations are on.
DEFAULT_SITE = "verso_literate"
#: The page a fallback lands on when a module has no page of its own.
DEFAULT_INDEX = "index.html"

#: ``site_path``: no leading slash or ``#``, no ``..`` segment, no whitespace,
#: and no colon (so a path never reads as a URL scheme).  The format's own
#: grammar, which the viewer's href check repeats.
SITE_PATH = re.compile(r"^(?![/#])(?!.*(^|/)\.\.(/|$))[^\s#:]+$")
#: A fragment the format accepts: no whitespace and no ``#``.
FRAGMENT = re.compile(r"^[^\s#]+$")

#: What each fallback tells the reader, by why it is a fallback.
FALLBACK_LABELS = {
    "no_page": "site index (the module has no page in this build)",
    "anonymous": "module page (an example or anonymous instance is given no anchor)",
    "absent": "module page (the anchor is not on the page)",
    "ambiguous": "module page (the anchor occurs more than once on the page)",
}


def module_page(module: str) -> str:
    """``Notes.Box`` -> ``Notes/Box/index.html``: the literate page of one module."""
    if not str(module or "").strip():
        raise CfError("a declaration with no module has no page to link to",
                      hint="every declaration record carries its module")
    page = module.replace(".", "/") + "/index.html"
    if not SITE_PATH.match(page):
        raise CfError(f"module {module!r} gives the site path {page!r}, which is not "
                      f"relative to the site root",
                      hint="a module name is dotted identifiers; this one is not")
    return page


def anchor_id(name: str) -> str:
    """``Notes.Box.volume_nonneg`` -> ``Notes___Box___volume_nonneg``."""
    anchor = str(name or "").replace(".", ANCHOR_SEP)
    if not anchor or not FRAGMENT.match(anchor):
        raise CfError(f"{name!r} gives no usable anchor id",
                      hint="a declaration name carries no whitespace and no '#'")
    return anchor


def anchor_count(html: str, anchor: str) -> int:
    """How often ``html`` carries ``anchor`` as an element id.

    Both quotings a renderer may write (``id="x"`` and ``id='x'``) count; a
    substring of a longer id does not, which is what ``in`` would have counted.
    """
    pattern = re.compile(r"""\bid\s*=\s*(?:"%s"|'%s')""" % (re.escape(anchor), re.escape(anchor)))
    return len(pattern.findall(html))


def build_destinations(site_dir: Path | str, doc: dict, *, site: str = DEFAULT_SITE,
                       index: str = DEFAULT_INDEX) -> dict:
    """``{"destinations": [...]}``: one record per declaration of ``doc``.

    ``site_dir`` is the built site's output root, read-only.  A declaration
    whose anchor the page really carries gets an ``exact`` destination; every
    other one gets a ``fallback`` with a visible label.
    """
    root = Path(site_dir)
    if not root.is_dir():
        raise CfError(f"{root} is not a directory",
                      hint="--site names the built site's output root")
    if site not in vocab.ANNOTATION_DESTINATION_SITES:
        raise CfError(f"{site!r} is not a destination site kind",
                      hint="--site-name " + "|".join(sorted(vocab.ANNOTATION_DESTINATION_SITES)))
    if not SITE_PATH.match(index):
        raise CfError(f"{index!r} is not a path relative to the site root",
                      hint="--index index.html")
    if not (root / index).is_file():
        raise CfError(f"{root / index} does not exist",
                      hint="build the site first; --index names its entry page")

    declarations = doc.get("declarations") or []
    if not isinstance(declarations, list):
        raise CfError("the document's declarations[] is not an array",
                      hint="--doc names an annotation document, format 1.0")
    cache: dict[str, str | None] = {}
    out: list[dict] = []
    for decl in declarations:
        if not isinstance(decl, dict) or "id" not in decl:
            raise CfError("a declarations[] entry has no id",
                          hint="--doc names an annotation document, format 1.0")
        page = module_page(decl.get("module", ""))
        if page not in cache:
            target = root / page
            cache[page] = (target.read_text(encoding="utf-8", errors="replace")
                           if target.is_file() else None)
        html = cache[page]
        record = {"id": f"{site}:{decl['id']}", "declaration": decl["id"], "site": site}
        name = decl.get("name")
        if html is None:
            record.update(href=index, resolution="fallback",
                          fallback_label=FALLBACK_LABELS["no_page"])
        elif not name:
            record.update(href=page, resolution="fallback",
                          fallback_label=FALLBACK_LABELS["anonymous"])
        else:
            anchor = anchor_id(name)
            found = anchor_count(html, anchor)
            if found == 1:
                record.update(href=page, fragment=anchor, resolution="exact")
            else:
                why = "absent" if found == 0 else "ambiguous"
                record.update(href=page, resolution="fallback",
                              fallback_label=FALLBACK_LABELS[why])
        out.append(record)
    seen: dict[str, str] = {}
    for record in out:
        prior = seen.setdefault(record["id"], record["declaration"])
        if prior != record["declaration"]:
            raise CfError(f"two declarations give the destination id {record['id']!r}")
    return {"destinations": sorted(out, key=lambda r: V.order_key(r["id"]))}


def load_document(path: Path) -> dict:
    """The annotation document at ``path``, parsed strictly."""
    if not path.is_file():
        raise CfError(f"{path} is not a file", hint="--doc names the annotation document")
    try:
        doc = V.loads(path.read_text(encoding="utf-8"))
    except (V.LoadError, UnicodeDecodeError) as exc:
        raise CfError(f"{path} is not a readable annotation document: {exc}") from exc
    if not isinstance(doc, dict):
        raise CfError(f"{path} is not a JSON object",
                      hint="--doc names an annotation document, format 1.0")
    if doc.get("schema") != vocab.ANNOTATION_SCHEMA:
        raise CfError(f"{path} declares schema {doc.get('schema')!r}, not "
                      f"{vocab.ANNOTATION_SCHEMA!r}")
    return doc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="destinations.py",
        description="Declaration destinations from a built Verso literate site.")
    parser.add_argument("--site", type=Path, required=True, help="the built site's output root")
    parser.add_argument("--doc", type=Path, required=True, help="the annotation document")
    parser.add_argument("--site-name", default=DEFAULT_SITE,
                        choices=sorted(vocab.ANNOTATION_DESTINATION_SITES),
                        help="the destination site kind")
    parser.add_argument("--index", default=DEFAULT_INDEX,
                        help="the site's entry page, which a module with no page falls back to")
    parser.add_argument("--out", type=Path, required=True, help="the fragment to write")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        doc = load_document(args.doc)
        fragment = build_destinations(args.site, doc, site=args.site_name, index=args.index)
    except CfError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    atomic_write(args.out, V.canonical_dumps(V.canonicalize(fragment)))
    kinds: dict[str, int] = {}
    for record in fragment["destinations"]:
        kinds[record["resolution"]] = kinds.get(record["resolution"], 0) + 1
    counts = ", ".join(f"{n} {name}" for name, n in sorted(kinds.items()))
    print(f"wrote {args.out} ({len(fragment['destinations'])} destinations"
          + (f": {counts}" if counts else "") + ")")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
