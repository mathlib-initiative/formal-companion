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
from typing import Any, Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify_findings import finding_id  # noqa: E402  (same-directory helper)

V1_FAITHFUL_WORDS = {"none", "unsure", "all", "all_differ", "all differ", "n/a", ""}


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
    repos: list[str],
    lift_witness: bool,
) -> dict[str, Any]:
    """Convert one v1 disagreement; unknown keys are preserved untouched."""
    out = dict(g)
    decls = [d for d in (g.get("unfaithful_decls") or []) if isinstance(d, str)]
    out["unfaithful_decls"] = decls
    faithful, note = split_faithful(g.get("faithful"), list(g.get("repos_involved") or []), repos)
    out["faithful"] = faithful
    if note:
        out["faithful_note"] = note
    kind = str(g.get("kind") or "")
    out["finding_id"] = finding_id(run_id, str(out.get("source_id") or ""), item, kind, decls)
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
    book_sha: str,
    mode: str = "differential",
    preset: str = "",
    repos: Iterable[str] = (),
    fold_no_weakness: bool = True,
    lift_witness: bool | None = None,
) -> dict[str, Any]:
    """Convert one v1 findings document to v2. Pure; never touches the disk."""
    repos = list(repos)
    if lift_witness is None:
        lift_witness = mode == "attack"
    out = dict(doc)
    out["schema_version"] = 2
    out["run_id"] = run_id
    out["book_sha"] = book_sha
    out["mode"] = mode
    if preset:
        out["preset"] = preset
    item = str(doc.get("item") or "")
    matches = []
    for m in doc.get("matches") or []:
        m = dict(m) if isinstance(m, dict) else m
        if fold_no_weakness and isinstance(m, dict) and m.get("verdict") == "NO_WEAKNESS_FOUND":
            m["verdict"] = "NO_DEFECT_FOUND"
            m["v1_verdict"] = "NO_WEAKNESS_FOUND"
        matches.append(m)
    out["matches"] = matches
    out["disagreements"] = [
        convert_disagreement(
            g, item=item, run_id=run_id, repos=repos, lift_witness=lift_witness
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
    ap.add_argument("--book-sha", required=True, help="sha256 of the edition they were judged against")
    ap.add_argument("--mode", default="differential", choices=["differential", "attack"])
    ap.add_argument("--preset", default="")
    ap.add_argument("--repos", default="", help="comma-separated known repo ids")
    ap.add_argument("--no-fold-no-weakness", action="store_true")
    args = ap.parse_args(argv)

    n = convert_tree(
        pathlib.Path(args.src).expanduser(),
        pathlib.Path(args.out).expanduser(),
        run_id=args.run_id,
        book_sha=args.book_sha,
        mode=args.mode,
        preset=args.preset,
        repos=[r for r in args.repos.split(",") if r],
        fold_no_weakness=not args.no_fold_no_weakness,
    )
    print(f"converted {n} file(s) from {args.src} to {args.out} (v1 -> v2, run {args.run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
