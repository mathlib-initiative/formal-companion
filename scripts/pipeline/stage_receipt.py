"""Write ``pipelines/<id>/stages/<stage>.stage-receipt.json``.

New code for docs/proposals/pdf-to-companion.md section 4.1, which gives the
receipt's fields:

    {input_doc_sha256, output_doc_sha256, pipeline_yaml_sha256,
     fleet_runs: [{run_id, run_yaml_sha256, receipt_sha256}],
     prompt_shas, model_ids, gate_result, consent, cost_usd, tokens, wall_clock_s}

and says that "fleet cost follows issue #11's design: harvest writes each
fleet's priced totals into its RECEIPT.json, with prices in workspace.yaml,
and the stage receipt sums its fleets".  :func:`sum_fleets` is that sum: it
reads every fleet run's ``run.yaml`` and ``RECEIPT.json``, records both shas,
and refuses a fleet whose receipt carries no cost block, an unpriced model or
a block that counted no response, because a stage receipt that quietly
omitted one fleet's dollars would be the cost line every report so far
shipped without.

The writer fails closed: a missing field, an extra field, a sha that is not 64
lowercase hex characters, a stage name outside S0 to S9, a negative cost or a
token count that is not an integer is refused and nothing is written.  The
written file carries an envelope (``schema_version``, ``pipeline_id``,
``stage``, ``written_at``) around the eleven fields, and :func:`read` checks
the same rules on the way back in.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.jsonio import read_json, write_json
from cflib.state import now
from cost.usage import FIELDS as TOKEN_FIELDS
from harvest.harvest import RECEIPT_NAME
from pipeline.pipeline_yaml import STAGES

SCHEMA_VERSION = 1

#: the eleven fields of proposal section 4.1, in its order
RECEIPT_FIELDS: tuple[str, ...] = (
    "input_doc_sha256", "output_doc_sha256", "pipeline_yaml_sha256", "fleet_runs",
    "prompt_shas", "model_ids", "gate_result", "consent", "cost_usd", "tokens", "wall_clock_s")
SHA_FIELDS: tuple[str, ...] = ("input_doc_sha256", "output_doc_sha256", "pipeline_yaml_sha256")
FLEET_RUN_FIELDS: tuple[str, ...] = ("run_id", "run_yaml_sha256", "receipt_sha256")
ENVELOPE_FIELDS: tuple[str, ...] = ("schema_version", "pipeline_id", "stage", "written_at")

_SHA = re.compile(r"[0-9a-f]{64}")


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(receipt: Any, *, with_envelope: bool = False) -> list[str]:
    """Problems with a receipt body (or, ``with_envelope``, a written file)."""
    if not isinstance(receipt, Mapping):
        return ["a stage receipt must be a mapping"]
    allowed = set(RECEIPT_FIELDS) | (set(ENVELOPE_FIELDS) if with_envelope else set())
    problems = [f"unknown field {k!r}" for k in receipt if k not in allowed]
    problems += [f"missing field {k!r}" for k in RECEIPT_FIELDS if k not in receipt]
    if with_envelope:
        problems += [f"missing field {k!r}" for k in ENVELOPE_FIELDS if k not in receipt]
        if "schema_version" in receipt and receipt["schema_version"] != SCHEMA_VERSION:
            problems.append(f"schema_version must be {SCHEMA_VERSION}")
        if "stage" in receipt and receipt["stage"] not in STAGES:
            problems.append(f"stage must be one of {STAGES[0]} to {STAGES[-1]}")
        if "pipeline_id" in receipt and not (isinstance(receipt["pipeline_id"], str)
                                             and receipt["pipeline_id"].strip()):
            problems.append("pipeline_id must be a non-empty string")
    for key in SHA_FIELDS:
        if key in receipt and not is_sha256(receipt[key]):
            problems.append(f"{key} must be 64 lowercase hex characters "
                            f"(found {_shape(receipt[key])})")
    fleet_runs = receipt.get("fleet_runs")
    if "fleet_runs" in receipt:
        if not isinstance(fleet_runs, list):
            problems.append("fleet_runs must be a list")
        else:
            seen: set[str] = set()
            for i, entry in enumerate(fleet_runs):
                where = f"fleet_runs[{i}]"
                if not isinstance(entry, Mapping):
                    problems.append(f"{where} must be a mapping")
                    continue
                problems += [f"{where}: unknown field {k!r}" for k in entry
                             if k not in FLEET_RUN_FIELDS]
                problems += [f"{where}: missing field {k!r}" for k in FLEET_RUN_FIELDS
                             if k not in entry]
                run_id = entry.get("run_id")
                if "run_id" in entry and not (isinstance(run_id, str) and run_id.strip()):
                    problems.append(f"{where}: run_id must be a non-empty string")
                elif isinstance(run_id, str):
                    if run_id in seen:
                        problems.append(f"{where}: run_id {run_id!r} is listed twice")
                    seen.add(run_id)
                for key in ("run_yaml_sha256", "receipt_sha256"):
                    if key in entry and not is_sha256(entry[key]):
                        problems.append(f"{where}: {key} must be 64 lowercase hex characters "
                                        f"(found {_shape(entry[key])})")
    prompt_shas = receipt.get("prompt_shas")
    if "prompt_shas" in receipt:
        if not isinstance(prompt_shas, Mapping):
            problems.append("prompt_shas must be a mapping of prompt name -> sha256")
        else:
            for name, sha in prompt_shas.items():
                if not (isinstance(name, str) and name.strip()):
                    problems.append(f"prompt_shas: name {name!r} is not a non-empty string")
                if not is_sha256(sha):
                    problems.append(f"prompt_shas[{name!r}] must be 64 lowercase hex characters "
                                    f"(found {_shape(sha)})")
    model_ids = receipt.get("model_ids")
    if "model_ids" in receipt and (not isinstance(model_ids, list) or any(
            not (isinstance(m, str) and m.strip()) for m in model_ids)):
        problems.append("model_ids must be a list of non-empty strings")
    gate = receipt.get("gate_result")
    if "gate_result" in receipt and not (isinstance(gate, Mapping)
                                         and isinstance(gate.get("ok"), bool)):
        problems.append("gate_result must be a mapping with a boolean `ok`")
    consent = receipt.get("consent")
    if "consent" in receipt and not (isinstance(consent, Mapping) and isinstance(
            consent.get("include_source_text"), bool)):
        problems.append("consent must be a mapping with a boolean `include_source_text`")
    if "cost_usd" in receipt and not _number(receipt["cost_usd"]):
        problems.append("cost_usd must be a non-negative number")
    tokens = receipt.get("tokens")
    if "tokens" in receipt:
        if not isinstance(tokens, Mapping):
            problems.append("tokens must be a mapping of model id -> token counts")
        else:
            for model, counts in tokens.items():
                if not (isinstance(model, str) and model.strip()):
                    problems.append(f"tokens: model id {model!r} is not a non-empty string")
                if not isinstance(counts, Mapping) or set(counts) != set(TOKEN_FIELDS) or any(
                        not _count(counts[f]) for f in TOKEN_FIELDS):
                    problems.append(f"tokens[{model!r}] must hold exactly "
                                    f"{', '.join(TOKEN_FIELDS)}, each a non-negative integer")
    if "wall_clock_s" in receipt and not _number(receipt["wall_clock_s"]):
        problems.append("wall_clock_s must be a non-negative number")
    return problems


def _shape(value: Any) -> str:
    if isinstance(value, str):
        return f"a string of {len(value)} characters"
    return type(value).__name__


def fleet_run_entry(paths, run_id: str) -> dict:
    """``{run_id, run_yaml_sha256, receipt_sha256}`` for one harvested fleet run.

    Both files must exist: a fleet without a receipt was never harvested, and a
    receipt without its plan cannot be tied to what was asked for.
    """
    run_yaml = Path(paths.run_yaml(run_id))
    receipt = Path(paths.receipt_json(run_id))
    if not run_yaml.exists():
        raise CfError(f"fleet run {run_id!r} has no run.yaml at {run_yaml}",
                      hint="a stage's fleets are runs `cf plan-run` planned in this workspace")
    if not receipt.exists():
        raise CfError(f"fleet run {run_id!r} has no {RECEIPT_NAME} at {receipt}",
                      hint=f"`cf harvest {run_id} --transcripts <path>` lands the findings "
                           f"and prices the run")
    return {"run_id": run_id, "run_yaml_sha256": sha256_file(run_yaml),
            "receipt_sha256": sha256_file(receipt)}


def sum_fleets(paths, run_ids) -> dict:
    """The stage's ``fleet_runs``, ``cost_usd``, ``tokens`` and ``model_ids`` from its fleets.

    Refuses a fleet whose receipt has no ``cost`` block, names an unpriced
    model, or counted no response (an empty or wrong transcripts path prices
    to $0): the sum would silently be a lower bound.
    """
    fleet_runs = []
    tokens: dict[str, dict[str, int]] = {}
    cost_usd = 0.0
    for run_id in [str(r) for r in run_ids]:
        entry = fleet_run_entry(paths, run_id)
        receipt = read_json(paths.receipt_json(run_id))
        block = receipt.get("cost") if isinstance(receipt, dict) else None
        if not isinstance(block, dict):
            raise CfError(f"fleet run {run_id!r}: its {RECEIPT_NAME} carries no cost block",
                          hint=f"`cf harvest {run_id} --transcripts <path>` prices it from the "
                               f"agents' transcripts (or a `cf cost --export` file)")
        if block.get("unpriced"):
            raise CfError(f"fleet run {run_id!r}: model(s) {', '.join(block['unpriced'])} are "
                          f"unpriced in its {RECEIPT_NAME}",
                          hint="add them to workspace.yaml budget.prices with a source note "
                               "and re-harvest")
        responses = block.get("message_count")
        if not isinstance(responses, int) or isinstance(responses, bool) or responses <= 0:
            what = ("counted no response" if responses == 0 and not isinstance(responses, bool)
                    else f"carries no response count (message_count {responses!r})")
            raise CfError(f"fleet run {run_id!r}: the cost block of its {RECEIPT_NAME} {what}, "
                          f"so its ${float(block.get('usd') or 0.0):.2f} is not a measurement "
                          f"of the fleet",
                          hint=f"re-run `cf harvest {run_id} --transcripts <path>` with the "
                               f"directory that holds the fleet's transcripts or its usage "
                               f"export")
        cost_usd += float(block.get("usd") or 0.0)
        for model, counts in (block.get("tokens") or {}).items():
            bucket = tokens.setdefault(str(model), {f: 0 for f in TOKEN_FIELDS})
            for f in TOKEN_FIELDS:
                bucket[f] += int(counts.get(f, 0) or 0)
        fleet_runs.append(entry)
    return {"fleet_runs": fleet_runs, "cost_usd": round(cost_usd, 6), "tokens": tokens,
            "model_ids": sorted(tokens)}


def write(paths, pipeline_id: str, stage: str, receipt: Mapping[str, Any]) -> Path:
    """Validate *receipt* and write it as ``pipelines/<id>/stages/<stage>.stage-receipt.json``.

    Nothing is written when anything is wrong; the error names every problem.
    """
    problems = []
    if stage not in STAGES:
        problems.append(f"stage {stage!r} is not one of {STAGES[0]} to {STAGES[-1]}")
    problems += validate(receipt)
    if problems:
        raise CfError(f"refusing to write the {stage} stage receipt of {pipeline_id!r}: "
                      + "; ".join(problems),
                      hint="every field of docs/proposals/pdf-to-companion.md section 4.1 is "
                           "required, shas are 64 lowercase hex characters, and nothing else "
                           "is allowed")
    doc = {"schema_version": SCHEMA_VERSION, "pipeline_id": pipeline_id, "stage": stage,
           "written_at": now()}
    doc.update({k: receipt[k] for k in RECEIPT_FIELDS})
    out = Path(paths.stage_receipt_json(pipeline_id, stage))
    write_json(out, doc, indent=1, sort_keys=True)
    return out


def read(path: str | Path) -> dict:
    """A written stage receipt, checked on the way in."""
    doc = read_json(path)
    problems = validate(doc, with_envelope=True)
    if problems:
        raise CfError(f"{path} is not a valid stage receipt: " + "; ".join(problems))
    return doc
