"""`cf export roadmap-locators`: a Markdown roadmap's declarations on the lines of a TeX manuscript.

New code, for section 9 of docs/exporters.md.  The convention is a companion
that keeps, beside its Lean, a roadmap of Markdown pages and a set of locator
files:

* a **page** (``<blueprint>/roadmap/**/*.md``, a ``README.md`` excepted) opens
  with a front matter whose ``lean:`` names the declarations that formalize the
  page, comma separated, and whose ``origin:`` says where the page's statement
  comes from (``cited``: it is a statement of the source); its ``## Sources``
  section links to sections of the locator files;
* a **locator file** (``<blueprint>/sources/*.md``) says in the text before
  its first ``##`` heading which TeX file it reads, either as ``Line ranges
  refer to `<file>.tex``` (``Line numbers`` too), in which case each section
  gives ``lines 93-121`` in its first sentence, or as a file whose ``locators
  are the TeX labels``, in which case each section names a ``\\label``.  A
  header that also gives the file's ``SHA-256`` pins its bytes.

What the export makes of that: one **passage** item per locator section some
page cites, titled by the section's heading; one link from it to every
declaration of every page that cites it, ``key`` when the page is ``origin:
cited`` with ``statement: formalized`` and ``support`` otherwise, with no
relation, since a page says which declarations formalize it and not how each
stands to the manuscript's sentence; and one location per box, where
`texplace` finds the section's lines on the manuscript's PDF.  Sections with
the same rows share a location.  Each declaration is resolved in the
checkout's tracked Lean files by `adapters.leansnippets`, which also cuts the
statement its card shows, and its destination is its line on the companion's
git host.

Nothing here reads a build.  A name the Lean source declares nowhere the text
parser can see (one a command generates) links nothing and is reported, as is
a name two files declare.
"""
from __future__ import annotations

import collections
import re
import shutil
from pathlib import Path, PurePosixPath

from adapters import leanparse
from adapters import leansnippets
from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import vocab

from . import common
from . import texplace
from .common import ExportError, Report, file_lock

#: Where the convention keeps its pages and its locator files, under `--blueprint`.
BLUEPRINT = "blueprint"
ROADMAP = "roadmap"
SOURCES = "sources"
#: A declaration's page is a blob view of the companion's git host.
SITE = "other"
PROVENANCE_ID = "prov-roadmap"
#: The front matter value that makes a page's declarations key links, and the one it needs beside it.
ORIGIN_KEY = "cited"
STATEMENT_FORMALIZED = "formalized"
STATEMENT_RETRACTED = "retracted"
#: The coordinate space of a box: fractions of the displayed page from its top left.
#: `pdftotext -bbox-layout` and `pdftoppm` both read the page as it is displayed.
COORDINATE_SPACE = {"origin": "top_left", "units": "normalized", "x_axis": "right",
                    "y_axis": "down", "page_box": "CropBox"}

_LINES_HEADER = re.compile(r"Line\s+(?:ranges|numbers)\s+refer\s+to\s+`([^`\s]+\.tex)`")
_LABELS_HEADER = re.compile(r"`([^`\s]+\.tex)`.*?locators\s+are\s+the\s+TeX\s+labels", re.S | re.I)
_SHA = re.compile(r"SHA-256\s+`([0-9a-f]{64})`")
_RANGE = r"\d+(?:\s*[-–]\s*\d+)?"
_LINES = re.compile(r"\blines?\s+(" + _RANGE + r"(?:(?:\s*,\s*|\s*,?\s+and\s+)" + _RANGE + r")*)", re.I)
_LABEL = re.compile(r"`([A-Za-z]+:[A-Za-z0-9:._-]+)`")
#: A Lean name as a page writes it: dotted components of letters, digits and the marks a name may end in.
_NAME = re.compile(r"[^\W\d][\w'!?]*(?:\.[\w'!?«»]+)*\Z")


def _key(text: str) -> str:
    """A heading or an anchor as they are compared: its letters and digits, lower-cased."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.replace("$", "").lower())).strip("-")


def _without_parentheses(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\([^()]*\)", " ", text)
    return text


def line_ranges(text: str, last_line: int) -> list[tuple[int, int]]:
    """The ranges ``text`` gives after the word ``line`` or ``lines``, merged, in order.

    ``lines 60-76 and 123-140`` is two ranges and ``line 404`` is one; a range
    that is not within the file, or that runs backwards, is no range.
    """
    found = []
    for match in _LINES.finditer(text):
        for first, last in re.findall(r"(\d+)(?:\s*[-–]\s*(\d+))?", match.group(1)):
            a, b = int(first), int(last or first)
            if 1 <= a <= b <= last_line:
                found.append((a, b))
    merged: list[tuple[int, int]] = []
    for a, b in sorted(set(found)):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def section_spans(body: str, source: texplace.Source, labels_only: bool) -> tuple[list[tuple[int, int]], str | None]:
    """The lines one locator section names, and how they were read.

    In a file of line ranges, the ranges of the section's first sentence, up
    to its first colon and outside parentheses, which is where the convention
    states the passage (``lines 93-121: the six inputs (lines 93-108), ...``);
    failing that, those of its first paragraph outside parentheses; failing
    that, a TeX label it names; failing that, those of the whole section.  In
    a file of labels, the first label of the first paragraph the source has.
    """
    paragraph = body.strip().split("\n\n")[0].replace("\n", " ")

    def labelled() -> tuple[list[tuple[int, int]], str | None]:
        for label in _LABEL.findall(paragraph):
            span = source.label_span(label)
            if span is not None:
                return [span], "label"
        return [], None

    if labels_only:
        return labelled()
    masked = _without_parentheses(re.sub(r"`[^`]*`", " ", paragraph))
    head = re.split(r":(?:\s|$)", masked, maxsplit=1)[0]
    for text, how in ((head, "head"), (masked, "paragraph")):
        ranges = line_ranges(text, len(source))
        if ranges:
            return ranges, how
    spans, how = labelled()
    if spans:
        return spans, how
    whole = _without_parentheses(re.sub(r"`[^`]*`", " ", body.replace("\n", " ")))
    ranges = line_ranges(whole, len(source))
    return (ranges, "section") if ranges else ([], None)


def front_matter(text: str) -> dict[str, str]:
    """The ``key: value`` lines between a page's opening ``---`` lines."""
    match = re.match(r"---\n(.*?)\n---\s*\n", text, re.S)
    out: dict[str, str] = {}
    if match:
        for line in match.group(1).split("\n"):
            key, colon, value = line.partition(":")
            if colon and key.strip() and not key.startswith((" ", "\t", "#")):
                out[key.strip()] = value.strip()
    return out


def source_links(text: str) -> list[str]:
    """The link targets of a page's ``## Sources`` section, in order."""
    match = re.search(r"^## Sources[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return re.findall(r"\]\(([^)\s]+)\)", match.group(1)) if match else []


def read_locator(text: str) -> dict:
    """One locator file: the TeX file its header says it reads, how, and its sections.

    ``tex`` is None for a file that reads no TeX file; ``labels_only`` says its
    sections name labels and not lines; ``sha256`` lists every SHA-256 the
    header gives for the TeX file; ``sections`` maps each ``##`` heading, as
    links to it are compared, to the heading and its text.
    """
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    header = parts[0]
    lines = _LINES_HEADER.search(header)
    labels = None if lines else _LABELS_HEADER.search(header)
    tex = (lines or labels).group(1) if (lines or labels) else None
    return {"tex": tex, "labels_only": bool(labels), "sha256": _SHA.findall(header) if tex else [],
            "sections": {_key(parts[i]): (parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)}}


def cited_sections(rel: str, text: str, locators: dict[str, dict]) -> tuple[list[tuple[str, str]], int, list[str]]:
    """The locator sections a page's ``## Sources`` cites, among the locator files given.

    Returns the ``(locator file, section)`` pairs that are of a file reading a
    TeX file, each once and in the page's order; how many links went to a
    locator file that reads none; and the links that name a section its file
    does not have.  A link to anything else is not the convention's.
    """
    found: list[tuple[str, str]] = []
    no_tex = 0
    dangling: list[str] = []
    for target in source_links(text):
        path, _, anchor = target.partition("#")
        if "://" in path or not path.endswith(".md"):
            continue
        parts: list[str] = []
        for part in PurePosixPath(rel).parent.joinpath(path).parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        locator = locators.get("/".join(parts))
        if locator is None:
            continue
        if locator["tex"] is None:
            no_tex += 1
        elif _key(anchor) not in locator["sections"]:
            dangling.append(f"{rel}: {target}")
        elif ("/".join(parts), _key(anchor)) not in found:
            found.append(("/".join(parts), _key(anchor)))
    return found, no_tex, dangling


def module_of(rel: str, tracked: frozenset[str]) -> str:
    """The module a tracked Lean file is, read from where its library's root file stands.

    Lake names a module by its path from the library's source directory, and a
    library ``Lib`` keeps a ``Lib.lean`` beside ``Lib/``; so the module starts
    at the first directory of the path that has such a file beside it, and at
    the checkout's root when none has.
    """
    parts = PurePosixPath(rel).parts
    for skip in range(len(parts) - 1):
        root = "/".join(parts[:skip] + (parts[skip] + ".lean",))
        if root in tracked:
            return common.module_of("/".join(parts[skip:]))
    return common.module_of(rel)


class _Export:
    """One export: the inputs, the records they make, and the report."""

    def __init__(self, checkout: Path, commit: str, *, pdf: Path, pdf_url: str, blueprint: str | None,
                 tex: str | None, name: str | None, repo_id: str | None, site_name: str,
                 source_id: str | None, source_title: str | None, source_edition: str | None,
                 page_offset: int | None) -> None:
        self.repo, self.pdf, self.pdf_url = checkout, pdf, pdf_url
        self.blueprint = common.repo_relative(blueprint or BLUEPRINT, "--blueprint")
        self.tex_flag = common.repo_relative(tex, "--tex") if tex else None
        self.site_name = site_name or SITE
        self.overrides = {"name": name, "repo_id": repo_id, "source_id": source_id, "title": source_title,
                          "edition": source_edition, "offset": page_offset}
        self.report = Report()
        self.head, self.head_date = common.pinned_checkout(checkout, commit)
        self.tracked = frozenset(p for p in common.git_out(checkout, "ls-files", "-z").split("\0") if p)
        self.read: list[str] = []
        self.declarations: dict[str, dict] = {}
        self.snippets: dict[str, dict] = {}
        self.destinations: dict[str, dict] = {}

    # -- the files of the checkout -----------------------------------------------------
    def text(self, rel: str) -> str:
        """One tracked file of the checkout as text, recorded as an input."""
        if rel not in self.tracked or (self.repo / rel).is_symlink():
            raise ExportError(f"{rel} is not a tracked file of {self.repo}")
        self.read.append(rel)
        return (self.repo / rel).read_bytes().decode("utf-8", "replace")

    def under(self, directory: str) -> list[str]:
        prefix = f"{self.blueprint}/{directory}/"
        return sorted(p for p in self.tracked if p.startswith(prefix) and p.endswith(".md"))

    # -- the locator files and the TeX they read ---------------------------------------
    def read_locators(self) -> None:
        """Each locator file's sections, and the one TeX file the files that read one agree on."""
        files = [p for p in self.under(SOURCES) if PurePosixPath(p).name != "README.md"]
        if not files:
            raise ExportError(f"{self.blueprint}/{SOURCES}/ holds no locator file at {self.head[:12]}",
                              hint="this convention reads a roadmap whose pages cite locator files "
                                   "(docs/exporters.md section 9); --blueprint names the directory")
        self.locators: dict[str, dict] = {}
        named: dict[str, list[str]] = collections.defaultdict(list)
        pinned: dict[str, list[str]] = collections.defaultdict(list)
        for rel in files:
            locator = self.locators[rel] = read_locator(self.text(rel))
            if locator["tex"]:
                named[locator["tex"]].append(rel)
                for sha in locator["sha256"]:
                    pinned[sha].append(rel)
        if not named:
            raise ExportError(f"no locator file under {self.blueprint}/{SOURCES}/ says which TeX file it reads",
                              hint="its header says `Line ranges refer to `<file>.tex`` or that its "
                                   "locators are the TeX labels of one (docs/exporters.md section 9)")
        if len(named) > 1:
            raise ExportError("the locator files read more than one TeX file: "
                              + "; ".join(f"{tex} ({', '.join(rels[:2])})" for tex, rels in sorted(named.items())),
                              hint="one export places one manuscript; export the others from a "
                                   "checkout of their own locators")
        self.tex_name = next(iter(named))
        rel = self.tex_flag or self.tex_name
        if rel not in self.tracked or (self.repo / rel).is_symlink():
            raise ExportError(f"the locators read {self.tex_name}, and {rel} is not a tracked file of "
                              f"the checkout at {self.head[:12]}",
                              hint="pass --tex <the file's path in the checkout>")
        raw = (self.repo / rel).read_bytes()
        self.read.append(rel)
        self.tex_rel, self.tex_sha256 = rel, common.sha256_bytes(raw)
        wrong = sorted(sha for sha in pinned if sha != self.tex_sha256)
        if wrong:
            raise ExportError(f"{rel} has sha256 {self.tex_sha256[:12]}..., and {pinned[wrong[0]][0]} "
                              f"gives line numbers of {wrong[0][:12]}...",
                              hint="pass --tex <the file with those bytes>; line numbers of one "
                                   "revision say nothing of another")
        if not pinned:
            self.report.bend("source.tex_unpinned", 1,
                             "no locator file gives the TeX file's SHA-256, so nothing says the line "
                             "numbers are of the file at this commit", [rel])
        self.source = texplace.Source(raw.decode("utf-8", "replace"))

    # -- the pages -----------------------------------------------------------------------
    def read_pages(self) -> None:
        """Every page's declarations against every locator section it cites that names lines."""
        counts = self.report.counts
        self.cited: dict[tuple[str, str], dict] = {}
        pages = [p for p in self.under(ROADMAP) if PurePosixPath(p).name != "README.md"]
        if not pages:
            raise ExportError(f"{self.blueprint}/{ROADMAP}/ holds no page at {self.head[:12]}",
                              hint="--blueprint names the directory that holds roadmap/ and sources/")
        counts["pages"] = len(pages)
        tally: collections.Counter = collections.Counter()
        dangling: list[str] = []
        for rel in pages:
            text = self.text(rel)
            meta = front_matter(text)
            if meta.get("statement") == STATEMENT_RETRACTED:
                tally["pages_retracted"] += 1
                continue
            names = [n.strip() for n in meta.get("lean", "").split(",") if n.strip()]
            bad = [n for n in names if not _NAME.fullmatch(n)]
            if bad:
                self.report.problem(f"{rel}: lean: names {bad[0]!r}, which is no declaration name")
                names = [n for n in names if n not in bad]
            role = "key" if (meta.get("origin") == ORIGIN_KEY
                             and meta.get("statement") == STATEMENT_FORMALIZED) else "support"
            sections, no_tex, astray = cited_sections(rel, text, self.locators)
            tally["links_to_files_that_read_no_tex"] += no_tex
            dangling += astray
            if not sections:
                tally["pages_citing_no_passage"] += 1
                continue
            if not names:
                tally["pages_naming_no_declaration"] += 1
            for key in sections:
                entry = self.cited.setdefault(key, {"pages": [], "links": {}})
                entry["pages"].append(rel)
                for declaration in names:
                    if role == "key" or declaration not in entry["links"]:
                        entry["links"][declaration] = role
        for key, value in sorted(tally.items()):
            counts[key] = value
        if dangling:
            self.report.problem(f"{len(dangling)} link(s) name a section its locator file does not have: "
                                + "; ".join(dangling[:3]))

    # -- the passages and their boxes ----------------------------------------------------
    def place(self) -> None:
        """Each cited section's lines, the rows they print on, and a box for every page of them."""
        counts = self.report.counts
        pdf_raw = self.pdf.read_bytes()
        self.pdf_sha256, self.pdf_bytes = common.sha256_bytes(pdf_raw), len(pdf_raw)
        try:
            self.layout = texplace.Layout.extract(self.pdf)
        except texplace.PlaceError as exc:
            raise ExportError(f"--pdf: {exc}") from exc
        placer = texplace.Placer(self.source, self.layout)
        self.passages: dict[str, dict] = {}
        extents: dict[str, list] = {}
        how_read: collections.Counter = collections.Counter()
        how_placed: collections.Counter = collections.Counter()
        unread, unplaced = [], []
        for (rel, anchor), entry in sorted(self.cited.items()):
            heading, body = self.locators[rel]["sections"][anchor]
            where = f"{rel}#{_slug(heading)}"
            spans, how = section_spans(body, self.source, self.locators[rel]["labels_only"])
            if not spans:
                unread.append(where)
                continue
            how_read[how] += 1
            found = []
            for first, last in spans:
                extent = placer.place(first, last)
                how_placed[extent.how if extent else "unplaced"] += 1
                if extent is None:
                    unplaced.append(f"{where}: lines {first}-{last}")
                else:
                    found.append(extent)
            if not found:
                continue
            stem = PurePosixPath(rel).name[:-len(".md")]
            stem = stem[:-len("-locators")] if stem.endswith("-locators") else stem
            rid = f"{stem}:{_slug(heading)}"
            if rid in self.passages:
                raise ExportError(f"{where}: two sections of the locator files would be the item {rid}")
            self.passages[rid] = {"heading": heading, "spans": spans, "where": where, **entry}
            extents[rid] = found
        agreement = placer.agreement
        nothing_placed = not extents and len(unread) < len(self.cited)
        if nothing_placed or (agreement is not None and agreement < texplace.MIN_WORD_AGREEMENT):
            share = f"{agreement:.0%}" if agreement else "none"
            raise ExportError(f"{self.pdf} prints {share} of the words of the lines the locators "
                              f"cite in {self.tex_rel}; it is not read as that file's PDF",
                              hint="pass the PDF typeset from that revision of the TeX, or --tex the "
                                   "file it was typeset from")
        ink = None
        if shutil.which(texplace.INK_PROGRAM) is not None:
            ink = texplace.Ink(self.pdf, self.layout)
        self.boxes, drawn = texplace.boxes(self.layout, extents, ink)
        counts["sections_cited"] = len(self.cited)
        counts["spans_read_from"] = dict(sorted(how_read.items()))
        counts["spans_placed_by"] = dict(sorted(how_placed.items()))
        counts["word_agreement"] = None if agreement is None else round(agreement, 4)
        counts["boxes"] = drawn["boxes"]
        counts["box_edges_checked"] = drawn["edges_checked"]
        counts["box_edges_on_ink"] = drawn["edges_on_ink"]
        counts["boxes_crossing"] = drawn["crossing"]
        counts["sections_naming_no_lines"] = unread
        counts["passages_unplaced"] = unplaced
        if unread:
            self.report.lose("passage.no_lines", len(unread),
                             "a cited locator section that names no line range and no label of the "
                             "TeX file is no passage, and its pages' declarations are linked to "
                             "nothing", unread[:5])
        if unplaced:
            self.report.lose("passage.unplaced", len(unplaced),
                             "a line range none of whose words, nor its neighbours', the PDF prints "
                             "where they were looked for gets no box", unplaced[:5])
        if not drawn["edges_checked"]:
            self.report.bend("location.edges_unchecked", drawn["boxes"],
                             f"{texplace.INK_PROGRAM} is not on PATH, so no box edge was held against "
                             "the rendered page")

    # -- the declarations ------------------------------------------------------------------
    def resolve(self) -> None:
        """Every linked name in the tracked Lean files: its file, line, kind and statement.

        The names are read by `adapters.leanparse`, which tracks namespaces, and
        the statement is cut where it found the declaration by the rules of
        `adapters.leansnippets`: a theorem through the ``:=`` of its proof, a
        definition whole.
        """
        wanted = {name for entry in self.passages.values() for name in entry["links"]}
        last_parts = {name.rsplit(".", 1)[-1] for name in wanted}
        found: dict[str, list] = collections.defaultdict(list)
        texts: dict[str, str] = {}
        lean = sorted(p for p in self.tracked if p.endswith(".lean"))
        for rel in lean:
            path = self.repo / rel
            if path.is_symlink():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not last_parts.intersection(re.findall(r"[^\W\d][\w'!?]*", text)):
                continue
            for decl in leanparse.parse_file(path, rel):
                if not decl.anonymous and decl.full_name in wanted:
                    found[decl.full_name].append(decl)
                    texts[rel] = text
        counts = self.report.counts
        counts["lean_files"] = len(lean)
        unresolved, ambiguous, private = [], [], []
        for name in sorted(wanted):
            hits = found.get(name, [])
            if not hits:
                unresolved.append(name)
                continue
            if len(hits) > 1:
                ambiguous.append(f"{name}: {hits[0].file} and {hits[1].file}")
                continue
            decl = hits[0]
            if decl.private or decl.kind not in vocab.ANNOTATION_LEAN_KINDS:
                private.append(name)
                continue
            lines = texts[decl.file].split("\n")
            code = leansnippets.code_line_starts(lines)
            at = decl.line - 1
            if decl.kind in leansnippets.DEFINITION_KEYWORDS:
                snippet = leansnippets.definition_text(lines, at, code)
            else:
                snippet = leansnippets.statement_text(lines, at, leansnippets.declaration_end(lines, at, code))
            self.declarations[name] = {"id": name, "id_source": "name", "name": name, "kind": decl.kind,
                                       "module": module_of(decl.file, self.tracked), "file": decl.file,
                                       "line": decl.line, "private": False, "anonymous": False}
            self.snippets[name] = {"id": f"snippet:{name}", "declaration": name, "text": snippet,
                                   "line": decl.line}
            self.destinations[name] = {"id": f"{self.site_name}:{name}", "declaration": name,
                                       "site": self.site_name,
                                       "href": f"https://{self.repo_id}/blob/{self.head}/{decl.file}",
                                       "resolution": "exact", "fragment": f"L{decl.line}"}
        counts["declarations_unresolved"] = unresolved
        counts["declarations_ambiguous"] = ambiguous
        counts["declarations_private"] = private
        if unresolved:
            self.report.lose("link.unresolved", len(unresolved),
                             "a name no tracked Lean file declares where the text parser reads it "
                             "(a structure's field, a declaration a command generates, or one that "
                             "is gone) links nothing", unresolved[:5])
        if ambiguous:
            self.report.lose("link.ambiguous", len(ambiguous),
                             "a name two tracked Lean files declare links nothing, since nothing "
                             "here says which the page means", ambiguous[:5])
        if private:
            self.report.lose("link.private", len(private),
                             "a private declaration is not linked: the format wants its private id, "
                             "and a page gives its name alone", private[:5])

    # -- the document ------------------------------------------------------------------------
    def companion(self) -> dict:
        name, repo_id = common.companion_identity(self.repo, self.overrides["name"], self.overrides["repo_id"])
        self.repo_id = repo_id
        return {"name": name, "repo": repo_id, "commit": self.head, "generated_at": self.head_date,
                "generator": common.generator("roadmap-locators")}

    def pdf_source(self) -> dict:
        if not re.fullmatch(r"https://[^\s#\\]+", self.pdf_url or ""):
            raise ExportError(f"--pdf-url {self.pdf_url!r} is not an https URL with no fragment",
                              hint="the overlay fetches the PDF from it and verifies its sha256")
        fields = common.pdf_source_fields(self.pdf_sha256, source_id=self.overrides["source_id"],
                                          title=self.overrides["title"], edition=self.overrides["edition"],
                                          offset=self.overrides["offset"])
        width, height = self.layout.sizes[0]
        source = {**fields, "kind": "pdf", "sha256": self.pdf_sha256, "bytes": self.pdf_bytes,
                  "url": self.pdf_url, "physical_page_count": len(self.layout.sizes),
                  "page_size": {"width_pt": width, "height_pt": height}}
        self.source_id = source["id"]
        return source

    def records(self) -> tuple[list[dict], list[dict], list[dict]]:
        """The items, their locations and their links, in the order the manuscript has them."""
        items, links = [], []
        located: dict[str, dict] = {}
        for rid, entry in sorted(self.passages.items(), key=lambda kv: (kv[1]["spans"][0], kv[0])):
            title = entry["heading"]
            record = {"id": rid, "id_source": "declared", "form": "passage", "source": self.source_id,
                      "kind": "passage"}
            if common.has_url(title):
                self.report.lose("item.title", 1,
                                 "a heading holding a URL would fail the format's `external-urls` "
                                 "lint, so the item is titled by its id", [entry["where"]])
                record["title"] = rid
            else:
                record["title"] = title
            section = self.source.section_at(entry["spans"][0][0])
            if section:
                record["section"] = section
            roles = []
            for name, role in sorted(entry["links"].items()):
                if name not in self.declarations:
                    continue
                roles.append(role)
                links.append({"id": f"{rid}/{role}/{name}", "item": rid, "declaration": name,
                              "role": role, "provenance": PROVENANCE_ID})
            if "key" in roles:
                record["coverage"] = "formalized"
            elif roles:
                record["coverage"] = "support_only"
            items.append(record)
            for box in self.boxes[rid]:
                location = located.setdefault(box.key, {
                    "id": box.key, "items": [],
                    "target": {"source": self.source_id, "selector": [{
                        "type": "PdfRegionSelector", "page": box.page,
                        "coordinate_space": dict(COORDINATE_SPACE),
                        "rect": {"x0": box.rect[0], "y0": box.rect[1],
                                 "x1": min(1.0, round(box.rect[0] + box.rect[2], 6)),
                                 "y1": min(1.0, round(box.rect[1] + box.rect[3], 6))}}]}})
                location["items"].append(rid)
        locations = []
        for location in located.values():
            order = list(location["items"])
            location["items"] = sorted(order)
            if order != location["items"]:
                location["x_item_order"] = order
            locations.append(location)
        return items, locations, links

    def provenance(self) -> dict:
        return {"id": PROVENANCE_ID, "actor": "author", "method": "curation",
                "confirmed_by_human": False,
                "tool": {"name": "cf export roadmap-locators", "version": common.__version__},
                "inputs": self.inputs(),
                "note": "the links are the roadmap's own: each page names its declarations and the "
                        "locator sections it cites; the boxes are those sections' lines of the TeX "
                        "file, found on the PDF by the words they print"}

    def inputs(self) -> list[dict]:
        return [file_lock(self.repo, rel) for rel in sorted(set(self.read))]

    def run(self) -> dict:
        companion = self.companion()
        self.read_locators()
        self.read_pages()
        self.place()
        source = self.pdf_source()
        self.resolve()
        items, locations, links = self.records()
        doc = {
            "schema": vocab.ANNOTATION_SCHEMA, "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
            "companion": companion,
            "sources": [source],
            "items": items,
            "locations": locations,
            "declarations": [self.declarations[name] for name in sorted(self.declarations)],
            "links": links,
            "provenance": [self.provenance()],
            "snippets": [self.snippets[name] for name in sorted(self.snippets)],
            "destinations": [self.destinations[name] for name in sorted(self.destinations)],
        }
        common.declare_profiles(doc)
        counts = self.report.counts
        for key in ("sources", "items", "declarations", "links", "locations", "snippets",
                    "destinations", "provenance"):
            counts[key] = len(doc.get(key, []))
        counts["links_by_role"] = dict(sorted(collections.Counter(l["role"] for l in links).items()))
        counts["items_linking_nothing"] = sum(1 for item in items if "coverage" not in item)
        counts["pdf"] = {"sha256": self.pdf_sha256, "bytes": self.pdf_bytes,
                         "pages": len(self.layout.sizes)}
        counts["tex"] = {"path": self.tex_rel, "sha256": self.tex_sha256, "lines": len(self.source)}
        counts["inputs"] = self.inputs()
        counts["checkout_commit"] = self.head
        return doc


def export(checkout: Path, commit: str, *, pdf: Path, pdf_url: str, blueprint: str | None = None,
           tex: str | None = None, name: str | None = None, repo_id: str | None = None,
           site_name: str = SITE, source_id: str | None = None, source_title: str | None = None,
           source_edition: str | None = None, page_offset: int | None = None) -> tuple[dict, Report]:
    """The annotation document for a roadmap whose pages cite lines of a TeX manuscript, and the report."""
    exporter = _Export(Path(checkout), commit, pdf=Path(pdf), pdf_url=pdf_url, blueprint=blueprint,
                       tex=tex, name=name, repo_id=repo_id, site_name=site_name, source_id=source_id,
                       source_title=source_title, source_edition=source_edition, page_offset=page_offset)
    return exporter.run(), exporter.report
