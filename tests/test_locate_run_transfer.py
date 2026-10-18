"""``cf run`` for a locate run: what is sent, where it lands, and what the host is checked for.

New code, for the locate integration's review findings C-04, C-05, OPS-08 and
OPS-09 (and the round-3 verifier's: a shard of thousands of sources, and the
lock directories read off the candidates layout), in the terms of
references/locate-documents.md section 9 (what
``cf run`` sends and verifies) and the code that does it
(``scripts/run/manifest.py``, ``scripts/run/remote.py``).  The runs are
planned by the real ``cf plan-locate`` over the synthetic campaign of
tests/_locate_fixtures.py.  The host is a temp directory: a planned host
command is run here with ``sh -c`` -- as an unprivileged user when the suite
runs as root, since the read-only directories an earlier transfer leaves are
exactly what the steps must work with -- and nothing is sent anywhere.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _locate_fixtures as F  # noqa: E402
from cflib import hashing  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib.workspace import Workspace  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402
from locate import inputs  # noqa: E402
from run import manifest, remote  # noqa: E402

#: Linux's cap on ONE argument string (MAX_ARG_STRLEN, 32 pages): an ssh argv
#: element over it is refused by execve, here or by the host's sshd
MAX_ARG_STRLEN = 128 * 1024

#: the uid/gid a root test runs host commands as (``nobody``)
NOBODY = 65534


def _unlock_and_remove(path: Path) -> None:
    """Remove a host tree whose directories a transfer left read-only."""
    for dirpath, dirnames, _files in os.walk(path):
        for name in dirnames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o755)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def as_host_user():
    """``preexec_fn`` for a host command: drop root, so a read-only directory binds."""
    if os.geteuid() != 0:
        return None

    def drop() -> None:
        os.setgroups([])
        os.setgid(NOBODY)
        os.setuid(NOBODY)
    return drop


def on_host(command: str) -> subprocess.CompletedProcess:
    """Run one planned host command here, as the host user."""
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True,
                          preexec_fn=as_host_user(), env={"PATH": os.environ["PATH"]})


class HostCase(unittest.TestCase):
    """The synthetic campaign, an allowlisted host whose remote root is a temp directory."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="cf-locate-run-"))
        self.addCleanup(_unlock_and_remove, tmp)
        os.chmod(tmp, 0o755)
        self.root = tmp / "ws"
        self.paths = F.campaign(self, self.root)
        self.host_root = tmp / "host"
        self.host_root.mkdir()
        if os.geteuid() == 0:
            os.chown(self.host_root, NOBODY, NOBODY)
        data = read_yaml(self.paths.workspace_yaml)
        data["hosts"] = {"allowlist": ["examplebox"],
                         "examplebox": {"remote_root": str(self.host_root), "ssh": "examplebox",
                                        "tend_cmd": "env"}}
        write_yaml(self.paths.workspace_yaml, data)
        self.planted = tmp / "planted.sqlite"
        shutil.copy2(self.paths.corpus_db(F.U), self.planted)
        for extra in (("--blind",), ("--tools-corpus-db", str(self.planted))):
            self.cf("locate", "candidates", "--sourceset", F.SS, "--universe", F.U, *extra)

    def cf(self, *argv: str) -> str:
        code, out, err = F.cf("--workspace", str(self.root), *argv)
        self.assertEqual(code, 0, out + err)
        return out + err

    def plan(self, run_id: str, *extra: str) -> dict:
        self.cf("plan-locate", "--preset", "locate-inventory", "--sourceset", F.SS,
                "--universe", F.U, "--host", "examplebox", "--max-cost", "5",
                "--run-id", run_id, *extra)
        return read_json(self.paths.gate_json(run_id))

    def entries(self, run_id: str, *, include_texts: bool = False):
        ws = Workspace.load(self.root, validate=False)
        return manifest.build_manifest(
            workspace_dir=self.root, skill_dir=SKILL, run_id=run_id,
            run=read_yaml(self.paths.run_yaml(run_id)),
            host=manifest.resolve_host(ws, "examplebox"), include_texts=include_texts)

    def steps(self, run_id: str, entries: list[dict]) -> list[dict]:
        ws = Workspace.load(self.root, validate=False)
        run = read_yaml(self.paths.run_yaml(run_id))
        return remote.plan_commands(
            host=manifest.resolve_host(ws, "examplebox"), run_id=run_id, run=run,
            entries=entries, names=manifest.remote_names(run, run_id),
            budget={"max_cost": 5, "currency": "USD"},
            concurrency={"workers": 1, "reviewers": 1},
            locate=manifest.locate_gate(self.paths.run_dir(run_id))[0])


class TestTheCandidatesAPromptNamesAreSent(HostCase):
    """Review C-04: a run sends what its prompts tell the workers to read."""

    def test_every_arm(self):
        for run_id, extra in (("inv-h", ()), ("inv-b", ("--blind",)),
                              ("inv-p", ("--tools-corpus-db", str(self.planted)))):
            with self.subTest(run=run_id):
                gate = self.plan(run_id, *extra)
                text = "\n".join(p.read_text(encoding="utf-8") for p in
                                 sorted(self.paths.prompts_dir(run_id).glob("*.md")))
                named = set(re.findall(r"`(/[^`]*/candidates/[^`]*)/<source-id>\.json`", text))
                self.assertEqual(named, {gate["candidates_dir"]})
                entries, _ = self.entries(run_id)
                entry = next(e for e in entries if e["name"] == "candidates")
                self.assertEqual(entry["dest"].rstrip("/"), gate["candidates_dir"])
                self.assertTrue(entry["exists"])
                self.assertEqual(sorted(Path(s).name for s in entry["srcs"]),
                                 sorted(f"{sid}.json" for sid in gate["sources"]))
                if gate["blind"]:
                    # what the blind arm's workers read: lexical hints, no curated hit
                    for src in entry["srcs"]:
                        doc = read_json(Path(src))
                        self.assertEqual((doc["blind"], doc["curated"], doc["article_qid"]),
                                         (True, [], None))


class TestAKindDirectoryLandsInsideALockedOne(HostCase):
    """Review C-05: a kind directory's transfer opens and creates the directories an
    earlier transfer left read-only, in any order, and leaves every one read-only."""

    def candidates_steps(self, run_id: str) -> list[str]:
        """The host commands the run's candidates transfer needs, its rsync emulated."""
        entries, _ = self.entries(run_id)
        entry = next(e for e in entries if e["name"] == "candidates")
        with mock.patch.object(remote, "rsync_supports_chmod", return_value=False):
            steps = [s for s in self.steps(run_id, [entry]) if s["stage"] == "transfer"
                     and not s["title"].startswith(("refuse to re-send", "commit "))]
        stage = self.host_root.parent / f"stage-{run_id}"
        stage.mkdir()
        for src in entry["srcs"]:
            shutil.copy2(src, stage / Path(src).name)
            os.chmod(stage / Path(src).name, 0o644)
        out = []
        for step in steps:
            if step["title"].startswith("rsync "):
                dest = entry["dest"].rstrip("/")
                out.append("set -e; " + "; ".join(
                    f"cp {shlex.quote(str(stage / Path(s).name))} {shlex.quote(dest + '/.tmp')}"
                    f" && mv -f {shlex.quote(dest + '/.tmp')} "
                    f"{shlex.quote(dest + '/' + Path(s).name)}" for s in entry["srcs"]))
            else:
                self.assertEqual(step["argv"][:2], ["ssh", "examplebox"])
                out.append(step["argv"][2])
        return out

    def test_the_mkdir_never_reaches_inside_a_locked_directory(self):
        gate = self.plan("inv-p", "--tools-corpus-db", str(self.planted))
        entries, _ = self.entries("inv-p")
        entry = next(e for e in entries if e["name"] == "candidates")
        universe_dir = str(Path(gate["candidates_dir"]).parent)
        self.assertEqual(entry["lock_dirs"], [universe_dir, gate["candidates_dir"]])
        (mkdir,) = [s for s in self.steps("inv-p", entries)
                    if s["title"] == "create remote directories"]
        self.assertNotIn(universe_dir, mkdir["shell"])

    def test_assisted_planted_blind_then_assisted_again(self):
        if os.geteuid() == 0 and not hasattr(os, "setuid"):
            self.skipTest("cannot drop root here")
        runs = [("inv-h", ()), ("inv-p", ("--tools-corpus-db", str(self.planted))),
                ("inv-b", ("--blind",)), ("inv-h2", ())]
        for run_id, extra in runs:
            gate = self.plan(run_id, *extra)
            for command in self.candidates_steps(run_id):
                done = on_host(command)
                self.assertEqual(done.returncode, 0,
                                 f"{run_id}: {command}\n{done.stdout}{done.stderr}")
            for sid in gate["sources"]:
                self.assertTrue((Path(gate["candidates_dir"]) / f"{sid}.json").is_file())
        top = Path(gate["candidates_dir"])
        for directory in [top] + [p for p in top.iterdir() if p.is_dir()]:
            self.assertFalse(directory.stat().st_mode & 0o222, f"{directory} is writable")
        self.assertEqual({p.name for p in top.iterdir() if p.is_dir()},
                         {"blind", f"planted-{hashing.sha256_file(self.planted)[:16]}"})


class TestTheVerifyStageChecksTheSourceLayers(HostCase):
    """Review OPS-08: `--stage verify` fails, by name, when a layer the gate reads is not
    on the host as pinned; and a run sent without its text says which it expects there."""

    def layer_step(self, run_id: str) -> dict:
        entries, _ = self.entries(run_id)
        (step,) = [s for s in self.steps(run_id, entries) if s["stage"] == "verify"
                   and s["title"].startswith("every source layer")]
        self.assertTrue(step["safe"])
        return step

    def send_gate_json(self, run_id: str) -> Path:
        """The transfer's copy of the staged gate.json, in the host's gate directory."""
        run = read_yaml(self.paths.run_yaml(run_id))
        hosted = self.host_root / manifest.remote_names(run, run_id)["gate"] / "gate.json"
        hosted.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.paths.tend_root(run_id) / "gate" / "gate.json", hosted)
        return hosted

    def test_present_missing_and_rewritten(self):
        gate = self.plan("inv-h")
        hosted = Path(gate["sourceset"]["sources_dir"])
        step = self.layer_step("inv-h")
        # bound to the gate.json the transfer sends: the staged gate/ directory's
        self.assertEqual(step["expected"], hashing.sha256_file(
            self.paths.tend_root("inv-h") / "gate" / "gate.json"))
        done = on_host(step["argv"][2])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("gate.json", done.stderr)     # not sent yet: the pins are read there
        config = self.send_gate_json("inv-h")
        done = on_host(step["argv"][2])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("REFUSED", done.stderr)
        self.assertIn(F.W1, done.stdout + done.stderr)
        local = self.paths.sourceset_sources_dir(F.SS)
        shutil.copytree(local, hosted)
        if os.geteuid() == 0:
            for dirpath, _dirs, files in os.walk(self.host_root):
                os.chown(dirpath, NOBODY, NOBODY)
                for name in files:
                    os.chown(os.path.join(dirpath, name), NOBODY, NOBODY)
        done = on_host(step["argv"][2])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"source layers: {len(gate['sources'])} pinned sha256 verified",
                      done.stdout)
        # verifier, round 4: the gate's SourceView also opens the two tables beside the
        # layer, which gate.json does not pin, and fails every call without them
        for name, damage in (("formulas.json", None), ("paragraphs.json", "{}"),
                             ("paragraphs.json", "[1, 2]"), ("formulas.json", "[{")):
            table = hosted / F.W1 / name
            kept = table.read_bytes()
            if damage is None:
                table.unlink()
            else:
                table.write_text(damage, encoding="utf-8")
            done = on_host(step["argv"][2])
            self.assertNotEqual(done.returncode, 0, (name, damage))
            self.assertIn(f"{F.W1}/{name}: FAILED", done.stdout)
            self.assertNotIn("layer.txt: FAILED", done.stdout)
            self.assertIn(f"1 of {len(gate['sources'])} source(s)", done.stderr)
            self.assertIn("--include-source-text", done.stderr)
            table.write_bytes(kept)
            if os.geteuid() == 0:
                os.chown(table, NOBODY, NOBODY)
        self.assertEqual(on_host(step["argv"][2]).returncode, 0)
        layer = hosted / F.W2 / "layer.txt"
        layer.write_text(layer.read_text(encoding="utf-8") + "edited", encoding="utf-8")
        done = on_host(step["argv"][2])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f"{F.W2}/layer.txt: FAILED", done.stdout)
        self.assertNotIn(f"{F.W1}/layer.txt: FAILED", done.stdout)
        # a gate.json on the host that is not the one sent is refused before its pins
        # are trusted: the gate reads that file
        layer.write_text(layer.read_text(encoding="utf-8")[:-len("edited")], encoding="utf-8")
        self.assertEqual(on_host(step["argv"][2]).returncode, 0)
        config.write_text(config.read_text(encoding="utf-8") + " ", encoding="utf-8")
        done = on_host(step["argv"][2])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("is not the gate.json this transfer sent", done.stderr)

    def test_a_run_sent_without_its_text_names_the_sources_it_expects(self):
        gate = self.plan("inv-h")
        _entries, warnings = self.entries("inv-h")
        (warning,) = [w for w in warnings if "--include-source-text" in w]
        for sid in gate["sources"]:
            self.assertIn(sid, warning)
        self.assertIn(gate["sourceset"]["sources_dir"], warning)
        _entries, warnings = self.entries("inv-h", include_texts=True)
        self.assertFalse([w for w in warnings if "--include-source-text" in w])


class TestAShardOfThousandsOfSources(unittest.TestCase):
    """Round-3 verifier: no host command of a locate run grows past one argument Linux
    allows with the number of its sources.  An article-tier shard holds up to 4,000 (8 a
    task, 500 tasks a root); the layer check took about 136 bytes a source in ONE ssh
    argument and failed at about 960, and the mkdir named each layer directory."""

    N = 8 * 500

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-locate-shard-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ids = [f"enwiki-{10000000 + i}-{2000000000 + i}" for i in range(self.N)]
        self.local_sources = self.tmp / "ws" / "sourcesets" / "wpm" / "sources"
        self.host_root = self.tmp / "host"
        self.host_sources = self.host_root / "sourcesets" / "wpm" / "sources"
        self.pins = {}
        for i, sid in enumerate(self.ids):
            layer = self.local_sources / sid / "layer.txt"
            layer.parent.mkdir(parents=True)
            layer.write_text(f"Widget lemma, part {i}\n", encoding="utf-8")
            for name in ("formulas.json", "paragraphs.json"):
                (layer.parent / name).write_text("[]", encoding="utf-8")
            self.pins[sid] = {"layer_sha256": hashing.sha256_file(layer)}

    def gate(self, sources_dir: Path) -> dict:
        return {"sourceset": {"sources_dir": str(sources_dir)}, "sources": self.pins}

    def test_the_layer_check_reads_the_pins_on_the_host(self):
        shutil.copytree(self.local_sources, self.host_sources)
        config = self.host_root / "shard-gate" / "gate.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(self.gate(self.host_sources), indent=2), encoding="utf-8")
        command = remote.layer_check(str(config), hashing.sha256_file(config))
        self.assertLess(len(command.encode("utf-8")), 8192)
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"source layers: {self.N} pinned sha256 verified", done.stdout)
        # a missing layer and a rewritten one are named; past LAYERS_NAMED the rest counted
        (self.host_sources / self.ids[7] / "layer.txt").unlink()
        rewritten = self.host_sources / self.ids[9] / "layer.txt"
        rewritten.write_text("Gadget theorem\n", encoding="utf-8")
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(f"{self.ids[7]}/layer.txt: FAILED open or read", done.stdout)
        self.assertIn(f"{self.ids[9]}/layer.txt: FAILED", done.stdout)
        self.assertIn(f"2 of {self.N} source(s)", done.stderr)
        shutil.rmtree(self.host_sources)
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(len([ln for ln in done.stdout.splitlines() if ": FAILED" in ln]),
                         remote.LAYERS_NAMED)
        # three files a source: the layer and the two tables the gate opens beside it
        self.assertIn(f"... and {3 * self.N - remote.LAYERS_NAMED} more", done.stdout)
        self.assertIn(f"{self.N} of {self.N} source(s)", done.stderr)

    def test_no_step_of_the_plan_has_an_argument_over_the_cap(self):
        run_dir = self.tmp / "ws" / "runs" / "shard"
        run_dir.mkdir(parents=True)
        (run_dir / "gate.json").write_text(json.dumps(self.gate(self.host_sources)),
                                           encoding="utf-8")
        (run_dir / "gate.local.json").write_text(json.dumps(self.gate(self.local_sources)),
                                                 encoding="utf-8")
        entries, _ = manifest.locate_entries(run_dir, include_texts=True)
        self.assertEqual(len(entries), self.N)
        host = {"name": "examplebox", "ssh": "examplebox", "remote_root": str(self.host_root),
                "tend_cmd": "env"}
        names = {"entrypoint": "shard", "tend_root": "shard-tend", "gate": "shard-gate"}
        with mock.patch.object(remote, "rsync_supports_chmod", return_value=True):
            steps = remote.plan_commands(
                host=host, run_id="shard", run={"locate": {"gate_files": {}}}, entries=entries,
                names=names, budget={"max_cost": 5, "currency": "USD"},
                concurrency={"workers": 1, "reviewers": 1},
                locate=manifest.locate_gate(run_dir)[0])
        widest = max((len(arg.encode("utf-8")), step["title"]) for step in steps
                     for arg in step["argv"])
        self.assertLess(widest[0], MAX_ARG_STRLEN, widest[1])
        (mkdir,) = [s for s in steps if s["title"] == "create remote directories"]
        self.assertIn(shlex.quote(str(self.host_sources)), mkdir["argv"][2])
        self.assertNotIn(self.ids[0], mkdir["argv"][2])
        (layers,) = [s for s in steps if s["title"].startswith("every source layer")]
        self.assertLess(len(layers["argv"][2].encode("utf-8")), 8192)


class TestTheLockedDirectoriesFollowTheCandidatesLayout(unittest.TestCase):
    """Round-3 verifier: which candidates transfer opens the per-universe directory too is
    read off ``locate.inputs.candidates_subdir`` (where the planner put the files), not
    off a list of kinds typed here, so a renamed or new kind cannot bring C-05 back."""

    def test_every_tools_kind(self):
        top = "/data/cf/sourcesets/wpm/candidates/u1"
        for kind in inputs.TOOLS_KINDS:
            with self.subTest(kind=kind):
                db = {"kind": kind, "sha256": "ab" * 32}
                sub = inputs.candidates_subdir(db)
                dest = top + (f"/{sub}" if sub else "")
                self.assertEqual(manifest.candidates_lock_dirs(dest + "/", db),
                                 [top, dest] if sub else [dest])
        for kind in (inputs.TOOLS_BLIND, inputs.TOOLS_PLANTED):
            self.assertEqual(len(manifest.candidates_lock_dirs(
                top + "/k", {"kind": kind, "sha256": "ab" * 32})), 2)
        self.assertEqual(manifest.candidates_lock_dirs(top, {}), [top])


class TestTheTendVersionIsChecked(unittest.TestCase):
    """Review OPS-09: the recorded tend range is checked when the probe SUCCEEDS."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-tend-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def stub(self, version: str) -> str:
        uv = self.tmp / version / "uv"
        uv.parent.mkdir()
        uv.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        uv.chmod(uv.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return f"{uv} run"

    def run_step(self, version: str, required: str = ">=0.4,<0.6"):
        fragment = remote._tend_version_step({"tend_version_required": required, "name": "b"},
                                             self.stub(version))
        return subprocess.run(["bash", "-c", "set -e; " + fragment], capture_output=True,
                              text=True)

    def test_out_of_range_is_refused_and_in_range_passes(self):
        done = self.run_step("0.1.0")
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("REFUSED: tend 0.1.0", done.stderr)
        done = self.run_step("0.5.2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("tend 0.5.2 (required >=0.4,<0.6)", done.stdout)


if __name__ == "__main__":
    unittest.main()
