# The workspace

The unit of organization is a **companion with a list of sources**, not a book with
a list of companions. A **workspace** holds N companions and M sources, and a
differential is possible exactly where two companions' source lists overlap.

Nothing about a source is typed in. `cf onboard <repo>` reads the repository,
fingerprints the source bytes, infers the page offset and the item kinds from the
text, finds the errata page and prepares the source.

Sources are identified by the sha256 of their bytes, so an edition in the shipped
registry (`registry/sources.yaml`) is recognized in any workspace. An edition a
workspace discovers stays in that workspace's `registry.yaml` overlay with
`confirmed: false`, so a second workspace that meets it starts again and prints
`nobody has catalogued this source`. `cf sources promote` writes a patch file that
a person merges by pull request (`CONTRIBUTING.md`, "Adding an edition to the
registry"), and no command writes the shipped registry.

## Layout

```text
<workspace>/
  workspace.yaml             companions (repo, adapter, sources[], anchor_source,
                             bundled_sources[]), sources (sha, offset, kinds, errata,
                             licence, page map, role), hosts, models, defaults,
                             budget, policy, library_paths
  registry.yaml              this workspace's overlay on the skill's source registry
  sources/<source_id>/       GITIGNORED: text.txt, pages/, images/, items.json,
                             meta.json, errata.md, errata.raw.html, errata.json
  companions/<name>/         adapter.yaml, conventions.md, statements.jsonl, state.json
  pairs/<source_id>/         pairs.jsonl, coverage.md, queue.json, targets, def-coverage,
                             anchor-check.json
  runs/<run-id>/             run.yaml, tasks/, prompts/, gate.json, tend-root/ (gitignored)
  corpus/<run-id>/           findings + RECEIPT.json  (committed: this is the baseline;
                             the receipt's cost block is the run's priced tokens)
  pipelines/<id>/            pipeline.yaml, doc.vN.json checkpoints and
                             stages/<stage>.stage-receipt.json: the PDF-to-companion
                             pipeline's records, written by `cf ingest` (S0) and
                             `cf inventory` (S1); repo/ is the generated companion,
                             its own git repository (gitignored here)
  ledger/                    accepted/refuted per companion, routed/findings.<name>.jsonl
  detect/<name>/             proxy-detection artifacts
  reports/                   tally.<run>.json, union.<name>.json, coverage.json,
                             score.<run>.json, handoff.<date>.md
  sourcesets/<id>/           locate's web sources: append-only manifest-<n>.jsonl,
                             state.json, candidates/, and sources/<source_id>/
                             (GITIGNORED: the page bytes and their rendered views)
  corpora/<u>/               a universe of libraries: universe.yaml, conventions/, and
                             (GITIGNORED, rebuildable) index/ and corpus.sqlite
  exports/<set>/             sealed Literature Links record sets (`cf export literature-links`)
  benches/<id>/              the locate benchmark's frame, gold and scores
  campaigns/<id>/            a sequence of locate shards and its host sequencer
  fixes/  reviews/           created by `cf init` and written by nothing in v1: they
                             are the extension points for the out-of-scope stages
```

`cf init` creates `companions`, `corpus`, `detect`, `fixes`, `ledger`, `pairs`,
`reports`, `reviews`, `runs` and `sources`. `pipelines`, `sourcesets`, `corpora`,
`exports`, `benches` and `campaigns` appear when a command first writes one.
`docs/design/stage_map.md` section 2.1 is the full version, and section 2.2 says what
carries the source sha and the companion pin.

## The rules it enforces

**No remote.** The corpus quotes the source and holds unchallenged findings, so
`cf init` runs `git init` and refuses a workspace repository that already has a remote
unless `policy.allow_remote` says otherwise. That refusal is the control. The pre-push
hook it installs is a reminder on top of it: `git push --no-verify` skips the hook, a
fresh clone does not carry it, and a remote added after `cf init` never passes through
it. So `cf status` prints, every run, whether the repository has a remote and whether
the hook is still in place. `cf init --no-remote-guard` adds the one guard a bare
`git push --no-verify` does not get past (`remote.pushDefault` to a remote that does not
exist and `push.default nothing`, both in that repository's local config). An explicit
`git push <remote> <ref>` still works, so it is a guard and not the control either.

**Copyright.** `sources/`, `pages/`, `images/`, `crops/` and `*.pdf` are gitignored,
and only the sha256 and the metadata are recorded. `corpus/` is the calibration baseline,
so it is deliberately *not* ignored. `POLICY.md` is the whole rule.

**One edition per source id.** Every artifact carries `source_id` and `source_sha`,
and a mismatch is refused.

**Consent.** `cf run` refuses hosts outside `hosts.allowlist` and requires
`--i-have-consent`, which is recorded in `run.yaml`.

**A workspace is never inside this repository.** `cf init` refuses a path under the
skill repository, and a path inside a git repository with a remote.

## Sharing a campaign

Staying local is not the same as being unshareable. `cf share export` writes the
campaign's conclusions (ledger rows, routed findings, tallies, receipts, source
metadata) with every quote redacted, and `cf share import` merges such a bundle into
another workspace without overwriting a verdict. `cf share export` refuses a companion
whose routed rows are UNCHALLENGED unless `--skip-unchallenged` leaves those rows behind.
