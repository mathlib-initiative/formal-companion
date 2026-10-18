# Server runbook — orchestrator + hosted-agent bridge

How a fleet of hundreds of read-heavy tasks actually runs, distilled from the five run roots of the
2026-09 audit (three differential variants, two attack variants) and the RUNBOOK addendum written
after the run moved to the server. Written as: **what `cf` automates**, **what you check**, and
**what stays manual**.

No host address, account or profile name appears in this file. They live in `workspace.yaml`, and
`cf run` refuses any host not in its allowlist.

**Contents** — §0 the two tools this needs · §1 the topology · §2 preconditions ·
§3 `cf plan-run` · §4 `cf run` · §5 monitoring · §6 failure modes · §7 `cf harvest` ·
§8 the local fallback · §9 a locate root · §10 what stays manual · §11 sources.

---

## 0. The two tools this needs, and where they come from

Everything in this file depends on two programs that **do not ship with this skill**:

- **`tend`** — the orchestrator. It owns a sqlite state database, a worktree per task, the
  retry/merge policy and the validation hooks. `cf plan-run` writes a *tend root* (a `config.yaml`,
  `.tend/*.yaml` and a `bin/` of bridges) for it; `cf run` copies that root to the host and starts
  it. Roughly 4,400 lines under `scripts/plan/` and `scripts/run/` exist to produce and drive it.
- **`agentbox`** — the hosted-session runner the bridges shell out to
  (`agentbox host claude --profile <profile> -- -p … --output-format json`, or
  `agentbox host codex … exec -m <model>`). It is what turns a rendered prompt into an agent turn.

**Where they come from: not recorded here.** Neither tool is vendored, pinned, installed or
version-checked by anything in this repository, and the repo does not record the version the 2026-09
runs used. Do not infer one from this file. The only version contract that exists is the one *you*
write: put a spec in your own `workspace.yaml` as `hosts.<host>.tend_version` (for example
`">=0.4,<0.6"`), and `cf run`'s preflight compares it against `tend --version` on the host and
refuses the run on a mismatch. With no spec recorded, preflight only prints the version and
continues — which is how a config-schema change becomes a run that validates locally and fails on
the host with no diagnosis. Every tend command runs as `cd <tend_dir> && uv run …`, with
`tend_dir` from `hosts.<host>.tend_dir` (default `$HOME/tend`, whatever `remote_root` is); set
`hosts.<host>.tend_cmd` instead to replace the whole prefix.

**Without them**, the supported path is the local Workflow fleet (§8) for anything under ~100
items, plus every mechanical stage — `pair`, `detect-proxies`, the anchor check, `import-corpus`,
`tally`, `route`, `report` — which needs neither tool and produced the whole def-coverage baseline.

**`cf run --execute` has never been exercised against this pair from this repo**: the five runs
that produced the baseline corpus pre-date the code that drives them. Treat the first server run as
a smoke test (§4.4), not as a resumption.

---

## 1. The topology

```
tend (orchestrator)                       entrypoint repo (git, main)
  ├─ worker agent ──► bridge ──► hosted Claude/codex session
  │                                   └─ writes findings/<task-id>.json + task yaml, commits
  ├─ validation gate  (mechanical, OUT OF TREE)
  ├─ reviewer agent ──► bridge ──► hosted session   (refute, not rubber-stamp)
  └─ pre-merge gate  → merge into main
```

- The orchestrator owns state (a sqlite database), worktrees, retries and merging. Its pipeline
  **worker → validation → reviewer → merge** maps exactly onto the audit's
  *probe → prove the quote is real → adversarially challenge → accept*.
- The **bridge scripts are the agent runtime**: `bin/worker-agent.sh` and `bin/reviewer-agent.sh`
  read the rendered prompts, shell out to a hosted session (`agentbox host claude --profile
  <profile> -- -p … --output-format json`, or `agentbox host codex … exec -m <model>` for the
  second model in a calibration pair), and pipe the result through `normalize_result.py`. This is
  legitimate under the orchestrator's contract, which accepts any executable that follows the
  stdout JSON contract.
- The five audited configs differ in exactly four places: **entrypoint path**, **gate `KINDS`**,
  **prompt add-ons** (page images; "no citation findings" + witness required), **bridge**
  (Claude vs codex/GPT), and **concurrency** (8/5 for the first differential, 6/4 for the image and
  GPT variants, 5/3 for both attack runs). Everything else — validation commands, merge target,
  batched merge, worktree cleanup — was identical. That is why a run is a *preset* in `run.yaml`
  rather than a new tree.

## 2. Preconditions — all manual, none automated

1. **ssh access to the host**, and the host listed in `workspace.yaml`'s allowlist.
2. **The hosted-agent profile is logged in on the server.** `cf` cannot log it in, and a stale login
   fails every task identically.
3. **The box owner's consent for a heavy job.** The audited box was shared; heavy jobs were
   coordinated by asking first. `cf run` requires `--i-have-consent` and records who consented, when
   and for what, in `run.yaml`. Without the flag it refuses.
4. **Source text and errata on the host, outside every repo** (copyrighted, and the gate reads
   them): `sources/<source_id>/` per source the run touches, transferred read-only.
   Page images too, for an image-enabled run.
5. **Source repos cloned read-only** on the host. "READ-ONLY" in a prompt is an instruction, not a
   control: copy or mount them read-only.
6. **Disk budget.** The orchestrator creates a worktree per ready task as soon as it is ready:
   382 worktrees took 1.1 GB within minutes even with one worker allowed. Budget
   `tasks × worktree size`, or gate with `depends_on` / copy-on-write copies.
7. **A billing decision.** When agents run inside a hosted session the orchestrator's usage counters
   read zero by construction and its budget command cannot cap the run; the only enforceable cap is
   gateway-side. Decide and record it before raising concurrency.
8. **git 2.31 or later on the host.** The one version floor this skill has. The gate blanks the
   content-filter drivers a worktree names, and a driver whose own name carries an `=` is reachable
   only through `GIT_CONFIG_COUNT`, which arrived in 2.31 — `git -c` splits its argument at the
   first `=`. Below that there is no channel left, so the gate stops (exit 3, "the gate could not
   run") rather than execute the program a worker named, and every task blocks. Debian 11 ships
   2.30.2. `cf doctor` reports the version and warns below the floor.

## 3. `cf plan-run` — what it writes

```
cf plan-run --preset attack-vac --companion ours --host <alias> --i-have-consent
```

- `runs/<run-id>/run.yaml` — mode, companion set, model ids, book access, gate mode, host alias,
  concurrency, consent record, billing path, task count, prompt shas.
- `runs/<run-id>/tasks/*.yaml` — one per item, title prefixed `DIFFERENTIAL:` / `SINGLE-SOURCE:` /
  `hunt:` / `vac:`; reconciled against `items.json ∩ any-repo` (`references/findings-schema-v2.md` §2).
- `runs/<run-id>/prompts/*.md` — worker-system, worker, worker-revision, reviewer-system, reviewer,
  rendered from `templates/prompts/` with the companion's `conventions.md` injected.
- `runs/<run-id>/gate.json` — `KINDS`, `VERDICTS`, witness requirement, a `sources` block
  (`{<source_id>: {text, sha256, text_sha256, page_offset, images_dir, item_pages}}`) and the
  allowed repo prefixes. The gate picks the text by the document's `source_id`; the two paths the
  legacy gate hard-wired (the book file and the repo-name regex) are fields here.
- a tend root — `config.yaml`, `.tend/*.yaml`, `bin/` (the two bridges, the normaliser, the
  out-of-tree gate invocation).

Then it runs `tend-task verify` over the tasks and stops if anything fails.

## 4. `cf run` — the launch sequence

What it does, in order — and each step is one you can also do by hand:

1. Refuse unless the host is in the allowlist and `--i-have-consent` is present.
2. `rsync` the entrypoint (tasks, scripts, data tables), the tend root and the prompts to the host.
   Book text, errata and page images go to a **separate directory outside every repo**.
3. Commit the tasks to the entrypoint's `main` — the orchestrator reads tasks from git.
4. **Smoke test 1/1.** Set limits to one worker and one reviewer, start the run, and watch exactly
   one task through worker → gate → reviewer → merge. Do not launch hundreds of tasks blind: model
   ids that have never been exercised from this machine fail immediately and repeatedly.
5. **Read that task's gate output once per tend version.** Its last line is `changed files: …`.
   `ownership checked against task <id>` means the orchestrator exported `TEND_TASK_ID` into the
   validation command's environment and the full ownership rule is running. `ownership: single-stem
   rule` means it did not: only the weaker rule (one task's findings/task pair per contribution) is
   in force, so a worker that rewrites a neighbour's landed findings file inside its own single
   stem is not caught. **Whether tend exports it has never been verified here**, and it is a
   property of the orchestrator version, not of the run — so check it again after an upgrade. To
   restore the full rule, append `--task-id <the id>` to both gate commands in the tend root's
   `config.yaml` by whatever substitution that version supports; an argv list is not shell-expanded,
   so a literal `$TEND_TASK_ID` element makes the gate refuse every run. The same argv serves
   `validation_commands` and `pre_merge_validation_commands`, and with `batched_merge: true` a
   pre-merge change set carries one findings/task pair per batched task — the findings file added,
   the task file modified, because step 3 above committed the tasks to the merge target. The stem
   count therefore only refuses several stems when some stem's findings file was already on the
   merge target, so a batch of new findings merges and a rewritten neighbour is still named. A 1/1
   smoke test cannot exercise that
   — there is nothing to batch at limits 1/1 — so read the pre-merge gate output again after step 6.
6. Only then raise the limits to the preset's concurrency.

```bash
# the manual equivalents, for when you are debugging rather than driving
tend-control limits --root <root> --workers 1 --reviewers 1
tend run --root <root> --log-level INFO
tend-control limits --root <root> --workers 8 --reviewers 5
```

**Set the budget ceiling before raising concurrency.** Each task runs a worker and at least one
reviewer, and a `request_changes` sends the task back for another worker turn, so the task count is
a lower bound on agent count, never a total.

## 5. `cf status` — monitoring

```bash
tend-control status --root <root> | grep -E 'tasks:|worktrees:|active_agents'
ls <entrypoint>/findings | wc -l        # merged findings = real progress
tail -f <orchestrator log>
```

`cf status` wraps these and writes the counts into `companions/<name>/state.json`. Steering:
`tend-control limits` (throttle), `pause` (stop new cost, keep state), `drain` (finish in flight,
start nothing), `stop`.

**Expected shape.** ~10–15 minutes per task through worker → gate → review → merge, because the
agents genuinely read the book and the Lean. At 8 workers that is ~8–12 h for 382 tasks and
~16–24 h for 763, plus rework loops. 13 concurrent agents left a 96-core box at load ~1 — the
limit is the hosted-session cap and the review loop, not CPU.

**Hard cap: 16 concurrent hosted sessions per account, across workspaces.** 8 workers + 5 reviewers
leaves three. Above the cap the API answers 409. The bridges now take one of 15 per-user `flock`
slots before every agent call (`CF_SLOT_DIR`, `CF_SLOTS`) and wait for a free one rather than
failing, so roots on one Unix user cannot together pass the cap; an empty or 409 envelope still
exits 75 so tend retries the same worktree.

## 6. Failure modes you will actually meet

| symptom | cause | response |
|---|---|---|
| a task completes perfectly and is silently re-queued | the agent's self-reported result did not match the orchestrator's strict schema | `normalize_result.py` (shipped) coerces it; check it is on the bridge path |
| a task loops, repeating a rejected mistake | the bridge re-ran the original prompt on rework | the shipped bridge appends the reviewer's feedback from `.tend/discussion.md` |
| the gate fails on every task | the book text is missing at the configured path | intended: the gate fails closed. Check the book directory before blaming the run |
| `aggregate usage: input=0` | agents run inside hosted sessions | expected; watch the gateway, not the orchestrator |
| 409s when starting | the session cap, reached from another Unix user or account | the bridge exits 75 and tend retries; lower concurrency, or wait for the other workspace |
| an agent call never returns | a hung session | every call runs under `timeout ${CF_AGENT_TIMEOUT:-45m}`; expiry exits 124, an infrastructure failure tend retries, never a verdict |
| the disk fills minutes after launch | one worktree per ready task | see §2.6 |
| a run dies | the orchestrator resumes from its sqlite state (`worker_running` → pending) | re-`tend run --root` with the same root; `run.yaml` records the exact command |

## 7. `cf harvest` — pulling results back

1. `rsync` the merged `findings/` from the entrypoint on the host into `corpus/<run-id>/`.
2. Write `RECEIPT.json`: origin host alias and path, expected count, actual count, sha256 manifest,
   `run.yaml` sha, and the run's `sources: {<id>: <sha>}`. **Fail on any mismatch.** A harvest once held 762 files where the
   host had 763 and the missing file was a real `CONTENT` finding.
3. Commit the corpus in the workspace repo (append-only per run-id).
4. Run `tally.py`, `union.py`, `agreed.py`; write the counts into `state.json`.
5. Price the run. `tend-control status` reports zero usage for every run, because the agents run
   inside Claude Code and the orchestrator never sees a token; the agents' own transcripts on the
   host carry every one (issue #11). The transcripts hold prompts, paths and tool output, so they
   stay on the host: run the standalone reader there, from a copy of `scripts/cost/usage.py`, as
   `python3 usage.py <dir> [<dir> ...] --export usage.jsonl` with every transcript directory of
   the run (one `{model, usage}` row per response, no text), bring the export back, and pass it to
   `cf harvest <run-id> --transcripts usage.jsonl`. The receipt then carries the `cost` block
   (tokens by model, dollars at `workspace.yaml` `budget.prices`, the models the table does not
   price, the table's sha) and `cf tally` prints the cost per task. A Codex fleet's
   `rollout-*.jsonl` logs read the same way; a log that names no model takes
   `--transcripts-model <id>`. A model missing from the price table is reported as UNPRICED,
   never guessed at, and the dollar figure is then a lower bound until the table has a row for it.

Everything downstream is a function of `corpus/`, never of files someone read.

## 8. Local fallback (< ~100 items)

A Workflow fleet on the laptop, per `references/workflow-patterns.md` §6. Two rules:

- The **default local bridge is `claude -p`**, not an API-key agent runtime: the 2026-09-05 attempt
  to run locally on an API key with no credit balance produced 20 requests, 18 retries and repeated
  HTTP 404/credit errors, which is what moved the run to the server in the first place.
- There is **no local analogue of the request-changes loop**. The worker self-gates; the real gate
  runs post-hoc at harvest — by default, since `cf harvest` gates unless `--no-gate` — and failures
  come back as a second run. On this path the harvest gate is the *only* independent check, which
  is why turning it off here is worse than turning it off on the server path, where
  `validation_commands` and `pre_merge_validation_commands` have already run it per task.

## 9. A locate root

A locate run (`cf plan-locate`; `docs/proposals/wikipedia-locate.md` §3, §6) uses the same tend
root, bridge and harvest, with these differences:

- **One task, its own directory.** A task writes `findings/<task-id>/<source-id>.json`, one
  document per source its manifest (`gate/tasks/<task-id>.json`) names, and changes nothing else
  but `tasks/<task-id>.yaml`. The gate is `gate/verify_locate.py`, invoked by tend's validation
  commands with `--changed-files --base <branch>`; it reads only the documents the change set
  touched and only the sources, candidates and manifest they name. Its last line names the
  ownership rule that judged the change set: the task id from `--task-id`/`TEND_TASK_ID`, the
  task id recovered from `<tend-root>/orchestrator.sqlite` (opened `mode=ro`, which reads the
  rows tend's WAL holds; `immutable=1` only when no `-wal` lies beside it or its directory
  cannot be written, and then the line says it was read immutably; **with an assumed
  schema**: read that line on the smoke task, as for §4 step 5), or the single-stem rule,
  with the reason the lookup found no task.
- **What travels.** `cf run` sends only the run's own sources (their rendered directories),
  `corpus.sqlite` (and the database the workers search, when the run is blind or planted) and the
  candidates directory, and checks the database's sha once at the verify stage. **A locate run
  needs a spend cap**: `cf run --execute` refuses one without a `budget.max_cost`, and has no
  `--no-budget-cap` escape for it.
- **The host-side directories**, all in the tend root and outside every worktree: `usage/` (each
  agent call's JSON envelope, a cost cross-check), `attempts/<task-id>.json` (genuine reviewer
  rejections only; the third makes the worker bridge write `UNRESOLVED` documents with
  `unresolved.by: attempt_cap`, which the reviewer bridge approves mechanically), `gate-log/gate.jsonl`
  (one line per gate call, by rule).
- **The worker's tools.** The worker allowlist adds the two corpus host tools by absolute path,
  pinned to the run's database (`CF_EXTRA_TOOLS`), and an inventory worker's passage-id tool.
- **Bringing it home.** `cf harvest <run-id>` lands `corpus/<run-id>/<task-id>/<source-id>.json`,
  re-gates the whole corpus out of tree with `gate.local.json`, counts `UNRESOLVED` documents and
  attempt-capped ones separately in the receipt, and also brings the host's gate log (and, for a
  campaign shard, its usage export and canary scan) to `runs/<run-id>/host/`. Then
  `cf tally --locate --run <run-id>`.
- **What travels, for a blind or planted run.** The gate always checks the full `corpus.sqlite`
  (`gate.json` `corpus_db`); the workers search the tools database (`tools_corpus_db`: the full one,
  `corpus.blind.sqlite` for `--blind`, or a planted file given to `cf plan-locate --tools-corpus-db`).
  `cf run` transfers the tools database too when it is another file, and checks both shas at the
  verify stage. A P run of the bench is planned that way with the bench's planted database
  (`references/locate-bench.md` §7).
- **Shards.** More than 500 tasks is a campaign:
  1. Run a **pilot** first: a small locate run taken to the smoke stage with `cf run <pilot>
     --i-have-consent --execute`, watched through worker → gate → reviewer → merge, then raised
     with `--stage raise --smoke-verified --execute`, which records `run.smoke_verified`. Read its
     p90 cost per merged task and gate-failure rate from its harvest.
  2. Plan each shard with `cf plan-locate` (each with its own `--max-cost`), then
     `cf campaign plan <id> --shard <r1> --shard <r2> … --host <alias> --pilot <pilot>` with the
     pilot's p90s. `cf campaign plan` stamps `locate.campaign` into each shard's `run.yaml` and
     refuses a shard stamped by another campaign. Give `--transcript-dirs` (or set
     `hosts.<alias>.transcript_dirs` in `workspace.yaml`) to have each shard's transcripts exported
     and pruned, and `--canaries <host path>` to have them scanned for the bench's label canaries.
  3. Take every shard through `cf run <shard> --i-have-consent --execute --stage verify`, which
     transfers and verifies it and launches nothing. `cf campaign render <id>` refuses a campaign
     without a smoke-verified pilot.
  4. Copy `campaigns/<id>/` to the host and start its `sequencer.sh` there (under `nohup` or a
     terminal multiplexer). It takes a lock (`sequencer.lock`), so a second start exits 3. It refuses
     a root that already has a live `tend run`, keys its state by run id, and runs shard k+1 only
     after shard k's `tend run` ends. Beside each shard its model-free watchdog drains the root
     (`tend-control drain`) when the shard's spend passes its cap, or when the gate-failure rate or the
     cost per merged task passes the configured multiple of the pilot's p90. After each shard it
     scans that shard's transcripts for canaries (`canary_scan.py` writes
     `{"scanned": N, "hits": [<file>, …], "canaries": K}`), exports their usage and prunes them;
     a canary hit or a failed scan keeps them. A drain, a canary hit or an unreadable watchdog record
     stops the campaign. `CF_TEND_CMD`, `CF_TRANSCRIPT_DIRS`, `CF_CANARIES` and `CF_WATCH_INTERVAL`
     override what was rendered.
  5. Harvest each shard as it finishes (`cf harvest <shard> --execute`); `cf campaign status <id>`
     reads the shards' receipts.

  The sequencer needs, on the host: bash 4.4 or later (`mapfile -d`), GNU `comm` (with `-z`),
  `flock`, `pgrep`, `python3` and rsync.

## 10. What stays manual, permanently

- ssh access and any credential or login on the host;
- the hosted-agent profile login;
- the box owner's consent for a heavy job (recorded, not assumed);
- obtaining the book PDF and pinning its sha;
- watching the first task end-to-end before limits are raised;
- deciding the billing path and setting any gateway-side cap;
- every command in `workflows/` — the Workflow tool is invoked inside a session, not from `cf`, so
  `cf adjudicate` / `report` / local `run` print the script path and the `args`, and the result is
  handed back with `cf adjudicate --transcribe <result.json> --from {adjudicate,challenge}` (a
  local fleet's journal comes back through `cf harvest <id> --journal <wf dir>`). No command
  ingests a result: `cf ingest` is pipeline stage S0, which pins a PDF (`docs/pipeline.md`), and
  this line once sent a stuck reader to it for results;
- opening, merging or reporting anything to a repository owner.

---

## 11. Sources

| claim | source |
|---|---|
| pipeline, smoke test, steering commands, budget-before-concurrency, harvest snippet | the five run roots' `RUNBOOK.md` (identical across the three differential variants) |
| the bridge architecture, zero usage counters, the normaliser's necessity | the same file's "ADDENDUM — the run as actually launched" |
| worktree reclamation and the pre-run state | the same file's "NOTE 2026-09-07" |
| per-variant differences (gate kinds, prompt add-ons, bridge, concurrency) | diffing `config.yaml`, `prompts/*`, `bin/*` and each root's own `verify_findings.py` across the five roots |
| 16 concurrent hosted sessions per account; 10–15 min/task; 16–24 h for 763 tasks; coordinate heavy jobs | the docstring-tag companion's `HANDOFF.md` §8 |
| the gate fails closed without the book text, and the two hard-wired paths | same, §8; the v1 corpus's own `verify_findings.py` |
| the local API-key bridge failure (20 requests, 18 retries, HTTP 404 on no credit) | the design critique, risks |
| gate out of tree + changed-files check; read-only source mounts; host allowlist and recorded consent | the design critique, gaps 3 and 13, risks 2–3 |
