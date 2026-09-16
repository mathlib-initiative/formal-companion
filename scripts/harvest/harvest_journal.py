"""Recover a killed local Workflow run's results from its journal.

Ported from scratchpad/recovered/b174aa13_L5690_Bash.txt -- the cell that saved
batch 1 after the local fleet died on the subscription session limit at 129 of
225 agents.  It reads the Workflow's ``journal.jsonl`` and ``agent-*.meta.json``
labels, pulls out the per-item results and the challenge rulings, and writes
both the recovered findings and the list of items still to do.

That recipe is the local path's answer to two things tend gives the server path
for free: durable state and resume.  A local fleet has neither, and the design
note is blunt about it -- treat resume as the normal path, not recovery.

Generalised here:

* results are written ONE FILE PER ITEM into a corpus directory, so
  ``tally.py``/``union.py`` read a recovered local run exactly like a server
  run (the original wrote a single ``harvest.partial.json`` that nothing else
  could read);
* challenge results are linked to findings by the item named in the agent
  label rather than by a 22-character prefix of it, with the old prefix rule
  kept as a fallback and the method recorded;
* the queue file and output paths are arguments, not hard-coded scratchpad
  paths.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in (None, ""):  # `python3 scripts/harvest/harvest_journal.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest._compat import CfError, cf_items, iso_now, read_json, write_json

#: the original's label key: item with spaces underscored, truncated
LEGACY_PREFIX_LEN = 22


def agent_labels(workflow_dir: Path) -> dict[str, str]:
    """{agentId: label} from ``agent-*.meta.json`` (missing/broken files skipped)."""
    out: dict[str, str] = {}
    for meta in sorted(Path(workflow_dir).glob("agent-*.meta.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        agent_id = meta.name.split(".")[0].replace("agent-", "")
        out[agent_id] = str(data.get("label", ""))
    return out


def _result_value(entry: dict):
    """The agent's returned value, whether the journal nested it or stringified it."""
    value = entry.get("value") if "value" in entry else entry.get("result")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    return value if isinstance(value, dict) else None


def read_journal(workflow_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """Split a journal into ``{item: result}`` and the list of challenge rulings.

    An item result is recognised by carrying ``item`` plus a defect list; a
    challenge by carrying ``ruling_survives``.  Anything else is ignored, so a
    journal with extra agent kinds (lenses, critics) recovers cleanly.
    """
    path = Path(workflow_dir) / "journal.jsonl"
    if not path.exists():
        raise CfError(f"no journal at {path}",
                      hint="pass the workflow directory under "
                           "~/.claude/projects/<project>/subagents/workflows/wf_*")
    labels = agent_labels(workflow_dir)
    items: dict[str, dict] = {}
    challenges: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("type") != "result":
            continue
        value = _result_value(entry)
        if value is None:
            continue
        label = labels.get(entry.get("agentId", ""), "")
        if "item" in value and ("disagreements" in value or "findings" in value):
            items[str(value["item"])] = value
        elif "ruling_survives" in value:
            challenges.append({**value, "_label": label})
    return items, challenges


def _label_item(label: str) -> str:
    """The item a challenge label names: ``chal0:Exercise_1.2.4`` -> ``Exercise 1.2.4``."""
    tail = label.split(":", 1)[-1].strip()
    return tail.replace("_", " ").strip()


def link_challenges(items: dict[str, dict], challenges: Sequence[dict]) -> dict[str, list[dict]]:
    """{item: [challenge, ...]}, by item name first and legacy prefix second."""
    by_item: dict[str, list[dict]] = {item: [] for item in items}
    for challenge in challenges:
        label_item = _label_item(challenge.get("_label", ""))
        method = ""
        target = ""
        for item in items:
            if label_item and label_item.lower() == item.lower():
                target, method = item, "label-item"
                break
        if not target:
            key = challenge.get("_label", "").split(":", 1)[-1]
            for item in items:
                if key and item.replace(" ", "_")[:LEGACY_PREFIX_LEN] == key:
                    target, method = item, "legacy-prefix"
                    break
        if target:
            by_item[target].append({
                "survives": challenge.get("ruling_survives"),
                "evidence": challenge.get("evidence", ""),
                "corrected": challenge.get("corrected_ruling", ""),
                "label": challenge.get("_label", ""),
                "link_method": method,
            })
    return by_item


def _slug(item: str) -> str:
    """Corpus filename for an item (the attack runs' dash style)."""
    parsed, _reject = cf_items.try_parse(item)
    if parsed is not None:
        return cf_items.slug(parsed, "dash")
    return re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-") or "item"


def write_corpus(items: dict[str, dict], challenges: dict[str, list[dict]],
                 dest: Path, run_id: str) -> list[str]:
    """Write one findings file per item into *dest*; returns the file names."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for item, payload in sorted(items.items()):
        record = dict(payload)
        record.setdefault("item", item)
        record["_meta"] = {
            "recovered_from": "workflow journal",
            "run_id": run_id,
            "recovered_at": iso_now(),
            "challenges": challenges.get(item, []),
        }
        name = _slug(item) + ".json"
        write_json(dest / name, record, indent=1, sort_keys=True)
        written.append(name)
    return written


def remaining(queue_path: Path, done: Iterable[str]) -> list[dict]:
    """Queue entries whose item is not among the recovered results."""
    args = read_json(queue_path, default=[])
    done_set = {str(d) for d in done}
    return [a for a in args if isinstance(a, dict) and str(a.get("item")) not in done_set]


def summarise(items: dict[str, dict], challenges: dict[str, list[dict]]) -> dict:
    """Counts the operator needs before deciding whether to resume or re-run."""
    defects = [d for payload in items.values()
               for key in ("disagreements", "findings")
               for d in (payload.get(key) or [])]
    challenged = {i: c for i, c in challenges.items() if c}
    return {
        "items": len(items),
        "book_located": sum(1 for p in items.values() if p.get("book_located")),
        "defects": len(defects),
        "challenge_results": sum(len(c) for c in challenges.values()),
        "items_challenged": len(challenged),
        "items_survived_all": sum(1 for c in challenged.values() if all(x["survives"] for x in c)),
        "items_refuted_by_any": sum(1 for c in challenged.values() if not all(x["survives"] for x in c)),
        "link_methods": sorted({x["link_method"] for c in challenges.values() for x in c}),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workflow_dir", help="the wf_* directory holding journal.jsonl")
    parser.add_argument("--dest", required=True, help="corpus/<run-id> directory to write")
    parser.add_argument("--run-id", default="local-recovered")
    parser.add_argument("--queue", help="queue args JSON, to compute what is left")
    parser.add_argument("--remaining-out", help="where to write the remaining queue")
    args = parser.parse_args(argv)

    items, challenge_rows = read_journal(Path(args.workflow_dir))
    challenges = link_challenges(items, challenge_rows)
    written = write_corpus(items, challenges, Path(args.dest), args.run_id)
    report = {"generated_at": iso_now(), "run_id": args.run_id,
              "workflow_dir": str(args.workflow_dir), "files": len(written),
              **summarise(items, challenges)}
    if args.queue:
        todo = remaining(Path(args.queue), items)
        report["remaining"] = len(todo)
        if args.remaining_out:
            write_json(Path(args.remaining_out), todo, indent=1)
    write_json(Path(args.dest) / "journal-harvest.json", report, indent=1, sort_keys=True)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
