#!/usr/bin/env python3
"""Shared helpers of the informalize runtime's tools, and the two commands build_pdf.sh runs.

Part of formal-companion's informalize runtime (docs/proposals/informalize.md, section 7, in
the formal-companion skill); `cf informalize sync` copies it byte for byte into an output
repository's informal/tools/. Ported from the pilot's rrcommon.py: what that file held as
constants (the job name, the declarations file, two hypothesis patterns) now comes from
informal/informalize.json, declarations.json and hypotheses.json, and an item's parts come
from items.json instead of being parsed from its gist.

Every tool reads a build directory, OUTDIR, that tools/build_pdf.sh wrote:

    OUTDIR/src/              the copy of informal/ that was built (its items.json, its
                             declarations.json, ... are the ones the tools check against)
    OUTDIR/runtime/          the marker package the build used
    OUTDIR/<job>.pdf .aux .log .fls
    OUTDIR/build-receipt.json
    OUTDIR/markers.json      written by extract_markers.py

The two commands, for build_pdf.sh:

    python3 fccommon.py config <informal dir>
        prints the job, the main file, SOURCE_DATE_EPOCH and then each TeX file the build
        needs, one per line, after checking informalize.json; exits 2 on a bad file
    python3 fccommon.py receipt <outdir> [--only N,M] [--source-commit SHA]
        writes OUTDIR/build-receipt.json, with the sections the .aux records as typeset;
        exits 2 when a section --only names was not typeset, and 1 when the build read or
        wrote a file of OUTDIR it may not (`recorded_inputs`)

Standard library only, and no import of a sibling. Apache License 2.0.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

RUNTIME_SCHEMA = "formal-companion/informalize-runtime"
ITEMS_SCHEMA = "formal-companion/informalize-items"
HYPOTHESES_SCHEMA = "formal-companion/informalize-hypotheses"
MARKERS_SCHEMA = "formal-companion/informalize-markers"
BUILD_SCHEMA = "formal-companion/informalize-build"
SCHEMA_VERSION = 1

#: what informalize.json's `boxes` holds when it says nothing: the pilot's figures, tuned to
#: an 11pt one-column article on letter paper
DEFAULT_BOXES = {"res_dpi": 288, "ink_level": 224, "pad_bp": 3.0, "frame_bp": 4.0}

SP_PER_BP = 65536 * 72.27 / 72      # TeX's scaled points per PostScript point

#: The environment of each kind, and the word its head prints.  The kinds, the reference
#: sources, the roles, the relations and the id grammar below are copies: the runtime runs
#: where cflib cannot be imported.  tests/test_informalize_runtime.py pins them to the
#: specification's lists, to cflib.vocab's INFORMALIZE_KINDS, REFERENCE_SOURCES,
#: INFORMALIZE_ROLES and INFORMALIZE_RELATIONS, and to cflib.items.INFORMAL_ITEM_ID.
KIND_NAMES = {
    "definition": "Definition",
    "lemma": "Lemma",
    "proposition": "Proposition",
    "theorem": "Theorem",
    "corollary": "Corollary",
    "example": "Example",
    "remark": "Remark",
}
REFERENCE_SOURCES = ("docstring", "module docstring", "inferred", "none")
ROLES = ("key", "helper")
RELATIONS = ("exactMatch", "broadMatch", "narrowMatch", "relatedMatch")

ITEM_ID = re.compile(r"\A[1-9][0-9]*\.[1-9][0-9]*\Z")
_JOB = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_MAIN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.tex\Z")
_TEX_FILE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_SECTIONS = re.compile(r"\A[0-9]+(?:,[0-9]+)*\Z")
_MACRO = re.compile(r"\A\\[A-Za-z]+\Z")
_HYP_KEY = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9-]{0,39}\Z")


class ToolError(Exception):
    """A problem a tool reports in one line and exits 2 on: missing input, bad config."""


class BuildRefused(ToolError):
    """A build that read or wrote a file of OUTDIR it may not; the receipt command exits 1."""


# ---------------------------------------------------------------------------
# small things

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise ToolError(f"{what} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError(f"{what} is not JSON: {path}: {exc}") from None


def dump_json(data: Any) -> str:
    """JSON with one line per box and per short list of numbers.

    Only a list that json.dumps spread over several lines is joined: a string value holds
    no line break of its own (it is written as \\n), so a title such as `$[ 0, 1 ]$` is
    left as it is.
    """
    text = json.dumps(data, indent=1, ensure_ascii=False)
    text = re.sub(r"\[\n\s*([-\d.,\s]+?)\n\s*\]",
                  lambda m: "[" + ", ".join(m.group(1).split()).replace(",,", ",") + "]", text)
    return text + "\n"


def write_json(path: Path, data: Any) -> None:
    """Write through a temporary file in the same directory, so a reader never sees half."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(dump_json(data), encoding="utf-8")
    os.replace(tmp, path)


def require_programs(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise ToolError("not on PATH: " + ", ".join(missing)
                        + " (poppler's pdftotext, pdftoppm and pdfinfo; TeX's pdflatex, latexmk"
                          " and kpsewhich)")


def section_list(text: str | None) -> list[str]:
    """``"2,3"`` -> ``["2", "3"]``; an empty or missing list is []."""
    text = (text or "").replace(" ", "")
    if not text:
        return []
    if not _SECTIONS.match(text):
        raise ToolError(f"not a comma list of section numbers: {text!r}")
    return [str(int(n)) for n in text.split(",")]


def tex_text(text: str) -> str:
    """A title as TeX wrote it into the .aux, in the spelling a person types.

    TeX's \\write puts a space after every control word (`$\\pi_1$` comes back as
    `$\\pi _1$`); the space is dropped where no letter follows, and every run of white
    space becomes one space.  Both sides of a comparison go through this.
    """
    text = re.sub(r"(\\[A-Za-z@]+) (?=[^A-Za-z@\s])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# informalize.json

@dataclass
class Config:
    """informal/informalize.json, as the runtime needs it."""

    job: str
    main: str
    source_date_epoch: int | None
    boxes: dict[str, float]
    tex_packages: list[str]


def load_config(informal: Path) -> Config:
    data = read_json(informal / "informalize.json", "informalize.json")
    if not isinstance(data, dict) or data.get("schema") != RUNTIME_SCHEMA:
        raise ToolError(f"informalize.json: schema is not {RUNTIME_SCHEMA}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ToolError(f"informalize.json: schema_version is not {SCHEMA_VERSION}")
    job, main = data.get("job"), data.get("main", "main.tex")
    if not isinstance(job, str) or not _JOB.match(job):
        raise ToolError(f"informalize.json: job {job!r} is not a plain file name")
    if not isinstance(main, str) or not _MAIN.match(main):
        raise ToolError(f"informalize.json: main {main!r} is not a .tex file of informal/")
    epoch = data.get("source_date_epoch")
    if epoch is not None and (isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0):
        raise ToolError(f"informalize.json: source_date_epoch {epoch!r} is not a time in seconds")
    boxes = dict(DEFAULT_BOXES)
    given = data.get("boxes") or {}
    if not isinstance(given, dict) or set(given) - set(DEFAULT_BOXES):
        raise ToolError(f"informalize.json: boxes may hold only {sorted(DEFAULT_BOXES)}")
    for key, value in given.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ToolError(f"informalize.json: boxes.{key} {value!r} is not a number >= 0")
        boxes[key] = value
    if not (36 <= boxes["res_dpi"] <= 1200 and 1 <= boxes["ink_level"] <= 255):
        raise ToolError("informalize.json: boxes.res_dpi must be 36..1200 and ink_level 1..255")
    boxes["res_dpi"], boxes["ink_level"] = int(boxes["res_dpi"]), int(boxes["ink_level"])
    packages = data.get("tex_packages") or []
    if not isinstance(packages, list) or not all(
            isinstance(p, str) and _TEX_FILE.match(p) for p in packages):
        raise ToolError("informalize.json: tex_packages is not a list of TeX file names")
    return Config(job, main, epoch, boxes, list(packages))


# ---------------------------------------------------------------------------
# a build directory

class Build:
    """OUTDIR, as build_pdf.sh leaves it."""

    def __init__(self, outdir: str | Path):
        self.outdir = Path(outdir).resolve()
        self.src = self.outdir / "src"
        if not self.src.is_dir():
            raise ToolError(f"{self.outdir} holds no build (no src/); run "
                            "informal/tools/build_pdf.sh OUTDIR first")
        self.config = load_config(self.src)
        job = self.config.job
        self.aux = self.outdir / f"{job}.aux"
        self.pdf = self.outdir / f"{job}.pdf"
        self.log = self.outdir / f"{job}.log"
        self.fls = self.outdir / f"{job}.fls"
        self.receipt_path = self.outdir / "build-receipt.json"
        self.markers = self.outdir / "markers.json"

    def need(self, *paths: Path) -> None:
        for path in paths:
            if not path.is_file():
                raise ToolError(f"{path} not found; run informal/tools/build_pdf.sh first")

    def receipt(self) -> dict:
        self.need(self.receipt_path)
        return read_json(self.receipt_path, "build-receipt.json")


# ---------------------------------------------------------------------------
# the .aux file

def _decode_carets(text: str) -> str:
    """TeX may write a byte as ^^xx; turn such sequences back into bytes."""
    if "^^" not in text:
        return text
    raw = re.sub(rb"\^\^([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]),
                 text.encode("utf-8", "surrogateescape"))
    return raw.decode("utf-8", "replace")


def _groups(text: str, pos: int, n: int) -> tuple[list[str], int] | None:
    """Read n brace groups starting at text[pos]; None if they are not there."""
    out = []
    for _ in range(n):
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            return None
        depth, start = 0, pos
        while pos < len(text):
            c = text[pos]
            if c == "\\":
                pos += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        if depth != 0:
            return None
        out.append(text[start + 1:pos])
        pos += 1
    return out, pos


@dataclass
class Aux:
    """What fcmarkers.sty wrote, in document order."""

    geometry: dict[str, int] = field(default_factory=dict)
    sections: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)
    leanrefs: list[dict] = field(default_factory=list)
    hyps: list[dict] = field(default_factory=list)
    hypdefs: list[dict] = field(default_factory=list)
    refs: list[dict] = field(default_factory=list)
    positions: dict[str, dict[str, int]] = field(default_factory=dict)

    def parts_of(self, item_id: str) -> list[str]:
        return [p["part"] for p in self.parts if p["id"] == item_id]

    def decls_of(self, item_id: str, part: str) -> list[str]:
        seen: list[str] = []
        for r in self.leanrefs:
            if r["id"] == item_id and r["part"] == part and r["name"] not in seen:
                seen.append(r["name"])
        return seen


RECORDS = {
    "fc@auxgeometry": 1, "fc@auxsection": 4, "fc@auxitem": 4, "fc@auxpart": 2,
    "fc@auxleanref": 3, "fc@auxhyp": 3, "fc@auxhypdef": 2, "fc@auxref": 4,
    "zref@newlabel": 2,
}
_CMD = re.compile(r"\\(" + "|".join(re.escape(k) for k in RECORDS) + r")(?![A-Za-z@])")


def parse_aux(text: str) -> Aux:
    text = _decode_carets(text)
    aux = Aux()
    for m in _CMD.finditer(text):
        name = m.group(1)
        got = _groups(text, m.end(), RECORDS[name])
        if got is None:
            continue
        g, _ = got
        if name == "fc@auxgeometry":
            for kv in g[0].split(","):
                k, _, v = kv.partition("=")
                if k.strip():
                    aux.geometry[k.strip()] = int(v)
        elif name == "fc@auxsection":
            aux.sections.append({"number": g[0], "typeset": g[1] == "1", "file": g[2],
                                 "title": tex_text(g[3])})
        elif name == "fc@auxitem":
            aux.items.append({"id": g[0], "kind": g[1], "section": g[2],
                              "title": tex_text(g[3])})
        elif name == "fc@auxpart":
            aux.parts.append({"id": g[0], "part": g[1]})
        elif name == "fc@auxleanref":
            aux.leanrefs.append({"id": g[0], "part": g[1], "name": g[2].strip()})
        elif name == "fc@auxhyp":
            aux.hyps.append({"id": g[0], "part": g[1], "key": g[2]})
        elif name == "fc@auxhypdef":
            aux.hypdefs.append({"key": g[0], "macro": g[1].strip()})
        elif name == "fc@auxref":
            aux.refs.append({"id": g[0], "part": g[1], "number": tex_text(g[2]),
                             "star": g[3] == "*"})
        elif name == "zref@newlabel":
            props = dict(re.findall(r"\\(\w+)\{([^{}]*)\}", g[1]))
            if g[0].startswith("fc:"):
                aux.positions[g[0]] = {k: int(v) for k, v in props.items()
                                       if k in ("posx", "posy", "abspage")}
    return aux


def read_aux(path: Path) -> Aux:
    return parse_aux(path.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# items.json, declarations.json, hypotheses.json

@dataclass
class Items:
    """items.json: the sections in order, and the items by id with their order and parts."""

    sections: list[dict]
    by_id: dict[str, dict]
    problems: list[str]

    def in_v1(self, section: str | None = None) -> list[str]:
        return [i for i, it in self.by_id.items()
                if it["in_v1"] and (section is None or it["section"] == section)]


def given_reference(ref: Any) -> dict | None:
    """The reference as a dict when it gives a number, else None."""
    if isinstance(ref, dict) and ref.get("number") and ref.get("source") != "none":
        return ref
    return None


def load_items(path: Path) -> Items:
    """items.json, with the shape problems the checks would trip on (section 5.4).

    The full validator is the skill's (`cf informalize check-items`); this one reports what
    would make a check meaningless, and keeps going where it can.
    """
    data = read_json(path, "items.json")
    problems: list[str] = []
    if not isinstance(data, dict) or data.get("schema") != ITEMS_SCHEMA:
        raise ToolError(f"items.json: schema is not {ITEMS_SCHEMA}")
    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not str(sec.get("number", "")).isdigit():
            problems.append(f"a section without a number: {sec!r}")
            continue
        sections.append({"number": str(int(sec["number"])), "title": str(sec.get("title", "")),
                         "slug": str(sec.get("slug", ""))})
    by_id: dict[str, dict] = {}
    order: dict[str, int] = {}
    for raw in data.get("items") or []:
        if not isinstance(raw, dict) or not ITEM_ID.match(str(raw.get("id", ""))):
            problems.append(f"an item whose id is not <section>.<n>: {raw.get('id')!r}")
            continue
        iid = raw["id"]
        if iid in by_id:
            problems.append(f"item {iid} is listed twice")
            continue
        item = dict(raw)
        item["section"] = str(raw.get("section", ""))
        if item["section"] != iid.split(".")[0]:
            problems.append(f"item {iid} names section {item['section']!r}")
        if raw.get("kind") not in KIND_NAMES:
            problems.append(f"item {iid} has kind {raw.get('kind')!r}")
        item["in_v1"] = raw.get("in_v1") is True
        item["order"] = order[item["section"]] = order.get(item["section"], -1) + 1
        letters = [p.get("letter") for p in raw.get("parts") or [] if isinstance(p, dict)]
        if letters != [chr(ord("a") + n) for n in range(len(letters))]:
            problems.append(f"item {iid}: parts {letters} are not a, b, ... in order")
        item["part_letters"] = letters
        item["part_refs"] = {p.get("letter"): given_reference(p.get("reference"))
                             for p in raw.get("parts") or [] if isinstance(p, dict)}
        decls = [d for d in raw.get("declarations") or [] if isinstance(d, dict)]
        for d in decls:
            if d.get("role") not in ROLES:
                problems.append(f"item {iid}: {d.get('name')} has role {d.get('role')!r}")
            if d.get("part") is not None and d.get("part") not in letters:
                problems.append(f"item {iid}: {d.get('name')} names part {d.get('part')!r}, "
                                "which the item does not have")
        item["decls"] = decls
        item["keys"] = [d for d in decls if d.get("role") == "key"]
        by_id[iid] = item
    return Items(sections, by_id, problems)


def load_declarations(path: Path) -> dict[str, dict]:
    """declarations.json: an object whose `declarations` list holds one entry per scope member."""
    data = read_json(path, "declarations.json")
    rows = data.get("declarations") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ToolError("declarations.json: no `declarations` list")
    return {d["name"]: d for d in rows if isinstance(d, dict) and isinstance(d.get("name"), str)}


def load_hypotheses(path: Path) -> tuple[list[dict], list[str]]:
    """hypotheses.json's rows and its problems (section 5.5).  A missing file is no rows."""
    if not path.exists():
        return [], []
    data = read_json(path, "hypotheses.json")
    problems: list[str] = []
    if not isinstance(data, dict) or data.get("schema") != HYPOTHESES_SCHEMA:
        return [], [f"schema is not {HYPOTHESES_SCHEMA}"]
    rows, keys, macros = [], set(), set()
    for row in data.get("hypotheses") or []:
        if not isinstance(row, dict):
            problems.append(f"not an object: {row!r}")
            continue
        key, macro = row.get("key"), row.get("macro")
        head, pattern = row.get("binder_head"), row.get("binder_type")
        if not isinstance(key, str) or not _HYP_KEY.match(key):
            problems.append(f"key {key!r} is not letters, digits and -")
        elif key in keys:
            problems.append(f"key {key} is listed twice")
        if not isinstance(macro, str) or not _MACRO.match(macro):
            problems.append(f"{key}: macro {macro!r} is not a control word")
        elif macro in macros:
            problems.append(f"{key}: macro {macro} is listed twice")
        if head is None and pattern is None:
            problems.append(f"{key}: neither binder_head nor binder_type")
        if head is not None and (not isinstance(head, str) or not head.strip()
                                 or re.search(r"\s", head)):
            problems.append(f"{key}: binder_head {head!r} is not one name")
        if pattern is not None:
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                problems.append(f"{key}: binder_type {pattern!r} is not a regular expression")
        keys.add(key)
        macros.add(macro)
        rows.append(row)
    return rows, problems


_OPENERS, _CLOSERS = "([{⦃⟨", ")]}⦄⟩"
_NAME_CHAR = r"[^\s()\[\]{}⦃⦄⟨⟩,:→]"
_NAME = _NAME_CHAR + "+"
#: what starts a segment that is the conclusion, not a hypothesis
_CONCLUSION_START = ("∃", "fun ", "λ")


def _top_level(text: str, token: str) -> int:
    """The index of the first `token` in `text` outside every bracket, or -1."""
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(token, i):
            return i
    return -1


def _group_end(text: str) -> int:
    """The index of the bracket that closes the one `text` starts with, or -1."""
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _binder_types(text: str, parenthesised: bool = False) -> list[str]:
    """The types of a run of binders: `{X : Type} (U V : Set X) [Finite U]`, or one
    unbracketed binder, `x y : T` or a binder predicate `x ∈ s`.  A group without a colon
    is an instance's type in `[...]`, names elsewhere, and a type in parentheses when
    `parenthesised` (the antecedent `(A ∧ B) →`)."""
    out: list[str] = []
    text = text.strip()
    while text:
        if text[0] not in _OPENERS:
            colon = _top_level(text, " : ")
            if colon >= 0:
                out.append(text[colon + 3:].strip())
            elif re.search(r"[^\w\s.'@]", text):
                out.append(text)               # x ∈ s, a binder predicate
            break
        end = _group_end(text)
        if end < 0:
            break
        inner = text[1:end].strip()
        colon = _top_level(inner, " : ")
        if colon >= 0:
            out.append(inner[colon + 3:].strip())
        elif text[0] == "[" or (text[0] == "(" and parenthesised):
            out.append(inner)
        text = text[end + 1:].strip()
    return out


def hypothesis_types(signature: str) -> list[str]:
    """The printed types of what a one-line Lean signature binds before its conclusion.

    Only the leading telescope is read: the binders of each `∀ ... ,` (in any bracket kind,
    `[Head ...]` without a name too), a bracketed binder followed by `→`, and, as the probe
    prints a hypothesis nothing depends on, the antecedent of an arrow outside every
    bracket.  It stops at the conclusion: a segment holding `↔` outside brackets (`→`
    binds tighter, so what follows `A ↔` is all conclusion), one starting with `∃`, `fun`
    and the like, and the last segment.  Nothing inside a binder's type is read, so
    `(f : IsOpen U → Prop)` binds a function, not `IsOpen U`.
    """
    rest = " ".join(signature.split())
    out: list[str] = []
    while rest:
        if rest.startswith(("∀", "Π")):
            body = rest[1:]
            comma = _top_level(body, ",")
            if comma < 0:
                break
            out.extend(_binder_types(body[:comma]))
            rest = body[comma + 1:].strip()
            continue
        arrow = _top_level(rest, "→")
        if arrow < 0:
            break
        segment = rest[:arrow].strip()
        if _top_level(segment, "↔") >= 0 or segment.startswith(_CONCLUSION_START):
            break
        if segment[:1] in _OPENERS and _group_end(segment) == len(segment) - 1:
            out.extend(_binder_types(segment, parenthesised=True))
        else:
            out.append(segment)
        rest = rest[arrow + 1:].strip()
    return out


def type_head(text: str) -> str | None:
    """The head constant of a printed type (`IsOpen` of `@IsOpen X _ U`), or None for a
    function type, a binder or a type that starts with a bracket."""
    text = text.strip()
    if _top_level(text, "→") >= 0 or text.startswith(("∀", "Π", "∃", "fun ", "λ")):
        return None
    m = re.match(r"@?(" + _NAME + ")", text)
    return m.group(1) if m else None


def hypothesis_matches(row: dict, printed: str) -> bool:
    """Does a row of hypotheses.json name a binder whose type is printed so?  By the type's
    head constant (`binder_head`), or by a regular expression the whole type matches with
    its spaces collapsed (`binder_type`), for a hypothesis in infix notation or several
    with one head."""
    printed = " ".join(printed.split())
    head, pattern = row.get("binder_head"), row.get("binder_type")
    if head and type_head(printed) == head:
        return True
    return bool(pattern) and re.fullmatch(pattern, printed) is not None


# ---------------------------------------------------------------------------
# the PDF

def pdf_pages(pdf: Path) -> list[tuple[float, float]]:
    """Width and height in points of every page, from pdfinfo."""
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True,
                          check=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not m:
        raise ToolError(f"pdfinfo: no page count for {pdf}")
    n = int(m.group(1))
    info = subprocess.run(["pdfinfo", "-f", "1", "-l", str(n), str(pdf)],
                          capture_output=True, text=True, check=True).stdout
    sizes = {}
    for pm in re.finditer(r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)", info, re.M):
        sizes[int(pm.group(1))] = (float(pm.group(2)), float(pm.group(3)))
    return [sizes[i] for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# the two commands build_pdf.sh runs

def _version(argv: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (out.stdout or out.stderr).splitlines():
        if line.strip():
            return line.strip()
    return None


#: the job's own files: what a build may write into OUTDIR, and of those what it may read back
JOB_OUTPUTS = (".aux", ".log", ".out", ".toc", ".pdf")
JOB_INPUTS = (".aux", ".out", ".toc")


def recorded_inputs(fls: Path, outdir: Path, job: str) -> dict[str, str]:
    """``{path: sha256}`` of every file the recorder saw TeX read.

    A file of the build directory is named relative to it (`src/main.tex`,
    `runtime/fcmarkers.sty`) and the job's own .aux, .out and .toc are left out; a file of
    the TeX installation keeps its absolute path.  A build that read any other file of
    OUTDIR, or wrote any file but the job's own, is refused (BuildRefused): TeX opens a
    file of its output directory before it searches TEXINPUTS, and a section may \\openout
    one there, so such a file would replace the runtime's fcmarkers.sty or any package.
    """
    pwd = outdir
    found: dict[str, str] = {}
    refused: list[str] = []
    for line in fls.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PWD "):
            pwd = Path(line[4:].strip())
            continue
        kind, _, name = line.partition(" ")
        if kind not in ("INPUT", "OUTPUT"):
            continue
        path = Path(name.strip())
        if not path.is_absolute():
            path = pwd / path
        path = Path(os.path.normpath(path))
        try:
            rel = path.relative_to(outdir)
        except ValueError:
            rel = None
        if kind == "OUTPUT":
            if rel is None or rel.as_posix() not in {job + s for s in JOB_OUTPUTS}:
                refused.append(f"wrote {path}")
            continue
        if rel is None:
            key = str(path)
        elif rel.as_posix() in {job + s for s in JOB_INPUTS}:
            continue
        elif rel.parts[:1] in (("src",), ("runtime",)):
            key = rel.as_posix()
        else:
            refused.append(f"read {path}")
            continue
        if key not in found and path.is_file():
            found[key] = sha256_file(path)
    if refused:
        raise BuildRefused("the build " + ", ".join(dict.fromkeys(refused)) + "; in OUTDIR "
                           "a build may read only src/, runtime/ and its own .aux, .out and "
                           ".toc, and write only its own files (remove any other file there "
                           "and rebuild with --clean)")
    return dict(sorted(found.items()))


def write_receipt(outdir: Path, only: list[str], source_commit: str | None) -> dict:
    build = Build(outdir)
    build.need(build.pdf, build.fls, build.aux)
    if source_commit is not None and not _COMMIT.match(source_commit):
        raise ToolError(f"--source-commit {source_commit!r} is not a 40-hex commit")
    listed = read_aux(build.aux).sections
    typeset = [s["number"] for s in listed if s["typeset"]]
    missing = [n for n in only if n not in typeset]
    if missing:
        raise ToolError(f"--only {','.join(missing)}: no such section was typeset; "
                        f"sections.tex lists {[s['number'] for s in listed]}")
    receipt = {
        "schema": BUILD_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "job": build.config.job,
        "main": build.config.main,
        "only": only,
        "sections": typeset,
        "source_commit": source_commit,
        "source_date_epoch": build.config.source_date_epoch,
        "pdf": {"sha256": sha256_file(build.pdf), "bytes": build.pdf.stat().st_size},
        # -norc and the build directory: latexmk reads a latexmkrc (Perl) from the
        # directory it runs in, and build_pdf.sh runs this command in OUTDIR/src/
        "pdftex": _version(["pdflatex", "--version"], build.outdir),
        "latexmk": _version(["latexmk", "-norc", "-v"], build.outdir),
        "inputs": recorded_inputs(build.fls, build.outdir, build.config.job),
    }
    write_json(build.receipt_path, receipt)
    return receipt


def _cli(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "config":
        config = load_config(Path(argv[1]))
        if config.source_date_epoch is None:
            raise ToolError("informalize.json: source_date_epoch is null; `cf informalize "
                            "fetch-library` fills it with the library commit's time")
        print(config.job)
        print(config.main)
        print(config.source_date_epoch)
        for name in config.tex_packages:
            print(name)
        return 0
    if argv and argv[0] == "receipt" and len(argv) >= 2:
        rest, only, commit = argv[2:], [], None
        while rest:
            if rest[0] == "--only" and len(rest) > 1:
                only, rest = section_list(rest[1]), rest[2:]
            elif rest[0] == "--source-commit" and len(rest) > 1:
                commit, rest = rest[1] or None, rest[2:]
            else:
                raise ToolError(f"receipt: unknown argument {rest[0]!r}")
        write_receipt(Path(argv[1]), only, commit)
        return 0
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    print("usage: fccommon.py config <informal dir> | receipt <outdir> [--only N,M] "
          "[--source-commit SHA]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv[1:]))
    except BuildRefused as exc:
        print(f"fccommon: {exc}", file=sys.stderr)
        sys.exit(1)
    except ToolError as exc:
        print(f"fccommon: {exc}", file=sys.stderr)
        sys.exit(2)
