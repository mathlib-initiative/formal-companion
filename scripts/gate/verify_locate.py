#!/usr/bin/env python3
"""The out-of-tree locate gate: rules 1-10 over one task's own documents.

New code, implementing docs/proposals/wikipedia-locate.md section 4 (gates,
locate mode) over the formats of references/locate-documents.md sections 1-4
and 7-9.  It is the locate counterpart of ``verify_findings.py`` and follows
its contract:

* **out of tree** -- copied into ``<tend-root>/gate/`` with ``gate.json``, the
  task manifests (``gate/tasks/<task-id>.json``), ``locate_text.py``,
  ``verify_findings.py`` (whose hardened changed-file reader it reuses) and
  ``vendor/``, and invoked by absolute path, never from the worktree a worker
  can edit;
* **stdlib only, no cflib** -- the run host has neither; the vocabulary it
  needs is inlined below and ``tests/test_verify_locate.py`` pins it to
  ``cflib.vocab``;
* **the exit vocabulary** -- 0 every document validated; 1 REJECTED, fix the
  documents; 3 the gate could not run (no, unreadable or malformed gate.json,
  a vendored package that is not the pinned release, a layer missing or not
  the pinned bytes, a formula list or paragraph table missing, malformed or
  not the view the pin names, an unreadable corpus.sqlite or one that is not
  the pinned bytes, a candidates file that is not the pinned bytes, a missing
  task-manifest directory, a change set git cannot read safely, a gate
  directory missing one of its own modules, a command line it refuses, a
  crash of the gate itself).
  Every infrastructure failure arrives as 3, never as the interpreter's 1 out
  of a traceback, because the worker's prompt says 1 means "your documents
  are wrong"; an exit 1 always comes with a ``VALIDATION FAILED`` list.
  ``--self-check`` validates nothing and says whether the gate can run, which
  is what ``cf run``'s verify stage asks of the host, under ``python3 -I``.

**Only the task's own documents.**  The v1 gate validated every findings file
of the root on every call, which made it O(N) per call.  This one validates
the documents this contribution changed (``--changed-files``) or the files
named with ``--findings``, and loads only the sources, candidates files and
task manifests those documents name.  Without either, it validates
``<root>/<findings-dir>/*/*.json`` (a harvested corpus: ``--findings-dir .``).

**The rules** (the proposal's section 4; the precise forms are written in
references/locate-documents.md sections 2-4 and 9):

1. the schema of the document for its mode, with the header pins; every
   record the document implies passes the vendored ``literature_links.check``;
   both vendored packages match their manifests and the manifests the shas in
   ``gate.json``; a coverage claim sits on a located passage and names at
   least two of its ``hasPart`` locations, with a note in the worker's own
   words, and no challenge document confirms one while refuting a part;
2. every quote matches exactly once on the source's layer, at its passage's
   span start and inside the span, and its ``In-text #<hex>`` is the hash of
   that start's head (an inventory's ids recomputed over all its passages; a
   locate document's passages are exactly the task's frozen ones); in a run
   whose ``gate.json`` names ``span_end_rule``, an inventory passage also ends
   where ``passage_ids.py``'s rule ends it (``locate_text.passage_end``: its
   ``until``, else its paragraph, else its quote), while a run planned before
   the key keeps the bound above (inside the layer, after the quote), since its
   ``passage_ids.py`` dropped the ``until`` the end was placed by; the
   document's ``source_sha``, ``layer_sha256`` and ``renderer`` are the pinned
   source's, ``layer.txt`` hashes to the pin, and so do ``formulas.json`` and
   ``paragraphs.json`` when the pin carries their ``views``;
3. every declaration a document names as found exists in ``corpus.sqlite``'s
   exact table at that library and the universe's commit, the database being
   the bytes ``gate.json`` pins (``corpus_db.sha256``, compared when the gate
   first opens it); ``proof_status``, ``commit`` and ``version`` are refused
   wherever a worker writes them;
4. with L2 curated hits for the article (read here, from the candidates file
   whose sha ``gate.json`` pins under ``candidates_sha256``, never from the
   document): an inventory keeps a ``subject`` passage scoped
   ``formal_math`` and scopes no ``subject`` passage out; a locate document
   leaves no hit handed to its task unhandled -- each is located with a
   relation that counts (a ``relatedMatch`` does not) or rejected with a
   reason -- and an ``accepted`` candidate is one of its passage's locations;
   off in the blind arm;
5. an absence lists at least three distinct queries, and the document names
   the frozen universe;
6. at most five locations per passage with distinct ranks 1..5, one per
   declaration;
7. the changed files are the task's own: ``findings/<task-id>/<source-id>.json``
   for the sources its manifest names and ``tasks/<task-id>.yaml``, whose
   change is its ``status:`` line; the task id comes from
   ``--task-id``/``TEND_TASK_ID``, else from tend's ``orchestrator.sqlite`` (an
   ASSUMED schema, looked up defensively; verify it on the host), else the
   single-stem rule (one task per change set; ``--pre-merge``, the batched
   pre-merge check, admits several tasks' new documents) -- and the last line
   says which;
8. an inventory drops no deterministic-finder passage of its task without a
   skip covering its span, every ``math`` paragraph is covered by a passage or
   a skip, and no skip holds the whole span of a passage the inventory keeps;
   in a run whose ``gate.json`` names ``prose_floor_rule`` 1 (the prose floor),
   so is every other block outside the back matter of at least
   ``PROSE_FLOOR_MIN_WORDS`` words;
9. the judgment rules (references/vocabulary.md section 1.11, R1-R7), only for
   a run whose ``gate.json`` names ``judgment_rules`` -- a run planned before
   them is gated as it was: a located or absent passage writes its claim list
   (``claims``); a location whose relation counts names the claim ids it states
   (``clauses``), every one for a strong relation, and a ``relatedMatch`` none;
   a strong location whose note or instantiation admits a clause it does not
   state is refused, and so is a coverage claim whose parts leave a clause
   unstated; an absence's receipt tags every query with the claims it targets
   (``receipt.query_clauses``) and gives each claim a query of its own, a worked
   example's general theorem too, and rejects no candidate for stating a part of
   the passage; a counting location on a declaration whose signature
   (``corpus.sqlite``'s ``decls.signature``) takes an instance argument at a
   binder position says how it is instantiated (``instantiation``); a
   ``hasPart`` or ``narrowMatch`` says what the declaration alone entails of the
   passage (``step``); a ``closeMatch`` names its convention from a closed list
   (``convention``), no other relation carries a convention or a step, and an
   exact or close note names no generality; every one-line field is checked
   wherever it appears, and every free text against the source in all its
   spellings (the layer, without format characters, and the reading copy's);
   a challenge document carries the challenger's own claim list of every
   passage it judges a location, coverage claim, absence or unresolved outcome
   of, which its verdicts name -- a coverage confirm per part, an uphold through
   its own tagged queries.  Under version 2 of the rules (the maintainer's
   PD5-PD9, references/vocabulary.md section 1.11) two checks join them: a
   coverage claim, or a challenger's confirm of one, one of whose parts alone
   names every claim of the passage is refused (PD6: such a part is one location
   with its own relation), and ``junk_value`` is no convention (PD7), save on a
   confirm that keeps a record's own, written under version 1.  Under version 3
   (the statement cut, docs/proposals/statement-cut.md sections 1.3, 1.4 and 5)
   the clause ids are the frozen passage's conjunct ids, or ``c1`` for a simple
   statement, and no document writes a claim list; a ``hasPart`` of a compound
   statement gives its ``conjunct_relation``; a coverage claim is a bundle -- a
   compound statement's conjuncts partitioned among strong parts, none stating
   them all -- and a compound statement whose strong parts state every conjunct
   writes one; a strong location writes ``tests`` and ``instantiation`` and
   passes the naturals-for-integers tripwire, under the longer generality list,
   and a part is held to all of it by its ``conjunct_relation`` (a strong one
   is a strong location: the bundle takes its weakest part's relation); an
   absence names its nearest ``special_case``; ``cut_flag`` sits on an
   unresolved passage only; a challenge confirms a bundle with ``part_relations``
   -- beside no verdict of its own that gives one part every conjunct (PD6) --
   and refutes one with ``unstated`` and ``part_reasons``, a verdict that keeps
   or leaves a strong relation (a part's by its conjunct relation) runs its own
   ``tests``, an uphold names its special case, a document that empties a
   located passage brings its ``fills`` (an absent one accepting no candidate),
   and a prominence sample's conditional records are judged exactly when the
   document overturns their passage to ``subject`` or ``central``;
10. the statement cut (docs/proposals/statement-cut.md section 3.3), only for a
   run whose ``gate.json`` names ``statement_cut_rule`` (with the span-end rule
   it reads, 2 or 3, which ends a passage without ``until`` at the earlier of its
   extent, or its continued extent, and the next passage's start, less the
   separator before it): the spans of an
   inventory are disjoint, one text one passage; a span runs from one block
   into the next only inside a theorem box, into the displays its last
   sentence introduces or over the items of one list (a compound statement),
   and under statement-cut rule 2 into the paragraph that finishes its
   sentence after them (the loose bound, ``locate_text.Blocks.runs_on``), under
   statement-cut rule 3 also across a footnote mark and into a ``dd`` or a
   paragraph opening with a formula that finishes it (the wide bound,
   ``runs_on(..., wide=True)``), and never ends inside a formula; ``conjuncts`` are
   none or 2-5 with a ``shape`` of the closed list, on a ``formal_math`` passage;
   a passage scoped out is ``supporting``; and, under statement-cut rule 2 or 3,
   at most one ``formal_math`` passage of a source is marked ``prominence:
   subject`` (h).  Rule 10 refuses nothing on a passage's size: its counts of
   compound statements and their conjuncts, sentences and displays (and under
   statement-cut rule 2 the subjects, continuations and connectives, and under 3
   the passages the wide bound let run on and its tails) are the harvest
   receipt's ``cut`` block.

One JSON line per call is appended to ``gate.json``'s ``log_dir`` when that is
writable (the watchdog and the bench read it); a log that cannot be written
never fails the gate.  The line's ``caller`` says which call it was:
``validation`` (``--changed-files``, tend's validation command), ``pre_merge``
(``--changed-files --pre-merge``), ``findings_check`` (``--findings``, an
agent's own check) or ``directory`` (a directory of documents, as at harvest).

**The command line is the label**, so the gate refuses one that could fake it,
with exit 3 and before it reads anything: ``--findings`` with
``--changed-files``, ``--pre-merge`` without ``--changed-files``, an
abbreviated flag, a repeated ``--gate-config``, and -- when a ``gate.json``
sits beside this script, as in every gate directory -- a ``--gate-config``
that resolves to any other file.  A refused command line validates nothing
and writes no log line.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import stat
import sys
import time
import unicodedata
from collections import Counter
from typing import Any, Callable, Iterable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
#: the vendored packages: beside this file in a gate directory, else the checkout's
VENDOR = HERE / "vendor" if (HERE / "vendor").is_dir() else HERE.parent.parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

#: What went wrong importing the gate's siblings (references/locate-documents.md
#: section 9: the gate directory holds locate_text.py, verify_findings.py and
#: locate_records.py beside this file).  A missing or broken one is an
#: infrastructure failure: ``main`` turns it into exit 3 with ONE ``FAIL`` line,
#: never the interpreter's exit 1 out of a traceback -- the code a worker is told
#: means "your documents are wrong".
IMPORT_ERRORS: list[str] = []
try:
    import locate_text as lt  # noqa: E402
except Exception as _exc:  # noqa: BLE001 - any failure to import is the same failure
    lt = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"locate_text.py ({_exc.__class__.__name__}: {_exc})")
try:
    import verify_findings as vf  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    vf = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"verify_findings.py ({_exc.__class__.__name__}: {_exc})")


def _load_records():
    """The export's record builder: ``locate_records.py`` staged here, else the checkout's.

    The gate directory carries ``scripts/locate/records.py`` as
    ``locate_records.py``; the harvest's in-process gate runs from the skill
    checkout, where the same file sits at ``scripts/locate/records.py``.
    """
    try:
        import locate_records  # noqa: PLC0415
        return locate_records
    except ModuleNotFoundError as exc:
        if exc.name != "locate_records":
            raise
    path = HERE.parent / "locate" / "records.py"
    if not path.is_file():
        raise ModuleNotFoundError("no locate_records.py beside the gate, and no checkout "
                                  "scripts/locate/records.py")
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("locate_records", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["locate_records"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


try:
    lr = _load_records()
except Exception as _exc:  # noqa: BLE001
    lr = None
    IMPORT_ERRORS.append(f"locate_records.py ({_exc.__class__.__name__}: {_exc})")

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_GATE_BROKEN = 3

#: what a gate directory must hold beside this file, besides gate.json, tasks/
#: and vendor/ (scripts/locate/tend_root.py stages them; a test pins the two).
#: A missing or broken one is in IMPORT_ERRORS above: exit 3, never a verdict.
GATE_MODULES = ("locate_text.py", "verify_findings.py", "locate_records.py")

GATE_SCHEMA = "cf-gate-locate/1"
DOCUMENT_SCHEMA = "cf-locate/1"
#: the tables beside ``layer.txt`` that a source pin's ``views`` holds the sha of
#: (copied by the planner from the source's ``render.json``; references/locate-documents.md
#: section 9)
PINNED_VIEWS = ("formulas.json", "paragraphs.json")
#: what a gate log line's ``caller`` says the call was (``run_gate``;
#: cflib.vocab.GATE_CALLERS, inlined and pinned by a test)
CALLER_VALIDATION = "validation"
CALLER_PRE_MERGE = "pre_merge"
CALLER_FINDINGS = "findings_check"
CALLER_DIRECTORY = "directory"
CALLERS = (CALLER_VALIDATION, CALLER_PRE_MERGE, CALLER_FINDINGS, CALLER_DIRECTORY)

# --------------------------------------------------------------------------- #
# the vocabulary, inlined from cflib.vocab (no cflib on the host).  Keep in
# step; tests/test_verify_locate.py asserts every set equals cflib's.
# --------------------------------------------------------------------------- #
LOCATE_INVENTORY = "locate-inventory"
LOCATE = "locate"
LOCATE_CHALLENGE = "locate-challenge"
LOCATE_MODES = frozenset({LOCATE_INVENTORY, LOCATE, LOCATE_CHALLENGE})
KINDS_BY_MODE = {
    LOCATE_INVENTORY: frozenset({"PASSAGE", "UNRESOLVED"}),
    LOCATE: frozenset({"LOCATION", "ABSENCE", "UNRESOLVED"}),
    LOCATE_CHALLENGE: frozenset({"LOCATION", "ABSENCE", "UNRESOLVED"}),
}
PASSAGE_KINDS = frozenset({"definition", "theorem", "conjecture", "example", "construction",
                           "notation", "other"})
PROMINENCE = frozenset({"subject", "central", "supporting"})
SCOPES = frozenset({"formal_math", "expository", "non_math"})
LOCATED_SCOPES = frozenset({"formal_math"})
LOCATE_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch", "narrowMatch",
                              "hasPart", "relatedMatch"})
#: how strong a relation is, strongest first: a challenge's ``downgrade`` names a
#: relation of a HIGHER number than the record's (references/locate-documents.md
#: section 4); cflib.vocab.RELATION_STRENGTH, inlined and pinned by a test
RELATION_STRENGTH = {"exactMatch": 0, "closeMatch": 1, "broadMatch": 2, "narrowMatch": 3,
                     "hasPart": 3, "relatedMatch": 4}
RELATED_MATCH = "relatedMatch"
CLOSE_MATCH = "closeMatch"
NO_MATCH = "noMatch"
LOCATE_OUTCOMES = frozenset({"located", "absent", "unresolved"})
FINDERS = frozenset({"theorem_box", "bold_definition", "lead", "model"})
DETERMINISTIC_FINDERS = frozenset({"theorem_box", "bold_definition", "lead"})
CONFIDENCES = frozenset({"high", "medium", "low"})
CANDIDATE_VERDICTS = frozenset({"accepted", "rejected"})
CHALLENGE_TARGETS = frozenset({"location", "absence", "unresolved", "scope", "prominence",
                               "coverage"})
CHALLENGE_ACTIONS = frozenset({"confirm", "uphold", "downgrade", "refute", "overturn"})
#: which actions a target admits (references/locate-documents.md section 4)
CHALLENGE_COMBOS = {
    "location": frozenset({"confirm", "downgrade", "refute"}),
    "absence": frozenset({"uphold", "overturn"}),
    "unresolved": frozenset({"uphold", "overturn"}),
    "scope": frozenset({"uphold", "overturn"}),
    "prominence": frozenset({"uphold", "overturn"}),
    # a passage's coverage claim (cflib.vocab.COVERAGE_ACTIONS): refuted by default
    "coverage": frozenset({"confirm", "refute"}),
}
UNRESOLVED_BY = frozenset({"worker", "attempt_cap"})
HAS_PART = "hasPart"
#: a coverage claim's keys and its least number of parts (cflib.vocab.MIN_COVERAGE_PARTS;
#: references/locate-documents.md section 3)
COVERAGE_KEYS = frozenset({"complete", "parts", "note"})
MIN_COVERAGE_PARTS = 2
UNRESOLVED_ATTEMPT_CAP = "attempt_cap"
#: cflib.vocab.LOCATE_ATTEMPT_CAP: the genuine reviewer rejections at which the
#: bridge ends a task UNRESOLVED (scripts/run/attempt_cap.py)
LOCATE_ATTEMPT_CAP = 3
#: the reason ``attempt_cap.cap_reason`` writes; a test pins the two together
ATTEMPT_CAP_REASON = re.compile(r"attempt_cap: ([0-9]+) genuine reviewer rejections; the bridge "
                                r"ended the task without another agent call\Z")
MAX_LOCATIONS_PER_PASSAGE = 5
MIN_ABSENCE_QUERIES = 3
LOCATE_EXTENSION = "org.mathlib-initiative.formal-companion.locate"
#: the judgment rules this gate knows (rule 9): a gate.json names the version its run
#: was planned under as ``judgment_rules``, and one without the key (a run planned
#: before them) is gated without rule 9.  Version 1 is R1-R7; version 2
#: (cflib.vocab.RULES_PD5_PD9) adds PD5-PD9's two mechanical checks: no coverage part
#: that alone names every claim (PD6), and no ``junk_value`` convention (PD7)
RULES_PD5_PD9 = 2
#: The statement cut's versions (docs/proposals/statement-cut.md section 2.2;
#: cflib.vocab.RULES_STATEMENT_CUT, SPAN_END_STATEMENT_CUT, STATEMENT_CUT_RULE): judgment
#: rules 3 (rule 9 against the inventory's conjunct ids: no claim list, a coverage claim
#: only as a bundle, the smoke-3 audit's failure-mode rules), span-end rule 2 (the end
#: at the earlier of the extent and the next passage's start) and statement-cut rule 1
#: (rule 10, the inventory's cut).  Each binds a run only when its gate.json names it,
#: which ``cf plan-locate`` does from the proposal's flip on.  Span-end rule 3 and
#: statement-cut rule 2 (cflib.vocab.SPAN_END_CONTINUED, STATEMENT_CUT_CONTINUED;
#: inv-pilot-1's fixes) are the continued cut: the end and rule 10 (b) read the paragraph
#: that finishes a sentence after its display, and rule 10 adds (h) and further counts;
#: ``cf plan-locate`` writes them from the flip of those fixes on (the proposal's
#: section 7).  Statement-cut rule 3 (cflib.vocab.STATEMENT_CUT_WIDE; inv-pilot-2's
#: repairs) is rule 2 with (b) read against the wide bound (``locate_text.Blocks.runs_on``
#: with ``wide=True``: across footnote marks, and into a ``dd`` or a paragraph that
#: finishes the sentence) and three more counts; it reads span-end rule 3 too, and
#: ``cf plan-locate`` writes it from the flip of the wide bound on (the proposal's
#: section 7).  A statement-cut rule reads exactly one span-end rule
#: (STATEMENT_CUT_SPAN_END)
RULES_STATEMENT_CUT = 3
SPAN_END_STATEMENT_CUT = 2
SPAN_END_CONTINUED = 3
STATEMENT_CUT_FIRST = 1
STATEMENT_CUT_CONTINUED = 2
STATEMENT_CUT_WIDE = 3
STATEMENT_CUT_SPAN_END = {STATEMENT_CUT_FIRST: SPAN_END_STATEMENT_CUT,
                          STATEMENT_CUT_CONTINUED: SPAN_END_CONTINUED,
                          STATEMENT_CUT_WIDE: SPAN_END_CONTINUED}
STATEMENT_CUT_RULE = STATEMENT_CUT_WIDE
#: Judgment rules 4 (cflib.vocab.RULES_SMOKE4: the smoke-4 audit's calls, references/
#: vocabulary.md section 1.11): the statement cut's rule 9 with four more checks -- PD6's
#: shape tripwire on an existence-and-uniqueness or iff bundle (a part that concludes with
#: an iff, an equivalence, a unique existence or a bijection says in ``pd6`` which conjunct
#: it does not state alone, and a confirm of its bundle in ``part_limits``), the reading a
#: confirm credits a bundle's closeMatch part with (``part_conventions``), a part's
#: ``conjunct_relation`` that may say ``hasPart`` (CONJUNCT_RELATIONS_PART), and the bridge
#: of a challenge downgrade of a strong record to relatedMatch (``bridge`` or
#: ``incomparable``).  Each binds a run only when its gate.json names 4
RULES_SMOKE4 = 4
#: the versions the planner writes (cflib.vocab.JUDGMENT_RULES, SPAN_END_RULE and
#: STATEMENT_CUT_RULE: the statement cut's from its flip on, the continued cut's from
#: the flip of inv-pilot-1's fixes on, the wide cut's from the flip of the wide bound
#: on, and judgment rules 4 from the flip of the smoke-4 audit's calls on): a gate.json
#: names the version its run was planned
#: under as ``judgment_rules`` and ``span_end_rule``, and one without ``span_end_rule``
#: (a run planned before it, whose inventories ``passage_ids.py`` wrote without
#: ``until``) is gated without the end check
JUDGMENT_RULES = RULES_SMOKE4
SPAN_END_RULE = SPAN_END_CONTINUED
#: the highest version of each rule this gate can hold a run to: a gate.json naming a
#: higher one was planned by a newer planner than this gate directory (exit 3)
KNOWN_JUDGMENT_RULES = max(JUDGMENT_RULES, RULES_SMOKE4)
KNOWN_SPAN_END_RULE = max(SPAN_END_RULE, SPAN_END_CONTINUED)
KNOWN_STATEMENT_CUT_RULE = max(STATEMENT_CUT_RULE, STATEMENT_CUT_WIDE)
#: The prose floor (rule 8; cflib.vocab.PROSE_FLOOR_FIRST, PROSE_FLOOR_RULE,
#: PROSE_FLOOR_MIN_WORDS, and the back matter of AUDIT_BACK_MATTER): a run whose gate.json
#: names ``prose_floor_rule`` 1 holds every block of paragraphs.json that is no math
#: paragraph, lies outside the back matter and holds at least PROSE_FLOOR_MIN_WORDS words
#: to the coverage floor a math paragraph is held to -- a passage or a skip covers it --
#: so no paragraph of the body is left out silently (the smoke-4 audit: two statements in
#: formula-free paragraphs that no passage and no skip held).  A gate.json without the
#: key (every inventory planned before it) is gated without it
PROSE_FLOOR_FIRST = 1
PROSE_FLOOR_RULE = PROSE_FLOOR_FIRST
KNOWN_PROSE_FLOOR_RULE = max(PROSE_FLOOR_RULE, PROSE_FLOOR_FIRST)
PROSE_FLOOR_MIN_WORDS = 8
BACK_MATTER = ("references", "notes", "footnotes", "citations", "sources", "bibliography",
               "further reading", "external links", "see also", "works cited",
               "notes and references")
#: a compound statement's conjuncts (cflib.vocab's statement-cut block, inlined)
COMPOUND_SHAPES = frozenset({"existence_uniqueness", "iff", "cases", "conclusions",
                             "equations"})
MIN_CONJUNCTS = 2
MAX_CONJUNCTS = 5
SHAPE_CONJUNCT_COUNTS = {"existence_uniqueness": 2}
SIMPLE_STATEMENT_CLAUSE = "c1"
CONJUNCT_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch", "narrowMatch"})
#: judgment rules 4's: a part may say it states only part of its conjuncts
#: (cflib.vocab.CONJUNCT_RELATIONS_PART)
CONJUNCT_RELATIONS_PART = CONJUNCT_RELATIONS | {HAS_PART}
BUNDLE_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch"})
#: PD6's shape tripwire under judgment rules 4 (cflib.vocab.PD6_SHAPES,
#: PD6_CONCLUSION_MARKS): the shapes whose every conjunct one declaration can state as a
#: characterisation, and what such a declaration's conclusion holds
PD6_SHAPES = frozenset({"existence_uniqueness", "iff"})
PD6_CONCLUSION_MARKS = ("↔", "≃", "∃!", "Bijective")
TWO_WAY_TEST_KEYS = ("r2", "r3")
CUT_FLAGS = frozenset({"not_a_statement", "several_statements"})
#: the naturals-for-integers tripwire's words (fix 2b; cflib.vocab.NAT_FOR_INT_INTEGER_WORDS,
#: NAT_FOR_INT_NATURAL_WORDS, matched by word_pattern)
NAT_FOR_INT_INTEGER_WORDS = ("integer", "integers", "ℤ")
NAT_FOR_INT_NATURAL_WORDS = ("ℕ", "natural*")
#: what a passage not scoped formal_math is (rule 10 (f)): the one prominence nothing reads
PROMINENCE_SUPPORTING = "supporting"
#: the prominences an overturn reopens a supporting passage's records to, as
#: refuted-by-default or hunted ones (A3; cflib.vocab.REFUTE_BY_DEFAULT_PROMINENCE)
REFUTE_BY_DEFAULT_PROMINENCE = frozenset({"subject", "central"})
#: the relations that say a declaration states the whole passage
#: (cflib.vocab.STRONG_RELATIONS)
STRONG_RELATIONS = frozenset({"exactMatch", "closeMatch", "broadMatch"})
#: a passage's claim list (cflib.vocab.CLAIM_ID, MAX_PASSAGE_CLAIMS, MAX_CLAIM_CHARS)
CLAIM_ID = re.compile(r"c[1-9][0-9]?\Z")
MAX_PASSAGE_CLAIMS = 12
MAX_CLAIM_CHARS = 300
CLAIM_KEYS = frozenset({"id", "text"})
#: the tag of a query for the general theorem a worked example instantiates
#: (cflib.vocab.GENERAL_QUERY_TAG)
GENERAL_QUERY_TAG = "general"
PASSAGE_EXAMPLE = "example"
#: a proof, a remark or any statement of no other kind
PASSAGE_OTHER = "other"
NARROW_MATCH = "narrowMatch"
EXACT_MATCH = "exactMatch"
#: the relations whose location names the step or clause the declaration alone
#: entails (cflib.vocab.STEP_RELATIONS)
STEP_RELATIONS = frozenset({HAS_PART, NARROW_MATCH})
#: the conventions a closeMatch names, and the words an exact or close note may not
#: say (cflib.vocab.CLOSE_MATCH_CONVENTIONS, CLOSE_MATCH_GENERALITY_PHRASES); under
#: judgment rules 1 the list held ``junk_value`` too
#: (cflib.vocab.LEGACY_CLOSE_MATCH_CONVENTIONS), which PD7 took off it
CONVENTION_JUNK_VALUE = "junk_value"
CLOSE_MATCH_CONVENTIONS = frozenset({"notation", "encoding", "normalisation", "renaming",
                                     "unfolding", "bundling"})
LEGACY_CLOSE_MATCH_CONVENTIONS = CLOSE_MATCH_CONVENTIONS | {CONVENTION_JUNK_VALUE}
CLOSE_MATCH_GENERALITY_PHRASES = ("stronger", "strengthen*", "weaker", "weaken*",
                                  "more general*", "generali*", "special case",
                                  "particular case", "broader", "narrower",
                                  "strict derivative*", "strict differentiab*",
                                  "strictly differentiab*")
#: the list under judgment rules 3 (cflib.vocab.CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT:
#: the smoke-3 audit's fix 2c)
CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT = CLOSE_MATCH_GENERALITY_PHRASES + (
    "table", "for all primes", "every point", "differentiable at")
#: a judgment rule's one-line field (cflib.vocab.MAX_JUDGMENT_LINE_CHARS,
#: MIN_JUDGMENT_LINE_WORDS)
MAX_JUDGMENT_LINE_CHARS = 300
MIN_JUDGMENT_LINE_WORDS = 3
#: the brackets of a binder group in a signature as the index prints it, and the
#: other pairs a binder-position scan keeps its depth by
BINDER_BRACKETS = {"(": ")", "{": "}", "[": "]", "\u2983": "\u2984"}
DEPTH_BRACKETS = {**BINDER_BRACKETS, "\u27e8": "\u27e9"}
#: the phrases by which a strong record's note admits a clause it does not state
#: (cflib.vocab.CLAUSE_GAP_PHRASES; the matcher is cflib.vocab.admits_missing_clause's)
CLAUSE_GAP_PHRASES = ("does not state", "do not state", "not stated", "not part of",
                      "not a part of", "rather than asserting", "rather than stating",
                      "rather than proving", "only the existence", "only existence",
                      "only proves", "only states", "proves only", "states only",
                      "only one direction", "one direction only", "not the converse",
                      "converse is not", "without the converse", "without uniqueness",
                      "uniqueness is not", "does not cover", "do not cover", "not covered",
                      "not asserted", "not included", "omits", "omitted", "leaves out",
                      "left out", "does not prove", "does not assert", "does not establish")
#: the phrases by which an absence's rejected candidate is said to state a part, a
#: case or a generalisation of the passage (cflib.vocab.CANDIDATE_PART_PHRASES)
CANDIDATE_PART_PHRASES = ("only part", "part of the passage", "one clause",
                          "one of the clauses", "not the whole", "not the full",
                          "special case", "not the worked example", "not the example",
                          "partially", "more general*", "generali*")
#: what breaks a line (str.splitlines' boundaries): a one-line field holds none
LINE_BREAKS = re.compile("[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")

#: the one spelling of a passage item a stage writes (``items.passage_item``)
ITEM = re.compile(r"In-text #[0-9a-f]{8}\Z")
QID = re.compile(r"Q[1-9][0-9]*\Z")
#: keys no worker document may carry anywhere: the proof status is joined from
#: the index at export, and the commit is the universe's
FORBIDDEN_KEYS = ("proof_status", "commit", "version")
NOTES_MAX = 2000
#: problems kept (and printed) per gate call; the rest are counted by rule
MAX_PROBLEMS = 200
#: the largest task file rule 7 reads to compare it with the merge base
MAX_TASK_FILE_BYTES = 1024 * 1024
#: defaults gate.json may override
MAX_QUOTE_CHARS = 1500
MAX_CONTEXT_CHARS = 200
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
#: tend's validation runs this from the worktree; a harvested corpus keeps the
#: task directories at its top
DEFAULT_FINDINGS_DIR = "findings"
NON_DOCUMENT_FILES = frozenset(vf.NON_FINDING_FILES) if vf is not None else frozenset()
VENDORED_PACKAGES = ("literature_links", "formal_companion_annotations")


class GateBroken(Exception):
    """The gate cannot run: an infrastructure failure, never a verdict on the documents."""


def ro_uri(path: pathlib.Path, *, immutable: bool = True) -> str:
    """A read-only SQLite URI for ``path`` (``verify_findings.ro_uri``, which both gates use)."""
    return vf.ro_uri(path, immutable=immutable)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text[:24]} is not a finite JSON number")
    return value


#: deeper than this a value is refused before anything walks it; the formats
#: nest about six levels
MAX_JSON_DEPTH = 64


def _check_tree(value: Any) -> None:
    """Refuse a value nested deeper than :data:`MAX_JSON_DEPTH` or holding a lone surrogate.

    Iterative, so no worker document can exhaust the interpreter's stack; a
    lone surrogate (a ``\\ud800`` escape) is not UTF-8 text, and every writer
    downstream encodes UTF-8.
    """
    stack: list[tuple[Any, int, str]] = [(value, 1, "$")]
    while stack:
        node, depth, where = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"nested deeper than {MAX_JSON_DEPTH} levels at {where}")
        if isinstance(node, dict):
            for key, child in node.items():
                _check_text(key, f"a key under {where}")
                stack.append((child, depth + 1, f"{where}.{key[:40]}"))
        elif isinstance(node, list):
            stack.extend((child, depth + 1, f"{where}[{i}]") for i, child in enumerate(node))
        elif isinstance(node, str):
            _check_text(node, where)


def _check_text(text: str, where: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{where} holds a lone surrogate (not UTF-8 text)") from None


def load_json_strict(text: str) -> Any:
    """JSON a second reader reads the same way: no duplicate key, no NaN/Infinity and
    no number that overflows to one, no lone surrogate, at most :data:`MAX_JSON_DEPTH`
    levels deep.  Any of them is a ``ValueError``."""
    try:
        value = json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_constant,
                           parse_float=_finite_float)
    except RecursionError:
        raise ValueError(f"nested deeper than {MAX_JSON_DEPTH} levels") from None
    _check_tree(value)
    return value


def _one_of(value: Any, allowed) -> bool:
    """``value in allowed`` for a worker-written value that may be a list or an object."""
    return isinstance(value, str) and value in allowed


def _positive_int(data: dict, key: str, default: int) -> int:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GateBroken(f"gate.json {key} is {value!r}, not a positive integer")
    return value


def _path_field(value: Any, key: str) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        raise GateBroken(f"gate.json {key} is {value!r}, not a path")
    return pathlib.Path(value).expanduser()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: a sha256 as gate.json pins one
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _sha256_file(path: pathlib.Path) -> str:
    """The sha256 of a file's bytes, read in chunks (a corpus.sqlite is hundreds of MB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_map(value: Any, key: str) -> dict[str, str] | None:
    """A ``{name: sha256}`` pin of gate.json, or None when gate.json has no such key."""
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and SHA256.match(v)
            for k, v in value.items()):
        raise GateBroken(f"gate.json {key} is not a map of names to sha256 pins")
    return dict(value)


def _rule_version(data: dict, key: str, what: str, known: int) -> int:
    """The version of a gate rule a gate.json names under ``key``: 0 when it names none.

    A rule the gate gained after a run was planned binds the run only when its
    planner wrote the key (``judgment_rules`` for rule 9, ``span_end_rule`` for
    rule 2's end check), so a re-gate judges an older run by the rules it ran
    under.  A value that is not a non-negative integer, or a version above the
    gate's own (a gate directory older than its planner), is a broken gate.json.
    """
    version = data.get(key)
    version = 0 if version is None else version
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise GateBroken(f"gate.json {key} is {version!r}, not a version number")
    if version > known:
        raise GateBroken(f"gate.json asks for {what} {version}, and this gate knows "
                         f"{known}: the gate directory is older than its planner")
    return version


def _safe_name(value: Any) -> bool:
    """An id that names one file inside a directory and nothing else."""
    return (isinstance(value, str) and bool(value.strip()) and "/" not in value
            and "\\" not in value and "\x00" not in value and value not in (".", "..")
            and not value.startswith("."))


class GateConfig:
    """``gate.json`` for a locate run (``cf-gate-locate/1``), validated on load."""

    def __init__(self, data: Any, path: pathlib.Path | None = None):
        if not isinstance(data, dict):
            raise GateBroken("gate.json is not a JSON object")
        if data.get("schema") != GATE_SCHEMA:
            raise GateBroken(f"gate.json schema is {data.get('schema')!r}, not {GATE_SCHEMA!r} "
                             f"(this is the locate gate; a faithfulness run uses verify_findings.py)")
        self.data = data
        self.path = path
        self.mode = str(data.get("mode") or "")
        if self.mode not in LOCATE_MODES:
            raise GateBroken(f"gate.json mode {self.mode!r} is not a locate mode")
        self.preset = str(data.get("preset") or self.mode)
        self.run_id = str(data.get("run_id") or "")
        if not self.run_id:
            raise GateBroken("gate.json has no run_id")
        kinds = data.get("kinds")
        if kinds is not None and not (isinstance(kinds, list)
                                      and all(isinstance(k, str) for k in kinds)):
            raise GateBroken(f"gate.json kinds is {kinds!r}, not a list of kinds")
        self.kinds = frozenset(kinds or KINDS_BY_MODE[self.mode])
        universe = data.get("universe") if isinstance(data.get("universe"), dict) else {}
        self.universe_id = str(universe.get("id") or "")
        self.universe_sha = str(universe.get("sha256") or "")
        libraries = universe.get("libraries") if isinstance(universe.get("libraries"), dict) else {}
        self.libraries: dict[str, dict] = {}
        for lib, body in sorted(libraries.items()):
            if not isinstance(body, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(body.get("commit") or "")):
                raise GateBroken(f"gate.json universe library {lib!r} has no 40-hex commit")
            self.libraries[str(lib)] = {"repo": str(body.get("repo") or ""),
                                        "commit": str(body["commit"])}
        if self.mode != LOCATE_INVENTORY and not (self.universe_id and self.universe_sha
                                                  and self.libraries):
            raise GateBroken("gate.json has no frozen universe (id, sha256, libraries); a "
                             f"{self.mode} run cannot be gated without one")
        corpus = data.get("corpus_db") if isinstance(data.get("corpus_db"), dict) else {}
        self.corpus_db = (_path_field(corpus["path"], "corpus_db.path") if corpus.get("path")
                          else None)
        self.corpus_db_sha = str(corpus.get("sha256") or "")
        sourceset = data.get("sourceset") if isinstance(data.get("sourceset"), dict) else {}
        if not sourceset.get("sources_dir"):
            raise GateBroken("gate.json sourceset has no sources_dir")
        self.sourceset = sourceset
        self.sources_dir = _path_field(sourceset["sources_dir"], "sourceset.sources_dir")
        sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        if not sources:
            raise GateBroken("gate.json pins no sources (the gate fails closed)")
        self.sources: dict[str, dict] = {str(k): dict(v) for k, v in sources.items()
                                         if isinstance(v, dict)}
        self.candidates_dir = (_path_field(data["candidates_dir"], "candidates_dir")
                               if data.get("candidates_dir") else None)
        #: ``{source_id: sha256}`` of each candidates file the plan read, or None for a
        #: gate.json planned before the pin (whose candidates are read as found)
        self.candidates_sha256 = _sha_map(data.get("candidates_sha256"), "candidates_sha256")
        self.blind = bool(data.get("blind"))
        if not data.get("tasks_dir"):
            raise GateBroken("gate.json names no tasks_dir (the task manifests)")
        self.tasks_dir = _path_field(data["tasks_dir"], "tasks_dir")
        self.log_dir = _path_field(data["log_dir"], "log_dir") if data.get("log_dir") else None
        self.orchestrator_db = (_path_field(data["orchestrator_db"], "orchestrator_db")
                                if data.get("orchestrator_db") else None)
        self.max_locations = _positive_int(data, "max_locations", MAX_LOCATIONS_PER_PASSAGE)
        self.min_absence_queries = _positive_int(data, "min_absence_queries", MIN_ABSENCE_QUERIES)
        self.max_quote_chars = _positive_int(data, "max_quote_chars", MAX_QUOTE_CHARS)
        self.max_context_chars = _positive_int(data, "max_context_chars", MAX_CONTEXT_CHARS)
        self.max_document_bytes = _positive_int(data, "max_document_bytes", MAX_DOCUMENT_BYTES)
        self.vendor = data.get("vendor") if isinstance(data.get("vendor"), dict) else {}
        #: rule 9 binds a run planned under the judgment rules, and no other
        self.judgment_rules = _rule_version(data, "judgment_rules", "judgment rules",
                                            KNOWN_JUDGMENT_RULES)
        #: rule 2's end check binds a run planned under the span-end rule, and no other
        self.span_end_rule = _rule_version(data, "span_end_rule", "span-end rule",
                                           KNOWN_SPAN_END_RULE)
        #: rule 10 (the statement cut) binds a run planned under it, and no other
        self.statement_cut_rule = _rule_version(data, "statement_cut_rule",
                                                "statement-cut rule", KNOWN_STATEMENT_CUT_RULE)
        #: rule 8's prose floor binds an inventory planned under it, and no other
        self.prose_floor_rule = _rule_version(data, "prose_floor_rule", "prose-floor rule",
                                              KNOWN_PROSE_FLOOR_RULE)
        self._check_the_pair()

    def _check_the_pair(self) -> None:
        """A statement-cut rule reads exactly one span-end rule, and span-end rule 3 only
        with statement-cut rule 2 or 3 (docs/proposals/statement-cut.md section 2.2): any
        other pair is a broken gate.json (exit 3).  No key, span-end rule 1 or 2 alone,
        (2, 1) and (3, 2) are what runs planned before the flip of the wide bound name, and
        stay admitted, and (3, 3) is what the planner writes from it.  The message for
        span-end rule 3 with no statement-cut rule, which only a broken gate.json prints,
        is the one it was."""
        cut, end = self.statement_cut_rule, self.span_end_rule
        if cut:
            if cut == STATEMENT_CUT_FIRST and end < SPAN_END_STATEMENT_CUT:
                # rule 10's disjointness rests on the end rule that stops a passage at the
                # next one's start
                raise GateBroken(f"gate.json names statement_cut_rule {cut} with "
                                 f"span_end_rule {end}: the statement cut needs span-end "
                                 f"rule {SPAN_END_STATEMENT_CUT}")
            need = STATEMENT_CUT_SPAN_END[cut]
            if end != need:
                raise GateBroken(f"gate.json names statement_cut_rule {cut} with "
                                 f"span_end_rule {end}: statement-cut rule {cut} reads "
                                 f"span-end rule {need}, and no other")
        elif end >= SPAN_END_CONTINUED:
            raise GateBroken(f"gate.json names span_end_rule {end} without statement_cut_rule "
                             f"{STATEMENT_CUT_CONTINUED}: span-end rule {end} is the continued "
                             f"cut's end, and binds only with its rule 10")

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        try:
            data = load_json_strict(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"gate config at {path} is unreadable ({exc})") from None
        try:
            return cls(data, path)
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            # a value of the wrong shape is a broken gate.json (exit 3), never a
            # traceback the worker would read as exit 1
            raise GateBroken(f"gate config at {path} has a malformed value "
                             f"({exc.__class__.__name__}: {exc})") from None


def verify_vendor(cfg: GateConfig, vendor_dir: pathlib.Path = VENDOR) -> None:
    """Both vendored packages are the pinned releases, byte for byte; else :class:`GateBroken`.

    The manifest of each is hashed and compared with ``gate.json``'s ``vendor``
    pin, and every file the manifest lists is hashed and compared with the
    manifest: a gate whose checker was edited on the host is not a gate.
    """
    for package in VENDORED_PACKAGES:
        manifest_path = vendor_dir / f"{package}.manifest.json"
        want = str(cfg.vendor.get(package) or "")
        if not want:
            raise GateBroken(f"gate.json pins no manifest sha for the vendored {package}")
        try:
            raw = manifest_path.read_bytes()
        except OSError as exc:
            raise GateBroken(f"the vendored {package} manifest is missing ({exc})") from None
        got = _sha256_bytes(raw)
        if got != want:
            raise GateBroken(f"the vendored {package} manifest is {got[:12]}…, gate.json pins "
                             f"{want[:12]}…: not the release this run was planned with")
        try:
            files = json.loads(raw.decode("utf-8")).get("files") or {}
        except (ValueError, AttributeError):
            raise GateBroken(f"the vendored {package} manifest does not parse") from None
        for rel, sha in sorted(files.items()):
            path = vendor_dir / package / rel
            try:
                actual = _sha256_bytes(path.read_bytes())
            except OSError:
                raise GateBroken(f"vendored file {package}/{rel} is missing") from None
            if actual != sha:
                raise GateBroken(f"vendored file {package}/{rel} is not the manifest's bytes "
                                 f"(edited on this host?)")


# --------------------------------------------------------------------------- #
# per-call caches: only what the documents name is loaded
# --------------------------------------------------------------------------- #

class SourceView:
    """One pinned source's layer, head map and tables, loaded on first use."""

    def __init__(self, cfg: GateConfig, source_id: str):
        self.source_id = source_id
        self.pin = cfg.sources.get(source_id)
        if self.pin is None:
            raise KeyError(source_id)
        self.dir = cfg.sources_dir / source_id
        layer_path = self.dir / "layer.txt"
        try:
            raw = layer_path.read_bytes()
        except OSError as exc:
            raise GateBroken(f"{source_id}: layer.txt is missing at {layer_path} ({exc})") from None
        want = str(self.pin.get("layer_sha256") or "")
        got = _sha256_bytes(raw)
        if not want or got != want:
            raise GateBroken(f"{source_id}: layer.txt on this host is {got[:12]}…, the run pinned "
                             f"{want[:12] or 'nothing'}… (not the rendering this run was planned on)")
        self.layer = raw.decode("utf-8")
        self.view_pins = self._view_pins()
        try:
            formulas = load_json_strict(self._view_bytes("formulas.json").decode("utf-8"))
        except ValueError as exc:
            raise GateBroken(f"{source_id}: formulas.json is unreadable ({exc})") from None
        if not isinstance(formulas, list) or not all(isinstance(f, dict) for f in formulas):
            raise GateBroken(f"{source_id}: formulas.json is not a list of formula entries")
        try:
            self.map = lt.HeadMap(self.layer, formulas)
        except lt.HeadError as exc:
            raise GateBroken(f"{source_id}: formulas.json does not fit the layer ({exc})") from None
        self.formulas = formulas
        self._paragraphs: list[dict] | None = None
        self._blocks = None
        self._views: tuple[str, str] | None = None

    @property
    def head(self) -> str:
        return self.map.head

    def _view_pins(self) -> dict[str, str] | None:
        """The pin's ``views`` (``{"formulas.json": sha, "paragraphs.json": sha}``), or None.

        The planner copies them from the source's ``render.json``; a gate.json
        planned before the pin has none, and its tables are read as found.
        """
        views = self.pin.get("views")
        if views is None:
            return None
        if not isinstance(views, dict) or not all(
                isinstance(views.get(name), str) and SHA256.match(views[name])
                for name in PINNED_VIEWS):
            raise GateBroken(f"gate.json sources.{self.source_id}.views is not a sha256 pin of "
                             f"each of {', '.join(PINNED_VIEWS)}")
        return {name: views[name] for name in PINNED_VIEWS}

    def _view_bytes(self, name: str) -> bytes:
        """``<source>/<name>``'s bytes, held to the pin's ``views`` sha when it has one."""
        path = self.dir / name
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GateBroken(f"{self.source_id}: {name} is unreadable ({exc})") from None
        if self.view_pins is not None:
            got, want = _sha256_bytes(raw), self.view_pins[name]
            if got != want:
                raise GateBroken(f"{self.source_id}: {name} on this host is {got[:12]}…, the run "
                                 f"pinned {want[:12]}… (not the rendering this run was planned "
                                 f"on)")
        return raw

    def views(self) -> tuple[str, str]:
        """The two other spellings of the source a worker may copy from (rule 9's cap).

        The layer with every format character dropped (a zero-width space, a soft
        hyphen), and the reading copy's spelling: ``text.txt`` writes each formula
        as ``$<normalized alttext>$`` (``sources.render_html``), where the layer
        carries the formula's fallback text and its ``{\\displaystyle ...}``
        annotation, so a sentence copied from ``text.txt`` with a formula in it
        never matches the layer.  Both are built here from ``layer.txt`` and
        ``formulas.json``, the files this gate already pins.
        """
        if self._views is None:
            pieces, cursor = [], 0
            for formula in self.formulas:       # sorted and disjoint: HeadMap checked it
                a, b = (int(x) for x in formula["span"])
                pieces.append(self.layer[cursor:a])
                pieces.append(f"${lt.normalize_alttext(str(formula.get('alttext', '')))}$")
                cursor = b
            pieces.append(self.layer[cursor:])
            self._views = (strip_format(self.layer), strip_format("".join(pieces)))
        return self._views

    def blocks(self):
        """``locate_text.Blocks`` of this source (paragraphs and formulas), built once.

        What span-end rule 2 and rule 10 read (docs/proposals/statement-cut.md
        section 3.1); a run planned before them never builds it.
        """
        if self._blocks is None:
            self._blocks = lt.Blocks(self.layer, self.paragraphs(), self.formulas)
        return self._blocks

    def paragraphs(self) -> list[dict]:
        if self._paragraphs is None:
            try:
                data = load_json_strict(self._view_bytes("paragraphs.json").decode("utf-8"))
            except ValueError as exc:
                raise GateBroken(f"{self.source_id}: paragraphs.json is unreadable ({exc})") from None
            if not isinstance(data, list) or not all(isinstance(p, dict) for p in data):
                raise GateBroken(f"{self.source_id}: paragraphs.json is not a list of block "
                                 f"entries")
            self._paragraphs = data
        return self._paragraphs


def check_corpus_pin(cfg: GateConfig) -> None:
    """``corpus.sqlite`` is the bytes gate.json pins (``corpus_db.sha256``); else :class:`GateBroken`.

    Rule 3 is only as good as the database it asks: a doctored copy with one
    more row would pass a phantom declaration.  ``cf run``'s verify stage checks
    the sha once on the host, but a gate run anywhere else (a contributor's
    machine, a harvest's re-gate) had nothing comparing it, so the gate hashes
    the file itself when it first opens it -- once per call, in the modes that
    read it.  A database rebuilt after planning is refused here too, because two
    builds of one universe are not byte identical: re-plan to pin the new one.
    """
    if cfg.corpus_db is None:
        raise GateBroken("gate.json names no corpus_db; rule 3 fails closed")
    if not SHA256.match(cfg.corpus_db_sha):
        raise GateBroken("gate.json pins no corpus_db.sha256; rule 3 fails closed without the "
                         "database's own pin")
    if not cfg.corpus_db.is_file():
        raise GateBroken(f"corpus.sqlite is missing at {cfg.corpus_db}")
    try:
        got = _sha256_file(cfg.corpus_db)
    except OSError as exc:
        raise GateBroken(f"corpus.sqlite at {cfg.corpus_db} is unreadable ({exc})") from None
    if got != cfg.corpus_db_sha:
        raise GateBroken(f"corpus.sqlite at {cfg.corpus_db} is {got[:12]}…, gate.json pins "
                         f"{cfg.corpus_db_sha[:12]}… (not the database this run was planned "
                         f"with)")


def check_candidates_pin(cfg: GateConfig, source_id: str, raw: bytes, path: pathlib.Path) -> None:
    """A candidates file is the bytes gate.json pins (``candidates_sha256``); else :class:`GateBroken`.

    Rule 4 reads the curated hits from this file, never from the document, so a
    file swapped or edited after planning would change what the rule asks.  A
    gate.json planned before the pin (no ``candidates_sha256``) reads the file
    as found; one that has the key pins every source whose file it reads.
    """
    pins = cfg.candidates_sha256
    if pins is None:
        return
    want = pins.get(source_id)
    if not want:
        raise GateBroken(f"{source_id}: gate.json pins the candidates files and none for this "
                         f"source: gate rule 4 fails closed")
    got = _sha256_bytes(raw)
    if got != want:
        raise GateBroken(f"{source_id}: candidates file {path} is {got[:12]}…, gate.json pins "
                         f"{want[:12]}… (not the file this run was planned with)")


class Context:
    """Everything one gate call loads lazily: sources, candidates, manifests, the corpus."""

    def __init__(self, cfg: GateConfig, *, check_counter: bool = False):
        self.cfg = cfg
        #: read the attempt counter for a ``by: attempt_cap`` document (tend's
        #: validation runs beside it; a harvested corpus has none)
        self.check_counter = check_counter
        self._sources: dict[str, SourceView] = {}
        self._candidates: dict[str, dict | None] = {}
        self._manifests: dict[str, dict | None] = {}
        self._db: sqlite3.Connection | None = None
        self._exists: dict[tuple[str, str, str], bool] = {}
        self._signatures: dict[tuple[str, str, str], str | None] = {}

    def source(self, source_id: str) -> SourceView | None:
        if source_id not in self.cfg.sources or not _safe_name(source_id):
            return None
        if source_id not in self._sources:
            self._sources[source_id] = SourceView(self.cfg, source_id)
        return self._sources[source_id]

    def manifest(self, task_id: str) -> dict | None:
        """``gate/tasks/<task-id>.json``, or None when there is no such task."""
        if task_id in self._manifests:
            return self._manifests[task_id]
        if not self.cfg.tasks_dir.is_dir():
            raise GateBroken(f"the task-manifest directory {self.cfg.tasks_dir} is missing")
        found = None
        if _safe_name(task_id):
            path = self.cfg.tasks_dir / f"{task_id}.json"
            if path.is_file():
                try:
                    data = load_json_strict(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise GateBroken(f"task manifest {path} is unreadable ({exc})") from None
                if not isinstance(data, dict) or data.get("task_id") != task_id:
                    raise GateBroken(f"task manifest {path} is not task {task_id!r}'s")
                found = data
        self._manifests[task_id] = found
        return found

    def candidates(self, source_id: str) -> dict | None:
        """The L2 candidates file for a source, or None in the blind arm / with no L2."""
        if self.cfg.blind or self.cfg.candidates_dir is None:
            return None
        if source_id not in self._candidates:
            path = self.cfg.candidates_dir / f"{source_id}.json"
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                raise GateBroken(f"{source_id}: no candidates file at {path}: gate rule 4 reads "
                                 f"L2 itself and fails closed without it") from None
            except OSError as exc:
                raise GateBroken(f"{source_id}: candidates file {path} is unreadable ({exc})") from None
            check_candidates_pin(self.cfg, source_id, raw, path)
            try:
                data = load_json_strict(raw.decode("utf-8"))
            except ValueError as exc:
                raise GateBroken(f"{source_id}: candidates file {path} is unreadable ({exc})") from None
            if not isinstance(data, dict) or data.get("source_id") != source_id:
                raise GateBroken(f"{path} is not {source_id}'s candidates file")
            if data.get("universe") not in (None, self.cfg.universe_id) or (
                    data.get("universe_sha256") not in (None, self.cfg.universe_sha)):
                raise GateBroken(f"{path} was built for universe {data.get('universe')!r}, not "
                                 f"this run's {self.cfg.universe_id!r}")
            self._candidates[source_id] = data
        return self._candidates[source_id]

    def _corpus(self) -> sqlite3.Connection:
        if self._db is None:
            check_corpus_pin(self.cfg)
            try:
                self._db = sqlite3.connect(ro_uri(self.cfg.corpus_db), uri=True)
            except sqlite3.Error as exc:
                raise GateBroken(f"corpus.sqlite at {self.cfg.corpus_db} will not open "
                                 f"({exc})") from None
        return self._db

    def exists(self, library: str, name: str) -> bool:
        """Is ``library:name`` in the exact table at the universe's commit?"""
        lib = self.cfg.libraries.get(library)
        if lib is None:
            return False
        key = (library, lib["commit"], name)
        if key not in self._exists:
            try:
                row = self._corpus().execute(
                    "SELECT 1 FROM decls WHERE library = ? AND commit_sha = ? AND name = ? LIMIT 1",
                    key).fetchone()
            except sqlite3.Error as exc:
                raise GateBroken(f"corpus.sqlite is not the cf-corpus/1 schema ({exc})") from None
            self._exists[key] = row is not None
        return self._exists[key]

    def signature(self, library: str, name: str) -> str | None:
        """``library:name``'s printed type in the exact table at the universe's commit.

        The ``decls.signature`` column the library probe recorded (cf-corpus/1,
        references/locate-documents.md section 8), or None when the declaration is
        not there or its type was not recorded.
        """
        lib = self.cfg.libraries.get(library)
        if lib is None:
            return None
        key = (library, lib["commit"], name)
        if key not in self._signatures:
            try:
                row = self._corpus().execute(
                    "SELECT signature FROM decls WHERE library = ? AND commit_sha = ? "
                    "AND name = ? LIMIT 1", key).fetchone()
            except sqlite3.Error as exc:
                raise GateBroken(f"corpus.sqlite is not the cf-corpus/1 schema ({exc})") from None
            self._signatures[key] = (row[0] if row is not None and isinstance(row[0], str)
                                     and row[0].strip() else None)
        return self._signatures[key]

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


# --------------------------------------------------------------------------- #
# problems
# --------------------------------------------------------------------------- #

class Problems:
    """``(rule, message)`` pairs, printed as ``[rule N] <where>: <message>``.

    At most :data:`MAX_PROBLEMS` are kept; the rest are only counted, by rule, so
    a document that is wrong a million times over costs a million counts, not a
    million lines on stderr.
    """

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self.more: dict[str, int] = {}

    def add(self, rule: int | str, where: str, message: str) -> None:
        if len(self.items) >= MAX_PROBLEMS:
            self.more[str(rule)] = self.more.get(str(rule), 0) + 1
            return
        self.items.append((str(rule), f"{where}: {message}"))

    def extend(self, other: "Problems") -> None:
        for rule, message in other.items:
            if len(self.items) >= MAX_PROBLEMS:
                self.more[rule] = self.more.get(rule, 0) + 1
            else:
                self.items.append((rule, message))
        for rule, count in other.more.items():
            self.more[rule] = self.more.get(rule, 0) + count

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items) + sum(self.more.values())

    def lines(self) -> list[str]:
        out = [f"[rule {rule}] {message}" for rule, message in self.items]
        if self.more:
            out.append(f"... and {sum(self.more.values())} more problem(s) not listed ("
                       + ", ".join(f"rule {r}: {n}" for r, n in sorted(self.more.items()))
                       + f"); the first {MAX_PROBLEMS} are above")
        return out

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = dict(self.more)
        for rule, _ in self.items:
            out[rule] = out.get(rule, 0) + 1
        return dict(sorted(out.items()))


def _forbidden_keys(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_forbidden_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_forbidden_keys(value, f"{path}[{i}]"))
    return found


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _span(value: Any, length: int) -> tuple[int, int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in value)
            and 0 <= value[0] < value[1] <= length):
        return value[0], value[1]
    return None


def _end_problem(layer: str, paragraphs: Any, span: tuple[int, int], exact: str,
                 until: Any, *, rule: int = 1, next_start: int | None = None,
                 formulas: list[dict] | None = None) -> str:
    """Why an inventory passage's end is not the one ``passage_ids.py``'s rule gives, or ''.

    ``locate_text.passage_end`` is the rule (references/locate-documents.md
    section 2): the end of the passage's ``until``, else of the paragraph it
    starts in, else of its quote.  Since the export carries the span, the end
    is the extent a consumer highlights, so any other end is refused: an
    offset edited by hand, one past the paragraph or into the next passage
    with no ``until`` that puts it there, or one counted in UTF-16 units.
    ``rule`` is the gate.json's ``span_end_rule``: under 2 (the statement cut,
    docs/proposals/statement-cut.md section 3.2) a passage without ``until`` ends
    at the earlier of its extent and ``next_start``, the start of the document's
    next passage, less the separator before that start
    (``locate_text.Blocks.separator_start``) when it is the earlier, and
    ``paragraphs`` is the source's ``locate_text.Blocks``; under 3 the same with
    the continued extent (``continued=True``).
    """
    try:
        want = lt.passage_end(layer, paragraphs, span[0], exact, until, rule=rule,
                              next_start=next_start, formulas=formulas)
    except lt.HeadError as exc:
        return f"{exc}; give the passage's last words exactly as the layer has them"
    if span[1] == want:
        return ""
    if until not in (None, ""):
        why = "the end of its `until`"
    elif rule >= SPAN_END_STATEMENT_CUT:
        continued = rule >= SPAN_END_CONTINUED
        bound = lt.Blocks.of(layer, paragraphs, formulas).extent(span[0], continued=continued)
        if next_start is not None and (bound is None or next_start < bound):
            why = "the start of the separator before the next passage"
        elif bound is not None and continued:
            why = ("the end of its block, of the displays its last sentence introduces and of "
                   "the paragraph that finishes that sentence after them")
        elif bound is not None:
            why = "the end of its block and of the displays its last sentence introduces"
        else:
            why = "the end of its quote"
    elif lt.paragraph_end(paragraphs, span[0]) is not None:
        why = "the end of the paragraph it starts in"
    else:
        why = "the end of its quote"
    return (f"the passage ends at {span[1]}, and passage_ids.py ends it at {want} ({why}; "
            f"offsets count code points): run passage_ids.py again rather than edit an "
            f"offset, and give `until` to end it elsewhere")


def in_back_matter(section: Any) -> bool:
    """A block of the back matter: its top heading is one of :data:`BACK_MATTER`."""
    return (isinstance(section, list) and bool(section)
            and str(section[0]).strip().casefold() in BACK_MATTER)


def prose_floor_applies(paragraph: dict, text: str) -> bool:
    """The prose floor (rule 8 under ``prose_floor_rule`` 1) holds a ``paragraphs.json``
    block that is no math paragraph, lies outside the back matter and holds at least
    :data:`PROSE_FLOOR_MIN_WORDS` words of the layer: a passage or a skip covers it."""
    return (not paragraph.get("math") and not in_back_matter(paragraph.get("section"))
            and len(text.split()) >= PROSE_FLOOR_MIN_WORDS)


def _union_length(spans: list[tuple[int, int]]) -> int:
    """How many layer characters the spans cover together."""
    total, reach = 0, -1
    for a, b in sorted(spans):
        if b <= reach:
            continue
        total += b - max(a, reach)
        reach = b
    return total


class _SpanIndex:
    """Non-empty half-open layer spans, sorted once, for rule 8's three questions.

    Each skip, finder and math paragraph asks one question of the kept passages
    or of the skips; asked by scanning, a document of n passages and n skips cost
    O(n^2), so a 4 MB document could hold the gate for minutes.  Sorted by start,
    with the furthest end of every prefix and the nearest end of every suffix,
    each question is one bisection.
    """

    def __init__(self, spans: Iterable[tuple[int, int]]) -> None:
        self.spans = sorted(set(spans))
        self.starts = [a for a, _b in self.spans]
        #: index of the span reaching furthest among spans[:i + 1]
        self._furthest: list[int] = []
        best = -1
        for i, (_a, b) in enumerate(self.spans):
            if best < 0 or b > self.spans[best][1]:
                best = i
            self._furthest.append(best)
        #: index of the span ending first among spans[i:]
        self._first_end = [0] * len(self.spans)
        best = -1
        for i in range(len(self.spans) - 1, -1, -1):
            if best < 0 or self.spans[i][1] <= self.spans[best][1]:
                best = i
            self._first_end[i] = best

    def overlapping(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span sharing a character with ``span``, else None."""
        i = bisect.bisect_left(self.starts, span[1])           # spans[:i] start before its end
        if i == 0:
            return None
        hit = self.spans[self._furthest[i - 1]]
        return hit if hit[1] > span[0] else None

    def covering(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span holding the whole of ``span``, else None."""
        i = bisect.bisect_right(self.starts, span[0])          # spans[:i] start at or before it
        if i == 0:
            return None
        hit = self.spans[self._furthest[i - 1]]
        return hit if hit[1] >= span[1] else None

    def inside(self, span: tuple[int, int]) -> tuple[int, int] | None:
        """A span lying wholly inside ``span``, else None."""
        i = bisect.bisect_left(self.starts, span[0])           # spans[i:] start at or after it
        if i == len(self.spans):
            return None
        hit = self.spans[self._first_end[i]]
        return hit if hit[1] <= span[1] else None


# --------------------------------------------------------------------------- #
# the preview records (rule 1): what the export will build, checked here
# --------------------------------------------------------------------------- #

#: what the gate stands in for when it builds a record the worker's document
#: implies: the challenge, the location key and the joined proof status are the
#: harvest's and the export's to fill, never the worker's
_PLACEHOLDER_SHA = "0" * 64
_WEB_SOURCE_ID = re.compile(r"[a-z][a-z0-9]{1,23}-([1-9][0-9]*)-([1-9][0-9]*)\Z")


def capped_quote(layer: str, span: tuple[int, int]) -> dict:
    """The export's quote of a passage (``locate_records.cap_quote``); RecordError when none."""
    return lr.cap_quote(layer, span[0], span[1])


def _preview_source(cfg: GateConfig, source_id: str) -> dict:
    pin = cfg.sources.get(source_id) or {}
    match = _WEB_SOURCE_ID.match(source_id)
    return {"source_id": source_id, "pageid": int(match.group(1)) if match else 0,
            "revision": match.group(2) if match else "0",
            "title": pin.get("title") or source_id,
            "html_sha256": pin.get("html_sha256") or _PLACEHOLDER_SHA,
            "layer_sha256": pin.get("layer_sha256") or _PLACEHOLDER_SHA,
            "renderer": pin.get("renderer") or "", "uri": pin.get("uri") or source_id,
            "container_title": pin.get("container_title") or "",
            "licence": pin.get("licence") or ""}


def _preview_universe(cfg: GateConfig) -> dict:
    return {"id": cfg.universe_id or "inventory", "sha256": cfg.universe_sha or _PLACEHOLDER_SHA,
            "libraries": [{"id": lib, **body} for lib, body in sorted(cfg.libraries.items())]}


def _own_words(exc: Exception, where: str, item: str) -> str:
    """Rule 1's message for a free text the export refuses as the source verbatim.

    The export's message starts with the item (``locate_records._free_text``);
    a ``where`` that already ends with it (a locate document's passage) names it
    once, while a challenge verdict's ``verdicts[i]`` keeps it.
    """
    message, lead = str(exc), f"{item}: "
    if where.endswith(f": {item}") and message.startswith(lead):
        message = message[len(lead):]
    return f"{message}; the export refuses it: say it in your own words"


def check_records(cfg: GateConfig, problems: Problems, where: str, *, source_id: str,
                  layer: str, span: tuple[int, int], quote: dict, item: str,
                  passage_kind: Any, outcome: str | None, locations: list[dict],
                  receipt: dict | None = None) -> None:
    """Rule 1: build the records this passage implies with the EXPORT'S builder, and check them.

    ``locate_records`` (``scripts/locate/records.py``, staged in the gate
    directory) is the code ``cf export literature-links`` runs, so a document
    this passes is one the export can turn into records the vendored checker
    accepts.  That includes the export's cap on free text: every location's note
    goes through ``locate_records.check_note``, and an absence's receipt is built
    by ``locate_records.receipt_document`` -- the export's own two calls -- so a
    note, a query or a candidate's reason that carries more than the quote cap of
    the source verbatim is refused here, while the worker can still rewrite it.
    What the worker does not control -- the challenge value, the location key,
    the proof status joined from the index, the source's citation fields when
    ``gate.json`` does not carry them -- is a placeholder.
    """
    source = _preview_source(cfg, source_id)
    universe = _preview_universe(cfg)
    passage = {"item": item, "source_id": source_id, "challenge": "not_required",
               "passage_kind": passage_kind if passage_kind in PASSAGE_KINDS else "other",
               "span": [span[0], span[1]], "absence": {"challenge": "not_required"}}
    span_sha = lr.sha256_text(layer[span[0]:span[1]])
    verbatim = False
    for loc in locations:
        try:
            lr.check_note(item, loc.get("note") if isinstance(loc.get("note"), str) else "",
                          layer)
        except lr.RecordError as exc:
            problems.add(1, where, _own_words(exc, where, item))
            verbatim = True
    reference = {"sha256": _PLACEHOLDER_SHA, "queries": 0, "candidates": 0}
    if outcome == "absent":
        try:
            _digest, _text, reference = lr.receipt_document(source_id, item, receipt or {},
                                                            universe, layer)
        except lr.RecordError as exc:
            problems.add(1, where, _own_words(exc, where, item))
            verbatim = True
    if verbatim:
        return
    records: list[dict] = []
    try:
        for loc in locations:
            body = cfg.libraries.get(str(loc.get("library") or ""))
            if body is None:
                continue                      # rule 3 names the library already
            location = {"declaration": str(loc.get("declaration") or ""),
                        "relation": str(loc.get("relation") or ""),
                        "proof_status": "proved", "location_key": _PLACEHOLDER_SHA,
                        "challenge": "not_required",
                        "confidence": loc.get("confidence") if isinstance(loc.get("confidence"), str) else None,
                        "note": loc.get("note") if isinstance(loc.get("note"), str) else None}
            if isinstance(loc.get("rank"), int) and not isinstance(loc.get("rank"), bool):
                location["rank"] = loc["rank"]
            records.append(lr.location_record(source, passage, location,
                                              {"id": str(loc["library"]), **body}, universe, quote,
                                              span_sha256=span_sha))
        if outcome == "absent" and universe["libraries"]:
            records.append(lr.absence_record(source, passage, universe, quote,
                                             span_sha256=span_sha, receipt=reference))
        elif not locations:
            records.append(lr.unresolved_record(source, passage, universe, quote,
                                                span_sha256=span_sha))
    except (lr.RecordError, KeyError, TypeError, ValueError) as exc:
        problems.add(1, where, f"the record this implies cannot be built: {exc}")
        return
    for record in records:
        for problem in lr.check(record):
            problems.add(1, where, f"the record this implies fails the Literature Links "
                                   f"checker: {problem}")


# --------------------------------------------------------------------------- #
# the documents
# --------------------------------------------------------------------------- #

def _header(doc: dict, name: str, cfg: GateConfig, task_id: str, manifest: dict | None,
            problems: Problems) -> str:
    """Rule 1's header half and rule 2's pins; returns the source id ('' when unusable)."""
    if doc.get("schema") != DOCUMENT_SCHEMA:
        problems.add(1, name, f"schema is {doc.get('schema')!r}, not {DOCUMENT_SCHEMA!r}")
    if doc.get("mode") != cfg.mode:
        problems.add(1, name, f"mode is {doc.get('mode')!r}, this run is {cfg.mode!r}")
    if doc.get("run_id") != cfg.run_id:
        problems.add(1, name, f"run_id is {doc.get('run_id')!r}, this run is {cfg.run_id!r}")
    if doc.get("task_id") != task_id:
        problems.add(1, name, f"task_id is {doc.get('task_id')!r}, but the document sits in "
                              f"task {task_id!r}'s directory")
    source_id = doc.get("source_id")
    if not _safe_name(source_id):
        problems.add(1, name, f"source_id {source_id!r} is not a source id")
        return ""
    if manifest is not None:
        named = [d.get("source_id") for d in manifest.get("documents") or [] if isinstance(d, dict)]
        if source_id not in named:
            problems.add(1, name, f"source {source_id} is not one of task {task_id}'s documents "
                                  f"({', '.join(map(str, named)) or 'none'})")
    pin = cfg.sources.get(source_id)
    if pin is None:
        problems.add(2, name, f"source {source_id} is not pinned by this run")
        return ""
    for key, want_key in (("source_sha", "html_sha256"), ("layer_sha256", "layer_sha256"),
                          ("renderer", "renderer")):
        if doc.get(key) != pin.get(want_key):
            problems.add(2, name, f"{key} is {str(doc.get(key))[:16]!r}, the pinned sourceset "
                                  f"says {str(pin.get(want_key))[:16]!r}")
    if cfg.mode == LOCATE_INVENTORY:
        for key in ("universe", "universe_sha256"):
            if key in doc:
                problems.add(1, name, f"an inventory document carries no {key}")
    else:
        if doc.get("universe") != cfg.universe_id or doc.get("universe_sha256") != cfg.universe_sha:
            problems.add(5, name, f"universe {doc.get('universe')!r} @ "
                                  f"{str(doc.get('universe_sha256'))[:12]!r} is not the frozen "
                                  f"universe {cfg.universe_id} @ {cfg.universe_sha[:12]}")
    for path in _forbidden_keys(doc):
        problems.add(3, name, f"{path} is not a worker's to write (proof_status is joined from "
                              f"the index at export; the commit is the universe's)")
    return source_id


def attempts_counter(cfg: GateConfig, task_id: str) -> pathlib.Path:
    """``<tend-root>/attempts/<task-id>.json``: the bridge's counter of genuine rejections.

    The bridge keeps it under its own root, the tend root its wrappers export as
    ``CF_BRIDGE_ROOT`` (the directory above ``bin/``).  ``gate.json`` names that
    directory through ``orchestrator_db``, which ``plan.host_layout`` puts at
    ``<tend root>/orchestrator.sqlite`` in both layouts: ``runs/<run>/tend-root/``
    on this machine, where the gate directory lies inside it, and
    ``<root>/<run>-tend/`` on a run host, where the gate directory is its sibling
    ``<root>/<run>-gate/`` -- so the counter is never looked for from the gate
    directory (references/locate-documents.md sections 1 and 9).  A ``gate.json``
    with no ``orchestrator_db`` cannot say where the counter is: that is a gate
    the planner did not write, and a broken one (exit 3).
    """
    if cfg.orchestrator_db is None:
        raise GateBroken("gate.json names no orchestrator_db, so the tend root holding the "
                         "bridge's attempts/ counter is unknown; a by: attempt_cap document "
                         "cannot be checked")
    return cfg.orchestrator_db.parent / "attempts" / f"{task_id}.json"


def _counted_rejections(cfg: GateConfig, task_id: str) -> int:
    counter = attempts_counter(cfg, task_id)
    try:
        data = json.loads(counter.read_text(encoding="utf-8"))
        count = data.get("rejections") if isinstance(data, dict) else 0
        return count if isinstance(count, int) and not isinstance(count, bool) else 0
    except (OSError, ValueError):
        return 0


def _unresolved_block(doc: dict, name: str, problems: Problems, cfg: GateConfig, task_id: str,
                      *, check_counter: bool) -> bool:
    """Is the document unresolved?  Its block's form, and whose it is, are checked here.

    ``by: attempt_cap`` is the bridge's (``scripts/run/attempt_cap.py``), never a
    worker's: its reason is the text the bridge writes, at a count of at least
    :data:`LOCATE_ATTEMPT_CAP`, and with ``check_counter`` (tend's validation,
    which runs beside the counter) the task's counter is at the cap too.
    """
    block = doc.get("unresolved")
    if block is None:
        return False
    if not isinstance(block, dict) or not _nonempty_str(block.get("reason")) \
            or not _one_of(block.get("by"), UNRESOLVED_BY):
        problems.add(1, name, "unresolved must be {reason: <text>, by: worker|attempt_cap}")
        return True
    if block["by"] == UNRESOLVED_ATTEMPT_CAP:
        said = ATTEMPT_CAP_REASON.match(block["reason"])
        if said is None or int(said.group(1)) < LOCATE_ATTEMPT_CAP:
            problems.add(1, name, "unresolved.by attempt_cap is written by the bridge at the "
                                  "attempt cap, never by a worker: a worker that cannot finish "
                                  "writes by: worker with its reason")
        elif check_counter:
            counted = _counted_rejections(cfg, task_id)
            if counted < LOCATE_ATTEMPT_CAP:
                problems.add(1, name, f"unresolved.by attempt_cap, but task {task_id} has "
                                      f"{counted} counted rejection(s), under the cap of "
                                      f"{LOCATE_ATTEMPT_CAP}: a worker that cannot finish "
                                      f"writes by: worker with its reason")
    return True


def _check_location(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                    location: Any) -> None:
    if not isinstance(location, dict):
        problems.add(1, where, "a location is an object")
        return
    library = location.get("library")
    declaration = location.get("declaration")
    if not _one_of(library, cfg.libraries):
        problems.add(3, where, f"library {library!r} is not in universe {cfg.universe_id} "
                               f"({', '.join(sorted(cfg.libraries))})")
    elif not _nonempty_str(declaration):
        problems.add(1, where, "a location names a declaration")
    elif not ctx.exists(library, declaration):
        problems.add(3, where, f"{library}:{declaration} is not in corpus.sqlite at "
                               f"{cfg.libraries[library]['commit'][:12]} (a phantom declaration)")
    if not _one_of(location.get("relation"), LOCATE_RELATIONS):
        problems.add(1, where, f"relation {location.get('relation')!r} is not a locate relation")
    if location.get("relation") == CLOSE_MATCH and not _nonempty_str(location.get("note")):
        problems.add(6, where, "closeMatch needs a note naming the convention difference")
    rank = location.get("rank")
    if not (isinstance(rank, int) and not isinstance(rank, bool)
            and 1 <= rank <= cfg.max_locations):
        problems.add(6, where, f"rank {rank!r} is not 1..{cfg.max_locations}")
    if not _one_of(location.get("confidence"), CONFIDENCES):
        problems.add(1, where, f"confidence {location.get('confidence')!r} is not "
                               f"{'/'.join(sorted(CONFIDENCES))}")
    if not _nonempty_str(location.get("witness")):
        problems.add(1, where, "a location carries a witness")
    if "note" in location and not isinstance(location.get("note"), str):
        problems.add(1, where, "note is a string")


def _check_locations(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                     locations: Any) -> list[dict] | None:
    """The passage's locations, checked; None when there are more than the cap.

    Over the cap the passage is refused (rule 6) and nothing else of it is read:
    each location costs a check and a record, and a document within the byte cap
    can hold a million of them.
    """
    if not isinstance(locations, list):
        problems.add(1, where, "locations is a list")
        return []
    if len(locations) > cfg.max_locations:
        problems.add(6, where, f"{len(locations)} locations; at most {cfg.max_locations} per "
                               f"passage (none of them was checked)")
        return None
    ranks = [loc.get("rank") for loc in locations if isinstance(loc, dict)]
    if len(ranks) != len(set(map(repr, ranks))):
        problems.add(6, where, f"ranks {ranks} are not distinct")
    # one location per declaration: two would share one location_key, and the
    # challenge plan refuses a key it sees twice
    named = Counter(f"{loc.get('library')}:{loc.get('declaration')}" for loc in locations
                    if isinstance(loc, dict))
    repeated = sorted(n for n, count in named.items() if count > 1)
    if repeated:
        problems.add(6, where, f"{', '.join(repeated)} is located more than once; one location "
                               f"per declaration")
    for i, location in enumerate(locations):
        _check_location(cfg, ctx, problems, f"{where}.locations[{i}]", location)
    return [loc for loc in locations if isinstance(loc, dict)]


def _check_receipt(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                   receipt: Any) -> dict:
    if not isinstance(receipt, dict):
        problems.add(1, where, "receipt is {queries: [...], candidates: [...]}")
        return {"queries": [], "candidates": []}
    queries = receipt.get("queries")
    candidates = receipt.get("candidates")
    if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
        problems.add(1, where, "receipt.queries is a list of strings")
        queries = []
    if not isinstance(candidates, list):
        problems.add(1, where, "receipt.candidates is a list")
        candidates = []
    for i, cand in enumerate(candidates):
        at = f"{where}.receipt.candidates[{i}]"
        if not isinstance(cand, dict):
            problems.add(1, at, "a candidate is an object")
            continue
        if not _one_of(cand.get("verdict"), CANDIDATE_VERDICTS):
            problems.add(1, at, f"verdict {cand.get('verdict')!r} is not accepted/rejected")
        if cand.get("verdict") == "rejected" and not _nonempty_str(cand.get("reason")):
            problems.add(1, at, "a rejected candidate carries its reason")
        if not _nonempty_str(cand.get("declaration")) or not _nonempty_str(cand.get("library")):
            problems.add(1, at, "a candidate names its library and declaration")
        elif cand.get("verdict") == "accepted":
            if cand["library"] not in cfg.libraries:
                problems.add(3, at, f"library {cand['library']!r} is not in the universe")
            elif not ctx.exists(cand["library"], cand["declaration"]):
                problems.add(3, at, f"accepted candidate {cand['library']}:{cand['declaration']} "
                                    f"is not in corpus.sqlite (a phantom declaration)")
    return {"queries": queries, "candidates": [c for c in candidates if isinstance(c, dict)]}


def _rank_of(location: dict) -> int | None:
    rank = location.get("rank")
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else None


def _check_coverage(problems: Problems, where: str, coverage: Any, *, outcome: Any,
                    locations: list[dict], layer: str) -> None:
    """Rule 1: a passage's coverage claim (the maintainer's decision D1).

    ``{"complete": true, "parts": [<rank>, ...], "note": "<one sentence>"}`` on a
    LOCATED passage only: at least two distinct parts, each the rank of one of the
    passage's ``hasPart`` locations, and a note in the worker's own words -- at
    most ``NOTES_MAX`` characters, and no more of the source verbatim than the
    export's quote cap (``locate_records.verbatim_run``).  A claim upgrades the
    passage's derived status once a challenger confirms it, so a malformed one is
    refused here, while the worker can still fix it.
    """
    at = f"{where}: coverage"
    if outcome != "located":
        problems.add(1, at, f"a coverage claim belongs to a located passage; this one is "
                            f"{outcome}")
        return
    if not isinstance(coverage, dict):
        problems.add(1, at, "coverage is {complete: true, parts: [<ranks>], note: <one "
                            "sentence>}")
        return
    extra = sorted(set(coverage) - COVERAGE_KEYS)
    if extra:
        problems.add(1, at, f"carries {', '.join(map(repr, extra))}; a claim is complete, "
                            f"parts and note, nothing else")
    if coverage.get("complete") is not True:
        problems.add(1, at, "complete is true: a passage claims coverage only when its parts "
                            "together state every clause")
    parts = coverage.get("parts")
    if not isinstance(parts, list) or not all(isinstance(r, int) and not isinstance(r, bool)
                                              for r in parts):
        problems.add(1, at, "parts is a list of the ranks of this passage's hasPart locations")
    else:
        if len(set(parts)) != len(parts):
            problems.add(1, at, f"parts {parts} names a rank twice")
        if len(set(parts)) < MIN_COVERAGE_PARTS:
            problems.add(1, at, f"a coverage claim names at least {MIN_COVERAGE_PARTS} parts "
                                f"(one location that states the whole passage is an "
                                f"exactMatch, not a hasPart)")
        by_rank = {_rank_of(loc): loc for loc in locations if _rank_of(loc) is not None}
        for rank in sorted(set(parts)):
            loc = by_rank.get(rank)
            if loc is None:
                problems.add(1, at, f"part {rank} is the rank of no location of this passage")
            elif loc.get("relation") != HAS_PART:
                problems.add(1, at, f"part {rank} is a {loc.get('relation')} location; a "
                                    f"claim's parts are its hasPart locations")
    note = coverage.get("note")
    if not _nonempty_str(note):
        problems.add(1, at, "note says, in one sentence of your own, how the parts together "
                            "state every clause")
    elif len(note) > NOTES_MAX:
        problems.add(1, at, f"note is {len(note)} characters; at most {NOTES_MAX}")
    else:
        run = lr.verbatim_run(note, layer)
        if run is not None:
            problems.add(1, at, f"note carries {len(run.split())}+ words of the source verbatim "
                                f"({run[:40]!r}...), past the {lr.QUOTE_WORDS}-word quote cap: "
                                f"say it in your own words")


# --------------------------------------------------------------------------- #
# rule 9: the judgment rules (references/vocabulary.md section 1.11, R1-R7)
# --------------------------------------------------------------------------- #

def fold_words(text: Any) -> str:
    """``text`` as the phrase lists read it (cflib.vocab.fold_words, inlined)."""
    folded = str(text or "").replace("’", "'").casefold()
    folded = re.sub(r"(?<![\w'])can't(?![\w'])", "can not", folded)
    folded = re.sub(r"(?<![\w'])won't(?![\w'])", "will not", folded)
    folded = re.sub(r"(?<![\w'])cannot(?![\w'])", "can not", folded)
    folded = re.sub(r"(?<=\w)n't(?![\w'])", " not", folded)
    return " ".join(folded.split())


def phrase_pattern(phrase: str) -> re.Pattern:
    """A phrase of a list as a pattern (cflib.vocab.phrase_pattern, inlined)."""
    wild = phrase.endswith("*")
    body = r"\s".join(re.escape(word) for word in phrase.rstrip("*").split())
    return re.compile(rf"(?<![\w']){body}" + (r"[\w']*" if wild else r"(?![\w'])"))


def _phrase_in(text: Any, phrases: Iterable[str]) -> str | None:
    """The first of ``phrases`` in ``text`` (cflib.vocab._phrase_in, inlined)."""
    folded = fold_words(text)
    for phrase in phrases:
        if phrase_pattern(phrase).search(folded):
            return phrase
    return None


_CLAUSE_END = re.compile(r"[.;:!?,()\[\]]")
_PASSAGE_WORD = re.compile(r"(?<![\w'])(?:passage|article)(?![\w'])")
_PASSAGE_AFTER = re.compile(r"\s*(?:by |in |of |from |for )?(?:the |this )(?:passage|article)"
                            r"(?![\w'])")
_NEGATED = re.compile(r"(?<![\w'])not\s*\Z")
_RELATIVE = re.compile(r"\s*(?:which|that|whose|where)(?![\w'])")


def _gap_exempt(folded: str, start: int, end: int, phrase: str) -> bool:
    """A gap phrase's hit that admits no gap of the declaration's (cflib.vocab's)."""
    before, after = folded[:start], folded[end:]
    if phrase.startswith("only") and _NEGATED.search(before):
        return True
    cuts = [m.end() for m in _CLAUSE_END.finditer(before)]
    clause_start = cuts[-1] if cuts else 0
    clause = before[clause_start:]
    stop = _CLAUSE_END.search(after)
    if _PASSAGE_WORD.search(clause) or _PASSAGE_AFTER.match(after[:stop.start() if stop
                                                                    else len(after)]):
        return True
    if len(cuts) >= 1 and _RELATIVE.match(clause):
        previous = before[cuts[-2] if len(cuts) >= 2 else 0:clause_start - 1]
        if previous.strip().startswith("unlike"):
            return True
    return False


def admits_missing_clause(note: Any) -> str | None:
    """The ``CLAUSE_GAP_PHRASES`` phrase by which a note admits a clause it does not state."""
    folded = fold_words(note)
    for phrase in CLAUSE_GAP_PHRASES:
        for hit in phrase_pattern(phrase).finditer(folded):
            if not _gap_exempt(folded, hit.start(), hit.end(), phrase):
                return phrase
    return None


def says_generality(note: Any, phrases: Iterable[str] = CLOSE_MATCH_GENERALITY_PHRASES
                    ) -> str | None:
    """The ``CLOSE_MATCH_GENERALITY_PHRASES`` phrase an exact or close note says.

    ``phrases`` is the list of the run's judgment rules: under rules 3 the longer
    ``CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT`` (:func:`generality_phrases`).
    """
    return _phrase_in(note, phrases)


def generality_phrases(rules: int) -> tuple[str, ...]:
    """The generality words an exact or close note may not say, under judgment rules ``rules``."""
    return (CLOSE_MATCH_GENERALITY_PHRASES_STATEMENT_CUT if rules >= RULES_STATEMENT_CUT
            else CLOSE_MATCH_GENERALITY_PHRASES)


def rejects_a_part(reason: Any) -> str | None:
    """The ``CANDIDATE_PART_PHRASES`` phrase a candidate's rejection says (cflib.vocab's)."""
    return _phrase_in(reason, CANDIDATE_PART_PHRASES)


def strip_format(text: str) -> str:
    """``text`` without its format characters (Unicode category Cf: U+200B, U+00AD, ...)."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def own_words_run(text: str, source: Any, *, raw: bool = True) -> str | None:
    """The source verbatim past the export's cap in ``text``, in any spelling a worker reads.

    The layer as it is (``raw``; what rule 1 holds every exported free text to
    already, through ``locate_records``), the layer without format characters,
    and the reading copy's spelling of it (:meth:`SourceView.views`), each with the
    format characters dropped from ``text`` too: a zero-width space after every
    word, or a sentence copied from ``text.txt`` with its formula as ``$x$``, is
    still the source's words.  None when ``source`` is None.
    """
    if source is None:
        return None
    run = lr.verbatim_run(text, source.layer)
    if run is not None:
        return run if raw else None           # not raw: rule 1's own check names it
    bare = strip_format(str(text or ""))
    for view in source.views():
        run = lr.verbatim_run(bare, view)
        if run is not None:
            return run
    return None


def _verbatim(problems: Problems, where: str, what: str, text: str, source: Any, *,
              raw: bool = True) -> bool:
    """Rule 1's cap on a worker's free text, in every spelling (:func:`own_words_run`)."""
    run = own_words_run(text, source, raw=raw)
    if run is not None:
        spelled = "" if raw and lr.verbatim_run(text, source.layer) else (
            ", as the reading copy or the layer spells it")
        problems.add(1, where, f"{what} carries {len(run.split())}+ words of the source verbatim "
                               f"({run[:40]!r}...){spelled}, past the {lr.QUOTE_WORDS}-word "
                               f"quote cap: say it in your own words")
        return True
    return False


def _blank(value: Any) -> bool:
    """None, or a string of whitespace: a field that says nothing (and no wrong shape)."""
    return value is None or (isinstance(value, str) and not value.strip())


def _one_line(value: str) -> bool:
    return LINE_BREAKS.search(value) is None


def _words_with_letters(value: str) -> int:
    return sum(1 for word in value.split() if any(ch.isalpha() for ch in word))


def check_line_field(problems: Problems, where: str, field: str, value: Any,
                     source: Any) -> bool:
    """A judgment rule's one-line field wherever a location or verdict carries it.

    Absent (None) or blank says nothing (False).  Otherwise it is a string of one
    line (no line break of any kind), at most ``MAX_JUDGMENT_LINE_CHARS``
    characters and at least ``MIN_JUDGMENT_LINE_WORDS`` words with a letter in
    them ("n/a" or "-" is no line), in the worker's own words (rule 1's cap, in
    every spelling).  True when it carries a usable line.
    """
    if _blank(value):
        return False
    if not isinstance(value, str):
        problems.add(9, where, f"{field} is one line of text, not a {type(value).__name__}")
        return False
    if not _one_line(value) or len(value) > MAX_JUDGMENT_LINE_CHARS:
        problems.add(9, where, f"{field} is one line of at most {MAX_JUDGMENT_LINE_CHARS} "
                               f"characters")
        return False
    if _words_with_letters(value) < MIN_JUDGMENT_LINE_WORDS:
        problems.add(9, where, f"{field} {value!r} says nothing: at least "
                               f"{MIN_JUDGMENT_LINE_WORDS} words of your own")
        return False
    return not _verbatim(problems, where, f"the {field}", value, source)


def check_free_text(problems: Problems, where: str, texts: Iterable[tuple[str, Any]],
                    source: Any) -> None:
    """Rule 1's cap, under the judgment rules, on the free texts the export publishes.

    A location's note and an absence's queries and reasons are held to the layer
    already (``locate_records.check_note``, ``receipt_document``); this holds them
    to the layer's other spellings too (:func:`own_words_run` without the raw
    layer, which names its own refusal), for a run planned under the rules only:
    a run planned before them exports exactly what it did.
    """
    for what, text in texts:
        if isinstance(text, str) and text.strip():
            _verbatim(problems, where, what, text, source, raw=False)


def check_generality(problems: Problems, where: str, relation: Any, note: Any, *,
                     rules: int = 1, label: str | None = None) -> None:
    """Rule 9 (R1-R3, FM4): an exactMatch or closeMatch note names no generality.

    The words are those of the run's judgment rules (:func:`generality_phrases`):
    rules 3 add the smoke-3 audit's four (fix 2c).  ``label`` names what is held
    when it is not the location's own relation: a bundle part, held by its
    ``conjunct_relation`` (:func:`held_label`).
    """
    if not _one_of(relation, (EXACT_MATCH, CLOSE_MATCH)):
        return
    word = says_generality(note, generality_phrases(rules))
    if word:
        subject = f"the {relation} note" if label is None else f"the note of a {label}"
        problems.add(9, where, f"{subject} says {word!r}: an exactMatch differs from "
                               f"its passage by nothing and a closeMatch by a convention only "
                               f"(R1); a declaration stronger or more general is broadMatch "
                               f"(R2), a special case narrowMatch (R3), a weakening hasPart or "
                               f"relatedMatch (R5). If the words name the mathematics instead "
                               f"(a strict inequality, an increasing list), reword the note")


def check_convention(problems: Problems, where: str, relation: Any, convention: Any, *,
                     rules: int, kept: bool = False) -> None:
    """Rule 9 (R1, FM4): a closeMatch names its convention; nothing else names one.

    The list is the judgment rules' (``rules``, the gate.json's version): from
    version 2 a junk value is none (PD7), and the refusal says what the
    declaration is instead.  ``kept`` is a challenger's confirm that keeps the
    record's own convention, by naming none or naming it again: a record written
    under version 1 may carry ``junk_value``, and a challenge, which never raises
    a relation, cannot make it the ``exactMatch`` PD7 would, so that convention
    stands.  A confirm that keeps nothing -- the record names no convention, or
    the verdict names another -- is held to its rules' own list.
    """
    given = not _blank(convention)
    if relation == CLOSE_MATCH:
        listed = (CLOSE_MATCH_CONVENTIONS if rules >= RULES_PD5_PD9 and not kept
                  else LEGACY_CLOSE_MATCH_CONVENTIONS)
        if convention == CONVENTION_JUNK_VALUE and convention not in listed:
            problems.add(9, where, "a junk value is no convention (PD7): a declaration that "
                                   "agrees with the passage on the passage's domain is "
                                   "exactMatch when every case outside it is trivial (a junk "
                                   "or default value, x/0 = 0; an extra case that reduces to "
                                   "a tautology), with a note that says why, and broadMatch "
                                   "(R2) when some case outside it has real mathematical "
                                   "content; closeMatch is for a difference where both sides "
                                   "are defined")
        elif not _one_of(convention, listed):
            problems.add(9, where, f"a closeMatch names the convention it differs by, one of "
                                   f"{', '.join(sorted(listed))} (convention: "
                                   f"<one>); a difference that is none of them is no closeMatch "
                                   f"(R1)")
    elif given:
        problems.add(9, where, f"convention belongs to a closeMatch; a {relation} differs from "
                               f"its passage by no convention: drop it, or the relation is "
                               f"closeMatch")


def check_step(problems: Problems, where: str, relation: Any, step: Any, source: Any, *,
               passage_kind: Any, required: bool = True) -> None:
    """Rule 9 (R5, FM3): a hasPart or narrowMatch says what the declaration alone entails.

    A lemma the proof uses, one that reaches the clause only with another
    declaration, a neighbouring passage's fact: none states a part of the passage,
    and each is ``relatedMatch`` (R5).  A ``hasPart`` or ``narrowMatch`` names, in
    ``step``, what the declaration alone entails of this passage -- on a proof the
    displayed step, on any other passage the clause, as the declaration states it.
    Any other relation carries no step.  ``required`` False (a confirm of a record
    that names one) checks a given step's form only.
    """
    given = check_line_field(problems, where, "step", step, source)
    if relation in STEP_RELATIONS:
        if required and not given and not _nonempty_str(step):
            what = ("the displayed step it states (the equation, the case, the inequality)"
                    if passage_kind == PASSAGE_OTHER else
                    "the clause it states, as the declaration states it")
            problems.add(9, where, f"a {relation} names, in step, what the declaration alone "
                                   f"entails of this passage: {what} (step: <one line>); a "
                                   f"lemma the argument only uses, or one that needs another "
                                   f"declaration to reach the clause, is relatedMatch (R5)")
    elif not _blank(step):
        problems.add(9, where, f"step belongs to a hasPart or narrowMatch; a {relation} states "
                               f"the whole passage or none of it: drop it")


def check_claims(problems: Problems, where: str, claims: Any, source: Any, *,
                 required: bool) -> list[str] | None:
    """Rule 9: a passage's claim list, ``[{id, text}, ...]``; returns its ids, or None.

    The worker writes the passage's formal clauses before it judges a declaration
    (rule R4): ``id`` is ``c1``, ``c2``, ... (``CLAIM_ID``), distinct; ``text`` is
    one line (no line break of any kind) of at most ``MAX_CLAIM_CHARS`` characters
    in the worker's own words -- rule 1's cap in every spelling of the source, on
    each claim and on the claims read one after the other, so a sentence split
    over two claims is the source verbatim still; at most ``MAX_PASSAGE_CLAIMS``
    claims.  None when the list is absent (refused when ``required``) or unusable.
    """
    if claims is None:
        if required:
            problems.add(9, where, "write the passage's claim list first: claims: [{id: c1, "
                                   "text: <one clause in your own words>}, ...], every formal "
                                   "clause the passage asserts")
        return None
    if not isinstance(claims, list) or not claims:
        problems.add(9, where, "claims is a non-empty list of {id, text}")
        return None
    if len(claims) > MAX_PASSAGE_CLAIMS:
        problems.add(9, where, f"{len(claims)} claims; at most {MAX_PASSAGE_CLAIMS} (one per "
                               f"formal clause)")
        return None
    ids: list[str] = []
    texts: list[str] = []
    usable, copied = True, False
    for i, claim in enumerate(claims):
        at = f"{where}.claims[{i}]"
        if not isinstance(claim, dict) or set(claim) != CLAIM_KEYS:
            problems.add(9, at, "a claim is {id, text} and nothing else")
            usable = False
            continue
        cid, text = claim["id"], claim["text"]
        if not isinstance(cid, str) or not CLAIM_ID.match(cid):
            problems.add(9, at, f"id {cid!r} is not c1, c2, ...")
            usable = False
            continue
        if cid in ids:
            problems.add(9, at, f"id {cid} is given twice")
            usable = False
            continue
        ids.append(cid)
        if not _nonempty_str(text):
            problems.add(9, at, "text says the clause, in a short paraphrase")
        elif not _one_line(text) or len(text) > MAX_CLAIM_CHARS:
            problems.add(9, at, f"text is one line of at most {MAX_CLAIM_CHARS} characters")
        else:
            texts.append(text)
            copied = _verbatim(problems, at, "the claim's text", text, source) or copied
    if len(texts) > 1 and not copied:
        _verbatim(problems, where, "the claims, read one after the other,", " ".join(texts),
                  source)
    return ids if usable else None


def check_clauses(problems: Problems, where: str, relation: Any, clauses: Any,
                  ids: list[str] | None) -> None:
    """Rule 9 (R4, R5): the claim ids a record states, against the passage's claim list.

    A relation that counts names at least one id of the passage's claims; an
    ``exactMatch``, ``closeMatch`` or ``broadMatch`` names every one, since it says
    the declaration states the whole passage; a ``relatedMatch`` names none,
    since it states something else.  ``ids`` None (no usable claim list, refused
    already) checks the list's shape alone.
    """
    if not _one_of(relation, LOCATE_RELATIONS):
        return
    if relation == RELATED_MATCH:
        if clauses not in (None, []):
            problems.add(9, where, "a relatedMatch states no clause of the passage: a "
                                   "declaration that states one, alone, is hasPart or "
                                   "narrowMatch (R4, R5)")
        return
    if clauses is None:
        problems.add(9, where, f"a {relation} names the ids of the claims it states "
                               f"(clauses: [c1, ...])")
        return
    if not isinstance(clauses, list) or not clauses or not all(
            isinstance(c, str) for c in clauses) or len(set(clauses)) != len(clauses):
        problems.add(9, where, "clauses is a non-empty list of distinct claim ids")
        return
    if ids is None:
        return
    unknown = [c for c in clauses if c not in ids]
    if unknown:
        problems.add(9, where, f"clauses {', '.join(unknown)} are not ids of the passage's "
                               f"claims ({', '.join(ids)})")
        return
    if relation in STRONG_RELATIONS:
        missing = [c for c in ids if c not in clauses]
        if missing:
            problems.add(9, where, f"a {relation} states every clause of the passage, and this "
                                   f"one does not state {', '.join(missing)}: a declaration "
                                   f"that states some clauses is hasPart, with a coverage claim "
                                   f"when the parts together state them all (R4)")


def _scan_group(text: str, start: int) -> int:
    """The index of the bracket closing the group opened at ``start`` (any depth pair), or -1."""
    want: list[str] = []
    for k in range(start, len(text)):
        ch = text[k]
        if ch in DEPTH_BRACKETS:
            want.append(DEPTH_BRACKETS[ch])
        elif want and ch == want[-1]:
            want.pop()
            if not want:
                return k
        elif ch in DEPTH_BRACKETS.values():
            return -1
    return -1


def _skip_space(text: str, at: int) -> int:
    while at < len(text) and text[at].isspace():
        at += 1
    return at


def _next_arrow(text: str, at: int) -> tuple[int, int] | None:
    """The next ``→`` (or ``->``) outside every bracket, as (index, length).

    None when the text ends first, or when a ``,`` outside every bracket comes
    first: the rest is the body of a binder (``∃ x, …``, ``∑ i ∈ s, …``), whose
    arrows are no binder position of the declaration.
    """
    k = at
    while k < len(text):
        ch = text[k]
        if ch in DEPTH_BRACKETS:
            end = _scan_group(text, k)
            if end < 0:
                return None
            k = end + 1
            continue
        if ch == ",":
            return None
        if ch == "→":
            return k, 1
        if text.startswith("->", k):
            return k, 2
        k += 1
    return None


def binder_groups(signature: Any) -> list[str]:
    """Every binder group at a binder position of a signature, as the library index prints it.

    The type is read as a telescope: ``∀ <binders>, <rest>`` gives the bracketed
    groups of its binder list (``∀ {G : Type u} [inst : Group G] (p : ℕ), …``,
    ``∀ ⦃a b : α⦄ …``), and ``<segment> → <rest>`` gives the segment when it is
    one balanced group (``{R : Type u} → [inst : CommRing R] → …``); a segment
    that is no group -- an anonymous explicit argument (``Set M →``), a
    non-dependent hypothesis (``p.Prime →``) -- is passed over, and the scan goes
    on, so a binder after one (``p.Prime → ∀ (W : Type) [inst : Foo W], …``) is
    found.  It stops at the conclusion: a segment with no arrow after it, or a
    ``,`` outside every bracket before the next arrow (an ``∃``'s body).  A bracket
    in a statement (``a ≡ b [MOD n]``) is at no binder position.
    """
    return _telescope(signature)[0]


def _telescope(signature: Any) -> tuple[list[str], str]:
    """A signature's binder groups (:func:`binder_groups`) and its conclusion.

    The conclusion is what is left where the scan stops: the segment after the last
    binder and the last hypothesis's arrow (``f = n.factorization ↔ …`` of ``∀ {n : ℕ}
    {f : ℕ →₀ ℕ}, n ≠ 0 → (f = n.factorization ↔ …)``), with an ``∃``'s body in it;
    empty when the text ends inside the binders or holds an unbalanced bracket.
    """
    text = str(signature or "")
    groups: list[str] = []
    i = _skip_space(text, 0)
    for _ in range(10_000):                     # bounded: each turn consumes text
        if i >= len(text):
            break
        if text.startswith("∀", i):
            i = _skip_space(text, i + 1)
            while i < len(text):
                if text[i] in BINDER_BRACKETS:
                    end = _scan_group(text, i)
                    if end < 0:
                        return groups, ""
                    groups.append(text[i:end + 1])
                    i = _skip_space(text, end + 1)
                elif text[i] == ",":
                    i = _skip_space(text, i + 1)
                    break
                elif text[i] in DEPTH_BRACKETS:
                    end = _scan_group(text, i)
                    if end < 0:
                        return groups, ""
                    i = _skip_space(text, end + 1)
                else:
                    i += 1                      # a bare binder name, or ∀ x ∈ s,
            else:
                return groups, ""
            continue
        arrow = _next_arrow(text, i)
        if arrow is None:
            break
        segment = text[i:arrow[0]].strip()
        if segment and segment[0] in BINDER_BRACKETS and _scan_group(segment, 0) == len(segment) - 1:
            groups.append(segment)
        i = _skip_space(text, arrow[0] + arrow[1])
    return groups, text[i:].strip()


def conclusion(signature: Any) -> str:
    """A signature's conclusion as the index prints it (:func:`_telescope`), or ''."""
    return _telescope(signature)[1]


def pd6_mark(cfg: GateConfig, ctx: Context, location: dict) -> str | None:
    """Judgment rules 4: the mark of PD6's shape tripwire in a declaration's conclusion.

    The first of ``PD6_CONCLUSION_MARKS`` (an iff, any ``≃`` equivalence, a unique
    existence, a bijection) in the conclusion of the signature ``corpus.sqlite``
    records for the location's declaration, or None -- also when it records none.
    """
    library, name = location.get("library"), location.get("declaration")
    if not (isinstance(library, str) and isinstance(name, str) and library in cfg.libraries):
        return None
    tail = conclusion(ctx.signature(library, name))
    return next((mark for mark in PD6_CONCLUSION_MARKS if mark in tail), None)


def instance_binders(signature: Any) -> list[str]:
    """The instance-implicit binders among a signature's binder groups: ``[…]`` groups."""
    return [group for group in binder_groups(signature) if group.startswith("[")]


def _binders_needing(cfg: GateConfig, ctx: Context, location: dict) -> list[str]:
    """The instance binders of the declaration a location or record names, at the pin."""
    library, name = location.get("library"), location.get("declaration")
    if not (isinstance(library, str) and isinstance(name, str) and library in cfg.libraries):
        return []
    return instance_binders(ctx.signature(library, name))


def _instantiation_problem(location: dict, binders: list[str], *, challenger: bool) -> str:
    who = ("the record names no instantiation: instantiate it yourself at the passage's "
           "objects before you keep a relation that counts"
           if challenger else
           "say at which of the passage's objects it applies and which instance or hypothesis "
           "makes it apply")
    return (f"{location.get('library')}:{location.get('declaration')} takes instance "
            f"argument(s) {' '.join(binders[:3])}{' …' if len(binders) > 3 else ''}: {who} "
            f"(instantiation: <one line>). A setting that neither contains nor is contained in "
            f"the passage's is relatedMatch (R5); a special case of it narrowMatch (R3)")


def _gap_problem(phrase: str, relation: str, field: str) -> str:
    return (f"the {field} says {phrase!r}, which admits a clause the declaration does not "
            f"state: then the relation is hasPart (R4), not {relation}. If the words are about "
            f"the passage, a convention or a case outside the passage's domain instead, say "
            f"what the declaration does (\"the map is bundled with its inverse\"; \"Lean also "
            f"returns 0 at n = 0, which the passage leaves undefined\")")


def check_judgment(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                   location: dict, *, ids: list[str] | None, passage_kind: Any,
                   source: Any, row: dict | None = None) -> None:
    """Rule 9 over one location a document asserts: what it states of its passage.

    Its ``clauses`` (R4); a strong one's note and instantiation admit no gap; an
    exact or close note names no generality (R1-R3, FM4); a ``closeMatch`` names
    its ``convention``, and no other relation carries one; a ``hasPart`` or
    ``narrowMatch`` names its ``step``, what the declaration alone entails of the
    passage, and no other relation carries one (FM3); every one-line field is
    checked wherever it appears; and, when the declaration's signature in
    ``corpus.sqlite`` (``decls.signature``, the probe's printed type) takes an
    instance argument at a binder position (:func:`binder_groups`), a counting
    location carries the ``instantiation`` that says at which of the passage's
    objects, and by which instance or hypothesis, the declaration applies (FM6).
    A declaration with no recorded signature is not held to the last: the gate
    cannot see its binders.

    Under judgment rules 3 (the statement cut; ``row`` is the frozen passage, its
    statement and conjuncts) a ``hasPart`` of a compound statement gives its
    ``conjunct_relation`` and nothing else does (J2); a strong location writes
    both instantiation ``tests`` and an ``instantiation`` whatever its binders,
    and states no naturals for the passage's integers unanswered (fixes 2a, 2b);
    and an exact or close note is held to the longer list of generality words
    (fix 2c).  A part is held by its ``conjunct_relation`` (:func:`held_label`):
    its convention, its generality words and fixes 2a-2b are a strong location's
    when that relation is strong, since a bundle takes its weakest part's
    relation (status rule 2).  The gap check stays the location's own: a part
    states some conjuncts and not the rest by definition.

    Under judgment rules 4 a part's ``conjunct_relation`` may be ``hasPart``
    (:func:`conjunct_relations`), and a ``pd6`` line -- a bundle part's answer to
    PD6's shape tripwire (:func:`check_pd6_shape`) -- belongs to a ``hasPart`` only.
    """
    relation = location.get("relation")
    known = relation if _one_of(relation, LOCATE_RELATIONS) else None
    check_clauses(problems, where, known, location.get("clauses"), ids)
    instantiation = location.get("instantiation")
    given = check_line_field(problems, where, "instantiation", instantiation, source)
    if known is None:
        return
    cut = cfg.judgment_rules >= RULES_STATEMENT_CUT
    check_step(problems, where, known, location.get("step"), source, passage_kind=passage_kind)
    held_to = (check_conjunct_relation(problems, where, known, location.get("conjunct_relation"),
                                       row, rules=cfg.judgment_rules) if cut else known)
    if cfg.judgment_rules >= RULES_SMOKE4 and location.get("pd6") is not None:
        if known != HAS_PART:
            problems.add(9, where, "pd6 belongs to a hasPart, a part of a bundle: what the part "
                                   "does not state of the passage on its own; drop it")
        else:
            check_line_field(problems, where, "pd6", location.get("pd6"), source)
    label = held_label(known, held_to)
    check_convention(problems, where, held_to, location.get("convention"),
                     rules=cfg.judgment_rules)
    check_generality(problems, where, held_to, location.get("note"), rules=cfg.judgment_rules,
                     label=label)
    if known in STRONG_RELATIONS:
        for field in ("note", "instantiation"):
            phrase = admits_missing_clause(location.get(field))
            if phrase:
                problems.add(9, where, _gap_problem(phrase, known, field))
                break
    if cut and held_to in STRONG_RELATIONS:
        check_strong_location(cfg, ctx, problems, where, location, held_to, row, source,
                              label=label)
        return
    if cut and location.get("tests") is not None:
        check_tests(problems, where, location.get("tests"), source)
    if known != RELATED_MATCH and not given and _blank(instantiation):
        binders = _binders_needing(cfg, ctx, location)
        if binders:
            problems.add(9, where, _instantiation_problem(location, binders, challenger=False))


def _normal_query(query: str) -> str:
    return " ".join(query.split()).casefold()


def _match_tags(tags_of: dict[str, set[str]], needed: list[str]) -> list[str]:
    """The ``needed`` tags left without a query of their own in a maximum matching.

    ``tags_of`` maps each distinct query to the tags it carries; a query serves one
    tag.  Augmenting paths (Kuhn's algorithm) over at most a dozen tags.
    """
    owner: dict[str, str] = {}

    def assign(tag: str, seen: set[str]) -> bool:
        for query, tags in tags_of.items():
            if tag in tags and query not in seen:
                seen.add(query)
                if query not in owner or assign(owner[query], seen):
                    owner[query] = tag
                    return True
        return False

    return [tag for tag in needed if not assign(tag, set())]


def check_query_clauses(problems: Problems, where: str, receipt: Any, ids: list[str] | None, *,
                        absent: bool, passage_kind: Any, what: str = "receipt") -> None:
    """Rule 9 (FM5): which claims each query of a receipt targets.

    ``<what>.query_clauses`` maps queries of ``<what>.queries`` (exactly as
    written) to the claim ids each targets, or ``general`` for the general theorem
    a worked example instantiates.  An absence carries it and tags every query it
    lists, and each claim -- with ``general`` for an ``example`` passage -- has a
    distinct query of its own among the tags (a matching of distinct queries to
    claims; queries that differ in case or spacing are one): an absence of a
    clause nobody searched for is no absence, and one query tagged with every
    claim searched for one.  On a located passage it is optional, and checked
    when given.
    """
    raw = receipt.get("query_clauses") if isinstance(receipt, dict) else None
    if raw is None:
        if absent:
            problems.add(9, where, f"an absence tags each query with the claims it targets: "
                                   f"{what}.query_clauses: {{<query>: [c1, ...]}}, every query "
                                   f"tagged and every claim searched for")
        return
    if not isinstance(raw, dict):
        problems.add(9, where, f"{what}.query_clauses is {{<query>: [<claim id>, ...]}}")
        return
    queries = receipt.get("queries") if isinstance(receipt.get("queries"), list) else []
    queries = [q for q in queries if isinstance(q, str)]
    tags = set(ids or []) | {GENERAL_QUERY_TAG}
    tags_of: dict[str, set[str]] = {}
    for query, targets in sorted(raw.items()):
        at = f"{where}.{what}.query_clauses[{query[:40]!r}]"
        if query not in queries:
            problems.add(9, at, f"is not one of {what}.queries, as written")
            continue
        if not isinstance(targets, list) or not targets or not all(
                isinstance(t, str) for t in targets) or len(set(targets)) != len(targets):
            problems.add(9, at, "is a non-empty list of distinct claim ids (or general)")
            continue
        if ids is not None:
            unknown = [t for t in targets if t not in tags]
            if unknown:
                problems.add(9, at, f"{', '.join(unknown)} are neither claim ids of the passage "
                                    f"({', '.join(ids)}) nor {GENERAL_QUERY_TAG}")
                continue
        tags_of.setdefault(_normal_query(query), set()).update(targets)
    if not absent or ids is None:
        return
    untagged = sorted({q for q in queries if q.strip() and q not in raw})
    if untagged:
        problems.add(9, where, f"an absence tags every query it lists, and "
                               f"{', '.join(repr(q[:40]) for q in untagged[:3])}"
                               f"{' …' if len(untagged) > 3 else ''} carries no tag: tag each "
                               f"with the claims it targets")
    targeted = set().union(*tags_of.values()) if tags_of else set()
    unsearched = [c for c in ids if c not in targeted]
    if unsearched:
        problems.add(9, where, f"an absence searched for every claim, and no query targets "
                               f"{', '.join(unsearched)}: run one for each, and tag it")
    example = passage_kind == PASSAGE_EXAMPLE
    if example and GENERAL_QUERY_TAG not in targeted:
        problems.add(9, where, f"a worked example's absence also searched for the general "
                               f"theorem it is an instance of: a query tagged "
                               f"{GENERAL_QUERY_TAG}")
    if unsearched or (example and GENERAL_QUERY_TAG not in targeted):
        return
    needed = list(ids) + ([GENERAL_QUERY_TAG] if example else [])
    left = _match_tags(tags_of, needed)
    if left:
        problems.add(9, where, f"each claim{' and the general theorem' if example else ''} "
                               f"has a query of its own, and {len(tags_of)} distinct tagged "
                               f"quer{'y' if len(tags_of) == 1 else 'ies'} cannot give one to "
                               f"each of {len(needed)}: {', '.join(left)} share a query with "
                               f"another; run a query for each, and tag it with what it targets")


#: what a part that alone states every clause is instead (PD6), in both refusals
WHOLE_PART = ("a part that alone states every clause is no part (PD6): it is one location "
              "with its own relation (exactMatch, closeMatch or broadMatch by R1-R2, "
              "narrowMatch by R3)")


def check_coverage_clauses(problems: Problems, where: str, coverage: dict,
                           locations: list[dict], ids: list[str] | None, *,
                           rules: int) -> None:
    """Rule 9: a coverage claim's parts together state every clause of the passage.

    Under judgment rules 2 (PD6) no part names every claim alone: a part that
    does is one location with its own relation, and the claim stands only on
    parts that each state some of the clauses.
    """
    if ids is None or not isinstance(coverage.get("parts"), list):
        return
    by_rank = {_rank_of(loc): loc for loc in locations if _rank_of(loc) is not None}
    stated: set[str] = set()
    whole: list[int] = []
    for rank in coverage["parts"]:
        clauses = (by_rank.get(rank) or {}).get("clauses") if isinstance(rank, int) else None
        if isinstance(clauses, list):
            named = {c for c in clauses if isinstance(c, str)}
            stated.update(named)
            if ids and set(ids) <= named and rank not in whole:
                whole.append(rank)
    missing = [c for c in ids if c not in stated]
    if missing:
        problems.add(9, f"{where}: coverage", f"no part states {', '.join(missing)}: a claim's "
                                              f"parts together state every clause of the "
                                              f"passage (R4)")
    if whole and rules >= RULES_PD5_PD9:
        problems.add(9, f"{where}: coverage",
                     f"part{'s' if len(whole) > 1 else ''} "
                     f"{', '.join(f'rank {r}' for r in whole)} name{'' if len(whole) > 1 else 's'} "
                     f"every claim ({', '.join(ids)}): {WHOLE_PART}; give "
                     f"{'them their' if len(whole) > 1 else 'it its'} relation, and claim "
                     f"coverage only over parts that each state some of the clauses, or none")


def check_rejections(problems: Problems, where: str, receipt: dict) -> None:
    """Rule 9 (FM5): an absence rejects no candidate for stating a part of the passage.

    A declaration that states one clause, a special case or a generalisation of
    the passage is a ``hasPart``, ``narrowMatch`` or ``broadMatch`` location (R2-R4),
    so a rejection whose reason says so (:func:`rejects_a_part`) makes the absence
    false: two of the smoke-2 audit's three false supporting absences rejected the
    stating declaration as "not the worked example" or "not the whole passage".
    """
    for i, cand in enumerate(receipt.get("candidates") or []):
        if cand.get("verdict") != "rejected":
            continue
        phrase = rejects_a_part(cand.get("reason"))
        if phrase:
            problems.add(9, f"{where}.receipt.candidates[{i}]",
                         f"{cand.get('library')}:{cand.get('declaration')} is rejected as "
                         f"{phrase!r}: a declaration that states a clause of the passage, a "
                         f"special case of it or a generalisation of it is a hasPart, "
                         f"narrowMatch or broadMatch location (R2-R4), and the passage is "
                         f"located, not absent. If it states no claim, say which it misses")


# --------------------------------------------------------------------------- #
# rule 9 under judgment rules 3: the statement cut (docs/proposals/statement-cut.md
# sections 1.3, 1.4 and 5)
# --------------------------------------------------------------------------- #

def clause_ids(row: dict, what: str) -> list[str]:
    """The clause ids of a frozen passage (a task manifest's passage or challenge record).

    Its conjuncts' ids, which the inventory's gate held to ``c1`` .. ``cN`` (rule
    10), or ``[SIMPLE_STATEMENT_CLAUSE]`` for a simple statement.  Conjuncts of
    another shape are an inconsistent frozen inventory: the gate cannot run.
    """
    conjuncts = row.get("conjuncts")
    if conjuncts is None:
        return [SIMPLE_STATEMENT_CLAUSE]
    if not isinstance(conjuncts, list) or not (MIN_CONJUNCTS <= len(conjuncts) <= MAX_CONJUNCTS) \
            or [c.get("id") if isinstance(c, dict) else None for c in conjuncts] != \
            [f"c{k + 1}" for k in range(len(conjuncts))]:
        raise GateBroken(f"{what} carries conjuncts that are not c1 .. cN: the frozen inventory "
                         f"is inconsistent")
    return [c["id"] for c in conjuncts]


def _is_compound(row: dict | None) -> bool:
    return isinstance((row or {}).get("conjuncts"), list) and bool(row["conjuncts"])


def _passage_texts(row: dict | None) -> list[str]:
    """What a frozen passage says in the inventory's words: its statement and conjuncts."""
    row = row or {}
    texts = [row.get("statement")]
    texts += [c.get("text") for c in row.get("conjuncts") or [] if isinstance(c, dict)]
    return [t for t in texts if isinstance(t, str)]


def word_pattern(words: Iterable[str]) -> re.Pattern:
    """A list of the tripwire's words as one pattern (cflib.vocab's ``NAT_FOR_INT_*_WORDS``).

    A word is matched as a phrase list's is (:func:`phrase_pattern`: whole words, a
    trailing ``*`` any ending); a symbol, any entry that is not ASCII, anywhere.
    """
    return re.compile("|".join(phrase_pattern(w).pattern if w.isascii() else re.escape(w)
                               for w in words))


#: the words by which a passage speaks of integers, and a note answers it (fix 2b)
_INTEGER_WORDS = word_pattern(NAT_FOR_INT_INTEGER_WORDS)
#: a binder's number type, as the index prints it, and the set it names
_NUMBER_TYPES = {"ℕ": "ℕ", "Nat": "ℕ", "ℤ": "ℤ", "Int": "ℤ", "ℚ": "ℚ", "Rat": "ℚ",
                 "ℝ": "ℝ", "Real": "ℝ", "ℂ": "ℂ", "Complex": "ℂ"}
_NATURAL_WORDS = word_pattern(NAT_FOR_INT_NATURAL_WORDS)


def numeric_binders(signature: Any) -> list[str]:
    """The number type (``ℕ``, ``ℤ``, ``ℚ``, ``ℝ``, ``ℂ``) of every binder group typed by one."""
    out = []
    for group in binder_groups(signature):
        _names, colon, typ = group[1:-1].partition(":")
        if colon and typ.strip() in _NUMBER_TYPES:
            out.append(_NUMBER_TYPES[typ.strip()])
    return out


def nat_for_int(cfg: GateConfig, ctx: Context, location: dict, row: dict | None,
                notes: Iterable[Any]) -> str:
    """The ℕ-for-ℤ tripwire (fix 2b): why a strong relation states naturals for integers, or ''.

    The passage's ``statement`` or a conjunct says "integer", "integers" or ``ℤ``;
    every number-typed binder group of the declaration's signature is ``ℕ``; and
    no note names "ℕ" or "natural" to answer it.  A statement over ℕ for one over
    ℤ is a special case (R3, PD2's ``narrowMatch``) unless the note says why the
    passage's integers are naturals.  A declaration with no recorded signature,
    or none of whose binders is number-typed, is not held to it.
    """
    if not any(_INTEGER_WORDS.search(fold_words(t)) for t in _passage_texts(row)):
        return ""
    library, name = location.get("library"), location.get("declaration")
    if not (isinstance(library, str) and isinstance(name, str) and library in cfg.libraries):
        return ""
    numbers = numeric_binders(ctx.signature(library, name))
    if not numbers or any(n != "ℕ" for n in numbers):
        return ""
    if any(_NATURAL_WORDS.search(fold_words(note)) for note in notes if isinstance(note, str)):
        return ""
    # judgment rules 4 (R-NAT): natural-number moduli for integer ones are an encoding, and
    # the same note answers the tripwire; the words of rules 3 and before are unchanged
    nat = ("; natural-number moduli for integer ones are an encoding, closeMatch (R-NAT): "
           "say so in the note, naming ℕ" if cfg.judgment_rules >= RULES_SMOKE4 else "")
    return (f"the passage speaks of integers and every number-typed binder of {library}:{name} "
            f"is ℕ: a statement over ℕ for one over ℤ is a special case, narrowMatch (R3, PD2); "
            f"if the passage's integers are naturals, say so in the note (name ℕ or the naturals)"
            + nat)


def check_tests(problems: Problems, where: str, tests: Any, source: Any, *,
                required: str = "") -> None:
    """Fixes 2a and 2d: a strong relation's two instantiation tests, ``tests: {r2, r3}``.

    ``r2`` says how the passage follows from the declaration, ``r3`` why the
    declaration at the passage's objects needs nothing the passage does not give;
    each is a one-line judgment field (:func:`check_line_field`).  ``required``
    names what owes them ('' when they are optional: checked when given).
    """
    if tests is None:
        if required:
            problems.add(9, where, f"{required} writes both instantiation tests: tests: {{r2: "
                                   f"<how the passage follows from the declaration>, r3: <why "
                                   f"the declaration, at the passage's objects, needs nothing "
                                   f"more>}} (R2, R3)")
        return
    if not isinstance(tests, dict) or set(tests) != set(TWO_WAY_TEST_KEYS):
        problems.add(9, where, "tests is {r2: <one line>, r3: <one line>} and nothing else")
        return
    for key in TWO_WAY_TEST_KEYS:
        if not check_line_field(problems, where, f"tests.{key}", tests[key], source) \
                and _blank(tests[key]):
            problems.add(9, where, f"tests.{key} is one line of your own: the test, as you ran it")


def conjunct_relations(rules: int) -> frozenset:
    """The ``conjunct_relation`` values of a run's judgment rules: 4 add ``hasPart``."""
    return CONJUNCT_RELATIONS_PART if rules >= RULES_SMOKE4 else CONJUNCT_RELATIONS


def check_conjunct_relation(problems: Problems, where: str, relation: str, value: Any,
                            row: dict | None, *, rules: int = RULES_STATEMENT_CUT) -> str:
    """J2: a ``hasPart`` of a compound statement gives its ``conjunct_relation``; nothing else does.

    Returns the relation the location's ``convention`` is held to: a part's
    ``conjunct_relation`` (a ``closeMatch`` part names its convention), else the
    location's own relation.  Under judgment rules 4 a part that states only some of
    what its conjuncts say gives ``hasPart`` (:func:`conjunct_relations`): it owes the
    ``step`` every ``hasPart`` owes and no ``tests``, and no bundle stands on it.
    """
    if relation == HAS_PART and _is_compound(row):
        allowed = conjunct_relations(rules)
        if not _one_of(value, allowed):
            problems.add(9, where, f"a hasPart of a compound statement says what it bears to "
                                   f"the conjunct(s) it names: conjunct_relation, one of "
                                   f"{', '.join(sorted(allowed))}, judged against "
                                   f"those conjuncts by R1-R3 and "
                                   f"PD1-PD{11 if rules >= RULES_SMOKE4 else 10}")
            return relation
        return value
    if value is not None:
        problems.add(9, where, ("conjunct_relation belongs to a hasPart of a compound "
                                "statement; a hasPart of a simple one names c1 and nothing more"
                                if relation == HAS_PART else
                                f"conjunct_relation belongs to a hasPart of a compound "
                                f"statement, and a {relation} is none: drop it"))
    return relation


def held_label(relation: Any, held_to: Any) -> str | None:
    """What a refusal calls a location held by another relation than its own, or None.

    Under judgment rules 3 a ``hasPart`` of a compound statement is held by its
    ``conjunct_relation`` (:func:`check_conjunct_relation`, a location verdict's
    :func:`_judge_verdict_conjunct_relation`): the relation it bears to the
    conjuncts it states, which is the relation a bundle takes from it (the
    weakest part's, status rule 2).  A part whose ``conjunct_relation`` is strong
    is held to every check a strong location is -- fixes 2a-2c and R1's words --
    and the refusal names it as "a part whose conjunct_relation is <relation>".
    """
    return None if held_to == relation else f"part whose conjunct_relation is {held_to}"


def check_strong_location(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                          location: dict, relation: str, row: dict | None, source: Any, *,
                          label: str | None = None) -> None:
    """Fixes 2a and 2b on a strong location: its ``tests``, its ``instantiation``, the ℕ tripwire.

    ``relation`` is the strong relation it is held by: its own, or a part's
    ``conjunct_relation``, which ``label`` then names (:func:`held_label`).
    """
    what = relation if label is None else label
    check_tests(problems, where, location.get("tests"), source,
                required=f"a {what}")
    if _blank(location.get("instantiation")):
        problems.add(9, where, f"a {what} says how the declaration is instantiated at the "
                               f"passage's objects, whatever its binders (instantiation: <one "
                               f"line>): which objects, and which instance or hypothesis makes "
                               f"it apply")
    why = nat_for_int(cfg, ctx, location, row, [location.get("note")])
    if why:
        problems.add(9, where, why)


def check_special_case(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                       value: Any, source: Any, *, what: str) -> None:
    """Fix 5b: an absence's nearest special case, ``special_case``, and why it fails R3/PD4.

    ``{library, declaration, reason}`` -- the declaration in ``corpus.sqlite`` at
    that library (rule 3) -- or ``{reason}`` when the search found none; the reason
    is a one-line judgment field.
    """
    if value is None:
        problems.add(9, where, f"an absence names the nearest special case you searched for and "
                               f"why it fails R3 or PD4: {what}.special_case: {{library, "
                               f"declaration, reason}}, or {{reason}} when there is none")
        return
    if not isinstance(value, dict) or set(value) not in ({"reason"},
                                                         {"library", "declaration", "reason"}):
        problems.add(9, where, f"{what}.special_case is {{library, declaration, reason}} or "
                               f"{{reason}}, nothing else")
        return
    if "library" in value:
        library, declaration = value["library"], value["declaration"]
        if not _one_of(library, cfg.libraries):
            problems.add(3, where, f"{what}.special_case names library {library!r}, which is "
                                   f"not in universe {cfg.universe_id}")
        elif not _nonempty_str(declaration):
            problems.add(9, where, f"{what}.special_case names its declaration")
        elif not ctx.exists(library, declaration):
            problems.add(3, where, f"{what}.special_case {library}:{declaration} is not in "
                                   f"corpus.sqlite (a phantom declaration)")
    if not check_line_field(problems, where, f"{what}.special_case.reason", value["reason"],
                            source) and _blank(value["reason"]):
        problems.add(9, where, f"{what}.special_case.reason says, in one line of your own, why "
                               f"the special case fails R3 or PD4 (or that none was found)")


def check_cut_flag(problems: Problems, where: str, passage: dict, outcome: Any) -> None:
    """J4: ``cut_flag`` -- what a worker says of a passage's cut -- on an unresolved one only."""
    if "cut_flag" not in passage:
        return
    if outcome != "unresolved":
        problems.add(9, where, "cut_flag belongs to an unresolved passage: what you say of a "
                               "cut you could not judge a declaration against")
    elif not _one_of(passage["cut_flag"], CUT_FLAGS):
        problems.add(9, where, f"cut_flag is one of {', '.join(sorted(CUT_FLAGS))}")


def _clauses_of(location: dict) -> set[str] | None:
    clauses = location.get("clauses")
    if not isinstance(clauses, list) or not clauses or not all(isinstance(c, str) for c in clauses):
        return None
    return set(clauses)


def check_bundle(problems: Problems, where: str, coverage: dict, locations: list[dict],
                 ids: list[str], row: dict | None) -> None:
    """J3: a coverage claim is a bundle -- on a compound statement, its conjuncts partitioned.

    Rule 1 has held the claim's object already (:func:`_check_coverage`).  Under
    judgment rules 3 it stands only on a compound statement; its parts'
    ``clauses`` are pairwise disjoint, together name every conjunct, and none
    names them all (PD6); and every part's ``conjunct_relation`` is one of
    ``BUNDLE_RELATIONS``, since a part that states only a special case of its
    conjuncts leaves them unstated.  The bundle's relation is computed from its
    parts, never written.
    """
    at = f"{where}: coverage"
    if not _is_compound(row):
        problems.add(9, at, "a coverage claim is a bundle, on a compound statement only: this "
                            "passage is a simple statement, so one declaration states it or "
                            "none does (a hasPart of it is partial)")
        return
    parts = coverage.get("parts")
    if not isinstance(parts, list):
        return
    by_rank = {_rank_of(loc): loc for loc in locations if _rank_of(loc) is not None}
    seen: dict[str, int] = {}
    for rank in parts:
        loc = by_rank.get(rank) if isinstance(rank, int) else None
        if loc is None or loc.get("relation") != HAS_PART:
            continue                       # rule 1 names it
        named = _clauses_of(loc) or set()
        if set(ids) <= named:
            problems.add(9, at, f"part {rank} names every conjunct ({', '.join(ids)}): "
                                f"{WHOLE_PART}")
        for cid in sorted(named):
            if cid in seen:
                problems.add(9, at, f"parts {seen[cid]} and {rank} both name {cid}: each conjunct "
                                    f"is stated by exactly one part")
            else:
                seen[cid] = rank
        if not _one_of(loc.get("conjunct_relation"), BUNDLE_RELATIONS):
            problems.add(9, at, f"part {rank} bears {loc.get('conjunct_relation')!r} to its "
                                f"conjuncts; a bundle's part states them strongly "
                                f"({', '.join(sorted(BUNDLE_RELATIONS))}): a special case of a "
                                f"conjunct, or a part of one, leaves it unstated, and the "
                                f"passage is partial")
    missing = [c for c in ids if c not in seen]
    if missing:
        problems.add(9, at, f"no part states {', '.join(missing)}: a bundle's parts together "
                            f"state every conjunct")


#: what a PD6 shape refusal asks of the part (locate) and of a confirm (challenge)
PD6_SHAPE_ASK = ("which conjunct it does not state on its own, and why (a characterisation "
                 "f = w ↔ P f of the passage's witness states existence at f := w and "
                 "uniqueness, so it states both)")


def check_pd6_shape(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                    coverage: dict, locations: list[dict], row: dict | None,
                    source: Any) -> None:
    """Judgment rules 4: PD6's shape tripwire on an existence-and-uniqueness or iff bundle.

    The smoke-4 audit: both of a run's PD6 misses were an iff characterisation of the
    passage's witness entered as the uniqueness part, which the clause check cannot
    see, since the part's ``clauses`` name one conjunct.  On a passage of a shape in
    ``PD6_SHAPES``, a part whose declaration's conclusion holds one of
    ``PD6_CONCLUSION_MARKS`` (:func:`pd6_mark`) may state every conjunct alone, so it
    says in ``pd6``, one line, which conjunct it does not state on its own and why;
    without it the part is refused as PD6's whole part.  A tripwire, not a parser: a
    part whose iff is about something else answers it in one line.
    """
    if (row or {}).get("shape") not in PD6_SHAPES or not isinstance(coverage.get("parts"), list):
        return
    by_rank = {_rank_of(loc): loc for loc in locations if _rank_of(loc) is not None}
    for rank in coverage["parts"]:
        loc = by_rank.get(rank) if isinstance(rank, int) else None
        if loc is None or loc.get("relation") != HAS_PART:
            continue                       # rule 1 names it
        mark = pd6_mark(cfg, ctx, loc)
        if mark is None or not _blank(loc.get("pd6")):
            continue                       # a given pd6 is checked with the location
        problems.add(9, f"{where}: coverage",
                     f"part {rank} ({loc.get('library')}:{loc.get('declaration')}) concludes "
                     f"with {mark!r} on a statement of shape {row['shape']}, so it may state every "
                     f"conjunct alone: {WHOLE_PART}. Give it pd6: <one line>, "
                     f"{PD6_SHAPE_ASK}, or give it its own relation and drop the bundle")


def unclaimed_bundle(locations: list[dict], ids: list[str]) -> list[int] | None:
    """Fix 4a: ranks of ``hasPart`` locations that state every conjunct strongly between them.

    Two or more, pairwise disjoint in their ``clauses``, together naming every
    conjunct, none naming them all, each with a ``conjunct_relation`` of
    ``BUNDLE_RELATIONS``: the bundle a passage with no coverage claim leaves
    unwritten.  None when there is no such set.
    """
    parts = [(_rank_of(loc), _clauses_of(loc)) for loc in locations
             if loc.get("relation") == HAS_PART and _rank_of(loc) is not None
             and _one_of(loc.get("conjunct_relation"), BUNDLE_RELATIONS)
             and _clauses_of(loc) and not set(ids) <= _clauses_of(loc)]
    parts = parts[:12]                     # max_locations is 5; a cap keeps this bounded
    want = set(ids)
    for mask in range(1, 1 << len(parts)):
        chosen = [parts[k] for k in range(len(parts)) if mask >> k & 1]
        if len(chosen) < MIN_COVERAGE_PARTS:
            continue
        union: set[str] = set()
        disjoint = True
        for _rank, named in chosen:
            if union & named:
                disjoint = False
                break
            union |= named
        if disjoint and union == want:
            return sorted(rank for rank, _named in chosen)
    return None


def judge_cut_passage(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                      p: dict, row: dict, locations: list[dict], receipt: dict, outcome: Any,
                      source: Any) -> None:
    """Rule 9 under judgment rules 3 over one passage of a locate document (J1-J4, section 5).

    The clause ids are the frozen passage's (:func:`clause_ids`): ``claims`` is
    refused (J1).  Each location is held to :func:`check_judgment` (J2, fixes
    2a-2c); a coverage claim is a bundle (J3, :func:`check_bundle`), and a
    compound statement whose strong parts state every conjunct between them
    writes it (fix 4a); queries name the clause ids; an absence's receipt names
    its nearest special case (fix 5b); and only an unresolved passage carries a
    ``cut_flag`` (J4).
    """
    kind = row.get("passage_kind")
    ids = clause_ids(row, f"task manifest passage {row.get('item')}")
    if "claims" in p:
        problems.add(9, where, "claims is the inventory's under the statement cut: the clause "
                               "ids are the passage's conjunct ids (or c1 for a simple "
                               "statement), so drop claims and name those")
    for j, loc in enumerate(locations):
        check_judgment(cfg, ctx, problems, f"{where}.locations[{j}]", loc, ids=ids,
                       passage_kind=kind, source=source, row=row)
    coverage = p.get("coverage") if isinstance(p.get("coverage"), dict) else None
    if outcome == "located" and coverage is not None:
        check_bundle(problems, where, coverage, locations, ids, row)
        if cfg.judgment_rules >= RULES_SMOKE4:
            check_pd6_shape(cfg, ctx, problems, where, coverage, locations, row, source)
    elif outcome == "located" and _is_compound(row) and "coverage" not in p:
        ranks = unclaimed_bundle(locations, ids)
        if ranks:
            problems.add(9, where, f"the hasPart locations ranked "
                                   f"{', '.join(map(str, ranks))} state every conjunct between "
                                   f"them, each strongly: write the bundle (coverage: {{complete: "
                                   f"true, parts: [{', '.join(map(str, ranks))}], note}}), or give "
                                   f"the part that falls short the conjunct_relation it bears")
    if outcome in ("located", "absent"):
        check_query_clauses(problems, where, p.get("receipt"), ids, absent=outcome == "absent",
                            passage_kind=kind)
    if outcome == "absent":
        check_rejections(problems, where, receipt)
        check_special_case(cfg, ctx, problems, where,
                           (p.get("receipt") or {}).get("special_case")
                           if isinstance(p.get("receipt"), dict) else None,
                           source, what="receipt")
    check_cut_flag(problems, where, p, outcome)
    check_free_text(problems, where, [
        *(("a location's note", loc.get("note")) for loc in locations),
        *(("a query", q) for q in receipt["queries"]),
        *(("a candidate's reason", c.get("reason")) for c in receipt["candidates"]),
        ("the coverage claim's note", (coverage or {}).get("note"))], source)


def _curated(ctx: Context, source_id: str) -> list[tuple[str, str]]:
    data = ctx.candidates(source_id)
    if not data:
        return []
    out = []
    for hit in data.get("curated") or []:
        if isinstance(hit, dict) and _nonempty_str(hit.get("library")) \
                and _nonempty_str(hit.get("declaration")):
            out.append((hit["library"], hit["declaration"]))
    return sorted(set(out))


def check_inventory(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                    manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1, 2, 4 and 8 for a ``locate-inventory`` document."""
    stats = {"passages": 0, "unresolved": unresolved}
    passages = doc.get("passages")
    skips = doc.get("skips", [])
    if not isinstance(passages, list):
        problems.add(1, name, "passages is a list")
        return stats
    if not isinstance(skips, list):
        problems.add(1, name, "skips is a list")
        skips = []
    if "notes" in doc and (not isinstance(doc["notes"], str) or len(doc["notes"]) > NOTES_MAX):
        problems.add(1, name, f"notes is a string of at most {NOTES_MAX} characters")
    if unresolved:
        if passages:
            problems.add(1, name, "an unresolved inventory carries no passages")
        return stats
    source = ctx.source(source_id)
    if source is None:
        return stats
    layer = source.layer
    stats["passages"] = len(passages)
    # under span-end rule 2 a passage without `until` ends no later than the next one's
    # start, so every start is read before any end is checked
    all_starts = sorted({sp[0] for sp in (_span(p.get("span"), len(layer)) for p in passages
                                          if isinstance(p, dict)) if sp is not None})
    starts: list[int] = []
    rows: list[tuple[int, dict, int]] = []         # (index, passage, head start)
    spans: list[tuple[int, int]] = []
    span_starts: set[int] = set()
    seen_items: set[str] = set()
    #: (index, passage, span) of every passage whose quote placed it: what rule 10 reads
    placed: list[tuple[int, dict, tuple[int, int]]] = []
    for i, p in enumerate(passages):
        where = f"{name}: passages[{i}]"
        if not isinstance(p, dict):
            problems.add(1, where, "a passage is an object")
            continue
        item = p.get("item")
        if not isinstance(item, str) or not ITEM.match(item):
            problems.add(1, where, f"item {item!r} is not `In-text #<8 hex>` as the stages write it")
        elif item in seen_items:
            problems.add(2, where, f"{item} is given twice")
        else:
            seen_items.add(item)
        for key, allowed in (("passage_kind", PASSAGE_KINDS), ("prominence", PROMINENCE),
                             ("scope", SCOPES), ("finder", FINDERS)):
            if not _one_of(p.get(key), allowed):
                problems.add(1, where, f"{key} {p.get(key)!r} is not one of "
                                       f"{', '.join(sorted(allowed))}")
        if "kind" in p and p["kind"] != "PASSAGE":
            problems.add(1, where, "an inventory row's kind, when written, is PASSAGE")
        if not _nonempty_str(p.get("statement")):
            problems.add(1, where, "statement restates the passage")
        if p.get("concept") is not None and not (isinstance(p["concept"], str)
                                                 and QID.match(p["concept"])):
            problems.add(1, where, "concept is null or a Wikidata QID")
        section = p.get("section", [])
        if not isinstance(section, list) or not all(isinstance(s, str) for s in section):
            problems.add(1, where, "section is a list of headings")
        if p.get("fragment") is not None and not isinstance(p.get("fragment"), str):
            problems.add(1, where, "fragment is null or a string")
        head_words = p.get("head_words")
        if not (isinstance(head_words, int) and not isinstance(head_words, bool)
                and 1 <= head_words <= lt.MAX_HEAD_WORDS):
            problems.add(1, where, f"head_words {head_words!r} is not 1..{lt.MAX_HEAD_WORDS}")
        span = _span(p.get("span"), len(layer))
        if span is None:
            problems.add(2, where, f"span {p.get('span')!r} is not [start, end] inside the layer "
                                   f"(0..{len(layer)})")
            continue
        spans.append(span)
        span_starts.add(span[0])
        quote = p.get("quote")
        if not isinstance(quote, dict) or not _nonempty_str(quote.get("exact")):
            problems.add(1, where, "quote is {exact, prefix, suffix} with a non-empty exact")
            continue
        exact = quote["exact"]
        prefix, suffix = quote.get("prefix", ""), quote.get("suffix", "")
        if not isinstance(prefix, str) or not isinstance(suffix, str):
            problems.add(1, where, "prefix and suffix are strings")
            continue
        if len(exact) > cfg.max_quote_chars or max(len(prefix), len(suffix)) > cfg.max_context_chars:
            problems.add(2, where, f"the quote is over the cap ({cfg.max_quote_chars} characters of "
                                   f"exact, {cfg.max_context_chars} of each context)")
            continue
        found = lt.find_quote(layer, exact, prefix, suffix)
        if len(found) != 1:
            problems.add(2, where, f"the quote matches the layer {len(found)} times; it must match "
                                   f"exactly once with its prefix and suffix")
            continue
        if found[0] != span[0] or span[0] + len(exact) > span[1]:
            problems.add(2, where, f"the quote starts at {found[0]} and ends at "
                                   f"{found[0] + len(exact)}; its passage is {list(span)}: the quote "
                                   f"starts the passage and lies inside it")
            continue
        # The end is what a reader highlights: the one passage_ids.py writes.  The
        # start is right here, so the passage still keys the others' ids below.  A
        # run planned before the span-end rule has no `until` to recompute it from
        # (its passage_ids.py dropped it), and keeps the bound above.
        if cfg.span_end_rule >= SPAN_END_STATEMENT_CUT:
            later = bisect.bisect_right(all_starts, span[0])
            end_problem = _end_problem(
                layer, source.blocks(), span, exact, p.get("until"), rule=cfg.span_end_rule,
                next_start=all_starts[later] if later < len(all_starts) else None,
                formulas=source.formulas)
            if end_problem:
                problems.add(2, where, end_problem)
        elif cfg.span_end_rule:
            end_problem = _end_problem(layer, source.paragraphs(), span, exact, p.get("until"))
            if end_problem:
                problems.add(2, where, end_problem)
        placed.append((i, p, span))
        try:
            head_start = source.map.to_head(span[0])
        except lt.HeadError as exc:
            problems.add(2, where, f"the passage cannot start there ({exc})")
            continue
        starts.append(head_start)
        rows.append((i, p, head_start))
        # Rule 1: the export quotes this passage with its own capped quote, so a
        # passage it cannot quote uniquely is refused NOW, while the worker can
        # still move the start, not after challenge at `cf export`.
        try:
            exported = capped_quote(layer, span)
        except lr.RecordError as exc:
            problems.add(1, where, f"the export cannot quote this passage ({exc}); start it "
                                   f"where its first words are distinctive")
            continue
        check_records(cfg, problems, where, source_id=source_id, layer=layer, span=span,
                      quote=exported, item=str(item), passage_kind=p.get("passage_kind"),
                      outcome=None, locations=[])
    if rows and len(starts) == len(rows):
        try:
            ids = lt.assign_ids(source.head, starts)
        except lt.HeadError as exc:
            problems.add(2, name, f"the passage table cannot be keyed: {exc}")
        else:
            for (i, p, _start), (_s, digest, words) in zip(rows, ids):
                if p.get("item") != f"In-text #{digest}" or p.get("head_words") != words:
                    problems.add(2, f"{name}: passages[{i}]",
                                 f"the passage at {p['span'][0]} is In-text #{digest} with a "
                                 f"{words}-word head, not {p.get('item')!r} with "
                                 f"{p.get('head_words')!r}")
    kept_by = _SpanIndex(spans)
    skip_spans: list[tuple[int, int]] = []
    for i, skip in enumerate(skips):
        at = f"{name}: skips[{i}]"
        span = _span(skip.get("span") if isinstance(skip, dict) else None, len(layer))
        if span is None:
            problems.add(1, at, "a skip is {span: [start, end] inside the layer, reason}")
            continue
        if not _nonempty_str(skip.get("reason")):
            problems.add(1, at, "a skip carries its reason")
        # A skip says "no passage here": one that holds a whole kept passage
        # contradicts it, and a skip of the whole layer beside the one passage
        # rule 4 asks for would otherwise meet the coverage floor alone.  A skip
        # INSIDE a kept passage stays allowed: it is how a finder nested in one
        # (a bolded definition in a theorem box) is dropped.
        held = kept_by.inside(span)
        if held is not None:
            problems.add(8, at, f"the skip {list(span)} holds the whole kept passage at "
                                f"{list(held)}: a skip drops text no passage keeps")
        skip_spans.append(span)
    skipped_by = _SpanIndex(skip_spans)
    skipped_finders = 0
    # rule 8: the deterministic finders' passages and the coverage floor
    for j, finder in enumerate(manifest_doc.get("finders") or []):
        if not isinstance(finder, dict):
            continue
        fspan = _span(finder.get("span"), len(layer))
        if fspan is None:
            raise GateBroken(f"task manifest finder {j} of {source_id} has no span in the layer")
        start = finder.get("start", fspan[0])
        kept = start in span_starts
        skipped = skipped_by.covering(fspan) is not None
        if not (kept or skipped):
            problems.add(8, name, f"the {finder.get('finder')} passage at {list(fspan)} was "
                                  f"dropped with no skip covering its span")
        elif skipped and not kept:
            skipped_finders += 1
    finder_starts = {(f.get("finder"), f.get("start", (f.get("span") or [None])[0]))
                     for f in manifest_doc.get("finders") or [] if isinstance(f, dict)}
    for i, p, _h in rows:
        if _one_of(p.get("finder"), DETERMINISTIC_FINDERS) \
                and (p.get("finder"), p["span"][0]) not in finder_starts:
            problems.add(8, f"{name}: passages[{i}]",
                         f"finder {p.get('finder')} is claimed, but no {p.get('finder')} passage of "
                         f"the task starts at {p['span'][0]}; a passage the enumerator found is "
                         f"`model`")
    skipped_math = skipped_prose = 0
    for k, paragraph in enumerate(source.paragraphs()):
        math_block = bool(paragraph.get("math"))
        if not math_block and not cfg.prose_floor_rule:
            continue
        pspan = _span(paragraph.get("span"), len(layer))
        if pspan is None:
            raise GateBroken(f"{source_id}: paragraphs.json entry {k} has no span in the layer")
        if not math_block and not prose_floor_applies(paragraph, layer[pspan[0]:pspan[1]]):
            continue
        if kept_by.overlapping(pspan) is not None:
            continue
        if skipped_by.overlapping(pspan) is not None:
            if math_block:
                skipped_math += 1
            else:
                skipped_prose += 1
            continue
        if math_block:
            problems.add(8, name, f"the math paragraph at {list(pspan)} (section "
                                  f"{paragraph.get('section') or []}) is covered by no passage "
                                  f"and no skip (the coverage floor)")
        else:
            problems.add(8, name, f"the paragraph at {list(pspan)} (section "
                                  f"{paragraph.get('section') or []}) is covered by no passage "
                                  f"and no skip (the prose floor): write it as a passage, "
                                  f"`expository` when it states nothing, or leave it out with a "
                                  f"skip and its reason")
    # what the floor's skips removed, for the harvest receipt (gate_corpus)
    stats["skipped"] = {"skips": len(skip_spans), "chars": _union_length(skip_spans),
                        "math_paragraphs": skipped_math, "finder_passages": skipped_finders}
    if cfg.prose_floor_rule:
        stats["skipped"]["prose_paragraphs"] = skipped_prose
    if cfg.statement_cut_rule:
        stats["cut"] = check_statement_cut(problems, name, passages, placed, source,
                                           rule=cfg.statement_cut_rule)
    # rule 4, inventory side
    curated = _curated(ctx, source_id)
    if curated:
        if not any(isinstance(p, dict) and _one_of(p.get("scope"), LOCATED_SCOPES)
                   for p in passages):
            problems.add(4, name, f"L2 holds {len(curated)} curated hit(s) for this article "
                                  f"({_hits(curated)}), so the inventory keeps a formal_math passage")
        for i, p in enumerate(passages):
            if isinstance(p, dict) and p.get("prominence") == "subject" \
                    and not _one_of(p.get("scope"), LOCATED_SCOPES):
                problems.add(4, f"{name}: passages[{i}]",
                             f"the subject passage is scoped {p.get('scope')} while L2 holds a "
                             f"curated hit for the article ({_hits(curated)})")
        # The hits are for the article's OWN subject, so the inventory names a
        # subject passage it keeps in formal math; relabelling the subject
        # `central` must not turn the half above off.
        if not any(isinstance(p, dict) and p.get("prominence") == "subject"
                   and _one_of(p.get("scope"), LOCATED_SCOPES) for p in passages):
            problems.add(4, name, f"L2 holds {len(curated)} curated hit(s) for this article "
                                  f"({_hits(curated)}), so a passage of prominence subject is "
                                  f"kept, scoped formal_math: the passage that states the "
                                  f"theorem or definition; a lead that only describes it is "
                                  f"`expository`")
    return stats


# --------------------------------------------------------------------------- #
# rule 10: the statement cut (docs/proposals/statement-cut.md section 3.3)
# --------------------------------------------------------------------------- #

def check_conjuncts(problems: Problems, where: str, passage: dict, source: Any) -> list[str] | None:
    """Rule 10 (d): a passage's ``conjuncts`` and ``shape``; returns the conjunct ids, or None.

    A simple statement carries neither (never ``[]``, never ``null``); a compound
    one carries ``MIN_CONJUNCTS``-``MAX_CONJUNCTS`` entries of exactly ``{id,
    text}``, the ids ``c1``, ``c2``, ... in order with no gap, each text held to
    the claim rules (one line of at most ``MAX_CLAIM_CHARS`` characters, in the
    worker's own words in every spelling of the source, alone and read one after
    the other: a bundle's conjuncts are exported), and a ``shape`` of
    ``COMPOUND_SHAPES``, with exactly the count ``SHAPE_CONJUNCT_COUNTS`` names
    for it.  None for a simple statement or a list refused here.
    """
    has_conjuncts, has_shape = "conjuncts" in passage, "shape" in passage
    if not has_conjuncts and not has_shape:
        return None
    if has_shape and not has_conjuncts:
        problems.add(10, where, "a shape belongs to a compound statement, which writes its "
                                "conjuncts: give both, or neither for a simple statement")
        return None
    conjuncts = passage["conjuncts"]
    if not isinstance(conjuncts, list) or not (MIN_CONJUNCTS <= len(conjuncts) <= MAX_CONJUNCTS):
        problems.add(10, where, f"conjuncts is absent on a simple statement, or a list of "
                                f"{MIN_CONJUNCTS} to {MAX_CONJUNCTS} {{id, text}} on a compound "
                                f"one (never [] or null): a statement holding more results "
                                f"than that is several statements, cut apart")
        return None
    ids: list[str] = []
    texts: list[str] = []
    usable, copied = True, False
    for k, conjunct in enumerate(conjuncts):
        at = f"{where}.conjuncts[{k}]"
        if not isinstance(conjunct, dict) or set(conjunct) != CLAIM_KEYS:
            problems.add(10, at, "a conjunct is {id, text} and nothing else")
            usable = False
            continue
        cid, text = conjunct["id"], conjunct["text"]
        if cid != f"c{k + 1}":
            problems.add(10, at, f"id {cid!r} is not c{k + 1}: the ids are c1, c2, ... in order")
            usable = False
        else:
            ids.append(cid)
        if not _nonempty_str(text):
            problems.add(10, at, "text says the conjunct, in a short paraphrase")
        elif not _one_line(text) or len(text) > MAX_CLAIM_CHARS:
            problems.add(10, at, f"text is one line of at most {MAX_CLAIM_CHARS} characters")
        else:
            texts.append(text)
            copied = _verbatim(problems, at, "the conjunct's text", text, source) or copied
    if len(texts) > 1 and not copied:
        _verbatim(problems, where, "the conjuncts, read one after the other,", " ".join(texts),
                  source)
    shape = passage.get("shape")
    if not has_shape:
        problems.add(10, where, f"a compound statement names its shape, one of "
                                f"{', '.join(sorted(COMPOUND_SHAPES))}")
    elif not _one_of(shape, COMPOUND_SHAPES):
        problems.add(10, where, f"shape {shape!r} is not one of {', '.join(sorted(COMPOUND_SHAPES))} "
                                f"(the closed list): anything else holding two results is two "
                                f"statements")
    elif shape in SHAPE_CONJUNCT_COUNTS and len(conjuncts) != SHAPE_CONJUNCT_COUNTS[shape]:
        problems.add(10, where, f"a {shape} statement has exactly {SHAPE_CONJUNCT_COUNTS[shape]} "
                                f"conjuncts, not {len(conjuncts)}")
    return ids if usable else None


def _items_of(rows: list[tuple[int, dict, tuple[int, int]]], k: int) -> str:
    return str(rows[k][1].get("item") or f"passages[{rows[k][0]}]")


def check_statement_cut(problems: Problems, name: str, passages: list,
                        placed: list[tuple[int, dict, tuple[int, int]]], source: Any,
                        rule: int = STATEMENT_CUT_FIRST) -> dict:
    """Rule 10 over an inventory document, under ``statement_cut_rule`` ``rule``; returns
    its counts.

    Refused (docs/proposals/statement-cut.md section 3.3): (a) two passages whose
    spans share a character; (b) a span that ends past its extent
    (``locate_text.extent``, compound when the passage has conjuncts: a passage
    runs from one block into the next only inside a theorem box, into the
    displays its last sentence introduces, or over the items of one list), or,
    for a passage no block holds, into the next block; (c) a span that ends
    strictly inside a formula; (d) conjuncts or a shape that are not a simple
    statement's none or a compound one's 2-5 (:func:`check_conjuncts`); (e)
    conjuncts on a passage not scoped ``formal_math``; (f) a passage not scoped
    ``formal_math`` whose prominence is not ``supporting``; (g) two passages with
    one span text.  Nothing is refused on a passage's size: the counts --
    the compound statements and their conjuncts, and the sentences
    (``locate_text.units``) and displays (``math`` blocks) of each ``formal_math``
    passage -- are the harvest receipt's ``cut`` block.

    Under ``statement_cut_rule`` 2 (``STATEMENT_CUT_CONTINUED``, inv-pilot-1's
    fixes), (b) reads the loose continued bound (``locate_text.Blocks.runs_on``,
    the maintainer's reading K1: a sentence may run on into the paragraph that
    finishes it, the display between seen by the gate or not), so an ``until``
    that keeps a sentence's words after its display is admitted; (h) refuses two
    or more ``formal_math`` passages of the source marked ``prominence:
    subject`` (reading K3), naming them all and choosing none; and the counts
    gain :func:`_continued_counts`'s.  Rule 1's checks, messages and counts are
    the ones they were.

    Under ``statement_cut_rule`` 3 (``STATEMENT_CUT_WIDE``, inv-pilot-2's repairs),
    (b) reads the wide bound (``locate_text.Blocks.extent(..., wide=True)``: the
    loose bound read across footnote marks, and into a ``dd`` or a paragraph that
    finishes the sentence, by its first word or by a formula opening it), never
    shorter than the loose one, so an ``until`` across a ``[1]`` or into such a
    paragraph is admitted; its refusal says so.  Everything else is rule 2's, and
    the counts gain :func:`_wide_counts`'s.  Rule 1's and rule 2's checks, messages
    and counts are the ones they were.
    """
    layer = source.layer
    blocks = source.blocks()
    top_starts = [blk.start for blk in blocks.top]
    continued = rule >= STATEMENT_CUT_CONTINUED
    wide = rule >= STATEMENT_CUT_WIDE
    counts = {"passages": len(passages), "formal": 0, "compound": 0, "conjuncts": 0,
              "by_shape": {}, "sentences": 0, "displays": 0, "multi_sentence": 0,
              "multi_display": 0}
    # (d), (e), (f): every passage the document writes
    for i, p in enumerate(passages):
        if not isinstance(p, dict):
            continue
        where = f"{name}: passages[{i}]"
        formal = _one_of(p.get("scope"), LOCATED_SCOPES)
        ids = check_conjuncts(problems, where, p, source)
        scoped_out = f"{'an' if str(p.get('scope'))[:1] in 'aeiou' else 'a'} {p.get('scope')}"
        if not formal and ("conjuncts" in p or "shape" in p):
            problems.add(10, where, f"conjuncts belong to a formal_math passage; {scoped_out} "
                                    f"passage states nothing a library could state")
        if not formal and _one_of(p.get("scope"), SCOPES) \
                and p.get("prominence") != PROMINENCE_SUPPORTING:
            problems.add(10, where, f"{scoped_out} passage is supporting, the one prominence "
                                    f"nothing reads there; the subject marker goes on the "
                                    f"passage that states the theorem, scoped formal_math")
        if formal:
            counts["formal"] += 1
            if ids:
                counts["compound"] += 1
                counts["conjuncts"] += len(ids)
                shape = p.get("shape")
                if _one_of(shape, COMPOUND_SHAPES):
                    counts["by_shape"][shape] = counts["by_shape"].get(shape, 0) + 1
    rows = sorted(placed, key=lambda r: (r[2][0], r[2][1]))
    # (a): spans of the source that share a character, swept by start
    reach = -1
    holder = -1
    for k, (i, p, span) in enumerate(rows):
        if span[0] < reach:
            problems.add(10, f"{name}: passages[{i}]",
                         f"{_items_of(rows, k)} {list(span)} shares text with "
                         f"{_items_of(rows, holder)} {list(rows[holder][2])}: the spans of one "
                         f"source are disjoint (a passage ends where the next one starts; give "
                         f"`until` to end one sooner)")
        if span[1] > reach:
            reach, holder = span[1], k
    # (g): one span text, one passage_key
    by_text: dict[str, int] = {}
    for k, (i, p, span) in enumerate(rows):
        text = layer[span[0]:span[1]]
        if text in by_text:
            problems.add(10, f"{name}: passages[{i}]",
                         f"{_items_of(rows, k)} has the same text as "
                         f"{_items_of(rows, by_text[text])}: two passages with one span text "
                         f"have one passage key, which no carry-over can tell apart")
        else:
            by_text[text] = k
    # (h): one subject at most, among the formal passages (a scoped-out one is (f)'s)
    if continued:
        subjects = [k for k, (_i, p, _span) in enumerate(rows)
                    if p.get("prominence") == "subject"
                    and _one_of(p.get("scope"), LOCATED_SCOPES)]
        if len(subjects) > 1:
            named = ", ".join(f"{_items_of(rows, k)} {list(rows[k][2])}" for k in subjects)
            problems.add(10, name, f"{len(subjects)} passages carry `prominence: subject` "
                                   f"({named}): the marker goes on one passage, the one that "
                                   f"states the theorem or definition the article is about; a "
                                   f"lead that only describes it is `expository`; mark any "
                                   f"other that states it `central`")
    # (b), (c), and the counts
    for i, p, span in rows:
        where = f"{name}: passages[{i}]"
        compound = isinstance(p.get("conjuncts"), list) and bool(p.get("conjuncts"))
        bound = blocks.extent(span[0], compound=compound, loose=continued, wide=wide)
        if bound is None:
            later = bisect.bisect_right(top_starts, span[0])
            bound = top_starts[later] if later < len(top_starts) else len(layer)
        if span[1] > bound and wide:
            problems.add(10, where, f"the passage ends at {span[1]}, past {bound}, where its "
                                    f"block ends: a passage runs from one block into the next "
                                    f"only inside a theorem box, into the displays its last "
                                    f"sentence introduces and the paragraph or indented line "
                                    f"that finishes that sentence after them (a footnote mark "
                                    f"between them, or a formula opening that paragraph, does "
                                    f"not end it), or -- a compound statement -- over the "
                                    f"items of one list; cut the rest into passages of their "
                                    f"own")
        elif span[1] > bound and continued:
            problems.add(10, where, f"the passage ends at {span[1]}, past {bound}, where its "
                                    f"block ends: a passage runs from one block into the next "
                                    f"only inside a theorem box, into the displays its last "
                                    f"sentence introduces and the paragraph that finishes "
                                    f"that sentence after them, or -- a compound statement -- "
                                    f"over the items of one list; cut the rest into passages "
                                    f"of their own")
        elif span[1] > bound:
            problems.add(10, where, f"the passage ends at {span[1]}, past {bound}, where its "
                                    f"block ends: a passage runs from one block into the next "
                                    f"only inside a theorem box, into the displays its last "
                                    f"sentence introduces, or -- a compound statement -- over "
                                    f"the items of one list; cut the rest into passages of "
                                    f"their own")
        formula = blocks.formula_at(span[1])
        if formula is not None and formula[0] < span[1]:
            problems.add(10, where, f"the passage ends at {span[1]}, inside the formula at "
                                    f"[{formula[0]}, {formula[1]}]: a display is never cut; end "
                                    f"the passage after it")
        if _one_of(p.get("scope"), LOCATED_SCOPES):
            units = blocks.units(span)
            sentences, displays = len(units["sentences"]), len(units["displays"])
            counts["sentences"] += sentences
            counts["displays"] += displays
            counts["multi_sentence"] += sentences > 1
            counts["multi_display"] += displays > 1
    counts["by_shape"] = dict(sorted(counts["by_shape"].items()))
    if continued:
        counts.update(_continued_counts(blocks, rows))
    if wide:
        counts.update(_wide_counts(blocks, rows))
    return counts


def _continued_counts(blocks: Any, rows: list[tuple[int, dict, tuple[int, int]]]) -> dict:
    """The counts statement-cut rule 2 adds to the receipt's ``cut`` block (the
    maintainer's reading 7; docs/proposals/statement-cut.md section 3.3).  Reported,
    never a refusal.

    ``subjects``: the ``formal_math`` passages of prominence subject.
    ``continued``: the formal passages that end past their extent without the
    continuation (``continued=False``), so the continued bound let them run on.
    ``continuation_starts``: passages of any scope that start at a continuation's
    first word (:meth:`~locate_text.Blocks.continuations`): the words after a
    display cut from their sentence.  ``continuations_unheld``: continuations
    after a block a formal passage shares a character with, that no passage
    shares one with.  ``loose_starts`` and ``loose_unheld``: the same two counts
    over the paragraphs only the loose bound reaches
    (:meth:`~locate_text.Blocks.loose_tails`: after a display the gate cannot see,
    a paragraph with no full stop or a container's sentence), whose default end
    stops before them, the run-on head in place of the display -- so a tail that
    no ``until`` kept and no passage holds, or that starts a passage of its own,
    is counted too.  ``connective_points`` and ``by_connective``: the cut points
    (:meth:`~locate_text.Blocks.cut_points`) inside formal passages, an upper bound
    on the cuts the rules make there, and ``formal_with_connective`` the formal
    passages holding one.  ``connective_starts``: formal passages whose first word
    is a connective and that start inside a sentence
    (:meth:`~locate_text.Blocks.mid_sentence`) -- a sentence that starts "Then" is
    a correct passage, and is not counted.
    """
    out = {"subjects": 0, "continued": 0, "continuation_starts": 0, "continuations_unheld": 0,
           "loose_starts": 0, "loose_unheld": 0, "connective_points": 0,
           "formal_with_connective": 0, "connective_starts": 0, "by_connective": {}}
    spans = [span for _i, _p, span in rows]
    formal = [(p, span) for _i, p, span in rows if _one_of(p.get("scope"), LOCATED_SCOPES)]
    starts = {span[0] for span in spans}
    connectives = _connective_words()
    for p, span in formal:
        out["subjects"] += p.get("prominence") == "subject"
        compound = isinstance(p.get("conjuncts"), list) and bool(p.get("conjuncts"))
        plain = blocks.extent(span[0], compound=compound)
        out["continued"] += plain is not None and span[1] > plain
        points = blocks.cut_points(span)
        out["connective_points"] += len(points)
        out["formal_with_connective"] += bool(points)
        for _at, connective in points:
            out["by_connective"][connective] = out["by_connective"].get(connective, 0) + 1
        words = blocks.layer[span[0]:span[1]].split(None, 1)
        first = words[0].rstrip(",;:").lower() if words else ""
        out["connective_starts"] += first in connectives and blocks.mid_sentence(span[0])

    def shares(block, among) -> bool:
        return any(a < block.end and block.start < b for a, b in among)

    def first_word(tail) -> int:
        first = tail.start
        while first < tail.end and lt._is_white(blocks.layer[first]):
            first += 1
        return first

    formal_spans = [span for _p, span in formal]
    for display, tail in blocks.continuations():
        out["continuation_starts"] += first_word(tail) in starts
        out["continuations_unheld"] += shares(display, formal_spans) and not shares(tail, spans)
    for head, tail in blocks.loose_tails():
        out["loose_starts"] += first_word(tail) in starts
        out["loose_unheld"] += any(shares(b, formal_spans) for b in head) \
            and not shares(tail, spans)
    out["by_connective"] = dict(sorted(out["by_connective"].items()))
    return out


def _wide_counts(blocks: Any, rows: list[tuple[int, dict, tuple[int, int]]]) -> dict:
    """The counts statement-cut rule 3 adds to the receipt's ``cut`` block, beside rule
    2's (docs/proposals/statement-cut.md section 3.3).  Reported, never a refusal.

    ``widened``: the formal passages that end past their loose extent
    (``loose=True``), so only the wide bound let them run on -- each such ``until``
    is a merge the wide bound alone admits, for a reader to judge.
    ``wide_starts`` and ``wide_unheld``: ``loose_starts`` and ``loose_unheld``
    over the blocks only the wide bound reaches
    (:meth:`~locate_text.Blocks.wide_tails`: across a footnote mark, a ``dd`` or a
    paragraph that finishes the sentence), whose default end stops before them --
    a passage of any scope that starts at such a tail's first word, and a tail
    after a head a formal passage shares a character with that no passage holds.
    ``wide_starts`` is an upper bound on the cuts the wide bound would undo: a
    tail can be a correct start of its own.
    """
    out = {"widened": 0, "wide_starts": 0, "wide_unheld": 0}
    spans = [span for _i, _p, span in rows]
    formal = [(p, span) for _i, p, span in rows if _one_of(p.get("scope"), LOCATED_SCOPES)]
    starts = {span[0] for span in spans}
    for p, span in formal:
        compound = isinstance(p.get("conjuncts"), list) and bool(p.get("conjuncts"))
        loose = blocks.extent(span[0], compound=compound, loose=True)
        out["widened"] += loose is not None and span[1] > loose
    formal_spans = [span for _p, span in formal]
    for head, tail in blocks.wide_tails():
        first = tail.start
        while first < tail.end and lt._is_white(blocks.layer[first]):
            first += 1
        out["wide_starts"] += first in starts
        out["wide_unheld"] += any(_shares(b, formal_spans) for b in head) \
            and not _shares(tail, spans)
    return out


def _shares(block: Any, among: list) -> bool:
    """Does ``block`` share a character with one of the spans ``among``?"""
    return any(a < block.end and block.start < b for a, b in among)


def _connective_words() -> frozenset[str]:
    """The words a connective of ``locate_text.CUT_CONNECTIVES`` is, its separators set
    aside: "since", ..., and the "and" of ", and"."""
    return frozenset(c.lstrip(",; ") for c in lt.CUT_CONNECTIVES) - {""}


def _hits(curated: list[tuple[str, str]], n: int = 3) -> str:
    shown = ", ".join(f"{lib}:{decl}" for lib, decl in curated[:n])
    return shown + (f" and {len(curated) - n} more" if len(curated) > n else "")


def check_locate(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                 manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1-6 for a ``locate`` document."""
    stats = {"passages": 0, "unresolved_passages": 0, "unresolved": unresolved}
    passages = doc.get("passages")
    if not isinstance(passages, list):
        problems.add(1, name, "passages is a list")
        return stats
    source = ctx.source(source_id)
    if source is None:
        return stats
    frozen: dict[str, dict] = {}
    for p in manifest_doc.get("passages") or []:
        if not isinstance(p, dict) or not isinstance(p.get("item"), str):
            continue
        span = _span(p.get("span"), len(source.layer))
        try:
            if span is None:
                raise lt.HeadError(f"span {p.get('span')!r} is not in the layer")
            digest = lt.hex_at(source.head, source.map.to_head(span[0]), int(p.get("head_words") or 0))
        except (lt.HeadError, ValueError) as exc:
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} does not key on "
                             f"the pinned layer ({exc}): the frozen inventory is inconsistent") from None
        if p["item"] != f"In-text #{digest}":
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} hashes to "
                             f"In-text #{digest} on the pinned layer: the frozen inventory is "
                             f"inconsistent")
        try:
            quote = capped_quote(source.layer, span)
        except lr.RecordError as exc:
            raise GateBroken(f"task manifest passage {p['item']} of {source_id} cannot be quoted "
                             f"by the export ({exc}): the frozen inventory predates the rule "
                             f"that refuses such a passage") from None
        frozen[p["item"]] = dict(p, span=span, quote=quote)
    written: dict[str, int] = {}
    stats["passages"] = len(passages)
    handled: set[tuple[str, str]] = set()
    absent_where: list[str] = []
    for i, p in enumerate(passages):
        where = f"{name}: passages[{i}]"
        if not isinstance(p, dict):
            problems.add(1, where, "a passage is an object")
            continue
        item = p.get("item")
        if not isinstance(item, str) or item not in frozen:
            problems.add(2, where, f"item {item!r} is not one of this task's passages")
            continue
        if item in written:
            problems.add(2, where, f"{item} is given twice")
            continue
        written[item] = i
        where = f"{name}: {item}"
        outcome = p.get("outcome")
        if not _one_of(outcome, LOCATE_OUTCOMES):
            problems.add(1, where, f"outcome {outcome!r} is not located/absent/unresolved")
            continue
        if unresolved and outcome != "unresolved":
            problems.add(1, where, "an unresolved document leaves every passage unresolved")
        locations = _check_locations(cfg, ctx, problems, where, p.get("locations"))
        receipt = _check_receipt(cfg, ctx, problems, where, p.get("receipt"))
        if locations is None:
            continue                          # over the cap: refused, nothing more to read
        counting = [loc for loc in locations if loc.get("relation") != RELATED_MATCH]
        if outcome == "located" and not counting:
            problems.add(1, where, "located needs a location whose relation counts; with only "
                                   "relatedMatch the passage is absent, beside them")
        if outcome == "absent":
            if counting:
                problems.add(1, where, "an absence may sit beside relatedMatch locations only")
            distinct = {" ".join(q.split()).casefold() for q in receipt["queries"] if q.strip()}
            if len(distinct) < cfg.min_absence_queries:
                problems.add(5, where, f"an absence lists {len(distinct)} distinct quer"
                                       f"{'y' if len(distinct) == 1 else 'ies'}; at least "
                                       f"{cfg.min_absence_queries}")
            absent_where.append(where)
        if outcome == "unresolved":
            stats["unresolved_passages"] += 1
            if locations:
                problems.add(1, where, "an unresolved passage has no locations")
            if not _nonempty_str(p.get("reason")) and not unresolved:
                problems.add(1, where, "an unresolved passage says why (reason)")
        if "coverage" in p:
            _check_coverage(problems, where, p["coverage"], outcome=outcome,
                            locations=locations, layer=source.layer)
        if cfg.judgment_rules >= RULES_STATEMENT_CUT:
            judge_cut_passage(cfg, ctx, problems, where, p, frozen[item], locations, receipt,
                              outcome, source)
        elif cfg.judgment_rules:
            kind = frozen[item].get("passage_kind")
            ids = check_claims(problems, where, p.get("claims"), source,
                               required=outcome in ("located", "absent"))
            for j, loc in enumerate(locations):
                check_judgment(cfg, ctx, problems, f"{where}.locations[{j}]", loc, ids=ids,
                               passage_kind=kind, source=source)
            coverage = p.get("coverage") if isinstance(p.get("coverage"), dict) else None
            if outcome == "located" and coverage is not None:
                check_coverage_clauses(problems, where, coverage, locations, ids,
                                       rules=cfg.judgment_rules)
            if outcome in ("located", "absent"):
                check_query_clauses(problems, where, p.get("receipt"), ids,
                                    absent=outcome == "absent", passage_kind=kind)
            if outcome == "absent":
                check_rejections(problems, where, receipt)
            check_free_text(problems, where, [
                *(("a location's note", loc.get("note")) for loc in locations),
                *(("a query", q) for q in receipt["queries"]),
                *(("a candidate's reason", c.get("reason")) for c in receipt["candidates"]),
                ("the coverage claim's note", (coverage or {}).get("note"))], source)
        # Rule 4's "located or rejected with a reason": a location whose relation
        # COUNTS locates a hit (a relatedMatch is "relevant but states something
        # else", so it discharges nothing on its own), and a rejection with a
        # reason rejects one.  An `accepted` candidate is one of the passage's
        # counting locations; accepted and not located is neither.
        located = {(str(loc.get("library")), str(loc.get("declaration"))) for loc in counting}
        handled.update(located)
        for cand in receipt["candidates"]:
            key = (str(cand.get("library")), str(cand.get("declaration")))
            if cand.get("verdict") == "accepted" and key not in located:
                problems.add(1, where, f"accepted candidate {key[0]}:{key[1]} is not one of this "
                                       f"passage's locations with a relation that counts: "
                                       f"locate it, or reject it with a reason")
            elif cand.get("verdict") == "rejected" and _nonempty_str(cand.get("reason")):
                handled.add(key)
        span = frozen[item]["span"]
        check_records(cfg, problems, where, source_id=source_id, layer=source.layer, span=span,
                      quote=frozen[item]["quote"], item=item,
                      passage_kind=frozen[item].get("passage_kind"), outcome=outcome,
                      locations=locations, receipt=receipt)
    missing = sorted(set(frozen) - set(written))
    if missing:
        problems.add(2, name, f"the task's passages {', '.join(missing)} have no outcome")
    # rule 4: every curated hit HANDED TO THIS TASK is located or rejected with a
    # reason.  The planner gives each of a source's curated hits to exactly one of
    # its tasks and names them in the manifest's `curated` list; the gate takes
    # that list intersected with the candidates file it reads itself, so a
    # manifest cannot add a hit, and a manifest that predates the key hands the
    # task every hit of the file.
    if not unresolved:
        hits = _curated(ctx, source_id)
        if "curated" in manifest_doc:
            named = {(str(e.get("library")), str(e.get("declaration")))
                     for e in manifest_doc.get("curated") or [] if isinstance(e, dict)}
            hits = [hit for hit in hits if hit in named]
        unhandled = [hit for hit in hits if hit not in handled]
        if unhandled:
            problems.add(4, name, f"curated L2 hit(s) {_hits(unhandled)} are neither located "
                                  f"(with a relation that counts: a relatedMatch is not) nor "
                                  f"rejected with a reason in any receipt"
                                  + (f", and {len(absent_where)} passage(s) end absent"
                                     if absent_where else ""))
    return stats


def _challenge_claims(cfg: GateConfig, problems: Problems, name: str, doc: dict,
                      records: dict[tuple, dict], source: Any) -> dict[str, list[str] | None]:
    """Rule 9: a challenge document's ``claims``, ``{<item>: [{id, text}, ...]}``.

    The challenger's own claim list of each passage it judges (the FM7 rule:
    list the passage's clauses before you confirm, downgrade, refute or uphold),
    which its verdicts name.  Returns ``{item: ids}`` (ids None for a list
    refused here); ``{}`` outside the judgment rules.
    """
    if not cfg.judgment_rules:
        return {}
    if cfg.judgment_rules >= RULES_STATEMENT_CUT:
        # the statement cut: one list, the inventory's (docs/proposals/statement-cut.md 1.4)
        if "claims" in doc:
            problems.add(9, name, "claims is the inventory's under the statement cut: every "
                                  "verdict names the record's clause ids (its passage's "
                                  "conjunct ids, or c1 for a simple statement); drop claims")
        out = {}
        for rec in records.values():
            item = rec.get("item")
            if isinstance(item, str) and (item not in out or _is_compound(rec)):
                out[item] = clause_ids(rec, f"challenge record {item}")
        return out
    given = doc.get("claims")
    if given is None:
        return {}
    if not isinstance(given, dict):
        problems.add(9, name, "claims is {<item>: [{id, text}, ...]}: the claim list of each "
                              "passage this task judges")
        return {}
    items = {r.get("item") for r in records.values()}
    out: dict[str, list[str] | None] = {}
    for item, claims in sorted(given.items()):
        if item not in items:
            problems.add(9, name, f"claims names {item!r}, which no record of this task is on")
            continue
        out[item] = check_claims(problems, f"{name}: claims[{item}]", claims, source,
                                 required=True)
    return out


def _challenger_note(v: dict, rec: dict, source: Any, result: Any) -> str:
    """The note a downgrade's challenger writes for the record it leaves.

    ``locate.outcomes`` keeps a location's non-empty note, and gives a
    ``closeMatch`` that has none ``locate_records.downgrade_note`` (the verdict's
    ``note``, else a sentence of its witness): that is the challenger's text the
    record will carry.  Otherwise it is the verdict's own ``note``.  Rule 9 reads
    the challenger's words only: a record's note is its worker's, which the
    challenge plan refutes by default when it admits a gap, and a challenger is
    never forced to a relation by text it did not write.
    """
    if result == CLOSE_MATCH and not str(rec.get("note") or "").strip():
        try:
            return lr.downgrade_note(v, source.layer if source is not None else None)
        except Exception:  # noqa: BLE001 - the note's own check (rule 1) names its fault
            pass
    return str(v.get("note") or "")


#: the verdicts whose challenger lists the passage's claims first (the FM7 rule): every
#: judgment of what a passage asserts -- a location's, a coverage claim's, an absence's
#: or an unresolved outcome's -- whatever its action
CLAIMED_TARGETS = frozenset({"location", "coverage", "absence", "unresolved"})


def _judge_location_verdict(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                            v: dict, rec: dict, ids: list[str] | None, source: Any) -> None:
    """Rule 9 over a location verdict: the relation it leaves states what it says.

    A confirm leaves the record's relation and a downgrade the one it names; one
    that counts names the claim ids it states (``clauses``, against the
    document's ``claims`` for the passage), all of them for a strong relation
    (R4), and a ``relatedMatch`` names none.  A strong relation it keeps or leaves
    rests on a claim list at least as long as the worker's (a challenger splits a
    worker's clause, never drops one).  A ``hasPart`` or ``narrowMatch`` it leaves
    names its ``step`` (FM3), and a ``closeMatch`` its ``convention`` (R1, FM4): a
    downgrade's own, a confirm's when the record names none; a step or convention
    on a verdict that leaves any other relation is refused.  The challenger's own
    note (:func:`_challenger_note`) and instantiation admit no gap on a strong
    relation, and an exact or close one's note says no generality.  A counting
    relation it keeps on a declaration with instance arguments, whose record names
    no instantiation (every record of a run planned before the rules), carries the
    challenger's own.  Under judgment rules 3 a verdict on a part of a compound
    statement is held by the ``conjunct_relation`` it leaves the part
    (:func:`held_label`): a strong one owes its own ``tests`` (fix 2d), the ℕ
    tripwire (fix 2b) and the generality words (fix 2c, R1), as a verdict that
    keeps a strong location does.
    """
    action, old = v.get("action"), rec.get("relation")
    result = old if action == "confirm" else v.get("relation") if action == "downgrade" else None
    if not _one_of(result, LOCATE_RELATIONS):
        return
    cut = cfg.judgment_rules >= RULES_STATEMENT_CUT
    check_clauses(problems, where, result, v.get("clauses"), ids)
    worker = rec.get("claims")
    if not cut and result in STRONG_RELATIONS and ids is not None and isinstance(worker, list) \
            and len(ids) < len(worker):
        problems.add(9, where, f"a verdict that keeps a {result} states every clause, and your "
                               f"claim list of the passage has {len(ids)} claim(s) where the "
                               f"worker's has {len(worker)}: split a clause, never drop one; "
                               f"list each clause the passage asserts, then judge")
    keeps_step = action == "confirm" and _nonempty_str(rec.get("step"))
    check_step(problems, where, result, v.get("step"), source,
               passage_kind=rec.get("passage_kind"), required=not keeps_step)
    held_to = result
    if cut:
        held_to = _judge_verdict_conjunct_relation(problems, where, v, rec, result, action,
                                                   rules=cfg.judgment_rules)
    if cfg.judgment_rules >= RULES_SMOKE4:
        check_bridge(cfg, ctx, problems, where, v, rec, result, action, source)
    label = held_label(result, held_to)
    own = v.get("convention")
    if held_to == CLOSE_MATCH and action == "confirm" and not _nonempty_str(own):
        own = rec.get("convention")
    # a confirm keeps the record's own convention when it names none or names it again;
    # one that names another relabels it, held to its own rules' list like a downgrade's
    kept = (held_to == CLOSE_MATCH and action == "confirm" and _nonempty_str(own)
            and own == rec.get("convention"))
    check_convention(problems, where, held_to, own, rules=cfg.judgment_rules, kept=kept)
    note = (_challenger_note(v, rec, source, result) if action == "downgrade"
            else str(v.get("note") or ""))
    check_generality(problems, where, held_to, note, rules=cfg.judgment_rules, label=label)
    if cut and held_to in STRONG_RELATIONS:
        # fix 2d: the challenger's own two-way test, and the tripwire on what it leaves --
        # a part's by the conjunct_relation the verdict leaves it (held_label)
        check_tests(problems, where, v.get("tests"), source,
                    required=f"a verdict that keeps or leaves a {label or held_to}")
        why = nat_for_int(cfg, ctx, rec, rec, [v.get("note"), rec.get("note")])
        if why:
            problems.add(9, where, why)
    elif cut and v.get("tests") is not None:
        check_tests(problems, where, v.get("tests"), source)
    given = check_line_field(problems, where, "instantiation", v.get("instantiation"), source)
    if result in STRONG_RELATIONS:
        for field, text in (("note", note), ("instantiation", v.get("instantiation"))):
            phrase = admits_missing_clause(text)
            if phrase:
                problems.add(9, where, _gap_problem(phrase, result, field))
                break
    if result != RELATED_MATCH and not given and _blank(v.get("instantiation")) \
            and not _nonempty_str(rec.get("instantiation")):
        binders = _binders_needing(cfg, ctx, rec)
        if binders:
            problems.add(9, where, _instantiation_problem(rec, binders, challenger=True))


def held_record(rec: dict) -> str | None:
    """The relation a challenge record is held to: its own, or a part's ``conjunct_relation``."""
    relation = rec.get("relation")
    if relation == HAS_PART and _one_of(rec.get("conjunct_relation"), CONJUNCT_RELATIONS_PART):
        return rec["conjunct_relation"]
    return relation if _one_of(relation, LOCATE_RELATIONS) else None


def check_bridge(cfg: GateConfig, ctx: Context, problems: Problems, where: str, v: dict,
                 rec: dict, result: Any, action: Any, source: Any) -> None:
    """Judgment rules 4: a challenge downgrade of a strong record to relatedMatch names why.

    The smoke-4 audit: a third of one run's downgrades from broadMatch to relatedMatch
    were wrong, most of them an encoding or plain arithmetic taken for a PD8 bridge.
    A downgrade to ``relatedMatch`` of a record held to a strong relation (its own, or
    a part's ``conjunct_relation``: :func:`held_record`) gives exactly one of
    ``bridge``, ``{library, declaration, why}`` -- the named lemma the connection
    needs, in ``corpus.sqlite`` at that library (rule 3), and why in one line -- or
    ``incomparable``, one line saying why the two settings neither contain one another
    (R5's other ground).  A lemma that only links two definitions of one notion is
    PD10's encoding and no bridge, so the record keeps its relation.  Neither field
    belongs to any other verdict.
    """
    bridge, apart = v.get("bridge"), v.get("incomparable")
    downgrade = action == "downgrade" and result == RELATED_MATCH \
        and held_record(rec) in STRONG_RELATIONS
    if not downgrade:
        if bridge is not None or apart is not None:
            problems.add(9, where, "bridge and incomparable belong to a downgrade of a strong "
                                   "record to relatedMatch: drop them")
        return
    if (bridge is None) == (apart is None):
        problems.add(9, where, "a downgrade of a strong record to relatedMatch says why, by "
                               "exactly one of bridge: {library, declaration, why} (the named "
                               "lemma the connection needs, PD8) or incomparable: <one line> "
                               "(two settings neither containing the other, R5). A lemma that "
                               "only links two definitions of one notion is no bridge but an "
                               "encoding (PD10): then the record keeps its relation")
        return
    if apart is not None:
        if not check_line_field(problems, where, "incomparable", apart, source) and _blank(apart):
            problems.add(9, where, "incomparable is one line of your own: why the two settings "
                                   "neither contain one another")
        return
    if not isinstance(bridge, dict) or set(bridge) != {"library", "declaration", "why"}:
        problems.add(9, where, "bridge is {library, declaration, why}, nothing else")
        return
    library, declaration = bridge["library"], bridge["declaration"]
    if not _one_of(library, cfg.libraries):
        problems.add(3, where, f"bridge names library {library!r}, which is not in universe "
                               f"{cfg.universe_id}")
    elif not _nonempty_str(declaration):
        problems.add(9, where, "bridge names its declaration, the lemma the connection needs")
    elif not ctx.exists(library, declaration):
        problems.add(3, where, f"bridge {library}:{declaration} is not in corpus.sqlite (a "
                               f"phantom declaration)")
    elif declaration == rec.get("declaration") and library == rec.get("library"):
        problems.add(9, where, "bridge names the record's own declaration: name the other "
                               "lemma the connection needs")
    if not check_line_field(problems, where, "bridge.why", bridge["why"], source) \
            and _blank(bridge["why"]):
        problems.add(9, where, "bridge.why is one line of your own: what the lemma adds that "
                               "decidable arithmetic does not close")


def _judge_verdict_conjunct_relation(problems: Problems, where: str, v: dict, rec: dict,
                                     result: str, action: Any, *,
                                     rules: int = RULES_STATEMENT_CUT) -> str:
    """Rules 3: a location verdict's ``conjunct_relation``, never stronger than the record's.

    Only a verdict that leaves a ``hasPart`` of a compound statement may give one
    (one of ``CONJUNCT_RELATIONS``, and under judgment rules 4 ``hasPart`` too:
    :func:`conjunct_relations`), and a challenge never raises a relation, so
    it is no stronger (``RELATION_STRENGTH``) than the record's own.  Returns the
    relation the verdict's convention is held to: the part's relation, the
    verdict's or -- a confirm that gives none -- the record's.
    """
    allowed = conjunct_relations(rules)
    given = v.get("conjunct_relation")
    if result != HAS_PART or not _is_compound(rec):
        if given is not None:
            problems.add(9, where, "conjunct_relation belongs to a verdict that leaves a hasPart "
                                   "of a compound statement: drop it")
        return result
    if given is None:
        own = rec.get("conjunct_relation") if action == "confirm" else None
        return own if _one_of(own, CONJUNCT_RELATIONS_PART) else result
    if not _one_of(given, allowed):
        problems.add(9, where, f"conjunct_relation is one of "
                               f"{', '.join(sorted(allowed))}")
        return result
    own = rec.get("conjunct_relation")
    if _one_of(own, CONJUNCT_RELATIONS_PART) and RELATION_STRENGTH[given] < RELATION_STRENGTH[own]:
        problems.add(9, where, f"conjunct_relation {given} is stronger than the record's {own}: "
                               f"a challenge never raises a relation; keep {own} or name a "
                               f"weaker one")
    return given


def _part_keys(rec: dict) -> list[tuple[str, dict]]:
    return [(str(part.get("location_key")), part) for part in rec.get("parts") or []
            if isinstance(part, dict)]


def _judge_bundle_confirm(problems: Problems, where: str, v: dict, rec: dict,
                          source_id: str) -> None:
    """Rules 3 (section 1.4): a coverage confirm gives each part's relation, ``part_relations``.

    One entry for each part of the record and no other, each one of
    ``BUNDLE_RELATIONS`` and no stronger than that part's own
    ``conjunct_relation`` (a challenge never raises a relation); the bundle then
    takes the weakest.  ``part_clauses`` is the claim list's and is refused: the
    parts' conjuncts are the locate document's.
    """
    if "part_clauses" in v:
        problems.add(9, where, "part_clauses is refused under the statement cut: the parts' "
                               "conjuncts are the locate document's; give part_relations")
    parts = _part_keys(rec)
    given = v.get("part_relations")
    if not isinstance(given, dict):
        problems.add(9, where, f"a coverage confirm gives each part's relation to its "
                               f"conjuncts: part_relations: {{<part's location_key>: <one of "
                               f"{', '.join(sorted(BUNDLE_RELATIONS))}>}}, one entry for each "
                               f"part")
        return
    keys = [key for key, _part in parts]
    for key in sorted(set(given) - set(keys)):
        problems.add(9, where, f"part_relations names {key[:12]!r}, which is no part of the claim")
    for key, part in parts:
        if key not in given:
            problems.add(9, where, f"part_relations gives part {key[:12]} no relation: give it "
                                   f"one, or refute the claim")
            continue
        value = given[key]
        if not _one_of(value, BUNDLE_RELATIONS):
            problems.add(9, where, f"part_relations[{key[:12]}] is {value!r}; a bundle's part "
                                   f"states its conjuncts strongly "
                                   f"({', '.join(sorted(BUNDLE_RELATIONS))}): a part that states "
                                   f"less leaves its conjunct unstated, so refute the claim")
            continue
        own = part.get("conjunct_relation")
        if not _one_of(own, CONJUNCT_RELATIONS_PART):
            raise GateBroken(f"challenge record coverage of {rec.get('item')} of {source_id} "
                             f"has a part {key[:12]} with no conjunct_relation, so no "
                             f"part_relations of it can be checked")
        if RELATION_STRENGTH[value] < RELATION_STRENGTH[own]:
            problems.add(9, where, f"part_relations[{key[:12]}] {value} is stronger than the "
                                   f"part's own {own}: a challenge never raises a relation")


def _keyed_lines(problems: Problems, where: str, field: str, given: Any, want: list[str],
                 keys: list[str], source: Any, *, ask: str) -> None:
    """A confirm's ``{<part's location_key>: <one line>}`` field: exactly the parts ``want``."""
    if not isinstance(given, dict):
        given = {}
    for key in sorted(set(given) - set(keys)):
        problems.add(9, where, f"{field} names {key[:12]!r}, which is no part of the claim")
    for key in keys:
        if key not in want:
            if key in given:
                problems.add(9, where, f"{field} gives part {key[:12]} a line it does not owe: "
                                       f"drop it")
            continue
        if key not in given or _blank(given[key]):
            problems.add(9, where, f"{field}[{key[:12]}] is owed: {ask}")
        else:
            check_line_field(problems, where, f"{field}[{key[:12]}]", given[key], source)


def _judge_bundle_readings(cfg: GateConfig, ctx: Context, problems: Problems, where: str,
                           v: dict, rec: dict, source: Any) -> None:
    """Judgment rules 4 on a bundle confirm: the readings it credits the parts with.

    Fix 6d's mechanical half, beside PD6's shape tripwire (:func:`check_pd6_shape`).
    ``part_conventions``, ``{<part's location_key>: <convention>}``: the R1 convention
    (``CLOSE_MATCH_CONVENTIONS``) by which the confirm reads each part its
    ``part_relations`` credits with ``closeMatch``, for those parts and no other, so the
    reading the confirm credits a part with is on the record when it asks whether
    another part states every conjunct.  ``part_limits``, ``{<part's location_key>:
    <one line>}``: on a passage of a shape in ``PD6_SHAPES``, for each part whose
    declaration concludes with an iff, an equivalence, a unique existence or a
    bijection (:func:`pd6_mark`) and no other, which conjunct it does not state on its
    own, and why -- else the claim stands on PD6's whole part, and is refuted.
    """
    parts = _part_keys(rec)
    keys = [key for key, _part in parts]
    relations = v.get("part_relations") if isinstance(v.get("part_relations"), dict) else {}
    readings = v.get("part_conventions")
    credited = [key for key in keys if relations.get(key) == CLOSE_MATCH]
    if readings is not None and not isinstance(readings, dict):
        problems.add(9, where, "part_conventions is {<part's location_key>: <convention>}")
        readings = {}
    readings = readings or {}
    for key in sorted(set(readings) - set(keys)):
        problems.add(9, where, f"part_conventions names {key[:12]!r}, which is no part of the "
                               f"claim")
    for key in keys:
        if key not in credited:
            if key in readings:
                problems.add(9, where, f"part_conventions belongs to a part your part_relations "
                                       f"credit with closeMatch; part {key[:12]} is none: drop "
                                       f"it")
        elif not _one_of(readings.get(key), CLOSE_MATCH_CONVENTIONS):
            problems.add(9, where, f"part_relations credit part {key[:12]} with closeMatch: "
                                   f"part_conventions[{key[:12]}] names the R1 convention you "
                                   f"read it by, one of "
                                   f"{', '.join(sorted(CLOSE_MATCH_CONVENTIONS))}, and you read "
                                   f"every other part by the same readings when you ask whether "
                                   f"one alone states every conjunct (Fix 6d)")
    if rec.get("shape") not in PD6_SHAPES:
        if v.get("part_limits") is not None:
            problems.add(9, where, f"part_limits belongs to a bundle of a statement of shape "
                                   f"{' or '.join(sorted(PD6_SHAPES))}: drop it")
        return
    tripped = [key for key, part in parts if pd6_mark(cfg, ctx, part) is not None]
    if v.get("part_limits") is not None and not isinstance(v.get("part_limits"), dict):
        problems.add(9, where, "part_limits is {<part's location_key>: <one line>}")
        return
    _keyed_lines(problems, where, "part_limits", v.get("part_limits"), tripped, keys, source,
                 ask=f"the part concludes with an iff, an equivalence, a unique existence or a "
                     f"bijection on a statement of shape {rec.get('shape')}, so say "
                     f"{PD6_SHAPE_ASK}; "
                     f"if it states every conjunct, {WHOLE_PART}, and the claim is refuted")


def _judge_bundle_refute(problems: Problems, where: str, v: dict, rec: dict,
                         ids: list[str] | None, source: Any) -> None:
    """Rules 3 (fix 1a): a coverage refute names what no part states, and why, part by part.

    ``unstated``: the conjunct ids (non-empty, distinct, the record's) that no part
    states; ``part_reasons``: one line for every part and no other, saying why no
    R1 convention reaches its conjunct -- "they state it after unfolding" is a
    confirm.  A refute that names a part's own conjunct thus says why that part
    fails (fix 1b).
    """
    unstated = v.get("unstated")
    if not isinstance(unstated, list) or not unstated or not all(
            isinstance(c, str) for c in unstated) or len(set(unstated)) != len(unstated):
        problems.add(9, where, "a coverage refute names the conjuncts no part states: unstated: "
                               "[c1, ...], non-empty and distinct")
    elif ids is not None and [c for c in unstated if c not in ids]:
        problems.add(9, where, f"unstated names {', '.join(c for c in unstated if c not in ids)}, "
                               f"which are not the passage's clause ids ({', '.join(ids)})")
    parts = _part_keys(rec)
    reasons = v.get("part_reasons")
    if not isinstance(reasons, dict):
        problems.add(9, where, "a coverage refute says, for every part, why no R1 convention "
                               "reaches its conjunct: part_reasons: {<part's location_key>: "
                               "<one line>}")
        return
    keys = [key for key, _part in parts]
    for key in sorted(set(reasons) - set(keys)):
        problems.add(9, where, f"part_reasons names {key[:12]!r}, which is no part of the claim")
    for key in keys:
        if key not in reasons:
            problems.add(9, where, f"part_reasons gives part {key[:12]} no reason: say why it "
                                   f"fails, or confirm the claim")
        elif not check_line_field(problems, where, f"part_reasons[{key[:12]}]", reasons[key],
                                  source) and _blank(reasons[key]):
            problems.add(9, where, f"part_reasons[{key[:12]}] is one line of your own")


def _judge_coverage_confirm(problems: Problems, where: str, v: dict, rec: dict,
                            ids: list[str] | None, *, rules: int) -> None:
    """Rule 9: a coverage confirm names, per part, the challenger's claims it states.

    ``part_clauses: {<location_key>: [c1, ...]}``, one entry for each part of the
    record and no other, each a non-empty list of distinct ids of the challenger's
    claim list, together naming every claim: the decision D1 verdict that raises
    a passage's status is checked against the challenger's own clauses, as a
    location's is.  Under judgment rules 2 (PD6) no part names every claim alone:
    by the challenger's own clauses such a part is one location, not a part, and
    the claim is refuted.
    """
    keys = [str(part.get("location_key")) for part in rec.get("parts") or []
            if isinstance(part, dict)]
    given = v.get("part_clauses")
    if not isinstance(given, dict):
        problems.add(9, where, "a coverage confirm names, for each part, the ids of your claims "
                               "it states: part_clauses: {<part's location_key>: [c1, ...]}, "
                               "the parts together naming every claim")
        return
    for key in sorted(set(given) - set(keys)):
        problems.add(9, where, f"part_clauses names {key[:12]!r}, which is no part of the claim")
    for key in keys:
        if key not in given:
            problems.add(9, where, f"part_clauses gives part {key[:12]} no clauses: name what "
                                   f"it states, or refute the claim")
    stated: set[str] = set()
    for key in keys:
        clauses = given.get(key)
        if key not in given:
            continue
        if not isinstance(clauses, list) or not clauses or not all(
                isinstance(c, str) for c in clauses) or len(set(clauses)) != len(clauses):
            problems.add(9, where, f"part_clauses[{key[:12]}] is a non-empty list of distinct "
                                   f"claim ids")
            continue
        if ids is not None:
            unknown = [c for c in clauses if c not in ids]
            if unknown:
                problems.add(9, where, f"part_clauses[{key[:12]}] names {', '.join(unknown)}, "
                                       f"which are not ids of your claims ({', '.join(ids)})")
                continue
        stated.update(clauses)
        if ids and rules >= RULES_PD5_PD9 and set(ids) <= set(clauses):
            problems.add(9, where, f"part_clauses[{key[:12]}] names every claim of your list "
                                   f"({', '.join(ids)}): {WHOLE_PART}, so the claim stands "
                                   f"on no such part; refute it, and judge that part's own "
                                   f"record on its own")
    if ids is not None:
        missing = [c for c in ids if c not in stated]
        if missing:
            problems.add(9, where, f"no part states {', '.join(missing)}: a claim stands only "
                                   f"when its parts together state every clause (R4); refute it")


def _judge_uphold(cfg: GateConfig, problems: Problems, where: str, v: dict, rec: dict,
                  ids: list[str] | None, source: Any) -> None:
    """Rule 9 (FM5): an uphold of an absence or an unresolved outcome says what it searched.

    ``queries`` (at least ``min_absence_queries`` distinct, each in the
    challenger's own words) and ``query_clauses``, tagged against the challenger's
    claim list as a locate document's absence is (:func:`check_query_clauses`):
    the hunt that upholds a false absence is held to the rule that refuses one.
    """
    queries = v.get("queries")
    if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
        problems.add(9, where, f"an uphold lists the queries you ran: queries: [...], at least "
                               f"{cfg.min_absence_queries} distinct, each tagged with the claims "
                               f"it targets in query_clauses")
        return
    distinct = {_normal_query(q) for q in queries if q.strip()}
    if len(distinct) < cfg.min_absence_queries:
        problems.add(9, where, f"an uphold lists {len(distinct)} distinct quer"
                               f"{'y' if len(distinct) == 1 else 'ies'}; at least "
                               f"{cfg.min_absence_queries}")
    for q in queries:
        _verbatim(problems, where, "a query", q, source)
    check_query_clauses(problems, where, {"queries": queries,
                                          "query_clauses": v.get("query_clauses")},
                        ids, absent=True, passage_kind=rec.get("passage_kind"), what="verdict")


def check_verdict(cfg: GateConfig, ctx: Context, problems: Problems, where: str, v: dict,
                  rec: dict, claims_of: dict[str, list[str] | None], source: Any) -> None:
    """Rule 9 over one verdict of a challenge document (the FM7 rule and its checks).

    Every verdict on a location, a coverage claim, an absence or an unresolved
    outcome rests on the challenger's own claim list of the passage
    (``claims[<item>]``), a refute and an uphold as much as a confirm; then each
    kind of verdict is held to what it says: :func:`_judge_location_verdict`,
    :func:`_judge_coverage_confirm`, :func:`_judge_uphold`.  Its free texts are
    held to rule 1's cap in every spelling of the source.  Under judgment rules 4 a
    downgrade's ``bridge`` (:func:`check_bridge`) and a bundle confirm's readings and
    limits (:func:`_judge_bundle_readings`) too.
    """
    target, action, item = v.get("target"), v.get("action"), v.get("item")
    cut = cfg.judgment_rules >= RULES_STATEMENT_CUT
    ids = claims_of.get(item) if not cut else clause_ids(rec, f"challenge record {item}")
    if not cut and target in CLAIMED_TARGETS and item not in claims_of:
        article = "an" if target[0] in "aeiou" else "a"
        problems.add(9, where, f"a verdict on {article} {target} rests on your own claim list of "
                               f"the passage: write it under claims first (claims: {{<item>: "
                               f"[{{id, text}}, ...]}}), then judge against it")
    if target == "location":
        _judge_location_verdict(cfg, ctx, problems, where, v, rec, ids, source)
    elif target == "coverage" and action == "confirm" and cut:
        _judge_bundle_confirm(problems, where, v, rec, source.source_id if source else "")
        if cfg.judgment_rules >= RULES_SMOKE4:
            _judge_bundle_readings(cfg, ctx, problems, where, v, rec, source)
    elif target == "coverage" and action == "refute" and cut:
        _judge_bundle_refute(problems, where, v, rec, ids, source)
    elif target == "coverage" and action == "confirm":
        _judge_coverage_confirm(problems, where, v, rec, ids, rules=cfg.judgment_rules)
    elif target in ("absence", "unresolved") and action == "uphold":
        _judge_uphold(cfg, problems, where, v, rec, ids, source)
        if cut:
            check_special_case(cfg, ctx, problems, where, v.get("special_case"), source,
                               what="verdict")
    texts = [("the verdict's note", v.get("note"))]
    if target == "location" and action == "downgrade" and v.get("relation") == CLOSE_MATCH \
            and not str(rec.get("note") or "").strip() and not _nonempty_str(v.get("note")):
        texts.append(("the note the outcomes take from the witness",
                      _challenger_note(v, rec, source, CLOSE_MATCH)))
    check_free_text(problems, where, texts, source)


def check_challenge(doc: dict, name: str, cfg: GateConfig, ctx: Context, source_id: str,
                    manifest_doc: dict, problems: Problems, unresolved: bool) -> dict:
    """Rules 1, 3 and 6 for a ``locate-challenge`` document."""
    stats = {"verdicts": 0, "unresolved": unresolved}
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        problems.add(1, name, "verdicts is a list")
        return stats
    if unresolved:
        if verdicts:
            problems.add(1, name, "an unresolved challenge carries no verdicts")
        return stats
    records: dict[tuple, dict] = {}
    for r in manifest_doc.get("records") or []:
        if isinstance(r, dict):
            lkey = r.get("location_key") if r.get("target") == "location" else None
            records[(r.get("item"), r.get("target"), lkey)] = r
    stats["verdicts"] = len(verdicts)
    judged: set[tuple] = set()
    source = ctx.source(source_id)
    claims_of = _challenge_claims(cfg, problems, name, doc, records, source)
    for i, v in enumerate(verdicts):
        where = f"{name}: verdicts[{i}]"
        if not isinstance(v, dict):
            problems.add(1, where, "a verdict is an object")
            continue
        target, action = v.get("target"), v.get("action")
        if not _one_of(target, CHALLENGE_TARGETS):
            problems.add(1, where, f"target {target!r} is not a challenge target")
            continue
        if not isinstance(v.get("item"), str) or (
                target == "location" and not isinstance(v.get("location_key"), str)):
            problems.add(1, where, "a verdict names its record by item"
                                   + (" and location_key" if target == "location" else "")
                                   + " (strings)")
            continue
        if not _one_of(action, CHALLENGE_COMBOS[target]):
            problems.add(1, where, f"action {action!r} does not apply to a {target} "
                                   f"({'/'.join(sorted(CHALLENGE_COMBOS[target]))})")
        key = (v.get("item"), target, v.get("location_key") if target == "location" else None)
        if key not in records:
            problems.add(1, where, f"{v.get('item')!r} {target}"
                                   + (f" {str(v.get('location_key'))[:12]}" if target == "location" else "")
                                   + " is not one of the records this task judges")
            continue
        if key in judged:
            problems.add(1, where, "this record is judged twice")
        judged.add(key)
        if not _nonempty_str(v.get("witness")):
            problems.add(1, where, "a verdict carries a witness")
        if action == "downgrade":
            new, old = v.get("relation"), records[key].get("relation")
            if not _one_of(new, LOCATE_RELATIONS):
                problems.add(1, where, "a downgrade names the weaker relation")
            elif not _one_of(old, LOCATE_RELATIONS):
                raise GateBroken(f"challenge record {v.get('item')} of {source_id} carries no "
                                 f"relation, so no downgrade of it can be checked")
            elif RELATION_STRENGTH[new] <= RELATION_STRENGTH[old]:
                problems.add(1, where, f"a downgrade names a relation weaker than the record's "
                                       f"{old}; {new} is not")
            elif (new == CLOSE_MATCH and source is not None
                  and not str(records[key].get("note") or "").strip()):
                # the note the outcomes give the downgraded location (locate/outcomes.py
                # takes the location's own note first; a location record of a challenge
                # manifest carries none, so this is checked for every such downgrade)
                note = lr.downgrade_note(v, source.layer)
                if not note and _nonempty_str(v.get("witness")):
                    # every sentence of the witness is an R-SPECIFIC recall miss, which is
                    # never a note: the export would refuse a closeMatch with none
                    problems.add(1, where, "a downgrade to closeMatch exports its note, else "
                                           "a sentence of its witness that is no "
                                           f"{lr.SPECIFIC_MISS_MARK!r} recall miss, as the "
                                           "location's note: this one has neither")
                try:
                    lr.check_note(str(v.get("item")), note, source.layer)
                except lr.RecordError as exc:
                    problems.add(1, where, _own_words(exc, where, str(v.get("item")))
                                 + " (a downgrade to closeMatch exports its note, else a "
                                   "sentence of its witness, as the location's note)")
        if cfg.judgment_rules:
            check_verdict(cfg, ctx, problems, where, v, records[key], claims_of, source)
        if action == "overturn" and target == "prominence":
            new, old = v.get("prominence"), records[key].get("prominence")
            if not _one_of(new, PROMINENCE):
                problems.add(1, where, "a prominence overturn names the new prominence")
            elif new == old:
                # the outcomes would mark the passage downgraded and change nothing: a
                # subject passage would keep its label and the article's QID
                problems.add(1, where, f"a prominence overturn names a prominence other than "
                                       f"the record's {old}; to keep {old}, uphold")
        if action == "overturn" and target in ("absence", "unresolved", "scope"):
            locations = _check_locations(cfg, ctx, problems, where, v.get("locations"))
            if locations is None:
                continue                      # over the cap: refused, nothing more to read
            if target != "scope" and not [loc for loc in locations
                                          if loc.get("relation") != RELATED_MATCH]:
                problems.add(1, where, "overturning an absence or an unresolved outcome gives a "
                                       "location whose relation counts")
            rec = records[key]
            if cfg.judgment_rules and locations:
                if target == "scope" and v.get("item") not in claims_of:
                    problems.add(9, where, "an overturn's locations name the claims they state: "
                                           "write the passage's claim list under claims first")
                for j, loc in enumerate(locations):
                    check_judgment(cfg, ctx, problems, f"{where}.locations[{j}]", loc,
                                   ids=claims_of.get(v.get("item")),
                                   passage_kind=rec.get("passage_kind"), source=source, row=rec)
                check_free_text(problems, where, [("a location's note", loc.get("note"))
                                                  for loc in locations], source)
            span = _span(rec.get("span"), len(source.layer)) if source is not None else None
            if span is not None and locations:
                try:
                    quote = capped_quote(source.layer, span)
                except lr.RecordError as exc:
                    raise GateBroken(f"challenge record {v.get('item')} of {source_id} cannot be "
                                     f"quoted by the export ({exc})") from None
                check_records(cfg, problems, where, source_id=source_id, layer=source.layer,
                              span=span, quote=quote, item=str(v.get("item")),
                              passage_kind=rec.get("passage_kind"), outcome="located",
                              locations=locations)
    # one document may not confirm a coverage claim and refute or downgrade one of the
    # parts it names: the claim then names a location that is no longer a hasPart one
    changed = {v.get("location_key") for v in verdicts if isinstance(v, dict)
               and v.get("target") == "location" and v.get("action") in ("refute", "downgrade")}
    #: the conjunct relations this document's location verdicts give (judgment rules 3)
    weakened = {str(v.get("location_key")): v.get("conjunct_relation") for v in verdicts
                if isinstance(v, dict) and v.get("target") == "location"
                and v.get("conjunct_relation") is not None}
    #: the clause ids this document's location verdicts say each record states (rules 3)
    stated = {str(v.get("location_key")): _clauses_of(v) for v in verdicts
              if isinstance(v, dict) and v.get("target") == "location"
              and _clauses_of(v) is not None}
    for i, v in enumerate(verdicts):
        if not (isinstance(v, dict) and v.get("target") == "coverage"
                and v.get("action") == "confirm"):
            continue
        rec = records.get((v.get("item"), "coverage", None)) or {}
        hit = sorted(str(part.get("location_key")) for part in rec.get("parts") or []
                     if isinstance(part, dict) and part.get("location_key") in changed)
        if hit:
            problems.add(1, f"{name}: verdicts[{i}]",
                         f"confirms the coverage claim of {v.get('item')} and refutes or "
                         f"downgrades its part(s) {', '.join(k[:12] for k in hit)}: a claim "
                         f"stands only while every part is a hasPart location; refute it")
        if cfg.judgment_rules >= RULES_STATEMENT_CUT and isinstance(v.get("part_relations"), dict):
            # a part's relation in the confirm is no stronger than the conjunct_relation the
            # same document gives that part's own record (docs/proposals/statement-cut.md 1.4)
            for key, given in sorted(weakened.items()):
                said = v["part_relations"].get(key)
                if _one_of(said, BUNDLE_RELATIONS) and (
                        not _one_of(given, BUNDLE_RELATIONS)
                        or RELATION_STRENGTH[said] < RELATION_STRENGTH[given]):
                    problems.add(9, f"{name}: verdicts[{i}]",
                                 f"part_relations[{key[:12]}] {said} is stronger than the "
                                 f"conjunct_relation {given} this document gives that part's "
                                 f"record: give the part the weaker relation, or refute the "
                                 f"claim when it is no strong one")
        if cfg.judgment_rules >= RULES_STATEMENT_CUT:
            # PD6, which rules 2 held through a confirm's part_clauses: a part this
            # document's own verdict says states every conjunct is one location, no part
            ids = claims_of.get(v.get("item")) or []
            for key, _part in _part_keys(rec):
                named = stated.get(key)
                if ids and named is not None and set(ids) <= named:
                    problems.add(9, f"{name}: verdicts[{i}]",
                                 f"your verdict on part {key[:12]} names every conjunct "
                                 f"({', '.join(ids)}): {WHOLE_PART}, so the claim stands on "
                                 f"no such part; refute it, and judge that part's own record "
                                 f"on its own")
    unowed: set[tuple] = set()
    if cfg.judgment_rules >= RULES_STATEMENT_CUT:
        unowed = check_conditional(problems, name, verdicts, records)
        check_fills(cfg, ctx, problems, name, doc, verdicts, records, source, source_id)
    owed = [k for k in records if k not in judged and k not in unowed]
    # what the OK line says a reviewer need not count again (:func:`coverage_line`)
    stats.update(records_owed=len(records) - len(unowed), conditional_not_owed=len(unowed))
    if owed:
        problems.add(1, name, f"{len(owed)} record(s) of this task have no verdict "
                              f"({', '.join(str(k[0]) + ' ' + str(k[1]) for k in owed[:3])})")
    return stats


def check_conditional(problems: Problems, name: str, verdicts: list,
                      records: dict[tuple, dict]) -> set[tuple]:
    """Rules 3 (anomaly A3): the conditional records of a prominence sample.

    The challenge plan puts a sampled ``supporting`` passage's unrequired records
    in the task of its prominence record, each ``conditional: "prominence"``.  A
    prominence overturn of the passage to ``subject`` or ``central`` makes them
    refuted-by-default or hunted records, so the same document judges every one
    of them (a record left out is owed, as any record is); without such an
    overturn none is owed, and a verdict on one is refused.  Returns the keys of
    the conditional records this document does not owe.
    """
    reopened = {v.get("item") for v in verdicts if isinstance(v, dict)
                and v.get("target") == "prominence" and v.get("action") == "overturn"
                and v.get("prominence") in REFUTE_BY_DEFAULT_PROMINENCE}
    conditional = {k for k, rec in records.items() if rec.get("conditional")}
    for i, v in enumerate(verdicts):
        if not isinstance(v, dict):
            continue
        key = (v.get("item"), v.get("target"),
               v.get("location_key") if v.get("target") == "location" else None)
        if key in conditional and v.get("item") not in reopened:
            problems.add(1, f"{name}: verdicts[{i}]",
                         f"{v.get('item')} {v.get('target')} is a conditional record: it is "
                         f"judged only beside a prominence overturn of its passage to subject "
                         f"or central in this document, which none is")
    return {k for k in conditional if k[0] not in reopened}


def _verdict_by_key(verdicts: list) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for v in verdicts:
        if isinstance(v, dict):
            key = (v.get("item"), v.get("target"),
                   v.get("location_key") if v.get("target") == "location" else None)
            out.setdefault(key, v)
    return out


def emptied_passages(verdicts: list, records: dict[tuple, dict]) -> dict[str, dict]:
    """Rules 3 (anomalies A1, A2): located passages these verdicts leave with no counting location.

    ``{item: a record of the passage}``: a ``located`` passage whose judged
    location records -- a confirm keeps the relation, a downgrade leaves the one
    it names, a refute removes it -- and whose other locations of the run (each
    location record's ``passage_locations``, judged elsewhere by no task) together
    hold no relation but ``relatedMatch``.
    """
    by_key = _verdict_by_key(verdicts)
    left: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict] = {}
    for key, rec in records.items():
        item, target, lkey = key
        if target != "location" or rec.get("outcome") != "located":
            continue
        rows.setdefault(item, rec)
        mine = left.setdefault(item, {})
        v = by_key.get(key)
        relation = rec.get("relation")
        if v is not None and v.get("action") == "refute":
            relation = None
        elif v is not None and v.get("action") == "downgrade":
            relation = v.get("relation")
        mine[str(lkey)] = relation
        for other in rec.get("passage_locations") or []:
            if isinstance(other, dict) and str(other.get("location_key")) not in mine \
                    and (item, "location", other.get("location_key")) not in records:
                mine[str(other.get("location_key"))] = other.get("relation")
    return {item: rows[item] for item, mine in left.items()
            if not any(_one_of(r, LOCATE_RELATIONS) and r != RELATED_MATCH
                       for r in mine.values())}


def check_fills(cfg: GateConfig, ctx: Context, problems: Problems, name: str, doc: dict,
                verdicts: list, records: dict[tuple, dict], source: Any, source_id: str) -> None:
    """Rules 3 (anomalies A1, A2): what replaces a located passage's last counting location.

    A document whose verdicts leave a ``located`` passage with no counting
    location (:func:`emptied_passages`) gives ``fills[<item>]``: ``{outcome:
    absent, receipt}``, held to an absence's checks (the queries, a query of its
    own per clause, the rejections, the special case, the export's receipt), or
    ``{outcome: located, locations}``, held to an overturn's.  ``fills`` on any
    other item is refused: the passage keeps what it had.
    """
    emptied = emptied_passages(verdicts, records)
    fills = doc.get("fills", {})
    if not isinstance(fills, dict):
        problems.add(9, name, "fills is {<item>: {outcome: absent, receipt} | {outcome: located, "
                              "locations}}")
        fills = {}
    for item in sorted(emptied):
        if item not in fills:
            problems.add(9, name, f"your verdicts leave {item}, a located passage, with no "
                                  f"location whose relation counts: give fills[{item}], an "
                                  f"absence with its hunt ({{outcome: absent, receipt}}) or the "
                                  f"location that replaces them ({{outcome: located, "
                                  f"locations}})")
    for item, fill in sorted(fills.items()):
        where = f"{name}: fills[{item}]"
        if item not in emptied:
            problems.add(9, where, "a fill replaces what your verdicts take from a located "
                                   "passage, and they leave this one a location that counts (or "
                                   "it is no located passage of this task): drop it")
            continue
        rec = emptied[item]
        ids = clause_ids(rec, f"challenge record {item}")
        if not isinstance(fill, dict) or fill.get("outcome") not in ("absent", "located") \
                or set(fill) != {"outcome", "receipt" if fill.get("outcome") == "absent"
                                 else "locations"}:
            problems.add(9, where, "a fill is {outcome: absent, receipt} or {outcome: located, "
                                   "locations}")
            continue
        span = _span(rec.get("span"), len(source.layer)) if source is not None else None
        if span is None:
            raise GateBroken(f"challenge record {item} of {source_id} has no span on the layer")
        try:
            quote = capped_quote(source.layer, span)
        except lr.RecordError as exc:
            raise GateBroken(f"challenge record {item} of {source_id} cannot be quoted by the "
                             f"export ({exc})") from None
        kind = rec.get("passage_kind")
        if fill["outcome"] == "absent":
            receipt = _check_receipt(cfg, ctx, problems, where, fill["receipt"])
            distinct = {_normal_query(q) for q in receipt["queries"] if q.strip()}
            if len(distinct) < cfg.min_absence_queries:
                problems.add(5, where, f"an absence lists {len(distinct)} distinct quer"
                                       f"{'y' if len(distinct) == 1 else 'ies'}; at least "
                                       f"{cfg.min_absence_queries}")
            check_query_clauses(problems, where, fill["receipt"], ids, absent=True,
                                passage_kind=kind)
            check_rejections(problems, where, receipt)
            # rule 4's reading, as a locate document's absence is held to it (check_locate):
            # an accepted candidate is one of the passage's counting locations, and an
            # absent fill leaves the passage none
            for cand in receipt["candidates"]:
                if cand.get("verdict") == "accepted":
                    problems.add(1, where, f"accepted candidate {cand.get('library')}:"
                                           f"{cand.get('declaration')} is not one of this "
                                           f"passage's locations with a relation that counts: "
                                           f"an absent fill leaves it none, so locate it "
                                           f"(outcome: located), or reject it with a reason")
            check_special_case(cfg, ctx, problems, where,
                               fill["receipt"].get("special_case")
                               if isinstance(fill["receipt"], dict) else None,
                               source, what="receipt")
            check_free_text(problems, where, [
                *(("a query", q) for q in receipt["queries"]),
                *(("a candidate's reason", c.get("reason")) for c in receipt["candidates"])],
                source)
            check_records(cfg, problems, where, source_id=source_id, layer=source.layer,
                          span=span, quote=quote, item=item, passage_kind=kind,
                          outcome="absent", locations=[], receipt=receipt)
            continue
        locations = _check_locations(cfg, ctx, problems, where, fill["locations"])
        if locations is None:
            continue
        if not [loc for loc in locations if loc.get("relation") != RELATED_MATCH]:
            problems.add(1, where, "a located fill gives a location whose relation counts")
        for j, loc in enumerate(locations):
            check_judgment(cfg, ctx, problems, f"{where}.locations[{j}]", loc, ids=ids,
                           passage_kind=kind, source=source, row=rec)
        check_free_text(problems, where, [("a location's note", loc.get("note"))
                                          for loc in locations], source)
        if locations:
            check_records(cfg, problems, where, source_id=source_id, layer=source.layer,
                          span=span, quote=quote, item=item, passage_kind=kind,
                          outcome="located", locations=locations)


CHECKS = {LOCATE_INVENTORY: check_inventory, LOCATE: check_locate,
          LOCATE_CHALLENGE: check_challenge}


def read_regular(path: pathlib.Path, limit: int, name: str, problems: Problems) -> bytes | None:
    """A document's bytes, read only from a REGULAR file and at most ``limit + 1`` of them.

    A committed symlink would be followed to any file of the host -- what merges
    is then a link, not the bytes the gate checked -- and a device such as
    ``/dev/zero`` would be read without end; both are refused before a byte is
    read, and the file is opened ``O_NOFOLLOW`` so it cannot be swapped between
    the check and the read.
    """
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            problems.add(1, name, "is not a regular file (a symlink, device or fifo is refused)")
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                problems.add(1, name, "is not a regular file")
                return None
            return fh.read(limit + 1)
    except OSError as exc:
        problems.add(1, name, f"cannot be read ({exc})")
        return None


def validate_document(path: pathlib.Path, name: str, task_id: str, cfg: GateConfig,
                      ctx: Context, problems: Problems) -> dict:
    """Every rule but 7 over one document; returns its counts for the receipt and the log."""
    stats: dict[str, Any] = {"task_id": task_id, "mode": cfg.mode}
    raw = read_regular(path, cfg.max_document_bytes, name, problems)
    if raw is None:
        return stats
    if len(raw) > cfg.max_document_bytes:
        problems.add(1, name, f"{len(raw)} bytes; a document is at most {cfg.max_document_bytes}")
        return stats
    try:
        doc = load_json_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        problems.add(1, name, f"not valid, unambiguous JSON ({exc})")
        return stats
    if not isinstance(doc, dict):
        problems.add(1, name, "a document is a JSON object")
        return stats
    manifest = ctx.manifest(task_id)
    if manifest is None:
        problems.add(7, name, f"task {task_id!r} has no manifest in this run's gate: not a task "
                              f"of run {cfg.run_id}")
        return stats
    if manifest.get("mode") not in (None, cfg.mode) or manifest.get("run_id") not in (None, cfg.run_id):
        raise GateBroken(f"task manifest {task_id} is for {manifest.get('mode')}/"
                         f"{manifest.get('run_id')}, not this gate's {cfg.mode}/{cfg.run_id}")
    source_id = _header(doc, name, cfg, task_id, manifest, problems)
    unresolved = _unresolved_block(doc, name, problems, cfg, task_id,
                                   check_counter=ctx.check_counter)
    stats.update({"source_id": source_id, "unresolved": unresolved,
                  "attempt_capped": bool(unresolved and isinstance(doc.get("unresolved"), dict)
                                         and doc["unresolved"].get("by") == "attempt_cap")})
    if not source_id:
        return stats
    if path.name != f"{source_id}.json":
        problems.add(1, name, f"holds source {source_id}'s document; that lives at "
                              f"findings/{task_id}/{source_id}.json (one document per source, "
                              f"named for it)")
        return stats
    manifest_doc = next((d for d in manifest.get("documents") or []
                         if isinstance(d, dict) and d.get("source_id") == source_id), None)
    if manifest_doc is None:
        return stats
    stats.update(CHECKS[cfg.mode](doc, name, cfg, ctx, source_id, manifest_doc, problems, unresolved))
    return stats


# --------------------------------------------------------------------------- #
# rule 7: the change set, and whose task it is
# --------------------------------------------------------------------------- #

#: where tend's orchestrator.sqlite names a worktree's task: verify_findings' lookup, which
#: the informalize gate shares (docs/proposals/informalize.md section 8.3 rule 1)
IMMUTABLE_NOTE = getattr(vf, "IMMUTABLE_NOTE", "")


def task_from_orchestrator(db_path: pathlib.Path | None, worktree: pathlib.Path) -> tuple[str, str]:
    """``(task id, how)`` from tend's ``orchestrator.sqlite``: ``verify_findings.task_from_orchestrator``."""
    return vf.task_from_orchestrator(db_path, worktree)


def _classify(path: str) -> tuple[str, str, str] | None:
    """``('findings', task, source)`` / ``('task', task, '')`` / None for anything else.

    A task file is ``tasks/<task-id>.yaml``, the name the planner writes; a
    ``tasks/<task-id>.yml`` beside it would be a second task file, not this one.
    """
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "findings" and parts[2].endswith(".json") \
            and _safe_name(parts[1]) and _safe_name(parts[2][:-len(".json")]):
        return "findings", parts[1], parts[2][:-len(".json")]
    if len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith(".yaml"):
        stem = parts[1][:-len(".yaml")]
        if _safe_name(stem):
            return "task", stem, ""
    return None


#: a task file's ``status:`` line, the one line a contribution changes
_STATUS_LINE = re.compile(r"status:")
#: what that line may hold: one plain status word (``status: complete``), never a
#: flow mapping, a list, a tag or anything else YAML would read as more than a word
_STATUS_WORD = re.compile(r"status: [a-z][a-z_-]*")


def _task_file_change(before: str | None, path: pathlib.Path) -> str:
    """Why a task file's change is more than its status, or '' when it is only that.

    Every line but the ``status:`` one must read as at the merge base: the id,
    the dependencies and the priority are the plan's, and a contribution that
    rewrote them would land them on the merge target, where tend reads tasks.
    The status line itself holds one status word (:data:`_STATUS_WORD`), and
    there is one: a ``status: {id: ..., priority: 99}`` flow mapping, an
    unparseable ``status: [`` or a ``!!python/object`` tag would otherwise land
    in the same file, by a line the comparison below leaves out.
    """
    if before is None:
        return "cannot be read at the merge base"
    try:
        raw = path.read_bytes()[:MAX_TASK_FILE_BYTES + 1]
    except OSError as exc:
        return f"cannot be read ({exc})"
    if len(raw) > MAX_TASK_FILE_BYTES:
        return f"is over {MAX_TASK_FILE_BYTES} bytes"
    after = raw.decode("utf-8", errors="replace")
    statuses = [line.rstrip() for line in after.splitlines() if _STATUS_LINE.match(line)]
    if len(statuses) > 1:
        return (f"has {len(statuses)} status lines; a contribution sets the one `status:` "
                f"line and leaves the plan's lines as they are")
    bad = next((line for line in statuses if not _STATUS_WORD.fullmatch(line)), None)
    if bad is not None:
        return (f"sets its status line to something other than one status word "
                f"({bad[:80]!r}); a contribution writes `status: complete`")

    def rest(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if not _STATUS_LINE.match(line)]

    old, new = rest(before), rest(after)
    if old == new:
        return ""
    changed = next((n for o, n in zip(old, new) if o != n), None)
    if changed is None:
        changed = (new[len(old)] if len(new) > len(old) else f"(removed) {old[len(new)]}")
    return (f"changes more than its status line (first other change: {changed[:80]!r}); a "
            f"contribution sets `status:` and leaves the plan's lines as they are")


def check_changed(entries: dict[str, str], ctx: Context, *, task_id: str = "",
                  allow_empty: bool = False, root: pathlib.Path | None = None,
                  pre_merge: bool = False,
                  at_base: Callable[[str], str | None] | None = None) -> Problems:
    """Rule 7: only the task's own documents and task file, nothing removed.

    With no task id the single-stem rule of ``verify_findings.check_changed``
    stands in: one worker's change set is one task's.  Only ``pre_merge`` (the
    batched pre-merge command, ``--pre-merge``) admits several tasks, and then
    only when every one of their documents is an addition.  With ``root`` (the
    worktree) every path the change set writes must be a regular file there,
    and a task that wrote documents must hold one for every source its manifest
    names (rule 1: one document per source of the task).  The task file is the
    task's own ``tasks/<task-id>.yaml``, already on the merge target, and with
    ``at_base`` (its text at the merge base) a change to it is its status line
    and nothing else.
    """
    problems = Problems()
    written: dict[str, list[tuple[str, str]]] = {}
    #: the tasks whose task file the change set holds, accepted or not: a task
    #: file refused below has its own line, and "has no tasks/<id>.yaml" would
    #: be a second, false one
    tasks: set[str] = set()
    seen: set[str] = set()
    for path in sorted(entries):
        status = entries[path]
        kind = _classify(path)
        if kind is None:
            problems.add(7, path, "is outside findings/<task-id>/<source-id>.json and "
                                  "tasks/<task-id>.yaml")
            continue
        what, stem, source = kind
        seen.add(stem)
        if what == "task":
            tasks.add(stem)
        if status in ("D", "R"):
            problems.add(7, path, "was deleted or renamed away; a contribution writes its own "
                                  "documents and removes nothing")
            continue
        if task_id and stem != task_id:
            problems.add(7, path, f"belongs to task {stem!r}, not to this worker's {task_id!r}")
            continue
        if root is not None:
            try:
                regular = stat.S_ISREG(os.lstat(root / path).st_mode)
            except OSError:
                regular = False
            if not regular:
                problems.add(7, path, "is not a regular file in the worktree (a symlink is "
                                      "refused: what merges must be the bytes the gate read)")
                continue
        if what == "task":
            if status == "A":
                problems.add(7, path, "adds a task file: a task's file is on the merge target "
                                      "already, and a contribution only sets its status")
                continue
            if root is not None and at_base is not None:
                why = _task_file_change(at_base(path), root / path)
                if why:
                    problems.add(7, path, why)
            continue
        written.setdefault(stem, []).append((source, status))
        manifest = ctx.manifest(stem)
        if manifest is None:
            problems.add(7, path, f"task {stem!r} is not a task of run {ctx.cfg.run_id}")
            continue
        named = {d.get("source_id") for d in manifest.get("documents") or [] if isinstance(d, dict)}
        if source not in named:
            problems.add(7, path, f"source {source} is not one of task {stem}'s documents")
    for stem in sorted(set(written) - tasks):
        problems.add(7, f"findings/{stem}/", f"has no tasks/{stem}.yaml in the same change set")
    for stem in sorted(written):
        lacking = _lacking_documents(ctx, stem, {src for src, _st in written[stem]},
                                     root / "findings" / stem if root is not None else None)
        if lacking:
            problems.add(1, f"findings/{stem}/", f"the task's documents for {', '.join(lacking)} "
                                                 f"are missing: one document per source of the "
                                                 f"task manifest")
    if not task_id and len(seen) > 1 and not pre_merge:
        problems.add(7, "change set", f"belongs to {len(seen)} tasks ({', '.join(sorted(seen))}) "
                                      f"and no task id says which is this worker's: one "
                                      f"worker's contribution is one task's (only the batched "
                                      f"pre-merge check, --pre-merge, admits several)")
    elif not task_id and len(seen) > 1:
        landed = sorted(stem for stem, docs in written.items() if any(s != "A" for _src, s in docs))
        landed += sorted(stem for stem in seen - set(written))
        if landed:
            problems.add(7, "change set", f"belongs to {len(seen)} tasks ({', '.join(sorted(seen))}) "
                                          f"and no task id says which is this worker's; "
                                          f"{', '.join(sorted(set(landed)))} added no new document — "
                                          f"pass --task-id <id> or export TEND_TASK_ID")
    if not written and not allow_empty:
        problems.add(7, "change set", "no document was written")
    return problems


def _lacking_documents(ctx: Context, task_id: str, have: set[str],
                       directory: pathlib.Path | None) -> list[str]:
    """The sources task ``task_id``'s manifest names that have no document.

    ``have`` is what this call validates; ``directory`` (the task's findings
    directory, when known) supplies documents an earlier commit of the same task
    already holds.
    """
    manifest = ctx.manifest(task_id)
    if manifest is None:
        return []
    named = sorted({str(d.get("source_id")) for d in manifest.get("documents") or []
                    if isinstance(d, dict) and d.get("source_id")})
    return [sid for sid in named if sid not in have
            and not (directory is not None and (directory / f"{sid}.json").is_file())]


#: where a task id came from (:func:`ownership_line`, the gate log's ``task_id_from``)
TASK_ID_FROM_FLAG = "--task-id"
TASK_ID_FROM_ENV = "$TEND_TASK_ID"


def ownership_line(task_id: str, how: str) -> str:
    """The gate's last line: which rule judged the change set, and why when it is the weak one.

    ``how`` is where the task id came from (``--task-id``, ``$TEND_TASK_ID``,
    ``orchestrator.sqlite``, which may carry :data:`IMMUTABLE_NOTE`), or, with
    no task id, why the lookup found none -- said on the line, never dropped.
    An agent runs with ``TEND_TASK_ID`` in its environment, so a worker's or a
    reviewer's own call names its task that way; the line says so, and a
    task id on it is not by itself proof that a validation command's
    ``orchestrator.sqlite`` lookup works (field watch item W1).
    """
    if task_id and how.startswith("orchestrator.sqlite"):
        note = how[len("orchestrator.sqlite"):].strip().strip("()")
        return (f"ownership checked against task {task_id} (task id recovered from "
                f"orchestrator.sqlite: an assumed schema, verify on the host"
                + (f"; {note}" if note else "") + ")")
    if task_id and how in (TASK_ID_FROM_FLAG, TASK_ID_FROM_ENV):
        return f"ownership checked against task {task_id} (task id from {how})"
    if task_id:
        return f"ownership checked against task {task_id}"
    return ("ownership: single-stem rule (no task id); pass --task-id or export TEND_TASK_ID "
            "for the full check" + (f" ({how})" if how else ""))


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def _documents_in(directory: pathlib.Path) -> list[tuple[pathlib.Path, str, str]]:
    """``[(path, display name, task id)]`` for ``<directory>/<task>/<source>.json``."""
    out = []
    if not directory.is_dir():
        return out
    for task_dir in sorted(directory.iterdir()):
        if task_dir.is_symlink() or not task_dir.is_dir() or not _safe_name(task_dir.name):
            continue
        for path in sorted(task_dir.glob("*.json")):
            if path.name in NON_DOCUMENT_FILES or path.name.startswith("."):
                continue
            out.append((path, f"{task_dir.name}/{path.name}", task_dir.name))
    return out


def _strays(directory: pathlib.Path) -> list[str]:
    """JSON under ``directory`` that is not a ``<task>/<source>.json`` document.

    A locate corpus is exactly that layout; the receipt and every reader of a
    harvested corpus count JSON at any depth, so a file the gate never read
    would land as counted and "gated".  The listing is the corpus readers' own:
    hidden components skipped, the corpus's own receipt and copies excluded.
    """
    out: list[str] = []
    if not directory.is_dir():
        return out
    for here, dirs, files in os.walk(directory):
        rel_dir = pathlib.Path(here).relative_to(directory)
        depth = len(rel_dir.parts)
        for d in sorted(dirs):
            if not d.startswith(".") and (pathlib.Path(here) / d).is_symlink():
                out.append(f"{(rel_dir / d).as_posix()}/")
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if not f.endswith(".json") or f.startswith(".") or f in NON_DOCUMENT_FILES:
                continue
            if depth != 1 or not _safe_name(rel_dir.parts[0]):
                out.append((rel_dir / f).as_posix())
    return sorted(out)


def write_log(cfg: GateConfig, record: dict) -> None:
    """Append one line to the gate log; any failure to write is silent by design."""
    if cfg.log_dir is None:
        return
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(cfg.log_dir / "gate.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            try:
                import fcntl  # noqa: PLC0415 - POSIX only
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        return


def incomplete_message() -> str:
    return ("the gate directory is incomplete: " + "; ".join(IMPORT_ERRORS)
            + " (cf plan-locate stages verify_locate.py with locate_text.py, verify_findings.py, "
              "locate_records.py and vendor/ beside it)")


def _merge_base_reader(root: pathlib.Path, base: str) -> Callable[[str], str | None]:
    """``path -> its text at the merge base of base and HEAD`` (None when it has none).

    ``git cat-file blob`` runs no filter and no textconv; the git boundary is
    ``verify_findings._git``'s.  ``changed_entries`` has refused a history with
    no single merge base before this is asked.
    """
    code, out = vf._git(["merge-base", base, "HEAD"], root)
    merge_base = out.strip() if code == 0 else ""

    def read(path: str) -> str | None:
        if not merge_base:
            return None
        code, text = vf._git(["cat-file", "blob", f"{merge_base}:{path}"], root)
        return text if code == 0 else None

    return read


def caller_of(*, changed_files: bool, pre_merge: bool, findings: bool) -> str:
    """Which call a gate run is, as its log line's ``caller`` says it.

    ``validation`` is tend's validation command (a change set, ``--changed-files``)
    and the only call the log's readers count as an attempt; ``pre_merge`` is its
    pre-merge command (``--changed-files --pre-merge``); ``findings_check`` is a
    worker's, a reviewer's or the bridge's own check of named documents
    (``--findings``); ``directory`` is a directory of documents (a harvested
    corpus, a hand check).  The command line refuses every combination that
    would blur two of them (:func:`_main`).
    """
    if changed_files:
        return CALLER_PRE_MERGE if pre_merge else CALLER_VALIDATION
    return CALLER_FINDINGS if findings else CALLER_DIRECTORY


def run_gate(cfg: GateConfig, *, root: pathlib.Path, findings: list[pathlib.Path] | None = None,
             findings_dir: str = DEFAULT_FINDINGS_DIR, changed_files: bool = False,
             base: str = "main", task_id: str = "", allow_empty: bool = False,
             check_vendor: bool = True, task_id_from: str | None = None,
             pre_merge: bool = False) -> dict:
    """Run the gate in-process; ``{exit, problems, broken, documents, ownership, ...}``.

    ``cf harvest`` calls this over a landed corpus; ``main`` is the CLI around it.
    ``task_id_from`` says where a given ``task_id`` came from (``--task-id``,
    the default, or ``$TEND_TASK_ID``).  Raises nothing: an infrastructure
    failure is ``exit: 3`` with ``broken`` set.  Every call appends one line to
    the gate log with its verdict, the ``ownership`` line, ``task_id_from``,
    which call it was (``caller``, :func:`caller_of`), whether it judged a
    change set (``changed_files``) and whether it was tend's batched pre-merge
    check (``pre_merge``, ``--pre-merge``): the one call in which a change set
    of several tasks' new documents is admitted without a task id (rule 7), and
    a re-check of contributions already validated, which the log's readers
    count apart from the validation calls.  A line written before either field
    has none, and a missing ``pre_merge`` reads as false.
    """
    result: dict[str, Any] = {"exit": EXIT_OK, "problems": [], "broken": [], "documents": 0,
                              "by_rule": {}, "stats": [], "ownership": "", "task_id": task_id,
                              "mode": cfg.mode, "run_id": cfg.run_id}
    if IMPORT_ERRORS:
        result.update(exit=EXIT_GATE_BROKEN, broken=[incomplete_message()])
        return result
    ctx = Context(cfg, check_counter=changed_files)
    problems = Problems()
    how = (task_id_from or TASK_ID_FROM_FLAG) if task_id else ""
    try:
        if check_vendor:
            verify_vendor(cfg)
        if changed_files and not task_id:
            task_id, how = task_from_orchestrator(cfg.orchestrator_db, root)
            if not task_id:
                result["ownership_note"] = how
        docs: list[tuple[pathlib.Path, str, str]] = []
        if changed_files:
            entries, errs = vf.changed_entries(root, base)
            if errs:
                raise GateBroken("; ".join(errs))
            changed = check_changed(entries, ctx, task_id=task_id, allow_empty=allow_empty,
                                    root=root, pre_merge=pre_merge,
                                    at_base=_merge_base_reader(root, base))
            problems.extend(changed)
            if not findings:
                for path, status in sorted(entries.items()):
                    kind = _classify(path)
                    if kind and kind[0] == "findings" and status not in ("D", "R") \
                            and (not task_id or kind[1] == task_id):
                        docs.append((root / path, path, kind[1]))
            result["ownership"] = ownership_line(task_id, how)
        if findings:
            for f in findings:
                f = pathlib.Path(f)
                docs.append((f, f"{f.parent.name}/{f.name}", f.parent.name))
        elif not changed_files:
            docs = _documents_in(root / findings_dir)
            if not docs:
                problems.add(1, str(root / findings_dir), "the gate found no locate documents to "
                                                         "validate (an empty set is not a clean one)")
            for stray in _strays(root / findings_dir):
                problems.add(7, stray, "is not a <task-id>/<source-id>.json document; a locate "
                                       "corpus holds nothing else, and the gate did not read it")
            by_task: dict[str, set[str]] = {}
            for path, _name, doc_task in docs:
                by_task.setdefault(doc_task, set()).add(path.stem)
            for doc_task, have in sorted(by_task.items()):
                lacking = _lacking_documents(ctx, doc_task, have, None)
                if lacking:
                    problems.add(1, f"{doc_task}/", f"the task's documents for "
                                                    f"{', '.join(lacking)} are missing: one "
                                                    f"document per source of the task manifest")
        for path, name, doc_task in docs:
            if task_id and doc_task != task_id:
                problems.add(7, name, f"is task {doc_task!r}'s document, not {task_id!r}'s")
                continue
            try:
                result["stats"].append(validate_document(path, name, doc_task, cfg, ctx,
                                                         problems))
            except (TypeError, AttributeError, KeyError, ValueError, IndexError,
                    RecursionError) as exc:
                # the backstop of "raises nothing": a shape no check above
                # foresaw is the worker's document's fault, said as rule 1
                problems.add(1, name, f"the document's shape could not be checked "
                                      f"({exc.__class__.__name__}: {str(exc)[:200]})")
        result["documents"] = len(docs)
    except GateBroken as exc:
        result["broken"].append(str(exc))
        result["exit"] = EXIT_GATE_BROKEN
    finally:
        ctx.close()
    result["problems"] = problems.lines()
    result["by_rule"] = problems.by_rule()
    result["task_id"] = task_id
    if result["exit"] == EXIT_OK and len(problems):
        result["exit"] = EXIT_REJECTED
    result["task_id_from"] = how if task_id else None
    result["caller"] = caller_of(changed_files=changed_files, pre_merge=pre_merge,
                                 findings=bool(findings))
    write_log(cfg, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "run_id": cfg.run_id, "mode": cfg.mode, "task_id": task_id,
                    "task_id_from": result["task_id_from"],
                    "caller": result["caller"],
                    "changed_files": bool(changed_files),
                    "pre_merge": bool(pre_merge),
                    "ownership": result["ownership"] or None,
                    "ownership_note": result.get("ownership_note"),
                    "ok": result["exit"] == EXIT_OK, "exit": result["exit"],
                    "documents": result["documents"], "by_rule": result["by_rule"],
                    "broken": result["broken"][:3],
                    "reasons": [p[:300] for p in result["problems"][:20]]})
    return result


def self_check(cfg: GateConfig) -> str:
    """Everything this gate needs before a document reaches it; a summary, or :class:`GateBroken`.

    ``cf run``'s verify stage runs ``--self-check`` on the host, with
    ``python3 -I`` from the gate directory alone, before any agent starts: the
    gate's own modules imported (a missing or broken one -- ``locate_records``
    imports ``literature_links`` -- is exit 3 before this runs), both vendored
    packages the pinned releases, the task manifests and the sources where
    gate.json says they are, ``corpus.sqlite`` there and the pinned bytes, and
    each candidates file gate.json pins the pinned bytes.  A gate directory
    staged without one of its modules used to surface only as every worker's
    first validation failing (field defect P2).
    """
    verify_vendor(cfg)
    if not cfg.tasks_dir.is_dir():
        raise GateBroken(f"the task manifests directory {cfg.tasks_dir} is missing")
    manifests = sum(1 for p in cfg.tasks_dir.glob("*.json") if p.is_file())
    if not manifests:
        raise GateBroken(f"{cfg.tasks_dir} holds no task manifest")
    if not cfg.sources_dir.is_dir():
        raise GateBroken(f"the sources directory {cfg.sources_dir} is missing")
    if cfg.corpus_db is not None:
        check_corpus_pin(cfg)
    pinned = 0
    if cfg.candidates_sha256 is not None and cfg.candidates_dir is not None and not cfg.blind:
        for source_id in sorted(cfg.candidates_sha256):
            path = cfg.candidates_dir / f"{source_id}.json"
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise GateBroken(f"{source_id}: the pinned candidates file {path} is unreadable "
                                 f"({exc})") from None
            check_candidates_pin(cfg, source_id, raw, path)
            pinned += 1
    return (f"OK: gate self-check: {cfg.mode} run {cfg.run_id}, {manifests} task manifest(s), "
            f"{len(cfg.sources)} pinned source(s), "
            + ("corpus.sqlite the pinned bytes, " if cfg.corpus_db is not None else "")
            + (f"{pinned} candidates file(s) the pinned bytes, " if pinned else "")
            + f"the vendored packages are the pinned releases and import from {VENDOR}")


def main(argv: list[str] | None = None) -> int:
    """The CLI; any failure of the gate itself is exit 3, never the interpreter's 1."""
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001 - a crash of the gate is not a verdict
        print(f"FAIL: the gate itself failed ({type(exc).__name__}: {exc}) — the gate could not "
              "run: this is not a finding of yours", file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN


class UsageRefused(Exception):
    """A command line the gate refuses: exit 3, before anything is read or logged."""


class _Parser(argparse.ArgumentParser):
    """argparse whose refusals are exit 3 with one ``FAIL`` line, never its exit 2."""

    def error(self, message: str):  # noqa: D401 - argparse's hook
        raise UsageRefused(message)


class _Once(argparse.Action):
    """An option that may be given once: a second ``--gate-config`` is refused.

    argparse keeps the LAST of a repeated option, so an allowlist entry whose
    prefix holds ``--gate-config <gate>/gate.json`` would otherwise let a caller
    append another and choose the config -- and with it the log directory and
    the label its line carries.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, f"_{self.dest}_given", False):
            raise UsageRefused(f"{option_string} is given twice; the gate takes one config")
        setattr(namespace, f"_{self.dest}_given", True)
        setattr(namespace, self.dest, values)


def check_command_line(args: argparse.Namespace, here: pathlib.Path = HERE) -> None:
    """Refuse a command line that could fake the log line's label (:class:`UsageRefused`).

    argparse refuses ``--findings`` with ``--changed-files`` (a mutually exclusive
    group), an abbreviation (``allow_abbrev=False``) and a repeated
    ``--gate-config`` (:class:`_Once`); this refuses ``--pre-merge`` without
    ``--changed-files``, and, when a ``gate.json`` sits beside the script -- as in
    every gate directory a planner stages -- a ``--gate-config`` that resolves to
    any other file: the gate directory's own config names the log the watchdog
    and the bench read, and no caller may point the gate at another.  A
    checkout's ``scripts/gate/`` holds no gate.json, so a hand check there takes
    any config.
    """
    if args.pre_merge and not args.changed_files:
        raise UsageRefused("--pre-merge is tend's pre-merge call and comes with --changed-files")
    beside = here / "gate.json"
    if beside.is_file():
        given = pathlib.Path(args.gate_config).expanduser()
        try:
            same = given.resolve() == beside.resolve()
        except OSError:
            same = False
        if not same:
            raise UsageRefused(f"--gate-config {given} is not {beside}, the config beside this "
                               f"gate: a gate directory runs with its own gate.json only")


def coverage_line(cfg: GateConfig, stats: list[dict]) -> str:
    """What a challenge's OK line adds: the gate counted the task's records itself.

    A reviewer that read only the ownership line took "not checked" to mean coverage
    was not checked, and asked for a verdict on a conditional record the gate had
    rightly left unowed (smoke 4's four rejections); the reviewer's rule card quotes
    these words.  '' for any other mode, or a task whose documents are all unresolved.
    """
    rows = [s for s in stats if isinstance(s, dict) and "records_owed" in s]
    if cfg.mode != LOCATE_CHALLENGE or not rows:
        return ""
    owed = sum(int(s["records_owed"]) for s in rows)
    unowed = sum(int(s.get("conditional_not_owed") or 0) for s in rows)
    return (f"; each of the {owed} record(s) the task owes has one verdict"
            + (f", and {unowed} conditional record(s) are not owed (no prominence overturn "
               f"to subject or central)" if unowed else ""))


def _main(argv: list[str] | None = None) -> int:
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                 allow_abbrev=False)
    ap.add_argument("--gate-config", default=str(HERE / "gate.json"), action=_Once,
                    help="the locate gate.json (cf-gate-locate/1; default: next to this script; "
                         "given once, and in a gate directory only that file)")
    ap.add_argument("--root", default=".", help="the worktree (or harvested corpus) root")
    ap.add_argument("--findings-dir", default=DEFAULT_FINDINGS_DIR,
                    help="with neither --findings nor --changed-files: the directory of "
                         "<task-id>/<source-id>.json documents under --root (a harvested corpus: '.')")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--findings", action="append", default=[],
                       help="validate this document only (repeatable); its task is its directory")
    which.add_argument("--changed-files", action="store_true",
                       help="validate the documents this contribution changed and refuse any "
                            "other path (rule 7)")
    ap.add_argument("--base", default="main", help="the branch the change set is read against")
    ap.add_argument("--task-id", default=None,
                    help="the worker's own task id (default: $TEND_TASK_ID, else "
                         "orchestrator.sqlite, else the single-stem rule)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="with --changed-files: tolerate a change set with no document")
    ap.add_argument("--pre-merge", action="store_true",
                    help="with --changed-files: the batched pre-merge check, where a change "
                         "set of several tasks' new documents is admitted without a task id")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-check", action="store_true",
                    help="check that this gate can run (its modules, the vendored packages, the "
                         "task manifests, the sources, corpus.sqlite) and validate nothing")
    try:
        args = ap.parse_args(argv)
        check_command_line(args)
    except UsageRefused as exc:
        print(f"FAIL: the command line is refused ({exc}) — the gate could not run: this is "
              f"not a finding of yours", file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN

    if IMPORT_ERRORS:
        # one line, never a traceback: exit 1 would tell the worker to fix its documents
        print(f"FAIL: {incomplete_message()} — the gate could not run: this is not a finding "
              f"of yours", file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN
    gate_path = pathlib.Path(args.gate_config).expanduser()
    try:
        if not gate_path.is_file():
            raise GateBroken(f"gate config missing at {gate_path}")
        cfg = GateConfig.load(gate_path)
        if args.self_check:
            print(self_check(cfg))
            print("ownership: not checked (--self-check validates no document)")
            return EXIT_OK
    except GateBroken as exc:
        print(f"FAIL: {exc} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
        print("ownership: not checked (the gate could not run)")
        return EXIT_GATE_BROKEN
    if args.task_id:
        task_id, task_id_from = args.task_id, TASK_ID_FROM_FLAG
    else:
        task_id = os.environ.get("TEND_TASK_ID", "")
        task_id_from = TASK_ID_FROM_ENV if task_id else None
    result = run_gate(cfg, root=pathlib.Path(args.root).resolve(),
                      findings=[pathlib.Path(f) for f in args.findings],
                      findings_dir=args.findings_dir, changed_files=args.changed_files,
                      base=args.base, task_id=task_id, allow_empty=args.allow_empty,
                      task_id_from=task_id_from, pre_merge=args.pre_merge)
    for broken in result["broken"]:
        print(f"FAIL: {broken} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
    if result["problems"]:
        print(f"VALIDATION FAILED ({len(result['problems'])} problem(s)):", file=sys.stderr)
        for line in result["problems"]:
            print("  - " + line, file=sys.stderr)
    elif result["exit"] == EXIT_OK and not args.quiet:
        unresolved = sum(1 for s in result["stats"] if s.get("unresolved"))
        print(f"OK: {result['documents']} locate document(s) validated against gate "
              f"{cfg.preset} ({cfg.mode}); every quote matched the pinned layer once and every "
              f"declaration is in the frozen universe {cfg.universe_id or '(none: inventory)'}"
              + (f"; {unresolved} unresolved" if unresolved else "")
              + coverage_line(cfg, result["stats"]))
    # The LAST line says which ownership rule judged the contribution.
    if result["ownership"]:
        print(f"  changed files: {result['ownership']}")
    elif result["broken"]:
        print("ownership: not checked (the gate could not run)")
    else:
        print("ownership: not checked (no --changed-files: these documents were validated, the "
              "change set was not)")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
