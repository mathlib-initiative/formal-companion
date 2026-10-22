"""Gold: G1 resolution and anchoring, the frozen labeler, G4, the planted index, the manifest.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.1 and 8.4
in the formats references/locate-bench.md sections 4 to 7 fix.

**G1** (maintainer-curated positives) starts mechanical: each curated entry --
a Mathlib ``1000.yaml`` QID (a suffixed key names its base QID), a ``100.yaml``
entry by its own QID or by L2's title key (``locate.candidates.normalise_title``:
the join ``cf locate candidates`` makes, so G1 and L2 agree; the build's curated
stratum reads the wider ``bench.curated``), an ``@[wikidata]`` tag in the
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

**The statement cut** (docs/proposals/statement-cut.md sections 2 and 6.2): gold of
a labeler that labels by judgment rules 3 keeps each ``hasPart``'s
``conjunct_relation``, takes the parts the labeler names as the span's ``bundle``
(:func:`gold_bundle`) and derives its statuses under status rule 2, as a run of
those rules is derived (:func:`gold_status_rule`); gold of an earlier labeler is
transcribed as it always was.

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
                     LABEL_ARGS_SCHEMA, LABEL_FILES, LABEL_RESULT_SCHEMA, MIN_OVERLAP_WORDS,
                     SPEND_SCHEMA, BenchLayout, canonical, curated_qid, now, overlap_words,
                     read_jsonl_rows, sha_of, write_canonical_jsonl)

#: the labeler, frozen by its file's sha (its prompts are inline in it)
LABELER_WORKFLOW = "workflows/locate-label.workflow.js"
#: bumped whenever the labeler's contract (args or result shape) or the rules it
#: labels by change (/6: the statement cut's judgment rules 3 -- the cut rules in the
#: inventory mode, a hasPart's conjunct_relation and the span's bundle in the label
#: mode, PD9 reworded and the smoke-3 audit's prompt rules, docs/proposals/
#: statement-cut.md section 7; /5: PD5-PD10, and R1's junk values replaced by PD7's test; PD10
#: and PD7's test for a value joined /5 unbumped: they add no gate check, so a /6
#: would label by the same judgment rules in LABELER_RULES below, and /5 had labelled
#: no gold yet (references/vocabulary.md section 1.11); inv-pilot-1's cut-rule words
#: for proofs, worked examples, context and the subject joined /6 unbumped for the
#: same reason: prompt words only, judgment rules 3 unchanged, and no gold labelled
#: under /6 (docs/proposals/statement-cut.md section 3.4); /4:
#: PD4, a stronger-hypotheses special case is narrowMatch; /3: the judgment rules
#: R1-R7 and the policy defaults PD1-PD3 of references/vocabulary.md section 1.11,
#: word for word)
LABELER_VERSION = "locate-label/6"
#: the first labeler version whose label rows name it (``labeler_version``): a row
#: without the field was labelled before it (``cf bench freeze``)
LABELER_VERSION_NAMED_FROM = "locate-label/5"
#: the judgment rules (``cflib.vocab.JUDGMENT_RULES``) each labeler version labels by:
#: the score holds gold and the runs it scores to one version from
#: ``cflib.vocab.RULES_PD5_PD9`` on (references/locate-bench.md section 8), and a
#: version not listed here (/1, /2, or none recorded) labelled by none of them.  Each
#: entry is a literal, frozen once its version is: a bump of ``LABELER_VERSION``
#: (for a rules change or any other change to the labeler's contract) adds a line
#: and never moves an old one
LABELER_RULES = {"locate-label/3": 1, "locate-label/4": 1, "locate-label/5": 2,
                 "locate-label/6": 3}
#: G4: at most this many maintainer spot checks per release (section 8.1)
G4_MAX_NEGATIVES = 30
G4_MAX_POSITIVES = 20
#: how many rename hops a declaration may take before resolution gives up
MAX_RENAME_HOPS = 10
#: an excerpt handed to the labeler is cut at this many characters
MAX_EXCERPT_CHARS = 4000
#: the relations that make a gold declaration count towards ``formalized``
COUNTING = frozenset(vocab.LOCATE_RELATIONS - {vocab.RELATED_MATCH})



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
    """Mathlib's ``$MATHLIB/docs/1000.yaml`` shape: QID -> ``{title, decl | decls, ...}``.

    A key may carry one letter (``Q4724004A``, ``Q180345X``) when one Wikidata item
    covers several theorems: the entry joins on the base QID and keeps its key.
    """
    data = _yaml_or_json(path)
    if not isinstance(data, dict):
        raise CfError(f"{path} is not a mapping keyed by Wikidata QID")
    out = []
    for key in sorted(data, key=str):
        entry = data[key] if isinstance(data[key], dict) else {}
        qid = curated_qid(key)
        if qid is None:
            raise CfError(f"{path}: key {key!r} is not a Wikidata QID (Q<digits>, or with one "
                          f"letter for one of several theorems of an item)")
        out.append({"key": str(key).strip(), "qid": qid, "title": entry.get("title"),
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
        out.append({"key": str(key), "qid": curated_qid(entry.get("wikidata")),
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

#: the name tokens ``to_additive`` translates, multiplicative -> additive: whole
#: ``.``/``_``-separated tokens, and CamelCase tokens -- a structure name exactly, else a
#: leading word (``MulAction`` -> ``AddAction``); the namespace ``Prod`` (a type) is not one
_TOKEN_TWINS = {"mul": "add", "one": "zero", "prod": "sum", "inv": "neg", "div": "sub",
                "pow": "nsmul", "zpow": "zsmul", "smul": "vadd"}
_STRUCTURE_TWINS = {"Monoid": "AddMonoid", "Group": "AddGroup", "Semigroup": "AddSemigroup",
                    "CommMonoid": "AddCommMonoid", "CommGroup": "AddCommGroup",
                    "Submonoid": "AddSubmonoid", "Subgroup": "AddSubgroup"}
_CAMEL_TWINS = {"Mul": "Add", "One": "Zero", "Inv": "Neg", "Div": "Sub"}
_SPLIT = re.compile(r"([._])")


def _token_kind(token: str) -> tuple[str | None, str]:
    """``("mul" | "add" | None, the token in the other kind)``."""
    for table in (_TOKEN_TWINS, _STRUCTURE_TWINS):
        for mul, add in table.items():
            if token == mul:
                return "mul", add
            if token == add:
                return "add", mul
    for mul, add in _CAMEL_TWINS.items():
        for kind, mine, other in (("mul", mul, add), ("add", add, mul)):
            rest = token[len(mine):]
            if token.startswith(mine) and rest[:1].isupper():
                return kind, other + rest
    return None, token


def twins(name: str) -> list[str]:
    """The name ``to_additive`` gives ``name``, or takes it from: the whole image, one direction.

    Every multiplicative token is translated at once (``mul_one`` -> ``add_zero``,
    never ``add_one`` or ``mul_zero``), and a name that mixes the two kinds
    (``mul_zero``) is no ``to_additive`` source or target and has no twin.  A
    twin is kept by the caller only when it exists at the pin.
    """
    parts = _SPLIT.split(name)
    kinds = [_token_kind(part) if part not in (".", "_") else (None, part) for part in parts]
    found = {kind for kind, _ in kinds if kind}
    if len(found) != 1:
        return []
    image = "".join(other if kind else part for part, (kind, other) in zip(parts, kinds))
    return [image] if image != name else []


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
            names = [at]
            # the rename table widens even when the old name survives at the pin (a
            # deprecated alias): a correct worker cites the new name
            nxt, seen = renames.get(at), {at}
            while nxt and nxt not in seen and len(seen) <= MAX_RENAME_HOPS:
                seen.add(nxt)
                if query.exists(conn, lib, lib_commit, nxt):
                    accept.setdefault((lib, nxt), "renamed")
                    names.append(nxt)
                nxt = renames.get(nxt)
            # and so does the index's own rename of a deprecated alias (corpus deprecated_to)
            for known in list(names):
                new_name = (inputs.declaration(conn, lib, lib_commit, known) or {}).get("deprecated_to")
                if new_name and query.exists(conn, lib, lib_commit, new_name):
                    accept.setdefault((lib, new_name), "renamed")
                    if new_name not in names:
                        names.append(new_name)
            for twin in sorted({t for n in names for t in twins(n)}):
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

def _source_block(paths, sourceset: str, article: dict, *, tag_blind: bool = False) -> dict:
    """What the labeler is told of a source; tag-blind (calibrate), never its Wikidata QID."""
    sdir = inputs.source_dir(paths, sourceset, article["source_id"])
    return {"source_id": article["source_id"], "title": article.get("title"),
            "qid": None if tag_blind else article.get("wikidata"),
            "revision": article.get("revision"),
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
        "sources": [_source_block(paths, sourceset, articles[s], tag_blind=mode == "calibrate")
                    for s in sorted(used)],
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
    ids = [str(l.get("task_id")) for l in result["labels"] if isinstance(l, dict)]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise CfError(f"the result labels {len(twice)} task(s) more than once: " + ", ".join(twice[:5]),
                      hint="one label per task: re-run the labeler on these tasks")


def _anchor_quote(layer: str, quote: Any) -> tuple[list[int] | None, str]:
    """The gold span a quote anchors: matched exactly once, holding at least four whole words.

    A span of fewer whole layer words than the join's floor could never join any
    run passage, so it would score as a certain inventory miss; it is refused here
    instead (G1 then scores at article level, GX records the refusal, G4 stops).
    """
    from gate import locate_text

    if quote is not None and not isinstance(quote, dict):
        return None, (f"the labeler's quote is a {type(quote).__name__}, not an object "
                      "{exact, prefix, suffix}")
    if not isinstance(quote, dict) or not str(quote.get("exact") or "").strip():
        return None, "the labeler gave no quote"
    hits = locate_text.find_quote(layer, quote.get("exact"), quote.get("prefix") or "",
                                  quote.get("suffix") or "")
    if len(hits) != 1:
        return None, f"the quote matches the layer {len(hits)} times, not exactly once"
    span = [hits[0], hits[0] + len(quote["exact"])]
    words = overlap_words(layer, tuple(span), tuple(span))
    if words < MIN_OVERLAP_WORDS:
        return None, (f"the quote holds {words} whole layer word(s), fewer than the join's "
                      f"{MIN_OVERLAP_WORDS}: it could never join a run passage")
    return span, ""


def _part_key(d: dict) -> str:
    """The key a gold bundle names a part by, and :func:`_gold_status` keys it by."""
    return f"{d['library']}:{d['declaration']}"


def _gold_status(passage_kind: str, declarations: list[dict], *, bundle: dict | None = None,
                 status_rule: int = vocab.STATUS_RULE_D1) -> tuple[str | None, bool]:
    """``(derived status, related)`` of a gold passage from its re-checked declarations.

    ``status_rule`` is the one the gold's labeler labels under (:func:`gold_status_rule`).
    A ``bundle`` (:func:`gold_bundle`, status rule 2 only) is the labeler's judgment,
    so it stands as a survived coverage claim does in a run: one more location of
    its weakest part's relation (``vocab.derive_status``; docs/proposals/
    statement-cut.md section 2.3).
    """
    kind = passage_kind if passage_kind in vocab.PASSAGE_KINDS else vocab.PASSAGE_OTHER
    counting = [d for d in declarations if d.get("relation") in COUNTING]
    related = any(d.get("relation") == vocab.RELATED_MATCH for d in declarations)
    if not counting:
        return vocab.STATUS_NOT_FORMALIZED, related
    if bundle is None:
        return vocab.derive_status(kind, counting, absent=False, status_rule=status_rule), related
    if status_rule != vocab.STATUS_RULE_BUNDLE:
        raise CfError(f"a gold bundle is status rule {vocab.STATUS_RULE_BUNDLE}'s, not "
                      f"{status_rule}'s")
    claim = {"complete": True, "challenge": vocab.CHALLENGE_SURVIVED,
             "parts": [_part_key(p) for p in bundle["parts"]], "relation": bundle["relation"]}
    located = [dict(d, location_key=_part_key(d)) for d in counting]
    return vocab.derive_status(kind, located, absent=False, coverage=claim,
                               status_rule=status_rule), related


def gold_bundle(declarations: list[dict], named: Any) -> tuple[dict | None, str | None]:
    """The span's bundle as the labeler names it, or why it stands as none.

    docs/proposals/statement-cut.md sections 2.1 and 6.2: under judgment rules 3 the
    labeler's label mode gives each ``hasPart`` its ``conjunct_relation`` and names in
    ``bundle`` the parts that together state every claim of the span, each claim
    stated by exactly one of them and none stating them all.  The bundle stands on at
    least ``MIN_COVERAGE_PARTS`` distinct parts, each one of the declarations kept at
    the pin (``declarations``, :func:`_recheck`'s), a ``hasPart`` and strong to its
    conjuncts (``BUNDLE_RELATIONS``), as the gate holds a run's bundle (J3); its
    relation is its weakest part's.  Returns ``({parts, relation}, None)``, ``(None,
    None)`` when the labeler names none, or ``(None, why)``: a bundle that does not
    stand leaves its parts each a ``hasPart``, as a refuted claim does in a run.
    """
    if named is None or named == []:
        return None, None
    if not isinstance(named, list) or not all(isinstance(p, dict) for p in named):
        return None, "the labeler's bundle is not a list of {library, declaration} parts"
    keys = [f"{p.get('library') or ''}:{p.get('declaration') or ''}" for p in named]
    if len(set(keys)) != len(keys) or len(keys) < vocab.MIN_COVERAGE_PARTS:
        return None, (f"a bundle names at least {vocab.MIN_COVERAGE_PARTS} distinct parts, "
                      f"not {len(set(keys))}")
    kept: dict[str, list[dict]] = {}
    for d in declarations:
        kept.setdefault(_part_key(d), []).append(d)
    parts = []
    for key in keys:
        if len(kept.get(key) or []) > 1:
            # _recheck keeps each once; a list holding one twice is no bundle's, in any order
            return None, (f"bundle part {key} is kept {len(kept[key])} times: a declaration "
                          f"has one relation to a span")
        d = (kept.get(key) or [None])[0]
        if d is None:
            return None, f"bundle part {key} is none of the declarations kept at the pin"
        if d["relation"] != vocab.HAS_PART:
            return None, f"bundle part {key} is a {d['relation']}, not a hasPart"
        if d.get("conjunct_relation") not in vocab.BUNDLE_RELATIONS:
            return None, (f"bundle part {key} states its claims as "
                          f"{d.get('conjunct_relation')!r}: a bundle's parts state them "
                          f"strongly ({', '.join(sorted(vocab.BUNDLE_RELATIONS))})")
        parts.append(d)
    relation = max((d["conjunct_relation"] for d in parts),
                   key=vocab.RELATION_STRENGTH.__getitem__)
    return {"parts": [{"library": d["library"], "declaration": d["declaration"]}
                      for d in sorted(parts, key=_part_key)],
            "relation": relation}, None


def _recheck(conn: sqlite3.Connection, libs: dict[str, dict], declarations: list[dict],
             *, conjuncts: bool = False) -> tuple[list[dict], list[dict]]:
    """The labeler's declarations kept at the pin, and the ones dropped with the reason.

    ``conjuncts`` (gold of judgment rules 3, :func:`gold_status_rule`): a ``hasPart``
    keeps the ``conjunct_relation`` the labeler gave it, one of
    ``CONJUNCT_RELATIONS`` or none (a part of a span that states one claim), and is
    dropped with the reason for any other value; no other relation carries one.  And
    a declaration is kept once (:func:`_once`): status rule 2 keys a location by
    ``library:declaration``, which a span's locations hold at most once each.
    """
    kept, dropped = [], []
    listed: list[dict] = []
    for d in declarations or []:
        lib, name = str(d.get("library") or ""), str(d.get("declaration") or "")
        relation = d.get("relation")
        if lib not in libs:
            dropped.append({**d, "reason": "library outside the universe"})
            continue
        if relation not in vocab.LOCATE_RELATIONS:
            dropped.append({**d, "reason": f"relation {relation!r} is not a locate relation"})
            continue
        part = d.get("conjunct_relation") if conjuncts and relation == vocab.HAS_PART else None
        if part is not None and part not in vocab.CONJUNCT_RELATIONS:
            dropped.append({**d, "reason": f"conjunct_relation {part!r} is not one of "
                                           f"{', '.join(sorted(vocab.CONJUNCT_RELATIONS))}"})
            continue
        row = inputs.declaration(conn, lib, libs[lib]["commit"], name)
        if not row:
            dropped.append({**d, "reason": "not in the universe at its commit (re-checked at the pin)"})
            continue
        kept.append({"library": lib, "declaration": name, "commit": libs[lib]["commit"],
                     "relation": relation, "proof_status": row["proof_status"],
                     "statement": bool(row["statement"]), "evidence": d.get("evidence") or ""})
        listed.append(d)
        if part is not None:
            kept[-1]["conjunct_relation"] = part
    if conjuncts:
        kept, again = _once(kept, listed)
        dropped.extend(again)
    return kept, dropped


def _once(kept: list[dict], listed: list[dict]) -> tuple[list[dict], list[dict]]:
    """Each ``library:declaration`` kept once, and the listings dropped with the reason.

    ``listed[i]`` is the labeler's entry ``kept[i]`` came from.  Under status rule 2 a
    standing bundle keys every counting declaration by ``library:declaration``
    (:func:`_gold_status`), and ``vocab.derive_status`` refuses a key held twice, so a
    labeler that listed one declaration twice aborted the whole transcription.  A
    declaration listed again with the same relation (and ``conjunct_relation``) says
    nothing new: the first listing is kept and each repeat dropped.  Listed with two
    relations it contradicts itself, and a declaration has one relation to a span:
    every listing is dropped, whatever their order, and a bundle naming it does not
    stand (:func:`gold_bundle`).
    """
    def judged(d: dict) -> tuple:
        return d["relation"], d.get("conjunct_relation")

    groups: dict[str, list[int]] = {}
    for i, d in enumerate(kept):
        groups.setdefault(_part_key(d), []).append(i)
    out, dropped = [], []
    for i, d in enumerate(kept):
        group = groups[_part_key(d)]
        if len(group) == 1:
            out.append(d)
            continue
        relations = sorted({judged(kept[j]) for j in group}, key=repr)
        if len(relations) > 1:
            said = ", ".join(r if c is None else f"{r} ({c})" for r, c in relations)
            dropped.append({**listed[i], "reason": f"listed {len(group)} times with different "
                                                   f"relations ({said}): a declaration has one "
                                                   f"relation to a span"})
        elif i == group[0]:
            out.append(d)
        else:
            dropped.append({**listed[i], "reason": f"listed {len(group)} times with the same "
                                                   f"relation: the first is kept"})
    return out, dropped


def labeler_rules(version: Any) -> int:
    """The judgment rules a labeler version labels by (:data:`LABELER_RULES`; 0 if none)."""
    return LABELER_RULES.get(version, 0) if isinstance(version, str) else 0


def gold_status_rule(version: Any) -> int:
    """The status rule a labeler version's gold statuses are derived under.

    ``STATUS_RULE_BUNDLE`` from the statement cut's judgment rules on
    (``RULES_STATEMENT_CUT``), as a locate run's ``status_rule`` is 2 exactly when its
    ``judgment_rules`` is 3 (docs/proposals/statement-cut.md section 2.2), so a score
    compares statuses derived alike; ``STATUS_RULE_D1`` for every earlier labeler.
    """
    return (vocab.STATUS_RULE_BUNDLE if labeler_rules(version) >= vocab.RULES_STATEMENT_CUT
            else vocab.STATUS_RULE_D1)


def provenance(args: dict) -> dict:
    """What every transcribed row carries of the labeler that made it (section 6, frozen).

    ``labeler_version`` (from locate-label/5 on) is the version the args were
    emitted for, which the result echoed: freeze reads the gold's version from
    the rows, never from this checkout's constant, so gold labelled before a
    policy change cannot be frozen as if labelled after it.
    """
    labeler = args.get("labeler") or {}
    return {"labeler_sha256": labeler.get("workflow_sha256"), "args_digest": args.get("digest"),
            "labeler_commit": labeler.get("skill_commit"),
            "labeler_version": labeler.get("version")}


def _keep_earlier(old: list[dict], fresh: set[str], keep: set[str], sha: str | None,
                  what: str) -> list[dict]:
    """The earlier rows a partial transcription keeps -- all from this same labeler."""
    kept = [r for r in old if r["gold_id"] not in fresh and r["gold_id"] in keep]
    others = sorted({str(r.get("labeler_sha256")) for r in kept} - {str(sha)})
    if others:
        raise CfError(f"{what}: {len(kept)} earlier row(s) this result does not answer came from "
                      f"another labeler ({', '.join(o[:12] for o in others)}…)",
                      hint="one tier, one labeler: re-run the labeler on every task of the mode "
                           "(re-emit, then transcribe a full result)")
    return kept


def _merged(layout: BenchLayout, tier: str, rows: list[dict], *, keep: set[str],
            sha: str | None) -> list[dict]:
    """This transcription's rows over the tier's earlier ones, by gold id.

    A resumed or partial labeler run answers some tasks; the rows it does not
    answer keep their earlier labels, as long as their task still exists and
    they came from the same labeler.
    """
    old = [{k: v for k, v in r.items() if k not in ("canary", "tier")}
           for r in read_labels(layout, tier, required=False)]
    return _keep_earlier(old, {r["gold_id"] for r in rows}, keep, sha, f"{tier} labels") + rows


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
    stamp = provenance(args)
    sha = stamp["labeler_sha256"]
    # the statement cut's gold (judgment rules 3): a hasPart's conjunct_relation, the
    # labeler's bundle and the statuses of status rule 2 (gold_status_rule)
    status_rule = gold_status_rule(stamp["labeler_version"])
    cut = status_rule == vocab.STATUS_RULE_BUNDLE
    summary: dict[str, Any] = {"mode": mode, "labels": len(by_task), "unlabeled": unlabeled}
    if mode == "anchor":
        entries = {e["entry_id"]: e for e in read_json(layout.g1_resolved).get("entries") or []}
        rows = []
        # an unanswered task stays unlabelled: it writes no row, and keeps any earlier one
        for task_id in sorted(by_task):
            entry = entries.get(task_id)
            if entry is None:
                raise CfError(f"{task_id} is no longer a resolved G1 entry; re-emit the args")
            label = by_task[task_id]
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
            status, related = (_gold_status(kind, declarations, status_rule=status_rule)
                               if relation else (None, False))
            rows.append({"gold_id": task_id, "source_id": entry["source_id"], "qid": entry["qid"],
                         "via": entry["via"], "level": "passage" if span else "article",
                         "span": span, "quote": label.get("quote") if span else None,
                         "anchor_problem": why or None, "passage_kind": kind,
                         "relation": relation, "status": status, "related": related,
                         "scope": vocab.SCOPE_FORMAL_MATH, "declarations": declarations,
                         "accept": [{"library": a["library"], "declaration": a["declaration"]}
                                    for a in entry["accept"]],
                         "evidence": label.get("evidence") or "", **stamp})
        write_labels(layout, "G1", _merged(layout, "G1", rows, keep=set(entries), sha=sha), canary,
                     after_freeze=after_freeze)
        summary.update(anchored=sum(1 for r in rows if r["level"] == "passage"),
                       article_level=sum(1 for r in rows if r["level"] == "article"))
    elif mode in ("label", "calibrate"):
        rows = []
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
            kept, dropped = _recheck(conn, libs, label.get("declarations") or [], conjuncts=cut)
            if mode == "calibrate":
                # h measures what the tag-blind HUNTERS found: a declaration counts only
                # when a hunter returned it and the labeler kept it at the pin
                accept = {(a["library"], a["declaration"]) for a in g1[task_id]["accept"]}
                hunted = {str(f) for h in label.get("hunters") or [] if isinstance(h, dict)
                          for f in h.get("found") or []}
                hit = any((d["library"], d["declaration"]) in accept
                          and f"{d['library']}:{d['declaration']}" in hunted for d in kept)
                rows.append({"gold_id": task_id, "found": hit,
                             "declarations": [{"library": d["library"], "declaration": d["declaration"]}
                                              for d in kept],
                             "hunters": label.get("hunters") or [], **stamp})
                continue
            formal = scope == vocab.SCOPE_FORMAL_MATH
            bundle, unbundled = (gold_bundle(kept, label.get("bundle")) if cut and formal
                                 else (None, None))
            status, related = (_gold_status(kind, kept, bundle=bundle, status_rule=status_rule)
                               if formal else (None, False))
            span_row = spans_by_id[task_id]
            row = {"gold_id": task_id, "source_id": task["source_id"], "span": task["span"],
                   "section": task.get("section"), "scope": scope, "passage_kind": kind,
                   "status": status, "related": related, "declarations": kept,
                   "dropped": dropped, "pi_span": span_row.get("pi_span"),
                   "pi_article": span_row.get("pi_article"),
                   "evidence": label.get("evidence") or "",
                   "hunters": label.get("hunters") or [], **stamp}
            if cut:
                row.update(bundle=bundle, bundle_problem=unbundled)
            rows.append(row)
        if mode == "calibrate":
            anchored = {gid for gid, r in g1.items() if r.get("level") == "passage"}
            earlier = (read_json(layout.calibration_json).get("rows") or []
                       if layout.calibration_json.is_file() else [])
            merged = _keep_earlier(earlier, {r["gold_id"] for r in rows}, anchored, sha,
                                   "calibration") + rows
            unanswered = sorted(anchored - {r["gold_id"] for r in merged})
            found = sum(1 for r in merged if r["found"])
            total = len(anchored)
            doc = {"schema": CALIBRATION_SCHEMA, "bench": layout.bench_id, "found": found,
                   "total": total, "answered": len(merged), "unanswered": unanswered,
                   # h is over EVERY anchored G1 passage, so a partial run reports none
                   "h": (found / total) if total and not unanswered else None,
                   "tag_blind": True, "rows": sorted(merged, key=lambda r: r["gold_id"]),
                   "labeler": args["labeler"], "at": now()}
            write_json(layout.calibration_json, doc, sort_keys=True)
            summary.update(found=found, total=total, h=doc["h"], unanswered=unanswered)
        else:
            write_labels(layout, "G3", _merged(layout, "G3", rows, keep=set(spans_by_id), sha=sha),
                         canary, after_freeze=after_freeze)
            summary.update(formal_math=sum(1 for r in rows if r["scope"] == vocab.SCOPE_FORMAL_MATH),
                           formalized=sum(1 for r in rows if r["status"] == vocab.STATUS_FORMALIZED))
    else:  # inventory
        if not signed_by:
            raise CfError("a GX inventory is signed by the maintainer",
                          hint="pass --signed-by <name> with --transcribe")
        rows, refused = [], []
        for task_id in sorted(by_task):
            sid = tasks[task_id]["source_id"]
            enumerators = set(by_task[task_id].get("enumerators") or [])
            if not {"A", "B"} <= enumerators:
                raise CfError(f"the GX inventory of {sid} lists enumerators "
                              f"{sorted(enumerators) or 'none'}, not both A and B",
                              hint="a GX inventory is two independent enumerations: re-run the "
                                   "labeler on that article")
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
                             "signed_by": signed_by, **stamp})
        seen: dict[str, dict] = {}
        for row in rows:
            seen.setdefault(row["gold_id"], row)
        relabelled = {tasks[t]["source_id"] for t in by_task}
        kept = [r for r in read_labels(layout, "GX", required=False)
                if r["source_id"] not in relabelled]
        others = sorted({str(r.get("labeler_sha256")) for r in kept} - {str(sha)})
        if others:
            raise CfError(f"GX labels: {len(kept)} earlier row(s) came from another labeler",
                          hint="one tier, one labeler: re-run the inventory on every GX article")
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

def plant(layout: BenchLayout, paths, universe, eval_g1: list[dict], into: Path) -> dict:
    """A copy of ``corpus.sqlite`` without the modules of the eval G1 declarations, in ``into``.

    ``into`` is a staging directory: ``cf bench freeze`` moves it to
    ``gold/planted/`` only once the manifest that records it is written, so a
    refused freeze leaves the frozen planted tier as it was.

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
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    planted_db = into / layout.planted_db.name
    staged = planted_db.with_name(planted_db.name + ".tmp")
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
    staged.replace(planted_db)
    check = query.open_db(planted_db)
    try:
        query.check_universe(check, universe)
    finally:
        check.close()
    doc = {"schema": ABLATION_SCHEMA, "bench": layout.bench_id,
           "universe": inputs.universe_pin(universe),
           "modules": [{"library": lib, "module": m} for lib, m in sorted(modules)],
           "removed": sorted(removed), "source_db_sha256": inputs.sha256_file(source_db),
           "corpus_sha256": inputs.sha256_file(planted_db)}
    write_json(into / layout.ablation_json.name, doc, sort_keys=True)
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


def verify_planted(layout: BenchLayout, planted: dict | None) -> None:
    """Refuse when the planted tier's files are not the ones a manifest froze."""
    if not planted:
        return
    for path, key in ((layout.ablation_json, "ablation_sha256"), (layout.planted_db, "corpus_sha256")):
        got = inputs.sha256_file(path) if path.is_file() else None
        if got != planted.get(key):
            raise CfError(f"the planted tier changed after freeze ({path.name})",
                          hint="a gold change is its own change: cf bench freeze --plant "
                               "--supersede writes a new manifest with a new sha")


def verify_frozen(layout: BenchLayout, manifest: dict, *, canary: str | None = None) -> None:
    """Refuse when any frozen file differs from the manifest: gold is never edited.

    Every label file, the frame, draw, spans and clusters, the planted tier's
    ``ablation.json`` and database, and -- given the bench's canary -- the canary
    the manifest hashed.
    """
    verify_planted(layout, (manifest.get("tiers") or {}).get("P"))
    if canary is not None and manifest.get("canary_sha256") != sha_of(canary):
        raise CfError("the bench's canary is not the one its gold manifest hashed",
                      hint="bench.yaml's canary is part of the frozen gold; restore it")
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
