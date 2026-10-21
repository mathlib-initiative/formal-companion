"""Gold across a re-cut: which label rows keep their labels, which move, which are relabelled.

New code, implementing docs/proposals/statement-cut.md section 4.4 ("bench gold") in
the format references/locate-bench.md section 7 fixes.  A gold row is labelled on a
span of its article's layer, and the score joins it to a run's passages by the words
they share (``bench.metrics.join``); when the cut changes the passages over that span,
a label judged against the old ones no longer says what it said (a "formalized" on a
three-statement passage joins only the statement that holds its quote).  So, over the
carry-over map between two inventory runs (``locate.carry``), whose old cut must be on
the layer the gold is labelled on (the frame article's ``layer_sha256``):

* a gold row is **carried** (``vocab.GOLD_CARRIED``) when the new cut is on that layer
  too and the passages over its span are the same before and after: every old passage
  that shares a character with it is ``unchanged``, and the new passages that share a
  character with it are exactly those passages, at the same spans.  That is stricter
  than the proposal's letter for a span inside one old passage ("carried when that
  passage is unchanged"), and on purpose: a new passage nested inside the unchanged
  one, or the unchanged text's twin cut at another place on the page, changes the run
  passages the label is joined against, so the row is relabelled.  A span over several
  passages, or over none, is carried when none of them moved, and is never placed by a
  guess;
* a gold row is **re-anchored** (``vocab.GOLD_REANCHOR``) when the same holds on a new
  cut on another layer (a new revision or rendering): every old passage over it is
  ``unchanged`` and moved by one offset d (over no passage, d puts the span on the one
  occurrence of its text in the new layer), the new layer holds the span's text at
  ``[start + d, end + d)``, and the new passages over that are exactly the old ones,
  moved by d.  Its labels stand, and its ``anchor`` is where its span is on the new
  layer; the bench scores only runs on the layers it pins, so the move is a new gold
  version on a new frame, which no tool here writes;
* any other row is **relabelled** (``vocab.GOLD_RELABEL``), with the old passages over
  it and their fates, the new passages over it (on another layer, where the old ones
  went), and the reason; so is every row of a page the map refused, that one run alone
  holds, or whose old cut is not on the gold's layer;
* across a labeler bump (the gold manifest's labeler version is not this checkout's
  ``bench.gold.LABELER_VERSION``) every row is relabelled, whatever its passages, and
  the report says so: a label is kept only while the labeler that labels it is the
  same.

It edits no label file -- a gold change is a new manifest version, made by the
relabel -- and writes one report, ``gold/carry-<n>.json`` for gold manifest version
``n``, with no time in it: a re-run over the same gold and the same two runs writes
the same bytes.  A row without a span (a G1 entry scored at article level) joins no
passage, so no cut moves it; it is counted, not listed.  This module computes no
score, so it is no part of the scorer's identity (``bench.score.NOT_SCORING``).
"""
from __future__ import annotations

from typing import Any

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json
from locate import carry as locate_carry

from . import gold as gold_mod
from .layout import BENCH_CARRY_SCHEMA, LABEL_FILES, BenchLayout, sha_of


def _span(value: Any) -> tuple[int, int] | None:
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(x) is int for x in value) and 0 <= value[0] < value[1]):
        return value[0], value[1]
    return None


def _overlaps(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _articles(layout: BenchLayout) -> dict[str, dict]:
    """``{source_id: {pageid, layer_sha256}}`` of the frame the gold is labelled on."""
    frame = read_json(layout.frame_json)
    return {a["source_id"]: {"pageid": a.get("pageid"), "layer_sha256": a.get("layer_sha256")}
            for a in frame.get("articles") or []}


def _ref(passage: dict) -> dict:
    return {"passage_key": passage["passage_key"], "item": passage["item"],
            "span": list(passage["span"])}


def _new_passages(page: dict) -> list[dict]:
    """Every passage of the page's new cut, ``{passage_key, item, span}``, by span."""
    found = {(tuple(n["span"]), n["passage_key"]): n
             for e in page["passages"] for n in e["into"]}
    for n in page["new_passages"]:
        found[(tuple(n["span"]), n["passage_key"])] = n
    return [_ref(n) for _, n in sorted(found.items())]


class _Pages:
    """The map's pages as gold rows look them up, with the reason a page places nothing."""

    def __init__(self, carried: locate_carry.Carry):
        doc = carried.doc
        self.cuts = carried.cuts
        self.pages = {p["pageid"]: p for p in doc.get("pages") or []}
        self.news = {pageid: _new_passages(p) for pageid, p in self.pages.items()}
        self.why: dict[int, str] = {r["pageid"]: f"the carry map refused page {r['pageid']}: "
                                                 f"{r['reason']}"
                                    for r in doc.get("refused") or []}
        for side, other in (("old", doc["new"]["inventory_run"]),
                            ("new", doc["old"]["inventory_run"])):
            for pageid in (doc.get("unpaired") or {}).get(side) or []:
                self.why[pageid] = f"{other} does not hold page {pageid}"

    def judge(self, row: dict, article: dict | None
              ) -> tuple[str, list[dict], list[dict], dict | None, str]:
        """``(state, old passages over it, new passages over it, anchor, reason)``.

        ``anchor`` is ``{source_id, layer_sha256, span}`` on the new layer for a
        re-anchored row and None otherwise; ``reason`` is empty for a carried row.
        """
        relabel = vocab.GOLD_RELABEL
        span = _span(row.get("span"))
        if article is None:
            return relabel, [], [], None, (f"{row.get('source_id')} is not an article of the "
                                           f"bench's frame")
        pageid = article["pageid"]
        page = self.pages.get(pageid)
        if page is None:
            return relabel, [], [], None, self.why.get(
                pageid, f"neither inventory run holds page {pageid}")
        layer = str(article["layer_sha256"])
        if page["old"]["layer_sha256"] != layer:
            return relabel, [], [], None, (
                f"the gold is labelled on layer {layer[:12]}…, and the old cut was made on "
                f"{page['old']['layer_sha256'][:12]}…: the map says nothing of the passages "
                f"the gold was joined against")
        olds = [{**_ref(e), "fate": e["fate"], "_into": e["into"]}
                for e in page["passages"] if _overlaps(e["span"], span)]
        same = page["new"]["layer_sha256"] == layer

        def went() -> list[dict]:
            # on another layer, the new passages over the span are where its old ones went
            if same:
                return [n for n in self.news[pageid] if _overlaps(n["span"], span)]
            found = {(tuple(n["span"]), n["passage_key"]): _ref(n)
                     for o in olds for n in o["_into"]}
            return [n for _, n in sorted(found.items())]

        def shown(passages: list[dict]) -> list[dict]:
            return [{k: v for k, v in p.items() if k != "_into"} for p in passages]

        moved = [o for o in olds if o["fate"] != vocab.CARRY_UNCHANGED]
        if moved:
            return relabel, shown(olds), went(), None, (
                f"the old passage {moved[0]['item']} over it is {moved[0]['fate']}")
        shift = 0
        if not same:
            old_cut, new_cut = self.cuts[pageid]
            text = old_cut.layer[span[0]:span[1]]
            if olds:
                shifts = {o["_into"][0]["span"][0] - o["span"][0] for o in olds}
                if len(shifts) != 1:
                    return relabel, shown(olds), went(), None, (
                        "the passages over it moved apart on the new layer")
                shift = shifts.pop()
            else:
                hits = locate_carry.occurrences(new_cut.layer, text)
                if len(hits) != 1:
                    return relabel, [], [], None, (
                        f"no passage is over it, and the new layer holds its text "
                        f"{'more than once' if hits else 'nowhere'}: it is never placed by a "
                        f"guess")
                shift = hits[0] - span[0]
            if new_cut.layer[span[0] + shift:span[1] + shift] != text:
                return relabel, shown(olds), went(), None, (
                    "the new layer does not hold its text where its passages went")
        placed = (span[0] + shift, span[1] + shift)
        news = [n for n in self.news[pageid] if _overlaps(n["span"], placed)]
        before = {(o["passage_key"], o["span"][0] + shift, o["span"][1] + shift) for o in olds}
        extra = [n for n in news if (n["passage_key"], *n["span"]) not in before]
        if extra:
            return relabel, shown(olds), news, None, (f"the new passage {extra[0]['item']} over "
                                                      f"it is not one of the old cut's")
        if len(news) != len(olds):
            return relabel, shown(olds), news, None, (
                "an old passage over it is elsewhere in the new cut")
        if same:
            return vocab.GOLD_CARRIED, shown(olds), news, None, ""
        anchor = {"source_id": page["new"]["source_id"],
                  "layer_sha256": page["new"]["layer_sha256"], "span": list(placed)}
        return vocab.GOLD_REANCHOR, shown(olds), news, anchor, (
            f"the new cut is on another layer ({page['new']['layer_sha256'][:12]}… of "
            f"{page['new']['source_id']}): the labels stand, the span moves to the anchor")


def carry_gold(layout: BenchLayout, bench: dict, carried: locate_carry.Carry) -> dict:
    """The ``cf-bench-carry/1`` report of the bench's newest frozen gold over one map."""
    carry_doc = carried.doc
    version, manifest = gold_mod.latest_manifest(layout)
    gold_mod.verify_frozen(layout, manifest, canary=bench.get("canary"))
    articles = _articles(layout)
    pages = _Pages(carried)
    gold_version = (manifest.get("labeler") or {}).get("version")
    bumped = gold_version != gold_mod.LABELER_VERSION
    bump = (f"the gold is labelled by {gold_version or 'an unrecorded labeler'}, and this "
            f"checkout labels by {gold_mod.LABELER_VERSION}")
    keys = ("rows", vocab.GOLD_CARRIED, vocab.GOLD_REANCHOR, vocab.GOLD_RELABEL, "no_span")
    rows, counts = [], {}
    for tier in sorted(LABEL_FILES):
        info = (manifest.get("tiers") or {}).get(tier)
        if not info or not info.get("file"):
            continue
        tally = dict.fromkeys(keys, 0)
        for row in gold_mod.read_labels(layout, tier):
            if _span(row.get("span")) is None:
                tally["no_span"] += 1
                continue
            state, olds, news, anchor, why = pages.judge(
                row, articles.get(str(row.get("source_id"))))
            if bumped:
                state, anchor, why = vocab.GOLD_RELABEL, None, (
                    bump if state != vocab.GOLD_RELABEL else why)
            tally["rows"] += 1
            tally[state] += 1
            rows.append({"tier": tier, "gold_id": row["gold_id"], "source_id": row["source_id"],
                         "span": list(row["span"]), "state": state, "anchor": anchor,
                         "passages": olds, "into": news,
                         "reason": None if state == vocab.GOLD_CARRIED else why})
        counts[tier] = tally
    total = {k: sum(t[k] for t in counts.values()) for k in keys}
    return {"schema": BENCH_CARRY_SCHEMA, "bench": layout.bench_id,
            "gold": {"version": version, "manifest_sha256": manifest.get("manifest_sha256")},
            "labeler": {"gold": gold_version, "current": gold_mod.LABELER_VERSION,
                        "bumped": bumped},
            "from": dict(carry_doc["old"]), "to": dict(carry_doc["new"]),
            "map_sha256": sha_of(carry_doc),
            "rows": sorted(rows, key=lambda r: (r["tier"], r["gold_id"])),
            "counts": {**counts, "total": total},
            "note": f"every row is relabelled: {bump}" if bumped else None}


def write(layout: BenchLayout, doc: dict):
    """``gold/carry-<n>.json``: one report per gold version, the same bytes on a re-run."""
    if not isinstance(doc.get("gold", {}).get("version"), int):
        raise CfError("a gold carry report names the gold manifest version it read")
    return write_json(layout.gold_carry_json(doc["gold"]["version"]), doc, sort_keys=True)
