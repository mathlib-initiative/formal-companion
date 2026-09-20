"""Plant ONE known defect of a named class into a synthetic companion file.

New code.  It exists so the answer key describes something a script produced,
not something a human remembered to write down: every entry in
``tests/e2e/answer-key.json`` is the record one of these mutations returned, so
a fixture that drifts from its key fails a test instead of quietly scoring
itself.

The classes are the defect shapes references/method.md S5 catalogues, one
function each::

    strengthened_hypothesis   an extra binder the source does not ask for
    vacuity                   the conclusion drops to something trivially true
    dropped_dimension         a bound variable pinned to a literal
    reversed_inequality       the two sides of a <= swapped
    proxy_definition          the defining term replaced by a library object
    missing_item              the book tag removed, so nothing is tagged to it
    wrong_page_anchor         the docstring's page moved off the printed one
    convention_rename         the declaration renamed; the content is identical

Every mutation is a pure text transformation of ONE declaration and is
deterministic given ``seed`` -- re-running the generator rewrites the same
bytes, which is what lets the fixture repos be regenerated and the key stay
true.

**Never run this on a real companion.**  Companion repositories are read-only
data (CLAUDE.md S4), and a planted defect in one would be indistinguishable from
a real one.  :func:`mutate_file` therefore refuses a path that is neither under
the system temp directory nor under an explicit ``--fixture-root``, and says so
in one line naming ``--i-know``.

usage:
    python3 scripts/score/mutate.py <file.lean> --class vacuity [--seed N]
        [--object <library constant>] [--fixture-root <dir>] [--i-know]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # `python3 scripts/score/mutate.py`
    sys.path.insert(0, str(_SCRIPTS))

from cflib.errors import CfError  # noqa: E402

#: Lean keywords the fixture declarations start with
KEYWORDS = ("def", "abbrev", "theorem", "lemma", "instance", "structure")

#: guards ``strengthened_hypothesis`` may add, indexed by the seed
_GUARDS = ("0 < %s", "%s > 0", "1 <= %s")
#: literals ``dropped_dimension`` may pin to, indexed by the seed
_PINS = ("1", "2", "0")
#: page deltas ``wrong_page_anchor`` may apply, indexed by the seed.  None is
#: +/-1: an adjacent page is advisory in pair/anchor_check.py, so a key that
#: planted one would be scoring a row the checker deliberately does not raise.
_PAGE_DELTAS = (-6, 4, -3)


class Declaration:
    """One Lean declaration of the fixture's shape, split at depth 0.

    ``theorem f (a : Nat) (h : p a) : q a := h`` ->
    keyword ``theorem``, name ``f``, binders ``(a : Nat) (h : p a)``,
    type ``q a``, body ``h``.  The split walks the text tracking parenthesis
    depth, because the first ``:`` in the line belongs to a binder.
    """

    def __init__(self, text: str, start: int, keyword: str, name: str,
                 binders: str, type_: str, body: str):
        self.text = text
        self.start = start
        self.keyword = keyword
        self.name = name
        self.binders = binders
        self.type = type_
        self.body = body

    def render(self) -> str:
        """The declaration as source, with the parts as they now stand."""
        head = f"{self.keyword} {self.name}"
        if self.binders.strip():
            head += " " + self.binders.strip()
        return f"{head} : {self.type.strip()} := {self.body.strip()}"

    def binder_groups(self) -> list[str]:
        """The ``(...)`` groups of the binder list, in order."""
        out, depth, cur = [], 0, ""
        for ch in self.binders:
            if ch == "(":
                depth += 1
            if depth:
                cur += ch
            if ch == ")":
                depth -= 1
                if depth == 0:
                    out.append(cur)
                    cur = ""
        return out

    def first_variable(self) -> str:
        """The first bound variable name, for a guard that mentions something."""
        for group in self.binder_groups():
            inner = group[1:-1]
            names = inner.split(":", 1)[0].split()
            if names:
                return names[0]
        return "n"


def parse_declaration(text: str) -> Declaration:
    """Split the LAST declaration in ``text`` (the fixture writes one per file)."""
    match = None
    pattern = re.compile(r"(?m)^(?P<kw>%s)[ \t]+(?P<name>[A-Za-z_][\w.']*)"
                         % "|".join(KEYWORDS))
    for match in pattern.finditer(text):
        pass
    if match is None:
        raise CfError("no Lean declaration in this text",
                      hint="mutate one of the fixture modules tests/e2e/make_notes_repos.py writes")
    rest = text[match.end():]
    depth, type_at, body_at = 0, None, None
    i = 0
    while i < len(rest):
        ch = rest[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch == ":":
            if rest[i + 1:i + 2] == "=":
                body_at = i
                break
            if type_at is None:
                type_at = i
        i += 1
    if type_at is None or body_at is None:
        raise CfError(f"declaration {match.group('name')!r} has no `: type := body` shape",
                      hint="the mutation classes rewrite a statement, so they need one")
    return Declaration(
        text, match.start(), match.group("kw"), match.group("name"),
        rest[:type_at], rest[type_at + 1:body_at], rest[body_at + 2:],
    )


def _replace_declaration(text: str, decl: Declaration) -> str:
    """Put a re-rendered declaration back where the old one was.

    Everything from the declaration's first keyword onwards is rewritten, which
    is exact for the fixture's one-declaration modules and is why the classes
    that rewrite a statement are documented as taking one.
    """
    return text[:decl.start] + decl.render() + "\n"


def _word(name: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_'.])%s(?![A-Za-z0-9_'])" % re.escape(name))


# --------------------------------------------------------------------------
# the mutation classes
# --------------------------------------------------------------------------
def _strengthened_hypothesis(text: str, seed: int, **_) -> tuple[str, dict]:
    """Add a binder the source never asks for (method.md S5 "strengthened")."""
    decl = parse_declaration(text)
    var = decl.first_variable()
    guard = _GUARDS[seed % len(_GUARDS)] % var
    decl.binders = decl.binders.rstrip() + f" (hMut : {guard})"
    return _replace_declaration(text, decl), {"decl": decl.name, "added_binder": f"(hMut : {guard})"}


def _vacuity(text: str, seed: int, **_) -> tuple[str, dict]:
    """Drop the conclusion to something that holds by ``rfl``.

    A negated conclusion loses its negation (the shape the booklet's "exhibit a
    set that is NOT ..." exercise invites); anything else becomes ``True``.
    """
    decl = parse_declaration(text)
    before = decl.type.strip()
    inner = re.match(r"^Not\s*\((?P<inner>.*)\)$", before, re.S)
    if inner:
        decl.type = inner.group("inner")
        decl.body = "rfl"
    else:
        decl.type = "True"
        decl.body = "trivial"
    return _replace_declaration(text, decl), {"decl": decl.name, "was": before,
                                              "now": decl.type.strip()}


def _dropped_dimension(text: str, seed: int, **_) -> tuple[str, dict]:
    """Pin the last bound variable to a literal -- the ``d := 1`` shape."""
    decl = parse_declaration(text)
    groups = decl.binder_groups()
    if not groups:
        raise CfError(f"{decl.name} binds nothing to pin",
                      hint="dropped_dimension needs a bound variable")
    last = groups[-1]
    names = last[1:-1].split(":", 1)[0].split()
    if len(names) < 2:
        raise CfError(f"{decl.name}'s last binder group binds one variable",
                      hint="pinning it would leave the statement about nothing; "
                           "pick a declaration with two bound variables")
    pinned, kept = names[-1], names[:-1]
    keep_type = last[1:-1].split(":", 1)[1].strip()
    decl.binders = decl.binders.replace(last, "(%s : %s)" % (" ".join(kept), keep_type))
    literal = _PINS[seed % len(_PINS)]
    decl.type = _word(pinned).sub(literal, decl.type)
    decl.body = _word(pinned).sub(literal, decl.body)
    return _replace_declaration(text, decl), {"decl": decl.name, "pinned": pinned,
                                              "to": literal}


def _reversed_inequality(text: str, seed: int, **_) -> tuple[str, dict]:
    """Swap the sides of the first ``<=`` -- subadditive becomes superadditive."""
    decl = parse_declaration(text)
    before = decl.type.strip()
    left, sep, right = before.partition("<=")
    if not sep:
        raise CfError(f"{decl.name}'s conclusion has no `<=` to reverse",
                      hint="reversed_inequality needs an inequality")
    decl.type = f"{right.strip()} <= {left.strip()}"
    return _replace_declaration(text, decl), {"decl": decl.name, "was": before,
                                              "now": decl.type.strip()}


def _proxy_definition(text: str, seed: int, library_object: str = "", **_) -> tuple[str, dict]:
    """Define the book's object as the library's -- the PROXY shape.

    ``library_object`` is the library constant to hand back.  It is applied to the
    declaration's first bound variable so the body still names its argument.
    """
    if not library_object:
        raise CfError("proxy_definition needs the library constant to substitute",
                      hint="pass --object <fully qualified constant>")
    decl = parse_declaration(text)
    before = decl.body.strip()
    decl.body = f"{library_object} {decl.first_variable()}"
    return _replace_declaration(text, decl), {"decl": decl.name, "was": before,
                                              "now": decl.body.strip(), "object": library_object}


_TAG_RE = re.compile(r"`(?P<tag>[A-Z][A-Za-z]+ \d+(?:\.\d+)+)`[ \t]*(?:\(fixture\)\.?)?[ \t]*")
_PAGE_RE = re.compile(r"(?P<lead>Book p\.[ \t]*)(?P<page>\d+)")


def _missing_item(text: str, seed: int, **_) -> tuple[str, dict]:
    """Remove the book tag, so the adapter tags no declaration to the item."""
    match = _TAG_RE.search(text)
    if not match:
        raise CfError("this module carries no book tag to remove",
                      hint="missing_item needs a docstring tag like `Definition 1.4.1`")
    out = text[:match.start()] + text[match.end():]
    return out, {"removed_tag": match.group("tag")}


def _wrong_page_anchor(text: str, seed: int, **_) -> tuple[str, dict]:
    """Move the docstring's page off the page the source prints the item on."""
    match = _PAGE_RE.search(text)
    if not match:
        raise CfError("this module carries no `Book p. N` anchor to move",
                      hint="wrong_page_anchor needs a page anchor in the docstring")
    was = int(match.group("page"))
    delta = _PAGE_DELTAS[seed % len(_PAGE_DELTAS)]
    now = max(1, was + delta)
    if now == was:  # a zero move plants nothing; refuse rather than pretend
        raise CfError(f"page anchor {was} cannot move by {delta}",
                      hint="pick another seed")
    out = text[:match.start()] + match.group("lead") + str(now) + text[match.end():]
    return out, {"was_page": was, "now_page": now}


def _convention_rename(text: str, seed: int, rename_to: str = "", **_) -> tuple[str, dict]:
    """Rename the declaration and nothing else: a CONVENTION-only difference."""
    decl = parse_declaration(text)
    new = rename_to or _camel(decl.name)
    if new == decl.name:
        raise CfError(f"{decl.name} is already spelled that way",
                      hint="pass --to <name> for the spelling to rename to")
    out = _word(decl.name).sub(new, text)
    return out, {"decl": new, "was_decl": decl.name}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


#: class name -> mutator.  The key of an answer-key entry's ``class`` field.
MUTATIONS: dict[str, Callable[..., tuple[str, dict]]] = {
    "strengthened_hypothesis": _strengthened_hypothesis,
    "vacuity": _vacuity,
    "dropped_dimension": _dropped_dimension,
    "reversed_inequality": _reversed_inequality,
    "proxy_definition": _proxy_definition,
    "missing_item": _missing_item,
    "wrong_page_anchor": _wrong_page_anchor,
    "convention_rename": _convention_rename,
}

#: which finding kind a class should be reported as, when it is a defect at all.
#: ``convention_rename`` maps to None: it is a decoy, and a finding on it is a
#: false positive by construction.
KIND_BY_CLASS: dict[str, str | None] = {
    "strengthened_hypothesis": "CONTENT",
    "vacuity": "VACUITY",
    "dropped_dimension": "CONTENT",
    "reversed_inequality": "CONTENT",
    "proxy_definition": "PROXY",
    "missing_item": "COVERAGE",
    "wrong_page_anchor": "PAGE_ANCHOR",
    "convention_rename": None,
}


def mutate_text(text: str, mutation: str, *, seed: int = 0, **options) -> tuple[str, dict]:
    """Apply one mutation to one module's text; returns (text, record).

    The record is the answer key's evidence: the class, the seed and what
    changed, so a key entry can be checked against the fixture it describes.
    """
    if mutation not in MUTATIONS:
        raise CfError(f"unknown mutation class {mutation!r}",
                      hint="classes: " + ", ".join(sorted(MUTATIONS)))
    out, record = MUTATIONS[mutation](text, int(seed), **options)
    if out == text:
        raise CfError(f"{mutation} changed nothing in this module",
                      hint="a mutation that plants no defect would make the answer key false")
    return out, {"class": mutation, "seed": int(seed),
                 "kind": KIND_BY_CLASS[mutation], **record}


# --------------------------------------------------------------------------
# the file-level guard
# --------------------------------------------------------------------------
def check_writable(path: str | Path, *, fixture_root: str | Path | None = None,
                   i_know: bool = False) -> Path:
    """Refuse to plant a defect anywhere but a fixture tree or a temp directory.

    A planted defect in a real companion is indistinguishable from a real one,
    and companion repositories are read-only data (CLAUDE.md S4).  So the
    generator's blast radius is bounded by the filesystem, not by intent.
    """
    target = Path(path).resolve()
    if i_know:
        return target
    roots = [Path(tempfile.gettempdir()).resolve()]
    if fixture_root:
        roots.append(Path(fixture_root).resolve())
    for root in roots:
        if target == root or root in target.parents:
            return target
    raise CfError(
        f"refusing to mutate {target}: it is outside the fixture root and the temp directory",
        hint="planting a defect in a real companion makes it indistinguishable from a real one. "
             "Pass --fixture-root <dir> for a generated fixture tree, or --i-know to override.",
    )


def mutate_file(path: str | Path, mutation: str, *, seed: int = 0,
                fixture_root: str | Path | None = None, i_know: bool = False,
                **options) -> dict:
    """Mutate a file in place; returns the record, with ``file`` added."""
    target = check_writable(path, fixture_root=fixture_root, i_know=i_know)
    text = target.read_text(encoding="utf-8")
    out, record = mutate_text(text, mutation, seed=seed, **options)
    target.write_text(out, encoding="utf-8")
    return {"file": str(target), **record}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mutate.py",
        description="plant one known defect into a synthetic companion file")
    ap.add_argument("file", help="the .lean module to mutate, in a fixture tree")
    ap.add_argument("--class", dest="mutation", required=True, choices=sorted(MUTATIONS),
                    help="the defect class to plant")
    ap.add_argument("--seed", type=int, default=0, help="deterministic variant (default 0)")
    ap.add_argument("--object", default="", help="proxy_definition: the library constant")
    ap.add_argument("--to", default="", help="convention_rename: the new spelling")
    ap.add_argument("--fixture-root", default=os.environ.get("CF_FIXTURE_ROOT", ""),
                    help="the generated fixture tree this file must live under")
    ap.add_argument("--i-know", action="store_true",
                    help="mutate a file outside a fixture root anyway")
    args = ap.parse_args(argv)
    try:
        record = mutate_file(args.file, args.mutation, seed=args.seed,
                             fixture_root=args.fixture_root or None, i_know=args.i_know,
                             library_object=args.object, rename_to=args.to)
    except CfError as exc:
        sys.stderr.write(exc.render() + "\n")
        return exc.exit_code
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
