#!/usr/bin/env python3
"""Prototype: export measure_theory's overlay data as a lean-companion/annotations 1.0 document.

Read-only on the companion checkout.  Reads the four geometry batches,
target-refinement.json, faithfulness-verdicts.json, declaration-snippets.json,
destinations.json, official-source.json and source-formalization-ledger.json;
scans the Lean files at the checkout's HEAD for declaration keywords, names,
namespaces and header cuts, and derives anonymous ids with the skill's
reference implementation (scripts/annotations/ids.py).  When a local build
has the .ilean file of a module with anonymous instances, Lean's generated
instance name is read from it (after checking the build postdates the file's
last commit and the positions match).

Usage:
    python3 export_mt.py [--repo DIR] [--skill DIR] [--out FILE] [--report FILE]

Every place where the source data still does not fit the schema is counted in
the report (a JSON file beside the output), under "lossy" and "bent".  The
document is written in canonical form (docs/annotations.md section 7).
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import glob
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = Path("/Users/jack/Desktop/measure_theory")
DEFAULT_SKILL = Path("/Users/jack/Desktop/cf-wt/annotations")

SOURCE_ID = "tao-measure-pdf"
SITE = "verso_literate"
DATA = "pdf-overlay/data"
LEDGER = "source-formalization-ledger.json"
RFC8118 = "http://tools.ietf.org/rfc/rfc8118"


def load(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def file_lock(repo: Path, rel: str) -> dict:
    data = (repo / rel).read_bytes()
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


# ---------------------------------------------------------------------------
# Lean scan: namespaces, named declarations and their header cuts
# ---------------------------------------------------------------------------

KW = ("theorem", "lemma", "def", "abbrev", "structure", "class", "inductive",
      "instance", "example", "opaque", "axiom")
MODS = ("private", "protected", "noncomputable", "unsafe", "partial", "nonrec")
DECL_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t\n]*)*"
    r"(?P<mods>(?:(?:" + "|".join(MODS) + r")[ \t]+)*)"
    r"(?P<kw>" + "|".join(KW) + r")(?![\w'!?.])(?P<rest>.*)$", re.M)
NAME_RE = re.compile(r"[ \t]+(?P<name>[^\s:({\[⦃]+)")
NS_RE = re.compile(r"^[ \t]*namespace[ \t]+(\S+)", re.M)
SEC_RE = re.compile(r"^[ \t]*(?:noncomputable[ \t]+)?section\b[ \t]*(\S*)", re.M)
END_RE = re.compile(r"^[ \t]*end\b[ \t]*(\S*)", re.M)


def namespace_index(code: str):
    """``ns_at(offset)``: the enclosing namespace at an offset of comment-blanked code."""
    events = [(m.start(), "ns", m.group(1)) for m in NS_RE.finditer(code)]
    events += [(m.start(), "sec", m.group(1)) for m in SEC_RE.finditer(code)]
    events += [(m.start(), "end", m.group(1)) for m in END_RE.finditer(code)]
    events.sort()
    positions, values, stack = [], [], []
    for pos, ev, arg in events:
        if ev == "ns":
            stack.append(arg.split("."))
        elif ev == "sec":
            stack.append([])
        elif ev == "end" and stack:
            stack.pop()
        positions.append(pos)
        values.append(".".join(p for parts in stack for p in parts))

    def ns_at(offset: int) -> str:
        i = bisect.bisect_left(positions, offset)
        return values[i - 1] if i else ""
    return ns_at


def scan_named(text: str, rel: str, ids_mod) -> tuple[list[dict], object, str]:
    """Named declarations, read from the comment- and literal-blanked copy (ids.blank_literals)."""
    code, _spans = ids_mod.strip_comments(text)
    scan = ids_mod.blank_literals(code)
    ns_at = namespace_index(scan)
    out = []
    for m in DECL_RE.finditer(scan):
        kw, rest = m.group("kw"), m.group("rest")
        name = None
        nm = NAME_RE.match(rest)
        if nm and kw != "example":
            cand = nm.group("name")
            if cand not in (":", "where") and cand[0] not in "([":
                name = cand
        if kw == "instance" and rest.lstrip().startswith("(priority"):
            pr = re.match(r"\s*\(priority[^)]*\)\s*(?P<name>[^\s:({\[⦃]+)?", rest)
            name = pr.group("name") if pr and pr.group("name") not in (None, ":") else None
        if name is None:
            continue
        start = m.start("kw")
        ns = ns_at(start)
        full = name[len("_root_."):] if name.startswith("_root_.") else ".".join(x for x in (ns, name) if x)
        rec = dict(file=rel, module=rel[:-5].replace("/", "."), kw=kw, name=name, full=full, namespace=ns,
                   line=code.count("\n", 0, start) + 1, private="private" in m.group("mods").split())
        # section 5.1's cut, the same as an anonymous declaration's; none when no := or where ends it
        sig = ids_mod.named_signature(code, scan, start, kw)
        if sig is not None:
            rec["signature"] = sig
            rec["statement_sha256"] = ids_mod.statement_sha256(sig)
        out.append(rec)
    return out, ns_at, scan


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def item_identity(label: str, old_id: str, items_mod) -> list[dict]:
    """One or two item records (a label naming two book items gives two)."""
    kinds = tuple(items_mod.DEFAULT_KINDS) + ("Equation",)
    m = re.match(r"^Equation \((\d+(?:\.\d+)+)\)(?::\s*(.+))?$", label)
    if m:
        rec = {"form": "numbered", "kind": "Equation", "number": m.group(1), "label": label}
        if m.group(2):
            rec["title"] = m.group(2)
        return [rec]
    it, _rej = items_mod.try_parse(label, kinds)
    if it is not None and it.kind != items_mod.IN_TEXT:
        number = ".".join(str(n) for n in it.levels)
        rec = {"form": "numbered", "kind": it.kind, "number": number, "label": label}
        if it.title:
            rec["title"] = it.title
        if it.sub:
            rec["sub"] = ",".join(re.findall(r"\(([^)]+)\)", it.sub))
        out = [rec]
        tm = re.match(r"^and equation \((\d+(?:\.\d+)+)\)$", it.trailing or "", re.I)
        if tm:
            out.append({"form": "numbered", "kind": "Equation", "number": tm.group(1), "_split_from": old_id})
        elif it.trailing:
            rec["_trailing"] = it.trailing
        return out
    # unnumbered passage: the geometry label is the producer's words, not a printed label
    title = re.sub(r"^Unnumbered\s+", "", label)
    title = title[0].upper() + title[1:]
    return [{"form": "passage", "kind": "passage", "title": title, "title_source": "producer",
             "_label": label}]


# ---------------------------------------------------------------------------
# main export
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    ap.add_argument("--out", type=Path, default=HERE / "measure_theory.annotations.json")
    ap.add_argument("--report", type=Path, default=HERE / "measure_theory.export-report.json")
    args = ap.parse_args(argv)
    repo, skill = args.repo.resolve(), args.skill.resolve()
    sys.path.insert(0, str(skill / "scripts"))
    from annotations import ids as ids_mod                                  # noqa: E402
    from annotations import validate as V                                    # noqa: E402
    from cflib import items as items_mod                                     # noqa: E402
    from adapters import leanparse                                           # noqa: E402

    D = repo / DATA
    batch_files = sorted(glob.glob(str(D / "geometry-batch-?.json")))
    batches = {Path(p).stem: load(Path(p)) for p in batch_files}
    refinement = load(D / "target-refinement.json")
    verdicts_in = load(D / "faithfulness-verdicts.json")
    snippets_in = load(D / "declaration-snippets.json")
    dest_in = load(D / "destinations.json")
    official = load(repo / "pdf-overlay" / "official-source.json")
    ledger = load(repo / LEDGER)
    ledger_lock = file_lock(repo, LEDGER)

    def ledger_input(sha: str) -> dict:
        """The ledger as a provenance input: the full lock when it is the ledger at HEAD."""
        return dict(ledger_lock) if sha == ledger_lock["sha256"] else {"path": LEDGER, "sha256": sha}

    lossy: dict[str, dict] = {}
    bent: dict[str, dict] = {}

    def note(bucket: dict, key: str, count: int, detail: str, examples=None):
        entry = bucket.setdefault(key, {"count": 0, "detail": detail, "examples": []})
        entry["count"] += count
        for ex in (examples or []):
            if len(entry["examples"]) < 5:
                entry["examples"].append(ex)

    head = git(repo, "rev-parse", "HEAD")
    head_epoch = int(git(repo, "log", "-1", "--format=%ct"))
    head_date = dt.datetime.fromtimestamp(head_epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dirty = git(repo, "status", "--porcelain")

    # ---------------- source ----------------
    pdf = next(iter(batches.values()))["pdf"]
    for b in batches.values():
        assert b["pdf"] == pdf and b["coordinate_space"] == {"origin": "top_left", "units": "normalized",
                                                             "x_axis": "right", "y_axis": "down"}
    assert official["sha256"] == pdf["sha256"] == ledger["source"]["sha256"]
    assert dest_in["ledger"]["sha256"] == ledger_lock["sha256"] and dest_in["ledger"]["bytes"] == ledger_lock["bytes"]
    offsets = collections.Counter()
    for node in ledger["source_nodes"]:
        pm = re.search(r"printed pp?\. (\d+)", node.get("locator", ""))
        qm = re.search(r"PDF pp?\. (\d+)", node.get("locator", ""))
        if pm and qm:
            offsets[int(qm.group(1)) - int(pm.group(1))] += 1
    offset, agree = offsets.most_common(1)[0]
    assert agree >= 0.9 * sum(offsets.values()), offsets
    note(lossy, "source.printed_page_offset_from_prose", 1,
         f"no measure_theory file records the printed-page offset; {offset} was read from ledger locator "
         f"prose ({dict(offsets)} over first printed/PDF page pairs; the outlier is a two-part locator)")
    width, height = pdf["default_page_size_points"]["width"], pdf["default_page_size_points"]["height"]
    source = {
        "id": SOURCE_ID, "kind": "pdf", "role": "audited",
        "title": "An Introduction to Measure Theory (Terence Tao)",
        "edition": ledger["source"]["revision"],
        "sha256": pdf["sha256"], "bytes": official["bytes"], "url": official["canonical_pdf_url"],
        "alternate_urls": sorted({official[k] for k in ("canonical_page_url", "final_pdf_url", "legacy_page_url")}
                                 - {official["canonical_pdf_url"]}),
        "retrieved_at": official["retrieved_at_utc"],
        "physical_page_count": pdf["physical_page_count"], "printed_page_offset": offset,
        "page_size": {"width_pt": width, "height_pt": height},
        "x_download_checks": {k: official[k] for k in ("pdf_header", "pdf_eof", "redirect_chain")},
    }
    note(bent, "source.x_download_checks", 1,
         "official-source.json's download checks (pdf_header %PDF-1.4, pdf_eof %%EOF, an empty redirect_chain) "
         "have no source field; kept in an x_ key")
    note(lossy, "source.path", 1,
         "ledger source.path (sources/gsm-126-tao5-measure-book.pdf) is relative to the external ff-core "
         "workspace, not the companion repo; source.path is a repo path, so it was not carried")

    # ---------------- items + locations ----------------
    items: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    region_to_loc: dict[str, str] = {}
    region_to_old: dict[str, str] = {}
    old_to_item: dict[str, str] = {}
    old_to_all: dict[str, list[str]] = {}
    provenance: dict[str, dict] = {}

    def rect_highlight(r):
        left, right = math.floor(r["x0"] * width), math.ceil(r["x1"] * width)
        top, bottom = math.floor(r["y0"] * height), math.ceil(r["y1"] * height)
        return f"{left},{right},{top},{bottom}"

    passage_ids: dict[str, str] = {}
    for batch_path in batch_files:
        batch_id = Path(batch_path).stem
        batch = batches[batch_id]
        letter = batch_id[-1]
        prov_id = f"prov-geometry-{letter}"
        verifiers = sorted({r["visual_signoff"]["verifier_id"] for it in batch["items"] for r in it["regions"]})
        inputs = [file_lock(repo, f"{DATA}/{batch_id}.json")]
        if letter == "d":
            provenance[prov_id] = {
                "id": prov_id, "actor": "tool", "method": "layout_extraction", "confirmed_by_human": False,
                "tool": {"name": "overlay_geometry_from_layout.py",
                         "path": "scripts/overlay_geometry_from_layout.py"},
                "agents": verifiers, "inputs": inputs,
                "note": "geometry-batch-d: regions from poppler pdftotext -bbox-layout line boxes, then an "
                        "agent visual sign-off per region (signoff.method is the file's manual_visual)."}
        else:
            provenance[prov_id] = {
                "id": prov_id, "actor": "agent", "method": "manual", "confirmed_by_human": False,
                "agents": verifiers, "inputs": inputs,
                "note": f"{batch_id}: regions drawn and visually signed off by an ff-core agent run "
                        f"(commit a5d5ca0); no generator script."}
        for it in batch["items"]:
            recs = item_identity(it["label"], it["item_id"], items_mod)
            pages = sorted({reg["page"] for reg in it["regions"]})
            new_ids = []
            for rec in recs:
                item = {"source": SOURCE_ID, "clusters": sorted(it["source_ids"])}
                item.update({k: v for k, v in rec.items() if not k.startswith("_")})
                if rec["form"] == "numbered":
                    iid = ids_mod.item_label_id(rec["kind"], rec["number"], rec.get("sub"))
                    item["id_source"] = "label"
                else:
                    item["pages"] = {"first": pages[0], "basis": "physical", "anchor": "own"}
                    if pages[-1] != pages[0]:
                        item["pages"]["last"] = pages[-1]
                    iid = ids_mod.passage_page_id(pages[0] - offset, rec["title"])
                    item["id_source"] = "page_title_hash"
                    passage_ids[iid] = rec["_label"]
                    note(lossy, "item.passage_label", 1,
                         "a passage's geometry label ('Unnumbered almost-everywhere logical calculus') is the "
                         "producer's name, not a printed label, so it became title (title_source producer) "
                         "without the 'Unnumbered ' prefix and with its first letter upper-cased; the verbatim "
                         "label is not carried (item.label means the label as the source prints it)",
                         [f"{rec['_label']!r} -> {rec['title']!r}"])
                item["id"] = iid
                if iid in items:
                    raise SystemExit(f"item id collision {iid} ({it['item_id']})")
                if "_split_from" not in rec:
                    item["legacy_ids"] = [it["item_id"]]
                if rec.get("_trailing"):
                    note(lossy, "item.label_trailing", 1, "label text after the parsed item that is neither a "
                         "title nor a second item", [it["label"]])
                items[iid] = item
                new_ids.append(iid)
            if len(recs) > 1:
                note(bent, "item.split_merged_label", 1,
                     "one geometry item names two book items ('Remark 1.1.3 and equation (1.1)'); exported as two "
                     "numbered items that share the location. Every curated link stays on the remark (the "
                     "curation never said which declarations state (1.1)); the geometry label and the old item "
                     "id stay on the remark only, and no item relation fits 'shown in the same region' "
                     "(relations are same_as, part_of, near)",
                     [it["item_id"] + " -> " + ", ".join(new_ids)])
            old_to_item[it["item_id"]] = new_ids[0]
            old_to_all[it["item_id"]] = new_ids
            per_page = collections.Counter()
            for reg in it["regions"]:
                page = reg["page"]
                per_page[page] += 1
                lid = f"{new_ids[0]}@p{page:03d}" + (f"-{per_page[page]}" if per_page[page] > 1 else "")
                geom = reg["geometry"]
                region_sel = {"type": "PdfRegionSelector", "page": page,
                              "coordinate_space": {"origin": "top_left", "units": "normalized",
                                                   "x_axis": "right", "y_axis": "down", "page_box": "CropBox"}}
                if "rect" in geom:
                    x0, y0, x1, y1 = geom["rect"]
                    region_sel["rect"] = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
                    bbox = region_sel["rect"]
                else:
                    quads = [{f"{a}{i}": q[i][0 if a == "x" else 1] for i in range(4) for a in "xy"}
                             for q in geom["quads"]]
                    region_sel["quads"] = quads
                    xs = [q[f"x{i}"] for q in quads for i in range(4)]
                    ys = [q[f"y{i}"] for q in quads for i in range(4)]
                    bbox = {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}
                    note(lossy, "location.quads_highlight", 1,
                         "the RFC 8118 highlight= fallback of a quads region is its bounding box (the "
                         "PdfRegionSelector keeps the quads exactly)", [reg["region_id"]])
                so = reg["visual_signoff"]
                assert so["pdf_sha256"] == pdf["sha256"]
                loc = {"id": lid, "items": sorted(new_ids), "provenance": prov_id,
                       "pages": {"first": page, "basis": "physical"},
                       "legacy_ids": [reg["region_id"]],
                       "signoff": {"method": so["method"], "verifier": so["verifier_id"],
                                   "evidence_id": so["evidence_id"], "evidence_sha256": so["evidence_sha256"]},
                       "target": {"source": SOURCE_ID, "selector": [
                           {"type": "FragmentSelector", "conformsTo": RFC8118,
                            "value": f"page={page}", "refinedBy": region_sel},
                           {"type": "FragmentSelector", "conformsTo": RFC8118,
                            "value": f"page={page}&highlight={rect_highlight(bbox)}"}]}}
                assert lid not in locations, lid
                locations[lid] = loc
                region_to_loc[reg["region_id"]] = lid
                region_to_old[reg["region_id"]] = it["item_id"]
    note(lossy, "geometry.batch_order", len(batches),
         "the in-batch ordering of items and the batch page partitions are gone; batch membership survives as "
         "each location's provenance (prov-geometry-a..d) and inside the legacy region/item ids")

    # ---------------- Lean scan ----------------
    lean_files = sorted(str(p.relative_to(repo)) for p in (repo / "MeasureTheory").rglob("*.lean"))
    if (repo / "MeasureTheory.lean").exists():
        lean_files.insert(0, "MeasureTheory.lean")
    named, anon = [], []
    anon_extra: dict[tuple[str, int], dict] = {}
    ns_checks = collections.Counter()
    for rel in lean_files:
        text = (repo / rel).read_text(encoding="utf-8")
        found, ns_at, code = scan_named(text, rel, ids_mod)
        named.extend(found)
        file_anon = ids_mod.scan_file(text, rel)
        anon.extend(file_anon)
        _code2, spans = ids_mod.strip_comments(text)
        assert ids_mod.blank_literals(_code2) == code
        by_line = {}
        for m in ids_mod._DECL_RE.finditer(code):
            by_line[code.count("\n", 0, m.start("kw")) + 1] = m
        for a in file_anon:
            m = by_line[a.line]
            tags = []
            for ca, cb, kind in ids_mod._comments_above(text, spans, m.start()):
                body = ids_mod._comment_body(text, ca, cb, kind)
                tm = re.match(r"^`((?:example|instance):[^`\s]+)`", body)
                if tm:
                    tags.append(tm.group(1))
            anon_extra[(rel, a.line)] = {"namespace": ns_at(m.start("kw")),
                                         "private": "private" in (m.group("mods") or "").split(),
                                         "comment_tags": tags}
        # cross-check the namespace against the skill's own parser where both see a declaration
        lp = {d.line: d.namespace for d in leanparse.parse_file(repo / rel, rel)}
        for d in found:
            if d["line"] in lp:
                ns_checks["named_agree" if lp[d["line"]] == d["namespace"] else "named_differ"] += 1
        for a in file_anon:
            if a.line in lp:
                ns_checks["anonymous_agree" if lp[a.line] == anon_extra[(rel, a.line)]["namespace"]
                          else "anonymous_differ"] += 1
    anon_ids = ids_mod.assign_ids(anon)
    assert all(src == "statement_hash" for _rid, src in anon_ids.values()), "a declared id appeared"
    by_full = collections.defaultdict(list)
    priv_by_module = collections.defaultdict(list)
    for d in named:
        by_full[d["full"]].append(d)
        if d["private"]:
            priv_by_module[d["module"]].append(d)

    # Lean's generated names for anonymous instances, from a local build when it is current
    ilean_names: dict[tuple[str, int], str] = {}
    ilean_used = []
    for a in anon:
        if a.keyword != "instance":
            continue
        il = repo / ".lake/build/lib/lean" / (a.file[:-5] + ".ilean")
        if not il.exists():
            continue
        last_commit = int(git(repo, "log", "-1", "--format=%ct", "--", a.file))
        if il.stat().st_mtime < last_commit:
            continue
        for name, pos in load(il).get("decls", {}).items():
            if pos[0] + 1 == a.line and re.match(r"^inst[A-Z]", name.rsplit(".", 1)[-1]):
                ilean_names[(a.file, a.line)] = name
                ilean_used.append(str(il.relative_to(repo)))

    declarations: dict[str, dict] = {}
    ledger_to_decl: dict[str, str] = {}
    artifacts: dict[str, dict] = {}

    def mk_named(d: dict) -> dict:
        rec = {"kind": d["kw"], "module": d["module"], "file": d["file"], "line": d["line"],
               "namespace": d["namespace"], "private": d["private"], "anonymous": False, "name": d["full"]}
        if d["private"]:
            rec.update(id=ids_mod.private_id(d["module"], d["full"]), id_source="private")
        else:
            rec.update(id=d["full"], id_source="name")
        if "signature" in d:
            rec["signature"], rec["statement_sha256"] = d["signature"], d["statement_sha256"]
        return rec

    # anonymous declarations: every one at HEAD
    tag_to_decl: dict[str, str] = {}
    for a, (rid, src) in sorted(anon_ids.items(), key=lambda kv: (kv[0].file, kv[0].line)):
        extra = anon_extra[(a.file, a.line)]
        rec = {"id": rid, "id_source": src, "kind": a.keyword, "module": a.file[:-5].replace("/", "."),
               "file": a.file, "line": a.line, "namespace": extra["namespace"], "private": extra["private"],
               "anonymous": True, "signature": a.signature, "statement_sha256": a.statement_sha256}
        if (a.file, a.line) in ilean_names:
            rec["lean_name"] = ilean_names[(a.file, a.line)]
        for tag in extra["comment_tags"]:
            assert a.rejected_tag, (a, tag)
            assert tag not in tag_to_decl, tag
            tag_to_decl[tag] = rid
            rec.setdefault("legacy_ids", []).append(tag)
        assert rid not in declarations, rid
        declarations[rid] = rec
    rejected = [a for a in anon if a.rejected_tag]
    assert {(a.file, a.line) for a in rejected} == {k for k, v in anon_extra.items() if v["comment_tags"]}
    note(bent, "declaration.comment_tag_as_legacy_id", len(tag_to_decl),
         "the companion's `example:<slug>` comment tags are each the first line of a longer comment, so under "
         "section 5.1 none declares an id (ids.py reports every one in rejected_tag); the declaration keeps its "
         "statement-hash id and the tag, which is also the ledger's node id, goes to legacy_ids so the ledger, "
         "refinement and destinations join through it", sorted(tag_to_decl)[:5])

    # ledger formalization nodes
    FIELD_KIND = {"structure field": "field", "projection": "projection", "structure projection": "projection"}
    ARTIFACT_KIND = {"module document": "module_doc", "module documentation": "module_doc",
                     "check command": "check", "figure": "figure"}
    pending_fields = []
    unlocated_bad = []
    for n in ledger["formalization_nodes"]:
        lid, kind = n["id"], n.get("kind")
        if kind in ARTIFACT_KIND:
            artifacts[lid] = {"id": lid, "kind": ARTIFACT_KIND[kind], "module": n["module"], "file": n["file"]}
            continue
        if lid.startswith("example:"):
            if lid in tag_to_decl:
                ledger_to_decl[lid] = tag_to_decl[lid]
                continue
            if not ids_mod.DECLARED_ID.match(lid):
                unlocated_bad.append(lid)
                continue
            declarations[lid] = {"id": lid, "id_source": "declared", "kind": "example", "module": n["module"],
                                 "file": n["file"], "private": False, "anonymous": True, "unlocated": True}
            ledger_to_decl[lid] = lid
            continue
        m = None
        if lid.startswith("private:"):
            m = re.match(r"^private:([^:]+):([^:]+):L(\d+):(.+)$", lid)
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
                    rec = {"id": ids_mod.private_id(module, full), "id_source": "private", "name": full,
                           "kind": "field", "module": module, "file": n["file"], "private": True,
                           "anonymous": False, "parent": ids_mod.private_id(module, pcs[0]["full"]),
                           "namespace": pcs[0]["namespace"], "legacy_ids": [lid]}
                    pending_fields.append((rec, pcs[0]))
                    declarations[rec["id"]] = rec
                    ledger_to_decl[lid] = rec["id"]
                    note(lossy, "declaration.private_field_kind_guessed", 1,
                         "the ledger gives private helpers no kind; a private id that names a member of a private "
                         "structure was exported as kind field (the ledger's public nodes distinguish 'structure "
                         "field' from 'projection', which it cannot here)", [lid])
                    continue
            if not cands:
                raise SystemExit(f"private not found at HEAD: {lid}")
            if len(cands) > 1:
                exact = [d for d in cands if line is not None and d["line"] == line]
                if len(exact) != 1:
                    raise SystemExit(f"private ambiguous at HEAD: {lid}")
                cands = exact
            rec = mk_named(cands[0])
            if m and cands[0]["line"] != line:
                note(lossy, "declaration.private_line_stale", 1,
                     "the ledger's positional private id names a line that no longer holds the helper at HEAD "
                     "(re-pins update file hashes, not ids); resolved by module + name, the old id kept in "
                     "legacy_ids", [lid])
        else:
            cands = [d for d in by_full.get(lid, []) if d["file"] == n["file"]]
            if len(cands) == 1:
                rec = mk_named(cands[0])
            elif kind in FIELD_KIND:
                parent = lid.rsplit(".", 1)[0]
                rec = {"id": lid, "id_source": "name", "name": lid, "kind": FIELD_KIND[kind],
                       "module": n["module"], "file": n["file"], "private": False, "anonymous": False,
                       "parent": parent}
                snip = snippets_in["snippets"].get(lid)
                if snip:
                    rec["line"] = snip["line"]
                pending_fields.append((rec, None))
            else:
                raise SystemExit(f"named not found at HEAD: {lid} ({kind})")
        if rec["id"] != lid:
            rec.setdefault("legacy_ids", []).append(lid)
        if rec["id"] in declarations:
            prev = declarations[rec["id"]]
            prev["legacy_ids"] = sorted(set(prev.get("legacy_ids", []) + rec.get("legacy_ids", [])))
            note(lossy, "declaration.two_ledger_ids_one_decl", 1,
                 "two ledger ids resolve to one declaration at HEAD", [lid])
        else:
            declarations[rec["id"]] = rec
        ledger_to_decl[lid] = rec["id"]
    assert not unlocated_bad, unlocated_bad

    # parents of fields and projections: must be a structure or class declaration
    added_parents = []
    for rec, pdecl in pending_fields:
        parent = declarations.get(rec["parent"])
        if parent is None:
            hits = by_full.get(rec["parent"], [])
            assert len(hits) == 1, rec
            parent = mk_named(hits[0])
            declarations[parent["id"]] = parent
            added_parents.append(parent["id"])
        assert parent["kind"] in ("structure", "class"), (rec["id"], parent["kind"])
        # section 3.3: a field's namespace is the parent's namespace plus the parent's name
        rec["namespace"] = parent["name"]
        assert rec["name"].startswith(rec["namespace"] + "."), (rec["name"], rec["namespace"])

    # ---------------- clusters + memberships ----------------
    node_items = collections.defaultdict(set)
    for new_ids in old_to_all.values():
        for iid in new_ids:
            for c in items[iid]["clusters"]:
                node_items[c].add(iid)
    clusters = {}
    rel_sup = dest_in["support_relation"]
    for n in ledger["source_nodes"]:
        c = {"id": n["id"], "x_plan_item_ids": sorted(n["plan_item_ids"]),
             "x_status": n["status"], "x_semantic_status": n["semantic_status"]}
        if n.get("locator"):
            c["locator"] = n["locator"]
        if node_items.get(n["id"]):
            c["items"] = sorted(node_items[n["id"]])
        if n["id"] == rel_sup["source_id"]:
            c["related_to"] = [rel_sup["related_to"]]
        assert not n["external_admissions"]
        clusters[n["id"]] = c
    note(bent, "cluster.x_plan_status", len(clusters),
         "ledger source-node plan_item_ids, status and semantic_status have no cluster field; kept as x_ keys")

    provenance["prov-ledger"] = {
        "id": "prov-ledger", "actor": "agent", "method": "pipeline", "confirmed_by_human": False,
        "tool": {"name": "source_formalization_ledger.py export", "path": "scripts/source_formalization_ledger.py"},
        "inputs": [dict(ledger_lock)],
        "note": "Node-level edges of source-formalization-ledger.json, a clone-local export of the external "
                "ff-core correspondence state (snapshot " + ledger["formalization_snapshot"]["commit"] + ")."}
    memberships: dict[str, dict] = {}
    realization = collections.Counter()

    def resolve(fid: str):
        if fid in artifacts:
            return "artifact", fid
        return "declaration", ledger_to_decl[fid]
    for e in ledger["edges"]:
        key, target = resolve(e["formalization_id"])
        mid = f"{e['source_id']}/{e['role']}/{target}"
        assert mid not in memberships, mid
        memberships[mid] = {"id": mid, "cluster": e["source_id"], key: target, "role": e["role"],
                            "provenance": "prov-ledger"}
        realization[(e["role"], e.get("realization"))] += 1
    note(lossy, "membership.realization", sum(v for (r, z), v in realization.items() if z),
         "ledger edges carry realization ('implemented' on every primary and support edge, absent on consumer "
         "edges: " + ", ".join(f"{r}/{z} {v}" for (r, z), v in sorted(realization.items(), key=str)) + "); "
         "membership has no field for it. Constant per role in this snapshot, so derivable today")
    note(lossy, "ledger.node_class_and_text_hash", len(ledger["formalization_nodes"]),
         "ledger formalization nodes' class (realized_artifact / implementation_helper) and "
         "verification_text_sha256 (a hash of external verified_with text) are not carried; class is "
         "implied by the node kind and id form, the hash is recoverable from the pinned ledger")

    # ---------------- links ----------------
    links: dict[str, dict] = {}
    ref_ledger = refinement["ledger_sha256"]
    provenance["prov-target-refinement"] = {
        "id": "prov-target-refinement", "actor": "agent", "method": "curation",
        "confirmed_by_human": refinement["provenance"]["confirmed_by_human"],
        "note": refinement["provenance"]["note"],
        "inputs": sorted([file_lock(repo, f"{DATA}/target-refinement.json"),
                          ledger_input(ref_ledger)], key=lambda d: d["path"]),
        "x_method": refinement["method"], "x_nodes_curated": refinement["provenance"]["nodes_curated"]}
    note(bent, "provenance.x_refinement_fields", 1,
         "target-refinement's own method name (agent_semantic_curation) and its nodes_curated count have no "
         "provenance field; kept as x_method and x_nodes_curated")
    per_item = collections.defaultdict(list)
    for e in refinement["entries"]:
        loc = region_to_loc[e["region_id"]]
        assert old_to_item[e["item_id"]] in locations[loc]["items"]
        per_item[e["item_id"]].append(e)
    differing = 0
    for old, entries in sorted(per_item.items()):
        sets = {(tuple(sorted(e["formalization_ids"])), tuple(sorted(e["key_formalization_ids"]))) for e in entries}
        differing += len(sets) > 1
        iid = old_to_item[old]
        keys = set(x for e in entries for x in e["key_formalization_ids"])
        for fid in sorted(set(x for e in entries for x in e["formalization_ids"])):
            role = "key" if fid in keys else "attributed"
            did = ledger_to_decl[fid]
            lk = f"{iid}/{role}/{did}"
            links[lk] = {"id": lk, "item": iid, "declaration": did, "role": role,
                         "provenance": "prov-target-refinement"}
    multi = sum(1 for v in per_item.values() if len(v) > 1)
    note(lossy, "link.region_granularity", multi,
         f"target-refinement is keyed by region; {multi} items have one entry per region. Entries were merged "
         f"per item ({differing} items had differing entries across regions, so nothing is lost but the "
         "per-region keying itself)")

    # ledger support edges: an item-level link only where the node has exactly one item
    sup = collections.Counter()
    for e in ledger["edges"]:
        if e["role"] != "support":
            continue
        its = sorted(node_items.get(e["source_id"], ()))
        if not its:
            sup["itemless_node"] += 1
            continue
        if e["formalization_id"] in artifacts:
            sup["artifact_target"] += 1
            continue
        if len(its) > 1:
            sup["multi_item_node"] += 1
            continue
        did = ledger_to_decl[e["formalization_id"]]
        role = "example" if declarations[did]["kind"] == "example" else "support"
        lk = f"{its[0]}/{role}/{did}"
        assert lk not in links, lk
        links[lk] = {"id": lk, "item": its[0], "declaration": did, "role": role, "provenance": "prov-ledger",
                     "raw_label": "support"}
        sup["linked_" + role] += 1
        if declarations[did].get("unlocated"):
            sup["linked_to_unlocated"] += 1

    # coverage: an item with no item-level link falls back to its clusters' memberships
    linked_roles = collections.defaultdict(set)
    for l in links.values():
        linked_roles[l["item"]].add(l["role"])
    populated = {m["cluster"] for m in memberships.values()}
    fallback_items = []
    for iid, item in items.items():
        roles = linked_roles.get(iid, set())
        if not roles:
            assert item.get("clusters") and populated.intersection(item["clusters"]), iid
            item["cluster_fallback"] = True
            fallback_items.append(iid)
        elif "key" not in roles:
            raise SystemExit(f"{iid} has links but no key link: {roles}")
    # the curation's class, where its provenance note names it item by item (section 1.7 only)
    prose = refinement["provenance"]["note"]
    assert ("33 carry none: 29 are formalised only by anonymous examples, Remark 1.7.22 by nothing, and "
            "Remark 1.7.4, Definition 1.7.7 and Definition 1.7.10 have one named declaration that only "
            "supports them") in prose
    s17 = sorted(i for i in fallback_items if items[i].get("number", "").startswith("1.7."))
    assert len(s17) == 33, (len(s17), s17)
    named_class = {"remark-1.7.22": "not_formalized", "remark-1.7.4": "support_only",
                   "definition-1.7.7": "support_only", "definition-1.7.10": "support_only"}
    assert set(named_class) <= set(s17), named_class
    for iid in s17:
        items[iid]["coverage"] = named_class.get(iid, "example_only")
    note(bent, "item.coverage_from_prose", len(s17),
         "target-refinement's provenance note classifies section 1.7's 33 items with no entry (29 formalised "
         "only by anonymous examples, Remark 1.7.22 by nothing, Remark 1.7.4 / Definition 1.7.7 / Definition "
         "1.7.10 only by a supporting declaration); the 33 were matched by count (exactly the 33 fallback items "
         "numbered 1.7.x) and carry that class as coverage beside cluster_fallback",
         [f"{i}: {items[i]['coverage']}" for i in sorted(named_class)])
    note(lossy, "item.coverage_unclassified", len(fallback_items) - len(s17),
         "the same note says the other items with no entry (sections 1.1 to 1.6) are formalised only by anonymous "
         "examples, by nothing, or only by supporting declarations, without saying which; they carry "
         "cluster_fallback and no coverage")

    # ---------------- snippets, destinations ----------------
    snippets = {}
    snip_line_diff = []
    for fid, s in snippets_in["snippets"].items():
        did = ledger_to_decl[fid]
        dec = declarations[did]
        assert s["file"] == dec["file"], fid
        if dec.get("line") is not None and s["line"] != dec["line"]:
            snip_line_diff.append(fid)
        rec = {"id": f"snippet:{did}", "declaration": did, "text": s["text"], "line": s["line"]}
        if s["text"].endswith("…"):
            rec["truncated"] = True
        assert rec["id"] not in snippets
        snippets[rec["id"]] = rec
    destinations = {}
    for r in dest_in["destinations"]:
        key, target = resolve(r["formalization_id"])
        rec = {"id": f"{SITE}:{target}", key: target, "site": SITE, "resolution": r["resolution"]}
        if r["resolution"] == "exact":
            href, _, frag = r["exact_href"].partition("#")
            rec.update(href=href, fragment=frag)
        else:
            rec.update(href=r["fallback_href"], fallback_label=r["fallback_label"])
        assert rec["id"] not in destinations, rec["id"]
        destinations[rec["id"]] = rec
    note(lossy, "destinations.header", 1,
         "destinations.json's pdf and ledger lock blocks and support_relation.relation_id are not carried: "
         "the ledger lock equals companion.graph (checked), the pdf lock equals the source's sha256, and the "
         "relation itself became cluster.related_to")

    # ---------------- verdicts ----------------
    diff_date = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", verdicts_in["method"])
    provenance["prov-verdicts-differential"] = {
        "id": "prov-verdicts-differential", "actor": "agent", "method": "adjudication", "confirmed_by_human": False,
        "note": verdicts_in["method"],
        "inputs": sorted([file_lock(repo, f"{DATA}/faithfulness-verdicts.json"),
                          ledger_input(verdicts_in["ledger_sha256"])], key=lambda d: d["path"])}
    if diff_date:
        provenance["prov-verdicts-differential"]["at"] = diff_date.group(1)
    by_item = collections.defaultdict(list)
    for e in verdicts_in["entries"]:
        by_item[old_to_item[region_to_old[e["region_id"]]]].append((region_to_loc[e["region_id"]], e))
    verdicts = {}
    replaced_notes = []
    for iid, pairs in sorted(by_item.items()):
        groups = collections.OrderedDict()
        for loc_id, e in pairs:
            key = json.dumps({k: v for k, v in e.items() if k != "region_id"}, sort_keys=True)
            groups.setdefault(key, []).append(loc_id)
        for n_group, (key, locs) in enumerate(groups.items(), start=1):
            e = json.loads(key)
            vid = f"verdict:{iid}" + (f"~{n_group}" if n_group > 1 else "")
            v = {"id": vid, "item": iid, "locations": sorted(locs), "verdict": e["verdict"],
                 "severity": e["severity"], "status": e["status"], "summary": e["summary"],
                 "evidence": list(e["evidence"]),
                 "declarations": sorted(({"declaration": ledger_to_decl[d["id"]], "verdict": d["verdict"],
                                          **({"note": d["note"]} if d.get("note") else {})}
                                         for d in e["declarations"]), key=lambda d: d["declaration"])}
            it = items[iid]
            sm = re.search(re.escape(f"{it['kind']} {it.get('number', '')}") + r"\((\w+)\)", e["summary"]) \
                if "number" in it else None
            pm = re.match(r"^In part \((\w+)\)", e["summary"])
            if sm or pm:
                v["sub"] = (sm or pm).group(1)
                note(bent, "verdict.sub_from_prose", 1,
                     "the sub-part lives only in summary prose; parsed by regex", [f"{iid} sub {v['sub']}"])
            ev = " ".join(e["evidence"])
            fm = re.match(r"^Fixed in PR #(\d+)\.", e["summary"])
            om = re.match(r"^Open, issue #(\d+)\.", e["summary"])
            if fm:
                res = {"state": "fixed", "pr": int(fm.group(1))}
                commits = set()
                for how, abbrev in re.findall(r"(commit|merge) ([0-9a-f]{7,40})\b", ev):
                    full = git(repo, "rev-parse", "--verify", abbrev + "^{commit}")
                    if how == "merge":
                        subject = git(repo, "log", "-1", "--format=%s", full)
                        assert subject.startswith(f"Merge pull request #{res['pr']} "), (abbrev, subject)
                    commits.add(full)
                if commits:
                    res["commits"] = sorted(commits)
                    note(lossy, "verdict.commit_roles", 1,
                         "resolution.commits holds the fix and merge commits as one sorted list (expanded to full "
                         "ids with git rev-parse; each merge commit's subject was checked to name the PR); which "
                         "one is the merge is not recorded", [iid])
                # replaced_by: names a declaration note cites as '<name> (<File>.lean:<line>)', other than the
                # verdict's own declarations (a restated declaration is not replaced)
                cands, unresolved = set(), set()
                own = {d["id"].rsplit(".", 1)[-1] for d in e["declarations"]}
                for d in e["declarations"]:
                    for nm, path in re.findall(r"([A-Za-z_][\w']*) \(([\w/]+\.lean):\d+\)", d.get("note", "")):
                        if nm in own:
                            continue
                        hits = [x for x in named if x["file"].endswith("/" + path)
                                and (x["name"] == nm or x["full"].endswith("." + nm))]
                        if len(hits) != 1:
                            unresolved.add(f"{nm} ({path}): {len(hits)} matches")
                            continue
                        rec_n = mk_named(hits[0])
                        if rec_n["id"] not in declarations:
                            declarations[rec_n["id"]] = rec_n
                            replaced_notes.append(rec_n["id"])
                        cands.add(rec_n["id"])
                if cands:
                    res["replaced_by"] = sorted(cands)
                    note(bent, "verdict.replaced_by_from_prose", 1,
                         "the replacing declaration is named only in note prose ('Fixed in PR #N: X (File.lean:L)'); "
                         "parsed by regex", [f"{iid} -> {sorted(cands)}"])
                if unresolved:
                    note(lossy, "verdict.replaced_by_unresolved", len(unresolved),
                         "a '<word> (<File>.lean:<line>)' mention in a declaration note that does not resolve to "
                         "exactly one HEAD declaration in that file (prose such as 'every dimension (...)')",
                         sorted(unresolved))
            elif om:
                res = {"state": "open", "issue": int(om.group(1))}
            else:
                res = None                  # no resolution in the source: the verdict is open (section 3.7)
            if res is not None:
                v["resolution"] = res
            lm = re.search(r"\((CONFIRMED_CONTENT|ATTRIBUTION_GAP|CONVENTION|ERRATA|REFUTED|UNSURE), \w+\)", ev)
            if lm:
                v["ledger_verdict"] = lm.group(1)
            if "method" in e:
                pid = f"prov-verdict-{slug(iid)}"
                p = {"id": pid, "actor": "agent", "method": "adjudication", "confirmed_by_human": False,
                     "note": e["method"],
                     "inputs": sorted([file_lock(repo, f"{DATA}/faithfulness-verdicts.json"),
                                       ledger_input(verdicts_in["ledger_sha256"])],
                                      key=lambda d: d["path"])}
                dm = re.search(r"on (\d{4}-\d{2}-\d{2})\s*$", e["method"])
                if dm:
                    p["at"] = dm.group(1)
                provenance[pid] = p
                v["provenance"] = pid
            else:
                v["provenance"] = "prov-verdicts-differential"
            verdicts[vid] = v
    note(lossy, "verdict.replaced_by_evidence_only", 1,
         "Exercise 1.2.23's evidence says PR #13 added volume_completion_unique_of_translation_invariant as well "
         "as volume_unique_of_lebesgue_axioms, but only the latter is cited as stating the exercise (in the "
         "declaration note), so replaced_by names only it; the evidence text itself is carried in full")
    for v in verdicts.values():
        for rb in v.get("resolution", {}).get("replaced_by", []):
            assert rb in declarations, rb

    # ---------------- changelog: ordinals ----------------
    changelog = {}
    for rid, a in ids_mod.ordinal_ids(anon_ids):
        cid = f"ordinal/{rid}"
        changelog[cid] = {"id": cid, "at": head_date, "event": "ordinal_assigned", "subject": rid,
                          "subject_kind": "declaration", "commit": head, "file": a.file, "line": a.line}

    # ---------------- document ----------------
    doc = {
        "schema": "lean-companion/annotations", "schema_version": "1.0",
        "companion": {"name": "measure_theory", "repo": "github.com/mathlib-initiative/measure_theory",
                      "commit": head, "generated_at": head_date,
                      "generator": "export_mt.py prototype for schema 1.0 (scratch, not in any repo)",
                      "graph": {"path": LEDGER, "sha256": ledger_lock["sha256"], "bytes": ledger_lock["bytes"],
                                "snapshot_commit": ledger["formalization_snapshot"]["commit"]}},
        "x_graph_scope": {"scope": ledger["scope"], "snapshot_tree": ledger["formalization_snapshot"]["tree"]},
        "sources": [source],
        "items": list(items.values()), "locations": list(locations.values()),
        "declarations": list(declarations.values()), "artifacts": list(artifacts.values()),
        "memberships": list(memberships.values()), "links": list(links.values()),
        "provenance": list(provenance.values()), "clusters": list(clusters.values()),
        "snippets": list(snippets.values()), "destinations": list(destinations.values()),
        "verdicts": list(verdicts.values()), "changelog": list(changelog.values()),
    }
    note(bent, "root.x_graph_scope", 1,
         "the ledger's scope name (tao-measure-chapter-1) and its snapshot tree hash have no companion.graph "
         "field; kept in a root x_ key")
    note(lossy, "ledger.integrity_blocks", 1,
         "the ledger's own integrity machinery is not carried: files[] (65 pinned file hashes), "
         "comparison_signatures, counts, provenance.canonical_inputs, authority and ledger_schema. All are "
         "recoverable from the ledger pinned in companion.graph; none is item or declaration data")
    head_named_not_in_ledger = sorted((ids_mod.private_id(d_["module"], d_["full"]) if d_["private"] else d_["full"])
                                      for d_ in named)
    head_named_not_in_ledger = [x for x in head_named_not_in_ledger if x not in declarations]
    note(lossy, "declaration.head_not_in_ledger", len(head_named_not_in_ledger),
         "named or private declarations found at HEAD but absent from the ledger snapshot are not exported (the "
         "document's declarations are the ledger's list, every anonymous declaration at HEAD and the declarations "
         "the verdicts cite as replacements). A declaration with no membership is exactly one the ledger does "
         "not list (every ledger node has an edge)", head_named_not_in_ledger[:5])

    canon = V.canonicalize(doc)
    text = V.canonical_dumps(canon)
    args.out.write_text(text, encoding="utf-8")

    # ---------------- counts + report ----------------
    arrays = ("sources", "items", "declarations", "artifacts", "memberships", "links", "provenance", "locations",
              "destinations", "snippets", "verdicts", "clusters", "changelog", "unattributed")
    counts = {k: len(canon.get(k, [])) for k in arrays}
    C = collections.Counter
    decls = canon["declarations"]
    counts["declarations_by_id_source"] = dict(sorted(C(d["id_source"] for d in decls).items()))
    counts["declarations_by_kind"] = dict(sorted(C(d["kind"] for d in decls).items()))
    counts["declarations_unlocated"] = sum(1 for d in decls if d.get("unlocated"))
    counts["declarations_with_signature"] = dict(sorted(C(("anonymous" if d["anonymous"] else "named")
                                                          for d in decls if "signature" in d).items()))
    counts["declarations_with_namespace"] = sum(1 for d in decls if "namespace" in d)
    counts["declarations_with_legacy_ids"] = sum(1 for d in decls if "legacy_ids" in d)
    counts["declarations_with_lean_name"] = sum(1 for d in decls if "lean_name" in d)
    counts["declarations_private_anonymous"] = sum(1 for d in decls if d["private"] and d["anonymous"])
    counts["declarations_added_as_replacements"] = sorted(replaced_notes)
    counts["declarations_added_as_parents"] = sorted(added_parents)
    counts["artifacts_by_kind"] = dict(sorted(C(a["kind"] for a in canon["artifacts"]).items()))
    counts["memberships_by_role"] = dict(sorted(C(m["role"] for m in canon["memberships"]).items()))
    counts["memberships_to_artifacts"] = sum(1 for m in canon["memberships"] if "artifact" in m)
    counts["memberships_to_unlocated"] = sum(1 for m in canon["memberships"]
                                             if declarations.get(m.get("declaration"), {}).get("unlocated"))
    counts["links_by_role"] = dict(sorted(C(l["role"] for l in canon["links"]).items()))
    counts["links_by_provenance"] = dict(sorted(C(l["provenance"] for l in canon["links"]).items()))
    counts["ledger_support_edges"] = dict(sorted(sup.items()))
    counts["items_by_form"] = dict(sorted(C(i["form"] for i in canon["items"]).items()))
    counts["items_by_id_source"] = dict(sorted(C(i["id_source"] for i in canon["items"]).items()))
    counts["items_by_kind"] = dict(sorted(C(i["kind"] for i in canon["items"]).items()))
    counts["items_by_coverage"] = dict(sorted(C(i.get("coverage", "-") for i in canon["items"]).items()))
    counts["items_cluster_fallback"] = sum(1 for i in canon["items"] if i.get("cluster_fallback"))
    counts["verdicts_without_resolution"] = sum(1 for v in canon["verdicts"] if "resolution" not in v)
    counts["passage_ids"] = {k: passage_ids[k] for k in sorted(passage_ids)}
    counts["locations_by_shape"] = dict(sorted(C("quads" if "quads" in l["target"]["selector"][0]["refinedBy"]
                                                 else "rect" for l in canon["locations"]).items()))
    counts["destinations_by_resolution"] = dict(sorted(C(d["resolution"] for d in canon["destinations"]).items()))
    counts["destinations_to_artifacts"] = sum(1 for d in canon["destinations"] if "artifact" in d)
    counts["destination_rows_in"] = len(dest_in["destinations"])
    counts["verdict_locations"] = {v["id"]: len(v["locations"]) for v in canon["verdicts"]}
    counts["verdict_resolutions"] = {v["id"]: v.get("resolution", "(none: open)") for v in canon["verdicts"]}
    counts["snippets_line_differs_from_scan"] = len(snip_line_diff)
    counts["snippets_truncated"] = sum(1 for s in canon["snippets"] if s.get("truncated"))
    counts["anonymous_at_head"] = len(anon)
    counts["anonymous_rejected_tags"] = len(rejected)
    counts["comment_tags_mapped_to_ledger"] = len(tag_to_decl)
    counts["named_scanned_at_head"] = len(named)
    counts["lean_files_scanned"] = len(lean_files)
    counts["namespace_crosscheck_vs_leanparse"] = dict(ns_checks)
    counts["ilean_files_read"] = sorted(set(ilean_used))
    counts["ledger"] = {"formalization_nodes": len(ledger["formalization_nodes"]), "edges": len(ledger["edges"]),
                        "source_nodes": len(ledger["source_nodes"]), "mapped_to_declarations": len(ledger_to_decl),
                        "mapped_to_artifacts": len(artifacts)}
    counts["repo_head"] = head
    counts["repo_dirty"] = bool(dirty)
    counts["bytes"] = len(text.encode("utf-8"))
    xkeys = C()
    for name, arr in canon.items():
        if name.startswith("x_"):
            xkeys[f"$.{name}"] += 1
        elif isinstance(arr, list):
            for r in arr:
                if isinstance(r, dict):
                    for k in r:
                        if k.startswith("x_"):
                            xkeys[f"{name}[].{k}"] += 1
    for k in canon["companion"]:
        if k.startswith("x_"):
            xkeys[f"companion.{k}"] += 1
    counts["x_keys"] = dict(sorted(xkeys.items()))
    x_bytes = 0
    for name, arr in canon.items():
        if name.startswith("x_"):
            x_bytes += len(V.canonical_dumps({name: arr}))
        elif isinstance(arr, list):
            for r in arr:
                if isinstance(r, dict):
                    x_bytes += sum(len(V.canonical_dumps({k: v})) for k, v in r.items() if k.startswith("x_"))
    counts["x_bytes_approx"] = x_bytes
    report = {"counts": counts, "lossy": lossy, "bent": bent}
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: counts[k] for k in arrays}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
