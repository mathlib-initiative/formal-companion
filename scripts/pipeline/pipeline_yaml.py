"""Load and validate ``pipelines/<id>/pipeline.yaml``.

New code for docs/proposals/pdf-to-companion.md section 4.1: the file "holds
the pipeline-wide settings (exercise_policy, scope, consent) and the run id of
every fleet a stage planned".  The shape:

    version: 1
    pipeline:
      id: <the directory name>
      source_id: <a source of the workspace>
      companion: <the companion the pipeline generates>
    exercise_policy: null | stub | prove     # unset until the maintainer decides
    scope:
      chapters: [1]                          # and/or
      sections: ["1.7"]
    consent:
      include_source_text: true              # page text or images may reach the host
      recorded: "2026-09-27"                 # required when the line above is true
      note: ""                               # optional
    fleets:
      S5: [<run id>, ...]                    # the runs a stage planned, per stage
      S9: [<run id>]
    notes: ""                                # optional

Every unknown key is refused, at every level: a misspelt setting would
otherwise be a setting nobody applied.  ``exercise_policy`` may be null, since
the proposal says S3 refuses to proceed while it is blank; anything but null,
``stub`` or ``prove`` is refused here.  A fleet's ``run.yaml`` is never the
place for a pipeline setting, so nothing here reads one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from cflib.errors import CfError
from cflib.yamlio import read_yaml

VERSION = 1
STAGES: tuple[str, ...] = tuple(f"S{i}" for i in range(10))
EXERCISE_POLICIES: tuple[str, ...] = ("stub", "prove")

#: key -> required, per block
TOP_KEYS = {"version": True, "pipeline": True, "exercise_policy": True, "scope": True,
            "consent": True, "fleets": True, "notes": False}
PIPELINE_KEYS = {"id": True, "source_id": True, "companion": True}
SCOPE_KEYS = {"chapters": False, "sections": False}
CONSENT_KEYS = {"include_source_text": True, "recorded": False, "note": False}

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SECTION = re.compile(r"\d+\.\d+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _keys(block: Mapping, allowed: Mapping[str, bool], where: str) -> list[str]:
    problems = [f"{where}: unknown key {k!r} (allowed: {', '.join(allowed)})"
                for k in block if k not in allowed]
    problems += [f"{where}: missing required key {k!r}"
                 for k, required in allowed.items() if required and k not in block]
    return problems


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(data: Any, *, expected_id: str | None = None) -> list[str]:
    """Problems with a loaded pipeline.yaml (empty list when it is valid)."""
    if not isinstance(data, Mapping):
        return ["pipeline.yaml must be a mapping at the top level"]
    problems = _keys(data, TOP_KEYS, "pipeline.yaml")
    if data.get("version") != VERSION:
        problems.append(f"version must be {VERSION} (found {data.get('version')!r})")

    pipeline = data.get("pipeline")
    if isinstance(pipeline, Mapping):
        problems += _keys(pipeline, PIPELINE_KEYS, "pipeline")
        for key in PIPELINE_KEYS:
            if key in pipeline and not _is_text(pipeline[key]):
                problems.append(f"pipeline.{key} must be a non-empty string")
        pid = pipeline.get("id")
        if _is_text(pid) and not _ID.fullmatch(pid):
            problems.append(f"pipeline.id {pid!r} may only hold letters, digits, '.', '_' and '-'")
        if expected_id is not None and _is_text(pid) and pid != expected_id:
            problems.append(f"pipeline.id {pid!r} is not the directory it lives in "
                            f"({expected_id!r})")
    elif "pipeline" in data:
        problems.append("pipeline must be a mapping (id, source_id, companion)")

    policy = data.get("exercise_policy", None)
    if policy is not None and policy not in EXERCISE_POLICIES:
        problems.append(f"exercise_policy must be null, {' or '.join(EXERCISE_POLICIES)} "
                        f"(found {policy!r})")

    scope = data.get("scope")
    if isinstance(scope, Mapping):
        problems += _keys(scope, SCOPE_KEYS, "scope")
        chapters = scope.get("chapters") or []
        sections = scope.get("sections") or []
        if not isinstance(chapters, list) or any(
                isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in chapters):
            problems.append("scope.chapters must be a list of positive integers")
        if not isinstance(sections, list) or any(
                not isinstance(s, str) or not _SECTION.fullmatch(s) for s in sections):
            problems.append("scope.sections must be a list of '<chapter>.<section>' strings")
        if isinstance(chapters, list) and isinstance(sections, list) and not (chapters or sections):
            problems.append("scope names no chapter and no section: the pipeline's scope is "
                            "explicit, never derived")
    elif "scope" in data:
        problems.append("scope must be a mapping (chapters, sections)")

    consent = data.get("consent")
    if isinstance(consent, Mapping):
        problems += _keys(consent, CONSENT_KEYS, "consent")
        include = consent.get("include_source_text")
        if "include_source_text" in consent and not isinstance(include, bool):
            problems.append("consent.include_source_text must be true or false")
        recorded = consent.get("recorded")
        if include is True and not (_is_text(recorded) and _DATE.fullmatch(recorded)):
            problems.append("consent.recorded (YYYY-MM-DD) is required when "
                            "include_source_text is true")
        if "note" in consent and not isinstance(consent["note"], str):
            problems.append("consent.note must be a string")
    elif "consent" in data:
        problems.append("consent must be a mapping (include_source_text, recorded, note)")

    fleets = data.get("fleets")
    if isinstance(fleets, Mapping):
        seen: dict[str, str] = {}
        for stage, run_ids in fleets.items():
            if stage not in STAGES:
                problems.append(f"fleets: {stage!r} is not a stage ({STAGES[0]} to {STAGES[-1]})")
                continue
            if not isinstance(run_ids, list):
                problems.append(f"fleets.{stage} must be a list of run ids")
                continue
            for run_id in run_ids:
                if not _is_text(run_id):
                    problems.append(f"fleets.{stage}: run id {run_id!r} is not a non-empty string")
                    continue
                if run_id in seen:
                    problems.append(f"fleets.{stage}: run id {run_id!r} is already listed "
                                    f"under {seen[run_id]}")
                seen[run_id] = stage
    elif "fleets" in data:
        problems.append("fleets must be a mapping of stage -> list of run ids")

    if "notes" in data and not isinstance(data["notes"], str):
        problems.append("notes must be a string")
    return problems


def load(path: str | Path, *, expected_id: str | None = None) -> dict:
    """Read and validate one pipeline.yaml; raises CfError naming every problem."""
    p = Path(path)
    data = read_yaml(p)
    problems = validate(data, expected_id=expected_id)
    if problems:
        raise CfError(f"{p} is invalid: " + "; ".join(problems),
                      hint="the shape is in scripts/pipeline/pipeline_yaml.py; every key "
                           "is checked and an unknown one is refused")
    return dict(data)


def load_for(paths, pipeline_id: str) -> dict:
    """The pipeline.yaml of ``pipelines/<id>/``, checked against its directory name."""
    return load(paths.pipeline_yaml(pipeline_id), expected_id=pipeline_id)


def fleet_run_ids(data: Mapping[str, Any], stage: str | None = None) -> list[str]:
    """The run ids a pipeline lists, for one stage or for all of them."""
    fleets = data.get("fleets") or {}
    if stage is not None:
        return [str(r) for r in (fleets.get(stage) or [])]
    return [str(r) for s in STAGES for r in (fleets.get(s) or [])]
