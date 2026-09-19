"""Join findings to an answer key and compute precision, recall and F1.

New code.  The join key is ``(source_id, repo, item, kind)`` and NOTHING else:
no run id, no finding id, no prose.  That is what makes one key score every
later run -- a model fleet, the mechanical detector, a re-run with a different
preset -- and what lets a key written against a fixture outlive the runs it was
written for.

Counting is per LOCUS, not per row.  Two workers that file the same complaint
about the same item are one true positive and not two, exactly as
``harvest/union.py`` collapses them; scoring rows instead would let a chatty
fleet buy precision by repeating itself.

What the numbers mean, and what they do not:

* they are measured against a FIXTURE whose defects were planted, so they are a
  statement about this fixture and this configuration, never about a real
  companion (references/method.md S10);
* a kind nothing reported has no precision -- the field is ``null``, not 0.0 --
  because there is no denominator.  Recall is 0.0, which is a measurement;
* a false positive that lands on a decoy is named as one, so "the fleet files
  CONVENTION differences as defects" is a countable claim rather than an
  impression.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from cflib import log  # noqa: E402
from cflib.state import now as iso_now  # noqa: E402
from harvest.tally import collapse_to_book_item  # noqa: E402
from score import answer_key as key_mod  # noqa: E402
from score.rows import kinds_for  # noqa: E402


def locus(source_id: str, repo: str, item: str, kind: str, kinds=None) -> tuple:
    """The run-independent key a finding and a key entry are joined on."""
    collapsed = collapse_to_book_item(item, kinds_for(kinds, source_id))
    return (str(source_id), str(repo), collapsed, str(kind or ""))


def _row_locus(row: Mapping, kinds=None) -> tuple:
    # rows.py has already collapsed the item; collapsing twice is a no-op, and
    # doing it here as well keeps this function usable on a hand-built row.
    return locus(row.get("source_id", ""), row.get("repo", ""), row.get("item", ""),
                 row.get("kind", ""), kinds)


def _entry_locus(entry: Mapping, kinds=None) -> tuple:
    return locus(entry.get("source_id", ""), entry.get("repo", ""), entry.get("item", ""),
                 entry.get("kind", ""), kinds)


def metrics(tp: int, fp: int, fn: int) -> dict:
    """Precision, recall and F1, with ``null`` where there is no denominator."""
    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall = (tp / (tp + fn)) if (tp + fn) else None
    if precision and recall:
        f1 = 2 * precision * recall / (precision + recall)
    elif precision is None and recall is None:
        f1 = None
    else:
        f1 = 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "reported": tp + fp,
        "planted": tp + fn,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
    }


def score(rows: Iterable[Mapping], key: Mapping, *, kinds=None) -> dict:
    """Score reported findings against an answer key.

    ``rows`` are the rows :mod:`score.rows` read from the corpus; ``key`` is a
    loaded answer key.  The result is the document ``cf score`` writes.
    """
    entries = list(key["entries"])
    defects = {_entry_locus(e, kinds): e for e in entries if e.get("expect") == "defect"}
    clean_at: dict[tuple, dict] = {}
    for entry in entries:
        if entry.get("expect") == "clean":
            where = _entry_locus(entry, kinds)[:3]
            clean_at.setdefault(where, entry)
    keyed_repos = {str(e.get("repo") or "") for e in entries}

    reported: dict[tuple, dict] = {}
    for row in rows:
        ident = _row_locus(row, kinds)
        seen = reported.setdefault(ident, {"locus": ident, "rows": 0, "runs": set(),
                                           "decls": set(), "severities": set()})
        seen["rows"] += 1
        seen["runs"].add(str(row.get("run") or ""))
        seen["decls"].update(row.get("decls") or [])
        if row.get("severity"):
            seen["severities"].add(str(row["severity"]))

    hits, false_positives = [], []
    for ident, seen in sorted(reported.items()):
        source_id, repo, item, kind = ident
        common = {
            "source_id": source_id, "repo": repo, "item": item, "kind": kind,
            "rows": seen["rows"], "runs": sorted(seen["runs"]),
            "decls": sorted(seen["decls"]),
        }
        entry = defects.get(ident)
        if entry is not None:
            expected = list(entry.get("decls") or [])
            hits.append({
                **common,
                "id": key_mod.entry_id(entry),
                "class": entry.get("class") or "",
                "expected_decls": expected,
                # A worker can name the right item and the wrong declaration.
                # That is still a hit; the disagreement is recorded, not hidden.
                "decls_agree": (not expected) or bool(set(expected) & set(common["decls"])),
            })
            continue
        decoy = clean_at.get(ident[:3])
        false_positives.append({
            **common,
            "on_decoy": decoy is not None,
            "decoy_class": (decoy or {}).get("class", ""),
            "why_clean": (decoy or {}).get("why", ""),
            "repo_in_key": repo in keyed_repos,
        })

    misses = [
        {"id": key_mod.entry_id(entry), "source_id": entry["source_id"], "repo": entry["repo"],
         "item": entry["item"], "kind": entry["kind"], "class": entry.get("class") or "",
         "decls": list(entry.get("decls") or []), "why": entry.get("why", "")}
        for ident, entry in sorted(defects.items(), key=lambda kv: kv[0])
        if ident not in reported
    ]

    hit_ids = {h["id"] for h in hits}
    by_kind = _by_kind(defects, hits, false_positives)
    by_class = _by_class(defects, hit_ids)
    totals = metrics(len(hits), len(false_positives), len(misses))
    return {
        "generated_at": iso_now(),
        "answer_key": key_mod.summary(key),
        "totals": totals,
        "by_kind": by_kind,
        "by_class": by_class,
        "decoys_reported": sum(1 for fp in false_positives if fp["on_decoy"]),
        "rows_read": sum(s["rows"] for s in reported.values()),
        "loci_reported": len(reported),
        "hits": hits,
        "misses": misses,
        "false_positives": false_positives,
    }


def _by_kind(defects: Mapping[tuple, Mapping], hits: Sequence[Mapping],
             false_positives: Sequence[Mapping]) -> dict:
    """One metrics block per finding kind that either side names."""
    kinds = ({str(ident[3]) for ident in defects}
             | {h["kind"] for h in hits} | {f["kind"] for f in false_positives})
    out = {}
    for kind in sorted(k for k in kinds if k):
        tp = sum(1 for h in hits if h["kind"] == kind)
        fp = sum(1 for f in false_positives if f["kind"] == kind)
        planted = sum(1 for ident in defects if ident[3] == kind)
        out[kind] = metrics(tp, fp, planted - tp)
    return out


def _by_class(defects: Mapping[tuple, Mapping], hit_ids: set) -> dict:
    """Caught / planted per defect class -- recall only; a class has no FPs."""
    out: dict[str, dict] = {}
    for entry in defects.values():
        name = entry.get("class") or "(unclassed)"
        block = out.setdefault(name, {"planted": 0, "caught": 0, "kind": entry.get("kind", "")})
        block["planted"] += 1
        if key_mod.entry_id(entry) in hit_ids:
            block["caught"] += 1
    for block in out.values():
        block["recall"] = round(block["caught"] / block["planted"], 4) if block["planted"] else None
    return dict(sorted(out.items()))


def _pct(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def render(result: Mapping) -> str:
    """The table ``cf score`` prints."""
    lines = []
    summary = result["answer_key"]
    lines.append(
        f"answer key: {summary['defects']} planted defect(s), {summary['decoys']} decoy(s), "
        f"{summary['clean']} clean locus/loci over {', '.join(summary['repos'])}")
    rows = []
    for kind, block in result["by_kind"].items():
        rows.append([kind, block["planted"], block["reported"], block["true_positives"],
                     block["false_positives"], block["false_negatives"],
                     _pct(block["precision"]), _pct(block["recall"]), _pct(block["f1"])])
    total = result["totals"]
    rows.append(["ALL", total["planted"], total["reported"], total["true_positives"],
                 total["false_positives"], total["false_negatives"],
                 _pct(total["precision"]), _pct(total["recall"]), _pct(total["f1"])])
    lines.append(log.format_table(
        ["kind", "planted", "reported", "tp", "fp", "fn", "precision", "recall", "f1"], rows))
    if result["by_class"]:
        lines.append("")
        lines.append(log.format_table(
            ["defect class", "kind", "caught", "planted", "recall"],
            [[name, block["kind"], block["caught"], block["planted"], _pct(block["recall"])]
             for name, block in result["by_class"].items()]))
    if result["misses"]:
        lines.append("")
        lines.append("missed (planted, not reported):")
        for miss in result["misses"]:
            lines.append(f"  {miss['repo']} {miss['item']} {miss['kind']} "
                         f"[{miss['class'] or 'unclassed'}]")
    if result["false_positives"]:
        lines.append("")
        lines.append("false positives (reported, not planted):")
        for fp in result["false_positives"]:
            tail = f" -- on the {fp['decoy_class']} decoy" if fp["on_decoy"] else ""
            if not fp["repo_in_key"]:
                tail = " -- this repo is not in the answer key"
            lines.append(f"  {fp['repo']} {fp['item']} {fp['kind']}{tail}")
    return "\n".join(lines)
