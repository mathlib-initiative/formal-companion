"""Milestone M1 on the synthetic booklet: `cf ingest` (S0) and `cf inventory` (S1).

docs/proposals/pdf-to-companion.md sections 2.1, 2.2 and 5.2; docs/pipeline.md
says what the two stages do today.  The booklet is ``tests/e2e/make_pdf.py``'s,
built with its planted passages, in a temporary directory; errata come from a
stand-in for curl, so nothing here touches the network.  Needs poppler
(``pdftotext``, ``pdfinfo``) and git, and skips without them.

Covered: the pinned facts and the generated repository of doc v0, the numbered
items and passages of doc v1 with their exact page quotes and neighbours, the
erratum items and their ``same_as`` links, every gate's failure case (a missing
slot, a selector off its page, a draft edition, a duplicate id, a weak offset),
the receipts' shape, and idempotence; and the review probes of pull request
16 (``TestReviewProbes``): a failing re-run, a registered id on another sha, an
unknown sha, a scope that meets the next chapter, an ambiguous heading, a
supplied quote with an invented tail, a duplicate supplied passage and a page
whose running head dissents from the offset.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
E2E = HERE / "e2e"
for _p in (SCRIPTS, E2E):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cf as cf_main                                       # noqa: E402
import make_pdf                                            # noqa: E402  (tests/e2e)
from annotations import ids                                # noqa: E402
from cflib import inference                                # noqa: E402
from cflib.errors import CfError, SourceError              # noqa: E402
from cflib.hashing import sha256_file                      # noqa: E402
from cflib.jsonio import read_json, write_json             # noqa: E402
from cflib.registry import Registry                        # noqa: E402
from cflib.workspace import Workspace                      # noqa: E402
from pipeline import document, ingest, inventory, pipeline_yaml, source_gates  # noqa: E402
from pipeline import recall, stage_receipt                 # noqa: E402
from sources import fetch_errata                           # noqa: E402

TOOLS = all(shutil.which(t) for t in ("pdftotext", "pdfinfo", "git"))
SID = "elementary-notes"
PID = "notes-ch1"
DRAFT_SHA = "62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8"

ERRATA_HTML = """<html><body><h1>Errata</h1><ul>
<li>Page 2: In Lemma 1.1.4, the word "so" should read "then".</li>
<li>Page 5: In Theorem 1.2.3 and Corollary 1.2.4, write the elementary measure out.</li>
<li>p. 3: a comma is missing in the second paragraph.</li>
<li>Page 40: In Theorem 2.1.1, the bound is off by a constant.</li>
<li>Home</li>
</ul></body></html>"""


def scratch(case) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-m1-", dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def fake_curl(url, timeout=60):
    return ERRATA_HTML.encode("utf-8"), 200, url


@unittest.skipUnless(TOOLS, "needs pdftotext, pdfinfo and git")
class M1Case(unittest.TestCase):
    """A fresh workspace and the planted booklet, outside the workspace."""

    def setUp(self):
        self.root = scratch(self)
        self.ws_dir = self.root / "ws"
        self.assertEqual(cf_main.main(["init", str(self.ws_dir), "--no-git", "-q"]), 0)
        self.pdf = self.root / "booklet.pdf"
        self.pdf.write_bytes(make_pdf.build(passages=True))

    @property
    def ws(self) -> Workspace:
        return Workspace.load(self.ws_dir)

    def ingest(self, **kw):
        kw.setdefault("scope_spec", "chapters=1")
        kw.setdefault("pipeline_id", PID)
        kw.setdefault("source_id", SID)
        kw.setdefault("no_errata", True)
        return ingest.ingest(self.ws, pdf=kw.pop("pdf", self.pdf), **kw)

    def inventory(self, **kw):
        return inventory.run_stage(self.ws.paths, PID, **kw)

    def pages(self) -> list[str]:
        return inventory.load_page_texts(self.ws.paths, SID)

    def doc(self, version: int) -> dict:
        return json.loads(self.ws.paths.pipeline_doc(PID, version).read_text(encoding="utf-8"))

    def validate_cli(self, path: Path) -> str:
        proc = subprocess.run([sys.executable, str(SCRIPTS / "annotations" / "validate.py"),
                               "--canonical", str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("warning", proc.stdout + proc.stderr)
        return proc.stdout


class TestIngest(M1Case):
    def test_doc_v0_pins_the_pdf_and_starts_the_companion(self):
        result = self.ingest()
        doc = self.doc(0)
        self.validate_cli(self.ws.paths.pipeline_doc(PID, 0))
        src = doc["sources"][0]
        self.assertEqual(src["id"], SID)
        self.assertEqual(src["sha256"], sha256_file(self.pdf))
        self.assertEqual(src["bytes"], self.pdf.stat().st_size)
        self.assertEqual(src["physical_page_count"], len(make_pdf.pages(True)))
        self.assertEqual(src["printed_page_offset"], 2)
        self.assertEqual(src["page_size"], {"width_pt": 612, "height_pt": 792})
        self.assertNotIn("page_sizes", src)
        self.assertEqual(src["text_layer"]["extractor"], "pdftotext")
        self.assertRegex(src["text_layer"]["version"], r"^\d+\.\d+")
        self.assertEqual(src["x_offset"]["method"], "running_head_mode")
        self.assertGreaterEqual(src["x_offset"]["confidence"], inference.MIN_OFFSET_CONFIDENCE)
        self.assertEqual(src["x_edition_check"]["status"], "unknown")
        # the rare kinds come from the numbering, and are recorded as such
        self.assertEqual(src["x_kinds"]["method"], "inferred+slot_filling")
        self.assertIn("Theorem", src["x_kinds"]["kinds"])
        prov = doc["provenance"][0]
        self.assertEqual((prov["actor"], prov["method"]), ("tool", "pipeline"))
        self.assertEqual(doc["items"], [])

        companion = doc["companion"]
        self.assertEqual(companion["repo"], f"local/{PID}")
        self.assertRegex(companion["commit"], r"^[0-9a-f]{40}$")
        repo = self.ws.paths.pipeline_repo(PID)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(companion["commit"], head)
        log = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, "1")
        self.assertEqual(ingest.repo_tracks_pdf(repo), [])
        self.assertEqual(result["commit"], head)

        settings = pipeline_yaml.load_for(self.ws.paths, PID)
        self.assertEqual(settings["scope"], {"chapters": [1]})
        self.assertIsNone(settings["exercise_policy"])
        self.assertEqual(settings["pipeline"]["source_id"], SID)

    def test_the_pdf_and_its_text_stay_out_of_git_and_out_of_the_document(self):
        self.ingest()
        paths = self.ws.paths
        self.assertTrue(paths.source_text(SID).exists())
        self.assertTrue((paths.source_pages_dir(SID) / "p003.txt").exists())
        ignore = paths.gitignore.read_text(encoding="utf-8")
        for rule in ("sources/", "*.pdf", "**/pages/", "pipelines/*/repo/"):
            self.assertIn(rule, ignore)
        text = paths.pipeline_doc(PID, 0).read_text(encoding="utf-8")
        for _kind, _number, _title, body in make_pdf.ITEMS:
            self.assertNotIn(body, text)
        self.assertEqual(list(paths.pipeline_dir(PID).rglob("*.pdf")), [])

    def test_the_receipt_has_the_section_4_1_shape_and_a_pending_spot_check(self):
        self.ingest()
        receipt = stage_receipt.read(self.ws.paths.stage_receipt_json(PID, "S0"))
        self.assertEqual(receipt["stage"], "S0")
        self.assertEqual(receipt["input_doc_sha256"], sha256_file(self.pdf))
        self.assertEqual(receipt["output_doc_sha256"],
                         sha256_file(self.ws.paths.pipeline_doc(PID, 0)))
        self.assertEqual(receipt["pipeline_yaml_sha256"],
                         sha256_file(self.ws.paths.pipeline_yaml(PID)))
        self.assertEqual((receipt["fleet_runs"], receipt["model_ids"], receipt["cost_usd"]),
                         ([], [], 0))
        gate = receipt["gate_result"]
        self.assertTrue(gate["ok"])
        self.assertEqual(set(gate["checks"]), {"offset_confidence", "edition", "full_sha",
                                               "errata", "validate_canonical",
                                               "no_pdf_in_git"})
        self.assertEqual(gate["checks"]["errata"]["note"], "skipped with --no-errata")
        # the booklet's sha is unknown to the registry: the edition is a pending gate
        self.assertEqual([(m["gate"], m["status"], m["method"]) for m in gate["manual"]],
                         [("spot_check", "pending", "manual"), ("edition", "pending", "manual")])
        self.assertEqual(gate["manual"][1]["sha256"], sha256_file(self.pdf))
        self.assertEqual(gate["checks"]["offset_confidence"]["dissenting_pages"], [])
        self.assertFalse(receipt["consent"]["include_source_text"])

    def test_a_second_ingest_changes_nothing(self):
        first = self.ingest()
        before = self.ws.paths.pipeline_doc(PID, 0).read_bytes()
        second = self.ingest()
        self.assertEqual(first["commit"], second["commit"])
        self.assertEqual(before, self.ws.paths.pipeline_doc(PID, 0).read_bytes())

    def test_another_source_or_scope_under_the_same_pipeline_id_is_refused(self):
        self.ingest()
        with self.assertRaises(CfError):
            self.ingest(source_id="another-book")
        with self.assertRaises(CfError):
            self.ingest(scope_spec="chapters=1,2")

    def test_the_scope_is_explicit(self):
        with self.assertRaises(CfError):
            ingest.parse_scope("")
        with self.assertRaises(CfError):
            ingest.parse_scope("chapter=1")
        self.assertEqual(ingest.parse_scope("chapters=2,1;sections=1.7"),
                         {"chapters": [1, 2], "sections": ["1.7"]})

    def test_the_cli_runs_both_stages(self):
        ws = str(self.ws_dir)
        self.assertEqual(cf_main.main(["--workspace", ws, "ingest", "--pdf", str(self.pdf),
                                       "--scope", "chapters=1", "--pipeline", PID,
                                       "--source-id", SID, "--no-errata", "-q"]), 0)
        self.assertEqual(cf_main.main(["--workspace", ws, "inventory", "--pipeline", PID,
                                       "-q"]), 0)
        self.validate_cli(self.ws.paths.pipeline_doc(PID, 1))


class TestEditionAndOffsetGates(M1Case):
    def test_the_shipped_registry_refuses_the_tao_draft(self):
        registry = Registry.load(None)
        with self.assertRaises(SourceError) as caught:
            ingest.edition_check(registry, DRAFT_SHA, None)
        self.assertIn("draft", str(caught.exception))
        self.assertIn("tao-measure-gsm126-published", str(caught.exception))
        allowed = ingest.edition_check(registry, DRAFT_SHA, "reproducing the 2011 anchors")
        self.assertEqual(allowed["status"], "draft")
        published = registry.get("tao-measure-gsm126-published")
        self.assertEqual(ingest.edition_check(registry, published.sha256, None)["status"],
                         "current")

    def _overlay_draft(self):
        sha = sha256_file(self.pdf)
        (self.ws_dir / "registry.yaml").write_text(
            "version: 1\nsources:\n  notes-draft:\n    title: Elementary Notes on Sets\n"
            "    edition: synthetic draft\n    sha256: " + sha + "\n"
            "    edition_status: draft\n    confirmed: false\n", encoding="utf-8")

    def test_a_draft_edition_is_refused_without_an_override(self):
        self._overlay_draft()
        with self.assertRaises(SourceError) as caught:
            self.ingest(source_id=None)
        self.assertIn("--override-edition", caught.exception.hint)
        self.assertFalse(self.ws.paths.pipeline_doc(PID, 0).exists())

    def test_an_override_is_recorded(self):
        self._overlay_draft()
        self.ingest(source_id=None, override_edition="the draft is all we have")
        receipt = stage_receipt.read(self.ws.paths.stage_receipt_json(PID, "S0"))
        edition = receipt["gate_result"]["checks"]["edition"]
        self.assertEqual((edition["status"], edition["override"]),
                         ("draft", "the draft is all we have"))
        self.assertEqual(self.doc(0)["sources"][0]["id"], "notes-draft")

    def test_a_weak_offset_is_refused_and_a_manual_one_is_stamped(self):
        unnumbered = [page[:-2] for page in make_pdf.pages(False)]
        with mock.patch.object(make_pdf, "PAGES", unnumbered):
            self.pdf.write_bytes(make_pdf.build())
        with self.assertRaises(SourceError) as caught:
            self.ingest()
        self.assertIn("--offset", caught.exception.hint)
        self.ingest(offset=2)
        src = self.doc(0)["sources"][0]
        self.assertEqual((src["printed_page_offset"], src["x_offset"]["method"]),
                         (2, "manual"))


class TestInventory(M1Case):
    def setUp(self):
        super().setUp()
        with mock.patch.object(fetch_errata, "curl", fake_curl):
            self.ingest(no_errata=False, errata_url="https://errata.example.org/notes")

    def test_every_numbered_item_with_its_page_and_heading_quote(self):
        result = self.inventory()
        self.validate_cli(result["doc_path"])
        doc = self.doc(1)
        numbered = {i["id"]: i for i in doc["items"] if i["form"] == "numbered"}
        expected = {ids.item_label_id(k, n) for k, n, _t, _b in make_pdf.ITEMS}
        self.assertEqual(set(numbered), expected)
        pages = self.pages()
        locations = {loc["items"][0]: loc for loc in doc["locations"]}
        for kind, number, title, _body in make_pdf.ITEMS:
            item = numbered[ids.item_label_id(kind, number)]
            self.assertEqual((item["kind"], item["number"], item["label"]),
                             (kind, number, f"{kind} {number}"))
            if title:
                self.assertEqual(item["title"], title)
            page = item["pages"]["first"]
            self.assertIn(f"{kind} {number}", pages[page - 1])
            quote = locations[item["id"]]["target"]["selector"][0]["refinedBy"]
            self.assertEqual(quote["exact"], f"{kind} {number}")
            self.assertLessEqual(len(quote.get("prefix", "")), document.CONTEXT_CHARS)
        # an item runs to the page before the next heading at most
        spans = [(i["pages"]["first"], i["pages"].get("last", i["pages"]["first"]))
                 for i in numbered.values()]
        self.assertTrue(all(first <= last for first, last in spans))

    def test_the_marked_planted_passages_are_found_beside_their_items(self):
        doc = self.inventory()["doc"]
        found = {}
        for item in doc["items"]:
            if item.get("x_finder") == "heuristic":
                found[item["kind"]] = found.get(item["kind"], []) + [item]
        planted_after = {"Proof": {"lemma-1.1.4", "theorem-1.4.3"}, "Remark": {"theorem-1.2.3"},
                         "Example": {"definition-1.3.3"}, "Claim": {"definition-1.3.3"}}
        for kind, near in planted_after.items():
            self.assertEqual({i["relations"]["near"][0] for i in found[kind]}, near, kind)
        heuristic = sum(len(v) for v in found.values())
        marked = sum(1 for v in make_pdf.PASSAGES.values() for _t, m in v if m)
        planted = sum(len(v) for v in make_pdf.PASSAGES.values())
        # recall on the planted passages: every marked one, never the unmarked one
        self.assertEqual((heuristic, marked, planted), (5, 5, 6))
        prov = {p["id"]: p for p in doc["provenance"]}
        self.assertIn("HEURISTIC", prov[inventory.PROV_HEURISTIC]["note"])
        for loc in doc["locations"]:
            if loc["items"][0] in {i["id"] for v in found.values() for i in v}:
                self.assertEqual(loc["provenance"], inventory.PROV_HEURISTIC)

    def _unmarked_passage(self) -> tuple[str, int]:
        text, _marked = make_pdf.PASSAGES["1.2.3"][1]
        exact = document.cap_words(text)
        for number, page in enumerate(self.pages(), start=1):
            if exact in document.normalize(page):
                return exact, number
        self.fail("the unmarked planted passage is not in the text")

    def test_supplied_passages_are_accepted_on_their_page_and_placed(self):
        exact, page = self._unmarked_passage()
        whole = make_pdf.PASSAGES["1.2.3"][1][0]
        self.assertGreater(document.word_count(whole), document.EXACT_WORD_CAP)
        supplied = self.root / "passages.json"
        write_json(supplied, {"items": [
            {"form": "passage", "kind": "passage", "title": "The empty set has measure zero",
             "pages": {"first": page - 2, "basis": "printed"},
             "quote": {"exact": whole}},
            {"form": "passage", "kind": "passage", "title": "A quote on the wrong page",
             "pages": {"first": page + 1, "basis": "physical"}, "quote": {"exact": exact}},
        ]})
        result = self.inventory(passages=supplied)
        doc = result["doc"]
        mine = [i for i in doc["items"] if i.get("x_finder") == "supplied"]
        self.assertEqual(len(mine), 1)
        item = mine[0]
        self.assertEqual(item["id"], ids.passage_quote_id(exact))
        self.assertEqual(item["pages"], {"first": page, "basis": "physical", "anchor": "own"})
        self.assertEqual(item["relations"], {"near": ["theorem-1.2.3"]})
        rejected = result["gate"]["rejected_passages"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("does not occur on its page", rejected[0]["reason"])
        # with the heuristic finder's five, all six planted passages are in the document
        planted = {document.cap_words(t) for v in make_pdf.PASSAGES.values() for t, _m in v}
        quotes = {loc["target"]["selector"][0]["refinedBy"]["exact"]
                  for loc in doc["locations"] if loc["provenance"] != inventory.PROV_HEADINGS}
        self.assertEqual({q for q in planted if q in quotes}, planted)

    def test_errata_become_items_linked_to_what_they_name(self):
        doc = self.inventory()["doc"]
        errata = [i for i in doc["items"] if i.get("x_finder") == "errata"]
        linked = {tuple(i["relations"]["same_as"]) for i in errata if "relations" in i}
        self.assertEqual(linked, {("lemma-1.1.4",), ("corollary-1.2.4", "theorem-1.2.3")})
        page_only = [i for i in errata if "relations" not in i]
        self.assertEqual(len(page_only), 1)
        self.assertIn("printed page 3", page_only[0]["title"])
        for item in errata:
            self.assertEqual(item["source"], f"{SID}-errata")
            self.assertLessEqual(document.word_count(item["quote"]["exact"]),
                                 document.EXACT_WORD_CAP)
        gate = self.inventory()["gate"]
        self.assertEqual(gate["errata"]["out_of_scope"], 1)
        self.assertEqual(gate["errata"]["no_item_no_page"], 1)
        self.assertTrue(gate["checks"]["errata_quotes"]["ok"])
        web = [s for s in doc["sources"] if s["kind"] == "web"][0]
        self.assertEqual(web["url"], "https://errata.example.org/notes")

    def test_the_s1_receipt_chains_to_doc_v0(self):
        result = self.inventory()
        receipt = stage_receipt.read(result["receipt"])
        self.assertEqual(receipt["stage"], "S1")
        self.assertEqual(receipt["input_doc_sha256"],
                         sha256_file(self.ws.paths.pipeline_doc(PID, 0)))
        self.assertEqual(receipt["output_doc_sha256"], result["doc_sha256"])
        gate = receipt["gate_result"]
        self.assertTrue(gate["ok"])
        self.assertEqual(set(gate["checks"]),
                         {"slot_completeness", "slot_contiguity", "exact_selectors",
                          "unique_ids", "exact_cap", "headings_placed",
                          "validate_canonical", "errata_quotes"})
        self.assertEqual({m["gate"] for m in gate["manual"]},
                         {"maintainer_signoff", "recall_calibration"})
        self.assertEqual(gate["stats"]["numbered"], len(make_pdf.ITEMS))

    def test_a_second_inventory_writes_the_same_document(self):
        self.inventory()
        before = self.ws.paths.pipeline_doc(PID, 1).read_bytes()
        self.inventory()
        self.assertEqual(before, self.ws.paths.pipeline_doc(PID, 1).read_bytes())

    def test_every_exact_is_capped(self):
        doc = self.inventory()["doc"]
        self.assertEqual(document.over_cap(doc), [])
        text = self.ws.paths.pipeline_doc(PID, 1).read_text(encoding="utf-8")
        for _kind, _number, _title, body in make_pdf.ITEMS:
            if document.word_count(body) > document.EXACT_WORD_CAP:
                self.assertNotIn(body, text)


class TestGateFailures(M1Case):
    def setUp(self):
        super().setUp()
        self.ingest()

    def test_a_missing_slot_fails_and_a_reason_settles_it(self):
        path = self.ws.paths.source_items(SID)
        table = read_json(path)
        table["items"] = [r for r in table["items"] if r["item"] != "Lemma 1.1.3"]
        write_json(path, table)
        with self.assertRaises(CfError):
            self.inventory()
        self.assertFalse(self.ws.paths.pipeline_doc(PID, 1).exists())
        report = read_json(self.ws.paths.stage_receipts_dir(PID) / inventory.GATE_REPORT)
        missing = report["checks"]["slot_contiguity"]["missing"]
        self.assertEqual([m["number"] for m in missing], ["1.1.3"])
        self.assertEqual(missing[0]["seen_as"][0]["label"], "Lemma 1.1.3")
        reasons = self.ws.paths.pipeline_dir(PID) / inventory.SLOT_REASONS_FILE
        reasons.write_text('"1.1.3": "dropped on purpose for this test"\n', encoding="utf-8")
        result = self.inventory()
        explained = result["gate"]["checks"]["slot_contiguity"]["explained"]
        self.assertEqual([(e["number"], e["reason"]) for e in explained],
                         [("1.1.3", "dropped on purpose for this test")])

    def test_a_selector_that_does_not_match_its_page_fails(self):
        doc = inventory.build(self.ws.paths, PID)["doc"]
        pages = self.pages()
        self.assertTrue(source_gates.exact_selectors(doc, {SID: pages})["ok"])
        moved = copy.deepcopy(doc)
        loc = moved["locations"][0]
        page = loc["pages"]["first"]
        loc["target"]["selector"][0]["value"] = f"page={page + 1}"
        failed = source_gates.exact_selectors(moved, {SID: pages})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["failures"][0]["location"], loc["id"])
        garbled = copy.deepcopy(doc)
        garbled["locations"][0]["target"]["selector"][0]["refinedBy"]["exact"] += " garbled"
        self.assertFalse(source_gates.exact_selectors(garbled, {SID: pages})["ok"])

    def test_a_duplicate_id_fails(self):
        exact = document.cap_words("Every box is an elementary set.")
        supplied = self.root / "dup.json"
        write_json(supplied, [{"id": "lemma-1.1.3", "id_source": "declared",
                               "form": "passage", "kind": "passage", "title": "a clash",
                               "pages": {"first": 4, "basis": "physical"},
                               "quote": {"exact": exact}}])
        page = next(n for n, p in enumerate(self.pages(), start=1)
                    if exact in document.normalize(p))
        data = read_json(supplied)
        data[0]["pages"]["first"] = page
        write_json(supplied, data)
        with self.assertRaises(CfError):
            self.inventory(passages=supplied)
        report = read_json(self.ws.paths.stage_receipts_dir(PID) / inventory.GATE_REPORT)
        self.assertEqual(report["checks"]["unique_ids"]["duplicates"],
                         {"items": ["lemma-1.1.3"], "locations": ["lemma-1.1.3@p003"]})
        self.assertFalse(report["checks"]["unique_ids"]["ok"])

    def test_an_exact_over_the_cap_fails(self):
        doc = inventory.build(self.ws.paths, PID)["doc"]
        doc["locations"][0]["target"]["selector"][0]["refinedBy"]["exact"] = " ".join(
            ["word"] * (document.EXACT_WORD_CAP + 1))
        self.assertFalse(source_gates.exact_cap(doc)["ok"])


def _stamp(lines: list[str], printed: int) -> list[str]:
    """A booklet page: ``lines`` padded out, with the running head (the printed number)."""
    padded = lines + [""] * max(0, make_pdf.LINES_PER_PAGE - len(lines))
    return padded + ["", "%s%d" % (" " * 40, printed)]


class TestReviewProbes(M1Case):
    """The probes of the pull request 16 review, each planted on the booklet."""

    def test_a_failing_rerun_removes_the_stale_doc_v1_and_receipt(self):
        self.ingest()
        self.inventory()
        doc_v1 = self.ws.paths.pipeline_doc(PID, 1)
        receipt = self.ws.paths.stage_receipt_json(PID, "S1")
        self.assertTrue(doc_v1.exists() and receipt.exists())
        path = self.ws.paths.source_items(SID)
        table = read_json(path)
        table["items"] = [r for r in table["items"] if r["item"] != "Lemma 1.2.2"]
        write_json(path, table)
        with self.assertRaises(CfError) as caught:
            self.inventory()
        self.assertIn("slot_contiguity", str(caught.exception))
        self.assertFalse(doc_v1.exists())
        self.assertFalse(receipt.exists())
        report = read_json(self.ws.paths.stage_receipts_dir(PID) / inventory.GATE_REPORT)
        self.assertFalse(report["ok"])

    def test_a_registered_source_id_with_another_sha_is_refused(self):
        for sid in ("tao-measure-gsm126-published", "tao-measure-2011-draft"):
            with self.assertRaises(SourceError) as caught:
                self.ingest(source_id=sid)
            self.assertIn(sid, str(caught.exception))
            self.assertIn("cf sources add", caught.exception.hint)
        self.assertFalse(self.ws.paths.pipeline_doc(PID, 0).exists())

    def test_an_unknown_sha_is_a_pending_edition_gate_and_a_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cf_main.main(["--workspace", str(self.ws_dir), "ingest", "--pdf",
                                           str(self.pdf), "--scope", "chapters=1",
                                           "--pipeline", PID, "--source-id", SID,
                                           "--no-errata"]), 0)
        self.assertIn("the registry does not know sha", err.getvalue())
        receipt = stage_receipt.read(self.ws.paths.stage_receipt_json(PID, "S0"))
        pending = [m for m in receipt["gate_result"]["manual"] if m["gate"] == "edition"]
        self.assertEqual([(m["status"], m["source_id"]) for m in pending], [("pending", SID)])

    def _chapter_two(self) -> tuple[int, int]:
        """The booklet with Chapter 2 opening on its own page and Definition 2.1.1 two
        pages on; returns (the opening's physical page, Definition 2.1.1's)."""
        body = make_pdf.pages(True)
        printed = len(body) - 2
        extra = [
            _stamp(["Chapter 2. Abstract measure spaces", "",
                    "Remark. This remark belongs to the second chapter, not the first."],
                   printed + 1),
            _stamp(["2.1 Abstract measure spaces", "",
                    "Some words that open the second chapter's first section."], printed + 2),
            _stamp(["Definition 2.1.1 (Measure space). A measure space is a set with a "
                    "measure."], printed + 3),
        ]
        with mock.patch.object(make_pdf, "pages", lambda passages=False: body + extra):
            self.pdf.write_bytes(make_pdf.build(True))
        return len(body) + 1, len(body) + 3

    def test_the_scope_stops_before_the_next_chapter_opens(self):
        opening, definition = self._chapter_two()
        self.ingest()
        result = self.inventory()
        doc = result["doc"]
        last = next(i for i in doc["items"] if i["id"] == "example-1.4.5")
        self.assertLess(last["pages"].get("last", last["pages"]["first"]), opening)
        self.assertEqual(result["stats"]["scope_pages"][1], opening - 1)
        self.assertNotIn("definition-2.1.1", {i["id"] for i in doc["items"]})
        quotes = [loc["target"]["selector"][0]["refinedBy"]["exact"]
                  for loc in doc["locations"]]
        self.assertFalse([q for q in quotes if "second chapter" in q])
        self.assertEqual(result["gate"]["scope_stops"][0]["label"], "Chapter 2")
        self.assertEqual(result["gate"]["scope_stops"][0]["page"], opening)
        self.assertTrue(result["gate"]["ok"])
        # the out-of-scope heading, not its opening, is what an unmarked tail stops at
        self.assertGreater(definition, opening)

    def test_an_ambiguous_heading_is_placed_by_its_shape(self):
        planted = dict(make_pdf.PASSAGES)
        planted["1.1.2"] = [("Lemma 1.1.3 is what the next item states, as every box is an "
                             "elementary set.", False)]
        with mock.patch.object(make_pdf, "PASSAGES", planted):
            self.pdf.write_bytes(make_pdf.build(True))
        self.ingest()
        rows = {r["item"]: r for r in read_json(self.ws.paths.source_items(SID))["items"]}
        self.assertTrue(rows["Lemma 1.1.3"]["ambiguous"])
        result = self.inventory()
        pages = self.pages()
        item = next(i for i in result["doc"]["items"] if i["id"] == "lemma-1.1.3")
        heading_page = next(n for n, p in enumerate(pages, start=1)
                            if "Lemma 1.1.3 (Boxes are elementary)" in document.normalize(p))
        self.assertEqual(item["pages"]["first"], heading_page)
        self.assertEqual(item["title"], "Boxes are elementary")
        loc = next(l for l in result["doc"]["locations"] if l["items"] == ["lemma-1.1.3"])
        quote = loc["target"]["selector"][0]["refinedBy"]
        self.assertTrue(quote["suffix"].startswith(" (Boxes are elementary)"), quote)
        self.assertTrue(result["gate"]["checks"]["headings_placed"]["ok"])

    def test_an_ambiguous_heading_with_no_heading_shape_is_reported_not_guessed(self):
        rows = [{"item": "Lemma 1.1.3", "kind": "Lemma", "number": "1.1.3", "pdf_page": 1,
                 "ambiguous": True, "occurrences": [{"pdf_page": 1}, {"pdf_page": 2}]}]
        pages = ["Lemma 1.1.3 is cited here first\n", "Lemma 1.1.3 is cited again\n"]
        items, _locs, _anchors, unplaced = inventory.numbered_items(
            rows, pages, {"chapters": [1]}, SID)
        self.assertEqual(items, [])
        self.assertEqual(unplaced[0]["item"], "Lemma 1.1.3")
        self.assertIn("heading's shape", unplaced[0]["reason"])

    def test_a_wrapped_title_beats_a_closing_parenthesis_cross_reference(self):
        # Found by the Tao calibration: the heading of Exercise 1.2.11 opens a title that
        # wraps onto the next line, and the next page cites it at line start as
        # "Exercise 1.2.11.)", a period that closes a parenthetical; the cross-reference
        # must not win on shape.
        rows = [{"item": "Exercise 1.2.11", "kind": "Exercise", "number": "1.2.11", "pdf_page": 1,
                 "ambiguous": True, "occurrences": [{"pdf_page": 1}, {"pdf_page": 2}]}]
        pages = ["have another important result:\n\nExercise 1.2.11 (Monotone convergence theorem "
                 "for measurable\nsets).\n\n(i) Show that the union is measurable.\n",
                 "measure. Show that m(En) converges to m(E). (Hint: use\nthe monotone convergence "
                 "theorems,\nExercise 1.2.11.)\n(iii) Give a counterexample.\n"]
        items, _locs, _anchors, unplaced = inventory.numbered_items(
            rows, pages, {"chapters": [1]}, SID)
        self.assertEqual(unplaced, [])
        item = next(i for i in items if i["id"] == "exercise-1.2.11")
        self.assertEqual(item["pages"]["first"], 1)
        self.assertEqual(item["title"], "Monotone convergence theorem for measurable sets")

    def test_a_supplied_quote_is_checked_whole_before_it_is_capped(self):
        self.ingest()
        text = make_pdf.PASSAGES["1.1.4"][0][0]
        true12 = document.cap_words(text)
        page = next(n for n, p in enumerate(self.pages(), start=1)
                    if true12 in document.normalize(p))
        supplied = self.root / "tail.json"
        write_json(supplied, [{"form": "passage", "kind": "passage", "title": "a tail",
                               "pages": {"first": page, "basis": "physical"},
                               "quote": {"exact": true12 + " THIS TAIL IS FABRICATED AND "
                                                           "NOT ON THE PAGE AT ALL"}}])
        result = self.inventory(passages=supplied)
        self.assertEqual([i for i in result["doc"]["items"]
                          if i.get("x_finder") == "supplied"], [])
        rejected = result["gate"]["rejected_passages"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("the rest does not", rejected[0]["reason"])

    def test_a_duplicate_supplied_passage_is_listed(self):
        self.ingest()
        whole = make_pdf.PASSAGES["1.2.3"][1][0]
        exact = document.cap_words(whole)
        page = next(n for n, p in enumerate(self.pages(), start=1)
                    if exact in document.normalize(p))
        supplied = self.root / "twice.json"
        write_json(supplied, [
            {"form": "passage", "kind": "passage", "title": "physical",
             "pages": {"first": page, "basis": "physical"}, "quote": {"exact": whole}},
            {"form": "passage", "kind": "passage", "title": "printed",
             "pages": {"first": page - 2, "basis": "printed"}, "quote": {"exact": exact}}])
        result = self.inventory(passages=supplied)
        self.assertEqual(result["stats"]["passages_supplied"], 1)
        self.assertEqual(result["gate"]["duplicate_passages"],
                         [{"record": "passages[1]", "id": ids.passage_quote_id(exact),
                           "page": page, "duplicate_of": "passages[0]"}])
        self.assertEqual(result["stats"]["passages_duplicate"], 1)

    def test_the_offset_dissenters_reach_the_receipt(self):
        shifted = [list(page) for page in make_pdf.pages(False)]
        shifted[5][-1] = "%s%d" % (" " * 40, 4 + 5)   # physical 6 prints 9, not 4
        with mock.patch.object(make_pdf, "PAGES", shifted):
            self.pdf.write_bytes(make_pdf.build())
        result = self.ingest()
        self.assertEqual(result["offset"], 2)
        self.assertLess(result["offset_confidence"], 1.0)
        receipt = stage_receipt.read(self.ws.paths.stage_receipt_json(PID, "S0"))
        gate = receipt["gate_result"]
        self.assertEqual(gate["checks"]["offset_confidence"]["dissenting_pages"],
                         [{"page": 6, "printed": [9]}])
        spot = next(m for m in gate["manual"] if m["gate"] == "spot_check")
        self.assertEqual(spot["look_at_pages"], [6])
        self.assertEqual(self.doc(0)["sources"][0]["x_offset"]["dissenting_pages"], [6])


class TestCountersAndSlots(unittest.TestCase):
    def test_two_counters_are_read_off_the_numbers(self):
        numbers = {"Exercise": ["1.1.1", "1.1.2", "1.1.3"], "Definition": ["1.1.1"],
                   "Lemma": ["1.1.2"], "Remark": ["1.1.3"]}
        self.assertEqual(inference.infer_counters(numbers),
                         [["Exercise"], ["Definition", "Lemma", "Remark"]])

    def test_a_gap_in_one_counter_is_not_hidden_by_the_other(self):
        numbers = {"Exercise": ["1.1.1", "1.1.2", "1.1.3"], "Definition": ["1.1.1"],
                   "Lemma": ["1.1.3"]}
        result = source_gates.slot_contiguity(numbers)
        self.assertFalse(result["ok"])
        self.assertEqual([m["number"] for m in result["missing"]], ["1.1.2"])
        # pooled across kinds, completeness sees nothing wrong: that is why both exist
        self.assertEqual(source_gates.slot_completeness(
            [n for v in numbers.values() for n in v])["completeness"], 1.0)
        self.assertTrue(source_gates.slot_contiguity(
            numbers, {"Lemma 1.1.2": "numbered in the text as a displayed claim"})["ok"])

    def test_slot_filling_kinds_need_every_number_to_fit(self):
        text = ("Definition 1.1.1.\nLemma 1.1.2.\nTheorem 1.1.3.\nDefinition 1.1.4.\n"
                "Remark 1.1.5.\nFigure 1.1.6.\nClaim 1.1.2 again.\nClaim 1.1.9.\n")
        found = inference.slot_filling_kinds(
            text, {"Definition": ["1.1.1", "1.1.4"], "Lemma": ["1.1.2"]})
        self.assertEqual(found, {"Remark": ["1.1.5"], "Theorem": ["1.1.3"]})


class TestRecall(unittest.TestCase):
    SHA = "ab" * 32

    def truth(self) -> dict:
        return {
            "sources": [{"id": "book", "sha256": self.SHA, "printed_page_offset": 10}],
            "items": [
                {"id": "lemma-1.1.1", "source": "book", "form": "numbered", "kind": "Lemma",
                 "number": "1.1.1", "pages": {"first": 1, "basis": "printed"}},
                {"id": "exercise-1.1.1", "source": "book", "form": "numbered",
                 "kind": "Exercise", "number": "1.1.1"},
                {"id": "equation-1.2", "source": "book", "form": "numbered",
                 "kind": "Equation", "number": "1.2", "pages": {"first": 2, "basis": "printed"}},
                {"id": "lemma-2.1.1", "source": "book", "form": "numbered", "kind": "Lemma",
                 "number": "2.1.1", "pages": {"first": 30, "basis": "printed"}},
                {"id": "passage-p3-aaaaaaaa", "source": "book", "form": "passage",
                 "kind": "passage", "title": "t", "pages": {"first": 3, "basis": "printed"},
                 "relations": {"near": ["lemma-1.1.1"]}},
            ],
            "locations": [{"id": "exercise-1.1.1@p013", "items": ["exercise-1.1.1"],
                           "pages": {"first": 13, "basis": "physical"},
                           "target": {"source": "book"}}],
        }

    def test_numbered_items_match_on_kind_number_and_first_page(self):
        truth, excluded = recall.numbered_truth(self.truth(), self.SHA, [1])
        self.assertEqual(sorted(truth), [("Exercise", "1.1.1"), ("Lemma", "1.1.1")])
        self.assertEqual([e["id"] for e in excluded], ["equation-1.2"])
        ours = {"items": [
            {"form": "numbered", "kind": "Lemma", "number": "1.1.1",
             "pages": {"first": 11, "basis": "physical"}},
            {"form": "numbered", "kind": "Exercise", "number": "1.1.1",
             "pages": {"first": 14, "basis": "physical"}}]}
        result = recall.numbered_recall(ours, truth)
        self.assertEqual((result["found"], result["total"]), (1, 2))
        self.assertIn("found on physical page 14", result["misses"][0]["why"])

    def test_a_passage_is_recovered_on_its_page_or_beside_its_item(self):
        truth = recall.passage_truth(self.truth(), self.SHA, 100)
        self.assertEqual([(t["page"], t["near"]) for t in truth], [(13, ["lemma-1.1.1"])])
        beside = {"items": [{"form": "passage", "pages": {"first": 12},
                             "relations": {"near": ["lemma-1.1.1"]}}]}
        self.assertEqual(recall.passage_recall(beside, truth)["found"], 1)
        elsewhere = {"items": [{"form": "passage", "pages": {"first": 20}}]}
        self.assertEqual(recall.passage_recall(elsewhere, truth)["misses"], truth)


class TestBookletIsUnchanged(unittest.TestCase):
    def test_the_default_booklet_has_no_planted_passage(self):
        self.assertEqual(make_pdf.pages(False), make_pdf.PAGES)
        self.assertNotEqual(make_pdf.build(True), make_pdf.build())


if __name__ == "__main__":
    unittest.main()
