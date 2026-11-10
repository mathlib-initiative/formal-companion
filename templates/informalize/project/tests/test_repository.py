"""Check the generated runtime in this output repository.

New code for docs/design/stage_map.md section 4.2: verify recorded files without requiring
the producer checkout, TeX, Lean, or network access. Add project regression
tests beside this file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GeneratedRuntime(unittest.TestCase):
    def test_runtime_matches_its_manifest(self):
        runtime = ROOT / "informal"
        record = json.loads((runtime / "GENERATED.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "formal-companion/informalize-generated")
        self.assertEqual(record["schema_version"], 1)
        self.assertTrue(record["files"], "The runtime manifest must list its files")
        seen = set()
        for row in record["files"]:
            relative = row["path"]
            with self.subTest(path=relative):
                path = PurePosixPath(relative)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertEqual(str(path), relative)
                self.assertNotIn(relative, seen)
                seen.add(relative)
                self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", row["sha256"]))
                target = runtime / relative
                self.assertTrue(target.resolve().is_relative_to(runtime.resolve()))
                self.assertFalse(any(p.is_symlink() for p in [target, *target.parents]
                                     if p != runtime.parent))
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                                 row["sha256"], f"Regenerate changed runtime file: {relative}")


if __name__ == "__main__":
    unittest.main()
