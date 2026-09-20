"""Two tiny synthetic Lean companions for the generated booklet, with SEEDED defects.

They exist so the SECOND source is not a source with nothing formalised against
it: `cf pair` can only show that the per-source trees never mix if both sources
actually carry pairs.  Each is a real git repo with a real formalization.yaml
and real docstring tags, so `cf onboard` discovers them exactly the way it
discovers the two target companions -- no --adapter, no --book, no --offset.

One declaration per file, plus a few untagged helper modules, because
`detect_adapter` only scores a repo with at least 20 Lean files: a repo with
three of them is not evidence of a convention, and that floor is a deliberate
heuristic to satisfy rather than to lower.

The tables below are the FAITHFUL formalisation, and every page in them is the
page the booklet really prints the item on.  The defects are then planted on top
of it by `scripts/score/mutate.py`, one named class each (:data:`PLANTED`), and
`tests/e2e/answer-key.json` records exactly those eight -- which is what gives
`cf score` a recall denominator.  Nothing else in either repo is a defect: the
two decoys are CONVENTION-only differences that a worker is meant to leave
alone, and every other item is clean.

The repos are never built.  `lake` is never run against them and their proofs
are decorative; what the pipeline reads is the docstring tag, the page and the
statement.

usage: python3 make_notes_repos.py <parent dir> <booklet.pdf>
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))

from score.mutate import mutate_text  # noqa: E402

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

# The items both companions formalise, with the printed page each really sits
# on (sources/<id>/items.json is the arbiter, and pair/anchor_check.py compares
# against it: a page that is merely close plants a PAGE_ANCHOR finding nobody
# asked for, and the fixture then scores its own sloppiness).
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
    ("Exercise 1.2.7", 5, "measure_point",
     "theorem measure_point : measure 0 = 0 := rfl"),
    ("Definition 1.3.1", 6, "jordanInner", "def jordanInner (n : Nat) : Nat := n"),
    ("Definition 1.3.2", 6, "jordanOuter", "def jordanOuter (n : Nat) : Nat := n"),
    ("Definition 1.3.3", 7, "jordanMeasurable",
     "def jordanMeasurable (n : Nat) : Prop := jordanInner n = jordanOuter n"),
    ("Exercise 1.3.6", 8, "elementary_jordan",
     "theorem elementary_jordan (n : Nat) : jordanMeasurable n := rfl"),
    # The booklet asks for a set that is NOT Jordan measurable; this is the
    # faithful reading of it, and notes-b's planted `vacuity` drops the negation.
    ("Exercise 1.3.7", 8, "not_jordan",
     "theorem not_jordan (n : Nat) (h : jordanInner n < jordanOuter n) :\n"
     "    Not (jordanMeasurable n) := fun hEq => absurd hEq (Nat.ne_of_lt h)"),
    ("Definition 1.4.1", 9, "lebesgueOuter", "def lebesgueOuter (n : Nat) : Nat := n"),
    ("Lemma 1.4.2", 9, "lebesgue_subadditive",
     "theorem lebesgue_subadditive (m n : Nat) :\n"
     "    lebesgueOuter (m + n) <= lebesgueOuter m + lebesgueOuter n := Nat.le_refl _"),
    ("Exercise 1.4.4", 10, "countable_null",
     "theorem countable_null : lebesgueOuter 0 = 0 := rfl"),
]

# One extra declaration each, on an item the other repo also formalises: a
# second, equivalent spelling of the same definition.  These are DECOYS -- a
# difference of convention, not of content -- and the answer key lists them as
# loci that must not be reported.
A_ONLY = [
    ("Definition 1.1.1", 1, "boxAlt", "def boxAlt (d : Nat) : Prop := box d"),
]
B_ONLY = [
    ("Definition 1.1.2", 1, "elementaryAlt", "def elementaryAlt (d : Nat) : Prop := elementary d"),
]

#: The library the fixture's stage table calls foreign, used by the two planted
#: proxies.  tests/e2e/stages.anonymous-elementary-sets.json stages it.
LIBRARY_OBJECT = "Lib.Jordan.jordanContent"

#: repo -> declaration -> (mutation class, seed, options).  Each entry is ONE
#: planted defect and has one row in tests/e2e/answer-key.json; the decoys are
#: the `convention_rename` ones, which plant a difference and not a defect.
PLANTED = {
    "notes-a": {
        "union_elementary": ("strengthened_hypothesis", 0, {}),
        "jordanInner": ("proxy_definition", 0, {"library_object": LIBRARY_OBJECT}),
    },
    "notes-b": {
        "not_jordan": ("vacuity", 0, {}),
        "measure_translation": ("dropped_dimension", 0, {}),
        "lebesgue_subadditive": ("reversed_inequality", 0, {}),
        "jordanOuter": ("proxy_definition", 0, {"library_object": LIBRARY_OBJECT}),
        "lebesgueOuter": ("missing_item", 0, {}),
        "countable_null": ("wrong_page_anchor", 0, {}),
        # a decoy: the same lemma, spelled the other way
        "measure_well_defined": ("convention_rename", 0, {}),
    },
}

HELPERS = ["Basic", "Order", "Tactic", "Util", "Prelude", "Notation"]


def module_name(decl: str) -> str:
    """A file name per declaration, so one file is one item."""
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", decl) if p)


def items_for(name):
    """The clean (item, page, decl, statement) table for one repo."""
    return SHARED + (A_ONLY if name == "notes-a" else B_ONLY)


def module_text(tag, page, statement):
    """One module: the convention the docstring_tags adapter defaults to.

    A backtick-wrapped book tag first, then the item's own printed page.
    """
    return ("/-- `%s` (fixture).  Book p. %d.\n\nFormalised from the booklet. -/\n%s\n"
            % (tag, page, statement))


def modules_for(name):
    """[(module, text, planted record or None)] for one repo, mutations applied.

    Pure: no filesystem, no git.  The fixture's tests read the planted records
    from here and check them against tests/e2e/answer-key.json, so a mutation
    that stops firing fails a test instead of quietly emptying the key.
    """
    out = []
    for tag, page, decl, statement in items_for(name):
        text = module_text(tag, page, statement)
        record = None
        plan = PLANTED.get(name, {}).get(decl)
        if plan:
            mutation, seed, options = plan
            text, record = mutate_text(text, mutation, seed=seed, **options)
            record = {"repo": name, "item": tag, "clean_decl": decl, **record}
        out.append((module_name(decl), text, record))
    return out


def write(name, root, pdf):
    """Write one companion repo; returns the planted records."""
    repo = os.path.join(root, name)
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(os.path.join(repo, "Notes"), exist_ok=True)
    os.makedirs(os.path.join(repo, "sources"), exist_ok=True)
    # the booklet travels with the repo, exactly as tao-measure-lean ships its draft
    shutil.copyfile(pdf, os.path.join(repo, "sources", "elementary-notes.pdf"))
    with open(os.path.join(repo, "formalization.yaml"), "w") as fh:
        fh.write(FORMALIZATION % name)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("# %s\n\nA Lean companion to *Elementary Notes on Sets* "
                 "(`sources/elementary-notes.pdf`), a public-domain booklet.\n" % name)
    modules, planted = [], []
    for module, text, record in modules_for(name):
        modules.append(module)
        with open(os.path.join(repo, "Notes", module + ".lean"), "w") as fh:
            fh.write(text)
        if record:
            planted.append({"file": "Notes/%s.lean" % module, **record})
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
    print("%s: %d items, %d .lean files, %d planted defect(s)"
          % (repo, len(modules), n_lean, len(planted)))
    return planted


def build(root, pdf):
    """Write both companions; returns {repo: [planted records]}."""
    return {name: write(name, root, pdf) for name in ("notes-a", "notes-b")}


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
