"""Export a PDF-overlay companion (the attribution-table convention) as an annotation document.

New code, ported from the prototype exporter of docs/annotations.md section 10
(the measure_theory paragraph) into a repository command; the design is
docs/proposals/pdf-to-companion.md section 5.4.  The convention is the one
``adapters.attribution_table`` reads:

    pdf-overlay/data/geometry-batch-?.json      regions, one per item and page
    pdf-overlay/data/target-refinement.json     region -> declarations, key or attributed;
                                                schema version 2: coverage entries (a class,
                                                the declarations the class admits, a note)
    pdf-overlay/data/faithfulness-verdicts.json adjudicated verdicts per region
    pdf-overlay/data/destinations.json          where each ledger node is rendered
    pdf-overlay/official-source.json            the PDF's download record
    source-formalization-ledger.json            the graph authority: source nodes,
                                                formalization nodes, edges

:data:`LAYOUT` is only the default spelling of that layout: ``data_dir``,
``ledger`` and ``official_source`` (``--data-dir``, ``--ledger``,
``--official-source``) move the three the convention does not fix.

Every file is read as it is; the Lean sources are scanned at the pinned commit
with ``adapters.leanbuild`` (every declaration, its namespace and privacy) and
``formal_companion_annotations.ids`` (statement-hash ids and signatures), the
snippet of each declaration the refinement lists is cut from them by
``adapters.leansnippets``, and a local build's ``.ilean`` gives an anonymous
instance its generated name when the build postdates the file's last commit.
What the format cannot carry is counted in the report's ``lossy`` and ``bent``
buckets rather than dropped in silence; docs/exporters.md lists the buckets.

Two differences from the prototype are deliberate and documented in
tests/test_export.py: the PDF is named through the skill's registry (one id
per edition) and ``companion.generator`` names this command.
"""
from __future__ import annotations

import collections
import json
import math
import re
from pathlib import Path

from adapters import leanbuild, leansnippets
from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import ids
from cflib import items as cf_items
from formal_companion_annotations import vocab

from . import common
from .common import ExportError, HeaderCutter, Report, file_lock, git_out, module_of

#: The convention's default layout, relative to the checkout.  ``data``,
#: ``ledger`` and ``official`` are the three an export may move.
LAYOUT = {
    "data": "pdf-overlay/data",
    "geometry_glob": "geometry-batch-?.json",
    "refinement": "target-refinement.json",
    "verdicts": "faithfulness-verdicts.json",
    "destinations": "destinations.json",
    "official": "pdf-overlay/official-source.json",
    "ledger": "source-formalization-ledger.json",
    # the companion's own generators, named as provenance tools when they exist
    "ledger_tool": "scripts/source_formalization_ledger.py",
    "geometry_tool": "scripts/overlay_geometry_from_layout.py",
}
#: The three layout keys an export may move, and the flag that moves each.
MOVABLE_LAYOUT = {"data": "--data-dir", "ledger": "--ledger", "official": "--official-source"}
RFC8118 = vocab.CONFORMS_TO_PDF
COORDINATE_SPACE = {"origin": "top_left", "units": "normalized", "x_axis": "right", "y_axis": "down"}
FIELD_KIND = {"structure field": "field", "projection": "projection", "structure projection": "projection"}
ARTIFACT_KIND = {"module document": "module_doc", "module documentation": "module_doc",
                 "check command": "check", "figure": "figure"}
_PRIVATE_POSITIONAL = re.compile(r"^private:([^:]+):([^:]+):L(\d+):(.+)$")
_EQUATION_LABEL = re.compile(r"^Equation \((\d+(?:\.\d+)+)\)(?::\s*(.+))?$")
_FILE_MENTION = re.compile(r"([A-Za-z_][\w']*) \(([\w/]+\.lean):\d+\)")
_AND_EQUATION = re.compile(r"^and equation \((\d+(?:\.\d+)+)\)$", re.I)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def item_identity(label: str, old_id: str, kinds) -> list[dict]:
    """One or two item records for a geometry label (a label naming two book items gives two).

    A numbered label goes through the item grammar; ``Equation (1.1)`` is the
    one shape the grammar has no kind for.  A label the grammar refuses is an
    unnumbered passage whose label is the producer's title.  So are the words
    after ``Equation (n):``, since a book prints an equation by number alone.
    """
    m = _EQUATION_LABEL.match(label)
    if m:
        rec = {"form": "numbered", "kind": "Equation", "number": m.group(1), "label": label}
        if m.group(2):
            rec["title"] = m.group(2)
            rec["title_source"] = "producer"
        return [rec]
    it, _reject = cf_items.try_parse(label, kinds)
    if it is not None and it.kind != cf_items.IN_TEXT:
        number = ".".join(str(n) for n in it.levels)
        rec = {"form": "numbered", "kind": it.kind, "number": number, "label": label}
        title, trailing = it.title, it.trailing
        sub, rest, refused = common.render_sub(common.after_number(label, number))
        if refused:
            rec["_sub_refused"] = refused
        elif sub:
            rec["sub"] = sub
            if "-" in sub or "," in sub:
                # a range or a list: the grammar kept its first group and left the rest as trailing text
                again, _ = cf_items.try_parse(f"{it.kind} {number} {rest}", kinds)
                title, trailing = (again.title, again.trailing) if again is not None else ("", rest)
        if title:
            rec["title"] = title
        out = [rec]
        tm = _AND_EQUATION.match(trailing or "")
        if tm:
            out.append({"form": "numbered", "kind": "Equation", "number": tm.group(1), "_split_from": old_id})
        elif trailing:
            rec["_trailing"] = trailing
        return out
    title = re.sub(r"^Unnumbered\s+", "", label)
    if not title.strip():
        raise ExportError(f"geometry label {label!r} ({old_id}) names neither a numbered item nor a passage title",
                          hint="an unnumbered region needs a label that says what the passage is")
    title = title[0].upper() + title[1:]
    return [{"form": "passage", "kind": "passage", "title": title, "title_source": "producer",
             "_label": label}]


#: The target-refinement table the exporter reads.  Version 2 carries *coverage
#: entries*: an entry with ``coverage`` (a class of docs/annotations.md section
#: 5.2), the declarations that class admits in ``formalization_ids``, no key
#: ids, and the curator's ``note``.  Version 1 classified the items with no
#: entry in the provenance note's prose; that reading is gone.
REFINEMENT_SCHEMA_VERSION = 2
#: The link role a coverage entry's listed declarations take, by class, as I5
#: admits: a ``partial_only`` entry lists the partial statements (``attributed``,
#: relation ``part`` or ``special_case``); the other classes list neighbouring or
#: supporting statements (``support``), an anonymous example (``example``) under
#: ``example_only``, and a private declaration that states the item (``key``)
#: under ``private_only``.
COVERAGE_CLASSES = frozenset({"partial_only", "support_only", "example_only", "private_only", "not_formalized"})
_SPECIAL_CASE = re.compile(r"\bspecial[ -]case\b", re.I)


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------

class AttributionTableExport:
    """One export: the checkout's files read once, the document built in stages."""

    def __init__(self, repo: Path, commit: str, *, name: str | None = None, repo_id: str | None = None,
                 lean_build: Path | None = None, site_name: str = "verso_literate",
                 source_id: str | None = None, source_title: str | None = None,
                 source_edition: str | None = None, page_offset: int | None = None,
                 data_dir: str | Path | None = None, ledger: str | Path | None = None,
                 official_source: str | Path | None = None):
        self.repo = repo.resolve()
        self.head, self.head_date = common.pinned_checkout(self.repo, commit)
        self.name, self.repo_id = common.companion_identity(self.repo, name, repo_id)
        self.lean_build = (lean_build or self.repo / ".lake" / "build" / "lib" / "lean").resolve()
        self.site_name = site_name
        self.overrides = {"source_id": source_id, "title": source_title, "edition": source_edition,
                          "offset": page_offset}
        self.report = Report()
        self.layout = dict(LAYOUT)
        for key, value in (("data", data_dir), ("ledger", ledger), ("official", official_source)):
            if value is not None:
                self.layout[key] = common.repo_relative(value, MOVABLE_LAYOUT[key])
        self.data = self.repo / self.layout["data"]
        for rel in ("refinement", "verdicts", "destinations"):
            if not (self.data / self.layout[rel]).is_file():
                raise ExportError(f"{self.repo} has no {self.layout['data']}/{self.layout[rel]}",
                                  hint="the attribution-table convention needs the overlay data files; "
                                       "--data-dir says where they are")
        for rel in ("official", "ledger"):
            if not (self.repo / self.layout[rel]).is_file():
                raise ExportError(f"{self.repo} has no {self.layout[rel]}",
                                  hint=f"{MOVABLE_LAYOUT[rel]} says where it is")
        self.batch_paths = sorted(self.data.glob(self.layout["geometry_glob"]))
        if not self.batch_paths:
            raise ExportError(f"{self.data} holds no {self.layout['geometry_glob']}")
        self.batches = {p.stem: load(p) for p in self.batch_paths}
        self.refinement = load(self.data / self.layout["refinement"])
        self.verdicts_in = load(self.data / self.layout["verdicts"])
        self.dest_in = load(self.data / self.layout["destinations"])
        self.official = load(self.repo / self.layout["official"])
        self.ledger = load(self.repo / self.layout["ledger"])
        self.ledger_lock = file_lock(self.repo, self.layout["ledger"])
        try:
            # Only the tracked Lean files, and a symlink only to one: pinned_checkout found those as
            # the commit has them, and it cannot see an ignored file.
            self.snippets_in, self.snippets_unlocated = leansnippets.cut(
                self.refinement, self.ledger, leansnippets.reader(self.repo, self.lean_files()))
        except leansnippets.SnippetError as exc:
            raise ExportError(str(exc), hint=f"every formalization node {self.layout['ledger']} gives a file "
                                             "must name a tracked Lean file of the checkout") from exc
        # the record arrays, keyed by id
        self.items: dict[str, dict] = {}
        self.locations: dict[str, dict] = {}
        self.declarations: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.clusters: dict[str, dict] = {}
        self.memberships: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.provenance: dict[str, dict] = {}
        self.snippets: dict[str, dict] = {}
        self.destinations: dict[str, dict] = {}
        self.verdicts: dict[str, dict] = {}
        self.changelog: dict[str, dict] = {}
        # joins
        self.region_to_loc: dict[str, str] = {}
        self.region_to_old: dict[str, str] = {}
        self.old_to_item: dict[str, str] = {}
        self.old_to_all: dict[str, list[str]] = {}
        self.ledger_to_decl: dict[str, str] = {}
        self.passage_ids: dict[str, str] = {}
        self.named: list[dict] = []
        self.anon: list[ids.AnonymousDecl] = []
        self.anon_ids: dict = {}
        self.tag_to_decl: dict[str, str] = {}
        self.replaced_notes: list[str] = []
        self.added_parents: list[str] = []
        self.ilean_used: list[str] = []
        self.ns_checks: collections.Counter = collections.Counter()
        self.support_counts: collections.Counter = collections.Counter()
        self.coverage_counts: collections.Counter = collections.Counter()
        self.table_links: set[str] = set()
        self.fallback_items: list[str] = []
        self.formalized_by_example: list[str] = []
        self.snippet_line_diff: list[str] = []
        self.width = self.height = 0.0

    # -- helpers ---------------------------------------------------------------
    def ledger_input(self, sha: str) -> dict:
        """The ledger as a provenance input: the full lock when it is the ledger at HEAD."""
        if sha == self.ledger_lock["sha256"]:
            return dict(self.ledger_lock)
        return {"path": self.layout["ledger"], "sha256": sha}

    def tool(self, key: str, name: str) -> dict | None:
        """A provenance ``tool`` naming one of the companion's own scripts, when it exists."""
        rel = self.layout[key]
        if (self.repo / rel).is_file():
            return {"name": name, "path": rel}
        self.report.lose(f"provenance.{key}_missing", 1,
                         f"the checkout has no {rel}, so the provenance names no tool")
        return None

    # -- source -----------------------------------------------------------------
    def build_source(self) -> dict:
        pdf = next(iter(self.batches.values()))["pdf"]
        for b in self.batches.values():
            if b["pdf"] != pdf or b["coordinate_space"] != COORDINATE_SPACE:
                raise ExportError(f"{b['batch_id']} describes another PDF or coordinate space")
        if not (self.official["sha256"] == pdf["sha256"] == self.ledger["source"]["sha256"]):
            raise ExportError("official-source.json, the geometry batches and the ledger disagree on the PDF")
        if (self.dest_in["ledger"]["sha256"], self.dest_in["ledger"]["bytes"]) != \
                (self.ledger_lock["sha256"], self.ledger_lock["bytes"]):
            raise ExportError("destinations.json was written against another ledger than the one on disk")
        offsets: collections.Counter = collections.Counter()
        for node in self.ledger["source_nodes"]:
            pm = re.search(r"printed pp?\. (\d+)", node.get("locator", ""))
            qm = re.search(r"PDF pp?\. (\d+)", node.get("locator", ""))
            if pm and qm:
                offsets[int(qm.group(1)) - int(pm.group(1))] += 1
        prose_offset = None
        if offsets:
            prose_offset, agree = offsets.most_common(1)[0]
            if agree < 0.9 * sum(offsets.values()):
                prose_offset = None
        fields = common.pdf_source_fields(pdf["sha256"], source_id=self.overrides["source_id"],
                                          title=self.overrides["title"], edition=self.overrides["edition"],
                                          offset=self.overrides["offset"] if self.overrides["offset"] is not None
                                          else prose_offset)
        if prose_offset is not None and prose_offset != fields["printed_page_offset"]:
            self.report.problem(f"the ledger's locators give a printed-page offset of {prose_offset}, "
                                f"the source record carries {fields['printed_page_offset']}")
        self.report.lose("source.printed_page_offset_from_prose", 1,
                         f"no companion file records the printed-page offset; the ledger's locator prose gives "
                         f"{dict(offsets)} over first printed/PDF page pairs (the outlier is a two-part "
                         f"locator) and the registry {fields['printed_page_offset']}")
        self.width = pdf["default_page_size_points"]["width"]
        self.height = pdf["default_page_size_points"]["height"]
        source = {
            "id": fields["id"], "kind": "pdf", "role": "audited", "title": fields["title"],
            "sha256": pdf["sha256"], "bytes": self.official["bytes"], "url": self.official["canonical_pdf_url"],
            "alternate_urls": sorted({self.official[k] for k in ("canonical_page_url", "final_pdf_url",
                                                                  "legacy_page_url")}
                                     - {self.official["canonical_pdf_url"]}),
            "retrieved_at": self.official["retrieved_at_utc"],
            "physical_page_count": pdf["physical_page_count"],
            "printed_page_offset": fields["printed_page_offset"],
            "page_size": {"width_pt": self.width, "height_pt": self.height},
            "x_download_checks": {k: self.official[k] for k in ("pdf_header", "pdf_eof", "redirect_chain")},
            "x_companion_edition": self.ledger["source"]["revision"],
        }
        if "edition" in fields:
            source["edition"] = fields["edition"]
        self.report.bend("source.x_download_checks", 1,
                         "official-source.json's download checks (pdf_header, pdf_eof, redirect_chain) have no "
                         "source field; kept in an x_ key")
        self.report.bend("source.x_companion_edition", 1,
                         "the ledger's own words for the edition (source.revision) are kept in an x_ key; the "
                         "source's id, title and edition are the registry's for these bytes")
        self.report.lose("source.path", 1,
                         f"ledger source.path ({self.ledger['source']['path']}) is relative to an external "
                         "workspace, not the companion repo; source.path is a repo path, so it was not carried")
        self.source_id = fields["id"]
        self.offset = fields["printed_page_offset"]
        return source

    # -- items and locations -----------------------------------------------------
    def rect_highlight(self, r: dict) -> str:
        left, right = math.floor(r["x0"] * self.width), math.ceil(r["x1"] * self.width)
        top, bottom = math.floor(r["y0"] * self.height), math.ceil(r["y1"] * self.height)
        return f"{left},{right},{top},{bottom}"

    def batch_provenance(self, batch_path: Path, batch: dict) -> str:
        """One provenance record per geometry batch; returns its id."""
        batch_id = batch_path.stem
        letter = batch_id[-1]
        prov_id = f"prov-geometry-{letter}"
        verifiers = sorted({r["visual_signoff"]["verifier_id"] for it in batch["items"] for r in it["regions"]})
        methods = sorted({r["visual_signoff"]["method"] for it in batch["items"] for r in it["regions"]})
        rel = f"{self.layout['data']}/{batch_path.name}"
        inputs = [file_lock(self.repo, rel)]
        sidecar = batch_path.with_name(batch_path.stem + ".sources.json")
        if sidecar.is_file():
            # a batch with a sources sidecar was generated from the text layer, then signed off
            rec = {"id": prov_id, "actor": "tool", "method": "layout_extraction", "confirmed_by_human": False,
                   "agents": verifiers, "inputs": inputs,
                   "note": f"{batch_id}: regions from poppler pdftotext -bbox-layout line boxes, then an "
                           f"agent visual sign-off per region (signoff.method is the file's "
                           f"{', '.join(methods)})."}
            tool = self.tool("geometry_tool", Path(self.layout["geometry_tool"]).name)
            if tool:
                rec["tool"] = tool
        else:
            added = git_out(self.repo, "log", "--diff-filter=A", "--format=%h", "--", rel).splitlines()
            rec = {"id": prov_id, "actor": "agent", "method": "manual", "confirmed_by_human": False,
                   "agents": verifiers, "inputs": inputs,
                   "note": f"{batch_id}: regions drawn and visually signed off by an ff-core agent run "
                           f"(commit {added[-1] if added else 'unknown'}); no generator script."}
        self.provenance[prov_id] = rec
        return prov_id

    def build_items(self) -> None:
        kinds = tuple(cf_items.DEFAULT_KINDS) + ("Equation",)
        for batch_path in self.batch_paths:
            batch = self.batches[batch_path.stem]
            prov_id = self.batch_provenance(batch_path, batch)
            for it in batch["items"]:
                self.add_item(it, prov_id, kinds)
        self.report.lose("geometry.batch_order", len(self.batches),
                         "the in-batch ordering of items and the batch page partitions are gone; batch membership "
                         "survives as each location's provenance (prov-geometry-a..d) and inside the legacy "
                         "region/item ids")

    def add_item(self, it: dict, prov_id: str, kinds) -> None:
        recs = item_identity(it["label"], it["item_id"], kinds)
        pages = sorted({reg["page"] for reg in it["regions"]})
        new_ids: list[str] = []
        for rec in recs:
            item = {"source": self.source_id, "clusters": sorted(it["source_ids"])}
            item.update({k: v for k, v in rec.items() if not k.startswith("_")})
            if rec.get("_sub_refused"):
                self.report.problem(f"{it['item_id']}: the sub-part {rec['_sub_refused']!r} of {it['label']!r} is "
                                    "not one the format can carry (docs/annotations.md section 3.2); it was "
                                    "dropped, so the item is keyed without it")
            if rec["form"] == "numbered":
                iid = ids.item_label_id(rec["kind"], rec["number"], rec.get("sub"))
                item["id_source"] = "label"
            else:
                item["pages"] = {"first": pages[0], "basis": "physical", "anchor": "own"}
                if pages[-1] != pages[0]:
                    item["pages"]["last"] = pages[-1]
                if pages[0] - self.offset < 1:
                    raise ExportError(f"{it['item_id']} ({it['label']!r}) is an unnumbered passage on physical page "
                                      f"{pages[0]}, before printed page 1 (offset {self.offset}), so it has no "
                                      "printed page to key its id by",
                                      hint="docs/annotations.md section 5.2: a passage with neither a quote nor a "
                                           "printed page must be declared (id_source: declared)")
                iid = ids.passage_page_id(pages[0] - self.offset, rec["title"])
                item["id_source"] = "page_title_hash"
                self.passage_ids[iid] = rec["_label"]
                self.report.lose("item.passage_label", 1,
                                 "a passage's geometry label ('Unnumbered almost-everywhere logical calculus') "
                                 "is the producer's name, not a printed label, so it became title (title_source "
                                 "producer) without the 'Unnumbered ' prefix and with its first letter "
                                 "upper-cased; the verbatim label is not carried (item.label means the label as "
                                 "the source prints it)", [f"{rec['_label']!r} -> {rec['title']!r}"])
            item["id"] = iid
            if iid in self.items:
                raise ExportError(f"item id collision {iid} ({it['item_id']})")
            if "_split_from" not in rec:
                item["legacy_ids"] = [it["item_id"]]
            if rec.get("_trailing"):
                self.report.lose("item.label_trailing", 1, "label text after the parsed item that is neither a "
                                 "title nor a second item", [it["label"]])
            self.items[iid] = item
            new_ids.append(iid)
        if len(recs) > 1:
            self.report.bend("item.split_merged_label", 1,
                             "one geometry item names two book items ('Remark 1.1.3 and equation (1.1)'); exported "
                             "as two numbered items that share the location. Every curated link stays on the "
                             "remark (the curation never said which declarations state (1.1)); the geometry label "
                             "and the old item id stay on the remark only, and no item relation fits 'shown in the "
                             "same region' (relations are same_as, part_of, near); the order the label names them in "
                             "is the location's x_item_order",
                             [it["item_id"] + " -> " + ", ".join(new_ids)])
        self.old_to_item[it["item_id"]] = new_ids[0]
        self.old_to_all[it["item_id"]] = new_ids
        per_page: collections.Counter = collections.Counter()
        for reg in it["regions"]:
            self.add_location(reg, it, new_ids, per_page, prov_id)

    def add_location(self, reg: dict, it: dict, new_ids: list[str], per_page, prov_id: str) -> None:
        page = reg["page"]
        per_page[page] += 1
        lid = f"{new_ids[0]}@p{page:03d}" + (f"-{per_page[page]}" if per_page[page] > 1 else "")
        geom = reg["geometry"]
        region_sel = {"type": "PdfRegionSelector", "page": page,
                      "coordinate_space": dict(COORDINATE_SPACE, page_box="CropBox")}
        if "rect" in geom:
            x0, y0, x1, y1 = geom["rect"]
            region_sel["rect"] = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
            bbox = region_sel["rect"]
        else:
            if not geom.get("quads") or any(len(q) < 4 or any(len(pt) < 2 for pt in q[:4]) for q in geom["quads"]):
                raise ExportError(f"{reg['region_id']}: a quads region needs four (x, y) points per quad",
                                  hint="fix the geometry batch; the exporter does not guess a missing corner")
            quads = [{f"{a}{i}": q[i][0 if a == "x" else 1] for i in range(4) for a in "xy"}
                     for q in geom["quads"]]
            region_sel["quads"] = quads
            xs = [q[f"x{i}"] for q in quads for i in range(4)]
            ys = [q[f"y{i}"] for q in quads for i in range(4)]
            bbox = {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}
            self.report.lose("location.quads_highlight", 1,
                             "the RFC 8118 highlight= fallback of a quads region is its bounding box (the "
                             "PdfRegionSelector keeps the quads exactly)", [reg["region_id"]])
        so = reg["visual_signoff"]
        if so["pdf_sha256"] != self.batches[next(iter(self.batches))]["pdf"]["sha256"]:
            raise ExportError(f"{reg['region_id']} was signed off against another PDF")
        loc = {"id": lid, "items": sorted(new_ids), "provenance": prov_id,
               "pages": {"first": page, "basis": "physical"},
               "legacy_ids": [reg["region_id"]],
               "signoff": {"method": so["method"], "verifier": so["verifier_id"],
                           "evidence_id": so["evidence_id"], "evidence_sha256": so["evidence_sha256"]},
               "target": {"source": self.source_id, "selector": [
                   {"type": "FragmentSelector", "conformsTo": RFC8118, "value": f"page={page}",
                    "refinedBy": region_sel},
                   {"type": "FragmentSelector", "conformsTo": RFC8118,
                    "value": f"page={page}&highlight={self.rect_highlight(bbox)}"}]}}
        if len(new_ids) > 1:
            loc["x_item_order"] = list(new_ids)
        if lid in self.locations:
            raise ExportError(f"location id collision {lid}")
        self.locations[lid] = loc
        self.region_to_loc[reg["region_id"]] = lid
        self.region_to_old[reg["region_id"]] = it["item_id"]

    # -- the Lean at HEAD ---------------------------------------------------------
    def lean_files(self) -> list[str]:
        """Every tracked ``.lean`` file at the pinned commit, in path order."""
        out = git_out(self.repo, "ls-files", "-z", "--", "*.lean").split("\0")
        return sorted(f for f in out if f)

    def scan_lean(self) -> None:
        cutter = HeaderCutter(self.repo)
        anon_extra: dict[tuple[str, int], dict] = {}
        files = self.lean_files()
        build = leanbuild.Build.find(self.repo, self.lean_build)
        for rel in files:
            text = (self.repo / rel).read_text(encoding="utf-8")
            decls = leanbuild.parse_file(self.repo / rel, rel, build=build, text=text)
            by_line = {d.line: d for d in decls}
            file_anon = ids.scan_file(text, rel)
            lean_anon = {d.line for d in decls if d.anonymous}
            ids_anon = {a.line for a in file_anon}
            if lean_anon != ids_anon:
                self.report.problem(f"{rel}: the two scanners disagree on the anonymous declarations at lines "
                                    f"{sorted(lean_anon ^ ids_anon)}")
            for d in decls:
                if d.anonymous:
                    continue
                rec = {"file": rel, "module": module_of(rel), "kw": d.kind, "name": d.name, "full": d.full_name,
                       "namespace": d.namespace, "line": d.line, "private": d.private}
                got = cutter.cut(rel, d.line, d.kind)
                if got is not None:
                    rec["signature"], rec["statement_sha256"] = got
                self.named.append(rec)
                self.ns_checks["named"] += 1
            for a in file_anon:
                d = by_line.get(a.line)
                anon_extra[(rel, a.line)] = {"namespace": d.namespace if d else "",
                                             "private": bool(d.private) if d else False,
                                             "comment_tags": list(a.comment_tags)}
                self.ns_checks["anonymous"] += 1
            self.anon.extend(file_anon)
        self.anon_extra = anon_extra
        self.anon_ids = ids.assign_ids(self.anon)
        self.report.counts["lean_files_scanned"] = len(files)
        self.report.counts["lean_source"] = leanbuild.tally(build)
        self.report.counts["signature_skipped"] = dict(sorted(cutter.skipped.items()))

    def ilean_names(self) -> dict[tuple[str, int], str]:
        """Lean's generated name for an anonymous instance, from a build that postdates the file."""
        out: dict[tuple[str, int], str] = {}
        for a in self.anon:
            if a.keyword != "instance":
                continue
            il = self.lean_build / (a.file[:-5] + ".ilean")
            if not il.exists():
                continue
            last_commit = int(git_out(self.repo, "log", "-1", "--format=%ct", "--", a.file))
            if il.stat().st_mtime < last_commit:
                continue
            for name, pos in load(il).get("decls", {}).items():
                if pos[0] + 1 == a.line and re.match(r"^inst[A-Z]", name.rsplit(".", 1)[-1]):
                    out[(a.file, a.line)] = name
                    self.ilean_used.append(a.file[:-5] + ".ilean")
        return out

    def mk_named(self, d: dict) -> dict:
        rec = {"kind": d["kw"], "module": d["module"], "file": d["file"], "line": d["line"],
               "namespace": d["namespace"], "private": d["private"], "anonymous": False, "name": d["full"]}
        if d["private"]:
            rec.update(id=ids.private_id(d["module"], d["full"]), id_source="private")
        else:
            rec.update(id=d["full"], id_source="name")
        if "signature" in d:
            rec["signature"], rec["statement_sha256"] = d["signature"], d["statement_sha256"]
        return rec

    def build_anonymous_declarations(self) -> None:
        names = self.ilean_names()
        for a, (rid, src) in sorted(self.anon_ids.items(), key=lambda kv: (kv[0].file, kv[0].line)):
            extra = self.anon_extra[(a.file, a.line)]
            rec = {"id": rid, "id_source": src, "kind": a.keyword, "module": module_of(a.file),
                   "file": a.file, "line": a.line, "namespace": extra["namespace"], "private": extra["private"],
                   "anonymous": True, "signature": a.signature, "statement_sha256": a.statement_sha256}
            if (a.file, a.line) in names:
                rec["lean_name"] = names[(a.file, a.line)]
            for tag in extra["comment_tags"]:
                if tag in self.tag_to_decl:
                    raise ExportError(f"comment tag {tag} opens the comments of two declarations")
                self.tag_to_decl[tag] = rid
                rec.setdefault("legacy_ids", []).append(tag)
            if rid in self.declarations:
                raise ExportError(f"declaration id collision {rid}")
            self.declarations[rid] = rec
        rejected = sum(1 for a in self.anon if a.rejected_tag)
        self.report.counts["anonymous_rejected_tags"] = rejected
        self.report.bend("declaration.comment_tag_as_legacy_id", len(self.tag_to_decl),
                         "the companion's `example:<slug>` comment tags are each the first line of a longer "
                         "comment, so under section 5.1 none declares an id (ids.py reports every one in "
                         "rejected_tag); the declaration keeps its statement-hash id and the tag, which is also "
                         "the ledger's node id, goes to legacy_ids so the ledger, refinement and destinations "
                         "join through it", sorted(self.tag_to_decl)[:5])

    # -- the ledger's nodes ----------------------------------------------------------
    def build_ledger_declarations(self) -> None:
        by_full: dict[str, list[dict]] = collections.defaultdict(list)
        priv_by_module: dict[str, list[dict]] = collections.defaultdict(list)
        for d in self.named:
            by_full[d["full"]].append(d)
            if d["private"]:
                priv_by_module[d["module"]].append(d)
        self.by_full = by_full
        pending_fields: list[tuple[dict, dict | None]] = []
        for n in self.ledger["formalization_nodes"]:
            lid, kind = n["id"], n.get("kind")
            if kind in ARTIFACT_KIND:
                self.artifacts[lid] = {"id": lid, "kind": ARTIFACT_KIND[kind], "module": n["module"],
                                       "file": n["file"]}
                continue
            if lid.startswith("example:"):
                self.ledger_example(lid, n)
                continue
            if lid.startswith("private:"):
                rec = self.ledger_private(lid, n, priv_by_module, pending_fields)
                if rec is None:
                    continue
            else:
                cands = [d for d in by_full.get(lid, []) if d["file"] == n["file"]]
                if len(cands) == 1:
                    rec = self.mk_named(cands[0])
                elif kind in FIELD_KIND:
                    rec = {"id": lid, "id_source": "name", "name": lid, "kind": FIELD_KIND[kind],
                           "module": n["module"], "file": n["file"], "private": False, "anonymous": False,
                           "parent": lid.rsplit(".", 1)[0]}
                    snip = self.snippets_in.get(lid)
                    if snip:
                        rec["line"] = snip["line"]
                    pending_fields.append((rec, None))
                else:
                    raise ExportError(f"ledger node {lid} ({kind}) names no declaration at HEAD")
            self.add_ledger_declaration(lid, rec)
        self.attach_parents(pending_fields)

    def ledger_example(self, lid: str, n: dict) -> None:
        if lid in self.tag_to_decl:
            self.ledger_to_decl[lid] = self.tag_to_decl[lid]
            return
        if not ids.DECLARED_ID.match(lid):
            raise ExportError(f"ledger example id {lid} is not a declared id")
        self.declarations[lid] = {"id": lid, "id_source": "declared", "kind": "example", "module": n["module"],
                                  "file": n["file"], "private": False, "anonymous": True, "unlocated": True}
        self.ledger_to_decl[lid] = lid

    def ledger_private(self, lid: str, n: dict, priv_by_module, pending_fields) -> dict | None:
        m = _PRIVATE_POSITIONAL.match(lid)
        if m:
            module, line, short = m.group(1), int(m.group(3)), m.group(4)
            cands = [d for d in priv_by_module[module]
                     if d["name"] == short or d["full"] == short or d["full"].endswith("." + short)]
        else:
            full = lid[len("private:"):]
            module, line = n["module"], None
            cands = [d for d in priv_by_module[module] if d["full"] == full]
        if not cands and m and "." in short:
            parent_short = short.rsplit(".", 1)[0]
            pcs = [d for d in priv_by_module[module] if d["kw"] in ("structure", "class")
                   and (d["name"] == parent_short or d["full"].endswith("." + parent_short))]
            if len(pcs) == 1:
                full = pcs[0]["full"] + "." + short.rsplit(".", 1)[1]
                rec = {"id": ids.private_id(module, full), "id_source": "private", "name": full,
                       "kind": "field", "module": module, "file": n["file"], "private": True,
                       "anonymous": False, "parent": ids.private_id(module, pcs[0]["full"]),
                       "namespace": pcs[0]["namespace"], "legacy_ids": [lid]}
                pending_fields.append((rec, pcs[0]))
                self.declarations[rec["id"]] = rec
                self.ledger_to_decl[lid] = rec["id"]
                self.report.lose("declaration.private_field_kind_guessed", 1,
                                 "the ledger gives private helpers no kind; a private id that names a member of a "
                                 "private structure was exported as kind field (the ledger's public nodes "
                                 "distinguish 'structure field' from 'projection', which it cannot here)", [lid])
                return None
        if not cands:
            raise ExportError(f"ledger private node {lid} names no declaration at HEAD")
        if len(cands) > 1:
            exact = [d for d in cands if line is not None and d["line"] == line]
            if len(exact) != 1:
                raise ExportError(f"ledger private node {lid} is ambiguous at HEAD")
            cands = exact
        rec = self.mk_named(cands[0])
        if m and cands[0]["line"] != line:
            self.report.lose("declaration.private_line_stale", 1,
                             "the ledger's positional private id names a line that no longer holds the helper at "
                             "HEAD (re-pins update file hashes, not ids); resolved by module + name, the old id "
                             "kept in legacy_ids", [lid])
        return rec

    def add_ledger_declaration(self, lid: str, rec: dict) -> None:
        if rec["id"] != lid:
            rec.setdefault("legacy_ids", []).append(lid)
        if rec["id"] in self.declarations:
            prev = self.declarations[rec["id"]]
            prev["legacy_ids"] = sorted(set(prev.get("legacy_ids", []) + rec.get("legacy_ids", [])))
            self.report.lose("declaration.two_ledger_ids_one_decl", 1,
                             "two ledger ids resolve to one declaration at HEAD", [lid])
        else:
            self.declarations[rec["id"]] = rec
        self.ledger_to_decl[lid] = rec["id"]

    def attach_parents(self, pending_fields) -> None:
        """A field's or projection's parent is a structure or class; its namespace is the parent's name."""
        for rec, pdecl in pending_fields:
            parent = self.declarations.get(rec["parent"])
            if parent is None:
                hits = [pdecl] if pdecl is not None else self.by_full.get(rec["parent"], [])
                if len(hits) != 1:
                    raise ExportError(f"{rec['id']}: parent {rec['parent']} is not one declaration at HEAD")
                parent = self.mk_named(hits[0])
                if parent["id"] not in self.declarations:
                    self.added_parents.append(parent["id"])
                # a private structure is already exported under its private id: keep that record
                parent = self.declarations.setdefault(parent["id"], parent)
            rec["parent"] = parent["id"]
            if parent["kind"] not in ("structure", "class"):
                raise ExportError(f"{rec['id']}: parent {parent['id']} is a {parent['kind']}")
            rec["namespace"] = parent["name"]
            if not rec["name"].startswith(rec["namespace"] + "."):
                raise ExportError(f"{rec['id']}: its name is not under its parent's")

    # -- clusters, memberships ----------------------------------------------------
    def build_clusters(self) -> None:
        node_items: dict[str, set[str]] = collections.defaultdict(set)
        for new_ids in self.old_to_all.values():
            for iid in new_ids:
                for c in self.items[iid]["clusters"]:
                    node_items[c].add(iid)
        self.node_items = node_items
        rel_sup = self.dest_in["support_relation"]
        for n in self.ledger["source_nodes"]:
            c = {"id": n["id"], "x_plan_item_ids": sorted(n["plan_item_ids"]),
                 "x_status": n["status"], "x_semantic_status": n["semantic_status"]}
            if n.get("locator"):
                c["locator"] = n["locator"]
            if node_items.get(n["id"]):
                c["items"] = sorted(node_items[n["id"]])
            if n["id"] == rel_sup["source_id"]:
                c["related_to"] = [rel_sup["related_to"]]
            if n.get("external_admissions"):
                self.report.lose("cluster.external_admissions", 1,
                                 "a source node's external admissions have no cluster field", [n["id"]])
            self.clusters[n["id"]] = c
        self.report.bend("cluster.x_plan_status", len(self.clusters),
                         "ledger source-node plan_item_ids, status and semantic_status have no cluster field; "
                         "kept as x_ keys")
        prov = {"id": "prov-ledger", "actor": "agent", "method": "pipeline", "confirmed_by_human": False,
                "inputs": [dict(self.ledger_lock)],
                "note": "Node-level edges of source-formalization-ledger.json, a clone-local export of the "
                        "external ff-core correspondence state (snapshot "
                        + self.ledger["formalization_snapshot"]["commit"] + ")."}
        tool = self.tool("ledger_tool", Path(self.layout["ledger_tool"]).name + " export")
        if tool:
            prov["tool"] = tool
        self.provenance["prov-ledger"] = prov
        realization: collections.Counter = collections.Counter()
        for e in self.ledger["edges"]:
            key, target = self.resolve(e["formalization_id"], f"{self.layout['ledger']} edge of {e['source_id']}")
            mid = f"{e['source_id']}/{e['role']}/{target}"
            if mid in self.memberships:
                raise ExportError(f"membership id collision {mid}")
            self.memberships[mid] = {"id": mid, "cluster": e["source_id"], key: target, "role": e["role"],
                                     "provenance": "prov-ledger"}
            realization[(e["role"], e.get("realization"))] += 1
        self.report.lose("membership.realization", sum(v for (r, z), v in realization.items() if z),
                         "ledger edges carry realization ('implemented' on every primary and support edge, absent "
                         "on consumer edges: " + ", ".join(f"{r}/{z} {v}" for (r, z), v in
                                                            sorted(realization.items(), key=str))
                         + "); membership has no field for it. Constant per role in this snapshot, so "
                         "derivable today")
        self.report.lose("ledger.node_class_and_text_hash", len(self.ledger["formalization_nodes"]),
                         "ledger formalization nodes' class (realized_artifact / implementation_helper) and "
                         "verification_text_sha256 (a hash of external verified_with text) are not carried; "
                         "class is implied by the node kind and id form, the hash is recoverable from the "
                         "pinned ledger")

    def decl_of(self, fid: str, where: str) -> str:
        """The declaration id a formalization id resolves to; a refusal naming the file when the ledger lacks it."""
        did = self.ledger_to_decl.get(fid)
        if did is None:
            raise ExportError(f"{where} names {fid!r}, which {self.layout['ledger']} does not list as a formalization "
                              "node", hint="the overlay data and the ledger must come from the same snapshot; "
                                           "regenerate the one that is stale")
        return did

    def resolve(self, fid: str, where: str) -> tuple[str, str]:
        if fid in self.artifacts:
            return "artifact", fid
        return "declaration", self.decl_of(fid, where)

    # -- links -----------------------------------------------------------------------
    def build_links(self) -> None:
        where = f"{self.layout['data']}/{self.layout['refinement']}"
        if self.refinement.get("schema_version") != REFINEMENT_SCHEMA_VERSION:
            raise ExportError(f"{where} is schema_version {self.refinement.get('schema_version')!r}; the exporter "
                              f"reads version {REFINEMENT_SCHEMA_VERSION} (coverage entries)",
                              hint="version 1 classified the items with no entry in the provenance note's prose, "
                                   "which the format's coverage classes replaced; export from a commit whose "
                                   "table is version 2")
        ref_ledger = self.refinement["ledger_sha256"]
        self.provenance["prov-target-refinement"] = {
            "id": "prov-target-refinement", "actor": "agent", "method": "curation",
            "confirmed_by_human": self.refinement["provenance"]["confirmed_by_human"],
            "note": self.refinement["provenance"]["note"],
            "inputs": sorted([file_lock(self.repo, f"{self.layout['data']}/{self.layout['refinement']}"),
                              self.ledger_input(ref_ledger)], key=lambda d: d["path"]),
            "x_method": self.refinement["method"],
            "x_nodes_curated": self.refinement["provenance"]["nodes_curated"]}
        self.report.bend("provenance.x_refinement_fields", 1,
                         "target-refinement's own method name (agent_semantic_curation) and its nodes_curated "
                         "count have no provenance field; kept as x_method and x_nodes_curated")
        per_item: dict[str, list[dict]] = collections.defaultdict(list)
        for e in self.refinement["entries"]:
            loc = self.region_to_loc[e["region_id"]]
            if self.old_to_item[e["item_id"]] not in self.locations[loc]["items"]:
                raise ExportError(f"refinement entry {e['region_id']} names an item its region does not show")
            per_item[e["item_id"]].append(e)
        differing = 0
        for old, entries in sorted(per_item.items()):
            sets = {(tuple(sorted(e["formalization_ids"])), tuple(sorted(e["key_formalization_ids"])),
                     e.get("coverage"), e.get("note")) for e in entries}
            differing += len(sets) > 1
            iid = self.old_to_item[old]
            keys = {x for e in entries for x in e["key_formalization_ids"]}
            listed = {x for e in entries for x in e["formalization_ids"]}
            classes = {e.get("coverage") for e in entries}
            if len(classes) > 1:
                self.report.problem(f"{where}: the entries of {old} disagree on coverage "
                                    f"({', '.join(sorted(map(str, classes)))}); no link and no coverage was "
                                    "written for it, so it falls back to its clusters")
                continue
            coverage = next(iter(classes))
            if coverage is not None:
                if coverage not in COVERAGE_CLASSES:
                    raise ExportError(f"{where}: the entry of {old} carries coverage {coverage!r}, which is not a "
                                      f"class the table may state ({', '.join(sorted(COVERAGE_CLASSES))})")
                if keys:
                    self.report.problem(f"{where}: the {coverage} entry of {old} names key ids {sorted(keys)}; a "
                                        "coverage entry lists no key (its class says no declaration states the "
                                        "whole item); they were written with the role the class admits")
                notes = sorted({e.get("note") for e in entries if e.get("note")})
                self.coverage_entry(iid, old, coverage, sorted(listed), " ".join(notes), where)
                continue
            if keys - listed:
                self.report.lose("link.key_outside_formalization_ids", len(keys - listed),
                                 "a refinement entry names a key id that its own formalization_ids does not list; "
                                 "links follow formalization_ids, so that key is not carried",
                                 [f"{iid}: {fid}" for fid in sorted(keys - listed)])
            for fid in sorted(listed):
                role = "key" if fid in keys else "attributed"
                did = self.decl_of(fid, f"{where} entry for {old}")
                lk = f"{iid}/{role}/{did}"
                self.links[lk] = {"id": lk, "item": iid, "declaration": did, "role": role,
                                  "provenance": "prov-target-refinement"}
                self.table_links.add(lk)
        multi = sum(1 for v in per_item.values() if len(v) > 1)
        self.report.lose("link.region_granularity", multi,
                         f"target-refinement is keyed by region; {multi} items have one entry per region. Entries "
                         f"were merged per item ({differing} items had differing entries across regions, so "
                         "nothing is lost but the per-region keying itself)")
        self.support_links()

    def coverage_entry(self, iid: str, old: str, coverage: str, listed: list[str], note: str, where: str) -> None:
        """A coverage entry: the item's class and note, and its declarations with the role the class admits (I5)."""
        item = self.items[iid]
        item["coverage"] = coverage
        if note:
            if common.has_url(note):
                self.report.problem(f"{iid}: the coverage note of {old} holds a URL, which the external-urls lint "
                                    "keeps out of every non-source field; the note was dropped")
            else:
                item["coverage_note"] = note
        relation = None
        if coverage == "partial_only":
            relation = "special_case" if _SPECIAL_CASE.search(note or "") else "part"
            if relation == "special_case":
                self.report.bend("link.relation_from_note", 1,
                                 "a partial_only entry's note says 'special case', so its attributed links carry "
                                 "relation special_case; without those words the relation is part", [iid])
        if coverage == "private_only" and not listed:
            self.report.problem(f"{iid}: the private_only entry of {old} lists no declaration; I5 needs at least "
                                "one link (a key link to the private declaration)")
        for fid in listed:
            did = self.decl_of(fid, f"{where} entry for {old}")
            decl = self.declarations[did]
            if coverage == "partial_only":
                role = "attributed"
            elif coverage == "example_only" and decl["kind"] == "example":
                role = "example"
            elif coverage == "private_only" and decl["private"]:
                role = "key"
            else:
                role = "support"
            lk = f"{iid}/{role}/{did}"
            rec = {"id": lk, "item": iid, "declaration": did, "role": role, "provenance": "prov-target-refinement"}
            if relation is not None and role == "attributed":
                rec["relation"] = relation
            if lk in self.links:
                self.coverage_counts["duplicate_in_entries"] += 1
                continue
            self.links[lk] = rec
            self.table_links.add(lk)
            self.coverage_counts[f"{coverage}/{role}"] += 1
            if coverage in ("not_formalized", "example_only", "private_only") and role == "support":
                self.report.bend("link.support_role_from_coverage_class", 1,
                                 f"a {coverage} entry lists a declaration that is not what the class names (an "
                                 "anonymous example, a private declaration, or nothing); I5 admits it only as a "
                                 "support link, so that is its role", [f"{iid}: {did}"])
        self.coverage_counts[coverage] += 1

    def support_links(self) -> None:
        """A ledger support edge becomes an item-level link only where the node has exactly one item."""
        sup = self.support_counts
        for e in self.ledger["edges"]:
            if e["role"] != "support":
                continue
            its = sorted(self.node_items.get(e["source_id"], ()))
            if not its:
                sup["itemless_node"] += 1
                continue
            if e["formalization_id"] in self.artifacts:
                sup["artifact_target"] += 1
                continue
            if len(its) > 1:
                sup["multi_item_node"] += 1
                continue
            did = self.decl_of(e["formalization_id"], f"{self.layout['ledger']} support edge of {e['source_id']}")
            role = "example" if self.declarations[did]["kind"] == "example" else "support"
            lk = f"{its[0]}/{role}/{did}"
            if lk in self.table_links:
                # the refinement's coverage entry already lists this declaration with this role
                sup["already_listed_by_refinement"] += 1
                continue
            if lk in self.links:
                # a second single-item node carrying the same support edge: the link already says it
                sup["same_link_from_another_node"] += 1
                continue
            self.links[lk] = {"id": lk, "item": its[0], "declaration": did, "role": role,
                              "provenance": "prov-ledger", "raw_label": "support"}
            sup["linked_" + role] += 1
            if self.declarations[did].get("unlocated"):
                sup["linked_to_unlocated"] += 1

    # -- coverage ---------------------------------------------------------------------
    def build_coverage(self) -> None:
        """An item with no item-level link is shown through its clusters; a class for support-edge-only items.

        The format writes no flag for the cluster fallback (a reader derives it:
        an item with ``clusters`` and no links); the exporter checks that such
        an item has a populated cluster to fall back to and counts it.  An item
        whose only links are ledger support edges and whose refinement entry
        gives no class carries the class those edges imply (I5).  An item the
        table keys to an anonymous example is ``formalized``: the example
        states it (section 5.2), and the class says so.
        """
        linked_roles: dict[str, set[str]] = collections.defaultdict(set)
        keyed_by_example: set[str] = set()
        for link in self.links.values():
            linked_roles[link["item"]].add(link["role"])
            if link["role"] == "key" and self.declarations.get(link.get("declaration"), {}).get("kind") == "example":
                keyed_by_example.add(link["item"])
        populated = {m["cluster"] for m in self.memberships.values()}
        for iid, item in self.items.items():
            roles = linked_roles.get(iid, set())
            if not roles:
                if not (item.get("clusters") and populated.intersection(item["clusters"])):
                    raise ExportError(f"{iid} has no link and no populated cluster to fall back to")
                self.fallback_items.append(iid)
            elif iid in keyed_by_example and "coverage" not in item:
                # the table keys the item to an anonymous example: the example states it, so the item is
                # formalized, not example_only (docs/annotations.md section 5.2); the class is written so a
                # reader does not take the example for an illustration
                item["coverage"] = "formalized"
                self.formalized_by_example.append(iid)
            elif "key" not in roles and "coverage" not in item:
                if not roles <= {"example", "support"}:
                    raise ExportError(f"{iid} has links but no key link and no coverage class: {sorted(roles)}",
                                      hint="a refinement entry that lists declarations without a key is a coverage "
                                           "entry: give it the class those declarations state (I5)")
                # only the ledger's support edges link it: section I5's example_only or support_only
                item["coverage"] = "example_only" if roles == {"example"} else "support_only"
                self.report.bend("item.coverage_from_support_edges", 1,
                                 "an item whose only links are ledger support edges (example or support links, no "
                                 "key) carries the class those edges imply as coverage (I5)",
                                 [f"{iid}: {item['coverage']}"])
        unclassified = sorted(i for i in self.fallback_items if "coverage" not in self.items[i])
        self.report.lose("item.coverage_unclassified", len(unclassified),
                         "items with no refinement entry and no item-level link are shown through their clusters "
                         "(the format writes no flag for it) and carry no coverage class: the curation has not "
                         "said which of them are stated by an anonymous example, by a neighbour's declaration or "
                         "by nothing", unclassified[:5])
        if self.coverage_counts:
            self.report.bend("item.coverage_from_table", sum(self.coverage_counts[c] for c in COVERAGE_CLASSES),
                             "the refinement's coverage entries give the class and note; their declarations are "
                             "links with the role the class admits (partial_only: attributed with relation part or "
                             "special_case; support_only and not_formalized: support; example_only: example; "
                             "private_only: key to a private declaration)",
                             [f"{c}: {self.coverage_counts[c]}" for c in sorted(COVERAGE_CLASSES)
                              if self.coverage_counts[c]])

    # -- snippets and destinations ------------------------------------------------------
    def build_snippets(self) -> None:
        """One snippet per declaration the refinement lists, cut from the Lean (``adapters.leansnippets``)."""
        for fid, s in self.snippets_in.items():
            did = self.decl_of(fid, f"{self.layout['data']}/{self.layout['refinement']}")
            dec = self.declarations[did]
            if s["file"] != dec["file"]:
                raise ExportError(f"snippet {fid} is in {s['file']}, the declaration in {dec['file']}")
            if dec.get("line") is not None and s["line"] != dec["line"]:
                self.snippet_line_diff.append(fid)
            rec = {"id": f"snippet:{did}", "declaration": did, "text": s["text"], "line": s["line"]}
            if s["text"].endswith("…"):
                rec["truncated"] = True
            if rec["id"] in self.snippets:
                raise ExportError(f"two snippets for {did}")
            self.snippets[rec["id"]] = rec

    def build_destinations(self) -> None:
        for r in self.dest_in["destinations"]:
            key, target = self.resolve(r["formalization_id"], f"{self.layout['data']}/{self.layout['destinations']}")
            rec = {"id": f"{self.site_name}:{target}", key: target, "site": self.site_name, "resolution": r["resolution"]}
            if r["resolution"] == "exact":
                href, _, frag = r["exact_href"].partition("#")
                rec.update(href=href, fragment=frag)
            else:
                rec.update(href=r["fallback_href"], fallback_label=r["fallback_label"])
            if rec["id"] in self.destinations:
                raise ExportError(f"two destinations for {target}")
            self.destinations[rec["id"]] = rec
        self.report.lose("destinations.header", 1,
                         "destinations.json's pdf and ledger lock blocks and support_relation.relation_id are "
                         "not carried: the ledger lock equals companion.graph (checked), the pdf lock equals the "
                         "source's sha256, and the relation itself became cluster.related_to")

    # -- verdicts ----------------------------------------------------------------------------
    def build_verdicts(self) -> None:
        vin = self.verdicts_in
        diff_date = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", vin["method"])
        inputs = sorted([file_lock(self.repo, f"{self.layout['data']}/{self.layout['verdicts']}"),
                         self.ledger_input(vin["ledger_sha256"])], key=lambda d: d["path"])
        self.provenance["prov-verdicts-differential"] = {
            "id": "prov-verdicts-differential", "actor": "agent", "method": "adjudication",
            "confirmed_by_human": False, "note": vin["method"], "inputs": inputs}
        if diff_date:
            self.provenance["prov-verdicts-differential"]["at"] = diff_date.group(1)
        by_item: dict[str, list] = collections.defaultdict(list)
        for e in vin["entries"]:
            iid = self.old_to_item[self.region_to_old[e["region_id"]]]
            by_item[iid].append((self.region_to_loc[e["region_id"]], e))
        for iid, pairs in sorted(by_item.items()):
            groups: dict[str, list[str]] = collections.OrderedDict()
            for loc_id, e in pairs:
                key = json.dumps({k: v for k, v in e.items() if k != "region_id"}, sort_keys=True)
                groups.setdefault(key, []).append(loc_id)
            for n_group, (key, locs) in enumerate(groups.items(), start=1):
                self.add_verdict(iid, json.loads(key), locs, n_group, inputs)
        for v in self.verdicts.values():
            for rb in v.get("resolution", {}).get("replaced_by", []):
                if rb not in self.declarations:
                    raise ExportError(f"{v['id']} is replaced by {rb}, which is not a declaration")

    def add_verdict(self, iid: str, e: dict, locs: list[str], n_group: int, inputs: list[dict]) -> None:
        vid = f"verdict:{iid}" + (f"~{n_group}" if n_group > 1 else "")
        v = {"id": vid, "item": iid, "locations": sorted(locs), "verdict": e["verdict"],
             "severity": e["severity"], "status": e["status"], "summary": e["summary"],
             "evidence": list(e["evidence"]),
             "declarations": sorted(({"declaration": self.decl_of(d["id"], f"{self.layout['data']}/{self.layout['verdicts']}"),
                                      "verdict": d["verdict"],
                                      **({"note": d["note"]} if d.get("note") else {})}
                                     for d in e["declarations"]), key=lambda d: d["declaration"])}
        it = self.items[iid]
        sm = re.search(re.escape(f"{it['kind']} {it.get('number', '')}") + r"\((\w+'*)\)", e["summary"]) \
            if "number" in it else None
        pm = re.match(r"^In part \((\w+'*)\)", e["summary"])
        if sm or pm:
            v["sub"] = (sm or pm).group(1)
            self.report.bend("verdict.sub_from_prose", 1, "the sub-part lives only in summary prose; parsed by "
                             "regex", [f"{iid} sub {v['sub']}"])
        ev = " ".join(e["evidence"])
        fm = re.match(r"^Fixed in PR #(\d+)\.", e["summary"])
        om = re.match(r"^Open, issue #(\d+)\.", e["summary"])
        res = None
        if fm:
            res = self.fixed_resolution(iid, int(fm.group(1)), ev, e)
        elif om:
            res = {"state": "open", "issue": int(om.group(1))}
        if res is not None:
            v["resolution"] = res
        lm = re.search(r"\((CONFIRMED_CONTENT|ATTRIBUTION_GAP|CONVENTION|ERRATA|REFUTED|UNSURE), \w+\)", ev)
        if lm:
            v["x_ledger_verdict"] = lm.group(1)
            self.report.bend("verdict.x_ledger_verdict", 1,
                             "the adjudication ledger's own verdict class, read from the evidence prose, has no "
                             "verdict field (the format's vocabulary is its own); kept as x_ledger_verdict",
                             [f"{iid}: {lm.group(1)}"])
        if "method" in e:
            pid = f"prov-verdict-{slug(iid)}"
            p = {"id": pid, "actor": "agent", "method": "adjudication", "confirmed_by_human": False,
                 "note": e["method"], "inputs": list(inputs)}
            dm = re.search(r"on (\d{4}-\d{2}-\d{2})\s*$", e["method"])
            if dm:
                p["at"] = dm.group(1)
            self.provenance[pid] = p
            v["provenance"] = pid
        else:
            v["provenance"] = "prov-verdicts-differential"
        self.verdicts[vid] = v

    def named_in(self, name: str, path: str) -> list[dict]:
        """HEAD's named declarations called ``name`` in the file ``path`` (a repo path or a tail of one)."""
        return [x for x in self.named if (x["file"] == path or x["file"].endswith("/" + path))
                and (x["name"] == name or x["full"].endswith("." + name))]

    def fixed_resolution(self, iid: str, pr: int, evidence: str, e: dict) -> dict:
        """A ``fixed`` resolution: the PR, the commits its evidence names, the replacing declarations its notes name."""
        res: dict = {"state": "fixed", "pr": pr}
        commits: set[str] = set()
        for how, abbrev in re.findall(r"(commit|merge) ([0-9a-f]{7,40})\b", evidence):
            full = git_out(self.repo, "rev-parse", "--verify", abbrev + "^{commit}")
            if how == "merge":
                subject = git_out(self.repo, "log", "-1", "--format=%s", full)
                if not subject.startswith(f"Merge pull request #{pr} "):
                    raise ExportError(f"{iid}: merge commit {abbrev} does not merge PR #{pr}: {subject!r}")
            commits.add(full)
        if commits:
            res["commits"] = sorted(commits)
            self.report.lose("verdict.commit_roles", 1,
                             "resolution.commits holds the fix and merge commits as one sorted list (expanded to "
                             "full ids with git rev-parse; each merge commit's subject was checked to name the PR); "
                             "which one is the merge is not recorded", [iid])
        cands, unresolved = set(), set()
        own = {d["id"].rsplit(".", 1)[-1] for d in e["declarations"]}
        for d in e["declarations"]:
            for nm, path in _FILE_MENTION.findall(d.get("note", "")):
                if nm in own:
                    continue
                hits = self.named_in(nm, path)
                if len(hits) != 1:
                    unresolved.add(f"{nm} ({path}): {len(hits)} matches")
                    continue
                rec = self.mk_named(hits[0])
                if rec["id"] not in self.declarations:
                    self.declarations[rec["id"]] = rec
                    self.replaced_notes.append(rec["id"])
                cands.add(rec["id"])
        if cands:
            res["replaced_by"] = sorted(cands)
            self.report.bend("verdict.replaced_by_from_prose", 1,
                             "the replacing declaration is named only in note prose ('Fixed in PR #N: X "
                             "(File.lean:L)'); parsed by regex", [f"{iid} -> {sorted(cands)}"])
        evidence_only = set()
        for nm, path in _FILE_MENTION.findall(evidence):
            hits = self.named_in(nm, path)
            if nm not in own and len(hits) == 1 and cands and self.mk_named(hits[0])["id"] not in cands:
                evidence_only.add(f"{iid}: {nm} ({path})")
        if evidence_only:
            self.report.lose("verdict.replaced_by_evidence_only", len(evidence_only),
                             "a verdict's evidence names a declaration its resolution does not (the evidence names "
                             "declarations a PR added and the declaration note cites only the one that states the "
                             "item); replaced_by follows the note, the evidence text is carried in full",
                             sorted(evidence_only))
        if unresolved:
            self.report.lose("verdict.replaced_by_unresolved", len(unresolved),
                             "a '<word> (<File>.lean:<line>)' mention in a declaration note that does not resolve "
                             "to exactly one HEAD declaration in that file (prose such as 'every dimension "
                             "(...)')", sorted(unresolved))
        return res

    # -- changelog ---------------------------------------------------------------------------
    def build_changelog(self) -> None:
        for rid, a in ids.ordinal_ids(self.anon_ids):
            cid = f"ordinal/{rid}"
            self.changelog[cid] = {"id": cid, "at": self.head_date, "event": "ordinal_assigned", "subject": rid,
                                   "subject_kind": "declaration", "commit": self.head, "file": a.file,
                                   "line": a.line}

    # -- the document ----------------------------------------------------------------------------
    def run(self) -> dict:
        source = self.build_source()
        self.build_items()
        self.scan_lean()
        self.build_anonymous_declarations()
        self.build_ledger_declarations()
        self.build_clusters()
        self.build_links()
        self.build_coverage()
        self.build_snippets()
        self.build_destinations()
        self.build_verdicts()
        self.build_changelog()
        doc = {
            "schema": vocab.ANNOTATION_SCHEMA, "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
            "companion": {"name": self.name, "repo": self.repo_id, "commit": self.head,
                          "generated_at": self.head_date, "generator": common.generator("attribution-table"),
                          "graph": {"path": self.layout["ledger"], "sha256": self.ledger_lock["sha256"],
                                    "bytes": self.ledger_lock["bytes"],
                                    "snapshot_commit": self.ledger["formalization_snapshot"]["commit"]}},
            "x_graph_scope": {"scope": self.ledger["scope"],
                              "snapshot_tree": self.ledger["formalization_snapshot"]["tree"]},
            "sources": [source],
            "items": list(self.items.values()), "locations": list(self.locations.values()),
            "declarations": list(self.declarations.values()), "artifacts": list(self.artifacts.values()),
            "memberships": list(self.memberships.values()), "links": list(self.links.values()),
            "provenance": list(self.provenance.values()), "clusters": list(self.clusters.values()),
            "snippets": list(self.snippets.values()), "destinations": list(self.destinations.values()),
            "verdicts": list(self.verdicts.values()), "changelog": list(self.changelog.values()),
        }
        common.declare_profiles(doc)
        self.report.bend("root.x_graph_scope", 1,
                         "the ledger's scope name and its snapshot tree hash have no companion.graph field; kept "
                         "in a root x_ key")
        self.report.lose("ledger.integrity_blocks", 1,
                         "the ledger's own integrity machinery is not carried: files[] (pinned file hashes), "
                         "comparison_signatures, counts, provenance.canonical_inputs, authority and ledger_schema. "
                         "All are recoverable from the ledger pinned in companion.graph; none is item or "
                         "declaration data")
        absent = sorted((ids.private_id(d["module"], d["full"]) if d["private"] else d["full"])
                        for d in self.named)
        absent = [x for x in absent if x not in self.declarations]
        self.report.lose("declaration.head_not_in_ledger", len(absent),
                         "named or private declarations found at HEAD but absent from the ledger snapshot are "
                         "not exported (the document's declarations are the ledger's list, every anonymous "
                         "declaration at HEAD and the declarations the verdicts cite as replacements). A "
                         "declaration with no membership is exactly one the ledger does not list (every ledger "
                         "node has an edge)", absent[:5])
        self.count(doc)
        self.report.counts["inputs"] = self.inputs()
        return doc

    def inputs(self) -> list[dict]:
        """``{path, sha256, bytes}`` of every curation file this export read (`cf overlay export` records them)."""
        rels = [f"{self.layout['data']}/{p.name}" for p in self.batch_paths]
        rels += [f"{self.layout['data']}/{p.stem}.sources.json" for p in self.batch_paths
                 if p.with_name(p.stem + ".sources.json").is_file()]
        rels += [f"{self.layout['data']}/{self.layout[k]}"
                 for k in ("refinement", "verdicts", "destinations")]
        rels += [self.layout["official"], self.layout["ledger"]]
        return [file_lock(self.repo, rel) for rel in sorted(set(rels))]

    def count(self, doc: dict) -> None:
        """The report's counts: per array, and the breakdowns the docs cite."""
        C = collections.Counter
        counts = self.report.counts
        for k in ("sources", "items", "declarations", "artifacts", "memberships", "links", "provenance",
                  "locations", "destinations", "snippets", "verdicts", "clusters", "changelog"):
            counts[k] = len(doc.get(k, []))
        decls = doc["declarations"]
        counts["declarations_by_id_source"] = dict(sorted(C(d["id_source"] for d in decls).items()))
        counts["declarations_by_kind"] = dict(sorted(C(d["kind"] for d in decls).items()))
        counts["declarations_unlocated"] = sum(1 for d in decls if d.get("unlocated"))
        counts["declarations_with_signature"] = dict(sorted(C(("anonymous" if d["anonymous"] else "named")
                                                              for d in decls if "signature" in d).items()))
        counts["declarations_with_legacy_ids"] = sum(1 for d in decls if "legacy_ids" in d)
        counts["declarations_with_lean_name"] = sum(1 for d in decls if "lean_name" in d)
        counts["declarations_added_as_replacements"] = sorted(self.replaced_notes)
        counts["declarations_added_as_parents"] = sorted(self.added_parents)
        counts["artifacts_by_kind"] = dict(sorted(C(a["kind"] for a in doc["artifacts"]).items()))
        counts["memberships_by_role"] = dict(sorted(C(m["role"] for m in doc["memberships"]).items()))
        counts["memberships_to_artifacts"] = sum(1 for m in doc["memberships"] if "artifact" in m)
        counts["memberships_to_unlocated"] = sum(1 for m in doc["memberships"]
                                                 if self.declarations.get(m.get("declaration"), {}).get("unlocated"))
        counts["links_by_role"] = dict(sorted(C(link["role"] for link in doc["links"]).items()))
        counts["links_by_provenance"] = dict(sorted(C(link["provenance"] for link in doc["links"]).items()))
        counts["ledger_support_edges"] = dict(sorted(self.support_counts.items()))
        counts["items_by_form"] = dict(sorted(C(i["form"] for i in doc["items"]).items()))
        counts["items_by_id_source"] = dict(sorted(C(i["id_source"] for i in doc["items"]).items()))
        counts["items_by_kind"] = dict(sorted(C(i["kind"] for i in doc["items"]).items()))
        counts["items_by_coverage"] = dict(sorted(C(i.get("coverage", "-") for i in doc["items"]).items()))
        counts["items_with_clusters_and_no_links"] = len(self.fallback_items)
        counts["items_stated_by_an_anonymous_example_given_coverage_formalized"] = sorted(self.formalized_by_example)
        counts["items_with_coverage_note"] = sum(1 for i in doc["items"] if "coverage_note" in i)
        counts["coverage_entries"] = dict(sorted(self.coverage_counts.items()))
        counts["links_by_relation"] = dict(sorted(C(link.get("relation", "-") for link in doc["links"]).items()))
        counts["profiles"] = dict(doc.get("profiles", {}))
        counts["verdicts_without_resolution"] = sum(1 for v in doc["verdicts"] if "resolution" not in v)
        counts["passage_ids"] = {k: self.passage_ids[k] for k in sorted(self.passage_ids)}
        counts["locations_by_shape"] = dict(sorted(C(
            "quads" if "quads" in loc["target"]["selector"][0]["refinedBy"] else "rect"
            for loc in doc["locations"]).items()))
        counts["destinations_by_resolution"] = dict(sorted(C(d["resolution"] for d in doc["destinations"]).items()))
        counts["destinations_to_artifacts"] = sum(1 for d in doc["destinations"] if "artifact" in d)
        counts["destination_rows_in"] = len(self.dest_in["destinations"])
        counts["verdict_resolutions"] = {v["id"]: v.get("resolution", "(none: open)") for v in doc["verdicts"]}
        counts["snippets_line_differs_from_scan"] = len(self.snippet_line_diff)
        counts["snippets_truncated"] = sum(1 for s in doc["snippets"] if s.get("truncated"))
        counts["snippets_unlocated"] = list(self.snippets_unlocated)
        if (self.data / leansnippets.RETIRED_SNIPPETS).is_file():
            counts["ignored_inputs"] = [f"{self.layout['data']}/{leansnippets.RETIRED_SNIPPETS}"]
        counts["anonymous_at_head"] = len(self.anon)
        counts["comment_tags_mapped_to_ledger"] = len(self.tag_to_decl)
        counts["named_scanned_at_head"] = len(self.named)
        counts["declarations_scanned"] = dict(self.ns_checks)
        counts["ilean_files_read"] = sorted(set(self.ilean_used))
        counts["ledger"] = {"formalization_nodes": len(self.ledger["formalization_nodes"]),
                            "edges": len(self.ledger["edges"]), "source_nodes": len(self.ledger["source_nodes"]),
                            "mapped_to_declarations": len(self.ledger_to_decl),
                            "mapped_to_artifacts": len(self.artifacts)}
        counts["repo_head"] = self.head
        xkeys: collections.Counter = collections.Counter()
        for name, arr in doc.items():
            if name.startswith("x_"):
                xkeys[f"$.{name}"] += 1
            elif isinstance(arr, list):
                for r in arr:
                    for k in r:
                        if k.startswith("x_"):
                            xkeys[f"{name}[].{k}"] += 1
        counts["x_keys"] = dict(sorted(xkeys.items()))


def export(repo: Path, commit: str, **options) -> tuple[dict, Report]:
    """The document and the report for one attribution-table companion checkout."""
    job = AttributionTableExport(repo, commit, **options)
    doc = job.run()
    return doc, job.report
