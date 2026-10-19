"""A source another renderer rendered is refused wherever locate would read it.

New code, for references/locate-documents.md section 7 ("Another renderer's
source"): a manifest entry whose layer sha is recorded under any renderer id
but this checkout's (``cflib.vocab.RENDERER_WEB_HTML``) carries another rule's
layer, offsets and passage ids.  ``cf sourceset render`` already refused to
re-render one in place, and ``locate.inputs.check_source`` caught a ``web-html/1``
rendering only because its ``layer_map.json`` named no renderer; a
self-consistent older rendering went straight through: the candidates wrote
its files, ``cf plan-locate`` pinned it into ``gate.json`` and the task
documents, and the export wrote its records.  ``inputs.check_renderer`` now
runs first in ``check_source`` (the candidates, the inventory tasks, the
export) and in ``plan._document_pin`` and ``gate.json``'s ``sources`` (the
locate and challenge tasks), naming the ``create --from`` step that settles it.

The workspaces built here are what one rendered under ``web-html/2`` looks
like: the fixtures' own producers, run with ``render_html.RENDERER`` patched to
the older id while they write, then restored.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (str(SCRIPTS), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _locate_fixtures as F  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from locate import inputs, plan as lplan  # noqa: E402
from sources import render_html as rh  # noqa: E402

#: the renderer before this checkout's: its renderings are self-consistent
#: (render.json, layer_map.json and the manifest all name it)
OLD = "web-html/2"


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def rendered_before(case: unittest.TestCase):
    """While it is active, the fixtures' producers render and pin as ``OLD`` did."""
    patch = mock.patch.object(rh, "RENDERER", OLD)
    patch.start()
    case.addCleanup(patch.stop)          # stopped again even if a test fails mid-way
    return patch


def ws(root: Path, *argv: str) -> tuple[int, str]:
    code, out, err = F.cf("--workspace", str(root), *argv)
    return code, out + err


class TestTheRule(unittest.TestCase):

    ENTRY = {"source_id": F.W1, "html_sha256": "a" * 64, "layer_sha256": "b" * 64}

    def test_only_this_renderers_layer_passes(self):
        inputs.check_renderer({**self.ENTRY, "renderer": rh.RENDERER}, F.SS)
        for renderer in ("web-html/1", OLD, "web-html/99", None, ""):
            with self.subTest(renderer=renderer), self.assertRaises(CfError) as ctx:
                inputs.check_renderer({**self.ENTRY, "renderer": renderer}, F.SS)
            self.assertIn(F.W1, ctx.exception.message)
            self.assertIn(rh.RENDERER, ctx.exception.message)
            if renderer:
                self.assertIn(renderer, ctx.exception.message)

    def test_an_unrendered_entry_is_the_callers_other_question(self):
        inputs.check_renderer({"source_id": F.W3, "layer_sha256": None, "renderer": None}, F.SS)

    def test_the_refusal_names_the_step_that_settles_it(self):
        with self.assertRaises(CfError) as ctx:
            inputs.check_renderer({**self.ENTRY, "renderer": OLD}, F.SS)
        self.assertIn(f"cf sourceset create <new id> --from {F.SS}", ctx.exception.hint)
        self.assertIn(OLD, ctx.exception.hint)
        self.assertNotIn(f"cf sourceset render {F.SS}", ctx.exception.hint)

    def test_no_document_pin_is_made_of_it(self):
        with self.assertRaises(CfError) as ctx:
            lplan._document_pin({**self.ENTRY, "renderer": OLD})
        self.assertIn(f"{F.W1} was rendered by {OLD}", ctx.exception.message)
        self.assertEqual(lplan._document_pin({**self.ENTRY, "renderer": rh.RENDERER}),
                         {"source_id": F.W1, "source_sha": "a" * 64, "layer_sha256": "b" * 64,
                          "renderer": rh.RENDERER})


class TestASourcesetRenderedBefore(unittest.TestCase):
    """Sourceset ``wpm`` whose two rendered sources are recorded under web-html/2."""

    def setUp(self):
        self.root = scratch(self) / "ws"
        self.paths = F.make_workspace(self.root)
        patch = rendered_before(self)
        F.write_sourceset(self.paths)
        F.write_universe(self.paths)
        patch.stop()
        self.assertEqual(rh.RENDERER, "web-html/3")
        self.entries = F.manifest_entries(self.paths)
        self.assertEqual({self.entries[s]["renderer"] for s in (F.W1, F.W2)}, {OLD})
        # self-consistent: its own files agree with it, so only the renderer refuses it
        code, text = ws(self.root, "sourceset", "status", F.SS, "--verify")
        self.assertEqual(code, 0, text)

    def test_check_source_refuses_it_with_the_seeding_step(self):
        with self.assertRaises(CfError) as ctx:
            inputs.check_source(self.paths, F.SS, self.entries[F.W1])
        self.assertIn(f"{F.W1} was rendered by {OLD}", ctx.exception.message)
        self.assertIn(f"--from {F.SS}", ctx.exception.hint)

    def test_candidates_refuse_it_and_write_nothing(self):
        code, text = ws(self.root, "locate", "candidates", "--sourceset", F.SS, "--universe", F.U)
        self.assertNotEqual(code, 0, text)
        self.assertIn(f"{F.W1} was rendered by {OLD}", text)
        directory = self.paths.candidates_json(F.SS, F.U, F.W1).parent
        self.assertFalse(directory.exists() and any(directory.iterdir()),
                         "a refused candidates run wrote a file")

    def test_plan_locate_refuses_it_and_writes_no_run(self):
        # the candidates a checkout of the older renderer would have written
        with mock.patch.object(rh, "RENDERER", OLD):
            code, text = ws(self.root, "locate", "candidates", "--sourceset", F.SS,
                            "--universe", F.U)
            self.assertEqual(code, 0, text)
        for extra in ((), ("--source", F.W2)):
            with self.subTest(extra=extra):
                code, text = ws(self.root, "plan-locate", "--preset", "locate-inventory",
                                "--sourceset", F.SS, "--universe", F.U, "--run-id", "inv-1",
                                *extra)
                self.assertNotEqual(code, 0, text)
                self.assertIn(f"{extra[-1] if extra else F.W1} was rendered by {OLD}", text)
                self.assertFalse(self.paths.run_dir("inv-1").exists())
                self.assertFalse(self.paths.tasks_dir("inv-1").exists())

    def test_gate_json_pins_no_source_of_it(self):
        manifest = inputs.manifest(self.paths, F.SS)
        with self.assertRaises(CfError) as ctx:
            lplan.gate_config(preset_mode="locate", preset_name="locate", run_id="loc-x",
                              layout={}, universe=None, corpus_db_sha256="", tools_db={},
                              manifest=manifest, source_ids=[F.W2], blind=False,
                              inventory_run=None, challenged_runs=[])
        self.assertIn(f"{F.W2} was rendered by {OLD}", ctx.exception.message)


class TestASourcesetOfTwoRenderers(unittest.TestCase):
    """``wpm`` with W1 rendered under this checkout's renderer and W2 under web-html/2.

    W1 sorts first, so a check made only as the loop reaches each source would
    have written W1's candidates before it refused W2.
    """

    def setUp(self):
        self.root = scratch(self) / "ws"
        self.paths = F.make_workspace(self.root)
        current, real = rh.RENDERER, rh.render_directory

        def render_directory(directory):
            rh.RENDERER = OLD if Path(directory).name == F.W2 else current
            return real(directory)

        with mock.patch.object(rh, "RENDERER", current), \
                mock.patch.object(rh, "render_directory", render_directory):
            F.write_sourceset(self.paths)
        F.write_universe(self.paths)
        self.assertEqual(rh.RENDERER, current)
        entries = F.manifest_entries(self.paths)
        self.assertEqual((entries[F.W1]["renderer"], entries[F.W2]["renderer"]), (current, OLD))
        self.assertLess(F.W1, F.W2)
        code, text = ws(self.root, "sourceset", "status", F.SS, "--verify")
        self.assertEqual(code, 0, text)

    def candidates(self, *extra: str) -> tuple[int, str]:
        return ws(self.root, "locate", "candidates", "--sourceset", F.SS, "--universe", F.U,
                  *extra)

    def test_candidates_refuse_it_before_writing_any_sources_file(self):
        code, text = self.candidates()
        self.assertNotEqual(code, 0, text)
        self.assertIn(f"{F.W2} was rendered by {OLD}", text)
        directory = self.paths.candidates_json(F.SS, F.U, F.W1).parent
        self.assertFalse(directory.exists() and any(directory.iterdir()),
                         "a refused candidates run wrote a file")

    def test_this_renderers_source_alone_is_retrieved(self):
        code, text = self.candidates("--source", F.W1)
        self.assertEqual(code, 0, text)
        self.assertTrue(self.paths.candidates_json(F.SS, F.U, F.W1).is_file())
        self.assertFalse(self.paths.candidates_json(F.SS, F.U, F.W2).exists())


class TestRunsPlannedBefore(unittest.TestCase):
    """An inventory, a locate run and its challenge, planned while web-html/2 was the renderer."""

    def setUp(self):
        self.root = scratch(self) / "ws"
        self.patch = rendered_before(self)
        self.paths = F.campaign(self, self.root)

    def plan_challenge(self) -> tuple[int, str]:
        return ws(self.root, "plan-locate", "--preset", "locate-challenge", "--sourceset", F.SS,
                  "--universe", F.U, "--challenge-of", "loc-1", "--run-id", "chl-1")

    def test_a_locate_run_on_its_inventory_is_refused(self):
        self.patch.stop()
        code, text = ws(self.root, "plan-locate", "--preset", "locate", "--sourceset", F.SS,
                        "--universe", F.U, "--inventory-run", "inv-1", "--run-id", "loc-1")
        self.assertNotEqual(code, 0, text)
        # the frozen inventory's reading-copy spans read the layer map first, and
        # its reader refuses a map another renderer wrote: a refusal, no traceback
        self.assertIn(f"written by renderer '{OLD}'", text)
        self.assertIn("--from", text)
        self.assertFalse(self.paths.run_dir("loc-1").exists())

    def test_a_challenge_of_its_locate_run_is_refused(self):
        F.plan_locate(self, self.root)
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths, "loc-1"))
        self.patch.stop()
        code, text = self.plan_challenge()
        self.assertNotEqual(code, 0, text)
        self.assertIn(f"was rendered by {OLD}", text)
        self.assertFalse(self.paths.run_dir("chl-1").exists())

    def export(self, set_id: str) -> dict:
        code, text = ws(self.root, "export", "literature-links", "--run", "loc-1",
                        "--challenge-run", "chl-1", "--set-id", set_id)
        self.assertIn(code, (0, 1), text)
        return read_json(self.paths.export_report_json(set_id))

    def harvested(self) -> None:
        F.plan_locate(self, self.root)
        F.write_harvest(self.paths, "loc-1", F.locate_documents(self.paths, "loc-1"))
        code, text = self.plan_challenge()
        self.assertEqual(code, 0, text)
        F.write_harvest(self.paths, "chl-1", F.challenge_documents(self.paths, "chl-1"))
        self.assertEqual(read_json(self.paths.gate_json("loc-1"))["sources"][F.W1]["renderer"],
                         OLD)

    def test_the_export_refuses_each_of_its_sources(self):
        self.harvested()
        self.patch.stop()
        report = self.export("set-1")
        self.assertEqual(report["exported"].get("sources", 0), 0)
        refused = {r["source_id"]: r["reason"] for r in report["refused"] if r["item"] is None}
        self.assertEqual(sorted(refused), [F.W1, F.W2])
        for sid, reason in refused.items():
            self.assertIn(f"{sid} was rendered by {OLD}", reason)

    def test_the_same_run_exports_under_the_renderer_it_was_made_with(self):
        """The control: nothing but the renderer stands between that run and its records."""
        self.harvested()
        report = self.export("set-2")
        self.assertEqual(report["exported"]["sources"], 2)
        self.assertFalse([r for r in report["refused"] if OLD in r["reason"]])


if __name__ == "__main__":
    unittest.main()
