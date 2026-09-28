"""The price table, and the arithmetic that turns token totals into dollars.

New code for milestone M0a (docs/proposals/pdf-to-companion.md section 5.1),
after issue #11: "the price table can live in workspace.yaml beside the model
ids, so the number is reproducible".  It lives at ``budget.prices`` there, one
entry per model id with the four per-million prices and a ``source`` note
saying where the numbers came from.  :data:`DEFAULT_PRICE_TABLE` is what a
fresh workspace is rendered with and what `cf cost` uses when it runs outside
a workspace; it carries the Opus 5 list prices issue #11 records and nothing
else, because every other price would be a guess.

A model the table does not list is **unpriced**: its tokens are reported under
``unpriced`` and ``unpriced_tokens`` and never priced at a neighbour's rate.
``usd`` is therefore the priced subtotal, and a reader that quotes it while
``unpriced`` is non-empty is quoting a lower bound; ``describe`` says so.

Stdlib only and no ``cflib`` import; it needs only ``cost.usage`` for the
field names, so the ``scripts/cost/`` package can be copied to a host whole.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from cost.usage import FIELDS, zero

#: workspace.yaml key path of the table
PRICE_TABLE_KEY: tuple[str, str] = ("budget", "prices")

#: the shipped default: Opus 5 list prices in US dollars per million tokens,
#: as recorded in issue #11 (input $5, output $25, cache write $6.25, cache
#: read $0.50).  No other model is priced here.
DEFAULT_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "claude-opus-5": {
        "input": 5.0,
        "output": 25.0,
        "cache_write": 6.25,
        "cache_read": 0.5,
        "source": "Opus 5 list prices per million tokens, as recorded in issue #11 (2026-09)",
    },
}

#: where a table came from, for the receipt
SOURCE_DEFAULT = "shipped default (scripts/cost/pricing.py DEFAULT_PRICE_TABLE, issue #11)"
SOURCE_WORKSPACE = "workspace.yaml budget.prices"
SOURCE_FILE = "price table file"

_MILLION = 1_000_000.0


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def validate_table(table: Any) -> list[str]:
    """Problems with a price table (empty list when it is usable).

    Every entry needs the four per-million prices as non-negative numbers and a
    non-empty ``source`` note; nothing else is allowed in an entry, so a typo
    (``cache_reads``) is refused rather than silently unpriced.
    """
    problems: list[str] = []
    if not isinstance(table, Mapping):
        return ["the price table must be a map keyed by model id"]
    if not table:
        problems.append("the price table is empty: every model is unpriced")
    for model, entry in table.items():
        if not isinstance(model, str) or not model.strip():
            problems.append(f"model id {model!r} is not a non-empty string")
            continue
        if not isinstance(entry, Mapping):
            problems.append(f"{model}: the entry must be a map of prices")
            continue
        for f in FIELDS:
            if f not in entry:
                problems.append(f"{model}: missing price {f!r} (US dollars per million tokens)")
            elif _price(entry[f]) is None:
                problems.append(f"{model}: price {f!r} must be a non-negative number, "
                                f"found {entry[f]!r}")
        source = entry.get("source")
        if not isinstance(source, str) or not source.strip():
            problems.append(f"{model}: a `source` note saying where the prices came from "
                            "is required")
        for key in entry:
            if key not in FIELDS and key != "source":
                problems.append(f"{model}: unknown key {key!r} (allowed: "
                                f"{', '.join(FIELDS)}, source)")
    return problems


def table_sha256(table: Mapping[str, Mapping[str, Any]]) -> str:
    """sha256 of the table's canonical JSON (sorted keys, no spaces).

    The receipt records it so a figure can be traced to the exact prices that
    produced it, source notes included.
    """
    blob = json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def from_workspace_data(data: Mapping | None) -> tuple[dict, str]:
    """(table, source) from a loaded workspace.yaml, or the default when it has none.

    A table that is present but invalid is an error the operator must fix; a
    workspace written before the key existed falls back to the default and the
    caller says so.
    """
    budget = (data or {}).get(PRICE_TABLE_KEY[0]) if isinstance(data, Mapping) else None
    table = budget.get(PRICE_TABLE_KEY[1]) if isinstance(budget, Mapping) else None
    if table is None:
        return dict(DEFAULT_PRICE_TABLE), SOURCE_DEFAULT
    problems = validate_table(table)
    if problems:
        raise ValueError("workspace.yaml budget.prices is invalid: " + "; ".join(problems))
    return {str(k): dict(v) for k, v in table.items()}, SOURCE_WORKSPACE


def price(tokens: Mapping[str, Mapping[str, int]], table: Mapping[str, Mapping[str, Any]]) -> dict:
    """Price per-model token totals against *table*.

    Returns ``{usd, usd_by_model, priced, unpriced, unpriced_tokens,
    price_table_sha256}``.  ``usd`` is the sum over the PRICED models only.
    """
    problems = validate_table(table)
    if problems and table:
        raise ValueError("invalid price table: " + "; ".join(problems))
    usd_by_model: dict[str, float] = {}
    unpriced: list[str] = []
    unpriced_tokens: dict[str, dict[str, int]] = {}
    for model in sorted(tokens):
        counts = {f: int(tokens[model].get(f, 0) or 0) for f in FIELDS}
        entry = table.get(model) if isinstance(table, Mapping) else None
        if entry is None:
            unpriced.append(model)
            unpriced_tokens[model] = counts
            continue
        amount = sum(counts[f] * float(entry[f]) / _MILLION for f in FIELDS)
        usd_by_model[model] = round(amount, 6)
    return {
        "usd": round(sum(usd_by_model.values()), 6),
        "usd_by_model": usd_by_model,
        "priced": sorted(usd_by_model),
        "unpriced": unpriced,
        "unpriced_tokens": unpriced_tokens,
        "price_table_sha256": table_sha256(table),
    }


def total_tokens(tokens: Mapping[str, Mapping[str, int]]) -> dict[str, int]:
    """The four fields summed over every model."""
    out = zero()
    for counts in tokens.values():
        for f in FIELDS:
            out[f] += int(counts.get(f, 0) or 0)
    return out


def format_usd(value: float) -> str:
    return f"${value:,.2f}"
