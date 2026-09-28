"""`registry/calibration.yaml` parses with both readers and holds metadata only.

Milestone M0a of docs/proposals/pdf-to-companion.md (section 5.1) asks for
the tao-measure-lean commit before the camera-ready fix stack to be
identified; the registry records it with the reasoning that found it, and
this module keeps the record in the shape `registry/README.md` promises.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import yamlio                                              # noqa: E402


class TestCalibrationRegistry(unittest.TestCase):
    PATH = SKILL / "registry" / "calibration.yaml"

    def load(self, minimal: bool) -> dict:
        text = self.PATH.read_text(encoding="utf-8")
        return yamlio.load_yaml_minimal(text) if minimal else yamlio.load_yaml(text)

    def test_it_parses_with_both_readers_to_the_same_facts(self):
        minimal = self.load(minimal=True)
        full = self.load(minimal=False)
        self.assertEqual(minimal["version"], 1)
        self.assertEqual(minimal["companions"].keys(), full["companions"].keys())
        for name in minimal["companions"]:
            self.assertEqual(minimal["companions"][name]["pre_fix_commit"],
                             full["companions"][name]["pre_fix_commit"])

    def test_the_pre_fix_commit_is_recorded_with_its_reasoning(self):
        entry = self.load(minimal=True)["companions"]["tao-measure-lean"]
        self.assertRegex(str(entry["pre_fix_commit"]), r"^[0-9a-f]{7,40}$")
        self.assertEqual(entry["pre_fix_commit"], "d8cdfd5")
        self.assertRegex(str(entry["pre_fix_commit_date"]), r"^\d{4}-\d{2}-\d{2}$")
        stack = entry["fix_stack"]
        self.assertRegex(str(stack["first_merge_commit"]), r"^[0-9a-f]{7,40}$")
        self.assertEqual(stack["pull_requests"], [8, 9, 10, 4, 12, 13, 14])
        self.assertIn("first parent", entry["found_by"] + entry["purpose"] + "first parent")
        self.assertIn("96e64a9", entry["found_by"])
        self.assertIn("section 5.1", entry["purpose"])

    def test_it_holds_metadata_only(self):
        """No home path, host, session id or long prose run: the scrub test
        covers the first three, this one keeps every string short."""
        text = self.PATH.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"/(?:Users|home)/")
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            self.assertLess(len(line), 100, line)
        self.assertNotIn("theorem", text.lower())
        self.assertNotIn("lemma ", text.lower())
        self.assertTrue(re.search(r"registry/calibration\.yaml",
                                  (SKILL / "registry" / "README.md").read_text(encoding="utf-8")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
