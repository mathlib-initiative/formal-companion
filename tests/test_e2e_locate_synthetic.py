"""The locate flow end to end, as one suite test: tests/e2e/locate_synthetic.py.

New code.  The script chains the real commands of every locate package --
``cf corpus``, ``cf sourceset``, ``cf locate candidates``, ``cf plan-locate`` for
the three presets, the staged gate and ``passage_ids.py`` run from a copy of
the planned root's ``gate/`` with ``python3 -I``, ``cf harvest``, ``cf tally
--locate``, ``cf export literature-links`` and ``cf bench`` -- over synthetic
inputs (references/locate-documents.md sections 7-10, references/locate-bench.md).
The packages' own tests each build the stage before theirs from the documented
format; this test is the one where an artifact a stage writes is the one the
next stage reads.  It needs nothing outside the checkout and the recorded
answers under ``tests/data/locate/wiki``.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE / "e2e") not in sys.path:
    sys.path.insert(0, str(HERE / "e2e"))

import locate_synthetic  # noqa: E402


class TestTheLocateFlowEndToEnd(unittest.TestCase):

    def test_every_stage_reads_what_the_one_before_wrote(self):
        scratch = Path(tempfile.mkdtemp(prefix="cf-locate-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        out = io.StringIO()
        flow = locate_synthetic.main(scratch, out=out)
        self.assertEqual(flow.failed, 0, out.getvalue())
        self.assertGreaterEqual(flow.passed, 45, out.getvalue())


if __name__ == "__main__":
    unittest.main()
