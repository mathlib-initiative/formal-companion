"""The frame, the draws, the clusters, the split and the G3 span sample.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.3 and 8.5
(steps 1 to 3) in the shapes references/locate-bench.md sections 2 and 3 fix.

* **The frame** is a list of articles (the live Tier A list), each resolved to
  the one source of the pinned sourceset manifest a run reads: by page id, the
  sourceset's own rule (references/locate-documents.md section 7) -- a page a
  consumer pinned at its adopted revision, any other at its highest enumerated
  one -- never the highest *rendered* revision, which is the newer enumerated
  one as soon as both are rendered.  The article's QID and assessment are the
  page's: when the resolved entry lacks one, the page's other entries supply
  it, so an adopted revision recorded without them stays in its stratum.  An
  article the manifest does not hold, or holds unrendered, is refused by name:
  a frame that silently lost an article would bias every inclusion probability.
* **The draws** are two simple random samples without replacement, each with
  its own seeded generator: ``uniform`` from the whole frame and ``curated``
  from the frame articles whose QID a curated list names.  They are drawn
  independently, so an article can be in both; its inclusion probability is
  ``1 - (1 - pi_uniform) (1 - pi_curated)``.
* **Clusters** are a union-find over the drawn articles: two articles are one
  cluster when they share an article QID or a gold accept-set declaration.
  Clustering runs again whenever gold changes (after anchoring, and at freeze).
* **The split** is picked by each cluster's own salted hash against the
  cumulative shares (section 8.3: "the cluster hash picks the split"), so a
  cluster never spans two splits and its split depends on its members alone;
  the shares hold in expectation.
* **The G3 span sample** draws, per article, ``k`` blocks of ``paragraphs.json``
  uniformly (each block holds at least four words, so it can join), each
  carrying the section path ``sections.json`` gives it.  It reads no run.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Iterable

from cflib.errors import CfError
from cflib.yamlio import load_yaml

from . import inputs
from .layout import overlap_words, word_spans

_QID = re.compile(r"Q[1-9][0-9]*\Z")
_INT = re.compile(r"[1-9][0-9]*\Z")
#: the fewest words a sampled span holds -- the join's overlap floor
MIN_SPAN_WORDS = 4


# --------------------------------------------------------------------------
# input lists
# --------------------------------------------------------------------------

def _list_file(path: Path) -> list[Any]:
    """A JSON list, a JSONL file, a YAML/JSON mapping's keys, or one value per text line."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            try:
                return [json.loads(line) for line in stripped.splitlines() if line.strip()]
            except ValueError as exc:
                raise CfError(f"{path}: neither JSON nor JSON lines ({exc})") from exc
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data)
        raise CfError(f"{path}: JSON that is neither a list nor a mapping")
    lines = [line.split("#", 1)[0].rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]
    if lines and not lines[0].lstrip().startswith("- ") and ":" in lines[0]:
        data = load_yaml(text, where=str(path))
        if isinstance(data, dict):
            return list(data)
        if isinstance(data, list):
            return data
    out = []
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            out.append(line)
    return out


def read_frame_list(path: Path) -> list[dict]:
    """``[{"pageid": int} | {"source_id": str}]`` from a tier list file."""
    out = []
    for value in _list_file(path):
        if isinstance(value, dict):
            if value.get("source_id"):
                out.append({"source_id": str(value["source_id"])})
            elif value.get("pageid") is not None:
                out.append({"pageid": int(value["pageid"])})
            else:
                raise CfError(f"{path}: a frame row names neither source_id nor pageid: {value!r}")
        elif isinstance(value, int) and not isinstance(value, bool):
            out.append({"pageid": value})
        elif isinstance(value, str) and _INT.match(value.strip()):
            out.append({"pageid": int(value.strip())})
        elif isinstance(value, str) and value.strip():
            out.append({"source_id": value.strip()})
        else:
            raise CfError(f"{path}: {value!r} is neither a page id nor a source id")
    return out


def read_qids(path: Path) -> set[str]:
    """QIDs from a list file, or the QID keys of a curated mapping (Mathlib's ``1000.yaml``)."""
    qids = set()
    for value in _list_file(path):
        token = str(value.get("qid") if isinstance(value, dict) else value).strip()
        if _QID.match(token):
            qids.add(token)
    return qids


# --------------------------------------------------------------------------
# the frame
# --------------------------------------------------------------------------

def _page_fact(entry: dict, others: list[dict], key: str) -> tuple[Any, str | None]:
    """``(value, the source id it was read from)`` of a page-level fact (QID, assessment).

    The entry's own value when it has one; else the one value the page's other
    entries agree on; else nothing (two disagreeing values are not guessed between).
    """
    own = entry.get(key)
    if own:
        return own, None
    found = {json.dumps(e.get(key), sort_keys=True): e["source_id"]
             for e in others if e is not entry and e.get(key)}
    if len(found) == 1:
        text, sid = next(iter(found.items()))
        return json.loads(text), sid
    return own, None


def build_frame(rows: list[dict], manifest, curated: set[str]) -> dict:
    """Resolve every frame row to the source of the pinned manifest a run reads."""
    preferred = manifest.preferred()
    pages = manifest.page_entries()
    articles: dict[str, dict] = {}
    seen_pages: dict[int, str] = {}
    missing, unrendered, duplicate = [], [], []
    for row in rows:
        if "source_id" in row:
            entry = manifest.entries.get(row["source_id"])
            if entry is None:
                missing.append(row["source_id"])
                continue
            basis = "source_id"
        else:
            sid = preferred.get(row["pageid"])
            if sid is None:
                missing.append(f"pageid {row['pageid']}")
                continue
            entry = manifest.entries[sid]
            basis = "adopted" if inputs.adopted(entry) else "enumerated"
        if not entry.get("layer_sha256"):
            unrendered.append(entry["source_id"])
            continue
        sid = entry["source_id"]
        pageid = entry.get("pageid")
        if sid in articles or (pageid is not None and int(pageid) in seen_pages):
            duplicate.append(sid)
            continue
        others = pages.get(int(pageid), []) if pageid is not None else []
        qid, qid_from = _page_fact(entry, others, "wikidata")
        assessment, assessment_from = _page_fact(entry, others, "assessment")
        qid = qid or None
        articles[sid] = {
            "source_id": sid, "pageid": pageid, "revision": entry.get("revision"),
            "title": entry.get("title"), "wikidata": qid,
            "assessment": assessment or {},
            "layer_sha256": entry.get("layer_sha256"), "renderer": entry.get("renderer"),
            "revision_basis": basis,
            "page_facts_from": ({"wikidata": qid_from, "assessment": assessment_from}
                                if qid_from or assessment_from else None),
            "curated": bool(qid and qid in curated),
        }
        if pageid is not None:
            seen_pages[int(pageid)] = sid
    if missing or unrendered:
        raise CfError(
            f"the frame names {len(missing)} article(s) the pinned manifest does not hold and "
            f"{len(unrendered)} it holds unrendered: "
            + ", ".join((missing + unrendered)[:10]) + (" …" if len(missing + unrendered) > 10 else ""),
            hint="a frame that silently loses an article biases every inclusion probability; "
                 "enumerate, fetch and render them, or pin the manifest version that has them")
    ordered = [articles[s] for s in sorted(articles)]
    return {"articles": ordered, "N": len(ordered),
            "N_curated": sum(1 for a in ordered if a["curated"]),
            "N_adopted": sum(1 for a in ordered if a["revision_basis"] == "adopted"),
            "duplicates": sorted(duplicate)}


# --------------------------------------------------------------------------
# the draws
# --------------------------------------------------------------------------

def _sample(ids: list[str], n: int, seed: str) -> list[str]:
    rng = random.Random(seed)
    if n >= len(ids):
        return sorted(ids)
    return sorted(rng.sample(sorted(ids), n))


def draw(frame: dict, *, uniform: int, curated: int, seed: int) -> dict:
    """Two independent seeded draws, with each article's inclusion probabilities."""
    if uniform < 0 or curated < 0:
        raise CfError("a draw size is a count of articles, never negative")
    every = [a["source_id"] for a in frame["articles"]]
    eligible = [a["source_id"] for a in frame["articles"] if a["curated"]]
    picked_u = _sample(every, uniform, f"{seed}:uniform")
    picked_c = _sample(eligible, curated, f"{seed}:curated")
    pi_u = (min(uniform, len(every)) / len(every)) if every else 0.0
    pi_c = (min(curated, len(eligible)) / len(eligible)) if eligible else 0.0
    eligible_set, in_u, in_c = set(eligible), set(picked_u), set(picked_c)
    rows = []
    for sid in sorted(in_u | in_c):
        p_c = pi_c if sid in eligible_set else 0.0
        rows.append({"source_id": sid, "in_uniform": sid in in_u, "in_curated": sid in in_c,
                     "pi_uniform": pi_u, "pi_curated": p_c,
                     "pi": 1.0 - (1.0 - pi_u) * (1.0 - p_c)})
    return {"seed": seed,
            "uniform": {"n": uniform, "N": len(every), "drawn": picked_u,
                        "short": uniform > len(every)},
            "curated": {"n": curated, "N": len(eligible), "drawn": picked_c,
                        "short": curated > len(eligible)},
            "articles": rows}


# --------------------------------------------------------------------------
# clusters and the split
# --------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def cluster_key(members: Iterable[str]) -> str:
    """A cluster's id: a hash of its sorted member source ids."""
    return "c-" + hashlib.sha256("\n".join(sorted(members)).encode("utf-8")).hexdigest()[:16]


def clusters(articles: list[dict], gold_declarations: dict[str, set[str]] | None = None) -> dict:
    """``{cluster id: [source ids]}``: shared article QIDs and shared gold declarations join."""
    ids = [a["source_id"] for a in articles]
    uf = _UnionFind(ids)
    by_qid: dict[str, str] = {}
    for a in articles:
        qid = a.get("wikidata")
        if qid:
            if qid in by_qid:
                uf.union(by_qid[qid], a["source_id"])
            else:
                by_qid[qid] = a["source_id"]
    by_decl: dict[str, str] = {}
    for sid in sorted(gold_declarations or {}):
        if sid not in uf.parent:
            continue
        for key in sorted(gold_declarations[sid]):
            if key in by_decl:
                uf.union(by_decl[key], sid)
            else:
                by_decl[key] = sid
    groups: dict[str, list[str]] = {}
    for sid in ids:
        groups.setdefault(uf.find(sid), []).append(sid)
    return {cluster_key(m): sorted(m) for m in groups.values()}


def split(cluster_map: dict[str, list[str]], *, salt: str,
          fractions: dict[str, float]) -> dict[str, str]:
    """``{cluster id: split}``: the cluster's own salted hash picks its split.

    ``u = sha256(salt:cluster id) / 2**256`` falls in one of the cumulative
    shares dev, eval, test.  A cluster's split is a function of its members
    alone, so adding an article never moves another cluster, and a merge moves
    only the merged one.  The shares hold in expectation, not exactly: 40/40 is
    the target of a bench v0 draw, and the build prints what it got.
    """
    order = [s for s in ("dev", "eval", "test") if fractions.get(s, 0) > 0]
    if not order:
        raise CfError("no split has a positive share")
    total = sum(fractions[s] for s in order)
    bounds, running = [], 0.0
    for s in order:
        running += fractions[s] / total
        bounds.append((running, s))
    out: dict[str, str] = {}
    for cid in sorted(cluster_map):
        u = int(hashlib.sha256(f"{salt}:{cid}".encode("utf-8")).hexdigest(), 16) / float(1 << 256)
        out[cid] = next((s for bound, s in bounds if u < bound), order[-1])
    return out


def article_splits(cluster_map: dict[str, list[str]], splits: dict[str, str]) -> dict[str, str]:
    return {sid: splits[cid] for cid, members in cluster_map.items() for sid in members}


def article_clusters(cluster_map: dict[str, list[str]]) -> dict[str, str]:
    return {sid: cid for cid, members in cluster_map.items() for sid in members}


# --------------------------------------------------------------------------
# the G3 span sample
# --------------------------------------------------------------------------

def _section_of(sections: list[dict], start: int) -> tuple[list, str | None]:
    for section in sections or []:
        a, b = section.get("span") or (0, 0)
        if a <= start < b:
            return list(section.get("path") or []), section.get("fragment")
    return [], None


def span_frame(layer: str, paragraphs: list[dict], sections: list[dict]) -> list[dict]:
    """The blocks a span may be drawn from: inside the layer, at least four words."""
    out = []
    for block in paragraphs or []:
        a, b = (int(x) for x in block.get("span") or (0, 0))
        if not (0 <= a < b <= len(layer)):
            continue
        if len(word_spans(layer, a, b)) < MIN_SPAN_WORDS:
            continue
        path, fragment = _section_of(sections, a)
        out.append({"span": [a, b], "section": block.get("section") or path,
                    "fragment": fragment, "block": block.get("block"),
                    "math": bool(block.get("math"))})
    return out


def span_id(source_id: str, span: list[int]) -> str:
    return "g3-" + hashlib.sha256(f"{source_id}\n{span[0]}\n{span[1]}".encode("utf-8")).hexdigest()[:12]


def sample_spans(source_id: str, frame: list[dict], k: int, seed: int,
                 pi_article: float) -> list[dict]:
    """``k`` blocks of one article, uniformly, with their inclusion probability."""
    if not frame:
        return []
    rng = random.Random(f"{seed}:spans:{source_id}")
    chosen = sorted(rng.sample(range(len(frame)), min(k, len(frame))))
    pi_span = min(k, len(frame)) / len(frame)
    return [{"span_id": span_id(source_id, frame[i]["span"]), "source_id": source_id,
             **frame[i], "pi_span": pi_span, "pi_article": pi_article} for i in chosen]


def check_spans_disjoint(layer: str, spans: list[dict]) -> None:
    """Two sampled spans of one article share no word (blocks do not nest)."""
    for i, one in enumerate(spans):
        for two in spans[i + 1:]:
            if overlap_words(layer, tuple(one["span"]), tuple(two["span"])):
                raise CfError(f"{one['source_id']}: sampled spans {one['span']} and {two['span']} "
                              f"overlap; paragraphs.json blocks must not nest")
