"""The M1 calibration's derived truth table and the one-to-one passage recall.

``scripts/pipeline/derive_truth.py`` and ``scripts/pipeline/recall.py``
(docs/proposals/pdf-to-companion.md section 5.2; docs/pipeline.md,
"Calibration").  Everything here is a synthetic annotation document built in
the test: no companion's document, no PDF, no network.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib.errors import CfError                           # noqa: E402
from pipeline import derive_truth, recall                  # noqa: E402

SHA = "cd" * 32
OTHER = "ef" * 32


def synthetic_document() -> dict:
    """A companion document with everything the table must leave behind."""
    quote = {"type": "TextQuoteSelector", "exact": "A SECRET BOOK SENTENCE the table must drop"}
    return {
        "sources": [{"id": "book", "sha256": SHA, "printed_page_offset": 10},
                    {"id": "other", "sha256": OTHER}],
        "items": [
            {"id": "definition-1.1.1", "source": "book", "form": "numbered",
             "kind": "Definition", "number": "1.1.1", "title": "A SECRET TITLE",
             "pages": {"first": 1, "basis": "printed"}},
            {"id": "exercise-1.1.1", "source": "book", "form": "numbered",
             "kind": "Exercise", "number": "1.1.1"},
            {"id": "equation-1.2", "source": "book", "form": "numbered", "kind": "Equation",
             "number": "1.2", "pages": {"first": 2, "basis": "printed"}},
            {"id": "computed-1.1.9", "source": "book", "form": "numbered", "kind": "Computed",
             "kind_source": "producer", "number": "1.1.9",
             "pages": {"first": 3, "basis": "printed"}},
            {"id": "passage-p3-aaaaaaaa", "source": "book", "form": "passage",
             "kind": "passage", "title": "In-text, book p. 3",
             "quote": {"exact": "ANOTHER SECRET QUOTE"},
             "pages": {"first": 3, "basis": "printed"},
             "relations": {"near": ["definition-1.1.1"]}},
            {"id": "definition-9.9.9", "source": "other", "form": "numbered",
             "kind": "Definition", "number": "9.9.9", "pages": {"first": 5}},
        ],
        "locations": [{"id": "exercise-1.1.1@p014", "items": ["exercise-1.1.1"],
                       "pages": {"first": 14, "basis": "physical"},
                       "target": {"source": "book", "selector": [
                           {"type": "FragmentSelector", "value": "page=14",
                            "refinedBy": quote}]}}],
        "declarations": [{"name": "Secret.decl", "signature": "theorem secret : True"}],
        "snippets": [{"text": "theorem secret : True := trivial"}],
        "verdicts": [{"note": "a SECRET verdict"}],
    }


class TestDeriveTruth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-m1-truth-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_table_holds_only_kind_number_page_and_page_near(self):
        table, counts = derive_truth.derive(synthetic_document(), SHA, "synthetic")
        self.assertEqual(set(table), derive_truth.TOP_KEYS)
        self.assertEqual(table["numbered"], [
            {"kind": "Definition", "number": "1.1.1", "page": 11},
            {"kind": "Equation", "number": "1.2", "page": 12},
            {"kind": "Exercise", "number": "1.1.1", "page": 14}])
        self.assertEqual(table["passages"], [{"page": 13, "near": ["definition-1.1.1"]}])
        self.assertEqual(counts["left_out_producer_kind"], 1)
        self.assertEqual(derive_truth.check_table(table), [])
        text = json.dumps(table)
        for secret in ("SECRET", "Secret", "theorem", "title", "quote", "9.9.9"):
            self.assertNotIn(secret, text)

    def test_any_other_field_is_refused_and_nothing_is_written(self):
        table, _ = derive_truth.derive(synthetic_document(), SHA, "synthetic")
        for mutate in (lambda t: t["numbered"][0].__setitem__("title", "A title"),
                       lambda t: t["passages"][0].__setitem__("quote", "some words"),
                       lambda t: t.__setitem__("declarations", []),
                       lambda t: t["numbered"][0].__setitem__("kind", "Definition of a box"),
                       lambda t: t["passages"][0].__setitem__("near", ["a sentence here"])):
            bad = json.loads(json.dumps(table))
            mutate(bad)
            out = self.tmp / "bad.truth.json"
            with self.assertRaises(CfError):
                derive_truth.write_truth(out, bad)
            self.assertFalse(out.exists())

    def test_the_script_runs_by_path_on_a_gzipped_document(self):
        src = self.tmp / "doc.annotations.json.gz"
        with gzip.open(src, "wt", encoding="utf-8") as fh:
            json.dump(synthetic_document(), fh)
        out = self.tmp / "synthetic.truth.json"
        proc = subprocess.run([sys.executable, str(SCRIPTS / "pipeline" / "derive_truth.py"),
                               str(src), "--companion", "synthetic", "--sha256", SHA,
                               "--out", str(out)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("3 numbered items, 1 passages", proc.stdout)
        table = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(derive_truth.check_table(table), [])
        wrong = subprocess.run([sys.executable, str(SCRIPTS / "pipeline" / "derive_truth.py"),
                                str(src), "--companion", "synthetic", "--sha256", "00" * 32,
                                "--out", str(self.tmp / "x.json")],
                               capture_output=True, text=True)
        self.assertEqual(wrong.returncode, 1)
        self.assertFalse((self.tmp / "x.json").exists())

    def test_the_table_reads_back_as_the_document_does(self):
        doc = synthetic_document()
        table, _ = derive_truth.derive(doc, SHA, "synthetic")
        from_doc, excluded_doc = recall.numbered_truth(doc, SHA, [1])
        from_table, excluded_table, passages = recall.table_truth(table, [1])
        self.assertEqual({k: v["pages"] for k, v in from_doc.items()},
                         {k: v["pages"] for k, v in from_table.items()})
        self.assertEqual([e["label"] for e in excluded_table], ["Equation 1.2"])
        self.assertEqual({e["label"] for e in excluded_doc}, {"Equation 1.2", "Computed 1.1.9"})
        self.assertEqual([(p["page"], p["near"]) for p in passages],
                         [(13, ["definition-1.1.1"])])
        self.assertNotIn("quote", passages[0])


class TestPassageRecallIsOneToOne(unittest.TestCase):
    def ours(self, *passages) -> dict:
        items, locations = [], []
        for n, (page, exact, near) in enumerate(passages):
            item = {"id": f"ours-{n}", "form": "passage", "pages": {"first": page}}
            if near:
                item["relations"] = {"near": [near]}
            items.append(item)
            if exact:
                locations.append({"id": f"ours-{n}@p{page:03d}", "items": [item["id"]],
                                  "target": {"selector": [{"type": "FragmentSelector",
                                                           "refinedBy": {
                                                               "type": "TextQuoteSelector",
                                                               "exact": exact}}]}})
        return {"items": items, "locations": locations}

    def test_two_of_ours_recover_at_most_two_references_on_one_page(self):
        truth = [{"id": f"ref-{n}", "page": 45, "near": []} for n in range(19)]
        result = recall.passage_recall(self.ours((45, "", None), (45, "", None)), truth)
        self.assertEqual((result["found"], result["total"]), (2, 19))
        self.assertEqual(result["rule"], "page")
        self.assertIn("upper bound", result["note"])
        self.assertEqual(len(result["misses"]), 17)

    def test_a_quoted_reference_needs_an_overlapping_quote(self):
        truth = [{"id": "ref-a", "page": 10, "near": [],
                  "quote": "the outer measure of a countable set is zero"},
                 {"id": "ref-b", "page": 10, "near": [],
                  "quote": "every open set is a countable union of boxes"}]
        ours = self.ours((10, "Remark. The outer measure of a countable set is zero.", None),
                         (10, "Proof. Something else entirely, on the same page.", None))
        result = recall.passage_recall(ours, truth)
        self.assertEqual(result["rule"], "quote")
        self.assertEqual((result["found"], result["matched_by_quote"]), (1, 1))
        self.assertEqual(result["page_only"], 1)
        self.assertEqual(result["page_only_matches"][0]["reference"], "ref-b")
        self.assertEqual([m["id"] for m in result["misses"]], ["ref-b"])

    def test_beside_its_item_is_one_to_one_too(self):
        truth = [{"id": "ref-a", "page": 13, "near": ["lemma-1.1.1"]},
                 {"id": "ref-b", "page": 13, "near": ["lemma-1.1.1"]}]
        result = recall.passage_recall(self.ours((12, "", "lemma-1.1.1")), truth)
        self.assertEqual((result["found"], result["matched_beside_item"]), (1, 1))

    def test_quotes_overlap_on_a_run_of_words(self):
        self.assertTrue(recall.quotes_overlap("a b c d e", "z b c d e"))
        self.assertFalse(recall.quotes_overlap("a b c d e", "a b c x e"))
        self.assertTrue(recall.quotes_overlap("Proof.", "proof. the rest"))


if __name__ == "__main__":
    unittest.main()
