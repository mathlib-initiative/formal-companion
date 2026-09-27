"""The lean-companion annotation format: schema, invariants, ids, canonical form.

New code, testing docs/annotations.md.  The shipped examples must validate, the
two deliberately broken ones must fail with exactly the message they record in
their own ``x_expected_error`` key, and a shuffled document must canonicalise
back to the same bytes.  When the ``jsonschema`` package is importable the
in-repo evaluator is also checked against it; the suite never needs it.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import hashlib
import io
import json
import random
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from annotations import ids                                        # noqa: E402
from annotations import validate as V                              # noqa: E402
from annotations.schemacheck import SchemaError, Validator         # noqa: E402
from cflib import vocab                                            # noqa: E402

EXAMPLES = SKILL / "examples" / "annotations"
SCHEMA = SKILL / "schema" / "annotations-v1.schema.json"
VALID = ("ledger-graph.json", "mixed.json", "other-media.json", "pdf-region.json", "site-pages.json",
         "tex-lines.json", "web-passage.json")
INVALID = ("invalid-dangling-reference.json", "invalid-reversed-line-range.json")
EPUB_CFI = "http://www.idpf.org/epub/linking/cfi/epub-cfi.html"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def problems(doc: dict) -> list[str]:
    return V.validate(doc)


def warnings(doc: dict) -> list[str]:
    return V.check(doc).warnings


def by_id(records: list[dict], rid: str) -> dict:
    return next(r for r in records if r["id"] == rid)


def walk_selectors(sel: dict):
    yield sel
    for key in ("refinedBy", "startSelector", "endSelector"):
        if key in sel:
            yield from walk_selectors(sel[key])


class TestTheSchemaFile(unittest.TestCase):

    def schema(self) -> dict:
        return json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_it_is_draft_2020_12_with_an_id_that_is_never_fetched(self):
        schema = self.schema()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("/lean-companion/", schema["$id"])
        self.assertIn("rename", schema["description"])
        self.assertIn("never fetched", schema["description"])

    def test_it_is_itself_in_canonical_form(self):
        text = SCHEMA.read_text(encoding="utf-8")
        self.assertEqual(text, V.canonical_dumps(json.loads(text)))

    def test_schema_version_is_a_major_minor_string(self):
        root = self.schema()["properties"]
        self.assertEqual(root["schema_version"]["type"], "string")
        self.assertTrue(re.match(root["schema_version"]["pattern"], vocab.ANNOTATION_SCHEMA_VERSION))
        self.assertEqual(V.schema_path(vocab.ANNOTATION_SCHEMA_MAJOR), SCHEMA)

    def test_the_evaluator_refuses_a_keyword_it_does_not_implement(self):
        with self.assertRaises(SchemaError):
            Validator({"type": "string", "format": "date-time"})
        with self.assertRaises(SchemaError):
            Validator({"$ref": "https://example.org/other.json"})

    def test_every_record_is_closed_and_takes_x_keys(self):
        d = self.schema()["$defs"]
        for name in ("source", "item", "declaration", "artifact", "membership", "link", "provenance",
                     "location", "destination", "snippet", "verdict", "unattributed", "cluster", "change"):
            with self.subTest(record=name):
                self.assertIs(d[name]["additionalProperties"], False)
                self.assertEqual(list(d[name]["patternProperties"]), ["^x_[a-z0-9_]+$"])
                self.assertIn("id", d[name]["required"])
        for name in ("pages", "quote", "signoff", "target", "relations", "resolution", "graph_lock",
                     "input_lock", "page_size", "page_hash", *vocab.ANNOTATION_SELECTOR_TYPES):
            with self.subTest(sub_object=name):
                self.assertIs(d[name]["additionalProperties"], False)
                self.assertNotIn("patternProperties", d[name])

    def test_a_site_page_path_and_a_relative_href_share_one_grammar(self):
        d = self.schema()["$defs"]
        self.assertEqual(d["SitePageSelector"]["properties"]["path"], {"$ref": "#/$defs/site_path"})
        self.assertIn({"$ref": "#/$defs/site_path"}, d["destination"]["properties"]["href"]["anyOf"])
        ours = Validator(self.schema())
        for path in ("Notes/Section_2/", "a/b.html", "a:b.html", "/abs", "../up", "a#frag", "a b"):
            doc = load("site-pages.json")
            loc = next(l for l in doc["locations"] if l["target"]["selector"][0]["type"] == "SitePageSelector")
            loc["target"]["selector"][0]["path"] = path
            dest = doc["destinations"][0]
            dest["href"] = path
            errors = [p for p, _m in ours.errors(doc)]
            as_path = any(".path" in p for p in errors)
            as_href = any(".href" in p for p in errors)
            with self.subTest(path=path):
                self.assertEqual(as_path, as_href, errors)
                self.assertEqual(as_path, path not in ("Notes/Section_2/", "a/b.html"))

    def test_the_root_arrays_are_the_checkers(self):
        root = self.schema()["properties"]
        arrays = {k for k, v in root.items() if v.get("type") == "array"}
        self.assertEqual(arrays, set(V.RECORD_ARRAYS))


class TestOneVocabulary(unittest.TestCase):
    """CLAUDE.md S4: the schema's enums are cflib.vocab's, and documented."""

    def schema(self) -> dict:
        return json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]

    #: Every enum in the schema, by ($defs name, property path), and its vocab set.
    #: A property path is the chain of property names below the definition
    #: (``declarations.verdict``: through an array's ``items``).
    ENUMS = {
            ("source", "kind"): vocab.ANNOTATION_SOURCE_KINDS,
            ("source", "scope"): vocab.ANNOTATION_SOURCE_SCOPES,
            ("source", "role"): vocab.SOURCE_ROLES,
            ("item", "form"): vocab.ANNOTATION_ITEM_FORMS,
            ("item", "id_source"): vocab.ANNOTATION_ITEM_ID_SOURCES,
            ("item", "kind_source"): vocab.ANNOTATION_TEXT_ORIGINS,
            ("item", "title_source"): vocab.ANNOTATION_TEXT_ORIGINS,
            ("item", "coverage"): vocab.ANNOTATION_COVERAGE,
            ("pages", "basis"): vocab.ANNOTATION_PAGE_BASES,
            ("pages", "anchor"): vocab.ANNOTATION_PAGE_ANCHORS,
            ("declaration", "kind"): vocab.ANNOTATION_LEAN_KINDS,
            ("declaration", "id_source"): vocab.ANNOTATION_ID_SOURCES,
            ("declaration", "sorry"): vocab.SORRY_CLASSES,
            ("artifact", "kind"): vocab.ANNOTATION_ARTIFACT_KINDS,
            ("membership", "role"): vocab.ANNOTATION_MEMBERSHIP_ROLES,
            ("link", "role"): vocab.ANNOTATION_LINK_ROLES,
            ("provenance", "actor"): vocab.ANNOTATION_PROVENANCE_ACTORS,
            ("provenance", "method"): vocab.ANNOTATION_PROVENANCE_METHODS,
            ("destination", "site"): vocab.ANNOTATION_DESTINATION_SITES,
            ("destination", "resolution"): vocab.ANNOTATION_DESTINATION_RESOLUTIONS,
            ("coordinate_space", "origin"): vocab.ANNOTATION_COORDINATE_ORIGINS,
            ("coordinate_space", "units"): vocab.ANNOTATION_COORDINATE_UNITS,
            ("coordinate_space", "x_axis"): vocab.ANNOTATION_COORDINATE_X_AXES,
            ("coordinate_space", "y_axis"): vocab.ANNOTATION_COORDINATE_Y_AXES,
            ("coordinate_space", "page_box"): vocab.ANNOTATION_PAGE_BOXES,
            ("text_layer", "unicode"): vocab.ANNOTATION_TEXT_UNICODE,
            ("text_layer", "whitespace"): vocab.ANNOTATION_TEXT_WHITESPACE,
            ("text_layer", "case"): vocab.ANNOTATION_TEXT_CASE,
            ("verdict", "verdict"): vocab.VIEWER_VERDICTS,
            ("verdict", "declarations.verdict"): vocab.VIEWER_VERDICTS,
            ("verdict", "severity"): vocab.VIEWER_SEVERITIES,
            ("verdict", "status"): vocab.VIEWER_STATUS,
            ("verdict", "ledger_verdict"): vocab.LEDGER_VERDICTS,
            ("verdict", "finding_kind"): vocab.FINDING_KINDS,
            ("resolution", "state"): vocab.ANNOTATION_RESOLUTION_STATES,
            ("unattributed", "claim"): vocab.ANNOTATION_UNATTRIBUTED_CLAIMS,
            ("change", "event"): vocab.ANNOTATION_CHANGELOG_EVENTS,
            ("change", "subject_kind"): vocab.ANNOTATION_CHANGELOG_SUBJECTS,
            ("change", "reason"): vocab.ANNOTATION_CHANGELOG_REASONS,
            ("selector", "type"): vocab.ANNOTATION_SELECTOR_TYPES,
    }
    _CONDITIONAL = frozenset({"allOf", "anyOf", "oneOf", "if", "then", "else", "not"})

    def schema_enums(self):
        """Every ``enum`` in the schema: (key, values, conditional).

        ``conditional`` is true for an enum inside allOf/anyOf/oneOf/if/then/
        else/not, which narrows a property the definition declares.
        """
        whole = json.loads(SCHEMA.read_text(encoding="utf-8"))

        def walk(node, owner, props, conditional):
            if isinstance(node, dict):
                if "enum" in node:
                    yield (owner, ".".join(props)), node["enum"], conditional
                for key, child in node.items():
                    if key == "properties" and isinstance(child, dict):
                        for name, sub in child.items():
                            yield from walk(sub, owner, props + [name], conditional)
                    elif key == "$defs":
                        for name, sub in child.items():
                            yield from walk(sub, name, [], False)
                    elif key != "enum":
                        yield from walk(child, owner, props, conditional or key in self._CONDITIONAL)
            elif isinstance(node, list):
                for child in node:
                    yield from walk(child, owner, props, conditional)
        yield from walk(whole, "$root", [], False)

    def test_every_enum_in_the_schema_is_a_vocab_set(self):
        found = list(self.schema_enums())
        self.assertGreater(len(found), 40)
        declared = set()
        for key, values, conditional in found:
            with self.subTest(enum=key, conditional=conditional):
                self.assertIn(key, self.ENUMS, f"schema enum {key} has no cflib.vocab set in ENUMS")
                if conditional:
                    self.assertLessEqual(set(values), set(self.ENUMS[key]))
                else:
                    declared.add(key)
                    self.assertEqual(set(values), set(self.ENUMS[key]))
        self.assertEqual(declared, set(self.ENUMS), "an ENUMS row names no enum in the schema")

    def test_the_other_closed_sets_are_the_vocab_sets(self):
        d = self.schema()
        self.assertEqual(set(d["relations"]["properties"]), set(vocab.ANNOTATION_ITEM_RELATIONS))
        root = json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]
        self.assertEqual(root["schema"]["const"], vocab.ANNOTATION_SCHEMA)
        self.assertEqual(set(vocab.ANNOTATION_ORIGIN_Y_AXIS), set(vocab.ANNOTATION_COORDINATE_ORIGINS))
        self.assertEqual(set(vocab.ANNOTATION_ORIGIN_Y_AXIS.values()), set(vocab.ANNOTATION_COORDINATE_Y_AXES))

    def test_a_walker_that_misses_an_enum_would_fail(self):
        """The walk sees a nested enum (inside ``items``) and a conditional one."""
        keys = {(key, conditional) for key, _v, conditional in self.schema_enums()}
        self.assertIn((("verdict", "declarations.verdict"), False), keys)
        self.assertIn((("declaration", "kind"), True), keys)

    def test_resolution_states_are_section_1_5_plus_open(self):
        self.assertEqual(vocab.ANNOTATION_RESOLUTION_STATES,
                         {"open"} | vocab.STATIC_RESOLUTIONS
                         | {p.rstrip(":") for p in vocab.RESOLUTION_PREFIXES})

    def test_conforms_to_and_relation_are_open_strings(self):
        d = self.schema()
        self.assertNotIn("enum", d["FragmentSelector"]["properties"]["conformsTo"])
        self.assertNotIn("conformsTo", d["FragmentSelector"]["required"])
        self.assertNotIn("enum", d["link"]["properties"]["relation"])
        self.assertEqual(set(V.CONFORMS), set(vocab.ANNOTATION_CONFORMS_TO))

    def test_every_selector_type_has_a_definition(self):
        d = self.schema()
        for name in vocab.ANNOTATION_SELECTOR_TYPES:
            with self.subTest(selector=name):
                self.assertEqual(d[name]["properties"]["type"]["const"], name)

    def test_every_value_is_documented(self):
        text = (SKILL / "references" / "vocabulary.md").read_text(encoding="utf-8")
        section = text.split("### 1.10 ", 1)[1].split("\n---", 1)[0]
        for name in dir(vocab):
            value = getattr(vocab, name)
            if not name.startswith("ANNOTATION_") or not isinstance(value, frozenset):
                continue
            for entry in value:
                with self.subTest(enum=name, value=entry):
                    self.assertIn(f"`{entry}`", section)
        for reused in ("SOURCE_ROLES", "LEDGER_VERDICTS", "FINDING_KINDS", "VIEWER_VERDICTS",
                       "VIEWER_SEVERITIES", "VIEWER_STATUS", "SORRY_CLASSES"):
            with self.subTest(reused=reused):
                self.assertIn(reused, section)


class TestOneIdGrammar(unittest.TestCase):
    """ids.py (the writer) and validate.py (the checker) share cflib.vocab's grammar."""

    def test_both_sides_compile_the_vocab_pattern(self):
        self.assertIn(vocab.DECLARED_ID_PATTERN, ids.DECLARED_ID.pattern)
        self.assertIn(vocab.HASH_ID_PATTERN, ids.HASH_ID.pattern)
        self.assertIs(V._DECLARED_ID, ids.DECLARED_ID)
        self.assertIs(V._HASH_ID, ids.HASH_ID)

    def test_capitals_are_allowed_and_hash_shaped_slugs_are_reserved(self):
        for good in ("example:L1-seminorm-ae-laws", "instance:Foo.bar_baz", "example:0123456789abc"):
            with self.subTest(id=good):
                self.assertTrue(ids.DECLARED_ID.match(good))
        for bad in ("example:0123456789ab", "example:-x", "theorem:foo", "example:a b", "Foo.bar"):
            with self.subTest(id=bad):
                self.assertFalse(ids.DECLARED_ID.match(bad))


class TestTheExamples(unittest.TestCase):

    def test_the_valid_examples_validate_in_canonical_form(self):
        for name in VALID:
            with self.subTest(example=name):
                text = (EXAMPLES / name).read_text(encoding="utf-8")
                self.assertEqual(V.validate(V.loads(text), text=text, canonical=True), [])

    def test_the_only_warning_is_the_unknown_conforms_to(self):
        for name in VALID:
            with self.subTest(example=name):
                found = warnings(load(name))
                if name == "other-media.json":
                    self.assertEqual(len(found), 1)
                    self.assertIn("is not an IRI this checker knows", found[0])
                else:
                    self.assertEqual(found, [])

    def test_each_invalid_example_fails_with_exactly_its_recorded_error(self):
        for name in INVALID:
            with self.subTest(example=name):
                doc = load(name)
                self.assertEqual(problems(doc), [doc["x_expected_error"]])

    def test_every_record_type_appears(self):
        docs = [load(n) for n in VALID]
        for key in V.RECORD_ARRAYS:
            with self.subTest(array=key):
                self.assertTrue(any(d.get(key) for d in docs), key)
        ledger = load("ledger-graph.json")
        self.assertIn("graph", ledger["companion"])
        self.assertTrue(any("inputs" in p for p in ledger["provenance"]))
        values = lambda array, field: {r.get(field) for d in docs for r in d.get(array, [])}
        self.assertEqual(values("items", "id_source"), set(vocab.ANNOTATION_ITEM_ID_SOURCES))
        self.assertEqual(values("items", "form"), set(vocab.ANNOTATION_ITEM_FORMS))
        self.assertEqual(values("declarations", "id_source"), set(vocab.ANNOTATION_ID_SOURCES))
        self.assertLessEqual({"field", "projection", "structure", "class", "example", "instance"},
                             values("declarations", "kind"))
        self.assertIn(True, values("declarations", "unlocated"))
        self.assertEqual(values("artifacts", "kind") - {None}, {"module_doc", "check", "figure"})
        self.assertEqual(values("memberships", "role"), set(vocab.ANNOTATION_MEMBERSHIP_ROLES))
        self.assertEqual(values("changelog", "event"), set(vocab.ANNOTATION_CHANGELOG_EVENTS))
        self.assertTrue({"exact", "generalization", "special_case", "invocation", "related"}
                        <= values("links", "relation"))
        self.assertIn(True, values("items", "cluster_fallback"))
        fallback = next(i for d in docs for i in d["items"] if i.get("cluster_fallback"))
        self.assertIn("coverage", fallback, "the curation's class sits beside the fallback")
        self.assertTrue(any("page_hashes" in s for d in docs for s in d["sources"]))
        self.assertTrue(any("source" in entry for d in docs for p in d["provenance"]
                            for entry in p.get("inputs", [])))
        self.assertTrue(any(i["id_source"] == "quote_hash" and "quote" not in i for d in docs for i in d["items"]),
                        "a quote_hash passage whose quote is its location's")
        ordinal = [c for c in ledger["changelog"] if c["event"] == "ordinal_assigned"]
        self.assertTrue(ordinal and all("file" in c for c in ordinal))
        retired = {c["subject"] for c in ledger["changelog"] if c["event"] == "retired"}
        self.assertTrue(any(c["subject"] in retired for c in ordinal), "a record outlives its retired id")
        self.assertTrue(any("resolution" not in v for d in docs for v in d.get("verdicts", [])),
                        "an open verdict carries no resolution")
        self.assertTrue(any("artifact" in r for d in docs for r in d.get("destinations", [])))
        rels = {k for d in docs for i in d["items"] for k in i.get("relations", {})}
        self.assertEqual(rels, set(vocab.ANNOTATION_ITEM_RELATIONS))
        verdict = load("ledger-graph.json")["verdicts"][0]
        self.assertTrue({"evidence", "locations"} <= set(verdict))
        self.assertTrue({"issue", "commits", "replaced_by"} <= set(verdict["resolution"]))

    def test_every_location_kind_appears(self):
        docs = [load(n) for n in VALID]
        families = {V.source_family(s) for d in docs for s in d["sources"]}
        self.assertEqual(families, {"pdf", "html", "text", "generic"})
        kinds = {s["kind"] for d in docs for s in d["sources"]}
        self.assertEqual(kinds, set(vocab.ANNOTATION_SOURCE_KINDS))
        seen, conforms, regions = set(), set(), set()
        for d in docs:
            for loc in d.get("locations", []):
                for top in loc["target"]["selector"]:
                    for sel in walk_selectors(top):
                        seen.add(sel["type"])
                        if "conformsTo" in sel:
                            conforms.add(sel["conformsTo"])
                        if sel["type"] == "PdfRegionSelector":
                            regions.add("rect" if "rect" in sel else "quads")
                            regions.add(sel["coordinate_space"]["units"])
                        if sel["type"] == "FragmentSelector" and "refinedBy" in sel \
                                and sel["refinedBy"]["type"] == "RangeSelector":
                            regions.add("range-in-page")
        self.assertTrue({"FragmentSelector", "TextQuoteSelector", "TextPositionSelector", "CssSelector",
                         "RangeSelector", "PdfRegionSelector", "SitePageSelector"} <= seen, seen)
        self.assertEqual(conforms - {EPUB_CFI}, set(vocab.ANNOTATION_CONFORMS_TO))
        self.assertEqual(regions, {"rect", "quads", "normalized", "pt", "range-in-page"})
        site = load("site-pages.json")
        unhosted = [s for s in site["sources"] if s["kind"] == "web"]
        self.assertTrue(unhosted and all("url" not in s and s["scope"] == "site" for s in unhosted))
        page_only = [loc for d in docs for loc in d.get("locations", [])
                     if "pages" in loc and all(s["type"] == "FragmentSelector" and "refinedBy" not in s
                                               for s in loc["target"]["selector"])]
        self.assertTrue(page_only)
        mixed = load("mixed.json")
        passages = [i for i in mixed["items"] if i["form"] == "passage"]
        self.assertGreaterEqual(len(passages), 2)
        pages = [loc["target"]["selector"][0].get("page") for loc in mixed["locations"]]
        self.assertGreater(len(pages), len(set(pages)), "no page carries two items")

    def test_the_examples_quote_no_real_source(self):
        """POLICY.md: examples are synthetic; every source title says so."""
        for name in VALID + INVALID:
            for source in load(name)["sources"]:
                if source["kind"] != "lean":
                    with self.subTest(example=name, source=source["id"]):
                        self.assertIn("synthetic", source["title"])
                for url in [source.get("url")] + source.get("alternate_urls", []):
                    if url:
                        self.assertRegex(url, r"^https://[a-z.]*example\.org/")


class TestTheCli(unittest.TestCase):

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = V.main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def test_valid_invalid_and_unreadable(self):
        status, out, _ = self.run_cli(*(str(EXAMPLES / n) for n in VALID), "--canonical")
        self.assertEqual(status, 0, out)
        self.assertEqual(out.count(": ok ("), len(VALID))
        self.assertEqual(out.count(": warning: "), 1)
        bad = EXAMPLES / INVALID[0]
        status, out, _ = self.run_cli(str(bad))
        self.assertEqual(status, 1)
        self.assertIn(load(INVALID[0])["x_expected_error"], out)
        with tempfile.TemporaryDirectory() as tmp:
            dup = Path(tmp) / "dup.json"
            dup.write_text('{"schema": 1, "schema": 2}', encoding="utf-8")
            status, _, err = self.run_cli(str(dup))
            self.assertEqual(status, 2)
            self.assertIn("duplicate key", err)
            nan = Path(tmp) / "nan.json"
            nan.write_text('{"x": NaN}', encoding="utf-8")
            self.assertEqual(self.run_cli(str(nan))[0], 2)

    def test_print_canonical_writes_the_canonical_bytes(self):
        name = "mixed.json"
        doc = load(name)
        with tempfile.TemporaryDirectory() as tmp:
            messy = Path(tmp) / name
            messy.write_text(json.dumps(doc, indent=4), encoding="utf-8")
            status, out, _ = self.run_cli(str(messy), "--canonical")
            self.assertEqual(status, 1)
            self.assertIn("not in canonical form", out)
            status, out, _ = self.run_cli(str(messy), "--print-canonical")
            self.assertEqual(status, 0)
            self.assertEqual(out, (EXAMPLES / name).read_text(encoding="utf-8"))


class TestCanonicalForm(unittest.TestCase):
    """Section 7: RFC 8785 key order and numbers, independent of Python's float spelling."""

    def test_numbers_follow_ecmascript(self):
        cases = {
            1.0: "1", 0.00001: "0.00001", 0.247647: "0.247647", -0.0: "0", 0: "0",
            1e21: "1e+21", 1e20: "100000000000000000000", 1e-7: "1e-7", 1e-6: "0.000001",
            333333333.33333329: "333333333.3333333", 1e30: "1e+30", 4.50: "4.5", 2e-3: "0.002",
            1e-27: "1e-27", 5e-324: "5e-324", 1.7976931348623157e308: "1.7976931348623157e+308",
            -1.5: "-1.5", 612: "612", 2 ** 53: "9007199254740992", 2 ** 60: "1152921504606847000",
            0.1 + 0.2: "0.30000000000000004", 123e-20: "1.23e-18",
        }
        for value, want in cases.items():
            with self.subTest(value=value):
                self.assertEqual(V.jcs_number(value), want)
        with self.assertRaises(ValueError):
            V.jcs_number(float("nan"))
        with self.assertRaises(TypeError):
            V.jcs_number(True)

    def test_python_and_canonical_spellings_differ_where_the_critique_found(self):
        self.assertEqual(json.dumps(1.0), "1.0")
        self.assertEqual(json.dumps(0.00001), "1e-05")
        self.assertEqual(V.canonical_dumps({"a": 1.0, "b": 0.00001, "c": 0.247647}),
                         '{\n  "a": 1,\n  "b": 0.00001,\n  "c": 0.247647\n}\n')

    def test_keys_sort_by_utf16_code_units(self):
        text = V.canonical_dumps({"": 1, "\U0001f600": 2, "b": 3, "a": 4})
        order = [line.split('"')[1] for line in text.splitlines()[1:-1]]
        self.assertEqual(order, ["a", "b", "\U0001f600", ""])

    def test_records_and_id_lists_sort_by_utf16_code_units_too(self):
        astral, bmp = "\U0001d400", "\uff21"            # MATHEMATICAL BOLD CAPITAL A, FULLWIDTH A
        self.assertLess(bmp, astral)                       # code point order
        self.assertLess(V.order_key(astral), V.order_key(bmp))   # UTF-16 order, as JavaScript sorts
        doc = load("pdf-region.json")
        base = by_id(doc["items"], "definition-1.1")
        doc["items"] += [dict(base, id=f"item-{c}", id_source="declared", number=f"9.{n}", legacy_ids=[bmp, astral])
                         for n, c in ((1, bmp), (2, astral))]
        doc = V.canonicalize(doc)
        order = [i["id"] for i in doc["items"]]
        self.assertLess(order.index(f"item-{astral}"), order.index(f"item-{bmp}"))
        self.assertEqual(doc["items"][order.index(f"item-{bmp}")]["legacy_ids"], [astral, bmp])
        self.assertEqual(problems(doc), [])
        i, j = order.index(f"item-{astral}"), order.index(f"item-{bmp}")
        doc["items"][i], doc["items"][j] = doc["items"][j], doc["items"][i]
        self.assertProblem(doc, "is not sorted by id (UTF-16 code units)")

    def assertProblem(self, doc: dict, fragment: str) -> None:
        found = problems(doc)
        self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_strings_are_escaped_as_json_stringify_does(self):
        self.assertEqual(V.canonical_dumps(["\u001f\n\t\"\\/é "]),
                         '[\n  "\\u001f\\n\\t\\"\\\\/é "\n]\n')
        self.assertEqual(V.canonical_dumps({"a": [], "b": {}, "c": None, "d": True}),
                         '{\n  "a": [],\n  "b": {},\n  "c": null,\n  "d": true\n}\n')

    def shuffled(self, doc: dict, seed: int) -> dict:
        rng = random.Random(seed)

        def mix(value):
            if isinstance(value, dict):
                keys = list(value)
                rng.shuffle(keys)
                return {k: mix(value[k]) for k in keys}
            if isinstance(value, list):
                return [mix(v) for v in value]
            return value
        out = mix(copy.deepcopy(doc))
        for key in V.RECORD_ARRAYS:
            if key in out:
                rng.shuffle(out[key])
                for rec in out[key]:
                    for dotted in V.SET_FIELDS.get(key, ()):
                        values = V._get(rec, dotted)
                        if isinstance(values, list):
                            rng.shuffle(values)
                    for field_name, _key in V.KEYED_LISTS.get(key, ()):
                        if isinstance(rec.get(field_name), list):
                            rng.shuffle(rec[field_name])
        return out

    def test_a_shuffled_document_canonicalises_to_the_same_bytes(self):
        for name in VALID:
            original = (EXAMPLES / name).read_text(encoding="utf-8")
            for seed in range(5):
                with self.subTest(example=name, seed=seed):
                    messy = self.shuffled(V.loads(original), seed)
                    again = V.canonical_dumps(V.canonicalize(messy))
                    self.assertEqual(again, original)
                    self.assertEqual(V.canonical_dumps(V.canonicalize(V.loads(again))), again)

    def test_a_float_that_is_an_integer_is_written_without_a_point(self):
        doc = load("ledger-graph.json")
        loc = by_id(doc["locations"], "definition-1.1@p007-2")
        loc["target"]["selector"][0]["rect"]["x0"] = 90.0
        self.assertEqual(V.canonical_dumps(doc), (EXAMPLES / "ledger-graph.json").read_text(encoding="utf-8"))

    def test_selector_alternatives_keep_their_order(self):
        doc = load("pdf-region.json")
        loc = by_id(doc["locations"], "definition-1.1@p007")
        loc["target"]["selector"].reverse()
        before = [s["value"] for s in loc["target"]["selector"]]
        after = by_id(V.canonicalize(doc)["locations"], "definition-1.1@p007")["target"]["selector"]
        self.assertEqual([s["value"] for s in after], before)

    def test_unsorted_arrays_and_nested_sets_are_reported(self):
        doc = load("pdf-region.json")
        doc["items"].reverse()
        self.assertTrue(any("is not sorted by id" in p for p in problems(doc)))
        doc = load("ledger-graph.json")
        doc["verdicts"][0]["resolution"]["commits"].reverse()
        self.assertTrue(any("resolution.commits: must be sorted" in p for p in problems(doc)))
        doc = load("ledger-graph.json")
        doc["provenance"][1]["inputs"].reverse()
        self.assertTrue(any("inputs: must be sorted by path" in p for p in problems(doc)))


class TestTheInvariants(unittest.TestCase):
    """One broken copy of a valid example per rule, each failing for its reason."""

    def assertProblem(self, doc: dict, fragment: str) -> None:
        found = problems(doc)
        self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def assertValid(self, doc: dict) -> None:
        self.assertEqual(problems(doc), [])

    def item(self, doc: dict, rid: str) -> dict:
        return by_id(doc["items"], rid)

    # -- items -----------------------------------------------------------------
    def test_an_item_needs_an_anchor(self):
        doc = load("web-passage.json")
        doc["locations"] = []
        self.assertProblem(doc, "item 'passage-lead-definition' is anchored nowhere")

    def test_a_passage_needs_a_title_and_takes_no_number_or_sub(self):
        doc = load("web-passage.json")
        del self.item(doc, "passage-lead-definition")["title"]
        self.assertProblem(doc, "missing required key 'title'")
        doc = load("web-passage.json")
        self.item(doc, "passage-lead-definition")["number"] = "1.2"
        self.assertProblem(doc, "(not)")
        doc = load("web-passage.json")
        self.item(doc, "passage-lead-definition")["sub"] = "ii"
        self.assertProblem(doc, "(not)")

    def test_form_is_a_required_discriminator(self):
        doc = load("pdf-region.json")
        del self.item(doc, "definition-1.1")["form"]
        self.assertProblem(doc, "missing required key 'form'")
        doc = load("pdf-region.json")
        self.item(doc, "definition-1.1")["form"] = "passage"
        self.assertTrue(problems(doc))

    def test_many_items_may_share_a_page(self):
        self.assertValid(load("mixed.json"))

    def test_links_without_a_key_need_coverage(self):
        doc = load("mixed.json")
        del self.item(doc, "passage-remark-unions")["coverage"]
        self.assertProblem(doc, "has links but no key link, so it must carry coverage")

    def test_attributed_needs_a_key(self):
        doc = load("mixed.json")
        doc["links"] = [l for l in doc["links"] if l["id"] != "definition-1.1/key/Notes.Box"]
        self.item(doc, "definition-1.1")["coverage"] = "partial"
        self.assertProblem(doc, "has attributed links and no key link")

    def test_not_formalized_excludes_a_key_link(self):
        doc = load("pdf-region.json")
        self.item(doc, "definition-1.1")["coverage"] = "not_formalized"
        self.assertProblem(doc, "is not_formalized but has a key link")

    def test_cluster_fallback_needs_a_populated_cluster_and_no_key(self):
        doc = load("ledger-graph.json")
        self.assertTrue(self.item(doc, "remark-1.3")["cluster_fallback"])
        self.item(doc, "remark-1.3")["clusters"] = []
        self.assertProblem(doc, "falls back to its clusters but names no cluster")
        doc = load("ledger-graph.json")
        self.item(doc, "definition-1.1")["cluster_fallback"] = True
        self.assertProblem(doc, "falls back to its clusters but has a key link")
        doc = load("ledger-graph.json")
        self.item(doc, "remark-1.3")["clusters"] = ["node-unions"]
        self.assertValid(doc)
        doc["memberships"] = [m for m in doc["memberships"] if m["cluster"] != "node-unions"]
        self.assertProblem(doc, "none of ['node-unions'] has a membership")
        del doc["memberships"]
        self.assertValid(doc)          # no memberships[]: the document does not say

    def test_cluster_only_is_no_longer_a_coverage_value(self):
        doc = load("ledger-graph.json")
        self.item(doc, "remark-1.3")["coverage"] = "cluster_only"
        self.assertProblem(doc, "is not one of")

    def test_numbered_identity_is_unique(self):
        doc = load("pdf-region.json")
        doc["items"].append(dict(self.item(doc, "definition-1.1"), id="definition-1.1-again",
                                 id_source="declared"))
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "the same numbered item as")

    def test_item_relations_resolve_and_are_not_reflexive(self):
        doc = load("mixed.json")
        self.item(doc, "passage-boxes-motivation")["relations"]["near"] = ["passage-boxes-motivation"]
        self.assertProblem(doc, "an item is not related to itself")
        doc = load("mixed.json")
        self.item(doc, "passage-boxes-motivation")["relations"]["near"] = ["definition-9.9"]
        self.assertProblem(doc, "'definition-9.9' is not an id in items[]")

    # -- item ids (I12) --------------------------------------------------------
    def test_a_label_id_is_kind_and_number(self):
        doc = load("pdf-region.json")
        doc["items"][0]["id_source"] = "label"
        item = by_id(doc["items"], "definition-1.1")
        item["number"] = "1.9"
        self.assertProblem(doc, "a label id is <kind>-<number>[-<sub>], here 'definition-1.9'")

    def test_a_quote_hash_id_is_the_quote_hash(self):
        doc = load("web-passage.json")
        item = next(i for i in doc["items"] if i["id_source"] == "quote_hash")
        self.assertEqual(item["id"], ids.passage_quote_id(item["quote"]["exact"]))
        item["quote"]["exact"] += " again"
        self.assertProblem(doc, "a quote_hash id is passage-<8 hex of the normalised quote> (item.quote)")

    def test_a_quote_hash_id_may_come_from_the_first_locations_quote(self):
        doc = load("site-pages.json")
        item = next(i for i in doc["items"] if i["id_source"] == "quote_hash")
        self.assertNotIn("quote", item)
        loc = by_id(doc["locations"], f"{item['id']}@p022")
        quote = loc["target"]["selector"][0]["refinedBy"]
        self.assertEqual(item["id"], ids.passage_quote_id(quote["exact"]))
        quote["exact"] = "every  null set is contained in a Borel null set"   # the normaliser collapses it
        self.assertValid(doc)
        quote["exact"] += " again"
        self.assertProblem(doc, f"(the quote of location '{item['id']}@p022')")
        loc["target"]["selector"][0] = {"type": "FragmentSelector", "conformsTo": vocab.CONFORMS_TO_PDF,
                                        "value": "page=22"}
        self.assertProblem(doc, "a quote_hash id needs item.quote, or a TextQuoteSelector on the item's "
                                "first location")

    def test_the_same_quote_twice_is_one_passage_or_a_duplicate_id(self):
        doc = load("site-pages.json")
        item = next(i for i in doc["items"] if i["id_source"] == "quote_hash")
        doc["items"].append(dict(item, title="Another title for the same passage"))
        self.assertProblem(doc, f"duplicate id {item['id']!r} in items[]")

    def test_a_page_title_hash_id_needs_a_printed_page_and_no_quote(self):
        doc = load("pdf-region.json")
        item = next(i for i in doc["items"] if i["id_source"] == "page_title_hash")
        self.assertEqual(item["id"], ids.passage_page_id(item["pages"]["first"], item["title"]))
        item["title"] += "!"
        self.assertProblem(doc, "a page_title_hash id is passage-p<printed page>-<8 hex")
        doc = load("pdf-region.json")
        item = next(i for i in doc["items"] if i["id_source"] == "page_title_hash")
        item["quote"] = {"exact": "a quote"}
        self.assertProblem(doc, "takes the quote_hash id")
        doc = load("pdf-region.json")
        item = next(i for i in doc["items"] if i["id_source"] == "page_title_hash")
        item["pages"] = {"first": 10, "basis": "physical"}
        self.assertValid(doc)
        del doc["sources"][0]["printed_page_offset"]
        self.assertProblem(doc, "page_title_hash needs a printed page")

    def test_a_declared_item_id_may_not_take_a_mechanical_shape(self):
        doc = load("mixed.json")
        item = self.item(doc, "passage-remark-unions")
        item["id"] = "passage-0123abcd"
        for loc in doc["locations"]:
            loc["items"] = ["passage-0123abcd" if i == "passage-remark-unions" else i for i in loc["items"]]
        for link in doc["links"]:
            if link["item"] == "passage-remark-unions":
                link["item"] = "passage-0123abcd"
        doc["clusters"][0]["items"] = sorted("passage-0123abcd" if i == "passage-remark-unions" else i
                                             for i in doc["clusters"][0]["items"])
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "has the shape of a mechanical passage id")

    # -- locations and selectors (I7) -------------------------------------------
    def test_a_region_must_agree_with_the_page_it_refines(self):
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][0]["refinedBy"]["page"] = 8
        self.assertProblem(doc, "disagrees with the refined page=7")

    def test_a_region_stays_on_its_page(self):
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][0]["refinedBy"]["rect"]["x1"] = 1.5
        self.assertProblem(doc, "coordinates must lie in [0, 1] for units normalized")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][0]["refinedBy"]["rect"]["x1"] = 1.0
        self.assertValid(doc)
        doc = load("ledger-graph.json")
        by_id(doc["locations"], "definition-1.1@p007-2")["target"]["selector"][0]["rect"]["x1"] = 700
        self.assertProblem(doc, "the rect leaves the 612 x 792 pt page")

    def test_page_beyond_the_source(self):
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][1]["value"] = "page=41"
        self.assertProblem(doc, "page=41 is past the source's 40 physical pages")

    def test_location_pages_agree_with_the_selectors(self):
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["pages"] = {"first": 8, "basis": "physical"}
        self.assertProblem(doc, "the selectors name physical page(s) [7], outside 8..8")
        doc = load("web-passage.json")
        doc["locations"][0]["pages"] = {"first": 1, "basis": "physical"}
        self.assertProblem(doc, "pages are for a pdf source")

    def test_a_pdf_offset_needs_an_extractor_and_a_page(self):
        doc = load("pdf-region.json")
        del doc["sources"][0]["text_layer"]["extractor"]
        del doc["sources"][0]["text_layer"]["version"]
        self.assertProblem(doc, "offsets into a PDF need the source's text_layer.extractor and version "
                                "('notes-pdf' declares no extractor or version)")
        doc = load("pdf-region.json")
        del doc["sources"][0]["text_layer"]["version"]
        self.assertProblem(doc, "$.sources[0].text_layer: is missing required key 'version'")
        doc = load("tex-lines.json")
        doc["sources"][0]["text_layer"]["version"] = "1.0"
        self.assertProblem(doc, "$.sources[0].text_layer: is missing required key 'extractor'")
        doc = load("pdf-region.json")
        loc = by_id(doc["locations"], "exercise-1.4@p010")
        loc["target"]["selector"][1] = {"type": "TextPositionSelector", "start": 1, "end": 2}
        self.assertProblem(doc, "offsets into a PDF count within one page")

    def test_a_range_may_refine_a_page(self):
        doc = load("pdf-region.json")
        sel = by_id(doc["locations"], "lemma-1.2@p009-proof-step")["target"]["selector"][0]
        self.assertEqual(sel["refinedBy"]["type"], "RangeSelector")
        self.assertValid(doc)
        sel["refinedBy"]["endSelector"] = {"type": "FragmentSelector", "conformsTo": vocab.CONFORMS_TO_PDF,
                                           "value": "page=10"}
        self.assertProblem(doc, "startSelector is TextQuoteSelector and endSelector is FragmentSelector")

    def test_a_selector_the_source_kind_cannot_use(self):
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"].append(
            {"type": "CssSelector", "value": "p"})
        self.assertProblem(doc, "CssSelector does not apply to a pdf source")
        doc = load("tex-lines.json")
        doc["locations"][0]["target"]["selector"][0]["conformsTo"] = vocab.CONFORMS_TO_PDF
        self.assertProblem(doc, "a tex source takes http://tools.ietf.org/rfc/rfc5147")
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][1]["conformsTo"] = \
            vocab.CONFORMS_TO_HTML
        self.assertProblem(doc, "a pdf source takes http://tools.ietf.org/rfc/rfc3778 or "
                                "http://tools.ietf.org/rfc/rfc8118")

    def test_conforms_to_is_open_with_a_warning(self):
        doc = load("tex-lines.json")
        sel = doc["locations"][0]["target"]["selector"][0]
        sel["conformsTo"] = "https://example.org/some-grammar"
        sel["value"] = "anything at all"
        self.assertValid(doc)
        self.assertTrue(any("is not an IRI this checker knows" in w for w in warnings(doc)))
        del sel["conformsTo"]
        self.assertValid(doc)
        self.assertTrue(any("absent (W3C: SHOULD have one)" in w for w in warnings(doc)))

    def test_rfc3778_is_a_synonym_and_rfc3236_names_html_ids(self):
        doc = load("pdf-region.json")
        for loc in doc["locations"]:
            for sel in loc["target"]["selector"]:
                if sel.get("conformsTo") == vocab.CONFORMS_TO_PDF:
                    sel["conformsTo"] = vocab.CONFORMS_TO_PDF_RFC3778
        self.assertValid(doc)
        doc = load("site-pages.json")
        inner = by_id(doc["locations"], "Notes.sum_le@site")["target"]["selector"][0]["refinedBy"]
        self.assertEqual(inner["conformsTo"], vocab.CONFORMS_TO_HTML)
        inner["value"] = "two words"
        self.assertProblem(doc, "is not an HTML element id")

    def test_zoom_takes_one_or_three_numbers(self):
        doc = load("pdf-region.json")
        sel = by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][1]
        for value in ("page=7&zoom=100", "page=7&zoom=100,0,0"):
            sel["value"] = value
            self.assertValid(doc)
        sel["value"] = "page=7&zoom=100,0"
        self.assertProblem(doc, "zoom= takes 1 or 3 numbers")

    def test_media_fragments_take_time_as_well_as_xywh(self):
        doc = load("other-media.json")
        sel = by_id(doc["locations"], "passage-video-proof-sketch@t")["target"]["selector"][0]
        for value in ("t=30,60", "t=npt:10,20", "t=,20", "t=10", "t=0:02:00,121.5", "t=npt:120,0:02:01.5",
                      "xywh=0,0,10,10", "track=audio&t=5"):
            sel["value"] = value
            with self.subTest(value=value):
                self.assertValid(doc)
        for value, fragment in (("t=60,30", "the start must be before the end"),
                                ("t=npt:1:30", "is not a Media Fragments t= value"),
                                ("xywh=0,0,0,10", "width and height must be positive"),
                                ("xywh=1.5,0,10,10", "is not a Media Fragments xywh= value")):
            sel["value"] = value
            with self.subTest(value=value):
                self.assertProblem(doc, fragment)
        for value, fragment in (("t=1&t=2", "appears more than once; the last one is used"),
                                ("frame=3&t=1", "is not a Media Fragments dimension")):
            sel["value"] = value
            with self.subTest(value=value):
                self.assertValid(doc)
                self.assertTrue(any(fragment in w for w in warnings(doc)), warnings(doc))

    def test_a_site_names_its_pages(self):
        doc = load("site-pages.json")
        loc = by_id(doc["locations"], "Notes.sum_le@site")
        loc["target"]["selector"] = [loc["target"]["selector"][0]["refinedBy"]]
        self.assertProblem(doc, "is a site: each alternative names its page with a SitePageSelector")
        doc = load("web-passage.json")
        doc["locations"][0]["target"]["selector"].append({"type": "SitePageSelector", "path": "index.html"})
        self.assertProblem(doc, "a SitePageSelector names a page of a site")
        doc = load("site-pages.json")
        loc = by_id(doc["locations"], "Notes.sum_le@site")
        loc["target"]["selector"][0]["refinedBy"] = {"type": "SitePageSelector", "path": "a/"}
        self.assertProblem(doc, "a SitePageSelector is a top-level alternative, never a refinement")

    def test_a_location_shows_items_of_its_own_source(self):
        doc = load("mixed.json")
        loc = by_id(doc["locations"], "definition-1.1@p007")
        errata = next(i["id"] for i in doc["items"] if i["source"] == "notes-errata")
        loc["items"] = sorted(loc["items"] + [errata])
        self.assertProblem(doc, "a location shows items of its own source 'notes-pdf'")

    def test_a_location_with_no_items_is_named_by_something(self):
        doc = load("pdf-region.json")
        link = next(l for l in doc["links"] if "locations" in l)
        del link["locations"]
        self.assertProblem(doc, "a location shows at least one item, anchors a declaration, or is named by a link")

    def test_an_anchor_shows_lean_text(self):
        doc = load("pdf-region.json")
        by_id(doc["declarations"], "Notes.Box")["anchors"] = ["definition-1.1@p007"]
        self.assertProblem(doc, "an anchor shows the declaration's own text, on a lean, web, or other "
                                "text/html source; 'notes-pdf' is pdf")

    def test_an_anchor_may_sit_on_an_other_text_source_but_not_on_video(self):
        doc = load("other-media.json")
        md = next(s for s in doc["sources"] if s.get("media_type") == "text/markdown")
        loc = next(l for l in doc["locations"] if l["target"]["source"] == md["id"])
        doc["declarations"][0]["anchors"] = [loc["id"]]
        self.assertValid(doc)
        video = next(s for s in doc["sources"] if s.get("media_type") == "video/mp4")
        loc = next(l for l in doc["locations"] if l["target"]["source"] == video["id"])
        doc["declarations"][0]["anchors"] = [loc["id"]]
        self.assertProblem(doc, "is other (video/mp4)")

    def test_a_link_location_is_on_the_items_source(self):
        doc = load("mixed.json")
        errata = next(i["id"] for i in doc["items"] if i["source"] == "notes-errata")
        doc["links"].append({"id": f"{errata}/support/Notes.Box", "item": errata, "declaration": "Notes.Box",
                             "role": "support", "provenance": "prov-curation",
                             "locations": ["definition-1.1@p007"]})
        by_id(doc["items"], errata)["coverage"] = "support_only"
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "not on the item's source 'notes-errata'")

    # -- sources -------------------------------------------------------------------
    def test_a_web_source_is_pinned_by_sha256_or_revision(self):
        doc = load("web-passage.json")
        del doc["sources"][0]["revision"]
        self.assertProblem(doc, "matches none of the allowed alternatives (anyOf)")
        doc = load("site-pages.json")
        web = next(s for s in doc["sources"] if s["kind"] == "web")
        self.assertNotIn("url", web)
        self.assertValid(doc)

    def test_page_fields_and_site_scope_belong_to_their_kinds(self):
        doc = load("web-passage.json")
        doc["sources"][0]["page_size"] = {"width_pt": 612, "height_pt": 792}
        self.assertProblem(doc, "(not)")
        doc = load("pdf-region.json")
        doc["sources"][0]["scope"] = "site"
        self.assertProblem(doc, "must be \"web\"")
        doc = load("other-media.json")
        del doc["sources"][0]["media_type"]
        self.assertProblem(doc, "missing required key 'media_type'")
        doc = load("other-media.json")
        video = next(s for s in doc["sources"] if s.get("media_type") == "video/mp4")
        video["page_size"] = {"width_pt": 612, "height_pt": 792}
        self.assertProblem(doc, "page_size describe PDF pages")
        doc = load("pdf-region.json")
        doc["sources"][0]["page_sizes"] = [{"first": 3, "last": 5, "width_pt": 500, "height_pt": 700},
                                           {"first": 5, "last": 41, "width_pt": 500, "height_pt": 700}]
        found = problems(doc)
        self.assertTrue(any("page_sizes ranges overlap" in p for p in found), found)
        self.assertTrue(any("page 41 is past the source's 40 physical pages" in p for p in found), found)

    def test_a_site_sha256_is_the_sha256_of_its_page_manifest(self):
        pages = [{"path": "b/index.html", "sha256": "b" * 64}, {"path": "a.html", "sha256": "a" * 64}]
        manifest = f"{'a' * 64}  a.html\n{'b' * 64}  b/index.html\n"
        self.assertEqual(V.site_sha256(pages), hashlib.sha256(manifest.encode("utf-8")).hexdigest())
        doc = load("site-pages.json")
        site = by_id(doc["sources"], "literate-site")
        self.assertEqual(site["sha256"], V.site_sha256(site["page_hashes"]))
        site["page_hashes"][0]["sha256"] = "0" * 64
        self.assertProblem(doc, "a site's sha256 is the sha256 of its page manifest")
        doc = load("site-pages.json")
        site = by_id(doc["sources"], "literate-site")
        site["page_hashes"] = [p for p in site["page_hashes"] if p["path"] != "Notes/Section_2/index.html"]
        site["sha256"] = V.site_sha256(site["page_hashes"])
        self.assertProblem(doc, "'Notes/Section_2/index.html' is not a page in 'literate-site''s page_hashes")
        doc = load("site-pages.json")
        site = by_id(doc["sources"], "literate-site")
        site["page_hashes"].reverse()
        self.assertProblem(doc, "page_hashes: must be sorted by path, one entry each")
        doc = load("pdf-region.json")
        doc["sources"][0]["page_hashes"] = [{"path": "a.html", "sha256": "a" * 64}]
        self.assertProblem(doc, "(not)")

    def test_companion_commit_is_a_full_sha(self):
        doc = load("mixed.json")
        doc["companion"]["commit"] = doc["companion"]["commit"][:7]
        self.assertProblem(doc, "$.companion.commit")

    # -- URLs (I10) ------------------------------------------------------------------
    def test_urls_stay_in_sources_and_destinations(self):
        doc = load("mixed.json")
        doc["provenance"][0]["note"] = "see https://example.org/run"
        self.assertProblem(doc, "http(s) URLs belong in sources[] and destinations[] only")
        doc = load("mixed.json")
        doc["companion"]["repo"] = "https://git.example.org/x/y"
        self.assertTrue(problems(doc))
        doc = load("mixed.json")
        self.item(doc, "passage-boxes-motivation")["title"] = "see http://example.org"
        self.assertProblem(doc, "http(s) URLs belong in sources[] and destinations[] only")

    def test_quoted_source_text_may_contain_a_url(self):
        doc = load("mixed.json")
        errata = next(i for i in doc["items"] if i["source"] == "notes-errata")
        errata["quote"]["prefix"] = "from http://notes.example.org: "
        sel = by_id(doc["locations"], "passage-remark-unions@p007")["target"]["selector"][1]["refinedBy"]
        sel["suffix"] = " at https://example.org/x"
        self.assertValid(doc)

    # -- declarations (I8) -----------------------------------------------------------
    def test_a_hash_id_must_match_its_statement(self):
        doc = load("pdf-region.json")
        decl = next(d for d in doc["declarations"] if d["id_source"] == "statement_hash")
        decl["statement_sha256"] = "0" * 64
        del decl["signature"]
        self.assertProblem(doc, "is not the first 12 digits of statement_sha256")

    def test_a_signature_is_what_statement_sha256_hashes(self):
        doc = load("pdf-region.json")
        decl = by_id(doc["declarations"], "Notes.Box.volume_nonneg")
        decl["signature"] += " "
        self.assertProblem(doc, "must be normalised")
        decl["signature"] = decl["signature"].strip() + " extra"
        self.assertProblem(doc, "its sha256 is not statement_sha256")

    def test_an_ordinal_needs_a_changelog_record(self):
        doc = load("ledger-graph.json")
        doc["changelog"] = [c for c in doc["changelog"] if c["event"] != "ordinal_assigned"]
        self.assertProblem(doc, "carries an ordinal, which is positional")

    def test_the_latest_ordinal_record_names_the_declarations_file(self):
        doc = load("ledger-graph.json")
        rid = "example:04b2e561a56f~2"
        record = next(c for c in doc["changelog"] if c["subject"] == rid)
        self.assertEqual(record["file"], by_id(doc["declarations"], rid)["file"])
        by_id(doc["declarations"], rid)["file"] = "Notes/Unions.lean"      # a copy elsewhere took ~2
        by_id(doc["declarations"], rid)["module"] = "Notes.Unions"
        self.assertProblem(doc, "the latest ordinal_assigned record gives 'example:04b2e561a56f~2' to a "
                                "declaration in 'Notes/Basic.lean', and it is in 'Notes/Unions.lean'")
        doc["changelog"].append(dict(record, at="2026-09-05", file="Notes/Unions.lean",
                                     id="2026-09-05/ordinal/example:04b2e561a56f~2"))
        self.assertValid(V.canonicalize(doc))
        doc["changelog"][-1]["at"] = record["at"]
        self.assertProblem(V.canonicalize(doc), "name different files")

    def test_an_ordinal_record_outlives_its_retired_id(self):
        doc = load("ledger-graph.json")
        self.assertValid(doc)                  # ~3 is recorded, then retired
        doc = load("ledger-graph.json")
        doc["changelog"] = [c for c in doc["changelog"]
                            if not (c["event"] == "retired" and c["subject"] == "example:04b2e561a56f~3")]
        self.assertProblem(doc, "is not a live declaration id, and no later changelog entry retires or renames it")
        doc = load("ledger-graph.json")
        retire = next(c for c in doc["changelog"] if c["event"] == "retired")
        retire["at"] = "2026-08-01"            # retired before it was recorded: not later
        self.assertProblem(doc, "no later changelog entry retires or renames it")

    def test_an_impossible_changelog_date_is_a_problem_not_a_crash(self):
        doc = load("ledger-graph.json")
        rec = next(c for c in doc["changelog"] if c["event"] == "ordinal_assigned")
        rec["at"] = "2026-02-30"
        self.assertProblem(doc, "is not a calendar date or UTC instant")
        rec["at"] = "2026-09-05T25:00:00Z"
        self.assertProblem(doc, "is not a calendar date or UTC instant")

    def test_a_field_namespace_is_checked_without_a_parent_name(self):
        doc = load("ledger-graph.json")
        field = next(d for d in doc["declarations"] if d.get("kind") == "field")
        parent = by_id(doc["declarations"], field["parent"])
        parent.pop("name", None)
        field["namespace"] = "Totally.Wrong"
        self.assertProblem(doc, "namespace is its parent's full name (its name)")

    def test_a_passage_with_a_location_quote_may_not_take_a_page_title_hash_id(self):
        doc = load("site-pages.json")
        item = by_id(doc["items"], "passage-2caa5bb1")      # its quote comes from its location
        item["id_source"] = "page_title_hash"
        self.assertProblem(doc, "a passage with a quote (")

    def test_a_pattern_matching_definition_with_an_indented_where_has_no_signature(self):
        code = "def f : Nat → Nat\n  | 0 => 1\n  | n + 1 => g n\n  where g (n : Nat) : Nat := n\n"
        scan = ids.blank_literals(code)
        self.assertIsNone(ids.named_signature(code, scan, 0, "def"))
        code2 = "def h (n : Nat) : Nat := n\n"
        self.assertEqual(ids.named_signature(code2, ids.blank_literals(code2), 0, "def"), "def h (n : Nat) : Nat")

    def test_a_declared_id_allows_capitals_but_not_a_hash_shape(self):
        doc = load("ledger-graph.json")
        self.assertIn("example:Unions-of-boxes-are-elementary", {d["id"] for d in doc["declarations"]})
        decl = by_id(doc["declarations"], "example:Unions-of-boxes-are-elementary")
        decl["id"] = "example:0123456789ab"
        for rec in doc["memberships"] + doc["destinations"]:
            if rec.get("declaration") == "example:Unions-of-boxes-are-elementary":
                rec["declaration"] = "example:0123456789ab"
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "a slug of 12 hex digits is reserved for statement hashes")

    def test_a_private_id_is_module_and_name(self):
        doc = load("pdf-region.json")
        decl = next(d for d in doc["declarations"] if d["id_source"] == "private")
        decl["name"] = "Notes.Box.other"
        self.assertProblem(doc, "a private declaration's id is private:<module>:<name>")

    def test_a_field_has_a_structure_parent(self):
        doc = load("pdf-region.json")
        field = by_id(doc["declarations"], "Notes.Box.lower")
        del field["parent"]
        self.assertProblem(doc, "missing required key 'parent'")
        field["parent"] = "Notes.Box.volume_nonneg"
        self.assertProblem(doc, "a field's parent is a structure or class, not a theorem")
        doc = load("pdf-region.json")
        by_id(doc["declarations"], "Notes.Box.volume_nonneg")["parent"] = "Notes.Box"
        self.assertProblem(doc, "(not)")

    def test_an_unlocated_declared_id_has_no_line_or_statement(self):
        doc = load("ledger-graph.json")
        decl = next(d for d in doc["declarations"] if d.get("unlocated"))
        decl["line"] = 3
        self.assertProblem(doc, "(not)")
        doc = load("ledger-graph.json")
        decl = next(d for d in doc["declarations"] if d.get("unlocated"))
        decl["id_source"] = "statement_hash"
        self.assertTrue(problems(doc))

    # -- links, memberships, artifacts, verdicts --------------------------------
    def test_an_unknown_relation_warns(self):
        doc = load("web-passage.json")
        doc["links"][0]["relation"] = "analogy"
        self.assertValid(doc)
        self.assertTrue(any("outside the documented core" in w for w in warnings(doc)))

    def test_an_unattributed_declaration_has_no_link(self):
        doc = load("mixed.json")
        doc["links"].append({"id": "definition-1.1/support/Notes.simp_volume", "item": "definition-1.1",
                             "declaration": "Notes.simp_volume", "role": "support",
                             "provenance": "prov-curation"})
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "'Notes.simp_volume' is linked at")

    def test_memberships_and_destinations_resolve(self):
        doc = load("ledger-graph.json")
        member = next(m for m in doc["memberships"] if "declaration" in m)
        member["artifact"] = "module_doc:Nowhere"
        del member["declaration"]
        self.assertProblem(doc, "'module_doc:Nowhere' is not an id in artifacts[]")
        doc = load("ledger-graph.json")
        doc["memberships"].append(dict(doc["memberships"][0], id="zz-duplicate"))
        self.assertProblem(doc, "repeats the membership")
        doc = load("ledger-graph.json")
        dest = next(d for d in doc["destinations"] if "artifact" in d)
        dest["declaration"] = "Notes.Box"
        self.assertProblem(doc, "must match exactly one alternative (oneOf)")

    def test_a_verdict_location_must_show_its_item(self):
        doc = load("mixed.json")
        doc["verdicts"][0]["locations"] = ["definition-1.1@p007", "passage-remark-unions@p007"]
        self.assertProblem(doc, "does not show item 'definition-1.1'")

    def test_a_fixed_verdict_has_a_fixed_resolution(self):
        doc = load("mixed.json")
        doc["verdicts"][0]["resolution"] = {"state": "open"}
        self.assertProblem(doc, "status is fixed, so the resolution must be fixed")
        doc = load("mixed.json")
        del doc["verdicts"][0]["resolution"]
        self.assertProblem(doc, "$.verdicts[0].status: status is fixed, so the resolution must be fixed "
                                "(a verdict without a resolution is open)")

    def test_a_verdict_without_a_resolution_is_open(self):
        doc = load("mixed.json")
        verdict = doc["verdicts"][0]
        del verdict["resolution"]
        verdict["status"] = "unconfirmed"
        self.assertValid(doc)

    def test_a_field_namespace_is_its_parents_full_name(self):
        doc = load("web-passage.json")
        field = by_id(doc["declarations"], "Ideal.IsPrime.ne_top'")
        self.assertEqual(field["namespace"] + ".ne_top'", field["name"])
        field["namespace"] = "Ideal"
        self.assertProblem(doc, "a projection's namespace is its parent's full name (its name), 'Ideal.IsPrime'")

    def test_an_input_names_a_file_or_a_source(self):
        doc = load("site-pages.json")
        prov = by_id(doc["provenance"], "prov-text")
        self.assertEqual(prov["inputs"], [{"source": "book-pdf"}])
        prov["inputs"] = [{"source": "no-such-source"}]
        self.assertProblem(doc, "'no-such-source' is not an id in sources[]")
        prov["inputs"] = [{"source": "book-pdf", "path": "book.pdf", "sha256": "0" * 64}]
        self.assertProblem(doc, "(oneOf)")
        prov["inputs"] = [{"source": "book-pdf"}, {"path": "_site/xref.json", "sha256": "0" * 64}]
        self.assertProblem(doc, "inputs: must be sorted by path, then source, one entry each")
        prov["inputs"].reverse()
        self.assertValid(doc)

    def test_resolution_replacements_resolve(self):
        doc = load("ledger-graph.json")
        doc["verdicts"][0]["resolution"]["replaced_by"].append("Notes.Zz")
        self.assertProblem(doc, "'Notes.Zz' is not an id in declarations[]")

    # -- changelog (I9) ------------------------------------------------------------
    def test_a_retired_id_is_never_reused(self):
        doc = load("mixed.json")
        retired = next(c for c in doc["changelog"] if c["event"] == "retired")
        doc["declarations"].append({
            "id": retired["subject"], "id_source": "statement_hash", "kind": "example",
            "module": "Notes.Exercises", "file": "Notes/Exercises.lean", "private": False,
            "anonymous": True, "statement_sha256": retired["old_statement_sha256"]})
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "is retired, so it may not be used again")

    def test_a_renamed_subject_is_not_live_and_differs_from_its_successor(self):
        doc = load("ledger-graph.json")
        change = next(c for c in doc["changelog"] if c["event"] == "renamed")
        change["subject"] = "Notes.Box"
        self.assertProblem(doc, "'Notes.Box' is renamed, so it may not be used again")
        change["subject"] = change["successor"] = "Notes.Box.lower"
        self.assertProblem(doc, "the successor differs from the subject")

    def test_an_orphaned_link_must_name_a_retired_id(self):
        doc = load("mixed.json")
        doc["changelog"] = [c for c in doc["changelog"] if c["event"] != "retired"]
        self.assertProblem(doc, "is orphaned but no changelog entry retires it")

    def test_an_orphaned_link_needs_a_retired_declaration_not_another_subject(self):
        doc = load("mixed.json")
        retired = next(c for c in doc["changelog"] if c["event"] == "retired")
        retired["subject_kind"] = "item"
        del retired["possible_successor"]
        self.assertProblem(doc, "is orphaned but no changelog entry retires it")

    def test_restated_names_a_declared_or_named_id(self):
        self.assertValid(load("mixed.json"))            # a named id, restated
        doc = load("ledger-graph.json")
        restated = {"id": "2026-09-25/restated/example:04b2e561a56f", "at": "2026-09-25", "event": "restated",
                    "subject": "example:04b2e561a56f", "subject_kind": "declaration",
                    "old_statement_sha256": "0" * 64, "new_statement_sha256": "1" * 64}
        doc["changelog"].append(restated)
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "'example:04b2e561a56f' is a statement-hash id, which changes with its "
                                "statement: record retired (and the new id), not restated")
        doc = load("ledger-graph.json")
        doc["changelog"].append(dict(restated, subject="Notes.Gone", id="2026-09-25/restated/Notes.Gone"))
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "'Notes.Gone' is not a live declaration id, and no later changelog entry")
        doc = load("ledger-graph.json")
        doc["changelog"].append(dict(restated, subject="Notes.Box", subject_kind="item",
                                     id="2026-09-25/restated/Notes.Box"))
        doc = V.canonicalize(doc)
        self.assertProblem(doc, "restated records a declaration whose statement changed")

    # -- placement (section 4.4): one test per shape the review found -----------
    def placed(self, selector: dict) -> list[str]:
        doc = load("pdf-region.json")
        by_id(doc["locations"], "definition-1.1@p007")["target"]["selector"][0] = selector
        return problems(doc)

    def region(self, page: int = 7) -> dict:
        return {"type": "PdfRegionSelector", "page": page,
                "coordinate_space": {"origin": "top_left", "units": "normalized", "x_axis": "right",
                                     "y_axis": "down", "page_box": "CropBox"},
                "rect": {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}}

    def page7(self, child: dict) -> dict:
        return {"type": "FragmentSelector", "conformsTo": vocab.CONFORMS_TO_PDF, "value": "page=7",
                "refinedBy": child}

    def quote(self, child: dict) -> dict:
        return {"type": "TextQuoteSelector", "exact": "Definition 1.1 (Boxes).", "refinedBy": child}

    def span(self, a: dict, b: dict) -> dict:
        return {"type": "RangeSelector", "startSelector": a, "endSelector": b}

    def offset(self) -> dict:
        return {"type": "TextPositionSelector", "start": 1, "end": 2}

    def test_a_region_does_not_refine_a_quote(self):
        found = self.placed(self.page7(self.quote(self.region())))
        self.assertTrue(any("a PdfRegionSelector is a top-level alternative, a RangeSelector end, or the "
                            "refinement of a PDF page= fragment" in p for p in found), found)

    def test_a_pdf_offset_does_not_refine_a_quote(self):
        found = self.placed(self.page7(self.quote(self.offset())))
        self.assertTrue(any("offsets into a PDF count within one page" in p for p in found), found)

    def test_a_range_of_pdf_offsets_refines_the_page_itself(self):
        found = self.placed(self.page7(self.quote(self.span(self.offset(), self.offset()))))
        self.assertEqual(len([p for p in found if "offsets into a PDF count within one page" in p]), 2, found)
        self.assertEqual(self.placed(self.page7(self.span(self.offset(), self.offset()))), [])

    def test_regions_may_end_a_range_that_refines_a_quote(self):
        self.assertEqual(self.placed(self.quote(self.span(self.region(), self.region()))), [])
        found = self.placed(self.page7(self.span(self.region(8), self.region(8))))
        self.assertTrue(any("page 8 disagrees with the refined page=7" in p for p in found), found)

    # -- page sizes, whitespace ids, the root $schema, x_ keys ---------------------
    def test_a_pt_region_uses_its_pages_size(self):
        doc = load("ledger-graph.json")
        rect = by_id(doc["locations"], "definition-1.1@p007-2")["target"]["selector"][0]["rect"]
        rect["x1"] = 1000
        self.assertProblem(doc, "the rect leaves the 612 x 792 pt page")
        doc["sources"][0]["page_sizes"] = [{"first": 7, "last": 7, "width_pt": 1224, "height_pt": 792}]
        self.assertValid(doc)
        rect["x1"] = 522
        doc["sources"][0]["page_sizes"] = [{"first": 7, "last": 7, "width_pt": 300, "height_pt": 200}]
        self.assertProblem(doc, "the rect leaves the 300 x 200 pt page")
        doc["sources"][0]["page_sizes"] = [{"first": 8, "last": 9, "width_pt": 300, "height_pt": 200}]
        self.assertValid(doc)          # page 7 is not in the range: page_size applies

    def test_a_whitespace_quote_or_title_is_a_problem_not_a_crash(self):
        doc = load("web-passage.json")
        next(i for i in doc["items"] if i["id_source"] == "quote_hash")["quote"]["exact"] = "  "
        self.assertProblem(doc, "a quote_hash id hashes a quote with text in it; item.quote is only whitespace")
        doc = load("pdf-region.json")
        next(i for i in doc["items"] if i["id_source"] == "page_title_hash")["title"] = " \t "
        self.assertProblem(doc, "a page_title_hash id hashes a title with text in it")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = V.main([str(path)])
            self.assertEqual(status, 1)
            self.assertIn(".title: a page_title_hash id hashes a title with text in it", out.getvalue())
            self.assertEqual(err.getvalue(), "")

    def test_the_root_schema_key_may_hold_a_url(self):
        doc = load("pdf-region.json")
        doc["$schema"] = "https://example.org/annotations-v1.schema.json"
        self.assertValid(doc)
        doc = load("pdf-region.json")
        doc["x_meta"] = {"$schema": "https://example.org/x"}
        self.assertProblem(doc, "$.x_meta.$schema: http(s) URLs belong in sources[] and destinations[] only")

    def test_x_keys_sit_on_records_companion_and_the_root_and_obey_i10(self):
        doc = load("mixed.json")
        self.assertTrue(any(k.startswith("x_") for k in doc))
        self.assertTrue(any(k.startswith("x_") for k in doc["companion"]))
        self.assertTrue(any(k.startswith("x_") for p in doc["provenance"] for k in p))
        self.assertValid(doc)
        prov = next(p for p in doc["provenance"] if any(k.startswith("x_") for k in p))
        prov["x_review_round"] = {"log": "https://example.org/run/2"}
        self.assertProblem(doc, ".x_review_round.log: http(s) URLs belong in sources[] and destinations[] only")
        doc = load("mixed.json")
        doc["companion"]["x_build"]["log"] = "http://example.org/build"
        self.assertProblem(doc, "$.companion.x_build.log: http(s) URLs belong")
        doc = load("mixed.json")
        doc["x_elsewhere"] = ["https://example.org/"]
        self.assertProblem(doc, "$.x_elsewhere[0]: http(s) URLs belong")
        doc = load("mixed.json")
        doc["pages"] = 3
        self.assertProblem(doc, "pages")                   # not an x_ key: the root is closed

    def test_a_signature_carries_no_comment(self):
        doc = load("pdf-region.json")
        decl = by_id(doc["declarations"], "Notes.Box.volume_nonneg")
        decl["signature"] += " -- a comment"
        decl["statement_sha256"] = ids.statement_sha256(decl["signature"])
        self.assertProblem(doc, "must be normalised: comments removed")
        decl["signature"] = decl["signature"].replace(" -- a comment", " /- a block -/ ")
        self.assertProblem(doc, "must be normalised: comments removed")


LEAN = """\
/-- `Exercise 1.4` -/
example : volume (Box.unit 2) = 1 := by
  sorry

-- the same statement again, reformatted
example :   volume (Box.unit 2)
    = 1 := by simp

example : let f := fun x : ℕ => x + 1
    f 0 = 1 := rfl

example (d : ℕ) := foo_eq d

/- `example:boxes-are-elementary` -/
example : IsElementary (Box.unit 1 : Set ℝ) := by
  exact isElementary_box _

instance : Inhabited Box := ⟨Box.unit 0⟩

instance instNamed : Nonempty Box := ⟨Box.unit 0⟩

noncomputable example : ℝ := 1
"""

LEAN_EDGES = """\
local instance : Fact (1 < 2) := ⟨by decide⟩

scoped instance : Fact (2 < 3) := ⟨by decide⟩

open Real in example : Real.pi > 3 := Real.pi_gt_three

set_option maxHeartbeats 400000 in
example : True := trivial

/-- A docstring that only mentions `example:other-id` in passing. -/
example : 1 = 1 := rfl

/-- The unit box.
lean-companion id: example:Unit-box
-/
example : Box.unit 1 = Box.unit 1 := rfl

/-! `example:with-prose`

Some prose after the tag. -/
example : 2 = 2 := rfl

/- `instance:wrong-keyword` -/
example : 3 = 3 := rfl

namespace Foo
  example (d : ℕ) := bar d
  theorem later : True := trivial
end Foo
"""


class TestMechanicalIds(unittest.TestCase):

    def scan(self, text: str = LEAN) -> list:
        return ids.scan_file(text, "Notes/Exercises.lean")

    def test_the_scan_finds_every_anonymous_declaration_and_no_named_one(self):
        found = self.scan()
        self.assertEqual([(d.line, d.keyword) for d in found],
                         [(2, "example"), (6, "example"), (9, "example"), (12, "example"),
                          (15, "example"), (18, "instance"), (22, "example")])

    def test_comments_and_whitespace_do_not_change_the_hash(self):
        first, second = self.scan()[:2]
        self.assertEqual(first.statement_sha256, second.statement_sha256)
        self.assertEqual(first.signature, "example : volume (Box.unit 2) = 1")
        self.assertEqual(first.statement_sha256, ids.statement_sha256(first.signature))

    def test_a_let_inside_the_type_does_not_end_the_statement(self):
        let = self.scan()[2]
        self.assertEqual(let.statement_sha256,
                         ids.statement_sha256("example : let f := fun x : ℕ => x + 1 f 0 = 1"))

    def test_an_untyped_example_hashes_the_whole_command(self):
        untyped = self.scan()[3]
        self.assertFalse(untyped.typed)
        self.assertEqual(untyped.statement_sha256, ids.statement_sha256("example (d : ℕ) := foo_eq d"))

    def test_duplicates_take_an_ordinal_and_declared_ids_win(self):
        assigned = ids.assign_ids(self.scan())
        by_line = {d.line: assigned[d] for d in assigned}
        digest = ids.statement_sha256("example : volume (Box.unit 2) = 1")
        self.assertEqual(by_line[2], (f"example:{digest[:12]}", "statement_hash"))
        self.assertEqual(by_line[6], (f"example:{digest[:12]}~2", "statement_hash"))
        self.assertEqual(by_line[15], ("example:boxes-are-elementary", "declared"))
        self.assertTrue(re.fullmatch(r"instance:[0-9a-f]{12}", by_line[18][0]))
        self.assertEqual([rid for rid, _d in ids.ordinal_ids(assigned)], [f"example:{digest[:12]}~2"])

    def test_local_scoped_and_prefixed_declarations_are_found(self):
        found = {d.line: d for d in self.scan(LEAN_EDGES)}
        self.assertEqual(found[1].keyword, "instance")
        self.assertEqual(found[1].signature, "instance : Fact (1 < 2)")
        self.assertEqual(found[3].keyword, "instance")
        self.assertEqual(found[5].signature, "example : Real.pi > 3")
        self.assertEqual(found[8].signature, "example : True")

    def test_a_tag_is_declared_only_when_the_comment_is_the_tag(self):
        found = {d.line: d for d in self.scan(LEAN_EDGES)}
        self.assertIsNone(found[11].declared_id)
        self.assertIsNone(found[11].rejected_tag)
        self.assertEqual(found[16].declared_id, "example:Unit-box")
        self.assertIsNone(found[21].declared_id)
        self.assertIn("holds the tag and nothing else", found[21].rejected_tag)
        self.assertIsNone(found[24].declared_id)
        self.assertIn("is on a example", found[24].rejected_tag)

    def test_an_indented_untyped_example_stops_at_its_sibling(self):
        found = {d.line: d for d in self.scan(LEAN_EDGES)}
        self.assertEqual(found[27].signature, "example (d : ℕ) := bar d")

    def test_string_and_char_literals_are_opaque(self):
        text = ('example : "a:=b" = "a:=b" := rfl\n\n'
                'example : "a:=c" = "a:=c" := rfl\n\n'
                "example : ':' = ':' := rfl\n\n"
                'example : "(" ++ "]" = "(]" := rfl\n\n'
                "example : '\\'' = '\\'' := rfl\n\n"
                'def doc : String := "\nexample : False := sorry\n"\n')
        found = self.scan(text)
        self.assertEqual([d.line for d in found], [1, 3, 5, 7, 9], "an example inside a string is no declaration")
        sigs = [d.signature for d in found]
        self.assertEqual(sigs[0], 'example : "a:=b" = "a:=b"')
        self.assertEqual(sigs[1], 'example : "a:=c" = "a:=c"')
        self.assertNotEqual(found[0].statement_sha256, found[1].statement_sha256)
        self.assertEqual(sigs[2], "example : ':' = ':'")
        self.assertEqual(sigs[3], 'example : "(" ++ "]" = "(]"')
        self.assertEqual(sigs[4], "example : '\\'' = '\\''")
        raw = self.scan('example : r#"x := "y""# = r#"x := "y""# := rfl\n')
        self.assertEqual(raw[0].signature, 'example : r#"x := "y""# = r#"x := "y""#')

    def test_one_line_prefixes_are_seen(self):
        text = ("#guard_msgs in example : True := trivial\n\n"
                "#guard_msgs (drop info) in example : 1 = 1 := rfl\n\n"
                "variable (n : Nat) in example : n = n := rfl\n\n"
                "include h in example : 2 = 2 := rfl\n\n"
                "omit [Foo] in example : 3 = 3 := rfl\n\n"
                "open Nat in set_option pp.all true in example : 4 = 4 := rfl\n\n"
                "/- `example:behind-a-prefix` -/\n"
                "variable (m : Nat) in\n"
                "example : m = m := rfl\n")
        found = {d.line: d for d in self.scan(text)}
        self.assertEqual(sorted(found), [1, 3, 5, 7, 9, 11, 15])
        self.assertEqual(found[1].signature, "example : True")
        self.assertEqual(found[5].signature, "example : n = n")
        self.assertEqual(found[11].signature, "example : 4 = 4")
        self.assertEqual(found[15].declared_id, "example:behind-a-prefix")

    def test_where_ends_a_typed_header(self):
        (decl,) = self.scan("example : Inhabited Nat where\n  default := 0\n")
        self.assertEqual(decl.signature, "example : Inhabited Nat")

    def test_a_named_signature_is_the_same_cut_or_nothing(self):
        text = ("theorem t (n : Nat) : n = n := rfl\n\n"
                "def d : Nat → Nat\n  | 0 => 1\n  | n + 1 => n\n\n"
                "def s : Inhabited Nat where\n  default := 0\n\n"
                "structure S where\n  x : Nat := 0\n\n"
                "def u := 1\n\n"
                "axiom a : False\n")
        code, _ = ids.strip_comments(text)
        scan = ids.blank_literals(code)

        def sig(keyword: str, name: str) -> str | None:
            return ids.named_signature(code, scan, code.index(f"{keyword} {name}"), keyword)
        self.assertEqual(sig("theorem", "t"), "theorem t (n : Nat) : n = n")
        self.assertIsNone(sig("def", "d"))
        self.assertEqual(sig("def", "s"), "def s : Inhabited Nat")
        self.assertIsNone(sig("structure", "S"))
        self.assertIsNone(sig("def", "u"))
        self.assertIsNone(sig("axiom", "a"))

    def test_a_bad_docstring_id_line_is_rejected(self):
        text = "/-- lean-companion id: Foo.bar -/\nexample : True := trivial\n"
        (decl,) = self.scan(text)
        self.assertIsNone(decl.declared_id)
        self.assertIn("'Foo.bar' is not a declared id", decl.rejected_tag)

    def test_the_example_documents_use_the_rule(self):
        doc = load("pdf-region.json")
        decl = next(d for d in doc["declarations"] if d["id_source"] == "statement_hash")
        self.assertEqual(decl["id"], ids.anonymous_id("example", decl["statement_sha256"]))
        self.assertEqual(ids.private_id("Notes.Basic", "Notes.Box.volume_aux"),
                         "private:Notes.Basic:Notes.Box.volume_aux")

    def test_bad_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            ids.anonymous_id("theorem", "0" * 64)
        with self.assertRaises(ValueError):
            ids.anonymous_id("example", "abc")
        with self.assertRaises(ValueError):
            ids.anonymous_id("example", "0" * 64, 0)
        with self.assertRaises(ValueError):
            ids.passage_quote_id("  ")
        with self.assertRaises(ValueError):
            ids.passage_page_id(0, "title")


class TestPassageIds(unittest.TestCase):

    def test_the_quote_is_nfkc_and_whitespace_normalised(self):
        self.assertEqual(ids.normalize_text("deﬁne  the µ-measure\n"), "define the μ-measure")
        self.assertEqual(ids.passage_quote_id("deﬁne the µ-measure"),
                         ids.passage_quote_id("define  the μ-measure"))
        self.assertRegex(ids.passage_quote_id("a quote"), r"^passage-[0-9a-f]{8}$")
        self.assertNotEqual(ids.passage_quote_id("A quote"), ids.passage_quote_id("a quote"))

    def test_the_page_form_uses_the_printed_page_and_the_title(self):
        self.assertRegex(ids.passage_page_id(151, "Maximal inequality"), r"^passage-p151-[0-9a-f]{8}$")
        self.assertEqual(ids.passage_page_id(7, "a  title"), ids.passage_page_id(7, "a title"))

    def test_label_ids(self):
        self.assertEqual(ids.item_label_id("Exercise", "1.2.3"), "exercise-1.2.3")
        self.assertEqual(ids.item_label_id("In-text Definition", "4", "ii"), "in-text-definition-4-ii")


class TestAgreementWithJsonschema(unittest.TestCase):
    """Only when the real package is installed; the skill never requires it."""

    def setUp(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        self.jsonschema = jsonschema
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_the_schema_is_a_valid_2020_12_schema(self):
        self.jsonschema.Draft202012Validator.check_schema(self.schema)

    def test_both_evaluators_agree_on_the_examples_and_on_mutations(self):
        real = self.jsonschema.Draft202012Validator(self.schema)
        ours = Validator(self.schema)
        docs = [load(n) for n in VALID + INVALID]
        rng = random.Random(0)
        values = [None, True, 0, -1, 1.5, "", "x", "1.0", [], {}, {"type": "TextQuoteSelector"}, "page=0",
                  "passage", "numbered", "site"]

        def paths(x, p=()):
            yield p
            if isinstance(x, dict):
                for k, v in x.items():
                    yield from paths(v, p + (k,))
            elif isinstance(x, list):
                for i, v in enumerate(x):
                    yield from paths(v, p + (i,))
        for doc in docs:
            self.assertEqual(real.is_valid(doc), not ours.errors(doc))
        for _ in range(600):
            doc = copy.deepcopy(rng.choice(docs))
            where = rng.choice(list(paths(doc))[1:])
            parent = doc
            for key in where[:-1]:
                parent = parent[key]
            parent[where[-1]] = rng.choice(values)
            with self.subTest(path=where):
                self.assertEqual(real.is_valid(doc), not ours.errors(doc))


class TestTheSpec(unittest.TestCase):

    def text(self) -> str:
        return (SKILL / "docs" / "annotations.md").read_text(encoding="utf-8")

    def test_every_json_block_in_the_spec_parses(self):
        blocks = re.findall(r"```json\n(.*?)```", self.text(), re.S)
        self.assertGreaterEqual(len(blocks), 8)
        for block in blocks:
            with self.subTest(block=block[:40]):
                body = block.strip()
                if body.startswith('"selector"'):
                    body = "{" + body + "}"
                body = body.replace("[ ... ]", "[]")
                json.loads(body)

    def test_the_spec_names_every_invariant_the_checker_enforces(self):
        text = self.text()
        for n in range(1, 14):
            self.assertIn(f"| I{n} |", text)

    def test_the_checkers_invariant_labels_are_the_tables(self):
        code = (SCRIPTS / "annotations" / "validate.py").read_text(encoding="utf-8")
        labels = {int(n) for line in code.splitlines() if line.strip().startswith("# -- I")
                  for n in re.findall(r"I(\d+)", line)}
        self.assertEqual(labels, set(range(1, 14)) - {11})
        self.assertIn("# I11", code)

    def test_the_spec_has_no_em_dash(self):
        self.assertNotIn("\u2014", self.text())


if __name__ == "__main__":
    unittest.main()
