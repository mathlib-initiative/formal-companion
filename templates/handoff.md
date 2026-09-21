# Handoff — {{workspace}} faithfulness audit ({{date}})

> Scope of this report: **findings only**. No fix PRs were authored, no PR was reviewed and no
> camera-ready audit was run by this pipeline; those stages exist as extension points, not as work
> that happened. Nothing below is an upstream report for a repository whose routed file still says
> UNCHALLENGED.
>
> {{scope_note}}

Sources under audit — one row per source, with the companions bound to it and the scope audited:

{{sources_table}}

**TL;DR.** {{tldr}}

## 0. How much of each source was audited

Every finding count below is over the scope in this table, not over the whole of a source. The
columns are: items the source contains, items in the audited scope, items in the differential's
task universe (items in scope at least one companion claims), items paired (items two or more
companions formalise, so a cross-check was possible at all), items a run actually examined, of
those the ones whose run passed the quote gate, items that carried a finding, and items in scope
that no run ever looked at.

{{coverage_table}}

{{coverage_note}}

The items never examined are listed by name in `reports/unexamined.<source_id>.json`, and every
figure in this section is in `reports/coverage.json`. Every figure here is over the audited scope,
so anything counted outside it — an examined item in an unaudited chapter, or a corpus document
that could not be read — is named in the notes above rather than dropped: those documents are
counted in §3 and in no column of this table.

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
