#!/usr/bin/env python3
"""Prototype: content-addressed ids for anonymous Lean declarations.

Read-only against the repos it scans (it only runs `git show` / `git ls-tree`
/ `git log` and reads files).

Scheme
------
  example id   = "example:"  + sha256(norm(stmt))[:12]   (+ "~k" for the k-th, k>=2,
                                                          textual duplicate in one file)
  anon instance= "instance:" + sha256(norm(stmt))[:12]   (same rule)
  private decl = "private:"  + <module> + ":" + <declared full name>   (Lean guarantees
                                                          uniqueness per module)

  stmt  = source text from the `example`/`instance` keyword up to the first
          `:=` (or a top-level `where` / `|` for instances) at bracket depth 0,
          with every comment (including the docstring) removed.
  norm  = whitespace runs collapsed to one space, ends stripped.

Usage
-----
  anon_ids.py scan  <repo> <subdir> [--rev REV] [--json OUT]
  anon_ids.py diff  <repo> <subdir> <REV_A> <REV_B>
  anon_ids.py history <repo> <subdir>          # first-parent walk, all commits
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys

KW = ("example", "instance", "theorem", "lemma", "def", "abbrev", "structure",
      "class", "inductive", "opaque", "axiom")
MODS = ("private", "protected", "noncomputable", "unsafe", "partial", "nonrec")
DECL_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t\n]*)*"
    r"(?P<mods>(?:(?:" + "|".join(MODS) + r")[ \t]+)*)"
    r"(?P<kw>" + "|".join(KW) + r")(?![\w'!?.])"
    r"(?P<rest>.*)$", re.M)
NAME_RE = re.compile(r"[ \t]+(?P<name>[^\s:({\[⦃]+)")
NS_RE = re.compile(r"^[ \t]*namespace[ \t]+(\S+)", re.M)
SEC_RE = re.compile(r"^[ \t]*(?:noncomputable[ \t]+)?section\b[ \t]*(\S*)", re.M)
END_RE = re.compile(r"^[ \t]*end\b[ \t]*(\S*)", re.M)
ITEM_RE = re.compile(
    r"\b(Definition|Lemma|Theorem|Proposition|Corollary|Exercise|Example|Remark|"
    r"Figure|Equation|Principle|Conjecture)s?\s+\d+(?:\.\d+)+[a-z]?|\(\d+\.\d+(?:\.\d+)?\)|"
    r"\bp(?:p)?\.\s*\d+|§\s*\d+(?:\.\d+)*")
# declared-id override: measure_theory's existing `example:<slug>` comment tag,
# or a docstring/comment line `lean-companion id: <id>`.
DECLARED_RE = re.compile(r"`((?:example|instance):[A-Za-z0-9._-]+)`|lean-companion id:\s*([A-Za-z0-9:._~-]+)")
OPEN_BR = "([{⟨⦃⟦"
CLOSE_BR = ")]}⟩⦄⟧"


def strip_comments(text: str):
    """Return (code, comments). code has comment characters replaced by spaces
    (newlines kept) so offsets and line numbers are preserved.  comments is a
    list of (start, end, kind) with kind in {'doc','moddoc','block','line'}."""
    out = list(text)
    comments = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/-", i):
            kind = "doc" if text.startswith("/--", i) else "moddoc" if text.startswith("/-!", i) else "block"
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/-", j):
                    depth += 1; j += 2
                elif text.startswith("-/", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            comments.append((i, j, kind))
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j, "line"))
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "'" and i + 2 < n and text[i + 2] == "'" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'.")):
            i += 3  # char literal 'x'
            continue
        i += 1
    return "".join(out), comments


IDENT_CH = "_'!?"


def _tok_at(code, i, word):
    n = len(word)
    if not code.startswith(word, i):
        return False
    before = code[i - 1] if i > 0 else " "
    after = code[i + n] if i + n < len(code) else " "
    return not (before.isalnum() or before in IDENT_CH or before == ".") and \
        not (after.isalnum() or after in IDENT_CH)


def command_end(code: str, start: int) -> int:
    """End of the command: the next line that starts at column 0 with code."""
    i = code.find("\n", start)
    while i != -1:
        j = i + 1
        if j < len(code) and code[j] not in " \t\n":
            return i
        i = code.find("\n", j)
    return len(code)


def statement_end(code: str, start: int, kw: str):
    """Return (end, typed).  typed=False when there is no top-level type
    ascription (`example (d : N) := t`): then the whole command is the
    statement, because the term determines the proposition."""
    limit = command_end(code, start)
    depth, i = 0, start + len(kw)
    typed = False
    pending = 0          # let/have binders in the type awaiting their :=
    while i < limit:
        c = code[i]
        if c in OPEN_BR:
            depth += 1
        elif c in CLOSE_BR:
            depth = max(0, depth - 1)
        elif depth == 0:
            if code.startswith(":=", i):
                if typed and pending:
                    pending -= 1; i += 2; continue
                return (i, True) if typed else (limit, False)
            if c == ":" and not typed:
                typed = True
            elif typed and any(_tok_at(code, i, w) for w in ("let", "have", "letI", "haveI", "let_fun")):
                pending += 1
            elif kw == "instance" and typed and (_tok_at(code, i, "where") or code.startswith("\n  | ", i - 0)):
                return i, True
        i += 1
    return limit, typed


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def h12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def attached_comment(text: str, comments, decl_start: int):
    """Docstring directly attached (only whitespace/attributes between), and the
    nearest preceding comment of any kind with no code between it and the decl."""
    doc = None; prev = None
    for (a, b, kind) in reversed(comments):
        if b > decl_start:
            continue
        gap = text[b:decl_start]
        gap_code = re.sub(r"@\[[^\]]*\]", "", gap)
        if gap_code.strip() == "":
            if kind == "doc" and doc is None and prev is None:
                doc = text[a:b]
            if prev is None:
                prev = (kind, text[a:b])
            continue
        # allow a chain of adjacent comments; stop at first code
        break
    return doc, prev


def module_of(path: str) -> str:
    return path[:-5].replace("/", ".") if path.endswith(".lean") else path


def scan_text(text: str, rel: str):
    code, comments = strip_comments(text)
    # namespace tracking on code
    events = []
    for m in NS_RE.finditer(code):
        events.append((m.start(), "ns", m.group(1)))
    for m in SEC_RE.finditer(code):
        events.append((m.start(), "sec", m.group(1)))
    for m in END_RE.finditer(code):
        events.append((m.start(), "end", m.group(1)))
    decls = []
    for m in DECL_RE.finditer(code):
        kw = m.group("kw")
        # skip decls nested in an expression line such as `  example` inside a proof:
        rest = m.group("rest")
        name_m = NAME_RE.match(rest)
        name = None
        if name_m and kw != "example":
            cand = name_m.group("name")
            if cand not in (":", "where") and not cand.startswith("(") and not cand.startswith("["):
                name = cand
        if kw == "instance" and rest.lstrip().startswith("(priority"):
            pr = re.match(r"\s*\(priority[^)]*\)\s*(?P<name>[^\s:({\[⦃]+)?", rest)
            name = pr.group("name") if pr and pr.group("name") and pr.group("name") != ":" else None
        kw_pos = m.start("kw")
        decls.append(dict(start=m.start(), kw_pos=kw_pos, kw=kw, name=name,
                          mods=m.group("mods").split()))
    # namespace stack at each decl
    evs = sorted(events)
    out = []
    ns_stack = []  # list of (kind, parts)
    ei = 0
    for d in decls:
        while ei < len(evs) and evs[ei][0] < d["start"]:
            pos, kind, arg = evs[ei]; ei += 1
            if kind == "ns":
                ns_stack.append(("ns", arg.split(".")))
            elif kind == "sec":
                ns_stack.append(("sec", []))
            elif kind == "end" and ns_stack:
                ns_stack.pop()
        ns = ".".join(p for k, parts in ns_stack for p in parts)
        line = code.count("\n", 0, d["kw_pos"]) + 1
        rec = dict(file=rel, module=module_of(rel), line=line, kw=d["kw"], name=d["name"],
                   private="private" in d["mods"], namespace=ns)
        if d["kw"] in ("example", "instance") and d["name"] is None:
            end, typed = statement_end(code, d["kw_pos"], d["kw"])
            stmt = norm(code[d["kw_pos"]:end])
            rec["stmt"] = stmt
            rec["typed"] = typed
            rec["hash"] = h12(stmt)
            doc, prev = attached_comment(text, comments, d["start"])
            rec["has_doc"] = doc is not None
            rec["doc_item"] = bool(doc and ITEM_RE.search(doc))
            rec["prev_kind"] = prev[0] if prev else None
            rec["prev_item"] = bool(prev and ITEM_RE.search(prev[1]))
            dm = prev and DECLARED_RE.search(prev[1])
            rec["declared_id"] = (dm.group(1) or dm.group(2)) if dm else None
            # nearest preceding /-! header naming an item (section context)
            hdr = [c for c in comments if c[2] == "moddoc" and c[1] <= d["start"]]
            rec["section_item"] = bool(hdr and ITEM_RE.search(text[hdr[-1][0]:hdr[-1][1]]))
        elif rec["private"] and d["name"]:
            full = d["name"][len("_root_."):] if d["name"].startswith("_root_.") else \
                ".".join(x for x in (ns, d["name"]) if x)
            rec["full_name"] = full
        out.append(rec)
    # ordinals among same-file textual duplicates
    seen = collections.Counter()
    for r in out:
        if "hash" in r:
            key = (r["kw"], r["hash"])
            seen[key] += 1
            k = seen[key]
            r["id"] = f"{r['kw']}:{r['hash']}" + (f"~{k}" if k > 1 else "")
            r["pos_id"] = f"{r['kw']}@{rel}:{r['line']}"
        elif r.get("full_name"):
            r["id"] = f"private:{r['module']}:{r['full_name']}"
    return out


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout


def files_at(repo, subdir, rev):
    if rev is None:
        import pathlib
        root = pathlib.Path(repo)
        return {str(p.relative_to(root)): p.read_text(encoding="utf-8")
                for p in sorted((root / subdir).rglob("*.lean"))}
    names = git(repo, "ls-tree", "-r", "--name-only", rev, "--", subdir).decode().split("\n")
    res = {}
    for nm in names:
        if nm.endswith(".lean"):
            res[nm] = git(repo, "show", f"{rev}:{nm}").decode("utf-8")
    return res


def scan_repo(repo, subdir, rev=None):
    recs = []
    for rel, text in files_at(repo, subdir, rev).items():
        recs.extend(scan_text(text, rel))
    return recs


def summary(recs):
    ex = [r for r in recs if r["kw"] == "example" and "hash" in r]
    ai = [r for r in recs if r["kw"] == "instance" and "hash" in r]
    pv = [r for r in recs if r.get("full_name")]
    def coll(rs):
        by_file = collections.Counter((r["file"], r["hash"]) for r in rs)
        same_file_groups = {k: v for k, v in by_file.items() if v > 1}
        by_hash_files = collections.defaultdict(set)
        for r in rs:
            by_hash_files[r["hash"]].add(r["file"])
        cross = {h: sorted(f) for h, f in by_hash_files.items() if len(f) > 1}
        return dict(same_file_dup_groups=len(same_file_groups),
                    same_file_dup_members=sum(same_file_groups.values()),
                    examples_needing_ordinal=sum(v - 1 for v in same_file_groups.values()),
                    cross_file_hashes=len(cross),
                    cross_file_members=sum(1 for r in rs if r["hash"] in cross),
                    distinct_hashes=len({r['hash'] for r in rs}),
                    distinct_ids_global=len({r['id'] for r in rs}),
                    distinct_ids_module_qualified=len({(r['module'], r['id']) for r in rs}))
    pv_names = collections.Counter(r["full_name"] for r in pv)
    pv_mod = collections.Counter((r["module"], r["full_name"]) for r in pv)
    return dict(
        files=len({r["file"] for r in recs}),
        examples=len(ex),
        examples_files=len({r["file"] for r in ex}),
        example_collisions=coll(ex),
        examples_untyped_whole_command_hashed=sum(not r["typed"] for r in ex),
        examples_with_docstring=sum(r["has_doc"] for r in ex),
        examples_docstring_names_item=sum(r["doc_item"] for r in ex),
        examples_prev_comment=sum(r["prev_kind"] is not None for r in ex),
        examples_prev_comment_kinds=dict(collections.Counter(r["prev_kind"] for r in ex)),
        examples_prev_comment_names_item=sum(r["prev_item"] for r in ex),
        examples_doc_or_prev_names_item=sum(r["doc_item"] or r["prev_item"] for r in ex),
        examples_section_header_names_item=sum(r["section_item"] for r in ex),
        examples_with_declared_id=sum(bool(r["declared_id"]) for r in ex),
        anon_instances=len(ai),
        anon_instance_collisions=coll(ai) if ai else None,
        private_decls=len(pv),
        private_distinct_module_name=len(pv_mod),
        private_dup_within_module=sum(1 for v in pv_mod.values() if v > 1),
        private_same_name_across_modules=sum(1 for v in pv_names.values() if v > 1),
    )


def diff(recs_a, recs_b):
    def idset(rs, key):
        return collections.Counter((r["file"], r[key]) for r in rs if "hash" in r)
    out = {}
    for key in ("id", "pos_id"):
        a, b = idset(recs_a, key), idset(recs_b, key)
        out[key] = dict(total_a=sum(a.values()), total_b=sum(b.values()),
                        retired=sum((a - b).values()), born=sum((b - a).values()))
    # file-free id comparison (moves between files keep a hash id)
    a = collections.Counter(r["id"] for r in recs_a if "hash" in r)
    b = collections.Counter(r["id"] for r in recs_b if "hash" in r)
    out["id_file_free"] = dict(retired=sum((a - b).values()), born=sum((b - a).values()))
    pa = {r["id"] for r in recs_a if r.get("full_name")}
    pb = {r["id"] for r in recs_b if r.get("full_name")}
    out["private"] = dict(total_a=len(pa), total_b=len(pb), retired=len(pa - pb), born=len(pb - pa))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan", "diff", "history"])
    ap.add_argument("repo"); ap.add_argument("subdir")
    ap.add_argument("revs", nargs="*")
    ap.add_argument("--rev"); ap.add_argument("--json")
    a = ap.parse_args()
    if a.mode == "scan":
        recs = scan_repo(a.repo, a.subdir, a.rev)
        s = summary(recs)
        print(json.dumps(s, indent=1, ensure_ascii=False))
        if a.json:
            json.dump(dict(summary=s, records=recs), open(a.json, "w"), indent=1, ensure_ascii=False)
    elif a.mode == "diff":
        ra = scan_repo(a.repo, a.subdir, a.revs[0]); rb = scan_repo(a.repo, a.subdir, a.revs[1])
        print(json.dumps(diff(ra, rb), indent=1))
    else:
        revs = git(a.repo, "log", "--first-parent", "--format=%h", "--reverse", "--", a.subdir).decode().split()
        tot = collections.Counter(); steps = []
        prev = None
        for rev in revs:
            cur = scan_repo(a.repo, a.subdir, rev)
            if prev is not None:
                d = diff(prev[1], cur)
                steps.append(dict(frm=prev[0], to=rev, **{k: v for k, v in d.items()}))
                for k in ("id", "pos_id", "id_file_free", "private"):
                    tot[k + ".retired"] += d[k]["retired"]; tot[k + ".born"] += d[k]["born"]
            prev = (rev, cur)
        print(json.dumps(dict(commits=len(revs), totals=tot,
                              steps_where_pos_changed_but_hash_did_not=sum(
                                  1 for s in steps if s["pos_id"]["retired"] and not s["id"]["retired"]),
                              steps_with_any_hash_change=sum(1 for s in steps if s["id"]["retired"] or s["id"]["born"]),
                              steps_with_any_pos_change=sum(1 for s in steps if s["pos_id"]["retired"] or s["pos_id"]["born"]),
                              ), indent=1))
        if a.json:
            json.dump(steps, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
