"""Milestone M1 calibration: S0 and S1 on Tao's PDF against the two companions' ground truth.

docs/proposals/pdf-to-companion.md section 5.2.  Input is only the published
PDF (sha256 ``64498329...``) and scope ``chapters=1``.  The measurements:

* the page offset: 16 at confidence at least 0.9 (the proposal's bar is 0.99),
  with 265 pages;
* numbered-item recall against the measure_theory companion, matched on
  ``(page, kind, number)`` with the page its regions start on;
* numbered-item recall against the tao-measure-lean companion's Chapter 1;
* passage recall against the tao-measure-lean companion's "In-text" passages,
  one to one (``pipeline/recall.py``).  The truth tables carry no quotes, so
  the page rule applies and the figure is an upper bound on recovery, which
  the report says;
* the errata, fetched raw, with an item linked to ``definition-1.5.11``.

The ground truth is the two derived tables ``tests/data/m1/<companion>.truth.json``
(``pipeline/derive_truth.py``: ``{kind, number, page}`` and ``{page, near}``
only; the README says how the maintainer regenerates them).  A table that is
absent skips the measurements that need it.

Every miss is printed with its page and label.  The proposal's bars (99% for
numbered items, 90% for passages) are asserted only when
``CF_M1_ENFORCE_BARS=1``: a miss is a finding to adjudicate, and the figures
are the deliverable.  ``CF_M1_REPORT=<file>`` also writes them as JSON.

The PDF is ``CF_TAO_PDF`` when set, or a copy already cached under the system
temporary directory.  Only with ``CF_M1_DOWNLOAD=1`` does the test download
the file the author publishes (the registry's ``download_url``) into that
cache, checking its sha256; otherwise, and when the network refuses, it skips.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

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
    if os.environ.get("CF_M1_DOWNLOAD") != "1":
        return None, ("set CF_TAO_PDF to the published PDF, or CF_M1_DOWNLOAD=1 to download "
                      "it")
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


def _table(name: str) -> dict | None:
    """The derived truth table of companion ``name``, or ``None`` when it is absent."""
    path = DATA / f"{name}.truth.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
        cls.report = {
            "offset": cls.s0["offset"], "offset_confidence": cls.s0["offset_confidence"],
            "pages": cls.s0["pages"], "tables_missing": [],
        }
        for name, key in (("measure_theory", "measure_theory"),
                          ("tao-measure-lean", "tao_measure_lean")):
            table = _table(name)
            if table is None:
                cls.report["tables_missing"].append(name)
                continue
            if table["source_sha256"] != sha:
                raise AssertionError(f"{name}.truth.json is about sha "
                                     f"{table['source_sha256'][:12]}, not {sha[:12]}")
            truth, excluded, passages = recall.table_truth(table, [1])
            cls.report[f"numbered_vs_{key}"] = recall.numbered_recall(cls.doc, truth)
            cls.report[f"{key}_excluded"] = excluded
            if name == "tao-measure-lean":
                last = max(p for row in truth.values() for p in row["pages"])
                cls.report["passages_vs_tao_measure_lean"] = recall.passage_recall(
                    cls.doc, [r for r in passages if r["page"] <= last])
        cls.report.update({
            "gate": {k: v["ok"] for k, v in cls.s1["gate"]["checks"].items()},
            "slot_contiguity_missing": cls.s1["gate"]["checks"]["slot_contiguity"]["missing"],
            "errata": cls.s1["gate"]["errata"], "errata_skipped": cls.errata_skipped,
            "stats": cls.s1["stats"],
        })
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
        for name in r["tables_missing"]:
            _say(f"  {name}: no tests/data/m1/{name}.truth.json, not measured")
        for key in ("numbered_vs_measure_theory", "numbered_vs_tao_measure_lean",
                    "passages_vs_tao_measure_lean"):
            m = r.get(key)
            if m is None:
                continue
            _say(f"  {key}: {m['found']}/{m['total']} = {m['recall']}"
                 + (f" ({m['note']})" if m.get("note") else ""))
            for miss in m["misses"]:
                where = miss.get("pages") or miss.get("page")
                _say(f"    miss: p.{where} {miss.get('label') or miss.get('title')}"
                     f" ({miss.get('why', 'no passage on that page or beside its item')})")
        for key in ("measure_theory_excluded", "tao_measure_lean_excluded"):
            for row in r.get(key, []):
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
        if self.report["tables_missing"]:
            self.skipTest(f"no truth table for {', '.join(self.report['tables_missing'])}")
        self.assertGreaterEqual(self.report["offset_confidence"], 0.99)
        for key in ("numbered_vs_measure_theory", "numbered_vs_tao_measure_lean"):
            self.assertGreaterEqual(self.report[key]["recall"], 0.99, key)
        self.assertGreaterEqual(self.report["passages_vs_tao_measure_lean"]["recall"], 0.9)


if __name__ == "__main__":
    unittest.main()
