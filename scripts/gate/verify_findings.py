#!/usr/bin/env python3
"""The mechanical findings gate (schema v2), driven by ``gate.json``.

Ported from ``ffmain:~/tao-diff-run/scripts/verify_findings.py`` (md5 ca976440,
the three differential entrypoints) and ``ffmain:~/tao-attack-vac/scripts/
verify_findings.py`` (md5 f4cc21a8, the two attack entrypoints). Those two files
differ only in the ``KINDS``/``VERDICTS`` constants; everything they hard-wired —
book path, repo list, kind and verdict sets — is now read from ``gate.json``.

Three things changed on purpose, per the design critique:

1. **It runs out of tree.** The original lived at ``scripts/verify_findings.py``
   *inside the worktree the worker commits to*, and both the validation and the
   pre-merge validation ran ``python3 scripts/verify_findings.py`` from that
   worktree — a worker that committed an edited gate passed both. This file is
   copied to ``<tend-root>/gate/`` and invoked by absolute path.
2. **Per-kind rules.** CONTENT/VACUITY need a quote and (in attack mode) a
   witness object; SUSPECTED_ERRATA needs a quote; COVERAGE needs neither;
   PROXY needs bridge + closure evidence; PAGE_ANCHOR is mechanical-only.
3. **Edition binding.** Every findings file carries ``book_sha`` and it must be
   the campaign's; batch 1's anchor findings were judged against the 2011 draft.

Deliberately stdlib-only and self-contained (no ``cflib`` import): it is rsynced
to a server and must run there with nothing installed.

Exit status: 0 when every file validates, 1 otherwise (errors on stderr).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import unicodedata
from typing import Any

#: Files that live beside findings in a corpus directory and are NOT findings.
#: ``cf harvest`` and ``cf import-corpus`` write RECEIPT.json into the findings
#: directory, so a directory-mode re-gate of a landed corpus used to report
#: eight fabricated failures against it.  Duplicated from
#: ``scripts/harvest/_compat.py`` (NON_FINDING_FILES) on purpose: this file runs
#: on a server with no cflib and no harvest package on the path.  Keep in sync.
NON_FINDING_FILES = {
    "RECEIPT.json", "run.yaml", "run.json", "manifest.json",
    "tally.json", "journal-harvest.json", "gate.json", "gate.local.json",
}

MIN_QUOTE_CHARS = 25
WINDOW_FRACTION = 0.6
MIN_WINDOW_WORDS = 6
#: the operator pass tolerates more mangling than the word pass: pdftotext
#: breaks display mathematics across lines, so a run of 50% of the quote's
#: operator-bearing tokens is enough.  Measured against the 3,019 quotes of the
#: baseline corpus: at 0.5, two legacy quotes fail; at 0.6, eleven do.
OPERATOR_WINDOW_FRACTION = 0.5
#: how many unmatched words the gate prints back when a quote matched only in part
REMAINDER_WORDS = 12

DEFAULT_KIND_RULES: dict[str, dict[str, Any]] = {
    "CONTENT": {"quote": True, "witness": True, "decls": True},
    "VACUITY": {"quote": True, "witness": True, "decls": True},
    "SUSPECTED_ERRATA": {"quote": True, "witness": False, "decls": False},
    "COVERAGE": {"quote": False, "witness": False, "decls": False},
    "PROXY": {"quote": False, "witness": False, "decls": True, "bridge": True, "closure": True},
    "PAGE_ANCHOR": {
        "quote": False,
        "witness": False,
        "decls": True,
        "pages": True,
        "mechanical_only": True,
    },
}


# --------------------------------------------------------------------------- #
# quote normalisation — ported verbatim (norm L22-27, quote_ok L29-39)
# --------------------------------------------------------------------------- #
#: mathematical operators the operator pass keeps.  Flipping any of these is the
#: defect class this audit exists to find, and the word pass below deletes them
#: all, so they are checked separately.
_OPERATOR_TOKENS: dict[str, str] = {
    "\u2264": "ople", "\u2265": "opge", "<": "oplt", ">": "opgt",
    "=": "opeq", "\u2260": "opne", "+": "opplus", "-": "opminus",
    "\u2208": "opin", "\u2282": "opsub", "\u2286": "opsub",
    "\u222a": "opcup", "\u2229": "opcap", "\u2192": "oparr", "\u00ac": "opnot",
}
#: ASCII spellings pdftotext and Lean sources use for the same operators
_OPERATOR_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("<=", "\u2264"), (">=", "\u2265"), ("!=", "\u2260"),
    ("/=", "\u2260"), ("->", "\u2192"),
)


def _fold(s: str) -> str:
    """The shared prefix of both normalisations: NFKC, ligatures, one dash."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = s.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"[\u2212\u2013\u2014]", "-", s)


def norm(s: str) -> str:
    """Normalise text for quote comparison: pdftotext mangles mathematics.

    LOSSY BY DESIGN and therefore blind by design: every non-alphanumeric — so
    every ``=``, ``\u2264``, ``\u2208``, ``\u222a`` — becomes a space, because a
    pdftotext extraction spells the same formula a dozen ways.  A quote whose
    operators have been flipped is word-identical to the real one under this
    normalisation, which is why :func:`norm_operators` exists and why
    ``check_operators`` is on for every non-legacy preset.
    """
    s = _fold(s)
    s = re.sub(r"[^0-9A-Za-z]+", " ", s)  # compare on words, not symbols
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_operators(s: str) -> str:
    """Like :func:`norm`, but the operators survive as words.

    ``a \u2264 b`` becomes ``a ople b``, so an operator keeps its position in the
    token stream and a flipped ``=`` \u2192 ``\u2260`` no longer matches the book.
    """
    s = _fold(s)
    for ascii_form, unicode_form in _OPERATOR_DIGRAPHS:
        s = s.replace(ascii_form, unicode_form)
    for ch, token in _OPERATOR_TOKENS.items():
        s = s.replace(ch, " " + token + " ")
    s = re.sub(r"[^0-9A-Za-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def has_operators(q: str) -> bool:
    """True when the raw quote contains one of the operators worth checking."""
    folded = _fold(q)
    return any(ch in folded for ch in _OPERATOR_TOKENS) or any(
        a in folded for a, _ in _OPERATOR_DIGRAPHS
    )


class BookText:
    """The pinned book text in the normalisations the quote gate compares against.

    ``words`` is the lossy :func:`norm` form (the historical one).  ``ops`` is
    the :func:`norm_operators` form, or ``None`` when the caller handed in a
    plain string — in which case the operator pass is skipped and says so.
    """

    def __init__(self, words: str, ops: str | None = None):
        self.words = words
        self.ops = ops

    @classmethod
    def coerce(cls, book: "BookText | str") -> "BookText":
        return book if isinstance(book, BookText) else cls(str(book))


def _window_match(book: str, words: list[str], fraction: float) -> int:
    """Index of the first >=``fraction`` contiguous window found, else -1."""
    k = max(MIN_WINDOW_WORDS, int(len(words) * fraction))
    for i in range(0, len(words) - k + 1):
        if " ".join(words[i : i + k]) in book:
            return i
    return -1


def unmatched_words(book: str, words: list[str], start: int, length: int) -> list[str]:
    """The quote words OUTSIDE the window that matched — what nobody verified."""
    return words[:start] + words[start + length :]


def quote_ok(book: "BookText | str", q: str) -> tuple[bool, str]:
    """True when ``q`` occurs in the book text (verbatim, or a 60% window).

    Two passes:

    1. the **word** pass (historical): operators deleted, a contiguous window of
       >=60% of the quote's words must occur in the book.  When only a window
       matched, the reason string names the words that were NOT verified —
       previously a quote that was 70% genuine and 30% invented passed with the
       gate saying nothing at all about the invented 30%.
    2. the **operator** pass: when the quote carries operators and the book was
       loaded with an operator normalisation, the operator-bearing token stream
       must match too, so an inequality or operator flip is refused.
    """
    bk = BookText.coerce(book)
    n = norm(q)
    if len(n) < MIN_QUOTE_CHARS:
        return False, f"quote too short to verify (need >={MIN_QUOTE_CHARS} normalised chars)"
    w = n.split()
    if n in bk.words:
        why = "verbatim"
    else:
        i = _window_match(bk.words, w, WINDOW_FRACTION)
        if i < 0:
            return False, "NOT FOUND in the book text"
        k = max(MIN_WINDOW_WORDS, int(len(w) * WINDOW_FRACTION))
        rest = unmatched_words(bk.words, w, i, k)
        shown = " ".join(rest[:REMAINDER_WORDS]) + ("…" if len(rest) > REMAINDER_WORDS else "")
        why = (
            f"partial ({k}/{len(w)} words matched as one window; pdftotext mangling "
            f"tolerated). UNVERIFIED: {shown!r}"
        )
    if bk.ops is not None and has_operators(q):
        o = norm_operators(q)
        ow = o.split()
        if o not in bk.ops and _window_match(bk.ops, ow, OPERATOR_WINDOW_FRACTION) < 0:
            return False, (
                "words match the book but the OPERATORS do not — an inequality, "
                "equality or set operator has been changed (or the quote splices "
                "two passages together)"
            )
    return True, why


#: characters pdftotext and LaTeX produce for a hyphen (mirrors cflib.items._DASHES)
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def clean_item(raw: str) -> str:
    """Canonicalise an item label before hashing it.

    Byte-for-byte the same transformation as ``cflib.items.clean`` (NFKC, one
    dash, non-breaking space to space, whitespace collapsed, wrapping backticks
    and trailing punctuation stripped).  Duplicated here rather than imported
    because this file is rsynced to a run host with no ``cflib`` on the path;
    ``tests/test_plan_gate.py`` asserts the two stay identical.

    Without it ``Exercise    1.3.19`` and ``Exercise 1.3.19`` -- the whitespace
    variant the baseline lost -- would hash to different finding ids.
    """
    s = unicodedata.normalize("NFKC", raw or "")
    for bad in _DASHES:
        s = s.replace(bad, "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("`").strip()
    return s.rstrip(" .,;:").strip()


def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: list[str]) -> str:
    """Stable content address of a finding — the ONE definition.

    ``sha256("\\n".join([run_id, source_id, clean_item(item), kind, *sorted(decls)]))`` —
    the critique rejected the original ``concept``-based id because ``concept``
    is LLM-written prose, so the same defect got a different id in every run.

    This is inlined from :func:`cflib.hashing.finding_id`; the two MUST produce
    identical digests, or the detector's ids and the gate's live in different
    namespaces and no ledger row can ever match a finding.  The gate cannot
    import ``cflib`` (it runs on a server with nothing installed), so equality
    is enforced by a test instead of by sharing code.
    """
    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    payload = "\n".join([str(run_id or ""), str(source_id or ""), clean_item(str(item or "")),
                         str(kind or ""), *decls])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# gate configuration
# --------------------------------------------------------------------------- #
class GateConfig:
    """The parsed ``gate.json``; every former hard-wired constant lives here."""

    def __init__(self, data: dict[str, Any], source: pathlib.Path | None = None):
        self.source = source
        self.data = data
        self.mode: str = data.get("mode", "differential")
        self.preset: str = data.get("preset", "")
        self.run_id: str = data.get("run_id", "")
        self.schema_version: int = int(data.get("schema_version", 2))
        book = data.get("book") or {}
        self.book_text = pathlib.Path(book["text"]).expanduser() if book.get("text") else None
        self.book_sha: str | None = book.get("sha256")
        self.book_text_sha: str | None = book.get("text_sha256")
        self.images_dir: str | None = book.get("images_dir")
        self.repos: list[str] = list(data.get("repos") or [])
        self.kinds: set[str] = set(data.get("kinds") or [])
        self.verdicts: set[str] = set(data.get("verdicts") or [])
        self.severities: set[str] = set(data.get("severities") or ["high", "medium", "low"])
        self.witness_required: bool = bool(data.get("witness_required", False))
        self.require_book_located: bool = bool(data.get("require_book_located", True))
        # Off only for the `mechanical` preset: pair/anchor_check.py and
        # detect/ produce findings without ever reading the book's prose, so
        # demanding a verbatim passage quote of them is demanding a fabrication.
        self.require_passage_quote: bool = bool(data.get("require_passage_quote", True))
        self.require_book_sha: bool = bool(data.get("require_book_sha", True))
        self.verify_text_sha: bool = bool(data.get("verify_text_sha", False))
        self.require_finding_id: bool = bool(data.get("require_finding_id", False))
        # The operator pass is on everywhere except the two *-legacy presets that
        # validate the imported 2011-draft corpus, whose quotes carry pdftotext
        # artifacts predating the check (two of 3,019 fail it).
        self.check_operators: bool = bool(data.get("check_operators", True))
        self.findings_dir: str = data.get("findings_dir", "findings")
        self.decl_index: str | None = data.get("decl_index")
        rules = dict(DEFAULT_KIND_RULES)
        for kind, override in (data.get("kind_rules") or {}).items():
            merged = dict(rules.get(kind, {}))
            merged.update(override)
            rules[kind] = merged
        self.kind_rules = rules

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    def rule(self, kind: str) -> dict[str, Any]:
        return self.kind_rules.get(kind, {"quote": True, "witness": True, "decls": True})


def _load_decl_index(spec: str) -> set[str]:
    """Load the full declaration index used to reject phantom names.

    Accepts a JSON list of ``repo:decl`` strings, a JSON object
    ``{repo: [decl, ...]}``, or JSONL rows carrying ``repo`` and ``decl``.
    """
    path = pathlib.Path(spec).expanduser()
    text = path.read_text(encoding="utf-8")
    out: set[str] = set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out.add(f"{row['repo']}:{row['decl']}")
        return out
    if isinstance(data, dict):
        for repo, decls in data.items():
            for d in decls:
                out.add(f"{repo}:{d}")
    else:
        for entry in data:
            if isinstance(entry, str):
                out.add(entry)
            else:
                out.add(f"{entry['repo']}:{entry['decl']}")
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _witness_ok(witness: Any) -> bool:
    """Presence/non-empty only — judging the witness is the reviewer's job."""
    if not isinstance(witness, dict):
        return False
    object_ = str(witness.get("object") or "").strip()
    why = str(witness.get("why") or "").strip()
    return bool(object_) and bool(why)


def validate_doc(
    doc: Any,
    name: str,
    cfg: GateConfig,
    book: "BookText | str",
    decl_index: set[str] | None = None,
) -> list[str]:
    """Return the list of gate errors for one findings document."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return [f"{name}: top level is not a JSON object"]

    for key in ("schema_version", "item", "book_located", "matches", "disagreements"):
        if key not in doc:
            errs.append(f"{name}: missing required key {key!r}")
    if doc.get("schema_version") != cfg.schema_version:
        errs.append(
            f"{name}: schema_version {doc.get('schema_version')!r} "
            f"(expected {cfg.schema_version}; run convert_v1.py on a v1 corpus)"
        )
    if cfg.require_book_sha:
        if not doc.get("book_sha"):
            errs.append(f"{name}: missing book_sha (the pinned edition must be stamped)")
        elif cfg.book_sha and doc["book_sha"] != cfg.book_sha:
            errs.append(
                f"{name}: book_sha {doc['book_sha'][:12]}… is not this campaign's "
                f"{(cfg.book_sha or '')[:12]}… — wrong edition"
            )
    if cfg.require_book_located and not doc.get("book_located"):
        errs.append(
            f"{name}: book_located is false — locate the passage, or raise a COVERAGE finding"
        )
    passage = doc.get("book_passage_quote")
    if cfg.require_passage_quote or passage:
        ok, why = quote_ok(book, passage or "")
        if not ok:
            errs.append(f"{name}: book_passage_quote {why}")

    for i, m in enumerate(doc.get("matches") or []):
        if not isinstance(m, dict):
            errs.append(f"{name}: matches[{i}] is not an object")
            continue
        if m.get("verdict") not in cfg.verdicts:
            errs.append(
                f"{name}: match[{i}] verdict {m.get('verdict')!r} not in {sorted(cfg.verdicts)}"
            )

    run_id = doc.get("run_id") or cfg.run_id
    for i, g in enumerate(doc.get("disagreements") or []):
        errs.extend(_validate_disagreement(g, i, name, cfg, book, decl_index, run_id, doc))
    return errs


def _validate_disagreement(
    g: Any,
    i: int,
    name: str,
    cfg: GateConfig,
    book: "BookText | str",
    decl_index: set[str] | None,
    run_id: str,
    doc: dict[str, Any],
) -> list[str]:
    errs: list[str] = []
    where = f"{name}: disagreement[{i}]"
    if not isinstance(g, dict):
        return [f"{where} is not an object"]

    for key in ("concept", "kind", "repos_involved", "faithful", "reasoning", "severity"):
        if key not in g:
            errs.append(f"{where}: missing required key {key!r}")

    kind = g.get("kind")
    if kind not in cfg.kinds:
        errs.append(f"{where}.kind {kind!r} not in {sorted(cfg.kinds)}")
        return errs  # without a known kind the per-kind rules are meaningless
    rule = cfg.rule(kind)

    if g.get("severity") not in cfg.severities:
        errs.append(f"{where}.severity {g.get('severity')!r} not in {sorted(cfg.severities)}")

    faithful = g.get("faithful")
    if not isinstance(faithful, list):
        errs.append(f"{where}.faithful must be a list of repos (schema v2), got {faithful!r}")
    else:
        for repo in faithful:
            if cfg.repos and repo not in cfg.repos:
                errs.append(f"{where}.faithful entry {repo!r} is not a known repo {cfg.repos}")

    decls = g.get("unfaithful_decls") or []
    if not isinstance(decls, list):
        errs.append(f"{where}.unfaithful_decls must be a list")
        decls = []
    if rule.get("decls") and not decls:
        errs.append(f"{where}: kind {kind} must name the unfaithful declaration(s)")
    prefixes = "|".join(re.escape(r) for r in cfg.repos) or r"[A-Za-z0-9_.-]+"
    for u in decls:
        if not isinstance(u, str) or not re.match(rf"^({prefixes}):\S", u):
            errs.append(f"{where}: unfaithful_decls entry {u!r} must be 'repo:decl'")
        elif decl_index is not None and u not in decl_index:
            errs.append(f"{where}: unfaithful_decls entry {u!r} names no declaration in the index")

    if rule.get("quote", True):
        ok, why = quote_ok(book, g.get("book_quote", ""))
        if not ok:
            errs.append(f"{where}.book_quote {why}")
    elif g.get("book_quote"):
        ok, why = quote_ok(book, g["book_quote"])
        if not ok:
            errs.append(f"{where}.book_quote {why} (optional for {kind}, but must be real)")

    if cfg.witness_required and rule.get("witness"):
        if not _witness_ok(g.get("witness")):
            errs.append(
                f"{where}: kind {kind} needs a witness object "
                '{"object": ..., "why": ...} with both fields non-empty'
            )

    if rule.get("bridge"):
        if g.get("bridge") not in {"proved", "fiat", "none"}:
            errs.append(f"{where}: kind {kind} needs bridge ∈ proved|fiat|none")
    if rule.get("closure"):
        if not g.get("closure"):
            errs.append(f"{where}: kind {kind} needs closure evidence (the dependency path)")
    if rule.get("pages"):
        page = g.get("page")
        if not isinstance(page, dict) or "claimed" not in page or "actual" not in page:
            errs.append(f"{where}: kind {kind} needs page {{claimed, actual}}")
    if rule.get("mechanical_only") and g.get("source") != "anchor_check":
        errs.append(
            f"{where}: kind {kind} is mechanical-only — it may only come from anchor_check.py"
        )

    if cfg.require_finding_id:
        want = finding_id(run_id, str(doc.get("source_id", "") or ""), doc.get("item", ""),
                          str(kind), [d for d in decls if isinstance(d, str)])
        got = g.get("finding_id")
        if not got:
            errs.append(f"{where}: missing finding_id")
        elif got != want:
            errs.append(f"{where}: finding_id {got[:12]}… != computed {want[:12]}…")
    return errs


def partial_quotes(doc: Any, name: str, book: "BookText | str") -> list[str]:
    """Quotes in ``doc`` that matched only as a window — what nobody verified.

    The gate prints these on success.  A quote that is 70% genuine and 30%
    invented passes the word pass; refusing it outright would reject 25 of the
    3,019 real quotes in the baseline corpus, so the gate names the unverified
    remainder instead of staying silent about it.
    """
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    todo = [("book_passage_quote", doc.get("book_passage_quote"))]
    for i, g in enumerate(doc.get("disagreements") or []):
        if isinstance(g, dict) and g.get("book_quote"):
            todo.append((f"disagreement[{i}].book_quote", g["book_quote"]))
    for where, q in todo:
        if not q:
            continue
        ok, why = quote_ok(book, q)
        if ok and why.startswith("partial"):
            out.append(f"{name}: {where} {why}")
    return out


# --------------------------------------------------------------------------- #
# changed-files check (kept in this file on purpose: only verify_findings.py and
# gate.json are transferred to the run host, so the check must travel with them)
# --------------------------------------------------------------------------- #
def _git(args: list[str], cwd: pathlib.Path) -> tuple[int, str]:
    import subprocess

    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "")


def changed_paths(root: pathlib.Path, base: str) -> tuple[set[str], list[str]]:
    """Paths this contribution changed against ``base``, including uncommitted work."""
    code, _ = _git(["rev-parse", "--verify", "--quiet", base], root)
    if code != 0:
        return set(), [f"base ref {base!r} does not exist in {root} (the check fails closed)"]
    errs: list[str] = []
    paths: set[str] = set()
    code, out = _git(["diff", "--name-only", base], root)
    if code != 0:
        errs.append(f"git diff against {base} failed")
    paths.update(p for p in out.splitlines() if p.strip())
    _code, out = _git(["ls-files", "--others", "--exclude-standard"], root)
    paths.update(p for p in out.splitlines() if p.strip())
    return paths, errs


def check_changed(paths: set[str], *, allow_empty: bool = False) -> list[str]:
    """Only ``findings/<id>.json`` and ``tasks/<id>.yaml`` may change, in pairs.

    This is the other half of moving the gate out of tree: the original run let
    a worker commit anything in its worktree, including the gate itself.
    """
    problems: list[str] = []
    findings: set[str] = set()
    tasks: set[str] = set()
    for p in sorted(paths):
        parts = p.split("/")
        if len(parts) == 2 and parts[0] == "findings" and parts[1].endswith(".json"):
            findings.add(parts[1][: -len(".json")])
        elif len(parts) == 2 and parts[0] == "tasks" and parts[1].endswith((".yaml", ".yml")):
            tasks.add(parts[1].rsplit(".", 1)[0])
        else:
            problems.append(f"changed path {p!r} is outside findings/<id>.json and tasks/<id>.yaml")
    for stem in sorted(findings - tasks):
        problems.append(f"findings/{stem}.json has no tasks/{stem}.yaml in the same change set")
    if not findings and not allow_empty:
        problems.append("no findings file was written")
    return problems


def load_book(cfg: GateConfig) -> tuple[BookText, list[str]]:
    """Read and normalise the book text; returns (BookText, errors).

    Both normalisations are built up front: the word form every quote is matched
    against, and — unless ``check_operators`` is off — the operator-bearing form.
    """
    empty = BookText("")
    if cfg.book_text is None:
        return empty, ["gate.json has no book.text path"]
    if not cfg.book_text.exists():
        return empty, [f"book text missing at {cfg.book_text} (the gate fails closed)"]
    raw = cfg.book_text.read_bytes()
    errs: list[str] = []
    if cfg.verify_text_sha and cfg.book_text_sha:
        got = hashlib.sha256(raw).hexdigest()
        if got != cfg.book_text_sha:
            errs.append(
                f"book text sha256 {got[:12]}… != gate.json book.text_sha256 "
                f"{cfg.book_text_sha[:12]}… — wrong extraction"
            )
    text = raw.decode("utf-8", errors="ignore")
    ops = norm_operators(text) if cfg.check_operators else None
    return BookText(norm(text), ops), errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--gate-config",
        default=str(pathlib.Path(__file__).resolve().parent / "gate.json"),
        help="gate.json written by cf plan-run (default: next to this script)",
    )
    ap.add_argument("--root", default=".", help="worktree root holding findings/ (default: cwd)")
    ap.add_argument(
        "--findings-dir",
        default=None,
        help="subdirectory of --root holding the findings (default: gate.json findings_dir). "
             "Use '.' to gate a harvested corpus, where the files sit directly in corpus/<run-id>/",
    )
    ap.add_argument("--findings", action="append", default=[], help="validate this file only (repeatable)")
    ap.add_argument("--book", default=None, help="override gate.json book.text")
    ap.add_argument(
        "--changed-files",
        action="store_true",
        help="also refuse a contribution that touched anything but findings/<id>.json "
             "and tasks/<id>.yaml (the out-of-tree gate's other half)",
    )
    ap.add_argument("--base", default="main", help="branch the changed-files check compares against")
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="with --changed-files: tolerate a change set with no findings file",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    gate_path = pathlib.Path(args.gate_config).expanduser()
    if not gate_path.exists():
        print(f"FAIL: gate config missing at {gate_path}", file=sys.stderr)
        return 1
    cfg = GateConfig.load(gate_path)
    if args.book:
        cfg.book_text = pathlib.Path(args.book).expanduser()
    if args.findings_dir:
        cfg.findings_dir = args.findings_dir

    book, errs = load_book(cfg)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    decl_index = _load_decl_index(cfg.decl_index) if cfg.decl_index else None

    root = pathlib.Path(args.root).resolve()
    problems: list[str] = []
    if args.changed_files:
        paths, cerrs = changed_paths(root, args.base)
        problems.extend(cerrs)
        problems.extend(check_changed(paths, allow_empty=args.allow_empty))

    if args.findings:
        files = [pathlib.Path(f) for f in args.findings]
    else:
        files = [
            f for f in sorted((root / cfg.findings_dir).glob("*.json"))
            if f.name not in NON_FINDING_FILES and not f.name.startswith(".")
        ]
    if not files and not problems:
        if not args.quiet:
            print("no findings files here; nothing to validate")
        return 0

    partial: list[str] = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a gate failure
            problems.append(f"{f.name}: not valid JSON ({exc})")
            continue
        problems.extend(validate_doc(doc, f.name, cfg, book, decl_index))
        partial.extend(partial_quotes(doc, f.name, book))

    if problems:
        print(f"VALIDATION FAILED ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    if not args.quiet:
        for note in partial:
            print("  ~ " + note)
        ops = "operators checked" if cfg.check_operators else "OPERATORS NOT CHECKED (legacy preset)"
        print(
            f"OK: {len(files)} findings file(s) validated against gate {cfg.preset or cfg.mode}; "
            f"every book quote matched the text ({len(partial)} only in part, listed above; {ops})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
