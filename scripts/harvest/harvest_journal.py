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
* challenge results are linked to findings by a KEY the challenge row itself
  carries, falling back to the agent label's item and last of all to the old
  22-character prefix, with the method recorded;
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

#: The corpus schema every finding file must declare, because the out-of-tree
#: gate refuses a file whose ``schema_version`` is not its own
#: (``gate/verify_findings.py`` vs ``plan/presets.gate_config``).  A recovered
#: local run is gated by exactly that command, so the recovery has to stamp it.
CORPUS_SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# the label contract
#
# A Workflow journal records an agent's label, not its role, so the label IS the
# join between a challenge ruling and the finding it refutes.  It was a
# convention, and the convention drifted: fleet-local emitted
# ``chal0:Exercise 1.2.4#0`` while this module parsed ``chal0:Exercise_1.2.4``,
# so every refuter verdict was dropped and `summarise` reported zeros.  It is a
# contract now, in one place, checked against the shipped scripts by
# ``tests/test_rf_localfleet.py``.

#: The grammar every ``workflows/*.js`` label follows and :func:`parse_label`
#: parses.  ``key`` is the LAST colon-separated field, so an optional ``slot``
#: (a lens name, a reader number) can never eat it -- but an item name may itself
#: contain a colon, so the last field is a candidate and not the answer; see
#: :func:`label_item_candidates`.
LABEL_GRAMMAR = "<role><index?>:[<slot>:]<key>      key = <item>[#<n>] | <finding_id>"

#: The literal ``label:`` expressions the shipped scripts emit, per workflow.
#: The test asserts these strings really occur in ``workflows/*.js`` AND that
#: the grammar above round-trips each of them: ``tests/test_harvest.py`` pinned
#: only the Python side, which is how a label the script never emitted stayed
#: green over a broken join for a whole generation of the corpus.
JS_LABEL_EXPRESSIONS: dict[str, tuple[str, ...]] = {
    "fleet-local.workflow.js": (
        "'work:' + t.item",
        "'chal' + k + ':' + did",
    ),
    "challenge.workflow.js": (
        "'chal' + k + ':' + f.item",
    ),
    "adjudicate.workflow.js": (
        "'F' + i + ':' + l + ':' + f.item",
        "'F' + i + ':reconcile:' + f.item",
        "'F' + i + ':skeptic-content:' + f.item",
        "'F' + i + ':skeptic-convention:' + f.item",
        "'D' + i + ':reader' + (k + 1) + ':' + (d.label || d.item || '')",
        "'D' + i + ':reconcile:' + (d.label || d.item || '')",
        "'U' + i + ':' + (u.label || u.item || '')",
        "'A' + i + ':' + (a.title || '').slice(0, 24)",
        "'critic'",
    ),
    "verify-claims.workflow.js": (
        "'verify:' + (l.key || i)",
        "'critic'",
    ),
}

_LABEL_HEAD = re.compile(r"^([A-Za-z]+)(\d*)$")
#: ``<item>#<n>`` -- the stable synthetic id of one disagreement inside an item
_KEY_INDEX = re.compile(r"^(?P<item>.+)#(?P<index>\d+)$")


def split_disagreement_id(key: str) -> tuple[str, int | None]:
    """``("Exercise 1.2.4", 3)`` from ``Exercise 1.2.4#3``; index ``None`` if absent.

    A disagreement id is ``<item>#<index>`` and nothing else.  Parsing one as a
    LABEL truncated every item that contains a colon -- the corpus holds such an
    item -- so the id is split here and never sent through :func:`parse_label`.
    """
    keyed = _KEY_INDEX.match(str(key or ""))
    if keyed is None:
        return str(key or ""), None
    return keyed.group("item"), int(keyed.group("index"))


def parse_label(label: str) -> dict:
    """Split an agent label into ``{role, index, slot, key, item, disagreement}``.

    ``key`` is the LAST colon-separated field, verbatim (it may be a
    ``finding_id``); ``item`` is ``key`` with a ``#<n>`` disagreement suffix
    removed and the legacy underscores turned back into spaces, so both
    ``chal0:Exercise 1.2.4#3`` and the original's ``chal0:Exercise_1.2.4`` name
    ``Exercise 1.2.4``.

    ``key_full``/``item_full`` are the same two things taken from everything
    after the FIRST colon.  They differ only when the item itself contains a
    colon, and then they are the correct reading: use
    :func:`label_item_candidates`, which tries both (and the suffixes between
    them), rather than picking one here.
    """
    raw = str(label or "")
    parts = raw.split(":")
    head = parts[0].strip()
    match = _LABEL_HEAD.match(head)
    role = match.group(1) if match else head
    index = int(match.group(2)) if match and match.group(2) else None
    key = parts[-1].strip() if len(parts) > 1 else ""
    key_full = ":".join(parts[1:]).strip()
    slot = ":".join(parts[1:-1]).strip()
    item, disagreement = split_disagreement_id(key)
    item_full, _ = split_disagreement_id(key_full)
    return {
        "role": role,
        "index": index,
        "slot": slot,
        "key": key,
        "key_full": key_full,
        "item": item.replace("_", " ").strip(),
        "item_full": item_full.replace("_", " ").strip(),
        "disagreement": disagreement,
        "raw": raw,
    }


def label_item_candidates(label: str) -> list[str]:
    """Every item name a label could name, best first.

    The key is the LAST colon-separated field, so an optional slot (a lens name,
    a reader number) can never eat it.  But an item may itself contain a colon
    -- the baseline corpus has one, an in-text convention whose parenthetical
    names an exercise -- and then the last field is a fragment and the right
    answer is a longer suffix.  So every suffix is a candidate, shortest first,
    each in both the verbatim and the legacy underscored spelling.

    A candidate that names no recovered item simply does not match; the first
    that does decides the join, and the method is recorded on the ruling.
    """
    parts = str(label or "").split(":")
    out: list[str] = []
    for start in range(len(parts) - 1, 0, -1):
        key, _index = split_disagreement_id(":".join(parts[start:]).strip())
        for candidate in (key.strip(), key.replace("_", " ").strip()):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


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


def _row_kind(value: dict) -> str:
    """Which agent produced this result, from the shape of the value alone.

    The two adjudication workflows return none of ``item`` + ``disagreements``,
    so a journal of either recovered nothing at all -- a killed ``cf adjudicate``
    or ``cf adjudicate --challenge-only`` run is the most expensive thing the
    skill launches (6 agents per finding), and it had no recovery path.  Each
    shape below is a schema in ``workflows/*.js``; anything unrecognised is kept
    as ``other`` rather than dropped, so an operator can see what a journal held.
    """
    if "item" in value and ("disagreements" in value or "findings" in value
                            or "matches" in value):
        return "item"
    if "ruling_survives" in value:
        return "challenge"
    if "overturned" in value:                       # adjudicate SKEPTIC_SCHEMA
        return "skeptic"
    if "per_definition" in value:                   # adjudicate DEF_READ_SCHEMA
        return "definition"
    if "classification" in value and "library_standin" in value:
        return "uncovered"                          # adjudicate UNCOVERED_SCHEMA
    if "verdict" in value and "rationale" in value:  # adjudicate RECONCILE_SCHEMA
        return "reconciled"
    if "verdict" in value and ("book_claim" in value or "lean_claim" in value):
        return "lens"                               # adjudicate LENS_SCHEMA
    if "claims" in value:                           # verify-claims CLAIMS_SCHEMA
        return "claims"
    return "other"


#: the row kinds :func:`read_rows` recovers, in the order a report lists them
ROW_KINDS = ("item", "challenge", "lens", "reconciled", "skeptic", "definition",
             "uncovered", "claims", "other")


def read_rows(workflow_dir: Path) -> dict[str, object]:
    """Every recoverable row in a journal, split by the shape that produced it.

    ``items`` is ``{item: result}``; every other kind is a list of the returned
    values with the agent's ``_label`` and parsed ``_role`` attached, so a killed
    adjudication or challenge run can be resumed from what its journal holds.
    """
    path = Path(workflow_dir) / "journal.jsonl"
    if not path.exists():
        raise CfError(f"no journal at {path}",
                      hint="pass the workflow directory under "
                           "<claude-projects>/<project>/subagents/workflows/wf_*")
    labels = agent_labels(workflow_dir)
    items: dict[str, dict] = {}
    rows: dict[str, list[dict]] = {k: [] for k in ROW_KINDS if k != "item"}
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
        kind = _row_kind(value)
        if kind == "item":
            items[str(value["item"])] = value
            continue
        rows[kind].append({**value, "_label": label, "_role": parse_label(label)["role"]})
    return {"items": items, **rows}


def read_journal(workflow_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """``({item: result}, [challenge ruling, ...])`` -- the two kinds harvest writes.

    The rest of what a journal holds is available from :func:`read_rows`.
    """
    rows = read_rows(workflow_dir)
    return rows["items"], rows["challenge"]            # type: ignore[return-value]


def _challenge_keys(challenge: dict) -> list[tuple[str, str]]:
    """``[(method, item), ...]`` to try for one challenge row, best first.

    The row's OWN ``disagreement_id`` comes first: ``fleet-local.workflow.js``
    makes the refuter copy it through, so the join no longer depends on a label
    surviving intact.  It is split by :func:`split_disagreement_id`, not by the
    label parser -- an id is not a label, and reading it as one truncated an
    item whose name contains a colon.  The label is the fallback, and the
    original's truncated prefix the fallback of last resort.
    """
    out: list[tuple[str, str]] = []
    did = str(challenge.get("disagreement_id") or "")
    if did:
        item, _index = split_disagreement_id(did)
        out.append(("disagreement-id", item))
    row_item = str(challenge.get("item") or "")
    if row_item:
        out.append(("row-item", row_item))
    for candidate in label_item_candidates(challenge.get("_label", "")):
        out.append(("label-item", candidate))
    return out


def link_challenges(items: dict[str, dict], challenges: Sequence[dict],
                    *, strict: bool = True) -> dict[str, list[dict]]:
    """{item: [challenge, ...]} by the row's own key, then the label, then the prefix.

    With *strict* (the default) a journal that holds challenge rulings none of
    which attach to a recovered item is a refusal, not a row of zeros: the
    adversarial half of the run is on disk and unread, and SKILL.md hard rule 5
    will then block the whole campaign for want of verdicts that already exist.
    """
    by_item: dict[str, list[dict]] = {item: [] for item in items}
    lowered = {item.lower(): item for item in items}
    unmatched: list[str] = []
    for challenge in challenges:
        method = ""
        target = ""
        for candidate_method, candidate in _challenge_keys(challenge):
            target = lowered.get(candidate.lower(), "")
            if target:
                method = candidate_method
                break
        if not target:
            key = str(challenge.get("_label", "")).split(":")[-1]
            for item in items:
                if key and item.replace(" ", "_")[:LEGACY_PREFIX_LEN] == key:
                    target, method = item, "legacy-prefix"
                    break
        if not target:
            unmatched.append(str(challenge.get("disagreement_id")
                                 or challenge.get("_label", "")))
            continue
        by_item[target].append({
            "survives": challenge.get("ruling_survives"),
            "evidence": challenge.get("evidence", ""),
            "corrected": challenge.get("corrected_ruling", ""),
            "label": challenge.get("_label", ""),
            "disagreement_id": str(challenge.get("disagreement_id") or ""),
            "link_method": method,
        })
    if strict and items and challenges and not any(by_item.values()):
        raise CfError(
            f"{len(challenges)} challenge ruling(s) in the journal match none of the "
            f"{len(items)} recovered item(s): " + ", ".join(sorted(set(unmatched))[:6]),
            hint=f"the label/id contract is `{LABEL_GRAMMAR}` "
                 "(scripts/harvest/harvest_journal.py); to recover the items anyway and "
                 "keep the orphaned rulings on record, add `--allow-unlinked-challenges` "
                 "to `cf harvest <run-id> --journal <wf-dir>` (the command that also "
                 "writes the receipt, runs the gate and recomputes the tally) or to "
                 "`python3 scripts/harvest/harvest_journal.py <wf-dir> --dest <corpus-dir>`",
        )
    return by_item


def _slug(item: str, source_id: str = "") -> str:
    """Corpus filename for an item (the attack runs' dash style).

    Prefixed with the source when the journal names one: two sources' corpus
    files would otherwise collide on ``exercise-1-3-19.json`` and the second
    written would silently replace the first.
    """
    parsed, _reject = cf_items.try_parse(item)
    if parsed is not None:
        tail = cf_items.slug(parsed, "dash")
    else:
        tail = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-") or "item"
    prefix = cf_items.source_slug(source_id) if source_id else ""
    return f"{prefix}--{tail}" if prefix else tail


def write_corpus(items: dict[str, dict], challenges: dict[str, list[dict]],
                 dest: Path, run_id: str, source_id: str = "",
                 source_sha: str = "",
                 schema_version: int = CORPUS_SCHEMA_VERSION) -> list[str]:
    """Write one findings file per item into *dest*; returns the file names.

    Each record is stamped with the source it was judged against -- the
    journal's own value when it has one, else the caller's -- because the gate
    that runs over this corpus picks a text by ``source_id`` and a file without
    one is refused.

    ``schema_version`` and ``run_id`` are stamped for the same reason: the
    command this recovery prints next is ``verify_findings.py``, which refuses
    every file whose ``schema_version`` is not the gate's.  Filling only the two
    source keys meant a recovered local corpus failed the gate wholesale, on the
    very command the tool told the operator to run.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for item, payload in sorted(items.items()):
        record = dict(payload)
        record.setdefault("item", item)
        # the worker emits these; a killed or older agent may not, and the gate
        # rejects a file that is missing any of the four
        record.setdefault("schema_version", int(schema_version))
        if run_id and not record.get("run_id"):
            record["run_id"] = run_id
        sid = str(record.get("source_id") or source_id or "")
        if sid:
            record["source_id"] = sid
        sha = str(record.get("source_sha") or source_sha or "")
        if sha:
            record["source_sha"] = sha
        record["_meta"] = {
            "recovered_from": "workflow journal",
            "run_id": run_id,
            "source_id": sid,
            "source_sha": sha,
            "recovered_at": iso_now(),
            "challenges": challenges.get(item, []),
        }
        name = _slug(item, sid) + ".json"
        write_json(dest / name, record, indent=1, sort_keys=True)
        written.append(name)
    return written


def remaining(queue_path: Path, done: Iterable[str]) -> list[dict]:
    """Queue entries whose item is not among the recovered results."""
    args = read_json(queue_path, default=[])
    done_set = {str(d) for d in done}
    return [a for a in args if isinstance(a, dict) and str(a.get("item")) not in done_set]


def summarise(items: dict[str, dict], challenges: dict[str, list[dict]],
              challenge_rows: Sequence[dict] = ()) -> dict:
    """Counts the operator needs before deciding whether to resume or re-run.

    ``challenge_rows`` is the unlinked list :func:`read_journal` returned.  It is
    what turns "no challenge results" into "N rulings were seen and M of them
    attached to nothing": a row of zeros is indistinguishable from a run whose
    challenge stage never started, and the two call for opposite actions.
    """
    defects = [d for payload in items.values()
               for key in ("disagreements", "findings")
               for d in (payload.get(key) or [])]
    challenged = {i: c for i, c in challenges.items() if c}
    linked = sum(len(c) for c in challenges.values())
    out = {
        "items": len(items),
        "sources": sorted({str(p.get("source_id") or "") for p in items.values()}),
        "book_located": sum(1 for p in items.values() if p.get("book_located")),
        "defects": len(defects),
        "challenge_results": linked,
        "items_challenged": len(challenged),
        "items_survived_all": sum(1 for c in challenged.values() if all(x["survives"] for x in c)),
        "items_refuted_by_any": sum(1 for c in challenged.values() if not all(x["survives"] for x in c)),
        "link_methods": sorted({x["link_method"] for c in challenges.values() for x in c}),
    }
    if challenge_rows:
        out["challenge_rows_seen"] = len(challenge_rows)
        out["challenges_unlinked"] = len(challenge_rows) - linked
        if out["challenges_unlinked"]:
            attached = {str(x.get("label") or "") for c in challenges.values() for x in c}
            out["unmatched_labels"] = sorted({
                str(r.get("disagreement_id") or r.get("_label") or "")
                for r in challenge_rows if str(r.get("_label") or "") not in attached
            })[:12]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workflow_dir", help="the wf_* directory holding journal.jsonl")
    parser.add_argument("--dest", required=True, help="corpus/<run-id> directory to write")
    parser.add_argument("--run-id", default="local-recovered")
    parser.add_argument("--queue", help="queue args JSON, to compute what is left")
    parser.add_argument("--remaining-out", help="where to write the remaining queue")
    parser.add_argument("--rows-out",
                        help="every recoverable journal row by kind (lenses, skeptics, "
                             "definitions): the resume input for a killed adjudication run")
    parser.add_argument("--allow-unlinked-challenges", action="store_true",
                        help="recover the items even when no challenge ruling attaches to "
                             "one; the orphans are counted in the report")
    args = parser.parse_args(argv)

    rows = read_rows(Path(args.workflow_dir))
    items: dict[str, dict] = rows["items"]              # type: ignore[assignment]
    challenge_rows: list[dict] = rows["challenge"]      # type: ignore[assignment]
    challenges = link_challenges(items, challenge_rows,
                                 strict=not args.allow_unlinked_challenges)
    written = write_corpus(items, challenges, Path(args.dest), args.run_id)
    report = {"generated_at": iso_now(), "run_id": args.run_id,
              "workflow_dir": str(args.workflow_dir), "files": len(written),
              "rows": {k: len(v) for k, v in rows.items() if k != "items" and v},
              **summarise(items, challenges, challenge_rows)}
    if args.rows_out:
        write_json(Path(args.rows_out), rows, indent=1)
    if args.queue:
        todo = remaining(Path(args.queue), items)
        report["remaining"] = len(todo)
        if args.remaining_out:
            write_json(Path(args.remaining_out), todo, indent=1)
    write_json(Path(args.dest) / "journal-harvest.json", report, indent=1, sort_keys=True)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    # Same rendering as `scripts/cf.py`: a refusal here is a one-line error plus
    # the hint that names the flag settling it.  Without this the new
    # unlinked-challenge refusal printed a traceback, so the escape it names was
    # invisible exactly where it applies.
    try:
        sys.exit(main())
    except CfError as exc:
        print(exc.render(), file=sys.stderr)
        sys.exit(exc.exit_code)
