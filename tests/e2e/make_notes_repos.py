"""Two tiny synthetic Lean companions for the generated booklet.

They exist so the SECOND source is not a source with nothing formalised against
it: `cf pair` can only show that the per-source trees never mix if both sources
actually carry pairs.  Each is a real git repo with a real formalization.yaml
and real docstring tags, so `cf onboard` discovers them exactly the way it
discovers the two target companions -- no --adapter, no --book, no --offset.

One declaration per file, plus a few untagged helper modules, because
`detect_adapter` only scores a repo with at least 20 Lean files: a repo with
three of them is not evidence of a convention, and that floor is a deliberate
heuristic to satisfy rather than to lower.

usage: python3 make_notes_repos.py <parent dir> <booklet.pdf>
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = sys.argv[1]
PDF = sys.argv[2]

FORMALIZATION = """version: v0.3
project:
  name: %s
  authors:
  - Scratch Fixture
  license: CC0-1.0
sources:
- title: Elementary Notes on Sets
  authors:
  - Anonymous
  id: 'public-domain booklet (generated fixture)'
  type: booklet
  license: 'public domain; dedicated by the author'
  author_contacted: 'n/a'
"""

# The items both companions formalise, with the printed page each sits on.
# Kinds are limited to the three the booklet has enough of for `infer_kinds`
# to report them, so every tag lands on an item that exists in the source.
SHARED = [
    ("Definition 1.1.1", 1, "box", "def box (d : Nat) : Prop := d > 0"),
    ("Definition 1.1.2", 1, "elementary", "def elementary (n : Nat) : Prop := n > 0"),
    ("Lemma 1.1.3", 1, "box_elementary",
     "theorem box_elementary (d : Nat) (h : box d) : elementary d := h"),
    ("Lemma 1.1.4", 2, "boolean_closure",
     "theorem boolean_closure (m n : Nat) (h : elementary m) : elementary (m + n) :=\n"
     "  Nat.lt_of_lt_of_le h (Nat.le_add_right m n)"),
    ("Exercise 1.1.5", 2, "union_elementary",
     "theorem union_elementary (d : Nat) (h : box d) : elementary d := h"),
    ("Exercise 1.1.6", 2, "boolean_algebra",
     "theorem boolean_algebra (n : Nat) (h : elementary n) : elementary n := h"),
    ("Definition 1.2.1", 3, "measure", "def measure (n : Nat) : Nat := n"),
    ("Lemma 1.2.2", 3, "measure_well_defined",
     "theorem measure_well_defined (n : Nat) : measure n = n := rfl"),
    ("Exercise 1.2.6", 4, "measure_translation",
     "theorem measure_translation (n k : Nat) : measure (n + k) = measure n + k := rfl"),
    ("Exercise 1.2.7", 4, "measure_point",
     "theorem measure_point : measure 0 = 0 := rfl"),
    ("Definition 1.3.1", 5, "jordanInner", "def jordanInner (n : Nat) : Nat := n"),
    ("Definition 1.3.2", 5, "jordanOuter", "def jordanOuter (n : Nat) : Nat := n"),
    ("Definition 1.3.3", 6, "jordanMeasurable",
     "def jordanMeasurable (n : Nat) : Prop := jordanInner n = jordanOuter n"),
    ("Exercise 1.3.6", 6, "elementary_jordan",
     "theorem elementary_jordan (n : Nat) : jordanMeasurable n := rfl"),
    ("Definition 1.4.1", 8, "lebesgueOuter", "def lebesgueOuter (n : Nat) : Nat := n"),
    ("Lemma 1.4.2", 8, "lebesgue_subadditive",
     "theorem lebesgue_subadditive (m n : Nat) :\n"
     "    lebesgueOuter (m + n) <= lebesgueOuter m + lebesgueOuter n := Nat.le_refl _"),
    ("Exercise 1.4.4", 9, "countable_null",
     "theorem countable_null : lebesgueOuter 0 = 0 := rfl"),
]

# A only: one item B never touches.  B only: one item A never touches.  And the
# two disagree about Exercise 1.3.7 -- B drops the hypothesis that makes it
# non-trivial, which is the sort of thing a differential is meant to surface.
A_ONLY = [
    ("Exercise 1.3.7", 6, "not_jordan",
     "theorem not_jordan (n : Nat) (h : 0 < n) : jordanMeasurable n := rfl"),
    ("Definition 1.1.1", 1, "boxAlt", "def boxAlt (d : Nat) : Prop := box d"),
]
B_ONLY = [
    ("Exercise 1.3.7", 6, "notJordan", "theorem notJordan (n : Nat) : jordanMeasurable n := rfl"),
    ("Lemma 1.2.2", 3, "measureWellDefinedAlt",
     "theorem measureWellDefinedAlt (n : Nat) : measure n = n := rfl"),
]

HELPERS = ["Basic", "Order", "Tactic", "Util", "Prelude", "Notation"]


def module_name(decl: str) -> str:
    """A file name per declaration, so one file is one item."""
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", decl) if p)


def write(name, items):
    repo = os.path.join(ROOT, name)
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(os.path.join(repo, "Notes"), exist_ok=True)
    os.makedirs(os.path.join(repo, "sources"), exist_ok=True)
    # the booklet travels with the repo, exactly as tao-measure-lean ships its draft
    shutil.copyfile(PDF, os.path.join(repo, "sources", "elementary-notes.pdf"))
    with open(os.path.join(repo, "formalization.yaml"), "w") as fh:
        fh.write(FORMALIZATION % name)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("# %s\n\nA Lean companion to *Elementary Notes on Sets* "
                 "(`sources/elementary-notes.pdf`), a public-domain booklet.\n" % name)
    modules = []
    for tag, page, decl, statement in items:
        module = module_name(decl)
        modules.append(module)
        # the convention the docstring_tags adapter defaults to: a backtick-wrapped
        # book tag first, then the item's own printed page.
        body = ("/-- `%s` (fixture).  Book p. %d.\n\nFormalised from the booklet. -/\n%s\n"
                % (tag, page, statement))
        with open(os.path.join(repo, "Notes", module + ".lean"), "w") as fh:
            fh.write(body)
    # untagged helper modules: no docstring, so they are not evidence against the
    # convention, but they do make this a repo with enough Lean in it to classify
    for helper in HELPERS:
        with open(os.path.join(repo, "Notes", helper + ".lean"), "w") as fh:
            fh.write("namespace Notes.%s\n\nend Notes.%s\n" % (helper, helper))
    with open(os.path.join(repo, "Notes.lean"), "w") as fh:
        fh.write("\n".join("import Notes.%s" % m for m in modules + HELPERS) + "\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="Scratch", GIT_AUTHOR_EMAIL="s@example.invalid",
               GIT_COMMITTER_NAME="Scratch", GIT_COMMITTER_EMAIL="s@example.invalid")
    subprocess.run(["git", "init", "-q", repo], check=True, env=env)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True, env=env)
    n_lean = sum(1 for _, _, fs in os.walk(repo) for f in fs if f.endswith(".lean"))
    print("%s: %d items, %d .lean files" % (repo, len(items), n_lean))


write("notes-a", SHARED + A_ONLY)
write("notes-b", SHARED + B_ONLY)
