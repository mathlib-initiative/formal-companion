"""The one module that talks to Wikipedia (and Wikidata): parse, list, flag.

New code, implementing the L1 stage of docs/proposals/wikipedia-locate.md section 3
("enumerate the corpus (PageAssessments API ...), fetch the rest once") and the
named network exception of CLAUDE.md section 3.  Every request goes through an
injectable :class:`Transport`, so nothing here decides whether the network is
used -- the caller does, and ``cf sourceset`` uses :class:`CurlTransport` only
under an explicit ``--execute``:

* :class:`CurlTransport` -- ``curl`` in a subprocess, the raw response bytes kept
  exactly as received, https only (on redirects too), ``--fail``, a bounded size
  and time, a User-Agent naming the skill and nothing about the operator, a
  ``maxlag`` on every request, a polite minimum interval between requests, and
  retries with backoff on a lagged replica, a 429 or a 5xx;
* :class:`FixtureTransport` -- recorded responses in a directory, keyed by the
  sha256 of the request's canonical query (:func:`canonical_query`), for tests
  and offline runs.  A request with no recording is refused by name, so a test
  that grew a request says which one.

Five requests are made, each with a fixed parameter set:

* :func:`fetch_parse` -- one revision's ``action=parse`` output with the
  parameters of ``sources.websource.PARSE_PARAMS``; the bytes are the source's
  ``source.raw.html``;
* :func:`project_pages` -- the PageAssessments listing of one WikiProject
  (``list=projectpages``, ``wppassessments=1``, followed through its
  continuation): page id, title, class and importance, articles only, the rows
  of other namespaces counted (:func:`project_page_batches` is the same, one
  answer at a time, resumable from a continuation);
* :func:`page_infos` -- each page's latest revision id, its own Wikidata item
  (``pageprops`` ``wikibase_item``) and whether it is a redirect (``info``) or a
  disambiguation page (``pageprops`` ``disambiguation``), fifty pages a request,
  each answer followed through its continuation;
* :func:`revision_pages` -- the page each of a consumer's pinned revisions
  belongs to (``revids``, fifty a request), for a pin that names its revision
  and only a title (titles move; the revision does not);
* :func:`wikidata_humans` -- which of a set of Wikidata items are instances of
  human (``P31`` = ``Q5``): one SPARQL query per :data:`SPARQL_BATCH` items to
  the Wikidata Query Service (:data:`WIKIDATA_SPARQL`), the biography flag's
  optional step.

No other module under scripts/ may fetch from Wikipedia or Wikidata;
tests/test_locate_sources.py enforces that.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Protocol

from cflib import __version__
from cflib.errors import CfError

from . import websource

#: names the skill and nothing about whoever runs it (Wikimedia's User-Agent policy)
USER_AGENT = (f"formal-companion-locate/{__version__} "
              "(+https://github.com/mathlib-initiative/formal-companion)")
#: seconds of replica lag past which the API refuses us, as it asks bots to
MAXLAG = "5"
#: never more than one request a second from one transport
MIN_INTERVAL = 1.0
MAX_ATTEMPTS = 4
BACKOFF = (2.0, 4.0, 8.0, 16.0)
MAX_FILESIZE = "64M"
TIMEOUT = 120
#: page ids per page-info request (the API's limit for an ordinary client)
INFO_BATCH = 50
#: the MediaWiki API's path on a family's site
API_PATH = "/w/api.php"
#: the Wikidata Query Service (the one other endpoint; GET with ``query`` and ``format``)
WIKIDATA_SPARQL = "https://query.wikidata.org"
SPARQL_PATH = "/sparql"
#: items per SPARQL query, so the GET request stays a few kilobytes long
SPARQL_BATCH = 150
#: the least wait after a ``maxlag`` refusal (the API's own Retry-After floor), and
#: the most any Retry-After may make a retry wait
MAXLAG_WAIT = 5.0
MAX_RETRY_WAIT = 120.0
#: parameters a transport adds to every request and a recording does not key on
TRANSPORT_PARAMS = frozenset({"maxlag"})
_QID = re.compile(r"Q[1-9][0-9]*")


class WikiError(CfError):
    """Wikipedia answered, and the answer is not what was asked for."""


class WikiUnavailable(WikiError):
    """Wikipedia is refusing or rate-limiting requests: retrying soon will not help."""


class Transport(Protocol):
    """GET one API request; return the raw response bytes.

    ``path`` is the endpoint on ``site``: the MediaWiki API unless a caller names
    another (the Wikidata Query Service's ``/sparql``).
    """

    def get(self, site: str, params: Mapping[str, str], *, path: str = API_PATH) -> bytes: ...


def canonical_query(params: Mapping[str, str]) -> str:
    """The request's parameters sorted and URL-encoded, without transport parameters."""
    pairs = sorted((str(k), str(v)) for k, v in params.items() if k not in TRANSPORT_PARAMS)
    return urllib.parse.urlencode(pairs)


def request_key(params: Mapping[str, str]) -> str:
    """sha256 of :func:`canonical_query`: what a recording is filed under."""
    return hashlib.sha256(canonical_query(params).encode("utf-8")).hexdigest()


def request_url(site: str, params: Mapping[str, str], path: str = API_PATH) -> str:
    """The full https URL of an API request on ``site`` (``https://en.wikipedia.org``).

    Every parameter is in it, the transport's ``maxlag`` included; only the
    recording key leaves that one out.
    """
    if not str(site).startswith("https://"):
        raise WikiError(f"the site {site!r} is not an https URL")
    if path not in (API_PATH, SPARQL_PATH):
        raise WikiError(f"no request is made to {path!r}: only the API and the SPARQL "
                        f"endpoint are")
    query = urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    return site.rstrip("/") + path + "?" + query


class CurlTransport:
    """``curl``, bounded, polite and retried; the bytes are kept as received."""

    def __init__(self, *, user_agent: str = USER_AGENT, min_interval: float = MIN_INTERVAL,
                 timeout: int = TIMEOUT, attempts: int = MAX_ATTEMPTS,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 runner: Callable = subprocess.run, exe: str | None = None):
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self.attempts = attempts
        self.sleep = sleep
        self.clock = clock
        self.runner = runner
        self.exe = exe
        self._last = None
        self.requests = 0

    def argv(self, url: str, out: str, headers: str | None = None) -> list[str]:
        """The exact command line, so a test can pin the bounds."""
        exe = self.exe or shutil.which("curl") or "curl"
        argv = [exe, "-sS", "--fail", "--proto", "=https", "--proto-redir", "=https",
                "--max-redirs", "3", "--max-filesize", MAX_FILESIZE,
                "--max-time", str(self.timeout), "-A", self.user_agent,
                "-o", out, "-w", "%{http_code}"]
        if headers:
            argv += ["-D", headers]
        return argv + [url]

    def _wait(self) -> None:
        if self._last is not None:
            gap = self.clock() - self._last
            if gap < self.min_interval:
                self.sleep(self.min_interval - gap)
        self._last = self.clock()

    def get(self, site: str, params: Mapping[str, str], *, path: str = API_PATH) -> bytes:
        # maxlag is the MediaWiki API's; the query service has no replicas to wait for
        extra = {"maxlag": MAXLAG} if path == API_PATH else {}
        url = request_url(site, {**params, **extra}, path)
        problem = "no attempt was made"
        delay = 0.0
        for attempt in range(max(1, self.attempts)):
            if attempt:
                self.sleep(delay)
            self._wait()
            fd, tmp = tempfile.mkstemp(prefix="cf-wiki-", suffix=".json")
            os.close(fd)
            fd, hdr = tempfile.mkstemp(prefix="cf-wiki-", suffix=".headers")
            os.close(fd)
            try:
                self.requests += 1
                try:
                    proc = self.runner(self.argv(url, tmp, hdr), capture_output=True, text=True)
                except OSError as exc:
                    raise WikiError(f"curl could not be run ({exc})",
                                    hint="install curl or put it on PATH; nothing was "
                                         "written for this request") from None
                status = (proc.stdout or "").strip()[-3:]
                body = Path(tmp).read_bytes() if os.path.exists(tmp) else b""
                headers = Path(hdr).read_bytes() if os.path.exists(hdr) else b""
            finally:
                for path in (tmp, hdr):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            backoff = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            wanted = retry_after(headers)
            if proc.returncode == 0 and status == "200":
                if not _lagged(body):
                    return body
                problem = "the replicas are lagged (maxlag)"
                delay = max(backoff, wanted or 0.0, MAXLAG_WAIT)
            elif status == "403":
                raise WikiUnavailable(f"GET {url} was refused (HTTP 403)",
                                      hint="the API is blocking this client; stop, and "
                                           "re-run much later")
            elif status in ("429", "500", "502", "503", "504") or proc.returncode in (
                    6, 7, 28, 35, 52, 55, 56):
                problem = f"curl exit {proc.returncode}, HTTP {status or '?'}: " \
                          f"{(proc.stderr or '').strip()[:200]}"
                delay = max(backoff, wanted or 0.0)
            else:
                raise WikiError(f"GET {url} failed: curl exit {proc.returncode}, HTTP "
                                f"{status or '?'}: {(proc.stderr or '').strip()[:200]}",
                                hint="nothing was written for this request")
            delay = min(delay, MAX_RETRY_WAIT)
        raise WikiUnavailable(f"GET {url} failed {max(1, self.attempts)} time(s): {problem}",
                              hint="Wikipedia is lagged or rate-limiting; re-run later, "
                                   "nothing was written for this request")


def retry_after(headers: bytes) -> float | None:
    """The seconds a ``Retry-After`` header of the LAST response asks for, or None.

    ``curl -D`` writes every response of a redirect chain; the last block is the
    answer.  Only the delta-seconds form is read (an HTTP-date is ignored).
    """
    text = headers.decode("latin-1", errors="replace")
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    for line in (blocks[-1].split("\n") if blocks else []):
        name, _, value = line.partition(":")
        if name.strip().lower() == "retry-after":
            value = value.strip()
            if value.isdigit():
                return float(value)
    return None


def _lagged(body: bytes) -> bool:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, dict) and (data.get("error") or {}).get("code") == "maxlag"


class FixtureTransport:
    """Recorded responses: ``<dir>/*.json`` files ``{"query": {...}, "body": "..."}``.

    ``body`` is the response text exactly; ``json`` instead holds a JSON value,
    whose bytes are its canonical encoding.  Files are matched by
    :func:`request_key` of their ``query``.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise CfError(f"no fixture directory at {self.directory}")
        self.recorded: dict[str, bytes] = {}
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("query"), dict):
                raise CfError(f"{path}: a recording is {{query, body | json}}")
            if "body" in data:
                body = str(data["body"]).encode("utf-8")
            else:
                body = json.dumps(data.get("json"), ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")
            self.recorded[request_key(data["query"])] = body
        self.requests: list[str] = []

    def get(self, site: str, params: Mapping[str, str], *, path: str = API_PATH) -> bytes:
        query = canonical_query(params)
        self.requests.append(query)
        try:
            return self.recorded[request_key(params)]
        except KeyError:
            raise WikiError(f"no recorded response for {query}",
                            hint=f"record it into {self.directory} as "
                                 f"{{\"query\": {{...}}, \"body\": ...}}") from None


def _json(body: bytes, what: str) -> dict:
    try:
        text = body.decode("utf-8")
        data = json.loads(text[1:] if text.startswith("\ufeff") else text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise WikiError(f"{what}: the response is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise WikiError(f"{what}: the response is not a JSON object")
    if isinstance(data.get("error"), dict):
        error = data["error"]
        raise WikiError(f"{what}: the API refused it ({error.get('code')}: "
                        f"{error.get('info', '')})")
    return data


def fetch_parse(transport: Transport, site: str, revision: int) -> bytes:
    """One revision's ``action=parse`` response, as raw bytes; checked, never altered."""
    params = websource.parse_params(revision)
    body = transport.get(site, params)
    check_parse(body, revision)
    return body


def check_parse(body: bytes, revision: int) -> dict:
    """``parse`` of an ``action=parse`` response of ``revision``; else :class:`WikiError`.

    The checks every recorded fetch passes, whether the bytes just arrived or were
    found on disk from an earlier run: JSON, a ``parse.text``, and the revision
    asked for (a missing ``revid`` is accepted, as the API omits it for none).
    """
    data = _json(body, f"parse of revision {revision}")
    parse = data.get("parse")
    if not isinstance(parse, dict) or not isinstance(parse.get("text"), (str, dict)):
        raise WikiError(f"parse of revision {revision}: no parse.text in the response")
    if parse.get("revid") not in (None, revision):
        raise WikiError(f"parse of revision {revision} answered for revision "
                        f"{parse.get('revid')}")
    return parse


def project_page_batches(transport: Transport, site: str, project: str, *,
                         cont: Mapping[str, str] | None = None, page_limit: int = 500
                         ) -> Iterator[tuple[list[dict], dict[int, int], dict | None]]:
    """The PageAssessments listing one answer at a time: ``(articles, skipped, next)``.

    ``articles`` are the rows of the article namespace as :func:`project_pages`
    yields them, ``skipped`` counts every other row by namespace (talk pages,
    categories, templates, drafts ...), and ``next`` is the continuation to
    resume from after this answer, None after the last one.  ``cont`` resumes a
    listing from a continuation an earlier call returned.
    """
    params = {"action": "query", "list": "projectpages", "wppprojects": project,
              "wppassessments": "1", "wpplimit": str(page_limit), "format": "json",
              "formatversion": "2"}
    cont = {str(k): str(v) for k, v in (cont or {}).items()}
    seen: set[str] = {canonical_query(cont)} if cont else set()
    while True:
        data = _json(transport.get(site, {**params, **cont}), f"projectpages {project}")
        projects = ((data.get("query") or {}).get("projects") or {})
        rows = projects.get(project)
        if rows is None and len(projects) == 1:
            rows = next(iter(projects.values()))
        articles: list[dict] = []
        skipped: dict[int, int] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            ns = row.get("ns", 0)
            if ns != 0:
                key = int(ns) if isinstance(ns, int) and not isinstance(ns, bool) else -1
                skipped[key] = skipped.get(key, 0) + 1
                continue
            assessment = row.get("assessment") or {}
            articles.append({"pageid": int(row["pageid"]), "title": row.get("title"),
                             "assessment": {
                                 "class": (assessment.get("class") or None),
                                 "importance": (str(assessment.get("importance")).lower()
                                                if assessment.get("importance") else None)}})
        nxt = data.get("continue")
        if not isinstance(nxt, dict) or not nxt:
            yield articles, skipped, None
            return
        cont = {str(k): str(v) for k, v in nxt.items()}
        token = canonical_query(cont)
        if token in seen:
            raise WikiError(f"projectpages {project}: the continuation repeats ({token})")
        seen.add(token)
        yield articles, skipped, dict(cont)


def project_pages(transport: Transport, site: str, project: str, *,
                  page_limit: int = 500, skipped: dict | None = None) -> Iterator[dict]:
    """Every article of a WikiProject with its assessment, through the continuation.

    Yields ``{"pageid", "title", "assessment": {"class", "importance"}}``;
    importance is lower-cased (``top`` ... ``low``), class kept as the project
    writes it (``FA``, ``B``, ``Start``).  Rows of other namespaces are not
    articles: they are skipped and, given a ``skipped`` dict, counted into it by
    namespace.
    """
    for articles, dropped, _next in project_page_batches(transport, site, project,
                                                         page_limit=page_limit):
        if skipped is not None:
            for ns, n in dropped.items():
                skipped[ns] = skipped.get(ns, 0) + n
        yield from articles


def revision_pages(transport: Transport, site: str, revisions: Iterable[int]
                   ) -> tuple[dict[int, dict], set[int]]:
    """``({revision: {"pageid", "title", "ns"}}, bad)``: the page of each revision.

    ``bad`` holds the revisions the API reports as no revision (``badrevids``) or
    did not answer for; the caller refuses them by name.
    """
    revs = sorted({int(r) for r in revisions})
    out: dict[int, dict] = {}
    for at in range(0, len(revs), INFO_BATCH):
        batch = revs[at:at + INFO_BATCH]
        params = {"action": "query", "revids": "|".join(str(r) for r in batch),
                  "prop": "revisions", "rvprop": "ids", "format": "json", "formatversion": "2"}
        for data in _continued(transport, site, params, f"the pages of {len(batch)} revision(s)"):
            for page in (data.get("query") or {}).get("pages") or []:
                if not isinstance(page, dict) or "pageid" not in page:
                    continue
                for rev in page.get("revisions") or []:
                    if isinstance(rev, dict) and rev.get("revid") is not None:
                        out[int(rev["revid"])] = {"pageid": int(page["pageid"]),
                                                  "title": page.get("title"),
                                                  "ns": page.get("ns")}
    return out, {r for r in revs if r not in out}


def _continued(transport: Transport, site: str, params: dict, what: str) -> Iterator[dict]:
    """Every answer to one query, following its ``continue`` until there is none."""
    cont: dict[str, str] = {}
    seen: set[str] = set()
    while True:
        data = _json(transport.get(site, {**params, **cont}), what)
        yield data
        nxt = data.get("continue")
        if not isinstance(nxt, dict) or not nxt:
            return
        cont = {str(k): str(v) for k, v in nxt.items()}
        token = canonical_query(cont)
        if token in seen:
            raise WikiError(f"{what}: the continuation repeats ({token})")
        seen.add(token)


#: the page-info request's fixed parameters (``pageids`` added per batch)
PAGE_INFO_PARAMS = {"action": "query", "prop": "info|revisions|pageprops", "rvprop": "ids",
                    "ppprop": "disambiguation|wikibase_item", "format": "json",
                    "formatversion": "2"}


def _merge_page(into: dict, page: dict) -> None:
    """Lay one continuation's view of a page over what earlier answers said of it."""
    for key, value in page.items():
        if key == "revisions" and isinstance(value, list):
            into.setdefault("revisions", []).extend(value)
        elif key == "pageprops" and isinstance(value, dict):
            into.setdefault("pageprops", {}).update(value)
        else:
            into.setdefault(key, value)


def page_info_batches(transport: Transport, site: str, pageids: Iterable[int]
                      ) -> Iterator[tuple[list[int], dict[int, dict]]]:
    """:func:`page_infos` one batch of :data:`INFO_BATCH` page ids at a time.

    Yields ``(batch, {pageid: info})``; a batch's answer is followed through its
    ``continue`` and every partial view of a page merged before it is read, so a
    page the API continues is never reported missing.
    """
    ids = sorted({int(p) for p in pageids})
    for at in range(0, len(ids), INFO_BATCH):
        batch = ids[at:at + INFO_BATCH]
        params = {**PAGE_INFO_PARAMS, "pageids": "|".join(str(p) for p in batch)}
        pages: dict[int, dict] = {}
        for data in _continued(transport, site, params, f"page info for {len(batch)} page(s)"):
            for page in (data.get("query") or {}).get("pages") or []:
                if isinstance(page, dict) and "pageid" in page:
                    _merge_page(pages.setdefault(int(page["pageid"]), {}), page)
        out: dict[int, dict] = {}
        for pageid, page in pages.items():
            if page.get("missing"):
                continue
            revisions = [r for r in page.get("revisions") or []
                         if isinstance(r, dict) and r.get("revid") is not None]
            if not revisions:
                continue
            props = page.get("pageprops") or {}
            out[pageid] = {
                "revision": max(int(r["revid"]) for r in revisions),
                "title": page.get("title"),
                "wikidata": props.get("wikibase_item"),
                "redirect": bool(page.get("redirect")),
                "disambiguation": "disambiguation" in props,
            }
        yield batch, out


def page_infos(transport: Transport, site: str, pageids: Iterable[int]) -> dict[int, dict]:
    """``{pageid: {"revision", "title", "wikidata", "redirect", "disambiguation"}}``.

    Each page's latest revision and QID, and whether it is a redirect or a
    disambiguation page.  A page the API reports missing is left out; the caller
    names it.
    """
    out: dict[int, dict] = {}
    for _batch, infos in page_info_batches(transport, site, pageids):
        out.update(infos)
    return out


def wikidata_humans(transport: Transport, qids: Iterable[str]) -> set[str]:
    """The items of ``qids`` that are instances of human (``wdt:P31 wd:Q5``).

    One SPARQL query per :data:`SPARQL_BATCH` items to the Wikidata Query
    Service, a fixed query shape; an item the service does not know is simply
    no human.  Anything that is no QID is refused before any request.
    """
    items = sorted({str(q) for q in qids}, key=lambda q: (len(q), q))
    bad = [q for q in items if not _QID.fullmatch(q)]
    if bad:
        raise WikiError(f"not Wikidata items: {', '.join(bad[:5])}")
    humans: set[str] = set()
    for at in range(0, len(items), SPARQL_BATCH):
        batch = items[at:at + SPARQL_BATCH]
        query = ("SELECT ?item WHERE { VALUES ?item { "
                 + " ".join(f"wd:{q}" for q in batch)
                 + " } ?item wdt:P31 wd:Q5 . }")
        body = transport.get(WIKIDATA_SPARQL, {"query": query, "format": "json"},
                             path=SPARQL_PATH)
        try:
            data = json.loads(body.decode("utf-8"))
            bindings = data["results"]["bindings"]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise WikiError(f"the Wikidata Query Service answered no SPARQL results "
                            f"({exc})") from None
        for row in bindings:
            value = str(((row or {}).get("item") or {}).get("value") or "")
            qid = value.rsplit("/", 1)[-1]
            if qid in batch:
                humans.add(qid)
    return humans
