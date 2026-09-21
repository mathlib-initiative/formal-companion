"""The companion-revision pin: which Lean text a finding was judged against.

New code (docs/design/archive/multi-source-model.md S6.2's row schema; the gap it closes is
"no finding is pinned to a companion revision, so nothing can be re-checked,
closed or expired").

A routed finding is a claim about a specific declaration at a specific revision.
Without the revision, six months later a live finding and one already fixed
upstream read exactly the same, a re-audit of a moved companion cannot be joined
to the previous one, and a maintainer who fixed the statement has no way to have
the finding acknowledged as closed -- so the ledger's open set only ever grows.

The pin is three facts, and each is recorded by the stage that can be trusted
with it:

* ``repo_commit`` -- the companion's HEAD when the run was PLANNED, written into
  ``run.yaml`` by ``cf plan-run`` (plus ``dirty``, because a working tree that
  does not match its commit is not pinned by it).  Read through
  ``cflib.gitsafe``: a companion tree is data, and even ``rev-parse`` must not
  become an execve for ``core.fsmonitor``.
* ``decl_fingerprint`` -- sha256 over the normalised statement text of each
  declaration, as the extracted ``companions/<name>/statements.jsonl`` holds it
  AT that commit.  This is what makes a re-check mechanical instead of a re-read.
* neither is ever taken from a worker.  ``cf harvest`` computes the pin for a
  landed corpus (``reports/pin.<run-id>.json``) for the same reason it recomputes
  ``finding_id``: a worker cannot be trusted to state what it was judged against.

A corpus imported from elsewhere has no pin at all.  That is recorded as such --
``unpinned`` -- and never guessed: a fingerprint taken at today's commit would
claim the finding was judged against code it never saw.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from cflib import gitsafe, hashing, state as state_mod
from cflib.jsonio import read_jsonl

#: the pin document's own version, so a reader can refuse a shape it predates
PIN_VERSION = 1

#: Lean block comments, which is where a docstring lives (``/-- … -/``)
_BLOCK_COMMENT = re.compile(r"/-.*?-/", re.DOTALL)


def repo_pin(repo: str | Path | None) -> dict:
    """``{repo_commit, dirty, method}`` for a companion checkout.

    ``dirty`` is part of the pin and not a nicety: the statement table is
    extracted from the WORKING TREE, so a commit recorded beside uncommitted
    edits names a revision that does not contain what was audited, and a
    re-check against that commit would report changes nobody made.

    Every git call goes through ``cflib.gitsafe``.  ``status`` is safe there in
    particular because ``GIT_OPTIONAL_LOCKS=0`` stops it writing the index of a
    repository this skill must never write to.  A tree that is not a checkout is
    not an error -- plenty of fixtures are not -- it is a pin that says so.
    """
    if not repo:
        return {"repo_commit": None, "dirty": None, "method": "no checkout recorded"}
    try:
        head = gitsafe.run(repo, ["rev-parse", "HEAD"], timeout=30)
        if head.returncode != 0 or not head.stdout.strip():
            return {"repo_commit": None, "dirty": None, "method": "not a git checkout"}
        commit = head.stdout.strip()
        status = gitsafe.run(repo, ["status", "--porcelain"], timeout=60)
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return {"repo_commit": None, "dirty": None, "method": "git unavailable"}
    return {"repo_commit": commit, "dirty": dirty,
            "method": "gitsafe rev-parse HEAD + status --porcelain"}


def normalise_statement(text: object) -> str:
    """The statement text a fingerprint is taken over.

    Block comments are dropped (a Lean docstring is one: ``/-- … -/``) and every
    run of whitespace becomes a single space.  So re-indenting a declaration,
    wrapping its binders differently or rewording its docstring does NOT report
    the statement as changed, while a renamed hypothesis, a flipped inequality or
    an added argument does.  That balance is the whole value of a re-check: one
    that shouts at reformatting gets run once.
    """
    return " ".join(_BLOCK_COMMENT.sub(" ", str(text or "")).split())


def decl_fingerprint(statement: object) -> str:
    """sha256 over :func:`normalise_statement` -- the per-declaration pin."""
    return hashing.sha256_text(normalise_statement(statement))


def split_decl(text: object) -> tuple[str, str]:
    """``"ours:Foo.bar"`` -> ``("ours", "Foo.bar")``; no prefix -> ``("", text)``.

    The first colon, as ``harvest.tally._decl_repos`` and ``ledger.compat.
    decl_repo`` both split it: a Lean name may carry later colons (an anonymous
    ``example «Exercise 1.2.1 (countable union)» at …``) and splitting on the
    last one would file it under a repo that does not exist.
    """
    s = str(text or "").strip()
    if ":" not in s:
        return "", s
    repo, _, decl = s.partition(":")
    return repo.strip(), decl.strip()


def statement_fingerprints(path: str | Path) -> dict[str, str]:
    """``{decl: fingerprint}`` for one companion's ``statements.jsonl``.

    A declaration appearing twice (two items tagging one theorem) keeps the first
    row's fingerprint; the rows are one declaration's text either way, and
    ``cf extract`` sorts them, so the choice is deterministic.
    """
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for row in read_jsonl(p, skip_meta=True):
        decl = str(row.get("decl") or "").strip()
        if decl and decl not in out:
            out[decl] = decl_fingerprint(row.get("statement"))
    return out


def run_pins(run_doc: Mapping | None) -> dict[str, dict]:
    """``{companion: {repo_commit, dirty, method}}`` out of a ``run.yaml``.

    ``cf plan-run`` writes the block; a run.yaml from before it existed has none,
    which is the ``unpinned`` case and not an error.
    """
    block = (run_doc or {}).get("companion_pins")
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, dict] = {}
    for name, value in block.items():
        if isinstance(value, Mapping):
            commit = str(value.get("repo_commit") or "").strip()
            out[str(name)] = {
                "repo_commit": commit or None,
                "dirty": value.get("dirty"),
                "method": str(value.get("method") or ""),
            }
    return out


def corpus_decls(directory: str | Path) -> list[str]:
    """Every ``repo:Decl`` the findings in a corpus directory name, sorted."""
    # local import: harvest is not a dependency of this package, and this is the
    # one list of what counts as a findings file (RECEIPT.json and friends are not)
    from harvest._compat import iter_finding_files, read_json

    seen: set[str] = set()
    for path in iter_finding_files(directory):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            continue
        for key in ("disagreements", "findings"):
            for defect in payload.get(key) or []:
                if not isinstance(defect, dict):
                    continue
                for decl in defect.get("unfaithful_decls") or []:
                    text = str(decl).strip()
                    if text:
                        seen.add(text)
    return sorted(seen)


def build(workspace, run_id: str, run_doc: Mapping | None,
          decls: Iterable[str], *, at: str = "") -> dict:
    """The pin document ``cf harvest`` writes for one landed corpus.

    A declaration is fingerprinted only when the statement table it would be
    fingerprinted from was extracted AT the commit the run was planned against.
    Otherwise the companion's block says why not and its declarations are listed
    as ``unpinned``: a fingerprint taken from a table the companion has since
    moved past would pin the finding to code that was never audited, which is
    worse than admitting there is no pin.
    """
    pins = run_pins(run_doc)
    companions: dict[str, dict] = {}
    tables: dict[str, dict[str, str]] = {}
    for name in sorted({*pins, *_companions_of(run_doc)}):
        pin = dict(pins.get(name) or {"repo_commit": None, "dirty": None,
                                      "method": "not recorded in run.yaml"})
        extracted = str((state_mod.read_state(workspace, name, missing_ok=True)
                         or {}).get("commit") or "")
        pin["statements_commit"] = extracted or None
        if not pin.get("repo_commit"):
            pin["fingerprinted"] = False
            pin["note"] = ("run.yaml records no commit for this companion, so nothing "
                           "says which revision its findings were judged against")
        elif pin.get("dirty"):
            pin["fingerprinted"] = False
            pin["note"] = ("the checkout was dirty when the run was planned, so the "
                           "statements audited are not the ones this commit holds")
        elif extracted != pin["repo_commit"]:
            pin["fingerprinted"] = False
            pin["note"] = (f"statements.jsonl was extracted at "
                           f"{hashing.short(extracted) or '(unrecorded)'}, not at the "
                           f"planned {hashing.short(pin['repo_commit'])}")
        else:
            tables[name] = statement_fingerprints(workspace.paths.statements_jsonl(name))
            pin["fingerprinted"] = True
            pin["declarations"] = len(tables[name])
        companions[name] = pin

    pinned: dict[str, dict] = {}
    unpinned: list[str] = []
    for written in sorted({str(d).strip() for d in decls if str(d).strip()}):
        repo, decl = split_decl(written)
        fingerprint = tables.get(repo, {}).get(decl)
        if fingerprint is None:
            unpinned.append(written)
            continue
        pinned[written] = {
            "repo": repo,
            "decl": decl,
            "repo_commit": companions[repo]["repo_commit"],
            "decl_fingerprint": fingerprint,
        }
    return {
        "version": PIN_VERSION,
        "generated_at": at or state_mod.now(),
        "run_id": run_id,
        "companions": companions,
        "decls": pinned,
        # named by a finding but absent from a table this pin could trust: they
        # re-check as `unpinned`, never as `gone`
        "unpinned": unpinned,
    }


def _companions_of(run_doc: Mapping | None) -> list[str]:
    """The companions a run covers, from whichever key its run.yaml used."""
    doc = run_doc or {}
    for key in ("repos", "companions", "companion"):
        value = doc.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value:
            return [str(v) for v in value if isinstance(v, str)]
    return []


def row_pin(row: Mapping, pin_doc: Mapping | None = None,
            companion: str = "") -> dict:
    """The pin a ledger/routed row carries, falling back to its run's pin file.

    A row written before the pin fields existed carries none; it is still joined
    to its run by ``run_id``, so ``reports/pin.<run-id>.json`` answers for it.
    Neither source present is the ``unpinned`` verdict, which is what every
    imported legacy corpus gets -- and why the pinned regression figures cannot
    move when this lands.
    """
    stored = row.get("decl_fingerprints")
    commit = str(row.get("repo_commit") or "")
    out: dict[str, str] = {}
    if isinstance(stored, Mapping):
        for decl, value in stored.items():
            text = str(value or "")
            if text:
                out[str(decl)] = text
    decls = [str(d) for d in (row.get("unfaithful_decls") or []) if d]
    if companion:
        decls = [d for d in decls if split_decl(d)[0] in ("", companion)]
    if not out and isinstance(pin_doc, Mapping):
        entries = pin_doc.get("decls")
        entries = entries if isinstance(entries, Mapping) else {}
        for decl in decls:
            entry = entries.get(decl)
            if isinstance(entry, Mapping) and entry.get("decl_fingerprint"):
                out[decl] = str(entry["decl_fingerprint"])
                commit = commit or str(entry.get("repo_commit") or "")
    return {"repo_commit": commit or "", "decl_fingerprints": out, "decls": decls}


def pin_for_decls(pin_doc: Mapping | None, decls: Sequence[str]) -> dict:
    """``{decl: fingerprint}`` from a pin document, for the named decls only."""
    entries = (pin_doc or {}).get("decls")
    entries = entries if isinstance(entries, Mapping) else {}
    out: dict[str, str] = {}
    for decl in decls:
        entry = entries.get(str(decl))
        if isinstance(entry, Mapping) and entry.get("decl_fingerprint"):
            out[str(decl)] = str(entry["decl_fingerprint"])
    return out
