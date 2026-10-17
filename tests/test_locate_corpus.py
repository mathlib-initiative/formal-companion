"""The locate corpus package: the host probe drivers, universes, ingest, corpus.sqlite.

New code, for docs/proposals/wikipedia-locate.md sections 2.3-2.5 and 6 and
references/locate-documents.md section 8.  The host-side files under
``scripts/corpus/`` run where ``cflib`` is not importable, so they inline the
git pins ``cflib.gitsafe`` defines; these tests hold the copies to the
definition.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import gitsafe  # noqa: E402
from corpus import probe_merge, universe_host  # noqa: E402


class TestTheHostDriversPinGit(unittest.TestCase):

    def test_the_inlined_pins_are_gitsafes_own(self):
        for module in (probe_merge, universe_host):
            with self.subTest(module=module.__name__):
                self.assertEqual(module._GIT_SAFE_CONFIG, gitsafe.SAFE_CONFIG)
                self.assertEqual(module._GIT_SAFE_ENV, gitsafe.SAFE_ENV)


if __name__ == "__main__":
    unittest.main()
