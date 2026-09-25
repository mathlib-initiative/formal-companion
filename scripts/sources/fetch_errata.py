"""Fetch the live errata page with curl and keep the raw HTML.

Ported from: nothing on disk -- the campaign's ``adam-detector/errata.md`` was a
hand summary produced by a summarising fetch, and it silently dropped the
Definition 1.5.11 entry (the design critique, corrections: "errata must be
fetched with a raw HTTP client (curl), never the summarising fetch tool").

So this module:

* fetches with ``curl`` and stores the page verbatim (markup and all, decoded
  to UTF-8) in ``errata.raw.html`` -- bounded: https only on the request and on
  every redirect, at most 3 redirects, at most ``MAX_FILESIZE``, and the
  ``url_effective`` recorded beside the sha (see :func:`curl_argv`);
* renders a deterministic ``errata.md`` from those bytes with a stdlib parser --
  no model, no summary, nothing dropped;
* stamps the fetch date, the http status, the raw sha256 and the workspace's
  ``source_sha`` into ``errata.json`` so a stale errata file is visible.

**The mathematics is content, and stripping tags used to delete all of it.** On
a WordPress page every formula is an ``<img>`` whose ``src`` is
``.../latex.php?latex=<url-encoded TeX>`` and whose ``alt`` carries the same
TeX; a tag-stripping renderer therefore kept the prose and dropped the symbols,
which is worse than dropping the entry -- an entry of the shape ``Page 57: In
Corollary 3.2.4(b), should be , and the same in the display preceding (3.9)`` (a
synthetic example) reads as English and says nothing, and a reader deciding
whether a formalisation follows the author's correction needs exactly the
symbols that were deleted.  So :func:`img_tex` recovers the TeX -- ``alt``
first, else the ``latex=`` parameter of the ``src`` -- and :func:`audit_entries`
REPORTS an entry that still looks emptied, because a silent one is the whole
defect.

The render is versioned (:data:`RENDERER_VERSION`).  A bump re-derives
``errata.md`` from the kept bytes on the next `cf sources refresh`, with no
network (:func:`rerender`), and it invalidates nothing else: no adapter reads
``errata.md``, so the extract key an adapter builds from ``key_files`` -- which
are paths inside the COMPANION repo -- cannot move when this converter changes
(``tests/test_sources_pair.py`` pins that).

Idempotent: an unchanged page rendered by this same converter leaves the files
alone and reports ``changed: false``; a changed page -- or a render by an OLDER
converter than this one -- rewrites them and records ``previous_sha256``.

Sections: ``docs/design/stage_map.md`` S3 ("The errata page") and its stage-1
row.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from html.parser import HTMLParser

from .compat import read_json, sha256_text, utcnow, write_json, write_text

_SKIP_TAGS = {"script", "style", "noscript", "head", "svg"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "ul", "ol", "table", "tr", "blockquote", "pre", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "br",
}

#: Bumped whenever this renderer would produce a DIFFERENT errata.md from the
#: same bytes.  It is stamped into errata.json, and `cf sources refresh`
#: re-derives (offline) every errata.md rendered by an older one -- see
#: :func:`rerender`.  v1: tags stripped, every formula deleted with them.
#: v2: maths recovered from ``<img>`` and suspect entries reported.
RENDERER_VERSION = 2
RENDERER = ("sources/fetch_errata.py html_to_markdown v%d "
            "(stdlib; maths recovered from img; never a summarising fetch)" % RENDERER_VERSION)

#: ``&`` as an HTML attribute may be written four ways, and WordPress picks the
#: one that survives no regex written for the others: ``&#038;``.
_AMP = re.compile(r"&(?:amp;|#0*38;|#x0*26;)", re.I)

#: The substring that says "this img IS a formula", whoever is serving it. The
#: host is not part of the rule: the pattern and ``class="latex"`` are.
_LATEX_ENDPOINT = "latex.php"

#: A rendering directive, not content: an alt of ``\displaystyle [0,1)`` is the
#: formula ``[0,1)`` told to typeset big.  Everything else in the alt is kept
#: exactly as the author wrote it -- an escaped ``\$`` included.
_DISPLAYSTYLE = re.compile(r"^\\(?:display|text)style\s+")

#: An entry that kept less than this fraction of the non-whitespace characters
#: it carried lost content in conversion -- which is the defect this module was
#: rewritten for.  What counts as "carried" is exactly what :class:`_Markdown`
#: can see: the entry's text, the TeX of every formula the page MARKS as one,
#: and the body of every ``<script type="math/...">``.  An image the page does
#: not mark as a formula is not counted (on the live pages those are commenters'
#: avatars, and counting their alt text would flag every comment), nor is an
#: ``<svg>``, so maths served that way is caught only by :data:`SUSPECT_SHAPES`.
MIN_ENTRY_DENSITY = 0.9

#: How many suspect entries errata.json quotes; the count is always exact.
MAX_SUSPECT_REPORTED = 20


def is_stale_render(version) -> bool:
    """Was errata.md rendered by an OLDER converter than this one?

    ``None`` is v1, which recorded no version.  A NEWER version is not stale:
    two checkouts sharing a workspace must not keep re-rendering each other's
    file, and the older one would be the one getting it wrong.
    """
    return (version or 1) < RENDERER_VERSION


def latex_from_src(url: str) -> str:
    r"""The TeX carried by a ``latex.php?latex=<url-encoded TeX>`` URL.

    The separators may be ``&``, ``&amp;`` or ``&#038;`` (WordPress writes the
    last one, and it reaches here verbatim whenever the URL is read as a string
    rather than through the parser's attribute unescaping).  ``+`` is a space:
    ``latex.php`` builds the query with ``urlencode``, so ``x+%5Cin+A`` is
    ``x \in A``, not ``x+\in+A``.  Empty when this is not a formula URL.
    """
    if not url or _LATEX_ENDPOINT not in url:
        return ""
    query = url.partition("?")[2]
    for part in _AMP.sub("&", html.unescape(query)).split("&"):
        name, sep, value = part.partition("=")
        if sep and name.strip() == "latex":
            return urllib.parse.unquote_plus(value)
    return ""


def is_math_img(attrs: dict) -> bool:
    """Is this ``<img>`` a formula? Two independent markers, neither a host.

    ``class="latex"`` is what the page says it is, and ``latex.php?latex=`` is
    what the URL is: either one is enough, so a site that serves the same
    endpoint from its own domain, or marks the class without the endpoint, is
    still read as mathematics.
    """
    classes = (attrs.get("class") or "").split()
    if "latex" in classes:
        return True
    for key in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
        if _LATEX_ENDPOINT in (attrs.get(key) or "") and "latex=" in (attrs.get(key) or ""):
            return True
    return False


def img_tex(attrs: dict) -> str:
    """The TeX of a formula ``<img>``: ``alt`` first, then the ``src``.

    ``alt`` is preferred because it is the TeX the author typed, stored by the
    page itself; the ``latex=`` parameter is the same string url-encoded and is
    the fallback for the tags that carry no alt.  ``srcset`` is consulted LAST
    and only for its first URL -- it repeats the same formula at 1x and 4x, and
    rendering a tag once per copy is its own kind of corruption.

    Empty when neither carries it, which :func:`audit_entries` then reports
    rather than passing off a hole as prose.
    """
    alt = _normalise_tex(attrs.get("alt") or "")
    if alt:
        return alt
    for key in ("src", "data-src", "data-lazy-src"):
        tex = _normalise_tex(latex_from_src(attrs.get(key) or ""))
        if tex:
            return tex
    for key in ("srcset", "data-srcset"):
        for candidate in (attrs.get(key) or "").split(","):
            tex = _normalise_tex(latex_from_src(candidate.strip().split(" ")[0]))
            if tex:
                return tex
    return ""


def _normalise_tex(tex: str) -> str:
    r"""Collapse the whitespace and drop a leading ``\displaystyle``.

    Delimiting ``$`` signs are dropped (they are added back around the formula);
    an ESCAPED dollar at the end, ``5\$``, is the author's text and is kept.
    """
    tex = re.sub(r"\s+", " ", (tex or "").replace("\r", " ").replace("\n", " ")).strip()
    tex = _DISPLAYSTYLE.sub("", tex).strip()
    tex = re.sub(r"(?<!\\)\$+$", "", re.sub(r"^\$+", "", tex))
    return tex.strip()


def _is_math_script(attrs) -> bool:
    """``<script type="math/tex">`` (and ``; mode=display``, ``math/mml``...)."""
    kind = (dict(attrs).get("type") or "").strip().lower()
    return kind.startswith("math/")


def _dense(text: str) -> int:
    """Non-whitespace characters: the unit both halves of the audit count in."""
    return len(re.sub(r"\s+", "", text or ""))


class _Markdown(HTMLParser):
    """A small, deterministic HTML -> Markdown renderer (stdlib only).

    It keeps a running account per block -- ``seen`` is every non-whitespace
    character of content the block carried that this parser can recognise as
    content, ``kept`` is what was emitted -- so :func:`audit_entries` can say an
    entry lost content without re-parsing the page or guessing which bullet
    matches which ``<li>``.  ``seen`` counts the block's text, the TeX of each
    formula image (its title, or ``?``, when the TeX cannot be recovered), and
    the body of each ``<script type="math/...">`` -- MathJax's in-page TeX,
    which this renderer does not emit, so it is seen and never kept.  See
    :data:`MIN_ENTRY_DENSITY` for what is deliberately NOT counted.
    Markdown punctuation this renderer ADDS (``- ``, ``**``, the link target)
    is counted in neither: the account is about the page's content.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._skip = 0
        # inside a <script type="math/...">: skipped like any script, but its
        # body is content the page carried, so it is SEEN (and not kept)
        self._math_script = False
        self._list: list[str] = []
        self._href: str | None = None
        self._line: list[str] = []
        self._seen = 0
        self._kept = 0
        self._math = 0
        self._unrecovered = 0

    # -- helpers ---------------------------------------------------------
    def _flush(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self._line)).strip()
        block = {"text": text, "seen": self._seen, "kept": self._kept,
                 "math": self._math, "unrecovered": self._unrecovered}
        self._line = []
        self._seen = self._kept = self._math = self._unrecovered = 0
        if text:
            self.blocks.append(block)

    def _emit(self, text: str) -> None:
        self._line.append(text)

    def _content(self, text: str, *, kept: bool = True) -> None:
        """Account for content the page carried, emitted or not."""
        size = _dense(text)
        self._seen += size
        if kept:
            self._kept += size

    def _image(self, attrs) -> None:
        """A formula ``<img>`` becomes its TeX; anything else is still dropped.

        The tag is rendered ONCE, from the tag's own attributes, which is why a
        ``srcset`` repeating the formula at 1x and 4x cannot double it: the
        parser sees one element, not one per URL.
        """
        attrs = {name: (value or "") for name, value in attrs}
        if not is_math_img(attrs):
            return
        self._math += 1
        tex = img_tex(attrs)
        if tex:
            self._content(tex)
            self._emit("$%s$" % tex)
            return
        # class="latex" with neither an alt nor a latex= parameter: the formula
        # is in the image bytes and nowhere else. Say so where it stood rather
        # than close the sentence over the hole.
        self._unrecovered += 1
        self._content(attrs.get("title") or "?", kept=False)
        self._emit("[math image: TeX not in the page]")

    # -- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            if tag == "script" and not self._skip and _is_math_script(attrs):
                self._math_script = True
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
        elif tag == "img":
            self._image(attrs)
        elif tag == "hr":
            self.blocks.append({"text": "---", "seen": 0, "kept": 0,
                                "math": 0, "unrecovered": 0})

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            if not self._skip:
                self._math_script = False
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
        if self._skip:
            if self._math_script:
                self._content(data, kept=False)
            return
        self._content(data)
        self._emit(data.replace("\r", " ").replace("\n", " "))

    def close(self):  # type: ignore[override]
        super().close()
        self._flush()

    def entries(self) -> list[dict]:
        """The blocks, with a repeated block collapsed as the markdown does."""
        body: list[dict] = []
        for block in self.blocks:
            if body and body[-1]["text"] == block["text"]:
                continue
            body.append(block)
        return body

    def markdown(self) -> str:
        return "\n\n".join(b["text"] for b in self.entries()).strip() + "\n"


def render(source: str) -> tuple[str, dict]:
    """Render the page, and report what the render did to it.

    Returns the Markdown and an audit: how many formulas were recovered, how
    many were not, and every entry that still looks like content was lost.
    """
    parser = _Markdown()
    parser.feed(source)
    parser.close()
    entries = parser.entries()
    audit = {
        "renderer_version": RENDERER_VERSION,
        "entries": sum(1 for b in entries if b["text"].lstrip().startswith("- ")),
        "math_recovered": sum(b["math"] - b["unrecovered"] for b in entries),
        "math_unrecovered": sum(b["unrecovered"] for b in entries),
        "suspect": audit_entries(entries),
    }
    audit["suspect_entries"] = len(audit["suspect"])
    return parser.markdown(), audit


def html_to_markdown(source: str) -> str:
    """Render HTML as Markdown deterministically (no model, nothing dropped)."""
    return render(source)[0]


#: Shapes a sentence takes when its mathematics has been deleted from under it,
#: the shapes a tag-stripping render of these pages produced (the examples are
#: synthetic): "In Corollary 3.2.4(b), should be , and the same", "replace
#: \"and not \" with \"\"".  They catch SOME emptied sentences, not all: an
#: entry that lost a formula mid-clause can still read cleanly, so an empty
#: ``suspect`` list is not proof that nothing was lost.
SUSPECT_SHAPES: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    ("comma_gap", re.compile(r",\s*,"),
     "a comma-space-comma: whatever stood between them is gone"),
    ("dangling_should_be", re.compile(r"\bshould\s+(?:be|read)\s*[,.;]"),
     "`should be` with nothing to be: the correction itself was dropped"),
    ("empty_quotes", re.compile(r'""|\u201c\s*\u201d'),
     "a quotation with nothing quoted"),
)


def audit_entries(entries: list[dict]) -> list[dict]:
    """Every entry that reads like its mathematics was deleted from under it.

    Two views, because they fail differently:

    * the SHAPE of the sentence (:data:`SUSPECT_SHAPES`) -- what a reader sees,
      and the only view that can catch maths this renderer does not recognise
      at all (an image the page does not mark as a formula, an ``<svg>``);
    * the ACCOUNT -- ``unrecovered_math`` for a formula image whose TeX is in
      the image bytes and nowhere else, and ``lost_characters`` for an entry
      that kept less than :data:`MIN_ENTRY_DENSITY` of the characters
      :class:`_Markdown` saw in it (in practice: TeX the page carried in a
      ``<script type="math/...">``, which is never emitted).

    Silently emitting "In Corollary 3.2.4(b), should be , and the same" is the
    failure this module exists to stop, so the result is reported by every
    caller -- `cf onboard` and `cf sources refresh` as a warning block naming
    the entries, `cf status` as a count, errata.json as a record.
    """
    suspect: list[dict] = []
    for block in entries:
        reasons = [name for name, pattern, _ in SUSPECT_SHAPES if pattern.search(block["text"])]
        density = (block["kept"] / block["seen"]) if block["seen"] else 1.0
        if block["unrecovered"]:
            reasons.append("unrecovered_math")
        if density < MIN_ENTRY_DENSITY:
            reasons.append("lost_characters")
        if reasons:
            suspect.append({
                "entry": block["text"][:300],
                "reasons": reasons,
                "kept": block["kept"],
                "seen": block["seen"],
                "density": round(density, 3),
            })
    return suspect


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


#: the page is HTML an operator reads and a worker is told to trust, so a body
#: this size is already a fetch that went wrong.  The flag only helps when the
#: server declares a length, so the bytes are checked again after the transfer.
MAX_BYTES = 10 * 1024 * 1024
MAX_FILESIZE = "10m"
MAX_REDIRS = "3"


def curl_argv(exe: str, url: str, tmp: str, timeout: int) -> list[str]:
    """The exact curl command line, so a test can pin the caps.

    Everything here bounds a fetch of a URL this machine may not have chosen (an
    errata URL can be harvested from the companion repo, and the page becomes the
    document that decides whether a deviation is a correction):

    * ``--proto =https`` / ``--proto-redir =https`` -- no plaintext, no FTP on a
      redirect (curl blocks file:// on redirect itself, but not FTP);
    * ``--max-redirs 3`` -- a redirect chain is not a fetch;
    * ``--max-filesize`` / ``--max-time`` -- a multi-gigabyte body has nowhere
      to stop, and this writes into every worker's prompt;
    * ``%{url_effective}`` -- what was actually fetched, recorded next to the sha
      so a redirect is visible in the audit trail.
    """
    return [exe, "-sS", "-L",
            "--proto", "=https", "--proto-redir", "=https",
            "--max-redirs", MAX_REDIRS,
            "--max-filesize", MAX_FILESIZE,
            "--max-time", str(timeout),
            "-o", tmp, "-w", "%{http_code}\\n%{url_effective}\\n", url]


def curl(url: str, *, timeout: int = 60) -> tuple[bytes, int, str]:
    """GET ``url`` with curl; returns (body bytes, http status, effective url)."""
    if not re.match(r"^https?://", url):
        raise ValueError("errata url must be http(s): %r" % url)
    if not url.lower().startswith("https://"):
        raise ValueError(
            "errata url must be https (this one is plaintext): %r -- the page is handed to "
            "workers as the document that decides whether a deviation is a correction; fix the "
            "URL with `cf sources set <id> errata_url=https://...`" % url)
    exe = shutil.which("curl")
    if exe is None:
        raise RuntimeError("curl not found")
    fd, tmp = tempfile.mkstemp(prefix="cf-errata-", suffix=".html")
    os.close(fd)
    try:
        proc = subprocess.run(
            curl_argv(exe, url, tmp, timeout),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError("curl failed (%s): %s" % (proc.returncode, proc.stderr.strip()))
        with open(tmp, "rb") as fh:
            body = fh.read()
        if len(body) > MAX_BYTES:
            # --max-filesize is a no-op when the response declares no length, so
            # the cap is enforced on what actually landed as well.
            raise RuntimeError("errata page is %d bytes, over the %s cap (%s)"
                               % (len(body), MAX_FILESIZE, url))
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        try:
            status = int(lines[0][-3:]) if lines else 0
        except ValueError:
            status = 0
        effective = lines[1] if len(lines) > 1 else ""
        return body, status, effective
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
    provenance: str = "unknown",
) -> dict:
    """Fetch the errata page into ``sources/<id>/errata.{raw.html,md,json}``.

    ``provenance`` says WHO chose this URL: ``registry`` (a curated entry),
    ``manual`` (``cf sources set``), or ``repo`` (harvested from the companion's
    own files).  A repo-supplied page is attacker-choosable text that ends up in
    worker prompts, so the provenance travels in ``errata.json`` and
    ``run/manifest.py`` refuses to ship one nobody has confirmed.
    """
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "errata.raw.html")
    md_path = os.path.join(out_dir, "errata.md")
    meta_path = os.path.join(out_dir, "errata.json")
    try:
        previous = read_json(meta_path, default={}) or {}
    except ValueError:
        # a damaged record is replaced, not fatal: a fetch is the repair
        # `cf onboard` / `cf sources refresh` point at for one
        previous = {}

    body, status, effective = curl(url, timeout=timeout)
    if status >= 400 or not body:
        raise RuntimeError("errata fetch returned HTTP %s for %s" % (status, url))
    source = _decode(body)
    digest = sha256_text(source)
    content_digest = sha256_text(stable_text(source))
    changed = (
        force
        or previous.get("content_sha256") != content_digest
        # an older converter rendered a DIFFERENT errata.md from the same
        # bytes, and the whole point of the version is that nobody has to
        # notice (a NEWER one's render is left alone: is_stale_render)
        or is_stale_render(previous.get("renderer_version"))
        or not os.path.exists(md_path)
        or not os.path.exists(raw_path)
    )
    meta = {
        "url": url,
        # WHAT WAS ACTUALLY FETCHED: a redirect is invisible otherwise, and this
        # page is handed to workers as authoritative errata.
        "url_effective": effective or url,
        "url_redirected": bool(effective and effective != url),
        "url_provenance": provenance,
        "repo_supplied": provenance == "repo",
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
        "renderer": RENDERER,
        "renderer_version": RENDERER_VERSION,
        "first_fetched_at": previous.get("first_fetched_at") or utcnow(),
    }
    if changed:
        write_text(raw_path, source)
        write_text(md_path, _document(source, meta))
    else:
        for key in ("entries", "math_recovered", "math_unrecovered",
                    "suspect_entries", "suspect"):
            meta[key] = previous.get(key)
        # the files on disk are the previous converter's, which may be NEWER
        # than this one (is_stale_render); record who really wrote them
        for key in ("renderer", "renderer_version"):
            meta[key] = previous.get(key, meta[key])
    write_json(meta_path, meta)
    return meta


def _header(meta: dict) -> list[str]:
    """The lines above the page itself; identical whoever rendered it.

    `fetch` and `rerender` both build the file from here, so a re-derived
    errata.md differs from a re-fetched one only where the facts differ.
    """
    header = [
        "# Errata (fetched verbatim, not summarised)",
        "",
        "- source: %s" % meta.get("url", ""),
    ]
    if meta.get("url_redirected"):
        header.append("- REDIRECTED to: %s" % meta.get("url_effective", ""))
    header += [
        "- fetched: %s" % meta.get("fetched_at", ""),
        "- raw sha256: %s" % meta.get("sha256", ""),
        "- content sha256 (comments stripped): %s" % meta.get("content_sha256", ""),
        "- url chosen by: %s" % meta.get("url_provenance", "unknown"),
        "- source: %s" % (meta.get("source_id") or "(unnamed)"),
        "- source sha256: %s" % (meta.get("source_sha") or "(unpinned)"),
    ]
    offset = meta.get("printed_page_offset")
    if offset is not None:
        header.append("- page numbers below are PRINTED pages; pdf page = printed + %d" % offset)
    if meta.get("rerendered_at"):
        header.append("- re-rendered (no re-fetch): %s" % meta["rerendered_at"])
    header += [
        "- renderer: %s" % meta.get("renderer", RENDERER),
        "- maths: %d formulas recovered from <img> tags, %d not recoverable from the page"
        % (meta.get("math_recovered") or 0, meta.get("math_unrecovered") or 0),
    ]
    if meta.get("suspect_entries"):
        header.append(
            "- WARNING: %d entries may have lost content in conversion; errata.json `suspect` "
            "lists them. READ THE RAW HTML for those before trusting them."
            % meta["suspect_entries"])
    header += [
        "- the whole page is rendered, navigation and all: an erratum has been lost before "
        "(Definition 1.5.11) by keeping only what looked relevant",
        "",
        "---",
        "",
    ]
    return header


def _document(source: str, meta: dict) -> str:
    """Render the page and fill ``meta`` with what the render did to it."""
    body_md, audit = render(source)
    meta.update(audit)
    meta["suspect"] = audit["suspect"][:MAX_SUSPECT_REPORTED]
    return "\n".join(_header(meta)) + body_md


def rerender(out_dir: str, *, force: bool = False) -> dict:
    """Re-derive ``errata.md`` from the kept bytes. No network, ever.

    This is what makes :data:`RENDERER_VERSION` more than a label: a source
    ingested by an older converter has an errata.md that this checkout would
    not produce, and the page it was made from is already on disk.  `cf onboard`
    and `cf sources refresh` call it for every source before they consider
    fetching anything -- including under ``--no-errata``, which means "do not
    go out to the network", not "keep a render this repo knows is stale".

    Returns the (updated) errata.json with ``changed`` saying whether the file
    on disk moved; when there is nothing to re-derive, the record as it stands
    with ``changed: False`` and a ``reason``.  Only a render by an OLDER
    converter is re-derived (:func:`is_stale_render`).
    """
    raw_path = os.path.join(out_dir, "errata.raw.html")
    md_path = os.path.join(out_dir, "errata.md")
    meta_path = os.path.join(out_dir, "errata.json")
    meta = read_json(meta_path, default={}) or {}
    if not os.path.exists(raw_path):
        # Nothing was kept to re-render from: a source with no errata, or one
        # fetched before the raw HTML was stored. Not an error, and not a fix
        # either -- the next `cf onboard --fetch-errata` is.
        return {**meta, "changed": False, "reason": "no errata.raw.html"}
    if (not force and not is_stale_render(meta.get("renderer_version"))
            and os.path.exists(md_path)):
        return {**meta, "changed": False, "reason": "already rendered by this converter"}
    source = _read_text(raw_path)
    meta = dict(meta)
    meta["previous_renderer_version"] = meta.get("renderer_version")
    meta["renderer"] = RENDERER
    meta["renderer_version"] = RENDERER_VERSION
    meta["rerendered_at"] = utcnow()
    # The shas are recomputed from the bytes being rendered, not copied from the
    # record: this file's header states what it was made from, and an errata.json
    # written before the raw HTML was kept has no sha at all. A record that
    # disagrees with the bytes is reported, never quietly overwritten.
    digest = sha256_text(source)
    meta["raw_sha_drift"] = bool(meta.get("sha256") and meta["sha256"] != digest)
    meta["sha256"] = digest
    meta["content_sha256"] = sha256_text(stable_text(source))
    write_text(md_path, _document(source, meta))
    meta["changed"] = True
    write_json(meta_path, meta)
    return meta


def _read_text(path: str) -> str:
    """The kept page, as :func:`fetch` wrote it: UTF-8, whatever it was served as.

    NOT :func:`_decode`: that trusts the page's own ``charset=`` tag, which is
    still in the file after fetch decoded the body, so an ``iso-8859-1`` page
    would be decoded twice and every non-ASCII character garbled.
    """
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")
