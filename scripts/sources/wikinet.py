"""The one module that talks to Wikipedia: parse a revision, list a WikiProject.

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

Three requests are made, each with a fixed parameter set:

* :func:`fetch_parse` -- one revision's ``action=parse`` output with the
  parameters of ``sources.websource.PARSE_PARAMS``; the bytes are the source's
  ``source.raw.html``;
* :func:`project_pages` -- the PageAssessments listing of one WikiProject
  (``list=projectpages``, ``wppassessments=1``, followed through its
  continuation): page id, title, class and importance, articles only;
* :func:`page_infos` -- each page's latest revision id and its own Wikidata item
  (``pageprops`` ``wikibase_item``), fifty pages a request.

No other module under scripts/ may fetch from Wikipedia;
tests/test_locate_sources.py enforces that.
"""
from __future__ import annotations

import hashlib
import json
import os
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
#: parameters a transport adds to every request and a recording does not key on
TRANSPORT_PARAMS = frozenset({"maxlag"})


class WikiError(CfError):
    """Wikipedia answered, and the answer is not what was asked for."""


class Transport(Protocol):
    """GET one API request; return the raw response bytes."""

    def get(self, site: str, params: Mapping[str, str]) -> bytes: ...


def canonical_query(params: Mapping[str, str]) -> str:
    """The request's parameters sorted and URL-encoded, without transport parameters."""
    pairs = sorted((str(k), str(v)) for k, v in params.items() if k not in TRANSPORT_PARAMS)
    return urllib.parse.urlencode(pairs)


def request_key(params: Mapping[str, str]) -> str:
    """sha256 of :func:`canonical_query`: what a recording is filed under."""
    return hashlib.sha256(canonical_query(params).encode("utf-8")).hexdigest()


def request_url(site: str, params: Mapping[str, str]) -> str:
    """The full https URL of an API request on ``site`` (``https://en.wikipedia.org``).

    Every parameter is in it, the transport's ``maxlag`` included; only the
    recording key leaves that one out.
    """
    if not str(site).startswith("https://"):
        raise WikiError(f"the site {site!r} is not an https URL")
    query = urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    return site.rstrip("/") + "/w/api.php?" + query


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

    def argv(self, url: str, out: str) -> list[str]:
        """The exact command line, so a test can pin the bounds."""
        exe = self.exe or shutil.which("curl") or "curl"
        return [exe, "-sS", "--fail", "--proto", "=https", "--proto-redir", "=https",
                "--max-redirs", "3", "--max-filesize", MAX_FILESIZE,
                "--max-time", str(self.timeout), "-A", self.user_agent,
                "-o", out, "-w", "%{http_code}", url]

    def _wait(self) -> None:
        if self._last is not None:
            gap = self.clock() - self._last
            if gap < self.min_interval:
                self.sleep(self.min_interval - gap)
        self._last = self.clock()

    def get(self, site: str, params: Mapping[str, str]) -> bytes:
        url = request_url(site, {**params, "maxlag": MAXLAG})
        problem = "no attempt was made"
        for attempt in range(max(1, self.attempts)):
            if attempt:
                self.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            self._wait()
            fd, tmp = tempfile.mkstemp(prefix="cf-wiki-", suffix=".json")
            os.close(fd)
            try:
                self.requests += 1
                proc = self.runner(self.argv(url, tmp), capture_output=True, text=True)
                status = (proc.stdout or "").strip()[-3:]
                body = Path(tmp).read_bytes() if os.path.exists(tmp) else b""
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if proc.returncode == 0 and status == "200":
                if _lagged(body):
                    problem = "the replicas are lagged (maxlag)"
                    continue
                return body
            problem = f"curl exit {proc.returncode}, HTTP {status or '?'}: " \
                      f"{(proc.stderr or '').strip()[:200]}"
            if status not in ("429", "500", "502", "503", "504") and proc.returncode not in (
                    6, 7, 28, 35, 52, 55, 56):
                break
        raise WikiError(f"GET {url} failed: {problem}",
                        hint="re-run later; nothing was written for this request")


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

    def get(self, site: str, params: Mapping[str, str]) -> bytes:
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
        data = json.loads(body.decode("utf-8"))
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
    data = _json(body, f"parse of revision {revision}")
    parse = data.get("parse")
    if not isinstance(parse, dict) or not isinstance(parse.get("text"), (str, dict)):
        raise WikiError(f"parse of revision {revision}: no parse.text in the response")
    if parse.get("revid") not in (None, revision):
        raise WikiError(f"parse of revision {revision} answered for revision "
                        f"{parse.get('revid')}")
    return body


def project_pages(transport: Transport, site: str, project: str, *,
                  page_limit: int = 500) -> Iterator[dict]:
    """Every article of a WikiProject with its assessment, through the continuation.

    Yields ``{"pageid", "title", "assessment": {"class", "importance"}}``;
    importance is lower-cased (``top`` ... ``low``), class kept as the project
    writes it (``FA``, ``B``, ``Start``).  Namespaces other than articles are
    skipped.
    """
    params = {"action": "query", "list": "projectpages", "wppprojects": project,
              "wppassessments": "1", "wpplimit": str(page_limit), "format": "json",
              "formatversion": "2"}
    cont: dict[str, str] = {}
    seen: set[str] = set()
    while True:
        data = _json(transport.get(site, {**params, **cont}), f"projectpages {project}")
        projects = ((data.get("query") or {}).get("projects") or {})
        rows = projects.get(project)
        if rows is None and len(projects) == 1:
            rows = next(iter(projects.values()))
        for row in rows or []:
            if not isinstance(row, dict) or row.get("ns", 0) != 0:
                continue
            assessment = row.get("assessment") or {}
            yield {"pageid": int(row["pageid"]), "title": row.get("title"),
                   "assessment": {
                       "class": (assessment.get("class") or None),
                       "importance": (str(assessment.get("importance")).lower()
                                      if assessment.get("importance") else None)}}
        nxt = data.get("continue")
        if not isinstance(nxt, dict) or not nxt:
            return
        cont = {str(k): str(v) for k, v in nxt.items()}
        token = canonical_query(cont)
        if token in seen:
            raise WikiError(f"projectpages {project}: the continuation repeats ({token})")
        seen.add(token)


def page_infos(transport: Transport, site: str, pageids: Iterable[int]) -> dict[int, dict]:
    """``{pageid: {"revision", "title", "wikidata"}}``: each page's latest revision and QID.

    A page the API reports missing is left out; the caller names it.
    """
    ids = sorted({int(p) for p in pageids})
    out: dict[int, dict] = {}
    for at in range(0, len(ids), INFO_BATCH):
        batch = ids[at:at + INFO_BATCH]
        params = {"action": "query", "prop": "revisions|pageprops",
                  "pageids": "|".join(str(p) for p in batch), "rvprop": "ids",
                  "ppprop": "wikibase_item", "format": "json", "formatversion": "2"}
        data = _json(transport.get(site, params), f"page info for {len(batch)} page(s)")
        for page in (data.get("query") or {}).get("pages") or []:
            if not isinstance(page, dict) or page.get("missing") or "pageid" not in page:
                continue
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            out[int(page["pageid"])] = {
                "revision": int(revisions[0]["revid"]),
                "title": page.get("title"),
                "wikidata": (page.get("pageprops") or {}).get("wikibase_item"),
            }
    return out
