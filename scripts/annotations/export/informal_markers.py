"""`cf export informal-markers`: an informalization companion's boxes as an annotation document.

New code, implementing section 10 of `docs/proposals/informalize.md`
(component 7 of its section 11) and ported from the pilot's
``produce_annotations.py``, which wrote Literature Links records itself.  Here
the convention is one more of `cf export`'s, so everything between the
document and the files on disk is the overlay's: `overlay.shards` converts,
names and writes them, and `overlay.export` records them in `GENERATED.json`.

What it reads is the output repository at a pinned commit -- the four files
`informal/informalize.json`, `items.json`, `declarations.json` and
`markers.json` -- the canonical PDF those markers were measured on, and the
library's source for the snippets a card shows.  What it writes is one
annotation per box:

* the **library** is the document's ``companion``, because `overlay.shards`
  makes every Lean end from ``companion.repo`` at ``companion.commit``; the
  output repository's own commit is the export's, which
  `overlay.export.export` records beside ``export.lean``;
* one item record per part, and per item without parts (an item with parts is
  the rectangle around them, and its own box would cover each part's): its id
  is ``ids.item_label_id``, its ``number`` the printed number, its ``sub`` the
  letter, and one location per box;
* one link per ``\\leanref`` the markers record in that box, with the role and
  the relation `items.json` gives the declaration, the declaration's keyword
  line as a destination on the library's git host, and its statement, cut by
  `adapters.leansnippets`, as a snippet;
* one **passage** record per notion box -- a ``\\fcnotion`` of the conventions,
  whose words the markers carry and whose Lean constant they carry beside them
  -- with the markers' own id (``notion:<constant>``, a declared id, so that
  rewording the notion does not re-key it) and, as its ``title``, the text the
  canonical PDF prints inside its boxes (`boxtext`, the box check's own
  cropper): the page shows ``B(x, rho)`` where the source says
  ``$\\ball{x}{\\rho}$``, and a card titled with the LaTeX would be a card no
  reader of the page recognises.  The reader holds the crop against the page's
  own glyphs, so the title carries the characters the page prints and not the
  ones a font's encoding spells them with: ``ℕ``, ``ℋᵢ``, ``Π₁(f)``,
  ``f ∘ γ``.  An item whose title is written with TeX
  markup is titled the same way, from the parenthesis its head prints; the
  fallback, for a PDF no box of can be read, is the words with their markup
  stripped, and the report names every title that took it.  Two narrower
  reports stand beside it: a script run Unicode has no characters for, left
  beside its base (``ιS``), and a box whose glyphs could not be read, whose
  title is pdftotext's flattened text.  The record is
  linked to the notion's constant with role ``key`` and relation ``exact``.  The
  informal text prints neither the name nor a documentation link (`STYLE.md`,
  "The conventions section"); this record is what supplies them.  A constant of
  the library is a declaration like any other.  A constant of a **dependency**
  (Mathlib's ``interior``, say) is a row of `declarations.json`'s ``references``,
  which carries its module and, from the library's `lake-manifest.json`, the
  package and the commit it is pinned at: its destination is its line on that
  dependency's git host at that commit when ``--packages-src`` holds the
  dependency's source to cut a snippet and a line from, and the documentation
  page the row names when it does not.  The library mirror
  (`cf informalize fetch-library`) carries the library's own `.lean` files and
  no package of ``.lake/packages/``, so without ``--packages-src`` the notion
  cards of a dependency show the documentation page and no statement, and the
  report says how many.

Nothing is written when an input disagrees with another: the markers must be
of this PDF and of this `informal/`, every declaration must be one
`declarations.json` holds, every key declaration must stand at the keyword
line it gives in the source under ``--library-src`` (the item table's own
check, since nothing else pins that directory to the library's commit), and
every box must fit its page.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

from adapters import leansnippets
from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids
from formal_companion_annotations import vocab

from . import boxtext
from . import common
from .common import ExportError, Report, file_lock

#: The directory of the output repository this convention reads, and the four files of it.
INFORMAL = "informal"
INPUTS = ("informalize.json", "items.json", "declarations.json", "markers.json")
#: An `items.json` relation as the legacy token `overlay.shards` turns back into it
#: (docs/proposals/informalize.md section 10).  The format's own tokens are
#: ``vocab.ANNOTATION_LINK_RELATIONS``; no token converts back to ``closeMatch``,
#: which is why the item table does not admit that relation.
RELATION_TOKENS = {"exactMatch": "exact", "broadMatch": "generalization",
                   "narrowMatch": "special_case", "relatedMatch": "related"}
#: A declaration's page is a blob view of the library's git host, which is none of the
#: rendered Lean sites the format names.
SITE = "other"
#: Where a dependency's constant is documented when its source is not on disk: the Lean
#: documentation site `scripts/informalize/scope.py` builds `doc_url` from.
DOC_SITE = "doc_gen4"
#: A DumpDeps kind (`scripts/detect/DumpDeps.lean.tmpl`) as the annotation format's.  A
#: constructor (`ctor`), a recursor (`rec`) or a quotient primitive (`quot`) is not a
#: notion the conventions fix, and the format would want a `parent` for the first two, so
#: a `\fcnotion` naming one is refused by name.
DEPENDENCY_KINDS = {"def": "def", "theorem": "theorem", "axiom": "axiom", "opaque": "opaque",
                    "inductive": "inductive"}
#: The one provenance record: the fleet wrote the text and its `\leanref`s together.
PROVENANCE_ID = "prov-informalize"
TOOL_NAME = "cf informalize"
#: The coordinate space of a `markers.json` box: fractions of the displayed page,
#: from its top left (section 5.6).  The box is the MediaBox: `extract_markers.py`
#: holds the page size against the `.aux`'s `\pdfpagewidth` and `\pdfpageheight`,
#: TeX's paper size, and the proof sheets render with `pdftoppm` without `-cropbox`.
COORDINATE_SPACE = {"origin": "top_left", "units": "normalized", "x_axis": "right",
                    "y_axis": "down", "page_box": "MediaBox"}
#: A box's coverage class: a key link states the whole of it, and a box whose
#: `\leanref`s are all helpers states none of it (invariant I5).
COVERAGE = "formalized"
COVERAGE_HELPERS_ONLY = "support_only"

_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _rounded(value: float) -> float:
    """A coordinate as the document carries it: a fraction, never past 1."""
    return min(1.0, max(0.0, round(float(value), 9)))


class _Export:
    """One export: the inputs, the records they make, and the report."""

    def __init__(self, checkout: Path, commit: str, *, pdf: Path, pdf_url: str,
                 library_src: Path, packages_src: Path | None, items_approved: bool,
                 name: str | None, repo_id: str | None,
                 site_name: str, source_id: str | None, source_title: str | None,
                 source_edition: str | None, page_offset: int | None) -> None:
        self.repo = checkout
        self.pdf = pdf
        self.pdf_url = pdf_url
        self.library_src = library_src
        self.packages_src = packages_src
        self.items_approved = items_approved
        self.site_name = site_name or SITE
        self.overrides = {"name": name, "repo_id": repo_id, "source_id": source_id,
                          "title": source_title, "edition": source_edition,
                          "offset": page_offset}
        self.report = Report()
        self._read = None
        self._read_package = None
        self.head, self.head_date = common.pinned_checkout(checkout, commit)
        self.read_inputs()
        self.declarations: dict[str, dict] = {}
        self.snippets: dict[str, dict] = {}
        self.destinations: dict[str, dict] = {}
        self.items: list[dict] = []
        self.locations: list[dict] = []
        self.links: list[dict] = []
        self.indexed: dict[str, dict] = {}
        self.indexed_packages: dict[str, dict] = {}
        self.notion_items: dict[str, str] = {}
        self.titles: dict[str, str | None] = {}
        self.keyword_checked: set[str] = set()
        self.source_tree = None
        self.box_text = boxtext.BoxText(self.pdf, self.runtime_config.boxes["res_dpi"])

    # -- the inputs -----------------------------------------------------------------
    def read_inputs(self) -> None:
        """The four files of `informal/`, each through the runtime's own reader where it has one."""
        # THIS checkout's runtime, never the output repository's copy of it: a companion's
        # copy of a tool is never run (docs/overlay.md section 6).  The import is here
        # rather than at the top so that `cf export` loads it with the convention alone.
        from informalize import runtime as informalize_runtime

        self.fc = informalize_runtime.fccommon()
        self.fcitems = informalize_runtime.fcitems()
        informal = self.repo / INFORMAL
        for rel in INPUTS:
            if not (informal / rel).is_file():
                raise ExportError(f"{INFORMAL}/{rel} is not a file of {self.repo}",
                                  hint="this convention reads an informalize output repository "
                                       "after its I6 stage (docs/proposals/informalize.md)")
        try:
            self.runtime_config = self.fc.load_config(informal)
            self.config = common.load_json(informal / "informalize.json")
            self.items_in = common.load_json(informal / "items.json")
            self.table = self.fc.load_items(informal / "items.json")
            self.decls_in = self.fc.load_declarations(informal / "declarations.json")
            self.refs_in = self.fc.load_references(informal / "declarations.json")
        except self.fc.ToolError as exc:
            raise ExportError(f"{INFORMAL}/: {exc}") from exc
        if self.table.problems:
            raise ExportError(f"{INFORMAL}/items.json: {self.table.problems[0]}",
                              hint="; ".join(self.table.problems[1:4])
                                   or "run `cf informalize check-items` on it")
        self.markers = common.load_json(informal / "markers.json")
        if self.markers.get("schema") != self.fc.MARKERS_SCHEMA:
            raise ExportError(f"{INFORMAL}/markers.json: schema is not {self.fc.MARKERS_SCHEMA}")
        self.library = self.config.get("library") or {}
        drafted = ((self.items_in.get("library") or {}).get("commit")
                   if isinstance(self.items_in, dict) else None)
        if drafted != self.library.get("commit"):
            raise ExportError(f"items.json was drafted against library commit {drafted}, "
                              f"informalize.json names {self.library.get('commit')}",
                              hint="the two files describe one library; `cf informalize scope` "
                                   "and the items phase settle it")

    # -- the PDF and the markers ----------------------------------------------------
    def check_markers(self) -> None:
        """The markers are of this PDF, of this build and of this `informal/`."""
        raw = self.pdf.read_bytes()
        self.pdf_sha256, self.pdf_bytes = common.sha256_bytes(raw), len(raw)
        if self.markers.get("pdf_sha256") != self.pdf_sha256:
            raise ExportError(f"{self.pdf} has sha256 {self.pdf_sha256[:12]}..., and "
                              f"markers.json was measured on {str(self.markers.get('pdf_sha256'))[:12]}...",
                              hint="export the canonical PDF `cf informalize fetch-pdf` brought home")
        if self.markers.get("job") != self.config.get("job"):
            raise ExportError(f"markers.json is of job {self.markers.get('job')!r}, "
                              f"informalize.json of {self.config.get('job')!r}")
        pages = self.markers.get("pages")
        if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
            raise ExportError("markers.json gives no page count")
        self.pages = pages
        source_commit = self.markers.get("source_commit")
        if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise ExportError(f"markers.json names no source commit: {source_commit!r}")
        if source_commit != self.head:
            try:
                diff = common.git_out(self.repo, "diff", "--name-only", source_commit,
                                      self.head, "--", INFORMAL)
            except ExportError as exc:
                raise ExportError(f"the repository does not hold the commit the PDF was built "
                                  f"from, {source_commit[:12]}: {exc.message}",
                                  hint="export from the checkout the canonical PDF was built "
                                       "in, or build it again from this commit") from exc
            changed = [line for line in diff.splitlines() if line.strip()]
            unexpected = sorted(set(changed) - {f"{INFORMAL}/markers.json"})
            if unexpected:
                raise ExportError(f"{INFORMAL}/ changed since the PDF was built at "
                                  f"{source_commit[:12]}: {', '.join(unexpected[:5])}",
                                  hint="build the canonical PDF again from this commit "
                                       "(`cf informalize pdf-command`); the boxes are of the "
                                       "text that was typeset")
        self.source_commit = source_commit

    # -- the source and the companion ------------------------------------------------
    def companion(self) -> dict:
        """The document's companion: the library, at the commit its items were written against."""
        repo_id = self.overrides["repo_id"] or common.repo_id_from_url(self.library.get("url") or "")
        if not repo_id:
            raise ExportError(f"the library URL {self.library.get('url')!r} names no repository",
                              hint="pass --repo-id host/org/name")
        name = self.overrides["name"] or repo_id.rsplit("/", 1)[-1]
        if not _NAME.fullmatch(name):
            raise ExportError(f"{name!r} is not a companion name", hint="pass --name")
        commit = self.library.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ExportError(f"informalize.json names no library commit: {commit!r}")
        self.repo_id, self.library_commit = repo_id, commit
        return {"name": name, "repo": repo_id, "commit": commit,
                "generated_at": self.head_date,
                "generator": common.generator("informal-markers")}

    def source(self) -> dict:
        """The one `pdf` source: the canonical PDF at the URL the maintainer publishes it at."""
        if not re.fullmatch(r"https://[^\s#\\]+", self.pdf_url or ""):
            raise ExportError(f"--pdf-url {self.pdf_url!r} is not an https URL with no fragment",
                              hint="the overlay fetches the PDF from it and verifies its sha256")
        source_id = self.overrides["source_id"] or self.config.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise ExportError("informalize.json names no id", hint="pass --source-id")
        offset = self.overrides["offset"] if self.overrides["offset"] is not None else 0
        source = {"id": source_id, "kind": "pdf",
                  "title": self.overrides["title"] or source_id,
                  "sha256": self.pdf_sha256, "bytes": self.pdf_bytes, "url": self.pdf_url,
                  "physical_page_count": self.pages, "printed_page_offset": offset}
        if self.overrides["edition"]:
            source["edition"] = self.overrides["edition"]
        size = self.markers.get("page_size_pt")
        if isinstance(size, list) and len(size) == 2 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in size):
            source["page_size"] = {"width_pt": float(size[0]), "height_pt": float(size[1])}
        else:
            self.report.lose("source.page_size", 1,
                             "markers.json gives no page size in points, so the source carries none; "
                             "the boxes are fractions of the page and need none")
        if not self.overrides["title"]:
            self.report.bend("source.title", 1,
                             "no file of the output repository carries the text's title "
                             "(informalize.yaml does), so the source is titled by its id; "
                             "pass --source-title")
        self.source_id = source_id
        return source

    # -- the declarations a box links to ----------------------------------------------
    def declaration(self, name: str, where: str) -> dict:
        """The declaration record for ``name``, with its snippet and its destination."""
        if name in self.declarations:
            return self.declarations[name]
        row = self.decls_in.get(name)
        if row is None:
            raise ExportError(f"{where}: {name} is not in {INFORMAL}/declarations.json")
        kind, module, path = row.get("kind"), row.get("module"), row.get("path")
        line = row.get("line")
        if kind not in vocab.ANNOTATION_LEAN_KINDS:
            raise ExportError(f"{where}: {name} has kind {kind!r}, which the annotation format "
                              f"does not know", hint="the format's kinds are "
                                                     f"{', '.join(sorted(vocab.ANNOTATION_LEAN_KINDS))}")
        if not (isinstance(module, str) and isinstance(path, str) and path.endswith(".lean")):
            raise ExportError(f"{where}: {name} has module {module!r} and path {path!r}")
        if not (isinstance(line, int) and not isinstance(line, bool) and line >= 1):
            raise ExportError(f"{where}: {name} has keyword line {line!r}")
        record = {"id": name, "id_source": "name", "name": name, "kind": kind, "module": module,
                  "file": path, "line": line, "private": False, "anonymous": False}
        self.declarations[name] = record
        self.snippet(record)
        self.destinations[name] = {
            "id": f"{self.site_name}:{name}", "declaration": name, "site": self.site_name,
            "href": f"https://{self.repo_id}/blob/{self.library_commit}/{path}",
            "resolution": "exact", "fragment": f"L{line}"}
        return record

    def snippet(self, record: dict) -> None:
        """The declaration's statement, cut from the library's source (`adapters.leansnippets`)."""
        rel = record["file"]
        if rel not in self.indexed:
            try:
                self.indexed[rel] = leansnippets.index_text(self.read_source(rel))
            except leansnippets.SnippetError as exc:
                # not `{exc}`: that reader was written for the attribution table's ledger,
                # and this convention reads none
                raise ExportError(f"{INFORMAL}/declarations.json names {rel}, which "
                                  f"--library-src does not hold as a readable file",
                                  hint="--library-src is the library's source at the commit, "
                                       "as `cf informalize fetch-library` unpacks it") from exc
        found = self.indexed[rel].get(record["id"])
        if found is None:
            self.report.counts.setdefault("snippets_unlocated", []).append(record["id"])
            return
        line, text = found
        self.snippets[record["id"]] = {"id": f"snippet:{record['id']}",
                                       "declaration": record["id"], "text": text, "line": line}
        if line != record["line"]:
            where = f"{record['id']}: {line} and {record['line']}"
            self.report.counts.setdefault("snippets_line_differs", []).append(where)
            self.report.bend("snippet.line_differs", 1,
                             "the line the snippet was cut at is not the keyword line "
                             "declarations.json gives (the scan reads the source, the probe read "
                             "the build)", [where])

    def check_keyword_line(self, name: str, where: str) -> None:
        """A key declaration's keyword line in `--library-src`, by the item table's own rule.

        `cf informalize check-items` and the gate hold every key declaration to
        :func:`fcitems.keyword_problem` against the library at the commit
        `informalize.json` names; this command is pointed at a bare directory,
        which nothing pins to that commit, so it asks the same question again
        rather than quoting bytes from another one.  A helper is exempt here as
        it is there: one may be a declaration a command such as `@[simps]`
        generated, whose line is the generating declaration's.
        """
        row = self.decls_in[name]
        if self.source_tree is None or name in self.keyword_checked \
                or row.get("kind") not in self.fcitems.KEYWORDS:
            return
        self.keyword_checked.add(name)
        problem = self.fcitems.keyword_problem(row, self.source_tree)
        if problem:
            raise ExportError(f"{where}: {problem}",
                              hint="--library-src must be the library's source at "
                                   f"{self.library_commit[:12]}, the commit informalize.json "
                                   "names; `cf informalize fetch-library` unpacks it")

    def read_source(self, rel: str) -> str:
        if self._read is None:
            raise ExportError("no --library-src, and the cards would show no statement",
                              hint="pass --library-src <workspace>/informalize/<id>/library/src")
        return self._read(rel)

    # -- the items, their boxes and their links ----------------------------------------
    def build(self) -> None:
        entries = {e["id"]: e for e in self.markers.get("entries") or []}
        for entry in self.markers.get("entries") or []:
            if "notion" in entry:
                self.add_notion(entry)
                continue
            if "parent" in entry:
                continue
            item = self.table.by_id.get(entry["id"])
            if item is None:
                raise ExportError(f"{entry['id']}: markers.json has it, items.json does not")
            if entry.get("kind") != item.get("kind") or entry.get("title") != item.get("title"):
                raise ExportError(f"{entry['id']}: markers.json gives "
                                  f"({entry.get('kind')!r}, {entry.get('title')!r}), items.json "
                                  f"({item.get('kind')!r}, {item.get('title')!r})")
            self.titles[item["id"]] = self.item_title(entry, item)
            parts = entry.get("parts") or []
            if not parts:
                self.add(entry, item, None)
                continue
            self.report.counts["items_with_parts"] = self.report.counts.get("items_with_parts", 0) + 1
            if entry.get("decls"):
                self.report.lose("markers.leanrefs_outside_a_part", len(entry["decls"]),
                                 "an item with parts is boxed around them, so its own box is not "
                                 "annotated and a `\\leanref` in its text, outside every part, "
                                 "carries no link", [f"{entry['id']}: {d}" for d in entry["decls"][:5]])
            for part_id in parts:
                part = entries.get(part_id)
                if part is None or part.get("parent") != entry["id"]:
                    raise ExportError(f"{entry['id']}: part {part_id} has no entry of its own "
                                      "in markers.json")
                self.add(part, item, part.get("part"))

    # -- the titles the page prints ------------------------------------------------------
    def item_title(self, entry: dict, item: dict) -> str | None:
        """The item's title: `items.json`'s, and for one written with TeX markup what the
        page prints in the item's head (`Lemma 1.2 (the title).`)."""
        title = item.get("title")
        if not title or not boxtext.has_tex(title):
            return title
        head = f"{self.fc.KIND_NAMES.get(item['kind'], item['kind'])} {item['id']}"
        group = self.read_boxes(entry["id"], (entry.get("boxes") or [])[:1])
        return self.printed(entry["id"], boxtext.parenthesis_after(group.text, head), title,
                            "the item's head")

    def notion_title(self, entry: dict, words: str) -> str:
        """The notion's title: the text the page prints inside its boxes, in reading order."""
        group = self.read_boxes(entry["id"], entry.get("boxes") or [])
        return self.printed(entry["id"], group.text, words, "the notion's boxes")

    def read_boxes(self, where: str, boxes: list) -> boxtext.BoxGroup:
        """One entry's boxes read, with what the page gave up on the way reported.

        Two things it can give up.  A subscript or a superscript run holding a character
        Unicode has no small one for (a capital, a bracket) is left as poppler read it,
        side by side with its base: the page prints an `ι` with an `S` under its right
        shoulder and the title says `ιS`.  And where the page's own glyphs cannot be read
        -- poppler's `pdftohtml` is not on PATH, or what it reads is not what pdftotext
        cropped -- the title is pdftotext's flattened text, which prints a double-struck
        `N` as `N` and a subscript as a digit of its own.
        """
        group = self.box_text.of(boxes)
        if group.kept:
            self.report.lose("title.script_run", len(group.kept),
                             "a subscript or a superscript Unicode has no characters for is "
                             "written beside its base, as poppler read it",
                             [f"{where}: {run}" for run in group.kept[:5]])
            self.report.counts.setdefault("titles_scripts_kept", []).append(
                f"{where}: {', '.join(group.kept)}")
        if not group.glyphs:
            self.report.lose("title.flattened_glyphs", 1,
                             "a title read without the page's own glyphs is pdftotext's text, "
                             "which flattens a double-struck letter and a script",
                             [where])
            self.report.counts.setdefault("titles_glyphs_unread", []).append(where)
        return group

    def printed(self, where: str, found: str | None, title: str, what: str) -> str:
        """``found``, or the words of the source with their TeX markup stripped, reported.

        A title is what the reader of the page sees; the words a `\\fcnotion` or an item
        was written with are LaTeX, and `$\\ball{x}{\\rho}$` is no title for a card.  The
        stripped words are the fallback for a PDF no box of can be read -- poppler absent,
        or a crop with no text in it -- and the report names every title that took it,
        unless the words hold no markup at all and the page prints what they say.
        """
        why = None
        if found and common.has_url(found):
            # a box is padded by `boxes.pad_bp`, so a crop can catch a URL the title
            # itself has none of, and a title holding one fails the `external-urls` lint
            found, why = None, "the crop holds a URL, which the format's lint refuses"
        if found:
            self.report.counts["titles_from_pdf"] = self.report.counts.get("titles_from_pdf", 0) + 1
            return found
        plain = boxtext.strip_tex(title)
        if why is None and not boxtext.has_tex(title):
            return plain                # plain prose: the words are what the page prints
        why = why or (self.box_text.why if not self.box_text.available
                      else f"{what} hold no text")
        self.report.lose("title.printed_text", 1,
                         "a title the canonical PDF could not be read for is the words of the "
                         "source with their TeX markup stripped, which is not what the page "
                         "prints", [f"{where}: {title} -> {plain} ({why})"])
        self.report.counts.setdefault("titles_not_printed", []).append(where)
        return plain

    # -- the notions of the conventions ------------------------------------------------
    def add_notion(self, entry: dict) -> None:
        """One passage record for a notion box, linked to the constant it marks."""
        where = entry["id"]
        name = entry.get("notion")
        words = str(entry.get("words") or "").strip()
        if not isinstance(name, str) or not name:
            raise ExportError(f"{where}: markers.json names no constant for the notion")
        if not words:
            raise ExportError(f"{where}: markers.json records no printed words for the notion",
                              hint="\\fcnotion takes the words as the text prints them")
        if common.has_url(words):
            raise ExportError(f"{where}: the notion's words hold a URL: {words[:60]}")
        rid = where                      # `notion:<constant>`, the markers' own id
        if rid in self.notion_items:
            raise ExportError(f"{where}: markers.json records the notion twice",
                              hint="a notion is fixed once, where the conventions introduce it")
        self.notion_items[rid] = name
        decl = self.notion_declaration(name, where)
        self.items.append({"id": rid, "id_source": "declared", "form": "passage",
                           "source": self.source_id, "kind": "passage",
                           "title": self.notion_title(entry, words), "coverage": COVERAGE})
        self.links.append({"id": f"{rid}/key/{name}", "item": rid, "declaration": decl["id"],
                           "role": "key", "relation": "exact", "provenance": PROVENANCE_ID})
        self.locate(entry, rid, where)

    def notion_declaration(self, name: str, where: str) -> dict:
        """The declaration a notion marks: a member of the library, or a dependency's constant."""
        if name in self.decls_in:
            record = self.declaration(name, where)
            self.check_keyword_line(name, where)
            return record
        if name in self.refs_in:
            return self.dependency_declaration(name, where)
        raise ExportError(f"{where}: {name} is neither a declaration of "
                          f"{INFORMAL}/declarations.json nor a constant of its `references`")

    def dependency_declaration(self, name: str, where: str) -> dict:
        """A constant of a library underneath this one, at the commit its manifest pins."""
        if name in self.declarations:
            return self.declarations[name]
        row = self.refs_in[name]
        module, kind = row.get("module"), DEPENDENCY_KINDS.get(row.get("kind"))
        if not isinstance(module, str) or not module:
            raise ExportError(f"{where}: {name} has module {module!r} in `references`")
        if kind is None:
            raise ExportError(f"{where}: {name} of {module} is a {str(row.get('kind'))!r}, which "
                              f"the annotation format does not carry as a declaration of its own",
                              hint="mark the structure or the definition a reader meets, not a "
                                   "constructor, a recursor or a quotient primitive")
        # Lake's layout: a module's file is its name with `/` for each `.`
        path = module.replace(".", "/") + ".lean"
        record = {"id": name, "id_source": "name", "name": name, "kind": kind, "module": module,
                  "file": path, "private": False, "anonymous": False}
        package = str(row.get("package") or "")
        if package:
            record["x_package"] = package
        if row.get("repo") and row.get("commit"):
            record["x_repo"], record["x_commit"] = row["repo"], row["commit"]
        if package or "x_repo" in record:
            self.report.bend("declaration.x_dependency", 1,
                             "a declaration of a library underneath this one, whose package, "
                             "repository and pinned commit the format has no field for",
                             [f"{where}: {name}"])
        self.declarations[name] = record
        line = self.dependency_snippet(record, package)
        destination = self.dependency_destination(name, row, path, line)
        if destination is None:
            self.report.lose("destination.reference", 1,
                             "a reference row with neither a documentation URL nor a pinned "
                             "dependency gives the card no page to open", [f"{where}: {name}"])
        else:
            self.destinations[name] = destination
        return record

    def dependency_snippet(self, record: dict, package: str) -> int | None:
        """The constant's statement from `--packages-src`; its line, or None when it is not there.

        The library mirror holds the library's own source alone, so this is the one input
        that can carry a dependency's: a `.lake/packages` directory, where each package's
        source is under its own name.
        """
        name = record["id"]
        if self._read_package is None or not package:
            self.report.counts.setdefault("references_without_source", []).append(name)
            return None
        rel = f"{package}/{record['file']}"
        if rel not in self.indexed_packages:
            try:
                self.indexed_packages[rel] = leansnippets.index_text(self._read_package(rel))
            except leansnippets.SnippetError:
                self.indexed_packages[rel] = {}
        found = self.indexed_packages[rel].get(name)
        if found is None:
            self.report.counts.setdefault("references_unlocated", []).append(name)
            return None
        line, text = found
        record["line"] = line
        self.snippets[name] = {"id": f"snippet:{name}", "declaration": name, "text": text,
                               "line": line}
        return line

    def dependency_destination(self, name: str, row: dict, path: str,
                               line: int | None) -> dict | None:
        """Where a dependency's constant is read: its source line, else its documentation page."""
        repo, commit = str(row.get("repo") or ""), str(row.get("commit") or "")
        if repo and commit and line is not None:
            return {"id": f"{self.site_name}:{name}", "declaration": name, "site": self.site_name,
                    "href": f"https://{repo}/blob/{commit}/{path}", "resolution": "exact",
                    "fragment": f"L{line}"}
        page, _, anchor = str(row.get("doc_url") or "").partition("#")
        if page.startswith("https://") and anchor:
            return {"id": f"{DOC_SITE}:{name}", "declaration": name, "site": DOC_SITE,
                    "href": page, "resolution": "exact", "fragment": anchor}
        if repo and commit:
            return {"id": f"{self.site_name}:{name}", "declaration": name, "site": self.site_name,
                    "href": f"https://{repo}/blob/{commit}/{path}", "resolution": "fallback",
                    "fallback_label": name}
        return None

    def add(self, entry: dict, item: dict, letter: str | None) -> None:
        """One item record for a box group, its locations and one link per `\\leanref` there."""
        where = entry["id"]
        kind = item["kind"]
        number = item["id"]
        rid = ids.item_label_id(kind, number, letter)
        record = {"id": rid, "id_source": "label", "form": "numbered", "source": self.source_id,
                  "kind": self.fc.KIND_NAMES.get(kind, kind), "number": number,
                  "section": str(item.get("section") or "")}
        if letter:
            record["sub"] = letter
        title = self.titles.get(item["id"], item.get("title"))
        if common.has_url(title):
            self.report.lose("item.title", 1,
                             "a title holding a URL would fail the format's `external-urls` "
                             "lint, so the item carries none", [f"{where}: {title}"])
        elif title:
            record["title"] = title
        links = self.link_records(entry, item, rid, letter, where)
        if any(link["role"] == "key" for link in links):
            record["coverage"] = COVERAGE
        elif links:
            # invariant I5: a record with links and no key link carries a class that
            # excludes one, and helpers state a neighbour, not the item
            record["coverage"] = COVERAGE_HELPERS_ONLY
            self.report.bend("item.coverage_from_helpers", 1,
                             "a box whose `\\leanref`s are all helpers states no declaration of "
                             "its own, so its coverage is support_only", [where])
        else:
            self.report.problem(f"{where}: its box names no declaration, so it links nothing")
        self.items.append(record)
        self.links += links
        self.locate(entry, rid, where)

    def link_records(self, entry: dict, item: dict, rid: str, letter: str | None,
                     where: str) -> list[dict]:
        """One link per `\\leanref` the build recorded in this box, in the order they stand."""
        listed = {d["name"]: d for d in item.get("decls") or [] if isinstance(d.get("name"), str)}
        out: list[dict] = []
        seen: set[str] = set()
        for name in entry.get("decls") or []:
            if name in seen:
                self.report.counts["leanrefs_repeated"] = self.report.counts.get("leanrefs_repeated", 0) + 1
                continue
            seen.add(name)
            decl = self.declaration(name, where)
            given = listed.get(name)
            role = given.get("role") if given else None
            if role not in ("key", "helper"):
                role = "helper"
                self.report.bend("link.role_not_in_the_table", 1,
                                 "a `\\leanref` the item's table does not list is linked as a "
                                 "helper, with no relation", [f"{where}: {name}"])
            if role == "key":
                self.check_keyword_line(name, where)
            link = {"id": f"{rid}/{role}/{name}", "item": rid, "declaration": decl["id"],
                    "role": role, "provenance": PROVENANCE_ID}
            token = RELATION_TOKENS.get(given.get("relation")) if given else None
            if token:
                link["relation"] = token
            elif given:
                self.report.lose("link.relation", 1,
                                 "a relation the legacy format cannot carry is written as none",
                                 [f"{where}: {name} is {given.get('relation')!r}"])
            if given and given.get("junk"):
                self.report.lose("link.junk", len(given["junk"]),
                                 "the junk values items.json lists have no field in the annotation "
                                 "format; the text states each of them in a sentence of its own",
                                 [f"{where}: {j}" for j in given["junk"][:5]])
            out.append(link)
        keys = [d["name"] for d in item.get("keys") or []
                if (d.get("part") or None) == letter and d["name"] not in seen]
        if keys:
            self.report.problem(f"{where}: its key declaration(s) {', '.join(sorted(keys))} are "
                                "not `\\leanref`ed in its box")
        return out

    def locate(self, entry: dict, rid: str, where: str) -> None:
        """One location per box: a `PdfRegionSelector` of the fractions markers.json gives."""
        boxes = entry.get("boxes") or []
        if not boxes:
            raise ExportError(f"{where}: no box in markers.json",
                              hint="rerun extract_markers.py on the canonical PDF")
        seen: collections.Counter = collections.Counter()
        for box in boxes:
            if not (isinstance(box, list) and len(box) == 5):
                raise ExportError(f"{where}: box {box!r} is not [page, x, y, w, h]")
            page, x, y, w, h = box
            if not (isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= self.pages):
                raise ExportError(f"{where}: page {page!r} is not a page of the "
                                  f"{self.pages}-page PDF")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in (x, y, w, h)) or w <= 0 or h <= 0 \
                    or not (0 <= x and 0 <= y and x + w <= 1 and y + h <= 1):
                raise ExportError(f"{where}: box {box[1:]} is not a fraction of page {page}")
            seen[page] += 1
            lid = f"{rid}@p{page:03d}" + (f"#{seen[page]}" if seen[page] > 1 else "")
            self.locations.append({
                "id": lid, "items": [rid],
                "target": {"source": self.source_id, "selector": [{
                    "type": "PdfRegionSelector", "page": page,
                    "coordinate_space": dict(COORDINATE_SPACE),
                    "rect": {"x0": _rounded(x), "y0": _rounded(y),
                             "x1": _rounded(x + w), "y1": _rounded(y + h)}}]}})

    # -- the document -------------------------------------------------------------------
    def inputs(self) -> list[dict]:
        return [file_lock(self.repo, f"{INFORMAL}/{rel}") for rel in sorted(INPUTS)]

    def provenance(self) -> dict:
        record = {"id": PROVENANCE_ID, "actor": "agent", "method": "agent_annotation",
                  "confirmed_by_human": bool(self.items_approved),
                  "tool": {"name": TOOL_NAME, "version": common.__version__},
                  "inputs": self.inputs(),
                  "note": "the fleet wrote each statement and its `\\leanref`s, and each notion "
                          "and its `\\fcnotion`, against the Lean; the boxes are the build's "
                          "position markers"}
        if not self.items_approved:
            self.report.counts["items_approval"] = "not pinned (--items-approved was not passed)"
        return record

    def run(self) -> dict:
        self._read = leansnippets.reader(self.library_src) if self.library_src else None
        self._read_package = leansnippets.reader(self.packages_src) if self.packages_src else None
        self.source_tree = self.fcitems.SourceTree(self.library_src) if self.library_src else None
        companion = self.companion()
        self.check_markers()
        source = self.source()
        self.build()
        doc = {
            "schema": vocab.ANNOTATION_SCHEMA, "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
            "companion": companion,
            "sources": [source],
            "items": self.items,
            "locations": self.locations,
            "declarations": list(self.declarations.values()),
            "links": self.links,
            "provenance": [self.provenance()],
            "snippets": list(self.snippets.values()),
            "destinations": list(self.destinations.values()),
        }
        common.declare_profiles(doc)
        self.count(doc)
        return doc

    def count(self, doc: dict) -> None:
        counts = self.report.counts
        for key in ("sources", "items", "declarations", "links", "locations", "snippets",
                    "destinations", "provenance"):
            counts[key] = len(doc.get(key, []))
        counts["parts"] = sum(1 for item in self.items if "sub" in item)
        counts["notions"] = len(self.notion_items)
        counts["pages"] = self.pages
        counts["boxes"] = len(self.locations)
        counts["links_by_role"] = dict(sorted(collections.Counter(
            link["role"] for link in self.links).items()))
        counts["links_by_relation"] = dict(sorted(collections.Counter(
            link.get("relation", "none") for link in self.links).items()))
        counts["pdf"] = {"sha256": self.pdf_sha256, "bytes": self.pdf_bytes, "pages": self.pages}
        # `cf overlay export` records these in GENERATED.json, so the companion's CI
        # can tell a stale export, and `export.commit` is this checkout's HEAD.
        counts["inputs"] = self.inputs()
        counts["checkout_commit"] = self.head
        counts["source_commit"] = self.source_commit
        counts["library"] = {"repo": self.repo_id, "commit": self.library_commit}
        counts.setdefault("titles_from_pdf", 0)
        counts.setdefault("titles_not_printed", [])
        counts.setdefault("titles_scripts_kept", [])
        counts.setdefault("titles_glyphs_unread", [])
        counts.setdefault("snippets_unlocated", [])
        counts.setdefault("snippets_line_differs", [])
        counts.setdefault("references_without_source", [])
        counts.setdefault("references_unlocated", [])


def export(checkout: Path, commit: str, *, pdf: Path, pdf_url: str, library_src: Path | None,
           packages_src: Path | None = None,
           items_approved: bool = False, name: str | None = None, repo_id: str | None = None,
           site_name: str = SITE, source_id: str | None = None, source_title: str | None = None,
           source_edition: str | None = None, page_offset: int | None = None,
           ) -> tuple[dict, Report]:
    """The annotation document for an informalization companion, and the export's report."""
    exporter = _Export(Path(checkout), commit, pdf=Path(pdf), pdf_url=pdf_url,
                       library_src=Path(library_src) if library_src else None,
                       packages_src=Path(packages_src) if packages_src else None,
                       items_approved=items_approved, name=name, repo_id=repo_id,
                       site_name=site_name, source_id=source_id, source_title=source_title,
                       source_edition=source_edition, page_offset=page_offset)
    return exporter.run(), exporter.report
