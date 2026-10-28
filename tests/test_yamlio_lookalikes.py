"""The YAML writer quotes every plain scalar a reader would type, keys included.

New code: regression tests for the brain4 review's F5 against ``cflib.yamlio``
(CLAUDE.md section 3: PyYAML when importable, else the fallback reader for the
subset the skill writes).  The writer quoted ``0x10``, ``.inf`` and the bool and
null words, but not an exponent: ``1.5e-3`` was written bare and read back as
0.0015 by both readers.  It also wrote ``=`` and ``<<`` bare, which PyYAML refuses
to construct (the whole file then failed to load), wrote every mapping key bare
(``no: 1`` came back as ``{False: 1}``, or ``{"False": 1}`` from the fallback),
and wrote a float as ``str()`` does (``1e-05``, ``inf``), which PyYAML reads as a
string.  Each case here is dumped and read back through both readers; the
PyYAML half runs only when PyYAML is importable, and then also checks the
writer against PyYAML's own resolver table, not against a copy of it.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import math
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import yamlio  # noqa: E402

try:  # optional: the PyYAML half skips without it
    import yaml as _pyyaml
except ImportError:  # pragma: no cover
    _pyyaml = None

#: strings PyYAML (YAML 1.1) or the fallback reader would type, by the review's
#: classes: exponents, other bases, the bool and null words in every case PyYAML
#: admits, dates and timestamps, the infinities and NaN, and merge and value
LOOKALIKES = (
    # exponents: the reported defect
    "1.5e-3", "1.5E-3", "1.e+3", ".5e-3", "1_000.5e+3", "+1.0e+5", "-2.5e-300", "1e3", "1.5e3",
    "1E+5",
    # other bases and decimal shapes
    "0x10", "0x_1", "-0x1F", "0b101", "+0b1_0", "010", "0o17", "08", "0", "-0", "+1", "1_000",
    "1__2", "3.14", "0.", ".5", "-.5", "1.2.3",
    # bools and nulls, every spelling PyYAML admits and the fallback's folded ones
    "true", "True", "TRUE", "false", "False", "FALSE", "yes", "Yes", "YES", "no", "No", "NO",
    "on", "On", "ON", "off", "Off", "OFF", "yEs", "nO", "null", "Null", "NULL", "nULL", "~", "",
    # dates and timestamps
    "2026-10-17", "2026-10-17T10:00:00Z", "2026-9-1 10:00:00", "2026-10-17 10:00:00.5 +02:00",
    # infinities and NaN
    ".inf", ".Inf", ".INF", "-.inf", "+.inf", ".nan", ".NaN", ".NAN", "+.nan",
    # base 60
    "1:30", "1:30.5", "190:20:30",
    # merge and value: PyYAML refuses to construct either
    "<<", "=",
)

#: strings that must stay bare: the quoting is for look-alikes, not for every digit
BARE = ("u1", "lean-core", "x1.5e-3", "1e3x", "0BSD", "v4.34.0", "a=b", "<<x", "Q99999001",
        "nan", "inf", "y", "n", "e3", "1.5e-3 m")


def _wrappers():
    return (("mapping value", lambda v: {"t": v}),
            ("sequence item", lambda v: [v]),
            ("nested", lambda v: {"a": {"b": [v, {"c": v}]}}),
            ("mapping key", lambda v: {v: 1}),
            ("nested key", lambda v: {"a": {v: [1, {v: v}]}}),
            ("first key of a sequence item", lambda v: [{v: 1, "x": v}]))


class TestEveryLookalikeIsQuoted(unittest.TestCase):

    def test_each_lookalike_survives_the_fallback_reader(self):
        for raw in LOOKALIKES:
            for label, wrap in _wrappers():
                obj = wrap(raw)
                text = yamlio.dump_yaml(obj)
                with self.subTest(scalar=raw, position=label):
                    self.assertEqual(yamlio.load_yaml_minimal(text), obj,
                                     f"the fallback reader read {text!r} differently")

    @unittest.skipUnless(yamlio.HAVE_PYYAML, "PyYAML is not importable")
    def test_each_lookalike_survives_pyyaml(self):
        for raw in LOOKALIKES:
            for label, wrap in _wrappers():
                obj = wrap(raw)
                text = yamlio.dump_yaml(obj)
                with self.subTest(scalar=raw, position=label):
                    self.assertEqual(yamlio.load_yaml(text), obj,
                                     f"PyYAML read {text!r} differently")

    def test_each_lookalike_is_written_double_quoted(self):
        for raw in LOOKALIKES:
            with self.subTest(scalar=raw):
                self.assertEqual(yamlio.dump_yaml({"t": raw}), f't: "{raw}"\n')
                self.assertEqual(yamlio.dump_yaml({raw: 1}), f'"{raw}": 1\n')

    def test_the_reported_value_reloads_as_itself(self):
        text = yamlio.dump_yaml({"rate": "1.5e-3"})
        self.assertEqual(text, 'rate: "1.5e-3"\n')
        self.assertEqual(yamlio.load_yaml_minimal(text), {"rate": "1.5e-3"})
        if yamlio.HAVE_PYYAML:
            self.assertEqual(yamlio.load_yaml(text), {"rate": "1.5e-3"})

    def test_a_plain_string_stays_bare(self):
        for raw in BARE:
            with self.subTest(scalar=raw):
                self.assertEqual(yamlio.dump_yaml({"t": raw}), f"t: {raw}\n")
                self.assertEqual(yamlio.dump_yaml({raw: 1}), f"{raw}: 1\n")
                for reader in (yamlio.load_yaml, yamlio.load_yaml_minimal):
                    self.assertEqual(reader(yamlio.dump_yaml({raw: raw})), {raw: raw})

    def test_a_quoted_scalar_item_holding_a_colon_is_still_a_scalar(self):
        """The fallback reader now opens a sequence item's mapping on a quoted key; an item
        that is one quoted string with ``: `` in it stays that string."""
        for obj in (["a: b"], ['"q": 1'], ["it's: x"], [{"no": "a: b"}, "c: d"]):
            text = yamlio.dump_yaml(obj)
            with self.subTest(obj=obj):
                self.assertEqual(yamlio.load_yaml_minimal(text), obj)
                if yamlio.HAVE_PYYAML:
                    self.assertEqual(yamlio.load_yaml(text), obj)


class TestFloatsAreWrittenAsFloats(unittest.TestCase):
    """``str()`` wrote ``1e-05`` and ``inf``: strings to PyYAML, since its float needs a dot
    and an infinity is ``.inf``."""

    FLOATS = (0.0015, 1e-05, 1e16, -2.5e-300, 1.5e300, 3.0, 0.1, -0.0, 123456789.125,
              float("inf"), float("-inf"))

    def test_both_readers_read_each_float_back(self):
        for value in self.FLOATS:
            text = yamlio.dump_yaml({"x": value, "l": [value]})
            readers = [yamlio.load_yaml_minimal] + ([yamlio.load_yaml] if yamlio.HAVE_PYYAML
                                                    else [])
            for reader in readers:
                with self.subTest(value=value, text=text, reader=reader.__name__):
                    back = reader(text)
                    self.assertIsInstance(back["x"], float)
                    self.assertEqual(back, {"x": value, "l": [value]})

    def test_the_spellings(self):
        self.assertEqual(yamlio.dump_yaml({"x": 1e-05}), "x: 1.0e-05\n")
        self.assertEqual(yamlio.dump_yaml({"x": 1e16}), "x: 1.0e+16\n")
        self.assertEqual(yamlio.dump_yaml({"x": 0.0015}), "x: 0.0015\n")
        self.assertEqual(yamlio.dump_yaml({"x": float("inf")}), "x: .inf\n")
        self.assertEqual(yamlio.dump_yaml({"x": float("-inf")}), "x: -.inf\n")
        self.assertEqual(yamlio.dump_yaml({"x": float("nan")}), "x: .nan\n")
        self.assertEqual(yamlio.dump_yaml({"x": 2}), "x: 2\n")
        self.assertEqual(yamlio.dump_yaml({"x": True}), "x: true\n")

    def test_nan_reads_back_as_nan(self):
        text = yamlio.dump_yaml({"x": float("nan")})
        self.assertTrue(math.isnan(yamlio.load_yaml_minimal(text)["x"]))
        if yamlio.HAVE_PYYAML:
            self.assertTrue(math.isnan(yamlio.load_yaml(text)["x"]))


@unittest.skipUnless(_pyyaml is not None, "PyYAML is not importable")
class TestTheWriterAgreesWithPyYAMLsResolver(unittest.TestCase):
    """The property, checked against PyYAML's own implicit-resolver table: any string the
    writer leaves bare is one PyYAML resolves to ``str``."""

    #: the characters PyYAML's int, float, bool, null, timestamp, merge and value rules use
    ALPHABET = tuple("0123456789._+-eExXoObB:~=<nNuUlLtTrRfFsSyYaA ") + (".inf", ".nan", "0x")

    def _bare(self, raw: str) -> bool:
        return yamlio.dump_yaml({"t": raw}) == f"t: {raw}\n"

    def _resolved(self, raw: str) -> str:
        return _pyyaml.resolver.Resolver().resolve(_pyyaml.ScalarNode, raw, (True, False))

    def test_a_seeded_battery(self):
        rng = random.Random(20261019)
        checked = 0
        for _ in range(20000):
            raw = "".join(rng.choice(self.ALPHABET) for _ in range(rng.randint(1, 7)))
            if not self._bare(raw):
                continue
            checked += 1
            self.assertEqual(self._resolved(raw), "tag:yaml.org,2002:str",
                             f"{raw!r} is written bare and PyYAML types it")
            self.assertEqual(yamlio.load_yaml(yamlio.dump_yaml({raw: raw})), {raw: raw})
            self.assertEqual(yamlio.load_yaml_minimal(yamlio.dump_yaml({raw: raw})), {raw: raw})
        self.assertGreater(checked, 1000)

    def test_every_word_of_the_resolver_table_is_quoted(self):
        words = set()
        for first, rules in _pyyaml.resolver.Resolver.yaml_implicit_resolvers.items():
            for tag, _regexp in rules:
                if tag.endswith((":bool", ":null", ":merge", ":value")):
                    for word in ("yes", "no", "true", "false", "on", "off", "null", "~", "<<",
                                 "="):
                        for spelling in (word, word.capitalize(), word.upper()):
                            if spelling[:1] == (first or "") and _regexp.match(spelling):
                                words.add(spelling)
        self.assertGreaterEqual(len(words), 22, sorted(words))
        for word in sorted(words):
            with self.subTest(word=word):
                self.assertNotEqual(self._resolved(word), "tag:yaml.org,2002:str")
                self.assertFalse(self._bare(word))


if __name__ == "__main__":
    unittest.main()
