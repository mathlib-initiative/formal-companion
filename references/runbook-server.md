# Server runbook — orchestrator + hosted-agent bridge

How a fleet of hundreds of read-heavy tasks actually runs, distilled from the five run roots of the
2026-09 audit (three differential variants, two attack variants) and the RUNBOOK addendum written
after the run moved to the server. Written as: **what `cf` automates**, **what you check**, and
**what stays manual**.

No host address, account or profile name appears in this file. They live in `workspace.yaml`, and
`cf run` refuses any host not in its allowlist.

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

## 3. `cf plan-run` — what it writes

```
cf plan-run --preset attack-vac --companion ours --host <alias> --i-have-consent
```

- `runs/<run-id>/run.yaml` — mode, companion set, model ids, book access, gate mode, host alias,
  concurrency, consent record, billing path, task count, prompt shas.
- `runs/<run-id>/tasks/*.yaml` — one per item, title prefixed `DIFFERENTIAL:` / `SINGLE-SOURCE:` /
  `ATTACK:`; reconciled against `items.json ∩ any-repo` (`references/findings-schema-v2.md` §2).
- `runs/<run-id>/prompts/*.md` — worker-system, worker, worker-revision, reviewer-system, reviewer,
  rendered from `templates/prompts/` with the companion's `conventions.md` injected.
- `runs/<run-id>/gate.json` — `KINDS`, `VERDICTS`, witness requirement, a `sources` block
  (`{<source_id>: {text, sha256, text_sha256, page_offset, images_dir}}`) and the allowed repo
  prefixes. The gate picks the text by the document's `source_id`; the two paths the legacy gate
  hard-wired (the book file and the repo-name regex) are fields here.
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
5. Only then raise the limits to the preset's concurrency.

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
leaves three. Above the cap you get 409s.

## 6. Failure modes you will actually meet

| symptom | cause | response |
|---|---|---|
| a task completes perfectly and is silently re-queued | the agent's self-reported result did not match the orchestrator's strict schema | `normalize_result.py` (shipped) coerces it; check it is on the bridge path |
| a task loops, repeating a rejected mistake | the bridge re-ran the original prompt on rework | the shipped bridge appends the reviewer's feedback from `.tend/discussion.md` |
| the gate fails on every task | the book text is missing at the configured path | intended: the gate fails closed. Check the book directory before blaming the run |
| `aggregate usage: input=0` | agents run inside hosted sessions | expected; watch the gateway, not the orchestrator |
| 409s when starting | the session cap | lower concurrency, or wait for the other workspace |
| the disk fills minutes after launch | one worktree per ready task | see §2.6 |
| a run dies | the orchestrator resumes from its sqlite state (`worker_running` → pending) | re-`tend run --root` with the same root; `run.yaml` records the exact command |

## 7. `cf harvest` — pulling results back

1. `rsync` the merged `findings/` from the entrypoint on the host into `corpus/<run-id>/`.
2. Write `RECEIPT.json`: origin host alias and path, expected count, actual count, sha256 manifest,
   `run.yaml` sha, and the run's `sources: {<id>: <sha>}`. **Fail on any mismatch.** A harvest once held 762 files where the
   host had 763 and the missing file was a real `CONTENT` finding.
3. Commit the corpus in the workspace repo (append-only per run-id).
4. Run `tally.py`, `union.py`, `agreed.py`; write the counts into `state.json`.

Everything downstream is a function of `corpus/`, never of files someone read.

## 8. Local fallback (< ~100 items)

A Workflow fleet on the laptop, per `references/workflow-patterns.md` §6. Two rules:

- The **default local bridge is `claude -p`**, not an API-key agent runtime: the 2026-09-05 attempt
  to run locally on an API key with no credit balance produced 20 requests, 18 retries and repeated
  HTTP 404/credit errors, which is what moved the run to the server in the first place.
- There is **no local analogue of the request-changes loop**. The worker self-gates; the gate runs
  post-hoc at harvest and failures come back as a second run.

## 9. What stays manual, permanently

- ssh access and any credential or login on the host;
- the hosted-agent profile login;
- the box owner's consent for a heavy job (recorded, not assumed);
- obtaining the book PDF and pinning its sha;
- watching the first task end-to-end before limits are raised;
- deciding the billing path and setting any gateway-side cap;
- every command in `workflows/` — the Workflow tool is invoked inside a session, not from `cf`, so
  `cf adjudicate` / `report` / local `run` print the script path and the `args` file and expect the
  results to be handed back (`cf ingest <results.json>`);
- opening, merging or reporting anything to a repository owner.

---

## 10. Sources

| claim | source |
|---|---|
| pipeline, smoke test, steering commands, budget-before-concurrency, harvest snippet | the five run roots' `RUNBOOK.md` (identical across the three differential variants) |
| the bridge architecture, zero usage counters, the normaliser's necessity | the same file's "ADDENDUM — the run as actually launched" |
| worktree reclamation and the pre-run state | the same file's "NOTE 2026-09-07" |
| per-variant differences (gate kinds, prompt add-ons, bridge, concurrency) | diffing `config.yaml`, `prompts/*`, `bin/*` and `scripts/verify_findings.py` across the five roots |
| 16 concurrent hosted sessions per account; 10–15 min/task; 16–24 h for 763 tasks; coordinate heavy jobs | `/Users/jack/Desktop/tao-measure-lean/docs/HANDOFF.md` §8 |
| the gate fails closed without the book text, and the two hard-wired paths | same, §8; `~/Desktop/tao-diff-run/scripts/verify_findings.py` |
| the local API-key bridge failure (20 requests, 18 retries, HTTP 404 on no credit) | design critique `critique.json`, risks |
| gate out of tree + changed-files check; read-only source mounts; host allowlist and recorded consent | design critique `critique.json`, gaps 3 and 13, risks 2–3 |
