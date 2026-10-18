"""Locate L1's import contract: a producer's pins, adopted, and the render held to them.

New code, for references/locate-documents.md section 7 ("Adopting a producer's
pins").  A pins row is ``{revision, pageid | title, bytes?, wikidata?,
assessment?, fetch?, layer_sha256?}``: ``fetch`` and ``layer_sha256`` speak of the
row's bytes, ``fetch`` must be the fixed one and the bytes then that fetch's exact
JSON response, ``bytes`` lies inside the pins file's directory, a source is pinned
by one row (judged once a title pin's page is resolved from its revision), and
one row that breaks any of this refuses the whole file.  ``cf sourceset adopt``
records the producer's layer sha in ``adopted_from``, and ``cf sourceset
render`` refuses, by name, a source whose layer is not that sha -- a refusal
that stands until a render writes the layer and that ``cf sourceset status
--verify`` reports.  Every page here is synthetic (the recorded responses of
tests/data/locate/wiki/), and nothing touches the network.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
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
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from sources import render_html as rh  # noqa: E402
from sources import websource  # noqa: E402
from sources.sourceset import Sourceset, SourcesetError  # noqa: E402

FIXTURES = HERE / "data" / "locate" / "wiki"
#: the recorded pages: (pageid, revision) -> the metadata a producer's catalog gives
PAGES = {
    (1001, 2002): {"title": "Widget lemma", "wikidata": "Q99999001",
                   "assessment": {"class": "B", "importance": "High"}},
    (1002, 2003): {"title": "Gadget theorem", "wikidata": "Q99999002",
                   "assessment": {"class": "Start", "importance": "Mid"}},
    (1003, 2004): {"title": "Sprocket", "wikidata": None,
                   "assessment": {"class": "Stub", "importance": "Low"}},
}
OTHER_SHA = hashlib.sha256(b"a layer some other rule gives").hexdigest()


def _scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def cf(root: Path, *argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.main(["--workspace", str(root), *argv])
        except CfError as exc:
            return 2, (f"{out.getvalue()}{err.getvalue()}error: {exc}"
                       + (f"\nhint: {exc.hint}" if exc.hint else ""))
    return code, out.getvalue() + err.getvalue()


def recorded(revision: int) -> bytes:
    """The recorded parse response of ``revision``, as the bytes a producer hands over."""
    data = json.loads((FIXTURES / f"parse-{revision}.json").read_text(encoding="utf-8"))
    return json.dumps(data["json"], ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def layer_sha(raw: bytes) -> str:
    """The sha of the text layer of ``raw`` -- what a conforming producer records."""
    return rh.render(rh.canonical_body(raw)).layer_sha256


def response(revid=2002, pageid=1001, text="<p>The <b>widget lemma</b> holds.</p>",
             title="Widget lemma", **extra) -> bytes:
    parse = {"revid": revid, "pageid": pageid, "title": title, "text": text}
    parse.update(extra)
    for key in [k for k, v in parse.items() if v is None]:
        del parse[key]
    return json.dumps({"parse": parse}).encode("utf-8")


def pin(pageid: int, revision: int, name: str, raw: bytes | None = None, *,
        layer: str | None | bool = True, fetch: dict | None | bool = True, **fields) -> dict:
    """A pins row the way a producer writes it: the fixed fetch and the layer sha filled."""
    row = {"pageid": pageid, "revision": revision, "bytes": name,
           **PAGES.get((pageid, revision), {})}
    if fetch is True:
        row["fetch"] = websource.fetch_block()
    elif fetch is not False:
        row["fetch"] = fetch
    if layer is True:
        row["layer_sha256"] = layer_sha(raw if raw is not None else recorded(revision))
    elif layer is not False:
        row["layer_sha256"] = layer
    row.update(fields)
    return row


# ---------------------------------------------------------------------------
# the row and the entry, as data
# ---------------------------------------------------------------------------

class TestThePinsRow(unittest.TestCase):

    def test_a_producers_row_has_no_problems(self):
        row = pin(1001, 2002, "1001-2002.json")
        self.assertEqual(set(row) | {"pageid", "title"}, set(websource.PIN_FIELDS))
        self.assertEqual(websource.pin_problems(row), [])
        for bare in ({"pageid": 1001, "revision": 2002}, {"title": "Widget lemma", "revision": 1},
                     {"pageid": 1001, "revision": 2002, "bytes": "b.html"}):
            with self.subTest(row=bare):
                self.assertEqual(websource.pin_problems(bare), [])
                self.assertEqual(websource.pin_problems(dict(
                    bare, fetch=None, layer_sha256=None, wikidata=None, assessment=None)), [])

    def test_an_unknown_field_is_refused_not_ignored(self):
        """A misspelt layer sha would otherwise switch the render's check off unseen."""
        row = pin(1001, 2002, "x.json", layer=False, layer_sha=OTHER_SHA, slug="widget-lemma")
        problems = websource.pin_problems(row)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unknown field(s) layer_sha, slug", problems[0])

    def test_missing_and_mistyped_fields(self):
        for row, needle in (
                ({"pageid": 1}, "revision is a positive integer (it is missing)"),
                ({"revision": 5}, "a pageid or a title"),
                ({"pageid": True, "revision": 2}, "pageid is a positive"),
                ({"pageid": 1, "revision": 0}, "revision is a positive"),
                ({"pageid": 1, "revision": 2, "bytes": ""}, "bytes is the path"),
                ({"pageid": 1, "revision": 2, "title": 5}, "title is a string"),
                ({"pageid": 1, "revision": 2, "wikidata": "Widget"}, "not a QID"),
                ({"pageid": 1, "revision": 2, "assessment": "B"}, "assessment"),
                ({"pageid": 1, "revision": 2, "assessment": {"class": "B", "quality": "x"}},
                 "assessment"),
                ({"pageid": 1, "revision": 2, "bytes": "b", "layer_sha256": "ABC"},
                 "layer_sha256 is null or 64"),
                ([1, 2], "a pin is a JSON object")):
            with self.subTest(needle=needle):
                self.assertIn(needle, " ".join(websource.pin_problems(row)))

    def test_fetch_and_the_layer_sha_speak_of_the_bytes(self):
        for fields, needle in (({"fetch": websource.fetch_block()}, "fetch speaks"),
                               ({"layer_sha256": OTHER_SHA}, "layer_sha256 speaks"),
                               ({"fetch": websource.fetch_block(), "layer_sha256": OTHER_SHA},
                                "fetch and layer_sha256 speak")):
            with self.subTest(fields=sorted(fields)):
                problems = websource.pin_problems({"pageid": 1, "revision": 2, **fields})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(needle, problems[0])
                self.assertIn("the pin names none", problems[0])

    def test_bytes_is_a_path_inside_the_pins_files_directory(self):
        for value in ("1001-2002.json", "pages/1001-2002.json", "./a.json", "a~b.json"):
            with self.subTest(value=value):
                self.assertEqual(websource.pin_problems(
                    {"pageid": 1, "revision": 2, "bytes": value}), [])
        for value in ("/abs/1001-2002.json", "../elsewhere/x.json", "pages/../../x.json",
                      "~/h.json", "~producer/h.json", "a\x00b"):
            with self.subTest(value=value):
                problems = websource.pin_problems({"pageid": 1, "revision": 2, "bytes": value})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("is not a path inside the pins file's directory", problems[0])

    def test_the_fetch_must_be_the_fixed_one(self):
        block = websource.fetch_block()
        for fetch in ({"api": "action=parse", "params_sha256": "0" * 64},
                      {"api": "action=query", "params_sha256": block["params_sha256"]},
                      {**block, "oldid": "2002"}, "action=parse"):
            with self.subTest(fetch=fetch):
                problems = websource.pin_problems(pin(1001, 2002, "x.json", fetch=fetch))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("is not the fixed fetch", problems[0])
                self.assertIn(block["params_sha256"], problems[0])

    def test_importance_is_lower_cased_and_class_kept(self):
        self.assertEqual(websource.pin_assessment({"class": "FA", "importance": "Top"}),
                         {"class": "FA", "importance": "top"})
        self.assertEqual(websource.pin_assessment({"class": "B", "importance": None}),
                         {"class": "B", "importance": None})
        self.assertIsNone(websource.pin_assessment(None))

    def test_adopted_from_has_one_shape(self):
        sha = "a" * 64
        self.assertEqual(websource.adopted_from_problems(None), [])
        self.assertEqual(websource.adopted_from_problems(
            {"consumer": "c", "pins_sha256": sha}), [])
        self.assertEqual(websource.adopted_from_problems(
            {"consumer": "c", "pins_sha256": sha, "layer_sha256": sha}), [])
        for adopted, needle in (
                ([], "is an object"),
                ({"consumer": "c"}, "missing pins_sha256"),
                ({"consumer": "c", "pins_sha256": sha, "slug": "x"}, "unknown field(s) slug"),
                ({"consumer": 3, "pins_sha256": sha}, "consumer is a string"),
                ({"consumer": "c", "pins_sha256": sha, "layer_sha256": "x"},
                 "layer_sha256 is 64")):
            with self.subTest(needle=needle):
                self.assertIn(needle, " ".join(websource.adopted_from_problems(adopted)))

    def test_an_entry_whose_layer_is_not_the_producers_is_malformed(self):
        fam = websource.family("enwiki")
        entry = websource.entry(fam, 1001, 2002, origin="adopted",
                                adopted_from={"consumer": "c", "pins_sha256": "a" * 64,
                                              "layer_sha256": "b" * 64})
        entry.update(raw_sha256="c" * 64, html_sha256="d" * 64, layer_sha256="e" * 64,
                     renderer=rh.RENDERER)
        problems = websource.entry_problems(entry)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("is not the producer's", problems[0])
        entry["layer_sha256"] = "b" * 64
        self.assertEqual(websource.entry_problems(entry), [])


# ---------------------------------------------------------------------------
# cf sourceset adopt and render
# ---------------------------------------------------------------------------

class _SourcesetCase(unittest.TestCase):

    def setUp(self):
        self.root = _scratch(self) / "ws"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cf_main.main(["init", str(self.root), "--no-git", "-q"]), 0)
        self.paths = WorkspacePaths(self.root)
        code, out = cf(self.root, "sourceset", "create", "wpm")
        self.assertEqual(code, 0, out)
        self.ss = Sourceset.open(self.paths, "wpm")
        self.pins = _scratch(self)

    def deliver(self, rows: list[dict], files: dict[str, bytes], name: str = "pins.jsonl",
                text: str | None = None) -> Path:
        for fname, data in files.items():
            (self.pins / fname).write_bytes(data)
        path = self.pins / name
        path.write_text(text if text is not None else
                        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                        encoding="utf-8")
        return path

    def adopt(self, rows, files, *extra, **kw):
        return cf(self.root, "sourceset", "adopt", "wpm", "--from",
                  str(self.deliver(rows, files, **kw)), "--consumer", "wikilean", *extra)

    def full_delivery(self, **layers) -> tuple[list[dict], dict[str, bytes]]:
        rows, files = [], {}
        for pageid, revision in PAGES:
            name = f"{pageid}-{revision}.json"
            files[name] = recorded(revision)
            rows.append(pin(pageid, revision, name, files[name],
                            layer=layers.get(f"enwiki-{pageid}-{revision}", True)))
        return rows, files

    def refused_whole(self, code: int, out: str, version: int = 1) -> None:
        """A refused delivery adopts nothing: no version, no source directory."""
        self.assertEqual(code, 2, out)
        self.assertEqual(self.ss.latest().version, version, out)


class TestAdoptingAProducersPins(_SourcesetCase):

    def test_a_delivery_is_adopted_with_its_contract_recorded(self):
        rows, files = self.full_delivery()
        code, out = self.adopt(rows, files)
        self.assertEqual(code, 0, out)
        self.assertIn("3 pin(s) carry the fixed fetch, 3 the producer's layer sha", out)
        pins_sha = hashlib.sha256((self.pins / "pins.jsonl").read_bytes()).hexdigest()
        m = self.ss.latest()
        self.assertEqual(len(m), 3)
        for row in rows:
            sid = f"enwiki-{row['pageid']}-{row['revision']}"
            e = m.get(sid)
            with self.subTest(source=sid):
                self.assertEqual(e["origin"], "adopted")
                self.assertEqual(e["fetch"], websource.fetch_block())
                self.assertEqual(e["adopted_from"], {"consumer": "wikilean",
                                                     "pins_sha256": pins_sha,
                                                     "layer_sha256": row["layer_sha256"]})
                self.assertEqual(e["assessment"]["importance"],
                                 row["assessment"]["importance"].lower())
                self.assertEqual(e["assessment"]["class"], row["assessment"]["class"])
                self.assertEqual((e["title"], e["wikidata"]), (row["title"], row["wikidata"]))
                self.assertEqual(e["raw_sha256"],
                                 hashlib.sha256(files[row["bytes"]]).hexdigest())
                d = self.ss.source_dir(sid)
                self.assertEqual(json.loads((d / "source.json").read_text()), e)
                self.assertEqual(self.ss.source_problems(e), [])
        adopt_state = self.ss.read_state()["commands"]["adopt"]
        self.assertEqual((adopt_state["with_fetch"], adopt_state["with_layer_sha256"],
                          adopt_state["layer_sha256_not_recorded"]), (3, 3, []))

    def test_a_row_without_the_new_fields_adopts_as_before(self):
        code, out = self.adopt([{"pageid": 1001, "revision": 2002, "bytes": "b.json"}],
                               {"b.json": response()})
        self.assertEqual(code, 0, out)
        e = self.ss.latest().get("enwiki-1001-2002")
        self.assertIsNone(e["fetch"])
        self.assertEqual(set(e["adopted_from"]), {"consumer", "pins_sha256"})
        code, out = self.adopt([{"pageid": 1002, "revision": 2003, "bytes": "b.html"}],
                               {"b.html": b"<p>A bare body, as a consumer cached it.</p>"},
                               name="bare.jsonl")
        self.assertEqual(code, 0, out)

    def test_a_fetch_that_is_not_the_fixed_one_refuses_the_delivery(self):
        rows, files = self.full_delivery()
        rows[1]["fetch"] = {"api": "action=parse", "params_sha256": "0" * 64}
        code, out = self.adopt(rows, files)
        self.refused_whole(code, out)
        self.assertIn("pins.jsonl:2: fetch", out)
        self.assertIn("is not the fixed fetch", out)
        self.assertIn("nothing was adopted", out)
        self.assertFalse(self.ss.source_dir("enwiki-1001-2002").exists())
        # the producer corrects the row: the delivery is adopted whole
        code, out = self.adopt(self.full_delivery()[0], files, name="fixed.jsonl")
        self.assertEqual(code, 0, out)
        self.assertIn("adopted 3 revision(s)", out)

    def test_a_row_that_carries_fetch_holds_that_fetchs_json_response(self):
        body = b"<p>The <b>widget lemma</b> as a consumer cached its HTML.</p>"
        cases = [
            (body, "it is not JSON"),
            (response(revid=None), "no integer parse.revid"),
            (response(pageid=None), "no integer parse.pageid"),
            (response(revid="2002"), "no integer parse.revid"),
            (response(revid=2003), "the parse of revision 2003"),
            (response(pageid=1002), "the parse of pageid 1002"),
            (json.dumps({"parse": {"revid": 2002, "pageid": 1001,
                                   "text": {"*": "<p>x</p>"}}}).encode(), "formatversion=1"),
            (b'{"error": {"code": "nosuchrevid"}}', "nosuchrevid"),
        ]
        for raw, needle in cases:
            with self.subTest(needle=needle):
                code, out = self.adopt([pin(1001, 2002, "b.json", raw, layer=False)],
                                       {"b.json": raw})
                self.refused_whole(code, out)
                self.assertIn("pins.jsonl:1", out)
                self.assertIn(needle, out)
        # without fetch, a bare body and a response without its ids are adopted as before
        code, out = self.adopt([pin(1001, 2002, "b.html", body, fetch=False),
                                pin(1002, 2003, "c.json", response(revid=None, pageid=None),
                                    fetch=False)],
                               {"b.html": body, "c.json": response(revid=None, pageid=None)})
        self.assertEqual(code, 0, out)

    def test_an_unknown_field_refuses_the_delivery_and_names_its_line(self):
        rows, files = self.full_delivery()
        rows[2]["layer_sha"] = rows[2].pop("layer_sha256")
        text = json.dumps(rows[0]) + "\n\n" + json.dumps(rows[1]) + "\n" + json.dumps(rows[2])
        code, out = self.adopt([], files, text=text)
        self.refused_whole(code, out)
        self.assertIn("pins.jsonl:4: unknown field(s) layer_sha", out)  # the file's own line
        code, out = self.adopt([], files, text="not json\n" + json.dumps(rows[0]) + "\n")
        self.refused_whole(code, out)
        self.assertIn("pins.jsonl:1: not JSON", out)

    def test_a_pins_file_that_is_not_utf8_is_refused_whole(self):
        rows, files = self.full_delivery()
        path = self.deliver(rows, files)
        path.write_bytes(path.read_bytes() + b'{"title": "caf\xe9"}\n')
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from", str(path))
        self.refused_whole(code, out)
        self.assertIn("a pins file is UTF-8 JSON lines", out)

    def test_the_pins_sha_is_of_the_rows_adopted(self):
        rows, files = self.full_delivery()
        path = self.deliver(rows[:1], files)
        code, out = self.adopt(rows[:1], {})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1001-2002")["adopted_from"]["pins_sha256"],
                         hashlib.sha256(path.read_bytes()).hexdigest())

    def test_a_redelivery_of_held_bytes(self):
        rows, files = self.full_delivery()
        self.assertEqual(self.adopt(rows[:1], files)[0], 0)
        code, out = self.adopt(rows[:1], files, name="again.jsonl")
        self.assertEqual(code, 0, out)
        self.assertIn("1 already held", out)
        version = self.ss.latest().version
        changed = dict(rows[0], layer_sha256=OTHER_SHA)
        code, out = self.adopt([changed], files, name="changed.jsonl")
        self.refused_whole(code, out, version)
        self.assertIn("the producer's text layer of the same bytes changed", out)

    def test_held_bytes_rendered_before_are_checked_against_the_producer(self):
        raw = recorded(2003)
        code, out = self.adopt([pin(1002, 2003, "g.json", raw, layer=False)], {"g.json": raw})
        self.assertEqual(code, 0, out)
        self.assertEqual(cf(self.root, "sourceset", "render", "wpm")[0], 0)
        version = self.ss.latest().version
        code, out = self.adopt([pin(1002, 2003, "g.json", raw, layer=OTHER_SHA)], {},
                               name="wrong.jsonl")
        self.refused_whole(code, out, version)
        self.assertIn("the layer rendered from enwiki-1002-2003's bytes is", out)
        code, out = self.adopt([pin(1002, 2003, "g.json", raw)], {}, name="right.jsonl")
        self.assertEqual(code, 0, out)
        self.assertIn("1 already held", out)
        # adopted before with no producer sha, and adopted_from never changes: said so
        self.assertEqual(self.ss.read_state()["commands"]["adopt"]["layer_sha256_not_recorded"],
                         ["enwiki-1002-2003"])
        self.assertIn("render does not check it", out)

    def test_a_drifted_page_in_a_redelivery_is_refused_by_name(self):
        """An old revision re-parsed later can expand other templates: other bytes."""
        rows, files = self.full_delivery()
        self.assertEqual(self.adopt(rows, files)[0], 0)
        data = json.loads(files["1002-2003.json"])
        data["parse"]["text"] += "<p>A template expanded differently today.</p>"
        files["1002-2003.json"] = json.dumps(data).encode("utf-8")
        rows[1]["layer_sha256"] = layer_sha(files["1002-2003.json"])
        version = self.ss.latest().version
        code, out = self.adopt(rows, files, name="again.jsonl")
        self.refused_whole(code, out, version)
        self.assertIn("again.jsonl:2: enwiki-1002-2003 already holds other bytes", out)

    def test_a_source_is_pinned_by_one_row(self):
        """Two rows for one source that disagree refuse the delivery: neither is the producer's."""
        rows, files = self.full_delivery()
        bare = dict(rows[0])
        del bare["layer_sha256"]
        code, out = self.adopt([bare, rows[1], rows[0]], files)
        self.refused_whole(code, out)
        self.assertIn("pins.jsonl: lines 1, 3 all pin enwiki-1001-2002 and disagree on "
                      "layer_sha256", out)
        # the same row twice is one pin, taken once
        code, out = self.adopt([rows[0], rows[0]], files, name="twice.jsonl")
        self.assertEqual(code, 0, out)
        self.assertIn("lines 1, 2 repeat one row; it is taken once", out)
        e = self.ss.latest().get("enwiki-1001-2002")
        self.assertEqual(e["adopted_from"]["layer_sha256"], rows[0]["layer_sha256"])

    def test_one_row_per_source_is_judged_after_a_title_pins_page_is_resolved(self):
        """A title pin has no source id until its revision names its page."""
        rows, files = self.full_delivery()
        by_title = {k: v for k, v in rows[0].items() if k != "pageid"}
        by_title["layer_sha256"] = OTHER_SHA
        answer = {"query": {"pages": [{"pageid": 1001, "ns": 0, "title": "Widget lemma",
                                       "revisions": [{"revid": 2002}]}]}}
        fixtures = _scratch(self)
        query = {"action": "query", "revids": "2002", "prop": "revisions", "rvprop": "ids",
                 "format": "json", "formatversion": "2"}
        (fixtures / "revids.json").write_text(json.dumps({"query": query, "json": answer}),
                                              encoding="utf-8")
        code, out = self.adopt([rows[0], by_title], files, "--fixtures", str(fixtures))
        self.refused_whole(code, out)
        self.assertIn("lines 1, 2 all pin enwiki-1001-2002 and disagree on layer_sha256, "
                      "pageid", out)
        code, out = self.adopt([by_title], files, "--fixtures", str(fixtures), name="t.jsonl")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1001-2002")["adopted_from"]
                         ["layer_sha256"], OTHER_SHA)

    def test_bytes_the_sourceset_fetched_become_the_consumers_pin(self):
        """A page the consumer pins at a revision the sourceset fetched plans that revision."""
        cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project", "Mathematics",
           "--fixtures", str(FIXTURES), "--limit", "1")
        cf(self.root, "sourceset", "fetch", "wpm", "--fixtures", str(FIXTURES))
        newer = _scratch(self)
        for p in FIXTURES.glob("*.json"):
            shutil.copy(p, newer / p.name)
        info = json.loads((newer / "pageinfo-1001.json").read_text())
        info["json"]["query"]["pages"][0]["revisions"][0]["revid"] = 2010
        (newer / "pageinfo-1001.json").write_text(json.dumps(info))
        code, out = cf(self.root, "sourceset", "enumerate-wikiproject", "wpm", "--project",
                       "Mathematics", "--fixtures", str(newer), "--limit", "1", "--restart")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().preferred()[1001], "enwiki-1001-2010")
        raw = (self.ss.source_dir("enwiki-1001-2002") / "source.raw.html").read_bytes()
        self.assertEqual(raw, recorded(2002))
        code, out = self.adopt([pin(1001, 2002, "w.json", raw)], {"w.json": raw})
        self.assertEqual(code, 0, out)
        self.assertIn("the source is now the consumer's pin", out)
        m = self.ss.latest()
        e = m.get("enwiki-1001-2002")
        self.assertEqual((e["origin"], e["adopted_from"]["consumer"]), ("enumerated", "wikilean"))
        self.assertEqual(e["adopted_from"]["layer_sha256"], layer_sha(raw))
        self.assertEqual(m.preferred()[1001], "enwiki-1001-2002")
        self.assertEqual(self.ss.source_problems(e), [])


class TestADeliveryIsOneDirectory(_SourcesetCase):
    """``bytes`` is relative to the pins file and resolves inside its directory.

    A deliberate change: ``bytes`` could name an absolute path or one under ``~``
    (any per-machine cache) before, so ``pins_sha256`` bound rows whose bytes
    lay anywhere.
    """

    def test_bytes_outside_the_delivery_are_refused(self):
        raw = recorded(2002)
        elsewhere = _scratch(self)
        (elsewhere / "x.json").write_bytes(raw)
        (self.pins / "out.json").symlink_to(elsewhere / "x.json")
        for value, needle in (
                (str(elsewhere / "x.json"), "is not a path inside the pins file's directory"),
                ("../" + elsewhere.name + "/x.json", "is not a path inside"),
                ("~/x.json", "is not a path inside"),
                ("out.json", "resolves to")):
            with self.subTest(value=value):
                code, out = self.adopt([pin(1001, 2002, value, raw)], {}, name="p.jsonl")
                self.refused_whole(code, out)
                self.assertIn("p.jsonl:1", out)
                self.assertIn(needle, out)

    def test_bytes_below_the_pins_file_and_symlinks_inside_it_are_adopted(self):
        (self.pins / "pages").mkdir()
        (self.pins / "pages" / "1001-2002.json").write_bytes(recorded(2002))
        (self.pins / "kept.json").write_bytes(recorded(2003))
        (self.pins / "pages" / "link.json").symlink_to(self.pins / "kept.json")
        code, out = self.adopt([pin(1001, 2002, "pages/1001-2002.json"),
                                pin(1002, 2003, "pages/link.json")], {})
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ss.latest()), 2)

    def test_a_pins_file_reached_through_a_symlinked_directory(self):
        real = _scratch(self)
        (real / "a.json").write_bytes(recorded(2002))
        (real / "pins.jsonl").write_text(json.dumps(pin(1001, 2002, "a.json")) + "\n",
                                         encoding="utf-8")
        link = self.pins / "delivery"
        os.symlink(real, link)
        code, out = cf(self.root, "sourceset", "adopt", "wpm", "--from",
                       str(link / "pins.jsonl"))
        self.assertEqual(code, 0, out)


class TestTheRenderIsHeldToTheProducersLayer(_SourcesetCase):

    def test_a_conforming_delivery_renders_whole(self):
        rows, files = self.full_delivery()
        self.assertEqual(self.adopt(rows, files)[0], 0)
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIn("rendered 3 source(s)", out)
        self.assertNotIn("not the producer's", out)
        for e in self.ss.latest():
            with self.subTest(source=e["source_id"]):
                self.assertEqual(e["layer_sha256"], e["adopted_from"]["layer_sha256"])
                self.assertEqual(self.ss.source_problems(e), [])
        state = self.ss.read_state()
        self.assertEqual(state["commands"]["render"]["not_the_producers_layer"], [])
        self.assertNotIn("parity_refusals", state, "nothing ever refused: no section")
        self.assertEqual(cf(self.root, "sourceset", "status", "wpm", "--verify")[0], 0)

    def test_a_layer_that_is_not_the_producers_is_refused_by_name(self):
        rows, files = self.full_delivery(**{"enwiki-1002-2003": OTHER_SHA})
        self.assertEqual(self.adopt(rows, files)[0], 0)
        for attempt in (1, 2):                      # a re-run refuses it again, unchanged
            with self.subTest(attempt=attempt):
                code, out = cf(self.root, "sourceset", "render", "wpm")
                self.assertEqual(code, 1, out)
                self.assertIn("1 refused, 1 of them because the layer is not the producer's: "
                              "enwiki-1002-2003", out)
                self.assertIn(f"the producer (wikilean) recorded {OTHER_SHA[:12]}", out)
                m = self.ss.latest()
                gadget = m.get("enwiki-1002-2003")
                self.assertEqual((gadget["layer_sha256"], gadget["renderer"]), (None, None))
                files_on_disk = sorted(p.name for p in
                                       self.ss.source_dir("enwiki-1002-2003").iterdir())
                self.assertEqual(files_on_disk, ["body.html", "source.json", "source.raw.html"])
                for sid in ("enwiki-1001-2002", "enwiki-1003-2004"):
                    self.assertEqual(m.get(sid)["layer_sha256"],
                                     m.get(sid)["adopted_from"]["layer_sha256"])
                render_state = self.ss.read_state()["commands"]["render"]
                self.assertEqual(render_state["not_the_producers_layer"], ["enwiki-1002-2003"])
                self.assertEqual(render_state["refused"], 1)
        code, out = cf(self.root, "sourceset", "status", "wpm", "--json")
        self.assertEqual(json.loads(out)["counts"]["rendered"], 2)

    def test_a_refusal_stands_across_a_partial_render_and_status_verify_names_it(self):
        rows, files = self.full_delivery(**{"enwiki-1002-2003": OTHER_SHA})
        self.assertEqual(self.adopt(rows, files)[0], 0)
        code, out = cf(self.root, "sourceset", "status", "wpm", "--json")
        self.assertEqual(json.loads(out)["producer_layer"],
                         {"pinned": 3, "rendered": 0, "refused": [], "waiting": 3})
        self.assertEqual(cf(self.root, "sourceset", "render", "wpm")[0], 1)
        code, out = cf(self.root, "sourceset", "render", "wpm", "--source", "enwiki-1001-2002")
        self.assertEqual(code, 0, out)
        self.assertIn("1 other source(s) still refused by an earlier render", out)
        state = self.ss.read_state()
        self.assertEqual(state["commands"]["render"]["not_the_producers_layer"], [])
        self.assertEqual(state["commands"]["render"]["parity_refusals_standing"], 1)
        standing = self.ss.parity_refusals()
        self.assertEqual(list(standing), ["enwiki-1002-2003"])
        self.assertEqual(standing["enwiki-1002-2003"]["producer_layer_sha256"], OTHER_SHA)
        self.assertEqual(standing["enwiki-1002-2003"]["layer_sha256"], layer_sha(recorded(2003)))
        self.assertEqual(standing["enwiki-1002-2003"]["renderer"], rh.RENDERER)
        state_bytes = self.paths.sourceset_state_json("wpm").read_bytes()
        code, out = cf(self.root, "sourceset", "status", "wpm")
        self.assertEqual(code, 0, out)
        self.assertIn("3 pinned, 2 rendered to it, 1 refused", out)
        self.assertIn("refused by render, the layer not the producer's: enwiki-1002-2003", out)
        self.assertEqual(self.paths.sourceset_state_json("wpm").read_bytes(), state_bytes,
                         "a plain status writes nothing")
        code, out = cf(self.root, "sourceset", "status", "wpm", "--verify")
        self.assertEqual(code, 1, out)
        self.assertIn("enwiki-1002-2003: render refused it", out)
        code, out = cf(self.root, "sourceset", "status", "wpm", "--json", "--verify")
        report = json.loads(out)
        self.assertEqual(report["producer_layer"],
                         {"pinned": 3, "rendered": 2, "refused": ["enwiki-1002-2003"],
                          "waiting": 0})
        self.assertEqual(len(report["problems"]), 1)

    def test_a_refusal_lifts_once_a_render_writes_the_producers_layer(self):
        rows, files = self.full_delivery(**{"enwiki-1002-2003": OTHER_SHA})
        self.assertEqual(self.adopt(rows, files)[0], 0)
        self.assertEqual(cf(self.root, "sourceset", "render", "wpm")[0], 1)
        real = rh.render

        def producers_rule(body):          # a renderer whose layer is the producer's
            r = real(body)
            r.layer = "a layer some other rule gives"
            return r

        with mock.patch.object(rh, "render", producers_rule):
            code, out = cf(self.root, "sourceset", "render", "wpm", "--source",
                           "enwiki-1002-2003")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1002-2003")["layer_sha256"], OTHER_SHA)
        self.assertEqual(self.ss.parity_refusals(), {})
        self.assertEqual(self.ss.read_state()["parity_refusals"], {})

    def test_a_wrong_producer_sha_is_corrected_in_a_new_sourceset(self):
        """A recorded sha never changes, so the corrected delivery says where it goes."""
        rows, files = self.full_delivery(**{"enwiki-1001-2002": OTHER_SHA})
        self.assertEqual(self.adopt(rows[:1], files)[0], 0)
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 1, out)
        self.assertIn("adopt its corrected delivery into a new sourceset", out)
        corrected = self.full_delivery()[0][:1]
        code, out = self.adopt(corrected, files, name="corrected.jsonl")
        self.refused_whole(code, out, self.ss.latest().version)
        self.assertIn("the producer's text layer of the same bytes changed", out)
        self.assertIn("adopt its corrected delivery into a new sourceset (`cf sourceset "
                      "create`)", out)
        self.assertEqual(cf(self.root, "sourceset", "create", "wpm-2")[0], 0)
        code, out = cf(self.root, "sourceset", "adopt", "wpm-2", "--from",
                       str(self.pins / "corrected.jsonl"), "--consumer", "wikilean")
        self.assertEqual(code, 0, out)
        code, out = cf(self.root, "sourceset", "render", "wpm-2")
        self.assertEqual(code, 0, out)
        self.assertEqual(cf(self.root, "sourceset", "status", "wpm-2", "--verify")[0], 0)

    def test_a_seeded_sourceset_carries_the_producer_sha_and_checks_it_again(self):
        """``create --from`` copies adopted_from, so the next renderer is held to it too."""
        rows, files = self.full_delivery(**{"enwiki-1002-2003": OTHER_SHA})
        self.assertEqual(self.adopt(rows, files)[0], 0)
        self.assertEqual(cf(self.root, "sourceset", "render", "wpm")[0], 1)
        code, out = cf(self.root, "sourceset", "create", "wpm-next", "--from", "wpm")
        self.assertEqual(code, 0, out)
        seeded = Sourceset.open(self.paths, "wpm-next")
        self.assertEqual(seeded.latest().get("enwiki-1002-2003")["adopted_from"]["layer_sha256"],
                         OTHER_SHA)
        code, out = cf(self.root, "sourceset", "render", "wpm-next")
        self.assertEqual(code, 1, out)
        self.assertIn("because the layer is not the producer's: enwiki-1002-2003", out)
        self.assertEqual(list(seeded.parity_refusals()), ["enwiki-1002-2003"])

    def test_a_source_with_no_producer_layer_is_rendered_unchecked(self):
        raw = recorded(2004)
        self.assertEqual(self.adopt([pin(1003, 2004, "s.json", raw, layer=False)],
                                    {"s.json": raw})[0], 0)
        code, out = cf(self.root, "sourceset", "render", "wpm")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ss.latest().get("enwiki-1003-2004")["layer_sha256"],
                         layer_sha(raw))

    def test_no_version_can_record_a_layer_that_is_not_the_producers(self):
        rows, files = self.full_delivery(**{"enwiki-1001-2002": OTHER_SHA})
        self.assertEqual(self.adopt(rows[:1], files)[0], 0)
        base = self.ss.latest()
        forged = dict(base.get("enwiki-1001-2002"), layer_sha256=layer_sha(recorded(2002)),
                      renderer=rh.RENDERER)
        with self.assertRaises(SourcesetError) as caught:
            self.ss.write_version(base, [forged])
        self.assertIn("is not the producer's", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
