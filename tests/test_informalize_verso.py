"""`cf informalize verso-command` and `verso_host.py`: the overlay's hovers.

New code, for docs/proposals/informalize.md section 10 (the Verso build the committed
`pdf-overlay/verso.json` is cut from).  No Lean and no host run here: the command writes
a folder and prints lines, the host script is read and checked against the drivers' rules
and `bash -n`, and the Python half is run on invented lakefiles and manifests, alone under
`python3 -I`.  What the file it ends with must look like is
`tests/test_overlay_verso.py`'s, which is where `verso_extract.py` is tested.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace (tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for entry in (str(HERE), str(SKILL / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from cflib import gitsafe                                     # noqa: E402
from corpus import universe_host                              # noqa: E402
from informalize import library, verso_host                   # noqa: E402
from test_informalize_library import (HOST, ID, LIB, MODULE, SSH, TOOLCHAIN,  # noqa: E402
                                      _Workspace, modern_bash, scratch)

VERSO_SCRIPT = SKILL / "scripts" / "informalize" / "verso_host.sh"
LIBRARY_SCRIPT = SKILL / "scripts" / "informalize" / "library_host.sh"
DRIVER = SKILL / "scripts" / "corpus" / "build_universe_host.sh"
TOOL = SKILL / "scripts" / "informalize" / "verso_host.py"

LAKEFILE = """\
name = "ExampleLib"
defaultTargets = ["ExampleLib"]

[[require]]
name = "mathlib"
git = "https://github.com/leanprover-community/mathlib4"
rev = "master"

[[lean_lib]]
name = "ExampleLib"
"""


def manifest(**revs: str) -> str:
    return json.dumps({"version": "1.1.0",
                       "packages": [{"name": name, "rev": rev} for name, rev in revs.items()]})


def bundle(cards: int = 2, modules: tuple[str, ...] = ("ExampleLib.B", "ExampleLib.A")) -> bytes:
    """An annotation bundle of `cards` cards, as `cf overlay sync` writes one: the command
    reads nothing of a card but how many there are, the host script its module."""
    rows = [{"path": f"annotations/1/theorem-1.{n}/key__ExampleLib.thm{n}.json",
             "annotation": {"id": f"theorem-1.{n}", "kind": "theorem",
                            "object": [{"location": {"declaration": f"ExampleLib.thm{n}",
                                                     "module": modules[n % len(modules)]}}]}}
            for n in range(1, cards + 1)]
    return json.dumps({"schema": "formal-companion/overlay-annotations", "schema_version": 1,
                       "directory": "annotations", "count": len(rows),
                       "annotations": rows}).encode("utf-8")


# ---------------------------------------------------------------------------
# the host script
# ---------------------------------------------------------------------------

class TestTheVersoHostScript(unittest.TestCase):

    def setUp(self):
        self.text = VERSO_SCRIPT.read_text(encoding="utf-8")

    def test_it_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(VERSO_SCRIPT)]).returncode, 0)

    def test_its_clean_environment_and_git_pins_are_the_drivers(self):
        driver = DRIVER.read_text(encoding="utf-8")

        def function(text: str, name: str) -> str:
            start = text.index(f"\n{name}() {{\n")
            return text[start:text.index("\n}\n", start) + 3]
        for name in ("cenv", "heavy"):
            with self.subTest(function=name):
                self.assertEqual(function(self.text, name), function(driver, name))
        body = re.search(r"^GIT_SAFE_CONFIG=\((.*?)\)", self.text, re.S | re.M).group(1)
        self.assertEqual(body.split(), list(gitsafe.SAFE_CONFIG))
        self.assertIn("py() { cenv python3 -I \"$@\"; }", self.text)

    def test_it_writes_nothing_in_the_i1_tree(self):
        # $ATLAS_ROOT is read: the checkout I1 built, which is copied, and never a target.
        # `i1_lock=` names I1's lock file, which the copy takes -- opened for reading, so
        # even that creates nothing there.
        uses = [line.strip() for line in self.text.splitlines()
                if "$ATLAS_ROOT" in line and not line.lstrip().startswith("#")]
        self.assertTrue(uses)
        for line in uses:
            with self.subTest(line=line):
                self.assertTrue(line.startswith(("[", "case", "built=", "i1_lock=", "die",
                                                 "fail"))
                                or "!=" in line, line)
        self.assertIn('[ "$ATLAS_ROOT" != "$VERSO_ROOT" ]', self.text)
        self.assertIn('exec 9<"$i1_lock"', self.text)
        self.assertNotIn('exec 9>"$i1_lock"', self.text)

    def test_the_run_is_locked_and_ends_with_done(self):
        text = self.text
        lock, stale = text.index("flock -n 8 ||"), text.index('rm -f "$host_dir/DONE"')
        trap = text.index("trap 'rc=$?; printf")
        self.assertLess(lock, stale)
        self.assertLess(stale, trap)
        self.assertLess(trap, text.index('require --lakefile'))
        self.assertLess(trap, text.index('lake build "$VERSO_TARGET"'))

    def test_it_renders_the_cards_modules_and_keeps_the_librarys_own_choice(self):
        text = self.text
        self.assertLess(text.index("ls-files --error-unmatch literate.toml"),
                        text.index('lake build "$VERSO_TARGET"'))
        self.assertIn('targets --bundle "$bundle" --out "$work/literate.toml"', text)
        self.assertIn("the library has a literate.toml of its own", text)

    def test_the_pins_are_checked_before_anything_is_built(self):
        text = self.text
        self.assertLess(text.index("moved --before"), text.index("heavy lake build"))
        self.assertIn("ulimit -n 65536", text)

    def test_the_open_file_limit_is_raised_before_the_cache_is_fetched(self):
        # Mathlib's cache unpacker needs it, so it comes before `lake exe cache get` and not
        # after -- as it does in the universe driver, which says why.
        text = self.text
        self.assertLess(text.index("ulimit -n 65536"), text.index("heavy lake exe cache get"))
        self.assertIn("Mathlib's cache unpacker needs it", text)
        self.assertIn("Mathlib's cache unpacker needs it", DRIVER.read_text(encoding="utf-8"))

    def test_it_keeps_the_drivers_free_space_floor(self):
        text = self.text
        floor = re.search(r"^MIN_FREE_GB=\$\{MIN_FREE_GB:-(\d+)\}", text, re.M)
        driver_floor = re.search(r"^MIN_FREE_GB=\$\{MIN_FREE_GB:-(\d+)\}",
                                 DRIVER.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(floor)
        self.assertEqual(floor.group(1), driver_floor.group(1))
        self.assertIn("MIN_FREE_GB must be a whole number of GiB", text)
        # every heavy step is behind the floor, with the GiB it may add
        for what in ("the clone of $lib", "the Mathlib cache", "the build of $lib",
                     "the literate pages of $lib"):
            with self.subTest(step=what):
                self.assertRegex(text, rf'ensure_space \d+ "{re.escape(what)}')
        # the copy of I1's whole built checkout is measured, not estimated, and the floor is
        # checked before the copy runs, not after
        self.assertIn('ensure_space "$built_gb" "the copy of I1\'s checkout', text)
        self.assertLess(text.index('ensure_space "$built_gb"'), text.index('cp -a "$built"'))

    def test_every_file_it_runs_is_shipped(self):
        shipped = set(library.verso_host_files())
        for rel in ("scripts/corpus/universe_host.py", "scripts/corpus/probe_merge.py",
                    "scripts/informalize/verso_host.sh",
                    "scripts/informalize/verso_host.py",
                    "templates/overlay/runtime/tools/verso_extract.py",
                    "templates/overlay/runtime/tools/overlay_annotations.py"):
            with self.subTest(file=rel):
                self.assertIn(rel, shipped)
        self.assertEqual(shipped, set(library.VERSO_HOST_FILES))
        # the extractor is the overlay's own file, byte for byte
        self.assertIn("verso_extract.py --pages",
                      (SKILL / "templates/overlay/runtime/tools/verso_extract.py")
                      .read_text(encoding="utf-8"))

    def test_it_says_how_it_is_run(self):
        self.assertIn("ATLAS_ROOT=$PWD/library VERSO_ROOT=$PWD/verso VERSO_REV=", self.text)
        self.assertIn("docs/proposals/informalize.md section 10", self.text)
        self.assertIn("docs/overlay.md section 5", self.text)
        # the two host scripts keep one spelling of the log and the exit status
        for line in ('trap \'rc=$?; printf "%s\\n" "$rc" > "$host_dir/DONE"; exit "$rc"\' EXIT',):
            self.assertIn(line, LIBRARY_SCRIPT.read_text(encoding="utf-8"))
            self.assertIn(line, self.text)


# ---------------------------------------------------------------------------
# the Python half
# ---------------------------------------------------------------------------

class TestTheVersoRequire(unittest.TestCase):

    def setUp(self):
        self.root = scratch(self)
        self.lakefile = self.root / "lakefile.toml"
        self.lakefile.write_text(LAKEFILE, encoding="utf-8")

    def test_it_goes_before_the_librarys_own_first_require(self):
        said = verso_host.add_require(self.lakefile, "v4.34.0")
        self.assertIn("before the first require", said)
        text = self.lakefile.read_text(encoding="utf-8")
        self.assertLess(text.index('name = "verso"'), text.index('name = "mathlib"'))
        self.assertIn('rev = "v4.34.0"', text)
        self.assertEqual(verso_host.verso_rev(text), "v4.34.0")
        # it is idempotent, and it is the same file again
        again = verso_host.add_require(self.lakefile, "v4.34.0")
        self.assertIn("already requires", again)
        self.assertEqual(self.lakefile.read_text(encoding="utf-8"), text)

    def test_a_lakefile_that_requires_nothing_gets_it_at_the_end(self):
        self.lakefile.write_text('name = "ExampleLib"\n', encoding="utf-8")
        self.assertIn("at the end", verso_host.add_require(self.lakefile, "main"))
        self.assertEqual(verso_host.verso_rev(self.lakefile.read_text(encoding="utf-8")), "main")

    def test_another_verso_a_bad_revision_and_a_missing_lakefile_are_refused(self):
        verso_host.add_require(self.lakefile, "v4.34.0")
        with self.assertRaises(verso_host.HostError) as caught:
            verso_host.add_require(self.lakefile, "v4.35.0")
        self.assertIn("VERSO_REV=v4.34.0", str(caught.exception))
        with self.assertRaises(verso_host.HostError):
            verso_host.add_require(self.lakefile, "v4.35.0; rm -rf /")
        with self.assertRaises(verso_host.HostError) as caught:
            verso_host.add_require(self.root / "lakefile.lean", "v4.34.0")
        self.assertIn("add it by hand", str(caught.exception))

    def test_the_pins_a_verso_may_not_move(self):
        before = {"mathlib": "a" * 40, "Cli": "b" * 40}
        self.assertEqual(verso_host.moved(before, {**before, "verso": "c" * 40}), [])
        self.assertEqual(verso_host.moved(before, {"mathlib": "a" * 40, "Cli": "d" * 40}),
                         ["Cli: bbbbbbbbbbbb -> dddddddddddd"])
        self.assertEqual(verso_host.moved(before, {"mathlib": "a" * 40}),
                         ["Cli: bbbbbbbbbbbb -> gone"])

    def test_the_modules_rendered_are_the_ones_the_cards_are_in(self):
        path = self.root / "bundle.json"
        path.write_bytes(bundle(4))
        self.assertEqual(verso_host.card_modules(path), ["ExampleLib.A", "ExampleLib.B"])
        out = self.root / "literate.toml"
        self.assertEqual(verso_host.write_targets(path, out), ["ExampleLib.A", "ExampleLib.B"])
        text = out.read_text(encoding="utf-8")
        self.assertIn('[[targets]]\nmodule = "ExampleLib.A"\n', text)
        self.assertIn('[[targets]]\nmodule = "ExampleLib.B"\n', text)
        self.assertIn("verso-command", text)
        path.write_bytes(json.dumps({"annotations": [{"path": "a", "annotation": {}}]})
                         .encode("utf-8"))
        with self.assertRaises(verso_host.HostError) as caught:
            verso_host.card_modules(path)
        self.assertIn("names a Lean module", str(caught.exception))

    def test_it_runs_alone_under_python_dash_i(self):
        def run(*argv: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-I", str(TOOL), *argv], capture_output=True,
                                  text=True, env={"PATH": "/usr/bin:/bin"})
        path = self.root / "lake-manifest.json"
        path.write_text(manifest(mathlib="a" * 40, Cli="b" * 40), encoding="utf-8")
        got = run("pins", "--manifest", str(path))
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(json.loads(got.stdout), {"mathlib": "a" * 40, "Cli": "b" * 40})
        before = self.root / "before.json"
        before.write_text(got.stdout, encoding="utf-8")
        self.assertEqual(run("moved", "--before", str(before), "--manifest", str(path)).returncode,
                         0)
        path.write_text(manifest(mathlib="a" * 40, Cli="c" * 40), encoding="utf-8")
        moved = run("moved", "--before", str(before), "--manifest", str(path))
        self.assertEqual(moved.returncode, 1)
        self.assertIn("Cli: bbbbbbbbbbbb -> cccccccccccc", moved.stdout)
        self.assertEqual(run("require", "--lakefile", str(self.lakefile), "--rev",
                             "v4.34.0").returncode, 0)
        cards = self.root / "bundle.json"
        cards.write_bytes(bundle())
        got = run("targets", "--bundle", str(cards), "--out", str(self.root / "literate.toml"))
        self.assertEqual((got.returncode, got.stdout.strip()),
                         (0, "literate.toml: 2 module(s) the cards name"), got.stderr)
        path.write_text("{]", encoding="utf-8")
        broken = run("pins", "--manifest", str(path))
        self.assertEqual(broken.returncode, 2)
        self.assertIn("is not JSON", broken.stderr)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

class TestVersoCommand(_Workspace):

    def setUp(self):
        super().setUp()
        # what stage I1 left in the workspace, and what stage I7 left in the repository
        host = self.paths.informalize_library(ID) / "host"
        host.mkdir(parents=True)
        (host / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
        overlay = self.repo / "pdf-overlay"
        overlay.mkdir()
        (overlay / "annotations.bundle.json").write_bytes(bundle())

    def verso(self, *extra: str, code: int = 0) -> str:
        return self.cf("informalize", "verso-command", ID, "--host", HOST, *extra, code=code)

    def test_it_writes_what_the_host_needs_and_prints_the_lines(self):
        out = self.verso()
        to_host = self.paths.informalize_verso_to_host(ID)
        for rel in library.verso_host_files():
            with self.subTest(file=rel):
                self.assertEqual((to_host / "drivers" / rel).read_bytes(),
                                 (SKILL / rel).read_bytes())
        (entry,) = universe_host.load_universe(to_host / "library.tsv")
        self.assertEqual((entry.id, entry.ref, entry.roots_spec), (LIB, self.commit, MODULE))
        self.assertIn(f"ssh -n {SSH} 'mkdir -p {self.host}/verso/host'", out)
        self.assertIn(f"rsync -a --delete {os.path.realpath(to_host)}/drivers/ "
                      f"{SSH}:{self.host}/verso/drivers/", out)
        self.assertIn(f"{SSH}:{self.host}/verso/annotations.bundle.json", out)
        start = [line for line in out.splitlines() if "setsid -f" in line]
        self.assertEqual(len(start), 1)
        self.assertIn(f"cd {self.host} && mkdir -p verso/host && setsid -f env "
                      'ATLAS_ROOT="$PWD/library" VERSO_ROOT="$PWD/verso" VERSO_REV=v4.34.0 '
                      "MIN_FREE_GB=60 "
                      "LEAN_THREADS=16 bash verso/drivers/scripts/informalize/verso_host.sh "
                      "verso/library.tsv verso/annotations.bundle.json >> "
                      "verso/host/verso_host.log 2>&1 < /dev/null", start[0])
        # the two cards it is cut to, and the step that follows
        self.assertIn("2 cards", out)
        self.assertIn("pdf-overlay/verso.json", out)
        self.assertIn("serve.py --build-only", out)
        script = (to_host / "verso-command.sh").read_text(encoding="utf-8")
        self.assertIn(start[0], script)
        self.assertEqual(subprocess.run(["sh", "-n", str(to_host / "verso-command.sh")])
                         .returncode, 0)
        self.assertIn("nothing ran", out)

    def test_the_revision_is_the_librarys_toolchain_unless_it_is_named(self):
        self.assertIn("VERSO_REV=v4.34.0", self.verso())
        self.assertIn("VERSO_REV=v4.36.0-rc2", self.verso("--verso-rev", "v4.36.0-rc2"))
        self.assertIn("LEAN_THREADS=4", self.verso("--lean-threads", "4"))
        self.assertIn("MIN_FREE_GB=120", self.verso("--min-free-gb", "120"))
        self.verso("--verso-rev", "not a rev", code=2)
        self.verso("--lean-threads", "0", code=2)
        self.verso("--min-free-gb", "-1", code=2)

    def test_a_toolchain_that_is_not_a_release_has_no_tag_to_default_to(self):
        # Verso is tagged once per Lean release, so `v<version>` answers for a release and
        # for nothing else: a nightly must not be printed as `VERSO_REV=vnightly-...`.
        toolchain = self.paths.informalize_library(ID) / "host" / "lean-toolchain"
        for text in ("leanprover/lean4:nightly-2026-01-01",
                     "leanprover/lean4-pr-releases:pr-release-1234"):
            with self.subTest(toolchain=text):
                toolchain.write_text(text + "\n", encoding="utf-8")
                out = self.verso(code=2)
                self.assertIn("nothing says which Verso revision", out)
                self.assertIn("not a released version", out)
                self.assertIn("--verso-rev", out)
                self.assertNotIn("VERSO_REV=v" + text.split(":")[-1], out)
                # naming the commit Verso built against it is the way through
                self.assertIn("VERSO_REV=" + "c" * 40, self.verso("--verso-rev", "c" * 40))

    def test_without_a_built_library_it_says_what_names_the_revision(self):
        (self.paths.informalize_library(ID) / "host" / "lean-toolchain").unlink()
        out = self.verso(code=2)
        self.assertIn("nothing says which Verso revision", out)
        self.assertIn("--verso-rev", out)
        # the revision alone is enough: the build clones the library itself
        self.assertIn("VERSO_REV=v4.34.0", self.verso("--verso-rev", "v4.34.0"))

    def test_without_the_overlays_bundle_it_names_the_command_that_writes_it(self):
        (self.repo / "pdf-overlay" / "annotations.bundle.json").unlink()
        out = self.verso(code=2)
        self.assertIn("pdf-overlay/annotations.bundle.json", out)
        # the command that really writes it, not `cf informalize overlay`, which will run it
        # but is not implemented yet (docs/proposals/informalize.md section 11)
        self.assertIn("cf overlay export --convention informal-markers", out)

    def test_a_host_off_the_allowlist_is_refused(self):
        self.assertIn("allowlist", self.cf("informalize", "verso-command", ID, "--host",
                                           "otherbox", code=2))


# ---------------------------------------------------------------------------
# step 1 on the host, run as the host runs it
# ---------------------------------------------------------------------------

#: the stub `lake`: every invocation fails, so the run stops at `lake update verso` (exit 4)
#: and step 1 -- the copy of I1's checkout, which costs the host the most -- has run whole
LAKE = """#!/bin/sh
echo "lake $*" >> "$HOME/lake.log"
exit 9
"""


class TestStepOneOnTheHost(TestVersoCommand):
    """The copy of stage I1's built checkout, its disk floor and I1's lock, run from a copy
    of what verso-command wrote, with a stub `lake`: no Lean and no network, and the run
    stops at `lake update verso` once step 1 is done."""

    def setUp(self):
        self.bash = modern_bash()
        if self.bash is None:
            self.skipTest("the host drivers need bash 4.3 or later")
        # the library as stage I1 left it on the host: a real checkout with a built .lake
        built = scratch(self) / "built"
        built.mkdir(parents=True)
        for rel, text in {"ExampleLib.lean": "", "lakefile.toml": LAKEFILE,
                          "lake-manifest.json": manifest(mathlib="a" * 40, Cli="b" * 40),
                          "lean-toolchain": TOOLCHAIN + "\n"}.items():
            (built / rel).write_text(text, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
               "GIT_CONFIG_NOSYSTEM": "1"}
        for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "pin"]):
            subprocess.run(["git", "-C", str(built), *argv], check=True, env=env,
                           capture_output=True)
        self.commit = subprocess.run(["git", "-C", str(built), "rev-parse", "HEAD"], check=True,
                                     capture_output=True, text=True).stdout.strip()
        # what the build left behind, which the copy must bring with it
        (built / ".lake" / "build").mkdir(parents=True)
        (built / ".lake" / "build" / "built-by-i1").write_text("x", encoding="utf-8")
        self.built = built
        super().setUp()

    def drive(self, *, free: str = "9999G", floor: str = "60",
              hold_i1_lock: bool = False) -> subprocess.CompletedProcess:
        """verso-command's two rsyncs as a local copy, then the printed line's job."""
        self.verso()
        to_host = self.paths.informalize_verso_to_host(ID)
        shutil.copytree(to_host / "drivers", self.host / "verso" / "drivers")
        shutil.copy(to_host / "library.tsv", self.host / "verso" / "library.tsv")
        shutil.copy(self.repo / "pdf-overlay" / "annotations.bundle.json",
                    self.host / "verso" / "annotations.bundle.json")
        atlas = self.host / "library"
        shutil.copytree(self.built, atlas / "libs" / LIB, symlinks=True)
        (atlas / "state").mkdir(parents=True)
        (atlas / "state" / "library_host.lock").write_text("", encoding="utf-8")
        home = self.host / "home"
        bindir = home / ".elan" / "bin"
        bindir.mkdir(parents=True)
        for name, body in (("lake", LAKE), ("ionice", 'shift; exec "$@"'),
                           ("df", 'echo " Avail"; for a in "$@"; do case "$a" in -*) ;; '
                                  f'*) echo " {free}";; esac; done')):
            (bindir / name).write_text(body if body.startswith("#!") else f"#!/bin/sh\n{body}\n",
                                       encoding="utf-8")
            (bindir / name).chmod(0o755)
        for tool, target in (("python3", sys.executable), ("git", shutil.which("git")),
                             ("bash", self.bash)):
            (bindir / tool).symlink_to(target)
        env = {"HOME": str(home), "USER": "tester", "PATH": f"{bindir}:/usr/bin:/bin",
               "ATLAS_ROOT": str(atlas), "VERSO_ROOT": str(self.host / "verso"),
               "VERSO_REV": "v4.34.0", "MIN_FREE_GB": floor, "LEAN_THREADS": "2"}
        argv = [self.bash, "verso/drivers/scripts/informalize/verso_host.sh",
                "verso/library.tsv", "verso/annotations.bundle.json"]
        if hold_i1_lock:   # a stage I1 build running now, holding its own lock
            argv = ["flock", str(atlas / "state" / "library_host.lock"), "-c",
                    " ".join(shlex.quote(a) for a in argv)]
        return subprocess.run(argv, cwd=self.host, env=env, capture_output=True, text=True,
                              timeout=300)

    def log(self, proc) -> str:
        host = self.host / "verso" / "host"
        text = proc.stdout + proc.stderr
        for path in sorted(host.glob("*.log")) if host.is_dir() else ():
            text += f"\n--- {path.name}\n" + path.read_text(encoding="utf-8", errors="replace")
        return text

    def test_it_copies_i1s_built_checkout_and_stops_at_the_verso_update(self):
        proc = self.drive()
        logs = self.log(proc)
        # step 1 ran whole; `lake update verso` is the first thing that needed Lean
        self.assertEqual(proc.returncode, 4, logs)
        self.assertIn("lake update verso failed", logs)
        work = self.host / "verso" / "libs" / LIB
        self.assertEqual((work / ".lake" / "build" / "built-by-i1").read_text(), "x")
        self.assertTrue((work / ".git").is_dir())
        self.assertFalse((work.parent / f"{LIB}.partial").exists())
        self.assertIn("copy I1's checkout", logs)
        self.assertIn("free before the copy of I1's checkout", logs)
        # it wrote nothing in I1's tree, the lock file included
        self.assertEqual(sorted(p.name for p in (self.host / "library" / "state").iterdir()),
                         ["library_host.lock"])
        self.assertEqual((self.host / "library" / "state" / "library_host.lock").read_text(), "")
        self.assertEqual((self.host / "verso" / "host" / "DONE").read_text(), "4\n")

    def test_it_stops_above_the_floor_rather_than_fill_the_disk(self):
        proc = self.drive(free="70G", floor="100")
        logs = self.log(proc)
        self.assertEqual(proc.returncode, 3, logs)
        self.assertIn("the floor is 100G", logs)
        # nothing was copied: the floor is checked before the copy, not after
        self.assertFalse((self.host / "verso" / "libs" / LIB).exists())

    def test_it_does_not_copy_a_tree_stage_i1_is_still_writing(self):
        proc = self.drive(hold_i1_lock=True)
        logs = self.log(proc)
        self.assertEqual(proc.returncode, 3, logs)
        self.assertIn("stage I1 is building", logs)
        self.assertFalse((self.host / "verso" / "libs" / LIB).exists())


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
