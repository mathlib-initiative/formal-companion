#!/usr/bin/env python3
"""Sum the tokens a fleet used, per model id, from the agents' own logs.

New code for milestone M0a (docs/proposals/pdf-to-companion.md section 5.1),
after the design of issue #11.  It is a port of the 30-line prototype that
read one directory of Claude Code transcripts at one price, extended to Codex
rollout logs and to a model id per response.

Three input shapes are read, and a directory may mix them:

* a **Claude Code transcript** (``*.jsonl``): every ``assistant`` line carries
  ``message.model`` and ``message.usage`` with ``input_tokens``,
  ``output_tokens``, ``cache_creation_input_tokens`` and
  ``cache_read_input_tokens``.  Claude Code writes one line per content block
  of a response, and the lines of one response repeat the same ``message.id``,
  ``requestId`` and usage, so a response is counted once, on its first line;
  the repeats are counted under ``duplicate_lines``.  A message with no usage
  is skipped and counted under ``skipped_without_usage``.
* a **Codex rollout log** (``rollout-*.jsonl``): a ``token_usage_record``
  event carries ``payload.usage`` for one response (``input_tokens``,
  ``cached_input_tokens``, ``cache_write_input_tokens``, ``output_tokens``,
  ``reasoning_output_tokens``, ``total_tokens``) beside the cumulative
  ``thread_token_usage``; an ``event_msg`` of type ``token_count`` carries the
  same counts again as ``info.last_token_usage`` beside the cumulative
  ``info.total_token_usage``.  The same counts therefore appear several times
  in a row, so the reader charges the ADVANCE of the cumulative block and
  skips an event that advanced it by nothing (``repeated_events``).  The model
  id is the ``model`` of the nearest preceding ``turn_context`` event; a log
  that names none is read under the id passed as ``model``, or under
  ``UNKNOWN_MODEL`` when there is none, which the price table cannot price.
* a **usage export** (``*.jsonl`` or ``*.jsonl.gz``, one ``{model, usage}``
  object per line, the shape of the s17b fixture under ``tests/data/m0a/``):
  the rows carry no ids, so they are summed as written.  ``export_rows`` writes
  one, one row per counted response, which is how usage leaves a host without
  the transcripts' text.

Every usage is normalised to four fields, ``input`` (uncached prompt tokens),
``output``, ``cache_write`` and ``cache_read``, which is what the price table
prices.  For the Codex shape ``input_tokens`` counts every prompt token, so the
cached and cache-written tokens are subtracted from it; ``output_tokens``
already includes ``reasoning_output_tokens``.

Deliberately stdlib-only and self-contained (no ``cflib`` import), like
``scripts/gate/verify_findings.py``: the transcripts live on the run host, and
``python3 scripts/cost/usage.py <dir> --export usage.jsonl`` runs there with
nothing installed.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

#: the four normalised token fields, in the order the tables print them
FIELDS: tuple[str, ...] = ("input", "output", "cache_write", "cache_read")

#: the model id a response is filed under when its log names none
UNKNOWN_MODEL = "unknown"

#: how a file was read
KIND_CLAUDE = "claude_transcript"
KIND_CODEX = "codex_rollout"
KIND_USAGE = "usage_export"

_CLAUDE_KEYS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}
_CODEX_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens")


def _count(value: Any) -> int | None:
    """A non-negative token count, or None for anything that is not one."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


def zero() -> dict[str, int]:
    return {f: 0 for f in FIELDS}


def normalise_usage(usage: Any) -> dict[str, int] | None:
    """One usage object in any of the three shapes -> ``{input, output, cache_write, cache_read}``.

    None when the object carries no token count at all, or a count that is not
    a number: such a message is skipped and counted, never charged as zero.
    """
    if not isinstance(usage, Mapping):
        return None
    if "cached_input_tokens" in usage or "cache_write_input_tokens" in usage:
        raw = [_count(usage.get(k)) for k in _CODEX_KEYS]
        if any(v is None for v in raw):
            return None
        prompt, cached, written, output = raw
        return {"input": max(prompt - cached - written, 0), "output": output,
                "cache_write": written, "cache_read": cached}
    if any(k in usage for k in _CLAUDE_KEYS.values()):
        out = {}
        for field_name, key in _CLAUDE_KEYS.items():
            value = _count(usage.get(key))
            if value is None:
                return None
            out[field_name] = value
        return out
    if any(k in usage for k in FIELDS):
        out = {}
        for field_name in FIELDS:
            value = _count(usage.get(field_name))
            if value is None:
                return None
            out[field_name] = value
        return out
    return None


def _cumulative_delta(now: dict[str, int], before: dict[str, int] | None) -> dict[str, int] | None:
    """``now - before`` field by field, or None when any field went backwards."""
    if before is None:
        return dict(now)
    delta = {}
    for f in FIELDS:
        d = now[f] - before[f]
        if d < 0:
            return None
        delta[f] = d
    return delta


@dataclass
class Record:
    """One counted response."""

    model: str
    usage: dict[str, int]
    file: str
    kind: str


@dataclass
class Totals:
    """Tokens per model id plus the counts that say how they were read."""

    tokens: dict[str, dict[str, int]] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=lambda: {KIND_CLAUDE: 0, KIND_CODEX: 0,
                                                            KIND_USAGE: 0})
    message_count: int = 0
    lines_read: int = 0
    duplicate_lines: int = 0
    repeated_events: int = 0
    skipped_without_usage: int = 0
    unreadable_lines: int = 0
    unknown_model_messages: int = 0

    def add(self, record: Record) -> None:
        block = self.tokens.setdefault(record.model, zero())
        for f in FIELDS:
            block[f] += record.usage[f]
        self.message_count += 1
        if record.model == UNKNOWN_MODEL:
            self.unknown_model_messages += 1

    @property
    def transcript_count(self) -> int:
        return sum(self.files.values())

    @property
    def models(self) -> list[str]:
        return sorted(self.tokens)

    def as_dict(self) -> dict:
        return {
            "tokens": {m: dict(self.tokens[m]) for m in self.models},
            "files": dict(self.files),
            "transcript_count": self.transcript_count,
            "message_count": self.message_count,
            "lines_read": self.lines_read,
            "duplicate_lines": self.duplicate_lines,
            "repeated_events": self.repeated_events,
            "skipped_without_usage": self.skipped_without_usage,
            "unreadable_lines": self.unreadable_lines,
            "unknown_model_messages": self.unknown_model_messages,
        }


# ---------------------------------------------------------------------------
# reading one file
# ---------------------------------------------------------------------------

def _open_text(path: Path):
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return open(path, encoding="utf-8")


def _lines(path: Path, totals: Totals) -> Iterator[dict]:
    """Every JSON object in a JSONL file; a line that is not one is counted."""
    with _open_text(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            totals.lines_read += 1
            try:
                obj = json.loads(line)
            except ValueError:
                totals.unreadable_lines += 1
                continue
            if not isinstance(obj, dict):
                totals.unreadable_lines += 1
                continue
            yield obj


def classify(path: Path) -> str:
    """Which of the three shapes a file is, from its name and its first object."""
    if path.name.startswith("rollout-"):
        return KIND_CODEX
    if path.name.endswith(".jsonl.gz"):
        return KIND_USAGE
    try:
        with _open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    return KIND_CLAUDE
                break
            else:
                return KIND_USAGE
    except OSError:
        return KIND_CLAUDE
    if not isinstance(obj, dict):
        return KIND_CLAUDE
    if "payload" in obj and "type" in obj:
        return KIND_CODEX
    if "usage" in obj and "model" in obj and "type" not in obj and "message" not in obj:
        return KIND_USAGE
    return KIND_CLAUDE


def read_claude_transcript(path: Path, totals: Totals) -> Iterator[Record]:
    """Counted responses of one Claude Code transcript (one per message id)."""
    seen: set[tuple] = set()
    for obj in _lines(path, totals):
        if obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            totals.skipped_without_usage += 1
            continue
        usage = normalise_usage(message.get("usage"))
        if usage is None:
            totals.skipped_without_usage += 1
            continue
        key = (message.get("id"), obj.get("requestId"))
        if key[0] is not None:
            if key in seen:
                totals.duplicate_lines += 1
                continue
            seen.add(key)
        model = str(message.get("model") or UNKNOWN_MODEL)
        yield Record(model=model, usage=usage, file=path.name, kind=KIND_CLAUDE)


def _codex_usage_and_cumulative(obj: dict) -> tuple[Any, Any, str | None]:
    """(usage, cumulative, thread id) of a Codex event, or (None, None, None)."""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None, None, None
    if obj.get("type") == "token_usage_record":
        thread = payload.get("thread_id") or payload.get("session_id")
        return payload.get("usage"), payload.get("thread_token_usage"), (
            str(thread) if thread else None)
    if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        if not isinstance(info, dict):
            return None, None, None
        return info.get("last_token_usage"), info.get("total_token_usage"), None
    return None, None, None


def read_codex_rollout(path: Path, totals: Totals, model: str | None = None) -> Iterator[Record]:
    """Counted responses of one Codex rollout log.

    The cumulative block (``thread_token_usage`` or ``total_token_usage``) is
    tracked per thread; an event is charged the amount it advanced that block
    by, so a count reported twice is counted once.  An event with no
    cumulative block is charged its own usage unless it repeats the previous
    event's counts exactly.  A cumulative block that went backwards (a new
    thread reusing an id, a reset) restarts the baseline at that event.
    """
    current_model = model
    cumulative: dict[str, dict[str, int]] = {}
    last_usage: dict[str, dict[str, int]] = {}
    current_thread = ""
    for obj in _lines(path, totals):
        payload = obj.get("payload")
        if obj.get("type") == "turn_context" and isinstance(payload, dict) and payload.get("model"):
            current_model = str(payload["model"])
            continue
        raw_usage, raw_cumulative, thread = _codex_usage_and_cumulative(obj)
        if raw_usage is None and raw_cumulative is None:
            continue
        if thread:
            current_thread = thread
        key = current_thread
        usage = normalise_usage(raw_usage)
        total = normalise_usage(raw_cumulative)
        charged: dict[str, int] | None
        if total is not None:
            delta = _cumulative_delta(total, cumulative.get(key))
            if delta is None:  # went backwards: a reset, charge this event's own usage
                charged = usage if usage is not None else dict(total)
            else:
                charged = delta
            cumulative[key] = dict(total)
        elif usage is not None:
            if last_usage.get(key) == usage:
                charged = None
            else:
                charged = usage
        else:
            totals.skipped_without_usage += 1
            continue
        if usage is not None:
            last_usage[key] = usage
        if charged is None or not any(charged.values()):
            totals.repeated_events += 1
            continue
        yield Record(model=str(current_model or UNKNOWN_MODEL), usage=charged,
                     file=path.name, kind=KIND_CODEX)


def read_usage_export(path: Path, totals: Totals, model: str | None = None) -> Iterator[Record]:
    """Rows of a ``{model, usage}`` export, summed as written."""
    for obj in _lines(path, totals):
        usage = normalise_usage(obj.get("usage"))
        if usage is None:
            totals.skipped_without_usage += 1
            continue
        yield Record(model=str(obj.get("model") or model or UNKNOWN_MODEL), usage=usage,
                     file=path.name, kind=KIND_USAGE)


# ---------------------------------------------------------------------------
# reading a path
# ---------------------------------------------------------------------------

def list_files(path: str | Path) -> list[Path]:
    """The log files under *path*: itself when it is a file, else every
    ``*.jsonl`` and ``*.jsonl.gz`` below it, sorted."""
    p = Path(path)
    if p.is_file():
        return [p]
    if not p.is_dir():
        raise FileNotFoundError(f"no such file or directory: {p}")
    return sorted(q for q in p.rglob("*")
                  if q.is_file() and (q.suffix == ".jsonl" or q.name.endswith(".jsonl.gz")))


def iter_records(path: str | Path, totals: Totals, model: str | None = None) -> Iterator[Record]:
    """Every counted response under *path*, updating *totals*' file counts."""
    for file in list_files(path):
        kind = classify(file)
        totals.files[kind] += 1
        if kind == KIND_CLAUDE:
            yield from read_claude_transcript(file, totals)
        elif kind == KIND_CODEX:
            yield from read_codex_rollout(file, totals, model=model)
        else:
            yield from read_usage_export(file, totals, model=model)


def read_path(path: str | Path, model: str | None = None) -> Totals:
    """Sum every log under *path* into one :class:`Totals`.

    An empty directory gives zero tokens, zero files and zero messages.
    """
    totals = Totals()
    for record in iter_records(path, totals, model=model):
        totals.add(record)
    return totals


def export_rows(path: str | Path, out: str | Path, model: str | None = None) -> Totals:
    """Write one ``{model, usage}`` row per counted response of *path* to *out*.

    The rows carry the four normalised fields and nothing else: no text, no
    paths, no ids.  Returns the totals of what was written.
    """
    totals = Totals()
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda: io.TextIOWrapper(gzip.open(out_path, "wb"), encoding="utf-8")) \
        if out_path.suffix == ".gz" else (lambda: open(out_path, "w", encoding="utf-8"))
    with opener() as fh:
        for record in iter_records(path, totals, model=model):
            totals.add(record)
            fh.write(json.dumps({"model": record.model, "usage": record.usage},
                                sort_keys=True) + "\n")
    return totals


def render_tokens(tokens: Mapping[str, Mapping[str, int]]) -> list[str]:
    """One line per model: the four token counts."""
    return [f"  {model}: " + ", ".join(f"{f}={int(tokens[model][f]):,}" for f in FIELDS)
            for model in sorted(tokens)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="a transcript or rollout directory, one log file, "
                                     "or a usage export")
    parser.add_argument("--model", default=None,
                        help="the model id for a log that names none (Codex rollouts "
                             "written before turn_context carried one)")
    parser.add_argument("--export", default=None, metavar="FILE",
                        help="write one {model, usage} row per counted response to FILE "
                             "(.jsonl or .jsonl.gz); no text leaves the host")
    parser.add_argument("--json", action="store_true", help="print the totals as JSON")
    args = parser.parse_args(argv)
    try:
        totals = (export_rows(args.path, args.export, model=args.model) if args.export
                  else read_path(args.path, model=args.model))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(totals.as_dict(), indent=1, sort_keys=True))
        return 0
    print(f"files: {totals.transcript_count} ({', '.join(f'{k}={v}' for k, v in totals.files.items() if v) or 'none'}), "
          f"responses counted: {totals.message_count}, duplicate lines: {totals.duplicate_lines}, "
          f"repeated events: {totals.repeated_events}, without usage: {totals.skipped_without_usage}")
    for line in render_tokens(totals.tokens):
        print(line)
    if args.export:
        print(f"wrote {totals.message_count} row(s) -> {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
