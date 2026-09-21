#!/usr/bin/env python3
"""Convert the v1 findings corpus to schema v2 — in memory or to a new tree.

Ported from: nothing. v1 is the shape produced by
``ffmain:~/tao-diff-{run,run-pdf,run-gpt}`` and ``~/tao-attack-{vac,hunt}``; the
local baseline of 2,672 files is at ``$CF_BASELINE_CORPUS`` (=
``/Users/jack/Desktop/tao-diff-run``) in ``findings/``, ``findings-pdf/``,
``findings-gpt/``, ``findings-vac/`` and ``findings-hunt/``.

The four v1 defects this fixes (design decision "adopt findings schema v2"):

* ``faithful`` was a single string and could not name two repositories — agents
  wrote ``"ours | adam"`` and ``"all_differ"`` to get around it;
* the witness lived in ``reasoning`` prose, so the witness gate could not be
  mechanical;
* findings had no id, so challenge/adjudication linked by 22-char label prefix;
* nothing recorded which edition of the book the quote came from.

v1 also predates the multi-source model: it had ONE implicit book, so there is
no ``source_id`` in the corpus to recover.  ``--source`` supplies the run's
single source and the converter stamps it on the document and on every
disagreement; ``--source-sha`` fills the slot v1 called ``book_sha``.  The ids
are recomputed under the five-part rule of docs/design/archive/multi-source-model.md
S6.1, which is exactly why they must be recomputed: a four-part id computed
without a source collides across sources.

v1 predates the workspace too, so its REPO vocabulary is whatever that campaign
called the repositories -- ``ours``, ``teorth``, ``adam`` -- while a workspace
names the same repository by its companion id (``tml-prefix``).  ``--repo-map
ours=tml-prefix`` says the two names are one repository, and the converter
rewrites the ``repo:`` half of every ``unfaithful_decls`` entry, of
``repos_involved``, of ``faithful`` and of ``matches[].decls[].repo``.  It
matters because the union key is ``(source_id, repo, item, kind)``: with the
repo halves spelled differently, a run of this workspace that re-finds a
baseline defect makes a SECOND entry and ``union --new-only`` calls it new --
the same non-join the in-text item key fixed on the item half.  The gate reads
the same vocabulary (``unfaithful_decls`` prefixes must be known repos), so an
unmapped import does not gate either.  The map is opt-in and never guessed:
without it nothing is renamed, and where it does rename, the row keeps the v1
spelling in ``unfaithful_decls_v1`` and the document records the map it was
converted under in ``repo_map``.

The baseline is **never** modified: the converter reads it and writes elsewhere
(it refuses an output directory that is also an input directory).

``NO_WEAKNESS_FOUND`` (the vac run's spelling) folds into ``NO_DEFECT_FOUND``;
the legacy gate presets still accept both so the corpus validates either way.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify_findings import finding_id  # noqa: E402  (same-directory helper)

V1_FAITHFUL_WORDS = {"none", "unsure", "all", "all_differ", "all differ", "n/a", ""}

#: a repo name as the corpus spells it inside a decl: ``ours:Foo.bar``
_REPO_WORD = re.compile(r"[A-Za-z0-9_.\-]+")


def parse_repo_map(value: Any) -> dict[str, str]:
    """``"ours=tml-prefix,teorth=peer"`` -> ``{'ours': 'tml-prefix', ...}``.

    Accepts a string, a list of strings (one ``--repo-map`` each) or a mapping.
    An entry without ``=`` is an error rather than a silently ignored token: a
    typo here produces a corpus that cannot join and says nothing about why.
    """
    if not value:
        return {}
    if isinstance(value, Mapping):
        pairs = [(str(k), str(v)) for k, v in value.items()]
    else:
        tokens: list[str] = []
        entries = [value] if isinstance(value, str) else list(value)
        for entry in entries:
            tokens.extend(t for t in re.split(r"[,\s]+", str(entry)) if t)
        pairs = []
        for token in tokens:
            old, sep, new = token.partition("=")
            if not sep:
                raise ValueError(
                    f"repo map entry {token!r} is not <v1-repo>=<companion-id> "
                    "(e.g. ours=tml-prefix)")
            pairs.append((old, new))
    out: dict[str, str] = {}
    for old, new in pairs:
        old, new = old.strip(), new.strip()
        if not old or not new:
            raise ValueError("a repo map entry needs both halves: <v1-repo>=<companion-id>")
        out[old] = new
    return out


def map_repo(name: Any, repo_map: Mapping[str, str]) -> Any:
    """One repo name through the map; anything unmapped is returned untouched."""
    if not isinstance(name, str):
        return name
    return repo_map.get(name.strip(), name)


def map_decl(decl: Any, repo_map: Mapping[str, str]) -> Any:
    """``ours:Foo.bar`` -> ``tml-prefix:Foo.bar``; the decl half is never touched.

    A string with no ``repo:`` prefix, or a prefix the map does not name, comes
    back exactly as it went in.
    """
    if not isinstance(decl, str):
        return decl
    repo, sep, rest = decl.partition(":")
    if not sep:
        return decl
    mapped = repo_map.get(repo.strip())
    return f"{mapped}:{rest}" if mapped else decl


def _map_faithful(value: Any, repo_map: Mapping[str, str]) -> Any:
    """v1's ``faithful`` -- a list, or prose like ``"ours | adam"`` -- remapped.

    Word by word, so ``none``/``all``/``unsure`` (which are verdicts, not repos)
    survive untouched unless an operator maps one, which would be their error.
    """
    if not repo_map:
        return value
    if isinstance(value, list):
        return [map_repo(v, repo_map) for v in value]
    if not isinstance(value, str) or not value:
        return value
    return _REPO_WORD.sub(lambda m: repo_map.get(m.group(0), m.group(0)), value)


def decl_repos(doc: Mapping[str, Any]) -> set[str]:
    """The repo names a findings document's ``unfaithful_decls`` entries carry.

    This is the ``repo`` half of the union key ``(source_id, repo, item, kind)``
    and of the gate's known-repo check, so it is the set an importer compares
    against the workspace's companions before the mismatch becomes a non-join.
    """
    out: set[str] = set()
    for g in doc.get("disagreements") or []:
        if not isinstance(g, dict):
            continue
        for decl in g.get("unfaithful_decls") or []:
            if isinstance(decl, str) and ":" in decl:
                repo = decl.partition(":")[0].strip()
                if repo:
                    out.add(repo)
    return out


def split_faithful(value: Any, repos_involved: list[str], known: list[str]) -> tuple[list[str], str]:
    """Map v1's single ``faithful`` string to (list of repos, note).

    ``'ours'`` → ``(['ours'], '')``; ``'ours | adam'`` → ``(['ours','adam'], '')``;
    ``'none'`` → ``([], 'none')``; ``'all'`` → ``(repos_involved, 'all')``;
    ``'unsure'``/``'all_differ'`` → ``([], word)`` — the judgement is preserved
    as a note rather than silently promoted to a repo list.
    """
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)], ""
    text = str(value or "").strip()
    low = text.lower()
    if low in {"all"}:
        return list(repos_involved), "all"
    if low in V1_FAITHFUL_WORDS:
        return [], low or "missing"
    parts = [p.strip() for p in re.split(r"[|,/]|\band\b|&", text) if p.strip()]
    repos = [p for p in parts if not known or p in known]
    note = "" if repos and len(repos) == len(parts) else text
    return repos, note


def _witness_object(g: dict[str, Any], item: str) -> str:
    """Pull a plausible witness ``object`` out of v1 prose (a lift, not a parse)."""
    reasoning = str(g.get("reasoning") or "")
    m = re.search(r"`([^`]{1,120})`", reasoning)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:at|take|with|when)\s+([A-Za-z0-9_\\∀∃^{}(). =<>+*/-]{3,60})", reasoning)
    if m:
        return m.group(1).strip()
    concept = str(g.get("concept") or "").strip()
    return concept or item


def convert_disagreement(
    g: dict[str, Any],
    *,
    item: str,
    run_id: str,
    source_id: str,
    source_sha: str,
    repos: list[str],
    lift_witness: bool,
    repo_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Convert one v1 disagreement; unknown keys are preserved untouched."""
    repo_map = repo_map or {}
    out = dict(g)
    v1_decls = [d for d in (g.get("unfaithful_decls") or []) if isinstance(d, str)]
    decls = [map_decl(d, repo_map) for d in v1_decls]
    out["unfaithful_decls"] = decls
    if decls != v1_decls:
        # the rewrite is recorded, never silent: this is the row's own audit
        # trail back to the corpus it was imported from
        out["unfaithful_decls_v1"] = v1_decls
    involved = [map_repo(r, repo_map) for r in (g.get("repos_involved") or [])]
    if "repos_involved" in g:
        out["repos_involved"] = involved
    faithful, note = split_faithful(_map_faithful(g.get("faithful"), repo_map),
                                    [r for r in involved if isinstance(r, str)], repos)
    out["faithful"] = faithful
    if note:
        out["faithful_note"] = note
    kind = str(g.get("kind") or "")
    # The row's OWN source, never the document's `get(...) or ""`: a v1 row has
    # no source at all, and an empty source_id would hash a different id from
    # the one the document is stamped with.
    out["source_id"] = source_id
    if source_sha:
        out["source_sha"] = source_sha
    out["finding_id"] = finding_id(run_id, source_id, item, kind, decls)
    out.setdefault("source", "convert_v1")
    if lift_witness and kind in {"CONTENT", "VACUITY"} and not isinstance(g.get("witness"), dict):
        out["witness"] = {
            "object": _witness_object(g, item),
            "why": str(g.get("reasoning") or "").strip() or "see reasoning",
            "source": "v1_reasoning_lift",
        }
    return out


def convert_doc(
    doc: dict[str, Any],
    *,
    run_id: str,
    source_id: str,
    source_sha: str,
    mode: str = "differential",
    preset: str = "",
    repos: Iterable[str] = (),
    fold_no_weakness: bool = True,
    lift_witness: bool | None = None,
    repo_map: Any = None,
) -> dict[str, Any]:
    """Convert one v1 findings document to v2. Pure; never touches the disk.

    ``repo_map`` (``{'ours': 'tml-prefix'}`` or ``"ours=tml-prefix"``) says which
    workspace companion each v1 repo name is.  ``finding_id`` is computed over
    the MAPPED declarations, because the converted row belongs to the importing
    workspace and is joined against that workspace's runs.
    """
    repos = list(repos)
    repo_map = parse_repo_map(repo_map)
    if lift_witness is None:
        lift_witness = mode == "attack"
    if not source_id:
        raise ValueError(
            "convert_doc needs a source_id: a v1 document has no source, and a "
            "finding_id computed without one collides across sources")
    out = dict(doc)
    out["schema_version"] = 2
    out["run_id"] = run_id
    out["source_id"] = source_id
    out["source_sha"] = source_sha
    out["mode"] = mode
    if preset:
        out["preset"] = preset
    if repo_map:
        # the document says which vocabulary it was converted under, so a reader
        # of corpus/<run-id>/ can tell a renamed repo from an original one
        out["repo_map"] = dict(sorted(repo_map.items()))
    item = str(doc.get("item") or "")
    matches = []
    for m in doc.get("matches") or []:
        m = dict(m) if isinstance(m, dict) else m
        if fold_no_weakness and isinstance(m, dict) and m.get("verdict") == "NO_WEAKNESS_FOUND":
            m["verdict"] = "NO_DEFECT_FOUND"
            m["v1_verdict"] = "NO_WEAKNESS_FOUND"
        if repo_map and isinstance(m, dict) and isinstance(m.get("decls"), list):
            m["decls"] = [dict(d, repo=map_repo(d.get("repo"), repo_map))
                          if isinstance(d, dict) and "repo" in d else d
                          for d in m["decls"]]
        matches.append(m)
    out["matches"] = matches
    out["disagreements"] = [
        convert_disagreement(
            g, item=item, run_id=run_id, source_id=source_id, source_sha=source_sha,
            repos=repos, lift_witness=lift_witness, repo_map=repo_map
        )
        if isinstance(g, dict)
        else g
        for g in (doc.get("disagreements") or [])
    ]
    return out


def convert_tree(
    src: pathlib.Path,
    dst: pathlib.Path,
    **kwargs: Any,
) -> int:
    """Convert every ``*.json`` in ``src`` into ``dst``; returns the file count."""
    src = src.resolve()
    dst = dst.resolve()
    if src == dst:
        raise SystemExit(f"refusing to convert {src} in place; give a different --out")
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        (dst / p.name).write_text(
            json.dumps(convert_doc(doc, **kwargs), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="directory of v1 findings JSON (read-only)")
    ap.add_argument("--out", required=True, help="output directory (must differ from src)")
    ap.add_argument("--run-id", required=True, help="run id these findings belong to")
    ap.add_argument("--source", required=True, dest="source_id",
                    help="the workspace source_id these v1 findings read (v1 had one "
                         "implicit book and recorded no id, so it must be supplied)")
    ap.add_argument("--source-sha", required=True,
                    help="sha256 of the edition they were judged against")
    ap.add_argument("--mode", default="differential", choices=["differential", "attack"])
    ap.add_argument("--preset", default="")
    ap.add_argument("--repos", default="", help="comma-separated known repo ids")
    ap.add_argument("--repo-map", action="append", default=[], metavar="V1=COMPANION",
                    help="the v1 name of a repository and this workspace's name for it "
                         "(e.g. ours=tml-prefix; repeatable or comma-separated). Without it "
                         "the v1 names are kept, and rows naming a repo the workspace does "
                         "not have can never join its runs")
    ap.add_argument("--no-fold-no-weakness", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo_map = parse_repo_map(args.repo_map)
    except ValueError as exc:
        ap.error(str(exc))

    n = convert_tree(
        pathlib.Path(args.src).expanduser(),
        pathlib.Path(args.out).expanduser(),
        run_id=args.run_id,
        source_id=args.source_id,
        source_sha=args.source_sha,
        mode=args.mode,
        preset=args.preset,
        repos=[r for r in args.repos.split(",") if r],
        fold_no_weakness=not args.no_fold_no_weakness,
        repo_map=repo_map,
    )
    print(f"converted {n} file(s) from {args.src} to {args.out} "
          f"(v1 -> v2, run {args.run_id}, source {args.source_id})")
    if repo_map:
        print("  repo map: " + ", ".join(f"{k} -> {v}" for k, v in sorted(repo_map.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
