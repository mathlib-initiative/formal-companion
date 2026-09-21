# The multi-source model: workspaces, companions and sources

**Status:** binding. Every work package named in §9 implements this document; a
package that needs to deviate changes this file first, in its own commit, and
says why in the commit message.

**Written:** 2026-09-16. **Supersedes:** the one-campaign-per-book model in
`SKILL.md`, `README.md`, `templates/campaign.yaml.example` and
`scripts/cflib/campaign.py`.

---

## 0. Why, and what is fixed

The current model was rejected by the user in these words:

> I don't want to manually have to input errata url and books. We are not "one
> campaign per book", we want "one companion per list of sources", and the list
> of sources may be more than one.

Four requirements follow, and they are not negotiable.

| | requirement |
|---|---|
| **R1** | The unit of organisation is a **companion** with a **list of sources** (one or more). Several companions may share sources; a differential is possible **exactly where two companions' source lists overlap**. The directory a user works in is a **workspace** holding N companions and M sources. `campaign` → `workspace` everywhere: the flag is `--workspace`, the env var is `CF_WORKSPACE`, the file is `workspace.yaml`. **No aliases are kept** — nothing is released, so there is no compatibility surface to preserve. |
| **R2** | **No manual input** of sources, editions, page offsets or errata URLs. `cf onboard <repo>` discovers them from the repository itself, fingerprints the actual PDF bytes when they can be located, infers the page offset and item grammar from the text, finds the errata page, prepares the source and runs extraction — one command, no flags in the common case. `cf sources add/set` exist as the exception, not the path. |
| **R3** | Sources are identified **by content** (sha256 of the PDF when available) and described in a **source registry shipped with the skill** (`registry/sources.yaml`) that remembers what was learned about each known source. Discovery consults the registry by sha and by title/ISBN; new sources are appended to the **workspace's own overlay** (`<workspace>/registry.yaml`) with `confirmed: false` and surfaced in `cf status`. |
| **R4** | Items are namespaced by source: **`(source_id, item)`**. Every statement row, finding, pair, task, ledger row and report carries `source_id` and `source_sha`. The old `book_sha` concept becomes `source_sha`. |

### 0.1 The rename table

This is the whole vocabulary change. Nothing else is renamed, and nothing keeps
an alias.

| was | is | note |
|---|---|---|
| campaign (concept) | **workspace** | N companions × M sources |
| `campaign.yaml` | `workspace.yaml` | schema version 1 → 2 |
| `--campaign`, `$CF_CAMPAIGN` | `--workspace`, `$CF_WORKSPACE` | |
| `cflib.campaign.Campaign` | `cflib.workspace.Workspace` | module renamed, not re-exported |
| `CampaignPaths` | `WorkspacePaths` | |
| `CampaignError` | `WorkspaceError` | |
| `book` (the single pinned thing) | **source** (one of many) | |
| `book.id` | `source_id` (a key in `sources:`) | slug, §3 |
| `book_sha` | `source_sha` | same 64-hex sha256 of PDF bytes |
| `book/<book-id>/` | `sources/<source_id>/` | gitignored |
| `book.txt` | `text.txt` | inside the source dir |
| `book.json` | `meta.json` | superset: identity + pagination + provenance |
| `scripts/book/` | `scripts/sources/` | package rename (see §9 note on `.gitignore`) |
| `prepare_book.prepare()` | `prepare_source.prepare()` | |
| `cf init <dir> --book … --pdf … --offset … --errata-url …` | `cf init <dir>` | no flags |
| `cf book <id>` | `cf sources refresh [<id>]` | command deleted, folded in |
| `pairs/…` | `pairs/<source_id>/…` | one pairing per source |
| `book_quote` (finding field) | `book_quote` | **unchanged** — it is still a quote from a book |

`book_quote` deliberately keeps its name: it is prose from a book, and the
findings schema field is well known to the baseline corpus. What changes is
*which* text it is verified against, which is now decided by the row's
`source_id`.

### 0.2 What does not change

Every hard rule in `SKILL.md` survives, with `source_sha` substituted for
`book_sha` and "the source named by the row" substituted for "the book":

1. **Quote gate** — `book_quote` must occur in `sources/<source_id>/text.txt`
   (normalised, ≥25 characters), and the gate still runs from outside the tree
   the worker edits.
2. **Witness gate** — unchanged.
3. **Edition gate → source gate** — every artifact that quotes a source or
   judges an anchor carries `source_id` **and** `source_sha`; a mismatch with
   the workspace's sha for that `source_id` is refused, not warned about. The
   one documented override stays `--allow-edition-mismatch` (or
   `--source-sha <hex>` on an import) and is recorded on the receipt.
4. **Count from the corpus** — unchanged; the tally now groups by
   `(source_id, repo)`.
5. **Challenge before report** — unchanged.
6. **Copyright boundary** — unchanged and tightened: `sources/` is gitignored,
   the registry stores *metadata and URLs only, never book text*, and a
   download happens only with `--download` **and** a registry licence note that
   permits fetching from the author's own site. Nothing is ever redistributed.
7. **Consent and allowlist for runs** — unchanged (`Workspace.check_host`,
   `--i-have-consent` recorded in `run.yaml`).
8. **Opus for subagents; never Fable** — unchanged.
9. **Session-limit awareness** — unchanged.
10. **Never modify a companion repo** — unchanged, and now load-bearing for
    discovery: discovery *reads* `formalization.yaml`, ledgers, `CLAUDE.md`,
    caches and PDFs, and writes nothing into a companion.

---

## 1. `workspace.yaml`

One file per workspace, at its root, committed. It is the only place a repo
path, a host, a model, a page offset, an errata URL or a library path is named.

### 1.1 Schema

```
version: 2                       # int, required, == 2

workspace:
  name: <str>                    # required
  created: <YYYY-MM-DD>
  notes: <str>

companions:                      # MAP keyed by companion name (was a LIST)
  <name>:
    repo: <abs path>             # required; never written to
    adapter: <adapter kind>      # required; docstring_tags | attribution_table | blueprint_tex |
                                 #   ledger_nodes | custom
    sources: [<source_id>, …]    # required, >=1, ordered; the sources this companion is audited against
    anchor_source: <source_id>   # required; MUST be one of `sources`; whose pagination the repo's
                                 #   page anchors follow (§4.6)
    bundled_sources: [<id>, …]   # optional; editions the repo names or ships that are NOT audit
                                 #   sources (e.g. a draft PDF committed for convenience)
    owner: <str>                 # a session id, a person, or "external"
    write_policy: read_only | branch_only | branch_and_pr
    commit: <sha|null>           # HEAD at onboarding; advisory, state.json is authoritative
    discovered:                  # provenance of the automatic decisions (§4)
      at: <iso8601>
      adapter_confidence: <0..1>
      anchor_confidence: <0..1>
      signals: [<repo-relative path or path#anchor>, …]

sources:                         # MAP keyed by source_id (§3)
  <source_id>:
    sha256: <64 hex|null>        # identity. null only while the PDF cannot be located
    title: <str>                 # required
    authors: [<str>, …]
    edition: <str>               # required; the human label that distinguishes it from siblings
    isbn: <str|null>
    kinds: [<Kind>, …]           # item kinds the strict grammar accepts for THIS source
    numbering: [<min>, <max>]    # OVERRIDE only: numeric segments a label may carry (§5.5).
                                 #   Absent means "read it off the headings"
    numbering_method: manual     # present only when `numbering` was typed
    source_format: pdf | text    # §5.7; a manuscript that is already text is a source too
    pagination: formfeed | none  # §5.7; `none` = no printed page, items located by section
    offset: <int>                # printed page = pdf page - offset
    offset_confidence: <0..1>    # §5.1; < 0.9 means `cf sources set` was required
    offset_method: running_head_mode | declared | manual | none
                                 #   `none` = unpaginated, so there is no offset to be sure of
    completeness_ok: <bool>      # §5.6 override: this table really is this small
    completeness_method: manual  # present only when `completeness_ok` was set
    pages: <int|null>            # PDF page count; 0 for an unpaginated source
    text_sha256: <64 hex|null>   # sha of the pdftotext -layout output (catches a poppler change)
    bytes: <int|null>
    errata_url: <url|null>
    errata_confidence: <0..1>
    download_url: <url|null>     # only when the author publishes it
    book_page_url: <url|null>
    licence:
      note: <str>
      fetch: none | author_site | open
      redistribute: false
    pdf: <abs path|null>         # machine-local; the bytes are NEVER copied into the workspace
    chapters: [<int>, …]         # scope, inferred from what the companions actually tag (§5.3)
    role: audited | reference    # reference = prepared for page_map only; no companion audits it
    same_work: [<source_id>, …]  # other editions of the same work
    page_map_to:                 # how printed pages map to a sibling edition
      <other_source_id>:
        default_shift: <int>     # other_page = this_page + default_shift, for most items
        exception_shift: <int>   # …and + exception_shift for the items named in `exceptions`
        exceptions: [<item>, …]  # optional explicit list; empty means "see note"
        note: <str>              # required when `exceptions` is empty
        confirmed_by: <str>
    confirmed: <bool>            # false => discovered, unreviewed; `cf status` surfaces it
    registry: skill | overlay | local
    discovered:
      at: <iso8601>
      from: [<companion>:<repo-relative path>[#hint], …]
      pdf_found_in: repo_declared | repo_cache | workspace_store | library_path | download | none

hosts:
  allowlist: [<host alias>, …]   # `cf run` refuses anything not listed
  agentbox_profile: <str|null>
  remote_root: <str|null>

models: {worker, reviewer, skeptic, adjudicator, calibration}

defaults: {mode, source_access, max_concurrent_workers, max_concurrent_reviewers,
           max_concurrent_sessions, max_repos_per_task, local_fleet_max_items,
           gate_out_of_tree, worktree_budget_gb}

budget: {billing_path, max_cost_usd, require_consent}

policy: {publish_unchallenged, redact_quotes_on_export, allow_remote,
         allow_download, require_confirmed_sources}

library_paths:                   # where `cf onboard` may look for a PDF it was told about,
  - ~/Downloads                  # PLUS the derived roots: the workspace's own sources/ store,
                                 # and <p>/**/sources + <p>/**/.lake/official-pdf for the
                                 # parent <p> of every onboarded repo (never the bare <p>)
```

Renames inside the config blocks: `defaults.book_access` → `defaults.source_access`
(values `text` | `text+images`, unchanged). Two new policy keys:
`allow_download` (default `false`; `--download` still required per invocation)
and `require_confirmed_sources` (default `false`; when `true`, `cf plan-run`
refuses a source whose `confirmed` is `false`).

### 1.2 Validation (`Workspace.validate()` returns these as strings)

- `version` must be 2.
- every companion: `repo` exists and is a directory; `adapter` is an
  implemented kind; `sources` is a non-empty list; every id in `sources` and in
  `bundled_sources` exists in `sources:`; `anchor_source` ∈ `sources`;
  `write_policy` ∈ the vocabulary.
- every source: `title` and `edition` non-empty; `offset` an int;
  `offset_confidence` in [0,1]; `sha256` either null or 64 lowercase hex;
  every id in `same_work` and every key of `page_map_to` exists in `sources:`
  **or** in the registry; `role` ∈ {audited, reference}; a source with
  `role: audited` is listed by at least one companion.
- `hosts.allowlist` is a list.
- **no two sources share a non-null `sha256`** — the same bytes are one source.

### 1.3 Worked example: the Tao workspace

Two companions, one shared audit source, one reference edition. This is the
example `cf init` + two `cf onboard` runs must produce on this machine.

```yaml
version: 2

workspace:
  name: tao-measure
  created: 2026-09-16
  notes: "Two companions of Tao's An Introduction to Measure Theory."

companions:
  ours:
    repo: /Users/jack/Desktop/tao-measure-lean
    adapter: docstring_tags
    sources: [tao-measure-gsm126-published]
    anchor_source: tao-measure-gsm126-published
    # the repo SHIPS the 2011 draft at sources/tao-measure-theory.pdf, but its
    # docstring anchors follow the PUBLISHED pagination (corrected 2026-09-04).
    bundled_sources: [tao-measure-2011-draft]
    owner: jack
    write_policy: read_only
    discovered:
      at: 2026-09-16T00:00:00Z
      adapter_confidence: 0.95
      anchor_confidence: 0.99
      signals:
        - formalization.yaml#sources
        - graph.json#metadata.sources
        - CLAUDE.md#L48-L55
        - sources/tao-measure-theory.pdf
  adam:
    repo: /Users/jack/Desktop/measure_theory
    adapter: attribution_table
    sources: [tao-measure-gsm126-published]
    anchor_source: tao-measure-gsm126-published
    bundled_sources: []
    owner: external
    write_policy: read_only
    discovered:
      at: 2026-09-16T00:00:00Z
      adapter_confidence: 0.98
      anchor_confidence: 1.0
      signals:
        - source-formalization-ledger.json#source
        - pdf-overlay/official-source.json
        - README.md#book-page-link
        - .lake/official-pdf/gsm-126-tao5-measure-book.pdf

sources:
  tao-measure-gsm126-published:
    sha256: 64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec
    title: An Introduction to Measure Theory
    authors: [Terence Tao]
    edition: "AMS GSM 126, author's preliminary AMS version (published pagination; PDF metadata 2012-12-04)"
    isbn: "978-0-8218-6919-2"
    kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark]
    offset: 16
    offset_confidence: 0.9959
    offset_method: running_head_mode
    pages: 265
    bytes: 1338765
    errata_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    errata_confidence: 1.0
    download_url: https://terrytao.wordpress.com/wp-content/uploads/2012/12/gsm-126-tao5-measure-book.pdf
    book_page_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    licence:
      note: "AMS copyright (GSM 126); the author publishes a free preliminary version on his own site."
      fetch: author_site
      redistribute: false
    pdf: /Users/jack/Desktop/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf
    chapters: [1]
    role: audited
    same_work: [tao-measure-2011-draft]
    page_map_to:
      tao-measure-2011-draft:
        default_shift: 0
        exception_shift: 1
        exceptions: []
        note: >-
          ~65 numbered items, nearly all in section 1.6, print one page LATER in
          the 2011 draft (draft_page = published_page + 1); every other item is
          unshifted. Both editions satisfy pdf_page = printed_page + 16.
        confirmed_by: "tao-measure-lean CLAUDE.md L48-L55"
    confirmed: true
    registry: skill
    discovered:
      at: 2026-09-16T00:00:00Z
      from:
        - adam:source-formalization-ledger.json#source
        - adam:pdf-overlay/official-source.json
        - ours:CLAUDE.md#L48-L55
      pdf_found_in: repo_cache

  tao-measure-2011-draft:
    sha256: 62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8
    title: An Introduction to Measure Theory
    authors: [Terence Tao]
    edition: "2011-01-24 draft (pre-publication pagination)"
    isbn: null
    kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark]
    offset: 16
    offset_confidence: 0.9960
    offset_method: running_head_mode
    pages: 265
    bytes: 1218984
    errata_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    errata_confidence: 0.5
    download_url: null
    book_page_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    licence: {note: "author's pre-publication draft; not redistributable", fetch: none, redistribute: false}
    pdf: /Users/jack/Desktop/tao-measure-lean/sources/tao-measure-theory.pdf
    chapters: [1]
    role: reference
    same_work: [tao-measure-gsm126-published]
    page_map_to:
      tao-measure-gsm126-published:
        default_shift: 0
        exception_shift: -1
        exceptions: []
        note: >-
          ~65 numbered items, nearly all in section 1.6, print one page EARLIER
          in the published book (published_page = draft_page - 1).
        confirmed_by: "tao-measure-lean CLAUDE.md L48-L55"
    confirmed: true
    registry: skill
    discovered:
      at: 2026-09-16T00:00:00Z
      from: [ours:CLAUDE.md#L48-L55, ours:graph.json#metadata.sources]
      pdf_found_in: repo_declared

hosts: {allowlist: [], agentbox_profile: null, remote_root: null}

models:
  worker: claude-sonnet-5
  reviewer: claude-opus-5
  skeptic: claude-opus-5
  adjudicator: claude-opus-5
  calibration: null

defaults:
  mode: attack:vac
  source_access: text
  max_concurrent_workers: 8
  max_concurrent_reviewers: 5
  max_concurrent_sessions: 16
  max_repos_per_task: 4
  local_fleet_max_items: 100
  gate_out_of_tree: true
  worktree_budget_gb: 8

budget: {billing_path: subscription, max_cost_usd: null, require_consent: true}

policy:
  publish_unchallenged: false
  redact_quotes_on_export: true
  allow_remote: false
  allow_download: false
  require_confirmed_sources: false

library_paths:
  - ~/Downloads
```

The differential surface falls straight out: `shared_sources()` returns
`{tao-measure-gsm126-published: [adam, ours]}`, so exactly one differential is
possible and `cf pair` produces exactly one `pairs/<source_id>/` tree.

---

## 2. The source registry

Two files, same schema, different authority.

| file | authority | committed | written by |
|---|---|---|---|
| `<skill>/registry/sources.yaml` | curated, shipped with the skill | yes, in this repo | humans; no command writes it in v1 |
| `<workspace>/registry.yaml` | the workspace's overlay | yes, in the workspace repo | `cf onboard`, `cf sources add` — always with `confirmed: false` |

**Lookup order is overlay first, then skill.** An overlay entry with the same
`source_id` shadows the shipped one field-by-field (a shallow merge: the
overlay's non-null keys win). The overlay never receives a `confirmed: true`
entry from a command; a human promotes an entry by editing the skill's
`registry/sources.yaml` and deleting the overlay row.

**The registry never stores book text.** Metadata, URLs, shas, page maps and
licence notes only. This is a copyright boundary, not a convention.

### 2.1 Schema

```
version: 1
sources:
  <source_id>:
    title, authors, edition, isbn
    sha256, bytes, pages
    download_url, book_page_url, errata_url
    licence: {note, fetch: none|author_site|open, redistribute: bool}
    offset, offset_confidence, offset_method
    kinds: [<Kind>, …]
    same_work: [<source_id>, …]
    page_map_to: {<source_id>: {default_shift, exception_shift, exceptions, note, confirmed_by}}
    aliases:                       # extra keys discovery matches on
      sha256_prefixes: [<hex>, …]  # truncated shas quoted in repo prose
      urls: [<url>, …]
      filenames: [<basename>, …]
      titles: [<str>, …]
    confirmed: bool
    notes: <str>
```

`page_map_to` is not symmetric by construction; both directions are written out
explicitly and a `cf sources` validator checks that the two agree
(`a→b.default_shift == -(b→a.default_shift)` and likewise for
`exception_shift`).

### 2.2 The shipped seed: `registry/sources.yaml`

```yaml
version: 1
# Shipped with the skill. Curated and committed here; a WORKSPACE never writes
# to this file. Metadata only — no book text, ever.
sources:

  tao-measure-gsm126-published:
    title: An Introduction to Measure Theory
    authors: [Terence Tao]
    edition: "AMS GSM 126, author's preliminary AMS version (published pagination; PDF metadata 2012-12-04)"
    isbn: "978-0-8218-6919-2"
    sha256: 64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec
    bytes: 1338765
    pages: 265
    download_url: https://terrytao.wordpress.com/wp-content/uploads/2012/12/gsm-126-tao5-measure-book.pdf
    book_page_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    errata_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    licence:
      note: >-
        AMS copyright (GSM 126). The author publishes a free preliminary version
        on his own site; fetching that copy for a local audit is fine,
        redistributing it is not.
      fetch: author_site
      redistribute: false
    offset: 16
    offset_confidence: 0.9959      # 244 of the 245 pages that carry a running-head number
    offset_method: running_head_mode
    kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark]
    same_work: [tao-measure-2011-draft]
    page_map_to:
      tao-measure-2011-draft:
        default_shift: 0
        exception_shift: 1
        exceptions: []
        note: >-
          ~65 numbered items, nearly all in section 1.6, print one page LATER in
          the 2011 draft. Both editions satisfy pdf_page = printed_page + 16, so
          a wrong-edition anchor is off by exactly one printed page, never by 16.
        confirmed_by: "mathlib-initiative/tao-measure-lean CLAUDE.md L48-L55 (anchors corrected 2026-09-04)"
    aliases:
      sha256_prefixes: ["64498329"]
      urls:
        - https://terrytao.wordpress.com/wp-content/uploads/2012/12/gsm-126-tao5-measure-book.pdf
        - https://teorth.github.io/tao-web/an-introduction-to-measure-theory.html
      filenames: ["gsm-126-tao5-measure-book.pdf"]
      titles: ["An Introduction to Measure Theory"]
    confirmed: true
    notes: >-
      The edition to audit against. Both local Lean companions anchor to this
      pagination even though one of them ships the draft PDF.

  tao-measure-2011-draft:
    title: An Introduction to Measure Theory
    authors: [Terence Tao]
    edition: "2011-01-24 draft (pre-publication pagination)"
    isbn: null
    sha256: 62d9565841b8593a7a3d12b3cbcea6ce8d3d3ae2f83b60ebf024d59714c0c6e8
    bytes: 1218984
    pages: 265
    download_url: null
    book_page_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    errata_url: https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
    licence: {note: "Author's pre-publication draft; not redistributable.", fetch: none, redistribute: false}
    offset: 16
    offset_confidence: 0.9960      # 248 of the 249 pages that carry a running-head number
    offset_method: running_head_mode
    kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark]
    same_work: [tao-measure-gsm126-published]
    page_map_to:
      tao-measure-gsm126-published:
        default_shift: 0
        exception_shift: -1
        exceptions: []
        note: >-
          ~65 numbered items, nearly all in section 1.6, print one page EARLIER
          in the published book.
        confirmed_by: "mathlib-initiative/tao-measure-lean CLAUDE.md L48-L55"
    aliases:
      sha256_prefixes: ["62d95658"]
      urls: []
      filenames: ["Measure.Theory.Tao.pdf", "tao-measure-theory.pdf"]
      titles: ["An Introduction to Measure Theory (Tao)"]
    confirmed: true
    notes: >-
      Batch-1 anchor findings were judged against THIS edition and were wrong for
      that reason. A workspace prepares it with role: reference so page_map_to
      can be checked; nothing is audited against it.
```

Both `offset_confidence` figures above were measured on this machine with the
algorithm in §5.1 (official: mode 16, 244/245; draft: mode 16, 248/249).

---

## 3. `source_id`

A short, stable, human-legible slug derived from **title + edition**. It is the
key in `workspace.yaml sources:`, in both registries, in every path under
`sources/` and `pairs/`, and in every row's `source_id` field.

```
source_id = <author> "-" <title> "-" <edition>
```

computed by `cflib.registry.source_id_for()`:

1. **author** — surname of the first author, NFKD-folded to ASCII, lowercased,
   non-alphanumerics collapsed to `-`. Empty when no author is known.
2. **title** — the title lowercased, with the leading article removed
   (`a|an|the`) and these stopwords dropped: `a an the of to on in for and`.
   Take the first **two** remaining words, then drop any word that is a generic
   mathematical noun already implied by the rest (`introduction`, `course`,
   `first`, `second`, `lectures`, `notes`, `theory` when another content word
   survives). `An Introduction to Measure Theory` → `introduction measure
   theory` → drop `introduction`, keep `measure theory`, drop `theory` because
   `measure` survives → `measure`.
3. **edition** — the edition label lowercased, non-alphanumerics collapsed to
   `-`, then the first **two** tokens that are either a 4-digit year, a digit
   run, a known series token (`gsm`, `ams`, `crc`, `springer`, `2e`, `3e`), or
   one of the edition words `draft|preprint|published|revised|corrected|final`.
   Adjacent series+number tokens join (`gsm 126` → `gsm126`).
   `AMS GSM 126, author's preliminary AMS version (published pagination…)` →
   `gsm126-published`. `2011-01-24 draft (pre-publication pagination)` →
   `2011-draft`.
4. Join with `-`, collapse repeats, strip, truncate to 48 characters at a `-`
   boundary.
5. **Collisions.** If the id is already taken by a *different* sha256, append
   `-2`, `-3`, … If no edition token survives and the id would collide, append
   the first 8 hex of the sha256 instead. An id is never silently reused.

Worked: `tao-measure-gsm126-published`, `tao-measure-2011-draft`. These are the
ids the seeded registry ships, so discovery on either target repo resolves to
them by sha before the derivation ever runs.

**Stability rule.** Once a `source_id` appears in a workspace's `sources:` it is
never recomputed. Discovery matches by sha → by registry alias → by derivation,
in that order, and a derived id is only used when nothing matched.

---

## 4. Discovery

`cf onboard <repo>` runs this. It reads the companion repo and never writes to
it.

### 4.1 Pipeline

```
detect_adapter(repo)                       -> (kind, confidence, evidence)
discover_sources_with_adapter(repo, adapter=kind)
                                           -> [SourceClaim, …]
  the seven signals, plus whatever Adapter.discover_sources ADDS (§4.3)
  plus one claim per `cf onboard --source <file>`
resolve_claims(claims, …)                  -> DiscoveryResult
  for each claim:  registry lookup -> locate_pdf -> fingerprint -> infer -> source record
  a claim that NAMES its files skips locate_pdf: the bytes are already in hand
choose_anchor_source(repo, rows, …)        -> (source_id, confidence, evidence)
register:   workspace.yaml + registry.yaml overlay
prepare:    sources/<id>/{text.txt, pages/, items.json, meta.json, errata.*}
extract:    companions/<name>/statements.jsonl
report:     print every inference with its confidence
```

### 4.2 Adapter detection (`detect_adapter`)

Each adapter class implements `Adapter.detect(repo) -> float`. The highest
score wins; a tie or a best score below 0.5 is a `CfError` asking for
`--adapter`. Shipped heuristics:

| kind | signal | score |
|---|---|---|
| `attribution_table` | `pdf-overlay/data/*.json` exists **and** `pdf-overlay/official-source.json` exists | 0.98 |
| `ledger_nodes` | `source-formalization-ledger.json` exists and `pdf-overlay/` does not | 0.9 |
| `docstring_tags` | ≥20 `*.lean` files outside `.lake/` whose docstrings open with a book tag matched by `adapters.itemtag.find_tag` | 0.6 + 0.35 × (tagged / **documented**), capped 0.95 |
| `custom` | never auto-detected | — |

`measure_theory` scores 0.98 on `attribution_table` (it has both files);
`tao-measure-lean` scores ~0.93 on `docstring_tags`.

**`documented`, not `sampled`.** The denominator counts only the sampled files
that carry a docstring at all. A Lean file with no docstring is not evidence
against the convention, and `tao-measure-lean` has 154 Lean files of which 110
are generated blueprint stubs with none: over `sampled` it scores 0.70 and looks
like a repo the skill barely recognises, over `documented` (42 of 44) it scores
0.93, which is what it is. Measured on this machine, 2026-09-16.

### 4.3 Repo signals (`discover_sources`)

Seven signals. Each produces zero or more `SourceClaim`s carrying the file it
came from. **All signals run for every adapter kind**; the adapter kind only
sets the order and the per-signal confidence, because a repo may carry a signal
that "belongs" to another kind (`tao-measure-lean` has both
`formalization.yaml` and an Autoform `graph.json`).

| id | signal | file | fields it yields | conf |
|---|---|---|---|---|
| **S1** | mathlib-initiative metadata | `formalization.yaml` → `sources:` list | `title`, `authors`, `isbn` and `urls` parsed out of the free-text `id` field, `licence` from `license` | 0.8 |
| **S2** | formalization ledger | `source-formalization-ledger.json` → `source: {kind, path, revision, sha256}` | `sha256` (authoritative), `pdf_hints=[path]`, `edition` from `revision` | 0.95 |
| **S3** | official-source record | `pdf-overlay/official-source.json` | `sha256`, `urls=[canonical_pdf_url, final_pdf_url]`, `book_page_url` from `legacy_page_url`/`canonical_page_url`, `bytes` | 0.95 |
| **S4** | Autoform graph metadata | `graph.json` → `metadata.sources[]` | `title`, `pdf_hints=[file]`, `offset_hint` parsed from `page_offset_note` (`PDF page = book page + 16` → 16), scope hint | 0.7 |
| **S5** | prose | `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/**/*.md` | `sha256_prefix` (8–63 hex followed by `…`/`...`/word boundary), full `sha256`, `urls`, `pdf_hints` (quoted paths ending `.pdf`), `offset_hint` (`PDF page = book page + N`, `printed page = PDF page - N`), `anchor_hint` (a sentence containing "anchor" and an edition word) | 0.5 |
| **S6** | filesystem | `sources/*.pdf`, `.lake/official-pdf/*.pdf`, `pdf-overlay/vendor/**/*.pdf`, any `*.pdf` in the repo excluding `.lake/packages/**` | `pdf_hints`, and after fingerprinting a real `sha256` | 0.6 |
| **S7** | citation links | `README.md` first 40 lines, `lakefile.toml`, `formalization.yaml` | `book_page_url` candidates (a link whose text contains the title) | 0.4 |

**The adapter adds an eighth voice, and may only add.** `cf onboard` merges
`Adapter.discover_sources(repo)` into the seven
(`discovery.discover_sources_with_adapter`); a claim repeating a signal cflib
already read is dropped in favour of cflib's, so no companion's prepared item
table depends on the companion's own reading of its source. The addition that
matters is a convention that IS its own manuscript (`blueprint_tex`): such a
claim carries `files` — absolute paths in **reading order** — instead of
`pdf_hints`, and `resolve_claims` builds the source record from those bytes
(`sha256` over the concatenation in that order, `source_format`, `pagination`
from §5.7) rather than locating a PDF. `cf onboard` writes a multi-file
concatenation once into `<workspace>/sources/<id>/manuscript.<suffix>` and the
row points there. `cf onboard --source <file>` makes the same shape of claim with
`discovered.pdf_found_in: manual`.

**Truncated shas are never written as `sha256`.** S5 stores them in
`sha256_prefix`; they resolve only by matching a registry alias prefix or a
fingerprinted PDF whose full sha has that prefix. A `sha256_prefix` that
resolves to nothing is reported in `DiscoveryResult.unresolved` and surfaced by
`cf status`.

**Claim merging.** Claims are merged into one candidate source when they share a
full `sha256`, or a resolved `sha256_prefix`, or a registry hit, or (last
resort) a normalised title **and** a compatible edition token. Merging unions
`urls` and `pdf_hints` and keeps the highest-confidence value per scalar field,
recording every contributing file in `discovered.from`.

On the two targets:

- **`measure_theory`** → S2 gives sha `64498329…` and path
  `sources/gsm-126-tao5-measure-book.pdf`; S3 gives the same sha plus the
  download URL and the book page; S6 finds the byte-identical file at
  `.lake/official-pdf/gsm-126-tao5-measure-book.pdf`; S7 finds the errata page
  in `README.md`. One claim, sha-confirmed, registry-matched →
  `tao-measure-gsm126-published`.
- **`tao-measure-lean`** → S1 gives title/authors/ISBN/preprint URL/licence; S4
  gives `sources/tao-measure-theory.pdf` and `offset_hint: 16`; S6 fingerprints
  that file as `62d95658…`; S5 (`CLAUDE.md` L48–L55) gives **both** prefixes
  `62d95658…` and `64498329…`, the sentence "Both satisfy PDF page = book page +
  16", the "~65 items one printed page earlier" note, and the anchor sentence
  "Book-page anchors in docstrings follow the PUBLISHED pagination". Two claims
  → `tao-measure-2011-draft` (sha-confirmed, locally present) and
  `tao-measure-gsm126-published` (prefix-resolved through the registry and then
  through the library search, §4.4).

### 4.4 Locating the PDF (`locate_pdf`, `search_order`)

Strictly ordered; the first candidate whose sha matches a known sha wins, and
when no sha is known the first candidate that fingerprints successfully wins.

1. **paths the repo names** — every `pdf_hints` entry, resolved (a) relative to
   the repo root, (b) `~`-expanded absolute. `rank = 0`,
   `pdf_found_in: repo_declared`.
2. **the repo's own caches** — the same *basename* found anywhere else in the
   repo, then `.lake/official-pdf/*.pdf`, `sources/*.pdf`,
   `pdf-overlay/vendor/**/*.pdf`. `rank = 1`, `repo_cache`.

   The basename match sits here and not in step 1, which is what §1.3 and the
   worked paragraph below already say: `measure_theory`'s ledger declares
   `sources/gsm-126-tao5-measure-book.pdf` and that path **does not exist**, so
   calling the bytes it actually found in `.lake/official-pdf/` "repo_declared"
   would hide a stale declaration — the one fact a reader of
   `discovered.pdf_found_in` most needs.
3. **the workspace's store** — `<workspace>/sources/<source_id>/*.pdf` and
   `<workspace>/sources/*.pdf`. `rank = 2`, `workspace_store`.
4. **`workspace.yaml library_paths`** — each pattern expanded with `~` and
   `glob`, depth-capped at 4 levels below the pattern's fixed prefix, at most
   5000 files scanned per pattern, `*.pdf` only. The configured list is joined
   by the DERIVED roots — this workspace's own `sources/` store, plus
   `<p>/**/sources` and `<p>/**/.lake/official-pdf` for the parent directory
   `<p>` of every onboarded companion repo — so a sibling checkout holding the
   PDF is reachable on any layout. Those two shapes, and never the bare `<p>`:
   this list is also the allowlist for an absolute path a repo names (rank 0),
   and the parent of a checkout kept on the desktop is the whole desktop, so a
   bare parent would widen the allowlist rather than narrow it. The only shipped
   entry is `~/Downloads`. `rank = 3`, `library_path`.

   The repo currently being onboarded contributes its own derived roots too,
   before it is registered as a companion: otherwise the first `cf onboard`
   searched without them and the second searched with them, and two identical
   commands differed in whether the anchor edition had any text at all.
5. **download** — the registry's `download_url`, **only** when all of:
   `--download` was passed, `policy.allow_download` is not `false`… (the flag
   alone suffices; the policy key exists to let a workspace forbid it
   outright), and `licence.fetch != none`. `curl -L --fail`, written to
   `<workspace>/sources/<source_id>/<basename>`, then fingerprinted and checked
   against the registry sha. The fetch is recorded in `meta.json.provenance`
   (url, http status, bytes, timestamp, sha). **Never redistributed, never
   committed** (`sources/` is gitignored). `rank = 4`, `download`.

Within a rank, candidates are matched in this order: (a) sha equal to a known
sha; (b) basename equal to a registry `aliases.filenames` entry; (c) basename
token overlap with the title/edition slug ≥ 2 tokens; (d) PDF `/Title` metadata
matching a registry `aliases.titles` entry. A candidate whose sha **contradicts**
a known sha is not adopted: it is reported as
`warnings += ["edition_mismatch: <path> is <sha8>, expected <sha8>"]`.

On this machine, step 1 finds `tao-measure-lean/sources/tao-measure-theory.pdf`
(`62d95658…`) and `measure_theory`'s declared `sources/gsm-126-tao5-measure-book.pdf`
does **not** exist, so step 2 finds `.lake/official-pdf/gsm-126-tao5-measure-book.pdf`
(`64498329…`). Onboarding `tao-measure-lean` second finds the published edition
already in the workspace store (step 3) — or, onboarding it first, at step 4
via the derived root that `measure_theory`'s own checkout sits in, once that
companion has been onboarded too.

### 4.5 Fingerprinting (`fingerprint`)

```
{"sha256": <cflib.hashing.sha256_file>, "bytes": <size>,
 "pages": <page count from split_pages>, "text_sha256": <sha256 of pdftotext -layout>,
 "header": <first 8 bytes, e.g. "%PDF-1.4">}
```

`sha256` is identity. `text_sha256` is a *tripwire*, not identity: if the same
`sha256` yields a different `text_sha256` than `meta.json` records, poppler
changed under us and every quote-gate offset must be re-derived — `cf status`
warns and `cf sources refresh <id> --force` fixes it.

### 4.6 Choosing the anchor edition

The hard case: a repo bundles one edition and anchors to another
(`tao-measure-lean`). Three rules, applied in order, and the mechanical one is
decisive.

1. **Unopposed.** Exactly one candidate edition → it is the anchor,
   `anchor_confidence = 1.0`, evidence `{"reason": "single_candidate"}`.
2. **Mechanical anchor agreement** (`pair.anchor_check.check_edition`). For each
   candidate edition, prepare `items.json` and score the companion's extracted
   `page_anchor` rows: `agreement = ok / (ok + mismatched)` over rows whose item
   is a heading in that edition. The winner is the highest agreement, and it is
   **accepted only if** `winner ≥ 0.80` and `winner - runner_up ≥ 0.05`.
   `anchor_confidence = winner`. Evidence records the full per-edition table.
   For `tao-measure-lean` the published edition wins by roughly the ~65 items of
   §1.6 — a margin far above 0.05 because both editions share the same
   `offset: 16`, so the difference is purely the one-page shift.
3. **Prose hint** (`SourceClaim.anchor_hint`) **corroborates only.** A sentence
   like "Book-page anchors in docstrings follow the PUBLISHED pagination" raises
   confidence by 0.05 (capped at 1.0) when it agrees with rule 2, and when it
   *disagrees* the whole decision is refused with a `CfError` quoting both the
   sentence and the agreement table. A prose hint alone, with no anchors to
   score, sets the anchor with `anchor_confidence = 0.6` and
   `confirmed: false` on the companion entry.

Below the thresholds, `cf onboard` refuses rather than guessing:

```
error: cannot decide which edition ours anchors to
  tao-measure-gsm126-published  agreement 0.62 (412 ok / 253 mismatched)
  tao-measure-2011-draft        agreement 0.58 (387 ok / 278 mismatched)
hint:  re-run with `cf onboard <repo> --anchor-source <id>`, or check whether the
       companion's anchors are internally consistent at all (`cf pair`)
```

The refusal is where a mechanical decision was really *attempted*. `cf onboard`
elects twice: once before extraction, where there are no rows and rule 3 is all
there is, and once after, against the rows this run just wrote. The first
election warns and keeps its 0.6 pick; the second refuses. Refusing at the first
refused every *first* onboard of any two-edition work, flags or not, because rule
3 scores 0.6 whether or not the editions are actually distinguishable — an anchor
that is undetermined until extraction is not an undecidable one. Both runs of the
identical command still end the same way: run 1 refuses after extracting, and run
2 scores the same rows at the first election and refuses there. The second
election covers **every** cluster of two or more editions, not only the anchor's
own, because the anchor may win a single-candidate cluster while another work's
editions are the undecided pair.

The bundled-but-not-anchored edition is recorded in
`companions.<name>.bundled_sources` and given `role: reference` in `sources:`,
so it is prepared (text, items) for `page_map_to` checking and is audited by
nobody.

### 4.7 Registration

- A claim that matched the registry copies the registry's fields into
  `workspace.yaml sources.<id>`, keeps `registry: skill|overlay`, and sets
  `confirmed` from the registry entry.
- A claim that matched nothing derives a `source_id` (§3), writes an entry to
  `<workspace>/registry.yaml` with `confirmed: false`, copies it into
  `workspace.yaml` with `registry: overlay, confirmed: false`, and `cf status`
  prints it under **"unconfirmed sources"** with the one command that fixes it.
- `cf onboard` prints a block naming every inference and its confidence
  (`DiscoveryResult.render()`); see §7.2 for the exact shape.

---

## 5. Inference

All of it lives in `scripts/cflib/inference.py`, is pure (text in, numbers out),
and is unit-testable without a PDF.

### 5.1 Page offset from running heads

```
for each PDF page i (1-based), after text.split("\f"):
    lines   = non-empty, right-stripped lines of the page
    cands   = {int(t) for l in (lines[0], lines[-1])
                      for t in (l.split()[0], l.split()[-1])
                      if t.strip(".,").isdigit() and 0 < int(t) <= n_pages}
    deltas[i] = {i - c for c in cands}          # one page votes at most once per delta
offset      = mode over the multiset {d : d in deltas[i], for all i}
voting      = #{i : deltas[i] non-empty}
agreeing    = #{i : offset in deltas[i]}
confidence  = agreeing / voting
```

Returned as `OffsetGuess(offset, confidence, voting_pages, total_pages,
histogram)`. **`confidence < 0.9` is refused**, not warned about:

```
error: could not infer the page offset for tao-measure-gsm126-published
       (best guess 16, confidence 0.71 over 245 pages with a running head)
hint:  set it explicitly: cf sources set tao-measure-gsm126-published offset=16
```

Measured on this machine, exactly this algorithm: official PDF 265 pages, 245
with a running-head number, mode **16** with 244 agreeing → **0.9959**; 2011
draft 265 pages, 249 voting, mode **16** with 248 agreeing → **0.9960**. A
declared `offset_hint` from S4/S5 (`PDF page = book page + 16`) is used only to
*cross-check*: agreement raises `offset_confidence` to `max(conf, 0.95)`,
disagreement is a refusal quoting both.

### 5.2 Item kinds from headings

```
ANY_HEADING = r"^[ \t]*([A-Z][A-Za-z]{2,15})\s+(\d+(?:\.\d+)*)\.?(?=[\s.(]|$)"
```

Count matches per capitalised word over the whole text, keeping only headings
at least as deep as the source's own numbering (§5.5). Case is folded onto one
spelling per word (`canonical_kind_word`: `THEOREM` → `Theorem`, `McShane`
untouched), because an AMS-style ALL-CAPS head is the same kind set differently
and was otherwise invisible. Drop the structural words `Chapter Section
Subsection Part Figure Table Page Appendix Volume Step Case Equation` into
`structural_kinds` (reported, never used as item kinds). Keep the rest with
`count >= min_count` (default 3), ordered by count. The result is written to
`meta.json.kind_counts` and becomes the source's `kinds:`. A kind in the
registry that the text does not produce is kept (the registry is the more
careful record) but flagged in `meta.json.kind_warnings`.

The leading capital stays required: `pdftotext -layout` wraps body lines, so a
continuation can start with any lower-case word and `and 1.2.3` is not a
heading. The heading regex that builds the item TABLE
(`sources.prepare_source.heading_regex`) matches its kind alternation
case-insensitively for the same reason and canonicalises the matched word
against this source's declared `kinds` at the match site — never in
`items.clean()`, which is frozen.

### 5.3 Scope from what the companions tag

Scope is **not** read off the book and is **not** a flag. After extraction:

```
infer_scope(rows, items_json, min_items=5, min_ratio=0.2) ->
  {"chapters": [c, …], "by_chapter": {c: {"tagged": n, "in_book": m, "ratio": r}},
   "method": "companion_tags"}
```

Chapter `c` is in scope when the companions bound to this source tag at least
`min_items` (5) items of chapter `c`, **or** at least `min_ratio` (20%) of the
book's items in chapter `c`. The union over the source's companions becomes
`sources.<id>.chapters`, which is the single scope every stage narrows to
(`cf pair`, `cf plan-run`, `cf detect-proxies`) — exactly the role
`book.chapters` used to play, now derived instead of typed. Re-running
`cf extract` recomputes it; a hand override is `cf sources set <id>
chapters=1,2`, which pins `scope_method: manual`.

For the Tao workspace both companions tag chapter 1 only, so `chapters: [1]` —
which is the scope that made def-coverage reproducible (the unscoped vs scoped
run disagreed by 3 bridge-only + 1 missing definition).

### 5.4 The errata page

Candidates, ranked:

1. the registry's `errata_url` for the resolved sha (confidence 1.0),
2. a `book_page_url` the repo names for this source — S3
   `legacy_page_url`/`canonical_page_url`, S7 README link (0.8),
3. the `download_url`'s parent page, i.e. the author-site page that links the
   PDF (0.5).

The chosen URL is fetched by `sources.fetch_errata.fetch()` — **curl, raw HTML
kept verbatim in `errata.raw.html`, rendered to `errata.md` by the stdlib
renderer, never a summarising fetch** (a summarising fetch already lost
Definition 1.5.11 once). After the fetch, the raw HTML is checked for the word
`errata` (case-insensitive); absent, `errata_confidence` is halved and
`meta.json.warnings` records `errata_url_unconfirmed`. Both Tao repos cite
`https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/`,
which is where the errata live, so this resolves at confidence 1.0 from the
registry and is corroborated by both repos.

### 5.5 Numbering depth from headings

How many numeric segments an item label carries is the **source's**, not a
constant. `Theorem 7`, `Theorem 3.4`, `Definition 1.2.3` and `Lemma 2.3.4.1`
are four real book conventions, and reading a book with the wrong one either
finds nothing or — the failure that produces *wrong* results rather than
missing ones — matches a deep label down to its first segments and merges
every item of a section onto one audit key.

```
infer_numbering(text, min_count=3, min_share=MIN_DEPTH_SHARE) ->
  NumberingGuess(min_segments, max_segments, confidence, headings, counts, method)
```

Count `ANY_HEADING` matches by segment depth (structural words excluded). A
depth is part of the numbering when it carries at least `min_count` (3)
headings **and** `MIN_DEPTH_SHARE` (0.05) of them — which drops the handful of
stray one-number lines a typeset book always has (`Let 2`, `Bibliography 1`;
both Tao editions carry four) without dropping a real appendix convention.
`confidence` is the share of headings the accepted depths account for. A text
with no numbered heading at all returns `DEFAULT_NUMBERING` `(2, 3)` at
confidence 0.0 with `method: "default"`.

The result is stored as `numbering: {min_segments, max_segments, confidence,
headings, counts, method}` in both `items.json` and `meta.json`, so every stage
reads one definition. `cflib.items` takes it as the `numbering=` keyword;
`items.DEFAULT_NUMBERING` `(2, 3)` is what a caller that does not know the
source gets, and it is the grammar the existing corpus was keyed with, so a
re-parse of the baseline is byte-identical. **An empty mapping means "nothing
was inferred" and reads back as `DEFAULT_NUMBERING`, never as `(2, 2)`** — a
host without `cflib` writes `numbering: {}` and its table really was built with
the default.

A label deeper than `max_segments` is a **reject**, `too_many_segments`, and
becomes no row at all; the caller reports it exactly as the item grammar's
reject list is reported. Both Tao editions infer `(3, 3)` at confidence > 0.95
and produce zero such rejects, so nothing in the corpus moves.

This is the one inference a re-run cannot settle, because re-running reads the
same headings and reaches the same depth. Its override is therefore
`cf sources set <id> numbering=<min>,<max>`, which stamps `numbering_method:
manual` on the workspace row and `method: "manual"` on the stored dict; both
the `too_many_segments` refusal and the `headings_deeper_than_the_numbering`
warning name it.

### 5.6 Item completeness

The only wrong-document guard used to be `items == 0`, which a real book almost
never trips: a 404-page book whose numbering puts the kind *after* the number
(`3.22 theorem`) prepared a table a fraction of a percent of its own size and
was adopted in silence. A book's numbering is its own denominator:

```
item_completeness(numbers) -> {found, expected, completeness, groups, worst}

    slots[prefix] = {last index seen}        # prefix = every segment but the last
    expected     = Σ max(slots[prefix])      # "items run up to 1.3.21" ⇒ 21 slots
    found        = Σ |{i ∈ slots[prefix] : 1 ≤ i ≤ max}|
    completeness = found / expected
```

The slots of one numeric prefix are **pooled across kinds**, deliberately
deviating from a per-`(kind, chapter, section)` denominator: whether a book runs
one counter per section (Tao: `Definition 1.3.8` then `Lemma 1.3.9`) or one per
kind is its own convention, and this estimate does not get to dictate it.
Pooling reads the numbering the most complete way it can be read, so the score
is a floor on what was found, never an accusation.

Below `MIN_ITEM_COMPLETENESS` (**0.6**) `cf onboard` / `cf sources refresh`
refuse, in the same style as the offset rule: say which signal fired, echo the
top few `kind_counts` so the user can see the grammar read prose rather than
headings, and name the settlement — `cf sources set <id> kinds=…`, or
`cf sources set <id> completeness_ok=true`, which stamps `completeness_method:
manual`. Measured, not chosen: both Tao editions score **0.9961** (254 of the
255 slots their sections number) and the kind-after-number book scores 0.086,
so the floor sits an order of magnitude above the bad case and 0.39 below the
good one.

A source declaring kinds of which the text uses none (`kinds_not_in_text`
covering every one, or `kinds_not_inferred`) is refused by the same function,
before the completeness figure is consulted: that kind list belongs to another
book.

### 5.7 Source format and pagination

A source need not be a paginated PDF. `sources/locator.py` holds the two facts
that make a manuscript a source and the refusals that keep it honest:

- `source_format(path)` → `pdf` | `text`, from the suffix alone (a human named
  the file; sniffing the bytes could prepare a different document than the one
  they meant). The accepted text suffixes are `locator.TEXT_SUFFIXES`, and
  every one of them either has a branch in `heading_paths` or is plain text by
  nature.
- `detect_pagination(text)` → `formfeed` when the text carries `\f` page breaks
  (a `pdftotext` dump kept as a file still has its pages, and everything
  page-dependent then works exactly as it does on the PDF), else `none`.

An unpaginated source has `pages: 0`, `offset: 0`, `offset_method: none`,
`locator: section`, and its items carry a heading path plus a line instead of a
`pdf_page`. Page-dependent features refuse on it by name rather than compute
from a zero nothing supports: `require_pagination(meta, feature)` for an
offset, a page bind or a printed-page anchor. Page IMAGES ask a second question,
because form feeds are not a PDF: `require_pdf(meta, feature)` refuses
`cf sources refresh --images` on any text manuscript, paginated or not.

---

## 6. Item namespacing and the on-disk layout

### 6.1 The namespacing rule

An item is the pair **`(source_id, item)`**. The item half follows the strict
grammar of `cflib/items.py` — `<Kind> <n>[.<n>…]`, one to four numeric levels
with the depth inferred per source and stored as `numbering` (§5.5), with an
optional sub-part, or `In-text p.<n>` — because the grammar is per-source and
the kinds list is per-source. A label deeper than the source's numbering is a
reject (`too_many_segments`), never truncated onto a shorter item's key.

**The item half has two normal forms, and a join must use the right one.**
`items.clean(label)` is the ID form: whitespace only, what `finding_id` hashes,
frozen. `items.join_key(label)` is the KEY form: identical to `clean()` for a
numbered label, and the strict grammar for an unnumbered ("in-text") one.
Unnumbered items have no printed id, so every generation of the corpus invents
its own spelling — the v1 findings write `In-text, book p. 71`,
`In-text convention, book p. 29 (Exercise 1.2.5: …)` and `In-text, Definition
1.4.35`, while extraction writes `In-text p.71`, `In-text p.29` and `In-text
Definition 1.4.35`. Exact-match overlap between those two vocabularies is
**zero**, so before the key form existed no in-text item could be joined across
runs at all: `union --new-only` called every re-found in-text defect new, the
agreed-across-runs report found no agreement, and a baseline regression check
scored a re-found defect as a miss (the live case, 2026-09-18: the p.71 defect
on `RiemannIntegrableOn.complexLebesgueIntegralOn_Icc`).

The key form collapses a descriptive prefix (`convention`, `proof of …`), a
trailing parenthetical and a page range onto one spelling, and a printed page
always beats a cross-reference the label happens to cite, so `In-text, book
p. 51 / Definition 1.3.6` is the p.51 item. A page-keyed item and an
item-anchored one are different keys, and an item-anchored one is **not** the
numbered item it names: `In-text Definition 1.4.35` is the unnumbered material
beside `Definition 1.4.35`, and merging them lets a finding about one hide a
finding about the other. An in-text label the grammar refuses keeps itself as
its own key and comes back with a `Reject` the caller reports (`tally()`
carries them in `unjoinable`) — it is never guessed onto some other item.

Callers: `harvest/tally.py::collapse_to_book_item` (hence `union.py`,
`agreed.py`) and `ledger/compat.py::item_join_key` (hence `route.py`,
`workflow_args.py`, `validate_ledger.py`, `migrate.py`). **Nothing hashes the
key form**, so no `finding_id` changes.

**The item is only one half of the union key.** `union.py` groups on
`(source_id, repo, item, kind)`, so two corpus generations join only when their
*repo* vocabularies agree as well. They did not: the v1 corpus spells the
audited repository `ours` (the campaign's name for it) and a workspace spells
the same repository by its companion id (`tml-prefix`), so on the live data the
canonical item key alone still produced two p.71 entries and `--new-only` still
called the re-found defect new. A repo name is not guessable from the corpus —
two names may or may not be one repository — so it is **stated at the import
boundary**, exactly where `--source` states the source a v1 row never recorded:
`convert_v1 --repo-map ours=tml-prefix` (and `cf import-corpus --repo-map`)
rewrites the `repo:` half of `unfaithful_decls`, `repos_involved`, `faithful`
and `matches[].decls[].repo`, keeps the v1 spelling in `unfaithful_decls_v1`,
and records the map on the document. Without it nothing is renamed and the
import **warns**, naming the repos the workspace does not have — the same
discipline as `unjoinable`: reported, never guessed. With it, the 2026-09-18
ground-truth run against the imported `findings-hunt` goes from 0 shared
entries / 8 new to 4 shared / 4 new, and the p.71 defect is `new: false`.

| context | representation |
|---|---|
| a JSON/JSONL row | two fields: `"source_id": "<id>"`, `"item": "Exercise 1.3.19"` |
| an in-memory dict key | the tuple `("tao-measure-gsm126-published", "Exercise 1.3.19")` |
| a single string (task id, finding payload, log line) | `qualified_item(source_id, item)` = `"<source_id>::<item>"` |
| a filename / slug | `source_slug(source_id) + "--" + items.slug(item)` → `tao-measure-gsm126-published--exercise-1-3-19` |

`::` is the one separator, `cflib.items.QUALIFIED_SEP`. `split_qualified()` is
its inverse and raises on a string without it. A bare item string is **never**
used as a cross-source key; a function that takes one takes a `source_id` too.

`finding_id` therefore becomes a five-part digest:

```python
finding_id(run_id, source_id, item, kind, unfaithful_decls)
  = sha256("\n".join([run_id, source_id, items.clean(item), kind, *sorted(decls)]))
```

The two load-bearing details survive: parts are joined with `\n` and the
declarations go in as separate parts, and the item goes through `items.clean()`
first so `Exercise    1.3.19` and `Exercise 1.3.19` still hash the same. The
digests change — which is correct, because the old ids collide across sources —
and `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` keeps asserting what
it actually asserts: that `cflib.hashing`, `gate/verify_findings.py` (which
inlines the function for a host without `cflib`), `sources/compat.py`,
`ledger/compat.py` and `detect/_compat.py` all agree on every digest.

### 6.2 Workspace layout

```
<workspace>/
  workspace.yaml                 committed
  registry.yaml                  committed: the overlay (metadata only, no book text)
  .gitignore                     written by `cf init`

  sources/<source_id>/           GITIGNORED (copyrighted)
    text.txt                     pdftotext -layout output          (was book/<id>/book.txt)
    pages/pNNN.txt               one file per PDF page
    images/pNNN.png              only when rendered
    items.json                   {source_id, source_sha, offset, kinds, numbering,
                                  pagination, locator, count, by_kind, by_chapter,
                                  ambiguous, rejects, items:[…]}
    meta.json                    identity + source_format + pagination + locator +
                                 inference (offset, kinds, numbering, item_completeness)
                                 + provenance        (was book/<id>/book.json)
    errata.md                    rendered verbatim
    errata.raw.html              the bytes curl returned
    errata.json                  fetch metadata (url, status, shas, changed)
    <basename>.pdf               only when `--download` fetched it; never committed

  companions/<name>/             committed
    adapter.yaml  conventions.md  statements.jsonl  state.json  lint.json

  pairs/<source_id>/             committed
    pairs.jsonl coverage.md index.json reconciliation.json rejected.json
    queue.json targets.<name>.json def-coverage.<name>.json uncovered.<name>.json
    anchor-check.json

  runs/<run-id>/                 run.yaml tasks/ prompts/ gate.json tend-root/(ignored)
  corpus/<run-id>/               findings + RECEIPT.json   (committed: the baseline)
  ledger/                        accepted.<name>.jsonl refuted.<name>.jsonl
                                 adjudicated.<name>.md routed/findings.<name>.jsonl
  detect/<name>/                 proxy-detection artifacts
  reports/                       tally.<run>.json union.<name>.json handoff.<date>.md
```

Rows and files carry the source everywhere it can be ambiguous:

- `statements.jsonl` rows: `source_id`, `source_sha` (required fields).
- `pairs/<source_id>/*`: the source is in the path **and** in each row.
- `runs/<id>/run.yaml`: `sources: [<id>, …]` plus per-task `source_id`.
- `runs/<id>/gate.json`: `sources: {<id>: {text, sha256, text_sha256,
  page_offset, images_dir, item_pages}}` — the gate picks the text by the
  document's `source_id`. `item_pages` is `{item: page OF THE PINNED TEXT}`
  (`items.json`'s `pdf_page`, never its printed `page`), the first of the three
  routes the gate uses to bind a quote to the page its item is stated on. The
  gate also reads a top-level `max_match_pages`: how many pages the matched text
  may occur on before it is refused as a running head (0 turns that off).
- findings documents and every `disagreements[]` row: `source_id`, `source_sha`.
- ledger rows, routed rows, tally rows, union entries: `source_id`,
  `source_sha`.
- `corpus/<run>/RECEIPT.json`: `sources: {<id>: <sha>}` and a per-source count.

### 6.3 `.gitignore` (both repos)

The workspace `.gitignore` written by `cf init` changes `book/` → `sources/`.

**Trap, and it is the same one the current file documents:** the skill repo's
own `.gitignore` carries the workspace patterns, so once `scripts/book/` becomes
`scripts/sources/`, the pattern `sources/` would hide the entire Stage-1 package
from git. The existing `!scripts/book/` negation must become `!scripts/sources/`
in the **same commit** as the package rename, or the package silently vanishes
from the next `git add`.

---

## 7. The CLI surface

Global: `--workspace <dir>` (default `$CF_WORKSPACE`, else the nearest ancestor
holding `workspace.yaml`), `-v`, `-q`. No `--campaign`, no `$CF_CAMPAIGN`.

| command | one line |
|---|---|
| `init <dir>` | create the workspace as a local git repo with no remote; write `workspace.yaml`, `registry.yaml`, `.gitignore`, pre-push hook. **No other flags** except `--name`, `--no-git`, `--allow-nested`, `--force` |
| `onboard <repo> [--name N] [--adapter K] [--download] [--anchor-source ID] [--source FILE] [--dry-run] [--force]` | discover → register → prepare → extract, printing every inference and its confidence. `--source` (repeatable) names a PDF or text manuscript the repo does not name (§4.3); it gives the LOCATION only, like `sources add`, and is what the "no source could be discovered" refusal points at — a companion that does not exist yet cannot be `--for`ed |
| `sources list` | one row per source: id, sha8, edition, offset±conf, kinds, items, errata, companions, confirmed |
| `sources show <id>` | the full record, its `same_work` siblings, its `page_map_to`, its provenance and its prepared artifacts |
| `sources add <source> --for <companion> [--anchor] [--download]` | add a source by hand (the exception); fingerprints, registers, prepares, and appends to that companion's `sources`. `<source>` is a PDF or a manuscript that is already text (§5.7) |
| `sources set <id> key=value …` | override a field (`offset=16`, `errata_url=…`, `chapters=1,2`, `kinds=…`, `numbering=3,4`, `completeness_ok=true`, `confirmed=true`); sets the matching `*_method: manual` and records the override in `meta.json` |
| `sources refresh [<id>] [--force] [--images] [--no-errata]` | re-prepare text / page map / items / errata for one or every source. `--images` needs the PDF itself and refuses on a text manuscript. **This replaces `cf book`** |
| `extract [--companion X]` | adapters → `statements.jsonl`; rows carry `source_id` + `source_sha` |
| `pair [--source ID] [--only a,b] [--chapter N]` | **iterates the shared sources**: one `pairs/<source_id>/` per source with ≥2 companions, plus per-source coverage/targets/anchors for single-companion sources |
| `plan-run --preset P [--source ID] [--companion X] [--host H]` | a run is scoped to one source; `--source` is required only when the selected companions share more than one |
| `run <run-id> --i-have-consent` | unchanged, plus the run's sources in the manifest and the consent record |
| `status [--companion X] [--source ID] [--json]` | per-companion state **and** a sources table; prints an `unconfirmed sources` block and the fix command |
| `harvest <run-id>` / `import-corpus --run-id … --from … [--source ID] [--source-sha HEX]` | receipts carry `sources: {<id>: <sha>}`; the source gate replaces the edition gate |
| `tally [--union] [--agreed] [--source ID]` | counts grouped by `(source_id, repo)` |
| `adjudicate` / `route` / `report` | unchanged flow; every row, message and number carries its source |
| `detect-proxies <name> [--source ID]` | mechanical detectors per source |

Deleted: `cf book`. Out of scope in v1, unchanged: `fix`, `review-pr`,
`camera-ready`, `publish-verdicts`, `curate`, `lint`.

### 7.1 `cf init <dir>` — no flags

```
$ cf init ~/Desktop/tao-workspace
wrote ~/Desktop/tao-workspace/workspace.yaml
wrote ~/Desktop/tao-workspace/registry.yaml        (overlay; empty)
wrote ~/Desktop/tao-workspace/.gitignore
git init ~/Desktop/tao-workspace (no remote)
installed ~/Desktop/tao-workspace/.git/hooks/pre-push

workspace : ~/Desktop/tao-workspace
sources   : none yet
next:
  cf onboard ~/Desktop/tao-measure-lean
```

The refusals `cf init` already performs are kept verbatim: not inside the skill
repo, not inside a Lean companion, not inside a git repo that has a remote,
`--allow-nested` for the remote-less nested case.

### 7.2 `cf onboard <repo>` — what it prints

One command, no flags in the common case, and every inference is shown with its
confidence and the file it came from.

```
$ cf onboard ~/Desktop/tao-measure-lean
repo      : /Users/jack/Desktop/tao-measure-lean @ 9afe70f
adapter   : docstring_tags                       confidence 0.95   (1,413 of 1,487 declarations tagged)
name      : ours                                 (from the directory name; --name overrides)

sources discovered
  tao-measure-gsm126-published   sha 64498329…   registry: skill (confirmed)
      title    An Introduction to Measure Theory — Terence Tao
      edition  AMS GSM 126, author's preliminary AMS version (published pagination)
      from     CLAUDE.md#L48-L55 (sha prefix), formalization.yaml#sources (title/licence)
      pdf      ~/Desktop/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf
               found in: library_path (<the derived root measure_theory sits in>)
      offset   16                                confidence 0.996  (244/245 running heads)
      kinds    Definition Lemma Theorem Proposition Corollary Exercise Example Remark
      errata   https://terrytao.wordpress.com/books/an-introduction-to-measure-theory/
               confidence 1.00 (registry), corroborated by README.md
  tao-measure-2011-draft         sha 62d95658…   registry: skill (confirmed)   role: reference
      from     graph.json#metadata.sources, CLAUDE.md#L48-L55
      pdf      sources/tao-measure-theory.pdf     found in: repo_declared
      offset   16                                confidence 0.996  (248/249 running heads)
      same work as tao-measure-gsm126-published; ~65 items of §1.6 shift by one printed page

anchor edition
  tao-measure-gsm126-published                   confidence 0.99
      anchor agreement  published 0.99 (1,281 ok / 13 mismatched)
                        draft     0.94 (1,229 ok / 65 mismatched)
      corroborated by CLAUDE.md: "Book-page anchors in docstrings follow the PUBLISHED pagination"
  the repo BUNDLES tao-measure-2011-draft but does not anchor to it -> bundled_sources

prepared  sources/tao-measure-gsm126-published/  text 265 pages, 763 items, errata 41 entries
prepared  sources/tao-measure-2011-draft/        text 265 pages, 763 items      (reference)
extracted companions/ours/statements.jsonl       1,157 rows / 763 items
scope     chapters [1]                           from the items ours actually tags

next:
  edit companions/ours/conventions.md and have the repo owner confirm it
  cf onboard ~/Desktop/measure_theory
```

(The extraction and item counts above are illustrative; the confidences,
offsets, shas and file provenance are the real values measured on this machine.)

### 7.3 `cf status` surfaces the unconfirmed

```
unconfirmed sources (discovered, nobody has checked them)
  smith-analysis-2e   sha 1a2b3c4d…   from ~/Desktop/foo/CLAUDE.md
      confirm with:  cf sources show smith-analysis-2e
                     cf sources set smith-analysis-2e confirmed=true
      or promote it into the skill's registry/sources.yaml
```

---

## 8. The interfaces the packages must agree on

This is the contract between the work packages. Signatures are binding; bodies
are not. Anything not listed keeps its current signature with `campaign` →
`workspace` and `book_sha` → `source_sha` substituted.

Reading conventions for the block below: `...` is an elided body, `…` inside a
parameter list means "the rest of the current parameters, unchanged", and a
commented-out `def` line is a signature that is already correct and is repeated
only for context. The block is a specification, not a module: it is not meant to
import.

```python
# =====================================================================
# CORE — scripts/cflib/workspace.py   (replaces cflib/campaign.py)
# =====================================================================
WORKSPACE_FILE = "workspace.yaml"
WORKSPACE_VERSION = 2


@dataclass(frozen=True)
class SourceRef:
    """One source as `workspace.yaml` describes it."""
    id: str
    sha256: str | None
    title: str
    authors: tuple[str, ...]
    edition: str
    isbn: str | None
    kinds: tuple[str, ...]
    offset: int
    offset_confidence: float
    offset_method: str
    pages: int | None
    text_sha256: str | None
    errata_url: str | None
    errata_confidence: float
    download_url: str | None
    book_page_url: str | None
    licence: dict
    pdf: Path | None
    chapters: tuple[int, ...]
    role: str                         # "audited" | "reference"
    same_work: tuple[str, ...]
    page_map_to: dict
    confirmed: bool
    registry: str                     # "skill" | "overlay" | "local"
    discovered: dict

    def short(self) -> str: ...                       # "tao-measure-gsm126-published (64498329)"
    def printed_page(self, pdf_page: int) -> int: ...  # pdf_page - offset
    def pdf_page(self, printed: int) -> int: ...       # printed + offset
    def shift_to(self, other_id: str, item: str | None = None) -> int | None:
        """Printed-page shift to a sibling edition, per page_map_to."""


@dataclass(frozen=True)
class CompanionRef:
    name: str
    repo: Path
    adapter: str
    sources: tuple[str, ...]
    anchor_source: str
    bundled_sources: tuple[str, ...]
    owner: str
    write_policy: str
    commit: str | None
    discovered: dict


class Workspace:
    root: Path
    data: dict

    @classmethod
    def load(cls, start: str | Path | None = None) -> "Workspace": ...
    def save(self) -> Path: ...

    @property
    def paths(self) -> "WorkspacePaths": ...
    @property
    def name(self) -> str: ...

    # --- sources -----------------------------------------------------
    @property
    def sources(self) -> dict[str, SourceRef]: ...
    def source(self, source_id: str) -> SourceRef: ...            # raises WorkspaceError
    def source_ids(self, *, role: str | None = None) -> list[str]: ...
    def source_by_sha(self, sha: str) -> SourceRef | None: ...
    def upsert_source(self, source_id: str, **fields) -> dict: ...  # does not save
    def remove_source(self, source_id: str) -> None: ...

    # --- companions --------------------------------------------------
    @property
    def companions(self) -> dict[str, CompanionRef]: ...
    def companion(self, name: str) -> CompanionRef: ...           # raises WorkspaceError
    def companion_names(self) -> list[str]: ...                   # yaml order + on-disk extras
    def upsert_companion(self, name: str, **fields) -> dict: ...

    # --- the multi-source joins (R1) ---------------------------------
    def sources_of(self, companion: str) -> list[str]: ...
    def companions_of(self, source_id: str) -> list[str]: ...
    def shared_sources(self, min_companions: int = 2) -> dict[str, list[str]]:
        """{source_id: [companion, …]} for every source ≥ min_companions audit.
        A differential is possible exactly for these keys."""
    def anchor_source(self, companion: str) -> str: ...
    def kinds(self, source_id: str) -> tuple[str, ...]: ...
    def offset(self, source_id: str) -> int: ...
    def chapters(self, source_id: str) -> tuple[int, ...]: ...

    # --- gates -------------------------------------------------------
    def require_source_sha(self, source_id: str, sha: str | None, where: str) -> None:
        """The source gate (was require_book_sha). Refuses, never warns."""
    def check_host(self, host: str) -> str: ...
    def unconfirmed_sources(self) -> list[SourceRef]: ...

    # --- config ------------------------------------------------------
    @property
    def hosts(self) -> dict: ...
    @property
    def host_allowlist(self) -> list[str]: ...
    @property
    def models(self) -> dict: ...
    def model(self, role: str) -> str | None: ...
    @property
    def defaults(self) -> dict: ...
    def default(self, key: str, fallback=None): ...
    @property
    def budget(self) -> dict: ...
    @property
    def policy(self) -> dict: ...
    @property
    def library_paths(self) -> list[str]: ...

    def validate(self) -> list[str]: ...


def find_workspace_root(start: str | Path | None = None) -> Path: ...
def from_args(args) -> Workspace: ...          # every cfcmd module uses this


# =====================================================================
# CORE — scripts/cflib/paths.py
# =====================================================================
@dataclass(frozen=True)
class WorkspacePaths:
    root: Path                                  # NOTE: no more book_id field

    @property
    def workspace_yaml(self) -> Path: ...       # workspace.yaml
    @property
    def registry_yaml(self) -> Path: ...        # registry.yaml (overlay)
    @property
    def gitignore(self) -> Path: ...

    # --- sources (gitignored) ---------------------------------------
    @property
    def sources_dir(self) -> Path: ...          # sources/
    def source_dir(self, source_id: str) -> Path: ...
    def source_text(self, source_id: str) -> Path: ...        # text.txt
    def source_meta(self, source_id: str) -> Path: ...        # meta.json
    def source_items(self, source_id: str) -> Path: ...       # items.json
    def source_pages_dir(self, source_id: str) -> Path: ...
    def source_images_dir(self, source_id: str) -> Path: ...
    def source_errata_md(self, source_id: str) -> Path: ...
    def source_errata_raw(self, source_id: str) -> Path: ...
    def source_errata_json(self, source_id: str) -> Path: ...
    def list_sources(self) -> list[str]: ...                  # prepared on disk

    # --- companions (unchanged names) --------------------------------
    @property
    def companions_dir(self) -> Path: ...
    def companion_dir(self, name: str) -> Path: ...
    def adapter_yaml(self, name: str) -> Path: ...
    def conventions_md(self, name: str) -> Path: ...
    def statements_jsonl(self, name: str) -> Path: ...
    def state_json(self, name: str) -> Path: ...
    def lint_json(self, name: str) -> Path: ...
    def list_companions(self) -> list[str]: ...

    # --- pairing, now per source -------------------------------------
    @property
    def pairs_root(self) -> Path: ...                          # pairs/
    def pairs_dir(self, source_id: str) -> Path: ...           # pairs/<source_id>/
    def pairs_jsonl(self, source_id: str) -> Path: ...
    def coverage_md(self, source_id: str) -> Path: ...
    def index_json(self, source_id: str) -> Path: ...
    def reconciliation_json(self, source_id: str) -> Path: ...
    def rejected_json(self, source_id: str) -> Path: ...
    def queue_json(self, source_id: str) -> Path: ...
    def targets_json(self, source_id: str, name: str) -> Path: ...
    def def_coverage_json(self, source_id: str, name: str) -> Path: ...
    def uncovered_json(self, source_id: str, name: str) -> Path: ...
    def anchor_check_json(self, source_id: str) -> Path: ...
    def list_paired_sources(self) -> list[str]: ...

    # --- runs, corpus, ledger, detect, reports (unchanged) -----------
    # runs_dir run_dir run_yaml gate_json tasks_dir prompts_dir tend_root
    # corpus_dir corpus_run_dir receipt_json
    # ledger_dir accepted_jsonl refuted_jsonl adjudicated_md routed_dir routed_jsonl
    # detect_dir detect_companion_dir
    # reports_dir tally_json union_json agreed_high_json
    def ensure_skeleton(self) -> list[Path]: ...


# =====================================================================
# CORE — scripts/cflib/registry.py   (new)
# =====================================================================
REGISTRY_VERSION = 1


def skill_registry_path() -> Path: ...          # <skill>/registry/sources.yaml


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    title: str
    authors: tuple[str, ...]
    edition: str
    isbn: str | None
    sha256: str | None
    bytes: int | None
    pages: int | None
    download_url: str | None
    book_page_url: str | None
    errata_url: str | None
    licence: dict
    offset: int | None
    offset_confidence: float | None
    offset_method: str
    kinds: tuple[str, ...]
    same_work: tuple[str, ...]
    page_map_to: dict
    aliases: dict                    # {sha256_prefixes, urls, filenames, titles}
    confirmed: bool
    notes: str
    origin: str                      # "skill" | "overlay"

    def to_source_fields(self) -> dict:
        """The workspace.yaml `sources.<id>` body this entry implies."""


class Registry:
    entries: dict[str, RegistryEntry]

    @classmethod
    def load(cls, workspace: "Workspace | None" = None) -> "Registry":
        """Skill registry, then the workspace overlay (overlay wins field-by-field)."""

    def get(self, source_id: str) -> RegistryEntry | None: ...
    def by_sha(self, sha: str) -> RegistryEntry | None: ...
    def by_sha_prefix(self, prefix: str) -> list[RegistryEntry]: ...
    def by_url(self, url: str) -> RegistryEntry | None: ...
    def by_filename(self, basename: str) -> list[RegistryEntry]: ...
    def by_title(self, title: str, *, edition: str = "", isbn: str = "") -> list[RegistryEntry]: ...
    def same_work(self, source_id: str) -> list[RegistryEntry]: ...
    def page_shift(self, frm: str, to: str, item: str | None = None) -> int | None: ...
    def unconfirmed(self) -> list[RegistryEntry]: ...
    def append_overlay(self, workspace: "Workspace", entry: RegistryEntry) -> Path:
        """Write `confirmed: false` into <workspace>/registry.yaml.
        NEVER writes the skill's registry/sources.yaml."""
    def validate(self) -> list[str]: ...        # incl. page_map_to symmetry


def slugify(text: str, *, max_words: int = 3) -> str: ...
def source_id_for(title: str, edition: str = "", authors: Sequence[str] = (),
                  sha256: str | None = None, taken: Container[str] = ()) -> str: ...


# =====================================================================
# CORE — scripts/cflib/discovery.py   (new)
# =====================================================================
SIGNALS = ("formalization_yaml", "ledger_source", "official_source_json",
           "graph_json", "prose", "filesystem", "citation")


@dataclass
class SourceClaim:
    signal: str
    path: str                        # repo-relative file (plus "#hint") the claim came from
    title: str = ""
    authors: tuple[str, ...] = ()
    edition: str = ""
    isbn: str = ""
    sha256: str = ""
    sha256_prefix: str = ""
    bytes: int | None = None
    pdf_hints: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    book_page_url: str = ""
    licence: str = ""
    offset_hint: int | None = None
    anchor_hint: str = ""
    files: tuple[str, ...] = ()      # §4.3: absolute paths, in READING order, whose
                                     # concatenated bytes ARE this source (no PDF to locate)
    source_format: str = ""          # pdf | text  (§5.7), read off those bytes
    pagination: str = ""             # formfeed | none  (§5.7)
    locator: str = ""                # how the ADAPTER keys its rows: number | label
    discovered_by: str = ""          # adapter | manual — recorded as `pdf_found_in`
    confidence: float = 0.0

    def merge(self, other: "SourceClaim") -> "SourceClaim": ...
    def matches(self, other: "SourceClaim") -> bool: ...


@dataclass
class LocatedPdf:
    path: Path
    sha256: str
    bytes: int
    pages: int | None
    text_sha256: str | None
    where: str                       # repo_declared|repo_cache|workspace_store|library_path|download
    rank: int


@dataclass
class DiscoveryResult:
    repo: Path
    name: str
    adapter: str
    adapter_confidence: float
    adapter_evidence: dict
    claims: list[SourceClaim]
    sources: list[dict]              # workspace.yaml `sources.<id>` bodies, ready to upsert
    anchor_source: str
    anchor_confidence: float
    anchor_evidence: dict
    bundled_sources: list[str]
    unresolved: list[str]
    warnings: list[str]

    def render(self) -> str:
        """The block `cf onboard` prints (§7.2)."""


def detect_adapter(repo: Path) -> tuple[str, float, dict]: ...
def discover_sources(repo: Path, *, adapter: str | None = None) -> list[SourceClaim]: ...
def discover_sources_with_adapter(repo: Path, *,
                                  adapter: str | None = None) -> list[SourceClaim]: ...
def merge_claims(claims: Sequence[SourceClaim], registry: Registry) -> list[SourceClaim]: ...
def search_order(claim: SourceClaim, *, repo: Path, workspace: "Workspace | None",
                 library_paths: Sequence[str]) -> list[tuple[Path, str, int]]: ...
def fingerprint(pdf: Path) -> dict: ...
def locate_pdf(claim: SourceClaim, *, repo: Path, workspace: "Workspace | None",
               registry: Registry, library_paths: Sequence[str],
               download: bool = False) -> LocatedPdf | None: ...
def resolve_claims(claims: Sequence[SourceClaim], *, repo: Path, workspace: "Workspace",
                   registry: Registry, download: bool = False) -> DiscoveryResult: ...
def choose_anchor_source(repo: Path, rows: Sequence[Mapping], candidates: Sequence[str], *,
                         workspace: "Workspace",
                         hints: Sequence[str] = ()) -> tuple[str, float, dict]: ...

MIN_ANCHOR_AGREEMENT = 0.80
MIN_ANCHOR_MARGIN = 0.05


# =====================================================================
# CORE — scripts/cflib/inference.py   (new)
# =====================================================================
MIN_OFFSET_CONFIDENCE = 0.9
STRUCTURAL_KINDS = frozenset({"Chapter", "Section", "Subsection", "Part", "Figure",
                              "Table", "Page", "Appendix", "Volume", "Step",
                              "Case", "Equation"})


@dataclass(frozen=True)
class OffsetGuess:
    offset: int
    confidence: float
    voting_pages: int
    total_pages: int
    histogram: dict[int, int]

    def ok(self, threshold: float = MIN_OFFSET_CONFIDENCE) -> bool: ...


def running_head_candidates(page_text: str, total_pages: int) -> set[int]: ...
def infer_offset(pages: Sequence[str], *, declared: int | None = None,
                 min_confidence: float = MIN_OFFSET_CONFIDENCE) -> OffsetGuess: ...
def infer_kinds(text: str, *, min_count: int = 3) -> tuple[dict[str, int], dict[str, int]]:
    """(item kind counts, structural kind counts)."""
def infer_scope(rows: Iterable[Mapping], items_json: Mapping, *, source_id: str,
                min_items: int = 5, min_ratio: float = 0.2) -> dict: ...
def infer_errata_url(claims: Sequence[SourceClaim],
                     entry: RegistryEntry | None) -> tuple[str | None, float, str]: ...
def confirm_errata(raw_html: str) -> bool: ...


# =====================================================================
# CORE — scripts/cflib/items.py  (additions only; the grammar is untouched)
# =====================================================================
QUALIFIED_SEP = "::"

def qualified_item(source_id: str, item: "Item | str") -> str: ...     # "<id>::Exercise 1.3.19"
def split_qualified(qualified: str) -> tuple[str, str]: ...            # raises on a bare item
def source_slug(source_id: str) -> str: ...
def qualified_slug(source_id: str, item: "Item | str", style: str = "dash") -> str: ...


# =====================================================================
# CORE — scripts/cflib/hashing.py
# =====================================================================
def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: Sequence[str]) -> str:
    """sha256("\n".join([run_id, source_id, items.clean(item), kind, *sorted(decls)]))."""


# =====================================================================
# CORE — scripts/cflib/state.py
# =====================================================================
STATE_VERSION = 2

def new_state(name: str, repo: str | None = None, commit: str | None = None,
              adapter: dict | None = None,
              sources: Sequence[str] = (), anchor_source: str | None = None) -> dict:
    """state.json gains `sources: {<id>: {"sha": …, "rows": …, "items": …}}` and
    `anchor_source`; the top-level `book_sha` key is gone."""

def source_section(state: dict, source_id: str, values: dict) -> dict: ...
def summary_row(state: dict) -> dict: ...            # gains "sources" and "anchor"


# =====================================================================
# ADAPTERS — scripts/cflib/adapters_api.py + scripts/adapters/*
# =====================================================================
ROW_REQUIRED_FIELDS = ("item", "item_raw", "source_id", "source_sha",
                       "decl", "file", "line", "statement", "repo")


class Adapter(ABC):
    kind: ClassVar[str] = ""

    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        """0.0-1.0 that this repo is this kind, plus the evidence."""

    def discover_sources(self, repo: str | Path) -> list[SourceClaim]:
        """Source claims from the signals THIS kind knows about (§4.3).
        The base class runs S5/S6/S7; subclasses add S1-S4 and call super()."""

    @abstractmethod
    def dry_run(self, repo: str | Path | None = None) -> dict: ...

    @abstractmethod
    def extract(self, repo: str | Path | None = None, *,
                sources: Mapping[str, str] | None = None) -> Iterator[dict]:
        """`sources` maps source_id -> source_sha for every source this companion
        is bound to. Every row carries the source_id it was attributed to."""

    def source_for(self, *, file: Path, tag: str, decl: str) -> str:
        """Which source a row belongs to. Default: adapter.yaml `sources.default`
        when the companion has one source; otherwise the first `sources.map`
        rule that matches by `files:` glob or `tag_prefix:`. Unattributable rows
        with no default are an AdapterError naming the file and tag."""

    def key_inputs(self) -> dict: ...      # now includes the bound source shas


# adapter.yaml gains:
#   sources:
#     default: <source_id>
#     map:
#       - {source: <source_id>, files: ["Chapter2/**"]}
#       - {source: <source_id>, tag_prefix: "AB:"}


# =====================================================================
# STAGES-A — scripts/sources/*  (was scripts/book/*)
# =====================================================================
# scripts/sources/prepare_source.py
def prepare(pdf: str, out_dir: str, *, source_id: str, offset: int | None = None,
            kinds: Sequence[str] | None = None, edition: str = "", parts: int = 3,
            expect_sha: str = "", force: bool = False, write_pages: bool = True,
            infer: bool = True) -> dict:
    """offset=None / kinds=None are INFERRED (§5.1, §5.2) and the returned
    meta.json payload carries offset_confidence, offset_method and kind_counts.
    `expect_sha` remains the hard source gate."""

def pdftotext(pdf: str, out_txt: str, *, layout: bool = True) -> str: ...
def split_pages(text: str) -> list[str]: ...
def heading_regex(kinds: Sequence[str], parts: int = 3) -> re.Pattern: ...
def find_items(text: str, kinds: Sequence[str], *, offset: int, parts: int = 3) -> list[dict]: ...
def load_items(source_dir: str) -> dict: ...
def items_index(items_json: Mapping) -> dict[str, dict]: ...
def load_meta(source_dir: str) -> dict: ...

# scripts/sources/fetch_errata.py
def fetch(url: str, out_dir: str, *, source_id: str = "", source_sha: str = "",
          offset: int | None = None, timeout: int = 60, force: bool = False) -> dict: ...

# scripts/sources/render_pages.py
def render(pdf: str, out_dir: str, pages: Sequence[int], *, dpi: int = 150,
           force: bool = False) -> dict: ...


# =====================================================================
# STAGES-A — scripts/pair/*
# =====================================================================
# compare.py
def compare(tables: Mapping[str, Sequence[dict]], out_dir: str, *, source_id: str,
            source_sha: str, kinds: Sequence[str], book_items: Mapping,
            chapters: Sequence[int] | None = None) -> dict: ...
def index_tables(tables, *, source_id: str, kinds) -> dict: ...
def load_tables(paths: Mapping[str, str], *, source_id: str | None = None) -> dict[str, list[dict]]:
    """With source_id, keeps only the rows of that source."""

# anchor_check.py
def check(items_index: Mapping[str, Mapping], tables: Mapping[str, Sequence[Mapping]], *,
          source_id: str, source_sha: str, offset: int = 0, run_id: str = "anchor-check",
          kinds: Sequence[str] = DEFAULT_KINDS, tolerance: int = 0) -> dict: ...
def check_edition(items_by_source: Mapping[str, Mapping], rows: Sequence[Mapping], *,
                  kinds: Sequence[str] = DEFAULT_KINDS) -> dict:
    """NEW. Anchor agreement per candidate edition — the mechanical half of
    choose_anchor_source (§4.6). Returns
    {source_id: {"ok": n, "mismatched": m, "agreement": r}}."""
def write_check(out_dir: str, result: Mapping) -> str: ...

# def_coverage.py
def def_coverage(items_json, rows, *, source_id: str, chapter=None, kinds=…) -> dict: ...
def uncovered(items_json, rows, *, source_id: str, kinds=…, chapter=None) -> dict: ...
def write_coverage(out_dir: str, name: str, cov: dict, unc: dict) -> dict: ...

# make_queue.py
def build_queue(pairs, *, source_id: str, offset: int, max_pages: int,
                book_items: Mapping, printed_max: int) -> list[dict]: ...
def build_targets(index, name: str, *, source_id: str, repos, offset: int, max_pages: int,
                  book_items: Mapping, kinds, printed_max: int) -> list[dict]: ...
def write_queue(out_dir: str, queue, targets, *, source_id: str, source_sha: str,
                offset: int) -> dict: ...


# =====================================================================
# STAGES-B — plan / gate / run / harvest / ledger / route / detect / report
# =====================================================================
# plan/presets.py
def gate_config(preset: Preset, *, run_id: str, sources: Mapping[str, Mapping],
                repos: list[str], decl_index: str | None = None) -> dict:
    """gate.json `sources` block:
       {source_id: {"text": <path>, "sha256": …, "text_sha256": …,
                    "images_dir": …, "page_offset": int,
                    "item_pages": {item: pdf_page}}}
       plus a top-level "max_match_pages": int (0 = off)."""
def item_pages(items: Mapping[str, Mapping]) -> dict[str, int]:
    """{item: pdf_page} for that block, from an items_index — the PINNED-TEXT
    page, never items.json's printed `page`."""

# gate/verify_findings.py   (rsynced to a host with no cflib: keep it stdlib-only)
class GateConfig:
    def source(self, source_id: str) -> dict: ...
    def source_ids(self) -> list[str]: ...
def load_sources(cfg: GateConfig) -> tuple[dict[str, BookText], list[str]]: ...
def validate_doc(doc, name: str, cfg: GateConfig, books: Mapping[str, BookText]) -> list[str]:
    """Picks the text by doc["source_id"]; a doc naming an unknown source, or a
    doc whose source_sha differs from cfg's, is a gate failure."""
def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: list[str]) -> str: ...     # byte-identical to cflib's

# gate/convert_v1.py
def convert_doc(doc: dict, *, run_id: str, source_id: str, source_sha: str,
                mode: str = "differential", preset: str = "", repos: Iterable[str] = (),
                fold_no_weakness: bool = True, lift_witness: bool | None = None) -> dict:
    """v1 -> v2. The v1 `book_sha` slot becomes `source_sha`, and every document
    and disagreement gains `source_id`. v1 rows have no source, so the importer
    supplies the run's single source; ids are recomputed under §6.1."""
def convert_tree(src: Path, dst: Path, **kwargs) -> int: ...

# run/manifest.py
def build_manifest(*, workspace_dir: Path, skill_dir: Path, run_id: str, run: dict,
                   host: dict, include_sources: bool = False,
                   include_texts: bool = False) -> list[dict]: ...
def source_shas(workspace, run: dict) -> dict[str, str]: ...      # was book_sha()
def source_access(run: dict) -> str: ...                          # was book_access()

# harvest/harvest.py
def source_audit(dest: Path, expected: Mapping[str, str]) -> dict:
    """Was edition_audit(). {source_id: sha} expected vs found; a mismatch fails
    the receipt unless --allow-edition-mismatch, which the receipt records."""
def build_receipt(*, run_id: str, source: dict, expected: Sequence[str], dest: Path,
                  sources: Mapping[str, str], …) -> dict: ...

# harvest/tally.py, union.py, agreed.py
def rows_for_run(run_id: str, directory, *, kinds=None, sources: Sequence[str] | None = None) -> list[dict]: ...
def tally(runs, *, kinds=None, group_by: tuple[str, ...] = ("source_id", "repo")) -> dict: ...
def union(rows, repo: str | None = None, *, source_id: str | None = None, …) -> list[dict]: ...

# ledger/findings.py, transcribe.py, validate_ledger.py
def normalise_finding(row: dict, *, run_id: str, source_id: str, source_sha: str, …) -> dict: ...
def validate_row(row: dict, *, sources: Mapping[str, str], …) -> list[LedgerError]:
    """A ledger row without a source_id, or with a source_sha the workspace does
    not know, is an error — the source gate at the ledger boundary."""

# route/route.py
def route_repo(findings, companion: str, *, source_id: str, source_sha: str,
               ledger_rows, workspace: str, anchors=None) -> dict: ...
def route_workspace(...) -> dict: ...        # was route_campaign

# detect/findings.py
def proxy_row(*, run_id: str, companion: str, source_id: str, source_sha: str,
              item: str, decl: str, rule: str, severity: str, …) -> dict: ...
def coverage_row(*, run_id: str, companion: str, source_id: str, source_sha: str, …) -> dict: ...
def meta(*, run_id: str, companion: str, tier: str, source_id: str, source_sha: str | None,
         counts: dict, inputs: dict) -> dict: ...

# report/claims.py, make_handoff.py
def build_sources(workspace: str, companions: Iterable[str], cfg) -> list[dict]: ...
def build_context(paths, *, sources: Mapping[str, SourceRef], …) -> dict: ...
```

---

## 9. File-by-file change list

Five packages: four that change code — **CORE**, **ADAPTERS**, **STAGES-A**,
**STAGES-B** — and **DOCS**, which changes prose only and lands last.

The grep that defines the surface (run from the repo root over `git ls-files`):

```sh
grep -lnE '\b(campaign|CF_CAMPAIGN|CampaignPaths|book_sha|book_id|book_dir|book_txt|book_json|book_quote|errata_url|offset)\b' $(git ls-files)
```

**110 tracked files match** (the "53" in the original brief was an undercount —
it misses the test suite, the workflows and the reference prose). Every one is
assigned below, plus 11 files that do **not** match the grep but must change
anyway, or must deliberately not change (marked ✚), and the 7 new files the
packages create (marked ✦). Counts are
matching lines, as a rough size signal.

### Package CORE — workspace, paths, registry, discovery, inference, `cf init/onboard/sources/status`

| file | n | change |
|---|---|---|
| ✦ `scripts/cflib/workspace.py` | new | `Workspace`, `SourceRef`, `CompanionRef`, `find_workspace_root`, `from_args`, `require_source_sha`, `shared_sources`, `check_host`, `validate` (§8) |
| `scripts/cflib/campaign.py` | 41 | **delete** once `workspace.py` lands; no shim, no alias |
| ✦ `scripts/cflib/registry.py` | new | `Registry`, `RegistryEntry`, `source_id_for`, `slugify`, overlay append, page-map symmetry validation |
| ✦ `scripts/cflib/discovery.py` | new | `detect_adapter`, `discover_sources`, `discover_sources_with_adapter`, `merge_claims`, `search_order`, `locate_pdf`, `fingerprint`, `resolve_claims`, `choose_anchor_source` |
| ✦ `scripts/cflib/inference.py` | new | `infer_offset`/`OffsetGuess`, `infer_kinds`, `infer_scope`, `infer_errata_url`, `confirm_errata` |
| ✦ `registry/sources.yaml` | new | the shipped registry, seeded with the two Tao editions (§2.2) |
| ✦ `templates/workspace.yaml.example` | new | replaces `campaign.yaml.example`; keeps every load-bearing comment, adds `sources:`, `library_paths:`, the map-shaped `companions:` |
| ✦ `scripts/cfcmd/sources.py` | new | `cf sources list|show|add|set|refresh` |
| `scripts/cflib/paths.py` | 18 | `CampaignPaths` → `WorkspacePaths`; drop `book_id`; `book_*` → `source_*(source_id)`; `pairs_*` take a `source_id`; add `registry_yaml`, `list_sources`, `list_paired_sources` |
| `scripts/cflib/state.py` | 14 | `STATE_VERSION` 2; drop top-level `book_sha`; add `sources: {<id>: {sha, rows, items}}` and `anchor_source`; `source_section()`; `summary_row` gains `sources`/`anchor` |
| `scripts/cflib/items.py` | 3 | add `QUALIFIED_SEP`, `qualified_item`, `split_qualified`, `source_slug`, `qualified_slug`; the grammar itself is untouched; docstring stops saying "campaign.yaml book.kinds" |
| ✚ `scripts/cflib/hashing.py` | 0 | `finding_id` gains `source_id` (§6.1); docstring records why |
| `scripts/cflib/errors.py` | 2 | `CampaignError` → `WorkspaceError`; add `SourceError`, `DiscoveryError` |
| `scripts/cflib/__init__.py` | 5 | re-export `Workspace`, `WorkspacePaths`, `find_workspace_root`, `registry`, `discovery`, `inference`; drop every `Campaign` name |
| `scripts/cflib/jsonio.py` | 2 | docstring only (`campaign artifact` → `workspace artifact`) |
| `scripts/cflib/templates.py` | 2 | docstring; render `workspace.yaml.example` |
| `scripts/cflib/vocab.py` | 4 | add `SOURCE_ROLES = {audited, reference}`, `LICENCE_FETCH = {none, author_site, open}`, `DISCOVERY_SIGNALS`; `book_sha` mentions → `source_sha` |
| `scripts/cflib/yamlio.py` | 1 | docstring; confirm the fallback reader handles the nested `sources:`/`page_map_to:` maps and the `>-` folded scalars the registry uses — **add a test** |
| `scripts/cflib/adapters_api.py` | 7 | interface only: `ROW_REQUIRED_FIELDS`, `Adapter.detect`, `Adapter.discover_sources`, `Adapter.extract(sources=…)`, `Adapter.source_for` (§8). Implementations are ADAPTERS |
| `scripts/cf.py` | 9 | `--campaign`/`$CF_CAMPAIGN` → `--workspace`/`$CF_WORKSPACE`; epilog rewritten |
| `scripts/cfcmd/__init__.py` | 2 | docstring |
| `scripts/cfcmd/init.py` | 55 | **`cf init <dir>` takes no source flags.** Drop `--book/--pdf/--sha256/--offset/--errata-url/--title/--edition/--kinds/--chapters`; write `workspace.yaml` + empty `registry.yaml`; `.gitignore` `book/` → `sources/`; keep every refusal (skill repo, Lean repo, nested git, remote) and the pre-push hook verbatim |
| `scripts/cfcmd/onboard.py` | 21 | **rewritten**: positional is the *repo path*; `--name` optional (default: normalised basename); `--adapter` optional (auto-detected); add `--download`, `--anchor-source`, `--dry-run`. Runs discover → register → prepare → extract and prints `DiscoveryResult.render()`. Keeps `_check_adapter_implemented`, the "0 tagged declarations" failure and the conventions.md copy |
| `scripts/cfcmd/book.py` | 34 | **delete**; `cf sources refresh` replaces it, calling STAGES-A's `sources/prepare_source.py` |
| `scripts/cfcmd/status.py` | 34 | add the sources table and the `unconfirmed sources` block; `_campaign_facts` → `_workspace_facts` reading per-source `meta.json`; the per-companion warning compares `state.sources[<id>].sha` to the workspace's |
| `templates/campaign.yaml.example` | 14 | **delete** (replaced by `workspace.yaml.example`) |
| ✚ `templates/adapter.yaml.example` | 0 | add the `sources: {default, map}` block (§8) |
| `tests/test_cflib.py` | 50 | `TestCampaign` → `TestWorkspace` (+ `shared_sources`, `sources_of`, `companions_of`, `require_source_sha`, validation); new `TestRegistry`, `TestSourceIdRule`, `TestDiscovery` (fixture repos, no network), `TestInference` (offset/kinds/scope on synthetic text); `TestOnboardIntegration` drives the new flow; `TestHashing` covers the 5-arg `finding_id` |

### Package ADAPTERS — `discover_sources()` per kind, rows carry the source

| file | n | change |
|---|---|---|
| `scripts/adapters/api.py` | 25 | `Adapter.detect`, base `discover_sources` (signals S5/S6/S7), `source_for`, `sources` config accessors; `page_offset()`/`book_sha()` → `offset_for(source_id)`/`sha_for(source_id)`; `key_inputs` includes the bound source shas |
| `scripts/adapters/__init__.py` | 1 | `load()` passes the workspace + bound sources; export `detect_all(repo)` for `detect_adapter` |
| `scripts/adapters/base.py` | 6 | `make_row`/`validate_row`/`validate_rows` require `source_id` + `source_sha`; `row_sort_key` sorts by `(source_id, …)` |
| `scripts/adapters/docstring_tags.py` | 5 | `detect()`; `discover_sources()` adds **S1** (`formalization.yaml`) and **S4** (`graph.json` `metadata.sources`, incl. the `page_offset_note` → `offset_hint` parse); rows stamp `source_id` via `source_for` |
| `scripts/adapters/attribution_table.py` | 7 | `detect()`; `discover_sources()` adds **S2** (`source-formalization-ledger.json` `source`) and **S3** (`pdf-overlay/official-source.json`); rows stamp the source; `key_inputs` keeps the export shas |
| `scripts/adapters/itemtag.py` | 1 | docstring; tag matching is source-agnostic and stays as-is |
| ✚ `scripts/adapters/leanparse.py` | 0 | unchanged — listed so the package knows it is deliberately untouched |
| ✚ `scripts/adapters/yamlio.py` | 0 | unchanged |
| `scripts/cfcmd/extract.py` | 22 | per-companion loop becomes per-(companion, source); `--allow-missing-book-sha` → `--allow-missing-source-sha`; validates against the *source's* kinds; writes `state.sources[<id>]`; calls `inference.infer_scope` and updates `sources.<id>.chapters` |
| `templates/adapters/attribution_table.measure_theory.yaml` | 4 | add the `sources:` block pinned to `tao-measure-gsm126-published`; drop the page-offset key |
| `templates/adapters/docstring_tags.tao-measure-lean.yaml` | 1 | same |
| `tests/test_adapters.py` | 13 | rows assert `source_id`/`source_sha`; new `TestDetect` and `TestDiscoverSources` over fixture trees mirroring both target repos; `ExtractCommandTest` uses a two-source workspace |

### Package STAGES-A — `scripts/book/*` → `scripts/sources/*`, pairing per source

| file | n | change |
|---|---|---|
| `scripts/book/__init__.py` | 1 | → `scripts/sources/__init__.py` |
| `scripts/book/prepare_book.py` | 26 | → `scripts/sources/prepare_source.py`; `prepare()` takes `source_id`, `offset=None`/`kinds=None` mean *infer*; writes `text.txt`, `items.json`, `meta.json` (the old `book.json` payload plus `offset_confidence`, `offset_method`, `kind_counts`, `text_sha256`, `provenance`); `expect_sha` stays the hard gate |
| `scripts/book/fetch_errata.py` | 10 | → `scripts/sources/fetch_errata.py`; `book_sha=` → `source_sha=`, add `source_id=`; **curl + raw HTML kept, never a summarising fetch** — unchanged behaviour |
| `scripts/book/render_pages.py` | 2 | → `scripts/sources/render_pages.py`; writes into `sources/<id>/images/` |
| `scripts/book/compat.py` | 40 | → `scripts/sources/compat.py`; the whole fallback layer retargets (`_FallbackCampaign` → `_FallbackWorkspace`, `campaign_paths` → `workspace_paths`, `book_sha`→`source_sha`); its inlined `finding_id` gains `source_id` and must stay byte-identical to `cflib.hashing` |
| ✚ `scripts/pair/__init__.py` | 0 | docstring |
| ✚ `scripts/pair/compat.py` | 0 | re-export shim follows the renames |
| `scripts/pair/compare.py` | 13 | `compare()`/`index_tables()`/`load_tables()` take `source_id`; every emitted row carries `source_id`+`source_sha`; reconciliation is per source |
| `scripts/pair/anchor_check.py` | 11 | `check()` takes `source_id`; rows carry it; `finding_id` call gains it; **new `check_edition()`** (§8) — the mechanical half of anchor selection; `PAGE_ANCHOR` stays mechanical-only |
| `scripts/pair/def_coverage.py` | 3 | `source_id` parameter; `write_coverage` writes under `pairs/<source_id>/` |
| `scripts/pair/make_queue.py` | 11 | `build_queue`/`build_targets`/`write_queue` take `source_id`; queue entries carry `source_id`; `late_section_threshold` reads the source's chapters |
| `scripts/cfcmd/pair.py` | 33 | **iterates `workspace.shared_sources()` plus every single-companion source**; `--book` → `--source`; one `pairs/<source_id>/` tree per source; the PAGE_ANCHOR corpus emission is per source and keyed `anchor-check-<source_id>`; state records coverage per source |
| `tests/test_book_pair.py` | 28 | → `tests/test_sources_pair.py`; `PrepareBookTests` → `PrepareSourceTests` (+ inference assertions); `AnchorCheckTests` gains `check_edition` coverage on a two-edition fixture; `CommandTests` runs `cf pair` over a two-source workspace |

### Package STAGES-B — plan, gate, run, harvest, ledger, route, detect, report

| file | n | change |
|---|---|---|
| `scripts/plan/presets.py` | 2 | `gate_config()` takes the `sources` map (§8); presets themselves unchanged |
| `scripts/plan/make_tasks.py` | 10 | `Entry`/`Task` carry `source_id`; slugs use `qualified_slug`; `_page_from` uses the source's offset; `reconcile()` counts against the per-source universe |
| `scripts/plan/decl_index.py` | 7 | reads `statements.jsonl` filtered by the run's source |
| `scripts/plan/render_prompts.py` | 3 | prompt context gains `source` (title, edition, sha8, offset, errata path) |
| `scripts/plan/make_tend_root.py` | 1 | gate command passes the per-source gate.json |
| `scripts/plan/render.py` | 1 | docstring |
| `scripts/gate/schema.json` | 3 | `book_sha` → `source_sha`; add required `source_id`; `$id` path unchanged; description records the v1→v2 mapping |
| `scripts/gate/verify_findings.py` | 15 | `GateConfig.source(id)`, `load_sources()`, per-source `BookText`; `validate_doc` selects the text by `source_id` and refuses an unknown source or a `source_sha` mismatch; `finding_id` gains `source_id` and stays byte-identical to `cflib.hashing`; **still stdlib-only** (it is rsynced to a host with no `cflib`) |
| `scripts/gate/convert_v1.py` | 3 | `convert_doc(..., source_id, source_sha)`; `--source` required; stamps `source_id` on the document and on every disagreement; recomputes ids |
| ✚ `scripts/gate/check_changed_files.py` | 0 | unchanged (listed so the package does not "fix" it) |
| `scripts/run/manifest.py` | 28 | `campaign_dir` → `workspace_dir`; `book_sha()` → `source_shas()`; `book_access()` → `source_access()`; manifest entries per source text/images dir; budget and host checks unchanged |
| `scripts/run/fleet_local.py` | 6 | args carry `source_id`; item notes name the source when a run spans more than one |
| `scripts/run/_compat.py` | 4 | rename only |
| `scripts/run/remote.py` | 1 | remote paths follow `sources/<id>/` |
| `scripts/harvest/harvest.py` | 16 | `edition_audit` → `source_audit(dest, expected: {id: sha})`; receipts carry `sources`; `--allow-edition-mismatch` keeps its name and its record on the receipt |
| `scripts/harvest/tally.py` | 2 | `rows_for_run`/`collect`/`tally` group by `(source_id, repo)`; `collapse_to_book_item` takes the source's kinds |
| `scripts/harvest/_compat.py` | 4 | `campaign_kinds` → `source_kinds(workspace, source_id)` |
| ✚ `scripts/harvest/union.py` | 0 | `union()`/`build()` take an optional `source_id`; entries carry it |
| ✚ `scripts/harvest/agreed.py` | 0 | agreement is computed within a source, never across |
| ✚ `scripts/harvest/gate_corpus.py` | 0 | passes the per-source gate config through |
| ✚ `scripts/harvest/harvest_journal.py` | 0 | journal items carry `source_id`; `_slug` → `qualified_slug` |
| `scripts/ledger/compat.py` | 40 | the whole fallback layer retargets (`CampaignInfo` → `WorkspaceInfo`, `LedgerPaths` per-source pairs, `book_sha`→`source_sha`); inlined `finding_id` gains `source_id` |
| `scripts/ledger/findings.py` | 14 | `normalise_finding` requires and propagates `source_id`/`source_sha`; `index_by_id` unchanged |
| `scripts/ledger/transcribe.py` | 22 | ledger rows carry `source_id`/`source_sha`; the markdown groups by source |
| `scripts/ledger/validate_ledger.py` | 22 | a row without `source_id`, or with a `source_sha` the workspace does not know, is an error (the source gate at the ledger boundary); `ledger_coverage` joins on `(source_id, finding_id)` |
| `scripts/ledger/migrate.py` | 6 | v1 ledger rows get the run's `source_id`; `match_finding` joins on the pair |
| `scripts/ledger/workflow_args.py` | 10 | `campaign_block` → `workspace_block` (+ `sources`); `companion_block` names the companion's sources and anchor |
| `scripts/route/route.py` | 29 | `route_campaign` → `route_workspace`; `route_repo(..., source_id, source_sha)`; the anchor-check withdrawal oracle reads `pairs/<source_id>/anchor-check.json`; owner messages name the edition |
| `scripts/detect/findings.py` | 10 | `proxy_row`/`coverage_row`/`meta` take `source_id`+`source_sha`; `default_run_id` unchanged |
| `scripts/detect/corpus.py` | 6 | documents carry `source_id`; `slug` → `qualified_slug` |
| `scripts/detect/def_coverage.py` | 4 | `load_items` reads `sources/<id>/items.json`; rows carry the source |
| `scripts/detect/analyze_stages.py` | 4 | finding rows carry the source |
| `scripts/detect/stages.py` | 1 | `StageTable.for_book(book)` → `for_source(source_id)`; the shipped table file is looked up by `source_id` |
| `scripts/detect/mathlib_stages/tao-measure.json` | 1 | rename to `tao-measure-gsm126-published.json` (and keep a `source_ids:` key inside listing both Tao editions, since the stage table is edition-independent) |
| `scripts/detect/_compat.py` | 1 | inlined `finding_id` gains `source_id` |
| `scripts/detect/deps.py` | 1 | docstring |
| `scripts/detect/dump_deps.sh` | 1 | `$CF_CAMPAIGN` → `$CF_WORKSPACE` |
| `scripts/report/claims.py` | 17 | `build_sources` walks the workspace's sources; verification commands name `--workspace` and `--source` |
| `scripts/report/make_handoff.py` | 20 | state/run/findings tables gain a source column; `_book_access` → `_source_access`; paths section lists `sources/<id>/` and `pairs/<id>/` |
| `scripts/cfcmd/plan_run.py` | 27 | add `--source`; required when the selected companions share >1 source; `run.yaml` records `sources`; gate config built from the source map; reconciliation is per source |
| `scripts/cfcmd/run.py` | 55 | `--include-book` → `--include-source-text`; manifest and consent record the sources; everything else (allowlist, consent, session cap, budget) unchanged |
| `scripts/cfcmd/harvest.py` | 30 | source gate replaces the edition gate; receipt carries `sources` |
| `scripts/cfcmd/import_corpus.py` | 22 | `--book-sha` → `--source-sha`, add `--source <id>` (required when the workspace has >1); passes both to `convert_v1` |
| `scripts/cfcmd/tally.py` | 18 | `--source` filter; the printed table groups by source |
| `scripts/cfcmd/adjudicate.py` | 15 | corpus rows carry the source; workflow args gain the source block |
| `scripts/cfcmd/route.py` | 21 | `--book-sha` → `--source-sha`; `--anchor-check` defaults per source; keeps the `redact_quotes_on_export` policy read |
| `scripts/cfcmd/report.py` | 3 | rename only |
| `scripts/cfcmd/detect_proxies.py` | 50 | `--book` → `--source`; per-source items/stages/statements; rows and corpus carry the source |
| `templates/prompts/worker-system.md.tmpl` | 2 | "the book" → the named source, with edition and sha8 in the header |
| `templates/tasks/attack.yaml.tmpl` | 2 | task front-matter gains `source_id` |
| `templates/tend/config.yaml.tmpl` | 1 | `$CF_CAMPAIGN` → `$CF_WORKSPACE` |
| `workflows/adjudicate.workflow.js` | 8 | args/results carry `source_id`; `book_sha` → `source_sha` |
| `workflows/challenge.workflow.js` | 7 | same |
| `workflows/fleet-local.workflow.js` | 13 | same, plus per-item source in the journal |
| `workflows/verify-claims.workflow.js` | 3 | verification commands use `--workspace` |
| `tests/test_plan_gate.py` | 31 | `TestFindingIdIsOneDefinition` moves to the 5-arg form and keeps asserting that **all five** implementations agree; gate tests build a two-source `gate.json`; `TestConvertV1` passes `--source`; `TestPlanRunEndToEnd` uses a two-source workspace |
| `tests/test_harvest.py` | 35 | receipts assert `sources`; tally/union/agreed assert per-source grouping; a new case asserts two sources with a colliding item id do **not** merge |
| `tests/test_ledger_route.py` | 56 | ledger/route rows assert `source_id`; a row with an unknown `source_sha` must fail validation |
| `tests/test_detect.py` | 15 | detector rows assert the source; stage-table lookup by `source_id` |
| `tests/test_integration.py` | 50 | `cf init` takes no flags; a full `init → onboard ×2 → pair → plan-run` over a synthetic two-companion/one-shared-source workspace; the git guards and the export policy tests keep their assertions |

### Package DOCS

| file | n | change |
|---|---|---|
| `SKILL.md` | 12 | rewrite the two-directory table, the default program (`cf onboard <repo>` first, `cf book` gone), the command index (§7), hard rule 3 (edition → source gate) and the vocabulary section; add a "workspaces, companions and sources" paragraph pointing here |
| `README.md` | 12 | rewrite "The campaign directory model" → "The workspace directory model" (§6.2) and the Tao quick start as `cf init` + two flagless `cf onboard`s |
| `CLAUDE.md` | 6 | §2 layout (`scripts/sources/`, `registry/`, `docs/design/`); §4 "the rules that must not drift" — stamp the *source*, one item grammar **per source**, `finding_id` is 5-part; §5 "adding a command" uses `cflib.workspace.from_args`; §6 "adding an adapter" adds `detect`/`discover_sources`/`source_for` |
| `references/onboarding-contract.md` | 15 | the biggest doc change: Stage 0 is now *discovery*, not typed metadata. Document the seven signals, the PDF search order, the anchor-edition rule and what a repo must provide for discovery to succeed (§4), and keep the seven obligations |
| `references/findings-schema-v2.md` | 22 | row schema gains `source_id`/`source_sha`; `book_sha` → `source_sha` throughout; `finding_id` definition updated; the ledger and task schemas gain the source |
| `references/traps.md` | 12 | keep the two-editions trap and re-file it as "the anchor edition is not the bundled edition"; add: truncated shas in prose are candidates not identities; `sources/` in `.gitignore` hides `scripts/sources/` without the negation; `offset_confidence < 0.9` means *ask*, never guess |
| `references/vocabulary.md` | 6 | `book_sha` → `source_sha`; add `SOURCE_ROLES`, `LICENCE_FETCH`, `DISCOVERY_SIGNALS`; note `PAGE_ANCHOR` is still mechanical-only |
| `references/method.md` | 2 | "one campaign per book" → "one workspace, N companions, M sources"; the differential is where source lists overlap |
| `references/review-patterns.md` | 5 | "the book" → "the source"; pattern list unchanged |
| `references/runbook-server.md` | 6 | `--campaign` → `--workspace`; transferred paths are `sources/<id>/` |
| `references/workflow-patterns.md` | 3 | workflow args carry the source |
| `templates/PRESETS.md` | 6 | presets are per source; `--source` in every example |
| `templates/handoff.md` | 2 | handoff template gains a sources table |
| `templates/conventions.md.example` | 1 | header names the companion *and* its sources |
| `.gitignore` | 2 | `book/` → `sources/` **and** `!scripts/book/` → `!scripts/sources/` — same commit as the package rename, or the Stage-1 package disappears from git |

### 9.1 Order of work and the seams

```
CORE  ──────────────────────────────────────────────►  (everything depends on it)
   │  publishes: cflib.workspace, paths, registry, discovery, inference,
   │             items.qualified_*, hashing.finding_id(5), state v2
   ├──► ADAPTERS   needs: adapters_api contract, discovery.SourceClaim
   │       publishes: Adapter.detect/discover_sources/source_for, rows with source_id
   ├──► STAGES-A   needs: paths.source_*/pairs_*(source_id), inference, items.qualified_*
   │       publishes: sources/prepare_source, pair/* per source, anchor_check.check_edition
   │                  (CORE's `cf onboard` and `cf sources` call these)
   └──► STAGES-B   needs: all of the above + gate.json `sources` block
DOCS  ── last, and only after the CLI it documents actually runs
```

Three seams need an explicit handshake:

1. **CORE ↔ STAGES-A.** `cf onboard` and `cf sources refresh` (CORE) call
   `sources.prepare_source.prepare()` and `sources.fetch_errata.fetch()`
   (STAGES-A). CORE may land first against a stub that raises
   `NotImplementedError`; the signatures in §8 are the contract.
2. **CORE ↔ STAGES-A on anchor selection.** `discovery.choose_anchor_source`
   (CORE) calls `pair.anchor_check.check_edition` (STAGES-A). Same rule: the
   signature is fixed here, and CORE degrades to rule 1/rule 3 of §4.6 (and a
   refusal) while the mechanical half is unimplemented.
3. **`finding_id` is defined five times on purpose.** `cflib/hashing.py`,
   `gate/verify_findings.py`, `sources/compat.py`, `ledger/compat.py` and
   `detect/_compat.py` each carry the definition because the gate and the
   compat layers run where `cflib` is not importable. All five change in
   lockstep, and `tests/test_plan_gate.py::TestFindingIdIsOneDefinition` is the
   enforcement — it must be updated in the same commit as the first of them.

### 9.2 Definition of done

- `python3 -m unittest discover -s tests` is green with no baseline env set.
- With `CF_BASELINE_CORPUS`, `CF_BASELINE_DIFF`, `CF_TML`, `CF_MT`,
  `CF_BOOK_TXT` set, the suite is green and **no coverage was deleted** — every
  test that existed either still exists or was renamed and rewritten for the
  new model.
- `grep -rn 'campaign\|book_sha\|CF_CAMPAIGN' $(git ls-files)` returns only
  deliberate historical prose (this document, `references/traps.md`,
  `references/method.md` where it explains what the old model was) — no code.
- On this machine: `cf init <tmp>` then `cf onboard ~/Desktop/tao-measure-lean`
  then `cf onboard ~/Desktop/measure_theory`, with **no other flags**, produces
  the `workspace.yaml` of §1.3 (modulo timestamps and the row counts), and
  `cf pair` produces exactly one `pairs/tao-measure-gsm126-published/` tree.
- Every gate in §0.2 still refuses what it refused before, now per source.
