"""Gold: G1 resolution and anchoring, the frozen labeler, G4, the planted index, the manifest.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.1 and 8.4
in the formats references/locate-bench.md sections 4 to 7 fix.

**G1** (maintainer-curated positives) starts mechanical: each curated entry --
a Mathlib ``1000.yaml`` QID, a ``100.yaml`` title, an ``@[wikidata]`` tag in the
universe's tag table -- names declarations; each is resolved at the universe's
commit through the rename table (else dropped with the reason), and the accept
set is widened with the ``to_additive`` twins and renames that exist at the pin.
Anchoring a resolved entry to a passage, and adjudicating its relation once,
is the frozen labeler's job: it proposes a quote, and the transcriber keeps the
anchor only when the quote matches the layer **exactly once**
(``gate/locate_text.find_quote``); an entry it cannot anchor scores at article
level only.

**The labeler** is ``workflows/locate-label.workflow.js``, run in a session
(a Workflow cannot be launched from this process).  :func:`emit_args` builds its
args from the bench, the sourceset and the universe -- never from a run, which
is what makes G3 blind -- and stamps the workflow file's sha; :func:`transcribe`
refuses a result whose labeler is not the one emitted.  Its four modes are
``cflib.vocab.BENCH_LABEL_MODES``.

**Label files** (``gold/g1.jsonl`` ...) carry the bench's canary string in every
row, so a transcript that quotes a label is detectable (section 8.4).  The
manifest :func:`freeze` writes hashes every one of them; a gold change is a new
manifest version, never an edit.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from cflib.paths import skill_root
from cflib.yamlio import load_yaml

from corpus import query
from locate.candidates import normalise_title

from . import inputs
from .layout import (ABLATION_SCHEMA, CALIBRATION_SCHEMA, G1_RESOLVED_SCHEMA, GOLD_SCHEMA,
                     LABEL_ARGS_SCHEMA, LABEL_FILES, LABEL_RESULT_SCHEMA, SPEND_SCHEMA,
                     BenchLayout, canonical, now, read_jsonl_rows, sha_of,
                     write_canonical_jsonl)

#: the labeler, frozen by its file's sha (its prompts are inline in it)
LABELER_WORKFLOW = "workflows/locate-label.workflow.js"
#: bumped whenever the labeler's contract (args or result shape) changes
LABELER_VERSION = "locate-label/1"
#: G4: at most this many maintainer spot checks per release (section 8.1)
G4_MAX_NEGATIVES = 30
G4_MAX_POSITIVES = 20
#: how many rename hops a declaration may take before resolution gives up
MAX_RENAME_HOPS = 10
#: an excerpt handed to the labeler is cut at this many characters
MAX_EXCERPT_CHARS = 4000
#: the relations that make a gold declaration count towards ``formalized``
COUNTING = frozenset(vocab.LOCATE_RELATIONS - {vocab.RELATED_MATCH})

_QID = re.compile(r"Q[1-9][0-9]*\Z")


def new_canary() -> str:
    """A string no transcript contains by accident (section 8.4)."""
    return "cf-bench-canary-" + secrets.token_hex(16)


def workflow_path() -> Path:
    return skill_root() / LABELER_WORKFLOW


def workflow_sha256() -> str:
    path = workflow_path()
    if not path.is_file():
        raise CfError(f"the labeler {path} is missing")
    return inputs.sha256_file(path)


def labeler_commit() -> str:
    """The checkout the labeler's args are emitted from (``locate.export.skill_commit``)."""
    from locate.export import skill_commit

    return skill_commit() or "unrecorded"


# --------------------------------------------------------------------------
# curated lists and the rename table
# --------------------------------------------------------------------------

def _yaml_or_json(path: Path) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    return load_yaml(text, where=str(path))


def _decls_of(entry: dict) -> list[str]:
    """An entry's declaration names, under the keys L2's reader takes (``locate.candidates``)."""
    out = []
    for key in ("decl", "declaration"):
        if isinstance(entry.get(key), str):
            out.append(entry[key])
    for key in ("decls", "declarations"):
        for name in entry.get(key) or []:
            if isinstance(name, str):
                out.append(name)
    return list(dict.fromkeys(n.strip() for n in out if n and n.strip()))


def read_curated_1000(path: Path) -> list[dict]:
    """Mathlib's ``$MATHLIB/docs/1000.yaml`` shape: QID -> ``{title, decl | decls, ...}``."""
    data = _yaml_or_json(path)
    if not isinstance(data, dict):
        raise CfError(f"{path} is not a mapping keyed by Wikidata QID")
    out = []
    for key in sorted(data, key=str):
        entry = data[key] if isinstance(data[key], dict) else {}
        qid = str(key).strip()
        if not _QID.match(qid):
            raise CfError(f"{path}: key {key!r} is not a Wikidata QID")
        out.append({"key": qid, "qid": qid, "title": entry.get("title"),
                    "declarations": _decls_of(entry)})
    return out


def read_curated_100(path: Path) -> list[dict]:
    """Mathlib's ``$MATHLIB/docs/100.yaml`` shape: number -> ``{title, decl | decls, ...}``; no QID."""
    data = _yaml_or_json(path)
    if not isinstance(data, dict):
        raise CfError(f"{path} is not a mapping keyed by theorem number")
    out = []
    for key in sorted(data, key=lambda k: (len(str(k)), str(k))):
        entry = data[key] if isinstance(data[key], dict) else {}
        qid = str(entry.get("wikidata") or "").strip() or None
        out.append({"key": str(key), "qid": qid if qid and _QID.match(qid) else None,
                    "title": entry.get("title"), "declarations": _decls_of(entry)})
    return out


def read_rename_table(path: Path | None) -> dict[str, str]:
    """``{old name: new name}`` from a JSON/YAML mapping or ``old new`` / ``old -> new`` lines."""
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped[0] == "{" or (":" in stripped.splitlines()[0] and "->" not in stripped.splitlines()[0]):
        data = load_yaml(text, where=str(path))
        if not isinstance(data, dict):
            raise CfError(f"{path} is not a mapping from old to new names")
        return {str(k).strip(): str(v).strip() for k, v in data.items() if v}
    out = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p for p in re.split(r"\s*->\s*|\s+", line) if p]
        if len(parts) != 2:
            raise CfError(f"{path}:{number}: a rename line is `old new` or `old -> new`")
        out[parts[0]] = parts[1]
    return out


def normalized_title(title: str | None) -> str:
    """The title key both L2 (``cf locate candidates``) and G1 join a 100.yaml entry on."""
    return normalise_title(str(title or ""))


# --------------------------------------------------------------------------
# to_additive twins
# --------------------------------------------------------------------------

#: whole-word substitutions ``to_additive`` makes, both ways; a twin is kept only
#: when it exists at the pin, so a wrong guess costs nothing
_TOKEN_TWINS = (("mul", "add"), ("one", "zero"), ("prod", "sum"), ("inv", "neg"),
                ("div", "sub"), ("pow", "nsmul"), ("zpow", "zsmul"), ("smul", "vadd"))
_CAMEL_TWINS = (("Mul", "Add"), ("One", "Zero"), ("Prod", "Sum"), ("Inv", "Neg"),
                ("Div", "Sub"), ("Monoid", "AddMonoid"), ("Group", "AddGroup"),
                ("Semigroup", "AddSemigroup"), ("Submonoid", "AddSubmonoid"),
                ("Subgroup", "AddSubgroup"))
_SPLIT = re.compile(r"([._])")


def twins(name: str) -> list[str]:
    """Names ``to_additive`` would give ``name`` or take it from: one substitution at a time."""
    parts = _SPLIT.split(name)
    found: set[str] = set()
    for i, part in enumerate(parts):
        if part in (".", "_"):
            continue
        swaps: set[str] = set()
        for a, b in _TOKEN_TWINS:
            if part == a:
                swaps.add(b)
            elif part == b:
                swaps.add(a)
        for a, b in _CAMEL_TWINS:
            for x, y in ((a, b), (b, a)):
                if part.startswith(x):
                    swaps.add(y + part[len(x):])
        for swapped in swaps:
            if swapped != part:
                found.add("".join(parts[:i] + [swapped] + parts[i + 1:]))
    found.discard(name)
    return sorted(found)


# --------------------------------------------------------------------------
# G1 resolution
# --------------------------------------------------------------------------

def _resolve(conn: sqlite3.Connection, library: str, commit: str, name: str,
             renames: dict[str, str]) -> tuple[str | None, list[str]]:
    """``(the name at the pin, the rename chain)``; None when neither it nor a rename exists."""
    chain = [name]
    current = name
    for _ in range(MAX_RENAME_HOPS + 1):
        if query.exists(conn, library, commit, current):
            return current, chain
        nxt = renames.get(current)
        if not nxt or nxt in chain:
            return None, chain
        chain.append(nxt)
        current = nxt
    return None, chain


def resolve_g1(articles: list[dict], conn: sqlite3.Connection, *, library: str, commit: str,
               lists: list[tuple[str, list[dict]]], renames: dict[str, str],
               tag_libraries: Iterable[str]) -> dict:
    """Every curated claim on a drawn article, resolved at the pin, with its accept set."""
    by_qid: dict[str, list[dict]] = {}
    by_title: dict[str, list[dict]] = {}
    for a in articles:
        if a.get("wikidata"):
            by_qid.setdefault(a["wikidata"], []).append(a)
        by_title.setdefault(normalized_title(a.get("title")), []).append(a)
    claims: list[dict] = []
    unmatched: dict[str, int] = {}
    no_declarations: dict[str, int] = {}
    for via, rows in lists:
        for row in rows:
            if not row["declarations"]:
                no_declarations[via] = no_declarations.get(via, 0) + 1
                continue
            # L2's join (locate.candidates.curated_hits): a 1000.yaml entry by its QID; a
            # 100.yaml entry by the QID it carries AND by the shared title key -- over the
            # article's title only, where L2 also reads aliases and bold terms
            matched = list(by_qid.get(row["qid"], [])) if row.get("qid") else []
            if via == "mathlib_100":
                matched += [a for a in by_title.get(normalized_title(row.get("title")), [])
                            if a not in matched]
            if not matched:
                unmatched[via] = unmatched.get(via, 0) + 1
                continue
            for article in matched:
                claims.append({"source_id": article["source_id"], "qid": article.get("wikidata"),
                               "title": article.get("title"), "via": via, "key": row["key"],
                               "declarations": [(library, d) for d in row["declarations"]],
                               "evidence": f"{via} {row['key']}"})
    libraries = sorted(set(tag_libraries) | {library})
    for article in articles:
        qid = article.get("wikidata")
        if not qid:
            continue
        for hit in inputs.tagged(conn, "wikidata", qid, libraries):
            claims.append({"source_id": article["source_id"], "qid": qid,
                           "title": article.get("title"), "via": "wikidata_tag",
                           "key": f"{hit['library']}:{hit['declaration']}",
                           "declarations": [(hit["library"], hit["declaration"])],
                           "evidence": f"@[wikidata {qid}] at {hit['path']}:{hit['line']}"})
    commits = {row["id"]: row["commit_sha"] for row in query.libraries(conn)}
    commits.setdefault(library, commit)
    resolved: list[dict] = []
    dropped_entries: list[dict] = []
    for claim in claims:
        accept: dict[tuple[str, str], str] = {}
        dropped: list[dict] = []
        for lib, name in claim["declarations"]:
            lib_commit = commits.get(lib, "")
            at, chain = _resolve(conn, lib, lib_commit, name, renames)
            if at is None:
                dropped.append({"library": lib, "declaration": name,
                                "reason": "not in the universe at its commit"
                                + (f" (rename chain {' -> '.join(chain)})" if len(chain) > 1 else "")})
                continue
            accept.setdefault((lib, at), "curated" if at == name else "renamed")
            # a deprecated alias that survives at the pin points at its new name, which
            # a correct worker cites: the index's own rename (corpus deprecated_to)
            row = inputs.declaration(conn, lib, lib_commit, at) or {}
            new_name = row.get("deprecated_to")
            if new_name and (lib, new_name) not in accept and query.exists(conn, lib, lib_commit,
                                                                          new_name):
                accept[(lib, new_name)] = "renamed"
            for twin in sorted({t for n in [at] + ([new_name] if new_name else [])
                                for t in twins(n)}):
                if (lib, twin) not in accept and query.exists(conn, lib, lib_commit, twin):
                    accept[(lib, twin)] = "to_additive"
        entry = {"source_id": claim["source_id"], "qid": claim["qid"], "title": claim["title"],
                 "via": [claim["via"]], "evidence": [claim["evidence"]],
                 "declarations": [{"library": lib, "declaration": n} for lib, n in claim["declarations"]],
                 "accept": [{"library": lib, "declaration": n, "commit": commits[lib], "why": why}
                            for (lib, n), why in sorted(accept.items())],
                 "dropped": dropped}
        if not accept:
            entry["reason"] = "no curated declaration exists at the pin"
            dropped_entries.append(entry)
        else:
            resolved.append(entry)
    merged = _merge_entries(resolved)
    return {"entries": merged, "dropped_entries": dropped_entries,
            "unmatched": dict(sorted(unmatched.items())),
            "listed_without_declarations": dict(sorted(no_declarations.items()))}


def _merge_entries(entries: list[dict]) -> list[dict]:
    """One entry per (article, statement): claims whose accept sets meet are merged."""
    out: list[dict] = []
    for entry in sorted(entries, key=lambda e: (e["source_id"], e["accept"][0]["declaration"])):
        keys = {(a["library"], a["declaration"]) for a in entry["accept"]}
        home = next((o for o in out if o["source_id"] == entry["source_id"]
                     and keys & {(a["library"], a["declaration"]) for a in o["accept"]}), None)
        if home is None:
            out.append(entry)
            continue
        have = {(a["library"], a["declaration"]) for a in home["accept"]}
        home["accept"] += [a for a in entry["accept"] if (a["library"], a["declaration"]) not in have]
        home["accept"].sort(key=lambda a: (a["library"], a["declaration"]))
        home["via"] = sorted(set(home["via"]) | set(entry["via"]))
        home["evidence"] = sorted(set(home["evidence"]) | set(entry["evidence"]))
        home["declarations"] += [d for d in entry["declarations"] if d not in home["declarations"]]
        home["dropped"] += entry["dropped"]
    for entry in out:
        entry["entry_id"] = "g1-" + hashlib.sha256(canonical(
            [entry["source_id"], [[a["library"], a["declaration"]] for a in entry["accept"]]]
        ).encode("utf-8")).hexdigest()[:12]
    return sorted(out, key=lambda e: e["entry_id"])


def gold_declarations(layout: BenchLayout) -> dict[str, set[str]]:
    """``{source id: {"library:declaration"}}`` of the G1 accept sets: what clusters articles.

    Only G1's accept sets join clusters.  A G3 or G4 label is drawn after the
    split and names whatever the hunters found -- one widely used lemma would
    otherwise fold the whole bench into one cluster -- so a gold declaration it
    shares with dev puts the passage in the seen-declaration stratum instead
    (section 8.3), which ``cf bench freeze`` computes over every tier.
    """
    out: dict[str, set[str]] = {}
    if layout.g1_resolved.is_file():
        for entry in read_json(layout.g1_resolved).get("entries") or []:
            out.setdefault(entry["source_id"], set()).update(
                f"{a['library']}:{a['declaration']}" for a in entry["accept"])
    return out


# --------------------------------------------------------------------------
# label files
# --------------------------------------------------------------------------

def write_labels(layout: BenchLayout, tier: str, rows: list[dict], canary: str, *,
                 after_freeze: bool = False) -> Path:
    """A tier's label file, every row carrying the canary, sorted by gold id.

    On a frozen bench a change to a frozen tier is refused unless the caller
    says it is the start of a new gold version (``after_freeze``); writing the
    same bytes again is not a change.
    """
    stamped = [dict(r, canary=canary, tier=tier) for r in sorted(rows, key=lambda r: r["gold_id"])]
    text = "".join(canonical(r) + "\n" for r in stamped)
    path = layout.label_file(tier)
    frozen = _frozen_sha(layout, tier)
    if frozen is not None and not after_freeze:
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != frozen:
            raise CfError(f"bench {layout.bench_id} is frozen and this would change its {tier} labels",
                          hint="gold is never edited: pass --supersede to start a new gold version, "
                               "then cf bench freeze --supersede")
    return write_canonical_jsonl(path, stamped)


def _frozen_sha(layout: BenchLayout, tier: str) -> str | None:
    manifests = layout.gold_manifests()
    if not manifests:
        return None
    doc = read_json(manifests[-1][1])
    return ((doc.get("tiers") or {}).get(tier) or {}).get("sha256")


def read_labels(layout: BenchLayout, tier: str, *, required: bool = True) -> list[dict]:
    path = layout.label_file(tier)
    if not path.is_file():
        if required:
            raise CfError(f"bench {layout.bench_id} has no {tier} labels at {path}")
        return []
    return read_jsonl_rows(path)


# --------------------------------------------------------------------------
# the labeler: emit
# --------------------------------------------------------------------------

def _source_block(paths, sourceset: str, article: dict) -> dict:
    sdir = inputs.source_dir(paths, sourceset, article["source_id"])
    return {"source_id": article["source_id"], "title": article.get("title"),
            "qid": article.get("wikidata"), "revision": article.get("revision"),
            "text": str(sdir / "text.txt"), "layer": str(sdir / "layer.txt")}


def _excerpt(layer: str, span: list[int]) -> str:
    text = layer[span[0]:span[1]]
    return text if len(text) <= MAX_EXCERPT_CHARS else text[:MAX_EXCERPT_CHARS] + " …"


def emit_args(mode: str, *, paths, layout: BenchLayout, bench: dict, articles: dict[str, dict],
              universe, conn: sqlite3.Connection | None, checkouts: dict[str, str],
              tools: dict, model: str, hunters: int, gx_articles: list[str] | None = None) -> dict:
    """The labeler's args for one mode.  Reads the bench, the sourceset and the universe only."""
    vocab.check(mode, vocab.BENCH_LABEL_MODES, "labeler mode")
    sourceset = bench["sourceset"]["id"]
    libs = inputs.libraries(universe)
    tasks: list[dict] = []
    used: set[str] = set()
    if mode in ("anchor", "calibrate"):
        if not layout.g1_resolved.is_file():
            raise CfError("no resolved G1 entries", hint="run cf bench anchor with the curated lists first")
        entries = read_json(layout.g1_resolved).get("entries") or []
        if mode == "anchor":
            for entry in entries:
                accept = []
                for a in entry["accept"]:
                    row = (inputs.declaration(conn, a["library"], a["commit"], a["declaration"])
                           if conn else None)
                    accept.append({"library": a["library"], "declaration": a["declaration"],
                                   "signature": (row or {}).get("signature"),
                                   "path": (row or {}).get("path"), "line": (row or {}).get("line")})
                tasks.append({"task_id": entry["entry_id"], "source_id": entry["source_id"],
                              "declarations": accept})
                used.add(entry["source_id"])
        else:
            anchored = {r["gold_id"]: r for r in read_labels(layout, "G1", required=False)
                        if r.get("level") == "passage"}
            if not anchored:
                raise CfError("no anchored G1 passage to calibrate the hunters on",
                              hint="transcribe the anchor labels first")
            for gid in sorted(anchored):
                row = anchored[gid]
                layer = inputs.read_layer(paths, sourceset, row["source_id"],
                                          articles[row["source_id"]].get("layer_sha256"))
                tasks.append({"task_id": gid, "source_id": row["source_id"], "span": row["span"],
                              "excerpt": _excerpt(layer, row["span"])})
                used.add(row["source_id"])
    elif mode == "label":
        spans = read_json(layout.spans_json).get("spans") or []
        if not spans:
            raise CfError("the bench has no G3 span sample", hint="cf bench build draws one")
        layers: dict[str, str] = {}
        for span in spans:
            sid = span["source_id"]
            if sid not in layers:
                layers[sid] = inputs.read_layer(paths, sourceset, sid, articles[sid].get("layer_sha256"))
            tasks.append({"task_id": span["span_id"], "source_id": sid, "span": span["span"],
                          "section": span.get("section"), "excerpt": _excerpt(layers[sid], span["span"])})
            used.add(sid)
    else:  # inventory
        for sid in gx_articles or []:
            if sid not in articles:
                raise CfError(f"{sid} is not an article of this bench")
            layer = inputs.read_layer(paths, sourceset, sid, articles[sid].get("layer_sha256"))
            finders = inputs.read_source_json(paths, sourceset, sid, "finders.json", required=False) or []
            tasks.append({"task_id": sid, "source_id": sid,
                          "finders": [{"finder": f.get("finder"), "span": f.get("span"),
                                       "excerpt": _excerpt(layer, f.get("span") or [0, 0])}
                                      for f in finders]})
            used.add(sid)
    args = {
        "schema": LABEL_ARGS_SCHEMA, "mode": mode, "bench": layout.bench_id,
        "labeler": {"version": LABELER_VERSION, "workflow_sha256": workflow_sha256(),
                    "skill_commit": labeler_commit()},
        "universe": {"id": bench["universe"]["id"], "sha256": bench["universe"]["sha256"],
                     "libraries": [{"id": lib, "repo": entry.get("repo"), "commit": entry["commit"],
                                    "checkout": checkouts.get(lib)}
                                   for lib, entry in sorted(libs.items())]},
        # never a run record: what the labeler is given is the bench, the sources and the
        # universe (section 8.1, G3 is blind)
        "tools": tools, "model": model, "hunters": hunters,
        "sources": [_source_block(paths, sourceset, articles[s]) for s in sorted(used)],
        "tasks": tasks,
    }
    args["digest"] = sha_of({k: v for k, v in args.items() if k != "digest"})
    return args


# --------------------------------------------------------------------------
# the labeler: transcribe
# --------------------------------------------------------------------------

def check_result(result: Any, args: dict) -> None:
    """Refuse a result that is not the emitted labeler's answer to these args."""
    if not isinstance(result, dict) or result.get("schema") != LABEL_RESULT_SCHEMA:
        raise CfError(f"not a {LABEL_RESULT_SCHEMA} result",
                      hint="pass the Workflow's return value for workflows/locate-label.workflow.js")
    for key in ("mode", "bench"):
        if result.get(key) != args.get(key):
            raise CfError(f"the result's {key} {result.get(key)!r} is not the args' {args.get(key)!r}")
    if result.get("args_digest") != args.get("digest"):
        raise CfError("the result answers other args than the ones emitted for this mode",
                      hint="re-emit with --emit-args and run the Workflow on that file")
    labeler = result.get("labeler") or {}
    if labeler.get("version") != args["labeler"]["version"]:
        raise CfError(f"the result came from labeler {labeler.get('version')!r}, not "
                      f"{args['labeler']['version']!r}")
    if workflow_sha256() != args["labeler"]["workflow_sha256"]:
        raise CfError("the labeler changed between emit and transcribe",
                      hint="the labeler is frozen by its sha: re-emit the args and re-run it")
    if not isinstance(result.get("labels"), list):
        raise CfError("the result has no labels list")


def _anchor_quote(layer: str, quote: Any) -> tuple[list[int] | None, str]:
    from gate import locate_text

    if not isinstance(quote, dict) or not str(quote.get("exact") or "").strip():
        return None, "the labeler gave no quote"
    hits = locate_text.find_quote(layer, quote.get("exact"), quote.get("prefix") or "",
                                  quote.get("suffix") or "")
    if len(hits) != 1:
        return None, f"the quote matches the layer {len(hits)} times, not exactly once"
    return [hits[0], hits[0] + len(quote["exact"])], ""


def _gold_status(passage_kind: str, declarations: list[dict]) -> tuple[str | None, bool]:
    """``(derived status, related)`` of a gold passage from its re-checked declarations."""
    kind = passage_kind if passage_kind in vocab.PASSAGE_KINDS else vocab.PASSAGE_OTHER
    counting = [d for d in declarations if d.get("relation") in COUNTING]
    related = any(d.get("relation") == vocab.RELATED_MATCH for d in declarations)
    if not counting:
        return vocab.STATUS_NOT_FORMALIZED, related
    return vocab.derive_status(kind, counting, absent=False), related


def _recheck(conn: sqlite3.Connection, libs: dict[str, dict], declarations: list[dict],
             ) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for d in declarations or []:
        lib, name = str(d.get("library") or ""), str(d.get("declaration") or "")
        relation = d.get("relation")
        if lib not in libs:
            dropped.append({**d, "reason": "library outside the universe"})
            continue
        if relation not in vocab.LOCATE_RELATIONS:
            dropped.append({**d, "reason": f"relation {relation!r} is not a locate relation"})
            continue
        row = inputs.declaration(conn, lib, libs[lib]["commit"], name)
        if not row:
            dropped.append({**d, "reason": "not in the universe at its commit (re-checked at the pin)"})
            continue
        kept.append({"library": lib, "declaration": name, "commit": libs[lib]["commit"],
                     "relation": relation, "proof_status": row["proof_status"],
                     "statement": bool(row["statement"]), "evidence": d.get("evidence") or ""})
    return kept, dropped


def _merged(layout: BenchLayout, tier: str, rows: list[dict], *, keep: set[str]) -> list[dict]:
    """This transcription's rows over the tier's earlier ones, by gold id.

    A resumed or partial labeler run answers some tasks; the rows it does not
    answer keep their earlier labels, as long as their task still exists.
    """
    fresh = {r["gold_id"] for r in rows}
    old = [{k: v for k, v in r.items() if k not in ("canary", "tier")}
           for r in read_labels(layout, tier, required=False)
           if r["gold_id"] not in fresh and r["gold_id"] in keep]
    return old + rows


def transcribe(result: dict, args: dict, *, paths, layout: BenchLayout, bench: dict,
               articles: dict[str, dict], universe, conn: sqlite3.Connection,
               signed_by: str | None = None, after_freeze: bool = False) -> dict:
    """Turn a checked labeler result into label rows; returns a summary."""
    check_result(result, args)
    mode = args["mode"]
    sourceset = bench["sourceset"]["id"]
    libs = inputs.libraries(universe)
    tasks = {t["task_id"]: t for t in args["tasks"]}
    labels = [l for l in result["labels"] if isinstance(l, dict)]
    unknown = sorted({str(l.get("task_id")) for l in labels} - set(tasks))
    if unknown:
        raise CfError(f"the result labels {len(unknown)} task(s) the args never asked for: "
                      + ", ".join(unknown[:5]))
    by_task = {l["task_id"]: l for l in labels}
    unlabeled = sorted(set(tasks) - set(by_task))
    layers: dict[str, str] = {}

    def layer_of(sid: str) -> str:
        if sid not in layers:
            layers[sid] = inputs.read_layer(paths, sourceset, sid, articles[sid].get("layer_sha256"))
        return layers[sid]

    canary = bench["canary"]
    summary: dict[str, Any] = {"mode": mode, "labels": len(by_task), "unlabeled": unlabeled}
    if mode == "anchor":
        entries = {e["entry_id"]: e for e in read_json(layout.g1_resolved).get("entries") or []}
        rows = []
        for task_id in sorted(tasks):
            entry = entries.get(task_id)
            if entry is None:
                raise CfError(f"{task_id} is no longer a resolved G1 entry; re-emit the args")
            label = by_task.get(task_id) or {}
            span, why = _anchor_quote(layer_of(entry["source_id"]), label.get("quote"))
            relation = label.get("relation")
            if relation not in vocab.LOCATE_RELATIONS:
                relation = None
            kind = label.get("passage_kind") if label.get("passage_kind") in vocab.PASSAGE_KINDS \
                else vocab.PASSAGE_OTHER
            declarations = []
            for a in entry["accept"]:
                row = inputs.declaration(conn, a["library"], a["commit"], a["declaration"])
                if row and relation:
                    declarations.append({"library": a["library"], "declaration": a["declaration"],
                                         "commit": a["commit"], "relation": relation,
                                         "proof_status": row["proof_status"],
                                         "statement": bool(row["statement"]), "why": a["why"]})
            status, related = _gold_status(kind, declarations) if relation else (None, False)
            rows.append({"gold_id": task_id, "source_id": entry["source_id"], "qid": entry["qid"],
                         "via": entry["via"], "level": "passage" if span else "article",
                         "span": span, "quote": label.get("quote") if span else None,
                         "anchor_problem": why or None, "passage_kind": kind,
                         "relation": relation, "status": status, "related": related,
                         "scope": vocab.SCOPE_FORMAL_MATH, "declarations": declarations,
                         "accept": [{"library": a["library"], "declaration": a["declaration"]}
                                    for a in entry["accept"]],
                         "evidence": label.get("evidence") or ""})
        write_labels(layout, "G1", _merged(layout, "G1", rows, keep=set(entries)), canary,
                     after_freeze=after_freeze)
        summary.update(anchored=sum(1 for r in rows if r["level"] == "passage"),
                       article_level=sum(1 for r in rows if r["level"] == "article"))
    elif mode in ("label", "calibrate"):
        rows, found = [], 0
        g1 = {r["gold_id"]: r for r in read_labels(layout, "G1", required=False)}
        spans_by_id = {s["span_id"]: s for s in read_json(layout.spans_json).get("spans") or []}
        for task_id in sorted(by_task):
            task, label = tasks[task_id], by_task[task_id]
            if mode == "calibrate" and task_id not in g1:
                raise CfError(f"{task_id} is no longer an anchored G1 passage; re-emit the args")
            if mode == "label" and task_id not in spans_by_id:
                raise CfError(f"{task_id} is no longer a span of the G3 sample; re-emit the args")
            scope = label.get("scope") if label.get("scope") in vocab.SCOPES else None
            kind = label.get("passage_kind") if label.get("passage_kind") in vocab.PASSAGE_KINDS \
                else vocab.PASSAGE_OTHER
            kept, dropped = _recheck(conn, libs, label.get("declarations") or [])
            if mode == "calibrate":
                accept = {(a["library"], a["declaration"]) for a in g1[task_id]["accept"]}
                hit = any((d["library"], d["declaration"]) in accept for d in kept)
                found += hit
                rows.append({"gold_id": task_id, "found": hit,
                             "declarations": [{"library": d["library"], "declaration": d["declaration"]}
                                              for d in kept]})
                continue
            status, related = (_gold_status(kind, kept) if scope == vocab.SCOPE_FORMAL_MATH
                               else (None, False))
            span_row = spans_by_id[task_id]
            rows.append({"gold_id": task_id, "source_id": task["source_id"], "span": task["span"],
                         "section": task.get("section"), "scope": scope, "passage_kind": kind,
                         "status": status, "related": related, "declarations": kept,
                         "dropped": dropped, "pi_span": span_row.get("pi_span"),
                         "pi_article": span_row.get("pi_article"),
                         "evidence": label.get("evidence") or "",
                         "hunters": label.get("hunters") or []})
        if mode == "calibrate":
            total = len(rows)
            doc = {"schema": CALIBRATION_SCHEMA, "bench": layout.bench_id, "found": found,
                   "total": total, "h": (found / total) if total else None,
                   "tag_blind": True, "rows": sorted(rows, key=lambda r: r["gold_id"]),
                   "labeler": args["labeler"], "at": now()}
            write_json(layout.calibration_json, doc, sort_keys=True)
            summary.update(found=found, total=total, h=doc["h"])
        else:
            write_labels(layout, "G3", _merged(layout, "G3", rows, keep=set(spans_by_id)), canary,
                         after_freeze=after_freeze)
            summary.update(formal_math=sum(1 for r in rows if r["scope"] == vocab.SCOPE_FORMAL_MATH),
                           formalized=sum(1 for r in rows if r["status"] == vocab.STATUS_FORMALIZED))
    else:  # inventory
        if not signed_by:
            raise CfError("a GX inventory is signed by the maintainer",
                          hint="pass --signed-by <name> with --transcribe")
        rows, refused = [], []
        for task_id in sorted(by_task):
            sid = tasks[task_id]["source_id"]
            for number, passage in enumerate(by_task[task_id].get("passages") or []):
                span, why = _anchor_quote(layer_of(sid), (passage or {}).get("quote"))
                if span is None:
                    refused.append({"source_id": sid, "passage": number, "reason": why})
                    continue
                gid = "gx-" + hashlib.sha256(f"{sid}\n{span[0]}\n{span[1]}".encode()).hexdigest()[:12]
                rows.append({"gold_id": gid, "source_id": sid, "span": span,
                             "quote": passage.get("quote"),
                             "passage_kind": passage.get("passage_kind")
                             if passage.get("passage_kind") in vocab.PASSAGE_KINDS else vocab.PASSAGE_OTHER,
                             "scope": passage.get("scope") if passage.get("scope") in vocab.SCOPES else None,
                             "from": sorted(set(passage.get("from") or [])),
                             "signed_by": signed_by})
        seen: dict[str, dict] = {}
        for row in rows:
            seen.setdefault(row["gold_id"], row)
        relabelled = {tasks[t]["source_id"] for t in by_task}
        kept = [r for r in read_labels(layout, "GX", required=False)
                if r["source_id"] not in relabelled]
        write_labels(layout, "GX", kept + list(seen.values()), canary, after_freeze=after_freeze)
        summary.update(passages=len(seen), refused=refused, articles=len(by_task))
    return summary


def import_g4(rows: list[dict], *, paths, layout: BenchLayout, bench: dict,
              articles: dict[str, dict], universe, conn: sqlite3.Connection,
              after_freeze: bool = False) -> dict:
    """The maintainer's spot checks: at most 30 negatives and 20 positives (section 8.1)."""
    libs = inputs.libraries(universe)
    sourceset = bench["sourceset"]["id"]
    out = []
    for number, row in enumerate(rows, 1):
        sid = row.get("source_id")
        if sid not in articles:
            raise CfError(f"G4 row {number}: {sid!r} is not an article of this bench")
        layer = inputs.read_layer(paths, sourceset, sid, articles[sid].get("layer_sha256"))
        span, why = _anchor_quote(layer, row.get("quote"))
        if span is None:
            raise CfError(f"G4 row {number}: {why}")
        if not row.get("checked_by"):
            raise CfError(f"G4 row {number}: a spot check names who checked it (checked_by)")
        kept, dropped = _recheck(conn, libs, row.get("declarations") or [])
        if dropped:
            raise CfError(f"G4 row {number}: {dropped[0]['declaration']!r} {dropped[0]['reason']}")
        kind = row.get("passage_kind") if row.get("passage_kind") in vocab.PASSAGE_KINDS \
            else vocab.PASSAGE_OTHER
        status, related = _gold_status(kind, kept)
        out.append({"gold_id": "g4-" + hashlib.sha256(f"{sid}\n{span[0]}\n{span[1]}".encode()).hexdigest()[:12],
                    "source_id": sid, "span": span, "quote": row.get("quote"),
                    "scope": vocab.SCOPE_FORMAL_MATH, "passage_kind": kind, "status": status,
                    "related": related, "declarations": kept, "checked_by": row["checked_by"]})
    negatives = sum(1 for r in out if r["status"] == vocab.STATUS_NOT_FORMALIZED)
    positives = sum(1 for r in out if r["status"] == vocab.STATUS_FORMALIZED)
    if negatives > G4_MAX_NEGATIVES or positives > G4_MAX_POSITIVES:
        raise CfError(f"{negatives} negative and {positives} positive spot checks; a release holds "
                      f"at most {G4_MAX_NEGATIVES} and {G4_MAX_POSITIVES}")
    write_labels(layout, "G4", out, bench["canary"], after_freeze=after_freeze)
    return {"rows": len(out), "negatives": negatives, "positives": positives}


# --------------------------------------------------------------------------
# spend: the benchmark's own receipt
# --------------------------------------------------------------------------

def record_spend(layout: BenchLayout, mode: str, labels: int, cost: dict | None) -> dict:
    """Append one labeler run to ``spend.json``; the bench's spend is never a run's."""
    doc = read_json(layout.spend_json, default={}) or {}
    runs = list(doc.get("runs") or [])
    runs.append({"mode": mode, "at": now(), "labels": labels, "cost": cost})
    priced = [r["cost"]["usd"] for r in runs if r.get("cost")]
    doc = {"schema": SPEND_SCHEMA, "bench": layout.bench_id, "runs": runs,
           "usd": round(sum(priced), 6), "unpriced_runs": sum(1 for r in runs if not r.get("cost"))}
    write_json(layout.spend_json, doc, sort_keys=True)
    return doc


# --------------------------------------------------------------------------
# P: the module-ablated index
# --------------------------------------------------------------------------

def plant(layout: BenchLayout, paths, universe, eval_g1: list[dict]) -> dict:
    """A copy of ``corpus.sqlite`` without the modules of the eval G1 declarations.

    The source is the universe's full database, checked against the universe
    first (``inputs.open_checked``); the copy keeps its ``meta`` -- so it passes
    ``corpus.query.check_universe`` for the same universe, which is what
    ``cf plan-locate`` checks of a tools database -- plus ``meta.planted``.  It
    is the TOOLS database of a P run (references/locate-documents.md section 9):
    the workers search it, while the gate and the outcomes check the full one,
    where a removed declaration still exists, so a citation of one from memory
    lands in the outcomes as a location and scores ``E-MEMORY``.
    """
    targets = {(a["library"], a["declaration"]) for row in eval_g1 for a in row.get("accept") or []}
    if not targets:
        raise CfError("no eval G1 declaration to ablate", hint="anchor G1 and freeze the splits first")
    source_db = paths.corpus_db(universe.id)
    inputs.open_checked(paths, universe, source_db).close()
    layout.planted_dir.mkdir(parents=True, exist_ok=True)
    staged = layout.planted_db.with_name(layout.planted_db.name + ".tmp")
    shutil.copyfile(source_db, staged)
    conn = sqlite3.connect(str(staged))
    try:
        modules: set[tuple[str, str]] = set()
        for lib, name in sorted(targets):
            row = conn.execute("SELECT module FROM decls WHERE library = ? AND name = ?",
                               (lib, name)).fetchone()
            if row and row[0]:
                modules.add((lib, row[0]))
        removed: list[str] = []
        for lib, module in sorted(modules):
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM decls WHERE library = ? AND module = ?", (lib, module))]
            names = [r[0] for r in conn.execute(
                "SELECT name FROM decls WHERE library = ? AND module = ?", (lib, module))]
            removed.extend(f"{lib}:{n}" for n in names)
            for decl_id in ids:
                conn.execute("DELETE FROM tags WHERE decl_id = ?", (decl_id,))
                conn.execute("DELETE FROM decls_fts WHERE rowid = ?", (decl_id,))
            conn.execute("DELETE FROM decls WHERE library = ? AND module = ?", (lib, module))
        for lib in sorted({lib for lib, _ in modules}):
            count = conn.execute("SELECT COUNT(*) FROM decls WHERE library = ?", (lib,)).fetchone()[0]
            conn.execute("UPDATE libraries SET declarations = ? WHERE id = ?", (count, lib))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('planted', ?)", (layout.bench_id,))
        conn.commit()
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    staged.replace(layout.planted_db)
    check = query.open_db(layout.planted_db)
    try:
        query.check_universe(check, universe)
    finally:
        check.close()
    doc = {"schema": ABLATION_SCHEMA, "bench": layout.bench_id,
           "universe": inputs.universe_pin(universe),
           "modules": [{"library": lib, "module": m} for lib, m in sorted(modules)],
           "removed": sorted(removed), "source_db_sha256": inputs.sha256_file(source_db),
           "corpus_sha256": inputs.sha256_file(layout.planted_db)}
    write_json(layout.ablation_json, doc, sort_keys=True)
    return doc


# --------------------------------------------------------------------------
# freeze
# --------------------------------------------------------------------------

def tier_summary(layout: BenchLayout, tier: str) -> dict | None:
    path = layout.label_file(tier)
    if not path.is_file():
        return None
    rows = read_jsonl_rows(path)
    out = {"file": f"gold/{LABEL_FILES[tier]}", "sha256": inputs.sha256_file(path), "rows": len(rows),
           "positives": sum(1 for r in rows if r.get("status") == vocab.STATUS_FORMALIZED)}
    if tier == "G1":
        out["anchored"] = sum(1 for r in rows if r.get("level") == "passage")
        out["article_level"] = sum(1 for r in rows if r.get("level") == "article")
    if tier == "G3":
        out["formal_math"] = sum(1 for r in rows if r.get("scope") == vocab.SCOPE_FORMAL_MATH)
    if tier == "GX":
        signers = sorted({r.get("signed_by") for r in rows if r.get("signed_by")})
        out["articles"] = len({r["source_id"] for r in rows})
        out["signed_by"] = signers
    return out


def check_canaries(layout: BenchLayout, canary: str) -> None:
    for tier in LABEL_FILES:
        for row in read_labels(layout, tier, required=False):
            if row.get("canary") != canary:
                raise CfError(f"{tier} label {row.get('gold_id')} does not carry the bench's canary",
                              hint="labels are written by cf bench anchor --transcribe, never by hand")


def manifest_body(doc: dict) -> dict:
    """The part of a manifest its sha covers: everything but the sha, the time and the chain."""
    return {k: v for k, v in doc.items() if k not in ("manifest_sha256", "created", "version",
                                                      "previous_sha256")}


def seal(doc: dict) -> dict:
    doc["manifest_sha256"] = sha_of(manifest_body(doc))
    return doc


def latest_manifest(layout: BenchLayout, version: int | None = None) -> tuple[int, dict]:
    manifests = layout.gold_manifests()
    if not manifests:
        raise CfError(f"bench {layout.bench_id} is not frozen", hint=f"cf bench freeze {layout.bench_id}")
    if version is None:
        number, path = manifests[-1]
    else:
        found = dict(manifests)
        if version not in found:
            raise CfError(f"bench {layout.bench_id} has no gold manifest version {version}")
        number, path = version, found[version]
    doc = read_json(path)
    if doc.get("manifest_sha256") != sha_of(manifest_body(doc)):
        raise CfError(f"{path} does not match its own manifest_sha256: the manifest was edited")
    return number, doc


def verify_frozen(layout: BenchLayout, manifest: dict) -> None:
    """Refuse when any frozen file differs from the manifest: gold is never edited."""
    for tier, info in sorted((manifest.get("tiers") or {}).items()):
        if not info or tier == "P" or not info.get("file"):
            continue
        path = layout.root / info["file"]
        got = inputs.sha256_file(path) if path.is_file() else None
        if got != info["sha256"]:
            raise CfError(f"{tier} labels changed after freeze ({info['file']})",
                          hint="a gold change is its own change: cf bench freeze --supersede "
                               "writes a new manifest with a new sha")
    for name, key in (("frame.json", "frame_sha256"), ("draw.json", "draw_sha256"),
                      ("spans.json", "spans_sha256"), ("clusters.json", "clusters_sha256")):
        want = manifest.get(key)
        path = layout.root / name
        if want and (not path.is_file() or inputs.sha256_file(path) != want):
            raise CfError(f"{name} changed after freeze", hint="re-freeze with --supersede")
