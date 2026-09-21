"""Nothing in this repo may come from the machine it was written on.

`CLAUDE.md` S1 is the repo's most-stated rule -- "no host names, agentbox
profiles, session ids or repo owners' identities" -- and four review findings
were the same defect: it was not being followed. When this check was written 77
tracked files matched it: 76 absolute home paths across 38 files, 37 `~/Desktop`
occurrences, 34 mentions of one run host, 15 Workflow run ids, and 34 `recovered/`
plus 18 `scratchpad/` citations into transcripts this repo has never held.

Those are three different costs, and only the first is small:

* a private repository's absolute path, a server alias and a profile name become
  org-visible the moment this repo moves org (security-safety#7, docs-accuracy#3);
* a default that is one person's Desktop layout is silently wrong for the second
  user -- `tests/e2e/*.sh` defaulted to two private checkouts, so the script
  reported "missing companion repo: <somebody else's path>" instead of saying
  what to set (docs-accuracy#3);
* a provenance comment nobody can resolve is not provenance. 30 module
  docstrings said "Ported from recovered/<transcript>" and 27 more named an
  absolute path, which a second maintainer cannot check either way
  (architecture#8, docs-accuracy#1).

So this is a WHOLE-REPO check, over every file `git ls-files` reports -- not
scripts/, not docs/: half the hits were in `tests/`, and one was in a shipped
JSON data file's `_source` key. It is deliberately a small set of literal rules
with a short, explicit allowlist, because a rule nobody can read is a rule that
gets broadened instead of obeyed.

It also holds the two CLI findings of the same package: a warning must reach ONE
stream as ONE block (first-run#4), and the README's onboarding transcript must
say what it is (first-run#7).
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib import log                                          # noqa: E402


# ---------------------------------------------------------------------------
# the tracked tree
# ---------------------------------------------------------------------------

def tracked_files() -> list[str]:
    """Every file git tracks here, so a new directory is covered for free."""
    try:
        out = subprocess.run(["git", "-C", str(SKILL), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):            # pragma: no cover
        raise unittest.SkipTest("git is not available")
    if out.returncode != 0:                                  # pragma: no cover
        raise unittest.SkipTest("not a git checkout")
    return [name for name in out.stdout.split("\0") if name]


def read(rel: str) -> str | None:
    """The file's text, or None when it is binary or unreadable."""
    try:
        return (SKILL / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

#: Placeholder home paths the repo uses on purpose: `/home/tester` in a host
#: fixture, `/home/someone` in a planned run, `/home/x` in a prompt-rendering
#: test, and `/home/<user>` where `run/manifest.py` prints what to set.
PLACEHOLDER_USERS = {"tester", "someone", "x", "user", "alice", "<user>"}

#: Host names this repo may print. Everything else is somebody's real machine:
#: `ffmain` was the v1 run host and appeared 34 times, including in `cf run`'s
#: own `--help` examples.
FORBIDDEN_HOSTS = ("ffmain",)

#: Addresses that name no host: the loopback, the three RFC 5737 documentation
#: ranges, and the link-local cloud-metadata address a security test proves the
#: errata fetcher refuses.
ALLOWED_ADDRESS_PREFIXES = ("127.", "0.0.0.0", "192.0.2.", "198.51.100.",
                            "203.0.113.", "169.254.")

_HOME = re.compile(r"/(?:Users|home)/(<?[A-Za-z0-9._-]+>?)")
#: A dotted quad here is almost always a book item number (`Exercise 2.3.4.1`),
#: so a candidate is read as an address only when an octet reaches 32: true of
#: every routable address and of none of the item numbers in the fixtures.
_QUAD = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")


def _homes(text: str) -> list[str]:
    return [m.group(0) for m in _HOME.finditer(text)
            if m.group(1) not in PLACEHOLDER_USERS]


def _addresses(text: str) -> list[str]:
    out = []
    for m in _QUAD.finditer(text):
        octets = [int(g) for g in m.groups()]
        if max(octets) < 32 or any(o > 255 for o in octets):
            continue                       # an item number, not an address
        if m.group(0).startswith(ALLOWED_ADDRESS_PREFIXES):
            continue
        out.append(m.group(0))
    return out


#: name -> (finder, why it may not be here). The name is what an allowlist entry
#: refers to, and what a failure prints.
RULES: dict[str, tuple] = {
    "absolute_home": (
        _homes,
        "an absolute home path resolves on one machine only; use a placeholder "
        "or an environment variable"),
    "desktop_home": (
        lambda text: re.findall(r"~/Desktop[\w./*-]*", text),
        "one person's Desktop layout is not a default anybody else can use"),
    "host_name": (
        lambda text: [h for h in FORBIDDEN_HOSTS if re.search(rf"\b{h}\b", text)],
        "a real host name (CLAUDE.md S1); the placeholder is `examplebox`"),
    "ip_address": (
        _addresses,
        "a real address (CLAUDE.md S1); use the documentation range 192.0.2.x"),
    "workflow_run_id": (
        lambda text: re.findall(r"\bwf_[0-9a-f]{8,}[\w-]*", text),
        "a Workflow run id is a session id: it identifies a run on one account "
        "(the eight the repo carried were `wf_` plus eight hex digits; a shorter "
        "fixture id such as `wf_ab12cd` names nothing)"),
    "session_uuid": (
        lambda text: re.findall(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", text),
        "a session uuid is a session id (CLAUDE.md S1)"),
    "transcript_path": (
        lambda text: re.findall(r"\b(?:scratchpad|recovered)/[\w./*-]*", text),
        "this repo has never held a `recovered/` or `scratchpad/` directory, so a "
        "citation into one is unresolvable for every reader"),
}

#: (file, rule, why). Four entries, and `test_every_allowlist_entry_is_needed`
#: fails on a fifth that has gone stale.
ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    ("tests/test_rf_scrub.py", "*",
     "this file spells out every pattern it forbids"),
    ("tests/test_rf_core.py", "desktop_home",
     "it asserts that the shipped files and the workspace template no longer "
     "name that directory, which means naming it"),
    ("tests/test_ledger_route.py", "transcript_path",
     "the workflows-only ancestor of this check, which names the prefix it refuses"),
    ("CLAUDE.md", "transcript_path",
     "S3 names the directory 44 docstrings once cited, so the rule can say what "
     "went wrong"),
)


def allowed(rel: str, rule: str) -> bool:
    return any(path == rel and name in ("*", rule) for path, name, _ in ALLOWLIST)


class TestNothingComesFromOneMachine(unittest.TestCase):
    """The rule CLAUDE.md S1 states, enforced over the whole tracked tree."""

    def test_no_tracked_file_carries_machine_local_content(self):
        for rule, (find, why) in sorted(RULES.items()):
            offenders: list[str] = []
            for rel in tracked_files():
                if allowed(rel, rule):
                    continue
                text = read(rel)
                if text is None:
                    continue
                for hit in find(text):
                    offenders.append(f"{rel}: {hit}")
            with self.subTest(rule=rule):
                self.assertEqual(offenders, [], f"{rule} -- {why}: "
                                                + "; ".join(offenders[:10]))

    def test_every_allowlist_entry_is_needed(self):
        """A stale exemption is how an allowlist becomes a second rulebook."""
        for rel, rule, why in ALLOWLIST:
            with self.subTest(file=rel, rule=rule):
                text = read(rel)
                self.assertIsNotNone(text, f"{rel} is gone; drop the entry")
                names = list(RULES) if rule == "*" else [rule]
                self.assertTrue(any(RULES[name][0](text) for name in names),
                                f"{rel} no longer matches {rule}; delete the "
                                f"allowlist entry ({why})")

    def test_the_rules_catch_what_they_are_written_for(self):
        """Each rule against the string it was added for, and a near miss."""
        home, desktop = RULES["absolute_home"][0], RULES["desktop_home"][0]
        self.assertTrue(home("repo: /Users/someone-real/tao-measure-lean"))
        self.assertTrue(home("examplebox:/home/realname/tao-attack-hunt/findings"))
        self.assertFalse(home("remote_root: /home/tester"))
        self.assertFalse(home('or "/home/<user>"'))
        self.assertTrue(desktop("cf onboard ~/Desktop/tao-measure-lean"))
        self.assertFalse(desktop("library_paths: [~/Downloads]"))
        address, run_id = RULES["ip_address"][0], RULES["workflow_run_id"][0]
        self.assertEqual(address("ssh operator@192.168.1.50"), ["192.168.1.50"])
        self.assertEqual(address("Exercise 2.3.4.1 and 2.3.4.10"), [])
        self.assertEqual(address("bind 127.0.0.1 or 192.0.2.7"), [])
        # a made-up id in the shape of a real one: the rule keys on eight hex
        # digits, so nothing here needs to be an id this account really ran.
        self.assertEqual(run_id("recovered/challenge-wf_0a1b2c3d-e4f.js"),
                         ["wf_0a1b2c3d-e4f"])
        self.assertEqual(run_id('"workflow_run_id": "wf_ab12cd"'), [])
        self.assertEqual(RULES["transcript_path"][0]("e.g. recovered/b174aa13.txt"),
                         ["recovered/b174aa13.txt"])


# ---------------------------------------------------------------------------
# a citation is a claim: the file has to be here
# ---------------------------------------------------------------------------

#: Directories whose name means "this repo". `bin/` is deliberately absent: a
#: `bin/worker-agent.sh` token names the generated tend root far more often than
#: it names this repo's shim.
REPO_ROOTS = ("scripts/", "references/", "docs/", "templates/", "tests/",
              "registry/", "workflows/", "evals/", ".github/")

_PATHISH = re.compile(
    r"[A-Za-z0-9_./*<>{}-]+"
    r"\.(?:py|md|sh|js|json|jsonl|yaml|yml|txt|tmpl|toml|lean|example)\b")

#: Tokens that look like this repo's paths and are not. Each one is a path in
#: ANOTHER tree that the prose is about.
FOREIGN_TOKENS: tuple[tuple[str, str], ...] = (
    ("scripts/verify_findings.py",
     "where the v1 gate lived INSIDE the worktree a worker commits to -- the "
     "reason this repo's gate is scripts/gate/verify_findings.py and runs out of tree"),
    ("scripts/update_status_table.py",
     "a companion repo's own validator, in an example `validators:` line"),
    ("scripts/gate.py",
     "a fixture path in a changed-file set the pre-merge check refuses"),
)

#: `docs/design/archive/` names the modules of the REJECTED model on purpose:
#: archiving a design record means keeping what it said, and those files were
#: never written. `tests/test_rf_docs.py` excludes it from its own path check
#: for the same reason.
ARCHIVE = "docs/design/archive/"


def cited_repo_paths(text: str) -> set[str]:
    found = set()
    for token in _PATHISH.findall(text):
        token = token.strip("`,;:()\"'")
        if token.startswith(REPO_ROOTS) and not any(
                token == foreign for foreign, _ in FOREIGN_TOKENS):
            found.add(token)
    return found


def resolves(token: str) -> bool:
    """Does it exist? A `<placeholder>` or `*` glob checks its fixed prefix."""
    keep: list[str] = []
    for part in Path(token).parts:
        if any(ch in part for ch in "<*{"):
            break
        keep.append(part)
    return True if not keep else (SKILL / Path(*keep)).exists()


class TestNoCitationOfAFileThatIsNotHere(unittest.TestCase):
    """43 docstrings cited four documents nobody wrote; 30 cited a lost tree."""

    def test_every_repo_path_a_tracked_file_cites_resolves(self):
        broken: list[str] = []
        for rel in tracked_files():
            if rel.startswith(ARCHIVE):
                continue
            text = read(rel)
            if text is None:
                continue
            for token in sorted(cited_repo_paths(text)):
                if not resolves(token):
                    broken.append(f"{rel}: {token}")
        self.assertEqual(broken, [], "cited but absent: " + "; ".join(broken[:10]))

    def test_each_foreign_token_is_still_used_and_still_foreign(self):
        for token, why in FOREIGN_TOKENS:
            with self.subTest(token=token):
                self.assertFalse((SKILL / token).exists(),
                                 f"{token} is in the tree now; drop the entry ({why})")
                hits = [rel for rel in tracked_files()
                        if (read(rel) or "").find(token) >= 0]
                self.assertTrue(hits, f"nothing cites {token} any more; drop the entry")


# ---------------------------------------------------------------------------
# first-run#4 -- one warning, one stream, one block
# ---------------------------------------------------------------------------

class _Recorder(io.StringIO):
    """A stream that remembers each write, so "one block" is checkable."""

    def __init__(self):
        super().__init__()
        self.writes: list[str] = []

    def write(self, text):                                   # noqa: D102
        self.writes.append(text)
        return super().write(text)


class TestAWarningIsOneBlockOnOneStream(unittest.TestCase):
    """`warn` wrote to stderr and `info` to stdout, so evidence was split off.

    `cf onboard <repo> 1>out 2>err` put "32 item tags rejected by the strict
    grammar; first few:" alone in `err` and the five rejected tags alone in
    `out`, sixty lines from the sentence that explained them.
    """

    HEADER = "32 item tags rejected by the strict grammar; first few:"
    LINES = ["    'Corollary (Lemma 1.2.5 + Exercise 1.2.4)'  (no_number)",
             "    'Unnumbered elementary-measure properties'  (unknown_kind)"]

    def test_the_header_and_its_evidence_land_on_stderr_together(self):
        out, err = io.StringIO(), _Recorder()
        with redirect_stdout(out), redirect_stderr(err):
            log.warn_block(self.HEADER, self.LINES)
        self.assertEqual(out.getvalue(), "", "a warning may not reach stdout")
        self.assertEqual(err.getvalue().splitlines(),
                         [f"warn {self.HEADER}"] + self.LINES)
        self.assertIn(self.LINES[-1], err.writes[0],
                      "the block must be ONE write: two cannot be interleaved")

    def test_quiet_silences_the_whole_block(self):
        log.set_level(0)
        self.addCleanup(log.set_level, 1)
        err = io.StringIO()
        with redirect_stderr(err):
            log.warn_block(self.HEADER, self.LINES)
        self.assertEqual(err.getvalue(), "")

    def test_a_real_command_keeps_its_warning_off_stdout(self):
        """`cf status` over an unreadable registry, with the streams separated."""
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="cf-scrub-",
                                     dir=os.environ.get("CF_TEST_TMP") or None))
        self.addCleanup(__import__("shutil").rmtree, root, ignore_errors=True)
        import cf as cf_main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cf_main.main(["init", str(root), "--no-git", "-q"]), 0)
        # the second `main` call loads the workspace again from disk, so the
        # broken registry is what `cf status` reads.
        (root / "registry.yaml").write_text("version: 1\nsources: [\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cf_main.main(["--workspace", str(root), "status"])
        stdout, stderr = out.getvalue(), err.getvalue()
        self.assertIn("could not be read", stderr)
        self.assertNotIn("could not be read", stdout)
        # the continuation -- the reason the file could not be read -- is the
        # half that used to go to stdout on its own.
        head = [l for l in stderr.splitlines() if "could not be read" in l][0]
        rest = stderr.splitlines()[stderr.splitlines().index(head) + 1]
        self.assertTrue(rest.strip(), "the warning's own explanation is missing")
        self.assertNotIn(rest.strip(), stdout)

    #: `cf run`'s disk warning is followed by "  transfer:", the first row of the
    #: PLAN table -- the command's own report, not the warning's evidence.
    SPLIT_EXEMPT = {("scripts/cfcmd/run.py", "  transfer:")}

    def test_no_warning_is_continued_by_an_indented_info_line(self):
        """The shape of the defect: `log.warn(...)` then `log.info("    ...")`."""
        offenders: list[str] = []
        for path in sorted(SCRIPTS.glob("**/*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            rel = str(path.relative_to(SKILL))
            for n, line in enumerate(lines):
                if not re.search(r"\blog\.warn\(", line):
                    continue
                nxt = next((l for l in lines[n + 1:n + 8]
                            if re.search(r"\blog\.(warn|warn_block|info|ok|error)\(", l)), "")
                match = re.search(r"""\blog\.info\(\s*f?["'](\s+\S[^"']*)""", nxt)
                if match and (rel, match.group(1)) not in self.SPLIT_EXEMPT:
                    offenders.append(f"{rel}:{n + 1} -> {match.group(1)!r}")
        self.assertEqual(offenders, [], "use log.warn_block(header, lines): "
                                        + "; ".join(offenders))


# ---------------------------------------------------------------------------
# first-run#7 -- the transcript says what it is
# ---------------------------------------------------------------------------

class TestTheOnboardingTranscriptSaysWhatItIs(unittest.TestCase):
    """It read as a promise (1,463 rows) while the tool printed 1,131."""

    @staticmethod
    def _caption_and_block() -> tuple[str, str]:
        text = (SKILL / "README.md").read_text(encoding="utf-8")
        head, _, rest = text.partition("cf onboard <path to a companion repo>\n```")
        caption, _, rest = rest.partition("```\n")
        block, _, _ = rest.partition("```")
        return caption, block

    def test_the_caption_dates_and_pins_the_run_it_shows(self):
        caption, block = self._caption_and_block()
        self.assertTrue(block.strip(), "the transcript block moved; fix this test")
        self.assertIn("abridged", caption.lower())
        self.assertRegex(caption, r"captured \d{4}-\d{2}-\d{2}")
        self.assertRegex(caption, r"`[0-9a-f]{7}`",
                         "name the companion commit the numbers belong to")
        self.assertIn("tests/e2e/synthetic.sh", caption,
                      "point at the transcript a reader can reproduce")

    def test_the_block_shows_no_path_from_one_machine(self):
        _caption, block = self._caption_and_block()
        for line in block.splitlines():
            self.assertNotRegex(line, r"(?<![\w<])/(Users|home)/",
                                "elide the paths: <companions>/<repo>/…")


class TestTheEndToEndScriptDefaultsToNoPath(unittest.TestCase):
    """It defaulted to two private checkouts, so it failed with someone's path."""

    SCRIPT = SKILL / "tests" / "e2e" / "multisource.sh"

    def test_it_names_the_variables_instead_of_defaulting(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        for var in ("CF_TML", "CF_MT", "CF_BASELINE_CORPUS"):
            self.assertIn(f'${{{var}:-}}', text,
                          f"{var} must default to empty, not to a path")

    def test_with_nothing_set_it_says_what_to_set(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CF_")}
        res = subprocess.run(["bash", str(self.SCRIPT)], capture_output=True,
                             text=True, env=env, cwd=str(SKILL), timeout=120)
        said = res.stdout + res.stderr
        self.assertEqual(res.returncode, 2, said)
        self.assertIn("CF_TML", said)
        self.assertIn("synthetic.sh", said, "name the half that needs no checkout")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
