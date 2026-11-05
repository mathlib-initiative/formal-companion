#!/usr/bin/env python3
"""Check a build of the informal text against items.json, declarations.json and hypotheses.json.

    python3 informal/tools/check_items.py OUTDIR [--section N] [--items IDS] [--strict]
                                                 [--aux-only] [--conventions]
                                                 [--declarations FILE]

Part of formal-companion's informalize runtime (docs/proposals/informalize.md, section 7.3,
in the formal-companion skill); ported from the pilot's check_items.py, with the reference
check made generic, the hypotheses read from hypotheses.json, and parts and their key
declarations read from items.json.

It checks the build in OUTDIR (written by tools/build_pdf.sh) against the items.json,
declarations.json and hypotheses.json of the copy that was built, OUTDIR/src/
(`--declarations` names another declarations.json, the gate's).  By default it checks the
items of every section the build typeset; `--section N` checks section N's and
`--items 1.2,1.3` those items.  Every line it prints names the rule it reports under:

  1. the sections are items.json's, in order, each with its title and its file
     sections/NN-<slug>.tex; every `in_v1` item of the checked sections appears once, with
     its id, kind and title, in the section its id names, in items.json order, and no other
     id appears; the checked sections hold at least one such item, and every section that
     build_pdf.sh --only named was typeset;
  2. each item has exactly its parts, in order;
  3. every \\leanref names a declaration of declarations.json (warning: one the item does
     not list);
  4. every key declaration is \\leanref'd in its part, or in the item's own text when its
     part is null, and nowhere else in the item;
  5. wherever a key declaration \\leanref'd there binds a hypothesis of hypotheses.json (a
     binder or an arrow's antecedent of its signature's leading telescope, matched by its
     binder_head or binder_type; or one of the item's listed `hypotheses`), the
     hypothesis's macro is recorded in that part or in the item's own text (warning: a
     recorded hypothesis that no key declaration there binds); hypotheses.json is well
     formed, and every key of it has a \\fcdefhyp with its macro (warning: a \\fcdefhyp of
     a key it does not list);
  6. \\fcref matches the item's or the part's reference number, where one is given, with a
     star exactly when its source is "inferred", once, and appears nowhere else in the item;
  7. the log has no multiply defined label (warning: an undefined reference or citation; a
     reference into a section the build did not typeset is not one);
 11. every \\fcnotion names a constant declarations.json holds, as an entry or as a row of
     its `references`, and no constant is marked twice;

and, without --aux-only (these need poppler):

  8. OUTDIR/markers.json is current: its pdf_sha256 is the PDF's and its entries are the
     .aux's;
  9. every box, cropped with pdftotext -x -y -W -H at res_dpi, holds text: an item's first
     box its head ("Theorem 1.2"), a part's first box its label ("(a)"), an item's boxes
     together the words of its title, and a notion's boxes some ink of their own;
 10. boxes of different items, and of two parts of one item, do not overlap, and a part's
     boxes lie inside its item's.

--strict makes every warning a failure.  --conventions is for a build whose section files
are still empty: it runs rules 7 and 11 and the \\fcdefhyp half of rule 5 only.  Exit
status: 0 pass, 1 fail, 2 an input is missing or unreadable.

Apache License 2.0.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fccommon as fc  # noqa: E402


@dataclass
class Report:
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    def fail(self, rule: int, where: str, msg: str) -> None:
        self.fails.append(f"FAIL [{rule}] {where}: {msg}")

    def warn(self, rule: int, where: str, msg: str) -> None:
        self.warns.append(f"WARN [{rule}] {where}: {msg}")

    def failed(self, strict: bool) -> bool:
        return bool(self.fails or (strict and self.warns))


@dataclass
class Scope:
    """Which items a run checks."""

    sections: set[str] | None     # None: every section
    ids: list[str] | None         # --items

    def has(self, item_id: str, section: str | None = None) -> bool:
        if self.ids is not None:
            return item_id in self.ids
        if self.sections is None:
            return True
        return item_id.split(".")[0] in self.sections or section in self.sections


def where_of(item_id: str, part: str) -> str:
    return f"{item_id}({part})" if part else item_id


def binder_types(row: dict) -> list[str]:
    """The types of an items.json declaration's `hypotheses` ("hU : IsOpen U")."""
    out = []
    for binder in row.get("hypotheses") or []:
        _, sep, rest = str(binder).partition(" : ")
        if sep and rest.strip():
            out.append(rest.strip())
    return out


# ---------------------------------------------------------------------------
# rules 1 to 6: the .aux against items.json

def check_sections(aux: fc.Aux, items: fc.Items, report: Report) -> None:
    want = [(s["number"], fc.tex_text(s["title"]), f"{int(s['number']):02d}-{s['slug']}")
            for s in items.sections]
    have = [(s["number"], s["title"], s["file"]) for s in aux.sections]
    if want != have:
        report.fail(1, "sections", f"the document's sections {have} are not items.json's {want}")


def check_aux(aux: fc.Aux, items: fc.Items, decls: dict[str, dict], hyps: list[dict],
              scope: Scope, report: Report) -> list[dict]:
    """Rules 1 to 6 (rule 5 without its \\fcdefhyp half).  Returns the items checked."""
    for problem in items.problems:
        report.fail(1, "items.json", problem)
    check_sections(aux, items, report)
    expected = [i for i in items.in_v1() if scope.has(i)]
    typeset = [it for it in aux.items if scope.has(it["id"], it["section"])]

    count: dict[str, int] = {}
    for it in typeset:
        count[it["id"]] = count.get(it["id"], 0) + 1
    for iid in expected:
        if count.get(iid, 0) != 1:
            report.fail(1, iid, f"appears {count.get(iid, 0)} times; it must appear once")
    for iid in count:
        if iid not in items.by_id:
            report.fail(1, iid, "id is not in items.json")
        elif not items.by_id[iid]["in_v1"]:
            report.fail(1, iid, "item is not in version 1 (in_v1 is not true)")
    last: dict[str, int] = {}
    for it in typeset:
        iid, meta = it["id"], items.by_id.get(it["id"])
        if meta is None:
            continue
        if it["kind"] != meta["kind"]:
            report.fail(1, iid, f"typeset as {it['kind']}, but items.json says {meta['kind']}")
        if it["section"] != iid.split(".")[0]:
            report.fail(1, iid, f"typeset in section {it['section']}, but its id names section "
                                f"{iid.split('.')[0]}")
        if it["title"] != fc.tex_text(str(meta.get("title", ""))):
            report.fail(1, iid, f"title {it['title']!r}, but items.json says {meta.get('title')!r}")
        if meta["order"] < last.get(meta["section"], -1):
            report.fail(1, iid, "appears out of items.json order in its section")
        last[meta["section"]] = max(last.get(meta["section"], -1), meta["order"])

    checked = [it for it in typeset if it["id"] in items.by_id
               and items.by_id[it["id"]]["in_v1"] and count[it["id"]] == 1]
    for it in checked:
        check_item(aux, items.by_id[it["id"]], decls, hyps, report)
    return checked


def check_item(aux: fc.Aux, meta: dict, decls: dict[str, dict], hyps: list[dict],
               report: Report) -> None:
    iid = meta["id"]
    parts = aux.parts_of(iid)
    if parts != meta["part_letters"]:
        report.fail(2, iid, f"parts {parts or 'none'}; items.json names "
                            f"{meta['part_letters'] or 'none'}")

    # Rule 3: every \leanref is a declaration.
    refs = [x for x in aux.leanrefs if x["id"] == iid]
    listed = {d.get("name") for d in meta["decls"]}
    for x in refs:
        where = where_of(iid, x["part"])
        if x["name"] not in decls:
            report.fail(3, where, f"\\leanref{{{x['name']}}} is not a declaration of "
                                  "declarations.json")
        elif x["name"] not in listed:
            report.warn(3, where, f"\\leanref{{{x['name']}}} is not listed for this item in "
                                  "items.json")
    scopes_of: dict[str, set[str]] = {}
    for x in refs:
        scopes_of.setdefault(x["name"], set()).add(x["part"])

    # Rule 4: every key declaration in its part, and nowhere else.
    allowed: dict[str, set[str]] = {}
    for d in meta["keys"]:
        allowed.setdefault(d.get("name"), set()).add(d.get("part") or "")
    for name, homes in allowed.items():
        got = scopes_of.get(name, set())
        for home in sorted(homes - got):
            report.fail(4, where_of(iid, home), f"key declaration {name} is not \\leanref'd here")
        for other in sorted(got - homes):
            report.fail(4, where_of(iid, other), f"key declaration {name} belongs to "
                        + ", ".join(where_of(iid, h) for h in sorted(homes)) + ", not here")

    # Rule 5: the hypotheses each key declaration binds.
    have = {(h["part"], h["key"]) for h in aux.hyps if h["id"] == iid}
    needed: set[tuple[str, str]] = set()
    for d in meta["keys"]:
        name = d.get("name")
        if name not in decls:
            continue
        types = fc.hypothesis_types(str(decls[name].get("signature") or "")) + binder_types(d)
        rows = []
        for row in hyps:
            printed = next((t for t in types if fc.hypothesis_matches(row, t)), None)
            if printed is not None:
                rows.append((row, row.get("binder_head") or printed))
        for scope in sorted(scopes_of.get(name, set()) & allowed.get(name, set())):
            for row, what in rows:
                needed.add((scope, row["key"]))
                if (scope, row["key"]) not in have and ("", row["key"]) not in have:
                    report.fail(5, where_of(iid, scope),
                                f"{row['macro']} is missing: {name} binds {what}")
    macro = {row["key"]: row["macro"] for row in hyps}
    for scope, key in sorted(have):
        name = macro.get(key, key)
        covered = [p for (p, k) in needed if k == key and (p == scope or scope == "")]
        if not covered:
            report.warn(5, where_of(iid, scope), f"{name} appears, but no key declaration here "
                                                 "binds it")
        elif scope == "":
            for p in parts:
                if any(p in scopes_of.get(d.get("name"), set()) for d in meta["keys"]) \
                        and (p, key) not in needed:
                    report.warn(5, iid, f"{name} in the item's own text also covers part ({p}), "
                                        "whose key declarations do not bind it")

    # Rule 6: the reference number, where one is given, and nowhere else.
    want = {"": fc.given_reference(meta.get("reference"))}
    want.update({p: r for p, r in meta["part_refs"].items() if r})
    got_refs: dict[str, list[dict]] = {}
    for r in aux.refs:
        if r["id"] == iid:
            got_refs.setdefault(r["part"], []).append(r)
    for scope in sorted(set(want) | set(got_refs)):
        ref, found = want.get(scope), got_refs.get(scope, [])
        where = where_of(iid, scope)
        if ref is None:
            if found:
                report.fail(6, where, f"\\fcref{{{found[0]['number']}}}, but items.json gives "
                                      "no reference number here")
            continue
        star = ref.get("source") == "inferred"
        spelled = f"\\fcref{'*' if star else ''}{{{ref['number']}}}"
        if len(found) != 1:
            report.fail(6, where, f"needs one {spelled}; found {len(found)}")
        elif (found[0]["number"], found[0]["star"]) != (fc.tex_text(str(ref["number"])), star):
            report.fail(6, where, f"\\fcref{'*' if found[0]['star'] else ''}"
                                  f"{{{found[0]['number']}}} should be {spelled}")


def check_notions(aux: fc.Aux, known: set[str], report: Report) -> None:
    """Rule 11: a `\\fcnotion` names a constant declarations.json holds, once.

    The conventions fix a notion in plain words and mark it with the Lean constant it is;
    the constant is the marker's argument and never reaches the page, so the only check
    that it is a real one is this.  `known` is the entries of declarations.json and the
    rows of its `references`.
    """
    seen: set[str] = set()
    for notion in aux.notions:
        name = notion["name"]
        where = f"notion {name}"
        if name not in known:
            report.fail(11, where, "is not a declaration of declarations.json, nor a constant "
                                   "of its `references`")
        if name in seen:
            report.fail(11, where, "is marked twice; a notion is fixed once")
        seen.add(name)
        if not notion["words"].strip():
            report.fail(11, where, "prints nothing; \\fcnotion takes the words as the text "
                                   "prints them")


def check_hypdefs(aux: fc.Aux, hyps: list[dict], problems: list[str], report: Report) -> None:
    """Rule 5's other half: hypotheses.json is well formed and every key has its \\fcdefhyp."""
    for problem in problems:
        report.fail(5, "hypotheses.json", problem)
    defined = {h["key"]: h["macro"] for h in aux.hypdefs}
    for row in hyps:
        key, macro = row.get("key"), row.get("macro")
        if key not in defined:
            report.fail(5, "notation", f"no \\fcdefhyp for the hypothesis {key} ({macro})")
        elif defined[key] != macro:
            report.fail(5, "notation", f"the hypothesis {key} is \\fcdefhyp'd as {defined[key]}, "
                                       f"but hypotheses.json names {macro}")
    listed = {row.get("key") for row in hyps}
    for key, macro in defined.items():
        if key not in listed:
            report.warn(5, "notation", f"\\fcdefhyp of {key} ({macro}), which hypotheses.json "
                                       "does not list")


# ---------------------------------------------------------------------------
# rule 7: the log

_UNDEFINED = re.compile(r"(Reference|Citation) `([^']*)' on page \d+ undefined")


def check_log(log: str, aux: fc.Aux, report: Report) -> None:
    for m in re.finditer(r"Label `([^']*)' multiply defined", log):
        report.fail(7, "log", f"label {m.group(1)} multiply defined")
    built = {s["number"] for s in aux.sections if s["typeset"]}
    partial = any(not s["typeset"] for s in aux.sections)
    seen = set()
    for kind, label in _UNDEFINED.findall(log):
        if (kind, label) in seen:
            continue
        seen.add((kind, label))
        if kind == "Reference" and partial:
            m = re.match(r"(?:item|sec|eq):(\d+)", label)
            if m and m.group(1) not in built:
                continue                      # into a section this build skipped
        report.warn(7, "log", f"{kind.lower()} {label} is undefined")


# ---------------------------------------------------------------------------
# rules 8 to 10: the boxes

def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def crop_text(pdf: Path, box: list[float], size: tuple[float, float], dpi: int) -> str:
    w, h = size
    s = dpi / 72
    x, y = int(box[1] * w * s), int(box[2] * h * s)
    ww = int(round((box[1] + box[3]) * w * s)) - x
    hh = int(round((box[2] + box[4]) * h * s)) - y
    page = str(int(box[0]))
    out = subprocess.run(
        ["pdftotext", "-q", "-f", page, "-l", page, "-r", str(dpi), "-x", str(x), "-y", str(y),
         "-W", str(ww), "-H", str(hh), "-enc", "UTF-8", "-nopgbrk", str(pdf), "-"],
        capture_output=True, text=True, check=True).stdout
    return norm(out)


def title_words(title: str) -> list[str]:
    plain = re.sub(r"\$[^$]*\$", " ", title)
    plain = re.sub(r"\\[A-Za-z@]+", " ", plain)
    return [w for w in re.findall(r"[A-Za-z]+", plain) if len(w) >= 4]


def overlap(a: list[float], b: list[float]) -> float:
    if a[0] != b[0]:
        return 0.0
    dx = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    dy = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
    return dx * dy if dx > 0 and dy > 0 else 0.0


def check_boxes(build: fc.Build, aux: fc.Aux, checked: list[dict], scope: Scope,
                report: Report) -> int:
    """Rules 8 to 10.  Returns the number of boxes cropped."""
    import extract_markers  # noqa: PLC0415 - a sibling; only this half needs it

    if not build.markers.is_file():
        report.fail(8, "markers", f"{build.markers} not found; run extract_markers.py")
        return 0
    markers = fc.read_json(build.markers, "markers.json")
    if markers.get("pdf_sha256") != fc.sha256_file(build.pdf) \
            or markers.get("job") != build.config.job:
        report.fail(8, "markers", f"{build.markers} is not of this PDF; rerun extract_markers.py")
        return 0
    skeletons = extract_markers.entry_skeletons(aux)
    if [{k: v for k, v in e.items() if k != "boxes"} for e in markers.get("entries", [])] \
            != skeletons:
        report.fail(8, "markers", f"the entries of {build.markers} are not this build's; rerun "
                                  "extract_markers.py")
        return 0

    dpi = int(build.config.boxes["res_dpi"])
    sizes = fc.pdf_pages(build.pdf)
    entries = {e["id"]: e for e in markers["entries"]}
    n_boxes = 0

    def texts_of(entry: dict, where: str) -> list[str]:
        nonlocal n_boxes
        out = []
        for n, b in enumerate(entry["boxes"]):
            t = crop_text(build.pdf, b, sizes[int(b[0]) - 1], dpi)
            n_boxes += 1
            out.append(t)
            if not t:
                report.fail(9, where, f"box {n + 1} (page {b[0]}) holds no text")
        return out

    for it in checked:
        iid = it["id"]
        e = entries.get(iid)
        if not e or not e["boxes"]:
            report.fail(9, iid, "no boxes in markers.json")
            continue
        texts = texts_of(e, iid)
        head = f"{fc.KIND_NAMES.get(it['kind'], it['kind'])} {iid}"
        if texts and head not in texts[0]:
            report.fail(9, iid, f"its first box does not hold the head {head!r}: {texts[0][:80]!r}")
        union = " ".join(texts).lower()
        missing = [w for w in title_words(it["title"]) if w.lower() not in union]
        if missing:
            report.fail(9, iid, f"its boxes do not hold the title words {missing}")
        part_ids = e.get("parts", [])
        for pn, pid in enumerate(part_ids):
            pe = entries.get(pid)
            if not pe or not pe["boxes"]:
                report.fail(9, pid, "no boxes in markers.json")
                continue
            ptexts = texts_of(pe, pid)
            if ptexts and f"({pe['part']})" not in ptexts[0]:
                report.fail(9, pid, f"its first box does not hold the label ({pe['part']}): "
                                    f"{ptexts[0][:80]!r}")
            for n, b in enumerate(pe["boxes"]):
                if not any(overlap(b, ib) > 0.98 * b[3] * b[4] for ib in e["boxes"]):
                    report.fail(10, pid, f"box {n + 1} lies outside the boxes of item {iid}")
            for qid in part_ids[pn + 1:]:
                if qid in entries and any(overlap(a, b) > 1e-6 for a in pe["boxes"]
                                          for b in entries[qid]["boxes"]):
                    report.fail(10, pid, f"its boxes overlap those of {qid}")
    for e in markers["entries"]:
        if "notion" not in e:
            continue
        if not e["boxes"]:
            report.fail(9, e["id"], "no boxes in markers.json")
            continue
        texts_of(e, e["id"])
    tops = [e for e in markers["entries"] if "parent" not in e and "notion" not in e]
    for n, a in enumerate(tops):
        for b in tops[n + 1:]:
            if not (scope.has(a["id"], a.get("section")) or scope.has(b["id"], b.get("section"))):
                continue
            if any(overlap(x, y) > 1e-6 for x in a["boxes"] for y in b["boxes"]):
                report.fail(10, a["id"], f"its boxes overlap those of item {b['id']}")
    return n_boxes


# ---------------------------------------------------------------------------

def scope_of(args: argparse.Namespace, aux: fc.Aux, items: fc.Items | None) -> Scope:
    built = {s["number"] for s in aux.sections if s["typeset"]}
    if args.items:
        ids = [s.strip() for s in args.items.split(",") if s.strip()]
        unknown = [i for i in ids if items is None or i not in items.by_id]
        if unknown:
            raise fc.ToolError(f"--items: not ids of items.json: {unknown}")
        return Scope(None, ids)
    if args.section:
        section = fc.section_list(args.section)
        if len(section) != 1:
            raise fc.ToolError("--section takes one section number")
        if aux.sections and section[0] not in built:
            raise fc.ToolError(f"section {section[0]} was not typeset in this build")
        return Scope(set(section), None)
    if aux.sections and len(built) < len(aux.sections):
        return Scope(built, None)
    return Scope(None, None)


def run(args: argparse.Namespace) -> int:
    build = fc.Build(args.outdir)
    build.need(build.aux, build.pdf)
    aux = fc.read_aux(build.aux)
    log = build.log.read_text(encoding="utf-8", errors="replace") if build.log.is_file() else ""
    hyps, hyp_problems = fc.load_hypotheses(build.src / "hypotheses.json")
    declarations = Path(args.declarations) if args.declarations else build.src / "declarations.json"
    report = Report()
    check_log(log, aux, report)
    check_hypdefs(aux, hyps, hyp_problems, report)
    if aux.notions:
        check_notions(aux, fc.notion_constants(declarations), report)
    checked: list[dict] = []
    n_boxes, what = 0, "conventions"
    if not args.conventions:
        items = fc.load_items(build.src / "items.json")
        decls = fc.load_declarations(declarations)
        scope = scope_of(args, aux, items)
        if args.items:
            what = f"items {args.items}"
        elif scope.sections is not None and not scope.sections:
            what = "no section"
        elif scope.sections:
            what = "sections " if len(scope.sections) > 1 else "section "
            what += ",".join(sorted(scope.sections, key=int))
        else:
            what = "all sections"
        checked = check_aux(aux, items, decls, hyps, scope, report)
        expected = len([i for i in items.in_v1() if scope.has(i)])
        built = {s["number"] for s in aux.sections if s["typeset"]}
        only = build.receipt().get("only") if build.receipt_path.is_file() else []
        for n in only or []:
            if n not in built:
                report.fail(1, "sections", f"build_pdf.sh --only named section {n}, which the "
                                           "build did not typeset")
        if not expected:
            report.fail(1, "sections", f"the checked scope ({what}) holds no item of items.json "
                                       f"with in_v1 true; the build typeset sections "
                                       f"{sorted(built, key=int)}")
        if not args.aux_only:
            fc.require_programs("pdftotext", "pdfinfo", "pdftoppm")
            n_boxes = check_boxes(build, aux, checked, scope, report)
    for line in report.fails + report.warns:
        print(line)
    fatal = report.failed(args.strict)
    if args.conventions:
        detail = f"{len(hyps)} hypotheses, {len(aux.notions)} notions"
    else:
        n_parts = sum(len(aux.parts_of(it["id"])) for it in checked)
        detail = f"{len(checked)} of {expected} items, {n_parts} parts, {n_boxes} boxes"
    print(f"check_items: {'FAIL' if fatal else 'PASS'} ({what}: {detail}; "
          f"{len(report.fails)} failures, {len(report.warns)} warnings)")
    return 1 if fatal else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", help="the build directory build_pdf.sh wrote")
    ap.add_argument("--section", help="check only the items of this section")
    ap.add_argument("--items", help="check only these items, e.g. 1.2,1.3")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--aux-only", action="store_true",
                    help="rules 1 to 7 only, which need no poppler")
    ap.add_argument("--conventions", action="store_true",
                    help="rule 7 and the \\fcdefhyp half of rule 5 only")
    ap.add_argument("--declarations", help="the declarations.json to check against "
                                           "(default: the built copy's)")
    args = ap.parse_args(argv)
    if args.section and args.items:
        ap.error("--section and --items exclude each other")
    try:
        return run(args)
    except fc.ToolError as exc:
        print(f"check_items: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
