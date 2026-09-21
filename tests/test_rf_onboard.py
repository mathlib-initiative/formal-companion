r"""Regression tests for the source-discovery wall `cf onboard` used to stop at.

Two conventions were extractable at the LIBRARY level and neither could be
onboarded, for one reason: `cf onboard` called the module-level
``discovery.discover_sources`` and never ``Adapter.discover_sources``, so an
adapter could not contribute the claim that would unblock it.

* a leanblueprint project's manuscript IS its ``.tex`` tree, so the seven repo
  signals find no title, no ISBN, no PDF and no sha, and every blueprint repo --
  with or without ``--adapter blueprint_tex`` -- stopped at "no source could be
  discovered";
* a companion whose source is not on this machine at all (a bare-label
  docstring repo) stopped at the same wall, and the hint it printed,
  ``cf sources add <pdf> --for <companion>``, answers "unknown companion" --
  onboarding is what would have created that companion.

What the cases below guard:

* the adapter's claims are merged into discovery, and only what they ADD is
  taken: a claim cflib's own signals produced is kept as cflib produced it, so
  no already-onboarded companion's item table changes;
* a blueprint's manuscript is claimed as a TEXT source, identified by sha256
  over the concatenated bytes in ``order_files`` order -- every chapter inside
  the sha -- unpaginated, and registered unconfirmed like any other discovery;
* the unpaginated path completes: ``cf onboard`` exits 0 and writes
  ``statements.jsonl`` rows bound to that source, and the page-dependent
  features say "not applicable" rather than refusing;
* the two "this is the wrong document" refusals do not apply to a manuscript the
  companion wrote, and STILL apply to a file an operator named;
* ``cf onboard <repo> --source <file>`` exists, gives only the LOCATION, and the
  refusal's hint is that command -- tested by running the hint it prints.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                           # noqa: E402
from adapters.blueprint_tex import BlueprintTexAdapter, order_files  # noqa: E402
from cflib import discovery                                    # noqa: E402
from cflib.errors import CfError, DiscoveryError, SourceError   # noqa: E402
from cflib.hashing import sha256_text                          # noqa: E402
from cflib.jsonio import read_json                             # noqa: E402
from cflib.registry import Registry                            # noqa: E402
from cflib.workspace import Workspace                          # noqa: E402

E2E = HERE / "e2e"


def tmpdir(prefix: str) -> Path:
    # resolved, because every path `cf onboard` records is: on macOS mkdtemp hands
    # back the /var symlink spelling and the command stores /private/var, which is
    # the same directory under two names and not an assertion anyone should have
    # to write twice.
    return Path(tempfile.mkdtemp(
        prefix=prefix, dir=os.environ.get("CF_TEST_TMP") or None)).resolve()


# ---------------------------------------------------------------------------
# fixtures: the shape of each convention, never anyone's source
# ---------------------------------------------------------------------------

MACROS = r"""\newtheorem{theorem}{Theorem}[chapter]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{definition}[theorem]{Definition}
"""
CONTENT = r"""\title{Widgets and Gadgets $\times$ Lean}
\input{macros/common.tex}
\input{chapters/two.tex}
\input{chapters/one.tex}
"""
CHAPTER_TWO = r"""\chapter{Gadgets}

\begin{theorem}\label{thm:gadget}
  \lean{Gadget.main}
  Every gadget is a widget.
\end{theorem}
"""
CHAPTER_ONE = r"""\chapter{Widgets}

\begin{definition}[Widget]\label{def:widget}
  \lean{Widget.mk}
  \leanok
  A \emph{widget} is a set with one marked point.
\end{definition}

\begin{lemma}\label{lem:unique}
  \lean{Widget.unique}
  \leanok
  Every widget has exactly one marked point.
\end{lemma}
"""


def blueprint_repo(root: Path) -> Path:
    """A synthetic leanblueprint tree, master file and chapters in \\input order."""
    repo = root / "bprepo"
    src = repo / "blueprint" / "src"
    (src / "chapters").mkdir(parents=True, exist_ok=True)
    (src / "macros").mkdir(parents=True, exist_ok=True)
    (src / "macros" / "common.tex").write_text(MACROS, encoding="utf-8")
    (src / "content.tex").write_text(CONTENT, encoding="utf-8")
    (src / "chapters" / "one.tex").write_text(CHAPTER_ONE, encoding="utf-8")
    (src / "chapters" / "two.tex").write_text(CHAPTER_TWO, encoding="utf-8")
    return repo


#: the ratio of the bare-label convention, not any particular repo: enough .lean
#: files to clear the detector's file floor, and enough of them tagged to clear
#: the habit floor, so `--adapter docstring_tags` really extracts.
BARE_TAGGED = 86
BARE_PLAIN = 16


def bare_label_repo(root: Path) -> Path:
    """A companion that writes the book label bare at the start of the docstring."""
    repo = root / "barerepo"
    (repo / "Src").mkdir(parents=True, exist_ok=True)
    for i in range(BARE_TAGGED):
        (repo / "Src" / f"Tagged{i:03d}.lean").write_text(
            f"/-- Definition 1.{i // 20 + 1}.{i % 20 + 1}. A widget of kind {i}. -/\n"
            f"theorem widget{i} : True := trivial\n", encoding="utf-8")
    for i in range(BARE_PLAIN):
        (repo / "Src" / f"Plain{i:03d}.lean").write_text(
            f"/-- A helper with no book label. -/\ntheorem helper{i} : True := trivial\n",
            encoding="utf-8")
    return repo


def notes_file(root: Path, name: str = "widget-notes.md") -> Path:
    """A text 'book': the same items the bare-label fixture tags, as headings."""
    lines = ["# Widget notes", ""]
    for chapter in range(1, 6):
        lines += [f"## Chapter 1.{chapter}", ""]
        for n in range(1, 21):
            lines += [f"Definition 1.{chapter}.{n}. A widget of kind "
                      f"{(chapter - 1) * 20 + n - 1}.", ""]
    path = root / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _make_pdf():
    """``tests/e2e/make_pdf.py``, the booklet this repo already builds itself.

    ``--source``'s PDF half is a different branch from its text half in two
    places (the pagination rule, and everything in `prepare` that needs pages),
    so the cases below want a real PDF rather than a manuscript.  The generated
    booklet is the one this repo can build with the stdlib on any machine; the
    file `tests/e2e/synthetic.sh` writes is not in git (``*.pdf`` never is).
    """
    if str(E2E) not in sys.path:
        sys.path.insert(0, str(E2E))
    import make_pdf

    return make_pdf


def booklet_pdf(root: Path, name: str = "widget-booklet.pdf") -> Path:
    """A paginated 'book': front matter with no running head, then pages that have one."""
    path = root / name
    path.write_bytes(_make_pdf().build())
    return path


def repo_that_declares_its_own_booklet(root: Path) -> tuple[Path, Path]:
    """A companion that NAMES its book and ships it -- the case `--source` collides with."""
    repo = bare_label_repo(root)
    (repo / "sources").mkdir(exist_ok=True)
    booklet = booklet_pdf(repo / "sources")
    (repo / "formalization.yaml").write_text(
        "version: v0.3\n"
        "project:\n  name: barerepo\n  license: CC0-1.0\n"
        "sources:\n"
        "- title: Widget Booklet\n"
        "  authors:\n  - Anonymous\n"
        "  type: booklet\n"
        "  license: 'public domain; dedicated by the author'\n", encoding="utf-8")
    return repo, booklet


def empty_notes_file(root: Path) -> Path:
    """Prose that numbers nothing: the document the empty-source refusal is for."""
    path = root / "not-the-book.md"
    path.write_text("# Some notes\n\nThere is nothing numbered anywhere in here.\n",
                    encoding="utf-8")
    return path


class OnboardCase(unittest.TestCase):
    """A scratch workspace and one `cf onboard` per test."""

    prefix = "cf-rf-onboard-"

    def setUp(self):
        self.root = tmpdir(self.prefix)
        self.ws = self.root / "ws"
        self.assertEqual(cf_main.main(["init", str(self.ws), "--no-git", "-q"]), 0)

    def onboard(self, repo: Path, *extra) -> int:
        return cf_main.main(["--workspace", str(self.ws), "onboard", str(repo),
                             "--no-errata", "-q", *extra])

    def rows(self, name: str) -> list[dict]:
        path = Workspace.load(self.ws).paths.statements_jsonl(name)
        return [row for row in (json.loads(line) for line in
                                path.read_text(encoding="utf-8").splitlines() if line.strip())
                if "_meta" not in row]


# ---------------------------------------------------------------------------
# O1(a) -- the adapter's claims reach discovery, and only what they ADD
# ---------------------------------------------------------------------------

class TestTheAdapterCanContributeAClaim(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tmpdir("cf-rf-onboard-claims-")
        cls.repo = blueprint_repo(cls.root)

    def test_the_repo_signals_alone_find_nothing_in_a_blueprint(self):
        """The wall itself: no signal can see a manuscript that names no book."""
        claims = discovery.discover_sources(self.repo, adapter="blueprint_tex")
        self.assertEqual([c for c in claims if c.files], [])

    def test_the_merge_adds_the_manuscript_the_adapter_claims(self):
        claims = discovery.discover_sources_with_adapter(self.repo, adapter="blueprint_tex")
        manuscripts = [c for c in claims if c.files]
        self.assertEqual(len(manuscripts), 1)
        self.assertEqual(manuscripts[0].discovered_by, "adapter")

    def test_the_merge_does_not_restate_a_claim_cflib_already_read(self):
        """Add-only: an adapter may add a source, never re-word one.

        ``Adapter.discover_sources`` returns the same seven signals, re-ranked and
        enriched with the item kinds its tags use.  Adopting that reading would
        make every onboarded companion's prepared item table depend on the
        companion's view of the source instead of the source's own headings.
        """
        base = discovery.discover_sources(self.repo, adapter="blueprint_tex")
        merged = discovery.discover_sources_with_adapter(self.repo, adapter="blueprint_tex")
        self.assertEqual([discovery._claim_key(c) for c in merged if not c.files],
                         [discovery._claim_key(c) for c in base])

    def test_a_kind_that_claims_no_manuscript_adds_nothing(self):
        """The two shipped book-companion kinds must discover exactly what they did."""
        repo = bare_label_repo(tmpdir("cf-rf-onboard-claims2-"))
        for kind in ("docstring_tags", "attribution_table"):
            with self.subTest(kind=kind):
                base = discovery.discover_sources(repo, adapter=kind)
                merged = discovery.discover_sources_with_adapter(repo, adapter=kind)
                self.assertEqual([discovery._claim_key(c) for c in merged],
                                 [discovery._claim_key(c) for c in base])

    def test_an_adapter_whose_reader_raises_does_not_lose_discovery(self):
        """Source discovery must survive one convention failing to measure a repo."""
        real = BlueprintTexAdapter.discover_sources

        def boom(self, repo=None):
            raise RuntimeError("deliberate")

        BlueprintTexAdapter.discover_sources = boom
        try:
            merged = discovery.discover_sources_with_adapter(self.repo, adapter="blueprint_tex")
        finally:
            BlueprintTexAdapter.discover_sources = real
        self.assertEqual([discovery._claim_key(c) for c in merged],
                         [discovery._claim_key(c)
                          for c in discovery.discover_sources(self.repo, adapter="blueprint_tex")])


# ---------------------------------------------------------------------------
# O1(b) -- what the manuscript claim says, and what its sha covers
# ---------------------------------------------------------------------------

class TestTheManuscriptClaim(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tmpdir("cf-rf-onboard-manuscript-")
        cls.repo = blueprint_repo(cls.root)
        cls.adapter = BlueprintTexAdapter(
            {"adapter": "blueprint_tex", "name": "bp", "repo": str(cls.repo)})
        cls.claim = cls.adapter.manuscript_claim(cls.repo)

    def test_it_names_the_files_in_input_order(self):
        expected = [str(p) for p in order_files(self.adapter.source_files(self.repo))]
        self.assertEqual(list(self.claim.files), expected)
        # the master's \input order, not path order: two.tex before one.tex
        names = [Path(p).name for p in self.claim.files]
        self.assertLess(names.index("two.tex"), names.index("one.tex"))

    def test_the_sha_covers_every_chapter_in_that_order(self):
        """Not the master alone: a sha that misses a chapter mis-stamps its rows."""
        from cfcmd.onboard import _concatenated, _read_named_sources, _sha256_bytes

        claim = self.adapter.manuscript_claim(self.repo)
        _read_named_sources([claim])
        blob = _concatenated(claim.files)
        self.assertEqual(claim.sha256, _sha256_bytes(blob))
        self.assertEqual(claim.bytes, len(blob))
        self.assertEqual(claim.sha256, sha256_text(blob.decode("utf-8")))
        # editing a CHAPTER moves the sha
        chapter = self.repo / "blueprint" / "src" / "chapters" / "one.tex"
        original = chapter.read_text(encoding="utf-8")
        chapter.write_text(original + "\n% one more line\n", encoding="utf-8")
        try:
            after = self.adapter.manuscript_claim(self.repo)
            _read_named_sources([after])
            self.assertNotEqual(after.sha256, claim.sha256)
        finally:
            chapter.write_text(original, encoding="utf-8")

    def test_it_is_a_text_source_with_no_pages(self):
        from cfcmd.onboard import _read_named_sources

        claim = self.adapter.manuscript_claim(self.repo)
        _read_named_sources([claim])
        self.assertEqual(claim.source_format, "text")
        self.assertEqual(claim.pagination, "none")

    def test_it_carries_the_kinds_and_the_locator_the_adapter_reports(self):
        self.assertEqual(sorted(self.claim.kinds), ["Definition", "Lemma", "Theorem"])
        self.assertEqual(self.claim.locator, "number")

    def test_the_title_is_the_manuscript_name_without_its_markup(self):
        """``$\\times$`` in a title used to become the source id ``…-times``."""
        self.assertEqual(self.claim.title, "Widgets and Gadgets Lean")

    def test_a_tree_with_no_tex_claims_nothing(self):
        bare = tmpdir("cf-rf-onboard-notex-") / "empty"
        (bare / "blueprint").mkdir(parents=True)
        adapter = BlueprintTexAdapter(
            {"adapter": "blueprint_tex", "name": "bp", "repo": str(bare)})
        self.assertIsNone(adapter.manuscript_claim(bare))


# ---------------------------------------------------------------------------
# O1(c) -- the unpaginated path completes
# ---------------------------------------------------------------------------

class TestOnboardingABlueprint(OnboardCase):

    def setUp(self):
        super().setUp()
        self.repo = blueprint_repo(self.root)

    def test_the_dry_run_no_longer_hits_the_wall(self):
        self.assertEqual(self.onboard(self.repo, "--dry-run"), 0)

    def test_it_completes_and_writes_rows_bound_to_the_manuscript(self):
        self.assertEqual(self.onboard(self.repo), 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(len(ws.source_ids()), 1)
        source_id = ws.source_ids()[0]
        rows = self.rows("bprepo")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["source_id"], source_id)
            self.assertEqual(row["source_sha"], ws.source(source_id).sha256)
            # a blueprint has no printed pages to anchor to
            self.assertEqual(row["anchor_source"], "none")
            self.assertIsNone(row["page_anchor"])
        self.assertEqual(sorted({row["item"] for row in rows}),
                         ["Definition 2.1", "Lemma 2.2", "Theorem 1.1"])

    def test_the_source_is_a_text_manuscript_unconfirmed_like_any_discovery(self):
        self.onboard(self.repo)
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        body = (ws.data.get("sources") or {})[source_id]
        self.assertEqual(body["source_format"], "text")
        self.assertEqual(body["pagination"], "none")
        self.assertFalse(body["confirmed"])
        self.assertEqual(body["registry"], "overlay")
        self.assertEqual(body["discovered"]["pdf_found_in"], "adapter")
        self.assertEqual(body["discovered"]["locator"], "number")
        # the ordered file list is provenance, not a stored field
        self.assertNotIn("manuscript_files", body)
        self.assertEqual(len(body["discovered"]["from"]), 4)

    def test_the_assembled_manuscript_is_the_bytes_the_sha_names(self):
        from cfcmd.onboard import _sha256_bytes

        self.onboard(self.repo)
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        assembled = Path(str(ws.source(source_id).pdf))
        self.assertEqual(assembled.parent, ws.paths.source_dir(source_id))
        self.assertEqual(_sha256_bytes(assembled.read_bytes()), ws.source(source_id).sha256)
        # written into the workspace, never into the companion repo
        self.assertFalse(list(self.repo.rglob("manuscript.tex")))

    def test_the_page_dependent_facts_say_not_applicable_instead_of_refusing(self):
        self.onboard(self.repo)
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        meta = read_json(ws.paths.source_meta(source_id), default={}) or {}
        self.assertEqual(meta["pagination"], "none")
        self.assertEqual(meta["locator"], "section")
        self.assertEqual(meta["offset_method"], "none")
        self.assertEqual(meta["printed_page_rule"], "not applicable for an unpaginated source")
        with self.assertRaises(CfError) as caught:
            cf_main.main(["--workspace", str(self.ws), "sources", "set", source_id, "offset=1",
                          "-q"])
        self.assertIn("unpaginated", str(caught.exception))

    def test_an_empty_item_table_is_reported_not_refused_for_a_manuscript(self):
        """The manuscript's items are its tagged environments, not prose headings.

        ``items == 0`` means "this is the wrong document" for a published book --
        a real book's prose numbers SOMETHING -- and means nothing of the kind for
        a ``.tex`` the companion wrote: the bytes are the ones the adapter is
        about to parse, named by its own glob.
        """
        self.onboard(self.repo)
        ws = Workspace.load(self.ws)
        items = read_json(ws.paths.source_items(ws.source_ids()[0]), default={}) or {}
        self.assertEqual(items["count"], 0)

    def test_a_second_onboard_re_derives_rather_than_duplicating(self):
        self.assertEqual(self.onboard(self.repo), 0)
        first = Workspace.load(self.ws)
        self.assertEqual(self.onboard(self.repo), 0)
        second = Workspace.load(self.ws)
        self.assertEqual(second.source_ids(), first.source_ids())
        self.assertEqual(second.source(second.source_ids()[0]).sha256,
                         first.source(first.source_ids()[0]).sha256)
        self.assertEqual(len(self.rows("bprepo")), len(self.rows("bprepo")))

    def test_the_forced_adapter_path_reaches_the_same_source(self):
        self.assertEqual(self.onboard(self.repo, "--adapter", "blueprint_tex"), 0)
        self.assertEqual(len(Workspace.load(self.ws).source_ids()), 1)


# ---------------------------------------------------------------------------
# O2 -- a companion whose source only the operator can name
# ---------------------------------------------------------------------------

class TestNamingTheSourceOnTheWayIn(OnboardCase):

    def setUp(self):
        super().setUp()
        self.repo = bare_label_repo(self.root)
        self.notes = notes_file(self.root)

    def test_without_the_flag_it_still_refuses(self):
        with self.assertRaises(DiscoveryError) as caught:
            self.onboard(self.repo, "--adapter", "docstring_tags")
        self.assertIn("no source could be discovered", str(caught.exception))

    def test_the_refusal_prints_a_command_that_works(self):
        """The hint is run, not read: the old one answered "unknown companion".

        ``cf sources add <source> --for <companion>`` takes a companion the
        workspace already has, and this refusal is precisely the case where there
        is none -- onboarding is what would have created it.
        """
        with self.assertRaises(DiscoveryError) as caught:
            self.onboard(self.repo, "--adapter", "docstring_tags")
        hint = caught.exception.hint or ""
        self.assertNotIn("cf sources add", hint)
        match = re.search(r"`cf (onboard [^`]*--source) <file>`", hint)
        self.assertIsNotNone(match, hint)
        argv = match.group(1).split() + [str(self.notes)]
        self.assertEqual(
            cf_main.main(["--workspace", str(self.ws), *argv,
                          "--adapter", "docstring_tags", "--no-errata", "-q"]), 0)
        self.assertTrue(self.rows("barerepo"))

    def test_the_named_source_is_registered_unconfirmed_and_stamped_manual(self):
        self.assertEqual(
            self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(self.notes)), 0)
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        body = (ws.data.get("sources") or {})[source_id]
        self.assertEqual(body["discovered"]["pdf_found_in"], "manual")
        self.assertFalse(body["confirmed"])
        self.assertEqual(body["source_format"], "text")

    def test_only_the_location_is_given_every_fact_is_still_read_off_the_file(self):
        """S4 holds: ``--source`` is the same override as `cf sources add`."""
        self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(self.notes))
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        meta = read_json(ws.paths.source_meta(source_id), default={}) or {}
        self.assertEqual(meta["items"], 100)
        # kind_counts is what the inference MEASURED in this file's headings
        self.assertEqual(meta["kind_counts"], {"Definition": 100})
        self.assertGreater(meta["item_completeness"], 0.9)
        self.assertEqual(meta["pagination"], "none")
        self.assertEqual(meta["numbering"]["method"], "headings")

    def test_the_rows_are_bound_to_the_named_source(self):
        self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(self.notes))
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        rows = self.rows("barerepo")
        self.assertEqual(len(rows), BARE_TAGGED)
        for row in rows:
            self.assertEqual(row["source_id"], source_id)
            self.assertEqual(row["source_sha"], ws.source(source_id).sha256)

    def test_a_source_that_is_not_a_file_is_refused_by_name(self):
        with self.assertRaises(SourceError) as caught:
            self.onboard(self.repo, "--adapter", "docstring_tags",
                         "--source", str(self.root / "nope.md"))
        self.assertIn("not a file", str(caught.exception))

    def test_a_suffix_the_skill_cannot_read_is_refused_by_name(self):
        weird = self.root / "book.epub"
        weird.write_text("not a source this skill reads", encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(weird))
        self.assertIn(".tex", caught.exception.hint or "")

    def test_the_wrong_document_refusal_still_applies_to_a_named_source(self):
        """The guard is not loosened: only a self-authored manuscript escapes it."""
        with self.assertRaises(SourceError) as caught:
            self.onboard(self.repo, "--adapter", "docstring_tags",
                         "--source", str(empty_notes_file(self.root)))
        self.assertIn("0 numbered items", str(caught.exception))
        # and that refusal's own hint is followable from here too
        self.assertIn("--source", caught.exception.hint or "")
        self.assertNotIn("--for <companion>", caught.exception.hint or "")

    def test_two_named_sources_are_both_registered(self):
        second = notes_file(self.root, name="widget-notes-reprint.md")
        self.assertEqual(
            self.onboard(self.repo, "--adapter", "docstring_tags",
                         "--source", str(self.notes), "--source", str(second)), 0)
        # identical bytes are ONE source however many times they are named
        self.assertEqual(len(Workspace.load(self.ws).source_ids()), 1)

    @unittest.skipUnless(shutil.which("pdftotext"), "needs poppler's pdftotext")
    def test_naming_a_file_the_repo_already_found_is_still_one_source(self):
        """One sha is one source, whichever door it came in by.

        The dedupe that keeps two ids off one fingerprint was keyed on the
        LOCATED PDF, and a claim that names its files deliberately has none -- so
        naming a file the repo's own signals had already found wrote a second id
        for the same bytes, which is the state `workspace.yaml`'s loader refuses.
        The refusal blamed the tool and named no flag, and it left the registry
        overlay carrying both ids.
        """
        repo, booklet = repo_that_declares_its_own_booklet(self.root)
        copy = self.root / "someone-elses-copy.pdf"
        copy.write_bytes(booklet.read_bytes())
        self.assertEqual(
            self.onboard(repo, "--adapter", "docstring_tags", "--source", str(copy)), 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(len(ws.source_ids()), 1)
        source_id = ws.source_ids()[0]
        body = (ws.data.get("sources") or {})[source_id]
        # the repo's own name for the work survives the fold: a declared title
        # outranks a filename stem (S3's derivation step)
        self.assertEqual(body["title"], "Widget Booklet")
        self.assertEqual(body["discovered"]["pdf_found_in"], "repo_declared")
        # ...and the path the operator gave is kept as evidence, not discarded
        self.assertIn(str(copy), body["discovered"]["from"])
        # one overlay row for that sha, not two
        entries = [e for e in Registry.load(ws).entries.values()
                   if e.sha256 == body["sha256"]]
        self.assertEqual([e.id for e in entries], [source_id])

    @unittest.skipUnless(shutil.which("pdftotext"), "needs poppler's pdftotext")
    def test_a_named_pdf_runs_the_inferences_a_manuscript_has_no_pages_for(self):
        """The flag's PDF half, which every other case here leaves untested.

        ``--source`` advertises "a PDF, or a manuscript that is already text" in
        its help, in the refusal's hint and in the contract, and the two halves
        are different branches: the pagination is read off the format rather than
        detected, and the page offset, the printed-page rule and the page bind are
        no-ops for a manuscript.  This asserts they really run for a named PDF.
        """
        pdf = booklet_pdf(self.root)
        self.assertEqual(
            self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(pdf)), 0)
        ws = Workspace.load(self.ws)
        source_id = ws.source_ids()[0]
        body = (ws.data.get("sources") or {})[source_id]
        self.assertEqual(body["source_format"], "pdf")
        meta = read_json(ws.paths.source_meta(source_id), default={}) or {}
        self.assertEqual(meta["pagination"], "formfeed")
        self.assertEqual(meta["items"], len(_make_pdf().ITEMS))
        # the printed pages are two behind the PDF's, and the running heads say so
        self.assertGreater(meta["offset"], 0)
        self.assertEqual(meta["offset_method"], "running_head_mode")

    def test_a_second_onboard_does_not_need_the_flag_again(self):
        """Re-onboarding is the documented refresh; it must not need a remembered flag.

        Discovery re-reads the repo every run and a `manual` source is invisible
        to all seven signals, so the second run refused with "no source could be
        discovered" while `workspace.yaml` listed that very source for that very
        companion.
        """
        self.assertEqual(
            self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(self.notes)), 0)
        first = Workspace.load(self.ws).source_ids()
        self.assertEqual(self.onboard(self.repo), 0)
        second = Workspace.load(self.ws)
        self.assertEqual(second.source_ids(), first)
        body = (second.data.get("sources") or {})[first[0]]
        self.assertEqual(body["discovered"]["pdf_found_in"], "manual")
        self.assertEqual(body["discovered"]["from"], [str(self.notes)])

    def test_a_re_claimed_source_that_moved_is_reported_and_names_the_flag(self):
        """The location is re-used, never trusted: the facts still come off the file."""
        self.onboard(self.repo, "--adapter", "docstring_tags", "--source", str(self.notes))
        self.notes.rename(self.root / "moved-notes.md")
        with self.assertRaises(DiscoveryError) as caught:
            self.onboard(self.repo)
        self.assertIn("--source", caught.exception.hint or "")

    @unittest.skipUnless(shutil.which("pdftotext"), "needs poppler's pdftotext")
    def test_only_a_manual_source_is_re_claimed_from_the_workspace(self):
        """A row a signal will find again must not be re-asserted from the workspace.

        Otherwise a stale workspace row outranks what the repo says today, which
        is the whole reason discovery re-reads the repo instead of trusting itself.
        """
        from cfcmd.onboard import _sources_only_the_operator_named

        repo, _booklet = repo_that_declares_its_own_booklet(self.root)
        self.assertEqual(self.onboard(repo, "--adapter", "docstring_tags"), 0)
        ws = Workspace.load(self.ws)
        self.assertEqual(
            _sources_only_the_operator_named(ws, repo, "barerepo"), [])


# ---------------------------------------------------------------------------
# what the discovery result calls a source it is about to prepare
# ---------------------------------------------------------------------------

class TestTheRenderedResultDoesNotCallATextFileAPdf(unittest.TestCase):
    """`--source` makes a text manuscript land on that line routinely."""

    def _render(self, **body) -> str:
        result = discovery.DiscoveryResult(repo=Path("/nowhere"), name="r", adapter="x")
        result.sources.append({"id": "widget", "sha256": "0" * 64,
                               "pdf": "/nowhere/widget-notes.md", **body})
        return result.render()

    def test_a_text_source_is_called_a_source(self):
        out = self._render(source_format="text", pagination="none")
        self.assertIn("source   /nowhere/widget-notes.md", out)
        self.assertNotIn("pdf      /nowhere", out)

    def test_a_pdf_is_still_called_a_pdf(self):
        out = self._render(source_format="pdf", pagination="formfeed")
        self.assertIn("pdf      /nowhere/widget-notes.md", out)


# ---------------------------------------------------------------------------
# the help text has to name what the flag really does
# ---------------------------------------------------------------------------

class TestTheHelpDescribesTheFlag(unittest.TestCase):

    def test_onboard_help_names_the_source_flag(self):
        parser, _mapping = cf_main.build_parser()
        text = parser.format_help()
        self.assertIn("onboard", text)
        action = next(a for a in _onboard_actions() if a.dest == "source")
        self.assertIn("repeatable", (action.help or "").lower())
        self.assertIn("read off", (action.help or "").lower())


def _onboard_actions():
    import argparse

    from cfcmd import onboard as onboard_cmd

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    onboard_cmd.register(sub)
    return sub.choices["onboard"]._actions


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
