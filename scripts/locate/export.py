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
  declaration missing from the index, a document whose pins are not the run's,
  a challenge that no longer matches the record it judged) -- every row the
  outcomes put a ``problem`` on;
* a ``formal_math`` passage with no outcome (a scope a challenge overturned
  without a location): it is not a scoped-out passage, and nothing says what
  it states;
* a passage whose receipt or note carries more than the quote cap of the source
  verbatim;
* a record the vendored ``literature_links.check`` rejects;
* a passage whose capped quote is not unique on the layer;
* a whole source whose records pass the per-source byte budget;
* a whole source whose licence does not allow it: its manifest entry's
  ``licence_class`` must be the one the registry gives the source
  (``Registry.licence_class``), that class must be one an exported record may
  quote (``cflib.vocab.OPEN_LICENCE_CLASSES``), and an attribution class needs
  the family's SPDX licence id the records carry -- every record quotes its
  passage, and no redacted quote form exists to fall back on.

The citation is the sources stage's: ``sources.websource.family`` (which
refuses an unknown or broken family) gives the container title and the SPDX
id, and ``sources.websource.revision_url`` the permalink of the pinned revision.
The manifest is the one the run pinned, through ``Sourceset.pinned`` (refused
when that version was rewritten), and each source's files must still be the
ones its entry pins.

Only ``formal_math`` passages with an outcome are exported; a scoped-out
passage, and one no document landed for, are counted in the report.  The set
is a function of its inputs: re-exporting the same runs against the same
sourceset, universe and checkout writes byte-identical files, and a re-export
into the same directory removes the files the PREVIOUS seal named that the new
set does not carry -- nothing else.  The target is refused when it is this
checkout or a companion repository (or inside one), a symlink, or a non-empty
directory that holds no ``cf-locate-recordset/1`` seal, or holds a file its seal
does not name.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from cflib import gitsafe, hashing, registry as registry_mod, vocab
from cflib.errors import CfError
from cflib.jsonio import atomic_write, write_json
from cflib.paths import WorkspacePaths, skill_root

from sources import websource

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
                     "licence": lib.get("licence")}
    return {"id": universe.id, "sha256": universe.sha256, "libraries": libs,
            "excluded": universe.excluded}


def _source_record(entry: dict, fam) -> dict:
    """One ``sources.jsonl`` row and the citation its records carry."""
    return {
        "source_id": entry["source_id"], "pageid": entry["pageid"],
        "revision": entry["revision"], "title": entry.get("title"),
        "wikidata": entry.get("wikidata"), "html_sha256": entry.get("html_sha256"),
        "layer_sha256": entry.get("layer_sha256"), "renderer": entry.get("renderer"),
        "assessment": entry.get("assessment"),
        "uri": websource.revision_url(fam, entry["pageid"], entry["revision"]),
        "container_title": fam.container_title,
        "licence": fam.spdx,
    }


def licence_problem(entry: dict, fam, reg) -> str | None:
    """Why a source's licence keeps it out of a record set, or None."""
    sid = entry["source_id"]
    recorded = str(entry.get("licence_class") or "")
    registered = reg.licence_class(sid)
    if recorded != registered:
        return (f"the manifest records licence_class {recorded or '(none)'!r} and the "
                f"registry gives {registered!r}")
    if registered not in vocab.OPEN_LICENCE_CLASSES:
        return (f"licence class {registered!r}: an exported record quotes the source, and only "
                f"{', '.join(sorted(vocab.OPEN_LICENCE_CLASSES))} may be quoted")
    if registered in vocab.ATTRIBUTION_LICENCE_CLASSES and not fam.spdx:
        return (f"licence class {registered!r} needs the licence named, and family {fam.id} "
                f"has no SPDX id")
    return None


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
    manifest = inputs.pinned_manifest(paths, pin)
    universe = inputs.load_universe(paths, built["universe"]["id"])
    uni_doc = {"id": universe.id, "sha256": universe.sha256,
               "libraries": [{"id": lid, **lib} for lid, lib in sorted(universe.libraries.items())]}
    reg = registry_mod.Registry.load(workspace)
    out = Path(out_dir).expanduser().resolve() if out_dir else paths.export_dir(set_id)
    previous = check_target(out, workspace)

    refused: list[dict] = []
    skipped = Counter()
    by_source: dict[str, list[dict]] = {}
    for row in built["outcomes"]:
        if row["missing"]:
            skipped["missing"] += 1
            continue
        if row.get("problem"):
            refused.append({"source_id": row["source_id"], "item": row["item"],
                            "reason": row["problem"]})
            continue
        if row["outcome"] is None:
            if row.get("scope") in vocab.LOCATED_SCOPES:
                refused.append({"source_id": row["source_id"], "item": row["item"],
                                "reason": "a formal_math passage with no outcome"})
                continue
            skipped["scoped_out"] += 1
            continue
        by_source.setdefault(row["source_id"], []).append(row)

    files: dict[str, str] = {}
    texts: dict[str, str] = {}
    sources_rows: list[dict] = []
    sidecars: dict[str, str] = {}
    exported = Counter()
    families: dict[str, object] = {}
    for sid in sorted(by_source):
        entry = manifest.entries.get(sid)
        if not inputs.rendered(entry):
            refused.append({"source_id": sid, "item": None,
                            "reason": "not a rendered source of the pinned manifest"})
            continue
        try:
            inputs.check_source(paths, pin["id"], entry)
            family_id = str(entry.get("family") or "")
            if family_id not in families:
                families[family_id] = websource.family(family_id, workspace)
            fam = families[family_id]
            source = _source_record(entry, fam)
        except CfError as exc:
            refused.append({"source_id": sid, "item": None, "reason": str(exc)})
            continue
        why = licence_problem(entry, fam, reg)
        if why:
            refused.append({"source_id": sid, "item": None, "reason": why})
            continue
        layer = inputs.source_view(paths, pin["id"], sid).layer()
        lines: list[str] = []
        source_sidecars: dict[str, str] = {}
        kept = 0
        for row in sorted(by_source[sid], key=lambda r: ((r.get("span") or [0])[0], r["item"])):
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
            for digest, car in cars:
                source_sidecars[digest] = car
            kept += 1
            exported["records"] += len(recs)
        if not lines:
            continue
        text = "\n".join(lines) + "\n"
        size = len(text.encode("utf-8")) + sum(len(car.encode("utf-8"))
                                               for car in source_sidecars.values())
        if byte_budget and size > byte_budget:
            refused.append({"source_id": sid, "item": None,
                            "reason": f"records/{sid}.jsonl and its receipts would be {size} "
                                      f"bytes, over the per-source budget of {byte_budget}"})
            exported["records"] -= len(lines)
            continue
        texts[f"records/{sid}.jsonl"] = text
        sidecars.update(source_sidecars)
        sources_rows.append({k: source[k] for k in ("source_id", "pageid", "revision", "title",
                                                     "wikidata", "html_sha256", "layer_sha256",
                                                     "renderer", "assessment")})
        exported["sources"] += 1
        exported["passages"] += kept
    for digest, car in sorted(sidecars.items()):
        texts[f"receipts/{digest}.json"] = car
    texts["universe.json"] = records.canonical(_universe_json(universe)) + "\n"
    texts["sources.jsonl"] = "".join(records.canonical(r) + "\n" for r in sources_rows)

    _write_set(out, texts, previous)
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


def check_target(out: Path, workspace=None) -> set[str]:
    """Refuse a target a record set may not be written to; the files its seal names.

    Never this checkout or a companion repository (CLAUDE.md sections 1 and 4),
    never a symlink, and never a directory that holds anything but an earlier
    set: a non-empty directory needs a ``cf-locate-recordset/1`` ``SEAL.json``,
    and every file in it must be one that seal names.  Returns the names, which
    a re-export may remove when the new set does not carry them.
    """
    from cflib.jsonio import read_json

    out = Path(out)
    root = skill_root().resolve()
    target = out.resolve()
    if target == root or root in target.parents:
        raise CfError(f"refusing to write a record set inside the skill repo ({root})",
                      hint="the skill repo holds code, templates and docs only (CLAUDE.md "
                           "section 1); pass --out outside it, or omit --out")
    for name, companion in sorted(getattr(workspace, "companions", {}).items()):
        if str(getattr(companion, "repo", "") or "") in ("", "."):
            continue
        repo = Path(str(companion.repo)).expanduser().resolve()
        if target == repo or repo in target.parents:
            raise CfError(f"refusing to write a record set inside the companion repo {name} "
                          f"({repo})",
                          hint="nothing this skill runs writes into a companion (CLAUDE.md "
                               "section 4); pass --out elsewhere, or omit --out")
    for path in (out, out / "records", out / "receipts", out / "SEAL.json"):
        if path.is_symlink():
            raise CfError(f"refusing to write a record set through the symlink {path}")
    if not out.exists():
        return set()
    if not out.is_dir():
        raise CfError(f"--out {out} is not a directory")
    present = {p.relative_to(out).as_posix() for p in out.rglob("*") if not p.is_dir()}
    if not present:
        return set()
    seal_path = out / "SEAL.json"
    seal = read_json(seal_path) if seal_path.is_file() else None
    if not isinstance(seal, dict) or seal.get("schema") != SEAL_SCHEMA:
        raise CfError(f"{out} is not empty and holds no {SEAL_SCHEMA} seal",
                      hint="pass an empty or new directory with --out")
    named = set(seal.get("files") or {})
    stray = sorted(present - named - {"SEAL.json"})
    if stray:
        raise CfError(f"{out} holds file(s) its seal does not name: {', '.join(stray[:5])}",
                      hint="a record set directory holds the set and nothing else; move them "
                           "out, or pass another --out")
    return named


def _write_set(out: Path, texts: dict[str, str], previous: set[str] = frozenset()) -> None:
    """Write the set's files; remove what the previous seal named and this set does not."""
    out.mkdir(parents=True, exist_ok=True)
    for rel in sorted(set(previous) - set(texts)):
        path = out / rel
        if path.is_file() and not path.is_symlink():
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
