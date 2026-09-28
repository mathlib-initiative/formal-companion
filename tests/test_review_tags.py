"""Tag review: each known defect planted in a small document is reported, and the references say what they say.

New code, testing scripts/annotations/review_tags.py and `cf review-tags`
(docs/proposals/pdf-to-companion.md section 5.4, the "tag review reproduces
known answers" half of M3).  The planted documents are built from one small
valid base; the last class lists what the checker reports over the two
shipped reference documents today, so a change to either shows up here.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import gzip
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                               # noqa: E402
from annotations import review_tags                                # noqa: E402
from annotations import validate as V                              # noqa: E402

DATA = SKILL / "tests" / "data" / "m3"


def base_document() -> dict:
    """A small valid document: two numbered items, a passage, four declarations, two key links."""
    return {
        "schema": "lean-companion/annotations", "schema_version": "1.0",
        "companion": {"name": "demo", "repo": "git.example.org/demo/demo", "commit": "a" * 40,
                      "generated_at": "2026-09-01T00:00:00Z", "generator": "hand"},
        "sources": [{"id": "book", "kind": "pdf", "title": "A Demo Book", "sha256": "1" * 64,
                     "physical_page_count": 20, "printed_page_offset": 2}],
        "provenance": [{"id": "prov-tag", "actor": "author", "method": "docstring_tag", "confirmed_by_human": False},
                       {"id": "prov-table", "actor": "agent", "method": "curation", "confirmed_by_human": False}],
        "items": [
            {"id": "definition-1.1.1", "id_source": "label", "form": "numbered", "source": "book",
             "kind": "Definition", "number": "1.1.1", "pages": {"first": 5, "basis": "printed"}},
            {"id": "exercise-1.1.2", "id_source": "label", "form": "numbered", "source": "book",
             "kind": "Exercise", "number": "1.1.2", "pages": {"first": 6, "basis": "printed"}},
        ],
        "declarations": [
            {"id": "Demo.Box", "id_source": "name", "name": "Demo.Box", "kind": "structure",
             "module": "Demo.Basic", "file": "Demo/Basic.lean", "private": False, "anonymous": False},
            {"id": "Demo.Box.left", "id_source": "name", "name": "Demo.Box.left", "kind": "field",
             "parent": "Demo.Box", "namespace": "Demo.Box",
             "module": "Demo.Basic", "file": "Demo/Basic.lean", "private": False, "anonymous": False},
            {"id": "Demo.volume_add", "id_source": "name", "name": "Demo.volume_add", "kind": "theorem",
             "module": "Demo.Basic", "file": "Demo/Basic.lean", "private": False, "anonymous": False},
            {"id": "Demo.helper", "id_source": "name", "name": "Demo.helper", "kind": "theorem",
             "module": "Demo.Basic", "file": "Demo/Basic.lean", "private": False, "anonymous": False},
        ],
        "links": [
            {"id": "definition-1.1.1/key/Demo.Box", "item": "definition-1.1.1", "declaration": "Demo.Box",
             "role": "key", "provenance": "prov-tag", "raw_label": "Definition 1.1.1"},
            {"id": "exercise-1.1.2/key/Demo.volume_add", "item": "exercise-1.1.2", "declaration": "Demo.volume_add",
             "role": "key", "provenance": "prov-tag", "raw_label": "Exercise 1.1.2",
             "pages": {"first": 6, "basis": "printed", "anchor": "own", "raw": "book p. 6"}},
        ],
    }


def valid(case: unittest.TestCase, doc: dict) -> None:
    case.assertEqual(V.validate(V.canonicalize(doc)), [])


class TestEachPlantedDefectIsReported(unittest.TestCase):

    def test_the_base_document_is_clean(self):
        doc = base_document()
        valid(self, doc)
        findings = review_tags.review(doc)
        self.assertEqual(findings["counts"], {"attribution_gap": 0,
                                              "key_on_structure_member": {"links": 0, "memberships": 0},
                                              "borrowed_key": 0, "hidden_spelling": 0,
                                              "adjudicated_attribution_gap": 0})

    def test_an_item_keyed_only_to_its_page_is_an_attribution_gap(self):
        doc = base_document()
        doc["items"].append({"id": "remark-1.1.3", "id_source": "label", "form": "numbered", "source": "book",
                             "kind": "Remark", "number": "1.1.3", "pages": {"first": 6, "basis": "printed"}})
        valid(self, doc)
        found = review_tags.check_attribution_gap(doc)
        self.assertEqual([f["item"] for f in found], ["remark-1.1.3"])
        self.assertTrue(found[0]["anchored"])
        # a coverage class is the curation's answer, so it is not a gap
        doc["items"][-1]["coverage"] = "not_formalized"
        self.assertEqual(review_tags.check_attribution_gap(doc), [])
        # a passage with only a location is a gap too
        doc["items"].append({"id": "aside-p6", "id_source": "declared", "form": "passage",
                             "source": "book", "kind": "passage", "title": "An aside",
                             "pages": {"first": 6, "basis": "printed"}})
        self.assertEqual([f["item"] for f in review_tags.check_attribution_gap(doc)], ["aside-p6"])

    def test_a_key_on_a_structure_field_is_reported_as_link_and_as_membership(self):
        doc = base_document()
        doc["links"].append({"id": "definition-1.1.1/key/Demo.Box.left", "item": "definition-1.1.1",
                             "declaration": "Demo.Box.left", "role": "key", "provenance": "prov-table"})
        doc["clusters"] = [{"id": "src:1.1"}]
        doc["memberships"] = [
            {"id": "src:1.1/primary/Demo.Box.left", "cluster": "src:1.1", "declaration": "Demo.Box.left",
             "role": "primary", "provenance": "prov-table"},
            {"id": "src:1.1/primary/Demo.Box", "cluster": "src:1.1", "declaration": "Demo.Box",
             "role": "primary", "provenance": "prov-table"}]
        valid(self, doc)
        found = review_tags.check_key_on_structure_member(doc)
        self.assertEqual([f["link"] for f in found["links"]], ["definition-1.1.1/key/Demo.Box.left"])
        self.assertEqual(found["links"][0]["parent"], "Demo.Box")
        self.assertEqual([f["membership"] for f in found["memberships"]], ["src:1.1/primary/Demo.Box.left"])

    def test_an_item_whose_only_key_states_its_neighbour_is_a_borrowed_key(self):
        doc = base_document()
        # Remark 1.1.3's only key is Exercise 1.1.2's key declaration
        doc["items"].append({"id": "remark-1.1.3", "id_source": "label", "form": "numbered", "source": "book",
                             "kind": "Remark", "number": "1.1.3", "pages": {"first": 6, "basis": "printed"}})
        doc["links"].append({"id": "remark-1.1.3/key/Demo.volume_add", "item": "remark-1.1.3",
                             "declaration": "Demo.volume_add", "role": "key", "provenance": "prov-table"})
        valid(self, doc)
        found = review_tags.check_borrowed_key(doc)
        self.assertEqual(sorted(f["item"] for f in found), ["exercise-1.1.2", "remark-1.1.3"])
        self.assertEqual(next(f for f in found if f["item"] == "remark-1.1.3")["shared_with"], ["exercise-1.1.2"])
        # a second key of its own settles it for the exercise
        doc["links"].append({"id": "exercise-1.1.2/key/Demo.helper", "item": "exercise-1.1.2",
                             "declaration": "Demo.helper", "role": "key", "provenance": "prov-table"})
        self.assertEqual([f["item"] for f in review_tags.check_borrowed_key(doc)], ["remark-1.1.3"])

    def test_a_fallback_item_whose_cluster_shows_only_a_neighbours_keys_is_a_borrowed_key(self):
        doc = base_document()
        doc["items"].append({"id": "remark-1.1.3", "id_source": "label", "form": "numbered", "source": "book",
                             "kind": "Remark", "number": "1.1.3", "pages": {"first": 6, "basis": "printed"},
                             "clusters": ["src:1.1"], "cluster_fallback": True})
        doc["clusters"] = [{"id": "src:1.1"}]
        doc["memberships"] = [{"id": "src:1.1/primary/Demo.volume_add", "cluster": "src:1.1",
                               "declaration": "Demo.volume_add", "role": "primary", "provenance": "prov-table"}]
        valid(self, doc)
        found = review_tags.check_borrowed_key(doc)
        self.assertEqual([(f["item"], f["via"]) for f in found], [("remark-1.1.3", "clusters")])
        # a primary of the cluster that is nobody's key means the fallback shows something of its own
        doc["memberships"].append({"id": "src:1.1/primary/Demo.helper", "cluster": "src:1.1",
                                   "declaration": "Demo.helper", "role": "primary", "provenance": "prov-table"})
        self.assertEqual(review_tags.check_borrowed_key(doc), [])

    def test_a_spelling_the_fold_script_hides_is_reported(self):
        doc = base_document()
        doc["links"] += [
            {"id": "exercise-1.1.2/helper/Demo.helper/i", "item": "exercise-1.1.2", "declaration": "Demo.helper",
             "role": "helper", "sub": "i", "provenance": "prov-tag",
             "raw_label": "In-text proof of Exercise 1.1.2(i), book p. 6"},
            {"id": "definition-1.1.1/key/Demo.helper", "item": "definition-1.1.1", "declaration": "Demo.helper",
             "role": "key", "provenance": "prov-tag", "raw_label": "Strategy 1.1.1"},
        ]
        valid(self, doc)
        found = review_tags.check_hidden_spelling(doc)
        self.assertEqual({f["link"]: f["why"] for f in found}, {
            "exercise-1.1.2/helper/Demo.helper/i": "matches the hide pattern (proof plumbing)",
            "definition-1.1.1/key/Demo.helper": "carries no book anchor the keep pattern recognises"})
        # a page anchor beside the tag is an anchor the script keeps
        doc["links"][-1]["pages"] = {"first": 5, "basis": "printed", "anchor": "own", "raw": "book p. 5"}
        self.assertEqual([f["link"] for f in review_tags.check_hidden_spelling(doc)],
                         ["exercise-1.1.2/helper/Demo.helper/i"])
        # only docstring tags are spellings a fold script reads; a table's raw label is not
        doc["links"][-2]["provenance"] = "prov-table"
        self.assertEqual(review_tags.check_hidden_spelling(doc), [])
        # another site's rules
        self.assertEqual(review_tags.check_hidden_spelling(base_document(), keep_pattern=r"Definition"),
                         [{"link": "exercise-1.1.2/key/Demo.volume_add", "item": "exercise-1.1.2",
                           "declaration": "Demo.volume_add", "role": "key", "raw_label": "Exercise 1.1.2",
                           "why": "carries no book anchor the keep pattern recognises"}])

    def test_an_adjudicated_attribution_gap_verdict_is_listed(self):
        doc = base_document()
        doc["verdicts"] = [{"id": "verdict:exercise-1.1.2", "item": "exercise-1.1.2", "verdict": "attribution_gap",
                            "severity": "low", "status": "fixed", "summary": "Fixed in PR #3.",
                            "resolution": {"state": "fixed", "pr": 3}}]
        valid(self, doc)
        found = review_tags.check_adjudicated_attribution_gap(doc)
        self.assertEqual(found, [{"verdict": "verdict:exercise-1.1.2", "item": "exercise-1.1.2", "status": "fixed",
                                  "resolution": "fixed", "declarations": []}])

    def test_the_command_prints_a_summary_and_json(self):
        doc = base_document()
        doc["links"].append({"id": "exercise-1.1.2/helper/Demo.helper/i", "item": "exercise-1.1.2",
                             "declaration": "Demo.helper", "role": "helper", "sub": "i", "provenance": "prov-tag",
                             "raw_label": "In-text proof of Exercise 1.1.2(i), book p. 6"})
        import tempfile
        import os
        root = Path(tempfile.mkdtemp(prefix="cf-review-", dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(__import__("shutil").rmtree, root, ignore_errors=True)
        path = root / "doc.json.gz"
        with gzip.open(path, "wb") as fh:
            fh.write(V.canonical_dumps(V.canonicalize(doc)).encode("utf-8"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cf_main.main(["review-tags", str(path)]), 0)
        self.assertIn("hidden_spelling: 1", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cf_main.main(["review-tags", str(path), "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["counts"]["hidden_spelling"], 1)


class TestWhatTheReferencesReportToday(unittest.TestCase):
    """The known answers over the two shipped reference documents."""

    @classmethod
    def setUpClass(cls):
        cls.docs = {}
        for name in ("measure_theory", "tao-measure-lean"):
            with gzip.open(DATA / f"{name}.annotations.json.gz", "rb") as fh:
                cls.docs[name] = json.loads(fh.read().decode("utf-8"))

    def test_measure_theory(self):
        findings = review_tags.review(self.docs["measure_theory"])
        # 205 items with no entry and no class: the fallback items of sections 1.1 to 1.6 (section 1.7's 33
        # carry the class its curation named), every one on a cluster fallback
        gap = findings["attribution_gap"]
        self.assertEqual(len(gap), 205)
        self.assertTrue(all(g["cluster_fallback"] for g in gap))
        self.assertFalse(any(g["item"].split("-")[-1].startswith("1.7.") for g in gap))
        self.assertEqual(sum(1 for g in gap if g["form"] == "passage"), 13)
        # the 142 primary edges the first viewer PR counted, and 10 key links over 3 items
        member = findings["key_on_structure_member"]
        self.assertEqual(len(member["memberships"]), 142)
        self.assertEqual(len(member["links"]), 10)
        self.assertEqual(len({m["item"] for m in member["links"]}), 3)
        # Exercise 1.1.2's statement-level attribution borrows Lemma 1.1.2's key declaration
        borrowed = {b["item"]: b for b in findings["borrowed_key"]}
        self.assertEqual(borrowed["exercise-1.1.2"]["shared_with"], ["lemma-1.1.2"])
        self.assertEqual(borrowed["exercise-1.1.2"]["via"], "links")
        self.assertEqual(sum(1 for b in borrowed.values() if b["via"] == "links"), 19)
        self.assertEqual(sum(1 for b in borrowed.values() if b["via"] == "clusters"), 127)
        self.assertEqual(findings["hidden_spelling"], [])
        self.assertEqual([a["verdict"] for a in findings["adjudicated_attribution_gap"]],
                         ["verdict:exercise-1.2.21", "verdict:exercise-1.3.4"])

    def test_tao_measure_lean(self):
        findings = review_tags.review(self.docs["tao-measure-lean"])
        # the one numbered item a passage names as its neighbour and nothing keys
        self.assertEqual([g["item"] for g in findings["attribution_gap"]], ["remark-1.4.50"])
        self.assertEqual(findings["counts"]["key_on_structure_member"], {"links": 0, "memberships": 0})
        self.assertEqual(findings["borrowed_key"], [])
        # the five helper links spelled "In-text proof of ...", which the fold script hides as proof plumbing
        hidden = findings["hidden_spelling"]
        self.assertEqual(len(hidden), 5)
        self.assertTrue(all(h["role"] == "helper" and h["item"] == "lemma-1.1.2" for h in hidden))
        self.assertTrue(all(h["raw_label"].startswith("In-text proof of Lemma 1.1.2") for h in hidden))
        self.assertEqual(findings["adjudicated_attribution_gap"], [])


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
