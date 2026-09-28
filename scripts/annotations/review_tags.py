"""Tag review: a document's key links against the defect shapes the audits found.

New code, implementing the "tag review reproduces known answers" half of
docs/proposals/pdf-to-companion.md section 5.4 (milestone M3), read-only over
an annotation document (docs/annotations.md).  Each check is one defect the
audits of the two worked companions met, so a checker that finds nothing on a
document where the defect is planted is wrong, not the document:

* ``attribution_gap``: an item keyed only to where it sits.  It has no ``key``
  link to a declaration, and no coverage class that excludes one says why
  (``not_formalized``, ``out_of_scope``, ``support_only``, ``example_only``);
  a ``formalized`` or ``partial`` item with no key is the very defect, a
  claimed formalization no key names.  Before the PDF-overlay companion's
  section 1.7 was curated, every item of that section was in this state: the
  adapter could key it only to its in-text page anchor.
* ``key_on_structure_member``: a ``key`` link, or a ``primary`` membership,
  whose declaration is a ``field`` or ``projection``.  A structure member states
  no item; its structure does.  The overlay companion's first viewer PR counted
  142 primary edges of that shape.
* ``borrowed_key``: a numbered item whose every key declaration is another
  item's key declaration, where one of those other items (the owner) also has
  a key of its own that this item lacks, so the item states nothing of its
  own; and, for an item with no key link that falls back to its clusters, a
  cluster whose primary declarations are all another item's keys.  The overlay
  companion's curation of batches a to c recorded such remarks as "formalised
  only by declarations that state a neighbouring item".
* ``shared_key``: the same shape with no owner: items keyed only to the same
  declarations, none holding a key the other lacks (two definitions one
  structure states, an equation and the proposition that proves it).  Each
  such item is listed once here, and neither is called a borrower.
* ``hidden_spelling``: a book-content docstring tag (a ``key`` or
  ``attributed`` link) spelled so that the literate site's fold script hides
  its declaration as API: it matches the script's proof-plumbing rule
  (``In-text proof``), or it carries none of the book anchors the script keeps.
  Before the docstring-tagged companion's docs PR, fourteen book-content tags
  were spelled ``In-text proof (book p. N)`` and folded away.  A ``helper``
  link is proof plumbing the script is right to hide, so it is not reported.
* ``adjudicated_attribution_gap``: the verdicts the document itself carries
  with the viewer verdict ``attribution_gap``: the adjudication ledger's rows
  of that class, when the export carried them.

The patterns of the fold script are the defaults and both are arguments, so
another site's rules can be checked against the same document.
"""
from __future__ import annotations

import collections
import re
from typing import Any

#: The book anchors the literate site's fold script keeps a declaration for (its ``KEEP_RE``).
KEEP_PATTERN = (r"(?:(?:Exercise|Definition|Theorem|Lemma|Proposition|Corollary|Remark|Example|Claim)"
                r"\s+[0-9]|In-text,|book pp?\.\s?[0-9])")
#: The spelling the same script hides as proof plumbing (its ``PROOF_RE``).
HIDE_PATTERN = r"\bIn-text proof"
STRUCTURE_MEMBER_KINDS = frozenset({"field", "projection"})
#: Coverage classes that say why an item has no key link (docs/annotations.md, I5).
UNKEYED_COVERAGE = frozenset({"not_formalized", "out_of_scope", "support_only", "example_only"})
#: The link roles a book-content tag carries; a helper tag is proof plumbing.
BOOK_CONTENT_ROLES = ("key", "attributed")
CHECKS = ("attribution_gap", "key_on_structure_member", "borrowed_key", "shared_key", "hidden_spelling",
          "adjudicated_attribution_gap")


def _by_id(doc: dict, name: str) -> dict[str, dict]:
    return {r["id"]: r for r in doc.get(name, []) or []}


def _live_links(doc: dict) -> list[dict]:
    return [ln for ln in doc.get("links", []) or [] if not ln.get("orphaned")]


def check_attribution_gap(doc: dict) -> list[dict]:
    """Items with no key link to a declaration and no coverage class that excludes one."""
    located = {i for loc in doc.get("locations", []) or [] for i in loc["items"]}
    keyed = {ln["item"] for ln in _live_links(doc) if ln["role"] == "key" and "declaration" in ln}
    out = []
    for item in doc.get("items", []) or []:
        if item["id"] in keyed or item.get("coverage") in UNKEYED_COVERAGE:
            continue
        anchored = item["id"] in located or "pages" in item or "quote" in item
        out.append({"item": item["id"], "form": item["form"], "kind": item["kind"],
                    "anchored": anchored, "cluster_fallback": bool(item.get("cluster_fallback"))})
        if "coverage" in item:
            out[-1]["coverage"] = item["coverage"]
    return out


def check_key_on_structure_member(doc: dict) -> dict[str, list[dict]]:
    """Key links and primary memberships whose declaration is a field or projection."""
    decls = _by_id(doc, "declarations")
    links = []
    for ln in _live_links(doc):
        d = decls.get(ln.get("declaration"))
        if ln["role"] == "key" and d is not None and d["kind"] in STRUCTURE_MEMBER_KINDS:
            links.append({"link": ln["id"], "item": ln["item"], "declaration": d["id"], "kind": d["kind"],
                          "parent": d.get("parent")})
    members = []
    for m in doc.get("memberships", []) or []:
        d = decls.get(m.get("declaration"))
        if m["role"] == "primary" and d is not None and d["kind"] in STRUCTURE_MEMBER_KINDS:
            members.append({"membership": m["id"], "cluster": m["cluster"], "declaration": d["id"],
                            "kind": d["kind"], "parent": d.get("parent")})
    return {"links": links, "memberships": members}


def _key_sharing(doc: dict) -> list[dict]:
    """Numbered items whose every key declaration (own, or the cluster's primaries) is another item's key.

    A finding ``via: links`` carries ``owned``: whether some item it shares
    with holds a key declaration this item lacks (one-way sharing, a borrower)
    rather than only keys this item has too (a mutual group).
    """
    items = _by_id(doc, "items")
    key_by_item: dict[str, set[str]] = collections.defaultdict(set)
    items_by_key: dict[str, set[str]] = collections.defaultdict(set)
    for ln in _live_links(doc):
        if ln["role"] == "key" and "declaration" in ln:
            key_by_item[ln["item"]].add(ln["declaration"])
            items_by_key[ln["declaration"]].add(ln["item"])
    primaries: dict[str, set[str]] = collections.defaultdict(set)
    for m in doc.get("memberships", []) or []:
        if m["role"] == "primary" and "declaration" in m:
            primaries[m["cluster"]].add(m["declaration"])
    out = []
    for iid, item in items.items():
        if item["form"] != "numbered":
            continue
        own = key_by_item.get(iid)
        if own:
            decls, via = own, "links"
        elif item.get("cluster_fallback"):
            decls = set().union(*(primaries.get(c, set()) for c in item.get("clusters", [])))
            via = "clusters"
        else:
            continue
        if not decls or not all(items_by_key[d] - {iid} for d in decls):
            continue
        shared = sorted(set().union(*(items_by_key[d] - {iid} for d in decls)))
        rec = {"item": iid, "via": via, "declarations": sorted(decls), "shared_with": shared}
        if via == "links":
            rec["owned"] = any(key_by_item[other] - decls for other in shared)
        out.append(rec)
    return out


def check_borrowed_key(doc: dict) -> list[dict]:
    """Items that borrow another item's key: shared one way (an owner holds more), or a cluster fallback."""
    return [{k: v for k, v in f.items() if k != "owned"} for f in _key_sharing(doc) if f.get("owned", True)]


def check_shared_key(doc: dict) -> list[dict]:
    """Items keyed only to declarations they share with items that hold nothing more: mutual groups."""
    return [{k: v for k, v in f.items() if k != "owned"} for f in _key_sharing(doc) if f.get("owned") is False]


def check_hidden_spelling(doc: dict, keep_pattern: str = KEEP_PATTERN,
                          hide_pattern: str = HIDE_PATTERN, roles=BOOK_CONTENT_ROLES) -> list[dict]:
    """Book-content docstring-tag links (``roles``) whose raw label a fold script would hide."""
    keep_re, hide_re = re.compile(keep_pattern), re.compile(hide_pattern)
    provenance = _by_id(doc, "provenance")
    out = []
    for ln in _live_links(doc):
        raw = ln.get("raw_label")
        if not raw or provenance.get(ln["provenance"], {}).get("method") != "docstring_tag":
            continue
        if ln["role"] not in roles:
            continue
        head = raw
        if ln.get("pages", {}).get("raw"):
            head += " " + ln["pages"]["raw"]
        if hide_re.search(raw):
            why = "matches the hide pattern (proof plumbing)"
        elif not keep_re.search(head):
            why = "carries no book anchor the keep pattern recognises"
        else:
            continue
        out.append({"link": ln["id"], "item": ln["item"], "declaration": ln.get("declaration"),
                    "role": ln["role"], "raw_label": raw, "why": why})
    return out


def check_adjudicated_attribution_gap(doc: dict) -> list[dict]:
    """Verdicts the document carries with the viewer verdict ``attribution_gap``."""
    out = []
    for v in doc.get("verdicts", []) or []:
        if v["verdict"] == "attribution_gap" or v.get("ledger_verdict") == "ATTRIBUTION_GAP":
            out.append({"verdict": v["id"], "item": v["item"], "status": v["status"],
                        "resolution": v.get("resolution", {}).get("state", "open"),
                        "declarations": [d["declaration"] for d in v.get("declarations", [])]})
    return out


def review(doc: dict, *, keep_pattern: str = KEEP_PATTERN, hide_pattern: str = HIDE_PATTERN) -> dict[str, Any]:
    """Every check over one document; ``counts`` summarises them."""
    member = check_key_on_structure_member(doc)
    findings = {
        "attribution_gap": check_attribution_gap(doc),
        "key_on_structure_member": member,
        "borrowed_key": check_borrowed_key(doc),
        "shared_key": check_shared_key(doc),
        "hidden_spelling": check_hidden_spelling(doc, keep_pattern, hide_pattern),
        "adjudicated_attribution_gap": check_adjudicated_attribution_gap(doc),
    }
    findings["counts"] = {
        "attribution_gap": len(findings["attribution_gap"]),
        "key_on_structure_member": {"links": len(member["links"]), "memberships": len(member["memberships"])},
        "borrowed_key": len(findings["borrowed_key"]),
        "shared_key": len(findings["shared_key"]),
        "hidden_spelling": len(findings["hidden_spelling"]),
        "adjudicated_attribution_gap": len(findings["adjudicated_attribution_gap"]),
    }
    return findings


def summary_lines(findings: dict, limit: int = 10) -> list[str]:
    """One block per check: the count, then up to ``limit`` examples."""
    lines: list[str] = []
    gap = findings["attribution_gap"]
    fallback = sum(1 for g in gap if g["cluster_fallback"])
    lines.append(f"attribution_gap: {len(gap)} items with no key link and no coverage class that excludes one "
                 f"({fallback} on a cluster fallback)")
    lines += [f"    {g['item']}" for g in gap[:limit]]
    member = findings["key_on_structure_member"]
    lines.append(f"key_on_structure_member: {len(member['links'])} key links and "
                 f"{len(member['memberships'])} primary memberships name a field or projection")
    lines += [f"    {m['link']}" for m in member["links"][:limit]]
    borrowed = findings["borrowed_key"]
    own = [b for b in borrowed if b["via"] == "links"]
    fallback = [b for b in borrowed if b["via"] == "clusters"]
    lines.append(f"borrowed_key: {len(own)} numbered items whose every key declaration is another item's that "
                 f"also states more, and {len(fallback)} with no key whose cluster fallback shows only other "
                 f"items' keys")
    lines += [f"    {b['item']} ({b['via']}) shares {', '.join(b['shared_with'][:3])}"
              for b in (own + fallback)[:limit]]
    mutual = findings["shared_key"]
    lines.append(f"shared_key: {len(mutual)} numbered items keyed only to declarations they share with items "
                 f"that hold nothing more (mutual groups, no owner)")
    lines += [f"    {b['item']} shares {', '.join(b['shared_with'][:3])}" for b in mutual[:limit]]
    hidden = findings["hidden_spelling"]
    lines.append(f"hidden_spelling: {len(hidden)} book-content docstring-tag links a fold script would hide")
    lines += [f"    {h['link']}: {h['raw_label']!r} ({h['why']})" for h in hidden[:limit]]
    adjudicated = findings["adjudicated_attribution_gap"]
    lines.append(f"adjudicated_attribution_gap: {len(adjudicated)} verdicts of that class")
    lines += [f"    {a['verdict']} ({a['resolution']})" for a in adjudicated[:limit]]
    return lines
