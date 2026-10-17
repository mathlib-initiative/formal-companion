"""``cf export literature-links``: a locate run's outcomes as a sealed record set.

New code, implementing docs/proposals/wikipedia-locate.md sections 2.7, 3 (L6)
and 11.1 in the layout of references/locate-documents.md section 6::

    <set>/SEAL.json                 every other file with its sha256, and the pins
    <set>/universe.json             the universe: per library its repo, commit, licence
    <set>/sources.jsonl             per source: ids, revision, title, QID, shas, renderer
    <set>/records/<source-id>.jsonl the Literature Links 2.0 records
    <set>/receipts/<sha256>.json    the search receipts the absence records reference

What it refuses, passage by passage, naming each one in the export report
(``reports/export.<set-id>.json``) and exiting non-zero after writing the rest:

* a passage whose challenge is owed -- hard rule 5: nothing leaves the
  workspace without a ``challenge`` value, per record;
* a passage whose outcome the harvest could not derive (a contradiction, a
  declaration missing from the index);
* a record the vendored ``literature_links.check`` rejects;
* a passage whose capped quote is not unique on the layer;
* a whole source whose records pass the per-source byte budget.

Only ``formal_math`` passages with an outcome are exported; a scoped-out
passage, and one no document landed for, are counted in the report.  The set
is a function of its inputs: re-exporting the same runs against the same
sourceset, universe and checkout writes byte-identical files, and a re-export
into the same directory removes the files the new set does not carry.
"""
from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable

from cflib import gitsafe, hashing, registry as registry_mod, vocab
from cflib.errors import CfError
from cflib.jsonio import atomic_write, write_json
from cflib.paths import WorkspacePaths, skill_root

from . import inputs, outcomes as outcomes_mod, records

SEAL_SCHEMA = "cf-locate-recordset/1"
#: the default per-source byte budget of records/<source-id>.jsonl
DEFAULT_BYTE_BUDGET = 256 * 1024


def skill_commit() -> str | None:
    """This checkout's HEAD through ``cflib.gitsafe``, or None outside a git checkout."""
    try:
        done = gitsafe.run(skill_root(), ["rev-parse", "HEAD"], timeout=30)
    except OSError:                                          # pragma: no cover
        return None
    found = done.stdout.strip() if done.returncode == 0 else ""
    return found if len(found) == 40 else None


def _universe_json(universe: inputs.Universe) -> dict:
    libs = {}
    for lid, lib in sorted(universe.libraries.items()):
        libs[lid] = {"repo": lib.get("repo"), "commit": lib["commit"],
                     "toolchain": lib.get("toolchain"), "brain_lib": lib.get("brain_lib"),
                     "display_name": lib.get("display_name") or lid,
                     "licence": lib.get("licence")}
    return {"id": universe.id, "sha256": universe.sha256, "libraries": libs,
            "excluded": universe.excluded}


def _source_record(entry: dict, family) -> dict:
    uri = ""
    if family is not None and family.revision_url:
        uri = family.revision_url.format(pageid=entry["pageid"], revision=entry["revision"])
    return {
        "source_id": entry["source_id"], "pageid": entry["pageid"],
        "revision": entry["revision"], "title": entry.get("title"),
        "wikidata": entry.get("wikidata"), "html_sha256": entry.get("html_sha256"),
        "layer_sha256": entry.get("layer_sha256"), "renderer": entry.get("renderer"),
        "assessment": entry.get("assessment"),
        "uri": uri,
        "container_title": family.container_title if family is not None else "",
        "licence": (family.licence or {}).get("spdx", "") if family is not None else "",
    }


def build_set(paths: WorkspacePaths, run_id: str, *, challenge_runs: Iterable[str] = (),
              set_id: str, byte_budget: int = DEFAULT_BYTE_BUDGET, out_dir: Path | None = None,
              workspace=None) -> dict:
    """Write the sealed set; return the export report (also written to reports/)."""
    challenge_runs = list(challenge_runs)
    built = outcomes_mod.build(paths, run_id, challenge_runs=challenge_runs, require_gate=True)
    if built["mode"] != vocab.LOCATE:
        raise CfError(f"run {run_id} is a {built['mode']} run; the export reads a locate run",
                      hint="pass the locate run with --run and its challenge runs with "
                           "--challenge-run")
    run = built["run"]
    pin = run["locate"]["sourceset"]
    manifest = inputs.load_manifest(paths, pin["id"], int(pin["version"]))
    if manifest.sha256 != pin.get("manifest_sha256"):
        raise CfError(f"sourceset {pin['id']} version {pin['version']} is not the manifest run "
                      f"{run_id} pinned", hint="a manifest version is never rewritten; this one was")
    universe = inputs.load_universe(paths, built["universe"]["id"])
    uni_doc = {"id": universe.id, "sha256": universe.sha256,
               "libraries": [{"id": lid, **lib} for lid, lib in sorted(universe.libraries.items())]}
    reg = registry_mod.Registry.load(workspace)
    out = Path(out_dir) if out_dir else paths.export_dir(set_id)

    refused: list[dict] = []
    skipped = Counter()
    by_source: dict[str, list[dict]] = {}
    for row in built["outcomes"]:
        if row["missing"]:
            skipped["missing"] += 1
            continue
        if row["outcome"] is None:
            skipped["scoped_out"] += 1
            continue
        by_source.setdefault(row["source_id"], []).append(row)

    files: dict[str, str] = {}
    texts: dict[str, str] = {}
    sources_rows: list[dict] = []
    sidecars: dict[str, dict] = {}
    exported = Counter()
    for sid in sorted(by_source):
        entry = manifest.entries.get(sid)
        if entry is None or not manifest.rendered(sid):
            refused.append({"source_id": sid, "item": None,
                            "reason": "not a rendered source of the pinned manifest"})
            continue
        view = inputs.source_view(paths, pin["id"], sid)
        if view.layer_sha256() != entry["layer_sha256"]:
            refused.append({"source_id": sid, "item": None,
                            "reason": "layer.txt is not the layer the manifest pins"})
            continue
        layer = view.layer()
        source = _source_record(entry, reg.family(str(entry.get("family") or "")))
        if not source["uri"]:
            refused.append({"source_id": sid, "item": None,
                            "reason": f"family {entry.get('family')!r} has no revision_url"})
            continue
        lines: list[str] = []
        source_sidecars: dict[str, dict] = {}
        kept = 0
        for row in sorted(by_source[sid], key=lambda r: ((r.get("span") or [0])[0], r["item"])):
            if row.get("problem"):
                refused.append({"source_id": sid, "item": row["item"], "reason": row["problem"]})
                continue
            try:
                recs, cars = records.passage_records(source, row, layer, uni_doc)
            except records.RecordError as exc:
                refused.append({"source_id": sid, "item": row["item"], "reason": str(exc)})
                continue
            problems = records.check_all(recs)
            if problems:
                refused.append({"source_id": sid, "item": row["item"],
                                "reason": "; ".join(problems[:3])})
                continue
            lines.extend(records.canonical(r) for r in recs)
            for digest, body in cars:
                source_sidecars[digest] = body
            kept += 1
            exported["records"] += len(recs)
        if not lines:
            continue
        text = "\n".join(lines) + "\n"
        size = len(text.encode("utf-8"))
        if byte_budget and size > byte_budget:
            refused.append({"source_id": sid, "item": None,
                            "reason": f"records/{sid}.jsonl would be {size} bytes, over the "
                                      f"per-source budget of {byte_budget}"})
            exported["records"] -= len(lines)
            continue
        texts[f"records/{sid}.jsonl"] = text
        sidecars.update(source_sidecars)
        sources_rows.append({k: source[k] for k in ("source_id", "pageid", "revision", "title",
                                                     "wikidata", "html_sha256", "layer_sha256",
                                                     "renderer", "assessment")})
        exported["sources"] += 1
        exported["passages"] += kept
    for digest, body in sorted(sidecars.items()):
        texts[f"receipts/{digest}.json"] = records.canonical(body) + "\n"
    texts["universe.json"] = records.canonical(_universe_json(universe)) + "\n"
    texts["sources.jsonl"] = "".join(records.canonical(r) + "\n" for r in sources_rows)

    _write_set(out, texts)
    for rel, text in sorted(texts.items()):
        files[rel] = hashing.sha256_text(text)
    harvested = sorted(str(r["harvested_at"]) for r in
                       [inputs.receipt(paths.corpus_run_dir(rid)) or {}
                        for rid in [run_id, *challenge_runs]] if r.get("harvested_at"))
    seal = {
        "schema": SEAL_SCHEMA,
        "campaign": run["locate"].get("campaign"),
        "runs": {"locate": run_id, "challenge": challenge_runs,
                 "inventory": run["locate"].get("inventory_run")},
        "universe": {"id": universe.id, "sha256": universe.sha256},
        "sourceset": {"id": pin["id"], "version": pin["version"],
                      "manifest_sha256": manifest.sha256},
        "skill_commit": skill_commit(),
        "created": harvested[-1] if harvested else None,
        "files": files,
    }
    atomic_write(out / "SEAL.json", records.canonical(seal) + "\n")
    report = {"set_id": set_id, "path": str(out), "run_id": run_id,
              "challenge_runs": challenge_runs, "exported": dict(sorted(exported.items())),
              "skipped": dict(sorted(skipped.items())), "refused": refused,
              "byte_budget": byte_budget, "seal_sha256": hashing.sha256_file(out / "SEAL.json")}
    write_json(paths.export_report_json(set_id), report, sort_keys=True)
    return report


def _write_set(out: Path, texts: dict[str, str]) -> None:
    """Write the set's files and remove what an earlier export left that this one does not."""
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("records", "receipts"):
        directory = out / sub
        if directory.is_dir():
            for path in directory.iterdir():
                if f"{sub}/{path.name}" not in texts:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
    for rel, text in sorted(texts.items()):
        atomic_write(out / rel, text)


def verify_seal(set_dir: Path) -> list[str]:
    """What a consumer checks: every file the seal names has its sha, and nothing else is there."""
    from cflib.jsonio import read_json

    seal = read_json(Path(set_dir) / "SEAL.json")
    problems = []
    named = dict(seal.get("files") or {})
    for rel, sha in sorted(named.items()):
        path = Path(set_dir) / rel
        if not path.is_file():
            problems.append(f"{rel}: missing")
        elif hashing.sha256_file(path) != sha:
            problems.append(f"{rel}: sha256 does not match the seal")
    for path in sorted(Path(set_dir).rglob("*")):
        rel = path.relative_to(set_dir).as_posix()
        if path.is_file() and rel != "SEAL.json" and rel not in named:
            problems.append(f"{rel}: not in the seal")
    return problems
