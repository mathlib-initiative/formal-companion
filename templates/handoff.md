# Handoff — {{campaign}} faithfulness audit ({{date}})

> Scope of this report: **findings only**. No fix PRs were authored, no PR was reviewed and no
> camera-ready audit was run by this pipeline; those stages exist as extension points, not as work
> that happened. Nothing below is an upstream report for a repository whose routed file still says
> UNCHALLENGED.

Book edition under audit: `{{book_id}}` — sha256 `{{book_sha}}`{{book_note}}.

**TL;DR.** {{tldr}}

## 1. State in one screen

{{state_table}}

{{status_note}}

## 2. The runs, and what each yielded

{{runs_table}}

{{yield_note}}

## 3. What the audit found

{{findings_tables}}

## 4. What needs a human

{{owner_actions}}

## 5. What this pass got wrong

{{wrong_section}}

## 6. Where things are

{{paths_list}}

## 7. Traps met this pass

{{traps_list}}

## 8. Verification — every number above, with the command that recomputes it

Do not decide "nothing is open" by re-reading the prose above; run these. Each number in this
report was emitted by one of them, and the command is only as good as the corpus it reads, so
check the harvest receipt first.

{{verification_commands}}

{{provenance}}
