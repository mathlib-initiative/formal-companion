"""Load, validate and update campaign.yaml.

New code (design/architecture.md S8 item 1: "cf.py and the campaign/state
model (nothing like it exists; state lived in prose handoffs)").

A campaign is a directory outside every repo -- the ~/Desktop/tao-differential
role -- holding the book, the statement tables, the corpus and the ledgers for
ONE book.  campaign.yaml is its only configuration file; commands read it and
never hard-code a path, a host, a model or a page offset.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .errors import CampaignError, CfError
from .items import DEFAULT_KINDS
from .paths import CampaignPaths
from .yamlio import read_yaml, write_yaml

CAMPAIGN_FILE = "campaign.yaml"
CAMPAIGN_VERSION = 1


def find_campaign_root(start: str | Path | None = None) -> Path:
    """Locate the campaign directory.

    Order: the explicit argument, ``$CF_CAMPAIGN``, then the current directory
    and its ancestors (like git).  Raises CampaignError when nothing is found.
    """
    candidates: list[Path] = []
    if start:
        candidates.append(Path(start).expanduser())
    elif os.environ.get("CF_CAMPAIGN"):
        candidates.append(Path(os.environ["CF_CAMPAIGN"]).expanduser())
    else:
        candidates.append(Path.cwd())
    for cand in candidates:
        cand = cand.resolve()
        if (cand / CAMPAIGN_FILE).exists():
            return cand
        for parent in cand.parents:
            if (parent / CAMPAIGN_FILE).exists():
                return parent
    raise CampaignError(
        f"no {CAMPAIGN_FILE} found from {candidates[0]}",
        hint="pass --campaign <dir>, set CF_CAMPAIGN, or run `cf init <dir> --book <id>`")


@dataclass
class Campaign:
    """One book's campaign directory."""

    root: Path
    data: dict[str, Any]

    # -- loading -------------------------------------------------------
    @classmethod
    def load(cls, start: str | Path | None = None) -> "Campaign":
        root = find_campaign_root(start)
        data = read_yaml(root / CAMPAIGN_FILE)
        if not isinstance(data, dict):
            raise CampaignError(f"{root / CAMPAIGN_FILE}: expected a mapping at the top level")
        camp = cls(root=root, data=data)
        problems = camp.validate()
        if problems:
            raise CampaignError(f"{root / CAMPAIGN_FILE} is invalid: " + "; ".join(problems))
        return camp

    def save(self) -> Path:
        """Rewrite campaign.yaml (comments in the original file are lost)."""
        return write_yaml(self.paths.campaign_yaml, self.data,
                          header=f"# campaign.yaml -- rewritten by cf on {date.today().isoformat()}\n")

    # -- sections ------------------------------------------------------
    @property
    def paths(self) -> CampaignPaths:
        return CampaignPaths(root=self.root, book_id=self.book_id)

    @property
    def book(self) -> dict:
        return self.data.get("book") or {}

    @property
    def book_id(self) -> str:
        return str(self.book.get("id") or "")

    @property
    def book_sha(self) -> str | None:
        """sha256 of the pinned PDF: the edition gate compares against this."""
        sha = self.book.get("sha256")
        return str(sha) if sha not in (None, "", "null") else None

    @property
    def offset(self) -> int:
        """printed page = PDF page - offset (16 for the published GSM 126)."""
        return int(self.book.get("offset") or 0)

    @property
    def errata_url(self) -> str | None:
        return self.book.get("errata_url") or None

    @property
    def pdf_path(self) -> Path | None:
        p = self.book.get("pdf")
        return Path(p).expanduser() if p else None

    @property
    def kinds(self) -> tuple[str, ...]:
        kinds = self.book.get("kinds") or list(DEFAULT_KINDS)
        return tuple(str(k) for k in kinds)

    @property
    def companions(self) -> list[dict]:
        return list(self.data.get("companions") or [])

    def companion(self, name: str) -> dict:
        for c in self.companions:
            if c.get("name") == name:
                return c
        raise CampaignError(f"unknown companion {name!r}",
                            hint=f"known: {', '.join(c.get('name', '?') for c in self.companions) or '(none)'}"
                                 " -- run `cf onboard <name> --repo <path> --adapter <kind>`")

    def companion_names(self) -> list[str]:
        """Names from campaign.yaml, plus any directory on disk with an adapter."""
        listed = [str(c.get("name")) for c in self.companions if c.get("name")]
        for name in self.paths.list_companions():
            if name not in listed:
                listed.append(name)
        return listed

    def upsert_companion(self, name: str, **fields) -> dict:
        """Add or update a companion entry (does not save)."""
        for c in self.data.setdefault("companions", []):
            if c.get("name") == name:
                c.update({k: v for k, v in fields.items() if v is not None})
                return c
        entry = {"name": name}
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.data.setdefault("companions", []).append(entry)
        return entry

    # -- hosts, models, defaults ---------------------------------------
    @property
    def hosts(self) -> dict:
        return self.data.get("hosts") or {}

    @property
    def host_allowlist(self) -> list[str]:
        return [str(h) for h in (self.hosts.get("allowlist") or [])]

    def check_host(self, host: str) -> str:
        """Refuse any host not listed in campaign.yaml hosts.allowlist.

        `cf run` rsyncs private Lean sources and copyrighted book text; the
        allowlist is the enforcement point (design/critique.json, risks).
        """
        if host not in self.host_allowlist:
            raise CfError(
                f"host {host!r} is not in hosts.allowlist",
                hint="add it to campaign.yaml hosts.allowlist; the owner of that box must consent "
                     "before private sources or book text are copied to it")
        return host

    @property
    def models(self) -> dict:
        return self.data.get("models") or {}

    def model(self, role: str) -> str | None:
        """Model id for a role (worker, reviewer, skeptic, adjudicator, calibration)."""
        return self.models.get(role)

    @property
    def defaults(self) -> dict:
        return self.data.get("defaults") or {}

    def default(self, key: str, fallback=None):
        val = self.defaults.get(key)
        return fallback if val is None else val

    @property
    def budget(self) -> dict:
        return self.data.get("budget") or {}

    @property
    def policy(self) -> dict:
        return self.data.get("policy") or {}

    # -- gates ---------------------------------------------------------
    def require_book_sha(self, sha: str | None, where: str) -> None:
        """The edition gate: refuse an artifact pinned to another edition.

        Batch-1 anchor findings were judged against the 2011 draft; every
        artifact that quotes the book carries book_sha and is checked here.
        """
        if not self.book_sha:
            raise CampaignError("campaign.yaml has no book.sha256",
                                hint="run `cf book <id> --pdf <path>` to pin the edition")
        if sha != self.book_sha:
            raise CfError(f"{where}: book_sha {sha!r} != campaign book.sha256 {self.book_sha!r}",
                          hint="this artifact was produced against a different edition; re-run its stage")

    def validate(self) -> list[str]:
        """Structural problems with campaign.yaml (empty list when valid)."""
        problems = []
        if not self.book_id:
            problems.append("book.id is required")
        if self.book.get("offset") is None:
            problems.append("book.offset is required (printed page = PDF page - offset)")
        if not isinstance(self.data.get("companions", []), list):
            problems.append("companions must be a list")
        if not isinstance(self.hosts.get("allowlist", []), list):
            problems.append("hosts.allowlist must be a list")
        for c in self.companions:
            if not isinstance(c, dict) or not c.get("name"):
                problems.append(f"companion entry without a name: {c!r}")
        return problems


def from_args(args) -> Campaign:
    """Load the campaign a cf command was pointed at.

    Every ``cfcmd`` module uses this: cf.py parses the global ``--campaign``
    option (defaulting to ``$CF_CAMPAIGN`` or the current directory) and
    stores it on ``args.campaign``.
    """
    return Campaign.load(getattr(args, "campaign", None))
