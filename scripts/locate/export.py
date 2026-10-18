"""``cf export literature-links``: a locate run's outcomes as a sealed record set.

New code, implementing docs/proposals/wikipedia-locate.md sections 2.7, 3 (L6)
and 11.1 in the layout of references/locate-documents.md section 6::

    <set>/SEAL.json                 every other file with its sha256, and the pins
    <set>/universe.json             the universe: its sha, frozen date, a LIST of libraries
    <set>/sources.jsonl             per source: ids, revision, title, QID, shas, renderer
    <set>/records/<source-id>.jsonl the Literature Links 2.0 records
    <set>/receipts/<sha256>.json    the search receipts the absence records reference

The shapes are the ones the consumer reads (references/locate-documents.md
section 6): ``SEAL.json`` names the set, a campaign token that is never null
(:func:`campaign_token`), the universe as ``{id, universe_sha256}``, the
sourceset, the skill commit, the creation time and the byte budget it applied;
``universe.json`` is ``{id, universe_sha256, frozen, libraries: [...] by id,
excluded: [{id, reason}]}``.  No per-source byte budget applies unless one is
asked for (``byte_budget``, default 0: off) -- a consumer with a cap passes its
own.

Before any file is written it refuses a ``--set-id``, a pinned sourceset id or a
campaign token outside the record set's token grammar (``cflib.vocab.RECORDSET_TOKEN``
and ``RUN_TOKEN_ID``): the consumer refuses the whole set over its seal.

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
* a passage the record set cannot describe: no section heading path or no
  prominence, a ``closeMatch`` with no note, or a location whose index row gives
  no module, path or line (``records.location_problem``);
* a whole source whose records pass the per-source byte budget, when one is
  given;
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

import re
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
#: the default per-source byte budget of records/<source-id>.jsonl and its receipts:
#: 0, off.  A consumer with a cap (an import into a store with a per-row limit) passes
#: its own with --byte-budget, and the seal records the budget that applied.
DEFAULT_BYTE_BUDGET = 0
#: what a run in no campaign is sealed under: ``run:<locate run>[+<challenge run>]``
RUN_CAMPAIGN_PREFIX = vocab.RUN_TOKEN_PREFIX
#: the seal's keys, exactly: the agreed record-set format (references/locate-documents.md
#: section 6) -- a consumer refuses a seal with any other key, so the export writes no other
SEAL_KEYS = ("schema", "set_id", "campaign", "universe", "sourceset", "skill_commit", "created",
             "byte_budget", "files")
#: a campaign id the seal may carry: letters, digits and ``._:-``, at most 128, never
#: starting ``run:`` (that prefix is a run token's); the grammar of ``set_id`` and
#: ``sourceset.id`` too (``cflib.vocab.RECORDSET_TOKEN``, the one definition)
CAMPAIGN_ID_RE = vocab.RECORDSET_TOKEN
#: a library's ``display_name`` the consumer shows: printable ASCII letters, digits,
#: spaces and ``,.()/+:'&_-`` or a middle dot, at most 100 -- no invisible or look-alike
#: character (its consumer refuses the whole set over any other, and the contract
#: lets the name be null instead)
DISPLAY_NAME_RE = re.compile(r"[A-Za-z0-9 ,.()\u00b7/+:'&_-]{1,100}")
#: a run id inside a run token: letters, digits and ``._-``, at most 64
#: (``cflib.vocab.RUN_TOKEN_ID``, which ``cf plan-locate`` checks a run id against)
TOKEN_RUN_ID_RE = vocab.RUN_TOKEN_ID


def skill_commit() -> str | None:
    """This checkout's HEAD through ``cflib.gitsafe``, or None outside a git checkout."""
    try:
        done = gitsafe.run(skill_root(), ["rev-parse", "HEAD"], timeout=30)
    except OSError:                                          # pragma: no cover
        return None
    found = done.stdout.strip() if done.returncode == 0 else ""
    return found if len(found) == 40 else None


def campaign_token(run: dict, run_id: str, challenge_runs: Iterable[str]) -> str:
    """The seal's ``campaign``: never null, and only a token the agreed grammar admits.

    The locate run's ``run.yaml`` ``locate.campaign`` (``cf campaign plan``
    stamps a shard with its campaign id); for a run in no campaign,
    ``run:<locate run>``, followed by ``+<challenge run>`` when one challenge
    run applies, so two sets of one run with and without its challenge are told
    apart.  The grammar is references/locate-documents.md section 6's: a
    campaign id is letters, digits and ``._:-`` (at most 128, never starting
    ``run:``), a run id letters, digits and ``._-`` (at most 64), and a run token
    names at most one challenge run.  Anything else is refused before a file is
    written: the consumer refuses the whole set over its seal.
    """
    challenge_runs = list(challenge_runs)
    stamped = str((run.get("locate") or {}).get("campaign") or "").strip()
    if stamped:
        if not CAMPAIGN_ID_RE.fullmatch(stamped) or stamped.startswith(RUN_CAMPAIGN_PREFIX):
            raise CfError(f"run {run_id}'s campaign {stamped!r} is not a campaign id a record "
                          "set may carry",
                          hint="a campaign id is letters, digits and ._:- (at most 128), never "
                               f"starting {RUN_CAMPAIGN_PREFIX!r} (references/locate-documents.md "
                               "section 6)")
        return stamped
    if len(challenge_runs) > 1:
        raise CfError(f"run {run_id} is in no campaign, and a run token names at most one "
                      f"challenge run, not {len(challenge_runs)} "
                      f"({', '.join(challenge_runs)})",
                      hint="export with the one --challenge-run whose verdicts apply, or export "
                           "a campaign's shard: its run.yaml locate.campaign names the set")
    for rid in [run_id, *challenge_runs]:
        if not TOKEN_RUN_ID_RE.fullmatch(rid):
            raise CfError(f"run id {rid!r} cannot be spelled in a run token",
                          hint="a run id in a record set's campaign token is letters, digits "
                               "and ._- (at most 64); plan the run under such an id")
    return RUN_CAMPAIGN_PREFIX + "+".join([run_id, *challenge_runs])


def check_token(value: str, what: str, hint: str) -> str:
    """``value`` when the record set's token grammar admits it, else refuse naming ``what``.

    A seal's ``set_id`` and ``sourceset.id`` are letters, digits and ``._:-``, at
    most 128 (``cflib.vocab.RECORDSET_TOKEN``): a consumer refuses the whole set over a
    seal naming any other, so the export refuses first, before a file is written.
    """
    if not isinstance(value, str) or not vocab.RECORDSET_TOKEN.fullmatch(value):
        raise CfError(f"{what} {value!r} is not a token a record set's seal may carry",
                      hint="a record-set token is letters, digits and ._:- (at most 128, "
                           f"starting with a letter or digit; references/locate-documents.md "
                           f"section 6); {hint}")
    return value


def _universe_json(universe: inputs.Universe) -> dict:
    """``universe.json``: the universe's sha and frozen date, and its libraries as a list.

    Each library: ``id``, ``brain_lib``, ``repo``, ``commit``, ``toolchain``,
    ``roots``, ``licence`` (``{spdx}``: a library with none is refused, by name,
    with the re-add that records it) and ``display_name`` -- the
    library's ``brain_lib``, the one name the universe gives it, when that is a
    display name (:data:`DISPLAY_NAME_RE`), else null: ``brain_lib`` has no grammar
    of its own, and the consumer refuses the whole set over a display name outside
    its own.  ``excluded`` is ``[{id, reason}]``.  Sorted by id; nothing the
    universe does not define.
    """
    frozen = str(universe.owner.frozen or "")
    if not frozen:
        raise CfError(f"universe {universe.id} records no frozen date",
                      hint="a universe made by `cf corpus init` carries one; set `frozen: "
                           "YYYY-MM-DD` in its universe.yaml to the day it was frozen")
    unlicensed = [lid for lid, lib in sorted(universe.libraries.items())
                  if not _spdx(lib.get("licence"))]
    if unlicensed:
        lib = universe.libraries[unlicensed[0]]
        raise CfError(f"universe {universe.id}: {len(unlicensed)} library(ies) record no "
                      f"licence: {', '.join(unlicensed)}",
                      hint="a record set names every library's SPDX licence and its consumer "
                           "refuses one without; record it with `cf corpus add "
                           f"{universe.id} --library {unlicensed[0]} --repo {lib.get('repo')} "
                           f"--commit {lib.get('commit')} --toolchain {lib.get('toolchain')} "
                           f"--roots {' '.join(str(r) for r in lib.get('roots') or [])} "
                           "--licence <SPDX id>` (outside universe_sha256: the pins stay valid)")
    libs = []
    for lid, lib in sorted(universe.libraries.items()):
        name = str(lib.get("brain_lib") or lid)
        libs.append({"id": lid, "brain_lib": name, "repo": lib.get("repo"),
                     "commit": lib["commit"], "toolchain": lib.get("toolchain"),
                     "roots": [str(r) for r in lib.get("roots") or []],
                     "licence": lib.get("licence"),
                     "display_name": name if DISPLAY_NAME_RE.fullmatch(name) else None})
    excluded = [{"id": str(row.get("id")), "reason": str(row.get("reason") or "")}
                for row in sorted(universe.excluded, key=lambda r: str(r.get("id")))]
    return {"id": universe.id, "universe_sha256": universe.sha256, "frozen": frozen,
            "libraries": libs, "excluded": excluded}


def _spdx(licence) -> str:
    """A library row's ``{spdx}`` licence id, or '' when it records none."""
    if isinstance(licence, dict) and set(licence) == {"spdx"} and isinstance(licence["spdx"], str):
        return licence["spdx"].strip()
    return ""


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
    check_token(set_id, "--set-id", "pass another --set-id")
    challenge_runs = list(challenge_runs)
    built = outcomes_mod.build(paths, run_id, challenge_runs=challenge_runs, require_gate=True)
    if built["mode"] != vocab.LOCATE:
        raise CfError(f"run {run_id} is a {built['mode']} run; the export reads a locate run",
                      hint="pass the locate run with --run and its challenge runs with "
                           "--challenge-run")
    run = built["run"]
    campaign = campaign_token(run, run_id, challenge_runs)
    pin = run["locate"]["sourceset"]
    check_token(pin.get("id"), f"run {run_id}'s sourceset id",
                "plan the run over a sourceset whose id is one (`cf sourceset create` "
                "refuses any other)")
    manifest = inputs.pinned_manifest(paths, pin)
    universe = inputs.load_universe(paths, built["universe"]["id"])
    uni_doc = {"id": universe.id, "sha256": universe.sha256,
               "libraries": [{"id": lid, **lib} for lid, lib in sorted(universe.libraries.items())]}
    universe_text = records.canonical(_universe_json(universe)) + "\n"
    reg = registry_mod.Registry.load(workspace)
    out = Path(out_dir).expanduser().resolve() if out_dir else paths.export_dir(set_id)
    previous = check_target(out, workspace)
    commit = skill_commit()
    if commit is None:
        raise CfError(f"this checkout ({skill_root()}) has no git HEAD to seal the set with",
                      hint="a sealed set names the skill commit that made it; export from a "
                           "git checkout of the skill")
    harvested = sorted(str(r["harvested_at"]) for r in
                       [inputs.receipt(paths.corpus_run_dir(rid)) or {}
                        for rid in [run_id, *challenge_runs]] if r.get("harvested_at"))
    if not harvested:
        raise CfError(f"run {run_id} and its challenge runs record no harvest time",
                      hint="the seal's `created` is the newest harvest time, so a re-export "
                           f"is byte-identical; re-run `cf harvest {run_id}`")

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
    texts["universe.json"] = universe_text
    texts["sources.jsonl"] = "".join(records.canonical(r) + "\n" for r in sources_rows)

    _write_set(out, texts, previous)
    for rel, text in sorted(texts.items()):
        files[rel] = hashing.sha256_text(text)
    seal = {
        "schema": SEAL_SCHEMA,
        "set_id": set_id,
        "campaign": campaign,
        "universe": {"id": universe.id, "universe_sha256": universe.sha256},
        "sourceset": {"id": pin["id"], "manifest_sha256": manifest.sha256},
        "skill_commit": commit,
        "created": harvested[-1],
        "byte_budget": int(byte_budget or 0),
        "files": files,
    }
    atomic_write(out / "SEAL.json", records.canonical(seal) + "\n")
    # the runs and the manifest version are the workspace's provenance, in the report:
    # the agreed seal carries the campaign token and the manifest's sha instead
    report = {"set_id": set_id, "path": str(out), "run_id": run_id, "campaign": campaign,
              "challenge_runs": challenge_runs,
              "inventory_run": run["locate"].get("inventory_run"),
              "sourceset": {"id": pin["id"], "version": pin["version"],
                            "manifest_sha256": manifest.sha256},
              "exported": dict(sorted(exported.items())),
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
