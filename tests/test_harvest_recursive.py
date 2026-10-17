"""Recursive harvest: a locate run lands ``<task-id>/<source-id>.json``.

New code, for docs/proposals/wikipedia-locate.md section 3 (findings layout:
listing, transfer and checksums become recursive) and section 6 (receipts
count UNRESOLVED separately), over references/locate-documents.md section 10.
The flat faithfulness corpus every existing receipt describes must come out
exactly as before; that is proved first.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import test_verify_locate as tvl  # noqa: E402
except ImportError:  # pragma: no cover
    from tests import test_verify_locate as tvl  # noqa: E402

import cf as cf_main  # noqa: E402
from cflib import hashing  # noqa: E402
from harvest import gate_corpus  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest._compat import iter_finding_files  # noqa: E402


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestAFlatCorpusIsWhatItWas(unittest.TestCase):

    def setUp(self):
        self.root = scratch(self)
        self.source = self.root / "findings"
        for name in ("exercise-1-1-1.json", "exercise-1-1-2.json", "lemma-2-1.json"):
            write(self.source / name, {"item": name, "source_id": "s", "source_sha": "a" * 64,
                                       "disagreements": []})
        for name in ("RECEIPT.json", "manifest.json", ".hidden.json"):
            write(self.source / name, {})
        (self.source / "notes.txt").write_text("not json")

    def test_the_listing_the_names_and_the_manifest(self):
        names = harvest_mod.source_names({"kind": "local", "path": str(self.source)})
        self.assertEqual(names, ["exercise-1-1-1.json", "exercise-1-1-2.json", "lemma-2-1.json"])
        self.assertEqual([p.name for p in iter_finding_files(self.source)], names)
        dest = self.root / "corpus" / "run-a"
        receipt = harvest_mod.harvest(run_id="run-a", source={"kind": "local",
                                                              "path": str(self.source)},
                                      dest=dest, sources={"s": "a" * 64}, verify_sha=True)
        self.assertTrue(receipt["complete"])
        self.assertEqual(sorted(receipt["manifest"]), names)
        self.assertEqual(receipt["expected"]["names"], names)
        self.assertEqual(receipt["edition"]["matched"], 3)
        self.assertNotIn("locate", receipt)
        self.assertEqual(sorted(p.name for p in dest.iterdir()),
                         sorted(names + ["RECEIPT.json"]))

    def test_the_remote_commands_list_and_hash_recursively(self):
        source = harvest_mod.parse_source("examplebox:/srv/runs/r1/findings")
        commands = {c["title"]: c["argv"][-1] for c in harvest_mod.list_commands(source, self.root)}
        listing = next(v for k, v in commands.items() if k.startswith("expected set"))
        self.assertIn("find . -type f -name '*.json' -print0", listing)
        self.assertIn("xargs -0 shasum -a 256", " ".join(commands.values()))
        outputs = {"remote listing": "./b.json\0./t-1/a.json\0./RECEIPT.json\0./.x/y.json\0",
                   "remote checksums": f"{'1' * 64}  ./b.json\n{'2' * 64}  ./t-1/a.json\n"}
        with mock.patch.object(harvest_mod, "_run", lambda argv, what: outputs[what]):
            self.assertEqual(harvest_mod.source_names(source), ["b.json", "t-1/a.json"])
            self.assertEqual(harvest_mod.source_shas(source),
                             {"b.json": "1" * 64, "t-1/a.json": "2" * 64})


def locate_doc(task: str, source: str, mode: str, **body) -> dict:
    return {"schema": "cf-locate/1", "mode": mode, "run_id": "r-1", "task_id": task,
            "source_id": source, "source_sha": tvl.HTML_SHA, **body}


class TestANestedCorpus(unittest.TestCase):

    def setUp(self):
        self.root = scratch(self)
        self.source = self.root / "findings"
        write(self.source / "t-1" / "enwiki-1001-2002.json",
              locate_doc("t-1", "enwiki-1001-2002", "locate",
                         passages=[{"item": "In-text #00000001", "outcome": "located"},
                                   {"item": "In-text #00000002", "outcome": "unresolved"}]))
        write(self.source / "t-2" / "enwiki-1001-2002.json",
              locate_doc("t-2", "enwiki-1001-2002", "locate",
                         passages=[{"item": "In-text #00000003", "outcome": "unresolved"}],
                         unresolved={"reason": "attempt_cap: 3", "by": "attempt_cap"}))
        write(self.source / "t-3" / "enwiki-1003-2004.json",
              locate_doc("t-3", "enwiki-1003-2004", "locate", passages=[],
                         unresolved={"reason": "no source text", "by": "worker"}))
        write(self.source / "t-3" / "RECEIPT.json", {})

    def test_names_are_relative_paths_and_land_nested(self):
        dest = self.root / "corpus" / "r-1"
        receipt = harvest_mod.harvest(run_id="r-1", source={"kind": "local", "path": str(self.source)},
                                      dest=dest, verify_sha=True)
        want = ["t-1/enwiki-1001-2002.json", "t-2/enwiki-1001-2002.json",
                "t-3/enwiki-1003-2004.json"]
        self.assertEqual(receipt["expected"]["names"], want)
        self.assertEqual(sorted(receipt["manifest"]), want)
        self.assertTrue(receipt["complete"], harvest_mod.describe(receipt))
        self.assertEqual(receipt["manifest"]["t-1/enwiki-1001-2002.json"],
                         hashing.sha256_file(self.source / "t-1" / "enwiki-1001-2002.json"))
        self.assertTrue((dest / "t-2" / "enwiki-1001-2002.json").is_file())
        # a later loss is named by its path
        (dest / "t-2" / "enwiki-1001-2002.json").unlink()
        receipt = harvest_mod.build_receipt(run_id="r-1", source={"kind": "local"},
                                            expected=want, dest=dest)
        self.assertEqual(receipt["missing"], ["t-2/enwiki-1001-2002.json"])
        self.assertFalse(receipt["complete"])

    def test_the_receipt_counts_unresolved_on_its_own(self):
        dest = self.root / "corpus" / "r-1"
        receipt = harvest_mod.harvest(run_id="r-1", source={"kind": "local", "path": str(self.source)},
                                      dest=dest)
        self.assertEqual(receipt["locate"], {
            "documents": 3, "by_mode": {"locate": 3}, "tasks": 3, "unresolved_documents": 2,
            "attempt_capped_documents": 1, "passages": 3, "unresolved_passages": 2, "verdicts": 0})
        self.assertIn("1 at the attempt cap", harvest_mod.describe(receipt))
        self.assertIn("never as not formalized", harvest_mod.describe(receipt))

    def test_the_edition_audit_names_nested_files(self):
        dest = self.root / "corpus" / "r-1"
        receipt = harvest_mod.harvest(run_id="r-1", source={"kind": "local", "path": str(self.source)},
                                      dest=dest, sources={"enwiki-1001-2002": tvl.HTML_SHA})
        self.assertEqual(receipt["edition"]["unknown_source"], ["t-3/enwiki-1003-2004.json"])
        self.assertFalse(receipt["complete"])


class TestTheLocateGateAtHarvest(unittest.TestCase):

    def setUp(self):
        self.fx = tvl.Fixture(self, "locate")
        self.fx.manifest("t-loc-0001", tvl.locate_manifest_doc(self.fx))
        self.landed = self.fx.root / "landed"
        self.fx.document("t-loc-0001", tvl.locate_body(), directory=self.landed)

    def test_a_good_corpus_passes_and_a_bad_one_names_its_rule(self):
        result = gate_corpus.gate_locate_corpus(self.landed, self.fx.root / "gate" / "gate.json")
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["files"], 1)
        body = tvl.locate_body()
        body["passages"][0]["locations"][0]["declaration"] = "Widget.phantom"
        self.fx.document("t-loc-0001", body, directory=self.landed)
        result = gate_corpus.gate_locate_corpus(self.landed, self.fx.root / "gate" / "gate.json")
        self.assertFalse(result["ok"])
        self.assertEqual(result["by_rule"], {"3": 1})
        self.assertIn("FAILED", gate_corpus.render(result))

    def test_cf_harvest_of_a_locate_run(self):
        ws = self.fx.root / "ws"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cf_main.main(["init", str(ws), "--no-git", "-q"])
        run_dir = ws / "runs" / "r-1"
        run_dir.mkdir(parents=True)
        (run_dir / "run.yaml").write_text(
            "version: 1\nrun_id: r-1\nmode: locate\nlocate:\n  mode: locate\n", encoding="utf-8")
        shutil.copy2(self.fx.root / "gate" / "gate.json", run_dir / "gate.local.json")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(ws), "harvest", "r-1", "--from", str(self.landed)])
        self.assertEqual(code, 0, out.getvalue())
        receipt = json.loads((ws / "corpus" / "r-1" / "RECEIPT.json").read_text())
        self.assertTrue(receipt["gate"]["ok"])
        self.assertEqual(receipt["gate"]["mode"], "locate")
        self.assertEqual(receipt["sources"], {tvl.SID: tvl.HTML_SHA})
        self.assertEqual(receipt["locate"]["documents"], 1)
        self.assertIn("locate run:", out.getvalue())
        self.assertFalse((ws / "reports" / "tally.r-1.json").exists())


if __name__ == "__main__":
    unittest.main()
