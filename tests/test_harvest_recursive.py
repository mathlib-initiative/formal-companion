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
import os
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
        self.assertEqual(result["by_rule"]["3"], 1)
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


FAKE_RSYNC = """#!{python}
# rsync for a 'host' that is a local directory: strip the host, copy
import os, re, shutil, sys
flags = [a for a in sys.argv[1:] if a.startswith("-")]
args = [a for a in sys.argv[1:] if not a.startswith("-")]
src, dst = re.sub(r"^[A-Za-z0-9_.-]+:", "", args[-2]), args[-1]
if src.endswith("/"):
    for here, dirs, files in os.walk(src):
        for name in files:
            if name.endswith(".json"):
                rel = os.path.relpath(os.path.join(here, name), src)
                os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
                shutil.copy2(os.path.join(here, name), os.path.join(dst, rel))
    sys.exit(0)
if not os.path.exists(src):
    if "--ignore-missing-args" in flags:
        sys.exit(0)
    sys.stderr.write(f"rsync: link_stat {{src}} failed: No such file or directory\\n")
    sys.exit(23)
shutil.copy2(src, dst)
"""
FAKE_SSH = '#!/bin/sh\nshift\nexec /bin/bash -c "$*"\n'


class TestTheHarvestRegateWritesNoGateLog(unittest.TestCase):
    """The whole-corpus re-gate is not a worker attempt (#55)."""

    def test_the_fixture_gate_log_stays_empty(self):
        fx = tvl.Fixture(self, "locate")
        fx.manifest("t-loc-0001", tvl.locate_manifest_doc(fx))
        landed = fx.root / "landed"
        fx.document("t-loc-0001", tvl.locate_body(), directory=landed)
        self.assertTrue(fx.gate.get("log_dir"))
        result = gate_corpus.gate_locate_corpus(landed, fx.root / "gate" / "gate.json")
        self.assertTrue(result["ok"], result["problems"])
        self.assertFalse((Path(fx.gate["log_dir"]) / "gate.jsonl").exists())


class TestHostArtifactsComeHome(unittest.TestCase):
    """`cf harvest <locate run> --execute` brings the gate log, the usage export and
    the canary scan to runs/<run>/host/, prices from the usage export, and moves
    the sourceset's row for the run to harvested (D3, D5; #43, #58, #68)."""

    def setUp(self):
        from cflib.workspace import Workspace
        from cflib.yamlio import write_yaml

        self.fx = tvl.Fixture(self, "locate")
        self.fx.manifest("t-loc-0001", tvl.locate_manifest_doc(self.fx))
        root = self.fx.root
        self.remote = root / "remote"
        findings = self.remote / "r-1" / "findings"
        self.fx.document("t-loc-0001", tvl.locate_body(), directory=findings)
        self.gate_log = self.remote / "r-1-tend" / "gate-log" / "gate.jsonl"
        self.gate_log.parent.mkdir(parents=True)
        self.gate_log.write_text(json.dumps({"task_id": "t-loc-0001", "ok": True,
                                             "by_rule": {}}) + "\n", encoding="utf-8")
        state = self.remote / "campaigns" / "c-1"
        state.mkdir(parents=True)
        (state / "usage-2.jsonl").write_text(json.dumps(
            {"model": "claude-opus-5", "usage": {"input": 1000, "output": 2000,
                                                 "cache_write": 0, "cache_read": 0}}) + "\n",
            encoding="utf-8")
        self.bin = root / "fakebin"
        self.bin.mkdir()
        (self.bin / "rsync").write_text(FAKE_RSYNC.format(python=sys.executable))
        (self.bin / "ssh").write_text(FAKE_SSH)
        for name in ("rsync", "ssh"):
            (self.bin / name).chmod(0o755)

        self.ws = root / "ws"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cf_main.main(["init", str(self.ws), "--no-git", "-q"])
        ws = Workspace.load(self.ws, validate=False)
        ws.data["hosts"] = {"allowlist": ["examplebox"],
                            "examplebox": {"remote_root": str(self.remote), "ssh": "examplebox",
                                           "tend_cmd": "env"}}
        ws.save()
        rd = self.ws / "runs" / "r-1"
        rd.mkdir(parents=True)
        (rd / "run.yaml").write_text(
            "version: 1\nrun_id: r-1\nmode: locate\n"
            f"host:\n  name: examplebox\n  kind: remote\n  remote_root: {self.remote}\n"
            "locate:\n  mode: locate\n  campaign: c-1\n  sourceset:\n    id: ss-1\n",
            encoding="utf-8")
        shutil.copy2(root / "gate" / "gate.json", rd / "gate.local.json")
        host_gate = dict(self.fx.gate, log_dir=str(self.gate_log.parent))
        write(rd / "gate.json", host_gate)
        write_yaml(self.ws / "campaigns" / "c-1" / "campaign.yaml",
                   {"version": 1, "id": "c-1", "host": "examplebox",
                    "shards": [{"index": 1, "run_id": "r-0"}, {"index": 2, "run_id": "r-1"}],
                    "sequencer": {"host_dir": str(state)}})
        (self.ws / "sourcesets" / "ss-1").mkdir(parents=True)
        (self.ws / "sourcesets" / "ss-1" / "manifest-1.jsonl").write_text("{}\n")
        from sources.sourceset import Sourceset

        self.paths = Workspace.load(self.ws, validate=False).paths
        Sourceset.open(self.paths, "ss-1").record_state(
            "runs", {"status": "planned", "preset": "locate-default", "tasks": 1}, key="r-1")

    def harvest(self, *extra) -> tuple[int, str]:
        out = self.out = io.StringIO()
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(out):
            code = cf_main.main(["--workspace", str(self.ws), "harvest", "r-1", *extra])
        return code, out.getvalue()

    def test_the_dry_run_names_every_transfer(self):
        from cflib.errors import CfError

        with self.assertRaises(CfError) as caught:
            self.harvest()
        self.assertIn("needs --execute", str(caught.exception))
        out = self.out.getvalue()
        self.assertIn("host artifact gate.jsonl", out)
        self.assertIn(f"{self.gate_log}", out)
        self.assertIn("usage-2.jsonl", out)
        self.assertIn("canary-2.json", out)
        self.assertFalse((self.ws / "runs" / "r-1" / "host").exists())

    def test_the_artifacts_land_beside_the_corpus_and_price_it(self):
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        host = self.ws / "runs" / "r-1" / "host"
        self.assertEqual((host / "gate.jsonl").read_text(), self.gate_log.read_text())
        self.assertTrue((host / "usage.jsonl").is_file())
        self.assertFalse((host / "canary.json").exists())
        self.assertEqual(sorted(p.name for p in host.iterdir()), ["gate.jsonl", "usage.jsonl"])
        corpus = self.ws / "corpus" / "r-1"
        receipt = json.loads((corpus / "RECEIPT.json").read_text())
        self.assertTrue(receipt["complete"], out)
        arts = receipt["host_artifacts"]
        self.assertTrue(arts["gate.jsonl"]["fetched"])
        self.assertEqual(arts["gate.jsonl"]["sha256"], hashing.sha256_file(self.gate_log))
        self.assertEqual((arts["usage.jsonl"]["campaign"], arts["usage.jsonl"]["shard"]),
                         ("c-1", 2))
        self.assertEqual(arts["canary.json"], {"remote": str(self.remote / "campaigns" / "c-1"
                                                             / "canary-2.json"),
                                               "fetched": False, "campaign": "c-1", "shard": 2,
                                               "reason": "absent on the host"})
        self.assertIsNotNone(receipt["cost"])
        self.assertTrue(receipt["cost"]["source"]["path"].endswith("usage.jsonl"))
        self.assertGreater(receipt["cost"]["usd"], 0)
        # nothing of the host's lands in the receipt's manifest
        self.assertEqual(sorted(receipt["manifest"]), [f"t-loc-0001/{tvl.SID}.json"])
        self.assertFalse(list(corpus.rglob("*.jsonl")))
        self.assertIn("host: canary.json NOT fetched", out)
        # the harvest's own re-gate wrote no line of its own
        self.assertFalse((Path(self.fx.gate["log_dir"]) / "gate.jsonl").exists())
        self.assertEqual(len(self.gate_log.read_text().splitlines()), 1)

    def test_the_sourceset_row_moves_to_harvested_and_keeps_the_plan(self):
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        state = json.loads(self.paths.sourceset_state_json("ss-1").read_text())
        row = state["runs"]["r-1"]
        self.assertEqual(row["status"], "harvested")
        self.assertEqual((row["preset"], row["tasks"]), ("locate-default", 1))
        self.assertIs(row["complete"], True)
        self.assertIs(row["gate_ok"], True)
        self.assertEqual(row["locate"]["documents"], 1)
        self.assertEqual(state["id"], "ss-1")

    def test_an_explicit_transcripts_path_wins_over_the_usage_export(self):
        logs = self.fx.root / "logs"
        logs.mkdir()
        (logs / "u.jsonl").write_text(json.dumps(
            {"model": "claude-opus-5", "usage": {"input": 1, "output": 1, "cache_write": 0,
                                                 "cache_read": 0}}) + "\n")
        code, out = self.harvest("--execute", "--transcripts", str(logs))
        self.assertEqual(code, 0, out)
        receipt = json.loads((self.ws / "corpus" / "r-1" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["cost"]["source"]["files"]["usage_export"], 1)
        self.assertFalse(receipt["cost"]["source"]["path"].endswith("host/usage.jsonl"))


class TestNestedCorporaAreCounted(unittest.TestCase):
    """Status, monitor and tally read a nested locate corpus as one (#56, #57)."""

    def setUp(self):
        from cflib.workspace import Workspace

        self.root = scratch(self)
        self.ws = self.root / "ws"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cf_main.main(["init", str(self.ws), "--no-git", "-q"])
        self.workspace = Workspace.load(self.ws, validate=False)
        corpus = self.ws / "corpus" / "loc-1"
        for task in ("t-1", "t-2"):
            write(corpus / task / "enwiki-1001-2002.json",
                  locate_doc(task, "enwiki-1001-2002", "locate", passages=[]))
        write(corpus / "RECEIPT.json", {"complete": True, "locate": {"documents": 2},
                                        "expected": {"count": 2}, "actual": {"count": 2}})
        (corpus / "run.yaml").write_text("run_id: loc-1\nmode: locate\nlocate:\n  mode: locate\n")
        write(corpus / "gate.local.json", {})

    def test_the_local_status_counts_documents_not_directories(self):
        from run import status as status_mod

        result = status_mod.corpus_status(self.workspace.paths, "loc-1")
        self.assertEqual(result["files"], 2)
        self.assertIn("corpus:  2 findings", status_mod.render("loc-1", {}, result, None))

    def test_the_remote_counts_are_recursive(self):
        from run import status as status_mod

        steps = status_mod.remote_steps({"ssh": "examplebox", "remote_root": "/remote",
                                         "tend_cmd": "env"}, "loc-1",
                                        {"tend_root": "loc-1-tend", "entrypoint": "loc-1"})
        merged = next(s for s in steps if s["title"].startswith("findings merged"))
        self.assertIn("find /remote/loc-1/findings -type f -name '*.json'", merged["argv"][2])
        source = (SCRIPTS / "run" / "remote.py").read_text(encoding="utf-8")
        self.assertNotIn("ls -1 {shlex.quote(entrypoint + '/findings')}", source)

    def test_cf_status_counts_the_nested_corpus(self):
        from cfcmd import status as cfstatus

        facts = {r["run_id"]: r for r in cfstatus._run_facts(self.workspace)}
        self.assertEqual(facts["loc-1"]["corpus_files"], 2)

    def test_the_faithfulness_tally_leaves_a_locate_corpus_out(self):
        from harvest import tally as tally_mod

        flat = self.ws / "corpus" / "diff-1"
        write(flat / "a.json", {"defects": []})
        runs = {"diff-1": flat, "loc-1": self.ws / "corpus" / "loc-1"}
        result = tally_mod.tally(runs)
        self.assertEqual(sorted(result["runs"]), ["diff-1"])
        self.assertEqual(result["locate_runs"], ["loc-1"])
        self.assertIn("locate run(s) not counted here: loc-1", tally_mod.render(result))
        self.assertIn("cf tally --locate --run <run-id>", tally_mod.render(result))
        self.assertNotIn("loc-1", result["cost"] or {})
        # the receipt alone decides it when the run.yaml copy is gone
        (self.ws / "corpus" / "loc-1" / "run.yaml").unlink()
        self.assertTrue(tally_mod.is_locate_corpus(self.ws / "corpus" / "loc-1"))
        self.assertFalse(tally_mod.is_locate_corpus(flat))


class TestTheGateReadsWhatTheReceiptCounts(unittest.TestCase):
    """Review #63, #70, #71, #78: one listing for the receipt and the gate."""

    def setUp(self):
        self.root = scratch(self)

    def faithfulness_config(self) -> Path:
        book = self.root / "text.txt"
        book.write_text("Show that the union of two widgets is a widget and that the "
                        "intersection is a widget as well.\n", encoding="utf-8")
        cfg = self.root / "gate.local.json"
        cfg.write_text(json.dumps({
            "schema_version": 2, "preset": "differential-text", "mode": "differential",
            "run_id": "run-1", "sources": {"a-book-1st": {"text": str(book), "sha256": "c" * 64,
                                                          "page_offset": 0}},
            "repos": ["ours"], "kinds": ["CONTENT", "COVERAGE"],
            "verdicts": ["AGREE", "CONTENT_DELTA"], "witness_required": False,
            "require_book_located": True, "require_source_sha": True,
            "findings_dir": ".", "kind_rules": {}}), encoding="utf-8")
        return cfg

    @staticmethod
    def finding(quote: str) -> dict:
        return {"schema_version": 2, "run_id": "run-1", "source_id": "a-book-1st",
                "source_sha": "c" * 64, "item": "Exercise 1001.1", "book_located": True,
                "book_passage_quote": quote, "matches": [], "disagreements": [],
                "coverage_notes": ""}

    def test_a_nested_faithfulness_file_is_gated(self):
        cfg = self.faithfulness_config()
        corpus = self.root / "corpus" / "run-1"
        write(corpus / "good.json", self.finding("Show that the union of two widgets is a widget"))
        self.assertTrue(gate_corpus.gate_corpus(corpus, cfg)["ok"])
        write(corpus / "sub" / "fab.json", self.finding("a sentence the book never printed at all"))
        result = gate_corpus.gate_corpus(corpus, cfg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["files"], 2)
        self.assertTrue(any(p.startswith("sub/fab.json") for p in result["problems"]),
                        result["problems"])

    def test_a_stray_in_a_landed_locate_corpus_fails_the_receipt(self):
        fx = tvl.Fixture(self, "locate")
        fx.manifest("t-loc-0001", tvl.locate_manifest_doc(fx))
        landed = fx.root / "landed"
        fx.document("t-loc-0001", tvl.locate_body(), directory=landed)
        write(landed / "t-loc-0001" / "sub" / f"{tvl.SID}.json", {"schema": "cf-locate/1"})
        result = gate_corpus.gate_locate_corpus(landed, fx.root / "gate" / "gate.json")
        self.assertFalse(result["ok"])
        self.assertTrue(any(f"t-loc-0001/sub/{tvl.SID}.json" in p for p in result["problems"]))
        receipt = harvest_mod.harvest(
            run_id="r-1", source={"kind": "local", "path": str(landed)},
            dest=fx.root / "corpus" / "r-1",
            gate_runner=lambda d: gate_corpus.gate_locate_corpus(d, fx.root / "gate" / "gate.json"))
        self.assertFalse(receipt["complete"])

    def test_a_symlink_is_neither_listed_nor_landed(self):
        source = self.root / "findings"
        write(source / "t-1" / "s.json", {"schema": "cf-locate/1"})
        (source / "t-2").mkdir()
        (source / "t-2" / "s.json").symlink_to(source / "t-1" / "s.json")
        self.assertEqual([p.relative_to(source).as_posix() for p in iter_finding_files(source)],
                         ["t-1/s.json"])
        rsync = harvest_mod.list_commands({"kind": "remote", "host": "examplebox",
                                           "path": "/remote/r-1/findings"}, self.root / "d")[1]
        self.assertIn("--no-links", rsync["argv"])

    def test_a_checksum_the_source_did_not_give_fails_closed(self):
        dest = self.root / "corpus" / "r-1"
        write(dest / "t1" / "a.json", {"schema": "cf-locate/1"})
        write(dest / "t1" / "b\\slash.json", {"schema": "cf-locate/1"})
        sha_a = hashing.sha256_file(dest / "t1" / "a.json")
        sha_b = hashing.sha256_file(dest / "t1" / "b\\slash.json")
        out = f"{sha_a}  ./t1/a.json\n\\{sha_b}  ./t1/b\\\\slash.json\n"
        with mock.patch.object(harvest_mod, "_run", return_value=out):
            shas = harvest_mod.source_shas({"kind": "remote", "host": "h", "path": "/p"})
        self.assertEqual(shas, {"t1/a.json": sha_a, "t1/b\\slash.json": sha_b})
        names = ["t1/a.json", "t1/b\\slash.json"]
        receipt = harvest_mod.build_receipt(run_id="r-1", source={"kind": "remote"},
                                            expected=names, dest=dest, source_manifest=shas)
        self.assertTrue(receipt["complete"] and receipt["sha_verified"])
        receipt = harvest_mod.build_receipt(run_id="r-1",
                                            source={"kind": "remote", "host": "h", "path": "/p"},
                                            expected=names, dest=dest,
                                            source_manifest={"t1/a.json": sha_a})
        self.assertEqual(receipt["sha_unverified"], ["t1/b\\slash.json"])
        self.assertFalse(receipt["sha_verified"])
        self.assertFalse(receipt["complete"])
        self.assertIn("SHA UNVERIFIED 1", harvest_mod.describe(receipt))

    def test_the_standalone_entry_point_takes_its_sources(self):
        source = self.root / "findings"
        write(source / "a.json", {"defects": [], "source_id": "s", "source_sha": "abc"})
        with redirect_stdout(io.StringIO()):
            code = harvest_mod.main(["r1", "--from", str(source), "--dest",
                                     str(self.root / "corpus" / "r1"), "--source", "s=abc"])
        self.assertIn(code, (0, 1))
        receipt = json.loads((self.root / "corpus" / "r1" / "RECEIPT.json").read_text())
        self.assertEqual(receipt["sources"], {"s": "abc"})


if __name__ == "__main__":
    unittest.main()
