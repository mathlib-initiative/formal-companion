"""Milestone M1 calibration: S0 and S1 on Tao's PDF against the two companions' ground truth.

docs/proposals/pdf-to-companion.md section 5.2.  Input is only the published
PDF (sha256 ``64498329...``) and scope ``chapters=1``.  The measurements:

* the page offset: 16 at confidence at least 0.9 (the proposal's bar is 0.99),
  with 265 pages;
* numbered-item recall against the measure_theory document, matched on
  ``(page, kind, number)`` with the page its regions start on;
* numbered-item recall against the tao-measure-lean document's Chapter 1;
* passage recall against the tao-measure-lean document's "In-text" passages,
  by page or by neighbour (``pipeline/recall.py``);
* the errata, fetched raw, with an item linked to ``definition-1.5.11``.

Every miss is printed with its page and label.  The proposal's bars (99% for
numbered items, 90% for passages) are asserted only when
``CF_M1_ENFORCE_BARS=1``: a miss is a finding to adjudicate, and the figures
are the deliverable.  ``CF_M1_REPORT=<file>`` also writes them as JSON.

The PDF is ``CF_TAO_PDF`` when set.  Otherwise the test downloads the file the
author publishes (the registry's ``download_url``) into a cache under the
system temporary directory, checks its sha256, and skips when the network
refuses.  The ground truth is ``tests/data/m1/`` (its README).
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                    # noqa: E402
from cflib.errors import CfError                        # noqa: E402
from cflib.hashing import sha256_file                   # noqa: E402
from cflib.registry import Registry                     # noqa: E402
from cflib.workspace import Workspace                   # noqa: E402
from pipeline import ingest, inventory, recall          # noqa: E402

SOURCE_ID = "tao-measure-gsm126-published"
DATA = HERE / "data" / "m1"
PID = "tao-ch1"
TOOLS = all(shutil.which(t) for t in ("pdftotext", "pdfinfo", "git"))


def _entry():
    return Registry.load(None).get(SOURCE_ID)


def tao_pdf() -> tuple[Path | None, str]:
    """``(path, why not)``: CF_TAO_PDF, a cached download, or a fresh one."""
    entry = _entry()
    given = os.environ.get("CF_TAO_PDF")
    if given:
        path = Path(given).expanduser()
        if not path.is_file():
            return None, f"CF_TAO_PDF={given} is not a file"
        if sha256_file(path) != entry.sha256:
            return None, f"CF_TAO_PDF has sha {sha256_file(path)[:12]}, not {entry.sha256[:12]}"
        return path, ""
    cache = Path(tempfile.gettempdir()) / "cf-m1-tao" / f"{entry.sha256}.pdf"
    if cache.is_file() and sha256_file(cache) == entry.sha256:
        return cache, ""
    if not entry.may_fetch():
        return None, "the registry does not allow fetching this edition"
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(entry.download_url, timeout=30) as resp:
            cache.write_bytes(resp.read())
    except Exception as exc:  # the network refused: skip, never fail
        return None, f"CF_TAO_PDF is unset and the download failed ({exc})"
    if sha256_file(cache) != entry.sha256:
        cache.unlink()
        return None, "the downloaded file is not the pinned edition"
    return cache, ""


def _truth(name: str) -> dict:
    with gzip.open(DATA / f"{name}.annotations.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _say(line: str = "") -> None:
    print(line, file=sys.stderr)


@unittest.skipUnless(TOOLS, "needs pdftotext, pdfinfo and git")
class TestTaoChapterOne(unittest.TestCase):
    report: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.pdf, why = tao_pdf()
        if cls.pdf is None:
            raise unittest.SkipTest(why)
        cls.tmp = Path(tempfile.mkdtemp(prefix="cf-m1-tao-",
                                        dir=os.environ.get("CF_TEST_TMP") or None))
        ws_dir = cls.tmp / "ws"
        assert cf_main.main(["init", str(ws_dir), "--no-git", "-q"]) == 0
        cls.ws = Workspace.load(ws_dir)
        cls.errata_skipped = ""
        try:
            cls.s0 = ingest.ingest(cls.ws, pdf=cls.pdf, scope_spec="chapters=1",
                                   pipeline_id=PID)
        except CfError as exc:
            if "errata" not in str(exc):
                raise
            cls.errata_skipped = str(exc)
            cls.s0 = ingest.ingest(cls.ws, pdf=cls.pdf, scope_spec="chapters=1",
                                   pipeline_id=PID, no_errata=True)
        cls.s1 = inventory.build(cls.ws.paths, PID)
        cls.doc = cls.s1["doc"]
        cls._measure()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)

    @classmethod
    def _measure(cls):
        sha = _entry().sha256
        mt_truth, mt_excluded = recall.numbered_truth(_truth("measure_theory"), sha, [1])
        tml = _truth("tao-measure-lean")
        tml_truth, tml_excluded = recall.numbered_truth(tml, sha, [1])
        last = max(p for row in tml_truth.values() for p in row["pages"])
        passages = recall.passage_truth(tml, sha, last)
        cls.report = {
            "offset": cls.s0["offset"], "offset_confidence": cls.s0["offset_confidence"],
            "pages": cls.s0["pages"],
            "numbered_vs_measure_theory": recall.numbered_recall(cls.doc, mt_truth),
            "measure_theory_excluded": mt_excluded,
            "numbered_vs_tao_measure_lean": recall.numbered_recall(cls.doc, tml_truth),
            "tao_measure_lean_excluded": tml_excluded,
            "passages_vs_tao_measure_lean": recall.passage_recall(cls.doc, passages),
            "gate": {k: v["ok"] for k, v in cls.s1["gate"]["checks"].items()},
            "slot_contiguity_missing": cls.s1["gate"]["checks"]["slot_contiguity"]["missing"],
            "errata": cls.s1["gate"]["errata"], "errata_skipped": cls.errata_skipped,
            "stats": cls.s1["stats"],
        }
        cls._print()
        out = os.environ.get("CF_M1_REPORT")
        if out:
            Path(out).write_text(json.dumps(cls.report, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")

    @classmethod
    def _print(cls):
        r = cls.report
        _say(f"\nM1 calibration on {SOURCE_ID}, chapters=1")
        _say(f"  offset {r['offset']} at confidence {r['offset_confidence']}, "
             f"{r['pages']} pages")
        for key in ("numbered_vs_measure_theory", "numbered_vs_tao_measure_lean",
                    "passages_vs_tao_measure_lean"):
            m = r[key]
            _say(f"  {key}: {m['found']}/{m['total']} = {m['recall']}")
            for miss in m["misses"]:
                where = miss.get("pages") or miss.get("page")
                _say(f"    miss: p.{where} {miss.get('label') or miss.get('title')}"
                     f" ({miss.get('why', 'no passage on that page or beside its item')})")
        for key in ("measure_theory_excluded", "tao_measure_lean_excluded"):
            for row in r[key]:
                _say(f"  {key}: {row['label']} p.{row['pages']} ({row['why']})")
        _say(f"  gates: {r['gate']}")
        for row in r["slot_contiguity_missing"]:
            _say(f"    missing slot {row['number']} ({row['counter']}) "
                 f"seen as {row.get('seen_as', [])}")
        _say(f"  errata: {r['errata']} {r['errata_skipped']}")
        _say(f"  stats: {r['stats']}")

    def test_the_offset_and_the_page_count(self):
        self.assertEqual(self.report["offset"], 16)
        self.assertGreaterEqual(self.report["offset_confidence"], 0.9)
        self.assertEqual(self.report["pages"], 265)

    def test_the_document_passes_the_checker(self):
        self.assertTrue(self.s1["gate"]["checks"]["validate_canonical"]["ok"],
                        self.s1["gate"]["checks"]["validate_canonical"])
        self.assertTrue(self.s1["gate"]["checks"]["exact_selectors"]["ok"])
        self.assertTrue(self.s1["gate"]["checks"]["unique_ids"]["ok"])

    def test_the_errata_name_definition_1_5_11(self):
        if self.errata_skipped:
            self.skipTest(f"the errata fetch failed: {self.errata_skipped}")
        linked = {t for i in self.doc["items"] if i.get("x_finder") == "errata"
                  for t in (i.get("relations") or {}).get("same_as", [])}
        self.assertIn("definition-1.5.11", linked)

    @unittest.skipUnless(os.environ.get("CF_M1_ENFORCE_BARS") == "1",
                         "the bars are reported, and enforced only with CF_M1_ENFORCE_BARS=1")
    def test_the_proposal_bars(self):
        self.assertGreaterEqual(self.report["offset_confidence"], 0.99)
        for key in ("numbered_vs_measure_theory", "numbered_vs_tao_measure_lean"):
            self.assertGreaterEqual(self.report[key]["recall"], 0.99, key)
        self.assertGreaterEqual(self.report["passages_vs_tao_measure_lean"]["recall"], 0.9)


if __name__ == "__main__":
    unittest.main()
