"""Fetch the live errata page with curl and keep the raw HTML.

Ported from: nothing on disk -- the campaign's ``adam-detector/errata.md`` was a
hand summary produced by a summarising fetch, and it silently dropped the
Definition 1.5.11 entry (design/critique.json, corrections: "errata must be
fetched with a raw HTTP client (curl), never the summarising fetch tool").

So this module:

* fetches with ``curl`` and stores the page verbatim (markup and all, decoded
  to UTF-8) in ``errata.raw.html``;
* renders a deterministic ``errata.md`` from those bytes with a stdlib parser --
  no model, no summary, nothing dropped;
* stamps the fetch date, the http status, the raw sha256 and the workspace's
  ``source_sha`` into ``errata.json`` so a stale errata file is visible.

Idempotent: an unchanged page leaves the files alone and reports ``changed:
false``; a changed page rewrites them and records ``previous_sha256``.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser

from .compat import read_json, sha256_text, utcnow, write_json, write_text

_SKIP_TAGS = {"script", "style", "noscript", "head", "svg"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "ul", "ol", "table", "tr", "blockquote", "pre", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "br",
}


class _Markdown(HTMLParser):
    """A small, deterministic HTML -> Markdown renderer (stdlib only)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._list: list[str] = []
        self._href: str | None = None
        self._line: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _flush(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self._line)).strip()
        self._line = []
        if text:
            self.out.append(text)

    def _emit(self, text: str) -> None:
        self._line.append(text)

    # -- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if re.fullmatch(r"h[1-6]", tag):
            self._emit("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._emit("  " * max(0, len(self._list) - 1) + "- ")
        elif tag in ("ul", "ol"):
            self._list.append(tag)
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._emit("[")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "hr":
            self.out.append("---")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a":
            self._emit("](%s)" % (self._href or ""))
            self._href = None
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("ul", "ol"):
            if self._list:
                self._list.pop()
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._emit(data.replace("\r", " ").replace("\n", " "))

    def close(self):  # type: ignore[override]
        super().close()
        self._flush()

    def markdown(self) -> str:
        body: list[str] = []
        for block in self.out:
            if body and body[-1] == block:
                continue
            body.append(block)
        return "\n\n".join(body).strip() + "\n"


def html_to_markdown(source: str) -> str:
    """Render HTML as Markdown deterministically (no model, nothing dropped)."""
    parser = _Markdown()
    parser.feed(source)
    parser.close()
    return parser.markdown()


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def stable_text(source: str) -> str:
    """The part of the page whose change means the ERRATA changed.

    HTML comments are dropped and whitespace collapsed: WordPress appends a
    batcache footer ("generated 17 seconds ago", "expires in 283 seconds") that
    differs on every fetch, and without this every ``cf sources refresh`` would
    rewrite errata.md and claim the errata had changed.
    """
    return re.sub(r"\s+", " ", _COMMENT.sub("", source)).strip()


def _decode(raw: bytes) -> str:
    m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', raw[:4096])
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def curl(url: str, *, timeout: int = 60) -> tuple[bytes, int]:
    """GET ``url`` with curl; returns (body bytes, http status)."""
    if not re.match(r"^https?://", url):
        raise ValueError("errata url must be http(s): %r" % url)
    exe = shutil.which("curl")
    if exe is None:
        raise RuntimeError("curl not found")
    fd, tmp = tempfile.mkstemp(prefix="cf-errata-", suffix=".html")
    os.close(fd)
    try:
        proc = subprocess.run(
            [exe, "-sS", "-L", "--max-time", str(timeout), "-o", tmp, "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError("curl failed (%s): %s" % (proc.returncode, proc.stderr.strip()))
        with open(tmp, "rb") as fh:
            body = fh.read()
        try:
            status = int(proc.stdout.strip()[-3:])
        except ValueError:
            status = 0
        return body, status
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def fetch(
    url: str,
    out_dir: str,
    *,
    source_id: str = "",
    source_sha: str = "",
    offset: int | None = None,
    timeout: int = 60,
    force: bool = False,
) -> dict:
    """Fetch the errata page into ``sources/<id>/errata.{raw.html,md,json}``."""
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "errata.raw.html")
    md_path = os.path.join(out_dir, "errata.md")
    meta_path = os.path.join(out_dir, "errata.json")
    previous = read_json(meta_path, default={}) or {}

    body, status = curl(url, timeout=timeout)
    if status >= 400 or not body:
        raise RuntimeError("errata fetch returned HTTP %s for %s" % (status, url))
    source = _decode(body)
    digest = sha256_text(source)
    content_digest = sha256_text(stable_text(source))
    changed = (
        force
        or previous.get("content_sha256") != content_digest
        or not os.path.exists(md_path)
        or not os.path.exists(raw_path)
    )
    meta = {
        "url": url,
        "fetched_at": utcnow(),
        "http_status": status,
        "sha256": digest,
        "content_sha256": content_digest,
        "bytes": len(body),
        "source_id": source_id,
        "source_sha": source_sha,
        "printed_page_offset": offset,
        "changed": changed,
        "previous_sha256": previous.get("sha256", ""),
        "previous_content_sha256": previous.get("content_sha256", ""),
        "entries": None,
        "renderer": "sources/fetch_errata.py html_to_markdown (stdlib; never a summarising fetch)",
        "first_fetched_at": previous.get("first_fetched_at") or utcnow(),
    }
    if changed:
        write_text(raw_path, source)
        body_md = html_to_markdown(source)
        header = [
            "# Errata (fetched verbatim, not summarised)",
            "",
            "- source: %s" % url,
            "- fetched: %s" % meta["fetched_at"],
            "- raw sha256: %s" % digest,
            "- content sha256 (comments stripped): %s" % content_digest,
            "- source: %s" % (source_id or "(unnamed)"),
            "- source sha256: %s" % (source_sha or "(unpinned)"),
        ]
        if offset is not None:
            header.append("- page numbers below are PRINTED pages; pdf page = printed + %d" % offset)
        header += [
            "- the whole page is rendered, navigation and all: an erratum has been lost before "
            "(Definition 1.5.11) by keeping only what looked relevant",
            "",
            "---",
            "",
        ]
        write_text(md_path, "\n".join(header) + body_md)
        meta["entries"] = sum(1 for line in body_md.splitlines() if line.startswith("- "))
    else:
        meta["entries"] = previous.get("entries")
    write_json(meta_path, meta)
    return meta
