#!/usr/bin/env python3
"""The inventory audit's records, in one schema: validate, strip, collateral and merge.

New code, for references/locate-documents.md section 13 (``cf-inventory-audit/1``):
the files an audited inventory run's reviewer turn reads and writes beside one
inventory document.  An auditor (round 1) or a checker (round 2 on) judges the
document, a skeptic judges the flags the auditor raised, and this module --
never an agent -- merges the two into the round's verdicts and decides by the
decision table below whether the document goes back for a repair.  Every rule
those files are held to is in this file: the vocabulary, the shapes, the
coverage rules, the mechanical kind verdict and the decision table, so the
validator an agent may call and the merge the bridge runs read one definition.

Every subcommand takes ``--rule 1|2`` (default 1, which is the base commit's behaviour
byte for byte).  **Audit rule 2** (section 13.11, ``cf-inventory-audit/2``) audits the
skips too: each skip of the snapshot is named ``r<k>-s<nnn>`` and judged once in the
auditor's ``skips`` (``clean`` or ``skipped_statements``), the skeptic hunts the skips no
flag names, the merge keeps an upheld skip flag whose evidence is inside the skip and adds
the mechanical **skip rule** (:func:`skip_facts`), ``collateral`` reports ``skip_edit``,
the decision table ``audit/2`` adds one clause, and every verdicts file carries the
round's :func:`skip_report`.

It runs on the run host from the gate directory, like the gate: standard
library only, no ``cflib`` (``cflib.vocab`` holds the vocabulary, and
``tests/test_locate_inventory_audit.py`` pins the copy below to it), invoked as
``python3 -I <gate>/inventory_audit.py``.  It reads a source's layer only
through ``verify_locate``'s pinned view (its sibling in the gate directory),
and it writes only under ``<cwd>/.tend/audit/`` and ``<cwd>/.tend/agent_result.json``.

    inventory_audit.py validate --kind audit|skeptic|check|repair|ops --file F --document D
                       [--flags F | --previous V --collateral C | --verdicts V]
                       [--expect E | --render-index .tend/audit/rK.render.json --part I]
                       [--rule 2 [--gate-config G]]
    inventory_audit.py strip --round K --document D (--audit F ... | --check F ... --previous V [--repair P]) --out .tend/audit/rK.flags.json
    inventory_audit.py collateral --round K --snapshot S --document D [--ops O] [--verdicts V] --out .tend/audit/rK.collateral.json
    inventory_audit.py merge --round K --review-rounds R --gate-config G --document D --skeptic S
                       (--audit F ... | --check F ... --previous V --collateral C [--repair P])
                       --out .tend/audit/rK.verdicts.json [--result .tend/agent_result.json]
    inventory_audit.py merge --round K --review-rounds R --document D --audit-failed REASON [--previous V] --out ...

Exit 0 when the file is valid (or the output was written), 1 when an input is
invalid (each problem on stdout, as ``INVALID: ...`` lines), 2 when an input
cannot be read or a path is refused, 3 when the gate configuration or a pinned
source cannot be read (as the gate's own exit 3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_INPUT = 2
EXIT_BROKEN = 3

# --------------------------------------------------------------------------- #
# the vocabulary (cflib.vocab's audit block; a test pins every value)
# --------------------------------------------------------------------------- #

#: the audit rules this tool knows (``--rule``, default 1): rule 1 audits the passages, rule
#: 2 the skips too (references/locate-documents.md section 13.11); a run's files carry its
#: rule's schema, and the decision table's version is ``audit/<rule>`` in a verdicts file
AUDIT_RULES = (1, 2)
AUDIT_SCHEMAS = {1: "cf-inventory-audit/1", 2: "cf-inventory-audit/2"}
AUDIT_SCHEMA = AUDIT_SCHEMAS[1]
#: the rule a call runs at without ``--rule``: rule 1, so inv-pilot-3's calls are what they were
DEFAULT_RULE = 1
#: how many review rounds an audited plan may name
AUDIT_REVIEW_ROUNDS = (2, 3)
VERDICT_CLEAN = "clean"
#: what an auditor, a checker's new entry or a skeptic says of a passage
AUDIT_VERDICTS = (VERDICT_CLEAN, "over_cut", "under_cut", "should_be_expository",
                  "should_be_non_math", "should_be_formal", "redundant", "wrong_kind",
                  "wrong_prominence", "statement_misstates")
#: the verdict a merged record carries for a change the checker judged harmful
VERDICT_REVERT = "revert"
#: where an audited passage sits
AUDIT_ORIGINS = ("lead", "theorem_box", "statement_section", "proof", "example", "list_item",
                 "application_or_extension", "history", "other")
#: what a checker says of a verdict the previous round asked for
CHECK_STATUSES = ("applied", "partial", "missing", "wrong", "gate_refused_ok",
                  "gate_refused_doubtful", "disputed")
#: the statuses whose verdict goes back to the worker
CHECK_ACTIONABLE = ("partial", "missing", "wrong", "gate_refused_doubtful")
#: what a worker says it did with a verdict
REPAIR_STATUSES = ("applied", "not_applied", "gate_refused", "disputed")
#: where a merged verdict came from
VERDICT_ORIGINS = ("auditor", "skeptic_missed", "kind_rule", "carried", "check_new",
                   "dispute_refuted", "collateral", "regression")
#: the decision table's outcomes
AUDIT_DECISIONS = ("approve", "request_changes", "revert")
#: the edit tool's operations, and the fields an operation may set
EDIT_OPS = ("merge", "split", "set", "add", "drop")
EDIT_FIELDS = ("scope", "passage_kind", "prominence", "statement", "shape", "conjuncts", "until",
               "concept", "quote", "quote_end")
#: the fields a new passage (``split``'s ``new``, ``add``'s ``fields``) needs, and the two
#: it may carry besides EDIT_FIELDS
NEW_PASSAGE_FIELDS = ("statement", "passage_kind", "scope", "prominence")
NEW_PASSAGE_PLACE = ("section", "fragment")
#: what a collateral entry says changed
COLLATERAL_CHANGES = ("hand_edit", "moved", "uncited_op", "ops_refused")
#: T = max(AUDIT_THRESHOLD_MIN, ceil(AUDIT_THRESHOLD_SHARE * formal)): below it a middle
#: round approves rather than reject for a handful of residuals
AUDIT_THRESHOLD_MIN = 5
AUDIT_THRESHOLD_SHARE = 0.03
#: the skeptic's missed-defect hunt in round 1: at least max(floor, share * clean formal)
#: of the formal passages no flag names, and never more than there are
SKEPTIC_FLOOR = 10
SKEPTIC_SHARE = 0.10
#: word caps (whitespace-separated words)
REASON_WORDS = 30
ACTION_WORDS = 40
EVIDENCE_WORDS = 25
SUMMARY_WORDS = 80
MAX_PATTERNS = 5
#: the bytes of rendering one audit, skeptic or check call reads (inventory_render.py's
#: parts), and the verdicts one repair session applies
PART_BYTES = 50_000
REPAIR_VERDICTS_PER_SESSION = 40
#: the kind rule: a formal passage of kind ``other`` whose section path names a proof or
#: an example gets a mechanical wrong_kind verdict unless the auditor said, with a reason,
#: that ``other`` is right (``kind_other_ok``)
KIND_OTHER = "other"
KIND_RULE_SECTION = re.compile(r"\b(?:proofs?|examples?)\b", re.IGNORECASE)
KIND_RULE_RULE = 6
KIND_RULE_ACTION = ("set passage_kind to theorem when it asserts a fact or an equation, "
                    "definition when it defines; other only for a formal statement that is "
                    "none of the kinds")
#: the verdicts that already settle a passage's kind, so the kind rule adds none beside them
KIND_SETTLED = frozenset({"wrong_kind", "should_be_expository", "should_be_non_math",
                          "redundant", "over_cut"})
#: the first words the rendering hints as a connective start
CONNECTIVE_HINTS = ("and", "so", "hence", "thus", "therefore", "then", "but")
#: --- rule 2: the skips (section 13.11).  A skip is named ``r<k>-s<nnn>``, round k's
#: numbering of the snapshot's skips in span order; each is judged once, ``clean`` or
#: ``skipped_statements``, the one verdict a skip takes and no passage does
SKIP_VERDICT = "skipped_statements"
SKIP_VERDICTS = (VERDICT_CLEAN, SKIP_VERDICT)
SKIP_ORIGINS = ("skip_rule",)
SKIP_OPS = ("unskip",)
SKIP_COLLATERAL = ("skip_edit",)
SKIP_HINTS = ("skip-rule", "in-proof", "in-example", "mostly-skipped", "partial", "furniture")
#: the back matter: a top heading of these (case-folded); with the lead, the furniture
BACK_MATTER = ("references", "notes", "footnotes", "citations", "sources", "bibliography",
               "further reading", "external links", "see also", "works cited",
               "notes and references")
#: the soft threshold of a mostly skipped section (reported, never refusing)
MOSTLY_SKIPPED_MIN = 2
MOSTLY_SKIPPED_SHARE = 0.5
SKIP_STATEMENTS_MAX = 40
SKIP_AT_WORDS = (3, 12)
SKIP_UNTIL_WORDS = 12
SKIP_RULE_RULE = 6
SKIP_RULE_ACTION = ("cut each definition, theorem, equation or declarative statement under "
                    "this skip into a passage of its own (C6); proof narration becomes "
                    "expository; keep a skip only on furniture")
SKIP_BLOCK_BYTES = 20_000
#: how the lead's section path is written where a section is named
LEAD = "(lead)"
FORMAL = "formal_math"
#: the file kinds of the schema, and which an agent writes
FILE_KINDS = ("audit", "check", "skeptic", "flags", "verdicts", "repair", "ops", "collateral",
              "render")
AGENT_KINDS = ("audit", "check", "skeptic", "repair", "ops")

ITEM = re.compile(r"In-text #[0-9a-f]{8}\Z")
QID = re.compile(r"Q[1-9][0-9]*\Z")
VERDICT_ID = re.compile(r"r([1-9][0-9]?)-v([0-9]{3,})\Z")
FLAG_ID = re.compile(r"r([1-9][0-9]?)-f([0-9]{3,})\Z")
COLLATERAL_ID = re.compile(r"r([1-9][0-9]?)-x([0-9]{3,})\Z")
SKIP_ID = re.compile(r"r([1-9][0-9]?)-s([0-9]{3,})\Z")
MAX_PROBLEMS = 200
#: where every file this module writes must be: the worktree's mirror of the stage's files
OUT_DIR = (".tend", "audit")
RESULT_FILE = (".tend", "agent_result.json")


class InputError(Exception):
    """An input cannot be read, or a path is refused (exit 2)."""


class Broken(Exception):
    """The gate configuration or a pinned source cannot be read (exit 3)."""


# --------------------------------------------------------------------------- #
# the shapes: one definition every file is held to
# --------------------------------------------------------------------------- #

#: ``{record: (required fields, optional fields)}``; a field's spec is a name below or a
#: tuple (``("enum", values)``, ``("words", n)``, ``("rows", record)``, ``("strings", n)``)
RECORDS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "audit": ({"schema": "schema", "kind": "kind", "round": "round", "source_id": "text",
               "passages": ("rows", "audit_row"),
               "expository_flags": ("rows", "expository_flag"),
               "per_spec_formal_estimate": "count", "patterns": ("strings", MAX_PATTERNS),
               "subject_check": "str"}, {}),
    "audit_row": ({"item": "item", "origin": ("enum", AUDIT_ORIGINS),
                   "verdict": ("enum", AUDIT_VERDICTS), "rule": "rule", "merge_with": "item?",
                   "reason": ("words", REASON_WORDS), "action": ("words", ACTION_WORDS)},
                  {"n": "count", "kind_other_ok": "bool"}),
    "expository_flag": ({"item": "item", "rule": "rule", "reason": ("words", REASON_WORDS),
                         "action": ("words", ACTION_WORDS)}, {"n": "count"}),
    "check": ({"schema": "schema", "kind": "kind", "round": "round", "source_id": "text",
               "checked": ("rows", "checked_row"), "collateral": ("rows", "collateral_row"),
               "new": ("rows", "new_row"), "regressions": ("rows", "regression_row")}, {}),
    "checked_row": ({"verdict_id": "verdict_id", "status": ("enum", CHECK_STATUSES),
                     "reason": ("words", REASON_WORDS)}, {}),
    "collateral_row": ({"id": "collateral_id", "harmful": "bool",
                        "reason": ("words", REASON_WORDS)}, {}),
    "new_row": ({"item": "item", "verdict": ("enum", AUDIT_VERDICTS), "rule": "rule",
                 "merge_with": "item?", "reason": ("words", REASON_WORDS),
                 "action": ("words", ACTION_WORDS)}, {"n": "count"}),
    "regression_row": ({"verdict_id": "verdict_id", "reason": ("words", REASON_WORDS)}, {}),
    "skeptic": ({"schema": "schema", "kind": "kind", "round": "round", "source_id": "text",
                 "verdicts": ("rows", "skeptic_verdict"), "missed": ("rows", "missed_row"),
                 "disputes": ("rows", "dispute_ruling"), "examined": "items",
                 "per_spec_formal_estimate": "count", "summary": ("words", SUMMARY_WORDS)}, {}),
    "skeptic_verdict": ({"flag_id": "flag_id", "upheld": "bool",
                         "verdict": ("enum", AUDIT_VERDICTS), "rule": "rule",
                         "evidence": ("words", EVIDENCE_WORDS),
                         "reason": ("words", REASON_WORDS)}, {}),
    "missed_row": ({"item": "item", "verdict": ("enum", AUDIT_VERDICTS), "rule": "rule",
                    "merge_with": "item?", "evidence": ("words", EVIDENCE_WORDS),
                    "reason": ("words", REASON_WORDS), "action": ("words", ACTION_WORDS)},
                   {"n": "count"}),
    "dispute_ruling": ({"verdict_id": "verdict_id", "worker_right": "bool",
                        "reason": ("words", REASON_WORDS)}, {}),
    "repair": ({"schema": "schema", "kind": "kind", "round": "round", "source_id": "text",
                "entries": ("rows", "repair_entry")}, {}),
    "repair_entry": ({"verdict_id": "verdict_id", "status": ("enum", REPAIR_STATUSES),
                      "why": ("words", ACTION_WORDS)}, {"gate_line": "line"}),
    "ops": ({"schema": "schema", "kind": "kind", "round": "round", "source_id": "text",
             "ops": ("rows", "op")}, {}),
    "op": ({"op": ("enum", EDIT_OPS)},
           {"verdict": "verdict_id", "neighbour_of": "verdict_id", "item": "item",
            "keep": "item", "drop": "item_or_items", "after": "item_or_null",
            "at": "quote", "quote": "quote", "fields": "fields", "new": "fields",
            "earlier": "fields"}),
}

#: which keys each operation takes beyond ``op`` and its citation, and which it needs
OP_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "merge": (("keep", "drop"), ("fields",)),
    "split": (("item", "at", "new"), ("earlier",)),
    "set": (("item", "fields"), ()),
    "add": (("after", "quote", "fields"), ()),
    "drop": (("item",), ()),
}


def _rule2_records() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Rule 2's shapes: rule 1's, with every skip judged (``skips``), the skeptic's skip
    hunt, the skip verdict a skeptic may uphold, and the edit tool's ``unskip``."""
    out = {name: (dict(req), dict(opt)) for name, (req, opt) in RECORDS.items()}
    out["audit"][0]["skips"] = ("rows", "skip_row")
    out["skip_row"] = ({"skip": "skip_id", "verdict": ("enum", SKIP_VERDICTS), "rule": "rule",
                        "merge_with": "item?", "reason": ("words", REASON_WORDS),
                        "action": ("words", ACTION_WORDS),
                        "statements": ("rows", "skip_statement")},
                       {"n": "count", "skip_ok": "bool"})
    out["skip_statement"] = ({"at": ("words", SKIP_AT_WORDS[1]),
                              "until": ("words", SKIP_UNTIL_WORDS), "passage_kind": "str"}, {})
    out["skeptic"][0].update(examined_skips="skip_ids",
                             missed_skips=("rows", "missed_skip_row"))
    out["skeptic_verdict"][0]["verdict"] = ("enum", AUDIT_VERDICTS + (SKIP_VERDICT,))
    out["missed_skip_row"] = ({"skip": "skip_id", "verdict": ("enum", (SKIP_VERDICT,)),
                               "rule": "rule", "merge_with": "item?",
                               "evidence": ("words", EVIDENCE_WORDS),
                               "reason": ("words", REASON_WORDS),
                               "action": ("words", ACTION_WORDS),
                               "statements": ("rows", "skip_statement")}, {"n": "count"})
    out["op"][0]["op"] = ("enum", EDIT_OPS + SKIP_OPS)
    out["op"][1].update(skip="skip_id", keep="item_or_pieces")
    return out


#: the shapes of each rule: rule 1's are :data:`RECORDS`, as inv-pilot-3's files were held
RECORDS_BY_RULE = {1: RECORDS, 2: _rule2_records()}
#: the operations of each rule, and what each takes
OP_KEYS_BY_RULE = {1: OP_KEYS, 2: {**OP_KEYS, "unskip": (("skip",), ("keep",))}}


def edit_ops(rule: int) -> tuple[str, ...]:
    return EDIT_OPS + (SKIP_OPS if rule >= 2 else ())


def verdict_origins(rule: int) -> tuple[str, ...]:
    return VERDICT_ORIGINS + (SKIP_ORIGINS if rule >= 2 else ())


def collateral_changes(rule: int) -> tuple[str, ...]:
    return COLLATERAL_CHANGES + (SKIP_COLLATERAL if rule >= 2 else ())


def known_rule(rule: Any) -> int:
    """``rule`` when it is one of :data:`AUDIT_RULES`, else :class:`InputError`."""
    if rule not in AUDIT_RULES:
        raise InputError(f"--rule {rule!r} is not an audit rule this tool knows "
                         f"({', '.join(map(str, AUDIT_RULES))})")
    return rule


def words(text: str) -> int:
    return len(str(text).split())


def _type_problem(value: Any, spec: Any, where: str, rule: int = DEFAULT_RULE) -> str | None:
    """One problem with ``value`` under a scalar ``spec``, or None."""
    if spec == "schema":
        want = AUDIT_SCHEMAS[rule]
        return None if value == want else f"{where} is {value!r}, not {want!r}"
    if spec in ("str", "kind"):
        return None if isinstance(value, str) else f"{where} is not a string"
    if spec == "text":
        return None if isinstance(value, str) and value.strip() else f"{where} is empty"
    if spec == "line":
        if not isinstance(value, str) or "\n" in value or len(value) > 1000:
            return f"{where} is not one line of at most 1000 characters"
        return None
    if spec in ("round", "count", "rule"):
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{where} is not an integer"
        if spec == "round" and value < 1:
            return f"{where} is {value}, not a round (1 or more)"
        if spec == "count" and value < 0:
            return f"{where} is negative"
        if spec == "rule" and not 0 <= value <= 10:
            return f"{where} is {value}, not a rule of the statement cut (0 for clean, 1-10)"
        return None
    if spec == "bool":
        return None if isinstance(value, bool) else f"{where} is not true or false"
    if spec in ("item", "item?"):
        if spec == "item?" and value == "":
            return None
        return None if isinstance(value, str) and ITEM.match(value) else \
            f"{where} is {value!r}, not an item spelled In-text #<8 hex>"
    if spec == "item_or_null":
        return None if value is None else _type_problem(value, "item", where)
    if spec == "item_or_items":
        if isinstance(value, list) and value:
            for i, one in enumerate(value):
                problem = _type_problem(one, "item", f"{where}[{i}]")
                if problem:
                    return problem
            return None
        return _type_problem(value, "item", where)
    if spec == "items":
        if not isinstance(value, list):
            return f"{where} is not a list of items"
        for i, one in enumerate(value):
            problem = _type_problem(one, "item", f"{where}[{i}]")
            if problem:
                return problem
        return None
    for name, pattern, shape in (("verdict_id", VERDICT_ID, "r<round>-v<nnn>"),
                                 ("flag_id", FLAG_ID, "r<round>-f<nnn>"),
                                 ("collateral_id", COLLATERAL_ID, "r<round>-x<nnn>"),
                                 ("skip_id", SKIP_ID, "r<round>-s<nnn>")):
        if spec == name:
            return None if isinstance(value, str) and pattern.match(value) else \
                f"{where} is {value!r}, not an id {shape}"
    if spec == "skip_ids":
        if not isinstance(value, list):
            return f"{where} is not a list of skip ids"
        for i, one in enumerate(value):
            problem = _type_problem(one, "skip_id", f"{where}[{i}]")
            if problem:
                return problem
        return None
    if spec == "item_or_pieces":
        if isinstance(value, list):
            return _pieces_problem(value, where)
        return _type_problem(value, "item", where)
    if spec == "quote":
        if not isinstance(value, dict) or set(value) - {"exact", "prefix", "suffix"}:
            return f"{where} is not a quote {{exact, prefix, suffix}}"
        if not isinstance(value.get("exact"), str) or not value["exact"].strip():
            return f"{where}.exact is empty"
        for key in ("prefix", "suffix"):
            if not isinstance(value.get(key, ""), str):
                return f"{where}.{key} is not a string"
        return None
    if spec == "fields":
        return None if isinstance(value, dict) and value else f"{where} is not an object of fields"
    raise AssertionError(f"unknown spec {spec!r}")


def _pieces_problem(value: list, where: str) -> str | None:
    """``unskip``'s ``keep``: a list of ``{span: [a, b], reason?}``, each span two integers
    with a < b (that they lie inside the skip, in order, is the edit tool's check)."""
    for i, piece in enumerate(value):
        at = f"{where}[{i}]"
        if not isinstance(piece, dict) or "span" not in piece or set(piece) - {"span", "reason"}:
            return f"{at} is not a piece {{span: [start, end], reason?}}"
        span = piece["span"]
        if not (isinstance(span, list) and len(span) == 2 and all(
                isinstance(x, int) and not isinstance(x, bool) for x in span)
                and span[0] < span[1]):
            return f"{at}.span is {span!r}, not [start, end] with start < end"
        if "reason" in piece and not (isinstance(piece["reason"], str)
                                      and piece["reason"].strip()):
            return f"{at}.reason is empty (leave it out to keep the skip's own)"
    return None


def check_record(value: Any, record: str, where: str, problems: list[str], *,
                 rule: int = DEFAULT_RULE) -> bool:
    """Hold ``value`` to rule ``rule``'s record ``record`` (:data:`RECORDS_BY_RULE`);
    append each problem; True when sound."""
    required, optional = RECORDS_BY_RULE[rule][record]
    if not isinstance(value, dict):
        problems.append(f"{where} is not an object")
        return False
    before = len(problems)
    for key in sorted(set(value) - set(required) - set(optional)):
        problems.append(f"{where} has the key {key!r}, which {record} does not take")
    for key, spec in required.items():
        if key not in value:
            problems.append(f"{where} has no {key!r}")
            continue
        _check_field(value[key], spec, f"{where}.{key}", problems, rule)
    for key, spec in optional.items():
        if key in value:
            _check_field(value[key], spec, f"{where}.{key}", problems, rule)
    if record in RECORDS and "kind" in required and value.get("kind") != record:
        problems.append(f"{where}.kind is {value.get('kind')!r}, not {record!r}")
    return len(problems) == before


def _check_field(value: Any, spec: Any, where: str, problems: list[str],
                 rule: int = DEFAULT_RULE) -> None:
    if isinstance(spec, tuple):
        what = spec[0]
        if what == "enum":
            if value not in spec[1]:
                problems.append(f"{where} is {value!r}, not one of {', '.join(spec[1])}")
        elif what == "words":
            if not isinstance(value, str):
                problems.append(f"{where} is not a string")
            elif words(value) > spec[1]:
                problems.append(f"{where} has {words(value)} words, more than {spec[1]}")
        elif what == "strings":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{where} is not a list of strings")
            elif len(value) > spec[1]:
                problems.append(f"{where} has {len(value)} entries, more than {spec[1]}")
        elif what == "rows":
            if not isinstance(value, list):
                problems.append(f"{where} is not a list")
            else:
                for i, row in enumerate(value):
                    check_record(row, spec[1], f"{where}[{i}]", problems, rule=rule)
        else:
            raise AssertionError(f"unknown spec {spec!r}")
        return
    problem = _type_problem(value, spec, where, rule)
    if problem:
        problems.append(problem)


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #

def read_json(path: str | Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"the {what} at {path} cannot be read ({exc})") from None


def read_document(path: str | Path) -> dict:
    doc = read_json(path, "document")
    if not isinstance(doc, dict) or not isinstance(doc.get("passages"), list):
        raise InputError(f"the document at {path} is not an inventory document with passages")
    if not all(isinstance(p, dict) for p in doc["passages"]):
        raise InputError(f"the document at {path} has a passage that is not an object")
    return doc


def sha256_file(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise InputError(f"{path} cannot be read ({exc})") from None


def span_of(passage: dict) -> tuple[int, int] | None:
    span = passage.get("span")
    if isinstance(span, list) and len(span) == 2 and all(
            isinstance(x, int) and not isinstance(x, bool) for x in span):
        return span[0], span[1]
    return None


def ordered(doc: dict) -> list[dict]:
    """The document's passages in span order (a passage with no span keeps its place last)."""
    rows = list(enumerate(doc.get("passages") or []))
    return [p for _i, p in sorted(rows, key=lambda r: (span_of(r[1]) is None,
                                                       (span_of(r[1]) or (0, 0)), r[0]))]


def by_item(doc: dict) -> dict[str, dict]:
    return {str(p.get("item")): p for p in doc.get("passages") or [] if p.get("item")}


def is_formal(passage: dict) -> bool:
    return passage.get("scope") == FORMAL


def formal_items(doc: dict) -> list[str]:
    return [str(p.get("item")) for p in ordered(doc) if is_formal(p)]


def section_path(passage: dict) -> list[str]:
    section = passage.get("section")
    return [str(s) for s in section] if isinstance(section, list) else []


def in_proof(passage: dict) -> bool:
    return any(re.search(r"\bproofs?\b", s, re.IGNORECASE) for s in section_path(passage))


def in_example(passage: dict) -> bool:
    return any(re.search(r"\bexamples?\b", s, re.IGNORECASE) for s in section_path(passage))


def kind_rule_applies(passage: dict) -> bool:
    """The kind rule's passages: formal, of kind ``other``, in a proof or example section."""
    return (is_formal(passage) and passage.get("passage_kind") == KIND_OTHER
            and any(KIND_RULE_SECTION.search(s) for s in section_path(passage)))


def threshold(formal: int) -> int:
    """T: the actionable verdicts below which a middle round approves."""
    return max(AUDIT_THRESHOLD_MIN, math.ceil(AUDIT_THRESHOLD_SHARE * formal))


def skeptic_floor(clean_formal: int) -> int:
    """How many unflagged formal passages a round-1 skeptic must examine."""
    return min(clean_formal, max(SKEPTIC_FLOOR, math.ceil(SKEPTIC_SHARE * clean_formal)))


def fold(text: str) -> str:
    """Text as the evidence check compares it: the rendering's line mark a space, and
    every run of whitespace one space."""
    return " ".join(str(text).replace("⏎", " ").split())


def fold_index(text: str) -> tuple[str, list[int]]:
    """``(fold(text), at)``: the folded text, and for each of its characters the index in
    ``text`` it came from (a run of whitespace folds to its first character)."""
    out: list[str] = []
    at: list[int] = []
    gap = None
    for i, ch in enumerate(text):
        if ch.isspace() or ch == "⏎":
            if gap is None:
                gap = i
            continue
        if gap is not None and out:
            out.append(" ")
            at.append(gap)
        gap = None
        out.append(ch)
        at.append(i)
    return "".join(out), at


def find_folded(layer: str, start: int, end: int, needle: str) -> int | None:
    """Where ``needle`` (folded) first starts in ``layer[start:end]`` (folded), as an index
    of the layer, or None."""
    want = fold(needle)
    if not want:
        return None
    folded, at = fold_index(layer[start:end])
    hit = folded.find(want)
    return None if hit < 0 else start + at[hit]


# --------------------------------------------------------------------------- #
# rule 2: the skips, their blocks and the soft threshold (section 13.11)
# --------------------------------------------------------------------------- #

def _overlaps(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(a < span[1] and span[0] < b for a, b in spans)


def section_key(path: list[str]) -> str:
    """A section path as the rendering, the index and the report name it."""
    return " > ".join(path) or LEAD


def is_furniture(path: list[str]) -> bool:
    """The lead (no section) or the back matter (a top heading of :data:`BACK_MATTER`)."""
    return not path or str(path[0]).strip().casefold() in BACK_MATTER


def skip_order(doc: dict) -> list[tuple[int, dict]]:
    """``[(index in doc.skips, skip)]`` in span order: the order skip ids number.  A skip
    with no span (the gate refuses one) is not numbered."""
    rows = [(i, s) for i, s in enumerate(doc.get("skips") or [])
            if isinstance(s, dict) and span_of(s)]
    return sorted(rows, key=lambda r: (span_of(r[1]), r[0]))


def skip_id(round_: int, n: int) -> str:
    return f"r{round_}-s{n:03d}"


def skip_number(value: str) -> tuple[int, int] | None:
    """``(round, n)`` of a skip id, else None."""
    m = SKIP_ID.match(str(value)) if isinstance(value, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def _paragraph_rows(paragraphs: list[dict]) -> list[dict]:
    """``paragraphs.json``'s entries with a span, each with its section path."""
    out = []
    for p in paragraphs or []:
        span = span_of(p) if isinstance(p, dict) else None
        if span:
            section = p.get("section")
            out.append({"span": span, "block": str(p.get("block") or ""),
                        "math": bool(p.get("math")),
                        "section": [str(s) for s in section] if isinstance(section, list)
                        else []})
    return sorted(out, key=lambda r: (r["span"][0], -r["span"][1]))


def math_under_skips(doc: dict, paragraphs: list[dict]) -> list[dict]:
    """The math paragraphs (``paragraphs.json`` entries with ``math``) a skip overlaps and
    no passage holds: what the gate's ``skipped.math_paragraphs`` counts."""
    kept = [s for p in doc.get("passages") or [] if (s := span_of(p))]
    skips = [span_of(s) for _i, s in skip_order(doc)]
    return [p for p in _paragraph_rows(paragraphs)
            if p["math"] and not _overlaps(p["span"], kept) and _overlaps(p["span"], skips)]


def section_counts(doc: dict, paragraphs: list[dict]) -> dict[str, dict]:
    """``{section: {"skipped", "math", "furniture", "path", "first"}}``: each section's math
    paragraphs and those of them under a skip and no passage, in reading order."""
    under = {tuple(p["span"]) for p in math_under_skips(doc, paragraphs)}
    out: dict[str, dict] = {}
    for p in _paragraph_rows(paragraphs):
        if not p["math"]:
            continue
        key = section_key(p["section"])
        row = out.setdefault(key, {"skipped": 0, "math": 0,
                                   "furniture": is_furniture(p["section"]),
                                   "path": p["section"], "first": p["span"][0]})
        row["math"] += 1
        row["skipped"] += tuple(p["span"]) in under
    return out


def mostly_skipped(counts: dict[str, dict]) -> list[str]:
    """The sections, not furniture, with at least :data:`MOSTLY_SKIPPED_MIN` math paragraphs
    under a skip and no passage and more than :data:`MOSTLY_SKIPPED_SHARE` of theirs so."""
    rows = [(row["first"], key) for key, row in counts.items()
            if not row["furniture"] and row["skipped"] >= MOSTLY_SKIPPED_MIN
            and row["skipped"] > MOSTLY_SKIPPED_SHARE * row["math"]]
    return [key for _first, key in sorted(rows)]


def _union_length(spans: list[tuple[int, int]]) -> int:
    total, end = 0, None
    for a, b in sorted(spans):
        if end is None or a >= end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def skip_report(doc: dict, paragraphs: list[dict]) -> dict:
    """What the round's document skips (the receipt's ``audit.skips``, section 13.11):
    ``{skips, chars, math_paragraphs, furniture_math_paragraphs, by_section, mostly_skipped}``,
    ``math_paragraphs`` the gate's count, ``by_section`` the sections with one under a skip."""
    counts = section_counts(doc, paragraphs)
    under = math_under_skips(doc, paragraphs)
    spans = [span_of(s) for _i, s in skip_order(doc)]
    return {"skips": len(spans), "chars": _union_length(spans),
            "math_paragraphs": len(under),
            "furniture_math_paragraphs": sum(is_furniture(p["section"]) for p in under),
            "by_section": {key: {"skipped": row["skipped"], "math": row["math"]}
                           for key, row in counts.items() if row["skipped"]},
            "mostly_skipped": mostly_skipped(counts)}


def skipped_math_outside(report: dict | None) -> int | None:
    """The report's math paragraphs under skips outside the lead and the back matter."""
    if not isinstance(report, dict):
        return None
    return int(report.get("math_paragraphs") or 0) - int(report.get("furniture_math_paragraphs")
                                                         or 0)


def _names_proof_or_example(path: list[str]) -> bool:
    return any(KIND_RULE_SECTION.search(s) for s in path)


def skip_facts(doc: dict, paragraphs: list[dict], round_: int) -> list[dict]:
    """Every numbered skip of ``doc`` with what the rendering, the validator and the merge
    read of it (section 13.11), in span order:

    ``id`` (``r<round>-s<nnn>``), ``index`` (in ``doc.skips``), ``span``, ``reason``,
    ``blocks`` (the outermost ``paragraphs.json`` blocks it overlaps, each ``{span, block,
    math, held, section}``, ``math`` when an entry inside it is), ``math`` and ``unheld``
    (the math entries it overlaps, and those no passage holds), ``unheld_outside`` (those
    outside the furniture), ``sections`` (its blocks' sections, in order), ``furniture``
    (every block in the lead or the back matter), ``in_proof``, ``in_example``, ``mostly``
    (a section of its is mostly skipped), ``partial`` (it starts or ends inside a block),
    ``inside`` (it lies inside a kept passage) and ``rule``: the skip rule applies -- it
    overlaps a math entry no passage holds, outside the furniture, whose section names a
    proof or an example or is mostly skipped.
    """
    kept = [s for p in doc.get("passages") or [] if (s := span_of(p))]
    rows = _paragraph_rows(paragraphs)
    mostly = set(mostly_skipped(section_counts(doc, paragraphs)))
    out = []
    for n, (index, skip) in enumerate(skip_order(doc), 1):
        a, b = span_of(skip)
        over = [p for p in rows if p["span"][0] < b and a < p["span"][1]]
        blocks: list[dict] = []
        for p in over:
            if blocks and p["span"][0] < blocks[-1]["span"][1]:
                blocks[-1]["math"] = blocks[-1]["math"] or p["math"]
                continue
            blocks.append({"span": list(p["span"]), "block": p["block"], "math": p["math"],
                           "section": p["section"]})
        for block in blocks:
            block["held"] = _overlaps(tuple(block["span"]), kept)
        math = [p for p in over if p["math"]]
        unheld = [p for p in math if not _overlaps(p["span"], kept)]
        outside = [p for p in unheld if not is_furniture(p["section"])]
        sections: list[str] = []
        for block in blocks:
            key = section_key(block["section"])
            if key not in sections:
                sections.append(key)
        paths = [block["section"] for block in blocks]
        out.append({
            "id": skip_id(round_, n), "index": index, "span": [a, b],
            "reason": str(skip.get("reason") or ""), "blocks": blocks,
            "math": len(math), "unheld": len(unheld), "unheld_outside": len(outside),
            "sections": sections,
            "furniture": bool(blocks) and all(is_furniture(path) for path in paths),
            "in_proof": any(re.search(r"\bproofs?\b", s, re.IGNORECASE)
                            for path in paths for s in path),
            "in_example": any(re.search(r"\bexamples?\b", s, re.IGNORECASE)
                              for path in paths for s in path),
            "mostly": any(section_key(path) in mostly for path in paths),
            "partial": any(block["span"][0] < a or block["span"][1] > b for block in blocks),
            "inside": any(x <= a and b <= y for x, y in kept),
            "rule": any(_names_proof_or_example(p["section"])
                        or section_key(p["section"]) in mostly for p in outside),
        })
    return out


def bare_skips(doc: dict, round_: int) -> list[dict]:
    """The skips' ids, spans and reasons alone: what a check without the source's tables
    knows (``rule`` and ``unheld_outside`` unknown, None)."""
    return [{"id": skip_id(round_, n), "index": index, "span": list(span_of(skip)),
             "reason": str(skip.get("reason") or ""), "rule": None, "unheld_outside": None}
            for n, (index, skip) in enumerate(skip_order(doc), 1)]


def skip_hints(fact: dict) -> list[str]:
    """A skip's rendering hints (:data:`SKIP_HINTS`)."""
    flags = {"skip-rule": fact["rule"], "in-proof": fact["in_proof"],
             "in-example": fact["in_example"], "mostly-skipped": fact["mostly"],
             "partial": fact["partial"], "furniture": fact["furniture"]}
    return [hint for hint in SKIP_HINTS if flags[hint]]


def skip_text(layer: str, fact: dict) -> str:
    """The layer a skip holds, folded: where its evidence and statements are looked for."""
    a, b = fact["span"]
    return fold(layer[a:b])


# --------------------------------------------------------------------------- #
# validation of each file, against what it answers
# --------------------------------------------------------------------------- #

def _header(data: dict, kind: str, round_: int | None, source_id: str, where: str,
            problems: list[str]) -> None:
    if round_ is not None and data.get("round") != round_:
        problems.append(f"{where}.round is {data.get('round')!r}, not {round_}")
    if data.get("source_id") != source_id:
        problems.append(f"{where}.source_id is {data.get('source_id')!r}, not the document's "
                        f"{source_id!r}")


def _exactly_once(seen: list[str], expected: Iterable[str], what: str, where: str,
                  problems: list[str]) -> None:
    expected = list(expected)
    want = set(expected)
    counts: dict[str, int] = {}
    for key in seen:
        counts[key] = counts.get(key, 0) + 1
    for key in sorted(k for k, n in counts.items() if n > 1):
        problems.append(f"{where}: {what} {key} is judged {counts[key]} times, not once")
    for key in sorted(set(counts) - want):
        problems.append(f"{where}: {what} {key} is not one this file is to judge")
    missing = [k for k in expected if k not in counts]
    if missing:
        shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        problems.append(f"{where}: {len(missing)} {what}(s) not judged: {shown}")


def _verdict_rules(row: dict, where: str, problems: list[str], *, items: dict[str, dict]) -> None:
    """The rules one judged passage row is held to beyond its types."""
    verdict, rule = row.get("verdict"), row.get("rule")
    if verdict == VERDICT_CLEAN:
        if rule not in (0, None):
            problems.append(f"{where}: a clean verdict gives rule 0, not {rule}")
    else:
        if rule == 0:
            problems.append(f"{where}: verdict {verdict} names no rule (give the 3.4 rule, 1-10)")
        if not str(row.get("reason") or "").strip():
            problems.append(f"{where}: verdict {verdict} gives no reason")
        if "action" in row and not str(row.get("action") or "").strip():
            problems.append(f"{where}: verdict {verdict} gives no action a worker can apply")
    merge_with = row.get("merge_with")
    if verdict == "over_cut" and not merge_with:
        problems.append(f"{where}: over_cut names no merge_with (the neighbour it merges into)")
    if merge_with:
        if merge_with == row.get("item"):
            problems.append(f"{where}: merge_with names the passage itself")
        elif merge_with not in items:
            problems.append(f"{where}: merge_with {merge_with} is no passage of the document")


def validate_audit(data: Any, doc: dict, *, expect: list[str] | None = None,
                   rule: int = DEFAULT_RULE, skips: list[dict] | None = None,
                   expect_skips: list[str] | None = None) -> list[str]:
    """Round 1's auditor file: every formal passage it is to judge, judged exactly once;
    under rule 2 also every skip it is to judge (``expect_skips``, a part's; else every
    skip of ``skips``, the document's facts or its index rows: :func:`validate_skip_rows`)."""
    problems: list[str] = []
    if not check_record(data, "audit", "audit", problems, rule=rule):
        return problems
    _header(data, "audit", 1, str(doc.get("source_id")), "audit", problems)
    items = by_item(doc)
    formal = formal_items(doc)
    seen = []
    for i, row in enumerate(data["passages"]):
        where = f"audit.passages[{i}]"
        item = row["item"]
        seen.append(item)
        passage = items.get(item)
        if passage is None:
            problems.append(f"{where}: {item} is no passage of the document")
            continue
        if not is_formal(passage):
            problems.append(f"{where}: {item} is scoped {passage.get('scope')}, not formal_math "
                            f"(an expository passage that should be formal goes in "
                            f"expository_flags)")
        if row["verdict"] == "should_be_formal":
            problems.append(f"{where}: should_be_formal is for expository_flags, not a formal "
                            f"passage")
        _verdict_rules(row, where, problems, items=items)
        if row.get("kind_other_ok"):
            if passage.get("passage_kind") != KIND_OTHER:
                problems.append(f"{where}: kind_other_ok on a passage of kind "
                                f"{passage.get('passage_kind')}, not other")
            if not row["reason"].strip():
                problems.append(f"{where}: kind_other_ok needs a reason why other is right")
    want = [i for i in formal if expect is None or i in set(expect)]
    if expect is not None:
        for key in sorted(set(expect) - set(formal)):
            problems.append(f"audit: the part names {key}, which is no formal passage")
    _exactly_once(seen, want, "formal passage", "audit.passages", problems)
    flagged = []
    for i, row in enumerate(data["expository_flags"]):
        where = f"audit.expository_flags[{i}]"
        passage = items.get(row["item"])
        flagged.append(row["item"])
        if passage is None:
            problems.append(f"{where}: {row['item']} is no passage of the document")
        elif is_formal(passage):
            problems.append(f"{where}: {row['item']} is already formal_math")
        if row["rule"] == 0:
            problems.append(f"{where}: names no rule (give the 3.4 rule, 1-10)")
        if not row["reason"].strip() or not row["action"].strip():
            problems.append(f"{where}: gives no reason or no action")
    for key in sorted({k for k in flagged if flagged.count(k) > 1}):
        problems.append(f"audit.expository_flags: {key} is flagged more than once")
    if rule >= 2:
        facts = skips if skips is not None else bare_skips(doc, 1)
        validate_skip_rows(data["skips"], "audit.skips", facts, items, problems)
        want_skips = [f["id"] for f in facts
                      if expect_skips is None or f["id"] in set(expect_skips)]
        if expect_skips is not None:
            for key in sorted(set(expect_skips) - {f["id"] for f in facts}):
                problems.append(f"audit: the part names {key}, which is no skip of the "
                                f"document")
        _exactly_once([r["skip"] for r in data["skips"]], want_skips, "skip", "audit.skips",
                      problems)
    return problems


def _statement_rows(rows: list[dict], where: str, problems: list[str]) -> None:
    """A skip verdict's statements: their number and each one's words and kind."""
    if len(rows) > SKIP_STATEMENTS_MAX:
        problems.append(f"{where} holds {len(rows)} statements, more than "
                        f"{SKIP_STATEMENTS_MAX} (give the first ones; the repair cuts the rest)")
    kinds = _passage_vocab()["passage_kind"]
    for i, st in enumerate(rows):
        at = f"{where}[{i}]"
        n = words(st["at"])
        if not SKIP_AT_WORDS[0] <= n <= SKIP_AT_WORDS[1]:
            problems.append(f"{at}.at has {n} words, not {SKIP_AT_WORDS[0]} to "
                            f"{SKIP_AT_WORDS[1]} (verbatim, where the statement starts)")
        if st["passage_kind"] not in kinds:
            problems.append(f"{at}.passage_kind is {st['passage_kind']!r}, not one of "
                            f"{', '.join(sorted(kinds))}")


def _skip_verdict_rules(row: dict, where: str, items: dict[str, dict],
                        problems: list[str]) -> None:
    """One skip verdict row beyond its types: a defect names its rule, reason, action and
    what it hides (statements or the passage it finishes); a clean row names none."""
    verdict, rule = row.get("verdict"), row.get("rule")
    if verdict == VERDICT_CLEAN:
        if rule != 0:
            problems.append(f"{where}: a clean skip gives rule 0, not {rule}")
        if row.get("statements"):
            problems.append(f"{where}: a clean skip lists no statements")
        if row.get("merge_with"):
            problems.append(f"{where}: a clean skip names no merge_with")
        return
    if rule == 0:
        problems.append(f"{where}: verdict {verdict} names no rule (give the 3.4 rule, 1-10)")
    for key in ("reason", "action"):
        if key in row and not str(row.get(key) or "").strip():
            problems.append(f"{where}: verdict {verdict} gives no {key}")
    if not row.get("statements") and not row.get("merge_with"):
        problems.append(f"{where}: {verdict} lists no statement and names no merge_with (the "
                        f"kept passage the skipped text belongs to)")
    if row.get("merge_with") and row["merge_with"] not in items:
        problems.append(f"{where}: merge_with {row['merge_with']} is no passage of the document")
    _statement_rows(row.get("statements") or [], f"{where}.statements", problems)


def validate_skip_rows(rows: list[dict], where: str, facts: list[dict], items: dict[str, dict],
                       problems: list[str]) -> None:
    """An auditor's ``skips`` rows: each a skip of the document, judged ``clean`` or
    ``skipped_statements`` by :func:`_skip_verdict_rules`, and ``skip_ok`` -- the skip rule's
    exemption -- only on a clean row, with its reason, of a skip the rule applies to (when
    the facts say: an index row or :func:`skip_facts`; ids alone leave it unknown)."""
    by_id = {f["id"]: f for f in facts}
    for i, row in enumerate(rows):
        at = f"{where}[{i}]"
        fact = by_id.get(row["skip"])
        if fact is None:
            problems.append(f"{at}: {row['skip']} is no skip of the document")
        _skip_verdict_rules(row, at, items, problems)
        if row.get("skip_ok"):
            if row["verdict"] != VERDICT_CLEAN:
                problems.append(f"{at}: skip_ok goes on a clean row only")
            if not row["reason"].strip():
                problems.append(f"{at}: skip_ok needs a reason why the skip is right")
            if fact is not None and fact.get("rule") is False:
                problems.append(f"{at}: skip_ok on {row['skip']}, which the skip rule does not "
                                f"judge (no HINT skip-rule)")


def validate_check(data: Any, doc: dict, previous: dict, collateral: dict | None, *,
                   round_: int, expect: list[str] | None = None,
                   expect_collateral: list[str] | None = None,
                   rule: int = DEFAULT_RULE) -> list[str]:
    """A checker file of round ``round_``: every verdict the last round asked for judged once,
    every collateral change judged once, new defects on passages of the document.

    A part file judges the verdicts ``expect`` names; with ``expect_collateral`` (the
    collateral ids of a part's expectation, possibly none) it judges exactly those
    collateral changes, and without it any it judges must be entries of the file."""
    problems: list[str] = []
    if not check_record(data, "check", "check", problems, rule=rule):
        return problems
    _header(data, "check", round_, str(doc.get("source_id")), "check", problems)
    items = by_item(doc)
    asked = [v["id"] for v in previous.get("verdicts") or []]
    want = asked if expect is None else [v for v in asked if v in set(expect)]
    if expect is not None:
        for key in sorted(set(expect) - set(asked)):
            problems.append(f"check: the part names {key}, which the last round did not ask for")
    _exactly_once([r["verdict_id"] for r in data["checked"]], want, "verdict", "check.checked",
                  problems)
    for i, row in enumerate(data["checked"]):
        if row["status"] != "applied" and not row["reason"].strip():
            problems.append(f"check.checked[{i}]: status {row['status']} gives no reason")
    entries = [e["id"] for e in (collateral or {}).get("collateral") or []]
    if expect is None or expect_collateral is not None:
        wanted = entries if expect_collateral is None else \
            [e for e in entries if e in set(expect_collateral)]
        if expect_collateral is not None:
            for key in sorted(set(expect_collateral) - set(entries)):
                problems.append(f"check: the part names {key}, which is no collateral entry")
        _exactly_once([r["id"] for r in data["collateral"]], wanted, "collateral change",
                      "check.collateral", problems)
    else:
        for i, row in enumerate(data["collateral"]):
            if row["id"] not in entries:
                problems.append(f"check.collateral[{i}]: {row['id']} is no collateral entry")
    for i, row in enumerate(data["collateral"]):
        if row["harmful"] and not row["reason"].strip():
            problems.append(f"check.collateral[{i}]: a harmful change gives no reason")
    for i, row in enumerate(data["new"]):
        where = f"check.new[{i}]"
        if row["item"] not in items:
            problems.append(f"{where}: {row['item']} is no passage of the document")
        if row["verdict"] == VERDICT_CLEAN:
            problems.append(f"{where}: a new entry is a defect, never clean")
        _verdict_rules(row, where, problems, items=items)
    known = set(asked) | {h["id"] for h in previous.get("history") or []}
    for i, row in enumerate(data["regressions"]):
        if row["verdict_id"] not in known:
            problems.append(f"check.regressions[{i}]: {row['verdict_id']} is no verdict of an "
                            f"earlier round")
        if not row["reason"].strip():
            problems.append(f"check.regressions[{i}]: gives no reason")
    return problems


def validate_skeptic(data: Any, doc: dict, flags: dict, *, round_: int,
                     expect: list[str] | None = None, rule: int = DEFAULT_RULE,
                     skips: list[dict] | None = None,
                     expect_skips: list[str] | None = None) -> list[str]:
    """The skeptic's file: every flag and every dispute judged once, its hunt over passages
    no flag names, at least the floor of them in round 1.

    ``expect`` is a part's formal passages (a chunked skeptic call, its flags the part's):
    the hunt is then over that part's unflagged formal passages, and its floor theirs.
    The floor is superadditive, so parts that each meet theirs meet the document's.
    Under rule 2 a skip flag is upheld only as ``skipped_statements`` and a passage flag
    never so, and in round 1 ``examined_skips`` holds every skip of the part
    (``expect_skips``, else of ``skips``) no flag names that holds a math paragraph no
    passage holds outside the furniture (its fact's ``unheld_outside``)."""
    problems: list[str] = []
    if not check_record(data, "skeptic", "skeptic", problems, rule=rule):
        return problems
    _header(data, "skeptic", round_, str(doc.get("source_id")), "skeptic", problems)
    items = by_item(doc)
    _exactly_once([r["flag_id"] for r in data["verdicts"]],
                  [f["flag_id"] for f in flags.get("flags") or []], "flag", "skeptic.verdicts",
                  problems)
    for i, row in enumerate(data["verdicts"]):
        where = f"skeptic.verdicts[{i}]"
        if row["upheld"]:
            if row["verdict"] == VERDICT_CLEAN:
                problems.append(f"{where}: an upheld flag names a defect, never clean")
            if row["rule"] == 0:
                problems.append(f"{where}: an upheld flag names its rule (1-10)")
        if not row["reason"].strip():
            problems.append(f"{where}: gives no reason")
    for i, row in enumerate(data["missed"]):
        where = f"skeptic.missed[{i}]"
        if row["item"] not in items:
            problems.append(f"{where}: {row['item']} is no passage of the document")
        _verdict_rules(row, where, problems, items=items)
    _exactly_once([r["verdict_id"] for r in data["disputes"]],
                  [d["verdict_id"] for d in flags.get("disputes") or []], "dispute",
                  "skeptic.disputes", problems)
    flagged = {f["item"] for f in flags.get("flags") or []}
    unflagged = [i for i in formal_items(doc) if i not in flagged
                 and (expect is None or i in set(expect))]
    for i, item in enumerate(data["examined"]):
        if item not in items:
            problems.append(f"skeptic.examined[{i}]: {item} is no passage of the document")
        elif expect is not None and item not in set(expect) and item not in flagged:
            problems.append(f"skeptic.examined[{i}]: {item} is no formal passage of this part")
        elif item in flagged:
            problems.append(f"skeptic.examined[{i}]: {item} is flagged; the hunt is over the "
                            f"passages no flag names")
    if round_ == 1:
        need = skeptic_floor(len(unflagged))
        got = len({i for i in data["examined"] if i in set(unflagged)})
        if got < need:
            problems.append(f"skeptic.examined holds {got} unflagged formal passage(s); the "
                            f"hunt judges at least {need}")
    if rule >= 2:
        _skeptic_skips(data, doc, flags, round_=round_, skips=skips,
                       expect_skips=expect_skips, problems=problems)
    return problems


def _skeptic_skips(data: dict, doc: dict, flags: dict, *, round_: int,
                   skips: list[dict] | None, expect_skips: list[str] | None,
                   problems: list[str]) -> None:
    """Rule 2's part of a skeptic file: the verdicts on skip flags, the skip hunt and its
    missed skips."""
    facts = skips if skips is not None else bare_skips(doc, 1)
    by_id = {f["id"]: f for f in facts}
    items = by_item(doc)
    kinds = {f["flag_id"]: bool(f.get("skip")) for f in flags.get("flags") or []}
    for i, row in enumerate(data["verdicts"]):
        if not row["upheld"] or row["flag_id"] not in kinds:
            continue
        if kinds[row["flag_id"]] and row["verdict"] != SKIP_VERDICT:
            problems.append(f"skeptic.verdicts[{i}]: a skip flag is upheld as {SKIP_VERDICT} "
                            f"or refuted, not {row['verdict']}")
        elif not kinds[row["flag_id"]] and row["verdict"] == SKIP_VERDICT:
            problems.append(f"skeptic.verdicts[{i}]: {SKIP_VERDICT} is a skip's verdict, not "
                            f"a passage's")
    flagged = {f["skip"] for f in flags.get("flags") or [] if f.get("skip")}
    mine = [f for f in facts if expect_skips is None or f["id"] in set(expect_skips)]
    for i, sid in enumerate(data["examined_skips"]):
        if sid not in by_id:
            problems.append(f"skeptic.examined_skips[{i}]: {sid} is no skip of the document")
        elif expect_skips is not None and sid not in set(expect_skips):
            problems.append(f"skeptic.examined_skips[{i}]: {sid} is no skip of this part")
        elif sid in flagged:
            problems.append(f"skeptic.examined_skips[{i}]: {sid} is flagged; the hunt is over "
                            f"the skips no flag names")
    if round_ == 1:
        owed = [f["id"] for f in mine if f["id"] not in flagged and f.get("unheld_outside")]
        missing = [sid for sid in owed if sid not in set(data["examined_skips"])]
        if missing:
            problems.append(f"skeptic.examined_skips leaves out {len(missing)} unflagged "
                            f"skip(s) over math no passage holds: {', '.join(missing[:10])}"
                            + (" …" if len(missing) > 10 else ""))
    for i, row in enumerate(data["missed_skips"]):
        at = f"skeptic.missed_skips[{i}]"
        if row["skip"] not in by_id:
            problems.append(f"{at}: {row['skip']} is no skip of the document")
        elif expect_skips is not None and row["skip"] not in set(expect_skips):
            problems.append(f"{at}: {row['skip']} is no skip of this part")
        _skip_verdict_rules(row, at, items, problems)
        if not row["evidence"].strip():
            problems.append(f"{at}: gives no evidence (verbatim, from inside the skip)")


def validate_repair(data: Any, verdicts: dict, *, source_id: str,
                    rule: int = DEFAULT_RULE) -> list[str]:
    """The worker's report: one entry per verdict the round asked for."""
    problems: list[str] = []
    if not check_record(data, "repair", "repair", problems, rule=rule):
        return problems
    _header(data, "repair", verdicts.get("round"), source_id, "repair", problems)
    _exactly_once([e["verdict_id"] for e in data["entries"]],
                  [v["id"] for v in verdicts.get("verdicts") or []], "verdict",
                  "repair.entries", problems)
    for i, row in enumerate(data["entries"]):
        where = f"repair.entries[{i}]"
        if row["status"] != "applied" and not row["why"].strip():
            problems.append(f"{where}: status {row['status']} gives no why")
        if row["status"] == "gate_refused" and not str(row.get("gate_line") or "").strip():
            problems.append(f"{where}: gate_refused quotes no gate_line (the refusal, verbatim)")
    return problems


def op_problems(op: Any, where: str, *, verdict_ids: set[str] | None = None,
                rule: int = DEFAULT_RULE) -> list[str]:
    """One edit operation's shape: its keys, its citation and its fields."""
    problems: list[str] = []
    if not check_record(op, "op", where, problems, rule=rule):
        return problems
    name = op["op"]
    need, may = OP_KEYS_BY_RULE[rule][name]
    for key in need:
        if key not in op:
            problems.append(f"{where}: {name} needs {key!r}")
    for key in sorted(set(op) - {"op", "verdict", "neighbour_of"} - set(need) - set(may)):
        problems.append(f"{where}: {name} does not take {key!r}")
    cites = [k for k in ("verdict", "neighbour_of") if k in op]
    if len(cites) != 1:
        problems.append(f"{where}: an operation cites exactly one verdict, as verdict (the "
                        f"verdict it applies) or neighbour_of (a neighbour's end or start that "
                        f"verdict forces)")
    elif verdict_ids is not None and op[cites[0]] not in verdict_ids:
        problems.append(f"{where}: {cites[0]} {op[cites[0]]} is no verdict of this round")
    for key in ("fields", "new", "earlier"):
        if key in op:
            new = (key == "new") or (name == "add" and key == "fields")
            problems.extend(field_problems(op[key], f"{where}.{key}", new=new))
    if name == "unskip" and "keep" in op and not isinstance(op["keep"], list):
        problems.append(f"{where}: unskip's keep is a list of pieces {{span, reason?}}")
    if name == "merge" and rule >= 2 and isinstance(op.get("keep"), list):
        problems.append(f"{where}.keep is a list; merge keeps one item")
    if name == "merge":
        drops = op.get("drop") if isinstance(op.get("drop"), list) else [op.get("drop")]
        if op.get("keep") in drops:
            problems.append(f"{where}: merge keeps and drops the same passage")
        if any(d in drops[:i] for i, d in enumerate(drops)):
            problems.append(f"{where}: merge drops the same passage twice")
    return problems


#: the passage vocabulary the edit fields are held to: the gate's own inlined copies
def _passage_vocab() -> dict[str, frozenset]:
    import verify_locate as vl  # noqa: PLC0415 - the gate directory's sibling
    return {"scope": vl.SCOPES, "passage_kind": vl.PASSAGE_KINDS,
            "prominence": vl.PROMINENCE, "shape": vl.COMPOUND_SHAPES}


def field_problems(fields: Any, where: str, *, new: bool = False) -> list[str]:
    """The fields an operation sets: names of :data:`EDIT_FIELDS`, each a value the
    inventory document admits (``null`` removes ``until``, ``shape`` and ``conjuncts``)."""
    if not isinstance(fields, dict) or not fields:
        return [f"{where} is not an object of fields"]
    problems = []
    allowed = set(EDIT_FIELDS) | (set(NEW_PASSAGE_PLACE) if new else set())
    for key in sorted(set(fields) - allowed):
        problems.append(f"{where}: {key!r} is not a field an operation sets (one of "
                        f"{', '.join(sorted(allowed))})")
    if new:
        for key in NEW_PASSAGE_FIELDS:
            if key not in fields:
                problems.append(f"{where}: a new passage needs {key!r}")
        if "quote_end" in fields:
            problems.append(f"{where}: a new passage gives its quote whole, not quote_end")
    vocab = _passage_vocab()
    for key in ("scope", "passage_kind", "prominence"):
        if key in fields and fields[key] not in vocab[key]:
            problems.append(f"{where}.{key} is {fields[key]!r}, not one of "
                            f"{', '.join(sorted(vocab[key]))}")
    if "statement" in fields and not (isinstance(fields["statement"], str)
                                      and fields["statement"].strip()):
        problems.append(f"{where}.statement is empty")
    if "shape" in fields and fields["shape"] is not None \
            and fields["shape"] not in vocab["shape"]:
        problems.append(f"{where}.shape is {fields['shape']!r}, not one of "
                        f"{', '.join(sorted(vocab['shape']))}")
    if "conjuncts" in fields and fields["conjuncts"] is not None:
        value = fields["conjuncts"]
        if not (isinstance(value, list) and value and all(
                isinstance(c, dict) and set(c) == {"id", "text"} for c in value)):
            problems.append(f"{where}.conjuncts is not a list of {{id, text}}")
    if "until" in fields and fields["until"] is not None and not (
            isinstance(fields["until"], str) and fields["until"]):
        problems.append(f"{where}.until is not a string of the layer (or null to remove it)")
    if "concept" in fields and fields["concept"] is not None and not (
            isinstance(fields["concept"], str) and QID.match(fields["concept"])):
        problems.append(f"{where}.concept is {fields['concept']!r}, not a QID or null")
    if "quote" in fields:
        problem = _type_problem(fields["quote"], "quote", f"{where}.quote")
        if problem:
            problems.append(problem)
    if "quote_end" in fields and not (isinstance(fields["quote_end"], str)
                                      and fields["quote_end"]):
        problems.append(f"{where}.quote_end is not the words the quote is to end with")
    if "quote" in fields and "quote_end" in fields:
        problems.append(f"{where}: give quote or quote_end, not both")
    for key in NEW_PASSAGE_PLACE:
        if key in fields:
            ok = (isinstance(fields[key], list) and all(isinstance(s, str) for s in fields[key])
                  if key == "section" else fields[key] is None or isinstance(fields[key], str))
            if not ok:
                want = "a list of strings" if key == "section" else "a string or null"
                problems.append(f"{where}.{key} is not {want}")
    return problems


def validate_ops(data: Any, doc: dict | None, *, verdict_ids: set[str] | None = None,
                 rule: int = DEFAULT_RULE) -> list[str]:
    """An ops file: its header and every operation's shape and citation."""
    problems: list[str] = []
    if not check_record(data, "ops", "ops", problems, rule=rule):
        return problems
    if doc is not None:
        _header(data, "ops", None, str(doc.get("source_id")), "ops", problems)
    for i, op in enumerate(data["ops"]):
        problems.extend(op_problems(op, f"ops.ops[{i}]", verdict_ids=verdict_ids, rule=rule))
    return problems


# --------------------------------------------------------------------------- #
# strip: what the skeptic sees
# --------------------------------------------------------------------------- #

def _start(doc: dict, item: str) -> int | None:
    passage = by_item(doc).get(item)
    span = span_of(passage) if passage else None
    return span[0] if span else None


def _order_key(doc: dict):
    order = {str(p.get("item")): n for n, p in enumerate(ordered(doc))}
    return lambda item: order.get(item, len(order))


def strip(round_: int, doc: dict, *, audits: list[dict] = (), checks: list[dict] = (),
          repair: dict | None = None, previous: dict | None = None,
          rule: int = DEFAULT_RULE) -> dict:
    """The flags file: the flags with only what the skeptic may see, in reading order.

    Round 1: every non-clean auditor row and every expository flag (as
    ``should_be_formal``), with the auditor's verdict, rule, merge_with and reason --
    never its actions, patterns, estimate or clean judgments.  Round 2 on: the
    checker's new entries, and the worker's disputes of the last round's verdicts.
    Under rule 2, round 1 also flags every ``skipped_statements`` skip row, as ``{flag_id,
    item: "", skip, span, verdict, rule, merge_with, reason}`` -- its statements and action
    withheld -- placed among the passage flags by where it starts.
    """
    if rule >= 2:
        return _strip2(round_, doc, audits=audits, checks=checks, repair=repair,
                       previous=previous, rule=rule)
    rows = []
    if round_ == 1:
        for audit in audits:
            for row in audit["passages"]:
                if row["verdict"] != VERDICT_CLEAN:
                    rows.append((row["item"], row["verdict"], row["rule"], row["merge_with"],
                                 row["reason"]))
            for row in audit["expository_flags"]:
                rows.append((row["item"], "should_be_formal", row["rule"], "", row["reason"]))
    else:
        for check in checks:
            for row in check["new"]:
                rows.append((row["item"], row["verdict"], row["rule"], row["merge_with"],
                             row["reason"]))
    key = _order_key(doc)
    rows.sort(key=lambda r: key(r[0]))
    flags = [{"flag_id": f"r{round_}-f{n:03d}", "item": item, "verdict": verdict, "rule": rule,
              "merge_with": merge_with, "reason": reason}
             for n, (item, verdict, rule, merge_with, reason) in enumerate(rows, 1)]
    disputes = []
    if round_ > 1 and repair is not None and previous is not None:
        asked = {v["id"]: v for v in previous.get("verdicts") or []}
        for entry in repair["entries"]:
            v = asked.get(entry["verdict_id"])
            if entry["status"] == "disputed" and v is not None:
                disputes.append({"verdict_id": v["id"], "item": v["item"],
                                 "verdict": v["verdict"], "rule": v["rule"],
                                 "action": v["action"], "worker_reason": entry["why"]})
    return {"schema": AUDIT_SCHEMA, "kind": "flags", "round": round_,
            "source_id": doc.get("source_id"), "flags": flags, "disputes": disputes}


def _strip2(round_: int, doc: dict, *, audits, checks, repair, previous, rule: int) -> dict:
    """:func:`strip` under rule 2: the passage flags of rule 1 and the skip flags, ordered
    by where each starts (a passage by its span, a skip by its own)."""
    starts = {str(p.get("item")): (span_of(p) or (math.inf,))[0] for p in ordered(doc)}
    key = _order_key(doc)
    spans = {f["id"]: f["span"] for f in bare_skips(doc, 1)}
    rows = []
    if round_ == 1:
        for audit in audits:
            for row in audit["passages"]:
                if row["verdict"] != VERDICT_CLEAN:
                    rows.append(((starts.get(row["item"], math.inf), 0, key(row["item"])),
                                 {"item": row["item"], "verdict": row["verdict"],
                                  "rule": row["rule"], "merge_with": row["merge_with"],
                                  "reason": row["reason"]}))
            for row in audit["expository_flags"]:
                rows.append(((starts.get(row["item"], math.inf), 0, key(row["item"])),
                             {"item": row["item"], "verdict": "should_be_formal",
                              "rule": row["rule"], "merge_with": "", "reason": row["reason"]}))
            for row in audit.get("skips") or []:
                if row["verdict"] == VERDICT_CLEAN:
                    continue
                span = spans.get(row["skip"]) or [math.inf, math.inf]
                rows.append(((span[0], 1, 0),
                             {"item": "", "skip": row["skip"], "span": spans.get(row["skip"]),
                              "verdict": row["verdict"], "rule": row["rule"],
                              "merge_with": row["merge_with"], "reason": row["reason"]}))
    else:
        for check in checks:
            for row in check["new"]:
                rows.append(((starts.get(row["item"], math.inf), 0, key(row["item"])),
                             {"item": row["item"], "verdict": row["verdict"],
                              "rule": row["rule"], "merge_with": row["merge_with"],
                              "reason": row["reason"]}))
    rows.sort(key=lambda r: r[0])
    flags = [{"flag_id": f"r{round_}-f{n:03d}", **row} for n, (_k, row) in enumerate(rows, 1)]
    disputes = []
    if round_ > 1 and repair is not None and previous is not None:
        asked = {v["id"]: v for v in previous.get("verdicts") or []}
        for entry in repair["entries"]:
            v = asked.get(entry["verdict_id"])
            if entry["status"] == "disputed" and v is not None:
                row = {"verdict_id": v["id"], "item": v["item"], "verdict": v["verdict"],
                       "rule": v["rule"], "action": v["action"], "worker_reason": entry["why"]}
                if v["verdict"] == SKIP_VERDICT:
                    row["span"] = v.get("span")
                disputes.append(row)
    return {"schema": AUDIT_SCHEMAS[rule], "kind": "flags", "round": round_,
            "source_id": doc.get("source_id"), "flags": flags, "disputes": disputes}


# --------------------------------------------------------------------------- #
# collateral: what changed that no operation asked for
# --------------------------------------------------------------------------- #

#: the fields a passage's content is compared on: everything but what passage_ids.py writes
CONTENT_FIELDS = ("quote", "until", "scope", "passage_kind", "prominence", "statement", "shape",
                  "conjuncts", "concept", "section", "fragment", "finder")


def content_key(passage: dict) -> str:
    return json.dumps({k: passage.get(k) for k in CONTENT_FIELDS}, sort_keys=True,
                      ensure_ascii=False)


def _brief(passage: dict | None) -> dict | None:
    if passage is None:
        return None
    return {"item": passage.get("item") or None, "span": passage.get("span"),
            **{k: passage.get(k) for k in ("scope", "passage_kind", "prominence", "statement",
                                           "until", "shape", "conjuncts", "concept")},
            "quote": passage.get("quote")}


def collateral(round_: int, snapshot: dict, doc: dict, ops: dict | None,
               verdicts: dict | None, *, rule: int = DEFAULT_RULE) -> dict:
    """The collateral file: every change between the reviewed snapshot and the document
    that the cited operations do not produce, and every passage they leave unnamed whose
    span still moved; under rule 2, every difference between the document's skips and
    what the operations make of the snapshot's (``skip_edit``, :func:`skip_edits`).

    The operations are re-applied to the snapshot (``inventory_edit.apply``); a passage of
    the document whose content (:data:`CONTENT_FIELDS`) that result does not hold, or one
    of the result the document does not hold, is a ``hand_edit``; a passage no operation
    names whose span differs from the snapshot's (a neighbour's merge or drop moved its
    default end) is ``moved``; an operation citing a verdict the round did not ask for is
    ``uncited_op``; operations the edit tool refuses on the snapshot are ``ops_refused``,
    and then every change is a hand edit.  The checker judges each entry harmful or not.
    """
    import inventory_edit  # noqa: PLC0415 - the gate directory's sibling

    entries: list[dict] = []

    def add(change: str, **fields) -> None:
        entries.append({"id": f"r{round_}-x{len(entries) + 1:03d}", "change": change, **fields})

    expected = snapshot
    named: set[str] = set()
    if ops is not None:
        asked = {v["id"] for v in (verdicts or {}).get("verdicts") or []}
        rows = ops.get("ops") if isinstance(ops, dict) and isinstance(ops.get("ops"), list) \
            else []
        for i, op in enumerate(rows):
            cite = (op.get("verdict") or op.get("neighbour_of")) if isinstance(op, dict) \
                else None
            if verdicts is not None and cite not in asked:
                add("uncited_op", op=i, cites=cite, before=None, after=None)
        problems = validate_ops(ops, doc, rule=rule)
        if not problems:
            if rule >= 2:
                result, _log, problems = inventory_edit.apply(
                    snapshot, rows, rule=rule, round_=ops.get("round"))
            else:
                result, _log, problems = inventory_edit.apply(snapshot, rows)
        if problems:
            add("ops_refused", problems=problems[:20], before=None, after=None)
        else:
            expected = result
            named = inventory_edit.named_items(rows)
    have: dict[str, list[dict]] = {}
    for p in doc.get("passages") or []:
        have.setdefault(content_key(p), []).append(p)
    want: dict[str, list[dict]] = {}
    for p in expected.get("passages") or []:
        want.setdefault(content_key(p), []).append(p)
    extra = [p for k, ps in have.items() for p in ps[len(want.get(k, [])):]]
    lost = [p for k, ps in want.items() for p in ps[len(have.get(k, [])):]]
    # pair a lost and an extra passage that share a quote: one passage edited by hand
    by_quote = {json.dumps(p.get("quote"), sort_keys=True): p for p in lost}
    for p in sorted(extra, key=lambda p: span_of(p) or (0, 0)):
        q = by_quote.pop(json.dumps(p.get("quote"), sort_keys=True), None)
        if q is not None:
            lost.remove(q)
        fields = sorted(k for k in CONTENT_FIELDS if q is not None and p.get(k) != q.get(k))
        add("hand_edit", item=p.get("item"), fields=fields, before=_brief(q), after=_brief(p))
    for q in lost:
        add("hand_edit", item=None, fields=[], before=_brief(q), after=None)
    before = {content_key(p): p for p in snapshot.get("passages") or []
              if str(p.get("item")) not in named}
    for p in doc.get("passages") or []:
        q = before.get(content_key(p))
        if q is not None and span_of(q) != span_of(p):
            add("moved", item=p.get("item"), was=q.get("item"), fields=["span"],
                before=_brief(q), after=_brief(p))
    if rule >= 2:
        for entry in skip_edits(snapshot, expected, doc, round_ - 1):
            add("skip_edit", **entry)
    return {"schema": AUDIT_SCHEMAS[rule], "kind": "collateral", "round": round_,
            "source_id": doc.get("source_id"), "collateral": entries}


def _skip_brief(skip: dict | None) -> dict | None:
    return None if skip is None else {"span": list(span_of(skip) or []),
                                      "reason": skip.get("reason")}


def skip_edits(snapshot: dict, expected: dict, doc: dict, snapshot_round: int) -> list[dict]:
    """The skips of ``doc`` that the operations do not make of the snapshot's (``expected``
    is their result), as ``{skip, before, after}``: a skip changed by hand (both), removed
    (``after`` null) or added (``before`` null), its ``skip`` the snapshot's skip whose span
    holds it, else one it overlaps (``r<round>-s<nnn>`` of the round the snapshot was
    reviewed in), else "".  A changed skip pairs a lost and a new one that overlap."""
    def key(s: dict) -> str:
        return json.dumps({"span": span_of(s), "reason": s.get("reason")}, sort_keys=True)

    want = [s for _i, s in skip_order(expected)]
    have = [s for _i, s in skip_order(doc)]
    pool: dict[str, int] = {}
    for s in want:
        pool[key(s)] = pool.get(key(s), 0) + 1
    extra = []
    for s in have:
        if pool.get(key(s)):
            pool[key(s)] -= 1
        else:
            extra.append(s)
    left = dict(pool)
    lost = []
    for s in want:
        if left.get(key(s)):
            left[key(s)] -= 1
            lost.append(s)
    ids = [(f["id"], tuple(f["span"])) for f in bare_skips(snapshot, max(1, snapshot_round))]

    def holder(s: dict) -> str:
        a, b = span_of(s)
        return next((sid for sid, (x, y) in ids if x <= a and b <= y), "") or next(
            (sid for sid, (x, y) in ids if x < b and a < y), "")

    out = []
    for s in extra:
        a, b = span_of(s)
        q = next((t for t in lost if span_of(t)[0] < b and a < span_of(t)[1]), None)
        if q is not None:
            lost.remove(q)
        out.append({"skip": holder(q if q is not None else s), "before": _skip_brief(q),
                    "after": _skip_brief(s)})
    for q in lost:
        out.append({"skip": holder(q), "before": _skip_brief(q), "after": None})
    return sorted(out, key=lambda e: (e["after"] or e["before"])["span"])


# --------------------------------------------------------------------------- #
# merge: the round's verdicts and the decision table
# --------------------------------------------------------------------------- #

def decide(round_: int, review_rounds: int, actionable: int, previous_actionable: int | None,
           harm: bool, formal: int, rounds: list[dict], *, rule: int = DEFAULT_RULE,
           skip_actionable: int = 0, skip_math: int | None = None,
           previous_skip_math: int | None = None) -> tuple[str, int | None, str]:
    """The decision table (rule ``audit/1``, and ``audit/2``): ``(decision, revert_to, why)``.

    * round 1: request changes when any verdict is actionable, else approve;
    * a middle round: request changes when at least T verdicts are actionable and fewer
      than the round before (progress); under ``audit/2`` also when any skip verdict is
      actionable, below T as well, and the round made progress -- fewer actionable
      verdicts, or fewer math paragraphs under skips and no passage outside the
      furniture (``skip_math`` against ``previous_skip_math``); otherwise it would approve;
    * an approval from round 2 on -- the final round's always is one -- stands only when
      the check found no ``wrong`` application, no harmful collateral and no regression;
      otherwise the document reverts to the best reviewed snapshot (:func:`best_round`).
    """
    if round_ == 1:
        if actionable:
            return "request_changes", None, f"{actionable} actionable verdict(s)"
        return "approve", None, "no actionable verdict"
    t = threshold(formal)
    if round_ < review_rounds and actionable >= t and previous_actionable is not None \
            and actionable < previous_actionable:
        return "request_changes", None, (f"{actionable} actionable verdict(s), at least T={t}, "
                                         f"fewer than round {round_ - 1}'s {previous_actionable}")
    if rule >= 2 and round_ < review_rounds and skip_actionable:
        fewer = previous_actionable is not None and actionable < previous_actionable
        freed = (skip_math is not None and previous_skip_math is not None
                 and skip_math < previous_skip_math)
        if fewer or freed:
            progress = (f"fewer than round {round_ - 1}'s {previous_actionable}" if fewer else
                        f"{skip_math} math paragraph(s) under skips outside the furniture, "
                        f"fewer than round {round_ - 1}'s {previous_skip_math}")
            return "request_changes", None, (f"{skip_actionable} skip verdict(s) actionable "
                                             f"({actionable} in all), {progress}")
    why = ("the final round" if round_ >= review_rounds else
           f"{actionable} actionable, below T={t}" if actionable < t else
           f"no progress on round {round_ - 1}'s {previous_actionable}")
    if not harm:
        return "approve", None, why
    best = best_round([r for r in rounds if r["round"] < round_])
    return "revert", best, (f"{why}, and the check found a wrong application, harmful "
                            f"collateral or a regression: revert to round {best}'s snapshot")


def best_round(rounds: list[dict]) -> int:
    """The best reviewed snapshot: of the rounds whose check found no harm, the one with
    the fewest actionable verdicts, the later on a tie (round 1 always qualifies)."""
    sound = [r for r in rounds if not r.get("harm")] or [{"round": 1, "actionable": 0}]
    return min(sound, key=lambda r: (r["actionable"], -r["round"]))["round"]


def _on_layer(evidence: str, layer_folded: str | None) -> bool:
    text = fold(evidence)
    if not text:
        return False
    return layer_folded is None or text in layer_folded


def _record(*, item: str, doc: dict, verdict: str, rule: int, merge_with: str, action: str,
            origin: str, **extra) -> dict:
    return {"item": item, "start": _start(doc, item), "verdict": verdict, "rule": rule,
            "merge_with": merge_with, "action": action, "origin": origin, **extra}


def _kind_rule(doc: dict, actionable: list[dict], exempt_starts: set,
               seen_starts: set) -> list[dict]:
    """The mechanical wrong_kind verdicts: :func:`kind_rule_applies`, less the passages the
    auditor said ``other`` is right for and those a verdict already settles."""
    settled = {v["item"] for v in actionable if v["verdict"] in KIND_SETTLED}
    out = []
    for p in ordered(doc):
        span = span_of(p)
        start = span[0] if span else None
        if not kind_rule_applies(p) or p.get("item") in settled:
            continue
        if start in exempt_starts or start in seen_starts:
            continue
        out.append(_record(item=str(p["item"]), doc=doc, verdict="wrong_kind",
                           rule=KIND_RULE_RULE, merge_with="", action=KIND_RULE_ACTION,
                           origin="kind_rule"))
    return out


ORIGIN_RANK = {o: n for n, o in enumerate(VERDICT_ORIGINS + SKIP_ORIGINS)}


def _number(round_: int, doc: dict, records: list[dict], rule: int = DEFAULT_RULE) -> list[dict]:
    """The round's verdict ids, in reading order: by passage (rule 1), by where each verdict's
    target starts, a passage or a skip (rule 2); the origin's rank breaks a tie."""
    if rule >= 2:
        records.sort(key=lambda v: (not isinstance(v.get("start"), int), v.get("start") or 0,
                                    ORIGIN_RANK[v["origin"]]))
    else:
        key = _order_key(doc)
        records.sort(key=lambda v: (key(v["item"]), ORIGIN_RANK[v["origin"]]))
    for n, v in enumerate(records, 1):
        v["id"] = f"r{round_}-v{n:03d}"
    return [{"id": v.pop("id"), **v} for v in records]


def _by_verdict(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in records:
        out[v["verdict"]] = out.get(v["verdict"], 0) + 1
    return dict(sorted(out.items()))


def merge_round1(doc: dict, audits: list[dict], skeptic: dict, *,
                 layer: str | None, rule: int = DEFAULT_RULE,
                 paragraphs: list[dict] | None = None) -> dict:
    """Round 1's verdicts: upheld flags plus the skeptic's evidenced missed entries plus the
    kind rule's, the refuted flags apart, with their counts; under rule 2 the skip flags,
    missed skips and the skip rule's too (:func:`_skips_round1`)."""
    layer_folded = fold(layer) if layer is not None else None
    flags = strip(1, doc, audits=audits, rule=rule)
    rows = {}
    for audit in audits:
        for row in audit["passages"]:
            rows[row["item"]] = row
        for row in audit["expository_flags"]:
            rows.setdefault(row["item"], {**row, "verdict": "should_be_formal", "merge_with": ""})
    rulings = {r["flag_id"]: r for r in skeptic["verdicts"]}
    actionable, refuted = [], []
    counts = {"formal": len(formal_items(doc)), "audited": sum(len(a["passages"]) for a in audits),
              "flagged": sum(1 for f in flags["flags"] if not f.get("skip")), "upheld": 0,
              "corrected": 0, "refuted": 0, "refuted_no_evidence": 0, "missed_added": 0,
              "missed_dropped": 0, "kind_rule": 0, "kind_other_ok": 0}
    for flag in flags["flags"]:
        ruling = rulings[flag["flag_id"]]
        if flag.get("skip"):
            continue                    # rule 2's skip flags: _skips_round1
        why = None
        if not ruling["upheld"]:
            why = "refuted"
        elif not _on_layer(ruling["evidence"], layer_folded):
            why = "no_evidence"
        if why:
            counts["refuted"] += 1
            counts["refuted_no_evidence"] += why == "no_evidence"
            refuted.append({"flag_id": flag["flag_id"], "item": flag["item"],
                            "verdict": flag["verdict"], "why": why,
                            "skeptic_reason": ruling["reason"]})
            continue
        row = rows[flag["item"]]
        counts["upheld"] += 1
        extra = {}
        if ruling["verdict"] != flag["verdict"]:
            counts["corrected"] += 1
            extra["corrected_from"] = flag["verdict"]
        actionable.append(_record(
            item=flag["item"], doc=doc, verdict=ruling["verdict"], rule=ruling["rule"],
            merge_with=flag["merge_with"], action=row["action"], origin="auditor",
            flag_id=flag["flag_id"], auditor_reason=flag["reason"],
            skeptic_reason=ruling["reason"], evidence=ruling["evidence"], **extra))
    _add_missed(doc, skeptic, actionable, counts, layer_folded)
    exempt = {}
    for item, row in rows.items():
        if row.get("kind_other_ok"):
            exempt[_start(doc, item)] = {"item": item, "start": _start(doc, item),
                                         "reason": row["reason"]}
    counts["kind_other_ok"] = len(exempt)
    kind = _kind_rule(doc, actionable, set(exempt), set())
    counts["kind_rule"] = len(kind)
    actionable.extend(kind)
    skips = None
    if rule >= 2:
        skips = _skips_round1(doc, audits, skeptic, flags, actionable, refuted, counts,
                              layer=layer, paragraphs=paragraphs or [])
    verdicts = _number(1, doc, actionable, rule)
    counts.update(_kind_counts(doc))
    counts["actionable"] = len(verdicts)
    counts["by_verdict"] = _by_verdict(verdicts)
    out = {"verdicts": verdicts, "refuted": refuted, "set_aside": [], "history": [],
           "kind_other_ok": sorted(exempt.values(), key=lambda e: (e["start"] is None,
                                                                   e["start"] or 0)),
           "kind_seen": sorted(s for s in {v["start"] for v in verdicts
                                           if v["verdict"] == "wrong_kind"} if s is not None),
           "counts": counts, "harm": False,
           "estimates": {"auditor": _estimate(audits), "skeptic":
                         skeptic["per_spec_formal_estimate"]}}
    if skips is not None:
        out.update(skips)
    return out


# rule 2: the skip verdicts of a merge (section 13.11)

def _in_skip(evidence: str, layer: str | None, span) -> bool:
    """Evidence holds for a skip when it is, folded, in the skip's own text."""
    text = fold(evidence)
    if not text:
        return False
    return layer is None or text in fold(layer[span[0]:span[1]])


def _statements(rows: list[dict], layer: str | None, span, counts: dict) -> list[dict]:
    """A skip verdict's statements whose ``at`` starts inside its span; the others dropped
    and counted (``statements_dropped``)."""
    out = []
    for st in rows:
        if layer is None or find_folded(layer, span[0], span[1], st["at"]) is not None:
            out.append({"at": st["at"], "until": st["until"],
                        "passage_kind": st["passage_kind"]})
        else:
            counts["statements_dropped"] += 1
    return out


def _skip_record(*, span, facts: list[dict], verdict: str, rule: int, merge_with: str,
                 action: str, origin: str, statements: list[dict], **extra) -> dict:
    """A skip verdict: its target is the span, and ``skips`` the skips of the round's
    snapshot that overlap it (``skip`` the one when there is one); ``unheld`` the math
    paragraphs under them that no passage holds (what a revision session weighs)."""
    span = [int(span[0]), int(span[1])]
    under = [f for f in facts if f["span"][0] < span[1] and span[0] < f["span"][1]]
    ids = [f["id"] for f in under]
    return {"item": "", "skip": ids[0] if len(ids) == 1 else "", "skips": ids, "span": span,
            "start": span[0], "verdict": verdict, "rule": rule, "merge_with": merge_with,
            "action": action, "origin": origin, "statements": statements,
            "unheld": sum(f["unheld"] for f in under), **extra}


SKIP_COUNTS_1 = ("skips", "skips_judged", "skips_flagged", "skips_upheld", "skips_refuted",
                 "skip_missed_added", "skip_missed_dropped", "skip_rule", "skip_ok",
                 "hidden_statements", "statements_dropped")
SKIP_COUNTS_LATER = ("skips", "skip_carried", "skip_missed_added", "skip_missed_dropped",
                     "skip_rule", "hidden_statements", "statements_dropped")


def _add_missed_skips(skeptic: dict, facts: list[dict], actionable: list[dict], counts: dict,
                      layer: str | None) -> None:
    by_id = {f["id"]: f for f in facts}
    for row in skeptic.get("missed_skips") or []:
        fact = by_id.get(row["skip"])
        settled = {sid for v in actionable if v["verdict"] == SKIP_VERDICT for sid in v["skips"]}
        if fact is None or not _in_skip(row["evidence"], layer, fact["span"]) \
                or fact["id"] in settled:
            counts["skip_missed_dropped"] += 1
            continue
        counts["skip_missed_added"] += 1
        statements = _statements(row["statements"], layer, fact["span"], counts)
        counts["hidden_statements"] += len(statements)
        actionable.append(_skip_record(
            span=fact["span"], facts=facts, verdict=SKIP_VERDICT, rule=row["rule"],
            merge_with=row["merge_with"], action=row["action"], origin="skeptic_missed",
            statements=statements, skeptic_reason=row["reason"], evidence=row["evidence"]))


def _skip_rule(facts: list[dict], actionable: list[dict], counts: dict, *,
               exempt: set[str], seen: list) -> None:
    """The skip rule's mechanical verdicts: every skip :func:`skip_facts` marks ``rule``,
    less those a skip verdict already settles, the auditor exempted (``exempt``, by id) and
    those inside a span an earlier round exempted or set aside (``seen``)."""
    settled = {sid for v in actionable if v["verdict"] == SKIP_VERDICT for sid in v["skips"]}
    for fact in facts:
        a, b = fact["span"]
        if not fact["rule"] or fact["id"] in settled or fact["id"] in exempt:
            continue
        if any(x <= a and b <= y for x, y in seen):
            continue
        counts["skip_rule"] += 1
        actionable.append(_skip_record(
            span=fact["span"], facts=facts, verdict=SKIP_VERDICT, rule=SKIP_RULE_RULE,
            merge_with="", action=SKIP_RULE_ACTION, origin="skip_rule", statements=[]))


def _skips_round1(doc: dict, audits: list[dict], skeptic: dict, flags: dict,
                  actionable: list[dict], refuted: list[dict], counts: dict, *,
                  layer: str | None, paragraphs: list[dict]) -> dict:
    """Rule 2's part of round 1's merge: each skip flag the skeptic upheld with evidence
    inside the skip (its statements those that start there), the skeptic's evidenced missed
    skips, and the skip rule, less the skips the auditor said ``skip_ok`` with a reason."""
    facts = skip_facts(doc, paragraphs, 1)
    by_id = {f["id"]: f for f in facts}
    rows = {r["skip"]: r for a in audits for r in a.get("skips") or []}
    rulings = {r["flag_id"]: r for r in skeptic["verdicts"]}
    counts.update({k: 0 for k in SKIP_COUNTS_1})
    counts["skips"] = len(facts)
    counts["skips_judged"] = len(rows)
    for flag in flags["flags"]:
        if not flag.get("skip"):
            continue
        counts["skips_flagged"] += 1
        ruling = rulings[flag["flag_id"]]
        fact = by_id.get(flag["skip"])
        why = None
        if not ruling["upheld"]:
            why = "refuted"
        elif fact is None or not _in_skip(ruling["evidence"], layer, fact["span"]):
            why = "no_evidence"
        if why:
            counts["skips_refuted"] += 1
            refuted.append({"flag_id": flag["flag_id"], "item": "", "skip": flag["skip"],
                            "verdict": flag["verdict"], "why": why,
                            "skeptic_reason": ruling["reason"]})
            continue
        counts["skips_upheld"] += 1
        row = rows[flag["skip"]]
        statements = _statements(row["statements"], layer, fact["span"], counts)
        counts["hidden_statements"] += len(statements)
        actionable.append(_skip_record(
            span=fact["span"], facts=facts, verdict=SKIP_VERDICT, rule=ruling["rule"],
            merge_with=flag["merge_with"], action=row["action"], origin="auditor",
            statements=statements, flag_id=flag["flag_id"], auditor_reason=flag["reason"],
            skeptic_reason=ruling["reason"], evidence=ruling["evidence"]))
    _add_missed_skips(skeptic, facts, actionable, counts, layer)
    exempt = [{"skip": sid, "span": by_id[sid]["span"], "reason": row["reason"]}
              for sid, row in rows.items()
              if row.get("skip_ok") and row["verdict"] == VERDICT_CLEAN
              and row["reason"].strip() and sid in by_id]
    exempt.sort(key=lambda e: e["span"])
    counts["skip_ok"] = len(exempt)
    _skip_rule(facts, actionable, counts, exempt={e["skip"] for e in exempt}, seen=[])
    return {"skip_ok": exempt, "skip_seen": [e["span"] for e in exempt],
            "skip_report": skip_report(doc, paragraphs)}


def _estimate(audits: list[dict]) -> int | None:
    values = [a["per_spec_formal_estimate"] for a in audits]
    return sum(values) if values else None


def _kind_counts(doc: dict) -> dict:
    formal = [p for p in doc.get("passages") or [] if is_formal(p)]
    return {"kind_other_formal": sum(p.get("passage_kind") == KIND_OTHER for p in formal),
            "kind_other_in_proof_or_example": sum(kind_rule_applies(p) for p in formal)}


def _add_missed(doc: dict, skeptic: dict, actionable: list[dict], counts: dict,
                layer_folded: str | None) -> None:
    have = {(v["item"], v["verdict"]) for v in actionable}
    for row in skeptic["missed"]:
        if row["verdict"] == VERDICT_CLEAN or not _on_layer(row["evidence"], layer_folded) \
                or (row["item"], row["verdict"]) in have:
            counts["missed_dropped"] += 1
            continue
        have.add((row["item"], row["verdict"]))
        counts["missed_added"] += 1
        actionable.append(_record(item=row["item"], doc=doc, verdict=row["verdict"],
                                  rule=row["rule"], merge_with=row["merge_with"],
                                  action=row["action"], origin="skeptic_missed",
                                  skeptic_reason=row["reason"], evidence=row["evidence"]))


def merge_later(round_: int, doc: dict, checks: list[dict], skeptic: dict, previous: dict,
                repair: dict | None, collateral_doc: dict, *, layer: str | None,
                rule: int = DEFAULT_RULE, paragraphs: list[dict] | None = None) -> dict:
    """Round 2 on: each verdict the last round asked for by its check status (or, when the
    worker disputed it, by the skeptic's ruling on the dispute), harmful collateral as
    ``revert`` verdicts, regressions re-issued, the checker's upheld new entries, and the
    kind rule over passages no earlier verdict judged.  Under rule 2 a skip verdict that
    goes back to the worker keeps its span and names the skips of this round's snapshot
    there, a skip verdict set aside adds its span to ``skip_seen``, and the skip rule runs
    over the skips no verdict and no earlier exemption settles."""
    layer_folded = fold(layer) if layer is not None else None
    flags = strip(round_, doc, checks=checks, repair=repair, previous=previous, rule=rule)
    status = {r["verdict_id"]: r for c in checks for r in c["checked"]}
    disputed = {d["verdict_id"]: d for d in flags["disputes"]}
    rulings = {r["verdict_id"]: r for r in skeptic["disputes"]}
    history = list(previous.get("history") or [])
    set_aside = list(previous.get("set_aside") or [])
    actionable: list[dict] = []
    statuses = {s: 0 for s in CHECK_STATUSES}
    counts = {"formal": len(formal_items(doc)), "checked": len(status), "disputed_upheld": 0,
              "disputed_refuted": 0, "disputed_unclaimed": 0, "collateral": 0,
              "collateral_harmful": 0, "regressions": 0, "new": 0, "new_upheld": 0,
              "refuted": 0, "refuted_no_evidence": 0, "missed_added": 0, "missed_dropped": 0,
              "kind_rule": 0}
    facts: list[dict] = []
    skip_seen = [list(x) for x in previous.get("skip_seen") or []]
    if rule >= 2:
        facts = skip_facts(doc, paragraphs or [], round_)
        counts.update({k: 0 for k in SKIP_COUNTS_LATER})
        counts["skips"] = len(facts)

    def is_skip(v: dict) -> bool:
        return rule >= 2 and v.get("verdict") == SKIP_VERDICT and bool(v.get("span"))

    def again(v: dict, origin: str, **extra) -> dict:
        """A verdict sent back to the worker: a skip verdict by its span, else by its item."""
        base = {k: v[k] for k in ("item", "verdict", "rule", "merge_with", "action")}
        if is_skip(v):
            counts["skip_carried"] += origin == "carried"
            return _skip_record(span=v["span"], facts=facts, verdict=v["verdict"],
                                rule=v["rule"], merge_with=v["merge_with"], action=v["action"],
                                origin=origin, statements=list(v.get("statements") or []),
                                **extra)
        return _record(doc=doc, origin=origin, **base, **extra)

    def kept(v: dict) -> dict:
        """What history and set_aside keep of a verdict: its target, by item or span."""
        base = {k: v[k] for k in ("item", "verdict", "rule", "merge_with", "action")}
        if is_skip(v):
            base.update(span=list(v["span"]), statements=list(v.get("statements") or []))
        return base

    harm = False
    for v in previous.get("verdicts") or []:
        row = status[v["id"]]
        statuses[row["status"]] += 1
        base = kept(v)
        if v["id"] in disputed:
            ruling = rulings[v["id"]]
            if ruling["worker_right"]:
                counts["disputed_upheld"] += 1
                set_aside.append({**base, "of": v["id"], "start": v.get("start"),
                                  "why": "dispute_upheld", "reason": ruling["reason"]})
                if is_skip(v):
                    skip_seen.append(list(v["span"]))
            else:
                counts["disputed_refuted"] += 1
                actionable.append(again(v, "dispute_refuted", of=v["id"],
                                        skeptic_reason=ruling["reason"]))
            continue
        if row["status"] == "applied":
            history.append({**base, "id": v["id"], "start": v.get("start"), "round": round_ - 1})
        elif row["status"] == "gate_refused_ok":
            set_aside.append({**base, "of": v["id"], "start": v.get("start"),
                              "why": "gate_refused_ok", "reason": row["reason"]})
            if is_skip(v):
                skip_seen.append(list(v["span"]))
        else:
            if row["status"] == "disputed":
                counts["disputed_unclaimed"] += 1
            harm = harm or row["status"] == "wrong"
            actionable.append(again(v, "carried", of=v["id"], status=row["status"],
                                    check_reason=row["reason"]))
    entries = {e["id"]: e for e in collateral_doc.get("collateral") or []}
    for check in checks:
        for row in check["collateral"]:
            counts["collateral"] += 1
            if not row["harmful"]:
                continue
            counts["collateral_harmful"] += 1
            harm = True
            entry = entries.get(row["id"], {})
            item = entry.get("item") or (entry.get("before") or {}).get("item") or ""
            what = ", ".join([str(entry.get("change")), *(entry.get("fields") or [])])
            start = _start(doc, item) if item else None
            if start is None and entry.get("change") == "skip_edit":
                start = ((entry.get("after") or entry.get("before") or {}).get("span")
                         or [None])[0]
            actionable.append({"item": item, "start": start,
                               "verdict": VERDICT_REVERT, "rule": 0, "merge_with": "",
                               "action": f"revert collateral {row['id']} ({what}): "
                                         f"{row['reason']}",
                               "origin": "collateral", "of": row["id"]})
    known = {h["id"]: h for h in history}
    known.update({v["id"]: v for v in previous.get("verdicts") or []})
    for check in checks:
        for row in check["regressions"]:
            counts["regressions"] += 1
            harm = True
            v = known[row["verdict_id"]]
            actionable.append(again(v, "regression", of=row["verdict_id"],
                                    check_reason=row["reason"]))
    rulings_new = {r["flag_id"]: r for r in skeptic["verdicts"]}
    rows = {(r["item"], r["verdict"]): r for c in checks for r in c["new"]}
    refuted = []
    for flag in flags["flags"]:
        counts["new"] += 1
        ruling = rulings_new[flag["flag_id"]]
        if not ruling["upheld"] or not _on_layer(ruling["evidence"], layer_folded):
            counts["refuted"] += 1
            counts["refuted_no_evidence"] += bool(ruling["upheld"])
            refuted.append({"flag_id": flag["flag_id"], "item": flag["item"],
                            "verdict": flag["verdict"],
                            "why": "no_evidence" if ruling["upheld"] else "refuted",
                            "skeptic_reason": ruling["reason"]})
            continue
        counts["new_upheld"] += 1
        row = rows[(flag["item"], flag["verdict"])]
        actionable.append(_record(item=flag["item"], doc=doc, verdict=ruling["verdict"],
                                  rule=ruling["rule"], merge_with=flag["merge_with"],
                                  action=row["action"], origin="check_new",
                                  flag_id=flag["flag_id"], check_reason=flag["reason"],
                                  skeptic_reason=ruling["reason"], evidence=ruling["evidence"]))
    _add_missed(doc, skeptic, actionable, counts, layer_folded)
    exempt = {e.get("start") for e in previous.get("kind_other_ok") or []}
    seen = set(previous.get("kind_seen") or [])
    seen |= {e.get("start") for e in set_aside if e.get("verdict") == "wrong_kind"}
    seen |= {e.get("start") for e in history if e.get("verdict") == "wrong_kind"}
    seen |= {v.get("start") for v in previous.get("verdicts") or []
             if v.get("verdict") == "wrong_kind"}
    kind = _kind_rule(doc, actionable, exempt, seen)
    counts["kind_rule"] = len(kind)
    actionable.extend(kind)
    if rule >= 2:
        _add_missed_skips(skeptic, facts, actionable, counts, layer)
        _skip_rule(facts, actionable, counts, exempt=set(), seen=skip_seen)
    verdicts = _number(round_, doc, actionable, rule)
    counts.update({f"status_{s}": n for s, n in statuses.items()})
    counts.update(_kind_counts(doc))
    counts["actionable"] = len(verdicts)
    counts["by_verdict"] = _by_verdict(verdicts)
    seen |= {v["start"] for v in verdicts if v["verdict"] == "wrong_kind"}
    out = {"verdicts": verdicts, "refuted": refuted, "set_aside": set_aside, "history": history,
           "kind_other_ok": list(previous.get("kind_other_ok") or []),
           "kind_seen": sorted(s for s in seen if s is not None), "counts": counts,
           "harm": harm, "estimates": {"auditor": None,
                                       "skeptic": skeptic["per_spec_formal_estimate"]}}
    if rule >= 2:
        out.update(skip_ok=list(previous.get("skip_ok") or []),
                   skip_seen=sorted({tuple(x) for x in skip_seen}),
                   skip_report=skip_report(doc, paragraphs or []))
        out["skip_seen"] = [list(x) for x in out["skip_seen"]]
    return out


def verdicts_file(round_: int, review_rounds: int, doc: dict, document_sha: str, merged: dict,
                  previous: dict | None, *, audit_failed: str | None = None,
                  rule: int = DEFAULT_RULE) -> dict:
    """The round's verdicts file, with the decision (``audit/<rule>``)."""
    rounds = list((previous or {}).get("rounds") or [])
    actionable = len(merged["verdicts"])
    rounds.append({"round": round_, "actionable": actionable, "harm": merged["harm"],
                   "document_sha256": document_sha})
    prev_actionable = (previous or {}).get("counts", {}).get("actionable")
    if audit_failed is not None:
        decision, revert_to, why = "approve", None, f"audit_failed: {audit_failed}"
    elif rule >= 2:
        decision, revert_to, why = decide(
            round_, review_rounds, actionable, prev_actionable, merged["harm"],
            merged["counts"]["formal"], rounds, rule=rule,
            skip_actionable=sum(v["verdict"] == SKIP_VERDICT for v in merged["verdicts"]),
            skip_math=skipped_math_outside(merged.get("skip_report")),
            previous_skip_math=skipped_math_outside((previous or {}).get("skip_report")))
    else:
        decision, revert_to, why = decide(round_, review_rounds, actionable, prev_actionable,
                                          merged["harm"], merged["counts"]["formal"], rounds)
    return {"schema": AUDIT_SCHEMAS[rule], "kind": "verdicts", "round": round_,
            "review_rounds": review_rounds, "final": round_ >= review_rounds,
            "decision_rule": f"audit/{rule}", "run_id": doc.get("run_id"),
            "task_id": doc.get("task_id"), "source_id": doc.get("source_id"),
            "document_sha256": document_sha, **merged, "rounds": rounds,
            "T": threshold(merged["counts"]["formal"]), "decision": decision,
            "revert_to": revert_to, "audit_failed": audit_failed is not None, "why": why}


def failed_merge(doc: dict, previous: dict | None, *, rule: int = DEFAULT_RULE,
                 report: dict | None = None) -> dict:
    """What an audit whose output stayed invalid leaves: no verdicts, nothing carried
    forward as asked, the kind counts still taken; under rule 2 the exemptions carried
    and, when the source could be read, the round's ``skip_report``."""
    previous = previous or {}
    out = {"verdicts": [], "refuted": [], "set_aside": list(previous.get("set_aside") or []),
           "history": list(previous.get("history") or []),
           "kind_other_ok": list(previous.get("kind_other_ok") or []),
           "kind_seen": list(previous.get("kind_seen") or []),
           "counts": {"formal": len(formal_items(doc)), "actionable": 0, "by_verdict": {},
                      **_kind_counts(doc)},
           "harm": False, "estimates": {"auditor": None, "skeptic": None}}
    if rule >= 2:
        out.update(skip_ok=list(previous.get("skip_ok") or []),
                   skip_seen=list(previous.get("skip_seen") or []))
        if report is not None:
            out["skip_report"] = report
    return out


def result_for(verdicts: dict, out_name: str) -> dict:
    """The reviewer's ``.tend/agent_result.json`` for the decision (normalize_result's shape)."""
    k, n = verdicts["round"], len(verdicts["verdicts"])
    by = ", ".join(f"{v} {c}" for v, c in verdicts["counts"].get("by_verdict", {}).items())
    if verdicts["decision"] == "approve":
        return {"verdict": "approve",
                "notes": f"Audit round {k}: approve ({verdicts['why']}); {n} residual verdict(s)"
                         f" in .tend/audit/{out_name}."[:1500]}
    if verdicts["decision"] == "revert":
        text = (f"Audit round {k}: revert to the round {verdicts['revert_to']} snapshot "
                f"({verdicts['why']}).")
    else:
        text = (f"Audit round {k}: {n} actionable verdict(s) ({by}) in .tend/audit/{out_name}; "
                f"apply them as the revision prompt says.")
    return {"verdict": "request_changes", "notes": text[:1500], "feedback_text": text[:1500]}


# --------------------------------------------------------------------------- #
# the source, read through the gate's pin
# --------------------------------------------------------------------------- #

def load_source(gate_config: str | Path, source_id: str):
    """``verify_locate.SourceView`` of ``source_id``: the layer and its tables as the gate
    pins them, or :class:`Broken` with the gate's own words."""
    try:
        import verify_locate as vl  # noqa: PLC0415 - the gate directory's sibling
    except Exception as exc:  # noqa: BLE001 - any failure to import is the same failure
        raise Broken(f"the gate beside this tool does not import ({exc.__class__.__name__}: "
                     f"{exc})") from None
    if vl.IMPORT_ERRORS:
        raise Broken("the gate directory is missing a module: " + "; ".join(vl.IMPORT_ERRORS))
    try:
        cfg = vl.GateConfig.load(Path(gate_config))
        if source_id not in cfg.sources:
            raise InputError(f"gate.json pins no source {source_id!r}")
        view = vl.SourceView(cfg, source_id)
        view.paragraphs()
    except vl.GateBroken as exc:
        raise Broken(str(exc)) from None
    return cfg, view


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #

def out_path(value: str, *, result: bool = False) -> Path:
    """An output path: inside ``<cwd>/.tend/audit/`` (or exactly the result file), through
    no symlink; else :class:`InputError`."""
    if result:
        return confined(value, RESULT_FILE[:-1], RESULT_FILE[-1])
    return confined(value, OUT_DIR)


def confined(value: str, directory: tuple[str, ...], name: str | None = None) -> Path:
    """``value`` as a path inside ``<cwd>/<directory>/`` (exactly ``<directory>/<name>`` when
    ``name`` is given), reached through no symlink; else :class:`InputError`.
    ``inventory_render.py`` writes by the same rule."""
    cwd = Path.cwd().resolve()
    want_dir = cwd.joinpath(*directory)
    raw = Path(value)
    path = Path(os.path.normpath(raw if raw.is_absolute() else cwd / raw))
    node = cwd
    for part in want_dir.relative_to(cwd).parts:
        node = node / part
        if node.is_symlink():
            raise InputError(f"{node} is a symlink; this tool writes through none")
    if path.is_symlink():
        raise InputError(f"{path} is a symlink; this tool writes through none")
    if name is not None:
        if path != want_dir / name:
            raise InputError(f"this path must be {'/'.join((*directory, name))} in the "
                             f"working directory, not {value}")
    elif path.parent != want_dir:
        raise InputError(f"this tool writes only into {'/'.join(directory)}/ of the working "
                         f"directory, not {value}")
    return path


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")


def _report(problems: list[str]) -> int:
    if problems:
        for line in problems[:MAX_PROBLEMS]:
            print(f"INVALID: {line}")
        if len(problems) > MAX_PROBLEMS:
            print(f"INVALID: … and {len(problems) - MAX_PROBLEMS} more")
        return EXIT_INVALID
    return EXIT_OK


def _expect(path: str | None) -> list[str] | None:
    if path is None:
        return None
    data = read_json(path, "expectation")
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise InputError(f"the expectation at {path} is not a list of ids")
    return data


def _check_files(paths: list[str], doc: dict, previous: dict, collateral_doc: dict,
                 round_: int, problems: list[str], rule: int = DEFAULT_RULE) -> list[dict]:
    """Several checker part files, each valid on its own; the union judged once."""
    checks = []
    for path in paths:
        data = read_json(path, "check file")
        own = validate_check(data, doc, previous, collateral_doc, round_=round_,
                             expect=[r["verdict_id"] for r in data.get("checked") or []
                                     if isinstance(r, dict)] if len(paths) > 1 else None,
                             rule=rule)
        problems.extend(f"{path}: {p}" for p in own)
        checks.append(data)
    if len(paths) > 1 and not problems:
        _exactly_once([r["verdict_id"] for c in checks for r in c["checked"]],
                      [v["id"] for v in previous.get("verdicts") or []], "verdict",
                      "the checks", problems)
        _exactly_once([r["id"] for c in checks for r in c["collateral"]],
                      [e["id"] for e in collateral_doc.get("collateral") or []],
                      "collateral change", "the checks", problems)
    return checks


def _audit_files(paths: list[str], doc: dict, problems: list[str], rule: int = DEFAULT_RULE,
                 skips: list[dict] | None = None) -> list[dict]:
    audits = []
    for path in paths:
        data = read_json(path, "audit file")
        many = len(paths) > 1
        own = validate_audit(data, doc, expect=[r["item"] for r in data.get("passages") or []
                                                if isinstance(r, dict)]
                             if many else None, rule=rule, skips=skips,
                             expect_skips=[r["skip"] for r in data.get("skips") or []
                                           if isinstance(r, dict)] if many else None)
        problems.extend(f"{path}: {p}" for p in own)
        audits.append(data)
    if len(paths) > 1 and not problems:
        _exactly_once([r["item"] for a in audits for r in a["passages"]], formal_items(doc),
                      "formal passage", "the audits", problems)
        if rule >= 2:
            _exactly_once([r["skip"] for a in audits for r in a["skips"]],
                          [f["id"] for f in (skips or bare_skips(doc, 1))], "skip",
                          "the audits", problems)
    return audits


def _facts(args, doc: dict, index: dict | None, round_: int) -> list[dict] | None:
    """The skip facts a rule-2 check reads: the rendering index's rows when one is given,
    else the source's (``--gate-config``), else None (ids alone)."""
    if index is not None:
        return list(index.get("skips") or [])
    if getattr(args, "gate_config", None):
        _cfg, view = load_source(args.gate_config, str(doc.get("source_id")))
        return skip_facts(doc, view.paragraphs(), round_)
    return None


def cmd_validate(args) -> int:
    rule = known_rule(args.rule)
    doc = read_document(args.document)
    data = read_json(args.file, f"{args.kind} file")
    if args.kind == "audit":
        expect = _expect(args.expect)
        index = expect_skips = None
        if args.render_index:
            expect = _part_items(args.render_index, args.part, args.document, doc)
            if rule >= 2:
                index, expect_skips = _part_skips(args.render_index, args.part)
        skips = _facts(args, doc, index, 1) if rule >= 2 else None
        problems = validate_audit(data, doc, expect=expect, rule=rule, skips=skips,
                                  expect_skips=expect_skips)
    elif args.kind == "skeptic":
        flags = read_json(_need(args.flags, "--flags"), "flags file")
        expect = index = expect_skips = None
        if args.render_index:
            expect = _part_items(args.render_index, args.part, args.document, doc)
            if rule >= 2:
                index, expect_skips = _part_skips(args.render_index, args.part)
        round_ = flags.get("round")
        skips = _facts(args, doc, index, round_ if isinstance(round_, int) else 1) \
            if rule >= 2 else None
        problems = validate_skeptic(data, doc, flags, round_=round_, expect=expect,
                                    rule=rule, skips=skips, expect_skips=expect_skips)
    elif args.kind == "check":
        previous = read_json(_need(args.previous, "--previous"), "previous verdicts")
        collateral_doc = read_json(_need(args.collateral, "--collateral"), "collateral file")
        expect = _expect(args.expect)
        problems = validate_check(
            data, doc, previous, collateral_doc, round_=int(previous.get("round") or 0) + 1,
            expect=None if expect is None else [x for x in expect if not COLLATERAL_ID.match(x)],
            expect_collateral=None if expect is None else
            [x for x in expect if COLLATERAL_ID.match(x)], rule=rule)
    elif args.kind == "repair":
        verdicts = read_json(_need(args.verdicts, "--verdicts"), "verdicts file")
        problems = validate_repair(data, verdicts, source_id=str(doc.get("source_id")),
                                   rule=rule)
    else:
        ids = None
        if args.verdicts:
            ids = {v["id"] for v in read_json(args.verdicts, "verdicts file").get("verdicts") or []}
        problems = validate_ops(data, doc, verdict_ids=ids, rule=rule)
    code = _report(problems)
    if code == EXIT_OK:
        print(f"OK: the {args.kind} file is valid")
    return code


def _part_items(index_path: str, part: int | None, document: str, doc: dict) -> list[str]:
    """The formal items part ``part`` of a rendering index holds (``inventory_render.py``'s
    ``r<k>.render.json``), held to the document it rendered."""
    index = read_json(index_path, "rendering index")
    parts = index.get("parts") if isinstance(index, dict) else None
    if not isinstance(parts, list) or part is None or not 1 <= part <= len(parts):
        raise InputError(f"--part {part} is not a part of the rendering index {index_path}")
    if index.get("source_id") != doc.get("source_id") \
            or index.get("document_sha256") != sha256_file(document):
        raise InputError(f"the rendering index {index_path} is of another document")
    items = parts[part - 1].get("formal")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise InputError(f"part {part} of {index_path} lists no formal items")
    return items


def _part_skips(index_path: str, part: int) -> tuple[dict, list[str]]:
    """``(index, the skip ids part ``part`` shows)`` of a rule-2 rendering index (held to
    its document by :func:`_part_items` first)."""
    index = read_json(index_path, "rendering index")
    if index.get("rule") != 2 or not isinstance(index.get("skips"), list):
        raise InputError(f"the rendering index {index_path} is no rule-2 rendering (no skips)")
    ids = index["parts"][part - 1].get("skips")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise InputError(f"part {part} of {index_path} lists no skips")
    return index, ids


def _need(value: str | None, flag: str) -> str:
    if not value:
        raise InputError(f"this needs {flag}")
    return value


def cmd_strip(args) -> int:
    rule = known_rule(args.rule)
    doc = read_document(args.document)
    problems: list[str] = []
    if args.round == 1:
        skips = None
        audits = _audit_files(_need_list(args.audit, "--audit"), doc, problems, rule, skips)
        flags = None if problems else strip(1, doc, audits=audits, rule=rule)
    else:
        previous = read_json(_need(args.previous, "--previous"), "previous verdicts")
        collateral_doc = read_json(_need(args.collateral, "--collateral"), "collateral file")
        checks = _check_files(_need_list(args.check, "--check"), doc, previous, collateral_doc,
                              args.round, problems, rule)
        repair = read_json(args.repair, "repair file") if args.repair else None
        if repair is not None:
            problems.extend(validate_repair(repair, previous,
                                            source_id=str(doc.get("source_id")), rule=rule))
        flags = None if problems else strip(args.round, doc, checks=checks, repair=repair,
                                             previous=previous, rule=rule)
    if problems:
        return _report(problems)
    write_json(out_path(args.out), flags)
    print(f"OK: {len(flags['flags'])} flag(s), {len(flags['disputes'])} dispute(s) -> {args.out}")
    return EXIT_OK


def _need_list(value, flag: str) -> list[str]:
    if not value:
        raise InputError(f"this needs {flag}")
    return value


def cmd_collateral(args) -> int:
    rule = known_rule(args.rule)
    snapshot = read_document(args.snapshot)
    doc = read_document(args.document)
    ops = read_json(args.ops, "ops file") if args.ops else None
    verdicts = read_json(args.verdicts, "verdicts file") if args.verdicts else None
    out = collateral(args.round, snapshot, doc, ops, verdicts, rule=rule)
    write_json(out_path(args.out), out)
    print(f"OK: {len(out['collateral'])} collateral entr(ies) -> {args.out}")
    return EXIT_OK


def cmd_merge(args) -> int:
    rule = known_rule(args.rule)
    if args.review_rounds not in AUDIT_REVIEW_ROUNDS:
        raise InputError(f"--review-rounds is {args.review_rounds}, not one of "
                         f"{AUDIT_REVIEW_ROUNDS}")
    if not 1 <= args.round <= args.review_rounds:
        raise InputError(f"--round {args.round} is outside 1..{args.review_rounds}")
    doc = read_document(args.document)
    document_sha = sha256_file(args.document)
    previous = read_json(args.previous, "previous verdicts") if args.previous else None
    if args.round > 1 and previous is None:
        raise InputError("a round after the first needs --previous (the last round's verdicts)")
    if previous is not None and previous.get("round") != args.round - 1:
        raise InputError(f"--previous is round {previous.get('round')!r}, not {args.round - 1}")
    if args.audit_failed is not None:
        report = None
        if rule >= 2 and args.gate_config:
            try:
                _cfg, view = load_source(args.gate_config, str(doc.get("source_id")))
                report = skip_report(doc, view.paragraphs())
            except (Broken, InputError):
                report = None           # an audit that failed keeps its approval regardless
        out = verdicts_file(args.round, args.review_rounds, doc, document_sha,
                            failed_merge(doc, previous, rule=rule, report=report), previous,
                            audit_failed=args.audit_failed or "invalid output", rule=rule)
        return _finish(args, out)
    problems: list[str] = []
    _cfg, view = load_source(_need(args.gate_config, "--gate-config"), str(doc.get("source_id")))
    paragraphs = view.paragraphs() if rule >= 2 else None
    skeptic = read_json(_need(args.skeptic, "--skeptic"), "skeptic file")
    if args.round == 1:
        facts = skip_facts(doc, paragraphs, 1) if rule >= 2 else None
        audits = _audit_files(_need_list(args.audit, "--audit"), doc, problems, rule, facts)
        if problems:
            return _report(problems)
        flags = strip(1, doc, audits=audits, rule=rule)
        problems.extend(validate_skeptic(skeptic, doc, flags, round_=1, rule=rule, skips=facts))
        if problems:
            return _report(problems)
        merged = merge_round1(doc, audits, skeptic, layer=view.layer, rule=rule,
                              paragraphs=paragraphs)
    else:
        collateral_doc = read_json(_need(args.collateral, "--collateral"), "collateral file")
        repair = read_json(args.repair, "repair file") if args.repair else None
        if repair is not None:
            problems.extend(validate_repair(repair, previous,
                                            source_id=str(doc.get("source_id")), rule=rule))
        checks = _check_files(_need_list(args.check, "--check"), doc, previous, collateral_doc,
                              args.round, problems, rule)
        if problems:
            return _report(problems)
        flags = strip(args.round, doc, checks=checks, repair=repair, previous=previous,
                      rule=rule)
        facts = skip_facts(doc, paragraphs, args.round) if rule >= 2 else None
        problems.extend(validate_skeptic(skeptic, doc, flags, round_=args.round, rule=rule,
                                         skips=facts))
        if problems:
            return _report(problems)
        merged = merge_later(args.round, doc, checks, skeptic, previous, repair, collateral_doc,
                             layer=view.layer, rule=rule, paragraphs=paragraphs)
    out = verdicts_file(args.round, args.review_rounds, doc, document_sha, merged, previous,
                        rule=rule)
    return _finish(args, out)


def _finish(args, out: dict) -> int:
    path = out_path(args.out)
    write_json(path, out)
    if args.result:
        write_json(out_path(args.result, result=True), result_for(out, path.name))
    print(f"OK: round {out['round']}: {out['decision']} ({out['why']}); "
          f"{len(out['verdicts'])} actionable verdict(s) -> {args.out}")
    return EXIT_OK


def _rule_arg(parser) -> None:
    parser.add_argument("--rule", type=int, default=DEFAULT_RULE,
                        help=f"the audit rule the run names ({' or '.join(map(str, AUDIT_RULES))}"
                             f"; default {DEFAULT_RULE})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check one file against the schema and what it answers")
    v.add_argument("--kind", required=True, choices=AGENT_KINDS)
    v.add_argument("--file", required=True)
    v.add_argument("--document", required=True)
    v.add_argument("--flags")
    v.add_argument("--previous")
    v.add_argument("--collateral")
    v.add_argument("--verdicts")
    v.add_argument("--expect", help="a JSON list of the items (audit), or the verdict ids and "
                                    "collateral ids (check), this part file judges")
    v.add_argument("--render-index", help="an audit or skeptic part: the rendering's "
                                          "r<k>.render.json")
    v.add_argument("--part", type=int, help="an audit or skeptic part: its number in "
                                            "--render-index")
    v.add_argument("--gate-config", help="rule 2 without --render-index: the run's gate.json, "
                                         "whose source the skips are judged on")
    _rule_arg(v)
    s = sub.add_parser("strip", help="write the flags file the skeptic sees")
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--document", required=True)
    s.add_argument("--audit", action="append")
    s.add_argument("--check", action="append")
    s.add_argument("--previous")
    s.add_argument("--collateral")
    s.add_argument("--repair")
    s.add_argument("--out", required=True)
    _rule_arg(s)
    c = sub.add_parser("collateral", help="list what changed that no operation asked for")
    c.add_argument("--round", type=int, required=True)
    c.add_argument("--snapshot", required=True)
    c.add_argument("--document", required=True)
    c.add_argument("--ops")
    c.add_argument("--verdicts")
    c.add_argument("--out", required=True)
    _rule_arg(c)
    m = sub.add_parser("merge", help="merge the round's files into its verdicts and decide")
    m.add_argument("--round", type=int, required=True)
    m.add_argument("--review-rounds", type=int, required=True)
    m.add_argument("--gate-config")
    m.add_argument("--document", required=True)
    m.add_argument("--skeptic")
    m.add_argument("--audit", action="append")
    m.add_argument("--check", action="append")
    m.add_argument("--previous")
    m.add_argument("--collateral")
    m.add_argument("--repair")
    m.add_argument("--audit-failed", metavar="REASON")
    m.add_argument("--out", required=True)
    m.add_argument("--result")
    _rule_arg(m)
    args = ap.parse_args(argv)
    try:
        return {"validate": cmd_validate, "strip": cmd_strip, "collateral": cmd_collateral,
                "merge": cmd_merge}[args.command](args)
    except InputError as exc:
        print(f"inventory_audit: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except Broken as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
