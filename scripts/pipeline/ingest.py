"""S0, ingest and pin: one PDF in, ``doc.v0.json`` and the S0 stage receipt out.

New code for milestone M1 of docs/proposals/pdf-to-companion.md, section 2.1.
Source preparation (``pdftotext``, the page offset from the running heads,
``items.json``) is ``sources/prepare_source.py`` and the raw errata fetch is
``sources/fetch_errata.py``; this module pins what they read and starts the
pipeline around them:

* the PDF's facts: sha256, bytes, page count and page sizes (``pdfinfo``), the
  text layer's extractor and version (``pdftotext -v``);
* the edition check: the sha is looked up in the registry, and an edition the
  registry marks ``draft`` or ``superseded`` (``edition_status``) is refused
  unless the caller records an override with a reason; a ``--source-id`` that
  names a registry entry pinned to another sha is refused (CLAUDE.md section
  4, "Stamp the source"), and an unknown sha leaves a pending ``edition`` gate
  in the receipt's ``manual`` list;
* the offset gate: below ``MIN_OFFSET_CONFIDENCE`` the stage stops and names
  ``--offset``, whose value is then stamped ``offset_method: manual``; the
  pages whose running head disagrees with the offset are listed in the
  receipt, so the pending spot check knows where to look;
* the generated companion repository: an empty git repository under the
  pipeline directory, committed once so ``companion.commit`` is a real 40-hex
  id and ``companion.repo`` is ``local/<name>``;
* ``pipelines/<id>/pipeline.yaml``, ``doc.v0.json`` and
  ``stages/S0.stage-receipt.json``.

The PDF and its page texts stay in the workspace: ``sources/<id>/`` is ignored
by the workspace's git, the document records the PDF by its sha256 only, and
the generated repository ignores ``*.pdf``.  The spot-check agent of section
2.1 is not built: the receipt records it as a pending manual gate.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cflib import gitsafe, inference
from cflib.errors import CfError, SourceError
from cflib.hashing import sha256_file
from cflib.registry import (EDITION_CURRENT, EDITION_DRAFT, EDITION_SUPERSEDED, Registry,
                            RegistryEntry, source_id_for)
from cflib.yamlio import write_yaml
from pipeline import document, pipeline_yaml, stage_receipt
from sources import fetch_errata, prepare_source

STAGE = "S0"
GENERATOR = "cf ingest (pipeline S0, v1)"
PROV_INGEST = "prov-s0-ingest"
#: who the generated repository's first commit is by; `-c` settings, because
#: gitsafe.run takes no environment of its own
_GIT_IDENTITY = ("-c", "user.name=cf ingest", "-c", "user.email=cf-ingest@example.invalid",
                 "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main")
_URL = re.compile(r"https?://", re.I)
_SCOPE = re.compile(r"^(chapters|sections)=(.+)$")


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------

def parse_scope(spec: str) -> dict:
    """``chapters=1,2`` or ``sections=1.3,1.7`` (``;`` joins both) -> the pipeline.yaml block."""
    scope: dict[str, list] = {}
    for part in [p.strip() for p in (spec or "").split(";") if p.strip()]:
        m = _SCOPE.match(part)
        if not m:
            raise CfError(f"--scope {spec!r}: expected chapters=<n>[,<n>...] or "
                          f"sections=<c>.<s>[,...]")
        values = [v.strip() for v in m.group(2).split(",") if v.strip()]
        if m.group(1) == "chapters":
            try:
                scope["chapters"] = sorted({int(v) for v in values})
            except ValueError:
                raise CfError(f"--scope {spec!r}: a chapter is a positive integer") from None
        else:
            scope["sections"] = sorted(set(values))
    if not scope:
        raise CfError("--scope is required and names at least one chapter or section",
                      hint="for example --scope chapters=1; the scope is explicit, never "
                           "derived (proposal section 2.1)")
    return scope


# ---------------------------------------------------------------------------
# the PDF's own facts
# ---------------------------------------------------------------------------

def _tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise SourceError(f"{name} not found on PATH",
                          hint="install poppler (poppler-utils): cf ingest needs pdftotext "
                               "and pdfinfo")
    return exe


def tool_version(name: str) -> str:
    """``24.02.0`` from ``<name> -v`` (poppler prints it on stderr)."""
    proc = subprocess.run([_tool(name), "-v"], capture_output=True, text=True)
    m = re.search(r"version\s+(\S+)", (proc.stderr or "") + (proc.stdout or ""))
    if not m:
        raise SourceError(f"cannot read the version of {name}")
    return m.group(1)


def pdfinfo(pdf: str | Path, pages: int | None = None) -> dict:
    """``{pages, sizes: [(w, h), ...], info: {Title, Author, ...}}`` from ``pdfinfo``."""
    exe = _tool("pdfinfo")
    argv = [exe]
    if pages:
        argv += ["-f", "1", "-l", str(pages)]
    proc = subprocess.run(argv + [str(pdf)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SourceError(f"pdfinfo failed on {pdf}: {proc.stderr.strip()}")
    info: dict[str, str] = {}
    sizes: dict[int, tuple[float, float]] = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", line)
        if m:
            sizes[int(m.group(1))] = (round(float(m.group(2)), 3), round(float(m.group(3)), 3))
            continue
        key, sep, value = line.partition(":")
        if sep and not key.startswith("Page "):
            info[key.strip()] = value.strip()
    count = int(info.get("Pages") or 0)
    if pages is None and count:
        return pdfinfo(pdf, count)
    return {"pages": count, "sizes": [sizes[p] for p in sorted(sizes)], "info": info}


def page_size_fields(sizes: list[tuple[float, float]]) -> dict:
    """``page_size`` (the commonest box) and ``page_sizes`` ranges for the others."""
    if not sizes:
        return {}
    common = Counter(sizes).most_common(1)[0][0]
    out: dict[str, Any] = {"page_size": {"width_pt": common[0], "height_pt": common[1]}}
    ranges: list[dict] = []
    for page, size in enumerate(sizes, start=1):
        if size == common:
            continue
        if ranges and ranges[-1]["last"] == page - 1 and (
                ranges[-1]["width_pt"], ranges[-1]["height_pt"]) == size:
            ranges[-1]["last"] = page
        else:
            ranges.append({"first": page, "last": page, "width_pt": size[0],
                           "height_pt": size[1]})
    if ranges:
        out["page_sizes"] = ranges
    return out


# ---------------------------------------------------------------------------
# the edition check
# ---------------------------------------------------------------------------

def source_id_check(registry: Registry, source_id: str | None, sha: str) -> None:
    """Refuse a ``--source-id`` that names a registry entry pinned to another sha.

    A document that names a registered edition under a sha the registry does
    not give it is a stamp mismatch (CLAUDE.md section 4), whatever the
    edition check says about the PDF's own sha.
    """
    if not source_id:
        return
    entry = registry.get(source_id)
    if entry is None or not entry.sha256 or entry.sha256 == sha:
        return
    raise SourceError(
        f"--source-id {source_id} names the registry's {entry.edition or 'edition'} with "
        f"sha {entry.sha256[:12]}, but this PDF has sha {sha[:12]}",
        hint="pass a --source-id of this PDF's own, or register this edition first with "
             "`cf sources add` (a registered id is never stamped on another file)")


def edition_check(registry: Registry, sha: str, override: str | None) -> dict:
    """What the registry knows about this sha, and whether S0 may proceed.

    A ``draft`` or ``superseded`` edition is refused unless ``override`` gives
    a reason; the returned record carries that reason into the receipt.
    """
    entry = registry.by_sha(sha)
    if entry is None:
        return {"known": False, "status": "unknown", "source_id": None, "override": override,
                "note": "the registry does not know this sha; the maintainer's S1 sign-off "
                        "must name the edition"}
    current = [e.id for e in registry.same_work(entry.id)
               if e.edition_status == EDITION_CURRENT]
    result = {"known": True, "status": entry.edition_status, "source_id": entry.id,
              "confirmed": entry.confirmed, "current_editions": current,
              "override": override}
    if entry.edition_status in (EDITION_DRAFT, EDITION_SUPERSEDED) and not override:
        instead = f"; the registry's current edition of this work is {', '.join(current)}" \
            if current else ""
        raise SourceError(
            f"this PDF is the {entry.edition_status} edition {entry.id} "
            f"({entry.edition}){instead}",
            hint="use the current edition's PDF, or pass --override-edition \"<reason>\" to "
                 "ingest this one anyway (the reason is recorded in the S0 receipt)")
    return result


# ---------------------------------------------------------------------------
# the generated companion repository
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    """One git command in the generated repository, through ``cflib.gitsafe``.

    The repository is this pipeline's own, but the rule of the tree holds: no
    git runs with a configuration the answering repository chose.
    """
    proc = gitsafe.run(repo, [*_GIT_IDENTITY, *args])
    if proc.returncode != 0:
        raise CfError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _iso_z(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def init_companion_repo(repo: Path, *, name: str, pipeline_id: str,
                        source_id: str) -> tuple[str, str]:
    """``(commit, committed_at)`` of the generated repository, created on first use.

    A second ingest reuses the repository and its head, so the companion block
    of doc v0 does not move when nothing changed.
    """
    if shutil.which("git") is None:
        raise CfError("git not found on PATH", hint="S0 creates the generated repository "
                                                    "with git")
    if (repo / ".git").exists():
        commit = _git(repo, "rev-parse", "HEAD")
    else:
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q")
        (repo / "README.md").write_text(
            f"# {name}\n\nThe companion generated by pipeline `{pipeline_id}` for source "
            f"`{source_id}`.\nEmpty until the statement stage (S5) writes its first files.\n",
            encoding="utf-8")
        (repo / ".gitignore").write_text(
            "# the source stays in the workspace, never in this repository\n*.pdf\n",
            encoding="utf-8")
        _git(repo, "add", "README.md", ".gitignore")
        _git(repo, "commit", "-q", "-m", f"Initialise {name} (pipeline {pipeline_id}, S0)")
        commit = _git(repo, "rev-parse", "HEAD")
    committed = int(_git(repo, "log", "-1", "--format=%ct", commit))
    return commit, _iso_z(committed)


def repo_tracks_pdf(repo: Path) -> list[str]:
    """Every ``*.pdf`` path the generated repository tracks (the no-PDF-in-git gate)."""
    listed = _git(repo, "ls-files")
    return [p for p in listed.splitlines() if p.lower().endswith(".pdf")]


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------

def _complete_kinds(pdf: Path, out_dir: Path, sid: str, meta: dict, **prepare_kwargs
                    ) -> tuple[dict, dict]:
    """Add the slot-filling kinds (``cflib.inference.slot_filling_kinds``) and re-prepare.

    The kinds stay inferred: the frequent heading words come from
    ``infer_kinds`` (``meta.kind_counts``), then the rare words whose every
    number fills a slot the frequent kinds' numbering leaves open.  The record
    is derived the same way on every run, so a second ingest writes the same
    document.  Nothing is typed in.
    """
    base = list(meta.get("kind_counts") or meta.get("kinds") or [])
    table = prepare_source.load_items(str(out_dir))
    kind_numbers: dict[str, list[str]] = {}
    for row in table.get("items") or []:
        if row["kind"] in base:
            kind_numbers.setdefault(row["kind"], []).append(row["number"])
    text = Path(out_dir, "text.txt").read_text(encoding="utf-8", errors="ignore")
    numbering = meta.get("numbering") or {}
    extra = inference.slot_filling_kinds(
        text, kind_numbers, min_segments=int(numbering.get("min_segments") or 2))
    kinds = base + [k for k in extra if k not in base]
    if kinds != list(meta.get("kinds") or []):
        meta = prepare_source.prepare(str(pdf), str(out_dir), source_id=sid, kinds=kinds,
                                      **prepare_kwargs)
    record = {"kinds": list(meta.get("kinds") or kinds),
              "method": "inferred+slot_filling" if extra else "inferred",
              "counts": dict(meta.get("kind_counts") or {}), "slot_filling": extra}
    return meta, record


def dissenting_pages(pages: list[str], offset: int) -> list[dict]:
    """The physical pages whose running head votes for another offset than ``offset``.

    The same reading ``cflib.inference.infer_offset`` votes with; a page that
    shows no running head is not listed (it did not vote).
    """
    out = []
    total = len(pages)
    for physical, text in enumerate(pages, start=1):
        printed = inference.running_head_candidates(text, total)
        if printed and offset not in {physical - n for n in printed}:
            out.append({"page": physical, "printed": sorted(printed)})
    return out


def _no_urls(value: str) -> str:
    return _URL.sub("", value).strip()


def _pdf_source(source_id: str, sha: str, size: int, info: dict, meta: dict,
                entry: RegistryEntry | None, pdf: Path, version: str, licence: str,
                offset_method: str, check: dict, kinds_record: dict,
                dissent: list[dict]) -> dict:
    title = (entry.title if entry and entry.title else
             info["info"].get("Title") or pdf.stem)
    src: dict[str, Any] = {
        "id": source_id, "kind": "pdf", "title": title, "sha256": sha, "bytes": size,
        "physical_page_count": info["pages"], "printed_page_offset": int(meta["offset"]),
        "text_layer": {"extractor": "pdftotext", "version": version,
                       **document.TEXT_LAYER_MATCH},
        "role": "audited",
        "x_offset": {"confidence": float(meta.get("offset_confidence") or 0.0),
                     "method": offset_method,
                     "voting_pages": int(meta.get("offset_voting_pages") or 0),
                     "dissenting_pages": [d["page"] for d in dissent]},
        "x_edition_check": {k: v for k, v in check.items() if v is not None},
        "x_kinds": kinds_record,
    }
    src.update(page_size_fields(info["sizes"]))
    if entry and entry.edition:
        src["edition"] = entry.edition
    if entry and entry.download_url:
        src["url"] = entry.download_url
    note = licence or (entry.licence.get("note") if entry else "") or ""
    if note:
        src["x_licence"] = _no_urls(str(note))
    facts = {k: _no_urls(v) for k, v in info["info"].items()
             if k in ("Title", "Author", "Creator", "Producer", "CreationDate", "ModDate")
             and v and _no_urls(v)}
    if facts:
        src["x_pdf_info"] = facts
    return src


def _errata_source(source_id: str, title: str, meta: dict) -> dict:
    fetched = str(meta.get("fetched_at") or "")
    src = {"id": f"{source_id}-errata", "kind": "web", "title": f"Errata: {title}",
           "sha256": meta["sha256"], "url": meta.get("url_effective") or meta["url"],
           "role": "reference",
           "x_errata": {"entries": meta.get("entries"),
                        "suspect_entries": meta.get("suspect_entries"),
                        "renderer_version": meta.get("renderer_version")}}
    if meta.get("url_effective") and meta.get("url_effective") != meta.get("url"):
        src["alternate_urls"] = [meta["url"]]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", fetched):
        src["retrieved_at"] = fetched
    return src


def ingest(workspace, *, pdf: str | Path, scope_spec: str, pipeline_id: str,
           errata_url: str | None = None, no_errata: bool = False, licence: str = "",
           override_edition: str | None = None, offset: int | None = None,
           source_id: str | None = None, companion: str | None = None,
           fetch=fetch_errata.fetch) -> dict:
    """Run S0; returns a summary with the written paths and the gate result.

    ``fetch`` is the errata fetcher (a test passes a stand-in so no network is
    touched).
    """
    started = time.monotonic()
    paths = workspace.paths
    pdf = Path(pdf).expanduser().resolve()
    if not pdf.is_file():
        raise SourceError(f"no such PDF: {pdf}")
    try:
        pdf.relative_to(Path(workspace.root).resolve() / "pipelines")
        raise SourceError(f"{pdf} is inside the pipelines directory, which the workspace "
                          f"commits", hint="keep the PDF outside the workspace's tracked tree")
    except ValueError:
        pass
    scope = parse_scope(scope_spec)
    sha = sha256_file(pdf)
    size = pdf.stat().st_size
    registry = Registry.load(workspace)
    source_id_check(registry, source_id, sha)
    check = edition_check(registry, sha, override_edition)
    entry = registry.get(check["source_id"]) if check["known"] else None

    info = pdfinfo(pdf)
    if not info["pages"]:
        raise SourceError(f"pdfinfo reports no pages in {pdf}")
    version = tool_version("pdftotext")
    title = entry.title if entry else (info["info"].get("Title") or pdf.stem)
    sid = source_id or (entry.id if entry else source_id_for(title, sha256=sha))
    name = companion or pipeline_id

    # the pipeline directory: an existing pipeline.yaml must describe this source
    pyaml = paths.pipeline_yaml(pipeline_id)
    if pyaml.exists():
        settings = pipeline_yaml.load_for(paths, pipeline_id)
        if settings["pipeline"]["source_id"] != sid:
            raise CfError(f"pipeline {pipeline_id!r} was started for source "
                          f"{settings['pipeline']['source_id']!r}, not {sid!r}",
                          hint="start a new pipeline id for a different PDF")
        name = settings["pipeline"]["companion"]
        if settings.get("scope") != scope:
            raise CfError(f"pipeline {pipeline_id!r} has scope {settings.get('scope')}, "
                          f"not {scope}", hint="the scope is fixed when a pipeline starts; "
                                               "start a new pipeline id for another scope")
    else:
        settings = {"version": pipeline_yaml.VERSION,
                    "pipeline": {"id": pipeline_id, "source_id": sid, "companion": name},
                    "exercise_policy": None, "scope": scope,
                    "consent": {"include_source_text": False}, "fleets": {},
                    "notes": ""}
        problems = pipeline_yaml.validate(settings, expected_id=pipeline_id)
        if problems:
            raise CfError("cannot start the pipeline: " + "; ".join(problems))

    # source preparation: text.txt, pages/, items.json, meta.json (workspace-local)
    out_dir = paths.source_dir(sid)
    declared = entry.offset if (entry and offset is None) else None
    try:
        meta = prepare_source.prepare(str(pdf), str(out_dir), source_id=sid, offset=offset,
                                      declared=declared,
                                      edition=(entry.edition if entry else ""))
    except (RuntimeError, ValueError) as exc:
        raise SourceError(f"source preparation failed: {exc}") from None
    meta, kinds_record = _complete_kinds(pdf, out_dir, sid, meta, offset=offset,
                                         declared=declared,
                                         edition=(entry.edition if entry else ""))
    offset_method = "manual" if offset is not None else str(meta.get("offset_method"))
    confidence = float(meta.get("offset_confidence") or 0.0)
    if offset is None and confidence < inference.MIN_OFFSET_CONFIDENCE:
        raise SourceError(
            f"the printed page offset is {meta.get('offset')} at confidence {confidence:.3f}, "
            f"below {inference.MIN_OFFSET_CONFIDENCE}",
            hint="read the printed page number off two pages of the PDF and pass --offset N "
                 "(physical page = printed page + N); it is stamped offset_method: manual")
    dissent = dissenting_pages(
        prepare_source.split_pages(paths.source_text(sid).read_text(encoding="utf-8",
                                                                     errors="ignore")),
        int(meta["offset"]))

    # errata, raw, when a URL is known
    url = None if no_errata else (errata_url or (entry.errata_url if entry else None))
    errata_meta = None
    if url:
        try:
            errata_meta = fetch(url, str(out_dir), source_id=sid, source_sha=sha,
                                offset=int(meta["offset"]),
                                provenance="manual" if errata_url else "registry")
        except (RuntimeError, OSError) as exc:
            raise SourceError(f"the errata fetch failed: {exc}",
                              hint="retry, or pass --no-errata to record that no errata "
                                   "were fetched") from None

    # the generated repository, committed once
    repo = paths.pipeline_repo(pipeline_id)
    commit, committed_at = init_companion_repo(repo, name=name, pipeline_id=pipeline_id,
                                               source_id=sid)
    tracked_pdfs = repo_tracks_pdf(repo)

    sources = [_pdf_source(sid, sha, size, info, meta, entry, pdf, version, licence,
                           offset_method, check, kinds_record, dissent)]
    inputs = [{"source": sid}]
    if errata_meta is not None:
        sources.append(_errata_source(sid, title, errata_meta))
        inputs.append({"source": sources[-1]["id"]})
    provenance = [{"id": PROV_INGEST, "actor": "tool", "method": "pipeline",
                   "confirmed_by_human": False, "tool": {"name": "cf ingest", "version": "1"},
                   "inputs": inputs,
                   "note": "S0 ingest and pin: the PDF's facts, the page offset from the "
                           "running heads, the registry edition check"}]
    doc = document.new_document(
        {"name": name, "repo": f"local/{name}", "commit": commit,
         "generated_at": committed_at, "generator": GENERATOR},
        sources, provenance)

    if not pyaml.exists():
        write_yaml(pyaml, settings)
    settings = pipeline_yaml.load_for(paths, pipeline_id)
    doc_path = paths.pipeline_doc(pipeline_id, 0)
    doc_sha = document.write_checkpoint(doc_path, doc)

    checks = {
        "offset_confidence": {"ok": offset is not None
                              or confidence >= inference.MIN_OFFSET_CONFIDENCE,
                              "value": confidence, "min": inference.MIN_OFFSET_CONFIDENCE,
                              "offset": int(meta["offset"]), "method": offset_method,
                              "dissenting_pages": dissent},
        "edition": {"ok": True, **{k: v for k, v in check.items() if v is not None}},
        "full_sha": {"ok": len(sha) == 64, "sha256": sha},
        "errata": ({"ok": True, "fetched": True, "http_status": errata_meta.get("http_status"),
                    "entries": errata_meta.get("entries"),
                    "suspect_entries": errata_meta.get("suspect_entries"),
                    "raw_sha256": errata_meta.get("sha256")}
                   if errata_meta is not None else
                   {"ok": True, "fetched": False, "note": "none found" if not no_errata
                    else "skipped with --no-errata"}),
        "validate_canonical": {"ok": True},
        "no_pdf_in_git": {"ok": not tracked_pdfs, "tracked": tracked_pdfs},
    }
    spot_check = {"gate": "spot_check", "status": "pending", "method": "manual",
                  "note": "the image spot check of 10 random pages and one planted "
                          "shifted page (proposal section 2.1) is not built; the "
                          "maintainer checks the offset by eye and records it"}
    if dissent:
        spot_check["look_at_pages"] = [d["page"] for d in dissent]
    manual = [spot_check]
    if not check["known"]:
        manual.append({"gate": "edition", "status": "pending", "method": "manual",
                       "sha256": sha, "source_id": sid,
                       "note": "the registry does not know this sha: the maintainer names "
                               "the edition (and registers it with `cf sources add`) before "
                               "the pipeline's output is trusted"})
    gate = {"ok": all(c["ok"] for c in checks.values()), "checks": checks, "manual": manual}
    receipt = {
        "input_doc_sha256": sha,
        "output_doc_sha256": doc_sha,
        "pipeline_yaml_sha256": sha256_file(pyaml),
        "fleet_runs": [], "prompt_shas": {}, "model_ids": [],
        "gate_result": gate,
        "consent": dict(settings.get("consent") or {"include_source_text": False}),
        "cost_usd": 0, "tokens": {},
        "wall_clock_s": round(time.monotonic() - started, 3),
    }
    receipt_path = stage_receipt.write(paths, pipeline_id, STAGE, receipt)
    if not gate["ok"]:
        failed = [k for k, v in checks.items() if not v["ok"]]
        raise CfError(f"S0 gate failed: {', '.join(failed)}",
                      hint=f"the receipt {receipt_path} names what failed")
    return {"pipeline_id": pipeline_id, "source_id": sid, "sha256": sha,
            "pages": info["pages"], "offset": int(meta["offset"]),
            "offset_confidence": confidence, "offset_method": offset_method,
            "items_found": int(meta.get("items") or 0), "edition": check,
            "errata": errata_meta, "doc": doc_path, "doc_sha256": doc_sha,
            "receipt": receipt_path, "repo": repo, "commit": commit, "gate": gate}
