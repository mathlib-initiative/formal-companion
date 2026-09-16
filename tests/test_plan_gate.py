"""Tests for the plan and gate stages (stdlib unittest).

Regression tests read the LOCAL baseline through environment variables and skip
cleanly when they are unset:

    CF_BASELINE_CORPUS=/Users/jack/Desktop/tao-diff-run      2,672 v1 findings
    CF_BASELINE_DIFF=/Users/jack/Desktop/tao-differential    queue/pairs/ledgers
    CF_BOOK_TXT=/Users/jack/Desktop/tao-differential/book.official.txt
    CF_TEND=/Users/jack/Desktop/tend                         for tend-task verify

    python3 -m unittest discover -s tests -v

The load-bearing claims they check:

* the v2 gate, in differential mode, passes every one of the 1,146 baseline
  differential findings after ``convert_v1``; in attack mode it passes all
  1,526 attack findings — including the witness gate, because the converter
  lifts v1's prose witness into the structured field;
* it FAILS a fabricated book quote and a missing witness;
* ``make_tasks`` over the baseline queue produces **383** differential tasks —
  the 382 the original run shipped plus ``Exercise 1.3.19``, which a
  single-space regex dropped — and **763** attack tasks for ``ours``;
* every generated task file parses as a tend ``Task``.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from cflib import items as cfitems  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from gate import convert_v1  # noqa: E402
from gate import verify_findings as vf  # noqa: E402
from plan import make_tasks as mt  # noqa: E402
from plan import presets as presets_mod  # noqa: E402
from plan import render_prompts as rp  # noqa: E402
from plan.render import TemplateError, render  # noqa: E402

BOOK_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "0" * 64


def _env_dir(name: str) -> pathlib.Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    p = pathlib.Path(value).expanduser()
    return p if p.exists() else None


CORPUS = _env_dir("CF_BASELINE_CORPUS")
BOOK_TXT = _env_dir("CF_BOOK_TXT")
TEND = _env_dir("CF_TEND")
TMPDIR = os.environ.get("CF_TMPDIR") or None

_BOOK_CACHE: dict[str, str] = {}


def normalised_book() -> str:
    if "book" not in _BOOK_CACHE:
        assert BOOK_TXT is not None
        _BOOK_CACHE["book"] = vf.norm(BOOK_TXT.read_text(errors="ignore"))
    return _BOOK_CACHE["book"]


def book_with_operators() -> vf.BookText:
    """The real book in BOTH normalisations, so the operator pass is live."""
    if "ops" not in _BOOK_CACHE:
        assert BOOK_TXT is not None
        raw = BOOK_TXT.read_text(errors="ignore")
        _BOOK_CACHE["ops"] = vf.BookText(vf.norm(raw), vf.norm_operators(raw))
    return _BOOK_CACHE["ops"]


def gate_cfg(preset_name: str, book: pathlib.Path, **over) -> vf.GateConfig:
    """A GateConfig for a preset, pointed at ``book``."""
    preset = presets_mod.get(preset_name)
    data = presets_mod.gate_config(
        preset,
        run_id=over.pop("run_id", "test-run"),
        book_text=str(book),
        book_sha=over.pop("book_sha", BOOK_SHA),
        repos=over.pop("repos", ["ours", "teorth", "adam"]),
    )
    data.update(over)
    return vf.GateConfig(data)


# --------------------------------------------------------------------------- #
# the tiny renderer
# --------------------------------------------------------------------------- #
class TestRenderer(unittest.TestCase):
    def test_substitutes_and_keeps_single_braces(self):
        out = render("a {{x}} b {y}", {"x": "1"})
        self.assertEqual(out, "a 1 b {y}")

    def test_if_block_kept_and_dropped(self):
        tpl = "head\n{{#if flag}}\nBODY\n{{/if}}\ntail\n"
        self.assertEqual(render(tpl, {"flag": True}), "head\nBODY\ntail\n")
        self.assertEqual(render(tpl, {"flag": False}), "head\ntail\n")

    def test_unless_block(self):
        tpl = "a\n{{#unless q}}\nB\n{{/unless}}\nc\n"
        self.assertEqual(render(tpl, {"q": False}), "a\nB\nc\n")
        self.assertEqual(render(tpl, {"q": True}), "a\nc\n")

    def test_missing_slot_raises(self):
        with self.assertRaises(TemplateError):
            render("{{nope}}", {}, name="t.md")


# --------------------------------------------------------------------------- #
# presets == the recorded diff of the five ffmain roots
# --------------------------------------------------------------------------- #
class TestPresets(unittest.TestCase):
    def test_five_original_presets_match_PRESETS_md(self):
        d = presets_mod.get("differential-text")
        self.assertEqual((d.max_workers, d.max_reviewers), (8, 5))
        self.assertFalse(d.witness_required)
        self.assertEqual(d.book_access, "text")
        self.assertNotIn("PAGE_ANCHOR", d.kinds)  # v2: mechanical only

        pdf = presets_mod.get("differential-pdf")
        self.assertTrue(pdf.page_images)
        self.assertEqual(pdf.book_access, "text+images")
        self.assertEqual((pdf.max_workers, pdf.max_reviewers), (6, 4))

        gpt = presets_mod.get("differential-gpt")
        self.assertEqual(gpt.bridge, "agentbox-codex")
        self.assertEqual(gpt.model, "gpt-5.6-sol")
        self.assertTrue(gpt.final_line)

        vac = presets_mod.get("attack-vac")
        self.assertEqual(sorted(vac.kinds), ["CONTENT", "SUSPECTED_ERRATA", "VACUITY"])
        self.assertTrue(vac.witness_required and vac.citation_ban)
        self.assertEqual(vac.bridge, "agentbox-codex")
        self.assertEqual((vac.max_workers, vac.max_reviewers), (5, 3))

        hunt = presets_mod.get("attack-hunt")
        self.assertEqual(hunt.bridge, "agentbox-claude")
        self.assertTrue(hunt.page_images and hunt.citation_ban)
        self.assertEqual(hunt.slug_style, "dash")

    def test_legacy_presets_are_not_runnable(self):
        self.assertNotIn("differential-legacy", presets_mod.RUNNABLE)
        self.assertNotIn("mechanical", presets_mod.RUNNABLE)
        self.assertIn("NO_WEAKNESS_FOUND", presets_mod.get("attack-legacy").verdicts)

    def test_priority_rule(self):
        self.assertEqual(presets_mod.priority_for("Definition", True, "1.2"), "max")
        self.assertEqual(presets_mod.priority_for("Definition", False, "1.2"), "high")
        self.assertEqual(presets_mod.priority_for("Exercise", True, "1.6"), "high")
        self.assertEqual(presets_mod.priority_for("Exercise", True, "1.2"), "default")
        self.assertEqual(presets_mod.priority_for("Exercise", False, "1.6"), "default")
        self.assertEqual(presets_mod.priority_for("In-text", True, None), "max")


# --------------------------------------------------------------------------- #
# the gate over the 2,672-file baseline
# --------------------------------------------------------------------------- #
@unittest.skipIf(CORPUS is None or BOOK_TXT is None, "CF_BASELINE_CORPUS/CF_BOOK_TXT unset")
class TestGateOnBaseline(unittest.TestCase):
    DIFFERENTIAL_DIRS = ("findings", "findings-pdf", "findings-gpt")
    ATTACK_DIRS = ("findings-vac", "findings-hunt")

    def _run(self, dirs, preset, mode, expect_files):
        book = normalised_book()
        cfg = gate_cfg(preset, BOOK_TXT)
        problems, n = [], 0
        for d in dirs:
            src = CORPUS / d
            for p in sorted(src.glob("*.json")):
                doc = convert_v1.convert_doc(
                    json.loads(p.read_text()),
                    run_id=f"baseline-{d}",
                    book_sha=BOOK_SHA,
                    mode=mode,
                    preset=preset,
                    repos=["ours", "teorth", "adam"],
                )
                problems.extend(vf.validate_doc(doc, f"{d}/{p.name}", cfg, book))
                n += 1
        self.assertEqual(n, expect_files, f"{dirs}: file count")
        self.assertEqual(problems[:20], [], f"{len(problems)} gate problem(s)")

    def test_differential_mode_passes_every_baseline_file(self):
        self._run(self.DIFFERENTIAL_DIRS, "differential-legacy", "differential", 1146)

    def test_attack_mode_passes_every_baseline_file(self):
        # attack-legacy requires a witness object on CONTENT/VACUITY; the only
        # reason this passes is that convert_v1 lifts the v1 prose witness.
        self.assertTrue(presets_mod.get("attack-legacy").witness_required)
        self._run(self.ATTACK_DIRS, "attack-legacy", "attack", 1526)

    def test_unconverted_v1_is_refused(self):
        cfg = gate_cfg("differential-legacy", BOOK_TXT)
        p = sorted((CORPUS / "findings").glob("*.json"))[0]
        errs = vf.validate_doc(json.loads(p.read_text()), p.name, cfg, normalised_book())
        joined = " ".join(errs)
        self.assertIn("schema_version", joined)
        self.assertIn("book_sha", joined)


# --------------------------------------------------------------------------- #
# the gate's negative cases (no baseline needed: a synthetic book)
# --------------------------------------------------------------------------- #
MINI_BOOK = (
    "Exercise 1.2.4 (Boolean closure). Let E and F be elementary sets. "
    "Show that the union of two elementary sets is elementary, and that the "
    "intersection is elementary as well.\n"
)


def mini_doc(**over) -> dict:
    doc = {
        "schema_version": 2,
        "run_id": "test-run",
        "book_sha": BOOK_SHA,
        "item": "Exercise 1.2.4",
        "book_located": True,
        "book_passage_quote": "Show that the union of two elementary sets is elementary",
        "matches": [
            {"concept": "boolean closure", "decls": [{"repo": "ours", "decl": "elem_union"}],
             "verdict": "SINGLE_SOURCE_DELTA", "note": "n"}
        ],
        "disagreements": [
            {
                "concept": "union",
                "kind": "CONTENT",
                "repos_involved": ["ours"],
                "faithful": [],
                "unfaithful_decls": ["ours:elem_union"],
                "book_quote": "the intersection is elementary as well",
                "witness": {"object": "E = [0,1), F = [1,2)", "why": "the union is not a box"},
                "reasoning": "r",
                "severity": "high",
                "suggested_fix": "f",
            }
        ],
        "coverage_notes": "",
    }
    doc.update(over)
    return doc


class TestGateNegative(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.book = pathlib.Path(self.tmp.name) / "book.txt"
        self.book.write_text(MINI_BOOK, encoding="utf-8")
        self.norm = vf.norm(MINI_BOOK)
        self.attack = gate_cfg("attack-vac", self.book, repos=["ours"])
        self.diff = gate_cfg("differential-text", self.book, repos=["ours", "teorth"])

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, doc, cfg=None):
        return vf.validate_doc(doc, "x.json", cfg or self.attack, self.norm)

    def test_clean_document_passes(self):
        self.assertEqual(self.errs(mini_doc()), [])

    def test_fabricated_passage_quote_fails(self):
        doc = mini_doc(book_passage_quote="Tao proves that every elementary set is a smooth manifold.")
        self.assertTrue(any("book_passage_quote" in e and "NOT FOUND" in e for e in self.errs(doc)))

    def test_fabricated_disagreement_quote_fails(self):
        doc = mini_doc()
        doc["disagreements"][0]["book_quote"] = (
            "the Lebesgue measure of a Vitali set is defined to be seventeen"
        )
        self.assertTrue(any("book_quote" in e and "NOT FOUND" in e for e in self.errs(doc)))

    def test_short_quote_fails(self):
        doc = mini_doc(book_passage_quote="Show that")
        self.assertTrue(any("too short" in e for e in self.errs(doc)))

    def test_missing_witness_fails_in_attack_mode_only(self):
        doc = mini_doc()
        doc["disagreements"][0].pop("witness")
        self.assertTrue(any("witness" in e for e in self.errs(doc)))
        self.assertEqual(self.errs(doc, self.diff), [])

    def test_empty_witness_field_fails(self):
        doc = mini_doc()
        doc["disagreements"][0]["witness"] = {"object": "", "why": "because"}
        self.assertTrue(any("witness" in e for e in self.errs(doc)))

    def test_wrong_edition_fails(self):
        self.assertTrue(any("wrong edition" in e for e in self.errs(mini_doc(book_sha=DRAFT_SHA))))

    def test_missing_required_disagreement_key_fails(self):
        doc = mini_doc()
        doc["disagreements"][0].pop("reasoning")
        self.assertTrue(any("missing required key 'reasoning'" in e for e in self.errs(doc)))

    def test_faithful_must_be_a_list(self):
        doc = mini_doc()
        doc["disagreements"][0]["faithful"] = "ours"
        self.assertTrue(any("faithful must be a list" in e for e in self.errs(doc)))

    def test_citation_kinds_banned_in_attack_mode(self):
        doc = mini_doc()
        doc["disagreements"][0]["kind"] = "PAGE_ANCHOR"
        self.assertTrue(any("not in" in e for e in self.errs(doc)))

    def test_page_anchor_is_mechanical_only_even_where_allowed(self):
        cfg = gate_cfg("mechanical", self.book, repos=["ours"])
        doc = mini_doc()
        d = doc["disagreements"][0]
        d["kind"] = "PAGE_ANCHOR"
        d["page"] = {"claimed": 165, "actual": 164}
        d["finding_id"] = vf.finding_id("test-run", doc["item"], "PAGE_ANCHOR", d["unfaithful_decls"])
        self.assertTrue(any("mechanical-only" in e for e in vf.validate_doc(doc, "x", cfg, self.norm)))
        d["source"] = "anchor_check"
        self.assertEqual(vf.validate_doc(doc, "x", cfg, self.norm), [])

    def test_proxy_needs_bridge_and_closure(self):
        cfg = gate_cfg("mechanical", self.book, repos=["ours"])
        doc = mini_doc()
        d = doc["disagreements"][0]
        d["kind"] = "PROXY"
        d["finding_id"] = vf.finding_id("test-run", doc["item"], "PROXY", d["unfaithful_decls"])
        errs = vf.validate_doc(doc, "x", cfg, self.norm)
        self.assertTrue(any("bridge" in e for e in errs))
        self.assertTrue(any("closure" in e for e in errs))

    def test_unknown_declaration_rejected_against_an_index(self):
        idx = pathlib.Path(self.tmp.name) / "decls.json"
        idx.write_text(json.dumps(["ours:something_else"]), encoding="utf-8")
        cfg = gate_cfg("attack-vac", self.book, repos=["ours"], decl_index=str(idx))
        errs = vf.validate_doc(mini_doc(), "x", cfg, self.norm, vf._load_decl_index(str(idx)))
        self.assertTrue(any("names no declaration" in e for e in errs))

    def test_gate_fails_closed_without_the_book(self):
        cfg = gate_cfg("attack-vac", pathlib.Path(self.tmp.name) / "nope.txt", repos=["ours"])
        _book, errs = vf.load_book(cfg)
        self.assertTrue(errs and "fails closed" in errs[0])

    def test_cli_reports_and_exits_nonzero(self):
        root = pathlib.Path(self.tmp.name) / "wt"
        (root / "findings").mkdir(parents=True)
        (root / "findings" / "a.json").write_text(json.dumps(mini_doc()), encoding="utf-8")
        cfgp = pathlib.Path(self.tmp.name) / "gate.json"
        cfgp.write_text(json.dumps(self.attack.data), encoding="utf-8")
        argv = ["--gate-config", str(cfgp), "--root", str(root), "--quiet"]
        self.assertEqual(vf.main(argv), 0)
        bad = mini_doc(book_passage_quote="a sentence that is certainly not in this little book")
        (root / "findings" / "a.json").write_text(json.dumps(bad), encoding="utf-8")
        self.assertEqual(vf.main(argv), 1)


# --------------------------------------------------------------------------- #
# the changed-files half of the gate
# --------------------------------------------------------------------------- #
class TestFindingIdIsOneDefinition(unittest.TestCase):
    """``finding_id`` is defined twice on purpose; the two MUST agree.

    ``cflib.hashing.finding_id`` is the canonical implementation and
    ``gate.verify_findings.finding_id`` inlines it (the gate is rsynced to a run
    host where ``cflib`` does not exist).  When they drift, the detector stamps
    ids in one namespace and the gate/ledger/route read another, and no ledger
    row can ever match its finding — which is exactly what happened.
    """

    CASES = [
        ("r1", "Definition 1.2.2", "PROXY", ["ours:Foo", "ours:Bar"]),
        ("run1", "Exercise 1.3.19", "CONTENT", ["ours:foo", "ours:bar"]),
        ("run1", "Exercise    1.3.19", "CONTENT", ["ours:bar", "ours:foo"]),
        ("dt-ff", "In-text p.42", "COVERAGE", []),
        ("", "", "", []),
        ("r", "`Lemma 1.2.5`", "VACUITY", ["adam:X", "adam:X", " adam:Y "]),
    ]

    def test_gate_and_cflib_agree_on_every_digest(self):
        from cflib.hashing import finding_id as cflib_fid

        for run_id, item, kind, decls in self.CASES:
            with self.subTest(item=item):
                self.assertEqual(
                    cflib_fid(run_id, item, kind, decls),
                    vf.finding_id(run_id, item, kind, list(decls)),
                )

    def test_every_call_site_agrees(self):
        """book/, ledger/ and detect/ must all resolve to the same digest."""
        from book import compat as book_compat
        from cflib.hashing import finding_id as cflib_fid
        from detect._compat import finding_id as detect_fid
        from ledger import compat as ledger_compat

        args = ("r1", "Definition 1.2.2", "PROXY", ["ours:Foo", "ours:Bar"])
        want = cflib_fid(*args)
        self.assertEqual(book_compat.finding_id(*args), want)
        self.assertEqual(ledger_compat.finding_id(*args), want)
        self.assertEqual(detect_fid(*args), want)
        self.assertEqual(vf.finding_id(*args), want)

    def test_whitespace_variant_hashes_to_the_canonical_item(self):
        """The regression the campaign exists for: ``Exercise    1.3.19``."""
        from cflib.hashing import finding_id as cflib_fid

        spaced = cflib_fid("r", "Exercise    1.3.19", "CONTENT", ["ours:a"])
        single = cflib_fid("r", "Exercise 1.3.19", "CONTENT", ["ours:a"])
        self.assertEqual(spaced, single)
        self.assertEqual(vf.finding_id("r", "Exercise\t1.3.19", "CONTENT", ["ours:a"]), single)

    def test_gate_clean_item_matches_cflib_items_clean(self):
        raws = [
            "Exercise    1.3.19", "  `Lemma 1.2.5`  ", "Definition\u00a01.1.1",
            "Corollary 1.3.14.", "Theorem 1.2.3 ;", "Exercise 1.1.6(i)", "",
            "Propo\u2013sition 1.1.1", "In-text p.  42",
        ]
        for raw in raws:
            with self.subTest(raw=raw):
                self.assertEqual(vf.clean_item(raw), cfitems.clean(raw))

    def test_detector_ids_pass_the_mechanical_gate(self):
        """A PROXY row the detector stamps must survive ``require_finding_id``."""
        from detect import findings as detect_findings

        row = detect_findings.proxy_row(
            run_id="detect-ours-cheap",
            companion="ours",
            item="Definition 1.2.2",
            decl="Foo",
            rule="mathlib_proxy",
            severity="medium",
            concept="uses mathlib's definition",
            reasoning="the companion's statement is a wrapper",
            bridge="fiat",
        )
        self.assertEqual(
            row["finding_id"],
            vf.finding_id("detect-ours-cheap", "Definition 1.2.2", "PROXY", ["ours:Foo"]),
        )


class TestQuoteGateOperators(unittest.TestCase):
    """The quote gate must not certify a doctored quote.

    ``norm()`` deletes every mathematical symbol before comparison, so the word
    pass alone accepts an inequality or operator flip — the exact defect class
    this audit exists to find.  ``norm_operators()`` is the second pass.
    """

    @classmethod
    def setUpClass(cls):
        if CORPUS is None or BOOK_TXT is None:
            raise unittest.SkipTest("needs CF_BASELINE_CORPUS and CF_BOOK_TXT")
        src = CORPUS / "findings-hunt" / "corollary-1-3-14.json"
        if not src.exists():
            raise unittest.SkipTest("baseline corollary-1-3-14 not present")
        cls.quote = json.loads(src.read_text())["book_passage_quote"]
        cls.book = book_with_operators()

    def test_the_real_quote_is_accepted(self):
        ok, why = vf.quote_ok(self.book, self.quote)
        self.assertTrue(ok, why)

    def test_operator_flips_are_refused(self):
        flipped = self.quote.replace("+", " - ").replace("=", "\u2260")
        self.assertNotEqual(flipped, self.quote)
        ok, why = vf.quote_ok(self.book, flipped)
        self.assertFalse(ok, f"a flipped +/= was certified: {why}")
        self.assertIn("OPERATOR", why)

    def test_a_wholly_fabricated_quote_is_still_refused(self):
        ok, _ = vf.quote_ok(
            self.book,
            "This corollary asserts that every bounded function on the unit "
            "interval admits a unique invariant measure of total mass one.",
        )
        self.assertFalse(ok)

    def test_a_partial_match_names_the_unverified_remainder(self):
        """A 70% genuine prefix with an invented tail passes, but SAYS SO."""
        words = self.quote.split()
        doctored = " ".join(words[: int(len(words) * 0.7)]) + (
            " and therefore the identity FAILS for every measurable function "
            "whatsoever in this setting"
        )
        ok, why = vf.quote_ok(vf.BookText(self.book.words), doctored)
        if ok:
            self.assertTrue(why.startswith("partial"), why)
            self.assertIn("UNVERIFIED", why)
            self.assertIn("fails", why.lower())

    def test_partial_quotes_reports_what_validate_doc_stayed_silent_about(self):
        doc = mini_doc(book_passage_quote="Show that the union of two elementary "
                                          "sets is elementary and pigs can fly here")
        book = vf.BookText(vf.norm(MINI_BOOK))
        self.assertEqual(vf.validate_doc(doc, "x.json", gate_cfg("attack-legacy",
                         BOOK_TXT, repos=["ours"]), book), [])
        notes = vf.partial_quotes(doc, "x.json", book)
        self.assertTrue(notes)
        self.assertIn("UNVERIFIED", notes[0])

    def test_legacy_presets_turn_the_operator_pass_off_and_say_so(self):
        self.assertFalse(presets_mod.get("attack-legacy").check_operators)
        self.assertFalse(presets_mod.get("differential-legacy").check_operators)
        for name in ("differential-text", "attack-vac", "attack-hunt", "mechanical"):
            self.assertTrue(presets_mod.get(name).check_operators, name)
        cfg = gate_cfg("attack-vac", BOOK_TXT)
        self.assertTrue(cfg.check_operators)
        book, errs = vf.load_book(cfg)
        self.assertEqual(errs, [])
        self.assertIsNotNone(book.ops)

    def test_norm_operators_keeps_the_operator_in_the_token_stream(self):
        self.assertIn("ople", vf.norm_operators("a \u2264 b"))
        self.assertIn("opne", vf.norm_operators("a != b"))
        self.assertEqual(vf.norm_operators("a <= b"), vf.norm_operators("a \u2264 b"))
        self.assertNotEqual(vf.norm_operators("a = b"), vf.norm_operators("a \u2260 b"))
        self.assertEqual(vf.norm("a \u2264 b"), vf.norm("a \u2265 b"))  # the blindness being fixed


class TestGateDirectoryMode(unittest.TestCase):
    """Directory mode must skip the receipt harvest/import write beside findings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        self.book = self.root / "book.txt"
        self.book.write_text(MINI_BOOK, encoding="utf-8")
        (self.root / "findings").mkdir()
        (self.root / "findings" / "exercise-1-2-4.json").write_text(
            json.dumps(mini_doc()), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _gate(self) -> int:
        data = presets_mod.gate_config(
            presets_mod.get("attack-legacy"), run_id="test-run",
            book_text=str(self.book), book_sha=BOOK_SHA, repos=["ours"],
        )
        cfgp = self.root / "gate.local.json"
        cfgp.write_text(json.dumps(data), encoding="utf-8")
        return vf.main(["--gate-config", str(cfgp), "--root", str(self.root), "--quiet"])

    def test_a_corpus_without_a_receipt_passes(self):
        self.assertEqual(self._gate(), 0)

    def test_the_receipt_harvest_writes_is_not_a_finding(self):
        """`cf harvest` writes RECEIPT.json into the findings dir; re-gating must pass."""
        receipt = {"run_id": "test-run", "complete": True, "expected": 1, "landed": 1}
        (self.root / "findings" / "RECEIPT.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        (self.root / "findings" / "run.yaml").write_text("run_id: test-run\n", encoding="utf-8")
        self.assertEqual(self._gate(), 0)

    def test_the_two_non_finding_sets_are_identical(self):
        from harvest._compat import NON_FINDING_FILES as harvest_set

        self.assertEqual(vf.NON_FINDING_FILES, set(harvest_set))


class TestChangedFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        self.env = env
        self.git("init", "-q", "-b", "main")
        (self.root / "tasks").mkdir()
        (self.root / "findings").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "tasks" / "a.yaml").write_text("id: a\n")
        (self.root / "scripts" / "gate.py").write_text("print('gate')\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, env=self.env,
                       capture_output=True, text=True)

    def paths(self):
        return vf.changed_paths(self.root, "main")[0]

    def test_findings_plus_task_is_allowed(self):
        self.git("checkout", "-q", "-b", "work")
        (self.root / "findings" / "a.json").write_text("{}\n")
        (self.root / "tasks" / "a.yaml").write_text("id: a\nstatus: complete\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "work")
        self.assertEqual(vf.check_changed(self.paths()), [])

    def test_editing_the_gate_is_refused(self):
        self.git("checkout", "-q", "-b", "sneaky")
        (self.root / "findings" / "a.json").write_text("{}\n")
        (self.root / "tasks" / "a.yaml").write_text("id: a\nstatus: complete\n")
        (self.root / "scripts" / "gate.py").write_text("import sys; sys.exit(0)\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "oops")
        problems = vf.check_changed(self.paths())
        self.assertTrue(any("scripts/gate.py" in p for p in problems))

    def test_uncommitted_files_are_seen(self):
        self.git("checkout", "-q", "-b", "dirty")
        (self.root / "findings" / "b.json").write_text("{}\n")  # untracked, no task
        problems = vf.check_changed(self.paths())
        self.assertTrue(any("has no tasks/b.yaml" in p for p in problems))

    def test_empty_change_set_is_refused(self):
        self.assertTrue(any("no findings file" in p for p in vf.check_changed(set())))

    def test_missing_base_fails_closed(self):
        _paths, errs = vf.changed_paths(self.root, "no-such-branch")
        self.assertTrue(errs and "fails closed" in errs[0])


# --------------------------------------------------------------------------- #
# convert_v1
# --------------------------------------------------------------------------- #
class TestConvertV1(unittest.TestCase):
    def test_faithful_string_to_list(self):
        f = convert_v1.split_faithful
        known = ["ours", "teorth", "adam"]
        self.assertEqual(f("ours", ["ours"], known), (["ours"], ""))
        self.assertEqual(f("ours | adam", ["ours", "adam"], known), (["ours", "adam"], ""))
        self.assertEqual(f("none", ["ours"], known), ([], "none"))
        self.assertEqual(f("unsure", ["ours"], known), ([], "unsure"))
        self.assertEqual(f("all_differ", ["ours"], known), ([], "all_differ"))
        self.assertEqual(f("all", ["ours", "teorth"], known), (["ours", "teorth"], "all"))

    def test_no_weakness_found_folds(self):
        doc = convert_v1.convert_doc(
            {"item": "x", "matches": [{"verdict": "NO_WEAKNESS_FOUND"}], "disagreements": []},
            run_id="r", book_sha=BOOK_SHA, mode="attack",
        )
        self.assertEqual(doc["matches"][0]["verdict"], "NO_DEFECT_FOUND")
        self.assertEqual(doc["matches"][0]["v1_verdict"], "NO_WEAKNESS_FOUND")

    def test_witness_is_lifted_and_marked(self):
        doc = convert_v1.convert_doc(
            {"item": "Exercise 1.1.1", "matches": [], "disagreements": [
                {"kind": "CONTENT", "faithful": "none", "unfaithful_decls": ["ours:a"],
                 "reasoning": "at `d = 0` the statement is vacuous", "severity": "low"}]},
            run_id="r", book_sha=BOOK_SHA, mode="attack",
        )
        w = doc["disagreements"][0]["witness"]
        self.assertEqual(w["object"], "d = 0")
        self.assertEqual(w["source"], "v1_reasoning_lift")

    def test_finding_id_is_stable_and_order_independent(self):
        a = vf.finding_id("run", "Exercise 1.1.1", "CONTENT", ["ours:b", "ours:a"])
        b = vf.finding_id("run", "Exercise 1.1.1", "CONTENT", ["ours:a", "ours:b"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, vf.finding_id("run2", "Exercise 1.1.1", "CONTENT", ["ours:a"]))

    def test_refuses_in_place_conversion(self):
        with tempfile.TemporaryDirectory(dir=TMPDIR) as d:
            with self.assertRaises(SystemExit):
                convert_v1.convert_tree(pathlib.Path(d), pathlib.Path(d),
                                        run_id="r", book_sha=BOOK_SHA)

    @unittest.skipIf(CORPUS is None, "CF_BASELINE_CORPUS unset")
    def test_baseline_is_not_modified(self):
        src = CORPUS / "findings"
        before = {p.name: p.stat().st_mtime_ns for p in src.glob("*.json")}
        with tempfile.TemporaryDirectory(dir=TMPDIR) as d:
            n = convert_v1.convert_tree(src, pathlib.Path(d) / "out",
                                        run_id="baseline-findings", book_sha=BOOK_SHA)
        self.assertEqual(n, 382)
        after = {p.name: p.stat().st_mtime_ns for p in src.glob("*.json")}
        self.assertEqual(before, after)


# --------------------------------------------------------------------------- #
# make_tasks over the baseline
# --------------------------------------------------------------------------- #
def book_headings(text: str, kinds=cfitems.DEFAULT_KINDS) -> set[str]:
    """Numbered headings, whitespace-tolerantly (``Exercise    1.3.19``)."""
    pat = re.compile(r"(?:^|\n)\s*(%s)\s+(\d+)\.(\d+)\.(\d+)" % "|".join(kinds))
    return {f"{m.group(1)} {m.group(2)}.{m.group(3)}.{m.group(4)}" for m in pat.finditer(text)}


def baseline_universe():
    """(queue rows, targets by companion) reproducing the original run's inputs."""
    heads = book_headings(BOOK_TXT.read_text(errors="ignore"))
    repos: dict[str, set[str]] = {}
    decls: dict[str, dict[str, list[str]]] = {}
    for repo in ("ours", "teorth", "adam"):
        for line in (CORPUS / "data" / f"{repo}.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            item, _ = cfitems.try_parse(row["item"])
            if item is None or not item.numbered or item.in_text:
                continue
            repos.setdefault(item.id, set()).add(repo)
            decls.setdefault(item.id, {}).setdefault(repo, []).append(row["decl"])
    targets: dict[str, list[dict]] = {r: [] for r in ("ours", "teorth", "adam")}
    queue: list[dict] = []
    for item, rs in sorted(repos.items(), key=lambda kv: cfitems.sort_key(kv[0])):
        in_book = item in heads
        if len(rs) >= 2:
            queue.append({"item": item, "repos": sorted(rs), "pdf_pages": [], "in_book": in_book})
        for repo in sorted(rs):
            targets[repo].append({
                "item": item,
                "decls": decls[item][repo],
                "peers": sorted(rs - {repo}),
                "single_source": len(rs) == 1,
                "in_book": in_book,
            })
    return queue, targets


@unittest.skipIf(CORPUS is None or BOOK_TXT is None, "CF_BASELINE_CORPUS/CF_BOOK_TXT unset")
class TestMakeTasksBaseline(unittest.TestCase):
    def test_differential_universe_is_383_not_382(self):
        queue, targets = baseline_universe()
        entries = mt.differential_entries(queue, targets, chapters=[1])
        self.assertEqual(len(entries), 383)
        ids = {e.item for e in entries}
        self.assertIn("Exercise 1.3.19", ids)          # the single-space-regex casualty
        self.assertNotIn("Definition 1.1.15", ids)     # a repo label the book does not have
        self.assertEqual(sum(1 for e in entries if not e.single_source), 225)
        self.assertEqual(sum(1 for e in entries if e.single_source), 158)

    def test_differential_tasks_render_and_reconcile(self):
        queue, targets = baseline_universe()
        entries = mt.differential_entries(queue, targets, chapters=[1])
        preset = presets_mod.get("differential-text")
        tasks = mt.make_tasks(entries, preset, {"run_id": "t", "book_sha": BOOK_SHA,
                                                "gate_command": "python3 gate.py"},
                              expected_items={e.item for e in entries})
        self.assertEqual(len(tasks), 383)
        by_id = {t.id: t for t in tasks}
        self.assertIn("exercise-1_3_19", by_id)          # the recovered task
        self.assertIn("corollary-1_3_14", by_id)         # the original run's spelling
        self.assertTrue(by_id["corollary-1_3_14"].text.startswith("schema_version: 1\n"))
        self.assertIn('title: "DIFFERENTIAL: Corollary 1.3.14"', by_id["corollary-1_3_14"].text)
        self.assertIn('title: "SINGLE-SOURCE: Corollary 1.5.10"', by_id["corollary-1_5_10"].text)
        self.assertEqual({t.priority for t in tasks}, {"default"})

    def test_original_382_task_ids_are_a_subset(self):
        queue, targets = baseline_universe()
        entries = mt.differential_entries(queue, targets, chapters=[1])
        tasks = mt.make_tasks(entries, presets_mod.get("differential-text"),
                              {"run_id": "t", "book_sha": BOOK_SHA, "gate_command": "g"})
        got = {t.id for t in tasks}
        original = {p.stem for p in (CORPUS / "tasks").glob("*.yaml")}
        self.assertEqual(len(original), 382)
        self.assertEqual(original - got, set())
        self.assertEqual(got - original, {"exercise-1_3_19"})

    def test_attack_tasks_are_763_for_ours(self):
        targets = json.loads((CORPUS / "targets.ours.json").read_text())
        self.assertEqual(len(targets), 763)
        entries = mt.target_entries(targets, "ours")
        preset = presets_mod.get("attack-hunt")
        tasks = mt.make_tasks(entries, preset, {
            "run_id": "t", "book_sha": BOOK_SHA, "gate_command": "g", "companion": "ours",
            "total_functions_sentence": "This repo: measures are total.",
        })
        self.assertEqual(len(tasks), 763)
        self.assertEqual(len({t.id for t in tasks}), 763)
        ids = {t.id for t in tasks}
        self.assertIn("corollary-1-3-14", ids)                      # dash slugs
        self.assertIn("corollary-lemma-1-2-5-exercise-1-2-4", ids)  # ungrammatical label kept
        self.assertTrue(any(i.startswith("in-text") for i in ids))
        self.assertEqual({t.priority for t in tasks}, {"default", "high", "max"})
        one = next(t for t in tasks if t.id == "corollary-1-3-14")
        self.assertIn('title: "hunt: Corollary 1.3.14"', one.text)
        self.assertIn("NO_DEFECT_FOUND", one.text)

    def test_recovered_priority_rule_reproduces_the_original_file(self):
        rows = json.loads((CORPUS / "targets.prioritised.json").read_text())
        wrong = []
        for row in rows:
            item, _ = cfitems.try_parse(row["item"])
            kind = item.kind if item is not None else row["item"].split()[0]
            got = presets_mod.priority_for(kind, row["single_source"], row.get("section"))
            if got != row["priority"]:
                wrong.append((row["item"], row["priority"], got))
        self.assertEqual(wrong[:5], [], f"{len(wrong)} of {len(rows)} priorities differ")

    def test_max_open_tasks_chains_dependencies(self):
        targets = json.loads((CORPUS / "targets.ours.json").read_text())[:10]
        tasks = mt.make_tasks(mt.target_entries(targets, "ours"),
                              presets_mod.get("attack-hunt"),
                              {"run_id": "t", "book_sha": BOOK_SHA, "gate_command": "g",
                               "companion": "ours", "total_functions_sentence": "x"},
                              max_open_tasks=4)
        self.assertEqual(tasks[0].depends_on, [])
        self.assertEqual(tasks[4].depends_on, [tasks[0].id])
        self.assertIn(f"depends_on: [{tasks[0].id}]", tasks[4].text)


class TestMakeTasksGuards(unittest.TestCase):
    def entries(self, items):
        return [mt.Entry(item=i, repos=["ours"], decls={"ours": ["d"]}) for i in items]

    def test_colliding_items_fall_back_to_the_raw_slug(self):
        e = self.entries(["In-text, book p. 18", "In-text convention, book p. 18"])
        slugs = mt.assign_slugs(e, "dash", cfitems.DEFAULT_KINDS)
        self.assertEqual(len(set(slugs.values())), 2)
        self.assertIn("in-text-convention-book-p-18", set(slugs.values()))

    def test_identical_items_are_refused_not_merged(self):
        e = self.entries(["Exercise 1.1.1", "Exercise 1.1.1"])
        with self.assertRaises(CfError) as cm:
            mt.assign_slugs(e, "dash", cfitems.DEFAULT_KINDS)
        self.assertIn("collision", str(cm.exception))

    def test_reconcile_names_the_missing_item(self):
        e = self.entries(["Exercise 1.1.1"])
        tasks = mt.make_tasks(e, presets_mod.get("differential-text"),
                              {"run_id": "t", "book_sha": BOOK_SHA, "gate_command": "g"})
        with self.assertRaises(CfError) as cm:
            mt.reconcile(e, tasks, expected_items={"Exercise 1.1.1", "Exercise 1.1.2"})
        self.assertIn("Exercise 1.1.2", str(cm.exception))

    def test_unknown_priority_is_refused(self):
        self.assertEqual(mt.tend_priority("medium"), "default")
        self.assertEqual(mt.tend_priority("max"), "max")
        with self.assertRaises(CfError):
            mt.tend_priority("urgent")

    def test_task_yaml_parses_as_a_tend_task(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("PyYAML not installed")
        e = [mt.Entry(item="Exercise 1.1.1", repos=["ours", "teorth"],
                      decls={"ours": ["a"], "teorth": ["b"]}, printed_page=12,
                      single_source=False, peers=["teorth"])]
        t = mt.make_tasks(e, presets_mod.get("differential-text"),
                          {"run_id": "t", "book_sha": BOOK_SHA, "gate_command": "g"})[0]
        doc = yaml.safe_load(t.text)
        self.assertEqual(doc["id"], "exercise-1_1_1")
        self.assertEqual(doc["status"], "open")
        self.assertEqual(doc["priority"], "default")
        self.assertEqual(doc["depends_on"], [])
        self.assertIn("ours: a", doc["description"])
        self.assertIn("Book page (printed): 12", doc["description"])
        self.assertTrue(doc["summary"] and doc["title"])


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
CONVENTIONS = """# House conventions -- ours

## 1. Total functions and junk values

- `measure` of a non-measurable set is `0` by junk value.
- (example) this placeholder must be dropped.

## 2. Standing type and structure conventions

- unsigned quantities live in `ℝ≥0∞`.

## 4. Sorry policy

- book exercises are permanent `sorry` stubs by design.
"""


class TestPrompts(unittest.TestCase):
    def ctx(self, preset_name, **over):
        preset = presets_mod.get(preset_name)
        kw = dict(
            run_id="r1",
            book_citation="A Book (GSM 126)",
            book_text="/home/x/book/book.txt",
            book_sha=BOOK_SHA,
            page_offset=16,
            companions=[{"name": "ours", "path": "/home/x/sources/ours", "files": "Src/*.lean"}],
            gate_command="python3 /home/x/gate/verify_findings.py",
        )
        kw.update(over)
        return preset, rp.build_context(preset, **kw)

    def test_conventions_parse_drops_placeholders(self):
        conv = rp.parse_conventions(CONVENTIONS)
        self.assertEqual(conv["total_functions"], ["`measure` of a non-measurable set is `0` by junk value."])
        self.assertEqual(len(conv["standing"]), 1)
        self.assertEqual(len(conv["sorry"]), 1)

    def test_companion_bullet_carries_the_conventions(self):
        conv = rp.parse_conventions(CONVENTIONS)
        line = rp.companion_bullet("ours", conv)
        self.assertTrue(line.startswith("- `ours`: "))
        self.assertIn("junk value", line)
        self.assertIn("permanent `sorry` stubs", line)

    def test_empty_conventions_say_so_rather_than_inventing(self):
        line = rp.companion_bullet("new", rp.parse_conventions("# nothing\n"))
        self.assertIn("no conventions recorded yet", line)

    def test_attack_prompts_carry_witness_and_citation_ban(self):
        preset, ctx = self.ctx("attack-hunt")
        out = rp.render_prompts(preset, ctx)
        self.assertIn("does NOT accept citation findings", out["worker-system.md"])
        self.assertIn('"witness"', out["worker-system.md"])
        self.assertIn("p-NNN.png", out["worker-system.md"])       # hunt sees page images
        self.assertNotIn("VERDICT: approve", out["reviewer.md"])  # claude bridge
        self.assertIn("/home/x/gate/verify_findings.py", out["worker.md"])

    def test_differential_text_prompt_has_no_attack_blocks(self):
        preset, ctx = self.ctx("differential-text")
        out = rp.render_prompts(preset, ctx)
        self.assertNotIn("citation findings", out["worker-system.md"])
        self.assertNotIn("p-NNN.png", out["worker-system.md"])
        self.assertIn("CONVENTION vs CONTENT", out["worker-system.md"])
        self.assertIn("faithful` is a **list", out["worker-system.md"])

    def test_codex_reviewer_gets_the_parsed_final_line(self):
        preset, ctx = self.ctx("differential-gpt")
        out = rp.render_prompts(preset, ctx)
        self.assertIn("VERDICT: approve", out["reviewer.md"])

    def test_prompts_never_leak_an_unfilled_slot(self):
        for name in ("differential-text", "differential-pdf", "differential-gpt",
                     "attack-vac", "attack-hunt"):
            preset, ctx = self.ctx(name)
            for fname, text in rp.render_prompts(preset, ctx).items():
                self.assertNotIn("{{", text, f"{name}/{fname} has an unfilled slot")


# --------------------------------------------------------------------------- #
# plan-run end to end, on a synthetic campaign
# --------------------------------------------------------------------------- #
class TestPlanRunEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMPDIR)
        self.root = pathlib.Path(self.tmp.name) / "campaign"
        (self.root / "book" / "mini").mkdir(parents=True)
        (self.root / "book" / "mini" / "book.txt").write_text(MINI_BOOK, encoding="utf-8")
        (self.root / "pairs").mkdir()
        # Real (tiny) Lean repos: plan-run builds the gate's declaration index
        # from the companion sources, and an index that does not cover every
        # repo of the run is deliberately not used at all.
        self.repos = pathlib.Path(self.tmp.name) / "repos"
        decls = {"ours": ["elem_union", "lemma5"], "peer": ["union_elem"]}
        for name, names in decls.items():
            src = self.repos / name / "Src"
            src.mkdir(parents=True)
            body = "".join(f"theorem {n} : True := trivial\n" for n in names)
            (src / "A.lean").write_text(body + "private theorem helper : True := trivial\n",
                                        encoding="utf-8")
        for name in ("ours", "peer"):
            d = self.root / "companions" / name
            d.mkdir(parents=True)
            (d / "adapter.yaml").write_text(f"name: {name}\n", encoding="utf-8")
            (d / "conventions.md").write_text(CONVENTIONS, encoding="utf-8")
            (d / "statements.jsonl").write_text(
                json.dumps({"item": "Exercise 1.2.4", "decl": "a", "repo": name}) + "\n",
                encoding="utf-8")
        (self.root / "campaign.yaml").write_text(
            "version: 1\n"
            "book:\n  id: mini\n  title: A Book\n  offset: 16\n"
            f"  sha256: {BOOK_SHA}\n  chapters: [1]\n"
            "companions:\n"
            f"  - name: ours\n    repo: {self.repos / 'ours'}\n    glob: Src/*.lean\n"
            f"  - name: peer\n    repo: {self.repos / 'peer'}\n    glob: Peer/*.lean\n"
            "hosts:\n  allowlist: [ffbox]\n  agentbox_profile: someone\n"
            "  ffbox:\n    ssh: someone@ffbox\n    remote_root: /home/someone\n"
            "models:\n  worker: claude-sonnet-5\n  reviewer: claude-opus-5\n"
            "budget:\n  billing_path: subscription\n  max_cost_usd: 50\n",
            encoding="utf-8")
        (self.root / "pairs" / "queue.json").write_text(json.dumps([
            {"item": "Exercise 1.2.4", "repos": ["ours", "peer"],
             "decls": {"ours": ["elem_union"], "peer": ["union_elem"]}, "book_page": 24},
        ]), encoding="utf-8")
        (self.root / "pairs" / "targets.ours.json").write_text(json.dumps([
            {"item": "Exercise 1.2.4", "decls": ["elem_union"], "peers": ["peer"],
             "single_source": False, "book_page": 24, "section": "1.2"},
            {"item": "Lemma 1.2.5", "decls": ["lemma5"], "peers": [], "single_source": True,
             "book_page": 25, "section": "1.2"},
        ]), encoding="utf-8")
        (self.root / "pairs" / "targets.peer.json").write_text(json.dumps([
            {"item": "Exercise 1.2.4", "decls": ["union_elem"], "peers": ["ours"],
             "single_source": False, "book_page": 24, "section": "1.2"},
        ]), encoding="utf-8")
        # `cf pair` writes this; plan-run REFUSES a differential run without it,
        # because an unreconciled plan is the 382-vs-383 state the gate exists for.
        (self.root / "pairs" / "reconciliation.json").write_text(json.dumps({
            "book_sha": BOOK_SHA, "task_universe": 2,
            "task_universe_items": ["Exercise 1.2.4", "Lemma 1.2.5"],
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **over):
        from cfcmd import plan_run

        class A:
            campaign = str(self.root)
            preset = "attack-hunt"
            host = "local"
            model = None
            companion = "ours"
            run_id = "r1"
            chapters = None
            max_open_tasks = None
            max_cost = None
            i_have_consent = False
            force = False
            allow_unreconciled = False

        for k, v in over.items():
            setattr(A, k, v)
        return plan_run.run(A())

    def read_run(self):
        import yaml

        return yaml.safe_load((self.root / "runs" / "r1" / "run.yaml").read_text())

    def test_attack_run_writes_everything(self):
        self.assertEqual(self.plan(), 0)
        rd = self.root / "runs" / "r1"
        for rel in ("run.yaml", "gate.json", "gate.local.json",
                    "tend-root/config.yaml", "tend-root/.tend/worker-agent.yaml",
                    "tend-root/bin/worker-agent.sh", "tend-root/gate/verify_findings.py",
                    "tend-root/gate/gate.json", "prompts/worker-system.md"):
            self.assertTrue((rd / rel).exists(), rel)
        self.assertEqual(sorted(p.name for p in (rd / "tasks").glob("*.yaml")),
                         ["exercise-1-2-4.yaml", "lemma-1-2-5.yaml"])
        gate = json.loads((rd / "gate.json").read_text())
        self.assertEqual(gate["mode"], "attack")
        self.assertTrue(gate["witness_required"])
        self.assertEqual(gate["book"]["sha256"], BOOK_SHA)
        self.assertNotIn("PAGE_ANCHOR", gate["kinds"])

    def test_run_yaml_records_prompt_shas_and_consent(self):
        self.plan()
        doc = self.read_run()
        self.assertEqual(doc["tasks"]["count"], 2)
        self.assertEqual(len(doc["prompts"]), 5)
        self.assertTrue(all(len(v) == 64 for v in doc["prompts"].values()))
        self.assertFalse(doc["consent"]["given"])
        self.assertTrue(doc["gate"]["out_of_tree"])
        self.assertEqual(doc["budget"]["max_cost"], 50)
        self.assertEqual(doc["disk"]["concurrent_worktrees"], 2)

    def test_idempotent(self):
        self.plan()
        first = (self.root / "runs" / "r1" / "tasks" / "exercise-1-2-4.yaml").read_text()
        shas = self.read_run()["prompts"]
        self.plan()
        self.assertEqual((self.root / "runs" / "r1" / "tasks" / "exercise-1-2-4.yaml").read_text(), first)
        self.assertEqual(self.read_run()["prompts"], shas)

    def test_remote_host_paths_and_consent_recorded(self):
        self.assertEqual(self.plan(host="ffbox", i_have_consent=True), 0)
        doc = self.read_run()
        self.assertTrue(doc["consent"]["given"])
        self.assertEqual(doc["host"]["remote_root"], "/home/someone")
        config = (self.root / "runs" / "r1" / "tend-root" / "config.yaml").read_text()
        self.assertIn("/home/someone/r1-gate/verify_findings.py", config)  # OUT of the worktree
        self.assertIn("--changed-files", config)
        self.assertNotIn("scripts/verify_findings.py", config)
        self.assertIn("entrypoint: /home/someone/r1", config)
        prompt = (self.root / "runs" / "r1" / "prompts" / "worker-system.md").read_text()
        self.assertIn("/home/someone/book/mini/book.txt", prompt)
        self.assertIn("/home/someone/sources/ours", prompt)

    def test_unknown_host_is_refused(self):
        with self.assertRaises(CfError) as cm:
            self.plan(host="someone-elses-box")
        self.assertIn("allowlist", str(cm.exception))

    def test_differential_preset_uses_the_merged_universe(self):
        self.assertEqual(self.plan(preset="differential-text", companion=None), 0)
        names = sorted(p.stem for p in (self.root / "runs" / "r1" / "tasks").glob("*.yaml"))
        self.assertEqual(names, ["exercise-1_2_4", "lemma-1_2_5"])
        doc = self.read_run()
        self.assertEqual(doc["tasks"]["differential"], 1)
        self.assertEqual(doc["tasks"]["single_source"], 1)
        self.assertEqual(doc["mode"], "differential")

    def test_a_differential_plan_without_a_reconciliation_is_refused(self):
        """It used to skip the check entirely, with no warning."""
        (self.root / "pairs" / "reconciliation.json").unlink()
        with self.assertRaises(CfError) as cm:
            self.plan(preset="differential-text", companion=None)
        self.assertIn("task universe is unknown", str(cm.exception))
        self.assertEqual(
            self.plan(preset="differential-text", companion=None,
                      allow_unreconciled=True), 0)

    def test_an_old_reconciliation_without_the_key_is_refused(self):
        (self.root / "pairs" / "reconciliation.json").write_text(
            json.dumps({"book_sha": BOOK_SHA, "pairs": 1}), encoding="utf-8")
        with self.assertRaises(CfError) as cm:
            self.plan(preset="differential-text", companion=None)
        self.assertIn("task_universe", str(cm.exception))

    def test_a_dropped_item_is_NAMED_not_counted(self):
        """The count-only check could say 382-vs-383 but never which item."""
        (self.root / "pairs" / "reconciliation.json").write_text(json.dumps({
            "book_sha": BOOK_SHA, "task_universe": 3,
            "task_universe_items": ["Exercise 1.2.4", "Lemma 1.2.5", "Exercise 1.3.19"],
        }), encoding="utf-8")
        with self.assertRaises(CfError) as cm:
            self.plan(preset="differential-text", companion=None)
        self.assertIn("Exercise 1.3.19", str(cm.exception))

    def test_an_attack_plan_reconciles_against_the_target_item_set(self):
        """`len(entries) == len(targets)` was tautological; the item SET is not."""
        targets = json.loads((self.root / "pairs" / "targets.ours.json").read_text())
        self.assertEqual(self.plan(preset="attack-hunt", companion="ours"), 0)
        names = sorted(p.stem for p in (self.root / "runs" / "r1" / "tasks").glob("*.yaml"))
        self.assertEqual(len(names), len(targets))

    def test_the_gate_gets_a_declaration_index(self):
        """It was plumbed end to end but never switched on: decl_index was always null."""
        self.plan()
        rd = self.root / "runs" / "r1"
        index = json.loads((rd / "decl-index.json").read_text())
        self.assertEqual(index["_meta"]["sources"]["ours"], "lean-source")
        self.assertIn("elem_union", index["ours"])
        self.assertIn("helper", index["ours"])          # untagged private helper
        gate = json.loads((rd / "gate.json").read_text())
        local = json.loads((rd / "gate.local.json").read_text())
        self.assertTrue(gate["decl_index"], "gate.json still carries a null decl_index")
        self.assertTrue((rd / "tend-root" / "gate" / "decl-index.json").exists(),
                        "the index must travel with the gate to the host")
        self.assertEqual(os.path.realpath(local["decl_index"]),
                         os.path.realpath(rd / "decl-index.json"))

    def test_a_partial_index_is_not_used_at_all(self):
        """An index missing one repo would reject every finding naming that repo."""
        import shutil as _shutil

        _shutil.rmtree(self.repos / "peer")
        self.plan(preset="differential-text", companion=None)
        rd = self.root / "runs" / "r1"
        self.assertIsNone(json.loads((rd / "gate.json").read_text())["decl_index"])
        self.assertFalse((rd / "tend-root" / "gate" / "decl-index.json").exists())

    def test_statements_jsonl_is_never_the_index(self):
        from plan import decl_index as di

        names, source = di.for_companion(
            type("P", (), {"detect_companion_dir": lambda _s, n: self.root / "nope",
                           "statements_jsonl": lambda _s, n: self.root / "companions" / n
                           / "statements.jsonl"})(),
            "ours", None)
        self.assertEqual(names, [])
        self.assertIn("no deps.json", source)

    def test_the_index_is_the_full_declaration_set_not_statements_jsonl(self):
        from plan import decl_index as di

        repo = pathlib.Path(self.tmp.name) / "lean-repo"
        (repo / "Src").mkdir(parents=True)
        (repo / ".lake" / "packages").mkdir(parents=True)
        (repo / "Src" / "A.lean").write_text(
            "namespace Demo\n"
            "private theorem helper_not_tagged : True := trivial\n"
            "/-- `Exercise 1.2.4` -/\ntheorem tagged : True := trivial\n"
            "end Demo\n", encoding="utf-8")
        (repo / ".lake" / "packages" / "Mathlib.lean").write_text(
            "theorem should_not_be_indexed : True := trivial\n", encoding="utf-8")
        names = di._from_lean(repo)
        self.assertIn("Demo.helper_not_tagged", names)   # untagged private helper
        self.assertIn("Demo.tagged", names)
        self.assertNotIn("should_not_be_indexed", names)  # .lake is excluded

    def test_a_phantom_declaration_is_rejected_and_a_real_one_is_not(self):
        idx = pathlib.Path(self.tmp.name) / "decls.json"
        idx.write_text(json.dumps({"ours": ["Real.name", "Private.helper"]}), encoding="utf-8")
        loaded = vf._load_decl_index(str(idx))
        self.assertEqual(loaded, {"ours:Real.name", "ours:Private.helper"})
        cfg = gate_cfg("attack-vac", BOOK_TXT or (self.root / "book" / "mini" / "book.txt"),
                       repos=["ours"], decl_index=str(idx))
        book = vf.BookText(vf.norm(MINI_BOOK))
        good = mini_doc()
        good["disagreements"][0]["unfaithful_decls"] = ["ours:Private.helper"]
        good["disagreements"][0]["witness"] = {"object": "o", "why": "w"}
        self.assertEqual(
            [e for e in vf.validate_doc(good, "x", cfg, book, loaded) if "index" in e], [])
        bad = mini_doc()
        bad["disagreements"][0]["unfaithful_decls"] = ["ours:Nope.at.all"]
        bad["disagreements"][0]["witness"] = {"object": "o", "why": "w"}
        self.assertTrue(any("names no declaration" in e
                            for e in vf.validate_doc(bad, "x", cfg, book, loaded)))

    def test_gate_only_preset_cannot_be_planned(self):
        with self.assertRaises(CfError):
            self.plan(preset="differential-legacy")

    def test_the_out_of_tree_gate_copy_runs_as_a_subprocess(self):
        """The copy in tend-root/gate/ is what the tend config invokes."""
        self.plan()
        rd = self.root / "runs" / "r1"
        gate_dir = rd / "tend-root" / "gate"
        wt = self.root / "wt"
        (wt / "findings").mkdir(parents=True)
        doc = mini_doc(run_id="r1")
        doc["disagreements"][0]["repos_involved"] = ["ours"]
        (wt / "findings" / "exercise-1-2-4.json").write_text(json.dumps(doc), encoding="utf-8")
        cmd = [sys.executable, str(gate_dir / "verify_findings.py"),
               "--gate-config", str(gate_dir / "gate.json"), "--root", str(wt)]
        ok = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        doc["disagreements"][0]["book_quote"] = "a sentence that is not in this little book at all"
        (wt / "findings" / "exercise-1-2-4.json").write_text(json.dumps(doc), encoding="utf-8")
        bad = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("NOT FOUND in the book text", bad.stderr)

    def test_generated_findings_pass_the_run_s_own_gate(self):
        self.plan()
        rd = self.root / "runs" / "r1"
        cfg = vf.GateConfig(json.loads((rd / "gate.local.json").read_text()))
        doc = mini_doc(run_id="r1")
        doc["disagreements"][0]["repos_involved"] = ["ours"]
        book, errs = vf.load_book(cfg)
        self.assertEqual(errs, [])
        self.assertEqual(vf.validate_doc(doc, "x", cfg, book), [])


@unittest.skipIf(TEND is None, "CF_TEND unset (no tend checkout to verify against)")
class TestTendTaskVerify(unittest.TestCase):
    """`uv run tend-task verify` on a generated task set — the real schema check."""

    def test_generated_tasks_pass_tend_task_verify(self):
        entries = [
            mt.Entry(item="Exercise 1.1.1", repos=["ours", "teorth"],
                     decls={"ours": ["a"], "teorth": ["b"]}, printed_page=12,
                     single_source=False, peers=["teorth"]),
            mt.Entry(item="Corollary 1.5.10", repos=["ours"], decls={"ours": ["c"]},
                     printed_page=124),
            mt.Entry(item="In-text, book p. 18", repos=["ours"], decls={"ours": ["d"]},
                     printed_page=18),
        ]
        with tempfile.TemporaryDirectory(dir=TMPDIR) as d:
            out = pathlib.Path(d) / "tasks"
            for preset_name in ("differential-text", "attack-hunt", "attack-vac"):
                preset = presets_mod.get(preset_name)
                tasks = mt.make_tasks(entries, preset, {
                    "run_id": "t", "book_sha": BOOK_SHA, "gate_command": "python3 g.py",
                    "companion": "ours", "total_functions_sentence": "This repo: totals.",
                }, max_open_tasks=2)
                if out.exists():
                    for p in out.glob("*.yaml"):
                        p.unlink()
                mt.write_tasks(tasks, out)
                proc = subprocess.run(
                    ["uv", "run", "tend-task", "verify", str(out)],
                    cwd=str(TEND), capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 0,
                                 f"{preset_name}: {proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
