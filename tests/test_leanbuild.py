"""adapters.leanbuild: declarations from a verified Lake build, the text parser per file otherwise.

The build artifacts are synthesised here in the shapes Lean v4.33 / Lake schema
2025-09-10 / verso-literate write (CI has no Lean).  One test reads a real built
companion and is skipped unless the environment names it:

    CF_TML=<a docstring-tag companion checkout>
    CF_MT=<an attribution-table companion checkout>
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import bisect
import dataclasses
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from adapters import leanbuild, leanparse  # noqa: E402
from adapters.api import from_config  # noqa: E402

try:
    import test_export as TE
except ImportError:  # pragma: no cover
    from tests import test_export as TE

DECL = "Lean.Parser.Command.declaration"
IN = "Lean.Parser.Command.in"
NS, SEC, END = "Lean.Parser.Command.namespace", "Lean.Parser.Command.section", "Lean.Parser.Command.end"


def _span(text: str, needle: str, after: int) -> tuple[int, int]:
    """Offsets of ``needle`` at or after ``after``; a ``|`` in it marks where the span starts."""
    pre, _bar, sel = needle.rpartition("|")
    off = text.index(pre + sel, after) + len(pre)
    return off, off + len(sel)


def _pos(text: str, off: int, utf16: bool) -> tuple[int, int]:
    line = text.count("\n", 0, off)
    col = text[text.rfind("\n", 0, off) + 1:off]
    return line, (len(col.encode("utf-16-le")) // 2 if utf16 else len(col))


def _crlf_pos(text: str, off: int) -> tuple[int, int]:
    """verso-literate's position of offset ``off`` of the LF text when the file is CRLF: by the CRLF lines."""
    starts = [0] + [m.end() + k + 1 for k, m in enumerate(re.finditer("\n", text))]
    line = bisect.bisect_right(starts, off) - 1
    return line, off - starts[line]


def _code_end(text: str, a: int, b: int) -> int:
    """End of the last code (not comment, not blank) in ``text[a:b]``: where Lean ends a command's range."""
    depth, i, end = 0, a, a
    while i < b:
        two = text[i:i + 2]
        if two == "/-":
            depth, i = depth + 1, i + 2
        elif two == "-/" and depth:
            depth, i = depth - 1, i + 2
        elif depth == 0 and two == "--":
            i = text.find("\n", i) % (b + 1)
        else:
            if depth == 0 and not text[i].isspace():
                end = i + 1
            i += 1
    return end


class Lake:
    """A checkout with a Lake build: module ``.trace``/``.ilean`` and verso ``literate`` JSON."""

    def __init__(self, root: Path):
        self.root = root
        self.lib = root / ".lake" / "build" / "lib" / "lean"
        self.lit = root / ".lake" / "build" / "literate"

    def module(self, rel: str, text: str, decls, items=None, olean: str = "0d960ed631dee6e6",
               module: str | None = None) -> str:
        """Write source, trace and .ilean (and literate JSON when ``items`` is given); returns the module."""
        module = module or ".".join(Path(rel).with_suffix("").parts)
        src = self.root / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(text, encoding="utf-8")
        cursor, spans = 0, []
        for full, start, sel in decls:
            a = _span(text, start, cursor)[0]
            spans.append((full, a, _span(text, sel, a)))
            cursor = a
        # a range ends at the next command: a declaration, a literate item or a bare `end` (of `mutual`)
        cursor, bounds = 0, {a for _f, a, _s in spans} | {m.start() for m in re.finditer(r"(?m)^end$", text)}
        for _kind, start, _defines in items or ():
            cursor = _span(text, start, cursor)[0]
            bounds.add(cursor)
        bounds = sorted(bounds | {len(text)})
        table = {}
        for full, a, (s0, s1) in spans:
            b = _code_end(text, a, next(x for x in bounds if x > a))
            table[full] = [*_pos(text, a, True), *_pos(text, b, True), *_pos(text, s0, True),
                           *_pos(text, s1, True)]
        ilean = json.dumps({"decls": table, "directImports": [], "module": module, "references": {},
                            "version": 5}, separators=(",", ":"), ensure_ascii=False)
        stem = self.lib.joinpath(*module.split("."))
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_name(stem.name + ".ilean").write_text(ilean, encoding="utf-8")
        ilean_hash = leanbuild.of_text(ilean)
        stem.with_name(stem.name + ".ilean.hash").write_text(ilean_hash, encoding="utf-8")
        self.write_json(stem.with_name(stem.name + ".trace"), {
            "synthetic": False, "schemaVersion": "2025-09-10",
            "outputs": {"o": [f"{olean}.olean"], "m": False, "i": f"{ilean_hash}.ilean", "c": "e29699fa4d3e2bd0.c"},
            "log": [], "depHash": "32a45d22a46e623f",
            "inputs": [["deps", [["imports", [["Init transitive imports (legacy)", "00000000000006bb"]]]]],
                       ["Lean 4.33.0, commit d8b18978322de05a8f3dba51ef03cf5461676c17", "3eca9d16ab6c877d"],
                       # built at another path: matched by suffix
                       [f"/elsewhere/checkout/{rel}", leanbuild.of_text(text)],
                       ["options", [["-Ddoc.verso=true", "9be3b9cd264a8cdb"]]],
                       [f"Module.name: {module}", "1d5b17685a04ebcc"]]})
        if items is not None:
            self.literate(rel, text, items, olean, module)
        return module

    def literate(self, rel: str, text: str, items, olean: str, module: str | None = None,
                 crlf: bool = False) -> None:
        module = module or ".".join(Path(rel).with_suffix("").parts)
        pos = (lambda off: _crlf_pos(text, off)) if crlf else (lambda off: _pos(text, off, False))
        cursor, starts = 0, []
        for kind, start, defines in items:
            cursor = _span(text, start, cursor)[0]
            starts.append((kind, cursor, defines))
        code: dict = {}
        tokens: dict = {}
        keys = itertools.count()

        def node(table: dict, value) -> int:
            key = next(keys)
            table[str(key)] = value
            return key

        def token(content: str, kind: str) -> int:
            return node(code, {"token": {"tok": node(tokens, {"content": content, "kind": kind})}})

        def highlighted(lead: str, s: str) -> dict:
            """Leading trivia as text; then comments as comment tokens and identifiers as their names."""
            seq = [node(code, {"text": {"str": lead}})] if lead else []
            for p in re.findall(r"\s+|--[^\n]*|/-(?!-)[\s\S]*?-/|\S+", s):
                seq.append(node(code, {"text": {"str": p}}) if p.isspace() else
                           token(p, "lineComment") if p.startswith("--") else
                           token(p, "blockComment") if p.startswith("/-") and p != "/--" else
                           token(re.sub(r"«([A-Za-z_]\w*)»", r"\1", p), "unknown"))
            return {"highlighted": node(code, {"seq": {"highlights": seq}})}

        def parts(lead: int, a: int, end: int, kind: str, defines) -> list:
            """Item text ``[a, end)``; docstrings of declarations and of `#guard_msgs` are kept apart."""
            out, i = [], a
            while (defines or kind == "Lean.guardMsgsCmd") and text.find("/--", i, end) >= 0:
                j = text.find("/--", i, end)
                doc = ({"verso": [0, defines[0], {"subsections": [], "text": []}]} if defines else
                       {"markdown": [0, None, {"blocks": []}]})
                out += [highlighted(text[lead:a] if i == a else "", text[i:j]), doc]
                i = leanbuild._comment_end(text, j)
            return out + [highlighted(text[lead:a] if i == a else "", text[i:end])]

        out = [{"code": [highlighted("", "")], "defines": [], "kind": "Lean.Parser.Module.header", "range": None}]
        last = max(n for n, (kind, _a, _d) in enumerate(starts) if kind not in (NS, SEC, END))
        prev = 0
        for n, (kind, a, defines) in enumerate(starts):
            nxt = starts[n + 1][1] if n + 1 < len(starts) else len(text)
            b = _code_end(text, a, nxt)
            # verso-literate runs the file's last declaration to the end of the file, over any `end`
            (l0, c0), (l1, c1) = pos(a), pos(len(text) if n == last else b)
            # an item's code runs on to the end of its last line (or the next item)
            end = min(nxt, text.find("\n", b) % (len(text) + 1))
            out.append({"code": parts(prev, a, end, kind, defines), "defines": list(defines), "kind": kind,
                        "range": [{"column": c0, "line": l0 + 1}, {"column": c1, "line": l1 + 1}]})
            prev = end
        line, col = pos(len(text))
        eoi = [node(code, {"text": {"str": text[prev:]}}), token("", "unknown")]
        out.append({"code": [{"highlighted": node(code, {"seq": {"highlights": eoi}})}], "defines": [],
                    "kind": "Lean.Parser.Command.eoi",
                    "range": [{"column": col, "line": line + 1}, {"column": col, "line": line + 1}]})
        doc = json.dumps({"images": [], "items": {"code": code, "goals": {}, "items": out, "messageContents": {},
                                                  "nextKey": next(keys), "tokens": tokens}, "module": module},
                         separators=(",", ":"), ensure_ascii=False)
        base = self.lit.joinpath(*module.split("."))
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_name(base.name + ".json").write_text(doc, encoding="utf-8")
        base.with_name(base.name + ".json.hash").write_text(leanbuild.of_text(doc), encoding="utf-8")
        olean_path = "/elsewhere/checkout/.lake/build/lib/lean/" + module.replace(".", "/") + ".olean"
        self.write_json(base.with_name(base.name + ".json.trace"), {
            "synthetic": False, "schemaVersion": "2025-09-10", "outputs": None, "log": [],
            "depHash": "40ec31576e8ee865",
            "inputs": [["/elsewhere/verso/.lake/build/bin/verso-literate", "3ec981dbeb0ed0c4"],
                       [f"{module}:olean", [[olean_path, olean]]],
                       ["Lean 4.33.0, commit d8b18978322de05a8f3dba51ef03cf5461676c17", "3eca9d16ab6c877d"],
                       ["leanOptions: #[-Ddoc.verso=true]", "3a3b8ae68e1246af"]]})

    @staticmethod
    def write_json(path: Path, obj) -> None:
        path.write_text(json.dumps(obj), encoding="utf-8")

    def path(self, rel: str, suffix: str) -> Path:
        base = (self.lit if suffix.startswith(".json") else self.lib).joinpath(*Path(rel).with_suffix("").parts)
        return base.with_name(base.name + suffix)


ORDINARY = """\
namespace Demo

/-- A one-line docstring. -/
def one : Nat := 1

/-- A multi-line
docstring, second line. -/
theorem one_eq : one = 1 := rfl

/-- Attribute on its own line. -/
@[simp]
theorem one_add (n : Nat) : one + n = n + 1 := by
  simp [one, Nat.add_comm]

@[simp] theorem simp_inline : one = 1 := rfl

private theorem hidden : True := trivial

protected theorem Prot.thm : True := trivial

/-- A structure with fields. -/
structure Box where
  left : Nat
  right : Nat

noncomputable def nc : Nat := Classical.choice ⟨0⟩

instance : Inhabited Box := ⟨⟨0, 0⟩⟩

/-- An anonymous example. -/
example : one = 1 := rfl

open Nat in
/-- Open-in theorem. -/
theorem open_in (n : Nat) : succ n = n + 1 := rfl

section Companions

variable (h : 1 = 1)

include h in
/-- Include-in theorem. -/
theorem include_in : True := trivial

/-- Section member. -/
theorem in_companions : True := trivial

end Companions

namespace Inner

/-- Nested namespace. -/
theorem nested : True := trivial

end Inner

end Demo

/-- Outside any namespace. -/
theorem top_level : True := trivial
"""

ORDINARY_DECLS = [
    ("Demo.one", "/-- A one-line", "def |one"),
    ("Demo.one_eq", "/-- A multi-line", "theorem |one_eq"),
    ("Demo.one_add", "/-- Attribute", "theorem |one_add"),
    ("Demo.simp_inline", "@[simp] theorem simp_inline", "theorem |simp_inline"),
    ("_private.Demo.Basic.0.Demo.hidden", "private theorem", "theorem |hidden"),
    ("Demo.Prot.thm", "protected theorem", "theorem |Prot.thm"),
    ("Demo.Box", "/-- A structure", "structure |Box"),
    ("Demo.nc", "noncomputable def", "def |nc"),
    ("Demo.instInhabitedBox", "instance : Inhabited", "instance"),
    ("Demo.open_in", "/-- Open-in", "theorem |open_in"),
    ("Demo.include_in", "/-- Include-in", "theorem |include_in"),
    ("Demo.in_companions", "/-- Section member", "theorem |in_companions"),
    ("Demo.Inner.nested", "/-- Nested", "theorem |nested"),
    ("top_level", "/-- Outside", "theorem |top_level"),
]

ORDINARY_ITEMS = [
    (NS, "namespace Demo", []),
    (DECL, "/-- A one-line", ["Demo.one"]),
    (DECL, "/-- A multi-line", ["Demo.one_eq"]),
    (DECL, "/-- Attribute", ["Demo.one_add"]),
    (DECL, "@[simp] theorem simp_inline", ["Demo.simp_inline"]),
    (DECL, "private theorem", ["_private.0.Demo.hidden"]),
    (DECL, "protected theorem", ["Demo.Prot.thm"]),
    (DECL, "/-- A structure", ["Demo.Box", "Demo.Box.left", "Demo.Box.right"]),
    (DECL, "noncomputable def", ["Demo.nc"]),
    (DECL, "instance : Inhabited", []),
    (DECL, "/-- An anonymous example", []),
    (IN, "open Nat in", ["Demo.open_in"]),
    (SEC, "section Companions", []),
    ("Lean.Parser.Command.variable", "variable (h", []),
    (IN, "include h in", ["Demo.include_in"]),
    (DECL, "/-- Section member", ["Demo.in_companions"]),
    (END, "end Companions", []),
    (NS, "namespace Inner", []),
    (DECL, "/-- Nested", ["Demo.Inner.nested"]),
    (END, "end Inner", []),
    (END, "end Demo", []),
    (DECL, "/-- Outside", ["top_level"]),
]

#: every case below is one leanparse gets wrong (checked against a real v4.33 build)
TRICKY = """\
namespace Lab

/-- Before. -/
theorem before : True := trivial

mutual
  /-- Even, in a mutual block. -/
  def isEven : Nat → Bool
    | 0 => true
    | n + 1 => isOdd n
  def isOdd : Nat → Bool
    | 0 => false
    | n + 1 => isEven n
end

/-- After the mutual block. -/
theorem after_mutual : True := trivial

class Widget (α : Type) where
  op : α → α

/-- A prioritised named instance. -/
instance (priority := 100) widgetBool : Widget Bool := ⟨id⟩

instance : Widget Unit := ⟨id⟩

/-- A class inductive. -/
class inductive Good : Nat → Prop
  | zero : Good 0

/-- Guillemets. -/
theorem «my thm» : True := trivial

theorem univ_thm.{u} (α : Sort u) : True := trivial

open Nat in theorem one_line_open : True := trivial

@[simp]
private
theorem split_mods : True := trivial

/-- doc -/ -- trailing comment
theorem doc_then_comment : True := trivial

/-- A documented example. -/
/- `example:tag` a plain comment between the docstring and the example -/
example : True := trivial

structure Box where
  left : Nat

def withWhere (n : Nat) : Nat := aux n
where
  aux (m : Nat) : Nat := m

theorem _root_.Nat.lab_root : True := trivial

def a𝔸 : Nat := 1 /- 𝔸𝔸 -/ def b𝔸 : Nat := 2

end Lab

namespace A.B

theorem in_ab : True := trivial

end B

theorem in_a : True := trivial

end A
"""

TRICKY_DECLS = [
    ("Lab.before", "/-- Before", "theorem |before"),
    ("Lab.isEven", "/-- Even, in", "def |isEven"),
    ("Lab.isOdd", "def isOdd", "def |isOdd"),
    ("Lab.after_mutual", "/-- After the mutual", "theorem |after_mutual"),
    ("Lab.Widget", "class Widget", "class |Widget"),
    ("Lab.widgetBool", "/-- A prioritised", "widgetBool"),
    ("Lab.instWidgetUnit", "instance : Widget Unit", "instance"),
    ("Lab.Good", "/-- A class inductive", "inductive |Good"),
    ("Lab.«my thm»", "/-- Guillemets", "«my thm»"),
    ("Lab.univ_thm", "theorem univ_thm", "theorem |univ_thm"),
    ("Lab.one_line_open", "theorem one_line_open", "theorem |one_line_open"),
    ("_private.Pkg.Tricky.0.Lab.split_mods", "@[simp]\nprivate", "theorem |split_mods"),
    ("Lab.doc_then_comment", "/-- doc -/", "theorem |doc_then_comment"),
    ("Lab.Box", "structure Box", "structure |Box"),
    ("Lab.withWhere", "def withWhere", "def |withWhere"),
    ("Nat.lab_root", "theorem _root_", "theorem |_root_.Nat.lab_root"),
    ("Lab.a𝔸", "def a𝔸", "def |a𝔸"),
    ("Lab.b𝔸", "def b𝔸", "def |b𝔸"),
    ("A.B.in_ab", "theorem in_ab", "theorem |in_ab"),
    ("A.in_a", "theorem in_a :", "theorem |in_a"),
]

TRICKY_ITEMS = [
    (NS, "namespace Lab", []),
    (DECL, "/-- Before", ["Lab.before"]),
    ("Lean.Parser.Command.mutual", "mutual", ["Lab.isEven", "Lab.isOdd"]),
    (DECL, "/-- After the mutual", ["Lab.after_mutual"]),
    (DECL, "class Widget", ["Lab.Widget", "Lab.Widget.op"]),
    (DECL, "/-- A prioritised", ["Lab.widgetBool"]),
    (DECL, "instance : Widget Unit", []),
    (DECL, "/-- A class inductive", ["Lab.Good.zero", "Lab.Good"]),
    (DECL, "/-- Guillemets", ["Lab.«my thm»"]),
    (DECL, "theorem univ_thm", ["Lab.univ_thm"]),
    (IN, "open Nat in theorem", ["Lab.one_line_open"]),
    (DECL, "@[simp]\nprivate", ["_private.0.Lab.split_mods"]),
    (DECL, "/-- doc -/", ["Lab.doc_then_comment"]),
    (DECL, "/-- A documented example", []),
    (DECL, "structure Box", ["Lab.Box", "Lab.Box.left"]),
    (DECL, "def withWhere", ["Lab.withWhere", "Lab.withWhere.aux"]),
    (DECL, "theorem _root_", ["Nat.lab_root"]),
    (DECL, "def a𝔸", ["Lab.a𝔸"]),
    (DECL, "def b𝔸", ["Lab.b𝔸"]),
    (END, "end Lab", []),
    (NS, "namespace A.B", []),
    (DECL, "theorem in_ab", ["A.B.in_ab"]),
    (END, "end B", []),
    (DECL, "theorem in_a :", ["A.in_a"]),
    (END, "end A", []),
]


#: anonymous examples behind `<command> ... in` prefixes (item kinds as a real v4.33 build writes them)
PREFIXED = """\
namespace Pre

#guard_msgs in
example : 1 + 1 = 2 := rfl

attribute [local instance] Classical.propDecidable in
/-- doc after attribute-in -/
example : True := trivial

/-- trace: hi -/
#guard_msgs in
example : True := by trace "hi"; trivial

attribute [local simp] «Nat».add_comm in example : True := trivial

set_option maxHeartbeats 400 in
#guard_msgs in
/-- doc after two prefixes -/
example : True := trivial

end Pre
"""

PREFIXED_ITEMS = [
    (NS, "namespace Pre", []),
    ("Lean.guardMsgsCmd", "#guard_msgs in\nexample : 1", []),
    (IN, "attribute [local instance]", []),
    ("Lean.guardMsgsCmd", "/-- trace", []),
    (IN, "attribute [local simp]", []),
    (IN, "set_option", []),
    (END, "end Pre", []),
]


def fields(d) -> dict:
    out = dataclasses.asdict(d)
    out.pop("origin")
    return out


class Scratch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cf-leanbuild-",
                                          dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.lake = Lake(self.root)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(leanbuild.ENV, None)

    def parse(self, rel: str, **kw):
        build = kw.pop("build", None) or leanbuild.Build.find(self.root)
        return leanbuild.parse_file(self.root / rel, rel, build=build, **kw), build


class HashTest(unittest.TestCase):
    def test_known_answers_computed_with_lean(self):
        """``mixHash 1723 (hash s.crlfToLf)`` in Lean v4.33, i.e. Lake's ``Hash.ofText``."""
        for text, want in [("", "21c42906adea99f5"), ("a", "6ccb214a796350f7"),
                           ("theorem foo : True := trivial\n", "86cc8148bd53757a"),
                           ("x\r\ny\r\n", "40ae68691b05adc1"),
                           ("∀ ε > 0, «my thm» ⟨⟩\n", "e36d0c111d6812bd"),
                           ("0123456789abcdefg", "b0f1b7c6f231ad1b")]:
            self.assertEqual(leanbuild.of_text(text), want, repr(text))


class FreshnessTest(Scratch):
    REL = "Demo/Basic.lean"

    def setUp(self):
        super().setUp()
        self.lake.module(self.REL, ORDINARY, ORDINARY_DECLS, ORDINARY_ITEMS)

    def fallback(self, reason: str):
        decls, build = self.parse(self.REL)
        self.assertEqual(build.files[self.REL], ("text", reason, None))
        self.assertEqual(build.tally(), {"text": 1, "fallback": {reason: 1}})
        self.assertEqual([dataclasses.asdict(d) for d in decls],
                         [dataclasses.asdict(d) for d in leanparse.parse_file(self.root / self.REL, self.REL)])

    def without_literate(self, reason: str):
        decls, build = self.parse(self.REL)
        self.assertEqual(build.files[self.REL], ("lean", None, reason))
        self.assertEqual(build.tally(), {"lean": 1, "without_literate": {reason: 1}})
        self.assertEqual({d.origin for d in decls if d.kind == "example"}, {"text"})
        self.assertEqual({d.origin for d in decls if d.kind != "example"}, {"lean"})
        return decls

    def test_a_current_build_is_read(self):
        decls, build = self.parse(self.REL)
        self.assertEqual(build.tally(), {"lean": 1})
        self.assertEqual({d.origin for d in decls}, {"lean"})

    def test_an_edited_source_falls_back(self):
        (self.root / self.REL).write_text(ORDINARY + "\ntheorem added : True := trivial\n", encoding="utf-8")
        self.fallback("source changed since the build")

    def test_a_crlf_checkout_of_the_same_text_is_current(self):
        (self.root / self.REL).write_bytes(ORDINARY.replace("\n", "\r\n").encode("utf-8"))
        _decls, build = self.parse(self.REL)
        self.assertEqual(build.files[self.REL], ("lean", None, None))

    def test_a_crlf_build_reads_the_ilean_only(self):
        self.lake.literate(self.REL, ORDINARY, ORDINARY_ITEMS, "0d960ed631dee6e6", crlf=True)
        (self.root / self.REL).write_bytes(ORDINARY.replace("\n", "\r\n").encode("utf-8"))
        decls = self.without_literate("literate does not match the source")
        self.assertEqual([fields(d) for d in decls],
                         [fields(d) for d in leanparse.parse_file(self.root / self.REL, self.REL)])

    def test_a_literate_file_older_than_the_source_leaves_the_ilean(self):
        # edits Lake's literate trace cannot see: the .olean (its only input) stays the same
        edited = (ORDINARY.replace("\n/-- Section member.", "section Beta\n/-- Section member.")
                  .replace("in_companions : True := trivial\n\n", "in_companions : True := trivial\nend Beta\n")
                  + "\n/-- `Exercise 1.2.3` -/\nexample : 4 = 4 := rfl\n")
        self.lake.module(self.REL, edited, ORDINARY_DECLS)
        decls = self.without_literate("literate does not match the source")
        self.assertEqual([fields(d) for d in decls],
                         [fields(d) for d in leanparse.parse_file(self.root / self.REL, self.REL)])
        self.assertEqual(next(d for d in decls if d.name == "in_companions").sections, ("Companions", "Beta"))
        self.assertEqual(decls[-1].doc, "`Exercise 1.2.3`")

    def test_a_literate_item_out_of_place_leaves_the_ilean(self):
        path = self.lake.path(self.REL, ".json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["items"]["items"][3]["range"][0]["column"] += 1
        text = json.dumps(doc)
        path.write_text(text, encoding="utf-8")
        self.lake.path(self.REL, ".json.hash").write_text(leanbuild.of_text(text), encoding="utf-8")
        self.without_literate("literate does not match the source")

    def test_a_tampered_ilean_falls_back(self):
        path = self.lake.path(self.REL, ".ilean")
        path.write_text(path.read_text(encoding="utf-8").replace('"version":5', '"version":5 '), encoding="utf-8")
        self.fallback(".ilean does not match its trace")

    def test_missing_build_pieces_fall_back(self):
        self.lake.path(self.REL, ".ilean").unlink()
        self.fallback("no .ilean")
        self.lake.path(self.REL, ".trace").unlink()
        self.fallback("no build data")

    def test_a_trace_of_another_source_falls_back(self):
        path = self.lake.path(self.REL, ".trace")
        path.write_text(path.read_text(encoding="utf-8").replace("/Demo/Basic.lean", "/Other/Basic.lean"),
                        encoding="utf-8")
        self.fallback("trace names another source")

    def test_unexpected_formats_fall_back_and_never_raise(self):
        trace = self.lake.path(self.REL, ".trace")
        good = json.loads(trace.read_text(encoding="utf-8"))
        Lake.write_json(trace, dict(good, outputs={"o": []}))
        self.fallback("unexpected trace format")
        trace.write_text("{not json", encoding="utf-8")
        self.fallback("unreadable trace")
        ilean = json.dumps({"decls": {"Demo.one": [3, 0, 3]}, "module": "Demo.Basic", "version": 5})
        self.lake.path(self.REL, ".ilean").write_text(ilean, encoding="utf-8")
        Lake.write_json(trace, dict(good, outputs=dict(good["outputs"], i=leanbuild.of_text(ilean) + ".ilean")))
        self.fallback("unexpected .ilean format")
        ilean = json.dumps({"decls": {"Demo.one": [300, 0, 300, 1, 300, 0, 300, 1]}, "module": "Demo.Basic"})
        self.lake.path(self.REL, ".ilean").write_text(ilean, encoding="utf-8")
        Lake.write_json(trace, dict(good, outputs=dict(good["outputs"], i=leanbuild.of_text(ilean) + ".ilean")))
        self.fallback(".ilean positions outside the source")

    def test_a_tampered_literate_file_leaves_the_ilean(self):
        path = self.lake.path(self.REL, ".json")
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.without_literate("literate does not match its hash")

    def test_a_literate_file_of_another_olean_leaves_the_ilean(self):
        self.lake.literate(self.REL, ORDINARY, ORDINARY_ITEMS, olean="1f10c24028125a44")
        decls = self.without_literate("literate built from another .olean")
        self.assertEqual([fields(d) for d in decls],
                         [fields(d) for d in leanparse.parse_file(self.root / self.REL, self.REL)])

    def test_a_literate_file_of_unexpected_shape_leaves_the_ilean(self):
        path = self.lake.path(self.REL, ".json")
        text = json.dumps({"module": "Demo.Basic", "items": {"items": [{"kind": DECL}]}})
        path.write_text(text, encoding="utf-8")
        self.lake.path(self.REL, ".json.hash").write_text(leanbuild.of_text(text), encoding="utf-8")
        self.without_literate("unexpected literate format")
        shutil.rmtree(self.lake.lit)
        self.without_literate("no literate")

    def test_the_given_text_is_the_text_hashed_and_parsed(self):
        build = leanbuild.Build.find(self.root)
        edited = ORDINARY.replace("theorem top_level", "theorem top_level_renamed")
        decls = leanbuild.parse_file(self.root / self.REL, self.REL, build=build, text=edited)
        self.assertEqual(build.files[self.REL][:2], ("text", "source changed since the build"))
        self.assertIn("top_level_renamed", [d.name for d in decls])
        decls = leanbuild.parse_file(self.root / self.REL, self.REL, build=build, text=ORDINARY)
        self.assertEqual(build.files[self.REL][0], "lean")

    def test_cf_lean_source_text_forces_the_text_parser(self):
        os.environ[leanbuild.ENV] = "text"
        self.assertIsNone(leanbuild.Build.find(self.root))
        self.assertEqual(leanbuild.tally(None), {"build": f"not read ({leanbuild.ENV}=text)"})
        decls = leanbuild.parse_file(self.root / self.REL, self.REL, build=None)
        self.assertEqual({d.origin for d in decls}, {"text"})
        os.environ.pop(leanbuild.ENV)
        self.assertIsNotNone(leanbuild.Build.find(self.root))
        self.assertIsNone(leanbuild.Build.find(self.root / "Demo"))

    def test_the_fingerprint_follows_the_build(self):
        files = [self.root / self.REL]
        before = leanbuild.fingerprint(leanbuild.Build.find(self.root), files, self.root)
        self.lake.literate(self.REL, ORDINARY, ORDINARY_ITEMS, olean="1f10c24028125a44")
        after = leanbuild.fingerprint(leanbuild.Build.find(self.root), files, self.root)
        self.assertNotEqual(after, before)
        self.lake.path(self.REL, ".ilean").unlink()
        self.assertNotEqual(leanbuild.fingerprint(leanbuild.Build.find(self.root), files, self.root), after)
        self.assertEqual(leanbuild.fingerprint(None, files, self.root), "text")


class ParityTest(Scratch):
    def test_ordinary_declarations_read_the_same_from_the_build(self):
        self.lake.module("Demo/Basic.lean", ORDINARY, ORDINARY_DECLS, ORDINARY_ITEMS)
        text = leanparse.parse_file(self.root / "Demo/Basic.lean", "Demo/Basic.lean")
        for items in (True, False):
            if not items:
                shutil.rmtree(self.lake.lit)
            built, build = self.parse("Demo/Basic.lean")
            self.assertEqual(build.files["Demo/Basic.lean"][0], "lean")
            self.assertEqual([fields(d) for d in built], [fields(d) for d in text])
            self.assertEqual([leanparse.signature(d.body) for d in built],
                             [leanparse.signature(d.body) for d in text])
        self.assertEqual(len(text), 15)

    def test_a_source_directory_below_the_checkout(self):
        rel = "src/Demo/Basic.lean"
        self.lake.module(rel, ORDINARY, ORDINARY_DECLS, ORDINARY_ITEMS, module="Demo.Basic")
        decls, build = self.parse(rel)
        self.assertEqual(build.tally(), {"lean": 1})
        self.assertEqual([fields(d) for d in decls], [fields(d) for d in leanparse.parse_file(self.root / rel, rel)])


class TrickyTest(Scratch):
    REL = "Pkg/Tricky.lean"
    #: (keyword needle, kind, name, full name, namespace, private, doc)
    EXPECTED = [
        ("theorem before", "theorem", "before", "Lab.before", "Lab", False, "Before."),
        ("def isEven", "def", "isEven", "Lab.isEven", "Lab", False, "Even, in a mutual block."),
        ("def isOdd", "def", "isOdd", "Lab.isOdd", "Lab", False, None),
        ("theorem after_mutual", "theorem", "after_mutual", "Lab.after_mutual", "Lab", False,
         "After the mutual block."),
        ("class Widget", "class", "Widget", "Lab.Widget", "Lab", False, None),
        ("instance (priority", "instance", "widgetBool", "Lab.widgetBool", "Lab", False,
         "A prioritised named instance."),
        ("instance : Widget Unit", "instance", None, None, "Lab", False, None),
        ("class inductive Good", "class", "Good", "Lab.Good", "Lab", False, "A class inductive."),
        ("theorem «my", "theorem", "«my thm»", "Lab.«my thm»", "Lab", False, "Guillemets."),
        ("theorem univ_thm", "theorem", "univ_thm", "Lab.univ_thm", "Lab", False, None),
        ("open Nat in theorem", "theorem", "one_line_open", "Lab.one_line_open", "Lab", False, None),
        ("theorem split_mods", "theorem", "split_mods", "Lab.split_mods", "Lab", True, None),
        ("theorem doc_then_comment", "theorem", "doc_then_comment", "Lab.doc_then_comment", "Lab", False, "doc"),
        ("example : True", "example", None, None, "Lab", False, "A documented example."),
        ("structure Box", "structure", "Box", "Lab.Box", "Lab", False, None),
        ("def withWhere", "def", "withWhere", "Lab.withWhere", "Lab", False, None),
        ("theorem _root_", "theorem", "_root_.Nat.lab_root", "Nat.lab_root", "Lab", False, None),
        ("def a𝔸", "def", "a𝔸", "Lab.a𝔸", "Lab", False, None),
        ("def a𝔸", "def", "b𝔸", "Lab.b𝔸", "Lab", False, None),
        ("theorem in_ab", "theorem", "in_ab", "A.B.in_ab", "A.B", False, None),
        ("theorem in_a :", "theorem", "in_a", "A.in_a", "A", False, None),
    ]

    def setUp(self):
        super().setUp()
        self.lake.module(self.REL, TRICKY, TRICKY_DECLS, TRICKY_ITEMS)

    def line(self, needle: str) -> int:
        return TRICKY[:TRICKY.index(needle)].count("\n") + 1

    def expected(self):
        out = []
        for needle, kind, name, full, ns, private, doc in self.EXPECTED:
            line = self.line(needle)
            out.append((line, kind, name, full or f"{kind}@{self.REL}:{line}", ns, private, doc))
        return out

    def test_the_build_reads_what_leanparse_gets_wrong(self):
        decls, build = self.parse(self.REL)
        self.assertEqual(build.tally(), {"lean": 1})
        got = [(d.line, d.kind, d.name, d.full_name, d.namespace, d.private, d.doc) for d in decls]
        self.assertEqual(got, self.expected())
        self.assertEqual({d.origin for d in decls}, {"lean"})
        by_name = {d.name: d for d in decls}
        self.assertEqual(by_name["split_mods"].modifiers, ("private",))
        self.assertEqual(by_name["doc_then_comment"].doc_line, self.line("/-- doc -/"))
        self.assertEqual(by_name["a𝔸"].body, "def a𝔸 : Nat := 1 /- 𝔸𝔸 -/ ")
        self.assertEqual(by_name["b𝔸"].body, "def b𝔸 : Nat := 2\n")
        self.assertEqual(leanparse.signature(by_name["univ_thm"].body), "theorem univ_thm.{u} (α : Sort u) : True")
        self.assertEqual(by_name["isEven"].body, "  def isEven : Nat → Bool\n    | 0 => true\n    | n + 1 => isOdd n")

    def test_without_literate_data_lean_s_names_correct_the_namespaces(self):
        shutil.rmtree(self.lake.lit)
        decls, build = self.parse(self.REL)
        self.assertEqual(build.tally(), {"lean": 1, "without_literate": {"no literate": 1}})
        named = [(d.line, d.name, d.full_name, d.namespace) for d in decls if d.kind != "example"]
        # nothing in the .ilean names the namespace around `_root_.X`: leanparse's value stays
        text_ns = {d.line: d.namespace for d in leanparse.parse_file(self.root / self.REL, self.REL)}
        self.assertEqual(named, [(line, name, full, text_ns[line] if (name or "").startswith("_root_") else ns)
                                 for line, kind, name, full, ns, _p, _d in self.expected() if kind != "example"])
        self.assertEqual([d.origin for d in decls if d.kind == "example"], ["text"])


class PrefixTest(Scratch):
    def test_examples_behind_any_in_prefix(self):
        self.lake.module("Pre.lean", PREFIXED, [], PREFIXED_ITEMS)
        decls, build = self.parse("Pre.lean")
        self.assertEqual(build.tally(), {"lean": 1})
        # the docstring before `#guard_msgs` is its expected output, not the example's
        self.assertEqual([(d.line, d.full_name, d.namespace, d.doc, d.origin) for d in decls], [
            (4, "example@Pre.lean:4", "Pre", None, "lean"),
            (8, "example@Pre.lean:8", "Pre", "doc after attribute-in", "lean"),
            (12, "example@Pre.lean:12", "Pre", None, "lean"),
            (14, "example@Pre.lean:14", "Pre", None, "lean"),
            (19, "example@Pre.lean:19", "Pre", "doc after two prefixes", "lean"),
        ])


class ConsumerTest(Scratch):
    """Every reader that used leanparse takes the build when one verifies."""

    def ignore_lake(self, repo: Path) -> None:
        (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (repo / ".git" / "info" / "exclude").write_text(".lake/\n", encoding="utf-8")

    def test_the_docstring_tags_adapter(self):
        text = TRICKY.replace("/-- Even, in a mutual block. -/", "/-- `Exercise 1.1.3` in a mutual block. -/")
        decls = [(f, "/-- `Exercise" if s == "/-- Even, in" else s, sel) for f, s, sel in TRICKY_DECLS]
        items = [(k, "/-- `Exercise" if s == "/-- Even, in" else s, d) for k, s, d in TRICKY_ITEMS]
        self.lake.module("Pkg/Tricky.lean", text, decls, items)
        adapter = from_config({"adapter": "docstring_tags", "name": "demo", "repo": str(self.root),
                               "glob": "Pkg/*.lean", "item_kinds": ["Exercise"]}, source="<synthetic>")
        rows = list(adapter.extract(sources={"src": "aa" * 32}))
        self.assertEqual([(r["item"], r["decl"]) for r in rows], [("Exercise 1.1.3", "Lab.isEven")])
        self.assertEqual(adapter.dry_run(sources={"src": "aa" * 32})["lean_source"], {"lean": 1})
        key = adapter.key()
        os.environ[leanbuild.ENV] = "text"
        text_adapter = from_config(adapter.config, source="<synthetic>")
        self.assertEqual(text_adapter.dry_run(sources={"src": "aa" * 32})["lean_source"],
                         {"build": f"not read ({leanbuild.ENV}=text)"})
        self.assertNotEqual(text_adapter.key(), key)
        self.assertIn("lean_build", adapter.key_inputs())
        self.assertNotIn("lean_build", text_adapter.key_inputs())   # a text-only key is the one it always was

    def test_the_attribution_table_adapter_and_exporter(self):
        repo = self.root / "repo"
        commit = TE.make_attribution_table_repo(repo)
        self.ignore_lake(repo)
        Lake(repo).module("Demo/Basic.lean", TE.AT_LEAN, [
            ("Demo.Box", "/-- Boxes", "structure |Box"),
            ("Demo.Box.volume", "/-- The volume", "def |Box.volume"),
            ("Demo.volume_add", "/-- Finite", "theorem |volume_add"),
            ("_private.Demo.Basic.0.Demo.helper_bound", "/-- A helper", "theorem |helper_bound"),
            ("Demo.replacement_stated", "/-- The declaration a fix", "theorem |replacement_stated"),
        ], [
            (NS, "namespace Demo", []),
            (DECL, "/-- Boxes", ["Demo.Box", "Demo.Box.left", "Demo.Box.right"]),
            (DECL, "/-- The volume", ["Demo.Box.volume"]),
            (DECL, "/-- Finite", ["Demo.volume_add"]),
            (DECL, "/-- A helper", ["_private.0.Demo.helper_bound"]),
            (DECL, "example (b", []),
            (DECL, "/-- The declaration a fix", ["Demo.replacement_stated"]),
            (END, "end Demo", []),
        ])
        adapter = from_config({"adapter": "attribution_table", "name": "adam", "repo": str(repo),
                               "item_kinds": ["Definition", "Exercise", "Lemma"]}, source="<synthetic>")
        claims, counts = adapter.lean_claims(repo)
        self.assertEqual(counts["declarations"], 6)
        self.assertEqual(leanbuild.tally(adapter.lean_build(repo)), {"lean": 1})
        _doc, report = TE.attribution_table.export(repo, commit, name="demo", repo_id="git.example.org/demo/demo",
                                                   source_id="demo-pdf", source_title="A Demo Book",
                                                   source_edition="first")
        self.assertEqual(report.counts["lean_source"], {"lean": 1})
        _doc, report = TE.attribution_table.export(repo, commit, name="demo", repo_id="git.example.org/demo/demo",
                                                   source_id="demo-pdf", source_title="A Demo Book",
                                                   source_edition="first", lean_build=self.root / "nowhere")
        self.assertEqual(report.counts["lean_source"], {"build": "none"})

    def test_the_docstring_tags_exporter(self):
        repo = self.root / "repo"
        commit, pdf, xref, pin, _site = TE.make_docstring_tags_repo(repo)
        self.ignore_lake(repo)
        Lake(repo).module("Demo/Section_1_2.lean", TE.DT_LEAN_2, [
            ("Demo.lemma_125", "/-- `Lemma 1.2.5`", "theorem |lemma_125")],
            [(NS, "namespace Demo", []), (DECL, "/-- `Lemma", ["Demo.lemma_125"]), (END, "end Demo", [])])
        config = self.root / "adapter.yaml"
        config.write_text(TE.DT_ADAPTER, encoding="utf-8")
        doc, report = TE.docstring_tags.export(
            repo, commit, pdf=pdf, xref=xref, site_pages=pin, site=None, adapter_config=config, name="demo",
            repo_id="git.example.org/demo/demo", pdf_text=TE.PdfText(list(TE.DT_PAGES), version="0.0-test"),
            source_id="demo-pdf", source_title="A Demo Book", page_offset=2)
        self.assertEqual(report.counts["lean_source"],
                         {"lean": 1, "text": 1, "fallback": {"no build data": 1}})
        self.assertIn("Demo.lemma_125", TE.by_id(doc, "declarations"))

    def test_the_declaration_index(self):
        from plan import decl_index

        self.lake.module("Pkg/Tricky.lean", TRICKY, TRICKY_DECLS, TRICKY_ITEMS)
        paths = type("P", (), {"detect_companion_dir": lambda _s, n: self.root / "nope"})()
        tally: dict = {}
        names, origin = decl_index.for_companion(paths, "lab", self.root, tally)
        self.assertEqual(origin, "lean-source")
        self.assertIn("Lab.isEven", names)
        self.assertIn("Lab.after_mutual", names)
        self.assertEqual(tally, {"lab": {"lean": 1}})


def _env_dir(name: str) -> Path | None:
    value = os.environ.get(name)
    p = Path(value).expanduser() if value else None
    return p if p is not None and p.exists() else None


class RealCompanionParityTest(unittest.TestCase):
    """Build vs text on a real built companion (skipped without CF_TML/CF_MT)."""

    def check(self, var: str) -> None:
        repo = _env_dir(var)
        if repo is None:
            self.skipTest(f"{var} not set")
        build = leanbuild.Build.find(repo)
        if build is None:
            self.skipTest(f"{var} has no Lake build")
        files = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", "*.lean"], check=True,
                               capture_output=True, text=True).stdout.split("\0")
        compared = 0
        for rel in filter(None, files):
            built = leanbuild.parse_file(repo / rel, rel, build=build)
            if build.files[rel][0] != "lean":
                continue
            text = leanparse.parse_file(repo / rel, rel)
            by_key = {(d.line, d.kind, d.name): d for d in text}
            for b in built:
                t = by_key.get((b.line, b.kind, b.name))
                if t is None:
                    continue
                compared += 1
                where = f"{rel}:{b.line} {b.full_name}"
                self.assertEqual(leanparse.signature(b.body), leanparse.signature(t.body), where)
                diff = {k for k, v in fields(b).items() if fields(t)[k] != v}
                # Lean attaches a docstring across a plain comment; leanparse does not
                if diff & {"doc", "doc_line"}:
                    self.assertIsNone(t.doc, where)
                    gap = Path(repo / rel).read_text(encoding="utf-8").split("\n")[b.doc_line - 1:b.line - 1]
                    self.assertEqual(leanparse.strip_comments("\n".join(gap)).strip(), "", where)
                # a body bound moves with the next declaration's docstring, over comments only
                if diff & {"body", "def_body"}:
                    self.assertEqual(leanparse.strip_comments(b.body).rstrip(),
                                     leanparse.strip_comments(t.body).rstrip(), where)
                self.assertLessEqual(diff, {"doc", "doc_line", "body"}, where)
        self.assertGreater(compared, 0, f"no file of {var} has a current build")

    def test_tao_measure_lean(self):
        self.check("CF_TML")

    def test_measure_theory(self):
        self.check("CF_MT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
