"""The ``cost`` block of a run's RECEIPT.json, and the per-task figures read from it.

New code for milestone M0a (docs/proposals/pdf-to-companion.md section 5.1),
after issue #11: "have `cf harvest` read those transcripts' usage lines for
the run's worktree directories, write the totals and the priced cost into
RECEIPT.json next to the counts, and let `cf tally`/`cf report` print cost
per task".

:func:`cost_block` is what `cf harvest --transcripts <path>` and `cf cost`
compute; :func:`run_cost` is what `cf tally` and `cf report` read back from a
harvested corpus.  The task count a per-task figure divides by comes from the
``run.yaml`` copy the harvest left in the corpus (``tasks.count``), or, for a
corpus that has none, from the number of findings documents that landed; the
figure says which (``tasks_from``), because the two are not the same number
when a task produced no findings file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cflib.errors import CfError
from cflib.jsonio import read_json
from cflib.yamlio import read_yaml
from cost import pricing
from cost import usage as usage_mod
from harvest.harvest import read_receipt

#: the receipt key
COST_KEY = "cost"


def source_label(path: str | Path) -> str:
    """What the receipt records of the path it priced: its last component only.

    A receipt is committed to the workspace repository and may be shared, so
    it never carries the machine's directory layout (a home directory names
    the user).  ``usage.jsonl`` and ``/srv/run-a/transcripts`` record as
    ``usage.jsonl`` and ``transcripts``; ``.`` records as the name of the
    directory it stands for.
    """
    p = Path(path)
    return p.name if p.name not in ("", ".", "..") else (p.resolve().name or ".")


def empty_warning(block: Mapping[str, Any]) -> str | None:
    """A warning when a cost block counted no response, else None.

    A fleet that ran produced responses, so zero is an empty or wrong path or
    logs the reader could not read, never a measured $0.  `cf harvest` and
    `cf cost` print this; `stage_receipt.sum_fleets` refuses such a block.
    """
    if int(block.get("message_count") or 0) > 0:
        return None
    files = int(block.get("transcript_count") or 0)
    if not files:
        return ("no log file (*.jsonl, *.jsonl.gz) was found under the transcripts path, "
                "so nothing was priced and $0.00 is not a measurement")
    return (f"{files} log file(s) read and no response counted "
            f"({int(block.get('skipped_without_usage') or 0)} message(s) without usage, "
            f"{int(block.get('unreadable_lines') or 0)} unreadable line(s)), so $0.00 is not "
            f"a measurement")


def cost_block(path: str | Path, *, table: Mapping[str, Mapping[str, Any]], table_source: str,
               model: str | None = None) -> dict:
    """Read every log under *path*, price it, and return the receipt's ``cost`` block."""
    try:
        totals = usage_mod.read_path(path, model=model)
    except FileNotFoundError as exc:
        raise CfError(str(exc),
                      hint="pass a directory of the agents' transcripts or rollout logs, one "
                           "log file, or a usage export written by `cf cost --export`")
    try:
        priced = pricing.price(totals.tokens, table)
    except ValueError as exc:
        raise CfError(str(exc), hint="fix workspace.yaml budget.prices (or the --prices file)")
    read = totals.as_dict()
    return {
        "source": {"path": source_label(path), "files": read["files"]},
        "tokens": read["tokens"],
        "total_tokens": pricing.total_tokens(read["tokens"]),
        "usd": priced["usd"],
        "usd_by_model": priced["usd_by_model"],
        "priced": priced["priced"],
        "unpriced": priced["unpriced"],
        "unpriced_tokens": priced["unpriced_tokens"],
        "price_table_sha256": priced["price_table_sha256"],
        "price_table_source": table_source,
        "transcript_count": read["transcript_count"],
        "message_count": read["message_count"],
        "duplicate_lines": read["duplicate_lines"],
        "repeated_events": read["repeated_events"],
        "skipped_without_usage": read["skipped_without_usage"],
        "unreadable_lines": read["unreadable_lines"],
    }


def describe(block: Mapping[str, Any], indent: str = "  ") -> list[str]:
    """The block as lines a terminal can show; every number is a key of it."""
    lines = [f"{indent}cost: {pricing.format_usd(float(block.get('usd') or 0.0))} priced over "
             f"{block.get('message_count', 0)} response(s) in {block.get('transcript_count', 0)} "
             f"file(s)"]
    tokens = block.get("tokens") or {}
    usd_by_model = block.get("usd_by_model") or {}
    for model in sorted(tokens):
        counts = tokens[model]
        amount = (pricing.format_usd(float(usd_by_model[model])) if model in usd_by_model
                  else "UNPRICED")
        lines.append(f"{indent}  {model}: {amount}  " + ", ".join(
            f"{f}={int(counts.get(f, 0)):,}" for f in usage_mod.FIELDS))
    if block.get("unpriced"):
        lines.append(f"{indent}  UNPRICED model(s): {', '.join(block['unpriced'])} -- their "
                     f"tokens are counted above and priced nowhere, so the total is a lower "
                     f"bound; add them to workspace.yaml budget.prices")
    skipped = int(block.get("skipped_without_usage") or 0)
    dupes = int(block.get("duplicate_lines") or 0)
    repeats = int(block.get("repeated_events") or 0)
    bad = int(block.get("unreadable_lines") or 0)
    lines.append(f"{indent}  read: {dupes} duplicate line(s) of one response, {repeats} repeated "
                 f"event(s), {skipped} message(s) without usage, {bad} unreadable line(s)")
    lines.append(f"{indent}  prices: {block.get('price_table_source')} "
                 f"(sha256 {str(block.get('price_table_sha256') or '')[:12]})")
    return lines


def tasks_of(corpus_dir: str | Path, receipt: Mapping[str, Any] | None) -> tuple[int | None, str]:
    """(task count, where it came from) for a harvested corpus."""
    run_yaml = Path(corpus_dir) / "run.yaml"
    if run_yaml.exists():
        try:
            doc = read_yaml(run_yaml)
        except CfError:
            doc = None
        tasks = doc.get("tasks") if isinstance(doc, dict) else None
        count = tasks.get("count") if isinstance(tasks, dict) else tasks
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return count, "run.yaml tasks.count"
    actual = ((receipt or {}).get("actual") or {}).get("count") if receipt else None
    if isinstance(actual, int) and actual > 0:
        return actual, "finding files (no run.yaml task count)"
    return None, "unknown"


def run_cost(run_id: str, corpus_dir: str | Path) -> dict | None:
    """The per-run cost summary `cf tally` records, or None when the receipt has no block."""
    receipt = read_receipt(Path(corpus_dir))
    block = receipt.get(COST_KEY) if isinstance(receipt, dict) else None
    if not isinstance(block, dict):
        return None
    tasks, tasks_from = tasks_of(corpus_dir, receipt)
    usd = float(block.get("usd") or 0.0)
    return {
        "run_id": run_id,
        "usd": usd,
        "tasks": tasks,
        "tasks_from": tasks_from,
        "usd_per_task": (round(usd / tasks, 4) if tasks else None),
        "models": sorted(block.get("tokens") or {}),
        "priced": list(block.get("priced") or []),
        "unpriced": list(block.get("unpriced") or []),
        "transcript_count": block.get("transcript_count"),
        "message_count": block.get("message_count"),
        "price_table_sha256": block.get("price_table_sha256"),
    }


def runs_cost(runs: Mapping[str, str | Path]) -> dict[str, dict | None]:
    """:func:`run_cost` for every run of a tally."""
    return {str(run_id): run_cost(str(run_id), directory)
            for run_id, directory in dict(runs or {}).items()}


def render_run_costs(costs: Mapping[str, dict | None]) -> list[str]:
    """One line per run with a cost block, and one naming the runs without."""
    lines = []
    missing = []
    for run_id in sorted(costs):
        entry = costs[run_id]
        if not entry:
            missing.append(run_id)
            continue
        per_task = (f"{pricing.format_usd(entry['usd_per_task'])} a task over {entry['tasks']} "
                    f"tasks, from {entry['tasks_from']}" if entry.get("usd_per_task") is not None
                    else "no task count to divide by")
        note = (f"; UNPRICED: {', '.join(entry['unpriced'])}" if entry.get("unpriced") else "")
        lines.append(f"cost {run_id}: {pricing.format_usd(entry['usd'])} ({per_task}; "
                     f"models {', '.join(entry['models']) or 'none'}{note})")
    if missing:
        lines.append(f"cost not recorded for run(s) {', '.join(missing)}: harvest with "
                     f"--transcripts <path> to price them from the agents' transcripts")
    return lines


def receipt_usd(receipt: Mapping[str, Any] | None) -> float | None:
    """The priced dollars of a receipt, or None when it has no cost block."""
    block = receipt.get(COST_KEY) if isinstance(receipt, Mapping) else None
    if not isinstance(block, Mapping):
        return None
    return float(block.get("usd") or 0.0)


def load_table(path: str | Path) -> dict:
    """A price table from a YAML or JSON file, validated."""
    p = Path(path)
    if not p.exists():
        raise CfError(f"price table file does not exist: {p}")
    data = read_json(p) if p.suffix == ".json" else read_yaml(p)
    if isinstance(data, dict) and "prices" in data and isinstance(data["prices"], dict):
        data = data["prices"]
    problems = pricing.validate_table(data)
    if problems:
        raise CfError(f"{p} is not a usable price table: " + "; ".join(problems),
                      hint="one entry per model id with input, output, cache_write, cache_read "
                           "(US dollars per million tokens) and a source note")
    return {str(k): dict(v) for k, v in data.items()}
