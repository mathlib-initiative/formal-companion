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

import json
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
#: what a cost block was priced from (its ``basis``): the agents' transcripts (or a
#: usage export of them), or the bridge's kept envelopes
BASIS_TRANSCRIPTS = "transcripts"
BASIS_ENVELOPES = "envelopes"
#: an envelope's ``modelUsage`` keys -> the four normalised fields
_MODEL_USAGE_KEYS = {"input": "inputTokens", "output": "outputTokens",
                     "cache_write": "cacheCreationInputTokens",
                     "cache_read": "cacheReadInputTokens"}


def source_label(path: str | Path) -> str:
    """The last component of *path*: a committed receipt must not leak the machine's layout."""
    p = Path(path)
    return p.name if p.name not in ("", ".", "..") else (p.resolve().name or ".")


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def empty_warning(block: Mapping[str, Any]) -> str | None:
    """A warning when *block* counted no response, else None (envelope-aware)."""
    if _count(block.get("message_count")) > 0:
        return None
    if block.get("basis") == BASIS_ENVELOPES:
        envelopes = _count(block.get("envelope_count"))
        if not envelopes:
            return ("no agent envelope was found in the tend root's usage/, so nothing was "
                    "priced and $0.00 is not a measurement")
        return (f"{envelopes} agent envelope(s) read and none priced "
                f"({_count(block.get('unpriced_calls'))} record(s) of a call that left no "
                f"envelope, {_count(block.get('unreadable_lines'))} unreadable), so $0.00 is "
                f"not a measurement")
    files = _count(block.get("transcript_count"))
    if not files:
        return ("no log file (*.jsonl, *.jsonl.gz) was found under the transcripts path, "
                "so nothing was priced and $0.00 is not a measurement")
    return (f"{files} log file(s) read and no response counted "
            f"({int(block.get('skipped_without_usage') or 0)} message(s) without usage, "
            f"{int(block.get('unreadable_lines') or 0)} unreadable line(s)), so $0.00 is not "
            f"a measurement")


def lower_bound(block: Mapping[str, Any] | None) -> str | None:
    """Why *block*'s ``usd`` is only a lower bound of the run's spend, or None.

    Under either basis, a call that left no priced envelope (``unpriced_calls``: a
    timeout, a signal, an empty stdout -- ``scripts/run/agent_call.py``'s record of
    it) spent what no figure holds; under the transcripts basis a model missing from
    the price table (``unpriced``) has its tokens counted and priced nowhere.  Under
    the envelope basis ``usd`` is the agent CLI's own ``total_cost_usd``, which a
    missing price-table row does not touch (only ``usd_from_price_table``).
    """
    if not isinstance(block, Mapping):
        return None
    calls = _count(block.get("unpriced_calls"))
    if calls > 0:
        return (f"{calls} agent call(s) left no priced envelope "
                f"({block.get('unpriced_seconds', 0)} s: a timeout, a signal or an empty "
                f"stdout), so their spend is in no figure")
    if block.get("unpriced") and block.get("basis") != BASIS_ENVELOPES:
        return (f"model(s) {', '.join(str(m) for m in block['unpriced'])} are in no price "
                f"table, so their tokens are priced nowhere")
    return None


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
        "basis": BASIS_TRANSCRIPTS,
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


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
        return float(value)
    return None


def _model_usage(entry: Any) -> dict[str, int] | None:
    """One ``modelUsage`` entry (``inputTokens`` ...) -> the four normalised fields."""
    if not isinstance(entry, Mapping):
        return None
    out = {}
    for field_name, key in _MODEL_USAGE_KEYS.items():
        value = entry.get(key, 0)
        if value is None:
            value = 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        out[field_name] = int(value)
    return out


#: the ``by_step`` key of an envelope whose file name is neither form ``agent_call.py`` writes
STEP_UNNAMED = "unnamed"


def envelope_step(name: str) -> tuple[str, bool]:
    """``(key, stepped)`` of a kept envelope's file name, for ``by_step``.

    An audited run's call is named ``<task>.<role>.<step>.<stamp>.<pid>.json`` and files
    under its step; any other call under its role (``worker``, ``reviewer``), and a name
    that is neither form under :data:`STEP_UNNAMED`.  The names are read by
    ``run.watchdog.envelope_name``, the one reader of both forms
    (references/locate-documents.md section 13.9).
    """
    from run.watchdog import envelope_name  # noqa: PLC0415 - the host reader, stdlib only

    named = envelope_name(name)
    if not named:
        return STEP_UNNAMED, False
    return (named["step"], True) if named["step"] else (named["role"], False)


def _add_step(by_step: dict[str, dict], name: str, cost: float | None) -> bool:
    key, stepped = envelope_step(name)
    row = by_step.setdefault(key, {"envelopes": 0, "usd": 0.0, "unpriced_calls": 0})
    row["envelopes"] += 1
    if cost is None:
        row["unpriced_calls"] += 1
    else:
        row["usd"] = round(row["usd"] + max(0.0, cost), 6)
    return stepped


def envelope_by_step(path: str | Path) -> dict[str, dict]:
    """``{step or role: {envelopes, usd, unpriced_calls}}`` over the envelopes under *path*.

    Every readable JSON object counts, as :func:`envelope_cost_block` counts it: its
    ``total_cost_usd`` is the dollars, and a record of a call that left no envelope is
    an unpriced call (:func:`envelope_step` names the key).  An absent *path* is ``{}``.
    """
    root = Path(path)
    if not root.exists():
        return {}
    by_step: dict[str, dict] = {}
    for file in sorted(p for p in (root.rglob("*.json") if root.is_dir() else [root])
                       if p.is_file()):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            _add_step(by_step, file.name, _number(data.get("total_cost_usd")))
    return dict(sorted(by_step.items()))


def envelope_cost_block(path: str | Path, *, table: Mapping[str, Mapping[str, Any]],
                        table_source: str, model: str | None = None) -> dict:
    """The receipt's ``cost`` block from the bridge's kept agent envelopes.

    Each ``*.json`` under *path* is one agent call's envelope
    (``scripts/run/agent_call.py``): its own ``total_cost_usd`` is the dollars,
    and its tokens are read per model from ``modelUsage`` (``costUSD`` the
    model's dollars), else from ``usage`` filed under the envelope's ``model``,
    else *model* (the run's), else ``usage.UNKNOWN_MODEL``.  A record of a call
    that left no envelope (``total_cost_usd: null``: a timeout, a signal, an
    empty stdout) is counted under ``unpriced_calls`` with its seconds, never
    priced at 0, and makes ``usd`` a lower bound.  The tokens are also priced
    with *table* (``usd_from_price_table``), a cross-check of the CLI's own
    figure.  ``basis`` is ``envelopes``.  When any envelope's name carries an audited
    call's step (``agent_call.py --step``), the block also has ``by_step``: every
    envelope's dollars, count and unpriced calls under its step, or its role when it
    names none (:func:`envelope_step`); a run with no stepped envelope gets no such
    key, so its block is what it was.
    """
    root = Path(path)
    if not root.exists():
        raise CfError(f"no envelopes at {root}",
                      hint="the bridge keeps one per agent call in the tend root's usage/")
    files = sorted(p for p in (root.rglob("*.json") if root.is_dir() else [root]) if p.is_file())
    tokens: dict[str, dict[str, int]] = {}
    usd_by_model: dict[str, float] = {}
    messages_by_model: dict[str, int] = {}
    usd = 0.0
    envelopes = unpriced_calls = unreadable = without_usage = 0
    unpriced_seconds = 0.0
    by_step: dict[str, dict] = {}
    stepped = False

    def add(name: str, counts: dict[str, int], dollars: float | None) -> None:
        block = tokens.setdefault(name, usage_mod.zero())
        for f in usage_mod.FIELDS:
            block[f] += counts[f]
        messages_by_model[name] = messages_by_model.get(name, 0) + 1
        if dollars is not None:
            usd_by_model[name] = round(usd_by_model.get(name, 0.0) + dollars, 6)

    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            unreadable += 1
            continue
        if not isinstance(data, dict):
            unreadable += 1
            continue
        envelopes += 1
        cost = _number(data.get("total_cost_usd"))
        stepped = _add_step(by_step, file.name, cost) or stepped
        if cost is None:
            call = data.get("cf_agent_call") if isinstance(data.get("cf_agent_call"), dict) else {}
            unpriced_calls += 1
            seconds = _number(call.get("elapsed_s"))
            if seconds is None and _number(data.get("duration_ms")) is not None:
                seconds = float(data["duration_ms"]) / 1000.0
            unpriced_seconds += max(0.0, seconds or 0.0)
            continue
        usd += max(0.0, cost)
        per_model = data.get("modelUsage") if isinstance(data.get("modelUsage"), Mapping) else {}
        counted = False
        for name, entry in sorted(per_model.items()):
            counts = _model_usage(entry)
            if counts is not None:
                add(str(name), counts, _number((entry or {}).get("costUSD")))
                counted = True
        if counted:
            continue
        counts = usage_mod.normalise_usage(data.get("usage"))
        if counts is None:
            without_usage += 1
            continue
        add(str(data.get("model") or model or usage_mod.UNKNOWN_MODEL), counts, cost)
    try:
        priced = pricing.price(tokens, table)
    except ValueError as exc:
        raise CfError(str(exc), hint="fix workspace.yaml budget.prices (or the --prices file)")
    block = {
        "basis": BASIS_ENVELOPES,
        "source": {"kind": BASIS_ENVELOPES, "path": source_label(path),
                   "files": {"envelopes": envelopes, "unreadable": unreadable}},
        "tokens": {m: dict(tokens[m]) for m in sorted(tokens)},
        "total_tokens": pricing.total_tokens(tokens),
        "usd": round(usd, 6),
        "usd_by_model": dict(sorted(usd_by_model.items())),
        "usd_from_price_table": priced["usd"],
        "messages_by_model": dict(sorted(messages_by_model.items())),
        "priced": priced["priced"],
        "unpriced": priced["unpriced"],
        "unpriced_tokens": priced["unpriced_tokens"],
        "price_table_sha256": priced["price_table_sha256"],
        "price_table_source": table_source,
        "envelope_count": envelopes,
        "unpriced_calls": unpriced_calls,
        "unpriced_seconds": round(unpriced_seconds, 1),
        "transcript_count": 0,
        "message_count": envelopes - unpriced_calls,
        "duplicate_lines": 0,
        "repeated_events": 0,
        "skipped_without_usage": without_usage,
        "unreadable_lines": unreadable,
    }
    if stepped:
        block["by_step"] = dict(sorted(by_step.items()))
    return block


def describe(block: Mapping[str, Any], indent: str = "  ") -> list[str]:
    """The block as lines a terminal can show; every number is a key of it."""
    if block.get("basis") == BASIS_ENVELOPES:
        return _describe_envelopes(block, indent)
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


def _describe_envelopes(block: Mapping[str, Any], indent: str) -> list[str]:
    lines = [f"{indent}cost: {pricing.format_usd(float(block.get('usd') or 0.0))} from "
             f"{block.get('envelope_count', 0)} agent envelope(s) (source: envelopes; the "
             f"price table gives "
             f"{pricing.format_usd(float(block.get('usd_from_price_table') or 0.0))})"]
    tokens = block.get("tokens") or {}
    usd_by_model = block.get("usd_by_model") or {}
    for model in sorted(tokens):
        counts = tokens[model]
        amount = (pricing.format_usd(float(usd_by_model[model])) if model in usd_by_model
                  else "no costUSD")
        lines.append(f"{indent}  {model}: {amount}  " + ", ".join(
            f"{f}={int(counts.get(f, 0)):,}" for f in usage_mod.FIELDS))
    if block.get("unpriced_calls"):
        lines.append(f"{indent}  {block['unpriced_calls']} call(s) left no priced envelope "
                     f"({block.get('unpriced_seconds', 0)} s: a timeout, a signal or an empty "
                     f"stdout), so the total is a lower bound")
    if block.get("unpriced"):
        lines.append(f"{indent}  not in the price table: {', '.join(block['unpriced'])}")
    if isinstance(block.get("by_step"), Mapping):
        lines.append(f"{indent}  by step: " + ", ".join(
            f"{key} {pricing.format_usd(float(row.get('usd') or 0.0))} "
            f"({row.get('envelopes', 0)} envelope(s))"
            for key, row in sorted(block["by_step"].items())))
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
        "basis": block.get("basis") or BASIS_TRANSCRIPTS,
        "envelope_count": block.get("envelope_count"),
        "unpriced_calls": block.get("unpriced_calls") or 0,
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
        if entry.get("basis") == BASIS_ENVELOPES:
            note = (f"; {entry['unpriced_calls']} call(s) left no priced envelope, so it is a "
                    f"lower bound" if entry.get("unpriced_calls") else "")
            lines.append(f"cost {run_id}: {pricing.format_usd(entry['usd'])} ({per_task}; "
                         f"models {', '.join(entry['models']) or 'none'}; source: envelopes, "
                         f"{entry.get('envelope_count') or 0} of them{note})")
            continue
        note = (f"; UNPRICED: {', '.join(entry['unpriced'])}, so the dollar figures are a "
                f"lower bound" if entry.get("unpriced") else "")
        lines.append(f"cost {run_id}: {pricing.format_usd(entry['usd'])} ({per_task}; "
                     f"models {', '.join(entry['models']) or 'none'}{note})")
    if missing:
        lines.append(f"cost not recorded for run(s) {', '.join(missing)}: harvest with "
                     f"--transcripts <path> to price them from the agents' transcripts (a "
                     f"remote locate run is priced from its agent envelopes when they came "
                     f"home)")
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
