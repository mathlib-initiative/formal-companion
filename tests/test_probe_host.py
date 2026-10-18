"""scripts/corpus/probe_host.sh, run against stub Lean tools.

New code, for docs/proposals/wikipedia-locate.md sections 2.3 to 2.5
(universes, library indexes, the probe): the driver the operator runs on the
run host.  The driver runs SHARD_COUNT ``lake env lean`` shards in the
background; these tests stand a stub ``lake`` in for Lean (on the clean PATH
the driver gives its shards, under a temporary HOME) and check that a failed
shard is reported by number on a bash older than 5.1, and that no shard
outlives the driver.  No Lean, no network.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE_HOST = HERE.parent / "scripts" / "corpus" / "probe_host.sh"

#: `lake env true` passes; `lake env lean <probe>` records its pid, fails for
#: the shard named in $HOME/fail, and otherwise sleeps $HOME/sleep seconds
STUB_LAKE = """#!/bin/sh
[ "$1" = env ] && shift
[ "$1" = true ] && exit 0
echo $$ >> "$HOME/lean.pids"
[ "$SHARD_INDEX" = "$(cat "$HOME/fail" 2>/dev/null)" ] && exit 1
exec sleep "$(cat "$HOME/sleep" 2>/dev/null || echo 1)"
"""

#: the `wait` of bash 4.4 and 5.0, which have no -p (bash 5.1 NEWS)
OLD_WAIT = """wait() {
  local a
  for a in "$@"; do
    if [ "$a" = -p ]; then echo "wait: -p: invalid option" >&2; return 2; fi
  done
  builtin wait "$@"
}
"""


class TestTheProbeDriver(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-probe-host-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        (self.home / ".elan" / "bin").mkdir(parents=True)
        lake = self.home / ".elan" / "bin" / "lake"
        lake.write_text(STUB_LAKE)
        lake.chmod(0o755)
        self.checkout = self.tmp / "alpha"
        (self.checkout / ".lake" / "build").mkdir(parents=True)
        (self.checkout / "lean-toolchain").write_text("leanprover/lean4:v4.0.0\n")
        self.old_bash = self.tmp / "old-bash.sh"
        self.old_bash.write_text(OLD_WAIT)
        self.addCleanup(self.kill_shards)

    def pids(self) -> list[int]:
        path = self.home / "lean.pids"
        return [int(x) for x in path.read_text().split()] if path.exists() else []

    def live_shards(self) -> list[int]:
        out = []
        for pid in self.pids():
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                continue
            if state != "Z":
                out.append(pid)
        return out

    def kill_shards(self) -> None:
        for pid in self.live_shards():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CF_", "GIT_CONFIG_", "SHARD_", "MAX_PARALLEL", "PROBE_"))}
        env.update(HOME=str(self.home), BASH_ENV=str(self.old_bash), **extra)
        return env

    def argv(self) -> list[str]:
        return ["bash", str(PROBE_HOST), str(self.checkout), "alpha", "Alpha",
                str(self.tmp / "out")]

    def test_a_failed_shard_is_named_on_a_bash_without_wait_p(self):
        """OPS-10: `wait -n -p` is bash 5.1; on 4.4 and 5.0 the driver died at once."""
        (self.home / "fail").write_text("1")
        (self.home / "sleep").write_text("1")
        proc = subprocess.run(self.argv(), capture_output=True, text=True, timeout=120,
                              env=self.env(SHARD_COUNT="3", MAX_PARALLEL="2"))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("shard 1 failed", proc.stderr)
        self.assertNotIn("unbound variable", proc.stderr)
        self.assertNotIn("shard 0 failed", proc.stderr)
        self.assertEqual(len(self.pids()), 3, "a shard never ran")
        self.assertEqual(self.live_shards(), [], "a shard outlived the driver")

    def test_a_driver_that_is_stopped_takes_its_shards_with_it(self):
        (self.home / "sleep").write_text("60")
        proc = subprocess.Popen(self.argv(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, env=self.env(SHARD_COUNT="3", MAX_PARALLEL="3"))
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 60
        while len(self.pids()) < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(len(self.pids()), 3, "the shards never started")
        proc.send_signal(signal.SIGTERM)        # what the universe driver's timeout sends
        proc.wait(timeout=30)
        proc.stderr.close()
        deadline = time.monotonic() + 10
        while self.live_shards() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(self.live_shards(), [], "a shard outlived the stopped driver")


if __name__ == "__main__":
    unittest.main()
