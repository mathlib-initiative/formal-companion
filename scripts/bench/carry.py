"""Gold across a re-cut: which label rows keep their labels, and which are relabelled.

New code, implementing docs/proposals/statement-cut.md section 4.4 ("bench gold") in
the format references/locate-bench.md section 7 fixes.  A gold row is labelled on a
span of its article's layer, and the score joins it to a run's passages by the words
they share; when the cut changes the passages over that span, a label judged against
the old ones no longer says what it said (a "formalized" on a three-statement passage
joins only the statement that holds its quote).  So, over the carry-over map between
two inventory runs (``locate.carry``), on the layer the gold is labelled on:

* a gold row is **carried** when the passages over its span are the same before and
  after: every old passage that shares a character with it is ``unchanged``, and the
  new passages that share a character with it are exactly those passages, at the same
  spans.  A span that lies in one passage of the old cut is carried exactly when that
  passage is unchanged (the proposal's case); a span over several passages, or over
  none, is carried when none of them moved, and is never placed by a guess;
* any other row is **relabelled**, with the old passages over it and their fates, the
  new passages over it, and the reason;
* a page the map refused or that one run alone holds, or whose cuts are not both on
  the layer the gold is labelled on (a new revision or rendering: the bench scores
  only runs on the layers it pins, so its gold is anchored there again, never
  carried), relabels its rows with the reason;
* across a labeler bump (the gold manifest's labeler version is not this checkout's
  ``bench.gold.LABELER_VERSION``) every row is relabelled, whatever its passages, and
  the report says so: a label is kept only while the labeler that labels it is the
  same.

It edits no label file -- a gold change is a new manifest version, made by the
relabel -- and writes one report, ``gold/carry-<n>.json`` for gold manifest version
``n``, with no time in it: a re-run over the same gold and the same two runs writes
the same bytes.  A row without a span (a G1 entry scored at article level) joins no
passage, so no cut moves it; it is counted, not listed.
"""
from __future__ import annotations

from typing import Any

from cflib import vocab
from cflib.errors import CfError
from cflib.jsonio import read_json, write_json

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


def _new_passages(page: dict) -> list[dict]:
    """Every passage of the page's new cut, ``{passage_key, item, span}``, by span."""
    found = {(tuple(n["span"]), n["passage_key"]): n
             for e in page["passages"] for n in e["into"]}
    for n in page["new_passages"]:
        found[(tuple(n["span"]), n["passage_key"])] = n
    return [{"passage_key": n["passage_key"], "item": n["item"], "span": list(n["span"])}
            for _, n in sorted(found.items())]


class _Pages:
    """The map's pages as gold rows look them up, with the reason a page places nothing."""

    def __init__(self, carry_doc: dict):
        self.pages = {p["pageid"]: p for p in carry_doc.get("pages") or []}
        self.news = {pageid: _new_passages(p) for pageid, p in self.pages.items()}
        self.why: dict[int, str] = {r["pageid"]: f"the carry map refused page {r['pageid']}: "
                                                 f"{r['reason']}"
                                    for r in carry_doc.get("refused") or []}
        for side, other in (("old", carry_doc["new"]["inventory_run"]),
                            ("new", carry_doc["old"]["inventory_run"])):
            for pageid in (carry_doc.get("unpaired") or {}).get(side) or []:
                self.why[pageid] = f"{other} does not hold page {pageid}"

    def judge(self, row: dict, article: dict | None) -> tuple[bool, list[dict], list[dict], str]:
        """``(the passages over it are unchanged, old passages, new passages, why not)``."""
        span = _span(row.get("span"))
        if article is None:
            return False, [], [], f"{row.get('source_id')} is not an article of the bench's frame"
        pageid = article["pageid"]
        page = self.pages.get(pageid)
        if page is None:
            return False, [], [], self.why.get(pageid,
                                               f"neither inventory run holds page {pageid}")
        layer = article["layer_sha256"]
        if page["old"]["layer_sha256"] != layer or page["new"]["layer_sha256"] != layer:
            return False, [], [], (
                f"the gold is labelled on layer {str(layer)[:12]}…, and the old cut was made on "
                f"{page['old']['layer_sha256'][:12]}… and the new one on "
                f"{page['new']['layer_sha256'][:12]}…: the bench scores only runs on the layers "
                f"it pins, so gold on another is anchored again, not carried")
        olds = [{"passage_key": e["passage_key"], "item": e["item"], "span": list(e["span"]),
                 "fate": e["fate"]}
                for e in page["passages"] if _overlaps(e["span"], span)]
        news = [n for n in self.news[pageid] if _overlaps(n["span"], span)]
        moved = [o for o in olds if o["fate"] != vocab.CARRY_UNCHANGED]
        if moved:
            return False, olds, news, (f"the old passage {moved[0]['item']} over it is "
                                       f"{moved[0]['fate']}")
        before = {(o["passage_key"], tuple(o["span"])) for o in olds}
        extra = [n for n in news if (n["passage_key"], tuple(n["span"])) not in before]
        if extra:
            return False, olds, news, (f"the new passage {extra[0]['item']} over it is not one "
                                       f"of the old cut's")
        if len(news) != len(olds):
            return False, olds, news, "an old passage over it is elsewhere in the new cut"
        return True, olds, news, ""


def carry_gold(layout: BenchLayout, bench: dict, carry_doc: dict) -> dict:
    """The ``cf-bench-carry/1`` report of the bench's newest frozen gold over one map."""
    version, manifest = gold_mod.latest_manifest(layout)
    gold_mod.verify_frozen(layout, manifest, canary=bench.get("canary"))
    articles = _articles(layout)
    pages = _Pages(carry_doc)
    gold_version = (manifest.get("labeler") or {}).get("version")
    bumped = gold_version != gold_mod.LABELER_VERSION
    bump = (f"the gold is labelled by {gold_version or 'an unrecorded labeler'}, and this "
            f"checkout labels by {gold_mod.LABELER_VERSION}")
    rows, counts = [], {}
    for tier in sorted(LABEL_FILES):
        info = (manifest.get("tiers") or {}).get(tier)
        if not info or not info.get("file"):
            continue
        tally = {"rows": 0, "carried": 0, "relabel": 0, "no_span": 0}
        for row in gold_mod.read_labels(layout, tier):
            if _span(row.get("span")) is None:
                tally["no_span"] += 1
                continue
            same, olds, news, why = pages.judge(row, articles.get(str(row.get("source_id"))))
            carried = same and not bumped
            tally["rows"] += 1
            tally["carried" if carried else "relabel"] += 1
            rows.append({"tier": tier, "gold_id": row["gold_id"], "source_id": row["source_id"],
                         "span": list(row["span"]), "carried": carried, "passages": olds,
                         "into": news, "reason": None if carried else (why or bump)})
        counts[tier] = tally
    total = {k: sum(t[k] for t in counts.values())
             for k in ("rows", "carried", "relabel", "no_span")}
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
